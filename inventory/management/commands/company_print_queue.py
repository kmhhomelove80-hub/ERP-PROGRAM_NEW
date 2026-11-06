"""Run as the Windows account that owns the installed office printer."""
import json,os,subprocess,tempfile,time
from pathlib import Path
from django.core.management.base import BaseCommand,CommandError
from django.conf import settings
from inventory.company_print import claim,finish


def run_job(job):
    powershell=Path(os.environ.get('SystemRoot',r'C:\Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
    script=Path(settings.BASE_DIR)/'inventory/printing/Print-Picking.ps1'
    # The native helper reads data from JSON; document fields are never commands.
    with tempfile.TemporaryDirectory(prefix='sunil-print-') as temp:
        payload=Path(temp)/'job.json';payload.write_text(json.dumps({'printer':job.printer,'copies':job.copies,'job_id':str(job.pk),'document':job.document},ensure_ascii=False),encoding='utf-8')
        result=subprocess.run([str(powershell),'-NoProfile','-NonInteractive','-File',str(script),'-PayloadPath',str(payload)],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180)
        if result.returncode:raise RuntimeError('인쇄 결과가 불명확합니다. 현장 대기열 확인 필요. '+result.stderr[-1000:])
        marker=json.loads(result.stdout.strip().splitlines()[-1])
        if marker.get('state')!='spooler_submitted':raise RuntimeError('인쇄 대기열 응답을 확인할 수 없습니다.')
        return 'Windows 인쇄 대기열에 전달했습니다. 종이 출력 완료는 현장에서 확인하세요.'


class Command(BaseCommand):
    help='회사 프린터 대기열을 처리합니다. 실제 인쇄 명령입니다.'
    def add_arguments(self,p):
        p.add_argument('--watch',action='store_true')
    def handle(self,*args,**options):
        if os.name!='nt':raise CommandError('회사 프린터가 등록된 Windows 서버에서 실행하세요.')
        from django.db import connection
        if connection.vendor!='postgresql':raise CommandError('기존 ERP의 PostgreSQL 환경으로 실행하세요. Run-PrintWorker.ps1을 사용하세요.')
        while True:
            job=claim()
            if job:
                try:result=run_job(job)
                except Exception as e:finish(job_id=job.pk,claim_id=job.claim_id,success=False,result=str(e));self.stderr.write('결과 확인 필요: '+str(job.pk))
                else:finish(job_id=job.pk,claim_id=job.claim_id,success=True,result=result);self.stdout.write('대기열 전달: '+str(job.pk))
            if not options['watch']:break
            from django.db import close_old_connections
            close_old_connections();time.sleep(2 if job is None else .1)
