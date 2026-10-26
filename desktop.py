#!/usr/bin/env python3
"""Small desktop entry point for the source tree and the standalone executable.

The bundle contains application assets only. Every post and draft stays inside
the real repository selected by the user, never in PyInstaller's extraction dir.
"""
from __future__ import annotations

import argparse
import errno
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import signal
import sys
import tempfile
import threading
import time
import webbrowser


DEFAULT_PORT = 4310
LOGGER = logging.getLogger('blog_studio.desktop')


class LauncherError(Exception):
    """An actionable error that can be displayed without a console."""


def config_directory():
    """Use the OS user configuration location, independent of the executable."""
    if sys.platform == 'win32':
        base = os.environ.get('LOCALAPPDATA') or os.environ.get('APPDATA')
        return (Path(base) if base else Path.home() / 'AppData' / 'Local') / 'Blog Studio'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'Blog Studio'
    base = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config')))
    if not base.is_absolute():
        base = Path.home() / '.config'
    return base / 'blog-studio'


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.studio-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_preferences(path=None):
    path = Path(path) if path is not None else config_directory() / 'settings.json'
    try:
        result = json.loads(path.read_text('utf-8'))
        return result if isinstance(result, dict) else {}
    except (OSError, ValueError):
        return {}


def save_preferences(repo, port, path=None):
    path = Path(path) if path is not None else config_directory() / 'settings.json'
    data = load_preferences(path)
    ports = data.get('ports')
    ports = dict(ports) if isinstance(ports, dict) else {}
    ports[str(Path(repo).resolve())] = port
    atomic_json(path, {'last_repo': str(Path(repo).resolve()), 'ports': ports})


def validate_repository(value):
    if not value:
        raise LauncherError('블로그 폴더를 선택해 주세요. _config.yml과 _posts가 있는 폴더가 필요합니다.')
    repo = Path(value).expanduser().resolve()
    extraction = getattr(sys, '_MEIPASS', None)
    if extraction and repo.is_relative_to(Path(extraction).resolve()):
        raise LauncherError('앱의 임시 실행 폴더에는 글을 저장할 수 없습니다. 실제 블로그 폴더를 선택해 주세요.')
    if not (repo / '_config.yml').is_file() or not (repo / '_posts').is_dir():
        raise LauncherError('Jekyll 블로그 폴더가 아닙니다. _config.yml과 _posts가 있는 폴더를 선택해 주세요.')
    return repo


def discover_repository(preferences=None, *, executable=None, cwd=None, frozen=None):
    """Try remembered and nearby folders, independently of the source layout."""
    preferences = preferences or {}
    frozen = bool(getattr(sys, 'frozen', False)) if frozen is None else frozen
    candidates = []
    remembered = preferences.get('last_repo')
    if isinstance(remembered, str):
        candidates.append(Path(remembered))
    if frozen:
        executable = Path(executable or sys.executable).resolve()
        candidates.extend([executable.parent, executable.parent.parent])
    current = Path(cwd or Path.cwd()).resolve()
    candidates.extend([current, current.parent])
    seen = set()
    for candidate in candidates:
        candidate = candidate.expanduser().resolve()
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            return validate_repository(candidate)
        except (LauncherError, OSError, RuntimeError):
            continue
    return None


def preferred_port(repo, explicit=None, preferences=None):
    if explicit is not None:
        return explicit
    ports = (preferences or {}).get('ports', {})
    port = ports.get(str(Path(repo).resolve())) if isinstance(ports, dict) else None
    return port if type(port) is int and 1 <= port <= 65535 else DEFAULT_PORT


class LogStream:
    """Keep server print/error messages available in a Windows windowed build."""
    encoding = 'utf-8'

    def __init__(self, level):
        self.level = level

    def write(self, message):
        if message and message.strip():
            LOGGER.log(self.level, message.rstrip())
        return len(message or '')

    def flush(self):
        pass

    def isatty(self):
        return False


