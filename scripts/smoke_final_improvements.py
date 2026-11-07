"""Disposable end-to-end column filters and shortage shortcuts, synthetic data only."""
import os, sys, json, secrets, socket, subprocess, tempfile, time, io
from pathlib import Path
from urllib.request import urlopen
from openpyxl import load_workbook
from playwright.sync_api import sync_playwright, expect
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'work/final-browser';OUT.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='final-erp-') as tmp,tempfile.TemporaryFile() as log:
 env={**os.environ,'ERP_DB_NAME':'','ERP_DEBUG':'1','ERP_SQLITE_PATH':str(Path(tmp)/'db.sqlite3')};password=secrets.token_urlsafe(18);env['FIXTURE_PASSWORD']=password
 subprocess.run([sys.executable,'manage.py','migrate','--noinput'],cwd=ROOT,env=env,check=True,stdout=subprocess.DEVNULL)
 seed="""import os
from django.contrib.auth import get_user_model
from inventory.models import Item,Stock
u=get_user_model().objects.create_superuser('fixture',password=os.environ['FIXTURE_PASSWORD'])
i=Item.objects.create(code='DEMO-A',name='가상 조명',specification='표준 25W')
Stock.objects.bulk_create([Stock(item=i,location=f'A{j:03}',quantity=10,reserved=2) for j in range(60)])
b=Item.objects.create(code='DEMO-B',name='가상 대체품',specification='표준 25W');Stock.objects.create(item=b,location='B001',quantity=30)
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
   # Global header values include rows beyond the first fifty.
   page.locator('.desktop-stock-table .stock-column-filter[data-field=location]').click();page.locator('#stock-option-search').fill('A059');expect(page.locator('#stock-column-values label')).to_have_count(1);page.locator('#stock-column-values input').check();page.locator('#stock-column-form').get_by_role('button',name='적용',exact=True).click();page.wait_for_url('**/?f_location=A059');expect(page.locator('.desktop-stock-table tbody tr')).to_have_count(1);expect(page.locator('.stock-summary')).to_contain_text('10');
   # Live search and additional filters retain the first selected column.
   page.locator('[name=q]').fill('DEMO-A');expect(page.locator('#stock-search-status')).to_have_text('조회 완료');assert 'f_location=A059' in page.url
   page.locator('.desktop-stock-table .stock-column-filter[data-field=quantity]').click();page.locator('#stock-column-form [name=minimum]').fill('9');page.locator('#stock-column-form').get_by_role('button',name='적용',exact=True).click();page.wait_for_url('**/*min_quantity=9*');expect(page.locator('.desktop-stock-table tbody tr')).to_have_count(1)
   with page.expect_download() as download:page.locator('#stock-export').click()
   book=load_workbook(io.BytesIO(Path(download.value.path()).read_bytes()),read_only=True);assert len(list(book.active.values))==2;book.close();page.screenshot(path=str(OUT/'stock-column-filters.png'),full_page=True)
   for width in (360,390,768):
    page.set_viewport_size({'width':width,'height':844});button=page.locator('.stock-mobile-filters .stock-column-filter[data-field=location]') if width<=900 else page.locator('.desktop-stock-table .stock-column-filter[data-field=location]');button.click();expect(page.locator('#stock-column-dialog')).to_be_visible();assert page.evaluate('document.documentElement.scrollWidth<=innerWidth');page.keyboard.press('Escape')
   page.set_viewport_size({'width':1440,'height':1000})
   page.goto(base+'/picking/new/');page.locator('[name=project]').fill('가상 부족 주문');page.locator('[name=customer_rows]').fill('DEMO-POR\t가상호선\t가상블록\t가상 품명\t6\tMISSING-A\t6');page.get_by_role('button',name='배정 미리보기',exact=True).click()
   shortage=page.locator('tr.shortage');expect(shortage).to_have_count(1);expect(shortage.get_by_role('button',name='대체품 등록')).to_be_visible();shortage.get_by_role('button',name='대체품 등록').click();page.wait_for_url('**/substitute/?requested=MISSING-A')
   expect(page.locator('[name=requested]')).to_have_value('MISSING-A');expect(page.locator('[name=quantity]')).to_have_value('6');page.locator('[name=stock]').select_option(label='DEMO-B | 가상 대체품 | B001 | 가용 30');page.locator('[name=quantity]').fill('2');page.locator('[name=reason]').fill('가상 동일 규격 확인');page.locator('[name=confirmed]').check();page.get_by_role('button',name='확인 후 반영').click();page.wait_for_url('**/picking/*/');expect(page.locator('main')).to_contain_text('미배정');expect(page.locator('main')).to_contain_text('대체품 등록');page.screenshot(path=str(OUT/'shortage-substitution.png'),full_page=True)
   page.get_by_role('link',name='고객 양식 인쇄',exact=True).click();expect(page.get_by_role('link',name='피킹 주문 이력',exact=True)).to_be_visible();page.pdf(path=str(OUT/'customer-picking.pdf'),format='A4',print_background=True)
   page.get_by_role('link',name='피킹 주문 이력',exact=True).click();page.wait_for_url('**/picking/');assert not errors,errors;browser.close()
  check="from inventory.models import Stock,Substitution; s=Stock.objects.get(item__code='DEMO-B');assert (s.quantity,s.reserved)==(30,2);assert Substitution.objects.get().allocation.state=='reserved'"
  subprocess.run([sys.executable,'manage.py','shell','-c',check],cwd=ROOT,env=env,check=True,stdout=subprocess.DEVNULL)
  print(json.dumps({'success':True,'checks':['global_column_choices','live_search_preserves_columns','numeric_range','matching_excel','mobile_dialog','signed_preview_shortcut','partial_substitution_reservation_only','customer_history_link','browser_pdf']},ensure_ascii=False))
 except Exception:
  log.seek(0);OUT.joinpath('server.log').write_bytes(log.read());raise
 finally:server.terminate();server.wait(timeout=10)
