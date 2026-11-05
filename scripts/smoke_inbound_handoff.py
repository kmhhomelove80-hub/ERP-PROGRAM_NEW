"""Actual browser: five-column transfer, deleted splits, final receipt, replay."""
import os,json,secrets,socket,subprocess,sys,tempfile,time
from pathlib import Path
from urllib.request import urlopen
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='sunil-handoff-browser-') as tmp,tempfile.TemporaryFile() as log:
 env={**os.environ,'ERP_DB_NAME':'','ERP_DEBUG':'1','ERP_SQLITE_PATH':str(Path(tmp)/'test.sqlite3')};password=secrets.token_urlsafe(18);env['FIXTURE_PASSWORD']=password
 subprocess.run([sys.executable,'manage.py','migrate','--noinput'],cwd=ROOT,env=env,check=True,stdout=subprocess.DEVNULL)
 seed="import os;from django.contrib.auth import get_user_model;from inventory.models import Item,Stock;get_user_model().objects.create_superuser('fixture',password=os.environ['FIXTURE_PASSWORD']);Stock.objects.create(item=Item.objects.create(code='001',name='기존 모델',specification='기존 사양'),location='A01',quantity=5)"
 subprocess.run([sys.executable,'manage.py','shell','-c',seed],cwd=ROOT,env=env,check=True,stdout=subprocess.DEVNULL);env.pop('FIXTURE_PASSWORD')
 with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 base=f'http://127.0.0.1:{port}';server=subprocess.Popen([sys.executable,'-c',f"from config.wsgi import application;from django.contrib.staticfiles.handlers import StaticFilesHandler;from waitress import serve;serve(StaticFilesHandler(application),host='127.0.0.1',port={port},threads=4)"],cwd=ROOT,env=env,stdout=log,stderr=log)
 try:
  for _ in range(100):
   try:urlopen(base+'/login/',timeout=.5).close();break
   except OSError:time.sleep(.1)
  with sync_playwright() as p:
   browser=p.chromium.launch(executable_path=os.environ.get('ERP_CHROMIUM','/usr/bin/chromium'),args=['--no-sandbox']);page=browser.new_page();page.on('dialog',lambda d:d.accept());errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
   page.goto(base+'/login/');page.locator('[name=username]').fill('fixture');page.locator('[name=password]').fill(password);page.get_by_role('button',name='로그인',exact=True).click();page.wait_for_url(base+'/');page.goto(base+'/inbound/')
   text='품목코드\t품목명\t규격\t수량\t비고\n001\t입력 모델\tD열 사양\t64\t32+32\nNEW\t새 모델\t새 사양\t6\t'
   page.locator('#inbound-file').set_input_files({'name':'fixture.xls','mimeType':'application/octet-stream','buffer':text.encode('cp949')});expect(page.locator('#inbound-rowCount')).to_have_text('3');page.locator('#inbound-receive').click();expect(page.locator('#inbound-copyStatus')).to_contain_text('로케이션을 입력');assert page.url.endswith('/inbound/')
   page.locator('.remark').nth(0).fill('A01');page.locator('.remark').nth(1).fill('B02');page.locator('#inbound-previewTab').click();page.locator('.preview-group .rowSelect').first.check();page.locator('#inbound-deleteSelected').click();expect(page.locator('#inbound-totalQty')).to_have_text('38')
   page.locator('#inbound-receive').click();page.wait_for_url('**/receipts/bulk/');expect(page.locator('[name=pasted_rows]')).to_have_value('001\t입력 모델\tD열 사양\t32\tA01\nNEW\t새 모델\t새 사양\t6\tB02');expect(page.locator('main')).to_contain_text('아직 재고는 변경되지 않았습니다');expect(page.locator('main')).to_contain_text('기존 모델·사양은 유지')
   token=page.locator('[name=preview_token]').input_value();csrf=page.locator('[name=csrfmiddlewaretoken]').first.input_value();before="from inventory.models import Stock,Receipt;assert Stock.objects.get(item__code='001').quantity==5;assert Receipt.objects.count()==0";subprocess.run([sys.executable,'manage.py','shell','-c',before],cwd=ROOT,env=env,check=True,stdout=subprocess.DEVNULL)
   page.locator('[name=confirmed]').check();page.get_by_role('button',name='전체 행 일괄 입고').click();page.wait_for_url('**/receipts/');expect(page.locator('.messages')).to_contain_text('2개 행을 일괄 입고')
   response=page.request.post(base+'/receipts/bulk/',form={'action':'confirm','confirmed':'on','preview_token':token,'csrfmiddlewaretoken':csrf});assert response.ok
   assert not errors,errors;browser.close()
  after="from inventory.models import Stock,Receipt,Item;assert Stock.objects.get(item__code='001').quantity==37;assert Stock.objects.get(item__code='NEW').quantity==6;assert Item.objects.get(code='NEW').specification=='새 사양';assert Item.objects.get(code='001').specification=='기존 사양';assert Receipt.objects.count()==2";subprocess.run([sys.executable,'manage.py','shell','-c',after],cwd=ROOT,env=env,check=True,stdout=subprocess.DEVNULL)
  print(json.dumps({'success':True,'checks':['five_fields_including_specification','missing_location_blocked','deleted_split_excluded','preview_without_mutation','actual_receipt','same_request_replay_no_duplicates','existing_description_preserved']},ensure_ascii=False))
 finally:server.terminate();server.wait(timeout=10)
