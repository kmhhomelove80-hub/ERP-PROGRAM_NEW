"""Measure query signals; NEVER declares query complete, exports, or imports.

No installation, credentials or database access. Captures only the condition
panel locally; grid pixels are hashed in memory and never saved to disk.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time
import uuid

import actas_export_check as pilot


def recognize_conditions(path, language='ko-KR'):
    if os.name != 'nt':return dict(status='windows_required')
    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    helper = Path(__file__).with_name('actas_conditions_ocr.ps1')
    if not helper.is_file():return dict(status='helper_missing')
    env = {**os.environ, 'PSModulePath': str(powershell.parent / 'Modules')}
    result = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                             '-File', str(helper), '-Image', str(Path(path).resolve()), '-Language', language],
                            env=env, capture_output=True, encoding='utf-8', timeout=45)
    try:
        value = json.loads(result.stdout)
        if value.get('status') in ('ok', 'language_unavailable', 'error'):return value
    except (ValueError, AttributeError):pass
    return dict(status='error', failure_type='OcrHelperOutput')


def capture_client(adapter, handle):
    """Capture an already verified process's visible client using native GDI."""
    c, wt, u = adapter.c, adapter.wt, adapter.u
    if adapter.pid(handle)[0] != adapter.process or not u.IsWindowVisible(handle):
        raise RuntimeError('확인한 Actas 표시 영역만 측정합니다.')
    g = c.WinDLL('gdi32', use_last_error=True)
    definitions = {
        'CreateCompatibleDC': ([wt.HDC], wt.HDC),
        'CreateCompatibleBitmap': ([wt.HDC, c.c_int, c.c_int], wt.HANDLE),
        'SelectObject': ([wt.HDC, wt.HANDLE], wt.HANDLE),
        'DeleteObject': ([wt.HANDLE], wt.BOOL), 'DeleteDC': ([wt.HDC], wt.BOOL),
        'BitBlt': ([wt.HDC, c.c_int, c.c_int, c.c_int, c.c_int, wt.HDC, c.c_int, c.c_int, wt.DWORD], wt.BOOL),
        'GetDIBits': ([wt.HDC, wt.HANDLE, wt.UINT, wt.UINT, c.c_void_p, c.c_void_p, wt.UINT], c.c_int),
    }
    for name, (args, result) in definitions.items():
        api = getattr(g, name);api.argtypes, api.restype = args, result
    u.GetClientRect.argtypes = [wt.HWND, c.POINTER(wt.RECT)];u.GetClientRect.restype = wt.BOOL
    u.GetDC.argtypes = [wt.HWND];u.GetDC.restype = wt.HDC
    u.ReleaseDC.argtypes = [wt.HWND, wt.HDC];u.ReleaseDC.restype = c.c_int
    rect = wt.RECT()
    if not u.GetClientRect(handle, c.byref(rect)):raise RuntimeError('표시 영역 크기를 확인하지 못했습니다.')
    width, height = rect.right, rect.bottom
    if not (width > 0 and height > 0 and width * height <= 8_000_000):
        raise RuntimeError('측정할 표시 영역 크기가 유효하지 않습니다.')
    dc = u.GetDC(handle);memory = bitmap = old = None
    try:
        if not dc:raise RuntimeError('표시 영역을 읽지 못했습니다.')
        memory = g.CreateCompatibleDC(dc)
        bitmap = g.CreateCompatibleBitmap(dc, width, height)
        if not memory or not bitmap:raise RuntimeError('화면 측정 버퍼를 만들지 못했습니다.')
        old = g.SelectObject(memory, bitmap)
        if not old or old == c.c_void_p(-1).value:raise RuntimeError('화면 측정 버퍼를 선택하지 못했습니다.')
        if not g.BitBlt(memory, 0, 0, width, height, dc, 0, 0, 0x00CC0020):
            raise RuntimeError('표시 영역을 측정하지 못했습니다.')
        g.SelectObject(memory, old);old = None
        header = struct.pack('<IiiHHIIiiII', 40, width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0)
        info = c.create_string_buffer(header)
        pixels = c.create_string_buffer(width * height * 4)
        if g.GetDIBits(memory, bitmap, 0, height, pixels, info, 0) != height:
            raise RuntimeError('표시 영역 픽셀을 읽지 못했습니다.')
        data = bytearray(pixels.raw)
        data[3::4] = b'\xff' * (width * height)  # Ignore undefined screen alpha.
        return struct.pack('<2sIHHI', b'BM', 54 + len(data), 0, 0, 54) + header + data
    finally:
        if old and memory:g.SelectObject(memory, old)
        if bitmap:g.DeleteObject(bitmap)
        if memory:g.DeleteDC(memory)
        if dc:u.ReleaseDC(handle, dc)