def configure_logging():
    directory = config_directory()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / 'desktop.log'
    LOGGER.setLevel(logging.INFO)
    LOGGER.propagate = False
    if not LOGGER.handlers:
        handler = RotatingFileHandler(path, maxBytes=1024 * 1024, backupCount=2, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        LOGGER.addHandler(handler)
    if sys.stdout is None:
        sys.stdout = LogStream(logging.INFO)
    if sys.stderr is None:
        sys.stderr = LogStream(logging.ERROR)
    # A windowed Windows executable may inherit a cp1252 pipe even when the
    # source interpreter honors PYTHONUTF8. Keep diagnostics safe for paths
    # containing Korean characters; custom/captured streams may not support it.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if callable(reconfigure):
            try:
                reconfigure(encoding='utf-8', errors='backslashreplace')
            except (OSError, ValueError, TypeError):
                pass
    return path


def open_browser(url):
    # runtime restores the host's loader environment before launching browsers
    # from a frozen bundle. It is shared with the Git subprocess implementation.
    from runtime import external_program_environment
    with external_program_environment():
        return webbrowser.open(url, new=2)


class StudioProcess:
    """Run the existing HTTP server in this process, with a controlled lifetime."""

    def __init__(self):
        self.server = None
        self.thread = None
        self.repo = None
        self.url = None
        self.error = None
        self.port_changed = False

    @property
    def running(self):
        return bool(self.thread and self.thread.is_alive())

    def start(self, repo, port=DEFAULT_PORT):
        if self.server is not None:
            raise LauncherError('이미 실행 중입니다. 먼저 현재 글쓰기 창을 종료해 주세요.')
        repo = validate_repository(repo)
        if type(port) is not int or not 0 <= port <= 65535:
            raise LauncherError('포트는 0부터 65535 사이의 숫자여야 합니다.')
        import server as backend
        self.error = None
        self.port_changed = False
        try:
            httpd = backend.create_server(repo, port)
        except OSError as error:
            # Windows reports an exclusive-listener collision as WSAEACCES
            # (10013) on some versions, and WSAEADDRINUSE (10048) on others.
            if not port or error.errno not in (errno.EADDRINUSE, 10048, 10013):
                raise
            # Do not open an existing service, which may belong to another repo.
            httpd = backend.create_server(repo, 0)
            self.port_changed = True
        self.server = httpd
        self.repo = repo
        self.url = f'http://127.0.0.1:{httpd.server_port}'

        def serve():
            try:
                httpd.serve_forever(poll_interval=0.2)
            except Exception as error:
                self.error = error
                LOGGER.exception('HTTP server stopped unexpectedly')

        self.thread = threading.Thread(target=serve, name='BlogStudioHTTP', daemon=True)
        try:
            self.thread.start()
        except Exception:
            httpd.server_close()
            self.server = None
            raise
        LOGGER.info('Started %s for %s', self.url, self.repo)
        return self.url

    def stop(self):
        if self.server is None:
            return
        httpd, thread = self.server, self.thread
        try:
            if thread and thread.is_alive():
                httpd.shutdown()
            # The shared server waits for current writes before releasing its
            # repository lock. GUI callers perform this wait off the Tk thread.
            httpd.server_close()
            if thread:
                thread.join()
        finally:
            self.server = None
            self.thread = None
            LOGGER.info('Stopped server for %s', self.repo)


def write_ready_file(path, process):
    if path is not None:
        atomic_json(path, {'url': process.url, 'repo': str(process.repo)})


def remove_ready_file(path, process):
    if path is None:
        return
    try:
        path = Path(path)
        if json.loads(path.read_text('utf-8')) == {'url': process.url, 'repo': str(process.repo)}:
            path.unlink()
    except (OSError, ValueError):
        pass


def remember_process(process):
    try:
        save_preferences(process.repo, process.server.server_port)
    except OSError:
        # A read-only profile must not prevent writing to the selected blog.
        LOGGER.exception('Could not remember the selected repository')


def run_headless(args):
    preferences = load_preferences()
    repo = args.repo or discover_repository(preferences)
    process = StudioProcess()
    stop = threading.Event()
    previous = {}
    for name in ('SIGINT', 'SIGTERM'):
        kind = getattr(signal, name, None)
        if kind is not None:
            previous[kind] = signal.signal(kind, lambda *_: stop.set())
    try:
        repo = validate_repository(repo)
        process.start(repo, preferred_port(repo, args.port, preferences))
        remember_process(process)
        write_ready_file(args.ready_file, process)
        # Machine-readable output must also work with non-reconfigurable pipes.
        print(json.dumps({'url': process.url, 'repo': str(process.repo)}, ensure_ascii=True), flush=True)
        if not args.no_browser:
            try:
                if not open_browser(process.url):
                    LOGGER.warning('No browser opened; visit %s manually', process.url)
            except Exception:
                LOGGER.exception('Could not open the browser')
        deadline = time.monotonic() + args.exit_after if args.exit_after is not None else None
        while not stop.wait(0.1):
            if not process.running:
                raise LauncherError('글쓰기 서버가 종료되었습니다. desktop.log를 확인해 주세요.')
            if deadline is not None and time.monotonic() >= deadline:
                break
        return 0
    finally:
        process.stop()
        remove_ready_file(args.ready_file, process)
        for kind, handler in previous.items():
            signal.signal(kind, handler)


def check_gui():
    import tkinter as tk
    window = tk.Tk()
    try:
        window.withdraw()
        window.update()
    finally:
        window.destroy()
    print('Blog Studio: desktop window is available.', flush=True)
    return 0


class DesktopWindow:
    def __init__(self, window, args, log_path):
        import tkinter as tk
        from tkinter import filedialog, font, messagebox, ttk
        self.window, self.args, self.log_path = window, args, log_path
        self.filedialog, self.messagebox = filedialog, messagebox
        self.process = StudioProcess()
        self.events = queue.Queue()
        self.busy = False
        self.closing = False
        self.preferences = load_preferences()
        repo = args.repo or discover_repository(self.preferences)
        self.repo = tk.StringVar(value=str(repo) if repo else '')
        self.status = tk.StringVar(value='폴더를 선택하고 작성 시작을 눌러 주세요.')
        self.phase = tk.StringVar(value='준비됨')
        self.address = tk.StringVar(value='')
        self.colors = {
            'paper': '#F5F4F0', 'surface': '#FFFFFF', 'ink': '#202826',
            'muted': '#66716C', 'line': '#DDE3DE', 'teal': '#187B71',
            'teal_dark': '#116358', 'tint': '#E7F2EF', 'error': '#A3433E',
        }
        c = self.colors
        families = set(font.families(window))
        preferred_fonts = ('Malgun Gothic', '맑은 고딕', 'Apple SD Gothic Neo',
                           'Noto Sans CJK KR', 'Noto Sans KR', 'NanumGothic')
        family = next((name for name in preferred_fonts if name in families),
                      font.nametofont('TkDefaultFont').actual('family'))
        self.font_family = family
        regular, small, bold = (family, 11), (family, 10), (family, 11, 'bold')
        window.title('Blog Studio')
        window.geometry('860x640')
        window.minsize(740, 620)
        window.configure(background=c['paper'])
        bundle = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent))
        for icon_path in (bundle / 'icon.png', Path(__file__).resolve().parent / 'packaging' / 'icon.png'):
            if icon_path.is_file():
                try:
                    self.icon = tk.PhotoImage(file=str(icon_path))
                    window.iconphoto(True, self.icon)
                except tk.TclError:
                    LOGGER.warning('Could not load desktop icon')
                break
        window.protocol('WM_DELETE_WINDOW', self.close)

        # Clam honors application colors on Windows and Linux, while keeping
        # native keyboard traversal, button activation and editable text fields.
        style = ttk.Style(window)
        if 'clam' in style.theme_names():
            style.theme_use('clam')
        style.configure('Studio.TEntry', font=regular, padding=(12, 12),
                        foreground=c['ink'], fieldbackground=c['paper'],
                        bordercolor=c['line'], lightcolor=c['line'], darkcolor=c['line'],
                        insertcolor=c['teal'], borderwidth=1)
        style.map('Studio.TEntry', bordercolor=[('focus', c['teal'])],
                  lightcolor=[('focus', c['teal'])], darkcolor=[('focus', c['teal'])],
                  fieldbackground=[('disabled', '#F0F1ED')],
                  foreground=[('disabled', c['muted'])])
        style.configure('Studio.TButton', font=bold, padding=(16, 11),
                        background=c['surface'], foreground=c['ink'],
                        bordercolor=c['line'], lightcolor=c['surface'], darkcolor=c['surface'],
                        focuscolor=c['teal'], focusthickness=2, borderwidth=1)
        style.map('Studio.TButton', background=[('disabled', c['paper']), ('pressed', c['tint']), ('active', c['tint'])],
                  foreground=[('disabled', '#8B948E')], bordercolor=[('focus', c['teal'])])
        style.configure('Primary.Studio.TButton', padding=(24, 13),
                        background=c['teal'], foreground=c['surface'], bordercolor=c['teal'],
                        lightcolor=c['teal'], darkcolor=c['teal'], focuscolor='#B9E6DA')
        style.map('Primary.Studio.TButton',
                  background=[('disabled', '#D4E3DE'), ('pressed', c['teal_dark']), ('active', c['teal_dark'])],
                  foreground=[('disabled', '#42665E')],
                  bordercolor=[('disabled', '#D4E3DE'), ('focus', c['teal_dark'])],
                  lightcolor=[('disabled', '#D4E3DE'), ('pressed', c['teal_dark']), ('active', c['teal_dark'])],
                  darkcolor=[('disabled', '#D4E3DE'), ('pressed', c['teal_dark']), ('active', c['teal_dark'])])
        style.configure('Quiet.Studio.TButton', font=small, padding=(12, 7),
                        background=c['paper'], foreground=c['muted'],
                        bordercolor=c['paper'], lightcolor=c['paper'], darkcolor=c['paper'])
        style.map('Quiet.Studio.TButton', bordercolor=[('focus', c['teal'])])

        frame = tk.Frame(window, background=c['paper'], padx=36, pady=24)
        frame.pack(fill='both', expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(3, weight=1)
        brand = tk.Frame(frame, background=c['paper'])
        brand.grid(row=0, column=0, sticky='ew')
        logo = tk.Canvas(brand, width=36, height=36, background=c['paper'],
                         highlightthickness=0, takefocus=False)
        logo.pack(side='left')
        logo.create_polygon(9, 1, 27, 1, 35, 1, 35, 9, 35, 27, 35, 35,
                            27, 35, 4, 35, 1, 35, 1, 32, 1, 9, 1, 1,
                            smooth=True, fill=c['teal'], outline=c['teal'])
        mark_family = next((name for name in ('Georgia', 'Liberation Serif', 'DejaVu Serif')
                            if name in families), 'Times')
        logo.create_text(18, 16, text='b.', font=(mark_family, -32, 'bold'), fill='white')
        tk.Label(brand, text='Blog Studio', font=(family, 14, 'bold'),
                 background=c['paper'], foreground=c['ink']).pack(side='left', padx=(12, 0))
        tk.Label(brand, text='내 컴퓨터의 글쓰기 공간', font=small,
                 background=c['paper'], foreground=c['muted']).pack(side='right')

        heading = tk.Frame(frame, background=c['paper'])
        heading.grid(row=1, column=0, sticky='ew', pady=(24, 20))
        tk.Label(heading, text='오늘의 글을 시작하세요', font=(family, 27, 'bold'),
                 background=c['paper'], foreground=c['ink'], anchor='w').pack(fill='x')
        self.intro_label = tk.Label(heading, text='블로그 폴더를 연결하면 브라우저에서 편집기가 열립니다.',
                                   font=regular, background=c['paper'], foreground=c['muted'],
                                   anchor='w', justify='left')
        self.intro_label.pack(fill='x', pady=(9, 0))

        card = tk.Frame(frame, background=c['surface'], highlightbackground=c['line'],
                        highlightcolor=c['line'], highlightthickness=1, padx=24, pady=20)
        card.grid(row=2, column=0, sticky='ew')
        card.columnconfigure(0, weight=1)
        tk.Label(card, text='블로그 폴더', font=(family, 12, 'bold'),
                 background=c['surface'], foreground=c['ink']).grid(row=0, column=0, sticky='w')
        folder = tk.Frame(card, background=c['surface'])
        folder.grid(row=1, column=0, sticky='ew', pady=(13, 12))
        folder.columnconfigure(0, weight=1)
        self.entry = ttk.Entry(folder, textvariable=self.repo, style='Studio.TEntry',
                               font=regular, width=1)
        self.entry.grid(row=0, column=0, sticky='ew')
        self.choose_button = ttk.Button(folder, text='폴더 선택', command=self.choose,
                                        style='Studio.TButton', takefocus=True)
        self.choose_button.grid(row=0, column=1, padx=(10, 0))
        self.folder_hint = tk.Label(card, text='글과 초안이 저장된 블로그 폴더를 선택하세요.', font=small,
                                    background=c['surface'], foreground=c['muted'], anchor='w', justify='left')
        self.folder_hint.grid(row=2, column=0, sticky='ew')
        action = tk.Frame(card, background=c['surface'])
        action.grid(row=3, column=0, sticky='ew', pady=(20, 0))
        action.columnconfigure(0, weight=1)
        self.remember_label = tk.Label(action, text='선택한 폴더는 다음에도 기억해요.', font=small,
                                      background=c['surface'], foreground=c['muted'], anchor='w', justify='left')
        self.remember_label.grid(row=0, column=0, sticky='w', padx=(0, 16))
        self.start_button = ttk.Button(action, text='작성 시작', command=self.start,
                                       style='Primary.Studio.TButton', width=15, takefocus=True)
        self.start_button.grid(row=0, column=1)
        self.browser_button = ttk.Button(action, text='편집기로 돌아가기', command=self.browse,
                                         style='Primary.Studio.TButton', width=15,
                                         state='disabled', takefocus=True)
        self.browser_button.grid(row=0, column=1)
        self.browser_button.grid_remove()

        # The status area keeps its space as readiness, errors and URL change.
        status_height = font.Font(font=bold).metrics('linespace') + 2 * font.Font(font=small).metrics('linespace') + 16
        status_area = tk.Frame(frame, background=c['paper'], height=max(84, status_height))
        status_area.grid(row=4, column=0, sticky='ew', pady=(15, 8))
        status_area.grid_propagate(False)
        status_area.columnconfigure(1, weight=1)
        self.status_dot = tk.Canvas(status_area, width=12, height=20, background=c['paper'],
                                    highlightthickness=0, takefocus=False)
        self.status_dot.grid(row=0, column=0, sticky='w', padx=(0, 8))
        self.dot_id = self.status_dot.create_oval(3, 7, 9, 13, fill=c['teal'], outline='')
        self.phase_label = tk.Label(status_area, textvariable=self.phase, font=bold,
                                    background=c['paper'], foreground=c['ink'], anchor='w')
        self.phase_label.grid(row=0, column=1, sticky='ew')
        self.status_label = tk.Label(status_area, textvariable=self.status, font=small,
                                     background=c['paper'], foreground=c['muted'],
                                     anchor='w', justify='left')
        self.status_label.grid(row=1, column=1, sticky='ew', pady=(3, 0))
        self.address_label = tk.Label(status_area, textvariable=self.address, font=small,
                                      background=c['paper'], foreground=c['teal'], anchor='w')
        self.address_label.grid(row=2, column=1, sticky='ew', pady=(3, 0))
        footer = tk.Frame(frame, background=c['paper'])
        footer.grid(row=5, column=0, sticky='ew')
        footer.columnconfigure(0, weight=1)
        tk.Frame(footer, height=1, background=c['line']).grid(row=0, column=0, columnspan=2, sticky='ew', pady=(0, 10))
        tk.Label(footer, text='내 글은 내 폴더에, 안전하게.', font=small,
                 background=c['paper'], foreground=c['muted']).grid(row=1, column=0, sticky='w')
        self.quit_button = ttk.Button(footer, text='종료', command=self.close,
                                      style='Quiet.Studio.TButton', takefocus=True)
        self.quit_button.grid(row=1, column=1, sticky='e')

        def resize(event):
            available = max(200, event.width - 72)
            self.intro_label.configure(wraplength=available)
            self.status_label.configure(wraplength=available - 24)
            self.folder_hint.configure(wraplength=available - 52)
            self.remember_label.configure(wraplength=max(160, available - 320))
        frame.bind('<Configure>', resize)
        window.bind('<Control-o>', lambda event: self.choose_shortcut())
        window.bind('<Return>', self.activate_focused)
        window.after_idle(self.entry.focus_set if not repo else self.start_button.focus_set)
        # Point-sized Korean fonts grow under Windows display scaling. Let the
        # content request enough room instead of clipping it in a fixed canvas.
        window.update_idletasks()
        required_height = max(620, frame.winfo_reqheight())
        window.minsize(740, required_height)
        window.geometry(f"{max(860, frame.winfo_reqwidth())}x{max(640, required_height)}")
        self.window.report_callback_exception = self.callback_error
        self.window.after(100, self.poll)
        if args.exit_after is not None:
            self.window.after(max(1, int(args.exit_after * 1000)), lambda: self.close(force=True))

    def set_phase(self, label, message, error=False):
        self.phase.set(label)
        self.status.set(message)
        self.status_dot.itemconfigure(self.dot_id, fill=self.colors['error' if error else 'teal'])

    def choose_shortcut(self):
        if not self.busy and self.process.server is None and not self.closing:
            self.choose()
        return 'break'

    def activate_focused(self, event):
        focused = self.window.focus_get()
        buttons = (self.choose_button, self.start_button, self.browser_button, self.quit_button)
        if focused in buttons:
            focused.invoke()
        elif focused == self.entry:
            self.start()
        return 'break'

    def callback_error(self, kind, value, traceback):
        LOGGER.error('Desktop callback error', exc_info=(kind, value, traceback))
        self.messagebox.showerror('Blog Studio', f'{value}\n\n로그: {self.log_path}', parent=self.window)

    def choose(self):
        selected = self.filedialog.askdirectory(title='Jekyll 블로그 폴더 선택', initialdir=self.repo.get() or str(Path.home()), parent=self.window)
        if selected:
            self.repo.set(selected)
            self.set_phase('준비됨', '폴더를 선택했어요. 작성 시작을 눌러 주세요.')

    def start(self):
        if self.busy or self.process.server is not None:
            return
        try:
            repo = validate_repository(self.repo.get())
        except (LauncherError, OSError) as error:
            self.set_phase('폴더를 확인해 주세요', '블로그가 저장된 폴더를 다시 선택해 주세요.', error=True)
            self.messagebox.showerror('블로그 폴더 확인', str(error), parent=self.window)
            return
        self.repo.set(str(repo))
        self.busy = True
        for widget in (self.entry, self.choose_button, self.start_button):
            widget.configure(state='disabled')
        self.set_phase('편집기 여는 중', '잠시만 기다려 주세요. 블로그 폴더를 연결하고 있어요.')
        self.start_button.configure(text='시작하는 중…')

        def worker():
            try:
                self.process.start(repo, preferred_port(repo, self.args.port, self.preferences))
                remember_process(self.process)
                write_ready_file(self.args.ready_file, self.process)
                self.events.put(('started', None))
            except Exception as error:
                LOGGER.exception('Could not start desktop server')
                self.process.stop()
                self.events.put(('error', error))
        threading.Thread(target=worker, name='BlogStudioStart', daemon=True).start()

    def browse(self):
        if not self.process.running:
            return
        url = self.process.url
        def worker():
            try:
                if not open_browser(url):
                    self.events.put(('browser_error', '기본 브라우저를 열지 못했어요. 표시된 주소를 브라우저에 입력해 주세요.'))
            except Exception as error:
                LOGGER.exception('Could not open browser')
                self.events.put(('browser_error', str(error)))
        threading.Thread(target=worker, name='BlogStudioBrowser', daemon=True).start()

    def poll(self):
        try:
            while True:
                event, value = self.events.get_nowait()
                if event == 'stopped':
                    self.window.destroy()
                    return
                if event == 'started':
                    self.busy = False
                    self.address.set(self.process.url)
                    self.set_phase('편집기 실행 중', '다른 앱이 사용 중인 포트를 피해 새 주소로 연결했어요.' if self.process.port_changed else '편집기를 사용하는 동안 이 창을 열어 두세요.')
                    self.folder_hint.configure(text='연결한 폴더의 글과 초안을 편집합니다.')
                    self.start_button.grid_remove()
                    self.browser_button.configure(state='normal')
                    self.browser_button.grid()
                    self.browser_button.focus_set()
                    if self.closing:
                        self.stop_async()
                    elif not self.args.no_browser:
                        self.browse()
                elif event == 'error':
                    self.busy = False
                    if self.closing:
                        self.window.destroy()
                        return
                    for widget in (self.entry, self.choose_button, self.start_button):
                        widget.configure(state='normal')
                    self.start_button.configure(text='작성 시작')
                    self.set_phase('연결을 확인해 주세요', '실행하지 못했어요. 폴더와 오류 내용을 확인해 주세요.', error=True)
                    self.messagebox.showerror('Blog Studio 실행 오류', f'{value}\n\n로그: {self.log_path}', parent=self.window)
                elif event == 'browser_error' and not self.closing:
                    self.messagebox.showwarning('브라우저 열기', value, parent=self.window)
        except queue.Empty:
            pass
        if self.process.server is not None and not self.process.running and not self.busy and not self.closing:
            self.set_phase('편집기가 종료됨', '앱을 다시 실행해 주세요. 저장한 글은 블로그 폴더에 남아 있어요.', error=True)
            self.browser_button.configure(state='disabled')
        self.window.after(100, self.poll)

    def stop_async(self):
        self.busy = True
        self.set_phase('종료하는 중', '진행 중인 저장을 마친 뒤 안전하게 종료하고 있어요…')
        self.browser_button.configure(state='disabled')
        self.quit_button.configure(state='disabled')
        def worker():
            try:
                self.process.stop()
                remove_ready_file(self.args.ready_file, self.process)
            except Exception:
                LOGGER.exception('Could not stop desktop server cleanly')
            finally:
                self.events.put(('stopped', None))
        threading.Thread(target=worker, name='BlogStudioStop', daemon=True).start()

    def close(self, force=False):
        if self.closing:
            return
        if self.process.server is not None and not force:
            if not self.messagebox.askokcancel('Blog Studio 종료', '브라우저에 “모든 변경 저장됨”이 표시되는지 확인해 주세요.\n\n아직 입력 중인 내용은 저장되지 않을 수 있습니다. 서버를 종료할까요?', parent=self.window):
                return
        self.closing = True
        if self.busy:
            self.set_phase('종료 대기 중', '현재 작업이 끝나면 안전하게 종료합니다…')
            self.quit_button.configure(state='disabled')
        elif self.process.server is not None:
            self.stop_async()
        else:
            self.window.destroy()


def run_gui(args, log_path):
    import tkinter as tk
    window = tk.Tk()
    app = DesktopWindow(window, args, log_path)
    try:
        window.mainloop()
    finally:
        app.process.stop()
        remove_ready_file(args.ready_file, app.process)
    return 0


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description='Blog Studio — 설치 없이 실행하는 로컬 글쓰기 도구')
    parser.add_argument('--repo', type=Path, help='실제 Jekyll 블로그 폴더')
    parser.add_argument('--port', type=int, default=None, help='기본 4310 또는 이전 사용 포트; 0은 자동 배정')
    parser.add_argument('--no-browser', action='store_true', help='브라우저 자동 열기 생략')
    parser.add_argument('--headless', action='store_true', help='데스크톱 창 없이 HTTP 서버 실행')
    parser.add_argument('--ready-file', type=Path, help='실행 후 URL/저장소 JSON 기록; 종료 시 제거')
    parser.add_argument('--exit-after', type=float, help='지정한 초 뒤 정상 종료 (자동화 검증용)')
    parser.add_argument('--check-gui', action='store_true', help='Tk 창 실행 가능 여부만 확인')
    args = parser.parse_args(argv)
    if args.port is not None and not 0 <= args.port <= 65535:
        parser.error('--port는 0부터 65535까지 가능합니다.')
    if args.exit_after is not None and (args.exit_after < 0 or not args.exit_after < float('inf')):
        parser.error('--exit-after는 0 이상의 유한한 초 단위 값이어야 합니다.')
    return args


def main(argv=None):
    args = parse_args(argv)
    log_path = config_directory() / 'desktop.log'
    try:
        log_path = configure_logging()
        if args.check_gui:
            return check_gui()
        return run_headless(args) if args.headless else run_gui(args, log_path)
    except Exception as error:
        LOGGER.exception('Blog Studio launcher failed')
        message = f'Blog Studio를 실행하지 못했습니다.\n{error}\n\n로그: {log_path}'
        if sys.stderr is not None:
            print(message, file=sys.stderr)
        if not args.headless:
            try:
                from tkinter import messagebox
                messagebox.showerror('Blog Studio', message)
            except Exception:
                pass
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
