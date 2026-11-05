"""Local authenticated bridge from checked Actas exports to existing references.

No application installation, migrations, stock writes, HTTP endpoint or task.
Credentials stay in memory. Originals/result.json are never rewritten.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

import actas_export_check as pilot

WAREHOUSES = ('Y02', 'Y09')
NAMESPACE = uuid.UUID('b5736516-e2df-4cc1-98f7-97570bda0103')


def source_time(value):
    try:
        stamp = datetime.fromisoformat(value)
        if stamp.tzinfo is None or stamp > datetime.now(timezone.utc):raise ValueError
        return stamp
    except (TypeError, ValueError):
        raise RuntimeError('수집 기록의 기준 시각이 유효하지 않습니다.') from None


def checked_source(folder):
    """Reparse bytes, compare every summary, require the successful v5 gates."""
    folder = Path(folder).resolve()
    path = folder / 'result.json'
    if path.is_symlink() or path.stat().st_size > 1024 * 1024:
        raise RuntimeError('정상 실행 기록 파일을 선택하세요.')
    record = json.loads(path.read_text(encoding='utf-8-sig'))
    if (record.get('format') != 3 or record.get('pilot_version') != 5
            or record.get('kind') != 'Actas-export-pilot-NOT-import'
            or record.get('phase') != 'export_files_checked'
            or record.get('registered_in_sunil') is not False
            or set(record.get('files', {})) != set(WAREHOUSES)):
        raise RuntimeError('v5 조회·저장 검증을 완료한 result.json만 등록할 수 있습니다.')
    started, completed = source_time(record['started_at']), source_time(record['completed_at'])
    if completed < started:raise RuntimeError('수집 기록의 시각 순서가 잘못됐습니다.')
    blobs, summaries = {}, {}
    for warehouse in WAREHOUSES:
        entry = record['files'][warehouse]
        evidence = record.get('input_evidence', {}).get(warehouse, {})
        query = evidence.get('query_check', {})
        if (entry.get('name') != warehouse + '.xls' or entry.get('parse_status') != 'valid'
                or entry.get('query_confirmed_by') != 'operator'
                or evidence.get('requested') != warehouse or evidence.get('editor_readback') != warehouse
                or evidence.get('input_method') != 'native_keyboard_then_Tab_ShiftTab'
                or evidence.get('tab_transition_observed') is not True
                or evidence.get('warehouse_name_confirmed_by') != 'operator'
                or evidence.get('query_sent') is not True
                or query.get('editor_readback') != warehouse or query.get('focus_verified') is not True
                or query.get('query_sent') is not True):
            raise RuntimeError(f'{warehouse}: 창고/조회 담당자 확인 기록이 없습니다.')
        confirmed, saved = source_time(entry['query_confirmed_at']), source_time(entry['saved_at'])
        if not started <= confirmed <= saved <= completed:
            raise RuntimeError(f'{warehouse}: 조회·저장 시각 순서가 잘못됐습니다.')
        target = folder / entry['name']
        if target.is_symlink():raise RuntimeError('연결된 파일은 등록하지 않습니다.')
        data = pilot.read_closed(target)
        summary = pilot.inspect_export(data)
        if hashlib.sha256(data).hexdigest() != entry.get('sha256') or any(entry.get(k) != v for k, v in summary.items()):
            raise RuntimeError(f'{warehouse}: 검증 이후 파일 또는 요약이 변경됐습니다.')
        if not summary['integer_quantity_compatible']:
            raise RuntimeError(f'{warehouse}: 현재 ERP는 정수 대조 수량만 저장합니다. 소수·공란을 변환하지 않고 중단합니다. 원본은 보존됩니다.')
        blobs[warehouse], summaries[warehouse] = data, summary
    if summaries['Y02']['code_set_digest'] == summaries['Y09']['code_set_digest']:
        raise RuntimeError('두 파일의 품목 구성이 같아 자동 등록하지 않습니다.')
    identity = json.dumps([record['started_at'], *(record['files'][w]['sha256'] for w in WAREHOUSES)], separators=(',', ':'))
    request_id = uuid.uuid5(NAMESPACE, identity)
    return record, blobs, summaries, request_id


@contextmanager
def registration_lock(folder):
    stream = (folder / 'registration.lock').open('a+b')
    if os.fstat(stream.fileno()).st_size == 0:
        stream.write(b'0');stream.flush()
    stream.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close()
        raise RuntimeError('이 자료의 등록 작업이 이미 진행 중입니다.') from None
    try:yield
    finally:stream.close()


def comparison_summary(snapshot):
    summary = {}
    for warehouse in WAREHOUSES:
        rows = list(snapshot.rows.filter(warehouse=warehouse))
        values = [r.erp_quantity for r in rows if r.erp_quantity is not None]
        summary[warehouse] = dict(rows=len(rows), source_items=len(values), erp_quantity=sum(values),
                                  negative_quantity_items=sum(v < 0 for v in values))
        if warehouse == 'Y09':
            summary[warehouse].update(matched=sum(r.erp_quantity is not None and r.difference == 0 for r in rows),
                                      different=sum(r.erp_quantity is not None and r.difference != 0 for r in rows),
                                      missing_in_erp=sum(r.erp_quantity is None for r in rows),
                                      managed_quantity=sum(r.managed for r in rows))
    return summary


def register_result(folder, *, actor):
    """Existing transactional service owns comparison rows and audit event.

