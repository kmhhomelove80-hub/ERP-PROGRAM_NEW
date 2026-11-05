from .menu_permissions import require_menu_write, can_write
"""Picking input, deterministic preview and atomic confirmation."""
from collections import OrderedDict
from django.core.exceptions import ValidationError
from django.db.models import F
from .locking import warehouse_transaction
from .models import Stock, Allocation, PickingOrder
from .services import audit
from .transaction_details import stock_details

MAX_QUANTITY = 2147483647

def parse_paste(text):
    rows = OrderedDict()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or len(lines) > 500:
        raise ValidationError('1~500행의 ERP 코드와 수량을 붙여넣으세요.')
    for n, line in enumerate(lines,1):
        columns = line.split('\t') if '\t' in line else line.split()
        if len(columns) != 2:
            raise ValidationError(f'{n}행: ERP 코드와 수량 두 열만 입력하세요.')
        code, quantity = [c.strip() for c in columns]
        if not code or len(code)>100 or code.startswith(('=','#')) or not quantity.isascii() or not quantity.isdecimal() or len(quantity)>10:
            raise ValidationError(f'{n}행: 유효한 ERP 코드와 양의 정수가 필요합니다. 헤더는 제외하세요.')
        quantity = int(quantity)
        if not 0 < quantity <= MAX_QUANTITY:
            raise ValidationError(f'{n}행: 수량은 1~{MAX_QUANTITY} 범위입니다.')
        rows[code] = rows.get(code,0) + quantity
        if rows[code] > MAX_QUANTITY: raise ValidationError(f'{n}행: 합산 수량이 허용 범위를 초과합니다.')
    return [{'code':code,'quantity':qty} for code,qty in rows.items()]

def plan_picking(rows, *, received_only=False, manual=None):
    available = {}
    from .location_planner import choose_locations, MAX_TRANSITIONS
    work_budget = [MAX_TRANSITIONS]
    stocks = Stock.objects.select_related('item').filter(warehouse='Y09',quantity__gt=F('reserved'),item__code__in=[r['code'] for r in rows]).order_by('location','pk')
    if received_only:
        stocks = stocks.filter(status=Stock.Status.RECEIVED)
    # Python code-point ordering is identical on SQLite and PostgreSQL locales.
    for s in sorted(stocks, key=lambda stock:(stock.location, stock.pk)):
        available.setdefault(s.item.code,[]).append(s)
    manual = dict(manual) if manual is not None else None
    if manual is not None and set(manual) - {s.pk for group in available.values() for s in group}:
        raise ValidationError('수동 배정 대상은 같은 품목의 가용 재고여야 합니다.')
    allocations, shortages = [], []
    for row in rows:
        candidates = available.get(row['code'], [])
        target = min(row['quantity'], sum(s.available for s in candidates))
        if manual is None:
            locations = OrderedDict()
            for stock in candidates: locations.setdefault(stock.location, []).append(stock)
            groups = list(locations.values())
            selected = choose_locations([sum(s.available for s in group) for group in groups], row['quantity'], budget=work_budget)
            selected_stocks = [s for i in selected for s in groups[i]]
        else:
            selected_stocks = [s for s in candidates if s.pk in manual]
            if sum(manual[s.pk] for s in selected_stocks) != target:
                raise ValidationError(f"{row['code']}: 수동 배정 합계는 현재 배정 가능 수량 {target}개여야 합니다.")
        remaining = target
        for stock in selected_stocks:
            qty = min(remaining, stock.available) if manual is None else manual[stock.pk]
            if manual is None and qty == 0:
                continue
            if isinstance(qty, bool) or not isinstance(qty, int) or not 0 < qty <= stock.available:
                raise ValidationError('수동 배정 수량이 가용량을 초과하거나 유효하지 않습니다.')
            if qty:
                allocations.append({'stock_id':stock.pk,'code':row['code'],'model':stock.legacy_name or stock.item.name,'specification':stock.legacy_specification or stock.item.specification,'location':stock.location,'status':stock.status,'quantity':qty})
                remaining -= qty
        if target < row['quantity']:
            shortages.append({'code':row['code'],'requested':row['quantity'],'allocated':target,'shortage':row['quantity']-target})
    from .reconciliation import latest_reference
    reference,snapshot=latest_reference('Y02',[r['code'] for r in shortages])
    for row in shortages:
        row['y02_quantity']=reference.get(row['code'],{}).get('erp_quantity')
        row['y02_as_of']=snapshot.erp_as_of.isoformat() if snapshot else None
    return {'allocations':allocations,'shortages':shortages}

