from io import BytesIO
from unittest.mock import patch
from django.test import TestCase,Client
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError,PermissionDenied
from openpyxl import load_workbook
from .models import Stock,Item,Allocation,StocktakeSession,AuditEvent,CleanupDecision
from . import stocktake
from .cleanup import reviewed_candidates

class StocktakeTests(TestCase):
 def setUp(self):
  self.owner=get_user_model().objects.create_superuser('count-owner',password='fixture-only')
  self.staff=get_user_model().objects.create_user('count-staff',is_staff=True)
  self.item=Item.objects.create(code='001',name='테스트',specification='사양')
  self.stock=Stock.objects.create(item=self.item,location='A01',quantity=15,reserved=4)
  self.allocation=Allocation.objects.create(stock=self.stock,project='PACKED',quantity=4)
  self.client.force_login(self.owner)
 def session(self,kind='count'):return stocktake.start(self.owner,kind=kind)
 def upload(self,s,qty=10,reason='실사 부족',new=None):
  book=load_workbook(BytesIO(stocktake.workbook(s)));ws=book.active;ws['M2']=qty;ws['N2']=reason
  if new:ws.append(new)
  out=BytesIO();book.save(out)
  return SimpleUploadedFile('실사.xlsx',out.getvalue())
 def preview(self,s,**kwargs):return stocktake.preview(s,self.upload(s,**kwargs),self.owner)
 @patch('inventory.stocktake.verified_backup',return_value='verified-fixture')
 def test_packed_quantity_preserved_and_reversed(self,backup):
  s=self.preview(self.session());stocktake.apply(s.pk,s.preview_id,self.owner)
  self.stock.refresh_from_db();self.assertEqual((self.stock.quantity,self.stock.reserved,self.stock.available),(14,4,10));backup.assert_called_once()
  stocktake.reverse(s.pk,self.owner);self.stock.refresh_from_db();self.assertEqual(self.stock.quantity,15)
  with self.assertRaises(ValidationError):stocktake.reverse(s.pk,self.owner)
 def test_export_baseline_and_zero_blank(self):
  s=self.session();ws=load_workbook(BytesIO(stocktake.workbook(s))).active
  self.assertEqual([ws['P2'].value,ws['Q2'].value],[11,4])
  with self.assertRaises(ValidationError):self.preview(s,qty=None)
  s=self.preview(s,qty=0);self.assertEqual(s.proposal[0]['after'],4)
  with self.assertRaises(ValidationError):self.preview(s,qty=10,reason=' ')
  s.refresh_from_db();self.assertEqual(s.proposal,[])
 def test_no_difference_and_reason_required(self):
  s=self.session()
  with self.assertRaises(ValidationError):self.preview(s,qty=11,reason='')
  with self.assertRaises(ValidationError):self.preview(s,qty=10,reason='')
 @patch('inventory.stocktake.verified_backup',side_effect=ValidationError('백업 실패'))
 def test_backup_failure_and_atomic_no_change(self,backup):
  s=self.preview(self.session())
  with self.assertRaises(ValidationError):stocktake.apply(s.pk,s.preview_id,self.owner)
  self.stock.refresh_from_db();s.refresh_from_db();self.assertEqual(self.stock.quantity,15);self.assertEqual(s.state,'open');self.assertFalse(AuditEvent.objects.filter(action='stocktake_apply').exists())
 @patch('inventory.stocktake.verified_backup',return_value='verified-fixture')
 def test_change_then_return_invalidates_download_and_undo(self,backup):
  s=self.session();self.stock.quantity=16;self.stock.save();self.stock.quantity=15;self.stock.save()
  with self.assertRaises(ValidationError):self.preview(s)
  s=self.preview(self.session());stocktake.apply(s.pk,s.preview_id,self.owner)
  self.stock.refresh_from_db();self.stock.quantity+=1;self.stock.save();self.stock.quantity-=1;self.stock.save()
  with self.assertRaises(ValidationError):stocktake.reverse(s.pk,self.owner)
 @patch('inventory.stocktake.verified_backup',return_value='verified-fixture')
 def test_new_item_explicit_approval_and_no_duplicate_apply(self,backup):
  s=self.session();new=['NEW','새 제품','새 사양','Y09','B02','입고 완료',None,None,None,None,None,None,3,'실사 중 발견']
  s=self.preview(s,qty=11,reason='',new=new)
  with self.assertRaises(ValidationError):stocktake.apply(s.pk,s.preview_id,self.owner)
  stocktake.apply(s.pk,s.preview_id,self.owner,True);self.assertEqual(Stock.objects.get(item__code='NEW').quantity,3)
  with self.assertRaises(ValidationError):stocktake.apply(s.pk,s.preview_id,self.owner,True)
  stocktake.reverse(s.pk,self.owner);self.assertEqual(Stock.objects.get(item__code='NEW').quantity,0)
 def test_conflicts_duplicate_and_formula_rejected(self):
  s=self.session();duplicate=['001','테스트','사양','Y09','A01','입고 완료',15,4,11,None,None,None,10,'발견']
  with self.assertRaises(ValidationError):self.preview(s,new=duplicate)
  with self.assertRaises(ValidationError):self.preview(s,qty='=10+1')
 def test_adjust_current_not_available_and_reservation_guard(self):
  s=self.session('adjust');s=self.preview(s,qty=12);self.assertEqual(s.proposal[0]['after'],12);self.assertEqual(s.proposal[0]['excluded'],0)
  with self.assertRaises(ValidationError):self.preview(s,qty=3)
 def test_super_only_all_routes_and_csrf(self):
  s=self.session();self.client.force_login(self.staff)
  for path in ('/stocktake/','/stocktake/adjust/',f'/stocktake/{s.pk}/',f'/stocktake/{s.pk}/download/',f'/stocktake/{s.pk}/source/'):
   self.assertEqual(self.client.get(path).status_code,403)
  with self.assertRaises(PermissionDenied):stocktake.start(self.staff)
  c=Client(enforce_csrf_checks=True);c.force_login(self.owner);self.assertEqual(c.post('/stocktake/',{}).status_code,403)
 def test_stale_preview_token(self):
  s=self.preview(self.session());old=s.preview_id;s=self.preview(s,qty=9)
  with self.assertRaises(ValidationError):stocktake.apply(s.pk,old,self.owner)

