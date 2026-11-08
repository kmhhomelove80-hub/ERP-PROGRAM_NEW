"""Regression checks at the boundary of the PR and existing ERP workflows."""
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import runpy

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.test import TestCase

from .models import Allocation, Item, ProductionPlanDraft, ProductionPlanSnapshot, Stock
from .test_production import fixture


class ProductionIntegrationTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user('integration-admin', is_staff=True)
        self.worker = get_user_model().objects.create_user('integration-worker')
        self.client.force_login(self.admin)
        item = Item.objects.create(code='EXISTING-ACS', name='ACS', specification='IP56')
        self.stock = Stock.objects.create(item=item, location='EXISTING-A', quantity=40, reserved=4)
        Allocation.objects.create(stock=self.stock, project='기존 예약', quantity=4)

    def existing_rows(self):
        return {
            model._meta.label: list(model.objects.order_by('pk').values())
            for model in apps.get_app_config('inventory').get_models()
            if model not in (ProductionPlanDraft, ProductionPlanSnapshot)
        }

    def publish(self):
        content, mapping = fixture(1, True)
        response = self.client.post('/production/settings/', {
            'action': 'upload', 'workbook': SimpleUploadedFile('synthetic-plan.xlsx', content),
        })
        self.assertEqual(response.status_code, 302)
        response = self.client.get('/production/settings/')
        draft = ProductionPlanDraft.objects.get(user=self.admin)
        config = response.context['config']
        response = self.client.post('/production/settings/', {
            'action': 'apply', 'revision': str(draft.revision),
            'configuration_token': response.context['configuration_token'],
            **{key: config[key] for key in ('sheet', 'first', 'last', 'start')},
            **{'col_' + key: value for key, value in mapping.items()},
        })
        self.assertRedirects(response, '/production/')

    def snapshot(self):
        output = StringIO()
        with redirect_stdout(output):
            runpy.run_path(str(Path(settings.BASE_DIR) / 'scripts/update_snapshot.py'))
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertTrue(lines[0].startswith('SUNIL_SNAPSHOT='))
        return json.loads(lines[0].removeprefix('SUNIL_SNAPSHOT=')), lines[0]

    def test_production_upload_apply_and_read_preserve_all_existing_inventory_rows(self):
        before = self.existing_rows()
        self.publish()
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get('/production/').status_code, 200)
        self.assertEqual(self.existing_rows(), before)
        self.assertEqual(ProductionPlanSnapshot.objects.get(pk=1).uploaded_by, self.admin)
        for action in ('upload', 'inspect', 'apply'):
            self.assertEqual(self.client.post('/production/settings/', {'action': action}).status_code, 403)
        self.assertEqual(self.existing_rows(), before)

    def test_combined_stock_search_and_grouped_navigation_remain_available(self):
        self.client.force_login(self.worker)
        response = self.client.get('/?model=acs&spec=ip56')
        self.assertContains(response, 'EXISTING-ACS')
        self.assertNotContains(self.client.get('/?model=acs&spec=wrong-spec'), 'EXISTING-ACS')
        for url in ('/', '/production/', '/movements/'):
            response = self.client.get(url)
            self.assertContains(response, 'href="/production/"')
            self.assertContains(response, '>생산계획조회</a>')
            self.assertContains(response, 'class="primary-nav"')
            self.assertContains(response, 'name="sunil-primary-menu"', count=3)
            self.assertContains(response, 'inventory/navigation.js')
            self.assertContains(response, '작업자—조회·이동 요청')
            self.assertNotContains(response, 'href="/picking/new/"')
            self.assertNotContains(response, 'href="/admin/auth/user/"')

    def test_migration_graph_has_one_inventory_leaf_and_preserves_existing_dependencies(self):
        loader = MigrationLoader(connection)
        self.assertEqual(loader.detect_conflicts(), {})
        self.assertEqual(loader.graph.leaf_nodes('inventory'), [('inventory', '0016_reconciliation_inventory_amount')])
        migration = loader.disk_migrations['inventory', '0012_production_plan']
        self.assertIn(('inventory', '0011_substitute_loans'), migration.dependencies)
        self.assertIn(('inventory', '0012_production_plan'), loader.disk_migrations['inventory', '0013_employee_menu_access'].dependencies)
        self.assertEqual({operation.name for operation in migration.operations}, {
            'ProductionPlanDraft', 'ProductionPlanSnapshot',
        })

    def test_data_fingerprint_supports_uploaded_binary_workbooks_and_detects_changes(self):
        self.publish()
        before_rows = self.existing_rows()
        before, before_line = self.snapshot()
        self.assertEqual(before['productionplandraft']['count'], 1)
        self.assertEqual(before['productionplansnapshot']['count'], 1)
        self.assertEqual(before['stock_totals'], {'quantity': 40, 'reserved': 4})
        self.assertNotIn('synthetic-plan.xlsx', before_line)
        self.assertNotIn('integration-admin', before_line)
        self.assertEqual(self.snapshot()[0], before)
        draft = ProductionPlanDraft.objects.get(user=self.admin)
        draft.workbook = bytes(draft.workbook) + b'fingerprint-only-change'
        draft.save(update_fields=['workbook'])
        after, _ = self.snapshot()
        self.assertNotEqual(before['productionplandraft']['sha256'], after['productionplandraft']['sha256'])
        self.assertEqual(before['productionplansnapshot'], after['productionplansnapshot'])
        self.assertEqual(self.existing_rows(), before_rows)
