import json
from django.test import TestCase,Client
from django.contrib.auth import get_user_model
from .models import Item,Stock,Allocation

class InboundTests(TestCase):
 def setUp(self):
  self.user=get_user_model().objects.create_user('inbound-owner',is_staff=True)
  self.client.force_login(self.user)
  self.item=Item.objects.create(code='001-TEST')
  self.stock=Stock.objects.create(item=self.item,location='A01',quantity=20,reserved=20)
  Stock.objects.create(item=self.item,location='B01',quantity=10)
  Stock.objects.create(item=self.item,location='TEMP',quantity=100,status='expected')
  Stock.objects.create(item=self.item,warehouse='Y02',location='OTHER',quantity=200)
 def test_recommend_physical_received_only_without_writes(self):
  before=list(Stock.objects.values())
  response=self.client.post('/inbound/locations/',json.dumps({'codes':['001-TEST','MISSING','001-TEST']}),content_type='application/json')
  self.assertEqual(response.status_code,200)
  self.assertEqual(response.json()['locations'],{'001-TEST':[{'location':'A01','quantity':20},{'location':'B01','quantity':10}],'MISSING':[]})
  self.assertEqual(before,list(Stock.objects.values()))
 def test_permissions_csrf_and_bounds(self):
  for payload in ({'codes':[]},{'codes':['X']*251},{'codes':[None]},[]):
   self.assertEqual(self.client.post('/inbound/locations/',json.dumps(payload),content_type='application/json').status_code,400)
  c=Client(enforce_csrf_checks=True);c.force_login(self.user)
  self.assertEqual(c.post('/inbound/locations/',data='{}',content_type='application/json').status_code,403)
  self.user.is_staff=False;self.user.save()
  self.assertEqual(self.client.post('/inbound/locations/',data='{}',content_type='application/json').status_code,403)
 def test_filtered_totals_span_pages(self):
  Allocation.objects.bulk_create([Allocation(stock=self.stock,project='MATCH',quantity=i+1) for i in range(55)])
  Allocation.objects.create(stock=self.stock,project='OTHER',quantity=999)
  for page in (1,2):
   response=self.client.get('/allocations/',{'q':'MATCH','page':page})
   self.assertEqual(response.context['filtered_quantity'],1540)
   self.assertContains(response,'id="processing-quantity"')
