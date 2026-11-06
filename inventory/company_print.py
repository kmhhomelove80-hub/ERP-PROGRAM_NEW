"""Immutable print snapshots and at-most-once queue claims; no stock mutation."""
import json,uuid
from django.core import signing
from django.core.exceptions import ValidationError
from django.utils import timezone
from .models import PickingOrder,CompanyPrinter,CompanyPrintJob
from .menu_permissions import require_menu_write
from .locking import warehouse_transaction
from .services import audit
from .inbound_work import digest


def document_for(order):
    rows=[]
    for allocation in order.allocations.select_related('stock__item','substitution').order_by('stock__item__code','stock__pk','pk'):
        d=allocation.display_details
        rows.append({'code':d['code'],'description':d.get('model','')+' / '+d.get('specification',''),'location':d['location'],'quantity':allocation.quantity,'status':allocation.get_state_display(),'receipt':'입고 예정' if d.get('status')=='expected' else '입고 완료'})
    if not rows or len(rows)>10000:raise ValidationError('1~10,000행의 피킹 목록만 인쇄할 수 있습니다.')
    return {'title':'피킹 목록','project':order.project,'number':order.number,'registered':timezone.localtime(order.created_at).strftime('%Y-%m-%d %H:%M'),'rows':rows,'total':sum(r['quantity'] for r in rows)}


def preview(order,actor):
    document=document_for(order)
    return document,signing.dumps({'actor':actor.pk,'order':str(order.pk),'digest':digest(document),'request':str(uuid.uuid4())},salt='company-print-preview')


@warehouse_transaction
def enqueue(*,order_id,token,copies,actor):
    require_menu_write(actor,'shipping')
    try:
        data=signing.loads(token,salt='company-print-preview',max_age=1800)
        if data['actor']!=actor.pk or data['order']!=str(order_id):raise ValueError
        request_id=uuid.UUID(data['request']);copies=int(copies)
        if not 1<=copies<=5:raise ValueError
    except (signing.BadSignature,ValueError,KeyError,TypeError):raise ValidationError('인쇄 미리보기가 만료되었거나 부수가 올바르지 않습니다. 다시 확인하세요.')
    prior=CompanyPrintJob.objects.filter(request_id=request_id).first()
    if prior:
        if prior.actor_id!=actor.pk or prior.order_id!=order_id or prior.copies!=copies:raise ValidationError('이미 사용한 인쇄 요청번호입니다.')
        return prior,False
    printer=CompanyPrinter.objects.filter(pk=1,enabled=True).first()
    if not printer or not printer.name:raise ValidationError('회사 프린터 설정과 인쇄 작업자 실행 상태를 관리자에게 확인하세요.')
    order=PickingOrder.objects.get(pk=order_id);document=document_for(order)
    if digest(document)!=data['digest']:raise ValidationError('미리보기 이후 피킹 기록이 변경됐습니다. 새로 확인하세요.')
    job=CompanyPrintJob.objects.create(request_id=request_id,order=order,actor=actor,printer=printer.name,copies=copies,document=document)
    audit(actor,'company_print_queue',job.pk,order=str(order.pk),copies=copies)
    return job,True


@warehouse_transaction
def claim():
    # Never requeue a claimed job automatically: the spooler may already have it.
    printer=CompanyPrinter.objects.filter(pk=1,enabled=True).first()
    if not printer:return None
    job=CompanyPrintJob.objects.select_for_update().filter(state='queued',printer=printer.name).order_by('created_at','pk').first()
    if job:
        job.state='sending';job.claimed_at=timezone.now();job.claim_id=uuid.uuid4();job.save(update_fields=['state','claimed_at','claim_id'])
    return job


@warehouse_transaction
def finish(*,job_id,claim_id,success,result):
    job=CompanyPrintJob.objects.select_for_update().get(pk=job_id)
    if job.state!='sending' or job.claim_id!=claim_id:raise ValidationError('다른 작업자의 인쇄 결과입니다.')
    job.state='submitted' if success else 'unknown';job.result=str(result)[:2000]
    if success:job.submitted_at=timezone.now()
    job.save(update_fields=['state','result','submitted_at'])
    audit(job.actor,'company_print_result',job.pk,state=job.state)
    return job