@warehouse_transaction
def confirm_picking(*, request_id, project, rows, preview, allow_shortage, actor, customer_rows=None, substitution_draft=False, manual=None, handoff=None):
    require_menu_write(actor, 'shipping')
    project = project.strip()
    if not project or len(project)>250: raise ValidationError('프로젝트/호선 이름이 필요합니다.')
    # Normalize again at the service boundary, not only in HTML forms.
    normalized = parse_paste('\n'.join(f"{r['code']}\t{r['quantity']}" for r in rows))
    payload = {'project':project,'rows':normalized}
    if manual is not None: payload['manual'] = sorted([[int(k), v] for k,v in dict(manual).items()])
    if substitution_draft: payload['substitution_draft']=True
    if customer_rows:
        from .customer_picking import parse_customer,serialize_customer
        customer_rows = parse_customer(pasted=serialize_customer(customer_rows))
        customer_totals = parse_paste('\n'.join(f"{r['code']}\t{r['quantity']}" for r in customer_rows))
        if customer_totals != normalized: raise ValidationError('고객 자료와 요청 수량이 일치하지 않습니다.')
        payload['customer_rows'] = customer_rows
    if handoff:
        from .delivery_bridge import validate_handoff
        handoff=validate_handoff(handoff,customer_rows or [],request_id)
        payload['handoff']=handoff
    previous = PickingOrder.objects.filter(pk=request_id).first()
    if previous:
        def comparable(value):
            result={k:v for k,v in value.items() if k!='handoff_result'}
            if result.get('handoff'):result['handoff']={k:v for k,v in result['handoff'].items() if k!='issued_at'}
            return result
        if previous.actor_id != actor.pk or comparable(previous.payload) != comparable(payload):
            raise ValidationError('이미 사용한 요청번호입니다.')
        return previous
    current = plan_picking(normalized, manual=manual)
    if current != preview:
        raise ValidationError('재고 또는 위치가 변경되었습니다. 배정 미리보기를 다시 확인하세요.')
    if not current['allocations'] and not (substitution_draft and allow_shortage): raise ValidationError('예약 가능한 수량이 없습니다. 전량 부족이면 대체 배정용 주문으로 등록하세요.')
    if current['shortages'] and not allow_shortage:
        raise ValidationError('부족 수량을 확인하고 부분 배정 동의에 체크하세요.')
    if handoff:
        from .delivery_bridge import build_reply
        payload['handoff_result']=build_reply(handoff,customer_rows,current,request_id)
    order = PickingOrder.objects.create(id=request_id,project=project,actor=actor,payload=payload,shortages=current['shortages'])
    for row in current['allocations']:
        stock = Stock.objects.select_for_update().get(pk=row['stock_id'])
        if row['quantity'] > stock.available:
            raise ValidationError('가용량이 변경되었습니다. 다시 확인하세요.')
        stock.reserved += row['quantity']
        stock.save(update_fields=['reserved'])
        allocation = Allocation.objects.create(stock=stock,order=order,project=project,quantity=row['quantity'],assigned_details=stock_details(stock))
        audit(actor,'reserve',allocation.pk,stock=stock.pk,quantity=row['quantity'],order=str(order.pk),code=row['code'],project=project)
    audit(actor,'picking_confirm',order.pk,shortages=current['shortages'],project=project)
    return order


