"""Real Windows GDI pagination and PowerShell syntax, without sending to a printer."""
import json,os,subprocess,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if os.name!='nt':raise SystemExit('Windows 전용 검사')
ps=str(Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe')
for script in (ROOT/'inventory/printing').glob('*.ps1'):
    # Parse only. Never invoke installation/sharing actions in tests.
    code="$tokens=$null;$errors=$null;[System.Management.Automation.Language.Parser]::ParseFile($args[0],[ref]$tokens,[ref]$errors)|Out-Null;if($errors.Count){$errors|Out-String|Write-Error;exit 1}"
    # -Command argument binding is intentionally avoided; pass through a temporary parser file.
    with tempfile.TemporaryDirectory() as tmp:
        parser=Path(tmp)/'parse.ps1';parser.write_text('param([string]$Path)\n'+code.replace('$args[0]','$Path'),encoding='utf-8-sig')
        subprocess.run([ps,'-NoProfile','-NonInteractive','-File',str(parser),'-Path',str(script)],check=True)
with tempfile.TemporaryDirectory() as tmp:
    p=Path(tmp);payload={'printer':'가상 프린터','copies':1,'job_id':'synthetic-only','document':{'project':'가상 프로젝트','number':'DEMO-PRINT','registered':'2026-11-06 09:00','total':125,'rows':[{'code':f'DEMO-{i:03}','description':('가상 사양 장문 검증 '*150 if i==45 else '가상 제품 사양'),'location':'A01','quantity':1,'receipt':'입고 완료','status':'예약'} for i in range(125)]}}
    f=p/'job.json';f.write_text(json.dumps(payload,ensure_ascii=False),encoding='utf-8')
    r=subprocess.run([ps,'-NoProfile','-NonInteractive','-File',str(ROOT/'inventory/printing/Print-Picking.ps1'),'-PayloadPath',str(f),'-PreviewDirectory',str(p/'pages')],check=True,capture_output=True,text=True,encoding='utf-8')
    result=json.loads(r.stdout.strip().splitlines()[-1]);assert result['state']=='preview' and result['pages']>=4 and result['rows']==125,result
    assert len(list((p/'pages').glob('*.png')))==result['pages'];print(json.dumps(result))
print('Windows 프린터 스크립트 구문·실제 GDI 여러 페이지 렌더링 성공. 실제 인쇄하지 않았습니다.')
