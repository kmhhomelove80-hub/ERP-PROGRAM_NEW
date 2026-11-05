import importlib.util
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import actas_query_probe as probe


class Adapter:
    editor = 1
    def __init__(self, code='Y09', fail=False):self.code,self.fail,self.queries=code,fail,[]
    def preflight(self):pass
    def read_edit(self, h):return self.code
    def query_committed(self, warehouse):
        if self.fail:raise RuntimeError('synthetic focus failure')
        self.queries.append(warehouse)
    def save_export(self, path):raise AssertionError('probe must never export')


class Sampler:
    def __init__(self, adapter):self.adapter=adapter
    def sample(self):return dict(responding=True, wait_cursor=False, main_enabled=True, grid_digest='unchanged')
    def save_conditions(self, folder):
        (folder/'conditions.bmp').write_bytes(b'synthetic-test-only')
        return 'conditions.bmp'


class ProbeTests(unittest.TestCase):
    def run_probe(self, adapter):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name)
        return probe.run_probe(adapter,self.root,delay=0,duration=0,sampler_factory=Sampler,ocr_reader=lambda p:dict(status='synthetic'),progress=lambda text:None)
    def test_probe_queries_current_code_once_and_never_claims_completion_or_import(self):
        adapter=Adapter();folder,report=self.run_probe(adapter)
        self.assertEqual(adapter.queries,['Y09'])
        self.assertTrue(report['query_sent']);self.assertFalse(report['query_completion_verified'])
        self.assertFalse(report['export_sent']);self.assertFalse(report['registered_in_sunil'])
        self.assertEqual(set(p.name for p in folder.iterdir()),{'query-probe.json','conditions.bmp'})
    def test_other_warehouse_stops_before_query(self):
        adapter=Adapter('Y01')
        with self.assertRaises(RuntimeError):self.run_probe(adapter)
        self.assertEqual(adapter.queries,[])
    def test_lost_focus_keeps_dispatch_uncertain_and_preserves_failure_report(self):
        adapter=Adapter(fail=True)
        with self.assertRaises(RuntimeError):self.run_probe(adapter)
        import json
        report=json.loads(next(self.root.glob('*/query-probe.json')).read_text())
        self.assertEqual(report['phase'],'failed');self.assertIsNone(report['query_sent'])
        self.assertFalse(report['registered_in_sunil']);self.assertFalse(report['query_completion_verified'])
    def test_cursor_busy_and_new_pixels_are_only_diagnostics(self):
        samples=[dict(stage='before_query',grid_digest='old'),
                 dict(stage='after_query',grid_digest='old',wait_cursor=True,responding=False,main_enabled=False),
                 dict(stage='after_query',grid_digest='new',wait_cursor=False,responding=True,main_enabled=True)]
        result=probe.summarize(samples)
        for key in ('wait_to_normal_observed','unresponsive_to_responsive_observed','disabled_to_enabled_observed','grid_changed'):
            self.assertTrue(result[key])
        self.assertFalse(result['query_completion_verified'])
    @unittest.skipUnless(os.name=='nt','Own disposable Win32 window GDI capture')
    def test_native_capture_of_our_own_control(self):
        adapter=probe.pilot.WindowsActas();u=adapter.u;c=adapter.c;wt=adapter.wt
        u.CreateWindowExW.argtypes=[wt.DWORD,wt.LPCWSTR,wt.LPCWSTR,wt.DWORD,c.c_int,c.c_int,c.c_int,c.c_int,wt.HWND,wt.HMENU,wt.HINSTANCE,c.c_void_p]
        u.CreateWindowExW.restype=wt.HWND;u.DestroyWindow.argtypes=[wt.HWND]
        handle=u.CreateWindowExW(0,'STATIC','Synthetic probe fixture',0x90000000,10,10,240,120,None,None,None,None)
        self.assertTrue(handle)
        try:
            adapter.process=adapter.pid(handle)[0]
            data=probe.capture_client(adapter,handle)
            self.assertEqual(data[:2],b'BM')
            width,height=struct.unpack_from('<ii',data,18)
            self.assertEqual((width,height),(240,-120));self.assertEqual(len(data),54+240*120*4)
            self.assertTrue(all(v==255 for v in data[57::4]))
            with tempfile.TemporaryDirectory() as tmp:
                path=Path(tmp)/'synthetic.bmp';path.write_bytes(data)
                result=probe.recognize_conditions(path, language='en-US')
                self.assertIn(result['status'],('ok','language_unavailable'),result)
                self.assertEqual((result['width'],result['height'],result['scale']),(240,120,3))
                self.assertEqual((result['processed_width'],result['processed_height']),(720,360))
        finally:u.DestroyWindow(handle)


if __name__=='__main__':unittest.main(verbosity=2)
