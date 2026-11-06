"""Private disposable browser workflow: shared placement, filters and print queue."""
import os,sys,json,secrets,socket,subprocess,tempfile,time
from pathlib import Path
from urllib.request import urlopen
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'work/inbound-work-browser';OUT.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='inbound-work-') as tmp,tempfile.TemporaryFile() as log:
 env={**os.environ,'ERP_DB_NAME':'','ERP_DEBUG':'1','ERP_SQLITE_PATH':str(Path(tmp)/'db.sqlite3')};password=secrets.token_urlsafe(18);env['FIXTURE_PASSWORD']=password
 subprocess.run([sys.executable,'manage.py','migrate','--noinput'],cwd=ROOT,env=env,check=True,stdout=subprocess.DEVNULL)
 seed="""import os
from django.contrib.auth import get_user_model
from inventory.models import Item,Stock,Allocation,EmployeeMenuAccess,PickingOrder,CompanyPrinter
u=get_user_model().objects.create_superuser('fixture',password=os.environ['FIXTURE_PASSWORD'])
w=get_user_model().objects.create_user('worker',password=os.environ['FIXTURE_PASSWORD']);EmployeeMenuAccess.objects.create(user=w,inbound_record=True)
i=Item.objects.create(code='DEMO-001',name='가상 조명',specification='25W');s=Stock.objects.create(item=i,location='A01',quantity=100,reserved=55)
o=PickingOrder.objects.create(project='가상 출력',actor=u)
Allocation.objects.bulk_create([Allocation(stock=s,quantity=1,project='가상 프로젝트 A',legacy_seq='2026110602',order=o) for _ in range(55)])
Allocation.objects.create(stock=s,quantity=2,project='가상 프로젝트 B',legacy_seq='2026100101')
CompanyPrinter.objects.create(name='가상 프린터',enabled=True)
"""
 subprocess.run([sys.executable,'manage.py','shell','-c',seed],cwd=ROOT,env=env,check=True,stdout=subprocess.DEVNULL);env.pop('FIXTURE_PASSWORD')
 with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 base=f'http://127.0.0.1:{port}';server=subprocess.Popen([sys.executable,'-c',f"from config.wsgi import application;from django.contrib.staticfiles.handlers import StaticFilesHandler;from waitress import serve;serve(StaticFilesHandler(application),host='127.0.0.1',port={port},threads=4)"],cwd=ROOT,env=env,stdout=log,stderr=log)
 try:
  for _ in range(100):
   try:urlopen(base+'/login/',timeout=.5).close();break
   except OSError:time.sleep(.1)
  with sync_playwright() as p:
   browser=p.chromium.launch(executable_path=os.environ.get('ERP_CHROMIUM','/usr/bin/chromium'),args=['--no-sandbox']);manager=browser.new_page(viewport={'width':1440,'height':1000});worker=browser.new_page(viewport={'width':390,'height':844});errors=[]
   for page in (manager,worker):page.set_default_timeout(15000);page.on('pageerror',lambda e:errors.append(str(e)));page.on('dialog',lambda d:d.accept())
   def login(page,name):
    page.goto(base+'/login/');page.locator('[name=username]').fill(name);page.locator('[name=password]').fill(password);page.get_by_role('button',name='로그인',exact=True).click();page.wait_for_url(base+'/')
   login(manager,'fixture');login(worker,'worker')
   manager.goto(base+'/inbound/');raw='품목코드\t품목명\t규격\t수량\t비고\nDEMO-001\t가상 조명\t25W\t64.\t32+32'
   manager.locator('#inbound-file').set_input_files({'name':'synthetic.xls','mimeType':'application/octet-stream','buffer':raw.encode('cp949')});expect(manager.locator('#inbound-rowCount')).to_have_text('2')
   manager.locator('#inbound-saveWork').click();manager.wait_for_url('**/inbound/saved/*/');detail=manager.url
   expect(manager.locator('.work-row')).to_have_count(2);worker.goto(detail);expect(worker.locator('.work-row')).to_have_count(2)
   first=worker.locator('.work-row').first;first.locator('[name=location]').fill('A01');first.locator('[name=location]').press('Tab');expect(first.locator('.row-status')).to_have_text('저장 완료');
   with worker.expect_response(lambda r:r.url.endswith('/row/') and r.request.method=='POST') as result:first.locator('[name=placed]').check()
   assert result.value.ok;expect(first.locator('.row-status')).to_have_text('저장 완료');expect(worker.locator('#work-done')).to_have_text('1')
   assert worker.locator('#work-confirm').count()==0
   assert worker.evaluate('document.documentElement.scrollWidth<=innerWidth')
   worker.screenshot(path=str(OUT/'mobile-placement.png'),full_page=True)
   # A stale manager form must not overwrite the worker's newer input.
   stale=manager.locator('.work-row').first;stale.locator('[name=location]').fill('WRONG');stale.locator('[name=location]').press('Tab');expect(stale.locator('.row-status')).to_contain_text('다른 사용자')
   manager.reload();expect(manager.locator('.work-row').first.locator('[name=location]')).to_have_value('A01');expect(manager.locator('.work-row').first.locator('[name=placed]')).to_be_checked()
   last=manager.locator('.work-row').last;last.locator('[name=location]').fill('A02');last.locator('[name=location]').press('Tab');expect(last.locator('.row-status')).to_have_text('저장 완료');
   with manager.expect_response(lambda r:r.url.endswith('/row/') and r.request.method=='POST') as result:last.locator('[name=placed]').check()
   assert result.value.ok;expect(last.locator('.row-status')).to_have_text('저장 완료')
   manager.reload();manager.screenshot(path=str(OUT/'shared-list.png'),full_page=True);manager.locator('#work-confirm [name=confirmed]').check();manager.locator('#work-confirm button').click();expect(manager.locator('#work-save-status')).to_contain_text('입고 완료');expect(manager.locator('.work-row')).to_have_count(0)
   manager.goto(base+'/inbound/saved/');manager.locator('[name=lists]').check();manager.get_by_role('button',name='선택 목록 숨기기').click();manager.wait_for_url('**/inbound/saved/?hidden=1');manager.goto(base+'/inbound/saved/');expect(manager.locator('[name=lists]')).to_have_count(0)
   manager.goto(base+'/allocations/');expect(manager.locator('form.filters')).to_have_count(0)
   manager.locator('.column-filter[data-field=project]').click();manager.locator('#column-filter-values').get_by_text('가상 프로젝트 A (55행)',exact=True).click();manager.locator('#column-filter-form').get_by_role('button',name='적용',exact=True).click();manager.wait_for_url('**/allocations/?**');expect(manager.locator('[name=allocations]')).to_have_count(55);expect(manager.locator('#filtered-quantity')).to_have_text('55')
   expect(manager.locator('.column-filter[data-field=project]')).to_have_class('column-filter active');manager.locator('#select-page').check();expect(manager.locator('#processing-quantity')).to_have_text('55')
   manager.screenshot(path=str(OUT/'header-filters.png'),full_page=True)
   manager.locator('.column-filter[data-field=location]').click();manager.locator('#column-filter-form [name=contains]').fill('A01');manager.locator('#column-filter-form').get_by_role('button',name='적용',exact=True).click();manager.wait_for_url('**/allocations/?**');expect(manager.locator('[name=allocations]:checked')).to_have_count(0);expect(manager.locator('#processing-quantity')).to_have_text('0');expect(manager.locator('#filtered-quantity')).to_have_text('55')
   for width in (360,390,768):
    manager.set_viewport_size({'width':width,'height':844});manager.locator('.column-filter[data-field=code]').click();expect(manager.locator('#allocation-filter-dialog')).to_be_visible();assert manager.evaluate('document.documentElement.scrollWidth<=innerWidth');manager.keyboard.press('Escape')
   manager.set_viewport_size({'width':1440,'height':1000});manager.get_by_role('link',name='P-',exact=False).first.click();manager.get_by_role('link',name='회사 프린터로 출력',exact=True).click();expect(manager.locator('main')).to_contain_text('가상 프린터');manager.locator('[name=confirmed]').check();manager.get_by_role('button',name='회사 프린터로 출력 요청').click();expect(manager.locator('main')).to_contain_text('대기');manager.screenshot(path=str(OUT/'print-queue.png'),full_page=True)
   assert not errors,errors;browser.close()
  check="from inventory.models import Stock,Receipt,InboundWorkList,CompanyPrintJob;assert Receipt.objects.count()==2;assert Stock.objects.get(item__code='DEMO-001',location='A01').quantity==132;assert Stock.objects.get(item__code='DEMO-001',location='A02').quantity==32;assert InboundWorkList.objects.get().state=='received';assert InboundWorkList.objects.get().archived;assert CompanyPrintJob.objects.count()==1;assert CompanyPrintJob.objects.get().state=='queued'"
  subprocess.run([sys.executable,'manage.py','shell','-c',check],cwd=ROOT,env=env,check=True,stdout=subprocess.DEVNULL)
  print(json.dumps({'success':True,'checks':['partial_mobile_then_manager','stale_edit_conflict','single_final_receipt','archive_keeps_stock','whole_query_55_row_filters','reset_selection','mobile_header_dialog','office_print_queue_only']},ensure_ascii=False))
 except Exception:
  log.seek(0);OUT.joinpath('server.log').write_bytes(log.read());raise
 finally:server.terminate();server.wait(timeout=10)
