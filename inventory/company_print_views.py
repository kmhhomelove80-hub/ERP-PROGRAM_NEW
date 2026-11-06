from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404,render,redirect
from django.views.decorators.http import require_GET
from .access import menu_access
from .models import PickingOrder,CompanyPrinter,CompanyPrintJob
from .company_print import preview,enqueue
from .locking import warehouse_lock
from .services import audit


@menu_access('shipping',write=True)
def order_print(request,pk):
    order=get_object_or_404(PickingOrder,pk=pk)
    if request.method=='POST':
        try:
            if request.POST.get('confirmed')!='on':raise ValidationError('인쇄할 피킹 목록과 부수를 확인하세요.')
            job,created=enqueue(order_id=pk,token=request.POST.get('token',''),copies=request.POST.get('copies','1'),actor=request.user)
            messages.success(request,'회사 프린터 출력 대기에 등록했습니다. 실제 종이 출력 완료는 현장에서 확인하세요.' if created else '이미 접수한 인쇄 요청입니다. 중복 등록하지 않았습니다.')
            return redirect('company-print-jobs')
        except ValidationError as e:messages.error(request,' / '.join(e.messages))
    with warehouse_lock():document,token=preview(order,request.user)
    return render(request,'inventory/company_print_preview.html',{'document':document,'order':order,'token':token,'printer':CompanyPrinter.objects.filter(pk=1).first()})


@menu_access('shipping')
def jobs(request):
    if request.method=='POST':
        from .menu_permissions import require_menu_write
        require_menu_write(request.user,'shipping')
        with warehouse_lock():
            from uuid import UUID
            from django.http import HttpResponseBadRequest
            try:job_id=UUID(request.POST.get('job',''))
            except (ValueError,TypeError,AttributeError):return HttpResponseBadRequest('인쇄 요청번호를 확인하세요.')
            job=get_object_or_404(CompanyPrintJob.objects.select_for_update(),pk=job_id)
            if job.state!='queued':messages.error(request,'이미 전송을 시작한 작업은 취소할 수 없습니다. 현장 대기열을 확인하세요.')
            else:job.state='cancelled';job.save(update_fields=['state']);audit(request.user,'company_print_cancel',job.pk);messages.success(request,'대기 작업을 취소했습니다.')
        return redirect('company-print-jobs')
    from django.core.paginator import Paginator
    rows=CompanyPrintJob.objects.select_related('actor','order').order_by('-created_at')
    return render(request,'inventory/company_print_jobs.html',{'page_obj':Paginator(rows,50).get_page(request.GET.get('page'))})


@menu_access('management',write=True)
def settings(request):
    printer=CompanyPrinter.objects.filter(pk=1).first() or CompanyPrinter(pk=1)
    if request.method=='POST':
        from .inbound_work import clean_text
        try:
            name=clean_text(request.POST.get('name',''),250,True);enabled=request.POST.get('enabled')=='on'
            if enabled and not name:raise ValidationError('Windows에 등록된 프린터 이름을 입력하세요.')
            with warehouse_lock():
                printer,_=CompanyPrinter.objects.select_for_update().get_or_create(pk=1)
                printer.name=name;printer.enabled=enabled;printer.updated_by=request.user;printer.save()
                audit(request.user,'company_printer_settings',1,enabled=enabled)
            messages.success(request,'프린터 설정을 저장했습니다. 인쇄 작업자가 실행 중이어야 출력됩니다.')
            return redirect('company-printer-settings')
        except ValidationError as e:messages.error(request,' / '.join(e.messages))
    return render(request,'inventory/company_printer_settings.html',{'printer':printer})
