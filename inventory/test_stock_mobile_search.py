from django.contrib.auth import get_user_model
from django.test import TestCase
from .models import Item, Stock, Allocation


class StockMobileSearchTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('scan-user', is_staff=True)
        self.client.force_login(self.user)
        self.item = Item.objects.create(code='8801234567890', name='조명', specification='24V')
        self.stock = Stock.objects.create(item=self.item, location='A-01', quantity=9, reserved=2)
        self.expected = Stock.objects.create(item=self.item, location='예정', status='expected', quantity=4)

    def test_scanner_text_and_location_search_including_partial(self):
        for query in [self.item.code, 'A-01']:
            response = self.client.get('/', {'q': query, 'partial': '1'})
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, f'/stock/{self.stock.pk}/move/')
            self.assertContains(response, 'stock-row')
            self.assertNotContains(response, 'scan-dialog')
        response = self.client.get('/', {'q': 'not-a-code'})
        self.assertEqual(response.context['page_obj'].paginator.count, 0)
        self.stock.refresh_from_db()
        self.assertEqual((self.stock.quantity, self.stock.reserved), (9, 2))

    def test_rows_expand_with_full_details_and_correct_movement(self):
        response = self.client.get('/')
        self.assertContains(response, '<details class="stock-row">', count=2)
        self.assertContains(response, '<summary aria-label=', count=2)
        self.assertContains(response, '24V')
        self.assertContains(response, f'/stock/{self.stock.pk}/move/', count=2)
        self.assertNotContains(response, f'/stock/{self.expected.pk}/move/')
        self.assertContains(response, '입고 완료된 가용 재고만 이동할 수 있습니다.')
        self.assertContains(response, 'id="scan-dialog"')
        self.assertContains(response, 'inventory/stock_scan.js')
        response = self.client.get(f'/stock/{self.stock.pk}/move/')
        self.assertEqual(response.context['stock'].pk, self.stock.pk)
        self.assertContains(response, '관리자 확인 시점')

    def test_zero_available_rows_cannot_offer_movement_and_login_required(self):
        self.stock.reserved = 9
        self.stock.save()
        self.assertNotContains(self.client.get('/'), f'/stock/{self.stock.pk}/move/')
        self.client.logout()
        self.assertEqual(self.client.get('/').status_code, 302)

    def test_scanned_markup_is_escaped_and_never_rendered_as_html(self):
        response = self.client.get('/', {'q': '<script>alert(1)</script>'})
        self.assertNotContains(response, '<script>alert(1)</script>')
        self.assertContains(response, '&lt;script&gt;')

    def test_phone_headers_and_hull_only_show_active_reservations(self):
        Allocation.objects.create(stock=self.stock, project='BLUE GRASS MARINER', quantity=2)
        Allocation.objects.create(stock=self.stock, project='완료 호선', quantity=1, state='released')
        response = self.client.get('/')
        for header in ['ITEM No. ↑', '잔량', 'Location', 'allocated', 'HULL']:
            self.assertContains(response, header)
        self.assertContains(response, 'BLUE GRASS MARINER (2)')
        self.assertNotContains(response, '완료 호선')
        self.assertEqual(response.context['page_obj'][0].available, 7)
