"""Disposable integration checks with synthetic workbooks and a private test DB."""
import json
import sys
import os
from pathlib import Path
import subprocess
import socket
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright

root=Path(__file__).resolve().parents[1]; python=os.environ.get('ERP_TEST_PYTHON',sys.executable)
artifacts=Path(os.environ.get('ERP_BROWSER_ARTIFACTS',tempfile.mkdtemp(prefix='production-browser-')));artifacts.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory() as td:
    env={**os.environ,'ERP_SQLITE_PATH':td+'/test.sqlite3','ERP_DEBUG':'1','ERP_DB_NAME':''}
    subprocess.run([python,'manage.py','migrate','--noinput'],cwd=root,env=env,check=True,stdout=subprocess.DEVNULL)
    seed="from django.contrib.auth import get_user_model; get_user_model().objects.create_superuser('plan-test','test@example.test','Browser-Test-Only-927!')"
    subprocess.run([python,'manage.py','shell','-c',seed],cwd=root,env=env,check=True,stdout=subprocess.DEVNULL)
    # Generate a shifted workbook with two distinct rows and realistic long fields.
    seed_file=Path(td)/'plan.xlsx'
    code='''import sys
sys.path.insert(0,'.')
from inventory.test_production import fixture
from inventory.production import open_workbook
from pathlib import Path
from io import BytesIO
raw,mapping=fixture(1,True)
w=open_workbook(raw);s=w.active
s[f"{mapping['Z']}4"]='ERP-CODE-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
s[f"{mapping['AB']}4"]='<script>window.bad=1</script>'
s[f"{mapping['S']}4"]='=HYPERLINK("https://example.test")'
s[f"{mapping['S']}4"].data_type='s'
s[f"{mapping['P']}4"]=2
s[f"{mapping['P']}5"]=10
w.save(sys.argv[1]);w.close()
'''
    generate=Path(td)/'generate.py';generate.write_text(code)

    # Django setup is needed because the fixture module imports models.
    env_gen={**env,'DJANGO_SETTINGS_MODULE':'config.settings','PYTHONPATH':str(root)}
    generate.write_text('import django\ndjango.setup()\n'+code)
    subprocess.run([python,str(generate),str(seed_file)],cwd=root,env=env_gen,check=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    base=f'http://127.0.0.1:{port}'
    log=open(artifacts/'server.log','w')
    server=subprocess.Popen([python,'manage.py','runserver',f'127.0.0.1:{port}','--noreload'],cwd=root,env=env,stdout=log,stderr=log)
    try:
        for _ in range(80):
            try: urllib.request.urlopen(base+'/login/');break
            except Exception: time.sleep(.15)
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=os.environ.get('ERP_CHROMIUM','/usr/bin/chromium'),args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':1920,'height':1080},accept_downloads=True)
            errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(base+'/login/')
            page.locator('[name=username]').fill('plan-test');page.locator('[name=password]').fill('Browser-Test-Only-927!');page.locator('button[type=submit],form button').first.click()
            page.goto(base+'/production/settings/')
            page.locator('[name=workbook]').set_input_files(str(seed_file));page.get_by_role('button',name='파일 읽기',exact=True).click()
            assert page.locator('select[name^=col_] option:checked').count()==13
            assert all(page.locator('select[name^=col_]').nth(i).input_value() for i in range(13))
            page.screenshot(path=str(artifacts/'mapping.png'),full_page=True)
            page.get_by_role('button',name='설정 저장 및 공유 자료 적용').click()
            page.wait_for_selector('#plan-table tbody tr')
            assert page.locator('#plan-table tbody tr').count()==2
            assert page.locator('#plan-table tbody tr').first.locator('td').nth(1).inner_text()=='인증절차 대기건'
            assert page.evaluate('window.bad') is None
            page.locator('#plan-table tbody tr').first.click()
            assert page.locator('#plan-selected tbody tr').count()==1
            assert page.locator('#plan-parts').inner_text().find('V-4')>=0
            assert page.locator('#plan-selected tbody tr td').all_text_contents()==page.locator('#plan-table tbody tr').first.locator('td').all_text_contents()
            for index in (1,8):
                cell=page.locator('#plan-table tbody tr').first.locator('td').nth(index)
                assert cell.evaluate('(e)=>e.scrollWidth<=e.clientWidth'),f'clipped column {index}'
            assert page.locator('#plan-table tbody tr').first.locator('td').nth(4).evaluate('(e)=>getComputedStyle(e).textAlign')=='right'
            assert page.locator('#plan-table tbody tr').first.locator('td').nth(1).evaluate('(e)=>getComputedStyle(e).textAlign')=='center'
            page.locator('#plan-table [data-sort="5"]').click()
            assert page.locator('#plan-table tbody tr').first.locator('td').nth(5).inner_text()=='2'
            page.locator('#plan-query').fill('X-5');assert page.locator('#plan-table tbody tr').count()==1
            page.locator('#plan-table tbody tr').click();assert 'V-5' in page.locator('#plan-parts').inner_text()
            page.locator('#plan-query').fill('no-match');assert page.locator('#plan-table tbody tr').count()==0
            assert page.locator('#plan-selected tbody tr').count()==0
            page.locator('#plan-reset').click()
            with page.expect_download() as download:
                page.locator('#plan-export').click()
            download.value.save_as(str(artifacts/'export.csv'))
            assert "'=HYPERLINK" in (artifacts/'export.csv').read_text(encoding='utf-8-sig')
            page.locator('#plan-table tbody tr').first.click()
            page.screenshot(path=str(artifacts/'desktop.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            page.screenshot(path=str(artifacts/'mobile.png'),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
            assert not errors,errors
            browser.close()
        print(json.dumps({'browser':'passed','checks':['shifted mapping','shared apply','search','sort','selection','alignment','unclipped schedule and ERP','CSV formula safety','XSS text','mobile'],'artifacts':str(artifacts)}))
    finally:
        server.terminate();server.wait(timeout=10);log.close()
