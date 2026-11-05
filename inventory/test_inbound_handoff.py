import json,uuid
from django.core import signing
from django.test import TestCase,Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from .models import Item,Stock,Receipt

class InboundHandoffTests(TestCase):
 def setUp(self):
  self.owner=get_user_model().objects.create_user('handoff-owner',is_staff=True);self.client.force_login(self.owner)
  self.nonce=self.client.get('/inbound/').context['inbound_nonce']
  self.rows=[['001','입력 모델','D열 사양',32,'A01'],['001','입력 모델','D열 사양',32,'B01']]
  self.item=Item.objects.create(code='001',name='기존 모델',specification='기존 사양');self.stock=Stock.objects.create(item=self.item,location='A01',quantity=5)
 def send(self,**change):
  data={'action':'handoff','inbound_nonce':self.nonce,'inbound_revision':'1','inbound_rows':json.dumps(self.rows)};data.update(change);return self.client.post('/receipts/bulk/',data)
 def confirm(self,response):return self.client.post('/receipts/bulk/',{'action':'confirm','confirmed':'on','preview_token':response.context['confirm_form'].initial['preview_token']})
 def test_exact_five_fields_no_stock_change_until_confirm_and_no_duplicate(self):
  response=self.send();self.assertEqual(response.status_code,200);self.stock.refresh_from_db();self.assertEqual(self.stock.quantity,5);self.assertFalse(Receipt.objects.exists())
  preview=response.context['preview'];self.assertEqual(preview[0],{'code':'001','name':'입력 모델','specification':'D열 사양','quantity':32,'location':'A01'})
  self.assertEqual(response.context['form'].initial['pasted_rows'],'001\t입력 모델\tD열 사양\t32\tA01\r\n001\t입력 모델\tD열 사양\t32\tB01')
  self.assertIn('001',response.context['warnings'])
  self.assertEqual(self.confirm(response).status_code,302);self.confirm(response);self.confirm(self.send())
  self.stock.refresh_from_db();self.assertEqual(self.stock.quantity,37);self.assertEqual(Stock.objects.get(location='B01').quantity,32);self.assertEqual(Receipt.objects.count(),2)
  self.item.refresh_from_db();self.assertEqual(self.item.specification,'기존 사양')
 def test_edit_after_registered_does_not_register_same_job_again(self):
  self.confirm(self.send());self.rows[0][3]=20;response=self.confirm(self.send());self.assertContains(response,'이미 사용한 요청번호');self.assertEqual(Receipt.objects.count(),2)
 def test_missing_location_fractional_bad_columns_and_expired_nonce(self):
  for rows in ([['001','이름','사양',1,'']], [['001','이름','사양',.5,'A']], [['001','이름',1,'A']], [['001','이름','사양',True,'A']]):
   response=self.send(inbound_rows=json.dumps(rows));self.assertIsNone(response.context['confirm_form']);self.assertFalse(Receipt.objects.exists())
  self.assertIsNone(self.send(inbound_nonce=self.nonce+'bad').context['confirm_form'])
 def test_other_user_and_write_permission_and_csrf(self):
  other=get_user_model().objects.create_user('other',is_staff=True);self.client.force_login(other);self.assertIsNone(self.send().context['confirm_form'])
  other.is_staff=False;other.save();self.assertEqual(self.send().status_code,403)
  c=Client(enforce_csrf_checks=True);c.force_login(self.owner);self.assertEqual(c.post('/receipts/bulk/',{'action':'handoff'}).status_code,403)
 def test_new_source_revision_has_distinct_request_id(self):
  a=self.send().context['confirm_form'].initial['preview_token'];b=self.send(inbound_revision='2').context['confirm_form'].initial['preview_token']
  self.assertNotEqual(signing.loads(a,salt='receipt-batch')['request_id'],signing.loads(b,salt='receipt-batch')['request_id'])
