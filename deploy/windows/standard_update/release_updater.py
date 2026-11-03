"""Transparent Windows code updater: explicit commands, additive DB changes only.

No downloaded executables, elevation, execution-policy changes, AV exclusions,
registry changes, local settings replacement, DB reset or DB restoration.
"""
import argparse
from contextlib import contextmanager
import ctypes
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import uuid

VAULT_COMMAND = "$c=Get-Content -LiteralPath $env:SUNIL_CONFIG_PATH -Raw|ConvertFrom-Json; $v=Import-Clixml -LiteralPath $c.SecretsPath; $s=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value; if($v.OwnerSid -ne $s){throw 'Run as the original secret owner'}; $r=@{sid=$s}; foreach($n in @('ERP_SECRET_KEY','ERP_DB_PASSWORD')){$r[$n]=[Management.Automation.PSCredential]::new('unused',$v.Secrets[$n]).GetNetworkCredential().Password}; $r|ConvertTo-Json -Compress"

TERMINAL = {'complete', 'rolled_back', 'no_changes'}
ALLOWED_MIGRATIONS = {('inventory', '0012_production_plan'), ('inventory', '0013_employee_menu_access')}


def digest(path):
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def no_links(path):
    current = Path(path).absolute()
    for part in (current, *current.parents):
        if part.exists() and (part.is_symlink() or getattr(part, 'is_junction', lambda: False)()):
            raise RuntimeError('연결된 경로는 자동 업데이트하지 않습니다: ' + str(part))


