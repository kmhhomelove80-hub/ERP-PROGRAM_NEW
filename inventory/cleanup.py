"""Read-only consolidation suggestions. Execution uses normal movement requests."""
from collections import defaultdict
from django.db.models import Q
from .models import Stock


def cleanup_candidates(query=''):
    stocks = Stock.objects.select_related('item').filter(warehouse='Y09',status='received',quantity__gt=0)
    if query:
        stocks = stocks.filter(Q(item__code__icontains=query)|Q(item__name__icontains=query))
    items = defaultdict(lambda: defaultdict(list))
    for stock in stocks.order_by('item__code','location','pk'):
        items[stock.item_id][stock.location].append(stock)
    groups = []
    for locations in items.values():
        if len(locations) < 2:
            continue
        buckets = [dict(location=location, stocks=rows, quantity=sum(s.quantity for s in rows), reserved=sum(s.reserved for s in rows)) for location,rows in locations.items()]
        eligible = [b for b in buckets if not b['reserved'] and b['location'].strip()]
        if len(eligible) < 2:
            continue
        destination = min(eligible, key=lambda b: (-b['quantity'], b['location'], b['stocks'][0].pk))
        sources = [b for b in eligible if b is not destination and b['quantity'] <= 3]
        if not sources:
            continue
        total = destination['quantity']
        moves = []
        for source in sources:
            if total + source['quantity'] > 2147483647:
                continue
            moves.append(dict(source=source, destination_before=total, destination_after=total+source['quantity']))
            total += source['quantity']
        if moves:
            groups.append(dict(item=destination['stocks'][0].item, destination=destination['location'],
                               moves=moves, emptied=len(moves), destination_before=destination['quantity'], destination_after=total))
    return sorted(groups, key=lambda g: (-g['emptied'], g['item'].code, g['item'].pk))
