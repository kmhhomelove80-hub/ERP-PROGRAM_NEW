import uuid
from datetime import date
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from .models import Item, Stock, Receipt, AuditEvent
from .services import register_receipt, confirm_arrival


class NewItemReceiptTests(TestCase):
    def setUp(self):
        self.writer=get_user_model().objects.create_user('receipt-writer',is_staff=True)
        self.reader=get_user_model().objects.create_user('receipt-reader')
        self.existing=Item.objects.create(code='EXISTING-001',name='기존 품목',specification='기존 사양')
        self.client.force_login(self.writer)
        self.url='/receipts/new/'
        self.request_id=uuid.uuid4()

    def payload(self,**overrides):
        data=dict(item_mode='new',new_code='AB12-CD*0001',new_name='신규 등기구',new_specification='AC220V / IP67',
                  request_id=self.request_id,status='received',quantity=7,location='D01')
        data.update(overrides)
        return data

    def register(self,**overrides):
        data=dict(item_mode='new',new_code='AB12-CD*0001',new_name='신규 등기구',new_specification='AC220V / IP67',
                  request_id=self.request_id,status='received',quantity=7,location='D01',expected_date=None,sender='',actor=self.writer)
        data.update(overrides)
        return register_receipt(**data)

    def test_new_item_and_receipt_created_together_and_spec_visible(self):
        response=self.client.post(self.url,self.payload())
        self.assertEqual(response.status_code,302)
        item=Item.objects.get(code='AB12-CD*0001')
        self.assertEqual((item.name,item.specification),('신규 등기구','AC220V / IP67'))
        stock=Stock.objects.get(item=item)
        self.assertEqual((stock.warehouse,stock.location,stock.quantity),('Y09','D01',7))
        self.assertEqual(Receipt.objects.get().payload['new_item']['code'],item.code)
        self.assertTrue(AuditEvent.objects.filter(action='item_created',reference=str(item.pk)).exists())
        self.assertContains(self.client.get('/',{'q':item.code}),'AC220V / IP67')

    def test_same_post_retries_once_but_changed_metadata_rejected(self):
        self.assertEqual(self.client.post(self.url,self.payload()).status_code,302)
        self.assertEqual(self.client.post(self.url,self.payload()).status_code,302)
        self.assertEqual(Item.objects.filter(code='AB12-CD*0001').count(),1)
        self.assertEqual(Receipt.objects.count(),1)
        self.assertEqual(Stock.objects.get().quantity,7)
        self.assertContains(self.client.post(self.url,self.payload(new_specification='다른 사양')),'이미 사용한 요청번호')
        self.assertEqual(Stock.objects.get().quantity,7)

    def test_duplicate_codes_case_insensitive_rejected_without_overwriting_existing(self):
        for code in ['EXISTING-001','existing-001']:
            self.assertContains(self.client.post(self.url,self.payload(new_code=code)),'이미 등록된 ERP 코드')
        self.existing.refresh_from_db()
        self.assertEqual((self.existing.name,self.existing.specification),('기존 품목','기존 사양'))
        self.assertFalse(Receipt.objects.exists());self.assertFalse(Stock.objects.exists())

    def test_existing_mode_ignores_new_item_values_and_accepts_zero_stock_item(self):
        self.assertFalse(Stock.objects.exists())
        response=self.client.post(self.url,self.payload(item_mode='existing',item=self.existing.pk,new_code='=bad'))
        self.assertEqual(response.status_code,302)
        self.assertEqual(Item.objects.count(),1)
        self.assertEqual(Receipt.objects.get().stock.item_id,self.existing.pk)

    def test_expected_new_item_arrival_uses_same_item_without_duplicate_receipt(self):
        self.assertContains(self.client.post(self.url,self.payload(status='expected')),'예정 재고에는 필수입니다.')
        self.assertEqual(Item.objects.count(),1)
        response=self.client.post(self.url,self.payload(status='expected',sender='담당자',expected_date='2026-11-03',location='EXPECTED'))
        self.assertEqual(response.status_code,302)
        receipt=Receipt.objects.get()
        self.assertEqual(receipt.stock.status,'expected')
        target=confirm_arrival(stock_id=receipt.stock_id,location='REAL',actor=self.writer)
        self.assertEqual(target.item_id,receipt.stock.item_id)
        self.assertEqual(target.quantity,7)
        self.assertEqual(Receipt.objects.count(),1)

    def test_invalid_or_missing_fields_do_not_create_orphan_items(self):
        for changes in [dict(new_code=''),dict(new_name=''),dict(location=''),dict(quantity=0),dict(item_mode='invalid'),dict(new_code='CODE WITH SPACE')]:
            response=self.client.post(self.url,self.payload(**changes))
            self.assertEqual(response.status_code,200)
            self.assertEqual(Item.objects.count(),1,changes)
            self.assertFalse(Receipt.objects.exists())

    def test_service_failure_rolls_back_item_stock_receipt_and_audit(self):
        with patch('inventory.services.audit',side_effect=RuntimeError('test audit failure')):
            with self.assertRaises(RuntimeError):self.register()
        self.assertEqual(Item.objects.count(),1)
        self.assertFalse(Receipt.objects.exists());self.assertFalse(Stock.objects.exists())
        self.assertFalse(AuditEvent.objects.exists())
        with self.assertRaises(ValidationError):self.register(quantity=0)
        self.assertEqual(Item.objects.count(),1)

    def test_reader_cannot_register_and_different_actor_cannot_replay(self):
        with self.assertRaises(ValidationError):self.register(actor=self.reader)
        self.client.force_login(self.reader)
        self.assertEqual(self.client.post(self.url,self.payload()).status_code,403)
        self.assertNotContains(self.client.get(self.url),'name="item_mode"')
        self.register()
        other=get_user_model().objects.create_user('another-writer',is_staff=True)
        with self.assertRaises(ValidationError):self.register(actor=other)
        self.assertEqual(Receipt.objects.count(),1)
