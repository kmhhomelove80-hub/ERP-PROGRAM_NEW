"""Interactive Actas adapter. Selectors MUST be checked on the actual PC first.

No database connection or Actas password is used. Never selects Save/Delete in
Actas itself; the only save action is the matched Excel Save As dialog.
"""
import json
import os
from pathlib import Path
import re
import time


def inspect_windows():
    """Read-only Win32 inspection; no optional package or admin rights required.

    Do not read Edit values, grid contents or password fields. Labels, control
    IDs/classes and hierarchy suffice for the first selector-discovery step.
    """
    if os.name != 'nt':
        raise RuntimeError('Actas가 실행 중인 Windows PC에서 진단하세요.')
    import ctypes
    from ctypes import wintypes
    user = ctypes.WinDLL('user32', use_last_error=True)
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback, wintypes.LPARAM]
    user.EnumChildWindows.argtypes = [wintypes.HWND, callback, wintypes.LPARAM]
    user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetDlgCtrlID.argtypes = [wintypes.HWND]
    user.GetParent.argtypes = [wintypes.HWND]
    user.GetParent.restype = wintypes.HWND
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.IsWindowEnabled.argtypes = [wintypes.HWND]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    roots = []

    def text(handle, kind='title'):
        buffer = ctypes.create_unicode_buffer(256)
        (user.GetClassNameW if kind == 'class' else user.GetWindowTextW)(handle, buffer, 256)
        return buffer.value

    @callback
    def visit(handle, unused):
        title = text(handle)
        if 'actas' in title.lower() and user.IsWindowVisible(handle):
            pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
            # Window titles can contain a signed-in person's name. Do not emit it.
            roots.append({'handle': int(handle), 'process_id': pid.value,
                          'class_name': text(handle, 'class'), 'controls': []})
        return True
    user.EnumWindows(visit, 0)
    labels = ('창고', '조회', '엑셀', '현재고 현황', '저장', '파일', '취소', '확인', 'Y01', 'Y02', 'Y09')
    for root in roots:
        @callback
        def child(handle, unused):
            if len(root['controls']) >= 1000:
                return False
            cls = text(handle, 'class')
            caption = '' if 'edit' in cls.lower() else text(handle)
            caption = caption if caption in labels else ''
            root['controls'].append({'handle': int(handle), 'parent': int(user.GetParent(handle) or 0),
                                     'class_name': cls, 'control_id': user.GetDlgCtrlID(handle),
                                     'label': caption, 'visible': bool(user.IsWindowVisible(handle)),
                                     'enabled': bool(user.IsWindowEnabled(handle))})
            return True
        user.EnumChildWindows(root['handle'], child, 0)
    return {'schema': 1, 'kind': 'read-only-control-inspection', 'windows': roots,
            'note': '입력값·비밀번호·재고 표 내용은 수집하지 않습니다. 자동 조작은 실행하지 않았습니다.'}


def require_interactive_desktop():
    import ctypes
    from ctypes import wintypes
    api = ctypes.WinDLL('user32', use_last_error=True)
    api.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    api.OpenInputDesktop.restype = wintypes.HANDLE
    api.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                            wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    api.CloseDesktop.argtypes = [wintypes.HANDLE]
    handle = api.OpenInputDesktop(0, False, 1)
    if not handle:
        raise RuntimeError('Windows 잠금을 해제하고 직접 로그인한 화면에서 실행하세요.')
    try:
        name, needed = ctypes.create_unicode_buffer(256), wintypes.DWORD()
        if not api.GetUserObjectInformationW(handle, 2, name, ctypes.sizeof(name), ctypes.byref(needed)) or name.value != 'Default':
            raise RuntimeError('Windows 잠금을 해제하고 직접 로그인한 화면에서 실행하세요.')
    finally:
        api.CloseDesktop(handle)


