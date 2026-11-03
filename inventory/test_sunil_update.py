import itertools
import random
import uuid
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.test import TestCase, SimpleTestCase
from .models import Item, Stock, Allocation, PickingOrder, AuditEvent, MovementRequest
from .picking import plan_picking, confirm_picking
from .location_planner import choose_locations
from .reassignment import reassign_allocation, allocation_snapshot
from .cleanup import cleanup_candidates


class ExactPlannerTests(SimpleTestCase):
    def test_requested_examples_and_pair_before_single(self):
        for qty, capacities, indices in [(40,[32,40],(1,)),(37,[34,3,100],(0,1)),(40,[45,100],(0,)),(50,[35,30,20],(1,2)),(13,[8,7,6,5,4],(0,3)),(17,[8,7,6],(0,1,2))]:
            self.assertEqual(choose_locations(capacities,qty),indices)

    def test_random_small_cases_match_exhaustive_oracle(self):
        rng = random.Random(20261103)
        for _ in range(450):
            values = [rng.randrange(1,35) for _ in range(rng.randrange(1,10))]
            request = rng.randrange(1,100)
            options = [comb for size in range(1,len(values)+1) for comb in itertools.combinations(range(len(values)),size) if sum(values[i] for i in comb)>=request]
            exact_single = [c for c in options if len(c)==1 and sum(values[i] for i in c)==request]
            exact_pair = [c for c in options if len(c)==2 and sum(values[i] for i in c)==request]
            expected = min(exact_single or exact_pair) if exact_single or exact_pair else min(options, key=lambda c:(len(c),sum(values[i] for i in c),c)) if options else tuple(range(len(values)))
            self.assertEqual(choose_locations(values,request),expected,(values,request))

    def test_cap_is_explicit_not_silent_approximation(self):
        with patch('inventory.location_planner.MAX_TRANSITIONS',0), self.assertRaisesMessage(ValidationError,'수동 위치 배정'):
            choose_locations([8,7,6],17)
        budget = [10]
        choose_locations([8,7,6],17,budget=budget)
        with self.assertRaisesMessage(ValidationError,'수동 위치 배정'):
            choose_locations([8,7,6],17,budget=budget)
        with self.assertRaisesMessage(ValidationError,'200곳'):
            choose_locations([35]*201,400)


