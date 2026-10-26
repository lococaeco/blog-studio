"""Process lifetime and external-program helpers shared by desktop and CLI."""
from __future__ import annotations

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import stat
import subprocess
import sys
import threading


class RepositoryInUseError(RuntimeError):
    pass


def _link_or_reparse(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return (stat.S_ISLNK(info.st_mode)
            or bool(getattr(info, 'st_file_attributes', 0) & 0x400))  # Windows reparse point/junction


class RepositoryLock:
    """Hold an OS lock, retaining its file permanently to avoid inode races."""
    def __init__(self, repo):
        self.repo = Path(repo).resolve()
        self.fd = None

    def acquire(self):
        if self.fd is not None:
            return self
        storage = self.repo / '.blog-studio'
        if _link_or_reparse(storage):
            raise RuntimeError('초안 폴더에는 심볼릭 링크를 사용할 수 없습니다.')
        storage.mkdir(mode=0o700, exist_ok=True)
        if _link_or_reparse(storage) or not storage.is_dir():
            raise RuntimeError('초안 폴더를 안전하게 열 수 없습니다.')
        path = storage / 'instance.lock'
        directory_fd = None
        fd = None
        try:
            flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NOINHERIT', 0)
            if os.name != 'nt':
                directory_fd = os.open(storage, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                fd = os.open('instance.lock', flags, 0o600, dir_fd=directory_fd)
            else:
                if _link_or_reparse(path):
                    raise RuntimeError('잠금 파일에는 심볼릭 링크를 사용할 수 없습니다.')
                fd = os.open(path, flags, 0o600)
            opened = os.fstat(fd)
            named = path.lstat()
            if (_link_or_reparse(storage) or _link_or_reparse(path)
                    or not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                    or not os.path.samestat(opened, named)):
                raise RuntimeError('저장소 잠금 파일을 안전하게 열 수 없습니다.')
            try:
                if os.name == 'nt':
                    import msvcrt
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                if exc.errno in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise RepositoryInUseError('이 블로그는 다른 Blog Studio에서 열려 있습니다. 먼저 실행 중인 앱을 종료해 주세요.') from exc
                raise
            self.fd, fd = fd, None
            return self
        finally:
            if fd is not None:
                os.close(fd)
            if directory_fd is not None:
                os.close(directory_fd)

    def release(self):
        if self.fd is None:
            return
        fd, self.fd = self.fd, None
        try:
            if os.name == 'nt':
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        # Never unlink: another process may already be waiting on this inode.

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *args):
        self.release()


_external_lock = threading.RLock()


def external_environment(env=None):
    """Undo PyInstaller library paths only in the external process's environment."""
    result = dict(os.environ if env is None else env)
    if not getattr(sys, 'frozen', False):
        return result
    for key in ('LD_LIBRARY_PATH', 'LIBPATH'):
        if key + '_ORIG' in result:
            result[key] = result[key + '_ORIG']
        else:
            result.pop(key, None)
    bundle = getattr(sys, '_MEIPASS', None)
    if bundle and 'PATH' in result:
        root = os.path.normcase(os.path.abspath(bundle))
        def bundled(value):
            value = os.path.normcase(os.path.abspath(value))
            return value == root or value.startswith(root + os.sep)
        result['PATH'] = os.pathsep.join(p for p in result['PATH'].split(os.pathsep) if not bundled(p))
    return result


def subprocess_options():
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}


@contextmanager
def _external_dll_path():
    # SetDllDirectory is process-wide. Serialize changes and restore its actual
    # prior value, rather than guessing the PyInstaller extraction directory.
    with _external_lock:
        kernel = None
        previous = None
        if os.name == 'nt' and getattr(sys, 'frozen', False):
            import ctypes
            kernel = ctypes.windll.kernel32
            length = kernel.GetDllDirectoryW(0, None)
            buffer = ctypes.create_unicode_buffer(length + 1)
            kernel.GetDllDirectoryW(len(buffer), buffer)
            previous = buffer.value or None
            if not kernel.SetDllDirectoryW(None):
                raise ctypes.WinError()
        try:
            yield
        finally:
            if kernel is not None:
                kernel.SetDllDirectoryW(previous)


def run_external(command, **kwargs):
    """Run system Git without inheriting bundled DLL/library search paths."""
    kwargs['env'] = external_environment(kwargs.get('env'))
    for key, value in subprocess_options().items():
        kwargs.setdefault(key, value)
    with _external_dll_path():
        return subprocess.run(command, **kwargs)


@contextmanager
def external_program_environment():
    """For browser APIs without an env argument; keep this scope short."""
    with _external_dll_path():
        before = {key: os.environ.get(key) for key in ('LD_LIBRARY_PATH', 'LIBPATH', 'PATH')}
        clean = external_environment()
        try:
            for key in before:
                if key in clean:
                    os.environ[key] = clean[key]
                else:
                    os.environ.pop(key, None)
            yield
        finally:
            for key, value in before.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
