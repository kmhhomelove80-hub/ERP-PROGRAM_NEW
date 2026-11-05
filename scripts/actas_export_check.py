"""Actas staged warehouse/query/export pilot; never imports into SUNIL.

Input-only mode remains available. Checked export mode requires operator
warehouse-name acceptance before query and query-completion acceptance before
saving. No automatic query-completion or warehouse-name claim; no DB access.
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
    seen, records = set(), []
    totals = {'현재고': Decimal(0), '출하대기': Decimal(0)}
    blanks = {'현재고': 0, '출하대기': 0}
    fractions = {'현재고': 0, '출하대기': 0}
    samples = {field: {'blank': [], 'fractional': []} for field in totals}
    negative = 0
    for number, row in enumerate(table[1:], 2):
        if not any(v.strip() for v in row):continue
        if len(row) <= max(indexes.values()):raise RuntimeError(f'{number}행: 일부 열이 누락됐습니다.')
        code = row[indexes['품목코드']].strip()
        if not code and row[0].strip() == '합계':continue
        if not code or len(code) > 100 or code.startswith(('=', '#')) or code in seen:
            raise RuntimeError(f'{number}행 품목코드: 빈 값·중복·잘못된 코드가 있습니다.')
        values = []
        for field in ('현재고', '출하대기'):
            raw = row[indexes[field]].strip()
            if not raw:
                # Preserve unknown/blank, never assume a displayed blank is 0.
                blanks[field] += 1
                if len(samples[field]['blank']) < 5:samples[field]['blank'].append({'row': number, 'value': None})
                values.append(None)
                continue
            try:
                value = Decimal(raw.replace(',', ''))
                if not value.is_finite() or not -2147483648 <= value <= 2147483647:
                    raise InvalidOperation
                if value.as_tuple().exponent < -12 or len(value.as_tuple().digits) > 30:
                    raise InvalidOperation
            except InvalidOperation:
                raise RuntimeError(f'{number}행 {field}: 유효한 원본 수량인지 확인하세요. 소수·음수는 허용합니다.') from None
            if value != value.to_integral_value():
                fractions[field] += 1
                if len(samples[field]['fractional']) < 5:samples[field]['fractional'].append({'row': number, 'value': raw})
            totals[field] += value
            if field == '현재고' and value < 0:negative += 1
            # Plain exact decimal strings (no binary float / rounding).
            values.append('0' if value == 0 else format(value.normalize(), 'f'))
        seen.add(code)
        records.append((code, *values))
    if not records:raise RuntimeError('빈 재고 자료는 검증 성공으로 처리하지 않습니다.')
    canonical = json.dumps(sorted(records), ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    def complete_total(field):
        if blanks[field]:return None
        value = totals[field]
        return int(value) if value == value.to_integral_value() else format(value, 'f')
    return {'items': len(records), 'quantity': complete_total('현재고'), 'shipping_pending': complete_total('출하대기'),
            'quantity_nonblank_sum': format(totals['현재고'], 'f'),
            'shipping_pending_nonblank_sum': format(totals['출하대기'], 'f'),
            'blank_quantity_items': blanks['현재고'], 'blank_shipping_pending_items': blanks['출하대기'],
            'fractional_quantity_items': fractions['현재고'], 'fractional_shipping_pending_items': fractions['출하대기'],
            'integer_quantity_compatible': not any((*blanks.values(), *fractions.values())),
            'original_quantities_preserved': True, 'samples': samples,
            'negative_quantity_items': negative, 'sha256': hashlib.sha256(data).hexdigest(),
            'inventory_digest': hashlib.sha256(canonical).hexdigest(),
            'code_set_digest': hashlib.sha256(json.dumps(sorted(seen), ensure_ascii=False).encode('utf-8')).hexdigest()}


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
    report = {'format': 3, 'pilot_version': getattr(adapter, 'pilot_version', 3), 'kind': 'Actas-export-pilot-NOT-import', 'started_at': now(),
              'phase': 'running', 'files': {}, 'registered_in_sunil': False,
              'automatic_query_completion_verified': False, 'warehouse_transition_verified': False}
    save_json(run / 'result.json', report)
    try:
        adapter.preflight()
        for warehouse in ('Y02', 'Y09'):
            report.update(phase='selecting_' + warehouse)
            save_json(run / 'result.json', report)
            progress(warehouse + ' 입력 확인 및 조회 단축키 전송')
            evidence = adapter.select_and_query(warehouse)
            if evidence is not None:
                report.setdefault('input_evidence', {})[warehouse] = evidence
                report['pilot_version'] = getattr(adapter, 'pilot_version', 4)
            report.update(phase='awaiting_operator_' + warehouse)
            save_json(run / 'result.json', report)
            if not confirm(warehouse):raise RuntimeError('담당자가 검증을 중단했습니다.')
            acknowledged = now()
            path = run / (warehouse + '.xls')
            if path.exists():raise RuntimeError('새 파일 경로가 비어 있지 않습니다.')
            adapter.save_export(path)
            data = reader(path)
            report['files'][warehouse] = {'name': path.name, 'sha256': hashlib.sha256(data).hexdigest(),
                                          'parse_status': 'pending', 'query_confirmed_by': 'operator',
                                          'query_confirmed_at': acknowledged, 'saved_at': now()}
            save_json(run / 'result.json', report)
            try:
                summary = inspect_export(data)
            except Exception:
                report['files'][warehouse]['parse_status'] = 'invalid'
                raise
            report['files'][warehouse].update(summary, parse_status='valid')
            save_json(run / 'result.json', report)
            total = summary['quantity'] if summary['quantity'] is not None else '공란 포함 · 합계 미확정'
            progress(f"{warehouse}: {summary['items']}품목 / 현재고 합계 {total} / 소수 {summary['fractional_quantity_items']}품목")
        if report['files']['Y02']['inventory_digest'] == report['files']['Y09']['inventory_digest']:
            raise RuntimeError('두 창고의 코드·수량 자료가 같습니다. 창고 전환 여부를 확인해야 합니다.')
        if report['files']['Y02']['code_set_digest'] == report['files']['Y09']['code_set_digest']:
            raise RuntimeError('두 파일의 품목 구성이 같아 창고 전환을 입증하지 못했습니다. 수량 변화만으로 성공 처리하지 않습니다.')
        report.update(phase='export_files_checked', completed_at=now())
        save_json(run / 'result.json', report)
        return run, report
    except Exception as error:
        report.update(phase='failed', failed_at=now(), failure_type=type(error).__name__, failure_message=str(error))
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
            'IsChild': ([wt.HWND, wt.HWND], wt.BOOL),
            'GetWindowRect': ([wt.HWND, ctypes.POINTER(wt.RECT)], wt.BOOL),
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

    def focus_state(self):
        c, wt = self.c, self.wt
        class State(c.Structure):
            _fields_ = [('size', wt.DWORD), ('flags', wt.DWORD), ('active', wt.HWND), ('focus', wt.HWND),
                        ('capture', wt.HWND), ('menu', wt.HWND), ('move', wt.HWND), ('caret', wt.HWND), ('rect', wt.RECT)]
        self.u.GetGUIThreadInfo.argtypes = [wt.DWORD, c.POINTER(State)]
        state = State();state.size = c.sizeof(state)
        if not self.u.GetGUIThreadInfo(self.pid(self.main)[1], c.byref(state)):
            raise RuntimeError('Actas 입력 포커스를 확인하지 못했습니다.')
        rect = wt.RECT()
        if not self.u.GetWindowRect(self.editor, c.byref(rect)):
            raise RuntimeError('창고 입력란 위치가 바뀌었습니다.')
        return int(state.focus or 0), (rect.left, rect.top, rect.right, rect.bottom), bool(self.u.IsWindowVisible(self.editor))

    def key_events(self, events, *, editor_required=False, condition_required=False):
        self.guard()
        if self.u.GetForegroundWindow() != self.main:raise RuntimeError('Actas가 활성 화면이 아닙니다.')
        deadline = time.monotonic() + 3
        while any(self.u.GetAsyncKeyState(k) & 0x8000 for k in (0x10, 0x11, 0x12, 0x5B, 0x5C, 0x0D, 1, 2)):
            if time.monotonic() >= deadline:raise RuntimeError('보조키·Enter·마우스 버튼에서 손을 떼고 진행하세요.')
            time.sleep(.05)
        if self.u.GetForegroundWindow() != self.main:raise RuntimeError('확인 중 활성 화면이 바뀌어 키 입력을 중단합니다.')
        c, wt = self.c, self.wt
        class Key(c.Structure):
            _fields_ = [('vk', wt.WORD), ('scan', wt.WORD), ('flags', wt.DWORD), ('time', wt.DWORD), ('extra', c.c_size_t)]
        class Mouse(c.Structure):
            _fields_ = [('x', wt.LONG), ('y', wt.LONG), ('data', wt.DWORD), ('flags', wt.DWORD), ('time', wt.DWORD), ('extra', c.c_size_t)]
        class Union(c.Union):_fields_ = [('keyboard', Key), ('mouse', Mouse)]
        class Input(c.Structure):_fields_ = [('type', wt.DWORD), ('data', Union)]
        focus, _, visible = self.focus_state()
        if editor_required and (focus != self.editor or not visible):
            raise RuntimeError('창고 입력란에 포커스가 없어 키 입력을 중단합니다.')
        if condition_required and not (focus == self.conditions or self.u.IsChild(self.conditions, focus)):
            raise RuntimeError('창고 조건 영역 밖으로 포커스가 이동했습니다.')
        items = (Input * len(events))()
        for event, (vk, scan, flags) in zip(items, events):
            event.type = 1
            event.data.keyboard.vk, event.data.keyboard.scan, event.data.keyboard.flags = vk, scan, flags
        self.u.SendInput.argtypes = [wt.UINT, c.POINTER(Input), c.c_int]
        sent = self.u.SendInput(len(events), items, c.sizeof(Input))
        if sent != len(events):
            if sent:
                ups = [(vk, scan, flags | 2) for vk, scan, flags in events if not flags & 2]
                release = (Input * len(ups))()
                for event, (vk, scan, flags) in zip(release, ups):
                    event.type = 1
                    event.data.keyboard.vk, event.data.keyboard.scan, event.data.keyboard.flags = vk, scan, flags
                self.u.SendInput(len(ups), release, c.sizeof(Input))
            raise RuntimeError('키를 전송하지 못했습니다. Actas와 같은 권한 수준인지 확인하세요.')

    def shortcut(self, purpose):
        key = {'query': 0x52, 'export': 0x45}.get(purpose)
        if key is None:raise RuntimeError('조회·추출 외의 단축키는 허용하지 않습니다.')
        self.key_events([(0x11, 0, 0), (key, 0, 0), (key, 0, 2), (0x11, 0, 2)])

    def wait_input(self, predicate, message, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.guard()
            if self.u.GetForegroundWindow() != self.main:
                raise RuntimeError('추가 창이 열리거나 활성 화면이 바뀌었습니다. 입력을 중단합니다.')
            if predicate():return
            time.sleep(.03)
        raise RuntimeError(message)

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

    def enter_warehouse(self, warehouse, *, already_selected=False):
        if warehouse not in ('Y02', 'Y09'):raise RuntimeError('Y02·Y09만 검증합니다.')
        self.guard()
        self.stock = self.child(self.main, 'FNWND3105', 200)
        if self.text(self.stock).strip() != '현재고 현황':raise RuntimeError('현재고 현황이 아닙니다.')
        self.conditions = self.child(self.stock, 'pbdw105', 1002, direct=True)
        self.editor = self.child(self.conditions, 'Edit', 10, direct=True)
        if not already_selected:self.activate(self.conditions, self.main)
        if self.u.GetForegroundWindow() != self.main or not self.u.IsWindowVisible(self.editor):
            raise RuntimeError('Actas 창고 코드 칸을 직접 클릭해 선택해 두세요.')
        if self.u.GetWindowLongW(self.editor, -16) & (0x20 | 0x800):
            raise RuntimeError('비밀번호 또는 읽기 전용 입력란에는 접근하지 않습니다.')
        if self.focus_state()[0] != self.editor:
            raise RuntimeError('창고 코드 입력란에 커서가 없습니다. 아무 값도 입력하지 않습니다.')
        before = self.read_edit(self.editor).strip()
        if not re.fullmatch(r'Y\d{2}', before):
            raise RuntimeError('선택된 칸을 창고 코드 칸으로 확인하지 못했습니다.')
        # Do NOT use WM_SETTEXT: PowerBuilder may not accept that into its
        # DataWindow buffer. Real keyboard text + Tab triggers edit/validation.
        self.message(self.editor, 0x00B1, 0, -1)  # Selection only, not a value write.
        self.key_events([(8, 0, 0), (8, 0, 2)], editor_required=True)
        self.wait_input(lambda: self.read_edit(self.editor) == '', '기존 코드 삭제 입력을 확인하지 못했습니다.')
        events = []
        for char in warehouse:events.extend([(0, ord(char), 4), (0, ord(char), 6)])
        self.key_events(events, editor_required=True)
        self.wait_input(lambda: self.read_edit(self.editor) == warehouse, '실제 키 입력 후 코드를 확인하지 못했습니다.')
        initial = self.focus_state()
        self.key_events([(9, 0, 0), (9, 0, 2)], editor_required=True)
        self.wait_input(lambda: self.focus_state() != initial, 'Tab 입력 확정 후 포커스 전환을 확인하지 못했습니다.')
        focus = self.focus_state()[0]
        if not (focus == self.conditions or self.u.IsChild(self.conditions, focus)):
            raise RuntimeError('Tab 후 다른 영역으로 이동했습니다. 조회는 실행하지 않았습니다.')
        self.key_events([(0x10, 0, 0), (9, 0, 0), (9, 0, 2), (0x10, 0, 2)], condition_required=True)
        self.wait_input(lambda: self.focus_state() == initial and self.read_edit(self.editor) == warehouse,
                        '입력 확정 후 원래 창고 칸의 코드를 재확인하지 못했습니다.')
        return {'before': before, 'requested': warehouse, 'editor_readback': warehouse,
                'input_method': 'native_keyboard_then_Tab_ShiftTab', 'tab_transition_observed': True,
                'warehouse_name_verified': False, 'query_sent': False, 'export_sent': False}

    def select_and_query(self, warehouse):
        evidence = self.enter_warehouse(warehouse)
        self.shortcut('query')
        return evidence

    def query_committed(self, warehouse):
        self.preflight()
        self.guard()
        if self.u.GetForegroundWindow() != self.main:
            raise RuntimeError('조회 직전 Actas가 활성 화면이 아닙니다. 창고 코드 불일치가 아닙니다.')
        focus, _, visible = self.focus_state()
        if focus != self.editor or not visible:
            raise RuntimeError('조회 직전 창고 코드 입력칸의 포커스를 확인하지 못했습니다. 코드 불일치로 판정한 것은 아닙니다.')
        actual = self.read_edit(self.editor).strip()
        if actual != warehouse:
            shown = actual if re.fullmatch(r'Y\d{2}', actual) else '(창고 코드 형식 아님)'
            raise RuntimeError(f'조회 직전 실제 코드 불일치: 요청 {warehouse}, 확인 {shown}. 조회하지 않았습니다.')
        self.shortcut('query')
        return {'editor_readback': actual, 'focus_verified': True, 'query_sent': True}

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



def confirm_token(expected, prompt):
    while True:
        answer = input(prompt).strip()
        if not answer:return False
        if answer.casefold() == expected.casefold():return True
        print(f'지금은 {expected} 확인 단계입니다. {expected}를 입력하거나 Enter로 중단하세요.')

def probe_input(adapter, root, warehouse, *, delay=12, progress=print):
    root = Path(root);root.mkdir(parents=True, exist_ok=True)
    folder = root / uuid.uuid4().hex;folder.mkdir()
    report = {'format': 3, 'pilot_version': 3, 'kind': 'warehouse-input-probe-NOT-query-export',
              'phase': 'prepared', 'warehouse': warehouse, 'started_at': now(),
              'query_sent': False, 'export_sent': False, 'registered_in_sunil': False,
              'warehouse_name_verified': False}
    save_json(folder / 'input-probe.json', report)
    try:
        progress(f'{delay}초 안에 Actas로 돌아가 창고 코드 칸을 클릭한 뒤 손을 떼세요.')
        time.sleep(delay)
        adapter.preflight()
        report.update(phase='input_only')
        save_json(folder / 'input-probe.json', report)
        report['evidence'] = adapter.enter_warehouse(warehouse, already_selected=True)
        report.update(phase='input_keys_checked_operator_review_required', completed_at=now())
        save_json(folder / 'input-probe.json', report)
        return folder, report
    except Exception as error:
        report.update(phase='failed', failure_type=type(error).__name__, failure_message=str(error))
        save_json(folder / 'input-probe.json', report)
        progress('입력 검증 기록: ' + str(folder / 'input-probe.json'))
        raise


class OperatorCheckedExport:
    """Require visual warehouse acceptance before query and again before export."""
    pilot_version = 5
    def __init__(self, adapter, prepare, accept_input, prepare_query):
        self.adapter, self.prepare, self.accept_input = adapter, prepare, accept_input
        self.prepare_query = prepare_query
    def preflight(self):self.adapter.preflight()
    def select_and_query(self, warehouse):
        if not self.prepare(warehouse):raise RuntimeError('창고 선택 준비를 중단했습니다.')
        self.adapter.preflight()
        evidence = self.adapter.enter_warehouse(warehouse, already_selected=True)
        if not self.accept_input(warehouse):raise RuntimeError('창고 코드·명칭 확인을 중단했습니다. 조회하지 않았습니다.')
        self.prepare_query(warehouse)
        evidence['query_check'] = self.adapter.query_committed(warehouse)
        evidence.update(query_sent=True, warehouse_name_confirmed_by='operator', input_confirmed_at=now())
        return evidence
    def save_export(self, path):self.adapter.save_export(path)


def prepare_export_input(warehouse):
    if not confirm_token('READY', f'{warehouse} 입력 준비입니다. ready 입력 후 12초 안에 Actas 창고 코드 칸을 클릭하세요. 중단은 Enter: '):return False
    print('12초 안에 창고 코드 칸을 클릭하고 손을 떼세요.', flush=True)
    time.sleep(12)
    return True


def export_main(root):
    print('Y02 영주본사·Y09 부산창고 조회/파일 저장 시험입니다. SUNIL 등록은 하지 않습니다.')
    print('검색어·분류 필터를 전체로 두고 Actas 저장창을 닫으세요. 다른 사용자는 Actas를 조작하지 마세요.')
    if not confirm_token('START', '시작은 start, 중단은 Enter: '):return 1
    def accept_input(warehouse):
        name = '영주제품창고' if warehouse == 'Y02' else '부산창고'
        print(f'Actas 코드 {warehouse}와 오른쪽 창고명 {name}를 직접 확인하세요. 아직 조회하지 않았습니다.', flush=True)
        return confirm_token(warehouse + ' INPUT', f'일치하면 {warehouse} input, 중단은 Enter: ')
    def prepare_query(warehouse):
        print(f'{warehouse} 코드·창고명 확인을 기록했습니다.', flush=True)
        print('12초 안에 Actas로 돌아가 같은 창고 코드 칸을 클릭하고 손을 떼세요. 그 뒤 도구가 조회합니다.', flush=True)
        time.sleep(12)
    def accept_query(warehouse):
        print(f'Actas에서 {warehouse} 코드·창고명이 유지되고 재고 표가 새로 조회됐는지 확인하세요.', flush=True)
        print('아직 조회 중이거나 이전 표라면 확인을 입력하지 마세요. 확인 전에는 저장하지 않습니다.', flush=True)
        return confirm_token(warehouse, f'조회 완료 확인은 {warehouse}, 중단은 Enter: ')
    try:
        with single_pilot():
            adapter = OperatorCheckedExport(WindowsActas(), prepare_export_input, accept_input, prepare_query)
            folder, report = run_check(adapter, root, accept_query, progress=lambda message: print(message, flush=True))
        print('두 파일의 형식 검사가 끝났습니다. 창고/조회는 담당자가 확인했으며 자동 판독이 아닙니다.')
        print('SUNIL ERP에는 등록하지 않았습니다. result.json과 두 XLS 파일을 전달하세요.')
        print('결과:', folder / 'result.json')
        return 0
    except Exception as error:
        print('조회/저장 검증 중단:', str(error))
        print('위 중단 기록을 전달하세요. 자동 재시도하지 않습니다.')
        return 1


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):stream.reconfigure(encoding='utf-8', errors='replace')
    parser = argparse.ArgumentParser(description='Actas 창고 입력 확정만 검증 · 조회/추출/등록 없음')
    parser.add_argument('--output-root', type=Path, default=Path(__file__).resolve().parent / 'runs')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--probe-warehouse', choices=('Y02', 'Y09'))
    mode.add_argument('--export-checked', action='store_true')
    args = parser.parse_args()
    if args.export_checked:
        return export_main(args.output_root)
    if not args.probe_warehouse:
        print('일괄 조회/추출은 중지된 검증본입니다. --probe-warehouse Y09로 입력 확정부터 확인하세요.')
        return 1
    print('이번에는 창고 코드 입력만 합니다. 조회·Excel 저장·SUNIL ERP 등록은 하지 않습니다.')
    print('Actas 저장창을 닫고 현재고 현황을 열어 두세요.')
    if not confirm_token('START', '준비되면 start를 입력하세요. 취소하려면 Enter: '):return 1
    try:
        with single_pilot():
            folder, report = probe_input(WindowsActas(), args.output_root, args.probe_warehouse,
                                         progress=lambda message: print(message, flush=True))
        print('키 입력 확인이 끝났습니다. 창고 코드와 오른쪽 창고명을 직접 확인해 주세요.')
        print('조회·파일 추출·SUNIL ERP 등록은 실행하지 않았습니다.')
        print('결과 파일:', folder / 'input-probe.json')
        return 0
    except Exception as error:
        print('입력 검증 중단:', str(error))
        print('오류 화면과 입력 검증 기록을 전달하세요. 반복 실행하지 마세요.')
        return 1


if __name__ == '__main__':raise SystemExit(main())
