"""CI-only PostgreSQL18 full backup/apply/undo/restore with disposable data."""
import os,sys,tempfile,subprocess,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
if os.environ.get('ERP_DB_NAME')!='integration_test' or os.environ.get('ERP_DB_HOST')!='127.0.0.1' or not os.environ.get('ERP_TEST_PG_CONTAINER'):raise SystemExit('일회성 CI PostgreSQL 서비스에서만 실행합니다.')
import django;django.setup()
from django.core.management import call_command
from django.conf import settings
from django.contrib.auth import get_user_model
from inventory.models import Item,Stock,Allocation,StocktakeSession
from inventory import stocktake
from inventory.backups import verify_backup
from django.core.files.uploadedfile import SimpleUploadedFile
from io import BytesIO
from openpyxl import load_workbook
call_command('migrate',verbosity=0)
with tempfile.TemporaryDirectory(prefix='stocktake-pg-check-') as tmp:
 directory=Path(tmp);pg=directory/'bin';pg.mkdir()
 # Clients run inside the PostgreSQL18 test container. Only dump bytes cross the
 # boundary, so host pg_dump versions and production credentials are irrelevant.
 wrapper='''#!/usr/bin/env python3
import os,sys,subprocess
from pathlib import Path
name=Path(sys.argv[0]).name;args=sys.argv[1:];file=None
if '--file' in args:
 i=args.index('--file');file=Path(args[i+1]);del args[i:i+2]
elif args and Path(args[-1]).is_file():file=Path(args.pop())
cmd=['docker','exec','-i']
for key in ('PGDATABASE','PGUSER','PGPASSWORD'):cmd+=['-e',key+'='+os.environ.get(key,'')]
cmd+=['-e','PGHOST=127.0.0.1','-e','PGPORT=5432',os.environ['ERP_TEST_PG_CONTAINER'],name,*args]
result=subprocess.run(cmd,input=file.read_bytes() if name=='pg_restore' and file else None,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
if name=='pg_dump' and file:file.write_bytes(result.stdout)
else:sys.stdout.buffer.write(result.stdout)
sys.stderr.buffer.write(result.stderr);sys.exit(result.returncode)
'''
 for name in ('pg_dump','pg_restore'):
  path=pg/name;path.write_text(wrapper);path.chmod(0o700)
 os.environ['ERP_PG_BIN']=str(pg);os.environ['ERP_STOCKTAKE_BACKUP_DIR']=str(directory/'backups')
 owner=get_user_model().objects.create_superuser('pg-count-fixture');item=Item.objects.create(code='PG-COUNT',name='가상 제품',specification='가상 규격');stock=Stock.objects.create(item=item,location='A',quantity=15,reserved=4);Allocation.objects.create(stock=stock,project='PACKED',quantity=4)
 session=stocktake.start(owner);book=load_workbook(BytesIO(stocktake.workbook(session)));book.active['M2']=10;book.active['N2']='가상 실사 부족';out=BytesIO();book.save(out)
 session=stocktake.preview(session,SimpleUploadedFile('fixture.xlsx',out.getvalue()),owner);session=stocktake.apply(session.pk,session.preview_id,owner);stock.refresh_from_db();assert(stock.quantity,stock.reserved)==(14,4)
 backup=Path(session.backup_path);verify_backup(backup);stocktake.reverse(session.pk,owner);stock.refresh_from_db();assert(stock.quantity,stock.reserved)==(15,4)
 import psycopg
 from psycopg import sql
 config=settings.DATABASES['default'];dbargs=dict(host=config['HOST'],port=config['PORT'],user=config['USER'],password=config['PASSWORD'])
 restored='stocktake_restore_'+uuid.uuid4().hex[:12]
 with psycopg.connect(dbname='postgres',autocommit=True,**dbargs) as admin:
  admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(restored)))
  try:
   env={**os.environ,'ERP_DB_NAME':restored}
   subprocess.run([sys.executable,'manage.py','restore_database',str(backup),'--apply','--pg-bin',str(pg)],cwd=ROOT,env=env,check=True,stdout=subprocess.DEVNULL)
   with psycopg.connect(dbname=restored,**dbargs) as restored_db:
    quantity,reserved=restored_db.execute('SELECT quantity,reserved FROM inventory_stock WHERE id=%s',(stock.pk,)).fetchone();assert(quantity,reserved)==(15,4)
    assert restored_db.execute('SELECT octet_length(source_file) FROM inventory_stocktakesession WHERE id=%s',(session.pk,)).fetchone()[0]>0
  finally:admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(restored)))
 print('PASS: PostgreSQL18 실제 백업·검증·실사 반영·되돌리기·새 DB 복원 및 원본 파일 보존')
