"""Synthetic-only end-to-end registration; no credentials/vendor UI required."""
from datetime import timedelta
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import subprocess
import tempfile
import uuid
from contextlib import nullcontext
from unittest.mock import patch
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from .models import Allocation, AuditEvent, ErpSnapshot, Item, Stock
from .reconciliation import create_snapshot

script_root = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(script_root))
spec = importlib.util.spec_from_file_location('actas_register_tested', script_root / 'actas_register.py')
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


class ActasRegistrationTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name) / 'capture';self.folder.mkdir()
        self.manager = get_user_model().objects.create_user('register-manager', is_staff=True)
        self.reader = get_user_model().objects.create_user('register-reader')
        item = Item.objects.create(code='BUSAN')
        stock = Stock.objects.create(item=item, location='A', quantity=20, reserved=4)
        Allocation.objects.create(stock=stock, quantity=4, project='기존 예약')
        missing = Item.objects.create(code='MANAGED-ONLY')
        Stock.objects.create(item=missing, location='B', quantity=3)
        self.before = list(Stock.objects.values()), list(Allocation.objects.values())
        self.record = self.make_record()

    def make_record(self, quantity=-2, pending=3):
        at = (timezone.now() - timedelta(minutes=2)).isoformat()
        record = dict(format=3, pilot_version=5, kind='Actas-export-pilot-NOT-import', phase='export_files_checked',
                      started_at=at, completed_at=at, registered_in_sunil=False, files={}, input_evidence={})
        for wh, code in [('Y02', 'HEADQUARTERS'), ('Y09', 'BUSAN')]:
            data = ('품목코드\t품목명\t현재고\t출하대기\r\n'
                    f'{code}\t테스트\t{quantity}\t{pending}\r\n').encode('cp949')
            (self.folder / (wh + '.xls')).write_bytes(data)
            record['files'][wh] = dict(name=wh + '.xls', parse_status='valid',
                                      query_confirmed_by='operator', query_confirmed_at=at, saved_at=at,
                                      **bridge.pilot.inspect_export(data))
            record['input_evidence'][wh] = dict(requested=wh, editor_readback=wh,
                input_method='native_keyboard_then_Tab_ShiftTab', tab_transition_observed=True,
                warehouse_name_confirmed_by='operator', query_sent=True,
                query_check=dict(editor_readback=wh, focus_verified=True, query_sent=True))
        self.write(record)
        return record

    def write(self, record):
        (self.folder / 'result.json').write_text(json.dumps(record), encoding='utf-8')

    def register(self):return bridge.register_result(self.folder, actor=self.manager)

    def assert_stock_unchanged(self):
        self.assertEqual((list(Stock.objects.values()), list(Allocation.objects.values())), self.before)

    def test_registers_both_files_and_freezes_comparison_without_stock_changes(self):
        original = (self.folder / 'result.json').read_bytes()
        result = self.register()
        self.assertEqual(ErpSnapshot.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action='erp_snapshot').count(), 1)
        self.assertEqual(result['counts'], {'Y02': 1, 'Y09': 1})
        self.assertEqual(result['comparison']['Y09'], dict(rows=2, source_items=1, erp_quantity=-2,
            negative_quantity_items=1, matched=0, different=1, missing_in_erp=1, managed_quantity=23))
        self.assertEqual(ErpSnapshot.objects.get().rows.get(code='BUSAN').difference, 22)
        self.assertTrue(result['registered_in_sunil']);self.assertFalse(result['stock_modified'])
        self.assertIn('/erp/', result['detail_path'])
        self.assertEqual((self.folder / 'result.json').read_bytes(), original)
        self.assertEqual(json.loads((self.folder / 'registration.json').read_text())['phase'], 'complete')
        self.assert_stock_unchanged()

    def test_retry_and_copied_run_have_same_uuid_and_do_not_recompare(self):
        first = self.register()
        Stock.objects.filter(item__code='BUSAN').update(quantity=99)
        second = self.register()
        copied = Path(self.temp.name) / 'copy';shutil.copytree(self.folder, copied)
        third = bridge.register_result(copied, actor=self.manager)
        self.assertEqual(first['snapshot_id'], second['snapshot_id']);self.assertEqual(first['snapshot_id'], third['snapshot_id'])
        self.assertEqual(first['comparison'], second['comparison'])
        self.assertEqual(ErpSnapshot.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action='erp_snapshot').count(), 1)

    def test_commit_then_lost_journal_retries_same_snapshot(self):
        write = bridge.pilot.save_json
        def fail_final(path, data):
            if data.get('phase') == 'complete':raise OSError('synthetic final write failure')
            return write(path, data)
        with patch.object(bridge.pilot, 'save_json', side_effect=fail_final):
            with self.assertRaises(OSError):self.register()
        self.assertEqual(ErpSnapshot.objects.count(), 1)
        self.assertEqual(json.loads((self.folder / 'registration.json').read_text())['phase'], 'registration_unconfirmed')
        self.register()
        self.assertEqual(ErpSnapshot.objects.count(), 1);self.assert_stock_unchanged()

    def test_audit_failure_rolls_back_both_warehouses_and_retry_works(self):
        with patch('inventory.reconciliation.audit', side_effect=RuntimeError('synthetic audit failure')):
            with self.assertRaises(RuntimeError):self.register()
        self.assertEqual(ErpSnapshot.objects.count(), 0)
        self.assert_stock_unchanged()
        self.register();self.assertEqual(ErpSnapshot.objects.count(), 1)

    def test_reader_denied_before_file_or_db_write(self):
        with self.assertRaises(ValidationError):bridge.register_result(self.folder, actor=self.reader)
        self.assertFalse((self.folder / 'registration.lock').exists());self.assertFalse(ErpSnapshot.objects.exists())

    def test_another_manager_cannot_claim_same_request(self):
        self.register()
        other = get_user_model().objects.create_user('different-manager', is_staff=True)
        with self.assertRaises(ValidationError):bridge.register_result(self.folder, actor=other)
        self.assertEqual(ErpSnapshot.objects.count(), 1)

    def test_bad_source_hash_phase_summary_or_query_evidence_prevents_registration(self):
        changes = [lambda r: r.update(pilot_version=2), lambda r: r.update(phase='failed'),
                   lambda r: r['files']['Y09'].update(sha256='0' * 64),
                   lambda r: r['files']['Y09'].update(items=999),
                   lambda r: r['input_evidence']['Y09']['query_check'].update(focus_verified=False)]
        for change in changes:
            with self.subTest(change=change):
                record = json.loads(json.dumps(self.record));change(record);self.write(record)
                with self.assertRaises(RuntimeError):self.register()
                self.assertFalse(ErpSnapshot.objects.exists())
        self.assert_stock_unchanged()

    def test_changed_file_after_export_rejects_entire_pair(self):
        with (self.folder / 'Y09.xls').open('ab') as f:f.write(b'\r\n')
        with self.assertRaises(RuntimeError):self.register()
        self.assertFalse(ErpSnapshot.objects.exists())

    def test_fractional_and_blank_source_are_preserved_and_not_rounded(self):
        for quantity, pending in [('-2.5', 3), ('', 3), (2, '')]:
            with self.subTest(quantity=quantity, pending=pending):
                self.make_record(quantity, pending)
                before = (self.folder / 'Y02.xls').read_bytes()
                with self.assertRaisesRegex(RuntimeError, '소수·공란'):self.register()
                self.assertFalse(ErpSnapshot.objects.exists())
                self.assertEqual((self.folder / 'Y02.xls').read_bytes(), before)

    def test_newer_reference_blocks_old_run_but_retry_of_existing_run_is_allowed(self):
        self.register()
        create_snapshot(request_id=uuid.uuid4(), label='새 기준', erp_as_of=timezone.now(), actor=self.manager,
            plan=dict(digest='f' * 64, source_name='new.xlsx', rows=[dict(warehouse='Y09', code='BUSAN', name='새 자료', quantity=1, shipping_pending=0)]))
        self.register()  # Retry must not create a third snapshot.
        self.assertEqual(ErpSnapshot.objects.count(), 2)
        old = json.loads(json.dumps(self.record));old['started_at'] = (timezone.now()-timedelta(days=1)).isoformat();self.write(old)
        with self.assertRaisesRegex(RuntimeError, '최신'):self.register()
        self.assertEqual(ErpSnapshot.objects.count(), 2)

    def test_registration_lock_rejects_second_writer(self):
        with bridge.registration_lock(self.folder):
            with self.assertRaises(RuntimeError):
                with bridge.registration_lock(self.folder):self.fail('second writer entered')

    def test_complete_pipeline_uses_saved_validated_pair_for_registration(self):
        class Adapter:
            def preflight(self):pass
            def enter_warehouse(self, warehouse, **kwargs):
                self.warehouse = warehouse
                return dict(requested=warehouse, editor_readback=warehouse,
                    input_method='native_keyboard_then_Tab_ShiftTab', tab_transition_observed=True)
            def query_committed(self, warehouse):
                return dict(editor_readback=warehouse, focus_verified=True, query_sent=True)
            def save_export(self, path):
                shutil.copyfile(self_source / (self.warehouse + '.xls'), path)
        self_source = self.folder
        with patch.object(bridge.pilot, 'WindowsActas', return_value=Adapter()), \
                patch.object(bridge.pilot, 'single_pilot', return_value=nullcontext()), \
                patch.object(bridge.pilot, 'confirm_token', return_value=True), \
                patch.object(bridge.pilot.time, 'sleep'), \
                patch.object(bridge.pilot, 'wait_file', side_effect=lambda p: p.read_bytes()), \
                patch('builtins.print'):
            folder, report = bridge.collect_checked(Path(self.temp.name) / 'collected')
        result = bridge.register_result(folder, actor=self.manager)
        self.assertEqual(result['counts'], {'Y02': 1, 'Y09': 1});self.assert_stock_unchanged()


