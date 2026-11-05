import json
import struct
from pathlib import Path
import tempfile
import unittest

import actas_auto_collect as auto


def ocr(name='부산창고', split=False):
    words=[dict(text='창고',x=32,y=7,width=22,height=12),dict(text='Y09',x=100,y=7,width=25,height=12)]
    if split:words += [dict(text='부산',x=200,y=7,width=20,height=12),dict(text='창고',x=222,y=7,width=20,height=12)]
    else:words.append(dict(text=name,x=180,y=7,width=60,height=12))
    return dict(status='ok',width=1270,height=53,lines=[dict(words=words)])


def ready(busy=False):
    return dict(responding=not busy,wait_cursor=busy,cursor_over_actas=True,
                main_enabled=True,conditions_enabled=True,grid_enabled=True)


class Clock:
    def __init__(self):self.now=0
    def __call__(self):return self.now
    def sleep(self,seconds):self.now += seconds


class AutoTests(unittest.TestCase):
    def test_filter_digest_ignores_warehouse_but_detects_search_category_and_checkbox_changes(self):
        width,height=1271,53
        header=struct.pack('<2sIHHI',b'BM',54+width*height*4,0,0,54)+struct.pack('<IiiHHIIiiII',40,width,-height,1,32,0,width*height*4,0,0,0,0)
        original=header+b'\xff'*(width*height*4)
        baseline=auto.filter_signature(original)
        def changed(x,y):
            value=bytearray(original);index=54+(y*width+x)*4;value[index:index+3]=b'\x00'*3;return value
        self.assertEqual(auto.filter_signature(changed(120,12)),baseline)
        self.assertEqual(auto.filter_signature(changed(220,12)),baseline)
        for x,y in ((500,12),(120,35),(280,35)):
            self.assertNotEqual(auto.filter_signature(changed(x,y)),baseline)
    def test_exact_name_anchors_allow_only_the_code_gap(self):
        for name,code in [('부산창고','Y09'),('영주제품창고','Y02'),('영주자재창고','Y01')]:
            anchor=auto.warehouse_anchor(ocr(name))
            self.assertEqual(anchor['code'],code)
            self.assertTrue(52 < anchor['click_x'] < 180)
            self.assertEqual(anchor['click_y'],13)
        self.assertEqual(auto.warehouse_anchor(ocr(split=True))['code'],'Y09')
    def test_unreadable_ambiguous_or_invalid_ocr_never_produces_click_coordinates(self):
        bad=[dict(status='language_unavailable'),ocr('부산창그')]
        bad.append(ocr());bad[-1]['lines'][0]['words'] += [dict(text='영주제품창고',x=260,y=7,width=90,height=12)]
        bad.append(ocr());bad[-1]['lines'][0]['words'][0]['x']=float('nan')
        for value in bad:
            with self.subTest(value=value),self.assertRaises(RuntimeError):auto.warehouse_anchor(value)
    def wait(self, samples, timeout=8):
        clock=Clock();index=[0]
        def sample():
            value=samples[min(index[0],len(samples)-1)];index[0]+=1;return value
        return auto.wait_ready(sample,timeout=timeout,clock=clock,pause=clock.sleep)
    def test_each_warehouse_waits_for_its_own_busy_period_then_ready(self):
        fast=self.wait([ready()]*2+[ready(True)]*8+[ready()])
        slow=self.wait([ready()]*2+[ready(True)]*70+[ready()])
        self.assertGreater(slow['elapsed_seconds'],fast['elapsed_seconds']+1)
        self.assertTrue(fast['busy_observed']);self.assertTrue(slow['busy_observed'])
    def test_elapsed_time_or_only_one_busy_signal_never_implies_completion(self):
        only_cursor={**ready(), 'wait_cursor':True}
        only_timeout={**ready(), 'responding':False}
        for states in ([ready()],[only_cursor]*8+[ready()],[only_timeout]*8+[ready()],[ready(True)]):
            with self.assertRaises(RuntimeError):self.wait(states,timeout=1)
    def test_ready_must_be_continuous_after_busy(self):
        states=[ready(True)]*3+[ready()]*10+[ready(True)]*3+[ready()]
        result=self.wait(states)
        self.assertGreater(result['elapsed_seconds'],1)
    def test_user_pointer_interference_stops_without_timeout(self):
        with self.assertRaisesRegex(RuntimeError,'마우스'):self.wait([{**ready(),'cursor_over_actas':False}])
    def test_report_never_claims_operator_confirmation_or_registration(self):
        class Adapter:
            pilot_version=6
            report_kind=auto.AutomaticAdapter.report_kind
            checked_phase=auto.AutomaticAdapter.checked_phase
            confirmation_source=auto.AutomaticAdapter.confirmation_source
            events=[]
            def preflight(self):pass
            def select_and_query(self,warehouse):
                self.warehouse=warehouse
                return dict(requested=warehouse,query_observation=dict(busy_observed=True))
            def save_export(self,path):
                path.write_bytes(('품목코드\t품목명\t현재고\t출하대기\n'+self.warehouse+'-SYNTHETIC\t시험\t-1.5\t0\n').encode('cp949'))
        with tempfile.TemporaryDirectory() as tmp:
            folder,report=auto.pilot.run_check(Adapter(),Path(tmp),lambda warehouse:True,progress=lambda text:None,reader=lambda path:path.read_bytes())
            self.assertEqual(report['phase'],'automatic_exports_checked_operator_review_required')
            self.assertFalse(report['registered_in_sunil']);self.assertFalse(report['automatic_query_completion_verified'])
            self.assertEqual(report['pilot_version'],6)
            self.assertEqual(report['files']['Y02']['query_confirmed_by'],'observed_signals_NOT_operator')
            self.assertEqual(report['files']['Y02']['quantity'],'-1.5')
            self.assertTrue(report['input_evidence']['Y09']['export_sent'])


if __name__=='__main__':unittest.main(verbosity=2)
