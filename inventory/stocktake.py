"""Superuser-only count/adjustment with immutable baselines and verified backups."""
import hashlib,io,json,os,uuid
from pathlib import Path
from functools import wraps
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.utils import timezone
from django.db import connection
from .models import Stock,Item,Allocation,ItemRevision,StocktakeSession,AuditEvent
from .locking import warehouse_lock
from .tabular import read_table,cell,integer

HEADERS=['ERP 코드','모델명','사양','창고','위치','입고 상태','현재고','allocated','가용량','품목 전체 출하대기','예정일','담당자','실사수량','사유','재고 식별번호','실사 기준수량','포장 제외수량','실사 회차']

def super_only(view):
 @login_required
 @wraps(view)
 def wrapped(request,*args,**kwargs):
  require_super(request.user)
  return view(request,*args,**kwargs)
 return wrapped

def require_super(actor):
 if not actor.is_active or not actor.is_superuser:raise PermissionDenied('SUPER USER만 사용할 수 있습니다.')

def state(codes):
 codes=sorted(set(codes));items={i.code:i for i in Item.objects.filter(code__in=codes)};result={}
 for code in codes:
  item=items.get(code)
  if not item:result[code]=None;continue
  result[code]={'item':[item.pk,item.name,item.specification], 'revision':ItemRevision.objects.filter(item=item).values_list('revision',flat=True).first() or 0,
   'stocks':list(Stock.objects.filter(item=item).order_by('pk').values('id','warehouse','location','status','quantity','reserved','expected_date','sender')),
   'allocations':list(Allocation.objects.filter(stock__item=item).order_by('pk').values('id','stock_id','quantity','state','project','legacy_seq','released_at','cancelled_at'))}
 # Canonical JSON values supported by JSONField (UUID/date converted).
 return json.loads(json.dumps(result,default=str,sort_keys=True))

def snapshot(rows):
 data=[]
 for s in rows:
  data.append({'id':s.pk,'code':s.item.code,'name':s.item.name,'spec':s.item.specification,'warehouse':s.warehouse,'location':s.location,'status':s.status,'quantity':s.quantity,'reserved':s.reserved,'available':s.quantity-s.reserved,'expected_date':str(s.expected_date or ''),'sender':s.sender})
 return {'rows':data,'state':state(r['code'] for r in data)}

def start(actor,query='',kind='count',stock_id=None):
 require_super(actor)
 if kind not in ('count','adjust'):raise ValidationError('처리 구분을 확인하세요.')
 with warehouse_lock():
  rows=Stock.objects.select_related('item').filter(warehouse='Y09').order_by('item__code','location','pk')
  if kind=='count':rows=rows.filter(status='received',quantity__gt=0)
  if stock_id:rows=rows.filter(pk=stock_id)
  if query:
   from django.db.models import Q
   rows=rows.filter(Q(item__code__icontains=query)|Q(item__name__icontains=query)|Q(location__icontains=query))
  if rows.count()>10000:raise ValidationError('10,000행 이하가 되도록 대상을 검색하세요.')
  return StocktakeSession.objects.create(actor=actor,kind=kind,query=query,snapshot=snapshot(rows))

def workbook(session):
 from openpyxl import Workbook
 from openpyxl.styles import PatternFill,Font
 from openpyxl.utils import get_column_letter
 book=Workbook();sheet=book.active;sheet.title='재고실사';sheet.append(HEADERS)
 for r in session.snapshot['rows']:
  excluded=r['reserved'] if session.kind=='count' else 0
  sheet.append([r['code'],r['name'],r['spec'],r['warehouse'],r['location'],'입고 완료' if r['status']=='received' else '입고 예정',r['quantity'],r['reserved'],r['quantity']-r['reserved'],None,r['expected_date'],r['sender'],None,None,str(r['id']),r['quantity']-excluded,excluded,str(session.pk)])
  for c in sheet[sheet.max_row]:
   if isinstance(c.value,str):c.data_type='s'
 sheet.freeze_panes='C2';sheet.auto_filter.ref=sheet.dimensions
 for i,c in enumerate(sheet[1],1):
  c.fill=PatternFill('solid',fgColor='FFF200' if i in (13,14) else '19344F');c.font=Font(color='000000' if i in (13,14) else 'FFFFFF',bold=True)
  sheet.column_dimensions[get_column_letter(i)].width={1:27,2:35,3:55,5:18,13:15,14:35,18:40}.get(i,18)
 meta=book.create_sheet('_실사정보');meta.append(['실사 회차',str(session.pk)]);meta.sheet_state='hidden'
 sheet.print_title_rows='1:1';sheet.sheet_properties.pageSetUpPr.fitToPage=True;sheet.page_setup.orientation='landscape';sheet.page_setup.paperSize=sheet.PAPERSIZE_A4;sheet.page_setup.fitToWidth=1;sheet.page_setup.fitToHeight=0
 out=io.BytesIO();book.save(out);return out.getvalue()

