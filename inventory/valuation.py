"""ERP-based estimates, rounded per physical location; never modify inventory."""
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP, localcontext
from .reconciliation import latest_reference


def estimate(reference, quantity, warehouse='Y09', status='received'):
    if warehouse != 'Y09': return None, 'Y09 외 창고'
    if status != 'received': return None, '입고 예정 제외'
    if not reference or reference.get('inventory_amount') is None: return None, '금액 자료 없음'
    basis = reference.get('erp_quantity')
    if basis is None or basis <= 0: return None, 'ERP 현재고 0 이하'
    amount = reference['inventory_amount']
    if amount < 0: return None, 'ERP 금액 확인 필요'
    with localcontext() as context:
        context.prec = 50
        value = (amount * Decimal(quantity) / Decimal(basis)).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    return value, ''


def stock_value(stock, references):
    return estimate(references.get(stock.item.code), stock.quantity, stock.warehouse, stock.status)


def summarize(rows):
    references, snapshot = latest_reference('Y09')
    total, valued, missing, excluded = Decimal('0'), 0, 0, 0
    reasons = Counter()
    for code, quantity, warehouse, status in rows.values_list('item__code', 'quantity', 'warehouse', 'status').iterator(chunk_size=1000):
        value, reason = estimate(references.get(code), quantity, warehouse, status)
        if value is not None:
            with localcontext() as context:
                context.prec = 50
                total += value
            valued += 1
        elif status == 'expected' or warehouse != 'Y09': excluded += 1
        else: missing += 1; reasons[reason] += 1
    return {'total': total, 'valued': valued, 'missing': missing, 'excluded': excluded, 'reasons': dict(reasons)}, references, snapshot
