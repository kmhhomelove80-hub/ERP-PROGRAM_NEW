import uuid
from django import forms
from django.core.paginator import Paginator
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import get_object_or_404,render,redirect
from django.views.decorators.http import require_http_methods,require_GET,require_POST
from . import stocktake
from .models import StocktakeSession,Stock

class StartForm(forms.Form):
 kind=forms.ChoiceField(label='처리 구분',choices=[('count','재고실사 — 포장분 제외 로케이션 잔량'),('adjust','재고조정 — 포장분 포함 전산 현재고')])
 q=forms.CharField(label='ERP 코드 / 모델 / 위치',required=False,max_length=250)
 confirmed=forms.BooleanField(label='실사 대상의 PICKING·포장을 모두 완료하고 자재를 분리했으며, 조사·반영 중 대상 재고의 입출고·이동·예약 변경을 중지하겠습니다.')

class UploadForm(forms.Form):
 workbook=forms.FileField(label='조사한 엑셀 파일')

class AdjustForm(forms.Form):
 stock=forms.ModelChoiceField(label='조정 대상 재고',queryset=Stock.objects.none())
 quantity=forms.IntegerField(label='조정 후 전산 현재고 (포장된 할당 수량 포함)',min_value=0,max_value=2147483647,widget=forms.NumberInput(attrs={'inputmode':'numeric'}))
 reason=forms.CharField(label='조정 사유',max_length=2000,widget=forms.Textarea)
 confirmed=forms.BooleanField(label='조정 수량과 사유를 확인했으며 관련 재고의 처리를 잠시 중지했습니다.')
 def __init__(self,*args,query='',**kwargs):
  super().__init__(*args,**kwargs)
  from django.db.models import Q
  rows=Stock.objects.select_related('item').filter(warehouse='Y09').order_by('item__code','location','pk')
  if query:rows=rows.filter(Q(item__code__icontains=query)|Q(item__name__icontains=query)|Q(location__icontains=query))
  else:rows=rows.none()
  self.fields['stock'].queryset=rows
  self.fields['stock'].label_from_instance=lambda s:f'{s.item.code} / {s.location} / {s.get_status_display()} / 현재 {s.quantity} / 예약 {s.reserved}'

@stocktake.super_only
@require_http_methods(['GET','POST'])
def index(request):
 form=StartForm(request.POST or None)
 if request.method=='POST' and form.is_valid():
  try:s=stocktake.start(request.user,form.cleaned_data['q'],form.cleaned_data['kind'])
  except ValidationError as e:form.add_error(None,e)
  else:return redirect('stocktake-detail',pk=s.pk)
 return render(request,'inventory/stocktake_index.html',{'form':form,'page_obj':Paginator(StocktakeSession.objects.select_related('actor').defer('source_file','snapshot','proposal','result','after_state').order_by('-created_at','pk'),50).get_page(request.GET.get('page'))})

@stocktake.super_only
@require_http_methods(['GET','POST'])
def adjust(request):
 query=request.GET.get('q','').strip()[:250];form=AdjustForm(request.POST or None,query=query)
 if request.method=='POST' and form.is_valid():
  data=form.cleaned_data;s=stocktake.start(request.user,kind='adjust',stock_id=data['stock'].pk)
  try:
   import io
   from openpyxl import load_workbook
   from django.core.files.uploadedfile import SimpleUploadedFile
   book=load_workbook(io.BytesIO(stocktake.workbook(s)));sheet=book.active;sheet['M2']=data['quantity'];sheet['N2']=data['reason'];sheet['N2'].data_type='s';out=io.BytesIO();book.save(out)
   stocktake.preview(s,SimpleUploadedFile('개별조정.xlsx',out.getvalue()),request.user)
  except ValidationError as e:form.add_error(None,e)
  else:return redirect('stocktake-detail',pk=s.pk)
 return render(request,'inventory/stocktake_adjust.html',{'form':form,'query':query})

@stocktake.super_only
@require_http_methods(['GET','POST'])
def detail(request,pk):
 s=get_object_or_404(StocktakeSession,pk=pk);form=UploadForm()
 if request.method=='POST':
  try:
   action=request.POST.get('action')
   if action=='upload':
    form=UploadForm(request.POST,request.FILES)
    if form.is_valid():s=stocktake.preview(s,form.cleaned_data['workbook'],request.user)
   elif action=='apply':
    if request.POST.get('confirmed')!='yes':raise ValidationError('변경 내용을 확인하세요.')
    s=stocktake.apply(s.pk,request.POST.get('preview_id',''),request.user,request.POST.get('approve_new')=='yes');messages.success(request,'백업 검증 후 재고 반영을 완료했습니다.');return redirect('stocktake-detail',pk=s.pk)
   elif action=='reverse':
    if request.POST.get('confirmed')!='yes':raise ValidationError('되돌리기 확인이 필요합니다.')
    stocktake.reverse(s.pk,request.user);messages.success(request,'해당 건의 수량을 되돌렸습니다. 신규 품목·재고 기록은 남기고 신규 수량은 0으로 복원합니다.');return redirect('stocktake-detail',pk=s.pk)
   elif action=='cancel':
    from .locking import warehouse_lock
    with warehouse_lock():
     session=StocktakeSession.objects.select_for_update().get(pk=s.pk)
     if session.state!='open':raise ValidationError('진행 중인 회차만 취소할 수 있습니다.')
     session.state='cancelled';session.save(update_fields=['state'])
    return redirect('stocktake-detail',pk=s.pk)
   else:raise ValidationError('처리 요청을 확인하세요.')
  except (ValidationError,OSError) as e:form.add_error(None,e if isinstance(e,ValidationError) else '백업 또는 파일 저장에 실패했습니다. 반영하지 않았습니다. 서버 백업 경로와 공간을 확인하세요.')
 s.refresh_from_db()
 return render(request,'inventory/stocktake_detail.html',{'session':s,'form':form,'rows':s.proposal if s.state=='open' else s.result,'has_new':any(p.get('new_item') for p in s.proposal),'packed':sum(r['reserved'] for r in s.snapshot['rows'])})

@stocktake.super_only
@require_GET
def download(request,pk):
 s=get_object_or_404(StocktakeSession,pk=pk)
 # Export the original baseline; never silently refresh a counted sheet.
 response=HttpResponse(stocktake.workbook(s),content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');response['Content-Disposition']=f'attachment; filename="stocktake-{s.pk}.xlsx"';response['Cache-Control']='no-store';return response

@stocktake.super_only
@require_GET
def source(request,pk):
 s=get_object_or_404(StocktakeSession,pk=pk)
 response=HttpResponse(bytes(s.source_file or b''),content_type='application/octet-stream');response['Content-Disposition']=f'attachment; filename="stocktake-source-{s.pk}.xlsx"' if bytes(s.source_file or b'').startswith(b'PK') else f'attachment; filename="stocktake-source-{s.pk}.tsv"';response['Cache-Control']='no-store';return response
