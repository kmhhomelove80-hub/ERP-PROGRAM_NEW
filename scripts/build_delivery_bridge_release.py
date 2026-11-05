"""Build a v3-only bridge patch using the proven standard updater unchanged."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = 'SUNIL-ERP-20261105-bridge-v1'
HELPERS = ('Update-Erp.py', 'release_updater.py', 'release_probe.py')


def sha(data):return hashlib.sha256(data).hexdigest()


def build(output, evidence_path):
    evidence = json.loads(Path(evidence_path).read_text())
    if not all(evidence.get(k) is True for k in ('success', 'windows_native_success', 'windows_schedule_restore_success', 'postgresql18_success', 'browser_success')):
        raise RuntimeError('Windows·예약 작업 복구·PostgreSQL 18·브라우저 검사 완료 기록이 필요합니다.')
    guide = ROOT / 'deploy/windows/delivery-bridge-v3'
    baseline = json.loads((guide / 'baseline.json').read_text())
    stage = ROOT / 'work' / VERSION
    if stage.exists():shutil.rmtree(stage)
    (stage / 'app').mkdir(parents=True)
    entries = []
    for entry in baseline['files']:
        name = entry['path'];data = (ROOT / name).read_bytes()
        target = stage / 'app' / name;target.parent.mkdir(parents=True, exist_ok=True);target.write_bytes(data)
        entries.append(dict(path=name, sha256=sha(data), accepted_previous_sha256=[entry['sha256']] if entry['sha256'] else [], required_existing=entry['sha256'] is not None))
    for name in HELPERS:shutil.copyfile(ROOT / 'deploy/windows/standard_update' / name, stage / name)
    shutil.copyfile(ROOT / 'scripts/update_snapshot.py', stage / 'update_snapshot.py')
    manifest = dict(format=3, release=VERSION, source_commit=subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                    verified_ci_commit=evidence['commit'], target_migration='0013_employee_menu_access', files=entries,
                    helpers={name:sha((stage / name).read_bytes()) for name in (*HELPERS, 'update_snapshot.py')})
    (stage / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    (stage / 'VALIDATION.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    for name in ('README-FIRST.txt', 'UPDATE-STEPS.md', 'REVIEW.md'):shutil.copyfile(guide / name, stage / name)
    checks = ''.join(sha(p.read_bytes())+'  '+p.relative_to(stage).as_posix()+'\n' for p in sorted(stage.rglob('*')) if p.is_file())
    (stage / 'SHA256SUMS.txt').write_text(checks)
    output = Path(output);output.mkdir(parents=True, exist_ok=True)
    archive = output / (VERSION + '.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zipped:
        for p in sorted(stage.rglob('*')):
            if p.is_file():zipped.write(p, VERSION + '/' + p.relative_to(stage).as_posix())
    with zipfile.ZipFile(archive) as zipped:assert zipped.testzip() is None
    digest = sha(archive.read_bytes())
    archive.with_suffix('.zip.sha256').write_text(digest+'  '+archive.name+'\n')
    print(json.dumps(dict(zip=str(archive), bytes=archive.stat().st_size, sha256=digest, files=len(entries))))
    return stage, archive


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description='현재 v3용 수주 연동 수정본 생성')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ci-evidence', type=Path, required=True)
    args=parser.parse_args();build(args.output, args.ci_evidence)