class NativeSampler:
    def __init__(self, adapter):
        self.a = adapter
        self.grid = adapter.child(adapter.stock, 'pbdw105', 1004, direct=True)
        c, wt, u = adapter.c, adapter.wt, adapter.u
        class Cursor(c.Structure):
            _fields_ = [('size', wt.DWORD), ('flags', wt.DWORD), ('cursor', wt.HANDLE), ('point', wt.POINT)]
        self.Cursor = Cursor
        u.GetCursorInfo.argtypes = [c.POINTER(Cursor)];u.GetCursorInfo.restype = wt.BOOL
        u.LoadCursorW.argtypes = [wt.HINSTANCE, wt.LPCWSTR];u.LoadCursorW.restype = wt.HANDLE
        u.WindowFromPoint.argtypes = [wt.POINT];u.WindowFromPoint.restype = wt.HWND
        u.GetAncestor.argtypes = [wt.HWND, wt.UINT];u.GetAncestor.restype = wt.HWND
        self.wait = {u.LoadCursorW(None, c.cast(c.c_void_p(n), wt.LPCWSTR)) for n in (32514, 32650)} - {None, 0}
        self.last_grid = None
        self.last_grid_at = float('-inf')

    def sample(self):
        a = self.a;a.guard()
        if a.u.GetForegroundWindow() != a.main:
            raise RuntimeError('Actas에서 다른 화면으로 이동해 측정을 중단했습니다.')
        cursor = self.Cursor();cursor.size = a.c.sizeof(cursor)
        found = a.u.GetCursorInfo(a.c.byref(cursor))
        over = bool(found and a.u.GetAncestor(a.u.WindowFromPoint(cursor.point), 2) == a.main)
        row = dict(main_enabled=bool(a.u.IsWindowEnabled(a.main)),
                   conditions_enabled=bool(a.u.IsWindowEnabled(a.conditions)),
                   grid_enabled=bool(a.u.IsWindowEnabled(self.grid)), cursor_over_actas=over,
                   wait_cursor=bool(found and over and cursor.flags & 1 and cursor.cursor in self.wait))
        start = time.monotonic()
        try:
            a.message(a.main, 0, timeout=25)
            row['responding'] = True
        except RuntimeError:row['responding'] = False
        row['response_ms'] = round((time.monotonic() - start) * 1000, 2)
        # Slow pixel sampling does not block fast cursor/busy observations.
        if time.monotonic() - self.last_grid_at >= .25:
            try:self.last_grid = hashlib.sha256(capture_client(a, self.grid)).hexdigest()
            except RuntimeError:self.last_grid = None  # A busy query may hide its grid briefly.
            self.last_grid_at = time.monotonic()
        row['grid_digest'] = self.last_grid
        return row

    def save_conditions(self, folder):
        self.a.guard()
        if self.a.u.GetForegroundWindow() != self.a.main:raise RuntimeError('Actas 활성 화면을 확인하세요.')
        path = folder / 'conditions.bmp'
        path.write_bytes(capture_client(self.a, self.a.conditions))
        return path.name


def summarize(samples):
    before = [s for s in samples if s['stage'] == 'before_query']
    after = [s for s in samples if s['stage'] == 'after_query']
    def transition(field, busy):
        observed = False
        for s in after:
            if s.get(field) is busy:observed = True
            elif observed and s.get(field) is (not busy):return True
        return False
    first = before[-1].get('grid_digest') if before else None
    return dict(samples=len(samples), wait_to_normal_observed=transition('wait_cursor', True),
                unresponsive_to_responsive_observed=transition('responding', False),
                disabled_to_enabled_observed=transition('main_enabled', False),
                grid_changed=bool(first and any(s.get('grid_digest') not in (None, first) for s in after)),
                query_completion_verified=False)


