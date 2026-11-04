"""Pilot-only checks. Never touches a real ERP, database or installed task."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('pilot', Path(__file__).with_name('actas_export_check.py'))
p = importlib.util.module_from_spec(spec)
spec.loader.exec_module(p)


def data(code='SYNTHETIC', quantity=2):
    return ('ABC분류\t품목코드\t품목명\t현재고\t출하대기\r\n'
            f'A\t{code}\t테스트\t{quantity}\t1\r\n'
            f'합계\t\t\t{quantity}\t1\r\n').encode('cp949')


class Fixture:
    def __init__(self, fail='', same=False):self.events=[];self.fail=fail;self.same=same
    def preflight(self):self.events.append('preflight')
    def select_and_query(self, warehouse):
        self.events.append('query-'+warehouse);self.warehouse=warehouse
        if self.fail == warehouse:raise RuntimeError('injected query failure')
    def save_export(self, path):
        self.events.append('export-'+self.warehouse)
        path.write_bytes(data('SAME' if self.same else self.warehouse, -2 if self.warehouse=='Y02' and not self.same else 3))


class PilotTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
    def run_pilot(self, adapter, confirm=lambda warehouse: True):
        return p.run_check(adapter,self.root,confirm,progress=lambda text: None,reader=lambda path:path.read_bytes())
    def test_both_files_require_confirmation_and_never_mark_registered(self):
        adapter=Fixture()
        def confirm(warehouse):
            self.assertNotIn('export-'+warehouse,adapter.events)
            adapter.events.append('confirm-'+warehouse)
            return True
        run,report=self.run_pilot(adapter,confirm)
        self.assertEqual(adapter.events,['preflight','query-Y02','confirm-Y02','export-Y02','query-Y09','confirm-Y09','export-Y09'])
        self.assertFalse(report['registered_in_sunil']);self.assertFalse(report['automatic_query_completion_verified'])
        self.assertEqual(report['phase'],'exports_validated')
        self.assertEqual(report['files']['Y02']['quantity'],-2)
        self.assertEqual(set(x.name for x in run.iterdir()),{'Y02.xls','Y09.xls','result.json'})
    def test_operator_cancel_never_saves_or_queries_next_warehouse(self):
        adapter=Fixture()
        with self.assertRaises(RuntimeError):self.run_pilot(adapter,lambda warehouse:False)
        self.assertEqual(adapter.events,['preflight','query-Y02'])
        self.assertEqual(json.loads(next(self.root.glob('*/result.json')).read_text(encoding='utf-8'))['phase'],'failed')
    def test_second_query_failure_does_not_reuse_existing_file(self):
        (self.root/'Y09.xls').write_bytes(data('OLD',999))
        adapter=Fixture(fail='Y09')
        with self.assertRaises(RuntimeError):self.run_pilot(adapter)
        report=json.loads(next(self.root.glob('*/result.json')).read_text(encoding='utf-8'))
        self.assertEqual(set(report['files']),{'Y02'})
        self.assertNotIn('export-Y09',adapter.events)
    def test_identical_inventory_is_flagged_for_review(self):
        with self.assertRaises(RuntimeError):self.run_pilot(Fixture(same=True))
        report=json.loads(next(self.root.glob('*/result.json')).read_text(encoding='utf-8'))
        self.assertEqual(report['phase'],'failed')
    def test_duplicate_codes_bad_quantities_and_wrong_format_rejected(self):
        for raw in (b'partial', data()+data().split(b'\r\n',1)[1],data(quantity='1.5'),data(quantity='NaN')):
            with self.subTest(raw=raw),self.assertRaises(RuntimeError):p.inspect_export(raw)
    def test_negative_values_and_original_code_are_preserved(self):
        report=p.inspect_export(data('ABC-001*',-4))
        self.assertEqual(report['items'],1);self.assertEqual(report['quantity'],-4)
        self.assertEqual(report['negative_quantity_items'],1)
    def test_open_or_missing_export_is_not_success(self):
        path=self.root/'not-saved.xls'
        with self.assertRaises(RuntimeError):p.wait_file(path,timeout=.05,quiet=0)
        path.write_bytes(b'')
        with self.assertRaises(RuntimeError):p.wait_file(path,timeout=.05,quiet=0)
    @unittest.skipUnless(os.name=='nt','Windows byte sharing')
    def test_native_windows_writer_must_close(self):
        path=self.root/'open.xls'
        with path.open('wb') as file:
            file.write(data());file.flush()
            with self.assertRaises(RuntimeError):p.wait_file(path,timeout=.15,quiet=0)
        self.assertEqual(p.wait_file(path,timeout=2,quiet=0),data())
    @unittest.skipUnless(os.name=='nt','Windows mutex')
    def test_native_mutex_rejects_second_pilot_and_is_released(self):
        name='Local\\SUNIL-Actas-Pilot-Test-'+uuid.uuid4().hex
        with p.single_pilot(name):
            with self.assertRaises(RuntimeError):
                with p.single_pilot(name):self.fail('second pilot entered')
        with p.single_pilot(name):pass
    @unittest.skipUnless(os.name=='nt','Native API argument binding')
    def test_native_adapter_binds_apis_and_rejects_unknown_shortcut(self):
        adapter=p.WindowsActas()
        with self.assertRaises(RuntimeError):adapter.shortcut('save-inventory')

    @unittest.skipUnless(os.name=='nt','Own native Win32 controls, no actual Actas')
    def test_native_editor_text_and_ancestor_binding(self):
        import ctypes as c
        from ctypes import wintypes as wt
        adapter=p.WindowsActas();u=adapter.u;k=adapter.k
        proc_type=c.WINFUNCTYPE(c.c_ssize_t,wt.HWND,wt.UINT,wt.WPARAM,wt.LPARAM)
        u.DefWindowProcW.argtypes=[wt.HWND,wt.UINT,wt.WPARAM,wt.LPARAM];u.DefWindowProcW.restype=c.c_ssize_t
        @proc_type
        def proc(h,m,w,l):return u.DefWindowProcW(h,m,w,l)
        class WC(c.Structure):
            _fields_=[('style',wt.UINT),('proc',proc_type),('ce',c.c_int),('we',c.c_int),('instance',wt.HINSTANCE),
                      ('icon',wt.HICON),('cursor',wt.HANDLE),('brush',wt.HBRUSH),('menu',wt.LPCWSTR),('name',wt.LPCWSTR)]
        k.GetModuleHandleW.argtypes=[wt.LPCWSTR];k.GetModuleHandleW.restype=wt.HMODULE
        instance=k.GetModuleHandleW(None)
        u.RegisterClassW.argtypes=[c.POINTER(WC)]
        u.UnregisterClassW.argtypes=[wt.LPCWSTR,wt.HINSTANCE]
        u.CreateWindowExW.argtypes=[wt.DWORD,wt.LPCWSTR,wt.LPCWSTR,wt.DWORD,c.c_int,c.c_int,c.c_int,c.c_int,wt.HWND,wt.HMENU,wt.HINSTANCE,c.c_void_p]
        u.CreateWindowExW.restype=wt.HWND;u.DestroyWindow.argtypes=[wt.HWND]
        names=['FNWND3105','pbdw105'];registered=[];root=None
        try:
            for name in names:
                cls=WC();cls.proc=proc;cls.instance=instance;cls.name=name
                self.assertTrue(u.RegisterClassW(c.byref(cls)));registered.append(name)
            root=u.CreateWindowExW(0,names[0],'ActasERP Synthetic Pilot',0x10CF0000,0,0,350,180,None,None,instance,None)
            stock=u.CreateWindowExW(0,names[0],'현재고 현황',0x50000000,0,0,300,120,root,200,instance,None)
            conditions=u.CreateWindowExW(0,names[1],'',0x50000000,0,0,300,50,stock,1002,instance,None)
            editor=u.CreateWindowExW(0,'Edit','Y01',0x50000000,0,0,80,24,conditions,10,instance,None)
            grid=u.CreateWindowExW(0,names[1],'',0x50000000,0,50,300,50,stock,1004,instance,None)
            grid_editor=u.CreateWindowExW(0,'Edit','DO-NOT-CHANGE',0x50000000,0,0,80,24,grid,10,instance,None)
            self.assertTrue(root and stock and conditions and editor and grid_editor)
            with patch.object(adapter,'desktop',return_value=None):adapter.preflight()
            self.assertEqual(adapter.editor,editor)
            adapter.set_edit(adapter.editor,'Y02');self.assertEqual(adapter.read_edit(editor),'Y02')
            self.assertEqual(adapter.read_edit(grid_editor),'DO-NOT-CHANGE')
        finally:
            if root:u.DestroyWindow(root)
            for name in reversed(registered):u.UnregisterClassW(name,instance)


if __name__=='__main__':unittest.main(verbosity=2)
