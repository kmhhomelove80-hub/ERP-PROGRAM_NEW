import copy
import uuid
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone
from .models import Item, Stock, PickingOrder, Allocation, AuditEvent
from .picking import plan_picking, confirm_picking, plan_supplement, confirm_supplement
from .services import receive_stock, release_selected, cancel_selected
from .substitutions import reserve_substitution, demand_balance
from .picking_totals import order_totals
from .customer_picking import customer_print_groups


class AdditionalPickingTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('additional',is_staff=True)
        self.reader=get_user_model().objects.create_user('additional-reader')
        self.item=Item.objects.create(code='TOPUP-A',name='추가 피킹 검증')
        self.stock=Stock.objects.create(item=self.item,location='OLD',quantity=80)
        self.rows=[{'code':self.item.code,'quantity':100}]
        self.customer=[dict(por='POR1',hull='호선100',block='B1',name='등기구',quantity=100,code=self.item.code)]
        self.order=confirm_picking(request_id=uuid.uuid4(),project='대량 주문',rows=self.rows,
            preview=plan_picking(self.rows),allow_shortage=True,actor=self.user,customer_rows=self.customer)
        self.original=copy.deepcopy(self.order.payload)
        self.shortages=copy.deepcopy(self.order.shortages)
        self.client.force_login(self.user)

    def receive(self,quantity=20,location='NEW'):
        return receive_stock(item_id=self.item.pk,location=location,quantity=quantity,status='received',
            expected_date=None,sender='',request_id=uuid.uuid4(),actor=self.user)

    def confirm(self,preview=None,request_id=None,**options):
        data=dict(request_id=request_id or uuid.uuid4(),order_id=self.order.pk,
            preview=preview or plan_supplement(self.order.pk),allow_shortage=True,confirmed=True,actor=self.user)
        data.update(options)
        return confirm_supplement(**data)

    def test_80_released_20_added_same_order_original_and_customer_print_preserved(self):
        first=self.order.allocations.get()
        release_selected(allocation_ids=[first.pk],shipped_at=timezone.now()-timedelta(seconds=1),actor=self.user)
        first.refresh_from_db();saved=copy.deepcopy(first.completed_details)
        self.receive()
        preview=plan_supplement(self.order.pk)
        self.assertEqual(sum(r['quantity'] for r in preview['allocations']),20)
        self.confirm(preview=preview)
        self.assertEqual(PickingOrder.objects.count(),1)
        self.order.refresh_from_db();first.refresh_from_db()
        self.assertEqual(self.order.payload,self.original);self.assertEqual(self.order.shortages,self.shortages)
        self.assertEqual(first.state,'released');self.assertEqual(first.completed_details,saved)
        self.assertEqual(order_totals(self.order)['shortage'],0)
        self.assertEqual(self.order.allocations.count(),2)
        new=Stock.objects.get(location='NEW');self.assertEqual((new.quantity,new.reserved),(20,20))
        groups=customer_print_groups(self.order)
        self.assertEqual(groups[0]['shortage'],0)
        self.assertEqual(sum(r['quantity'] for r in groups[0]['locations']),100)
        response=self.client.get(f'/picking/{self.order.pk}/')
        self.assertContains(response,'NEW')
        self.assertNotContains(response,'등록 당시 부족표')
        self.assertContains(self.client.get(f'/picking/{self.order.pk}/customer/'),'NEW')

    def test_partial_topups_can_repeat_until_fulfilled(self):
        self.assertContains(self.client.get(f'/picking/{self.order.pk}/'),'등록 당시 부족표')
        self.receive(8)
        preview=plan_supplement(self.order.pk)
        self.assertEqual(preview['shortages'][0]['shortage'],12)
        with self.assertRaises(ValidationError):self.confirm(preview=preview,allow_shortage=False)
        self.confirm(preview=preview)
        self.assertContains(self.client.get(f'/picking/{self.order.pk}/'),'등록 당시 부족표')
        self.receive(12,'NEW2');self.confirm()
        self.assertNotContains(self.client.get(f'/picking/{self.order.pk}/'),'등록 당시 부족표')
        self.assertEqual(demand_balance(self.order)[0]['shortage'],0)
        self.assertFalse(plan_supplement(self.order.pk)['allocations'])
        with self.assertRaises(ValidationError):self.confirm()
        self.assertEqual(PickingOrder.objects.count(),1)

    def test_same_request_is_idempotent_but_cross_order_or_actor_replay_fails(self):
        self.receive();preview=plan_supplement(self.order.pk);token=uuid.uuid4()
        self.confirm(preview=preview,request_id=token);self.confirm(preview=preview,request_id=token)
        self.assertEqual(self.order.allocations.count(),2)
        self.assertEqual(AuditEvent.objects.filter(action='picking_supplement').count(),1)
        other=get_user_model().objects.create_user('other-full',is_staff=True)
        with self.assertRaises(ValidationError):self.confirm(preview=preview,request_id=token,actor=other)
        with self.assertRaises(ValidationError):self.confirm(preview=preview,actor=self.reader)

    def test_stale_stock_or_existing_allocations_require_new_preview(self):
        self.receive();preview=plan_supplement(self.order.pk)
        Stock.objects.filter(location='NEW').update(reserved=1)
        with self.assertRaises(ValidationError):self.confirm(preview=preview)
        self.assertEqual(self.order.allocations.count(),1)
        self.assertFalse(AuditEvent.objects.filter(action='picking_supplement').exists())

    def test_expected_and_other_warehouse_are_not_new_arrivals(self):
        Stock.objects.create(item=self.item,location='EXPECTED',quantity=20,status='expected')
        Stock.objects.create(item=self.item,warehouse='Y02',location='HEAD',quantity=20)
        self.assertFalse(plan_supplement(self.order.pk)['allocations'])
        self.assertNotContains(self.client.get(f'/picking/{self.order.pk}/supplement/'),'기존 주문에 추가 예약 확정')

    def test_cancelled_quantities_are_not_silently_re_reserved(self):
        self.receive(100)
        cancel_selected(allocation_ids=[self.order.allocations.get().pk],actor=self.user)
        preview=plan_supplement(self.order.pk)
        self.assertEqual(preview['balance'][0]['cancelled'],80)
        self.assertEqual(preview['balance'][0]['remaining'],0)
        self.assertTrue(preview['cancelled_order'])
        self.assertFalse(preview['allocations'])
        with self.assertRaises(ValidationError): self.confirm(preview=preview)
        self.assertEqual(order_totals(self.order)['allocated'],0)

    def test_substitution_already_fills_demand_and_must_not_be_duplicated(self):
        alternate=Stock.objects.create(item=Item.objects.create(code='ALT-B'),location='ALT',quantity=20)
        reserve_substitution(request_id=uuid.uuid4(),order_id=self.order.pk,requested=self.item.code,
            stock_id=alternate.pk,quantity=20,reason='동일 제품',actor=self.user)
        self.receive()
        self.assertFalse(plan_supplement(self.order.pk)['allocations'])

    def test_audit_failure_rolls_back_reservations(self):
        self.receive();preview=plan_supplement(self.order.pk)
        with patch('inventory.picking.audit',side_effect=RuntimeError('test audit failure')):
            with self.assertRaises(RuntimeError):self.confirm(preview=preview)
        self.assertEqual(self.order.allocations.count(),1)
        self.assertEqual(Stock.objects.get(location='NEW').reserved,0)

    def test_signed_web_flow_stale_tampered_readonly_and_duplicate_post(self):
        url=f'/picking/{self.order.pk}/supplement/'
        self.receive();response=self.client.get(url)
        token=response.context['form'].initial['preview_token']
        data={'preview_token':token,'confirmed':'on','allow_shortage':'on'}
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.assertEqual(PickingOrder.objects.count(),1)
        self.assertEqual(self.order.allocations.count(),2)
        self.assertContains(self.client.post(url,{**data,'preview_token':token+'bad'}),'미리보기가 만료되었거나')
        self.client.force_login(self.reader)
        self.assertEqual(self.client.get(url).status_code,403)
        self.assertEqual(self.client.post(url,data).status_code,403)
        self.assertNotContains(self.client.get(f'/picking/{self.order.pk}/'),'미배정 물량 추가 피킹')

    def test_original_demand_missing_and_unconfirmed_request_rejected(self):
        legacy=PickingOrder.objects.create(project='이전자료',actor=self.user,payload={})
        with self.assertRaises(ValidationError):plan_supplement(legacy.pk)
        self.receive()
        with self.assertRaises(ValidationError):self.confirm(confirmed=False)

    def test_expired_or_other_users_preview_cannot_reserve(self):
        import time
        self.receive()
        url=f'/picking/{self.order.pk}/supplement/'
        token=self.client.get(url).context['form'].initial['preview_token']
        data={'preview_token':token,'confirmed':'on','allow_shortage':'on'}
        with patch('django.core.signing.time.time',return_value=time.time()+1801):
            self.assertContains(self.client.post(url,data),'미리보기가 만료되었거나')
        other=get_user_model().objects.create_user('preview-other',is_staff=True)
        self.client.force_login(other)
        self.assertContains(self.client.post(url,data),'미리보기 사용자가 일치하지 않습니다')
        self.assertEqual(self.order.allocations.count(),1)
        self.assertEqual(Stock.objects.get(location='NEW').reserved,0)

    def test_partly_cancelled_rows_do_not_turn_into_new_demand(self):
        self.receive();self.confirm()
        old=self.order.allocations.get(stock=self.stock)
        cancel_selected(allocation_ids=[old.pk],actor=self.user)
        self.receive(100,'SPARE')
        preview=plan_supplement(self.order.pk)
        self.assertFalse(preview['cancelled_order'])
        self.assertEqual(preview['balance'][0]['cancelled'],80)
        self.assertEqual(preview['balance'][0]['remaining'],0)
        self.assertFalse(preview['allocations'])