@skipUnless(os.name == 'nt', 'Native Windows same-account DPAPI fixture')
class ActasRuntimeTests(TestCase):
    def test_reads_temporary_dpapi_with_existing_python_and_no_config_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp);app = root / 'app';app.mkdir();(app / 'manage.py').write_text('# synthetic app')
            secret = root / 'synthetic.clixml'
            config_path = root / 'runtime.local.json'
            config = dict(PythonExe=sys.executable, AppRoot=str(app), DataRoot=str(root / 'data'), SecretsPath=str(secret),
                          Environment={'ERP_DB_NAME': 'synthetic-no-connection', 'ERP_DB_USER': 'synthetic'})
            config_path.write_text(json.dumps(config), encoding='utf-8')
            powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
            code = "$ErrorActionPreference='Stop'; $v=[pscustomobject]@{OwnerSid=[Security.Principal.WindowsIdentity]::GetCurrent().User.Value; Secrets=@{ERP_SECRET_KEY=(ConvertTo-SecureString 'synthetic-key-only' -AsPlainText -Force); ERP_DB_PASSWORD=(ConvertTo-SecureString 'synthetic-password-only' -AsPlainText -Force)}}; $v | Export-Clixml -LiteralPath $env:SUNIL_TEST_SECRET"
            proc = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-Command', code],
                env={**os.environ, 'SUNIL_TEST_SECRET': str(secret)}, capture_output=True, timeout=30)
            self.assertEqual(proc.returncode, 0)
            before = config_path.read_bytes(), secret.read_bytes()
            old_cwd, old_path = Path.cwd(), sys.path[:]
            try:
                with patch.dict(os.environ, ERP_DB_NAME='stale-do-not-use', ERP_SQLITE_PATH='stale.sqlite3'):
                    actual_app, actual_data = bridge.runtime_environment(config_path)
                    self.assertEqual(actual_app, app);self.assertEqual(actual_data, root / 'data')
                    self.assertEqual(os.environ['ERP_DB_NAME'], 'synthetic-no-connection')
                    self.assertEqual(os.environ['ERP_DB_PASSWORD'], 'synthetic-password-only')
                    self.assertNotIn('ERP_SQLITE_PATH', os.environ)
            finally:
                os.chdir(old_cwd);sys.path[:] = old_path
            self.assertEqual((config_path.read_bytes(), secret.read_bytes()), before)
