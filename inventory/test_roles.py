import uuid
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .models import Item, Stock, Allocation, MovementRequest, AuditEvent
from .menu_permissions import MENU_LABELS

User = get_user_model()


class TwoRoleAccessTests(TestCase):
    def setUp(self):
        self.reader=User.objects.create_user('reader')
        self.writer=User.objects.create_user('writer',is_staff=True)
        self.owner=User.objects.create_superuser('owner','owner@example.test','Long-Only-Test-Password-2048')
        self.stock=Stock.objects.create(item=Item.objects.create(code='ROLE-ITEM',name='권한 테스트'),location='A01',quantity=10,reserved=2)
        self.allocation=Allocation.objects.create(stock=self.stock,project='권한 검증 호선',quantity=2)
        self.move=MovementRequest.objects.create(source=self.stock,requested_by=self.writer,destination='B01',quantity=1)
        self.client.force_login(self.reader)

    def test_reader_browses_every_existing_list_and_others_moves(self):
        for path in ['/','/receipts/','/picking/','/allocation-history/','/allocations/','/history/','/erp/','/archives/','/substitutions/','/operations/','/movements/',f'/movements/{self.move.pk}/']:
            response=self.client.get(path)
            self.assertEqual(response.status_code,200,path)
            self.assertNotContains(response,'<form method="post" class="card"')
        self.assertContains(self.client.get('/allocations/'),'권한 검증 호선')
        self.assertNotContains(self.client.get('/allocations/'),'name="allocations"')
        self.assertContains(self.client.get('/'),f'/stock/{self.stock.pk}/move/')

    def test_reader_cannot_write_any_business_endpoint_even_with_direct_post(self):
        identifier=uuid.uuid4()
        paths=['/receipts/new/','/receipts/bulk/',f'/stock/{self.stock.pk}/arrival/','/picking/new/','/allocations/',f'/movements/{self.move.pk}/','/erp/upload/',f'/erp/rows/999/review/',f'/receipts/{identifier}/cancel/',f'/picking/{identifier}/substitute/',f'/substitutions/{identifier}/']
        for path in paths:
            self.assertEqual(self.client.post(path,{'quantity':999,'action':'process'}).status_code,403,path)
        self.stock.refresh_from_db();self.move.refresh_from_db()
        self.assertEqual((self.stock.quantity,self.stock.reserved,self.move.state),(10,2,'pending'))
        self.assertFalse(AuditEvent.objects.exists())
        self.assertEqual(self.client.get(f'/stock/{self.stock.pk}/move/').status_code,200)
        self.assertEqual(self.client.get('/admin/auth/user/').status_code,302)

    def test_full_role_can_request_and_process_movement_without_superuser(self):
        self.client.force_login(self.writer)
        response=self.client.post(f'/stock/{self.stock.pk}/move/',{'request_id':uuid.uuid4(),'destination':'C01','quantity':2,'confirmed':'on'})
        self.assertEqual(response.status_code,302)
        self.assertEqual(self.client.post(response.url,{'action':'process','confirmed':'on'}).status_code,302)
        self.assertEqual(Stock.objects.get(location='C01').quantity,2)
        self.assertContains(self.client.get('/admin/'),'사용자')
        self.assertEqual(self.client.get('/admin/inventory/item/').status_code,200)

    def test_simple_user_admin_hides_permission_fields_and_creates_both_roles(self):
        self.client.force_login(self.writer)
        response=self.client.get('/admin/auth/user/add/')
        self.assertContains(response,'조회만 가능')
        self.assertContains(response,'모두 가능')
        for key in MENU_LABELS:
            self.assertContains(response, f'name="{key}_access"')
        for field in ['is_superuser','groups','user_permissions','is_staff']:
            self.assertNotContains(response,f'name="{field}"')
        for role,staff in [('read',False),('full',True)]:
            response=self.client.post('/admin/auth/user/add/',{'username':'new-'+role,'password1':'Separate-Test-Pass-538!','password2':'Separate-Test-Pass-538!',**{key+'_access':role for key in MENU_LABELS},'_save':'저장'})
            self.assertEqual(response.status_code,302,response.context and response.context.get('adminform').form.errors)
            u=User.objects.get(username='new-'+role)
            self.assertEqual(u.is_staff,staff);self.assertFalse(u.is_superuser)
            self.assertTrue(u.check_password('Separate-Test-Pass-538!'))

    def test_edit_role_deactivate_and_protect_system_owner_and_self(self):
        self.client.force_login(self.writer)
        url=f'/admin/auth/user/{self.reader.pk}/change/'
        payload={'username':self.reader.username,**{key+'_access':'full' for key in MENU_LABELS},'is_active':'on','_save':'저장'}
        self.assertEqual(self.client.post(url,payload).status_code,302)
        self.reader.refresh_from_db();self.assertTrue(self.reader.is_staff);self.assertFalse(self.reader.is_superuser)
        payload.update({key+'_access':'read' for key in MENU_LABELS});payload.pop('is_active')
        self.assertEqual(self.client.post(url,payload).status_code,302)
        self.reader.refresh_from_db();self.assertFalse(self.reader.is_staff);self.assertFalse(self.reader.is_active)
        self.assertEqual(self.client.post(f'/admin/auth/user/{self.owner.pk}/change/',payload).status_code,403)
        response=self.client.post(f'/admin/auth/user/{self.writer.pk}/change/',{'username':'writer',**{key+'_access':'read' for key in MENU_LABELS},'is_active':'on','_save':'저장'})
        self.assertContains(response,'현재 로그인한 본인 계정')
        self.writer.refresh_from_db();self.assertTrue(self.writer.is_staff)
        self.assertEqual(self.client.post(f'/admin/auth/user/{self.reader.pk}/delete/',{'post':'yes'}).status_code,403)

    def test_demotion_revokes_an_existing_session_without_relogin(self):
        self.client.force_login(self.writer)
        User.objects.filter(pk=self.writer.pk).update(is_staff=False)
        self.assertEqual(self.client.post(f'/movements/{self.move.pk}/',{'action':'process','confirmed':'on'}).status_code,403)
