"""Candidate automatic collection, NOT registration or enabled scheduling.

Machine observations are never labelled as operator confirmation. OCR must
match exactly. Both busy signals must be observed after EACH query, followed
by sustained readiness. Timeout does not mean success. No DB/secret access.
"""
import argparse
import math
from pathlib import Path
import sys
import tempfile
import time

import actas_export_check as pilot
from actas_query_probe import NativeSampler, capture_client, recognize_conditions

NAMES = {'Y01': '영주자재창고', 'Y02': '영주제품창고', 'Y09': '부산창고'}


def compact(text):return ''.join(str(text).split())


def warehouse_anchor(ocr):
    """Find header and exact warehouse name, never guess unreadable labels."""
    if ocr.get('status') != 'ok':raise RuntimeError('한국어 창고명 판독을 사용할 수 없습니다. 추가 설치나 자동 등록은 하지 않습니다.')
    width, height = ocr.get('width', 0), ocr.get('height', 0)
    words = []
    for line in ocr.get('lines', []):
        for word in line.get('words', []):
            box = tuple(word.get(k) for k in ('x', 'y', 'width', 'height'))
            if any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in box):
                raise RuntimeError('판독 좌표가 유효하지 않습니다.')
            x, y, w, h = box
            if not (w > 0 and h > 0 and 0 <= x < x+w <= min(width, 400)+1 and 0 <= y < y+h <= height+1):
                raise RuntimeError('판독 좌표가 조건 영역을 벗어났습니다.')
            words.append(dict(text=compact(word['text']), x=x, y=y, right=x+w, center_y=y+h/2))
    headers = [w for w in words if w['text'] == '창고' and w['center_y'] < height/2]
    for first in words:
        for second in words:
            if (first['text']+second['text']=='창고' and first['center_y']<height/2
                    and abs(first['center_y']-second['center_y'])<=3 and 0<=second['x']-first['right']<=10):
                headers.append(dict(first,text='창고',right=second['right']))
    if not headers:raise RuntimeError('창고 제목을 판독하지 못했습니다. 클릭하지 않습니다.')
    header = min(headers, key=lambda w:w['x'])
    row = sorted([w for w in words if w['x'] > header['right'] and abs(w['center_y']-header['center_y']) <= 8], key=lambda w:w['x'])
    matches = []
    for i in range(len(row)):
        text = ''
        for j in range(i, min(i+8, len(row))):
            if j > i and row[j]['x']-row[j-1]['right'] > 40:break
            text += row[j]['text']
            for code, name in NAMES.items():
                if text == name:matches.append((code, name, row[i]['x'], row[j]['right']))
    if len(matches) != 1:raise RuntimeError('현재 창고명을 하나로 확인하지 못했습니다. 클릭하지 않습니다.')
    code, name, left, right = matches[0]
    x = (header['right'] + left) / 2
    y = header['center_y']
    if not (header['right'] < x < left and 0 < y < height/2):raise RuntimeError('창고 코드 입력 위치를 확인하지 못했습니다.')
    return dict(code=code, name=name, click_x=round(x), click_y=round(y))


def wait_ready(sample, *, timeout=180, busy_required=True, quiet=.6, clock=time.monotonic, pause=time.sleep, event=lambda row:None):
    """Require overlapping wait cursor/nonresponse, then uninterrupted readiness."""
    start = clock();since = None;overlaps = 0;seen = False;rows = 0
    while clock()-start < timeout:
        row = sample();rows += 1
        event(row)
        if row.get('cursor_over_actas') is False:
            raise RuntimeError('마우스가 Actas 밖으로 이동해 조회 검증을 중단합니다.')
        if row.get('wait_cursor') is True and row.get('responding') is False:
            overlaps += 1
            if overlaps >= 2:seen = True
        ready = (row.get('responding') is True and row.get('wait_cursor') is False and row.get('cursor_over_actas') is True
                 and all(row.get(k) is True for k in ('main_enabled', 'conditions_enabled', 'grid_enabled')))
        if ready and (seen or not busy_required):
            if since is None:since = clock()
            if clock()-since >= quiet:
                return dict(method='overlapping_wait_and_nonresponse_then_stable_ready',
                            overlapping_busy_samples=overlaps, samples=rows, elapsed_seconds=round(clock()-start, 3),
                            quiet_seconds=quiet, busy_observed=seen)
        else:since = None
        pause(.03)
    raise RuntimeError('조회 시작과 완료 신호를 제한 시간 안에 모두 확인하지 못했습니다. 시간 경과만으로 저장하지 않습니다.')


