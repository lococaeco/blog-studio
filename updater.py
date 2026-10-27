"""Verified portable updates with a separate helper that waits for app exit."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler
import uuid

from runtime import external_environment, external_program_environment, subprocess_options

CURRENT_VERSION = '0.2.0'
RELEASE_REPO = 'lococaeco/blog-studio'
RELEASES_URL = 'https://github.com/' + RELEASE_REPO + '/releases'
MAX_DOWNLOAD = 256 * 1024 * 1024


class UpdateError(RuntimeError):
    status = 400


def version_tuple(value):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', str(value))
    if not match:
        raise UpdateError('올바른 정식 버전 번호가 아닙니다.')
    return tuple(map(int, match.groups()))


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def config_directory():
    if sys.platform == 'win32':
        return Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData/Local') / 'Blog Studio'
    base = Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config')))
    return (base if base.is_absolute() else Path.home() / '.config') / 'blog-studio'


def running_executable():
    if not getattr(sys, 'frozen', False):
        return None
    image = os.environ.get('APPIMAGE')
    return Path(image if image and sys.platform == 'linux' else sys.executable).absolute()


def asset_name(target=None):
    if platform.machine().lower() not in ('x86_64', 'amd64'):
        raise UpdateError('이 운영체제의 자동 업데이트 파일은 제공하지 않습니다.')
    if sys.platform == 'win32':
        return 'BlogStudio.exe'
    if sys.platform == 'linux':
        return 'BlogStudio-x86_64.AppImage' if (target and str(target).lower().endswith('.appimage')) or os.environ.get('APPIMAGE') else 'BlogStudio-linux-x86_64'
    raise UpdateError('이 운영체제의 자동 업데이트 파일은 제공하지 않습니다.')


class ReleaseRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        host = urlparse(newurl)
        if host.scheme != 'https' or host.port not in (None, 443) or host.hostname not in {
                'github.com', 'api.github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'}:
            raise UpdateError('허용되지 않은 다운로드 주소입니다.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download_bytes(url, limit, destination=None):
    parsed = urlparse(url)
    if parsed.scheme != 'https' or parsed.hostname not in ('github.com', 'api.github.com') or parsed.port not in (None, 443):
        raise UpdateError('공식 GitHub 주소에서만 업데이트를 받을 수 있습니다.')
    try:
        opener = build_opener(ReleaseRedirects())
        with opener.open(Request(url, headers={'User-Agent': 'BlogStudio/' + CURRENT_VERSION,
                                              'Accept': 'application/vnd.github+json' if parsed.hostname == 'api.github.com' else 'application/octet-stream'}), timeout=40) as response:
            data, total = bytearray(), 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                if total > limit:
                    raise UpdateError('업데이트 파일의 크기가 허용 범위를 넘었습니다.')
                if destination is None:
                    data.extend(chunk)
                else:
                    destination.write(chunk)
            return bytes(data) if destination is None else total
    except (HTTPError, URLError, TimeoutError, OSError):
        raise UpdateError('업데이트 서버에 연결하지 못했습니다. 네트워크를 확인하고 다시 시도해 주세요.') from None


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix='.studio-update-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class Updater:
    def __init__(self, studio=None, *, target=None, cache=None, current=CURRENT_VERSION, shutdown_callback=None):
        self.studio = studio
        self.target = Path(target).absolute() if target is not None else running_executable()
        self.cache = Path(cache or config_directory() / 'updates')
        self.current = current
        self.shutdown_callback = shutdown_callback
        self.release = None
        self.prepared = None
        self._lock = threading.Lock()
        self._applying = False

    def check(self):
        try:
            release = json.loads(download_bytes('https://api.github.com/repos/' + RELEASE_REPO + '/releases/latest', 2 * 1024 * 1024))
            tag = release['tag_name']
            newer = version_tuple(tag) > version_tuple(self.current)
            if release.get('draft') or release.get('prerelease'):
                raise UpdateError('정식 릴리스 정보를 확인할 수 없습니다.')
            name = asset_name(self.target)
            assets = {item['name']: item for item in release.get('assets', [])}
            prefix = RELEASES_URL + '/download/' + tag + '/'
            for required in (name, name + '.sha256'):
                if required not in assets or assets[required].get('browser_download_url') != prefix + required:
                    raise UpdateError('실행 파일과 체크섬이 모두 있는 정식 릴리스가 필요합니다.')
            size = assets[name].get('size', 0)
            if not isinstance(size, int) or not 0 < size <= MAX_DOWNLOAD:
                raise UpdateError('업데이트 파일의 크기가 올바르지 않습니다.')
            self.release = {'version': tag.lstrip('v'), 'tag': tag, 'name': name, 'url': prefix + name,
                            'checksum_url': prefix + name + '.sha256', 'size': size}
            return {'current': self.current, 'latest': tag.lstrip('v'), 'available': newer,
                    'can_apply': bool(self.target and self.target.is_file() and not self.target.is_symlink() and os.access(self.target.parent, os.W_OK)),
                    'release_url': RELEASES_URL + '/tag/' + tag,
                    'message': '새 버전을 받을 수 있어요.' if newer else '최신 버전을 사용하고 있어요.'}
        except (KeyError, TypeError, ValueError):
            raise UpdateError('릴리스 정보를 확인하지 못했습니다.') from None

    def download(self):
        with self._lock:
            status = self.check()
            if not status['available']:
                return {**status, 'ready': False}
            release = dict(self.release)
            try:
                checksum = download_bytes(release['checksum_url'], 4096).decode('ascii').strip()
            except UnicodeDecodeError:
                raise UpdateError('업데이트 체크섬 형식이 올바르지 않습니다.') from None
            match = re.fullmatch(r'([a-fA-F0-9]{64})\s+\*?' + re.escape(release['name']), checksum)
            if not match:
                raise UpdateError('업데이트 체크섬 형식이 올바르지 않습니다.')
            expected = match.group(1).lower()
            folder = self.cache / release['tag']
            folder.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, temporary_name = tempfile.mkstemp(prefix='.download-', dir=folder)
            temporary = Path(temporary_name)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    total = download_bytes(release['url'], MAX_DOWNLOAD, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                if total != release['size'] or sha256(temporary) != expected:
                    raise UpdateError('다운로드 검증에 실패했습니다. 기존 앱은 변경하지 않았습니다.')
                with temporary.open('rb') as stream:
                    header = stream.read(11)
                if release['name'].endswith('.exe'):
                    valid = header[:2] == b'MZ'
                elif release['name'].endswith('.AppImage'):
                    valid = header[:4] == b'\x7fELF' and header[8:11] == b'AI\x02'
                else:
                    valid = header[:4] == b'\x7fELF'
                if not valid:
                    raise UpdateError('다운로드한 실행 파일 형식이 올바르지 않습니다.')
                temporary.chmod(0o755)
                destination = folder / release['name']
                os.replace(temporary, destination)
                self.prepared = {**release, 'source': str(destination), 'sha256': expected}
            finally:
                temporary.unlink(missing_ok=True)
            return {**status, 'ready': True, 'message': '다운로드와 SHA-256 검증을 마쳤습니다. 저장 후 업데이트를 적용하세요.'}

    def apply(self):
        with self._lock:
            if self._applying:
                raise UpdateError('이미 업데이트를 준비하고 있습니다.')
            if self.target is None or not self.target.is_file() or self.target.is_symlink():
                raise UpdateError('실행 파일 버전에서 업데이트를 사용할 수 있습니다. 소스 실행은 Git으로 업데이트해 주세요.')
            if not self.prepared:
                raise UpdateError('새 버전을 먼저 다운로드하고 검증해 주세요.')
            source = Path(self.prepared['source'])
            if source.is_symlink() or sha256(source) != self.prepared['sha256']:
                raise UpdateError('준비된 파일이 변경됐습니다. 업데이트를 다시 받아 주세요.')
            if not os.access(self.target.parent, os.W_OK):
                raise UpdateError('실행 파일 폴더에 쓰기 권한이 없습니다. 내 사용자 폴더로 앱을 옮겨 주세요.')
            self.cache.mkdir(parents=True, exist_ok=True, mode=0o700)
            identity = uuid.uuid4().hex[:12]
            helper = self.cache / ('helper-' + identity + self.target.suffix)
            shutil.copy2(self.target, helper)
            helper.chmod(0o755)
            manifest = self.cache / ('apply-' + identity + '.json')
            write_json(manifest, {'pid': os.getpid(), 'target': str(self.target), 'source': str(source),
                                  'sha256': self.prepared['sha256'], 'old_sha256': sha256(self.target),
                                  'version': self.prepared['version'], 'current': self.current,
                                  'repo': str(self.studio.repo) if self.studio else '',
                                  'backup': str(self.target.with_name(self.target.name + '.backup-' + identity)),
                                  'result': str(self.cache / 'last-result.json')})
            env = external_environment()
            env.update(PYINSTALLER_RESET_ENVIRONMENT='1', APPIMAGE_EXTRACT_AND_RUN='1')
            options = subprocess_options()
            if os.name == 'nt':
                options['creationflags'] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                options['start_new_session'] = True
            with external_program_environment():
                subprocess.Popen([str(helper), '--apply-update-manifest', str(manifest)],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 env=env, close_fds=True, **options)
            self._applying = True
            if self.shutdown_callback:
                timer = threading.Timer(0.5, self.shutdown_callback)
                timer.daemon = True
                timer.start()
            return {'scheduled': True, 'version': self.prepared['version'],
                    'message': '저장한 뒤 앱을 종료하면 새 버전으로 바뀌고 다시 실행됩니다.'}


def pid_alive(pid):
    if os.name == 'nt':
        import ctypes
        kernel = ctypes.windll.kernel32
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x100000, False, pid)  # SYNCHRONIZE
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 0x102
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def perform_update(manifest, *, wait_seconds=180, launch=True):
    """Run in the copied helper, never in the live application's process."""
    manifest = Path(manifest)
    payload = json.loads(manifest.read_text('utf-8'))
    for key in ('source', 'target', 'backup', 'result'):
        if not isinstance(payload.get(key), str) or not Path(payload[key]).is_absolute():
            raise UpdateError('업데이트 경로가 올바르지 않습니다.')
    source, target, backup = (Path(payload[key]) for key in ('source', 'target', 'backup'))
    if type(payload.get('pid')) is not int or payload['pid'] <= 0 or payload['pid'] == os.getpid():
        raise UpdateError('업데이트할 프로세스가 올바르지 않습니다.')
    deadline = time.monotonic() + wait_seconds
    while pid_alive(payload['pid']):
        if time.monotonic() >= deadline:
            raise UpdateError('앱이 종료되지 않아 업데이트를 적용하지 않았습니다.')
        time.sleep(0.2)
    if source.is_symlink() or target.is_symlink() or sha256(source) != payload['sha256'] or sha256(target) != payload['old_sha256']:
        raise UpdateError('실행 파일이 변경되어 업데이트를 중단했습니다.')
    if backup.exists() or backup.parent != target.parent:
        raise UpdateError('기존 백업을 덮어쓰지 않습니다.')
    with backup.open('xb') as stream, target.open('rb') as old:
        shutil.copyfileobj(old, stream)
    backup.chmod(target.stat().st_mode & 0o777)
    fd, name = tempfile.mkstemp(prefix='.studio-new-', dir=target.parent)
    replacement = Path(name)
    try:
        with os.fdopen(fd, 'wb') as stream, source.open('rb') as new:
            shutil.copyfileobj(new, stream)
            stream.flush()
            os.fsync(stream.fileno())
        replacement.chmod(0o755)
        if sha256(replacement) != payload['sha256']:
            raise UpdateError('업데이트 파일 복사 검증에 실패했습니다.')
        # The one-file bootloader can outlive its Python process briefly on
        # Windows. Wait for its executable handle to close, without killing it.
        deadline = time.monotonic() + 30
        while True:
            try:
                os.replace(replacement, target)
                break
            except PermissionError:
                if time.monotonic() >= deadline:
                    raise UpdateError('실행 파일이 아직 사용 중이어서 교체하지 못했습니다.') from None
                time.sleep(.25)
        if launch:
            arguments = [str(target)]
            if payload.get('repo'):
                arguments += ['--repo', payload['repo']]
            env = external_environment()
            env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
            try:
                with external_program_environment():
                    subprocess.Popen(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, env=env, close_fds=True, **subprocess_options())
            except OSError:
                # An OS-level launch failure should leave the original working
                # executable in place. Keep its backup for manual recovery too.
                rollback_fd, rollback_name = tempfile.mkstemp(prefix='.studio-rollback-', dir=target.parent)
                rollback = Path(rollback_name)
                try:
                    with os.fdopen(rollback_fd, 'wb') as stream, backup.open('rb') as old:
                        shutil.copyfileobj(old, stream)
                        stream.flush()
                        os.fsync(stream.fileno())
                    rollback.chmod(backup.stat().st_mode & 0o777)
                    if sha256(rollback) != payload['old_sha256']:
                        raise UpdateError('기존 실행 파일 복원 검증에 실패했습니다. 백업 파일을 확인해 주세요.')
                    os.replace(rollback, target)
                finally:
                    rollback.unlink(missing_ok=True)
                raise UpdateError('새 앱을 실행하지 못해 기존 실행 파일을 복원했습니다. 앱을 다시 열어 주세요.') from None
        write_json(payload['result'], {'success': True, 'version': payload['version'], 'backup': str(backup)})
        return payload['version']
    finally:
        replacement.unlink(missing_ok=True)


def maybe_handle_helper(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] != '--apply-update-manifest':
        return None
    if len(args) != 2:
        return 2
    try:
        perform_update(args[1])
        return 0
    except Exception as error:
        # No credentials or user content are present in this diagnostic.
        try:
            write_json(config_directory() / 'updates/last-result.json', {'success': False, 'message': str(error)[:500]})
        except OSError:
            pass
        return 1
