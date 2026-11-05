"""Actual browser checks with a disposable SQLite database and synthetic staff."""
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
from playwright.sync_api import sync_playwright, expect

ROOT=Path(__file__).resolve().parent.parent
ARTIFACTS=Path(os.environ.get('ERP_BROWSER_ARTIFACTS',ROOT/'work/menu-permission-browser'))
ARTIFACTS.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='sunil-menu-browser-') as directory, tempfile.TemporaryFile() as log:
    env={**os.environ,'ERP_DB_NAME':'','ERP_DEBUG':'1','ERP_SQLITE_PATH':str(Path(directory)/'test.sqlite3')}
    password=secrets.token_urlsafe(18);env['SUNIL_FIXTURE_PASSWORD']=password
    subprocess.run([sys.executable,'manage.py','migrate','--noinput'],cwd=ROOT,env=env,check=True,stdout=subprocess.DEVNULL)
    seed='''import os,json
from django.contrib.auth import get_user_model
from inventory.models import Item,Stock,EmployeeMenuAccess
owner=get_user_model().objects.create_user('notice-owner',is_staff=True,password=os.environ['SUNIL_FIXTURE_PASSWORD'])
worker=get_user_model().objects.create_user('notice-worker',password=os.environ['SUNIL_FIXTURE_PASSWORD'])
EmployeeMenuAccess.objects.create(user=worker)
i=Item.objects.create(code='NOTICE-FIXTURE');s=Stock.objects.create(item=i,location='NOTICE-A',quantity=10)
print(json.dumps({'worker':worker.pk,'stock':s.pk}))'''
    result=subprocess.check_output([sys.executable,'manage.py','shell','-c',seed],cwd=ROOT,env=env,text=True)
    ids=json.loads(result.strip().splitlines()[-1]);env.pop('SUNIL_FIXTURE_PASSWORD')
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    code=f"from config.wsgi import application;from django.contrib.staticfiles.handlers import StaticFilesHandler;from waitress import serve;serve(StaticFilesHandler(application),host='127.0.0.1',port={port},threads=4)"
    server=subprocess.Popen([sys.executable,'-c',code],cwd=ROOT,env=env,stdout=log,stderr=log)
    try:
        for _ in range(100):
            try:urlopen(base+'/login/',timeout=.5).close();break
            except OSError:time.sleep(.1)
        with sync_playwright() as play:
            browser=play.chromium.launch(executable_path=os.environ.get('ERP_CHROMIUM','/usr/bin/chromium'),args=['--no-sandbox'])
            owner=browser.new_page(viewport={'width':1600,'height':1000});worker=browser.new_page(viewport={'width':390,'height':844})
            errors=[]
            for page in (owner,worker):page.on('pageerror',lambda e:errors.append(str(e)))
            def login(page,name):
                page.goto(base+'/login/');page.locator('[name=username]').fill(name);page.locator('[name=password]').fill(password)
                page.get_by_role('button',name='로그인',exact=True).click();page.wait_for_url(base+'/')
            def change_rights(**values):
                owner.goto(base+f'/admin/auth/user/{ids["worker"]}/change/')
                assert owner.locator('select[name$="_access"]').count()==5
                for key in ('receiving','shipping','production','inventory','management'):
                    owner.locator('[name='+key+'_access]').select_option(values.get(key,'read'))
                owner.screenshot(path=str(ARTIFACTS/'employee-menu-permissions.png'),full_page=True)
                owner.locator('input[name=_save]').click()
                owner.wait_for_url('**/admin/auth/user/')
            login(owner,'notice-owner');login(worker,'notice-worker')
            expect(owner.locator('#movement-pending-notice')).to_be_hidden()
            assert worker.locator('#movement-pending-notice').count()==0
            nav=owner.locator('.primary-nav')
            assert nav.locator('> .extension-nav > a').all_inner_texts()==['생산계획조회','입고 LIST']
            last_menu=nav.locator('> details').last.bounding_box();extra=nav.locator('> .extension-nav').bounding_box()
            assert extra['x']>last_menu['x']+last_menu['width']
            worker.goto(base+f'/stock/{ids["stock"]}/move/')
            worker.locator('[name=destination]').fill('NOTICE-B');worker.locator('[name=quantity]').fill('2');worker.locator('[name=confirmed]').check()
            worker.get_by_role('button',name='이동 요청 등록',exact=True).click();worker.wait_for_url('**/movements/*/')
            movement_url=worker.url
            owner.bring_to_front();owner.evaluate("window.dispatchEvent(new Event('focus'))")
            expect(owner.locator('#movement-pending-notice')).to_be_visible()
            expect(owner.locator('[data-pending-count]')).to_have_text('1')
            owner.screenshot(path=str(ARTIFACTS/'pending-notice-desktop.png'),full_page=True)
            owner.locator('#movement-pending-notice a').click()
            expect(owner.locator('select[name=status]')).to_have_value('pending')
            assert 'NOTICE-FIXTURE' in owner.locator('main').inner_text()
            change_rights(receiving='full')
            worker.goto(base+'/receipts/new/')
            expect(worker.locator('form[method=post]').filter(has=worker.locator('[name=item_mode]'))).to_be_visible()
            assert worker.request.post(base+'/picking/new/',data={}).status==403
            assert worker.request.get(base+'/production/settings/').status==403
            change_rights(production='full')
            worker.goto(base+'/production/settings/');expect(worker.locator('[name=workbook]')).to_be_visible()
            assert worker.request.post(base+'/receipts/new/',data={}).status==403
            change_rights(inventory='full')
            worker.goto(movement_url)
            # The existing product intentionally executes movements from the PC layout.
            worker.set_viewport_size({'width':1440,'height':1000})
            worker.locator('[name=action]').select_option('process');worker.locator('[name=confirmed]').check()
            worker.get_by_role('button',name='확인 후 처리',exact=True).click()
            expect(worker.locator('main')).to_contain_text('완료')
            owner.goto(base+'/');expect(owner.locator('#movement-pending-notice')).to_be_hidden()
            worker.set_viewport_size({'width':390,'height':844});worker.goto(base+'/production/')
            worker.locator('#mobile-menu-toggle').click()
            assert worker.locator('.extension-nav').is_visible()
            assert worker.evaluate('document.documentElement.scrollWidth<=innerWidth')
            worker.screenshot(path=str(ARTIFACTS/'production-section-mobile.png'),full_page=True)
            assert not errors,errors
            browser.close()
        verify="from inventory.models import Stock,MovementRequest; from django.contrib.auth import get_user_model; assert Stock.objects.get(location='NOTICE-A').quantity==8; assert Stock.objects.get(location='NOTICE-B').quantity==2; assert MovementRequest.objects.get().state=='completed'; assert not get_user_model().objects.get(username='notice-worker').is_staff"
        subprocess.run([sys.executable,'manage.py','shell','-c',verify],cwd=ROOT,env=env,check=True,stdout=subprocess.DEVNULL)
        print('PASS: five menu selectors, session permission changes, cross-menu POST denial, worker request, owner live notice, delegated inventory approval without staff, resolved notice, right-separated production section and mobile layout.')
    finally:server.terminate();server.wait(timeout=10)