class CleanupDecisionTests(TestCase):
 def setUp(self):
  self.user=get_user_model().objects.create_user('clean-owner',is_staff=True);self.client.force_login(self.user)
  self.item=Item.objects.create(code='CLEAN');self.a=Stock.objects.create(item=self.item,location='A',quantity=2);self.b=Stock.objects.create(item=self.item,location='B',quantity=10)
 def test_keep_without_reason_recheck_after_change_and_reversal(self):
  token=reviewed_candidates()[0]['moves'][0]['token'];self.client.post('/cleanup/keep/',{'single_decision':token});self.assertEqual(reviewed_candidates(),[])
  self.a.quantity=3;self.a.save();self.a.quantity=2;self.a.save();groups=reviewed_candidates();self.assertTrue(groups[0]['moves'][0]['recheck'])
  self.assertEqual(CleanupDecision.objects.count(),1)
 def test_stale_and_permission(self):
  token=reviewed_candidates()[0]['moves'][0]['token'];self.a.quantity=3;self.a.save();self.client.post('/cleanup/keep/',{'decisions':[token]});self.assertEqual(CleanupDecision.objects.count(),0)
  self.user.is_staff=False;self.user.save();self.assertEqual(self.client.post('/cleanup/keep/',{'decisions':[token]}).status_code,403)
 def test_cancel_restores_suggestion(self):
  self.client.post('/cleanup/keep/',{'decisions':[reviewed_candidates()[0]['moves'][0]['token']]});d=CleanupDecision.objects.get();self.client.post(f'/cleanup/decisions/{d.pk}/cancel/');self.assertEqual(len(reviewed_candidates()),1)

class AllocationScrollTests(TestCase):
 def test_latest_seq_group_and_full_filter_json_selection(self):
  user=get_user_model().objects.create_user('ship-owner',is_staff=True);self.client.force_login(user)
  stock=Stock.objects.create(item=Item.objects.create(code='SORT'),location='A',quantity=100,reserved=55)
  old=Allocation.objects.create(stock=stock,project='AAA OLD',legacy_seq='2026100701',quantity=1)
  newer=Allocation.objects.create(stock=stock,project='ZZZ NEW',legacy_seq='2026110501',quantity=1)
  Allocation.objects.bulk_create([Allocation(stock=stock,project='LONG',legacy_seq='2026110401',quantity=1) for _ in range(53)])
  page=self.client.get('/allocations/');self.assertEqual(page.context['allocations'][0],newer)
  response=self.client.get('/allocations/?q=LONG');self.assertEqual(len(response.context['allocations']),53)
  import json
  ids=[str(a.pk) for a in response.context['allocations']]
  post=self.client.post('/allocations/?q=LONG',{'selected_json':json.dumps(ids),'scope':'selected','filter_token':response.context['filter_token'],'action':'cancel','confirmed':'on'})
  self.assertEqual(post.status_code,302);self.assertEqual(Allocation.objects.filter(project='LONG',state='cancelled').count(),53);old.refresh_from_db();self.assertEqual(old.state,'reserved')
