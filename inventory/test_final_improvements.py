import io
import uuid
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from django.urls import reverse
from openpyxl import load_workbook
from .models import Item, Stock, PickingOrder, Allocation, ErpSnapshot, ReconciliationRow, Substitution


class StockColumnFilterTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('filter-manager')
        self.client.force_login(self.user)
        item = Item.objects.create(code='SAMPLE-A', name='조명', specification='기본 사양')
        self.rows = [Stock.objects.create(item=item, location=f'A{i:03}', quantity=10, reserved=2) for i in range(55)]
        self.other = Stock.objects.create(item=item, location='B001', quantity=4, status='expected', legacy_specification='별도 사양')
        self.unknown = Stock.objects.create(item=Item.objects.create(code='UNKNOWN'), location='C001', quantity=3)
        snap = ErpSnapshot.objects.create(actor=self.user, label='가상 자료', erp_as_of=timezone.now(), digest='a'*64)
        ReconciliationRow.objects.create(snapshot=snap, warehouse='Y09', code=item.code, erp_quantity=554, shipping_pending=7)

    def test_whole_query_totals_export_and_multiselect(self):
        params = {'f_location': ['A000', 'A054'], 'min_available': '8', 'max_quantity': '10'}
        response = self.client.get('/', params)
        self.assertEqual(response.context['page_obj'].paginator.count, 2)
        self.assertEqual(response.context['totals'], {'quantity': 20, 'reserved': 4, 'available': 16})
        book = load_workbook(io.BytesIO(self.client.get('/stock/export/', params).content), read_only=True)
        self.assertEqual(len(list(book.active.values)), 3)
        book.close()
        response = self.client.get('/stock/options/', {'field': 'location'})
        self.assertIn('A054', [v['value'] for v in response.json()['values']])

    def test_spec_override_pending_and_unknown(self):
        self.assertEqual(self.client.get('/', {'f_specification': '별도 사양'}).context['page_obj'].paginator.count, 1)
        self.assertEqual(self.client.get('/', {'f_specification': '기본 사양'}).context['page_obj'].paginator.count, 55)
        self.assertEqual(self.client.get('/', {'f_pending': '7'}).context['page_obj'].paginator.count, 56)
        self.assertEqual(self.client.get('/', {'f_pending': ''}).context['page_obj'].paginator.count, 1)
        values = self.client.get('/stock/options/', {'field': 'pending'}).json()['values']
        self.assertEqual({v['value'] for v in values}, {'', '7'})
        # Choice search is applied to all filtered rows, not just the loaded page.
        self.assertEqual(self.client.get('/stock/options/', {'field': 'location', 'search': 'A054'}).json()['values'][0]['value'], 'A054')

    def test_validation_auth_and_pagination(self):
        for path in ['/', '/stock/export/', '/stock/options/?field=quantity']:
            self.assertEqual(self.client.get(path, {'min_quantity': '9', 'max_quantity': '2', 'field': 'quantity'}).status_code, 400)
        self.assertEqual(self.client.get('/stock/options/', {'field': 'password'}).status_code, 400)
        response = self.client.get('/', {'f_location': ['A000', 'A054'], 'sort': 'available'})
        self.assertContains(response, 'f_location=A000&amp;f_location=A054')
        self.client.logout()
        self.assertEqual(self.client.get('/stock/options/', {'field': 'code'}).status_code, 302)


class PickingShortageShortcutTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('shortcut-manager')
        self.client.force_login(self.user)
        self.a = Item.objects.create(code='REQUEST-A', name='요청 제품')
        self.b = Item.objects.create(code='SUB-B', name='대체 제품')
        self.stock = Stock.objects.create(item=self.b, location='B01', quantity=8)
        self.order = PickingOrder.objects.create(actor=self.user, project='가상 주문', payload={'rows': [{'code': self.a.code, 'quantity': 6}], 'customer_rows': []})

    def test_prefill_partial_returns_without_dispatch_and_replay(self):
        url = reverse('substitution-create', args=[self.order.pk]) + '?requested=REQUEST-A'
        response = self.client.get(url)
        self.assertEqual(response.context['form']['quantity'].value(), 6)
        self.assertEqual(response.context['form']['requested'].value(), 'REQUEST-A')
        data = {'request_id': str(uuid.uuid4()), 'requested': 'REQUEST-A', 'stock': self.stock.pk, 'quantity': 2, 'reason': '동일 규격', 'confirmed': 'on'}
        for _ in range(2):
            response = self.client.post(url, data)
            self.assertRedirects(response, reverse('picking-detail', args=[self.order.pk]))
        self.assertEqual(Substitution.objects.count(), 1)
        self.stock.refresh_from_db()
        self.assertEqual((self.stock.quantity, self.stock.reserved), (8, 2))
        self.assertEqual(self.order.allocations.get().state, 'reserved')
        response = self.client.get(reverse('picking-detail', args=[self.order.pk]))
        self.assertEqual(response.context['balance'][0]['shortage'], 4)
        self.assertContains(response, '대체품 등록')

    def test_preview_shortcut_creates_signed_order_then_opens_matching_code(self):
        response = self.client.post('/picking/new/', {'action': 'preview', 'request_id': str(uuid.uuid4()), 'project': '새 가상 주문', 'pasted_rows': '6\tREQUEST-A'})
        self.assertContains(response, 'name="substitute_requested" value="REQUEST-A"')
        token = response.context['confirm_form']['preview_token'].value()
        response = self.client.post('/picking/new/', {'action': 'confirm', 'preview_token': token, 'allow_shortage': 'on', 'substitution_draft': '1', 'substitute_requested': 'REQUEST-A'})
        order = PickingOrder.objects.exclude(pk=self.order.pk).get()
        self.assertRedirects(response, reverse('substitution-create', args=[order.pk]) + '?requested=REQUEST-A')
        self.assertEqual(order.allocations.count(), 0)
        self.stock.refresh_from_db(); self.assertEqual(self.stock.reserved, 0)
        # A tampered preview cannot create another order or reserve stock.
        response = self.client.post('/picking/new/', {'action': 'confirm', 'preview_token': token+'bad', 'allow_shortage': 'on', 'substitution_draft': '1', 'substitute_requested': 'REQUEST-A'})
        self.assertEqual(PickingOrder.objects.count(), 2)

    def test_customer_print_history_link_and_invalid_code_not_prefilled(self):
        response = self.client.get(reverse('customer-picking-print', args=[self.order.pk]))
        self.assertContains(response, '피킹 주문 이력')
        response = self.client.get(reverse('substitution-create', args=[self.order.pk]), {'requested': 'NOT-IN-ORDER'})
        self.assertIsNone(response.context['form']['quantity'].value())
