"""2026-11 update browser checks: synthetic SQLite, local Waitress, desktop/mobile."""
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parent.parent
ARTIFACTS=ROOT/'work/browser-update'
ARTIFACTS.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='sunil-update-') as directory, tempfile.TemporaryFile() as log:
    env=os.environ.copy()
    for key in list(env):
        if key.startswith('ERP_DB_'):env.pop(key)
    env.update(DJANGO_SETTINGS_MODULE='config.settings',ERP_DEBUG='1',ERP_SQLITE_PATH=str(Path(directory)/'fixture.sqlite3'))
    password=secrets.token_urlsafe(20)
    env['SUNIL_FIXTURE_PASSWORD']=password
    subprocess.run([sys.executable,'manage.py','migrate','--noinput'],cwd=ROOT,env=env,check=True,stdout=subprocess.DEVNULL)
    seed='''import os,json
from django.contrib.auth import get_user_model
from inventory.models import Item,Stock
for name,staff in [('admin',True),('worker',False)]:get_user_model().objects.create_user(name,password=os.environ['SUNIL_FIXTURE_PASSWORD'],is_staff=staff)
i=Item.objects.create(code='BROWSER-PICK',name='ACS 피킹 검증',specification='IP56')
ids={}
for loc,qty in [('A',34),('B',3),('C',100)]:ids[loc]=Stock.objects.create(item=i,location=loc,quantity=qty).pk
j=Item.objects.create(code='BROWSER-CLEAN',name='ACS 정리 검증',specification='IP65')
for loc,qty in [('DEST',20),('SOURCE1',1),('SOURCE2',3)]:ids[loc]=Stock.objects.create(item=j,location=loc,quantity=qty).pk
print(json.dumps(ids))'''
    result=subprocess.run([sys.executable,'manage.py','shell','-c',seed],cwd=ROOT,env=env,check=True,text=True,capture_output=True)
    ids=json.loads(result.stdout.strip().splitlines()[-1])
    env.pop('SUNIL_FIXTURE_PASSWORD')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    code=f"from config.wsgi import application;from django.contrib.staticfiles.handlers import StaticFilesHandler;from waitress import serve;serve(StaticFilesHandler(application),host='127.0.0.1',port={port},threads=4)"
    server=subprocess.Popen([sys.executable,'-c',code],cwd=ROOT,env=env,stdout=log,stderr=log)
    try:
        for _ in range(100):
            try:
                urlopen(base+'/login/',timeout=.5).close();break
            except OSError:time.sleep(.1)
        else:raise RuntimeError('fixture server startup failed')
        with sync_playwright() as play:
            browser=play.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1440,'height':1000})
            errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            def login(page,name):
                page.goto(base+'/login/')
                page.locator('[name=username]').fill(name)
                page.locator('[name=password]').fill(password)
                page.get_by_role('button',name='로그인',exact=True).click()
                page.wait_for_url(base+'/')
            def check_navigation(page):
                nav = page.locator('.primary-nav')
                for title in ['출고 관리', '재고 관리', '관리']:
                    nav.locator('summary').filter(has_text=title).last.click()
                    assert nav.locator('details[open]').count() == 1
                page.locator('main').click(position={'x':5,'y':5})
                assert nav.locator('details[open]').count() == 0
                summary = nav.locator('summary').first
                summary.focus()
                page.keyboard.press('Enter')
                assert nav.locator('details[open]').count() == 1
                page.keyboard.press('Escape')
                assert nav.locator('details[open]').count() == 0
                assert summary.evaluate('node => node === document.activeElement')
                # Exercise the fallback without the native exclusive group.
                nav.locator('details').evaluate_all("nodes => nodes.forEach(node => node.removeAttribute('name'))")
                for summary in nav.locator('summary').all():
                    summary.click()
                    assert nav.locator('details[open]').count() == 1
                page.keyboard.press('Escape')
                assert nav.locator('details[open]').count() == 0
            def check_combined_search(page):
                page.locator('.stock-advanced-filter > summary').click()
                page.locator('[name=model]').fill('acs')
                page.locator('[name=spec]').fill('ip56')
                page.wait_for_function("new URL(location.href).searchParams.get('model')==='acs' && new URL(location.href).searchParams.get('spec')==='ip56' && document.querySelector('#stock-search-status').textContent==='조회 완료'")
                assert page.locator('#stock-results .stock-row').count() == 3
                assert 'BROWSER-CLEAN' not in page.locator('#stock-results').inner_text()
                page.screenshot(path=str(ARTIFACTS / ('combined-search-mobile.png' if page.viewport_size['width'] < 900 else 'combined-search-desktop.png')),full_page=True)
                page.locator('[name=model]').fill('')
                page.locator('[name=spec]').fill('')
                page.wait_for_function("!new URL(location.href).searchParams.get('model') && !new URL(location.href).searchParams.get('spec') && document.querySelector('#stock-search-status').textContent==='조회 완료'")
                assert page.locator('#stock-results .stock-row').count() == 6
            login(page,'admin')
            check_navigation(page)
            check_combined_search(page)
            nav=page.locator('.primary-nav')
            assert nav.get_by_text('출고 관리',exact=True).is_visible()
            nav.get_by_text('출고 관리',exact=True).click()
            nav.get_by_role('link',name='피킹 요청',exact=True).click()
            page.locator('[name=project]').fill('브라우저 회귀')
            page.locator('[name=pasted_rows]').fill('37\tBROWSER-PICK')
            page.get_by_role('button',name='배정 미리보기',exact=True).click()
            assert page.get_by_role('cell',name='34',exact=True).count()>=1
            page.get_by_text('예약 전 위치·수량 수동 조정',exact=True).click()
            page.locator('[name=manual_targets]').fill(f"{ids['C']} 37")
            page.get_by_role('button',name='수동 배정 미리보기',exact=True).click()
            page.get_by_role('button',name='위 배정 결과로 예약 등록',exact=True).click()
            page.wait_for_url('**/picking/*/')
            page.get_by_role('link',name='위치·수량 변경',exact=True).first.click()
            page.locator('[name=targets]').fill(f"{ids['A']} 34\n{ids['B']} 3")
            page.locator('[name=reason]').fill('작업자 위치 확인')
            page.locator('[name=confirmed]').check()
            page.get_by_role('button',name='예약 배정 변경',exact=True).click()
            page.wait_for_url('**/picking/*/')
            assert page.get_by_role('cell',name='34',exact=True).count()>=1
            page.screenshot(path=str(ARTIFACTS/'picking-desktop.png'),full_page=True)
            page.goto(base+'/cleanup/')
            assert 'SOURCE1' in page.content() and 'SOURCE2' in page.content()
            assert '정리 후 24' in page.locator('main').inner_text()
            page.pdf(path=str(ARTIFACTS/'cleanup-print.pdf'),format='A4',landscape=True,print_background=True)
            page.screenshot(path=str(ARTIFACTS/'cleanup-desktop.png'),full_page=True)
            page.goto(base+'/')
            page.locator('#scan-open').click()
            assert page.locator('#scan-dialog').is_visible()
            assert 'Android' in page.locator('#scan-status').inner_text()
            assert page.locator('#binaryeye-launch').is_hidden()
            page.locator('#scan-close').click()
            # Return query is percent-encoded by the middleware and searched exactly.
            page.goto(base+'/?scan=1&content=BROWSER-PICK')
            assert 'q=BROWSER-PICK' in page.url
            assert page.locator('.scan-result-code').inner_text()=='BROWSER-PICK'
            mobile=browser.new_context(viewport={'width':390,'height':844},is_mobile=True,has_touch=True,
                user_agent='Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/131.0.0.0 Mobile Safari/537.36')
            phone=mobile.new_page();phone.on('pageerror',lambda error:errors.append(str(error)))
            login(phone,'worker')
            check_navigation(phone)
            check_combined_search(phone)
            assert phone.locator('.readonly-role').is_visible()
            assert phone.locator('.primary-nav').get_by_role('link',name='직원 계정').count()==0
            phone.locator('.primary-nav').get_by_text('재고 관리',exact=True).click()
            phone.locator('.primary-nav').get_by_role('link',name='정리 리스트',exact=True).click()
            phone.get_by_role('link',name='1개 이동 요청',exact=True).click()
            assert phone.locator('[name=destination]').input_value()=='DEST'
            phone.locator('[name=confirmed]').check()
            phone.get_by_role('button',name='이동 요청 등록',exact=True).click()
            phone.wait_for_url('**/movements/*/')
            assert '대기' in phone.locator('main').inner_text()
            assert phone.locator('[name=action]').count()==0
            phone.screenshot(path=str(ARTIFACTS/'movement-worker-mobile.png'),full_page=True)
            phone.goto(base+'/')
            phone.locator('#scan-open').click()
            phone.screenshot(path=str(ARTIFACTS/'binaryeye-debug.png'),full_page=True)
            phone.locator('#scan-dialog').wait_for(state='visible')
            href=phone.locator('#binaryeye-launch').get_attribute('href')
            from urllib.parse import urlparse,parse_qs
            assert href.startswith('binaryeye://scan?ret=')
            ret=parse_qs(urlparse(href).query)['ret'][0]
            assert ret==base+'/?scan=1&content={RESULT}'
            phone.screenshot(path=str(ARTIFACTS/'binaryeye-mobile.png'),full_page=True)
            phone.locator('#scan-close').click()
            # All five groups remain reachable at narrow widths.
            for width in [360,390,768]:
                phone.set_viewport_size({'width':width,'height':844})
                assert phone.evaluate('document.documentElement.scrollWidth <= innerWidth')
                assert phone.locator('.primary-nav').get_by_text('管理',exact=True).count()==0
                assert phone.locator('.primary-nav').get_by_text('관리',exact=True).is_visible()
            assert not errors,errors
            browser.close()
        print('PASS: desktop/mobile navigation, pre-reservation manual preview, post-reservation split, worker movement, cleanup print, Binary Eye launch URL/fallback/return; no browser errors')
    finally:
        server.terminate()
        try:server.wait(timeout=10)
        except subprocess.TimeoutExpired:server.kill();server.wait()
