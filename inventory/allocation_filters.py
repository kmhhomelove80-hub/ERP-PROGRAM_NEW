"""Validated, shared filter definition for queries, totals and signed actions."""
from django.core.exceptions import ValidationError
from django.db.models import Q,Case,When,Value,CharField,Count,F
from django.db.models.functions import Cast,Concat,Replace,Substr,TruncDate
from zoneinfo import ZoneInfo

FIELDS={'project':('project','프로젝트'),'seq':('filter_seq','SEQ / 주문번호'),'code':('stock__item__code','ERP 코드'),'location':('stock__location','위치'),'status':('stock__status','입고 상태'),'quantity':('quantity','수량')}

def annotated(rows):
    day=Replace(Cast(TruncDate('order__created_at',tzinfo=ZoneInfo('Asia/Seoul')),CharField()),Value('-'),Value(''))
    number=Concat(Value('P-'),day,Value('-'),Substr(Replace(Cast('order_id',CharField()),Value('-'),Value('')),1,12))
    return rows.annotate(filter_seq=Case(When(legacy_seq='',order__isnull=False,then=number),default=F('legacy_seq'),output_field=CharField()))


def parse(params):
    result={}
    for field in FIELDS:
        values=sorted(set(params.getlist('f_'+field)))
        if len(values)>200 or any(len(x)>250 or any(ord(c)<32 for c in x) for x in values):raise ValidationError('필터 선택은 열당 200개 이내로 지정하세요.')
        text=params.get('x_'+field,'').strip()
        if len(text)>250:raise ValidationError('검색 조건이 너무 깁니다.')
        if field=='status' and any(v not in ('expected','received') for v in values):raise ValidationError('입고 상태를 확인하세요.')
        if field=='quantity' and any(not v.isascii() or not v.isdigit() or not 0<int(v)<=2147483647 for v in values):raise ValidationError('수량 조건은 양의 정수로 입력하세요.')
        if values:result['f_'+field]=values
        if text:result['x_'+field]=text
    for key in ('min_quantity','max_quantity'):
        value=params.get(key,'').strip()
        if value:
            if not value.isascii() or not value.isdigit() or not 0<=int(value)<=2147483647:raise ValidationError('수량 범위를 확인하세요.')
            result[key]=str(int(value))
    if 'min_quantity' in result and 'max_quantity' in result and int(result['min_quantity'])>int(result['max_quantity']):raise ValidationError('최소 수량은 최대 수량보다 클 수 없습니다.')
    return result


def apply(rows,filters,exclude=None):
    for field,(path,label) in FIELDS.items():
        if field==exclude:continue
        if 'f_'+field in filters:rows=rows.filter(**{path+'__in':filters['f_'+field]})
        if 'x_'+field in filters:
            if field=='quantity':rows=rows.annotate(filter_quantity=Cast('quantity',CharField())).filter(filter_quantity__icontains=filters['x_'+field])
            elif field=='status':
                terms=[v for v,l in [('expected','입고 예정'),('received','입고 완료')] if filters['x_'+field] in l or filters['x_'+field] in v]
                rows=rows.filter(stock__status__in=terms)
            else:rows=rows.filter(**{path+'__icontains':filters['x_'+field]})
    if exclude!='quantity':
        if 'min_quantity' in filters:rows=rows.filter(quantity__gte=int(filters['min_quantity']))
        if 'max_quantity' in filters:rows=rows.filter(quantity__lte=int(filters['max_quantity']))
    return rows


def summary(filters):
    out=[]
    for field,(path,label) in FIELDS.items():
        values=filters.get('f_'+field,[])
        if field=='status':values=[{'received':'입고 완료','expected':'입고 예정'}[v] for v in values]
        if values:out.append(label+': '+', '.join(v or '(빈 값)' for v in values))
        if filters.get('x_'+field):out.append(label+' 포함: '+filters['x_'+field])
    if filters.get('min_quantity'):out.append('최소 수량: '+filters['min_quantity'])
    if filters.get('max_quantity'):out.append('최대 수량: '+filters['max_quantity'])
    return ' · '.join(out)


def options(request):
    from django.http import JsonResponse
    from .models import Allocation
    field=request.GET.get('field')
    if field not in FIELDS:return JsonResponse({'error':'열을 확인하세요.'},status=400)
    try:filters=parse(request.GET);offset=max(0,min(int(request.GET.get('offset','0')),200000))
    except (ValidationError,ValueError):return JsonResponse({'error':'필터를 확인하세요.'},status=400)
    rows=annotated(Allocation.objects.filter(state='reserved',substitution__isnull=True))
    if request.GET.get('q'):rows=rows.filter(project__icontains=request.GET['q'][:250])
    if request.GET.get('seq'):rows=rows.filter(Q(legacy_seq__icontains=request.GET['seq'][:250])|Q(order__id__icontains=request.GET['seq'][:250]))
    rows=apply(rows,filters,exclude=field);path=FIELDS[field][0];search=request.GET.get('search','').strip()[:250]
    if search:
        if field=='quantity':rows=rows.annotate(option_value=Cast('quantity',CharField())).filter(option_value__icontains=search)
        elif field=='status':rows=apply(rows,{'x_status':search})
        else:rows=rows.filter(**{path+'__icontains':search})
    values=list(rows.order_by(path).values(path).annotate(count=Count('pk'))[offset:offset+201]);more=len(values)>200
    data=[{'value':str(v[path]),'label':{'received':'입고 완료','expected':'입고 예정'}.get(str(v[path]),str(v[path]) or '(빈 값)'),'count':v['count']} for v in values[:200]]
    result=JsonResponse({'values':data,'next_offset':offset+200 if more else None});result['Cache-Control']='no-store';return result
