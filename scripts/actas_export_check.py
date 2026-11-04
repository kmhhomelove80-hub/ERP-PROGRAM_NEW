"""Interactive Actas export PILOT. No database/API/config/credential access.

Only confirmed stock-window selectors, Ctrl+R, Ctrl+E and the owned Save As
dialog are used. A person MUST confirm each completed query. No auto import.
"""
import argparse
from contextlib import contextmanager
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import uuid

LIMIT = 10 * 1024 * 1024


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def single_pilot(name=r'Local\SUNIL-Actas-Export-Pilot'):
    if os.name != 'nt':raise RuntimeError('Windows 검증 전용입니다.')
    import ctypes
    from ctypes import wintypes as wt
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]
    kernel.CreateMutexW.restype = wt.HANDLE
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    kernel.ReleaseMutex.argtypes = [wt.HANDLE]
    ctypes.set_last_error(0)
    handle = kernel.CreateMutexW(None, True, name)
    existed = ctypes.get_last_error() == 183
    if not handle:raise RuntimeError('동시 실행 방지를 준비하지 못했습니다.')
    try:
        if existed:raise RuntimeError('다른 Actas 추출 검증이 진행 중입니다.')
        yield
    finally:
        if not existed:kernel.ReleaseMutex(handle)
        kernel.CloseHandle(handle)


