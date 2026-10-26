#!/usr/bin/env python3
"""Exercise the shipped executable, with no Python or Git on its PATH.

Only disposable fixtures are opened; never points the app at the real blog.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def request(url, path, body=None, token=None):
    headers = {}
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode('utf-8')
        headers['Content-Type'] = 'application/json'
        if token:
            headers['X-Studio-Token'] = token
    with urlopen(Request(url + path, data=data, headers=headers), timeout=10) as response:
        raw = response.read()
        return json.loads(raw) if 'application/json' in response.headers.get('Content-Type', '') else raw


def start(binary, repo, work, env, iteration):
    ready = work / f'ready-{iteration}.json'
    process = subprocess.Popen([
        str(binary), '--headless', '--no-browser', '--repo', str(repo),
        '--port', '0', '--ready-file', str(ready), '--exit-after', '12',
    ], cwd=work, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(f'App stopped before ready: {process.returncode}\n{stdout!r}\n{stderr!r}')
        if ready.exists():
            try:
                metadata = json.loads(ready.read_text('utf-8'))
                assert set(metadata) == {'url', 'repo'}, metadata.keys()
                assert Path(metadata['repo']).resolve() == repo.resolve()
                assert metadata['url'].startswith('http://127.0.0.1:')
                return process, metadata['url']
            except json.JSONDecodeError:
                pass
        time.sleep(.1)
    process.kill()
    raise AssertionError('Timed out waiting for packaged application startup')


def finish(process):
    stdout, stderr = process.communicate(timeout=30)
    assert process.returncode == 0, (process.returncode, stdout, stderr)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('executable', type=Path)
    args = parser.parse_args()
    source = args.executable.resolve()
    assert source.is_file(), source
    with tempfile.TemporaryDirectory(prefix='blog-studio-package-') as folder:
        work = Path(folder)
        # Moving just this one file must be sufficient, including a Unicode path.
        binary_folder = work / '실행 파일'
        binary_folder.mkdir()
        binary = binary_folder / source.name
        shutil.copy2(source, binary)
        repo = work / '내 블로그 with spaces'
        (repo / '_posts').mkdir(parents=True)
        (repo / '_data').mkdir()
        (repo / '_config.yml').write_text('title: Package test\nurl: https://example.invalid\n', encoding='utf-8')
        (repo / '_data/categories.yml').write_text(
            'categories:\n  - lv1_category: AI\n    lv2:\n      - lv2_category: LLM\n        url: /category/ai/llm/\n', encoding='utf-8')
        env = os.environ.copy()
        env.update(PATH='', XDG_CONFIG_HOME=str(work / 'config'), XDG_STATE_HOME=str(work / 'state'),
                   LOCALAPPDATA=str(work / 'local'), APPDATA=str(work / 'roaming'))
        # Check the actual frozen Tcl/Tk runtime without opening the real repository.
        subprocess.run([str(binary), '--check-gui'], cwd=work, env=env, check=True, timeout=30)
        # Construct and close the full launcher too: fonts, styles and widgets
        # must work in the shipped Windows/Linux application, not only a blank Tk.
        subprocess.run([str(binary), '--repo', str(repo), '--no-browser', '--exit-after', '1.5'],
                       cwd=work, env=env, check=True, timeout=30)
        process, url = start(binary, repo, work, env, 1)
        try:
            state = request(url, '/api/bootstrap')
            assert state['site']['title'] == 'Package test'
            assert state['git']['available'] is False
            assert not state['drafts']
            for path in ('/', '/static/app.js', '/static/style.css',
                         '/static/vendor/toastui-editor-all.min.js',
                         '/static/vendor/katex/fonts/KaTeX_Main-Regular.woff2'):
                assert request(url, path), path
            try:
                request(url, '/api/drafts', {'title': 'Must not save'})
                raise AssertionError('Missing session token accepted')
            except HTTPError as error:
                assert error.code == 403
            draft = request(url, '/api/drafts', {
                'title': '단일 실행 파일 저장 검증', 'body': '본문과 **서식** 보존', 'category': 'ai/llm',
                'date': '2026-01-01', 'slug': 'packaged-smoke',
            }, state['token'])
            result = request(url, '/api/publish', {'id': draft['id'], 'version': draft['version'], 'mode': 'save'}, state['token'])
            assert (repo / result['path']).is_file()
            assert '본문과 **서식** 보존' in (repo / result['path']).read_text('utf-8')
            assert (repo / '.blog-studio/drafts' / (draft['id'] + '.json')).is_file()
        finally:
            finish(process)
        with socket.socket() as connection:
            connection.settimeout(2)
            assert connection.connect_ex(('127.0.0.1', int(url.rsplit(':', 1)[1]))) != 0
        # A fresh process must reopen the saved data and release the repository lock.
        process, url = start(binary, repo, work, env, 2)
        try:
            state = request(url, '/api/bootstrap')
            assert len(state['drafts']) == 1 and len(state['posts']) == 1
            restored = request(url, '/api/drafts/' + draft['id'])
            assert restored['body'] == '본문과 **서식** 보존'
        finally:
            finish(process)
        assert not (binary_folder / '.blog-studio').exists()
        print('PASS: packaged GUI, static assets, Unicode paths, no Python/Git, token guard, save, restart, and shutdown')


if __name__ == '__main__':
    main()
