"""Create a code-only update ZIP. Requires recorded successful CI evidence.

Runtime configuration, secrets, databases, the held EXE/updater, internal reports,
editable Word documents, and Caddy/task definitions are never included.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parent.parent
VERSION='SUNIL-ERP-20261104-integrated-v3'
BASELINES=['1460a6c','038ba87','e679f94','1217807','fa2eb0e']
HELPERS=['Update-Erp.py','release_updater.py','release_probe.py']


def sha(data):return hashlib.sha256(data).hexdigest()


def build(destination,evidence_path):
    evidence=json.loads(Path(evidence_path).read_text(encoding='utf-8'))
    if not evidence.get('success') or not evidence.get('windows_native_success') or not evidence.get('windows_schedule_restore_success'):
        raise RuntimeError('성공한 Windows·통합 CI 검증 기록이 필요합니다.')
    stage=ROOT/'work'/VERSION
    if stage.exists():shutil.rmtree(stage)
    (stage/'app').mkdir(parents=True)
    paths=subprocess.check_output(['git','ls-files','-z'],cwd=ROOT).decode().split('\0')
    entries=[]
    for name in sorted(paths):
        if not name:continue
        include=(name.startswith(('config/','inventory/','docs/manual/')) or name in (
            'manage.py','requirements.txt','scripts/update_snapshot.py'))
        if not include:continue
        source=ROOT/name
        # The app renders the bundled HTML/PDF, not this editable source.
        # The November 3 artifact omitted the Korean-named MD; preserve whatever
        # source manuscript is already on the server rather than overwrite it.
        if name.startswith('docs/manual/') and source.suffix.lower()=='.md' and source.name!='README.md':continue
        if source.suffix.lower() in ('.docx','.pyc','.log','.exe','.dll','.xlsx','.xlsm','.dump','.sqlite3','.clixml'):continue
        if '__pycache__' in source.parts or source.name.startswith(('runtime.','secrets.')):raise RuntimeError('운영 파일이 배포 목록에 들어갔습니다.')
        data=source.read_bytes();target=stage/'app'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        old=set();required=False
        for ref in BASELINES:
            result=subprocess.run(['git','show',f'{ref}:{name}'],cwd=ROOT,capture_output=True)
            if result.returncode==0:
                old.add(sha(result.stdout))
                if ref=='1460a6c':required=True
        entries.append({'path':name,'sha256':sha(data),'accepted_previous_sha256':sorted(old),'required_existing':required})
    for name in HELPERS:shutil.copyfile(ROOT/'deploy/windows/standard_update'/name,stage/name)
    shutil.copyfile(ROOT/'scripts/update_snapshot.py',stage/'update_snapshot.py')
    helpers={name:sha((stage/name).read_bytes()) for name in [*HELPERS,'update_snapshot.py']}
    source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    manifest={'format':3,'release':VERSION,'source_commit':source_commit,'verified_ci_commit':evidence['commit'],
              'target_migration':'0013_employee_menu_access','files':entries,'helpers':helpers}
    (stage/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    (stage/'VALIDATION.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
    for source,target in [('docs/STANDARD_UPDATE_20261104.md','README.md'),('docs/RELEASE_20261104.md','RELEASE_NOTES.md')]:
        shutil.copyfile(ROOT/source,stage/target)
    text=(stage/'README.md').read_text(encoding='utf-8')
    import html
    try:
        import markdown
        body=markdown.markdown(text,extensions=['fenced_code','tables'])
    except ImportError:
        body='<pre>'+html.escape(text)+'</pre>'
    page='<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SUNIL ERP 통합 업데이트 안내</title><style>body{font:17px/1.75 "Malgun Gothic",sans-serif;max-width:900px;margin:30px auto;padding:0 18px;color:#152b46}pre{background:#edf2f8;padding:14px;white-space:pre-wrap;overflow-wrap:anywhere}h2{margin-top:35px}li{margin:10px 0}code{overflow-wrap:anywhere}@media print{body{font-size:12px}}</style>'+body+'</html>'
    (stage/'UPDATE_GUIDE.html').write_text(page,encoding='utf-8')
    quick='SUNIL ERP 통합 업데이트 — 2026-11-04\n\n1. 회사 ERP 서버에 기존 Windows 계정으로 로그인하세요.\n2. 이 폴더를 C:\\SUNIL\\updates\\SUNIL-ERP-20261104-integrated-v3 에 둡니다.\n3. UPDATE_GUIDE.html을 열어 같은 계정의 관리자 PowerShell에서 check부터 실행합니다.\n4. 통과하면 직원들의 처리를 잠시 멈추고 apply --work-paused를 실행합니다.\n\n보류한 EXE는 사용하지 않습니다. 백신을 끄거나 허용·제외 설정을 하지 마세요.\napp 폴더를 수동 덮어쓰기 하지 마세요. DB 초기화·재이관은 하지 않습니다.\n'
    (stage/'README-FIRST.txt').write_text(quick,encoding='utf-8')
    checks=[]
    for p in sorted(stage.rglob('*')):
        if p.is_file():checks.append(sha(p.read_bytes())+'  '+p.relative_to(stage).as_posix())
    (stage/'SHA256SUMS.txt').write_text('\n'.join(checks)+'\n',encoding='utf-8')
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    archive=destination/(VERSION+'.zip')
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p in sorted(stage.rglob('*')):
            if p.is_file():z.write(p,VERSION+'/'+p.relative_to(stage).as_posix())
    with zipfile.ZipFile(archive) as z:assert z.testzip() is None
    (destination/(VERSION+'.sha256')).write_text(sha(archive.read_bytes())+'  '+archive.name+'\n')
    print(json.dumps({'zip':str(archive),'bytes':archive.stat().st_size,'sha256':sha(archive.read_bytes()),'files':len(entries),'source_commit':source_commit}))
    return stage,archive


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);p.add_argument('--ci-evidence',type=Path,required=True);a=p.parse_args()
    build(a.output,a.ci_evidence)