def run_probe(adapter, root, *, delay=12, duration=25, sampler_factory=NativeSampler, ocr_reader=recognize_conditions, progress=print):
    root = Path(root);root.mkdir(parents=True, exist_ok=True)
    folder = root / uuid.uuid4().hex;folder.mkdir()
    report = dict(format=1, kind='Actas-query-signal-probe-NOT-export-import', phase='prepared',
                  started_at=pilot.now(), query_attempted=False, query_sent=False,
                  query_completion_verified=False, export_sent=False, registered_in_sunil=False, samples=[])
    path = folder / 'query-probe.json';pilot.save_json(path, report)
    try:
        progress(f'{delay}초 안에 Actas 현재고 현황의 Y02 또는 Y09 창고 코드 칸을 클릭하고 손을 떼세요.')
        time.sleep(delay)
        adapter.preflight()
        warehouse = adapter.read_edit(adapter.editor).strip()
        if warehouse not in ('Y02', 'Y09'):raise RuntimeError('현재 창고가 Y02 또는 Y09여야 합니다. 창고 값은 변경하지 않습니다.')
        report['warehouse'] = warehouse
        sampler = sampler_factory(adapter)
        start = time.monotonic()
        def sample(stage):
            row = sampler.sample()
            report['samples'].append(dict(row, stage=stage, elapsed_ms=round((time.monotonic() - start) * 1000, 2)))
        for _ in range(4):sample('before_query');time.sleep(.05)
        report.update(phase='query_attempted', query_attempted=True, query_sent=None, query_started_at=pilot.now())
        pilot.save_json(path, report)
        adapter.query_committed(warehouse)
        report.update(query_sent=True, phase='measuring')
        deadline = time.monotonic() + duration
        while time.monotonic() < deadline:
            sample('after_query');time.sleep(.03)
        report['summary'] = summarize(report['samples'])
        report['condition_capture'] = sampler.save_conditions(folder)
        # Always preserve timing measurements even if optional local OCR fails.
        pilot.save_json(path, report)
        try:report['condition_ocr'] = ocr_reader(folder / report['condition_capture'])
        except Exception as error:report['condition_ocr'] = dict(status='error', failure_type=type(error).__name__)
        report.update(phase='measurement_complete_NOT_query_completion', completed_at=pilot.now())
        pilot.save_json(path, report)
        return folder, report
    except Exception as error:
        report.update(phase='failed', failure_type=type(error).__name__, failure_message=str(error))
        report['summary'] = summarize(report['samples'])
        pilot.save_json(path, report)
        progress('측정 기록: ' + str(path))
        raise


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='Actas 조회 신호 측정 · 엑셀 추출/등록 없음')
    parser.add_argument('--output-root', type=Path, default=Path(__file__).resolve().parent / 'runs')
    args = parser.parse_args()
    print('Actas 현재고 현황에서 창고를 Y02 또는 Y09로 두세요. 저장창을 닫으세요.')
    print('현재 창고로 Ctrl+R 조회 한 번만 실행하고 25초 측정합니다. 창고 변경·추출·등록은 하지 않습니다.')
    print('측정 중에는 화면 전환/마우스/키보드 조작을 멈춰 주세요. 잠금 화면에서는 실행하지 않습니다.')
    if not pilot.confirm_token('START', '준비되면 start, 중단은 Enter: '):return 1
    try:
        with pilot.single_pilot():
            folder, report = run_probe(pilot.WindowsActas(), args.output_root, progress=lambda text: print(text, flush=True))
        print('측정 완료. 조회 완료를 자동 판정한 것은 아닙니다.')
        print('전달할 기록:', folder / 'query-probe.json')
        print('창고명 판독 검사용 화면:', folder / 'conditions.bmp')
        return 0
    except Exception as error:
        print('조회 신호 측정 중단:', str(error))
        print('기록 파일을 전달하세요. 자동 재시도하지 않습니다.')
        return 1


if __name__ == '__main__':raise SystemExit(main())