def save_json(path, record):
    fd, temporary = tempfile.mkstemp(prefix='.check-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):os.unlink(temporary)


def inspect_export(data):
    if not data or len(data) > LIMIT:
        raise RuntimeError('추출 파일은 0바이트 초과, 10MB 이하여야 합니다.')
    text = None
    for encoding in ('utf-8-sig', 'cp949'):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:pass
    if text is None or '\t' not in text or '\x00' in text:
        raise RuntimeError('제공하신 Actas 탭 구분 XLS 형식이 아닙니다.')
    table = list(csv.reader(io.StringIO(text), delimiter='\t'))
    if not table or len(table) > 10005 or any(len(row) > 100 for row in table):
        raise RuntimeError('추출 자료의 행/열 범위를 확인하세요.')
    headers = [v.strip() for v in table[0]]
    required = ('품목코드', '품목명', '현재고', '출하대기')
    if any(headers.count(h) != 1 for h in required):
        raise RuntimeError('품목코드·품목명·현재고·출하대기 열이 필요합니다.')
    indexes = {h: headers.index(h) for h in required}
    seen, records, quantity, pending, negative = set(), [], 0, 0, 0
    for row in table[1:]:
        if not any(v.strip() for v in row):continue
        if len(row) <= max(indexes.values()):raise RuntimeError('일부 행의 열이 누락됐습니다.')
        code = row[indexes['품목코드']].strip()
        if not code and row[0].strip() == '합계':continue
        if not code or len(code) > 100 or code.startswith(('=', '#')) or code in seen:
            raise RuntimeError('빈 코드·중복 코드·잘못된 품목코드가 있습니다.')
        values = []
        for field in ('현재고', '출하대기'):
            try:
                value = Decimal(row[indexes[field]].strip().replace(',', ''))
                if not value.is_finite() or value != value.to_integral_value() or not -2147483648 <= value <= 2147483647:
                    raise InvalidOperation
                values.append(int(value))
            except InvalidOperation:
                raise RuntimeError('현재고·출하대기는 원본 정수 수량이어야 합니다.') from None
        seen.add(code)
        quantity += values[0]
        pending += values[1]
        negative += values[0] < 0
        records.append((code, *values))
    if not records:raise RuntimeError('빈 재고 자료는 검증 성공으로 처리하지 않습니다.')
    canonical = json.dumps(sorted(records), ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return {'items': len(records), 'quantity': quantity, 'shipping_pending': pending,
            'negative_quantity_items': negative, 'sha256': hashlib.sha256(data).hexdigest(),
            'inventory_digest': hashlib.sha256(canonical).hexdigest()}


def read_closed(path):
    if os.name != 'nt':
        with path.open('rb') as stream:return stream.read(LIMIT + 1)
    import ctypes
    from ctypes import wintypes as wt
    import msvcrt
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE]
    api.CreateFileW.restype = wt.HANDLE
    api.CloseHandle.argtypes = [wt.HANDLE]
    handle = api.CreateFileW(str(path), 0x80000000, 0, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
    try:fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except Exception:
        api.CloseHandle(handle)
        raise
    with os.fdopen(fd, 'rb') as stream:return stream.read(LIMIT + 1)


def wait_file(path, timeout=45, quiet=1.5):
    deadline, previous, changed = time.monotonic() + timeout, None, time.monotonic()
    while time.monotonic() < deadline:
        if path.is_symlink():raise RuntimeError('연결된 파일은 사용하지 않습니다.')
        try:
            stat = path.stat()
            if stat.st_size > LIMIT:raise RuntimeError('추출 파일이 10MB를 초과합니다.')
            current = (stat.st_size, stat.st_mtime_ns)
            if previous != current:previous, changed = current, time.monotonic()
            if stat.st_size and time.monotonic() - changed >= quiet:
                data = read_closed(path)
                after = path.stat()
                if len(data) == stat.st_size and (after.st_size, after.st_mtime_ns) == current:return data
        except (FileNotFoundError, PermissionError):pass
        except OSError as error:
            if getattr(error, 'winerror', None) not in (32, 33):raise
        time.sleep(.1)
    raise RuntimeError('새 파일의 저장 완료를 확인하지 못했습니다.')


def run_check(adapter, root, confirm, progress=print, reader=wait_file):
    """Injected adapter permits isolated state-machine tests without Actas."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    run = root / uuid.uuid4().hex
    run.mkdir()
    report = {'format': 1, 'kind': 'Actas-export-pilot-NOT-import', 'started_at': now(),
              'phase': 'running', 'files': {}, 'registered_in_sunil': False,
              'automatic_query_completion_verified': False}
    save_json(run / 'result.json', report)
    try:
        adapter.preflight()
        for warehouse in ('Y02', 'Y09'):
            report.update(phase='selecting_' + warehouse)
            save_json(run / 'result.json', report)
            progress(warehouse + ' 입력 확인 및 조회 단축키 전송')
            adapter.select_and_query(warehouse)
            report.update(phase='awaiting_operator_' + warehouse)
            save_json(run / 'result.json', report)
            if not confirm(warehouse):raise RuntimeError('담당자가 검증을 중단했습니다.')
            acknowledged = now()
            path = run / (warehouse + '.xls')
            if path.exists():raise RuntimeError('새 파일 경로가 비어 있지 않습니다.')
            adapter.save_export(path)
            data = reader(path)
            summary = inspect_export(data)
            report['files'][warehouse] = {**summary, 'name': path.name,
                                          'query_confirmed_by': 'operator', 'query_confirmed_at': acknowledged,
                                          'saved_at': now()}
            save_json(run / 'result.json', report)
            progress(f"{warehouse}: {summary['items']}품목 / 현재고 합계 {summary['quantity']}")
        if report['files']['Y02']['inventory_digest'] == report['files']['Y09']['inventory_digest']:
            raise RuntimeError('두 창고의 코드·수량 자료가 같습니다. 창고 전환 여부를 확인해야 합니다.')
        report.update(phase='exports_validated', completed_at=now())
        save_json(run / 'result.json', report)
        return run, report
    except Exception as error:
        report.update(phase='failed', failed_at=now(), failure_type=type(error).__name__)
        save_json(run / 'result.json', report)
        progress('중단 기록: ' + str(run / 'result.json'))
        raise


class WindowsActas:
    """Bounded Win32 API calls; no fixed screen positions or arbitrary keys."""
    def __init__(self):
        if os.name != 'nt':raise RuntimeError('Actas가 설치된 Windows PC에서 실행하세요.')
        import ctypes
        from ctypes import wintypes as wt
        self.c, self.wt = ctypes, wt
        self.u = ctypes.WinDLL('user32', use_last_error=True)
        self.k = ctypes.WinDLL('kernel32', use_last_error=True)
        self.callback = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        definitions = {
            'EnumWindows': ([self.callback, wt.LPARAM], wt.BOOL),
            'EnumChildWindows': ([wt.HWND, self.callback, wt.LPARAM], wt.BOOL),
            'GetWindowTextW': ([wt.HWND, wt.LPWSTR, ctypes.c_int], ctypes.c_int),
            'GetClassNameW': ([wt.HWND, wt.LPWSTR, ctypes.c_int], ctypes.c_int),
            'GetDlgCtrlID': ([wt.HWND], ctypes.c_int), 'GetParent': ([wt.HWND], wt.HWND),
            'GetWindowLongW': ([wt.HWND, ctypes.c_int], wt.LONG),
            'IsWindowVisible': ([wt.HWND], wt.BOOL), 'IsWindowEnabled': ([wt.HWND], wt.BOOL),
            'IsWindow': ([wt.HWND], wt.BOOL), 'GetForegroundWindow': ([], wt.HWND),
            'GetAsyncKeyState': ([ctypes.c_int], wt.SHORT),
            'GetWindowThreadProcessId': ([wt.HWND, ctypes.POINTER(wt.DWORD)], wt.DWORD),
            'AttachThreadInput': ([wt.DWORD, wt.DWORD, wt.BOOL], wt.BOOL),
            'SetForegroundWindow': ([wt.HWND], wt.BOOL), 'SetFocus': ([wt.HWND], wt.HWND),
            'OpenInputDesktop': ([wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
            'CloseDesktop': ([wt.HANDLE], wt.BOOL),
            'GetUserObjectInformationW': ([wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)], wt.BOOL),
            'SendMessageTimeoutW': ([wt.HWND, wt.UINT, wt.WPARAM, wt.LPARAM, wt.UINT, wt.UINT, ctypes.POINTER(ctypes.c_size_t)], wt.LPARAM),
        }
        for name, (args, result) in definitions.items():
            function = getattr(self.u, name)
            function.argtypes, function.restype = args, result
        self.k.GetCurrentThreadId.restype = wt.DWORD

    def text(self, handle, kind='title'):
        buffer = self.c.create_unicode_buffer(256)
        (self.u.GetClassNameW if kind == 'class' else self.u.GetWindowTextW)(handle, buffer, 256)
        return buffer.value

    def windows(self, parent=None):
        rows = []
        @self.callback
        def visit(handle, unused):
            rows.append(handle)
            return len(rows) < 2000
        if parent:self.u.EnumChildWindows(parent, visit, 0)
        else:self.u.EnumWindows(visit, 0)
        return rows

    def pid(self, handle):
        pid = self.wt.DWORD()
        thread = self.u.GetWindowThreadProcessId(handle, self.c.byref(pid))
        return pid.value, thread

    def child(self, parent, cls, identity, *, direct=False):
        found = [h for h in self.windows(parent) if self.text(h, 'class') == cls and self.u.GetDlgCtrlID(h) == identity
                 and (not direct or self.u.GetParent(h) == parent)]
        if len(found) != 1:raise RuntimeError('확인한 화면 제어 항목이 없거나 여러 개입니다.')
        return found[0]

    def desktop(self):
        handle = self.u.OpenInputDesktop(0, False, 1)
        if not handle:raise RuntimeError('Windows 잠금을 해제한 화면에서 실행하세요.')
        try:
            name, size = self.c.create_unicode_buffer(256), self.wt.DWORD()
            if not self.u.GetUserObjectInformationW(handle, 2, name, self.c.sizeof(name), self.c.byref(size)) or name.value != 'Default':
                raise RuntimeError('Windows 잠금을 해제한 화면에서 실행하세요.')
        finally:self.u.CloseDesktop(handle)

    def guard(self):
        self.desktop()
        if not self.u.IsWindow(self.main) or self.pid(self.main)[0] != self.process:
            raise RuntimeError('Actas 프로그램이 변경되거나 종료됐습니다.')

    def activate(self, handle, foreground):
        self.guard()
        if not self.u.IsWindowEnabled(handle):raise RuntimeError('Actas의 다른 대화상자를 먼저 닫아주세요.')
        self.u.SetForegroundWindow(foreground)
        current, target = self.k.GetCurrentThreadId(), self.pid(handle)[1]
        attached = current != target
        if attached and not self.u.AttachThreadInput(current, target, True):
            raise RuntimeError('Actas와 같은 Windows 계정·권한 수준에서 실행하세요.')
        try:self.u.SetFocus(handle)
        finally:
            if attached:self.u.AttachThreadInput(current, target, False)
        if self.u.GetForegroundWindow() != foreground:
            raise RuntimeError('Actas 화면을 활성화하지 못했습니다. 자동 입력을 중단합니다.')

    def message(self, handle, message, wp=0, lp=0, timeout=3000):
        if self.pid(handle)[0] != self.process:
            raise RuntimeError('Actas의 제어 대상이 바뀌어 조작을 중단합니다.')
        result = self.c.c_size_t()
        if not self.u.SendMessageTimeoutW(handle, message, wp, lp, 2, timeout, self.c.byref(result)):
            raise RuntimeError('Actas 화면이 응답하지 않아 조작을 중단했습니다.')
        return result.value

    def read_edit(self, handle):
        buffer = self.c.create_unicode_buffer(512)
        self.message(handle, 0x000D, len(buffer), self.c.addressof(buffer))
        return buffer.value

    def set_edit(self, handle, value):
        buffer = self.c.create_unicode_buffer(value)
        self.message(handle, 0x000C, 0, self.c.addressof(buffer))
        if self.read_edit(handle) != value:raise RuntimeError('입력한 코드 또는 저장 경로를 재확인하지 못했습니다.')

    def shortcut(self, purpose):
        key = {'query': 0x52, 'export': 0x45}.get(purpose)
        if key is None:raise RuntimeError('조회·추출 외의 키 입력은 허용하지 않습니다.')
        self.guard()
        if self.u.GetForegroundWindow() != self.main:raise RuntimeError('Actas가 활성 화면이 아닙니다.')
        deadline = time.monotonic() + 3
        while any(self.u.GetAsyncKeyState(k) & 0x8000 for k in (0x10, 0x11, 0x12, 0x5B, 0x5C, 0x0D)):
            if time.monotonic() >= deadline:raise RuntimeError('Ctrl·Shift·Alt·Windows·Enter 키에서 손을 떼고 진행하세요.')
            time.sleep(.05)
        if self.u.GetForegroundWindow() != self.main:raise RuntimeError('확인 중 활성 화면이 바뀌어 키 입력을 중단합니다.')
        c, wt = self.c, self.wt
        class Key(c.Structure):
            _fields_ = [('vk', wt.WORD), ('scan', wt.WORD), ('flags', wt.DWORD), ('time', wt.DWORD), ('extra', c.c_size_t)]
        class Mouse(c.Structure):
            _fields_ = [('x', wt.LONG), ('y', wt.LONG), ('data', wt.DWORD), ('flags', wt.DWORD), ('time', wt.DWORD), ('extra', c.c_size_t)]
        class Union(c.Union):_fields_ = [('keyboard', Key), ('mouse', Mouse)]
        class Input(c.Structure):_fields_ = [('type', wt.DWORD), ('data', Union)]
        items = (Input * 4)()
        for event, vk, flags in zip(items, (0x11, key, key, 0x11), (0, 0, 2, 2)):
            event.type, event.data.keyboard.vk, event.data.keyboard.flags = 1, vk, flags
        self.u.SendInput.argtypes = [wt.UINT, c.POINTER(Input), c.c_int]
        sent = self.u.SendInput(4, items, c.sizeof(Input))
        if sent != 4:
            if sent:
                release = (Input * 2)()
                for event, vk in zip(release, (key, 0x11)):
                    event.type, event.data.keyboard.vk, event.data.keyboard.flags = 1, vk, 2
                self.u.SendInput(2, release, c.sizeof(Input))
            raise RuntimeError('단축키를 전송하지 못했습니다. Actas와 같은 권한 수준인지 확인하세요.')

    def preflight(self):
        self.desktop()
        roots = [h for h in self.windows() if self.u.IsWindowVisible(h)
                 and re.fullmatch(r'FNWND\d+', self.text(h, 'class'))
                 and re.match(r'^Actas\s*ERP(?:\b|_)', self.text(h), re.I)]
        if len(roots) != 1:raise RuntimeError('Actas 본체가 정확히 한 개 실행 중이어야 합니다.')
        self.main = roots[0]
        self.process = self.pid(self.main)[0]
        if not self.u.IsWindowEnabled(self.main):raise RuntimeError('Actas 저장창 등을 취소로 닫은 뒤 실행하세요.')
        self.stock = self.child(self.main, 'FNWND3105', 200)
        if self.text(self.stock).strip() != '현재고 현황':raise RuntimeError('현재고 현황을 먼저 열어주세요.')
        self.conditions = self.child(self.stock, 'pbdw105', 1002, direct=True)
        self.editor = self.child(self.conditions, 'Edit', 10, direct=True)

    def select_and_query(self, warehouse):
        if warehouse not in ('Y02', 'Y09'):raise RuntimeError('Y02·Y09만 검증합니다.')
        self.guard()
        self.stock = self.child(self.main, 'FNWND3105', 200)
        if self.text(self.stock).strip() != '현재고 현황':raise RuntimeError('현재고 현황이 아닙니다.')
        self.conditions = self.child(self.stock, 'pbdw105', 1002, direct=True)
        self.editor = self.child(self.conditions, 'Edit', 10, direct=True)
        self.activate(self.conditions, self.main)
        deadline = time.monotonic() + 5
        while not self.u.IsWindowVisible(self.editor) and time.monotonic() < deadline:time.sleep(.1)
        if not self.u.IsWindowVisible(self.editor):
            raise RuntimeError('창고 코드 칸을 클릭해 선택해 둔 뒤 다시 실행하세요. 화면 좌표를 추정하지 않습니다.')
        if self.u.GetWindowLongW(self.editor, -16) & (0x20 | 0x800):
            raise RuntimeError('비밀번호 또는 읽기 전용 입력란에는 접근하지 않습니다.')
        before = self.read_edit(self.editor).strip()
        if not re.fullmatch(r'Y\d{2}', before):
            raise RuntimeError('선택된 칸이 창고 코드 칸인지 확인하지 못했습니다. 입력하지 않습니다.')
        self.activate(self.editor, self.main)
        self.set_edit(self.editor, warehouse)
        # Move out and back to the SAME DataWindow cell, without Tab changing
        # the selected column. The operator then verifies the retrieved table.
        self.activate(self.stock, self.main)
        self.activate(self.conditions, self.main)
        if not self.u.IsWindowVisible(self.editor) or self.read_edit(self.editor).strip() != warehouse:
            raise RuntimeError('창고 코드가 유지되는지 확인하지 못했습니다.')
        self.shortcut('query')

    def save_export(self, path):
        self.activate(self.stock, self.main)
        self.shortcut('export')
        deadline = time.monotonic() + 30
        dialog = None
        while time.monotonic() < deadline:
            matches = [h for h in self.windows() if self.text(h, 'class') == '#32770'
                       and self.pid(h)[0] == self.process and self.u.GetParent(h) == self.main and self.u.IsWindowVisible(h)]
            if len(matches) > 1:raise RuntimeError('Actas 대화상자가 여러 개입니다.')
            if matches:dialog = matches[0];break
            time.sleep(.1)
        if not dialog:raise RuntimeError('확인한 Actas 저장창이 열리지 않았습니다.')
        filename = self.child(dialog, 'Edit', 1152, direct=True)
        button = self.child(dialog, 'Button', 1, direct=True)
        self.child(dialog, 'Button', 2, direct=True)
        filetype = self.child(dialog, 'ComboBox', 1136, direct=True)
        selected = self.message(filetype, 0x0147)
        if selected > 100:raise RuntimeError('저장 형식을 확인하지 못했습니다.')
        length = self.message(filetype, 0x0149, selected)
        if not 0 < length < 256:raise RuntimeError('저장 형식 이름을 확인하지 못했습니다.')
        label = self.c.create_unicode_buffer(length + 1)
        self.message(filetype, 0x0148, selected, self.c.addressof(label))
        if not re.search(r'\*\.xls(?:[;)\s]|$)', label.value, re.I):
            raise RuntimeError('Actas 저장 형식이 Excel Files (*.xls)가 아닙니다.')
        if path.exists():raise RuntimeError('기존 파일은 덮어쓰지 않습니다.')
        self.activate(filename, dialog)
        self.set_edit(filename, str(path.resolve()))
        self.message(button, 0x00F5, timeout=30000)
        deadline = time.monotonic() + 30
        while self.u.IsWindow(dialog) and self.u.IsWindowVisible(dialog) and time.monotonic() < deadline:time.sleep(.1)
        if self.u.IsWindow(dialog) and self.u.IsWindowVisible(dialog):
            raise RuntimeError('저장창이 닫히지 않았습니다. 추가 메시지가 있는지 확인하세요.')


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='Actas Y02·Y09 추출 검증 · SUNIL 등록 없음')
    parser.add_argument('--output-root', type=Path, default=Path(__file__).resolve().parent / 'runs')
    args = parser.parse_args()
    print('Actas 현재고 현황에서 검색·분류 조건을 전체로 하고 창고 코드 칸을 선택해 두세요.')
    print('다른 사람이 Actas를 조작하지 않도록 하고, 실행 중 마우스·키보드는 확인 단계에서만 사용하세요.')
    if input('준비됐다면 START를 입력하세요. 취소하려면 Enter: ').strip() != 'START':return 1
    def confirm(warehouse):
        print(f'Actas에서 창고 코드 {warehouse}가 유지되고 해당 창고의 조회 표가 갱신됐는지 직접 확인하세요.')
        print('확인 후 이 PowerShell로 돌아오세요. 아직 갱신 중이면 기다리세요.')
        return input(f'완료를 확인했다면 {warehouse}를 입력, 중단하려면 Enter: ').strip() == warehouse
    try:
        with single_pilot():
            folder, report = run_check(WindowsActas(), args.output_root, confirm)
        print('두 파일 검증 완료. SUNIL ERP 등록·실제 재고 변경은 하지 않았습니다.')
        print('결과 파일:', folder / 'result.json')
        return 0
    except Exception as error:
        print('검증 중단:', str(error))
        print('Actas의 실제 상태를 확인하고 기록 파일을 전달하세요. 자동 재시도하지 않습니다.')
        return 1


if __name__ == '__main__':raise SystemExit(main())
