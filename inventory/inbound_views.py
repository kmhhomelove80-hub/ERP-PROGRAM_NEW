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
 from django.core import signing
 from uuid import uuid4
 nonce=signing.dumps({'actor':request.user.pk,'id':str(uuid4())},salt='inbound-receipt-page')
 return render(request,'inventory/inbound_list.html',{'inbound_nonce':nonce})

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


def receipt_handoff(data,actor):
 """Validate the five columns, bound to this user and source-screen operation."""
 from django.core import signing
 from django.core.exceptions import ValidationError
 from uuid import UUID,uuid5
 from .bulk_receipts import parse_receipts
 try:
  nonce=signing.loads(data.get('inbound_nonce',''),salt='inbound-receipt-page',max_age=86400)
  if nonce['actor']!=actor.pk:raise ValueError
  revision=int(data.get('inbound_revision',''))
  if not 1<=revision<=1000000:raise ValueError
  raw=data.get('inbound_rows','')
  if len(raw)>500000:raise ValueError
  rows=json.loads(raw)
  if not isinstance(rows,list) or not 1<=len(rows)<=1000:raise ValueError
  normalized=[]
  for row in rows:
   if not isinstance(row,list) or len(row)!=5:raise ValueError
   if any(not isinstance(v,str) for v in (row[0],row[1],row[2],row[4])):raise ValueError
   if isinstance(row[3],bool) or not isinstance(row[3],(str,int,float)):raise ValueError
   normalized.append([' '.join(str(v).replace('\t',' ').splitlines()).strip() for v in row])
  parsed=parse_receipts(pasted='\n'.join('\t'.join(row) for row in normalized))
  request_id=uuid5(UUID(nonce['id']),str(revision))
 except (signing.BadSignature,ValueError,KeyError,TypeError):
  raise ValidationError('입고 전달 자료가 만료되었거나 올바르지 않습니다. 입고 LIST에서 다시 보내세요.')
 return {'actor':actor.pk,'request_id':str(request_id),'rows':parsed},'\r\n'.join('\t'.join(row) for row in normalized)