def parse_quantity_code(text):
    """Public two-column input: quantity first, ERP code second."""
    lines=[line.strip() for line in text.splitlines() if line.strip()]
    if not 1<=len(lines)<=500:
        raise ValidationError('1~500행의 수량·ERP 코드를 붙여넣으세요.')
    swapped=[]
    for number,line in enumerate(lines,1):
        columns=line.split('\t') if '\t' in line else line.split()
        if len(columns)!=2:
            raise ValidationError(f'{number}행: 수량·ERP 코드 두 열 순서로 입력하세요.')
        quantity,code=[v.strip() for v in columns]
        if not quantity.isascii() or not quantity.isdecimal():
            raise ValidationError(f'{number}행: 첫 열은 양의 정수 수량, 두 번째 열은 ERP 코드입니다.')
        swapped.append(code+'\t'+quantity)
    return parse_paste('\n'.join(swapped))


@warehouse_transaction
def plan_supplement(order_id):
    """Only never-allocated demand; cancelled quantities are not silently revived."""
    from collections import Counter
    from .substitutions import demand_balance, requested_code
    order = PickingOrder.objects.select_for_update().get(pk=order_id)
    if not order.payload.get('rows'):
        raise ValidationError('원래 요청 수량이 없는 이전 주문은 추가 배정할 수 없습니다. 담당자에게 확인하세요.')
    cancelled = Counter()
    for allocation in order.allocations.filter(state='cancelled').select_related('stock__item', 'substitution'):
        cancelled[requested_code(allocation)] += allocation.quantity
    cancelled_order = order.allocations.exists() and not order.allocations.exclude(state='cancelled').exists()
    balance = demand_balance(order)
    for row in balance:
        row['cancelled'] = cancelled[row['code']]
        row['remaining'] = 0 if cancelled_order else max(row['shortage'] - row['cancelled'], 0)
    rows = [{'code': row['code'], 'quantity': row['remaining']} for row in balance if row['remaining']]
    preview = plan_picking(rows, received_only=True)
    return {'balance': balance, 'cancelled_order': cancelled_order, **preview}


@warehouse_transaction
def confirm_supplement(*, request_id, order_id, preview, allow_shortage, confirmed, actor):
    """Append reservations atomically, retaining the original demand and shortage snapshot."""
    import uuid
    from .models import AuditEvent
    if not can_write(actor, 'shipping'):
        raise ValidationError('모든 권한 계정만 추가 피킹을 확정할 수 있습니다.')
    if not confirmed:
        raise ValidationError('기존 주문과 추가 배정 수량을 확인하세요.')
    try:
        request_id = uuid.UUID(str(request_id))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValidationError('추가 요청번호가 유효하지 않습니다.') from error
    order = PickingOrder.objects.select_for_update().get(pk=order_id)
    previous = AuditEvent.objects.filter(action='picking_supplement', reference=str(request_id)).first()
    if previous:
        if previous.actor_id != actor.pk or previous.detail.get('order') != str(order.pk) or previous.detail.get('preview') != preview:
            raise ValidationError('이미 사용한 추가 요청번호입니다.')
        return order
    current = plan_supplement(order.pk)
    if current != preview:
        raise ValidationError('재고 또는 기존 배정이 변경되었습니다. 추가 피킹 미리보기를 다시 확인하세요.')
    if not current['allocations']:
        raise ValidationError('추가 예약할 입고 완료 재고가 없습니다.')
    if current['shortages'] and not allow_shortage:
        raise ValidationError('남은 부족 수량을 확인하고 부분 추가 배정에 동의하세요.')
    ids = []
    for row in current['allocations']:
        stock = Stock.objects.select_for_update().get(pk=row['stock_id'])
        if stock.status != Stock.Status.RECEIVED or row['quantity'] > stock.available:
            raise ValidationError('재고가 변경되었습니다. 추가 피킹 미리보기를 다시 확인하세요.')
        stock.reserved += row['quantity']
        stock.save(update_fields=['reserved'])
        allocation = Allocation.objects.create(stock=stock, order=order, project=order.project,
                                              quantity=row['quantity'], assigned_details=stock_details(stock))
        ids.append(str(allocation.pk))
        audit(actor, 'reserve', allocation.pk, stock=stock.pk, quantity=row['quantity'],
              order=str(order.pk), code=row['code'], project=order.project, supplement=str(request_id))
    audit(actor, 'picking_supplement', request_id, order=str(order.pk), project=order.project,
          preview=preview, allocations=ids, quantity=sum(r['quantity'] for r in current['allocations']))
    return order
