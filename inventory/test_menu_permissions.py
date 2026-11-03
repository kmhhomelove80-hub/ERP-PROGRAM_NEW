import itertools
import uuid
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from .menu_permissions import MENU_LABELS, can_write
from .models import EmployeeMenuAccess, Item, Stock, Allocation, MovementRequest, AuditEvent, ProductionPlanDraft
from .services import reserve, receive_stock, confirm_arrival, process_movement, submit_movement


class MenuPermissionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('menu-worker')
        self.item = Item.objects.create(code='MENU-TEST')
        self.stock = Stock.objects.create(item=self.item, location='A', quantity=10)
        self.move = MovementRequest.objects.create(source=self.stock, requested_by=self.user, quantity=1, destination='B')
        self.profile = EmployeeMenuAccess.objects.create(user=self.user)
        self.client.force_login(self.user)

    def grant(self, *menus):
        for key in MENU_LABELS:
            setattr(self.profile, key, 'full' if key in menus else 'read')
        self.profile.save()
        self.user.is_staff = 'management' in menus
        self.user.save(update_fields=['is_staff'])

    def test_all_32_independent_permission_combinations_at_http_boundary(self):
        paths = {'receiving': '/receipts/new/', 'shipping': '/picking/new/',
                 'production': '/production/settings/', 'inventory': f'/movements/{self.move.pk}/',
                 'management': '/erp/upload/'}
        for selected in itertools.product((False, True), repeat=5):
            menus = [key for key, flag in zip(MENU_LABELS, selected) if flag]
            self.grant(*menus)
            for key, path in paths.items():
                with self.subTest(granted=menus, endpoint=key):
                    response = self.client.post(path, {})
                    self.assertEqual(response.status_code == 403, key not in menus)
            self.stock.refresh_from_db();self.move.refresh_from_db()
            self.assertEqual((self.stock.quantity, self.stock.reserved, self.move.state), (10, 0, 'pending'))
        self.assertFalse(AuditEvent.objects.exists())

    def test_staff_flag_does_not_override_explicit_menu_restrictions(self):
        self.grant('management')
        self.assertTrue(self.user.is_staff)
        self.assertEqual(self.client.get('/admin/auth/user/').status_code, 200)
        for path in ('/receipts/new/', '/picking/new/', '/production/settings/'):
            self.assertEqual(self.client.post(path, {}).status_code, 403)
        self.assertEqual(self.client.post(f'/movements/{self.move.pk}/', {}).status_code, 403)

    def test_receiving_only_employee_can_receive_but_cannot_ship_or_administer(self):
        self.grant('receiving')
        receipt = receive_stock(item_id=self.item.pk, location='A', quantity=2, status='received',
                                expected_date=None, sender='', request_id=uuid.uuid4(), actor=self.user)
        self.assertEqual(receipt.quantity, 2)
        self.assertFalse(self.user.is_staff)
        with self.assertRaises(ValidationError): reserve(stock_id=self.stock.pk, project='forbidden', quantity=1, actor=self.user)
        with self.assertRaises(ValidationError): process_movement(request_id=self.move.pk, actor=self.user)
        self.assertEqual(self.client.get('/admin/auth/user/').status_code, 302)
        self.assertEqual(self.client.post('/production/settings/', {}).status_code, 403)

    def test_shipping_only_employee_can_reserve_but_cannot_receive(self):
        self.grant('shipping')
        row = reserve(stock_id=self.stock.pk, project='allowed', quantity=2, actor=self.user)
        self.assertEqual(row.quantity, 2)
        with self.assertRaises(ValidationError):
            receive_stock(item_id=self.item.pk, location='A', quantity=2, status='received', expected_date=None,
                          sender='', request_id=uuid.uuid4(), actor=self.user)
        with self.assertRaises(ValidationError): confirm_arrival(stock_id=self.stock.pk, location='X', actor=self.user)
        self.assertEqual(self.client.post('/erp/upload/', {}).status_code, 403)

    def test_inventory_only_employee_can_approve_without_staff_or_other_write_access(self):
        self.grant('inventory')
        response = self.client.post(f'/movements/{self.move.pk}/', {'action': 'process', 'confirmed': 'on'})
        self.assertEqual(response.status_code, 302)
        self.stock.refresh_from_db();self.move.refresh_from_db()
        self.assertEqual((self.stock.quantity,self.move.state), (9,'completed'))
        self.assertEqual(self.client.post('/receipts/new/', {}).status_code, 403)
        self.assertEqual(self.client.post('/picking/new/', {}).status_code, 403)
        self.assertEqual(self.client.get('/admin/auth/user/').status_code, 302)

    def test_production_only_employee_can_upload_without_other_write_access(self):
        self.grant('production')
        from .test_production import fixture
        from django.core.files.uploadedfile import SimpleUploadedFile
        content, _ = fixture()
        response = self.client.post('/production/settings/', {'action': 'upload', 'workbook': SimpleUploadedFile('synthetic.xlsx', content)})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(bytes(ProductionPlanDraft.objects.get(user=self.user).workbook), content)
        self.assertNotContains(self.client.get('/'), 'href="/picking/new/"')
        self.assertEqual(self.client.post('/receipts/new/', {}).status_code, 403)
        self.assertEqual(self.client.post('/erp/upload/', {}).status_code, 403)

    def test_revocation_applies_to_existing_session_and_service_calls(self):
        self.grant('inventory')
        self.assertEqual(self.client.get('/notifications/movements/').json(), {'pending': 1})
        EmployeeMenuAccess.objects.filter(pk=self.profile.pk).update(inventory='read')
        self.assertEqual(self.client.get('/notifications/movements/').status_code, 403)
        self.assertEqual(self.client.post(f'/movements/{self.move.pk}/', {}).status_code, 403)
        with self.assertRaises(ValidationError): process_movement(request_id=self.move.pk, actor=self.user)

    def test_read_only_employee_can_still_request_movement_without_changing_stock(self):
        self.grant()
        response = self.client.post(f'/stock/{self.stock.pk}/move/', {'request_id': uuid.uuid4(), 'destination':'C','quantity':2,'confirmed':'on'})
        self.assertEqual(response.status_code,302)
        self.stock.refresh_from_db();self.assertEqual(self.stock.quantity,10)
        self.assertEqual(self.client.post(response.url, {'action':'process','confirmed':'on'}).status_code,403)

    def test_superuser_retains_all_permissions(self):
        self.user.is_superuser=True;self.user.is_staff=True;self.user.save()
        self.assertTrue(all(can_write(self.user,key) for key in MENU_LABELS))

    def test_notice_counts_pending_only_and_has_no_inventory_side_effects(self):
        self.grant('inventory')
        MovementRequest.objects.create(source=self.stock,requested_by=self.user,destination='D',quantity=1,state='completed')
        MovementRequest.objects.create(source=self.stock,requested_by=self.user,destination='E',quantity=1,state='rejected')
        response=self.client.get('/')
        self.assertContains(response,'처리 대기 중인 재고 이동 요청')
        self.assertContains(response,'/movements/?status=pending')
        endpoint=self.client.get('/notifications/movements/')
        self.assertEqual(endpoint.json(),{'pending':1})
        self.assertIn('no-store',endpoint['Cache-Control'])
        self.assertEqual(self.client.post('/notifications/movements/',{}).status_code,405)
        self.assertEqual(Stock.objects.get(pk=self.stock.pk).quantity,10)
        self.assertFalse(AuditEvent.objects.exists())
        process_movement(request_id=self.move.pk,actor=self.user,rejection_reason='가상 거부')
        self.assertEqual(self.client.get('/notifications/movements/').json(),{'pending':0})
        self.assertContains(self.client.get('/'),'data-notifications-url="/notifications/movements/" hidden')
        self.grant()
        self.assertNotContains(self.client.get('/'),'id="movement-pending-notice"')
        self.assertEqual(self.client.get('/notifications/movements/').status_code,403)
        self.client.logout();self.assertEqual(self.client.get('/notifications/movements/').status_code,302)
