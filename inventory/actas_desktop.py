"""Local desktop button, never exposed as an unauthenticated web endpoint."""
import json
from pathlib import Path
import queue
import threading
import uuid


def show_collector(*, root_folder, profile):
    import tkinter as tk
    from tkinter import messagebox, ttk
    from django.contrib.auth import authenticate
    from django.db import close_old_connections
    from .actas_collection import collect, register_collected, write_record
    from .actas_windows import ActasExporter, inspect_windows
    from .menu_permissions import require_menu_write
    root_folder, profile = Path(root_folder), Path(profile)
    root_folder.mkdir(parents=True, exist_ok=True)
    window = tk.Tk()
    window.title('SUNIL · Actas 재고 수집 (검증용)')
    window.geometry('650x410')
    frame = ttk.Frame(window, padding=20)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='Y02 영주본사 + Y09 부산창고 → ERP 대조 자료', font=('', 13, 'bold')).pack(anchor='w')
    ttk.Label(frame, text='Actas 로그인 후 현재고 현황을 열어 두세요. 실행 중 마우스·키보드 조작을 멈춰 주세요.').pack(anchor='w', pady=10)
    ttk.Label(frame, text='SUNIL ERP 계정 (관리: 모두 가능)').pack(anchor='w')
    username = ttk.Entry(frame)
    username.pack(fill='x')
    ttk.Label(frame, text='SUNIL ERP 비밀번호 · 저장하지 않습니다').pack(anchor='w', pady=(8, 0))
    password = ttk.Entry(frame, show='●')
    password.pack(fill='x')
    status = tk.StringVar(value='화면 제어 항목을 먼저 진단·확인해야 합니다. 정기 실행은 하지 않습니다.')
    ttk.Label(frame, textvariable=status, wraplength=600).pack(anchor='w', pady=12)
    messages, active = queue.Queue(), [False]

    def diagnostic():
        try:
            data = inspect_windows()
            path = root_folder / ('controls-' + uuid.uuid4().hex + '.json')
            write_record(path, data)
            status.set('진단 완료. Actas와 DB는 변경하지 않았습니다.\n' + str(path))
        except Exception as error:
            messagebox.showerror('진단 중단', str(error))

    def work(name, secret):
        close_old_connections()
        try:
            actor = authenticate(username=name, password=secret)
            secret = None
            if actor is None:
                raise RuntimeError('SUNIL ERP 계정·비밀번호를 확인하세요.')
            require_menu_write(actor, 'management')
            pending = []
            for path in root_folder.glob('*/run.json'):
                record = json.loads(path.read_text(encoding='utf-8'))
                if record.get('actor_id') == actor.pk and record.get('phase') in ('validated', 'registering'):
                    pending.append(path.parent)
            if len(pending) > 1:
                raise RuntimeError('미완료 등록이 여러 개입니다. 기록을 확인한 후 진행하세요.')
            if pending:
                messages.put(('status', '이전 요청번호로 등록 결과를 재확인합니다. 새 수집은 하지 않습니다.'))
                result = register_collected(pending[0], actor=actor)
            else:
                exporter = ActasExporter(profile)
                result = collect(root_folder, exporter, actor=actor,
                                 progress=lambda value: messages.put(('status', value)))
            messages.put(('done', f"완료 · Y02 {result['counts']['Y02']}품목 / Y09 {result['counts']['Y09']}품목\n"
                          f"ERP 대조 번호: {result['snapshot_id']}\n기록: {result['run']}"))
        except Exception as error:
            # SQL/driver errors can include connection details; never show raw
            # unexpected exception text or persist credentials in the journal.
            from django.core.exceptions import ValidationError
            message = str(error) if isinstance(error, (RuntimeError, ValidationError, FileNotFoundError)) else type(error).__name__
            messages.put(('error', '수집·등록 중단: ' + message))
        finally:
            secret = None
            close_old_connections()

    def start():
        if active[0]:
            return
        name, secret = username.get().strip(), password.get()
        if not name or not secret:
            messagebox.showerror('계정 확인', 'SUNIL ERP 계정과 비밀번호를 입력하세요.')
            return
        password.delete(0, 'end')
        active[0] = True
        run_button.configure(state='disabled')
        diagnostic_button.configure(state='disabled')
        status.set('권한 확인 중 · 실제 재고는 변경하지 않습니다.')
        threading.Thread(target=work, args=(name, secret), daemon=False).start()

    def poll():
        while not messages.empty():
            kind, message = messages.get_nowait()
            status.set(message)
            if kind != 'status':
                active[0] = False
                run_button.configure(state='normal')
                diagnostic_button.configure(state='normal')
        window.after(150, poll)

    def close():
        if active[0]:
            messagebox.showinfo('진행 중', '수집·등록이 끝날 때까지 기다려 주세요.')
        else:
            window.destroy()
    diagnostic_button = ttk.Button(frame, text='화면 진단만 저장', command=diagnostic)
    diagnostic_button.pack(fill='x', pady=4)
    run_button = ttk.Button(frame, text='두 창고 수집 · 대조 자료 등록 / 미완료 등록 재확인', command=start)
    run_button.pack(fill='x', pady=4)
    window.protocol('WM_DELETE_WINDOW', close)
    window.after(150, poll)
    window.mainloop()
