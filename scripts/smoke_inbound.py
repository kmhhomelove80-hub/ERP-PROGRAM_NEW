"""Disposable browser integration including actual Chromium A4 PDF and clipboard formats."""
import os,secrets,socket,subprocess,sys,tempfile,time,json
from pathlib import Path
from urllib.request import urlopen
from playwright.sync_api import sync_playwright,expect
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'work/inbound-browser';OUT.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory() as tmp,tempfile.TemporaryFile() as log:
 env={**os.environ,'ERP_DB_NAME':'','ERP_DEBUG':'1','ERP_SQLITE_PATH':str(Path(tmp)/'test.sqlite3')};password=secrets.token_urlsafe(18);env['FIXTURE_PASSWORD']=password
 subprocess.run([sys.executable,'manage.py','migrate','--noinput'],cwd=ROOT,env=env,check=True,stdout=subprocess.DEVNULL)
 seed="""import os
from django.contrib.auth import get_user_model
from inventory.models import Item,Stock,Allocation
get_user_model().objects.create_superuser('fixture',password=os.environ['FIXTURE_PASSWORD'])
i=Item.objects.create(code='001',name='가상 제품');s=Stock.objects.create(item=i,location='A01',quantity=200,reserved=60)
Stock.objects.bulk_create([Stock(item=i,location=f'L{x:03}',quantity=1) for x in range(110)])
Allocation.objects.bulk_create([Allocation(stock=s,project='FILTER',quantity=x+1) for x in range(55)])
Allocation.objects.create(stock=s,project='OTHER',quantity=999)
"""
 subprocess.run([sys.executable,'manage.py','shell','-c',seed],cwd=ROOT,env=env,check=True,stdout=subprocess.DEVNULL);env.pop('FIXTURE_PASSWORD')
 with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 base=f'http://127.0.0.1:{port}'
 server=subprocess.Popen([sys.executable,'-c',f"from config.wsgi import application;from django.contrib.staticfiles.handlers import StaticFilesHandler;from waitress import serve;serve(StaticFilesHandler(application),host='127.0.0.1',port={port},threads=4)"],cwd=ROOT,env=env,stdout=log,stderr=log)
 try:
  for _ in range(100):
   try:urlopen(base+'/login/',timeout=.5).close();break
   except OSError:time.sleep(.1)
  with sync_playwright() as play:
   browser=play.chromium.launch(executable_path=os.environ.get('ERP_CHROMIUM','/usr/bin/chromium'),args=['--no-sandbox'])
   page=browser.new_page(viewport={'width':1500,'height':900},accept_downloads=True);errors=[];page.on('pageerror',lambda e:errors.append(str(e)));page.on('dialog',lambda d:d.accept())
   page.goto(base+'/login/');page.locator('[name=username]').fill('fixture');page.locator('[name=password]').fill(password);page.get_by_role('button',name='로그인',exact=True).click();page.wait_for_url(base+'/')
   page.wait_for_timeout(300)
   attempts=[]
   def interrupt_once(route):
    from urllib.parse import urlsplit,parse_qs
    u=urlsplit(route.request.url)
    if u.path=='/' and parse_qs(u.query).get('page')==['2']:
     attempts.append(route.request.url)
     if len(attempts)==1:route.fulfill(status=500,body='synthetic retry');return
    route.continue_()
   page.route('**/*',interrupt_once);page.reload()
   page.locator('.desktop-stock-table').evaluate('(el)=>el.scrollTop=el.scrollHeight')
   expect(page.locator('[data-stock-more]')).to_have_class(__import__('re').compile('load-failed'))
   expect(page.locator('[data-load-more]')).to_be_visible();page.wait_for_timeout(200);assert len(attempts)==1
   page.locator('[data-load-more]').click()
   for _ in range(3):page.locator('.desktop-stock-table').evaluate('(el)=>el.scrollTop=el.scrollHeight');page.wait_for_timeout(350)
   page.unroute('**/*',interrupt_once)
   assert page.locator('[data-load-more]:visible').count()==0
   expect(page.locator('.desktop-stock-table [data-stock-id]')).to_have_count(111)
   assert len(set(page.locator('.desktop-stock-table [data-stock-id]').evaluate_all('(els)=>els.map(e=>e.dataset.stockId)')))==111
   page.locator('[name=q]').fill('L003');expect(page.locator('.desktop-stock-table [data-stock-id]')).to_have_count(1)
   page.goto(base+'/allocations/?q=FILTER');expect(page.locator('#filtered-quantity')).to_have_text('1540')
   page.locator('#select-page').check();assert int(page.locator('#processing-quantity').inner_text().replace(',',''))==page.locator('[name=allocations]').evaluate_all('(els)=>els.reduce((s,e)=>s+Number(e.dataset.quantity),0)')
   page.locator('[name=scope][value=filtered]').check();expect(page.locator('#processing-quantity')).to_have_text('1,540')
   page.goto(base+'/inbound/');assert page.locator('.mobile-bottom-nav').count()==0
   text='품목코드\t품목명\t규격\t수량\t비고\n001\t가상 제품\tD값\t64.\t32+32\n품목별 합계\t\t\t64\n001\t다른 제품\t규격 둘\t12.\t\n전체 합계\t\t\t76'
   upload_counter=[0]
   def upload(name,data):
    upload_counter[0]+=1;name=str(upload_counter[0])+'-'+name
    page.locator('#inbound-file').set_input_files({'name':name,'mimeType':'application/octet-stream','buffer':data})
    expect(page.locator('#inbound-filename')).to_contain_text(name)
    expect(page.locator('#inbound-totalQty')).to_have_text('76');expect(page.locator('#inbound-locationStatus')).to_contain_text('확인 완료')
   private=os.environ.get('ERP_PRIVATE_INBOUND_FIXTURE')
   if private:
    import csv,io
    from decimal import Decimal
    content=Path(private).read_bytes();source=list(csv.reader(io.StringIO(content.decode('cp949')),delimiter='\t'));header=source[0];q=header.index('수량');code=header.index('품목코드')
    detail=[r for r in source[1:] if len(r)>q and r[code] and not any(word in r[code] for word in ('합계','소계','총계'))]
    total=sum(Decimal(r[q].replace(',','')) for r in detail)
    page.locator('#inbound-file').set_input_files({'name':'private-fixture.xls','mimeType':'application/octet-stream','buffer':content})
    expect(page.locator('#inbound-filename')).to_contain_text('private-fixture.xls')
    expect(page.locator('#inbound-editBody tr')).to_have_count(len(detail))
    assert Decimal(page.locator('#inbound-totalQty').inner_text().replace(',',''))==total
    assert not page.locator('#inbound-msg').inner_text()
    print('PASS: 비공개 첨부 파일 전체 상세 행·수량·분할 브라우저 검증')
   for encoding in ('cp949','utf-8'):
    upload('fixture.xls',text.encode(encoding));expect(page.locator('#inbound-rowCount')).to_have_text('3')
   page.locator('#inbound-applyLocations').click();page.locator('#inbound-previewTab').click();expect(page.locator('.rowNote').first).to_have_value('A01')
   page.locator('.rowNote').nth(1).fill('수동 보관');page.locator('#inbound-applyLocations').click();expect(page.locator('.rowNote').nth(1)).to_have_value('수동 보관')
   page.locator('.preview-group .rowSelect').first.check();page.locator('#inbound-deleteSelected').click();expect(page.locator('#inbound-totalQty')).to_have_text('44')
   page.locator('#inbound-undoDelete').click();expect(page.locator('#inbound-totalQty')).to_have_text('76')
   page.evaluate("navigator.clipboard.write=()=>Promise.reject(Error('test HTTP fallback'));window.addEventListener('copy',e=>window.fixtureClipboard={text:e.clipboardData.getData('text/plain'),html:e.clipboardData.getData('text/html')})")
   page.locator('#inbound-copyERP').click();expect(page.locator('#inbound-copyStatus')).to_contain_text('3행 복사 완료')
   clip=page.evaluate('window.fixtureClipboard');assert len(clip['text'].split('\r\n')[0].split('\t'))==5;assert clip['html'].count('<td')==15
   with page.expect_download() as download:page.locator('#inbound-download').click()
   download.value.save_as(OUT/'fixture.xlsx')
   from openpyxl import load_workbook
   ws=load_workbook(OUT/'fixture.xlsx').active;assert ws.column_dimensions['E'].hidden;assert str(next(m for m in ws.merged_cells.ranges if str(m)=='E3:E4'))=='E3:E4';assert ws['F4'].value=='수동 보관'
   for kind in ('biff8','xlsx','xlsm'):
    payload=page.evaluate("""kind=>{let b=XLSX.utils.book_new();XLSX.utils.book_append_sheet(b,XLSX.utils.aoa_to_sheet([['무시']]),'FIRST');XLSX.utils.book_append_sheet(b,XLSX.utils.aoa_to_sheet([['품목코드','품목명','규격','수량','비고'],['001','가상 제품','D값',64,'32+32'],['001','다른 제품','규격 둘',12,'']]),'RAW');return Array.from(XLSX.write(b,{type:'array',bookType:kind}) instanceof ArrayBuffer?new Uint8Array(XLSX.write(b,{type:'array',bookType:kind})):[])}""",kind)
    upload('fixture.'+('xls' if kind=='biff8' else kind),bytes(payload))
   page.locator('#inbound-previewTab').click();page.emulate_media(media='print');page.pdf(path=str(OUT/'sample.pdf'),prefer_css_page_size=True,print_background=True)
   import fitz
   doc=fitz.open(OUT/'sample.pdf');assert abs(doc[0].rect.width-595.3)<2 and abs(doc[0].rect.height-841.9)<2;printed=''.join(p.get_text() for p in doc);assert '총수량' in printed and 'D값' not in printed
   page.emulate_media(media='screen');page.locator('#inbound-editTab').click();page.locator('.split').first.fill('31+32');expect(page.locator('#inbound-print')).to_be_disabled();expect(page.locator('#inbound-copyERP')).to_be_disabled();expect(page.locator('#inbound-download')).to_be_disabled()
   page.emulate_media(media='print');assert page.locator('#inbound-preview').is_hidden();page.emulate_media(media='screen')
   many='품목코드\t품목명\t규격\t수량\t비고\n'+'\n'.join(f'001\t가상 제품 {i}\t인쇄제외규격\t64.\t32+32' for i in range(40))
   page.locator('#inbound-file').set_input_files({'name':'multi.xls','mimeType':'application/octet-stream','buffer':many.encode()});expect(page.locator('#inbound-rowCount')).to_have_text('80');page.locator('#inbound-previewTab').click();page.emulate_media(media='print');page.pdf(path=str(OUT/'multipage.pdf'),prefer_css_page_size=True,print_background=True)
   doc=fitz.open(OUT/'multipage.pdf');assert len(doc)>1
   for pdfpage in doc:assert '품목코드' in pdfpage.get_text() and '총수량' in pdfpage.get_text() and '인쇄제외규격' not in pdfpage.get_text()
   page.emulate_media(media='screen');page.screenshot(path=str(OUT/'inbound.png'),full_page=True)
   assert not errors,errors;browser.close()
  print(json.dumps({'success':True,'checks':['stock_scroll_filters','filtered_selected_totals','cp949_utf8_xls_xlsx_xlsm_RAW','location_manual_protection','delete_undo','clipboard_dual_format','excel_merge_hidden','A4_portrait_repeated_headers','mismatch_blocks_output']},ensure_ascii=False))
 finally:
  server.terminate();server.wait(timeout=10)
