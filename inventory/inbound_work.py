"""Shared placement records. Only final confirmation changes stock."""
import hashlib,json,re,uuid
from datetime import date
from decimal import Decimal,InvalidOperation
from django.core import signing
from django.core.exceptions import PermissionDenied,ValidationError
from django.db.models import Sum
from django.utils import timezone
from .models import InboundWorkList,InboundWorkRow,EmployeeMenuAccess,Stock
from .menu_permissions import can_write,require_menu_write
from .locking import warehouse_transaction
from .services import audit


def can_record(user):
    return bool(user.is_authenticated and user.is_active and (can_write(user,'receiving') or EmployeeMenuAccess.objects.filter(user=user,inbound_record=True).exists()))


def clean_text(value,limit,blank=True,multiline=False):
    if not isinstance(value,str) or len(value)>limit:raise ValidationError('입력 형식과 길이를 확인하세요.')
    if multiline:value=' '.join(value.split())
    if any(ord(c)<32 for c in value):raise ValidationError('입력 형식과 길이를 확인하세요.')
    value=value.strip()
    if not blank and not value:raise ValidationError('필수 값을 입력하세요.')
    return value


def digest(value):return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def recommendations(codes):
    result={}
    rows=(Stock.objects.filter(warehouse='Y09',status='received',quantity__gt=0,item__code__in=codes).exclude(location='').values('item__code','location').annotate(total=Sum('quantity')).order_by('item__code','-total','location'))
    for row in rows:
        if row['location'].strip():result.setdefault(row['item__code'],row['location'])
    return result


@warehouse_transaction
def create_work(*,data,actor):
    require_menu_write(actor,'receiving')
    try:
        nonce=signing.loads(data.get('inbound_nonce',''),salt='inbound-receipt-page',max_age=86400)
        revision=int(data.get('inbound_revision',''))
        if nonce['actor']!=actor.pk or not 1<=revision<=1000000:raise ValueError
        request_id=uuid.uuid5(uuid.UUID(nonce['id']),f'work:{revision}')
        raw=data.get('inbound_rows','')
        if len(raw)>600000:raise ValueError
        cells=json.loads(raw)
        if not isinstance(cells,list) or not 1<=len(cells)<=1000:raise ValueError
        document_date=date.fromisoformat(data.get('document_date',''))
        title=clean_text(data.get('title','입고 LIST'),250,False)
        rows=[]
        for cell in cells:
            if not isinstance(cell,list) or len(cell)!=5:raise ValueError
            code=clean_text(cell[0],100,False)
            if code.startswith(('=','#')):raise ValueError
            text=str(cell[3])
            if not re.fullmatch(r'\d+(?:\.\d{0,12})?',text):raise ValueError
            quantity=Decimal(text)
            if not 0<quantity<=2147483647:raise ValueError
            rows.append([code,clean_text(cell[1],250,multiline=True),clean_text(cell[2],5000,multiline=True),format(quantity,'f'),clean_text(cell[4],150)])
    except (ValueError,KeyError,TypeError,InvalidOperation,signing.BadSignature):
        raise ValidationError('저장 자료가 올바르지 않거나 만료되었습니다. 파일과 날짜를 다시 확인하세요.')
    fingerprint=digest([title,document_date.isoformat(),rows])
    prior=InboundWorkList.objects.filter(request_id=request_id).first()
    if prior:
        if prior.actor_id!=actor.pk or prior.digest!=fingerprint:raise ValidationError('이미 저장한 작업입니다. 저장 목록을 열어 이어서 작업하세요. 다른 파일은 새로 불러오세요.')
        return prior
    work=InboundWorkList.objects.create(request_id=request_id,digest=fingerprint,title=title,document_date=document_date,actor=actor)
    rec=recommendations([r[0] for r in rows])
    InboundWorkRow.objects.bulk_create([InboundWorkRow(work_list=work,line=i+1,code=r[0],name=r[1],specification=r[2],quantity=r[3],location=r[4],recommended=rec.get(r[0],'')) for i,r in enumerate(rows)])
    audit(actor,'inbound_work_create',work.pk,count=len(rows))
    return work


