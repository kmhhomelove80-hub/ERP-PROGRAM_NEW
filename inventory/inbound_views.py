"""Private browser conversion; only bounded code/location lookup reaches DB."""
import json
from django.db.models import Sum
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET,require_POST
from .access import menu_access
from .models import Stock

@menu_access('receiving')
@require_GET
def inbound_list(request):
 return render(request,'inventory/inbound_list.html')

@menu_access('receiving',write=True)
@require_POST
def inbound_locations(request):
 if len(request.body)>100000:return JsonResponse({'error':'조회 자료가 너무 큽니다.'},status=400)
 try:
  codes=json.loads(request.body)['codes']
  if not isinstance(codes,list) or not 1<=len(codes)<=250:raise ValueError
  if any(not isinstance(c,str) or not c or len(c)>100 for c in codes):raise ValueError
 except (ValueError,KeyError,TypeError,UnicodeDecodeError):
  return JsonResponse({'error':'ERP 코드 목록을 확인하세요.'},status=400)
 codes=list(dict.fromkeys(codes));result={c:[] for c in codes}
 rows=(Stock.objects.filter(warehouse='Y09',status=Stock.Status.RECEIVED,quantity__gt=0,item__code__in=codes)
       .exclude(location='').values('item__code','location').annotate(quantity=Sum('quantity')).order_by('item__code','-quantity','location'))
 for row in rows:
  if row['location'].strip():result[row['item__code']].append({'location':row['location'],'quantity':row['quantity']})
 response=JsonResponse({'locations':result,'warehouse':'Y09','basis':'입고 완료 현재고가 많은 위치 순'})
 response['Cache-Control']='no-store'
 return response