class UpdateWorkflowTests(TestCase):
    def setUp(self):
        self.admin=get_user_model().objects.create_user('update-admin',is_staff=True)
        self.worker=get_user_model().objects.create_user('update-worker')
        self.item=Item.objects.create(code='UPDATE*001')
        self.a=Stock.objects.create(item=self.item,location='A',quantity=34)
        self.b=Stock.objects.create(item=self.item,location='B',quantity=3)
        self.c=Stock.objects.create(item=self.item,location='C',quantity=100)
        self.rows=[{'code':self.item.code,'quantity':37}]
        self.client.force_login(self.admin)

    def order(self):
        return confirm_picking(request_id=uuid.uuid4(),project='SUNIL',rows=self.rows,preview=plan_picking(self.rows),allow_shortage=False,actor=self.admin)

    def test_expected_and_received_equal_priority_and_reservations_subtracted(self):
        self.c.status='expected';self.c.quantity=37;self.c.save()
        self.assertEqual([r['stock_id'] for r in plan_picking(self.rows)['allocations']],[self.c.pk])
        self.c.reserved=1;self.c.save()
        self.assertEqual([r['stock_id'] for r in plan_picking(self.rows)['allocations']],[self.a.pk,self.b.pk])

    def test_same_location_rows_aggregate_for_location_count(self):
        self.a.quantity=10;self.a.save()
        Stock.objects.create(item=self.item,location='A',status='expected',quantity=27)
        preview=plan_picking(self.rows)
        self.assertEqual({r['location'] for r in preview['allocations']},{'A'})
        self.assertEqual(sum(r['quantity'] for r in preview['allocations']),37)

    def test_manual_preview_confirm_and_duplicate_web_post(self):
        response=self.client.post('/picking/new/',dict(action='preview',project='SUNIL',request_id=uuid.uuid4(),pasted_rows='37\tUPDATE*001'))
        token=response.context['confirm_form']['preview_token'].value()
        response=self.client.post('/picking/new/',dict(action='adjust',preview_token=token,manual_targets=f'{self.c.pk} 37'))
        self.assertContains(response,'수동 배정 미리보기')
        self.assertEqual(response.context['preview']['allocations'][0]['stock_id'],self.c.pk)
        token=response.context['confirm_form']['preview_token'].value()
        payload=dict(action='confirm',preview_token=token)
        self.assertEqual(self.client.post('/picking/new/',payload).status_code,302)
        self.assertEqual(self.client.post('/picking/new/',payload).status_code,302)
        self.assertEqual(Allocation.objects.count(),1)
        self.c.refresh_from_db();self.assertEqual(self.c.reserved,37)

    def test_invalid_manual_preserves_stock_and_manual_path_after_compute_cap(self):
        with self.assertRaises(ValidationError):plan_picking(self.rows,manual={self.c.pk:38})
        with patch('inventory.location_planner.choose_locations',side_effect=ValidationError('계산 한도')):
            response=self.client.post('/picking/new/',dict(action='preview',project='SUNIL',request_id=uuid.uuid4(),pasted_rows='37\tUPDATE*001'))
        self.assertContains(response,'예약 전 위치·수량 수동 조정')
        self.assertIsNone(response.context['preview'])
        self.assertFalse(Allocation.objects.exists())

    def test_atomic_reassign_split_preserves_order_total_audit_and_idempotency(self):
        order=self.order();allocation=order.allocations.get(stock=self.a)
        expected=allocation_snapshot(allocation);request_id=uuid.uuid4()
        kwargs=dict(allocation_id=allocation.pk,targets={self.a.pk:4,self.c.pk:30},expected=expected,request_id=request_id,reason='현장 위치 선택',actor=self.admin)
        reassign_allocation(**kwargs);reassign_allocation(**kwargs)
        self.assertEqual(order.allocations.aggregate(q=Sum('quantity'))['q'],37)
        self.assertEqual(order.allocations.count(),3)
        self.a.refresh_from_db();self.c.refresh_from_db()
        self.assertEqual((self.a.reserved,self.c.reserved),(4,30))
        self.assertEqual(AuditEvent.objects.filter(action='allocation_reassign').count(),1)
        event=AuditEvent.objects.get(action='allocation_reassign')
        self.assertEqual(event.detail['before']['details']['location'],'A')
        with self.assertRaises(ValidationError):reassign_allocation(**dict(kwargs,reason='다른 요청'))

    def test_reassign_cannot_steal_other_reservations_or_change_item_or_total(self):
        order=self.order();allocation=order.allocations.get(stock=self.a)
        self.c.reserved=90;self.c.save()
        other=Stock.objects.create(item=Item.objects.create(code='OTHER'),location='OTHER',quantity=100)
        for targets in [{self.c.pk:34},{other.pk:34},{self.c.pk:1},{}]:
            with self.subTest(targets=targets),self.assertRaises(ValidationError):
                reassign_allocation(allocation_id=allocation.pk,targets=targets,expected=allocation_snapshot(allocation),request_id=uuid.uuid4(),reason='확인',actor=self.admin)
        self.a.refresh_from_db();self.assertEqual(self.a.reserved,34)
        # A substitute reservation keeps its immutable one-to-one loan link.
        from .models import Substitution
        loan = Substitution.objects.create(allocation=allocation,requested_code='CUSTOMER-CODE',actual_code=self.item.code,reason='동일 실물',created_by=self.admin)
        self.c.reserved=0;self.c.save()
        kwargs=dict(allocation_id=allocation.pk,expected=allocation_snapshot(allocation),request_id=uuid.uuid4(),reason='위치 변경',actor=self.admin)
        with self.assertRaisesMessage(ValidationError,'한 재고'):
            reassign_allocation(targets={self.a.pk:24,self.c.pk:10},**kwargs)
        reassign_allocation(targets={self.c.pk:34},**kwargs)
        allocation.refresh_from_db();loan.refresh_from_db()
        self.assertEqual((allocation.stock_id,loan.allocation_id),(self.c.pk,allocation.pk))

    def test_reassign_stale_shipped_worker_and_audit_failure_roll_back(self):
        order=self.order();allocation=order.allocations.get(stock=self.a);expected=allocation_snapshot(allocation)
        kwargs=dict(allocation_id=allocation.pk,targets={self.c.pk:34},expected=expected,request_id=uuid.uuid4(),reason='확인',actor=self.admin)
        with patch('inventory.reassignment.audit',side_effect=RuntimeError('failure')),self.assertRaises(RuntimeError):reassign_allocation(**kwargs)
        self.a.refresh_from_db();self.c.refresh_from_db();self.assertEqual((self.a.reserved,self.c.reserved),(34,0))
        with self.assertRaises(ValidationError):reassign_allocation(**dict(kwargs,actor=self.worker))
        allocation.state='released';allocation.save()
        with self.assertRaises(ValidationError):reassign_allocation(**kwargs)
        allocation.state='reserved';allocation.save();self.a.location='CHANGED';self.a.save()
        with self.assertRaisesMessage(ValidationError,'변경되었습니다'):reassign_allocation(**kwargs)

    def test_reassign_signed_web_flow_and_permission(self):
        allocation=self.order().allocations.get(stock=self.a)
        url=f'/allocations/{allocation.pk}/reassign/'
        stale_release_token=self.client.get('/allocations/').context['filter_token']
        response=self.client.get(url);token=response.context['form']['snapshot_token'].value()
        payload=dict(request_id=uuid.uuid4(),snapshot_token=token,targets=f'{self.c.pk} 34',reason='작업 위치',confirmed='on')
        self.assertEqual(self.client.post(url,payload).status_code,302)
        self.assertEqual(self.client.post(url,payload).status_code,302)
        response=self.client.post('/allocations/',dict(action='cancel',allocations=[str(allocation.pk)],filter_token=stale_release_token,confirmed='on'))
        self.assertContains(response,'예약 위치·수량 또는 필터 결과가 변경되었습니다')
        allocation.refresh_from_db();self.assertEqual(allocation.state,'reserved')
        self.assertContains(self.client.get('/history/',{'action':'allocation_reassign'}),'예약 위치 변경')
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get(url).status_code,403)
        self.assertEqual(self.client.post(url,payload).status_code,403)

    def test_worker_requests_move_but_only_admin_executes(self):
        self.client.force_login(self.worker)
        response=self.client.post(f'/stock/{self.b.pk}/move/',dict(request_id=uuid.uuid4(),destination='A',quantity=3,confirmed='on'))
        self.assertEqual(response.status_code,302)
        self.b.refresh_from_db();self.assertEqual(self.b.quantity,3)
        self.assertContains(self.client.get(response.url),'대기')
        self.assertContains(self.client.get(f'/stock/{self.b.pk}/move/'),'이동 요청 등록')
        self.assertEqual(self.client.post(response.url,dict(action='process',confirmed='on')).status_code,403)
        from .services import process_movement
        with self.assertRaises(ValidationError):process_movement(request_id=MovementRequest.objects.get().pk,actor=self.worker)
        for url in ['/receipts/new/','/allocations/','/picking/new/']:
            self.assertEqual(self.client.post(url,{}).status_code,403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.post(response.url,dict(action='process',confirmed='on')).status_code,302)
        self.b.refresh_from_db();self.assertEqual(self.b.quantity,0)

    def test_cleanup_received_only_no_mutations_and_priority(self):
        Stock.objects.create(item=self.item,location='EXPECT',status='expected',quantity=1)
        reserved=Stock.objects.create(item=self.item,location='RESERVE',quantity=2,reserved=1)
        item=Item.objects.create(code='PRIORITY')
        for loc,qty in [('X',20),('Y',1),('Z',3)]:Stock.objects.create(item=item,location=loc,quantity=qty)
        before=list(Stock.objects.values_list('pk','quantity','reserved'))
        groups=cleanup_candidates()
        self.assertEqual(groups[0]['item'].code,'PRIORITY')
        group=next(g for g in groups if g['item']==self.item)
        self.assertEqual((group['destination'],group['emptied'],group['destination_after']),('C',1,103))
        self.assertEqual(group['moves'][0]['source']['location'],'B')
        response=self.client.get('/cleanup/')
        self.assertContains(response,'공간·조건 확인');self.assertContains(response,'인쇄 / PDF 저장')
        self.assertEqual(list(Stock.objects.values_list('pk','quantity','reserved')),before)
        self.assertFalse(MovementRequest.objects.exists())

    def test_cleanup_aggregates_multiple_rows_and_omits_entire_reserved_location(self):
        # Simulate multiple rows from future/import adapters: uniqueness currently
        # permits only one received row per location, but grouping remains explicit.
        from .cleanup import cleanup_candidates
        other=Stock(item=self.item,location='B',quantity=2,pk=999)
        with patch('inventory.cleanup.Stock.objects') as objects:
            objects.select_related.return_value.filter.return_value.order_by.return_value=[self.b,other,self.c]
            self.assertEqual(cleanup_candidates(),[])
        other.quantity=0;other.reserved=1
        with patch('inventory.cleanup.Stock.objects') as objects:
            objects.select_related.return_value.filter.return_value.order_by.return_value=[self.b,other,self.c]
            self.assertEqual(cleanup_candidates(),[])

    def test_menu_and_server_template_customizations(self):
        for user in [self.worker,self.admin]:
            self.client.force_login(user)
            response=self.client.get('/')
            for text in ['재고 조회','입고 관리','출고 관리','재고 관리','정리 리스트','관리']:
                self.assertContains(response,text)
            self.assertNotContains(response,'개발 검증 중입니다')
            if user==self.worker:
                self.assertNotContains(response,'직원 계정');self.assertContains(response,'작업자—조회·이동 요청')
        self.assertNotContains(self.client.get('/operations/'),'현재 개발 검증 환경입니다')


