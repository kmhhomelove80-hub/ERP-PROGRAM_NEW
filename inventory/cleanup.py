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

def cleanup_fingerprint(item):
    import hashlib,json
    from .stocktake import state
    return hashlib.sha256(json.dumps(state([item.code]),sort_keys=True,separators=(',',':')).encode()).hexdigest()

def reviewed_candidates(query=''):
    from django.core import signing
    from .models import CleanupDecision
    groups=cleanup_candidates(query)
    decisions=list(CleanupDecision.objects.filter(active=True,item_id__in=[g['item'].pk for g in groups]))
    result=[]
    for group in groups:
        fingerprint=cleanup_fingerprint(group['item']);total=group['destination_before'];moves=[]
        for move in group['moves']:
            matching=[d for d in decisions if d.item_id==group['item'].pk and d.source==move['source']['location']]
            if any(d.destination==group['destination'] and d.fingerprint==fingerprint for d in matching):continue
            move['recheck']=bool(matching)
            move['destination_before']=total;total+=move['source']['quantity'];move['destination_after']=total
            move['token']=signing.dumps({'item':group['item'].pk,'source':move['source']['location'],'destination':group['destination'],'fingerprint':fingerprint},salt='cleanup-keep',compress=True)
            moves.append(move)
        if moves:
            group.update(moves=moves,emptied=len(moves),destination_after=total);result.append(group)
    return result
