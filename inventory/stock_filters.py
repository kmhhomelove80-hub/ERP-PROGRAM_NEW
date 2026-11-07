"""Column filters shared by the stock list, choices and Excel export."""
import re
from django.core.exceptions import ValidationError
from django.db.models import F, Value, CharField, Count, OuterRef, Subquery
from django.db.models.functions import Coalesce, NullIf, Cast
from .models import ErpSnapshot, ReconciliationRow

FIELDS = {'code': ('item__code', 'ITEM No.'), 'specification': ('filter_spec', '사양'),
          'location': ('location', '위치'), 'status': ('status', '상태'),
          'quantity': ('quantity', '현재고'), 'allocated': ('reserved', '할당'),
          'available': ('filter_available', '가용량'), 'pending': ('filter_pending', '출하대기')}
NUMERIC = {'quantity', 'allocated', 'available', 'pending'}


def parse(params):
    result = {}
    for field in FIELDS:
        values = sorted(set(params.getlist('f_' + field)))
        text = params.get('x_' + field, '').strip()
        if len(values) > 200 or any(len(v) > 10000 or any(ord(c) < 32 and c not in '\r\n\t' for c in v) for v in values) or len(text) > 250:
            raise ValidationError('필터 값의 길이와 선택 개수(열당 200개)를 확인하세요.')
        if field == 'status' and any(v not in ('received', 'expected') for v in values):
            raise ValidationError('입고 상태를 확인하세요.')
        if field in NUMERIC and any(v != '' and (not re.fullmatch(r'-?[0-9]{1,12}', v) or not -2147483648 <= int(v) <= 4294967295) for v in values):
            raise ValidationError('수량 조건을 확인하세요.')
        if values: result['f_' + field] = values
        if text: result['x_' + field] = text
        if field in NUMERIC:
            for bound in ('min_', 'max_'):
                key = bound + field
                value = params.get(key, '').strip()
                if value:
                    if not re.fullmatch(r'-?[0-9]{1,12}', value) or not -2147483648 <= int(value) <= 4294967295:
                        raise ValidationError('수량 범위를 확인하세요.')
                    result[key] = str(int(value))
            if 'min_' + field in result and 'max_' + field in result and int(result['min_' + field]) > int(result['max_' + field]):
                raise ValidationError('최소 수량은 최대 수량보다 클 수 없습니다.')
    return result


def annotate(rows, pending=False):
    rows = rows.annotate(filter_spec=Coalesce(NullIf('legacy_specification', Value('')), 'item__specification', Value(''), output_field=CharField()), filter_available=F('quantity') - F('reserved'))
    if pending:
        latest = ErpSnapshot.objects.filter(rows__warehouse='Y09').order_by('-created_at', '-pk').values('pk')[:1]
        reference = ReconciliationRow.objects.filter(snapshot_id=Subquery(latest), warehouse='Y09', code=OuterRef('item__code'), erp_quantity__isnull=False)
        rows = rows.annotate(filter_pending=Subquery(reference.values('shipping_pending')[:1]))
    return rows


def apply(rows, filters, exclude=None):
    rows = annotate(rows, any(k.endswith('_pending') for k in filters) or exclude == 'pending')
    for field, (path, label) in FIELDS.items():
        if field == exclude: continue
        values = filters.get('f_' + field)
        if values:
            from django.db.models import Q
            present = [int(v) if field in NUMERIC else v for v in values if v != '']
            condition = Q(**{path + '__in': present})
            if '' in values: condition |= Q(**{path + '__isnull': True}) if field in NUMERIC else Q(**{path: ''})
            rows = rows.filter(condition)
        text = filters.get('x_' + field)
        if text:
            if field == 'status':
                rows = rows.filter(status__in=[v for v, label in [('received', '입고 완료'), ('expected', '입고 예정')] if text in label or text in v])
            elif field in NUMERIC:
                rows = rows.annotate(filter_number=Cast(path, CharField())).filter(filter_number__icontains=text)
            else: rows = rows.filter(**{path + '__icontains': text})
        if field in NUMERIC:
            for bound, op in [('min_', '__gte'), ('max_', '__lte')]:
                if bound + field in filters: rows = rows.filter(**{path + op: int(filters[bound + field])})
    return rows


def options(request):
    from django.http import JsonResponse
    from .stock_search import search_stocks
    field = request.GET.get('field')
    if field not in FIELDS: return JsonResponse({'error': '열을 확인하세요.'}, status=400)
    try:
        filters = parse(request.GET)
        rows, _ = search_stocks(request.GET, columns=False)
        offset = max(0, min(int(request.GET.get('offset', '0')), 200000))
        rows = apply(rows, filters, exclude=field)
    except (ValidationError, ValueError): return JsonResponse({'error': '필터 조건을 확인하세요.'}, status=400)
    path = FIELDS[field][0]
    text = request.GET.get('search', '').strip()[:250]
    if text:
        rows = apply(rows, {'x_' + field: text})
    choices = list(rows.order_by(path).values(path).annotate(count=Count('pk'))[offset:offset + 201])
    data = []
    for choice in choices[:200]:
        value = choice[path]
        label = {'received': '입고 완료', 'expected': '입고 예정'}.get(value, '자료 없음' if value is None else str(value) or '(빈 값)')
        data.append({'value': '' if value is None else str(value), 'label': ' '.join(label.split()), 'count': choice['count']})
    result = JsonResponse({'values': data, 'next_offset': offset + 200 if len(choices) > 200 else None})
    result['Cache-Control'] = 'no-store'
    return result