class AutomaticAdapter:
    pilot_version = 6
    report_kind = 'Actas-automatic-collection-pilot-NOT-registration'
    confirmation_source = 'observed_signals_NOT_operator'
    checked_phase = 'automatic_exports_checked_operator_review_required'

    def __init__(self, adapter, *, query_timeout=180):
        self.a = adapter;self.timeout = query_timeout;self.events = []
    def log(self, stage, **values):self.events.append(dict(stage=stage, at=pilot.now(), **values))
    def preflight(self):self.a.preflight()
    def read_name(self):
        self.a.guard()
        if self.a.u.GetForegroundWindow() != self.a.main:raise RuntimeError('Actas 활성 화면이 바뀌었습니다.')
        with tempfile.TemporaryDirectory(prefix='sunil-condition-') as tmp:
            path = Path(tmp) / 'condition.bmp';path.write_bytes(capture_client(self.a, self.a.conditions))
            result = recognize_conditions(path)
            self.log('condition_ocr', result=result)
            return warehouse_anchor(result)
    def click_code(self, anchor):
        a = self.a;c, wt, u = a.c, a.wt, a.u
        a.guard()
        if u.GetForegroundWindow() != a.main:raise RuntimeError('클릭 전에 Actas가 비활성화됐습니다.')
        u.ClientToScreen.argtypes = [wt.HWND, c.POINTER(wt.POINT)];u.ClientToScreen.restype = wt.BOOL
        u.SetCursorPos.argtypes = [c.c_int, c.c_int];u.SetCursorPos.restype = wt.BOOL
        point = wt.POINT(anchor['click_x'], anchor['click_y'])
        if not u.ClientToScreen(a.conditions, c.byref(point)):raise RuntimeError('창고 코드 위치를 화면에 대응하지 못했습니다.')
        deadline = time.monotonic()+3
        while any(u.GetAsyncKeyState(k)&0x8000 for k in (0x10,0x11,0x12,0x5B,0x5C,0x0D,1,2)):
            if time.monotonic()>deadline:raise RuntimeError('키보드·마우스 버튼에서 손을 떼세요.')
            time.sleep(.05)
        if u.GetForegroundWindow() != a.main or not u.SetCursorPos(point.x, point.y):raise RuntimeError('창고 입력 위치로 이동하지 못했습니다.')
        hit = u.WindowFromPoint(point)
        if not (hit == a.conditions or u.IsChild(a.conditions, hit)):
            raise RuntimeError('다른 창이 입력 위치를 가리고 있어 클릭하지 않습니다.')
        class Mouse(c.Structure):
            _fields_=[('x',wt.LONG),('y',wt.LONG),('data',wt.DWORD),('flags',wt.DWORD),('time',wt.DWORD),('extra',c.c_size_t)]
        class Union(c.Union):_fields_=[('mouse',Mouse)]
        class Input(c.Structure):_fields_=[('type',wt.DWORD),('data',Union)]
        events=(Input*2)();events[0].data.mouse.flags=2;events[1].data.mouse.flags=4
        u.SendInput.argtypes=[wt.UINT,c.POINTER(Input),c.c_int];u.SendInput.restype=wt.UINT
        sent=u.SendInput(2,events,c.sizeof(Input))
        if sent!=2:
            if sent==1:u.SendInput(1,c.cast(c.byref(events,c.sizeof(Input)),c.POINTER(Input)),c.sizeof(Input))
            raise RuntimeError('창고 칸 클릭을 전송하지 못했습니다. 재시도하지 않습니다.')
        def focused_editor():
            try:a.editor=a.child(a.conditions,'Edit',10,direct=True)
            except RuntimeError:return False
            return a.focus_state()[0]==a.editor and u.IsWindowVisible(a.editor)
        a.wait_input(focused_editor, '창고 코드 칸의 포커스를 확인하지 못했습니다.')
        if a.read_edit(a.editor).strip()!=anchor['code']:
            raise RuntimeError('클릭한 입력란의 실제 코드와 창고명 판독 결과가 다릅니다. 입력하지 않습니다.')
    def select_and_query(self, warehouse):
        self.a.preflight();self.a.activate(self.a.stock,self.a.main)
        # Bind all APIs before pointer use. NativeSampler captures no external UI.
        sampler=NativeSampler(self.a)
        anchor=self.read_name();self.log('before_input', warehouse=warehouse, observed=anchor)
        self.click_code(anchor)
        evidence=self.a.enter_warehouse(warehouse, already_selected=True)
        deadline=time.monotonic()+12
        while True:
            observed=self.read_name()
            if observed['code']==warehouse:break
            if time.monotonic()>deadline:raise RuntimeError('입력 후 창고명이 요청 창고로 바뀌지 않았습니다.')
            time.sleep(.2)
        self.log('warehouse_name_verified', warehouse=warehouse, name=observed['name'])
        wait_ready(sampler.sample, timeout=12, busy_required=False)
        # No foreground changes or blind sleep between readiness and query.
        self.a.query_committed(warehouse)
        previous=[None]
        def trace(row):
            signature=tuple(row.get(k) for k in ('wait_cursor','responding','main_enabled','conditions_enabled','grid_enabled','cursor_over_actas'))
            if signature!=previous[0]:
                self.log('query_state',warehouse=warehouse,sample=row)
                previous[0]=signature
        signal=wait_ready(sampler.sample, timeout=self.timeout,event=trace)
        if self.a.read_edit(self.a.editor).strip()!=warehouse or self.read_name()['code']!=warehouse:
            raise RuntimeError('조회 후 창고 코드·명칭이 변경됐습니다. 저장하지 않습니다.')
        self.log('query_signals_observed', warehouse=warehouse, evidence=signal)
        evidence.update(query_sent=True, warehouse_name_verified_by='local_ocr_exact', query_observation=signal)
        return evidence
    def save_export(self, path):self.a.save_export(path)


