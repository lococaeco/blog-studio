"""Launcher regression tests using disposable, non-Git Jekyll repositories."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.request import Request, urlopen


APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
import desktop


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='studio-desktop-test-')
        self.addCleanup(self.temporary.cleanup)
        # Windows runners can return an 8.3 TEMP alias (e.g. RUNNER~1).
        # Compare canonical repository locations, not equivalent spellings.
        self.root = Path(self.temporary.name).resolve()
        self.repo = self.make_repository('blog')
        self.processes = []
        self.addCleanup(self.stop_servers)

    def make_repository(self, name):
        repo = self.root / name
        (repo / '_posts' / 'dev').mkdir(parents=True)
        (repo / '_config.yml').write_text('title: Desktop fixture\nurl: https://example.invalid\n', 'utf-8')
        (repo / '_data').mkdir()
        (repo / '_data/categories.yml').write_text('categories:\n  - lv1_category: Dev\n    lv2:\n      - lv2_category: Notes\n        url: /category/dev/notes/\n', 'utf-8')
        (repo / '_posts/dev/2026-01-01-existing.md').write_text('---\ntitle: Existing post\ncategories: [Dev, Notes]\n---\n\nOriginal body.\n', 'utf-8')
        return repo

    def start_server(self, repo=None, port=0):
        process = desktop.StudioProcess()
        self.processes.append(process)
        process.start(repo or self.repo, port)
        return process

    def stop_servers(self):
        for process in self.processes:
            process.stop()

    def request(self, process, path='/api/bootstrap', payload=None, token=None):
        request = Request(process.url + path,
                          data=json.dumps(payload).encode() if payload is not None else None,
                          headers={'Content-Type': 'application/json', **({'X-Studio-Token': token} if token else {})})
        with urlopen(request, timeout=10) as response:
            return json.load(response)

    def test_real_http_without_git_preserves_drafts_in_selected_repository(self):
        original = (self.repo / '_posts/dev/2026-01-01-existing.md').read_bytes()
        first = self.start_server()
        bootstrap = self.request(first)
        self.assertEqual(first.server.server_address[0], '127.0.0.1')
        self.assertFalse(bootstrap['git']['available'])
        self.assertEqual(bootstrap['posts'][0]['title'], 'Existing post')
        draft = self.request(first, '/api/drafts', {'title': '계속 쓸 글', 'body': '보관할 초안'}, bootstrap['token'])
        self.assertTrue((self.repo / '.blog-studio/drafts' / (draft['id'] + '.json')).is_file())
        first.stop()
        second = self.start_server()
        recovered = self.request(second, '/api/drafts/' + draft['id'])
        self.assertEqual(recovered['body'], '보관할 초안')
        self.assertEqual(original, (self.repo / '_posts/dev/2026-01-01-existing.md').read_bytes())
        with urlopen(second.url + '/', timeout=10) as response:
            self.assertIn(b'Blog Studio', response.read())

    def test_missing_git_executable_does_not_break_file_editing(self):
        process = self.start_server()
        with patch('server.subprocess.run', side_effect=FileNotFoundError('git')):
            bootstrap = self.request(process)
            draft = self.request(process, '/api/drafts', {'title': 'Git 없는 초안'}, bootstrap['token'])
        self.assertFalse(bootstrap['git']['available'])
        self.assertEqual(draft['title'], 'Git 없는 초안')

    def test_occupied_port_creates_a_separate_loopback_service(self):
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            occupied.listen()
            port = occupied.getsockname()[1]
            process = self.start_server(port=port)
            self.assertTrue(process.port_changed)
            self.assertNotEqual(process.server.server_port, port)
            self.assertEqual(self.request(process)['site']['title'], 'Desktop fixture')

    def test_different_repositories_never_reuse_each_others_server(self):
        other = self.make_repository('other')
        (other / '_config.yml').write_text('title: A different blog\n', 'utf-8')
        first = self.start_server()
        second = self.start_server(other, first.server.server_port)
        self.assertNotEqual(first.url, second.url)
        self.assertEqual(self.request(first)['site']['title'], 'Desktop fixture')
        self.assertEqual(self.request(second)['site']['title'], 'A different blog')

    def test_windows_exclusive_listener_error_uses_a_fresh_port(self):
        import server
        factory = server.create_server
        ports = []
        def create(repo, port):
            ports.append(port)
            if len(ports) == 1:
                raise OSError(10013, 'Windows exclusive listener collision')
            return factory(repo, port)
        with patch('server.create_server', side_effect=create):
            process = self.start_server(port=4310)
        self.assertEqual(ports, [4310, 0])
        self.assertTrue(process.port_changed)
        self.assertEqual(self.request(process)['site']['title'], 'Desktop fixture')

    def test_same_repository_second_instance_is_rejected_until_shutdown(self):
        first = self.start_server()
        with self.assertRaises(Exception) as error:
            self.start_server()
        self.assertEqual(getattr(error.exception, 'status', None), 409)
        self.assertTrue(first.running)
        first.stop()
        self.assertTrue(self.start_server().running)

    def test_stop_waits_for_an_inflight_save_before_releasing_repository(self):
        process = self.start_server()
        token = self.request(process)['token']
        entered, release = threading.Event(), threading.Event()
        save = process.server.studio.save_draft
        result, failures = [], []

        def slow_save(payload):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('Test did not release the write')
            return save(payload)

        def request_write():
            try:
                result.append(self.request(process, '/api/drafts', {'title': '종료 중에도 저장', 'body': '지켜야 할 내용'}, token))
            except Exception as error:
                failures.append(error)

        with patch.object(process.server.studio, 'save_draft', slow_save):
            writer = threading.Thread(target=request_write)
            writer.start()
            self.assertTrue(entered.wait(3))
            stopper = threading.Thread(target=process.stop)
            stopper.start()
            try:
                time.sleep(0.3)
                self.assertTrue(stopper.is_alive(), 'Shutdown must wait for the active write')
            finally:
                release.set()
                writer.join(5)
                stopper.join(5)
        self.assertFalse(writer.is_alive())
        self.assertFalse(stopper.is_alive())
        self.assertEqual(failures, [])
        recovered = self.request(self.start_server(), '/api/drafts/' + result[0]['id'])
        self.assertEqual(recovered['body'], '지켜야 할 내용')

    def test_bundle_directory_is_never_a_writable_blog_even_via_symlink(self):
        extraction = self.root / 'bundle'
        extraction.mkdir()
        bundled_repo = self.make_repository('bundle/blog')
        with patch.object(sys, '_MEIPASS', str(extraction), create=True):
            with self.assertRaises(desktop.LauncherError):
                desktop.validate_repository(bundled_repo)
            with self.assertRaises(desktop.LauncherError):
                self.start_server(bundled_repo)
            self.assertFalse((bundled_repo / '.blog-studio').exists())
            alias = self.root / 'alias'
            try:
                alias.symlink_to(bundled_repo, target_is_directory=True)
            except OSError:
                pass  # Windows accounts may lack symlink privileges.
            else:
                with self.assertRaises(desktop.LauncherError):
                    desktop.validate_repository(alias)

    def test_frozen_discovery_uses_executable_location_not_extraction(self):
        executable = self.repo / 'BlogStudio.exe'
        unrelated = self.root / 'downloads'
        unrelated.mkdir()
        extraction = self.make_repository('temporary-bundle')
        with patch.object(sys, '_MEIPASS', str(extraction), create=True):
            found = desktop.discover_repository({'last_repo': str(extraction)}, executable=executable,
                                               cwd=unrelated, frozen=True)
        self.assertEqual(found, self.repo)
        self.assertFalse((extraction / '.blog-studio').exists())

    def test_discovery_uses_working_directory_and_preferences_not_source_ancestors(self):
        unrelated = self.root / 'downloads'
        unrelated.mkdir()
        source = self.repo / 'unrelated-app/desktop.py'
        other = self.make_repository('other')
        with patch.object(desktop, '__file__', str(source)):
            self.assertIsNone(desktop.discover_repository(cwd=unrelated, frozen=False))
            self.assertEqual(desktop.discover_repository(cwd=self.repo, frozen=False), self.repo)
            self.assertEqual(desktop.discover_repository({'last_repo': str(other)},
                                                        cwd=unrelated, frozen=False), other)

    def test_preferences_preserve_each_repositories_origin_port(self):
        path = self.root / 'settings/settings.json'
        other = self.make_repository('other')
        desktop.save_preferences(self.repo, 4310, path)
        desktop.save_preferences(other, 47221, path)
        preferences = desktop.load_preferences(path)
        self.assertEqual(Path(preferences['last_repo']).resolve(), other.resolve())
        self.assertEqual(desktop.preferred_port(self.repo, preferences=preferences), 4310)
        self.assertEqual(desktop.preferred_port(other, preferences=preferences), 47221)
        self.assertEqual(desktop.preferred_port(other, explicit=0, preferences=preferences), 0)
        self.assertNotIn('token', path.read_text())
        path.write_text('broken JSON', 'utf-8')
        self.assertEqual(desktop.load_preferences(path), {})

    def test_invalid_repository_does_not_create_draft_storage(self):
        with self.assertRaises(desktop.LauncherError):
            self.start_server(self.root)
        self.assertFalse((self.root / '.blog-studio').exists())

    def test_headless_entry_ready_file_http_and_timed_shutdown(self):
        ready = self.root / 'ready.json'
        config = self.root / 'config'
        env = os.environ.copy()
        env.update(XDG_CONFIG_HOME=str(config), LOCALAPPDATA=str(config), APPDATA=str(config))
        child = subprocess.Popen([sys.executable, str(APP / 'desktop.py'), '--headless', '--repo', str(self.repo),
                                  '--port', '0', '--no-browser', '--ready-file', str(ready), '--exit-after', '1.5'],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', env=env)
        try:
            deadline = time.monotonic() + 10
            while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.03)
            self.assertTrue(ready.exists(), 'Launcher did not publish its ready file')
            metadata = json.loads(ready.read_text('utf-8'))
            self.assertEqual(set(metadata), {'url', 'repo'})
            self.assertEqual(Path(metadata['repo']).resolve(), self.repo.resolve())
            self.assertTrue(metadata['url'].startswith('http://127.0.0.1:'))
            with urlopen(metadata['url'] + '/api/bootstrap', timeout=5) as response:
                self.assertEqual(json.load(response)['site']['title'], 'Desktop fixture')
            stdout, stderr = child.communicate(timeout=10)
            self.assertEqual(child.returncode, 0, stderr)
            self.assertNotIn('token', stdout)
            self.assertFalse(ready.exists())
        finally:
            if child.poll() is None:
                child.terminate()
                child.communicate(timeout=10)

    def check_headless_with_cp1252_stream(self, stream_type=io.TextIOWrapper):
        repo = self.make_repository('내 블로그')
        output_bytes, error_bytes = io.BytesIO(), io.BytesIO()
        output = stream_type(output_bytes, encoding='cp1252', errors='strict')
        errors = stream_type(error_bytes, encoding='cp1252', errors='strict')
        old_handlers = list(desktop.LOGGER.handlers)
        ready = self.root / 'ready.json'
        real_ready = desktop.write_ready_file
        def check_http(path, process):
            self.assertEqual(self.request(process)['site']['title'], 'Desktop fixture')
            real_ready(path, process)
        try:
            with patch.object(sys, 'stdout', output), patch.object(sys, 'stderr', errors), \
                    patch.object(desktop, 'config_directory', return_value=self.root / 'profile'), \
                    patch.object(desktop, 'write_ready_file', side_effect=check_http) as signal_ready:
                code = desktop.main(['--headless', '--repo', str(repo), '--no-browser', '--port', '0',
                                     '--ready-file', str(ready), '--exit-after', '0'])
                output.flush()
                errors.flush()
            self.assertEqual(code, 0, error_bytes.getvalue().decode('utf-8', errors='replace'))
            signal_ready.assert_called_once()
            # The protocol itself is ASCII-safe, independent of pipe encoding.
            protocol = [line for line in output_bytes.getvalue().decode('ascii').splitlines()
                        if line.startswith('{"url"')]
            self.assertEqual(len(protocol), 1)
            self.assertIn('\\ub0b4', protocol[0])
            metadata = json.loads(protocol[0])
            self.assertEqual(Path(metadata['repo']).resolve(), repo.resolve())
            self.assertFalse(ready.exists())
            return output.encoding, errors.encoding
        finally:
            for handler in list(desktop.LOGGER.handlers):
                if handler not in old_handlers:
                    desktop.LOGGER.removeHandler(handler)
                    handler.close()
            output.close()
            errors.close()

    def test_headless_korean_path_reconfigures_windows_cp1252_pipes(self):
        self.assertEqual(self.check_headless_with_cp1252_stream(), ('utf-8', 'utf-8'))

    def test_headless_json_survives_non_reconfigurable_windows_pipe(self):
        class FixedEncodingStream(io.TextIOWrapper):
            def reconfigure(self, **_):
                raise OSError('Captured pipe cannot be reconfigured')
        self.assertEqual(self.check_headless_with_cp1252_stream(FixedEncodingStream), ('cp1252', 'cp1252'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
