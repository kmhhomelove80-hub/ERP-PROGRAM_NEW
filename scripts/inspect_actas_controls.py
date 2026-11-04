"""Actas read-only diagnostic v2. No installation, DB/config access or UI actions.
Only fixed labels and control metadata are emitted. No Edit/password/grid values.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import time

def is_actas_root(title, class_name):
    # A directory/browser title containing "Actas" is NOT the ERP process.
    return bool(re.match(r'^FNWND\d+$', class_name, re.I) and
                re.match(r'^Actas\s*ERP(?:\b|_)', title.strip(), re.I))

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
    user.GetForegroundWindow.restype = wintypes.HWND
    class GuiThreadInfo(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('flags', wintypes.DWORD),
                    ('active', wintypes.HWND), ('focus', wintypes.HWND), ('capture', wintypes.HWND),
                    ('menu_owner', wintypes.HWND), ('move_size', wintypes.HWND),
                    ('caret', wintypes.HWND), ('caret_rect', wintypes.RECT)]
    user.GetGUIThreadInfo.argtypes = [wintypes.DWORD, ctypes.POINTER(GuiThreadInfo)]
    user.GetMenu.argtypes = [wintypes.HWND]
    user.GetMenu.restype = wintypes.HMENU
    user.GetSubMenu.argtypes = [wintypes.HMENU, ctypes.c_int]
    user.GetSubMenu.restype = wintypes.HMENU
    user.GetMenuItemCount.argtypes = [wintypes.HMENU]
    user.GetMenuItemID.argtypes = [wintypes.HMENU, ctypes.c_int]
    user.GetMenuItemID.restype = wintypes.UINT
    user.GetMenuStringW.argtypes = [wintypes.HMENU, wintypes.UINT, wintypes.LPWSTR, ctypes.c_int, wintypes.UINT]
    roots, visible_windows = [], []

    def text(handle, kind='title'):
        buffer = ctypes.create_unicode_buffer(256)
        (user.GetClassNameW if kind == 'class' else user.GetWindowTextW)(handle, buffer, 256)
        return buffer.value

    @callback
    def visit(handle, unused):
        title = text(handle)
        if user.IsWindowVisible(handle):
            pid = wintypes.DWORD()
            user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
            visible_windows.append((handle, pid.value, is_actas_root(title, text(handle, 'class'))))
        return True
    user.EnumWindows(visit, 0)
    actas_pids = {pid for handle, pid, matched in visible_windows if matched}
    # Save As is an owned TOP-LEVEL window, not a descendant of the main
    # window. Include the same process's dialogs without exposing their titles.
    for handle, pid, matched in visible_windows:
        if pid in actas_pids:
            roots.append({'handle': int(handle), 'process_id': pid,
                          'owner': int(user.GetParent(handle) or 0),
                          'class_name': text(handle, 'class'), 'controls': []})
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
        tid = user.GetWindowThreadProcessId(root['handle'], None)
        state = GuiThreadInfo()
        state.cbSize = ctypes.sizeof(state)
        root['foreground'] = user.GetForegroundWindow() == root['handle']
        if user.GetGUIThreadInfo(tid, ctypes.byref(state)):
            root['focus'] = {'handle': int(state.focus or 0), 'class_name': text(state.focus, 'class') if state.focus else '',
                             'control_id': user.GetDlgCtrlID(state.focus) if state.focus else 0,
                             'caret_handle': int(state.caret or 0)}
        entries = []
        def menu_items(menu, path=(), depth=0):
            if not menu or depth > 8 or len(entries) >= 500:
                return
            for index in range(max(0, min(user.GetMenuItemCount(menu), 100))):
                buffer = ctypes.create_unicode_buffer(128)
                user.GetMenuStringW(menu, index, buffer, 128, 0x400)
                caption = buffer.value.replace('&', '').split('\t')[0].strip()
                # Never export recent-file paths or other application contents.
                if re.fullmatch(r'(조회|엑셀|Excel|창고|저장|파일|내보내기)(\([A-Za-z0-9]+\))?', caption, re.I):
                    entries.append({'path': list(path + (index,)), 'label': caption,
                                    'command_id': user.GetMenuItemID(menu, index)})
                menu_items(user.GetSubMenu(menu, index), path + (index,), depth + 1)
        menu_items(user.GetMenu(root['handle']))
        root['menus'] = entries
    return {'schema': 2, 'kind': 'read-only-control-inspection', 'windows': roots,
            'note': '입력값·비밀번호·재고 표 내용은 수집하지 않습니다. 자동 조작은 실행하지 않았습니다.'}

ACCESSIBILITY_COMMAND = r"""
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$inputData=$env:SUNIL_ACTAS_INSPECTION|ConvertFrom-Json
$walker=[System.Windows.Automation.TreeWalker]::RawViewWalker
$clock=[Diagnostics.Stopwatch]::StartNew()
$rows=@(foreach($root in $inputData.windows){
  $nodes=New-Object Collections.ArrayList
  try {
    $element=[System.Windows.Automation.AutomationElement]::FromHandle([intptr][long]$root.handle)
    if($element.Current.ProcessId -ne $root.process_id){throw 'Process changed'}
    $queue=New-Object Collections.Queue
    $queue.Enqueue(@($element,0,-1))
    while($queue.Count -gt 0 -and $nodes.Count -lt 600 -and $clock.Elapsed.TotalSeconds -lt 20){
      $work=$queue.Dequeue(); $item=$work[0]; $depth=[int]$work[1]; $parentIndex=[int]$work[2]
      try {
        $current=$item.Current
        if($current.ProcessId -ne $root.process_id){continue}
        $kind=$current.ControlType.ProgrammaticName
        $label=''
        if(-not $current.IsPassword -and $kind -in @('ControlType.Button','ControlType.MenuItem','ControlType.Text')){
          $caption=$current.Name.Replace('&','').Trim()
          if($caption -match '^(조회|엑셀|Excel|창고|현재고\s*현황|저장|취소|파일\s*이름)(\([A-Za-z0-9]+\))?$'){$label=$caption}
        }
        $identity=''
        if($kind -ne 'ControlType.DataItem' -and $current.AutomationId -match '^[A-Za-z_][A-Za-z0-9_.-]{0,80}$|^[0-9.]{1,40}$'){$identity=$current.AutomationId}
        $patterns=@($item.GetSupportedPatterns()|ForEach-Object{$_.ProgrammaticName})
        $index=$nodes.Count
        [void]$nodes.Add([pscustomobject]@{parent=$parentIndex;type=$kind;class_name=$current.ClassName;automation_id=$identity;label=$label;password=[bool]$current.IsPassword;enabled=[bool]$current.IsEnabled;offscreen=[bool]$current.IsOffscreen;patterns=$patterns})
        if($depth -lt 12){
          $child=$walker.GetFirstChild($item); $siblings=0
          while($null -ne $child -and $siblings -lt 200 -and $queue.Count -lt 1200){
            $queue.Enqueue(@($child,($depth+1),$index)); $child=$walker.GetNextSibling($child);$siblings++
          }
        }
      } catch { }
    }
    [pscustomobject]@{handle=$root.handle;nodes=@($nodes.ToArray());bounded=($queue.Count -gt 0)}
  } catch {[pscustomobject]@{handle=$root.handle;nodes=@();status='unavailable'}}
})
@{status='complete';windows=$rows}|ConvertTo-Json -Depth 9 -Compress
"""

def inspect_accessibility(report, timeout=30):
    """Read metadata with the existing Windows .NET 4 UIAutomation libraries.

    Never invokes patterns, reads Value/Text patterns, selects, or changes focus.
    The reader has its own timeout and cannot terminate Actas.
    """
    import subprocess
    if os.name != 'nt':
        raise RuntimeError('Windows 읽기 전용 진단입니다.')
    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    env = os.environ.copy()
    env['PSModulePath'] = str(powershell.parent / 'Modules')
    env['SUNIL_ACTAS_INSPECTION'] = json.dumps({'windows': [
        {'handle': w['handle'], 'process_id': w['process_id']} for w in report['windows']]})
    try:
        result = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-Command', ACCESSIBILITY_COMMAND],
                                capture_output=True, encoding='utf-8', errors='replace', timeout=timeout,
                                creationflags=subprocess.CREATE_NO_WINDOW, env=env)
        if result.returncode:
            return {'status': 'unavailable', 'windows': []}
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired:
        return {'status': 'reader_timeout', 'windows': []}
    except (OSError, ValueError):
        return {'status': 'unavailable', 'windows': []}

if __name__ == '__main__':
    # Korean output also works when stdout is redirected on non-Korean Windows.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')
    p = argparse.ArgumentParser(description='Actas 읽기 전용 상세 진단 v2')
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--delay', type=int, default=12)
    args = p.parse_args()
    try:
        if not 0 <= args.delay <= 60:
            raise RuntimeError('대기 시간은 0~60초로 지정하세요.')
        if args.output.exists():
            raise RuntimeError('기존 진단 파일은 덮어쓰지 않습니다. 새 파일명을 사용하세요.')
        if not args.output.parent.is_dir():
            raise RuntimeError('결과를 저장할 폴더가 없습니다.')
        print(f'{args.delay}초 안에 Actas로 돌아가 창고 코드 칸을 클릭하고 기다려 주세요.', flush=True)
        time.sleep(args.delay)
        report = inspect_windows()
        report['diagnostic_version'] = 2
        report['accessibility'] = inspect_accessibility(report)
        with args.output.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
        print('진단 완료. 자동 클릭·입력·DB 연결·프로그램 설정 변경은 하지 않았습니다.')
        print('Actas 관련 창 수:', len(report['windows']))
        print('접근성 확인 상태:', report['accessibility']['status'])
        print('결과 파일:', args.output)
    except Exception as error:
        print('진단 중단:', str(error))
        raise SystemExit(1)