def assert_open(work):
    if work.archived or work.state!='open':raise ValidationError('숨김 또는 입고 완료 목록은 수정할 수 없습니다.')


@warehouse_transaction
def update_rows(*,work_id,changes,actor,revision=None):
    if not can_record(actor):raise PermissionDenied
    work=InboundWorkList.objects.select_for_update().get(pk=work_id);assert_open(work)
    if revision is not None and str(work.revision)!=str(revision):raise ValidationError('다른 사용자가 변경했습니다. 새로 조회한 뒤 다시 시도하세요.')
    if not isinstance(changes,list) or not 1<=len(changes)<=1000:raise ValidationError('변경할 행을 확인하세요.')
    ids=[str(c.get('id')) for c in changes if isinstance(c,dict)]
    if len(ids)!=len(changes) or len(set(ids))!=len(ids) or any(not v.isascii() or not v.isdigit() or not 0<int(v)<=9223372036854775807 for v in ids):raise ValidationError('행 선택이 중복되었거나 올바르지 않습니다.')
    current={str(r.pk):r for r in work.rows.select_for_update().filter(pk__in=ids)}
    if len(current)!=len(ids):raise ValidationError('다른 목록의 행은 수정할 수 없습니다.')
    before=[]
    for change in changes:
        row=current[str(change['id'])]
        if str(change.get('version'))!=str(row.version):raise ValidationError(f'{row.line}행을 다른 사용자가 변경했습니다. 새로 조회하세요. 입력을 덮어쓰지 않았습니다.')
        location=clean_text(change.get('location',''),150)
        placed=change.get('placed')
        if not isinstance(placed,bool):raise ValidationError('배치 확인 값을 확인하세요.')
        if placed and not location:raise ValidationError(f'{row.line}행: 실제 위치를 입력한 후 배치를 확인하세요.')
        before.append({'line':row.line,'location':row.location,'placed':row.placed,'version':row.version})
        row.location=location;row.placed=placed;row.version+=1;row.updated_by=actor;row.updated_at=timezone.now();row.save()
    work.revision+=1;work.save(update_fields=['revision','updated_at'])
    audit(actor,'inbound_work_edit',work.pk,before=before,after=[{'line':r.line,'location':r.location,'placed':r.placed,'version':r.version} for r in current.values()])
    return work,list(current.values())


@warehouse_transaction
def confirm_work(*,work_id,revision,actor):
    require_menu_write(actor,'receiving')
    work=InboundWorkList.objects.select_for_update().get(pk=work_id)
    if work.state=='received':return work,False
    assert_open(work)
    if str(work.revision)!=str(revision):raise ValidationError('검토 후 기록이 변경됐습니다. 새로 조회하고 다시 확인하세요.')
    rows=list(work.rows.all())
    if not rows or any(not r.location.strip() or not r.placed for r in rows):raise ValidationError('모든 행의 실제 위치와 배치 완료 확인이 필요합니다. 관리자가 나머지를 입력해도 됩니다.')
    from .bulk_receipts import receive_batch
    data=[dict(code=r.code,name=r.name,specification=r.specification,quantity=r.quantity,location=r.location) for r in rows]
    receive_batch(request_id=work.pk,rows=data,actor=actor)
    work.state='received';work.received_at=timezone.now();work.received_by=actor;work.revision+=1;work.save()
    audit(actor,'inbound_work_receive',work.pk,count=len(rows),quantity=sum(int(r.quantity.split('.')[0]) for r in rows))
    return work,True


@warehouse_transaction
def archive_work(*,ids,archived,actor):
    require_menu_write(actor,'receiving')
    if not ids or len(ids)>1000:raise ValidationError('목록을 선택하세요.')
    works=list(InboundWorkList.objects.select_for_update().filter(pk__in=ids))
    if len(works)!=len(set(ids)):raise ValidationError('목록 선택을 확인하세요.')
    for work in works:
        work.archived=archived;work.revision+=1;work.save(update_fields=['archived','revision','updated_at'])
        audit(actor,'inbound_work_hide' if archived else 'inbound_work_restore',work.pk,state=work.state)