def check_baseline(session):
 if state(session.snapshot['state'])!=session.snapshot['state']:raise ValidationError('다운로드 이후 관련 재고·예약·품목이 변경됐습니다. 새 실사 회차로 다시 확인하세요.')

def preview(session,upload,actor):
 require_super(actor)
 with warehouse_lock():
  current=StocktakeSession.objects.select_for_update().get(pk=session.pk)
  if current.state!='open':raise ValidationError('이미 처리되거나 취소된 실사입니다.')
  current.proposal=[];current.preview_id=uuid.uuid4();current.save(update_fields=['proposal','preview_id'])
 table,content=read_table(upload)
 if not table:raise ValidationError('자료가 없습니다.')
 headers=[cell(v) for v in table[0]]
 for h in HEADERS[14:]:
  if headers.count(h)>1:raise ValidationError('중복된 열 이름입니다: '+h)
 for h in HEADERS[:14]:
  if headers.count(h)!=1:raise ValidationError('열 이름을 확인하세요: '+h)
 columns={h:i for i,h in enumerate(headers)}
 def get(row,key):
  idx=columns.get(key);return cell(row[idx]) if idx is not None and len(row)>idx else ''
 baseline={str(r['id']):r for r in session.snapshot['rows']};identities={(r['code'],r['warehouse'],r['location'],r['status']):r for r in baseline.values()}
 proposals=[];seen=set();new_item_names={}
 for n,row in enumerate(table[1:],2):
  if not any(cell(v) for v in row):continue
  if any(isinstance(v,str) and v.startswith('=') for v in row):raise ValidationError(f'{n}행: 수식은 허용하지 않습니다. 값을 붙여넣으세요.')
  raw=get(row,'실사수량')
  if not raw:continue
  if raw.startswith('=') or get(row,'사유').startswith('='):raise ValidationError(f'{n}행: 수식은 허용하지 않습니다.')
  qty=integer(raw)
  if qty<0:raise ValidationError(f'{n}행: 실사수량은 0 이상이어야 합니다.')
  code=get(row,'ERP 코드');location=get(row,'위치');warehouse=get(row,'창고');status={'입고 완료':'received','입고 예정':'expected'}.get(get(row,'입고 상태'))
  if not code or len(code)>100 or not location or len(location)>150 or any(c in code+location for c in '\t\r\n') or warehouse!='Y09' or not status:raise ValidationError(f'{n}행: ERP 코드·Y09 창고·위치·입고 상태를 확인하세요.')
  key=(code,warehouse,location,status)
  if key in seen:raise ValidationError(f'{n}행: 동일 재고가 중복됐습니다.')
  seen.add(key);stock_id=get(row,'재고 식별번호');original=baseline.get(stock_id) if stock_id else identities.get(key)
  if stock_id and not original:raise ValidationError(f'{n}행: 다른 실사의 재고 식별번호입니다.')
  if get(row,'실사 회차') and get(row,'실사 회차')!=str(session.pk):raise ValidationError(f'{n}행: 다른 실사 회차입니다.')
  if original and (original['code'],original['warehouse'],original['location'],original['status'])!=key:raise ValidationError(f'{n}행: 기존 재고의 코드·위치·상태는 변경할 수 없습니다. 이동 기능을 사용하세요.')
  if session.kind=='count' and status!='received':raise ValidationError(f'{n}행: 실사는 입고 완료 재고만 처리합니다.')
  if not original and Stock.objects.filter(item__code=code,warehouse=warehouse,location=location,status=status).exists():raise ValidationError(f'{n}행: 기존 재고와 충돌합니다. 해당 재고를 포함해 다시 내려받으세요.')
  reason=get(row,'사유')
  excluded=original['reserved'] if original and session.kind=='count' else 0
  target=qty+excluded
  if target>2147483647:raise ValidationError(f'{n}행: 수량이 너무 큽니다.')
  if original and target<original['reserved']:raise ValidationError(f'{n}행: 조정 후 현재고가 예약 수량보다 작습니다. 예약을 먼저 조정하세요.')
  before=original['quantity'] if original else 0
  if (not original or before!=target) and not reason:raise ValidationError(f'{n}행: 수량 변경·신규 등록 사유가 필요합니다.')
  if len(reason)>2000:raise ValidationError(f'{n}행: 사유는 2,000자 이하로 입력하세요.')
  item=Item.objects.filter(code=code).first();name=item.name if item else get(row,'모델명');spec=item.specification if item else get(row,'사양')
  if not item and (not name or len(name)>250 or not spec):raise ValidationError(f'{n}행: 신규 품목의 모델명·사양을 입력하세요.')
  if not item:
   if code in new_item_names and new_item_names[code]!=(name,spec):raise ValidationError(f'{n}행: 같은 신규 코드의 모델명·사양이 다릅니다.')
   new_item_names[code]=(name,spec)
  if not original and qty==0:raise ValidationError(f'{n}행: 신규 재고는 1개 이상이어야 합니다.')
  if not original or before!=target:
   proposals.append({'stock_id':original['id'] if original else None,'code':code,'name':name,'spec':spec,'warehouse':warehouse,'location':location,'status':status,'count':qty,'excluded':excluded,'before':before,'after':target,'delta':target-before,'reason':reason,'new_item':item is None})
 if not proposals:raise ValidationError('변경할 수량 또는 추가할 재고가 없습니다. 빈칸과 기존 수량은 변경하지 않습니다.')
 with warehouse_lock():
  session=StocktakeSession.objects.select_for_update().get(pk=session.pk)
  if session.state!='open':raise ValidationError('이미 처리되거나 취소된 실사입니다.')
  check_baseline(session)
  session.proposal=proposals;session.upload_digest=hashlib.sha256(content).hexdigest();session.source_file=content;session.source_name=Path(upload.name).name[:255];session.preview_id=uuid.uuid4()
  snap=dict(session.snapshot);snap['preview_state']=state(p['code'] for p in proposals);session.snapshot=snap
  session.save();return session

