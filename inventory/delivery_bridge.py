"""수주관리 전송과 예약 완료 회신. 재고 배정에는 행별 수량만 사용."""
import base64,gzip,hashlib,hmac,ipaddress,json,time
from urllib.parse import urlsplit,urlunsplit
from uuid import UUID
from django.core.exceptions import ValidationError

def canonical(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
def validate_handoff(value,customer,request_id):
 if not value:return None
 try:
  data=json.loads(value) if isinstance(value,str) else value
  if data['version']!=1 or UUID(str(data['transfer_id'])).hex!=UUID(str(request_id)).hex:raise ValueError()
  secret=bytes.fromhex(data['secret'])
  if len(secret)!=32 or not -120<=time.time()-data['issued_at']<=86400:raise ValueError()
  p=urlsplit(data['reply_url']);host=p.hostname or ''
  private=host=='localhost'
  try:private=private or ipaddress.ip_address(host).is_private
  except ValueError:pass
  if not private or p.scheme not in ('http','https') or p.path not in ('','/') or p.username or p.password or p.query or p.fragment:raise ValueError()
  rows=data['rows']
  if len(rows)!=len(customer) or not 1<=len(rows)<=500:raise ValueError()
  for i,(source,row) in enumerate(zip(rows,customer)):
   if source['line_id']!=i or source['values']!=[row['por'],row['hull'],row['block'],row['name'],row['quantity'],row['code'],row.get('total')]:raise ValueError()
  return {'version':1,'transfer_id':UUID(str(data['transfer_id'])).hex,'secret':data['secret'],'issued_at':data['issued_at'],'reply_url':urlunsplit((p.scheme,p.netloc,'/','','')),'rows':rows}
 except (ValueError,TypeError,KeyError,AttributeError):raise ValidationError('수주관리 전송 정보가 없거나 변경됐습니다. 원본에서 다시 전송하세요. 전송된 고객 자료는 수정하지 마세요.')

def build_reply(handoff,customer,preview,order_id):
 pools={}
 for row in preview['allocations']:pools[row['code']]=pools.get(row['code'],0)+row['quantity']
 rows=[]
 for i,row in enumerate(customer):
  requested=row['quantity'];allocated=min(requested,pools.get(row['code'],0));pools[row['code']]=pools.get(row['code'],0)-allocated
  rows.append({'line_id':i,'requested':requested,'allocated':allocated,'shortage':requested-allocated})
 body={'transfer_id':handoff['transfer_id'],'erp_order_id':UUID(str(order_id)).hex,'rows':rows}
 signature=hmac.new(bytes.fromhex(handoff['secret']),canonical(body),hashlib.sha256).hexdigest()
 return {'body':body,'signature':signature}

def reply_url(order):
 handoff=order.payload.get('handoff');reply=order.payload.get('handoff_result')
 if not handoff or not reply:return None
 fragment=base64.urlsafe_b64encode(gzip.compress(canonical(reply),mtime=0)).decode().rstrip('=')
 return handoff['reply_url']+'#sunil_result='+fragment