def code_path(name):
    p = PurePosixPath(name)
    if not name or '\\' in name or ':' in name or p.is_absolute() or any(x in ('', '.', '..') for x in name.split('/')):
        raise RuntimeError('허용되지 않은 파일 경로입니다.')
    allowed = (name.startswith(('config/', 'inventory/', 'docs/manual/'))
               or name in ('manage.py', 'requirements.txt', 'scripts/update_snapshot.py',
                           'deploy/windows/Common.ps1', 'deploy/windows/Run-Erp.ps1'))
    if not allowed or p.suffix.lower() in ('.exe', '.dll', '.clixml', '.dump', '.sqlite3', '.log', '.pyc', '.docx'):
        raise RuntimeError('운영 설정·데이터 또는 허용되지 않은 파일은 반영하지 않습니다: ' + name)
    if any(part.startswith(('runtime.', 'secrets.')) or part in ('.env', '.venv', '__pycache__') for part in p.parts):
        raise RuntimeError('운영 파일은 배포할 수 없습니다.')
    return name


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.sunil-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def atomic_copy(source, target):
    source, target = Path(source), Path(target)
    no_links(source); no_links(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.sunil-', dir=target.parent)
    os.close(fd)
    try:
        shutil.copyfile(source, temporary)
        if digest(temporary) != digest(source): raise RuntimeError('복사 파일의 체크섬이 다릅니다.')
        with open(temporary, 'r+b') as stream: os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


@contextmanager
def exclusive_lock(path):
    path = Path(path); no_links(path); path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open('a+b')
    stream.seek(0, 2)
    if not stream.tell(): stream.write(b'0'); stream.flush()
    stream.seek(0)
    try:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        stream.close(); raise RuntimeError('다른 업데이트 작업이 실행 중입니다.') from None
    try: yield
    finally:
        if os.name == 'nt':
            import msvcrt
            stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        stream.close()


def validate_payload(release, app, manifest, installed=True):
    if manifest.get('format') != 3 or manifest.get('target_migration') != '0013_employee_menu_access':
        raise RuntimeError('이 업데이트의 배포 명세가 아닙니다.')
    expected_helpers = {'Update-Erp.py', 'release_updater.py', 'release_probe.py', 'update_snapshot.py'}
    if set(manifest.get('helpers', {})) != expected_helpers:
        raise RuntimeError('업데이트 도구 파일 목록이 예상과 다릅니다.')
    for name, sha in manifest['helpers'].items():
        no_links(Path(release)/name)
        if digest(Path(release)/name) != sha: raise RuntimeError('업데이트 도구 파일이 손상됐습니다: ' + name)
    seen = set()
    if not 1 <= len(manifest['files']) <= 1000: raise RuntimeError('배포 파일 수가 예상과 다릅니다.')
    for entry in manifest['files']:
        name = code_path(entry['path'])
        if name.casefold() in seen: raise RuntimeError('중복 배포 경로입니다.')
        seen.add(name.casefold())
        source, target = Path(release)/'app'/name, Path(app)/name
        no_links(source); no_links(target)
        if digest(source) != entry['sha256']: raise RuntimeError('배포 파일이 손상되었습니다: ' + name)
        if installed:
            if target.exists():
                if not target.is_file() or digest(target) not in [entry['sha256'], *entry['accepted_previous_sha256']]:
                    raise RuntimeError('서버의 별도 수정 파일을 덮어쓰지 않습니다: ' + name)
            elif entry['required_existing']:
                raise RuntimeError('기존 프로그램 파일이 없습니다: ' + name)


def install_files(release, app, run, manifest, journal):
    validate_payload(release, app, manifest)
    rows = []
    for entry in manifest['files']:
        target = Path(app)/entry['path']; exists = target.is_file()
        old = digest(target) if exists else None
        if exists:
            saved = Path(run)/'old-files'/entry['path']
            atomic_copy(target, saved)
            if digest(saved) != old: raise RuntimeError('기존 코드가 복구본 생성 중 변경됐습니다.')
        rows.append({'path': entry['path'], 'existed': exists, 'old': old, 'new': entry['sha256']})
    atomic_json(Path(run)/'rollback.json', {'app_root': str(Path(app).absolute()), 'files': rows})
    journal['rollback_ready'] = True
    atomic_json(Path(run)/'journal.json', journal)
    for entry, row in zip(manifest['files'], rows):
        target = Path(app)/entry['path']
        if target.exists() and digest(target) != row['old']:
            raise RuntimeError('반영 직전에 기존 코드가 변경됐습니다: ' + entry['path'])
        if not target.exists() and row['existed']:
            raise RuntimeError('반영 직전에 기존 코드가 없어졌습니다: ' + entry['path'])
        atomic_copy(Path(release)/'app'/entry['path'], target)


def rollback_rows(app, run, manifest):
    data = json.loads((Path(run)/'rollback.json').read_text(encoding='utf-8'))
    if os.path.normcase(os.path.abspath(data['app_root'])) != os.path.normcase(os.path.abspath(app)):
        raise RuntimeError('복구 대상 설치 경로가 다릅니다.')
    allowed = {e['path']: e for e in manifest['files']}
    if len(data['files']) != len(allowed) or {r['path'] for r in data['files']} != set(allowed):
        raise RuntimeError('복구 대상 파일 목록이 다릅니다.')
    for row in data['files']:
        name = code_path(row['path']); target = Path(app)/name
        entry = allowed[name]
        if row['new'] != entry['sha256'] or (row['old'] is not None and row['old'] not in [entry['sha256'], *entry['accepted_previous_sha256']]):
            raise RuntimeError('복구 기록의 체크섬이 허용된 기준과 다릅니다.')
        no_links(target)
        if target.exists() and digest(target) not in (row['old'], row['new']):
            raise RuntimeError('업데이트 후 별도 수정된 파일은 복구하지 않습니다: ' + name)
        if row['existed']:
            source = Path(run)/'old-files'/name; no_links(source)
            if digest(source) != row['old']: raise RuntimeError('기존 코드 복구본이 손상되었습니다.')
    return data['files']


def original_files_restored(app, run, manifest):
    rows = rollback_rows(app, run, manifest)
    return all((Path(app)/row['path']).is_file() and digest(Path(app)/row['path']) == row['old']
               if row['existed'] else not (Path(app)/row['path']).exists() for row in rows)


def restore_files(app, run, manifest):
    for row in rollback_rows(app, run, manifest):
        target = Path(app)/row['path']
        if row['existed']: atomic_copy(Path(run)/'old-files'/row['path'], target)
        elif target.exists(): target.unlink()


def preserve_fingerprint(before, after):
    # Additive tables (plan and menu grants) are expected; existing rows/accounts
    # must remain identical. Never ignore changes in a previously present model.
    if any(after.get(key) != value for key, value in before.items()):
        raise RuntimeError('기존 업무 데이터 또는 계정 비교값이 달라졌습니다. DB는 자동 복원하지 않습니다.')


def recover(ops, run, manifest, journal):
    ops.check_protected(journal['protected'])
    if hasattr(ops, 'select_web'): ops.select_web(journal.get('web'))
    if journal['rollback_ready']:
        ops.maintenance(journal['tasks']); ops.stop()
        restore_files(ops.app, run, manifest)
        ops.validate(old=True)
    ops.start(journal['tasks']); ops.health(old=True)
    ops.restore_schedule(journal['tasks'])
    journal['phase'] = 'rolled_back'
    atomic_json(Path(run)/'journal.json', journal)



def reconcile(ops, run, manifest, journal):
    """Finalize a verified, already restored v2 run without stopping ERP or writing DB."""
    if journal['release'] != 'SUNIL-ERP-20261104-integrated-v2' or journal['phase'] != 'recovery_required' or not journal['rollback_ready']:
        raise RuntimeError('이미 원복된 v2 미완료 기록만 확인할 수 있습니다.')
    ops.check_protected(journal['protected'])
    ops.select_web(journal['web'])
    if not original_files_restored(ops.app, run, manifest):
        raise RuntimeError('기존 코드 원복이 완전하지 않습니다. 서버를 변경하지 않고 중단합니다.')
    recorded = {t['name']: t for t in journal['tasks']}
    current = {t['name']: t for t in ops.tasks}
    if set(recorded) != set(current):
        raise RuntimeError('예약 작업 목록이 기존 기록과 다릅니다.')
    for name, task in recorded.items():
        if task['path'] != current[name]['path'] or task['actions'] != current[name]['actions']:
            raise RuntimeError('예약 작업 정의가 기존 기록과 다릅니다.')
        if name in ('SUNIL-ERP', 'SUNIL-Caddy') and not (current[name]['enabled'] and current[name]['running']):
            raise RuntimeError('기존 ERP와 Caddy가 정상 실행 중일 때만 확인할 수 있습니다.')
    if current['SUNIL-LocalBackup']['running']:
        raise RuntimeError('정시 백업이 진행 중입니다. 완료 후 확인하세요.')
    ops.health(old=True)
    ops.check_protected(journal['protected'])
    ops.restore_schedule(journal['tasks'])
    journal['phase'] = 'rolled_back'
    journal['reconciled_by'] = manifest['release']
    atomic_json(Path(run)/'journal.json', journal)

def apply(ops, release, run, manifest):
    validate_payload(release, ops.app, manifest)
    journal = {'format': 1, 'release': manifest['release'], 'owner_sid': ops.sid, 'app_root': str(ops.app),
               'phase': 'prepared', 'rollback_ready': False, 'controlled': False,
               'tasks': ops.tasks, 'protected': ops.protected, 'backups': [], 'error': '', 'web': getattr(ops, 'web', '')}
    atomic_json(Path(run)/'journal.json', journal)
    try:
        ops.check_protected(journal['protected'])
        journal['controlled'] = True; atomic_json(Path(run)/'journal.json', journal)
        ops.maintenance(journal['tasks'])
        print('1/7 서버 중지 전 백업과 체크섬을 확인합니다.', flush=True)
        journal['backups'].append(ops.backup('before-stop'))
        atomic_json(Path(run)/'journal.json', journal)
        print('2/7 ERP만 중지합니다. Caddy와 PostgreSQL은 유지합니다.', flush=True)
        ops.stop(); journal['phase'] = 'stopped'; atomic_json(Path(run)/'journal.json', journal)
        print('3/7 중지 시점의 최신 DB 백업과 기존 데이터 비교값을 기록합니다.', flush=True)
        journal['backups'].append(ops.backup('stopped'))
        journal['fingerprint'] = ops.fingerprint()
        atomic_json(Path(run)/'journal.json', journal)
        print('4/7 코드 복구본을 만들고 새 프로그램을 반영합니다.', flush=True)
        install_files(release, ops.app, run, manifest, journal)
        journal['phase'] = 'files_installed'; atomic_json(Path(run)/'journal.json', journal)
        print('5/7 허용된 추가 마이그레이션만 적용하고 정적 파일을 수집합니다.', flush=True)
        ops.migrate(); ops.validate()
        preserve_fingerprint(journal['fingerprint'], ops.fingerprint())
        ops.check_protected(journal['protected'])
        print('6/7 기존 예약 작업으로 재시작하고 웹·로그인 후 화면을 확인합니다.', flush=True)
        ops.start(journal['tasks']); ops.health()
        journal['phase'] = 'web_verified'; atomic_json(Path(run)/'journal.json', journal)
        print('7/7 업데이트 후 백업을 만들고 정시 백업 설정을 복구합니다.', flush=True)
        warning = False
        try: journal['backups'].append(ops.backup('after-update'))
        except Exception: warning = True
        ops.restore_schedule(journal['tasks'])
        journal['phase'] = 'complete'; atomic_json(Path(run)/'journal.json', journal)
        if warning:
            print('주의: 업데이트는 완료됐지만 마지막 백업을 확인해야 합니다. 중지 시점의 검증된 백업은 남아 있습니다.')
            return 2
        print('업데이트와 백업 검증 완료. 브라우저를 새로고침해 업무 화면을 확인하세요.')
        return 0
    except Exception as error:
        journal['error'] = str(error); atomic_json(Path(run)/'journal.json', journal)
        print('업데이트 중단: ' + str(error), flush=True)
        if journal['controlled']:
            try:
                recover(ops, run, manifest, journal)
                print('기존 코드와 예약 작업으로 복구했습니다. DB는 덮어쓰지 않았습니다.')
                return 10
            except Exception as recovery_error:
                journal['phase'] = 'recovery_required'; journal['recovery_error'] = str(recovery_error)
                atomic_json(Path(run)/'journal.json', journal)
                print('자동 복구를 마치지 못했습니다. 반복 적용·DB 복원 없이 로그 폴더를 전달해 주세요.')
                return 20
        return 1


class WindowsOps:
    def __init__(self, release, config_path, run, recovery=False):
        if os.name != 'nt' or sys.version_info[:2] != (3,12): raise RuntimeError('Windows 서버의 기존 Python 3.12로 실행하세요.')
        if not ctypes.windll.shell32.IsUserAnAdmin(): raise RuntimeError('평소 Windows 계정의 관리자 PowerShell에서 실행하세요. 다른 계정으로 바꾸지 마세요.')
        self.release, self.run = Path(release), Path(run)
        self.config_path = Path(config_path).absolute(); no_links(self.config_path)
        self.config = json.loads(self.config_path.read_text(encoding='utf-8-sig'))
        self.app = Path(self.config['AppRoot']).absolute()
        if os.path.normcase(str(self.app)) != os.path.normcase(r'C:\SUNIL\ERP'): raise RuntimeError('기존 설치 위치가 예상과 다릅니다.')
        if self.config['Listen'] != '127.0.0.1:8000': raise RuntimeError('기존 ERP 포트 설정이 예상과 다릅니다.')
        if os.path.normcase(os.path.abspath(self.config['PythonExe'])) != os.path.normcase(os.path.abspath(sys.executable)):
            raise RuntimeError('기존 가상환경의 python.exe로 실행하세요.')
        for p in (self.app,self.release,self.config['DataRoot'],self.config['BackupDirectory'],self.config['SecretsPath']): no_links(p)
        self.powershell = str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe')
        self.env = os.environ.copy()
        self.env.update(PYTHONPATH=str(self.app), PYTHONIOENCODING='utf-8', PYTHONUTF8='1')
        for key, value in self.config['Environment'].items():
            if not re.fullmatch(r'ERP_[A-Z0-9_]+', key) or key in ('ERP_SECRET_KEY','ERP_DB_PASSWORD'): raise RuntimeError('로컬 설정의 환경 변수 이름을 확인하세요.')
            self.env[key] = str(value)
        if self.env.get('ERP_DEBUG') != '1': raise RuntimeError('기존 내부 HTTP·DEBUG=1 설정과 다릅니다. 변경하지 않고 중단합니다.')
        vault = self.ps(VAULT_COMMAND, secret=True)
        self.sid=vault.pop('sid'); self.env.update(vault)
        self.tasks=self.ps("@('SUNIL-ERP','SUNIL-Caddy','SUNIL-LocalBackup')|ForEach-Object{$a=@(Get-ScheduledTask -TaskName $_ -ErrorAction Stop); if($a.Count -ne 1){throw 'Task is missing or ambiguous'}; $t=$a[0]; [pscustomobject]@{name=$t.TaskName;path=$t.TaskPath;enabled=[bool]$t.Settings.Enabled;running=([string]$t.State -eq 'Running');actions=@($t.Actions|Select-Object Execute,Arguments,WorkingDirectory)}}|ConvertTo-Json -Depth 6 -Compress")
        if any(t['path'] != '\\' for t in self.tasks) or {t['name'] for t in self.tasks} != {'SUNIL-ERP','SUNIL-Caddy','SUNIL-LocalBackup'}: raise RuntimeError('예약 작업 구성이 다릅니다.')
        if not recovery:
            for task in self.tasks:
                if task['name'] in ('SUNIL-ERP','SUNIL-Caddy') and not (task['enabled'] and task['running']): raise RuntimeError('기존 ERP와 Caddy가 정상 실행 중일 때 업데이트하세요.')
                if task['name']=='SUNIL-LocalBackup' and task['running']: raise RuntimeError('정시 백업이 진행 중입니다. 완료 후 다시 점검하세요.')
        caddy=next(t for t in self.tasks if t['name']=='SUNIL-Caddy')['actions']
        if len(caddy)!=1: raise RuntimeError('Caddy 동작을 확인할 수 없습니다.')
        match=re.search(r'(?:^|\s)--config\s+(?:"([^"]+)"|(\S+))',caddy[0]['Arguments'],re.I)
        if not match: raise RuntimeError('실제 Caddy 설정 경로를 찾지 못했습니다.')
        caddy_path=Path(match.group(1) or match.group(2))
        if not caddy_path.is_absolute(): caddy_path=Path(caddy[0]['WorkingDirectory'])/caddy_path
        self.protected={str(p):digest(p) for p in (self.config_path,Path(self.config['SecretsPath']),caddy_path)}
        for path in self.protected: no_links(path)
        self.pg=Path(self.config.get('PgBin') or r'C:\Program Files\PostgreSQL\18\bin')
        if not (self.pg/'pg_dump.exe').is_file(): raise RuntimeError('PostgreSQL 18 백업 도구가 없습니다.')
        version=self.command([str(self.pg/'pg_dump.exe'),'--version'],'pg-version')
        if not re.search(r'\b18\.',version): raise RuntimeError('PostgreSQL 18 백업 도구가 아닙니다.')
        self.captured=self.process()
        if self.captured: self.check_process(self.captured)
        if not recovery and self.captured is None: raise RuntimeError('기존 ERP 8000 포트가 열려 있지 않습니다.')
        addresses=self.ps("@(Get-NetTCPConnection -State Listen -ErrorAction Stop|Where-Object LocalPort -eq 8080|Select-Object -ExpandProperty LocalAddress -Unique)|ConvertTo-Json -Compress") or []
        if isinstance(addresses,str): addresses=[addresses]
        self.web_candidates=['http://'+a+':8080' for a in addresses if ':' not in a and a not in ('0.0.0.0',)]
        self.web_candidates=list(dict.fromkeys([*self.web_candidates,'http://127.0.0.1:8080']))
        self.web=self.web_candidates[0]
        if not recovery:
            profile=self.probe('profile')
            if (profile['vendor'],profile['name'],profile['user']) != ('postgresql','sunil','sunil'): raise RuntimeError('기존 PostgreSQL sunil DB/사용자가 아닙니다.')
            if profile['host'] not in ('localhost','127.0.0.1','::1',''): raise RuntimeError('이 배포는 기존 로컬 PostgreSQL 서버용입니다. 외부 DB는 변경하지 않습니다.')
            need=int(profile['size'])*4+128*1024*1024
            if shutil.disk_usage(self.config['BackupDirectory']).free < need: raise RuntimeError('DB 백업을 위한 여유 공간이 부족합니다.')
            self.command([sys.executable,'manage.py','check'],'old-check')
            self.command([sys.executable,'manage.py','migrate','--check'],'old-migration-check')
            for candidate in self.web_candidates:
                self.web=candidate
                try: self.http('/login/');break
                except Exception: continue
            else: raise RuntimeError('기존 Caddy 로그인 화면에 접속할 수 없습니다.')

    def select_web(self,value):
        if value not in self.web_candidates: raise RuntimeError('복구 기록의 웹 주소가 현재 Caddy 주소와 다릅니다.')
        self.web=value

    def command(self, args, label, timeout=180, env=None, secret=False):
        failure = None
        child_env = {**(env or self.env), 'PYTHONIOENCODING':'utf-8', 'PYTHONUTF8':'1'}
        with subprocess.Popen(args,cwd=self.app,env=child_env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                              text=True,encoding='utf-8',start_new_session=os.name!='nt') as process:
            deadline = time.monotonic() + timeout
            while True:
                try:
                    stdout, stderr = process.communicate(timeout=min(30, max(.01, deadline-time.monotonic())))
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() < deadline:
                        if not secret: print('작업 진행 중입니다. 완료될 때까지 기다려 주세요: '+label,flush=True)
                        continue
                    if os.name == 'nt' and process.poll() is None:
                        subprocess.run([str(Path(os.environ['SystemRoot'])/'System32/taskkill.exe'),'/PID',str(process.pid),'/T','/F'],
                                       capture_output=True,check=False)
                    elif process.poll() is None:
                        import signal
                        os.killpg(process.pid, signal.SIGKILL)
                    stdout, stderr = process.communicate(timeout=30)
                    failure = label+' 시간이 초과되어 해당 자식 작업만 중단했습니다.'
                    break
            if not secret:
                self.run.mkdir(parents=True,exist_ok=True)
                (self.run/(label+'.stdout.log')).write_text(stdout,encoding='utf-8')
                (self.run/(label+'.stderr.log')).write_text(stderr,encoding='utf-8')
            if failure: raise RuntimeError(failure)
            if process.returncode: raise RuntimeError(label+' 실패. 종료 코드 '+str(process.returncode)+'. 로그를 확인하세요.')
            return stdout

    def ps(self, code, *, values=None, secret=False):
        env={key:value for key,value in self.env.items() if key not in ('ERP_SECRET_KEY','ERP_DB_PASSWORD')}
        env.update(SUNIL_CONFIG_PATH=str(self.config_path),SUNIL_VALUES=json.dumps(values or {}))
        # Windows PowerShell 5.1 must not autoload PowerShell 7 modules inherited
        # from the caller (including GitHub Actions' pwsh host). Process only.
        env['PSModulePath'] = str(Path(self.powershell).parent/'Modules')
        preamble="$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); Import-Module Microsoft.PowerShell.Security -ErrorAction Stop; "
        output=self.command([self.powershell,'-NoProfile','-NonInteractive','-Command',preamble+code], 'windows-command', env=env, secret=secret)
        return json.loads(output) if output.strip() else None

    def process(self):
        rows=self.ps("$r=@(Get-NetTCPConnection -State Listen -ErrorAction Stop|Where-Object LocalPort -eq 8000); if(@($r|Where-Object LocalAddress -ne '127.0.0.1').Count){throw 'Unexpected listener'}; $ids=@($r|Select-Object -ExpandProperty OwningProcess -Unique); if($ids.Count -gt 1){throw 'Ambiguous process'}; if($ids.Count){Get-CimInstance Win32_Process -Filter ('ProcessId='+$ids[0])|Select-Object ProcessId,@{Name='CreationDate';Expression={'ticks:'+$_.CreationDate.ToUniversalTime().Ticks.ToString()}},ExecutablePath,CommandLine|ConvertTo-Json -Compress}")
        return rows

    def check_process(self, row, captured=False):
        allowed={os.path.normcase(os.path.abspath(p)) for p in (sys.executable,getattr(sys,'_base_executable',sys.executable))}
        command=row.get('CommandLine') or ''
        if os.path.normcase(os.path.abspath(row.get('ExecutablePath') or '')) not in allowed or not re.search(r'-m\s+waitress\b',command) or not re.search(r'--listen=127\.0\.0\.1:8000(?:\s|$)',command) or 'config.wsgi:application' not in command:
            raise RuntimeError('8000 포트의 프로세스가 기존 ERP와 다릅니다. 종료하지 않습니다.')
        if captured and (self.captured is None or (row['ProcessId'],row['CreationDate'])!=(self.captured['ProcessId'],self.captured['CreationDate'])):
            raise RuntimeError('ERP 프로세스가 바뀌었습니다. 자동 종료하지 않습니다.')

    def check_protected(self, protected):
        for path, sha in protected.items():
            no_links(path)
            if digest(path)!=sha: raise RuntimeError('기존 설정·비밀키·Caddy 설정이 변경됐습니다. 덮어쓰지 않습니다.')

    def maintenance(self, tasks):
        self.ps("foreach($n in @('SUNIL-ERP','SUNIL-LocalBackup')){Disable-ScheduledTask -TaskName $n -ErrorAction Stop|Out-Null}; if([string](Get-ScheduledTask -TaskName 'SUNIL-LocalBackup').State -eq 'Running'){throw 'Backup is running; do not stop it'}")

    def stop(self):
        current=self.process()
        if current is None: return
        self.check_process(current,captured=True)
        self.ps("Stop-ScheduledTask -TaskName 'SUNIL-ERP' -ErrorAction Stop")
        for _ in range(5):
            if self.process() is None: return
            time.sleep(1)
        current=self.process()
        if current is None: return
        self.check_process(current,captured=True)
        self.ps("$v=$env:SUNIL_VALUES|ConvertFrom-Json; $p=Get-CimInstance Win32_Process -Filter ('ProcessId='+$v.pid); if($p -and ('ticks:'+$p.CreationDate.ToUniversalTime().Ticks.ToString()) -eq $v.created){Stop-Process -Id $v.pid -ErrorAction Stop}else{throw 'Process changed'}",values={'pid':current['ProcessId'],'created':str(current['CreationDate'])})
        time.sleep(1)
        if self.process() is not None: raise RuntimeError('ERP가 중지되지 않았습니다.')

    def restore_schedule(self,tasks):
        selected=[t for t in tasks if t['name'] in ('SUNIL-ERP','SUNIL-LocalBackup')]
        if not selected:
            return
        if any(not isinstance(t.get('name'), str) or not isinstance(t.get('path'), str)
               or type(t.get('enabled')) is not bool for t in selected):
            raise RuntimeError('예약 작업 복구 값의 형식이 올바르지 않습니다.')
        # PS 5.1 emits a JSON top-level array as ONE pipeline object. Wrapping
        # ConvertFrom-Json in @() creates a nested array: $t.name becomes string[].
        # A named envelope avoids pipeline enumeration differences in all hosts.
        result = self.ps("$v=$env:SUNIL_VALUES|ConvertFrom-Json; $rows=@(foreach($t in $v.tasks){$name=[string]$t.name; $path=[string]$t.path; if($t.enabled){Enable-ScheduledTask -TaskName $name -TaskPath $path -ErrorAction Stop|Out-Null}else{Disable-ScheduledTask -TaskName $name -TaskPath $path -ErrorAction Stop|Out-Null}; $actual=Get-ScheduledTask -TaskName $name -TaskPath $path -ErrorAction Stop; [pscustomobject]@{name=[string]$actual.TaskName;path=[string]$actual.TaskPath;enabled=[bool]$actual.Settings.Enabled}}); $rows|ConvertTo-Json -Compress", values={'tasks': selected})
        if isinstance(result, dict):
            result = [result]
        expected = {(t['name'], t['path']): t['enabled'] for t in selected}
        if not isinstance(result, list) or len(result) != len(selected) or {
                (t['name'], t['path']): t['enabled'] for t in result} != expected:
            raise RuntimeError('예약 작업 활성 상태가 기존 기록과 다릅니다.')

    def start(self,tasks):
        erp=next(t for t in tasks if t['name']=='SUNIL-ERP')
        self.restore_schedule([erp])
        if erp['running']:
            self.ps("if([string](Get-ScheduledTask -TaskName 'SUNIL-ERP').State -ne 'Running'){Start-ScheduledTask -TaskName 'SUNIL-ERP' -ErrorAction Stop}")
        for _ in range(40):
            current=self.process()
            if current:
                self.check_process(current);self.captured=current;return
            time.sleep(1)
        raise RuntimeError('ERP 재시작 후 포트가 열리지 않았습니다.')

    def probe(self,mode,*args):
        output=self.command([sys.executable,str(self.release/'release_probe.py'),mode,*map(str,args)],'probe-'+mode,600)
        return json.loads(output)

    def backup(self,label):
        result=self.probe('backup',self.config['BackupDirectory'],self.pg)
        path=Path(result['path']).absolute();root=Path(self.config['BackupDirectory']).absolute()
        if not path.is_relative_to(root) or path==root: raise RuntimeError('백업 결과 경로가 지정된 폴더 밖입니다.')
        no_links(path)
        data=json.loads((path/'manifest.json').read_text())
        if data['vendor']!='postgresql' or data['database_file']!='database.dump' or digest(path/'database.dump')!=data['sha256']:
            raise RuntimeError('PostgreSQL 백업 체크섬 검증에 실패했습니다.')
        print('검증된 백업: '+str(path),flush=True)
        return str(path)

    def fingerprint(self):
        output=self.command([sys.executable,str(self.release/'release_probe.py'),'snapshot'],'data-snapshot',600)
        return json.loads(output.strip().removeprefix('SUNIL_SNAPSHOT='))

    def migrate(self):
        plan=self.probe('plan')
        if any(backwards or (app,name) not in ALLOWED_MIGRATIONS for app,name,backwards in plan):
            raise RuntimeError('0012·0013 외의 예상하지 않은 마이그레이션이 있습니다. DB 변경을 시작하지 않습니다.')
        self.command([sys.executable,'manage.py','migrate','--noinput'],'migrate',600)

    def validate(self,old=False):
        self.command([sys.executable,'manage.py','check'],'django-check')
        if not old:self.command([sys.executable,'manage.py','migrate','--check'],'migration-check')
        self.command([sys.executable,'manage.py','collectstatic','--noinput'],'collectstatic',600)

    def http(self,path,contains=None):
        with urllib.request.urlopen(self.web+path,timeout=10) as response:
            data=response.read().decode('utf-8')
            if response.status!=200 or (contains and contains not in data):raise RuntimeError('웹 화면 또는 정적 파일 확인에 실패했습니다.')

    def health(self,old=False):
        for attempt in range(10):
            try:
                self.http('/login/')
                if not old:
                    self.http('/static/inventory/production_plan.js','production-data')
                    self.http('/static/inventory/movement_notifications.js','movement-pending-notice')
                self.probe('health',*(['--old'] if old else []),'--host',self.web.removeprefix('http://'));return
            except Exception:
                if attempt==9:raise
                time.sleep(1)


def main(argv=None):
    parser=argparse.ArgumentParser(description='SUNIL ERP 통합 업데이트. 운영 설정과 DB를 덮어쓰지 않습니다.')
    parser.add_argument('mode',choices=('check','apply','recover','reconcile'))
    parser.add_argument('--config',default=r'C:\ProgramData\SUNIL\runtime.local.json')
    parser.add_argument('--work-paused',action='store_true')
    args=parser.parse_args(argv)
    release=Path(__file__).resolve().parent
    try:
        manifest=json.loads((release/'manifest.json').read_text(encoding='utf-8'))
        # Validate source before importing the installed app or reading the vault.
        config=json.loads(Path(args.config).read_text(encoding='utf-8-sig'))
        app=Path(config['AppRoot']).absolute()
        validate_payload(release,app,manifest,installed=args.mode not in ('recover','reconcile'))
        runs=Path(config['DataRoot'])/'standard-update-runs';no_links(runs)
        run=release/'logs'/uuid.uuid4().hex;run.mkdir(parents=True)
        with exclusive_lock(runs/'updater.lock'):
            pending=[]
            for p in runs.glob('*/journal.json'):
                no_links(p)
                if json.loads(p.read_text(encoding='utf-8'))['phase'] not in TERMINAL:pending.append(p.parent)
            ops=WindowsOps(release,args.config,run,recovery=args.mode in ('recover','reconcile') or bool(pending))
            if args.mode in ('recover','reconcile'):
                if len(pending)!=1:raise RuntimeError('미완료 기록이 한 개일 때만 자동 코드 복구를 실행합니다. 로그를 전달해 주세요.')
                run=pending[0];journal=json.loads((run/'journal.json').read_text(encoding='utf-8'))
                if journal['owner_sid']!=ops.sid or journal['release']!=(('SUNIL-ERP-20261104-integrated-v2') if args.mode=='reconcile' else manifest['release']) or os.path.normcase(os.path.abspath(journal['app_root']))!=os.path.normcase(os.path.abspath(ops.app)):
                    raise RuntimeError('같은 계정·설치·버전의 복구 기록이 아닙니다.')
                # Retain original failure logs; reconciliation has its own logs.
                ops.run=run/'reconcile-logs'/uuid.uuid4().hex if args.mode=='reconcile' else run
                if args.mode=='reconcile':
                    reconcile(ops,run,manifest,journal)
                    print('기존 코드·웹·예약 작업 확인 및 미완료 기록 정리 완료. ERP 중지·파일 교체·DB 변경은 하지 않았습니다.')
                else:
                    recover(ops,run,manifest,journal)
                    print('기존 코드·예약 작업 복구 완료. DB는 덮어쓰지 않았습니다.')
                return 0
            if pending:raise RuntimeError('미완료 기록이 있습니다. 이미 원복된 v2는 reconcile로 확인하세요. apply는 실행하지 마세요.')
            if all((app/e['path']).is_file() and digest(app/e['path'])==e['sha256'] for e in manifest['files']):
                print('이미 같은 통합 배포본이 적용되어 있습니다. 다시 설치하거나 서버를 중지하지 않습니다.');return 0
            if args.mode=='check':
                print('사전 점검 완료. 서버 중지·파일 교체·DB 변경은 하지 않았습니다.')
                print('직원들의 저장·처리를 잠시 멈춘 뒤 apply --work-paused를 실행하세요.');return 0
            if not args.work_paused:raise RuntimeError('직원들의 저장·처리를 잠시 멈춘 뒤 --work-paused를 지정하세요.')
            run=runs/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]);run.mkdir()
            ops.run=run
            print('업데이트 기록 폴더: '+str(run),flush=True)
            return apply(ops,release,run,manifest)
    except Exception as error:
        print('중단: '+str(error),file=sys.stderr);return 1
