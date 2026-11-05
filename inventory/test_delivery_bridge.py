import json,time,uuid,hmac,hashlib
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from .models import Item,Stock,PickingOrder
from .customer_picking import parse_customer
from .delivery_bridge import validate_handoff,canonical,reply_url
from .picking import confirm_picking,parse_paste,plan_picking

class DeliveryBridgeTests(TestCase):
 def setUp(self):
  self.user=get_user_model().objects.create_user('bridge-picker',is_staff=True)
  item=Item.objects.create(code='CODE',name='조명');self.stock=Stock.objects.create(item=item,location='A01',quantity=7)
  self.id=uuid.uuid4();self.text='POR1\t8444\tA500\t조명\t6\tCODE\t10\nPOR2\t8444\tA501\t조명\t4\tCODE\t10'
  self.customer=parse_customer(pasted=self.text)
  self.handoff={'version':1,'transfer_id':self.id.hex,'secret':'ab'*32,'issued_at':int(time.time()),'reply_url':'http://127.0.0.1:8000/','rows':[{'line_id':i,'values':[r['por'],r['hull'],r['block'],r['name'],r['quantity'],r['code'],r['total']]} for i,r in enumerate(self.customer)]}
 def test_7_columns_retained_without_adding_totals(self):
  self.assertEqual(sum(r['quantity'] for r in self.customer),10);self.assertTrue(all(r['total']==10 for r in self.customer))
 def test_partial_reservation_returns_original_lines(self):
  rows=parse_paste('CODE\t10');preview=plan_picking(rows)
  order=confirm_picking(request_id=self.id,project='8444',rows=rows,preview=preview,allow_shortage=True,actor=self.user,customer_rows=self.customer,handoff=self.handoff)
  self.stock.refresh_from_db();self.assertEqual(self.stock.reserved,7);self.assertEqual(self.stock.quantity,7)
  reply=order.payload['handoff_result'];self.assertEqual([(r['allocated'],r['shortage']) for r in reply['body']['rows']],[(6,0),(1,3)])
  expected=hmac.new(bytes.fromhex(self.handoff['secret']),canonical(reply['body']),hashlib.sha256).hexdigest();self.assertEqual(reply['signature'],expected)
  self.assertIn('#sunil_result=',reply_url(order))
  again={**self.handoff,'issued_at':int(time.time())+1}
  self.assertEqual(confirm_picking(request_id=self.id,project='8444',rows=rows,preview=preview,allow_shortage=True,actor=self.user,customer_rows=self.customer,handoff=again).pk,order.pk)
 def test_modified_customer_rejected_before_reservation(self):
  bad=[{**r} for r in self.customer];bad[0]['quantity']=5
  with self.assertRaises(ValidationError):validate_handoff(self.handoff,bad,self.id)
  self.stock.refresh_from_db();self.assertEqual(self.stock.reserved,0)
 def test_no_public_reply_redirect(self):
  bad={**self.handoff,'reply_url':'https://example.com/'}
  with self.assertRaises(ValidationError):validate_handoff(bad,self.customer,self.id)
 def test_old_six_columns_remain_supported(self):
  rows=parse_customer(pasted='P\tH\tB\t등\t2\tCODE');self.assertEqual(rows[0]['quantity'],2);self.assertNotIn('total',rows[0])
 def test_view_preview_and_confirm_returns_only_after_reservation(self):
  self.client.force_login(self.user)
  response=self.client.post('/picking/new/',{'action':'preview','request_id':self.id,'project':'8444','customer_rows':self.text,'bridge_token':json.dumps(self.handoff)})
  self.assertEqual(response.status_code,200);self.assertFalse(PickingOrder.objects.exists())
  token=response.context['confirm_form'].initial['preview_token']
  response=self.client.post('/picking/new/',{'action':'confirm','preview_token':token,'allow_shortage':'on'})
  self.assertEqual(response.status_code,200);self.assertContains(response,'수주관리로 결과 보내기');self.assertTrue(PickingOrder.objects.exists())

 def test_retry_link_returns_saved_reply_without_new_reservation(self):
  self.client.force_login(self.user)
  rows=parse_paste('CODE\t10');preview=plan_picking(rows)
  order=confirm_picking(request_id=self.id,project='8444',rows=rows,preview=preview,allow_shortage=True,actor=self.user,customer_rows=self.customer,handoff=self.handoff)
  response=self.client.get('/picking/new/',{'delivery_id':self.id.hex})
  self.assertContains(response,'수주관리로 결과 보내기');self.assertEqual(PickingOrder.objects.count(),1)
  self.stock.refresh_from_db();self.assertEqual(self.stock.reserved,7)

 def test_shipping_read_only_account_cannot_reserve_through_bridge(self):
  from .models import EmployeeMenuAccess
  EmployeeMenuAccess.objects.create(user=self.user,shipping='read')
  rows=parse_paste('CODE\t10');preview=plan_picking(rows)
  with self.assertRaises(ValidationError):
   confirm_picking(request_id=self.id,project='8444',rows=rows,preview=preview,allow_shortage=True,actor=self.user,customer_rows=self.customer,handoff=self.handoff)
  self.client.force_login(self.user)
  response=self.client.post('/picking/new/',{'action':'preview','request_id':self.id,'project':'8444','customer_rows':self.text,'bridge_token':json.dumps(self.handoff)})
  self.assertEqual(response.status_code,403)
  self.stock.refresh_from_db();self.assertEqual(self.stock.reserved,0)

 def test_manual_allocation_keeps_handoff_and_reference_total(self):
  from .models import Stock
  extra=Stock.objects.create(item=self.stock.item,location='B02',quantity=10)
  rows=parse_paste('CODE\t10');manual={self.stock.pk:2,extra.pk:8}
  preview=plan_picking(rows,manual=manual)
  order=confirm_picking(request_id=self.id,project='8444',rows=rows,preview=preview,allow_shortage=False,actor=self.user,customer_rows=self.customer,handoff=self.handoff,manual=manual)
  self.assertEqual(order.payload['customer_rows'],self.customer)
  self.assertEqual([(r['allocated'],r['shortage']) for r in order.payload['handoff_result']['body']['rows']],[(6,0),(4,0)])
  self.stock.refresh_from_db();extra.refresh_from_db()
  self.assertEqual((self.stock.reserved,extra.reserved),(2,8))

 def test_view_manual_adjustment_preserves_handoff_for_confirmation(self):
  self.client.force_login(self.user)
  response=self.client.post('/picking/new/',{'action':'preview','request_id':self.id,'project':'8444','customer_rows':self.text,'bridge_token':json.dumps(self.handoff)})
  token=response.context['confirm_form'].initial['preview_token']
  response=self.client.post('/picking/new/',{'action':'adjust','preview_token':token,'manual_targets':f'{self.stock.pk}\t7'})
  self.assertFalse(PickingOrder.objects.exists())
  self.assertEqual(response.context['input_form'].initial['customer_rows'],self.text)
  token=response.context['confirm_form'].initial['preview_token']
  response=self.client.post('/picking/new/',{'action':'confirm','preview_token':token,'allow_shortage':'on'})
  self.assertEqual(response.status_code,200);self.assertContains(response,'수주관리로 결과 보내기')
  self.assertTrue(PickingOrder.objects.get().payload['handoff_result'])