class ActasExporter:
    def __init__(self, profile_path):
        if os.name != 'nt':
            raise RuntimeError('Actas 화면 수집은 Windows에서만 실행합니다.')
        self.profile = json.loads(Path(profile_path).read_text(encoding='utf-8-sig'))
        if self.profile.get('schema') != 1 or self.profile.get('verified_on_this_pc') is not True:
            raise RuntimeError('이 PC의 Actas 화면 제어 항목을 먼저 확인해야 합니다.')
        if self.profile.get('computer_name') != os.environ.get('COMPUTERNAME'):
            raise RuntimeError('다른 PC의 Actas 화면 설정은 사용하지 않습니다.')
        self.app = None

    @staticmethod
    def control(parent, selector):
        allowed = {'title', 'title_re', 'class_name', 'control_id', 'auto_id', 'control_type'}
        if not isinstance(selector, dict) or not selector or set(selector) - allowed:
            raise RuntimeError('확인된 화면 제어 항목만 사용할 수 있습니다.')
        candidate = parent.child_window(**selector)
        candidate.wait('exists visible', timeout=10)
        return candidate.wrapper_object()  # Ambiguity fails; no positional fallback.

    def scope(self, action):
        if action['scope'] == 'main':
            return self.main
        if action['scope'] == 'stock':
            return self.stock
        raise RuntimeError('Actas 조작 범위를 확인하세요.')

    def click(self, action):
        require_interactive_desktop()
        control = self.control(self.scope(action), action['selector'])
        if not control.is_enabled():
            raise RuntimeError('Actas 조회·추출 버튼이 활성 상태가 아닙니다.')
        if 'toolbar_button' in action:
            control = control.button(action['toolbar_button'])
        control.click_input()

    def export(self, warehouse, target):
        from pywinauto import Application, Desktop
        if warehouse not in ('Y02', 'Y09') or target.exists():
            raise RuntimeError('새 Y02/Y09 파일 경로만 추출할 수 있습니다.')
        require_interactive_desktop()
        p = self.profile
        self.app = Application(backend=p.get('backend', 'win32')).connect(**p['main_window'])
        self.main = self.app.window(**p['main_window'])
        self.main.wait('visible enabled', timeout=10)
        if 'actas' not in self.main.window_text().lower():
            raise RuntimeError('Actas 프로그램 창인지 확인하지 못했습니다.')
        self.stock = self.main.child_window(**p['stock_window'])
        self.stock.wait('visible enabled', timeout=10)
        self.main.set_focus()
        edit = self.control(self.stock, p['warehouse_edit'])
        edit.set_edit_text(warehouse)
        if edit.window_text().strip() != warehouse:
            raise RuntimeError('Actas 창고 입력값을 확인하지 못했습니다.')
        # Require an observed BUSY -> READY transition, not sleep/CPU-idle alone.
        # If a custom grid exposes no completion marker, profile verification
        # must stop here until a reliable adapter is built on the actual PC.
        completion = p['completion']
        busy, ready = re.compile(completion['busy_text_re']), re.compile(completion['ready_text_re'])
        if busy.pattern == ready.pattern:
            raise RuntimeError('조회 시작/완료 표시가 구분되지 않습니다.')
        self.click(p['query_action'])
        deadline, observed_busy = time.monotonic() + 90, False
        while time.monotonic() < deadline:
            require_interactive_desktop()
            status = self.control(self.scope(completion), completion['selector']).window_text()
            if busy.fullmatch(status):
                observed_busy = True
            elif observed_busy and ready.fullmatch(status):
                break
            time.sleep(.1)
        else:
            raise RuntimeError('Actas 조회 완료 상태 전환을 확인하지 못했습니다. 추출하지 않습니다.')
        if edit.window_text().strip() != warehouse:
            raise RuntimeError('조회 중 창고 선택이 바뀌었습니다.')
        queried_at = __import__('inventory.actas_collection', fromlist=['utc_now']).utc_now()
        self.click(p['export_action'])
        dialog = Desktop(backend=p.get('backend', 'win32')).window(process=self.app.process, **p['save_dialog'])
        dialog.wait('visible enabled', timeout=15)
        name = self.control(dialog, p['filename_edit'])
        name.set_edit_text(str(target.resolve()))
        if name.window_text() != str(target.resolve()):
            raise RuntimeError('Excel 저장 경로를 확인하지 못했습니다.')
        require_interactive_desktop()
        self.control(dialog, p['save_button']).click_input()
        dialog.wait_not('visible', timeout=30)
        return {'warehouse': warehouse, 'query_complete': True, 'queried_at': queried_at,
                'completion': 'observed_busy_to_ready'}
