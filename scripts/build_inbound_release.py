"""Code-only UI patch on integrated-v3 or bridge-v1, with unchanged recovery updater."""
import argparse,hashlib,json,shutil,subprocess,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
VERSION='SUNIL-ERP-20261105-inbound-v1'
def digest(data):return hashlib.sha256(data).hexdigest()
def build(output,evidence_path):
 evidence=json.loads(Path(evidence_path).read_text(encoding='utf-8'))
 for key in ('success','windows_native_success','windows_schedule_restore_success','postgresql18_success','browser_success'):
  if evidence.get(key) is not True:raise RuntimeError('필수 검증 기록이 없습니다: '+key)
 stage=ROOT/'work'/VERSION
 if stage.exists():shutil.rmtree(stage)
 (stage/'app').mkdir(parents=True)
 before={e['path']:e['sha256'] for e in json.loads((ROOT/'deploy/windows/delivery-bridge-v3/baseline.json').read_text())['files']}
 changed=subprocess.check_output(['git','diff','--name-only','eb57b40','HEAD'],cwd=ROOT,text=True).splitlines()
 names=set(before)|{n for n in changed if n.startswith(('inventory/','config/'))}
 names.add('inventory/static/inventory/vendor/xlsx.full.min.js')
 entries=[]
 for name in sorted(names):
  data=(ROOT/name).read_bytes();target=stage/'app'/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
  old=set()
  if before.get(name):old.add(before[name])
  r=subprocess.run(['git','show','eb57b40:'+name],cwd=ROOT,capture_output=True)
  if r.returncode==0:old.add(digest(r.stdout))
  entries.append(dict(path=name,sha256=digest(data),accepted_previous_sha256=sorted(old),required_existing=bool(before.get(name)) or (name not in before and r.returncode==0)))
 helpers=('Update-Erp.py','release_updater.py','release_probe.py','update_snapshot.py')
 for name in helpers:shutil.copyfile(ROOT/('scripts/update_snapshot.py' if name=='update_snapshot.py' else 'deploy/windows/standard_update/'+name),stage/name)
 manifest=dict(format=3,release=VERSION,source_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),verified_ci_commit=evidence['commit'],target_migration='0013_employee_menu_access',files=entries,helpers={n:digest((stage/n).read_bytes()) for n in helpers})
 (stage/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8');(stage/'VALIDATION.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),encoding='utf-8')
 shutil.copyfile(ROOT/'docs/INBOUND_RELEASE_20261105.md',stage/'README.md')
 (stage/'SHA256SUMS.txt').write_text(''.join(digest(p.read_bytes())+'  '+p.relative_to(stage).as_posix()+'\n' for p in sorted(stage.rglob('*')) if p.is_file()),encoding='utf-8')
 output=Path(output);output.mkdir(parents=True,exist_ok=True);archive=output/(VERSION+'.zip')
 with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
  for p in sorted(stage.rglob('*')):
   if p.is_file():z.write(p,VERSION+'/'+p.relative_to(stage).as_posix())
 with zipfile.ZipFile(archive) as z:assert z.testzip() is None
 archive.with_suffix('.zip.sha256').write_text(digest(archive.read_bytes())+'  '+archive.name+'\n')
 print(json.dumps(dict(archive=str(archive),bytes=archive.stat().st_size,sha256=digest(archive.read_bytes()),files=len(entries))))
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--output',required=True);parser.add_argument('--ci-evidence',required=True);args=parser.parse_args();build(args.output,args.ci_evidence)
