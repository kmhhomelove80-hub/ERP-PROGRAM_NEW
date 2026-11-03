from .menu_permissions import can_write
"""Audited reservation replacement, serialized with every warehouse mutation."""
import uuid
from django.core.exceptions import ValidationError
from .models import Allocation, Stock, AuditEvent
from .locking import warehouse_transaction
from .services import audit, require_quantity
from .transaction_details import stock_details


def parse_targets(text):
    targets = {}
    lines = text.strip().splitlines()
    if not 1 <= len(lines) <= 1000 or len(text) > 50000:
        raise ValidationError('재고 ID와 수량을 한 줄에 두 열로, 최대 1000행 입력하세요.')
    for line in lines:
        parts = line.split()
        if len(parts) != 2 or any(not p.isascii() or not p.isdecimal() or len(p) > 10 for p in parts):
            raise ValidationError('재고 ID와 양의 정수 수량을 입력하세요. 예: 12 5')
        key, value = map(int, parts)
        require_quantity(value)
        if key in targets:
            raise ValidationError('같은 재고 ID는 한 번만 입력하세요.')
        targets[key] = value
    return targets


def allocation_snapshot(allocation):
    return dict(stock_id=allocation.stock_id, quantity=allocation.quantity,
                state=allocation.state, details=stock_details(allocation.stock))


@warehouse_transaction
def reassign_allocation(*, allocation_id, targets, expected, request_id, reason, actor):
    if not can_write(actor, 'shipping'):
        raise ValidationError('관리자만 예약 배정을 변경할 수 있습니다.')
    try:
        request_id = uuid.UUID(str(request_id))
    except (ValueError, TypeError, AttributeError) as error:
        raise ValidationError('유효한 변경 요청번호가 필요합니다.') from error
    reason = reason.strip()
    if not reason or len(reason) > 1000:
        raise ValidationError('변경 사유를 1~1000자로 입력하세요.')
    targets = dict(targets)
    for key, value in targets.items():
        if not isinstance(key, int) or isinstance(key, bool):
            raise ValidationError('유효한 재고 ID가 필요합니다.')
        require_quantity(value)
    payload = dict(allocation=str(allocation_id), targets=sorted([[k,v] for k,v in targets.items()]), expected=expected, reason=reason)
    previous = AuditEvent.objects.filter(action='allocation_reassign', reference=str(request_id)).first()
    if previous:
        if previous.actor_id != actor.pk or previous.detail.get('request') != payload:
            raise ValidationError('이미 사용한 변경 요청번호입니다.')
        return Allocation.objects.get(pk=allocation_id)
    allocation = Allocation.objects.select_for_update().select_related('stock__item').get(pk=allocation_id)
    if allocation.state != 'reserved' or allocation.shipped_at or allocation.released_at:
        raise ValidationError('출고 전 예약만 변경할 수 있습니다.')
    if allocation_snapshot(allocation) != expected:
        raise ValidationError('예약 또는 재고 위치가 변경되었습니다. 화면을 새로 열어 확인하세요.')
    if not targets or sum(targets.values()) != allocation.quantity:
        raise ValidationError('변경 전후 총수량이 같아야 합니다.')
    if hasattr(allocation, 'substitution') and len(targets) != 1:
        raise ValidationError('대체품 예약은 빌림 연결 보존을 위해 한 재고로만 변경할 수 있습니다.')
    stocks = {s.pk:s for s in Stock.objects.select_for_update().filter(pk__in=set(targets)|{allocation.stock_id}).order_by('pk')}
    source = stocks[allocation.stock_id]
    if len(stocks) != len(set(targets)|{allocation.stock_id}):
        raise ValidationError('선택한 재고가 존재하지 않습니다.')
    if source.reserved < allocation.quantity:
        raise ValidationError('예약 합계가 원장과 다릅니다. 관리자 점검이 필요합니다.')
    for key, quantity in targets.items():
        stock = stocks[key]
        capacity = stock.available + (allocation.quantity if key == source.pk else 0)
        if stock.item_id != source.item_id or stock.warehouse != source.warehouse or quantity > capacity:
            raise ValidationError('동일 품목·창고의 가용량 이내에서만 변경할 수 있습니다. 다른 예약은 사용할 수 없습니다.')
    before = dict(id=str(allocation.pk), **allocation_snapshot(allocation))
    source.reserved -= allocation.quantity
    for key, quantity in targets.items(): stocks[key].reserved += quantity
    for stock in stocks.values(): stock.save(update_fields=['reserved'])
    changed = []
    for index, (key, quantity) in enumerate(sorted(targets.items())):
        if index == 0:
            allocation.stock = stocks[key]
            allocation.quantity = quantity
            allocation.assigned_details = stock_details(stocks[key])
            allocation.save(update_fields=['stock','quantity','assigned_details'])
            row = allocation
        else:
            row = Allocation.objects.create(stock=stocks[key], quantity=quantity, order_id=allocation.order_id,
                project=allocation.project, legacy_seq=allocation.legacy_seq, assigned_details=stock_details(stocks[key]))
        changed.append(dict(id=str(row.pk), **allocation_snapshot(row)))
    audit(actor, 'allocation_reassign', request_id, request=payload, before=before, after=changed,
          order=str(allocation.order_id) if allocation.order_id else None, code=source.item.code, project=allocation.project, reason=reason, quantity=before['quantity'])
    return allocation
