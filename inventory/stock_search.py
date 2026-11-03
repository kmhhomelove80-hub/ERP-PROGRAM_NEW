"""Shared filters for stock screens and their Excel export."""
from django.db.models import Q
from .models import Stock


def search_stocks(params):
    filters = {key: params.get(key, '').strip() for key in ('q', 'model', 'spec', 'status')}
    filters['scan'] = '1' if params.get('scan') == '1' and filters['q'] else ''
    if filters['status'] not in Stock.Status.values:
        filters['status'] = ''
    rows = Stock.objects.select_related('item').filter(quantity__gt=0).order_by('item__code', 'location', 'pk')
    query = filters['q']
    if filters['scan']:
        rows = rows.filter(item__code__iexact=query)
    elif query:
        rows = rows.filter(Q(item__code__icontains=query) | Q(item__name__icontains=query)
                           | Q(item__specification__icontains=query) | Q(location__icontains=query)
                           | Q(legacy_name__icontains=query) | Q(legacy_specification__icontains=query))
    # Use each row's displayed value, including the existing legacy override.
    # Chaining these filters requires both conditions on the same stock row.
    if filters['model']:
        rows = rows.filter(Q(legacy_name__icontains=filters['model'])
                           | Q(legacy_name='', item__name__icontains=filters['model']))
    if filters['spec']:
        rows = rows.filter(Q(legacy_specification__icontains=filters['spec'])
                           | Q(legacy_specification='', item__specification__icontains=filters['spec']))
    if filters['status']:
        rows = rows.filter(status=filters['status'])
    return rows, filters
