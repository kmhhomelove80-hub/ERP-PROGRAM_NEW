"""Pilot-only checks. Never touches a real ERP, database or installed task."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid
from types import SimpleNamespace
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


class EditBufferFixture(p.WindowsActas):
    """Regression: visual text can differ from the committed DataWindow value."""
    def __init__(self):
        self.main,self.stock,self.conditions,self.editor=1,2,3,4
        self.buffer=self.committed='Y02';self.stage='warehouse';self.keys=[]
        self.u=SimpleNamespace(IsWindowVisible=lambda h:True, GetForegroundWindow=lambda:1,
                               GetWindowLongW=lambda h,i:0, IsChild=lambda parent,child:parent==3 and child in (4,5))
    def guard(self):pass
    def preflight(self):pass
    def child(self,parent,cls,identity,**kwargs):return {200:2,1002:3,10:4}[identity]
    def text(self,h,**kwargs):return '현재고 현황'
    def read_edit(self,h):return self.buffer
    def focus_state(self):return (4,(0,0,10,10),True) if self.stage=='warehouse' else (5,(20,0,30,10),True)
    def set_edit(self,h,value):raise AssertionError('WM_SETTEXT must not be used for a warehouse')
    def message(self,h,code,*args,**kwargs):
        if code!=0xB1:raise AssertionError('only text selection message is expected')
    def key_events(self,events,**kwargs):
        self.keys.append(events)
        if events[0][0]==8:self.buffer=''
        elif events[0][0]==0:
            self.buffer+=''.join(chr(scan) for vk,scan,flags in events if flags==6)
        elif events[0][0]==9:
            self.committed=self.buffer;self.stage='next'
        elif events[0][0]==0x10:self.stage='warehouse'
    def shortcut(self,purpose):raise AssertionError('query/export must not run in input-only probe')


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
        self.assertEqual(report['phase'],'export_files_checked')
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
    def test_same_catalog_with_live_quantity_changes_cannot_prove_switch(self):
        adapter=Fixture()
        def save(path):path.write_bytes(data('SAME-CODE',2 if adapter.warehouse=='Y02' else 3))
        adapter.save_export=save
        with self.assertRaisesRegex(RuntimeError,'품목 구성'):self.run_pilot(adapter)
        report=json.loads(next(self.root.glob('*/result.json')).read_text(encoding='utf-8'))
        self.assertEqual(report['phase'],'failed')
        self.assertFalse(report['warehouse_transition_verified'])
    def test_keyboard_and_tab_commits_model_instead_of_only_visual_text(self):
        adapter=EditBufferFixture()
        evidence=adapter.enter_warehouse('Y09',already_selected=True)
        self.assertEqual(adapter.committed,'Y09')
        self.assertEqual(adapter.buffer,'Y09')
        self.assertTrue(evidence['tab_transition_observed'])
        self.assertFalse(evidence['query_sent']);self.assertFalse(evidence['warehouse_name_verified'])
    def test_probe_never_queries_or_exports_and_keeps_operator_review_pending(self):
        adapter=EditBufferFixture()
        folder,report=p.probe_input(adapter,self.root,'Y09',delay=0,progress=lambda text:None)
        self.assertEqual(adapter.committed,'Y09')
        self.assertFalse(report['query_sent']);self.assertFalse(report['export_sent'])
        self.assertFalse(report['registered_in_sunil']);self.assertFalse(report['warehouse_name_verified'])
        self.assertEqual({f.name for f in folder.iterdir()},{'input-probe.json'})
    def test_probe_with_wrong_focus_stops_before_any_key_event(self):
        adapter=EditBufferFixture();adapter.stage='next'
        with self.assertRaises(RuntimeError):p.probe_input(adapter,self.root,'Y09',delay=0,progress=lambda text:None)
        self.assertEqual(adapter.keys,[]);self.assertEqual(adapter.committed,'Y02')
    def test_batch_entry_point_is_disabled_before_any_ui_access(self):
        with patch('sys.argv',['actas_export_check.py']),patch('builtins.print'),patch.object(p,'WindowsActas') as factory:
            self.assertEqual(p.main(),1)
            factory.assert_not_called()
    def test_staged_export_requires_input_acceptance_before_query(self):
        adapter=EditBufferFixture();queries=[]
        adapter.query_committed=lambda warehouse:queries.append(warehouse)
        wrapper=p.OperatorCheckedExport(adapter,lambda warehouse:True,lambda warehouse:False)
        with self.assertRaisesRegex(RuntimeError,'조회하지 않았습니다'):wrapper.select_and_query('Y09')
        self.assertEqual(adapter.committed,'Y09');self.assertEqual(queries,[])
    def test_staged_export_uses_verified_keyboard_path_and_records_operator(self):
        adapter=EditBufferFixture();events=[]
        adapter.query_committed=lambda warehouse:events.append(('query',warehouse,adapter.committed))
        def accept(warehouse):
            self.assertEqual(events,[]);self.assertEqual(adapter.committed,warehouse);return True
        wrapper=p.OperatorCheckedExport(adapter,lambda warehouse:True,accept)
        evidence=wrapper.select_and_query('Y09')
        self.assertEqual(events,[('query','Y09','Y09')])
        self.assertEqual(evidence['warehouse_name_confirmed_by'],'operator')
        self.assertTrue(evidence['query_sent']);self.assertFalse(evidence['warehouse_name_verified'])
    def test_staged_cancel_prepare_does_not_change_code(self):
        adapter=EditBufferFixture()
        wrapper=p.OperatorCheckedExport(adapter,lambda warehouse:False,lambda warehouse:True)
        with self.assertRaises(RuntimeError):wrapper.select_and_query('Y09')
        self.assertEqual(adapter.keys,[])
    def test_duplicate_codes_bad_quantities_and_wrong_format_rejected(self):
        for raw in (b'partial', data()+data().split(b'\r\n',1)[1],data(quantity='NaN'),data(quantity='1e-999999')):
            with self.subTest(raw=raw),self.assertRaises(RuntimeError):p.inspect_export(raw)
    def test_negative_values_and_original_code_are_preserved(self):
        report=p.inspect_export(data('ABC-001*',-4))
        self.assertEqual(report['items'],1);self.assertEqual(report['quantity'],-4)
        self.assertEqual(report['negative_quantity_items'],1)
    def test_fractional_and_negative_fractional_quantities_are_valid(self):
        report=p.inspect_export(data(quantity='-565.9'))
        self.assertEqual(report['quantity'],'-565.9')
        self.assertEqual(report['fractional_quantity_items'],1)
        self.assertEqual(report['negative_quantity_items'],1)
        self.assertFalse(report['integer_quantity_compatible'])
        self.assertEqual(report['samples']['현재고']['fractional'],[{'row':2,'value':'-565.9'}])
    def test_blank_is_not_zero_and_complete_total_is_not_invented(self):
        raw=data(quantity='').replace(b'\t1\r\n',b'\t\r\n',1)
        report=p.inspect_export(raw)
        self.assertIsNone(report['quantity']);self.assertIsNone(report['shipping_pending'])
        self.assertEqual(report['blank_quantity_items'],1)
        self.assertEqual(report['blank_shipping_pending_items'],1)
        self.assertFalse(report['integer_quantity_compatible'])
        self.assertNotEqual(report['inventory_digest'],p.inspect_export(data(quantity=0))['inventory_digest'])
    def test_decimal_sum_is_exact_not_binary_float(self):
        raw=('품목코드\t품목명\t현재고\t출하대기\r\nA\t테스트\t0.1\t0.2\r\nB\t테스트\t0.2\t0.1\r\n').encode('cp949')
        report=p.inspect_export(raw)
        self.assertEqual(report['quantity'],'0.3');self.assertEqual(report['shipping_pending'],'0.3')
    def test_identical_inventory_detected_despite_decimal_display_scale(self):
        self.assertEqual(p.inspect_export(data(quantity='1.0'))['inventory_digest'],
                         p.inspect_export(data(quantity='1'))['inventory_digest'])
        self.assertEqual(p.inspect_export(data(quantity='-0.0'))['inventory_digest'],
                         p.inspect_export(data(quantity='0'))['inventory_digest'])
    def test_bad_quantity_error_identifies_row_and_field(self):
        with self.assertRaisesRegex(RuntimeError,'2행 현재고'):p.inspect_export(data(quantity='not-a-number'))
    def test_fractional_source_does_not_stop_second_export(self):
        adapter=Fixture()
        original=adapter.save_export
        def fractional(path):
            original(path)
            if adapter.warehouse=='Y02':path.write_bytes(data('Y02','1.5'))
        adapter.save_export=fractional
        run,report=self.run_pilot(adapter)
        self.assertEqual(report['phase'],'export_files_checked')
        self.assertEqual(set(report['files']),{'Y02','Y09'})
        self.assertFalse(report['registered_in_sunil'])
    def test_confirmation_ignores_case_and_reprompts_wrong_warehouse(self):
        with patch('builtins.input',side_effect=['y09','y02']),patch('builtins.print'):
            self.assertTrue(p.confirm_token('Y02','test: '))
        with patch('builtins.input',return_value='start'):
            self.assertTrue(p.confirm_token('START','test: '))
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
            focus,rect,visible=adapter.focus_state()
            self.assertEqual(len(rect),4);self.assertTrue(visible)
            adapter.set_edit(adapter.editor,'Y02');self.assertEqual(adapter.read_edit(editor),'Y02')
            self.assertEqual(adapter.read_edit(grid_editor),'DO-NOT-CHANGE')
        finally:
            if root:u.DestroyWindow(root)
            for name in reversed(registered):u.UnregisterClassW(name,instance)


if __name__=='__main__':unittest.main(verbosity=2)
