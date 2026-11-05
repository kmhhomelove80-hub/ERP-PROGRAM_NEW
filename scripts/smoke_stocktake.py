"""Browser acceptance: real SQLite backup/undo, mobile, long release and reviews."""
import os,json,secrets,socket,subprocess,sys,tempfile,time
from io import BytesIO
from pathlib import Path
from urllib.request import urlopen
from playwright.sync_api import sync_playwright,expect
from openpyxl import load_workbook
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'work/stocktake-browser';OUT.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='sunil-stocktake-browser-') as tmp,tempfile.TemporaryFile() as log:
 env={**os.environ,'ERP_DB_NAME':'','ERP_DEBUG':'1','ERP_SQLITE_PATH':str(Path(tmp)/'test.sqlite3'),'ERP_STOCKTAKE_BACKUP_DIR':str(Path(tmp)/'backups')};password=secrets.token_urlsafe(18);env['FIXTURE_PASSWORD']=password
 subprocess.run([sys.executable,'manage.py','migrate','--noinput'],cwd=ROOT,env=env,check=True,stdout=subprocess.DEVNULL)
 seed="""import os
from django.contrib.auth import get_user_model
from inventory.models import Item,Stock,Allocation
get_user_model().objects.create_superuser('fixture',password=os.environ['FIXTURE_PASSWORD'])
get_user_model().objects.create_user('staff',password=os.environ['FIXTURE_PASSWORD'],is_staff=True)
i=Item.objects.create(code='COUNT',name='가상 실사 제품',specification='가상 규격');s=Stock.objects.create(item=i,location='A01',quantity=15,reserved=4);Allocation.objects.create(stock=s,quantity=4,project='포장 완료',legacy_seq='2026100101')
i=Item.objects.create(code='CLEAN');Stock.objects.create(item=i,location='SOURCE',quantity=2);Stock.objects.create(item=i,location='DEST',quantity=10)
i=Item.objects.create(code='SHIP');s=Stock.objects.create(item=i,location='SHIPPING',quantity=100,reserved=55)
Allocation.objects.bulk_create([Allocation(stock=s,quantity=1,project='LONG',legacy_seq='2026110501') for _ in range(55)])
"""
 subprocess.run([sys.executable,'manage.py','shell','-c',seed],cwd=ROOT,env=env,check=True,stdout=subprocess.DEVNULL);env.pop('FIXTURE_PASSWORD')
 with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 base=f'http://127.0.0.1:{port}';server=subprocess.Popen([sys.executable,'-c',f"from config.wsgi import application;from django.contrib.staticfiles.handlers import StaticFilesHandler;from waitress import serve;serve(StaticFilesHandler(application),host='127.0.0.1',port={port},threads=4)"],cwd=ROOT,env=env,stdout=log,stderr=log)
 try:
  for _ in range(100):
   try:urlopen(base+'/login/',timeout=.5).close();break
   except OSError:time.sleep(.1)
  with sync_playwright() as p:
   browser=p.chromium.launch(executable_path=os.environ.get('ERP_CHROMIUM','/usr/bin/chromium'),args=['--no-sandbox']);page=browser.new_page(viewport={'width':1440,'height':1000});page.set_default_timeout(20000);errors=[];page.on('pageerror',lambda e:errors.append(str(e)));page.on('dialog',lambda d:d.accept())
   def login(target,name):
    target.goto(base+'/login/');target.locator('[name=username]').fill(name);target.locator('[name=password]').fill(password);target.get_by_role('button',name='로그인',exact=True).click();target.wait_for_url(base+'/')
   login(page,'fixture')
   page.goto(base+'/allocations/');expect(page.locator('[name=allocations]')).to_have_count(50)
   page.locator('.table').evaluate('(el)=>el.scrollTop=el.scrollHeight');expect(page.locator('[name=allocations]')).to_have_count(56)
   assert len(set(page.locator('[name=allocations]').evaluate_all('(els)=>els.map(e=>e.value)')))==56
   page.locator('[name=allocations]').last.check();page.get_by_role('link',name='피킹 주문 이력',exact=True).click();page.go_back();expect(page.locator('[name=allocations]')).to_have_count(56);expect(page.locator('[name=allocations]:checked')).to_have_count(1)
   page.goto(base+'/stocktake/');page.locator('[name=q]').fill('COUNT');page.locator('[name=confirmed]').check();page.get_by_role('button',name='재고 실사 리스트 출력 / 다운로드 준비').click();page.wait_for_url('**/stocktake/*/');detail=page.url
   with page.expect_download() as download:page.get_by_role('link',name='조사 엑셀 다운로드').click()
   path=Path(tmp)/'count.xlsx';download.value.save_as(path);book=load_workbook(path);sheet=book.active;assert sheet['P2'].value==11 and sheet['Q2'].value==4;sheet['M2']=10;sheet['N2']='실사 부족 확인';sheet.append(['NEW','가상 신규 제품','가상 신규 규격','Y09','NEW-LOC','입고 완료',None,None,None,None,None,None,3,'실사 중 발견']);book.save(path)
   page.locator('[name=workbook]').set_input_files(str(path));page.get_by_role('button',name='업로드 · 변경 미리보기').click();expect(page.locator('main')).to_contain_text('변경 내역 2행');page.locator('[name=approve_new]').check();page.locator('form').filter(has=page.locator('[name=preview_id]')).locator('[name=confirmed]').check();page.get_by_role('button',name='자동 백업·검증 후 전체 반영').click();expect(page.locator('.messages')).to_contain_text('재고 반영을 완료');assert len(list((Path(tmp)/'backups').glob('sunil-*/manifest.json')))==1
   page.locator('form').filter(has=page.locator('[name=action][value=reverse]')).locator('[name=confirmed]').check();page.get_by_role('button',name='백업 후 이 건 되돌리기').click();expect(page.locator('.messages')).to_contain_text('수량을 되돌렸습니다');assert len(list((Path(tmp)/'backups').glob('sunil-*/manifest.json')))==2
   page.goto(base+'/allocations/?q=LONG');expect(page.locator('[name=allocations]')).to_have_count(55);page.locator('#select-page').check();expect(page.locator('#processing-quantity')).to_have_text('55');page.locator('[name=action]').select_option('release');page.locator('[name=shipped_at]').fill('2026-11-05T09:00');page.locator('[name=confirmed]').check();page.get_by_role('button',name='선택 범위 전량 처리').click();expect(page.locator('.messages')).to_contain_text('55개 예약 행');expect(page.locator('[name=allocations]')).to_have_count(0)
   page.goto(base+'/cleanup/');page.get_by_role('button',name='기존 유지',exact=True).first.click();expect(page.locator('main')).to_contain_text('기존 유지로 판정');assert page.locator('.cleanup-group').count()==0
   page.goto(base+'/cleanup/decisions/');page.get_by_role('button',name='판정 취소').click();page.goto(base+'/cleanup/');expect(page.locator('.cleanup-group')).to_have_count(1)
   for width in (360,390,768):
    page.set_viewport_size({'width':width,'height':844});page.goto(base+'/allocations/');expect(page.locator('#mobile-menu-toggle')).to_be_visible();assert not page.locator('.primary-nav').is_visible();page.locator('#mobile-menu-toggle').click();expect(page.locator('.primary-nav')).to_be_visible();page.keyboard.press('Escape');assert not page.locator('.primary-nav').is_visible();assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),width
    page.locator('.table').evaluate('(el)=>el.scrollTop=el.scrollHeight');assert page.locator('header').bounding_box()['y']>=0
   page.screenshot(path=str(OUT/'mobile.png'),full_page=True)
   staff=browser.new_page();login(staff,'staff');assert staff.request.get(base+'/stocktake/').status==403;staff.goto(base+'/');assert staff.locator('a[href="/stocktake/"]').count()==0
   assert not errors,errors;browser.close()
  check="from inventory.models import Stock,Allocation;assert Stock.objects.get(item__code='COUNT').quantity==15;assert Stock.objects.get(item__code='COUNT').reserved==4;assert Stock.objects.get(item__code='NEW').quantity==0;assert Stock.objects.get(item__code='SHIP').quantity==45;assert Stock.objects.get(item__code='SHIP').reserved==0;assert Allocation.objects.filter(project='LONG',state='released').count()==55"
  subprocess.run([sys.executable,'manage.py','shell','-c',check],cwd=ROOT,env=env,check=True,stdout=subprocess.DEVNULL)
  print(json.dumps({'success':True,'checks':['real_backup_and_undo','packed_reserved_preserved','new_item_confirmation','55_rows_one_release','cleanup_keep_cancel','mobile_360_390_768','superuser_only']},ensure_ascii=False))
 finally:server.terminate();server.wait(timeout=10)
