"""Disposable end-to-end column filters and shortage shortcuts, synthetic data only."""
import os, sys, json, secrets, socket, subprocess, tempfile, time, io
from pathlib import Path
from urllib.request import urlopen
from openpyxl import load_workbook
from playwright.sync_api import sync_playwright, expect
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'work/stock-value-browser';OUT.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='final-erp-') as tmp,tempfile.TemporaryFile() as log:
 env={**os.environ,'ERP_DB_NAME':'','ERP_DEBUG':'1','ERP_SQLITE_PATH':str(Path(tmp)/'db.sqlite3')};password=secrets.token_urlsafe(18);env['FIXTURE_PASSWORD']=password
 subprocess.run([sys.executable,'manage.py','migrate','--noinput'],cwd=ROOT,env=env,check=True,stdout=subprocess.DEVNULL)
 seed="""import os
from django.contrib.auth import get_user_model
from inventory.models import Item,Stock
u=get_user_model().objects.create_superuser('fixture',password=os.environ['FIXTURE_PASSWORD'])
i=Item.objects.create(code='VALUE-A',name='가상 금액 품목',specification='가상 사양')
Stock.objects.create(item=i,location='A01',quantity=2,reserved=1)
Stock.objects.create(item=i,location='B01',quantity=1)
Stock.objects.create(item=i,location='예정',quantity=5,status='expected')
Stock.objects.create(item=Item.objects.create(code='VALUE-MISSING'),location='C01',quantity=1)
"""
 subprocess.run([sys.executable,'manage.py','shell','-c',seed],cwd=ROOT,env=env,check=True,stdout=subprocess.DEVNULL);env.pop('FIXTURE_PASSWORD')
 with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 base=f'http://127.0.0.1:{port}';server=subprocess.Popen([sys.executable,'-c',f"from config.wsgi import application;from django.contrib.staticfiles.handlers import StaticFilesHandler;from waitress import serve;serve(StaticFilesHandler(application),host='127.0.0.1',port={port},threads=4)"],cwd=ROOT,env=env,stdout=log,stderr=log)
 try:
  for _ in range(100):
   try:urlopen(base+'/login/',timeout=.5).close();break
   except OSError:time.sleep(.1)
  with sync_playwright() as p:
   browser=p.chromium.launch(executable_path=os.environ.get('ERP_CHROMIUM','/usr/bin/chromium'),args=['--no-sandbox']);page=browser.new_page(viewport={'width':1440,'height':1000});page.set_default_timeout(15000);errors=[];page.on('pageerror',lambda e:errors.append(str(e)));page.on('dialog',lambda d:d.accept())
   page.goto(base+'/login/');page.locator('[name=username]').fill('fixture');page.locator('[name=password]').fill(password);page.get_by_role('button',name='로그인',exact=True).click();page.wait_for_url(base+'/')
   expect(page.locator('.stock-summary')).to_contain_text('계산 불가')
   page.goto(base+'/erp/upload/');page.locator('[name=layout]').select_option('export');page.locator('[name=label]').fill('가상 금액 기준');page.locator('[name=erp_as_of]').fill('2026-11-01T09:00')
   text='품목코드\t품목명\t현재고\t재고금액\t출하대기\nVALUE-A\t가상 금액 품목\t3\t100.0000\t0'
   page.locator('[name=y09_file]').set_input_files({'name':'synthetic-Y09.xls','mimeType':'application/octet-stream','buffer':text.encode('cp949')});page.get_by_role('button',name='자료 미리보기',exact=True).click();expect(page.locator('main')).to_contain_text('금액 포함 행');page.locator('[name=confirmed]').check();page.get_by_role('button',name='이 자료로 대조 등록',exact=True).click();page.wait_for_url('**/erp/*/');expect(page.locator('main')).to_contain_text('100.0000')
   page.goto(base+'/');expect(page.locator('#stock-value-total')).to_have_text('100');expect(page.locator('.stock-summary')).to_contain_text('계산 불가 1행');expect(page.locator('.stock-summary')).to_contain_text('제외 1행')
   page.locator('.desktop-stock-table .stock-column-filter[data-field=location]').click();page.locator('#stock-column-values').get_by_text('A01 (1행)',exact=True).click();page.locator('#stock-column-form').get_by_role('button',name='적용',exact=True).click();page.wait_for_url('**/?f_location=A01');expect(page.locator('#stock-value-total')).to_have_text('67');expect(page.locator('.stock-value')).to_have_text('67')
   with page.expect_download() as result:page.locator('#stock-export').click()
   book=load_workbook(io.BytesIO(Path(result.value.path()).read_bytes()),read_only=True);rows=list(book.active.values);assert len(rows)==2 and rows[1][12]==67 and rows[1][14];book.close();page.screenshot(path=str(OUT/'valuation-desktop.png'),clip={'x':0,'y':0,'width':1440,'height':420})
   page.set_viewport_size({'width':390,'height':844});page.locator('.stock-row summary').click();expect(page.locator('.stock-row-detail')).to_contain_text('67원');assert page.evaluate('document.documentElement.scrollWidth<=innerWidth');page.screenshot(path=str(OUT/'valuation-mobile.png'),full_page=True)
   assert not errors,errors;browser.close()
  check="from inventory.models import Stock,ReconciliationRow;assert Stock.objects.get(location='A01').quantity==2;assert Stock.objects.get(location='A01').reserved==1;assert ReconciliationRow.objects.get(code='VALUE-A').inventory_amount==100"
  subprocess.run([sys.executable,'manage.py','shell','-c',check],cwd=ROOT,env=env,check=True,stdout=subprocess.DEVNULL)
  print(json.dumps({'success':True,'checks':['signed_ERP_amount_upload','no_stock_mutation','per_location_rounding','filtered_value_sum_excel','mobile_value']},ensure_ascii=False))
 except Exception:
  log.seek(0);OUT.joinpath('server.log').write_bytes(log.read());raise
 finally:server.terminate();server.wait(timeout=10)