def main():
    for stream in (sys.stdout,sys.stderr):
        if hasattr(stream,'reconfigure'):stream.reconfigure(encoding='utf-8',errors='replace')
    parser=argparse.ArgumentParser(description='확인 입력 없는 Actas 수집 검증 · ERP 등록/예약 실행 없음')
    parser.add_argument('--output-root',type=Path,default=Path(__file__).resolve().parent/'runs')
    args=parser.parse_args()
    print('Actas 현재고 현황을 열고 검색·분류를 전체로 두세요. 저장창을 닫으세요.')
    print('Y02·Y09 창고 입력·조회·파일 저장을 자동으로 검증합니다. ERP 등록·예약 실행은 하지 않습니다.')
    print('실행 중 화면·키보드·마우스를 조작하지 마세요. 창고별로 최대 180초까지 신호를 기다립니다.')
    if not pilot.confirm_token('START','시작은 start, 중단은 Enter: '):return 1
    try:
        time.sleep(3)
        with pilot.single_pilot():
            adapter=AutomaticAdapter(pilot.WindowsActas())
            folder,report=pilot.run_check(adapter,args.output_root,lambda warehouse:True,progress=lambda text:print(text,flush=True))
        print('자동 수집 파일 검사가 끝났습니다. 서버 현장 결과 확인은 아직 필요합니다.')
        print('결과 기록:',folder/'result.json')
        print('result.json과 Y02.xls·Y09.xls를 전달하세요. 기존 등록 도구에는 넣지 마세요.')
        return 0
    except Exception as error:
        print('자동 수집 검증 중단:',str(error))
        print('위에 표시된 result.json을 전달하세요. 반복 실행하지 않습니다.')
        return 1


if __name__=='__main__':raise SystemExit(main())