def verified_backup():
 from .backups import create_backup,verify_backup,_pg_program,_run_pg
 config={}
 if os.name=='nt':
  path=Path(os.environ.get('PROGRAMDATA',r'C:\ProgramData'))/'SUNIL/runtime.local.json'
  if path.is_file():config=json.loads(path.read_text(encoding='utf-8-sig'))
 directory=os.environ.get('ERP_STOCKTAKE_BACKUP_DIR') or config.get('BackupDirectory')
 if not directory:
  from django.conf import settings
  directory=str(Path(settings.DATABASES['default']['NAME']).parent/'stocktake-backups') if connection.vendor=='sqlite' else ''
 if not directory:raise ValidationError('실사 백업 경로를 서버에 설정하세요. 백업 없이 반영하지 않습니다.')
 pg=os.environ.get('ERP_PG_BIN') or config.get('PgBin') or (r'C:\Program Files\PostgreSQL\18\bin' if os.name=='nt' else None)
 folder=create_backup(directory,pg);database,manifest=verify_backup(folder)
 if database.stat().st_size==0:raise ValidationError('백업 파일이 비어 있습니다.')
 if manifest['vendor']=='postgresql':_run_pg([_pg_program('pg_restore',pg),'--list',str(database)])
 return str(folder)

def apply(session_id,preview_id,actor,approve_new=False):
 require_super(actor)
 with warehouse_lock():
  s=StocktakeSession.objects.select_for_update().get(pk=session_id)
  if s.state!='open':raise ValidationError('이미 처리되거나 취소된 실사입니다. 중복 반영하지 않습니다.')
  if not s.proposal or str(s.preview_id)!=str(preview_id):raise ValidationError('최신 미리보기를 다시 확인하세요.')
  check_baseline(s)
  if state(p['code'] for p in s.proposal)!=s.snapshot['preview_state']:raise ValidationError('미리보기 이후 관련 자료가 변경됐습니다. 다시 확인하세요.')
  if any(p['new_item'] for p in s.proposal) and not approve_new:raise ValidationError('신규 품목 등록을 명시적으로 확인하세요.')
  if StocktakeSession.objects.filter(upload_digest=s.upload_digest,state='applied').exclude(pk=s.pk).exists():raise ValidationError('이미 반영된 파일입니다.')
  s.backup_path=verified_backup() # No mutation occurs before verified full backup.
  result=[]
  for p in s.proposal:
   if p['stock_id']:stock=Stock.objects.select_for_update().get(pk=p['stock_id'])
   else:
    item=Item.objects.filter(code=p['code']).first()
    if not item:item=Item.objects.create(code=p['code'],name=p['name'],specification=p['spec'])
    stock=Stock(item=item,warehouse=p['warehouse'],location=p['location'],status=p['status'],quantity=0,reserved=0)
   stock.quantity=p['after'];stock.save();result.append(dict(p,stock_id=stock.pk))
  s.result=result;s.after_state=state(p['code'] for p in result);s.state='applied';s.applied_at=timezone.now();s.save()
  AuditEvent.objects.create(actor=actor,action='stocktake_apply',reference=str(s.pk),detail={'kind':s.kind,'rows':len(result),'delta':sum(r['delta'] for r in result),'backup':s.backup_path})
  return s

def reverse(session_id,actor):
 require_super(actor)
 with warehouse_lock():
  s=StocktakeSession.objects.select_for_update().get(pk=session_id)
  if s.state!='applied':raise ValidationError('반영 완료된 건만 한 번 되돌릴 수 있습니다.')
  if state(s.after_state)!=s.after_state:raise ValidationError('이후 관련 입출고·예약·이동·조정이 있어 자동 되돌리기를 차단했습니다. 별도 조정으로 처리하세요.')
  backup=verified_backup()
  for row in s.result:
   stock=Stock.objects.select_for_update().get(pk=row['stock_id']);stock.quantity=row['before'];stock.save(update_fields=['quantity'])
  s.state='reversed';s.reversed_at=timezone.now();s.reversed_by=actor;s.save()
  AuditEvent.objects.create(actor=actor,action='stocktake_reverse',reference=str(s.pk),detail={'backup':backup,'rows':len(s.result)})
  return s
