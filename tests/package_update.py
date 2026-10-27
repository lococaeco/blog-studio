"""Exercise a packaged updater helper using disposable executables only.

No release is downloaded, no login is read, and no installed app is replaced.
Linux relaunches a marker-writing script; Windows relaunches a temporary copy
of the operating system command interpreter with closed standard input.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    if len(sys.argv) != 2:
        raise SystemExit('Usage: python tests/package_update.py /path/to/BlogStudio')
    executable = Path(sys.argv[1]).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix='blog-studio-update-package-') as temporary:
        root = Path(temporary).resolve() / '업데이트 공백 경로'
        root.mkdir()
        target = root / ('Target.exe' if os.name == 'nt' else 'Target.AppImage')
        source = root / ('Next.exe' if os.name == 'nt' else 'Next.AppImage')
        marker = root / 'launched.txt'
        target.write_bytes(b'old disposable executable; never run')
        target.chmod(0o755)
        if os.name == 'nt':
            interpreter = Path(os.environ['SystemRoot']) / 'System32' / 'cmd.exe'
            shutil.copy2(interpreter, source)
        else:
            source.write_text('#!/bin/sh\nprintf launched > ' + shlex.quote(str(marker)) + '\n', 'utf-8')
        source.chmod(0o755)
        previous = subprocess.Popen([sys.executable, '-c', 'pass'], stdin=subprocess.DEVNULL,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        previous.wait(timeout=10)
        backup, result, manifest = root / 'previous.backup', root / 'result.json', root / 'manifest.json'
        manifest.write_text(json.dumps({
            'pid': previous.pid, 'target': str(target), 'source': str(source),
            'sha256': digest(source), 'old_sha256': digest(target),
            'version': '99.0.0', 'current': '0.0.0', 'backup': str(backup),
            'result': str(result), 'repo': '',
        }), 'utf-8')
        env = os.environ.copy()
        env.update(PYINSTALLER_RESET_ENVIRONMENT='1', APPIMAGE_EXTRACT_AND_RUN='1',
                   XDG_CONFIG_HOME=str(root / 'config'), LOCALAPPDATA=str(root / 'config'))
        process = subprocess.run([str(executable), '--apply-update-manifest', str(manifest)],
                                 stdin=subprocess.DEVNULL, capture_output=True, env=env, timeout=90)
        if process.returncode:
            diagnostic = root / 'config' / ('Blog Studio' if os.name == 'nt' else 'blog-studio') / 'updates/last-result.json'
            raise AssertionError('Packaged helper failed: ' + (diagnostic.read_text('utf-8') if diagnostic.exists() else repr(process.stderr[-2000:])))
        assert digest(target) == digest(source), 'Disposable target was not replaced'
        assert backup.read_bytes() == b'old disposable executable; never run', 'Previous executable backup was lost'
        assert json.loads(result.read_text('utf-8'))['success'] is True, 'Update success was not recorded'
        if os.name != 'nt':
            deadline = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(.05)
            assert marker.read_text('utf-8') == 'launched', 'Updated executable was not relaunched'
        else:
            # cmd.exe exits on its closed stdin. Give Windows time to release
            # its executable mapping before TemporaryDirectory cleanup.
            deadline = time.monotonic() + 10
            while True:
                try:
                    target.unlink()
                    break
                except PermissionError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(.1)
        print('Packaged update helper: replacement, backup, and relaunch passed (' + executable.name + ')')


if __name__ == '__main__':
    main()
