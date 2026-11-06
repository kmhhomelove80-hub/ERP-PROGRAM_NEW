import json
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError,PermissionDenied
from django.db.models import Count,Q
from django.http import JsonResponse,HttpResponseBadRequest
from django.shortcuts import get_object_or_404,redirect,render
from django.views.decorators.http import require_POST,require_GET
from .access import menu_access
from .models import InboundWorkList
from .menu_permissions import can_write
from .inbound_work import create_work,can_record,update_rows,confirm_work,archive_work,recommendations


@menu_access('receiving')
def index(request):
    if request.method=='POST':
        try:
            if request.POST.get('action')=='create':
                work=create_work(data=request.POST,actor=request.user)
                return redirect('inbound-work-detail',pk=work.pk)
            if request.POST.get('action') not in ('hide','restore'):raise ValidationError('작업을 확인하세요.')
            archive_work(ids=request.POST.getlist('lists'),archived=request.POST['action']=='hide',actor=request.user)
            messages.success(request,'저장 목록을 숨김/복구했습니다. 실제 재고와 입고 거래는 변경하지 않았습니다.')
            return redirect(request.path+('?hidden=1' if request.POST['action']=='hide' else ''))
        except (ValidationError,ValueError) as e:messages.error(request,' / '.join(e.messages) if isinstance(e,ValidationError) else '선택한 목록을 확인하세요.')
    hidden=request.GET.get('hidden')=='1';q=request.GET.get('q','').strip()[:250]
    rows=InboundWorkList.objects.filter(archived=hidden).select_related('actor','received_by').annotate(total=Count('rows'),done=Count('rows',filter=Q(rows__placed=True)),located=Count('rows',filter=~Q(rows__location=''))).order_by('-created_at')
    if q:
        from .models import InboundWorkRow
        rows=rows.filter(Q(title__icontains=q)|Q(pk__in=InboundWorkRow.objects.filter(code__icontains=q).values('work_list_id')))
    from django.core.paginator import Paginator
    return render(request,'inventory/inbound_work_index.html',{'page_obj':Paginator(rows,50).get_page(request.GET.get('page')),'hidden':hidden,'query':q})


@login_required
def detail(request,pk):
    request.business_menu='receiving'
    if not request.user.is_active:raise PermissionDenied
    if request.method=='POST' and not can_record(request.user):raise PermissionDenied
    work=get_object_or_404(InboundWorkList.objects.select_related('actor','received_by'),pk=pk)
    if request.method=='POST':
        try:
            action=request.POST.get('action')
            if action=='confirm':
                if request.POST.get('confirmed')!='on':raise ValidationError('전체 행과 수량을 확인하세요.')
                work,created=confirm_work(work_id=pk,revision=request.POST.get('revision'),actor=request.user)
                messages.success(request,'최종 입고했습니다. 기록은 잠금 상태입니다.' if created else '이미 입고한 목록입니다. 추가 입고하지 않았습니다.')
            elif action=='row':
                work,_=update_rows(work_id=pk,actor=request.user,changes=[{'id':request.POST.get('row_id'),'version':request.POST.get('version'),'location':request.POST.get('location',''),'placed':request.POST.get('placed')=='on'}])
                messages.success(request,'배치 기록을 저장했습니다.')
            elif action=='bulk':
                if not can_write(request.user,'receiving'):raise PermissionDenied
                ids=request.POST.getlist('selected_rows')
                chosen=list(work.rows.filter(pk__in=ids))
                if len(chosen)!=len(set(ids)) or not chosen:raise ValidationError('변경할 행을 선택하세요.')
                rec=recommendations([r.code for r in chosen]);changes=[]
                for row in chosen:
                    location=row.location
                    if request.POST.get('bulk_mode')=='recommend':
                        if location:continue
                        location=rec.get(row.code,'')
                        if not location:continue
                    elif request.POST.get('bulk_mode')=='location':location=request.POST.get('bulk_location','')
                    else:raise ValidationError('일괄 입력 방식을 확인하세요.')
                    changes.append({'id':row.pk,'version':row.version,'location':location,'placed':False if location!=row.location else row.placed})
                if not changes:raise ValidationError('추천 가능한 빈 위치가 없습니다. 기존 입력은 유지했습니다.')
                update_rows(work_id=pk,actor=request.user,changes=changes,revision=request.POST.get('revision'))
                messages.success(request,'선택한 행의 위치를 저장했습니다. 배치 완료는 실제 확인 후 별도로 체크하세요.')
            else:raise ValidationError('작업을 확인하세요.')
            return redirect('inbound-work-detail',pk=pk)
        except ValidationError as e:
            messages.error(request,' / '.join(e.messages));return redirect('inbound-work-detail',pk=pk)
    all_rows=work.rows.select_related('updated_by');rows=all_rows
    mode=request.GET.get('status','');q=request.GET.get('q','').strip()[:250]
    if mode=='missing':rows=rows.filter(location='')
    elif mode=='unplaced':rows=rows.filter(placed=False)
    elif mode=='done':rows=rows.filter(placed=True)
    if q:rows=rows.filter(Q(code__icontains=q)|Q(name__icontains=q)|Q(location__icontains=q))
    total=all_rows.count();done=all_rows.filter(placed=True).count();located=all_rows.exclude(location='').count()
    from .models import AuditEvent
    from .forms import HistoryFilterForm
    from .history import event_summary
    labels=dict(HistoryFilterForm.base_fields['action'].choices)
    events=list(AuditEvent.objects.filter(reference=str(pk),action__startswith='inbound_work_').select_related('actor').order_by('-pk')[:100])
    for event in events:event.action_label=labels.get(event.action,event.action);event.summary=event_summary(event)
    return render(request,'inventory/inbound_work_detail.html',dict(work=work,rows=rows,total=total,done=done,located=located,ready=bool(total and done==total and located==total),editable=not work.archived and work.state=='open' and can_record(request.user),status=mode,query=q,events=events))


@login_required
@require_POST
def save_row(request,pk):
    get_object_or_404(InboundWorkList,pk=pk)
    if not can_record(request.user):raise PermissionDenied
    try:
        if len(request.body)>10000:raise ValidationError('변경 자료가 너무 큽니다.')
        changes=json.loads(request.body)
        work,rows=update_rows(work_id=pk,changes=changes,actor=request.user)
    except (ValidationError,ValueError,TypeError,KeyError) as e:
        return JsonResponse({'error':' / '.join(e.messages) if isinstance(e,ValidationError) else '입력 형식을 확인하세요.'},status=409 if isinstance(e,ValidationError) else 400)
    return JsonResponse({'revision':work.revision,'total':work.rows.count(),'located':work.rows.exclude(location='').count(),'done':work.rows.filter(placed=True).count(),'rows':[{'id':r.pk,'version':r.version,'updated_by':request.user.get_username(),'updated_at':r.updated_at.isoformat()} for r in rows]})
