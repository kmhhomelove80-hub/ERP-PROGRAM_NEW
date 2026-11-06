"""Prove existing staff/worker grants survive the additive permission migration."""
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from .menu_permissions import MENU_LABELS


class MenuAccessMigrationTests(TransactionTestCase):
    def test_existing_accounts_are_preserved_and_receive_equivalent_menu_grants(self):
        executor=MigrationExecutor(connection)
        executor.migrate([('inventory','0012_production_plan')])
        try:
            User=get_user_model()
            User.objects.create_user('previous-manager',password='Synthetic-Only-Password',is_staff=True)
            User.objects.create_user('previous-worker',password='Synthetic-Only-Password')
            User.objects.create_user('previous-disabled',is_staff=True,is_active=False)
            before=list(User.objects.order_by('pk').values())
            executor=MigrationExecutor(connection)
            executor.migrate([('inventory','0013_employee_menu_access')])
            self.assertEqual(list(User.objects.order_by('pk').values()),before)
            EmployeeMenuAccess=executor.loader.project_state([('inventory','0013_employee_menu_access')]).apps.get_model('inventory','EmployeeMenuAccess')
            for user in User.objects.all():
                grant=EmployeeMenuAccess.objects.get(user_id=user.pk)
                level='full' if user.is_staff else 'read'
                self.assertEqual({key:getattr(grant,key) for key in MENU_LABELS},{key:level for key in MENU_LABELS})
        finally:
            executor=MigrationExecutor(connection)
            executor.migrate(executor.loader.graph.leaf_nodes())