class BinaryEyeTests(TestCase):
    def setUp(self):
        self.user=get_user_model().objects.create_user('binaryeye')
        self.client.force_login(self.user)
        item=Item.objects.create(code='A+B&한글*01')
        Stock.objects.create(item=item,location='REAL',quantity=5)

    def test_forwarding_url_encoding_search_and_no_loop(self):
        response=self.client.get('/',{'scan':'1','content':' A+B&한글*01 '})
        self.assertEqual(response.status_code,302)
        self.assertTrue(response.url.startswith('/?scan=1&q='))
        result=self.client.get(response.url)
        self.assertContains(result,'REAL')
        self.assertEqual(result.context['scan_mode'],'1')
        self.assertEqual(result.context['page_obj'].paginator.count,1)

    def test_only_root_get_scan_empty_q_valid_length(self):
        for data in [{'scan':'0','content':'abc'},{'scan':'1','content':''},{'scan':'1','content':'a'*101},{'scan':'1','q':'existing','content':'other'},{'scan':'1','content':'bad\ncode'}]:
            self.assertEqual(self.client.get('/',data).status_code,200)
        self.assertEqual(self.client.get('/',{'scan':'1','content':'a'*100}).status_code,302)
        self.assertEqual(self.client.get('/receipts/',{'scan':'1','content':'abc'}).status_code,200)
        self.assertEqual(self.client.post('/',{'scan':'1','content':'abc'}).status_code,200)
        self.client.logout()
        response=self.client.get('/',{'scan':'1','content':'abc'},follow=True)
        self.assertTrue(response.redirect_chain[-1][0].startswith('/login/'))