The deterministic UUID survives process failure after commit and copied folders.
Both DB checks and writes use the existing warehouse advisory transaction lock.
"""
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.urls import reverse
    from inventory.locking import warehouse_lock
    from inventory.menu_permissions import require_menu_write
    from inventory.models import ErpSnapshot
    from inventory.reconciliation import create_snapshot, inspect_erp_exports
    require_menu_write(actor, 'management')
    folder = Path(folder).resolve()
    with registration_lock(folder):
        record, blobs, summaries, request_id = checked_source(folder)
        plan = inspect_erp_exports({w: SimpleUploadedFile(w + '.xls', blobs[w]) for w in WAREHOUSES})
        label = 'Actas Y02·Y09 검증 수집 ' + record['started_at']
        journal = dict(schema=1, kind='Actas-reference-registration', request_id=str(request_id), actor_id=actor.pk,
                       phase='registering', source_started_at=record['started_at'], source_completed_at=record['completed_at'],
                       source_files={w: record['files'][w]['sha256'] for w in WAREHOUSES},
                       automatic_query_completion_verified=False, warehouse_confirmed_by='operator',
                       stock_modified=False)
        try:
            pilot.save_json(folder / 'registration.json', journal)
            with warehouse_lock():
                previous = ErpSnapshot.objects.filter(pk=request_id).first()
                if not previous and ErpSnapshot.objects.filter(erp_as_of__gt=source_time(record['started_at'])).exists():
                    raise RuntimeError('더 최신 ERP 대조 자료가 있습니다. 과거 파일을 최신 참고 자료로 등록하지 않습니다.')
                snapshot = create_snapshot(request_id=request_id, label=label, erp_as_of=source_time(record['started_at']), plan=plan, actor=actor)
                # Build the output inside the same transaction, preserving the
                # frozen management comparison on retries after later changes.
                result = dict(snapshot_id=str(snapshot.pk), detail_path=reverse('erp-detail', args=[snapshot.pk]),
                              erp_as_of=snapshot.erp_as_of.isoformat(), compared_at=snapshot.created_at.isoformat(),
                              counts={w: summaries[w]['items'] for w in WAREHOUSES}, comparison=comparison_summary(snapshot))
            journal.update(result, phase='complete', registered_in_sunil=True, completed_at=pilot.now())
            pilot.save_json(folder / 'registration.json', journal)
            return journal
        except Exception as error:
            # The DB may already have committed. Never label this as definitely
            # unregistered or run Actas again; retry the exact deterministic ID.
            journal.update(phase='registration_unconfirmed', failure_type=type(error).__name__)
            try:pilot.save_json(folder / 'registration.json', journal)
            except OSError:pass
            raise


def runtime_environment(config_path):
    """Load the EXISTING configuration and same-account DPAPI, without writes."""
    if os.name != 'nt' or sys.version_info[:2] != (3, 12):
        raise RuntimeError('기존 Windows 계정에서 ERP Python 3.12로 실행하세요.')
    config_path = Path(config_path).resolve()
    config = json.loads(config_path.read_text(encoding='utf-8-sig'))
    if os.path.normcase(os.path.abspath(config['PythonExe'])) != os.path.normcase(os.path.abspath(sys.executable)):
        raise RuntimeError('기존 ERP 가상환경의 python.exe로 실행하세요.')
    app = Path(config['AppRoot']).resolve()
    if not (app / 'manage.py').is_file():raise RuntimeError('기존 ERP 설치 경로를 확인하세요.')
    env = {k: v for k, v in os.environ.items() if not k.startswith('ERP_')}
    for key, value in config['Environment'].items():
        if not re.fullmatch(r'ERP_[A-Z0-9_]+', key) or key in ('ERP_SECRET_KEY', 'ERP_DB_PASSWORD'):
            raise RuntimeError('기존 로컬 환경 설정을 확인하세요.')
        env[key] = str(value)
    if not env.get('ERP_DB_NAME') or not env.get('ERP_DB_USER'):
        raise RuntimeError('운영 PostgreSQL 설정이 없어 중단합니다. SQLite로 대체하지 않습니다.')
    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    env.update(SUNIL_CONFIG_PATH=str(config_path), PSModulePath=str(powershell.parent / 'Modules'))
    code = "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); Import-Module Microsoft.PowerShell.Security; $c=Get-Content -LiteralPath $env:SUNIL_CONFIG_PATH -Raw|ConvertFrom-Json; $v=Import-Clixml -LiteralPath $c.SecretsPath; $sid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value; if($v.OwnerSid -ne $sid){throw 'Wrong secret owner'}; $r=@{}; foreach($n in @('ERP_SECRET_KEY','ERP_DB_PASSWORD')){$r[$n]=[Management.Automation.PSCredential]::new('unused',$v.Secrets[$n]).GetNetworkCredential().Password}; $r|ConvertTo-Json -Compress"
    proc = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-Command', code],
                          env=env, capture_output=True, encoding='utf-8', timeout=30)
    if proc.returncode:raise RuntimeError('기존 비밀키 소유 Windows 계정인지 확인하세요. 비밀값은 출력하지 않습니다.')
    secrets = json.loads(proc.stdout)
    if set(secrets) != {'ERP_SECRET_KEY', 'ERP_DB_PASSWORD'} or not all(isinstance(v, str) and v for v in secrets.values()):
        raise RuntimeError('기존 비밀키를 읽지 못했습니다.')
    for key in list(os.environ):
        if key.startswith('ERP_'):os.environ.pop(key)
    os.environ.update({k: v for k, v in env.items() if k.startswith('ERP_')})
    os.environ.update(secrets)
    os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
    sys.path.insert(0, str(app))
    os.chdir(app)
    return app, Path(config['DataRoot']).resolve()


def collect_checked(root):
    def accept_input(warehouse):
        name = '영주제품창고' if warehouse == 'Y02' else '부산창고'
        print(f'Actas {warehouse}·{name}를 직접 확인하세요. 아직 조회 전입니다.', flush=True)
        return pilot.confirm_token(warehouse + ' INPUT', f'일치하면 {warehouse} input, 중단은 Enter: ')
    def prepare_query(warehouse):
        print('12초 안에 Actas 창고 코드 칸을 다시 클릭하고 손을 떼세요. 이후 도구가 조회합니다.', flush=True)
        time.sleep(12)
    def accept_query(warehouse):
        print(f'{warehouse} 코드·명칭 유지와 새 재고 표의 조회 완료를 확인하세요. 이전 표/조회 중이면 입력하지 마세요.', flush=True)
        return pilot.confirm_token(warehouse, f'조회 완료 확인은 {warehouse}, 중단은 Enter: ')
    with pilot.single_pilot():
        adapter = pilot.OperatorCheckedExport(pilot.WindowsActas(), pilot.prepare_export_input, accept_input, prepare_query)
        return pilot.run_check(adapter, root, accept_query, progress=lambda value: print(value, flush=True), reader=pilot.wait_file)


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='Actas 검증 파일 → SUNIL ERP 대조 등록 및 결과 기록')
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--result', type=Path, help='v5 result.json 전체 경로 · Actas 재수집 없음')
    mode.add_argument('--select-result', action='store_true', help='검증 완료 result.json 선택 창')
    mode.add_argument('--collect', action='store_true', help='v5 방식 수집 후 자동 등록')
    parser.add_argument('--config', default=r'C:\ProgramData\SUNIL\runtime.local.json')
    parser.add_argument('--output-root', type=Path)
    args = parser.parse_args()
    result_path = args.result.resolve() if args.result else None
    root = args.output_root.resolve() if args.output_root else None
    folder = result_path.parent if result_path else None
    try:
        if args.select_result:
            import tkinter as tk
            from tkinter import filedialog
            window = tk.Tk();window.withdraw();window.attributes('-topmost', True)
            try:
                chosen = filedialog.askopenfilename(parent=window, title='v5 실행 폴더의 result.json 선택',
                    initialdir=r'C:\SUNIL\updates\actas-export-check-v5\runs', filetypes=[('result.json', 'result.json')])
            finally:window.destroy()
            if not chosen:return 1
            result_path = Path(chosen).resolve()
            folder = result_path.parent
        # Validate source bytes without loading runtime credentials or DB first.
        if result_path:
            if result_path.name != 'result.json':raise RuntimeError('실행 폴더의 result.json을 선택하세요.')
            checked_source(result_path.parent)
        _, data_root = runtime_environment(args.config)
        root = root or data_root / 'actas' / 'verified-runs'
        import django
        django.setup()
        from django.contrib.auth import authenticate
        from django.db import connection
        from inventory.menu_permissions import require_menu_write
        if connection.vendor != 'postgresql':raise RuntimeError('운영 PostgreSQL 연결이 아니므로 중단합니다.')
        print('SUNIL ERP 대조 자료만 등록합니다. 관리 재고·예약·설정·프로그램 파일은 변경하지 않습니다.')
        actor = authenticate(username=input('SUNIL ERP 계정: ').strip(), password=getpass.getpass('비밀번호 (표시·저장 안 함): '))
        if actor is None:raise RuntimeError('SUNIL ERP 계정·비밀번호를 확인하세요.')
        require_menu_write(actor, 'management')
        if args.collect:
            print('검색·분류 조건을 전체로 두세요. 창고명과 조회 완료 확인은 계속 필요합니다.')
            if not pilot.confirm_token('START', '수집 후 대조 등록 시작은 start, 중단은 Enter: '):return 1
            folder, _ = collect_checked(root)
        else:folder = result_path.parent
        print('수집 원본 기록:', folder / 'result.json', flush=True)
        result = register_result(folder, actor=actor)
        print('대조 등록 완료 · 번호:', result['snapshot_id'])
        print('현재 ERP 주소 뒤에 다음 경로를 붙여 확인하세요:', result['detail_path'])
        print('결과 기록:', folder / 'registration.json')
        print(f"Y02 {result['counts']['Y02']}품목 / Y09 {result['counts']['Y09']}품목")
        summary = result['comparison']['Y09']
        print(f"Y09 대조: 일치 {summary['matched']} / 차이 {summary['different']} / ERP 누락 {summary['missing_in_erp']}")
        return 0
    except Exception as error:
        from django.core.exceptions import ValidationError
        message = str(error) if isinstance(error, (RuntimeError, ValidationError)) else type(error).__name__
        print('등록 중단:', message)
        if folder:
            print('새로 수집하지 말고 같은 result.json으로 등록을 재확인하세요:', folder / 'result.json')
            print('registration.json이 생성됐다면 함께 전달하세요.')
        else:print('계정·실행 환경을 확인하세요. 원본 파일과 설정은 그대로 보존하세요.')
        return 1


if __name__ == '__main__':raise SystemExit(main())
