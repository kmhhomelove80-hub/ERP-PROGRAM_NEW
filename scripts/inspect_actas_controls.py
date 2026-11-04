"""Standalone READ-ONLY Actas control inspection, no installation/config/DB access.

Run with the existing Python, with Actas current-stock window open. Output omits
Edit values, password values and inventory grid contents. No input is sent.
"""
import json
import os
from pathlib import Path
import argparse

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

if __name__ == '__main__':
    p = argparse.ArgumentParser(description='Actas 화면 구조 읽기 전용 진단')
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    try:
        report = inspect_windows()
        if args.output.exists():
            raise RuntimeError('기존 진단 파일은 덮어쓰지 않습니다. 다른 파일명을 사용하세요.')
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
        print('진단 완료. Actas 조작·DB 연결·프로그램 설정 변경은 하지 않았습니다.')
        print('발견한 Actas 창 수:', len(report['windows']))
        print('결과 파일:', args.output)
    except Exception as error:
        print('진단 중단:', str(error))
        raise SystemExit(1)