from unittest import skipUnless
from django.db import connection
from django.test import TransactionTestCase
from .test_concurrency import PostgreSQLConcurrencyTests


@skipUnless(connection.vendor == 'postgresql','실제 PostgreSQL에서만 동시성 검증')
class ReassignmentConcurrencyTests(TransactionTestCase):
    parallel = PostgreSQLConcurrencyTests.parallel

    def setUp(self):
        self.admin=get_user_model().objects.create_user('reassign-race',is_staff=True)
        item=Item.objects.create(code='RACE-REASSIGN')
        self.a=Stock.objects.create(item=item,location='A',quantity=7,reserved=7)
        self.b=Stock.objects.create(item=item,location='B',quantity=10)
        self.c=Stock.objects.create(item=item,location='C',quantity=7,reserved=7)
        self.first=Allocation.objects.create(stock=self.a,quantity=7,project='P1')
        self.second=Allocation.objects.create(stock=self.c,quantity=7,project='P2')

    def attempt(self, allocation, request_id):
        try:
            reassign_allocation(allocation_id=allocation.pk,targets={self.b.pk:7},
                expected=allocation_snapshot(allocation),request_id=request_id,reason='동시성 확인',actor=self.admin)
            return 'ok'
        except ValidationError:
            return 'rejected'

    def test_competing_changes_do_not_steal_capacity(self):
        self.assertCountEqual(self.parallel([lambda:self.attempt(self.first,uuid.uuid4()),lambda:self.attempt(self.second,uuid.uuid4())]),['ok','rejected'])
        self.b.refresh_from_db();self.assertEqual(self.b.reserved,7)
        self.assertEqual(Stock.objects.aggregate(q=Sum('reserved'))['q'],14)

    def test_same_change_is_idempotent_under_concurrency(self):
        token=uuid.uuid4()
        self.assertEqual(self.parallel([lambda:self.attempt(self.first,token)]*2),['ok','ok'])
        self.b.refresh_from_db();self.assertEqual(self.b.reserved,7)
        self.assertEqual(AuditEvent.objects.filter(action='allocation_reassign').count(),1)

    def test_release_and_change_keep_physical_and_reserved_totals(self):
        from .services import release_selected
        from django.utils import timezone
        def release():
            release_selected(allocation_ids=[self.first.pk],shipped_at=timezone.now(),actor=self.admin)
            return 'released'
        results=self.parallel([lambda:self.attempt(self.first,uuid.uuid4()),release])
        self.assertIn('released',results)
        self.first.refresh_from_db()
        self.assertEqual(self.first.state,'released')
        self.assertEqual(Stock.objects.aggregate(q=Sum('quantity'))['q'],17)
        self.assertEqual(Stock.objects.aggregate(q=Sum('reserved'))['q'],7)
