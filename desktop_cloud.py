"""Native first-run GitHub connection/clone dialog (no existing blog needed)."""
from __future__ import annotations

from pathlib import Path
import queue
import threading
import webbrowser

from cloud_services import CloudService
from runtime import external_program_environment


def open_cloud_dialog(parent, on_cloned, service=None):
    import tkinter as tk
    from tkinter import filedialog, ttk

    cloud = service or CloudService()
    window = tk.Toplevel(parent)
    window.title('GitHub에서 블로그 가져오기')
    window.transient(parent)
    window.geometry('680x650')
    window.minsize(600, 580)
    window.configure(background='#F5F4F0')
    frame = ttk.Frame(window, padding=24)
    frame.pack(fill='both', expand=True)
    frame.columnconfigure(0, weight=1)
    messages = queue.Queue()
    status = tk.StringVar(value='GitHub에 연결하고 새 블로그 폴더를 가져오세요.')
    code = tk.StringVar(value='')
    token = tk.StringVar()
    repository = tk.StringVar()
    parent_folder = tk.StringVar(value=str(Path.home()))
    busy = False
    controls = []

    def close():
        if busy:
            status.set('진행 중인 작업을 마친 뒤 닫을 수 있어요. 잠시 기다려 주세요.')
            return
        if cloud.login_status().get('running'):
            cloud.disconnect()
        window.destroy()
    window.protocol('WM_DELETE_WINDOW', close)

    ttk.Label(frame, text='GitHub에서 블로그 가져오기', font=('TkDefaultFont', 16, 'bold')).grid(row=0, column=0, sticky='w', pady=(0, 12))
    ttk.Label(frame, text='브라우저 로그인 또는 토큰 연결을 사용하세요.\n토큰은 이 앱을 실행하는 동안에만 보관합니다.', justify='left').grid(row=1, column=0, sticky='w', pady=(0, 12))

    def launch_url(url):
        with external_program_environment():
            webbrowser.open(url)

    def run(callback, success=None):
        nonlocal busy
        if busy:
            return
        busy = True
        for button in controls:
            button.configure(state='disabled')
        def worker():
            try:
                value = callback()
                messages.put(('result', (value, success)))
            except Exception as error:
                messages.put(('error', str(error)))
        threading.Thread(target=worker, name='BlogStudioClone', daemon=True).start()

    row = ttk.Frame(frame)
    row.grid(row=2, column=0, sticky='ew', pady=(0, 8))
    login = ttk.Button(row, text='브라우저로 로그인', command=lambda: run(cloud.start_login))
    login.pack(side='left')
    controls.append(login)
    ttk.Button(row, text='인증 페이지 열기', command=lambda: launch_url('https://github.com/login/device')).pack(side='left', padx=8)
    ttk.Label(frame, textvariable=code, font=('TkDefaultFont', 16, 'bold')).grid(row=3, column=0, sticky='w')
    ttk.Label(frame, text='개인 액세스 토큰 (브라우저 로그인 대신 사용 가능)').grid(row=4, column=0, sticky='w', pady=(10, 4))
    secret = ttk.Entry(frame, textvariable=token, show='•')
    secret.grid(row=5, column=0, sticky='ew')
    def connect():
        value = token.get()
        token.set('')
        run(lambda: cloud.connect_token({'token': value}))
    controls.append(ttk.Button(frame, text='토큰으로 연결', command=connect))
    controls[-1].grid(row=6, column=0, sticky='w', pady=8)
    links = ttk.Frame(frame)
    links.grid(row=7, column=0, sticky='w')
    for title, url in [('토큰 만들기', 'https://github.com/settings/personal-access-tokens'),
                       ('Git 설치', 'https://git-scm.com/downloads'), ('GitHub CLI 설치', 'https://cli.github.com/')]:
        ttk.Button(links, text=title, command=lambda url=url: launch_url(url)).pack(side='left', padx=(0, 8))
    ttk.Label(frame, text='저장소 (예: lococaeco/lococaeco.github.io)').grid(row=8, column=0, sticky='w', pady=(18, 4))
    ttk.Entry(frame, textvariable=repository).grid(row=9, column=0, sticky='ew')
    ttk.Label(frame, text='저장할 상위 폴더 — 저장소 이름으로 새 폴더를 만듭니다.').grid(row=10, column=0, sticky='w', pady=(12, 4))
    location = ttk.Frame(frame)
    location.grid(row=11, column=0, sticky='ew')
    location.columnconfigure(0, weight=1)
    ttk.Entry(location, textvariable=parent_folder).grid(row=0, column=0, sticky='ew')
    def choose():
        folder = filedialog.askdirectory(parent=window, title='블로그를 저장할 상위 폴더 선택', initialdir=parent_folder.get())
        if folder:
            parent_folder.set(folder)
    ttk.Button(location, text='폴더 선택', command=choose).grid(row=0, column=1, padx=(8, 0))
    def clone():
        from cloud_services import repository_name
        try:
            name = repository_name(repository.get())
        except Exception as error:
            status.set(str(error))
            return
        destination = Path(parent_folder.get()) / name.split('/')[-1]
        status.set('GitHub에서 블로그를 가져오고 있어요…')
        def complete(result):
            if result.get('is_blog'):
                on_cloned(result['path'], cloud)
                window.destroy()
        run(lambda: cloud.clone({'repository': name, 'destination': str(destination)}), complete)
    controls.append(ttk.Button(frame, text='새 폴더로 가져오기', command=clone))
    controls[-1].grid(row=12, column=0, sticky='ew', pady=(18, 10))
    ttk.Label(frame, textvariable=status, wraplength=580, justify='left').grid(row=13, column=0, sticky='ew')
    ttk.Button(frame, text='닫기', command=close).grid(row=14, column=0, sticky='e', pady=(14, 0))

    style = ttk.Style(window)
    style.configure('Cloud.TFrame', background='#F5F4F0')
    style.configure('Cloud.TLabel', background='#F5F4F0', foreground='#26342E')
    style.configure('Cloud.TEntry', padding=7, fieldbackground='#FFFFFF', foreground='#26342E')
    style.configure('Cloud.TButton', padding=(10, 6), background='#FFFFFF', foreground='#26342E',
                    bordercolor='#DDE3DE', lightcolor='#FFFFFF', darkcolor='#FFFFFF')
    style.map('Cloud.TButton', background=[('active', '#E7F2EF')])

    def theme(widget):
        kind = widget.winfo_class()
        if kind in ('TFrame', 'TLabel', 'TEntry', 'TButton'):
            widget.configure(style='Cloud.' + kind)
        for child in widget.winfo_children():
            theme(child)
    theme(frame)
    window.update_idletasks()
    width, height = max(680, window.winfo_reqwidth()), max(650, window.winfo_reqheight())
    window.minsize(width, height)
    window.geometry(f'{width}x{height}')

    def poll():
        nonlocal busy
        if not window.winfo_exists():
            return
        try:
            while True:
                kind, value = messages.get_nowait()
                busy = False
                for button in controls:
                    button.configure(state='normal')
                if kind == 'error':
                    status.set(value)
                else:
                    result, success = value
                    status.set(result.get('message', '완료했습니다.'))
                    if success:
                        success(result)
                        if not window.winfo_exists():
                            return
        except queue.Empty:
            pass
        login_status = cloud.login_status()
        code.set(login_status.get('code', ''))
        if login_status.get('running'):
            status.set(login_status.get('message', 'GitHub에서 로그인을 완료해 주세요.'))
        window.after(150, poll)
    window.after(150, poll)
    window.grab_set()
    return window
