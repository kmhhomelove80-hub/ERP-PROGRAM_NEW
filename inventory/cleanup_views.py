from django.core import signing
from django.core.exceptions import ValidationError
from django.contrib import messages
from django.shortcuts import redirect,render,get_object_or_404
from django.views.decorators.http import require_POST,require_GET
from django.core.paginator import Paginator
from .access import menu_access
from .locking import warehouse_lock
from .models import CleanupDecision,AuditEvent,Item
from .cleanup import cleanup_fingerprint,cleanup_candidates

@menu_access('inventory',write=True)
@require_POST
def keep(request):
 tokens=[request.POST['single_decision']] if request.POST.get('single_decision') else request.POST.getlist('decisions')
 try:
  if not tokens:raise ValidationError('기존 유지할 행을 선택하세요.')
  with warehouse_lock():
   parsed=[]
   for token in tokens:
    try:data=signing.loads(token,salt='cleanup-keep',max_age=3600)
    except signing.BadSignature:raise ValidationError('목록이 만료됐습니다. 다시 조회하세요.')
    item=get_object_or_404(Item,pk=data['item'])
    if cleanup_fingerprint(item)!=data['fingerprint']:raise ValidationError('재고 상태가 바뀌었습니다. 새 목록을 확인하세요.')
    candidates=cleanup_candidates(item.code)
    if not any(g['item'].pk==item.pk and g['destination']==data['destination'] and any(m['source']['location']==data['source'] for m in g['moves']) for g in candidates):raise ValidationError('정리 제안이 변경됐습니다.')
    parsed.append(data)
   for data in parsed:
    decision,created=CleanupDecision.objects.get_or_create(item_id=data['item'],source=data['source'],destination=data['destination'],fingerprint=data['fingerprint'],active=True,defaults={'actor':request.user})
    if created:AuditEvent.objects.create(actor=request.user,action='cleanup_keep',reference=str(decision.pk),detail={'code':decision.item.code,'source_location':decision.source,'destination':decision.destination})
  messages.success(request,f'{len(parsed)}행을 기존 유지로 판정했습니다. 사유 입력은 필요하지 않습니다.')
 except ValidationError as e:messages.error(request,' / '.join(e.messages))
 return redirect('cleanup-list')

@menu_access('inventory',write=True)
@require_GET
def decisions(request):
 rows=CleanupDecision.objects.select_related('actor','item').order_by('-created_at','-pk')
 return render(request,'inventory/cleanup_decisions.html',{'page_obj':Paginator(rows,50).get_page(request.GET.get('page'))})

@menu_access('inventory',write=True)
@require_POST
def cancel(request,pk):
 with warehouse_lock():
  d=get_object_or_404(CleanupDecision.objects.select_for_update(),pk=pk)
  if d.active:
   d.active=False;d.save(update_fields=['active']);AuditEvent.objects.create(actor=request.user,action='cleanup_keep_cancel',reference=str(d.pk),detail={'code':d.item.code})
 messages.success(request,'기존 유지 판정을 취소했습니다.');return redirect('cleanup-decisions')
