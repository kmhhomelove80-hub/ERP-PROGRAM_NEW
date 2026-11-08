"""Read-only DB fingerprint for a quiet update window; prints no row values.

Run with manage.py shell -c "exec(open('scripts/update_snapshot.py', encoding='utf-8').read())"
Only hashes of canonical model rows and aggregate counts are emitted. Session,
auth logins and operational backup files are outside this fingerprint.
"""
import hashlib
import json
from django.apps import apps
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Sum
from django.contrib.auth import get_user_model
from inventory.locking import warehouse_lock
from inventory.models import Stock


class SnapshotEncoder(DjangoJSONEncoder):
    def default(self, value):
        # Production drafts contain binary XLSX data. Fingerprint the bytes
        # without printing the workbook or depending on backend byte types.
        if isinstance(value, (bytes, bytearray, memoryview)):
            return {'binary_sha256': hashlib.sha256(value).hexdigest(), 'binary_bytes': len(value)}
        return super().default(value)


with warehouse_lock():
    result={}
    for model in sorted(apps.get_app_config('inventory').get_models(),key=lambda m:m._meta.label_lower):
        digest=hashlib.sha256()
        count=0
        for row in model.objects.order_by('pk').values().iterator(chunk_size=1000):
            # Additive permission defaults must hash equally before/after schema upgrade.
            if model._meta.model_name=='reconciliationrow':row.setdefault('inventory_amount',None)
            if model._meta.model_name=='employeemenuaccess':row.setdefault('inbound_record',False)
            digest.update(json.dumps(row,cls=SnapshotEncoder,sort_keys=True,separators=(',',':')).encode())
            digest.update(b'\n')
            count+=1
        result[model._meta.model_name]={'count':count,'sha256':digest.hexdigest()}
    digest=hashlib.sha256()
    for row in get_user_model().objects.order_by('pk').values('id','username','password','is_staff','is_superuser','is_active','first_name','last_name','email'):
        digest.update(json.dumps(row,sort_keys=True,separators=(',',':')).encode())
        digest.update(b'\n')
    result['accounts']={'count':get_user_model().objects.count(),'sha256':digest.hexdigest()}
    result['stock_totals']=Stock.objects.aggregate(quantity=Sum('quantity'),reserved=Sum('reserved'))
print('SUNIL_SNAPSHOT='+json.dumps(result,sort_keys=True))
