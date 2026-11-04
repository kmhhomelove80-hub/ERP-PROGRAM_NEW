from datetime import timedelta
import io
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from . import actas_collection as collector
from .models import Allocation, ErpSnapshot, Item, ReconciliationRow, Stock


def export_bytes(code='ACTAS-1', quantity=12):
    # Synthetic data only; uploaded company exports are never test fixtures.
    return ('ABC분류\t품목코드\t품목명\t현재고\t출하대기\r\n'
            f'A\t{code}\t테스트 자재\t{quantity}\t3\r\n'
            f'합계\t\t\t{quantity}\t3\r\n').encode('cp949')


class SyntheticExporter:
    def __init__(self, fail='', receipt=''):
        self.calls = []
        self.fail, self.receipt = fail, receipt

    def export(self, warehouse, target):
        self.calls.append(warehouse)
        if warehouse == self.fail:
            raise RuntimeError('injected export failure')
        target.write_bytes(export_bytes(quantity=-2 if warehouse == 'Y02' else 12))
        return {'warehouse': self.receipt or warehouse, 'query_complete': True}


class ActasCollectionTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'runs'
        self.owner = get_user_model().objects.create_user('actas-manager', is_staff=True)
        self.reader = get_user_model().objects.create_user('actas-reader')
        item = Item.objects.create(code='ACTAS-1')
        self.stock = Stock.objects.create(item=item, location='A', quantity=20, reserved=4)
        Allocation.objects.create(stock=self.stock, quantity=4, project='기존 예약')
        self.before = list(Stock.objects.values()), list(Allocation.objects.values())

    def collect(self, exporter=None):
        # File stability is tested separately; avoid waiting three seconds for
        # each independent database transaction/failure scenario.
        with patch.object(collector, 'wait_for_export', side_effect=lambda path: path.read_bytes()):
            return collector.collect(self.root, exporter or SyntheticExporter(), actor=self.owner)

    def unchanged_inventory(self):
        self.assertEqual((list(Stock.objects.values()), list(Allocation.objects.values())), self.before)

    def journal(self):
        path = next(self.root.glob('*/run.json'))
        return path.parent, json.loads(path.read_text(encoding='utf-8'))

    def test_both_exports_create_one_reference_and_preserve_negative_quantities(self):
        exporter = SyntheticExporter()
        result = self.collect(exporter)
        self.assertEqual(exporter.calls, ['Y02', 'Y09'])
        self.assertEqual(result['counts'], {'Y02': 1, 'Y09': 1})
        self.assertEqual(ErpSnapshot.objects.count(), 1)
        self.assertEqual(ReconciliationRow.objects.get(warehouse='Y02').erp_quantity, -2)
        self.assertEqual(ReconciliationRow.objects.get(warehouse='Y09').erp_quantity, 12)
        self.assertEqual(self.journal()[1]['phase'], 'complete')
        self.unchanged_inventory()

    def test_second_export_failure_never_imports_first_or_old_file(self):
        self.root.mkdir()
        (self.root / 'Y09.xls').write_bytes(export_bytes('OLD', 999))
        with self.assertRaises(RuntimeError):self.collect(SyntheticExporter(fail='Y09'))
        self.assertFalse(ErpSnapshot.objects.exists())
        self.assertEqual(self.journal()[1]['phase'], 'collection_failed')
        self.unchanged_inventory()

    def test_wrong_warehouse_or_missing_completion_receipt_rejects_before_import(self):
        exporter = SyntheticExporter(receipt='Y09')
        with self.assertRaises(RuntimeError):self.collect(exporter)
        self.assertEqual(exporter.calls, ['Y02'])
        self.assertFalse(ErpSnapshot.objects.exists())

    def test_invalid_second_file_makes_entire_collection_fail(self):
        exporter = SyntheticExporter()
        export = exporter.export
        def broken(warehouse, target):
            result = export(warehouse, target)
            if warehouse == 'Y09':target.write_bytes(b'partial export')
            return result
        exporter.export = broken
        with self.assertRaises(ValidationError):self.collect(exporter)
        self.assertFalse(ErpSnapshot.objects.exists())
        self.assertFalse(ReconciliationRow.objects.exists())
        self.unchanged_inventory()

    def test_reader_denied_before_any_screen_or_file_action(self):
        exporter = SyntheticExporter()
        with self.assertRaises(ValidationError):collector.collect(self.root, exporter, actor=self.reader)
        self.assertEqual(exporter.calls, [])
        self.assertFalse(self.root.exists())

    def test_transaction_failure_rolls_back_reference_and_allows_same_request_retry(self):
        with patch('inventory.reconciliation.audit', side_effect=RuntimeError('injected audit failure')):
            with self.assertRaises(RuntimeError):self.collect()
        self.assertFalse(ErpSnapshot.objects.exists())
        self.assertFalse(ReconciliationRow.objects.exists())
        folder, record = self.journal()
        self.assertEqual(record['phase'], 'registering')
        result = collector.register_collected(folder, actor=self.owner)
        self.assertEqual(result['snapshot_id'], record['request_id'])
        self.unchanged_inventory()

    def test_commit_then_lost_result_retry_returns_same_snapshot(self):
        write = collector.write_record
        def fail_final(path, data):
            if data.get('phase') == 'complete':raise OSError('injected final journal failure')
            return write(path, data)
        with patch.object(collector, 'write_record', side_effect=fail_final):
            with self.assertRaises(OSError):self.collect()
        self.assertEqual(ErpSnapshot.objects.count(), 1)
        folder, record = self.journal()
        self.assertEqual(record['phase'], 'registering')
        first = collector.register_collected(folder, actor=self.owner)
        second = collector.register_collected(folder, actor=self.owner)
        self.assertEqual(first['snapshot_id'], second['snapshot_id'])
        self.assertEqual(ErpSnapshot.objects.count(), 1)
        self.assertEqual(ReconciliationRow.objects.count(), 2)
        self.unchanged_inventory()

    def pending(self):
        with patch('inventory.reconciliation.audit', side_effect=RuntimeError('injected audit failure')):
            with self.assertRaises(RuntimeError):self.collect()
        return self.journal()

    def test_changed_saved_file_cannot_be_registered_after_failure(self):
        folder, record = self.pending()
        (folder / 'Y09.xls').write_bytes(export_bytes('TAMPERED', 999))
        with self.assertRaises(RuntimeError):collector.register_collected(folder, actor=self.owner)
        self.assertFalse(ErpSnapshot.objects.exists())

    def test_old_unregistered_data_expires_instead_of_becoming_latest(self):
        folder, record = self.pending()
        record['started_at'] = (timezone.now() - timedelta(minutes=16)).isoformat()
        collector.write_record(folder / 'run.json', record)
        with self.assertRaises(RuntimeError):collector.register_collected(folder, actor=self.owner)
        self.assertFalse(ErpSnapshot.objects.exists())
        self.assertEqual(self.journal()[1]['phase'], 'expired')

    def test_a_different_manager_cannot_claim_pending_collection(self):
        folder, record = self.pending()
        other = get_user_model().objects.create_user('another-manager', is_staff=True)
        with self.assertRaises(RuntimeError):collector.register_collected(folder, actor=other)
        self.assertFalse(ErpSnapshot.objects.exists())

    def test_process_lock_rejects_concurrent_collector(self):
        with collector.collection_lock(self.root):
            with self.assertRaises(RuntimeError):
                with collector.collection_lock(self.root):self.fail('second writer entered')

    def test_empty_or_oversized_file_is_not_success(self):
        path = Path(self.temp.name) / 'unfinished.xls'
        path.write_bytes(b'')
        with self.assertRaises(RuntimeError):collector.wait_for_export(path, timeout=.05, quiet=0)
        with path.open('wb') as stream:stream.truncate(collector.MAX_BYTES + 1)
        with self.assertRaises(RuntimeError):collector.wait_for_export(path, timeout=.05, quiet=0)

    def test_explorer_and_browser_titles_are_not_erp_windows(self):
        from .actas_windows import is_actas_root
        self.assertFalse(is_actas_root('Actas-controls-warehouse-3.json', 'CabinetWClass'))
        self.assertFalse(is_actas_root('ActasERP - Browser', 'Chrome_WidgetWin_1'))
        self.assertFalse(is_actas_root('ActasErp_New', 'CabinetWClass'))
        self.assertTrue(is_actas_root('ActasERP 사업장: 테스트', 'FNWND3105'))
        self.assertFalse(is_actas_root('현재고 현황', 'FNWND3105'))

    @skipUnless(os.name == 'nt', 'Native Windows file sharing check')
    def test_native_windows_open_writer_must_close_before_export_is_read(self):
        path = Path(self.temp.name) / 'writing.xls'
        with path.open('wb') as writer:
            writer.write(export_bytes())
            writer.flush()
            with self.assertRaises(RuntimeError):collector.wait_for_export(path, timeout=.15, quiet=0)
        self.assertEqual(collector.wait_for_export(path, timeout=2, quiet=0), export_bytes())

    @skipUnless(os.name == 'nt', 'Native Windows read-only control inspection')
    def test_native_readonly_inspector_returns_bounded_structure(self):
        from .actas_windows import inspect_windows
        result = inspect_windows()
        self.assertEqual(result['kind'], 'read-only-control-inspection')
        self.assertIsInstance(result['windows'], list)
        for window in result['windows']:
            self.assertLessEqual(len(window['controls']), 1000)

    @skipUnless(os.name == 'nt', 'Windows profile gate')
    def test_unverified_profile_is_rejected_without_ui_action(self):
        from .actas_windows import ActasExporter
        profile = Path(self.temp.name) / 'profile.json'
        profile.write_text(json.dumps({'schema': 1, 'verified_on_this_pc': False}))
        with self.assertRaises(RuntimeError):ActasExporter(profile)

    @skipUnless(os.name == 'nt', 'Native .NET 4 accessibility reader without UI changes')
    def test_native_standalone_detail_diagnostic_uses_existing_windows_libraries(self):
        output = Path(self.temp.name) / 'detail.json'
        script = Path(__file__).resolve().parent.parent / 'scripts/inspect_actas_controls.py'
        result = subprocess.run([sys.executable, str(script), '--delay', '0', '--output', str(output)],
                                capture_output=True, encoding='utf-8', errors='replace', timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(output.read_text(encoding='utf-8'))
        self.assertEqual(report['diagnostic_version'], 2)
        self.assertEqual(report['accessibility']['status'], 'complete')

    @skipUnless(os.name == 'nt', 'Own disposable Win32 window, menu and password field')
    def test_native_diagnostic_filters_titles_and_reads_only_own_menu_metadata(self):
        import ctypes
        from ctypes import wintypes as wt
        from .actas_windows import inspect_windows
        user, kernel = ctypes.WinDLL('user32'), ctypes.WinDLL('kernel32')
        proc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM)
        user.DefWindowProcW.argtypes = [wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM]
        user.DefWindowProcW.restype = ctypes.c_ssize_t
        @proc_type
        def proc(handle, message, wp, lp):return user.DefWindowProcW(handle, message, wp, lp)
        class WindowClass(ctypes.Structure):
            _fields_ = [('style', wt.UINT), ('proc', proc_type), ('cls_extra', ctypes.c_int),
                        ('wnd_extra', ctypes.c_int), ('instance', wt.HINSTANCE), ('icon', wt.HICON),
                        ('cursor', wt.HANDLE), ('brush', wt.HBRUSH), ('menu_name', wt.LPCWSTR), ('name', wt.LPCWSTR)]
        kernel.GetModuleHandleW.argtypes = [wt.LPCWSTR]
        kernel.GetModuleHandleW.restype = wt.HMODULE
        instance = kernel.GetModuleHandleW(None)
        cls = WindowClass()
        cls.proc, cls.instance, cls.name = proc, instance, 'FNWND999901'
        user.RegisterClassW.argtypes = [ctypes.POINTER(WindowClass)]
        user.UnregisterClassW.argtypes = [wt.LPCWSTR, wt.HINSTANCE]
        user.CreateWindowExW.argtypes = [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, ctypes.c_int,
                                         ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.HWND, wt.HMENU, wt.HINSTANCE, ctypes.c_void_p]
        user.CreateWindowExW.restype = wt.HWND
        user.CreateMenu.restype = wt.HMENU
        user.AppendMenuW.argtypes = [wt.HMENU, wt.UINT, ctypes.c_size_t, wt.LPCWSTR]
        user.SetMenu.argtypes = [wt.HWND, wt.HMENU]
        user.DestroyWindow.argtypes = [wt.HWND]
        user.DestroyMenu.argtypes = [wt.HMENU]
        self.assertTrue(user.RegisterClassW(ctypes.byref(cls)))
        window, menu = None, None
        try:
            window = user.CreateWindowExW(0, cls.name, 'ActasERP Diagnostic Fixture', 0x10CF0000,
                                           0, 0, 240, 140, None, None, instance, None)
            self.assertTrue(window)
            menu = user.CreateMenu()
            self.assertTrue(user.AppendMenuW(menu, 0, 991, '조회'))
            self.assertTrue(user.SetMenu(window, menu))
            field = user.CreateWindowExW(0, 'Edit', 'synthetic-password-do-not-emit', 0x50000020,
                                         5, 5, 150, 25, window, 992, instance, None)
            self.assertTrue(field)
            report = inspect_windows()
            root = next(w for w in report['windows'] if w['handle'] == window)
            self.assertTrue(any(m['command_id'] == 991 and m['label'] == '조회' for m in root['menus']))
            self.assertTrue(any(c['class_name'] == 'Edit' and c['control_id'] == 992 for c in root['controls']))
            self.assertNotIn('synthetic-password-do-not-emit', json.dumps(report))
            self.assertIn('focus', root)
        finally:
            if window:user.DestroyWindow(window)
            if menu:user.DestroyMenu(menu)
            user.UnregisterClassW(cls.name, instance)
