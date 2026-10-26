"""Desktop/CLI lifetime regression tests; all repositories are disposable."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.request import urlopen

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
import runtime

spec = importlib.util.spec_from_file_location('studio_runtime_server', APP / 'server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='studio-runtime-')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / '한글 블로그'
        self.repo.mkdir()
        (self.repo / '_posts').mkdir()
        (self.repo / '_config.yml').write_text('title: 테스트\n', encoding='utf-8')

    def test_lock_blocks_other_process_and_keeps_same_inode_after_release(self):
        script = (
            'import sys; from runtime import RepositoryLock, RepositoryInUseError\n'
            'try:\n with RepositoryLock(sys.argv[1]): pass\n'
            'except RepositoryInUseError: sys.exit(23)\n'
        )
        environment = dict(os.environ, PYTHONPATH=str(APP))
        def child():
            return subprocess.run([sys.executable, '-c', script, str(self.repo)],
                                  env=environment, capture_output=True, timeout=10).returncode
        lock = runtime.RepositoryLock(self.repo).acquire()
        self.addCleanup(lock.release)
        inode = (self.repo / '.blog-studio/instance.lock').stat().st_ino
        self.assertEqual(child(), 23)
        lock.release()
        self.assertEqual(child(), 0)
        self.assertEqual((self.repo / '.blog-studio/instance.lock').stat().st_ino, inode)

    def test_crashed_process_does_not_leave_stale_lock(self):
        script = 'import os,sys; from runtime import RepositoryLock; lock=RepositoryLock(sys.argv[1]).acquire(); os._exit(0)'
        result = subprocess.run([sys.executable, '-c', script, str(self.repo)],
                                env=dict(os.environ, PYTHONPATH=str(APP)), timeout=10)
        self.assertEqual(result.returncode, 0)
        with runtime.RepositoryLock(self.repo):
            pass

    def test_symlink_storage_and_lock_are_rejected(self):
        outside = Path(self.temp.name) / 'outside'
        outside.mkdir()
        storage = self.repo / '.blog-studio'
        try:
            storage.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('Platform cannot create symlinks without extra privileges')
        with self.assertRaises((RuntimeError, OSError)):
            runtime.RepositoryLock(self.repo).acquire()
        self.assertEqual(list(outside.iterdir()), [])
        storage.unlink()
        storage.mkdir()
        target = outside / 'existing'
        target.write_text('untouched', encoding='utf-8')
        (storage / 'instance.lock').symlink_to(target)
        with self.assertRaises((RuntimeError, OSError)):
            runtime.RepositoryLock(self.repo).acquire()
        self.assertEqual(target.read_text(), 'untouched')

    def test_two_servers_cannot_edit_same_repository(self):
        http = server.create_server(self.repo)
        self.addCleanup(http.server_close)
        with self.assertRaises(server.StudioError) as result:
            server.create_server(self.repo)
        self.assertEqual(result.exception.status, 409)
        http.server_close()
        replacement = server.create_server(self.repo)
        replacement.server_close()

    def test_hardlinked_lock_does_not_lock_or_modify_external_file(self):
        storage = self.repo / '.blog-studio'
        storage.mkdir()
        target = Path(self.temp.name) / 'outside.txt'
        target.write_text('untouched', encoding='utf-8')
        os.link(target, storage / 'instance.lock')
        with self.assertRaises(RuntimeError):
            runtime.RepositoryLock(self.repo).acquire()
        self.assertEqual(target.read_text(), 'untouched')

    def test_bind_failure_releases_lock_for_port_fallback(self):
        import socket
        with socket.socket() as occupied:
            occupied.bind(('127.0.0.1', 0))
            occupied.listen()
            with self.assertRaises(OSError):
                server.create_server(self.repo, occupied.getsockname()[1])
            http = server.create_server(self.repo, 0)
            http.server_close()

    def test_distinct_repositories_cannot_share_a_listening_port(self):
        import socket
        first = server.create_server(self.repo, 0)
        self.addCleanup(first.server_close)
        other = Path(self.temp.name) / '다른 블로그'
        other.mkdir()
        (other / '_posts').mkdir()
        (other / '_config.yml').write_text('title: Other\n', encoding='utf-8')
        if os.name == 'nt':
            self.assertFalse(first.allow_reuse_address)
            self.assertEqual(first.socket.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE), 1)
        with self.assertRaises(OSError):
            server.create_server(other, first.server_port)
        fallback = server.create_server(other, 0)
        self.addCleanup(fallback.server_close)
        self.assertNotEqual(first.server_port, fallback.server_port)

    def test_initialization_failure_releases_lock(self):
        (self.repo / '_config.yml').write_text('title: [', encoding='utf-8')
        with self.assertRaises(Exception):
            server.create_server(self.repo)
        with runtime.RepositoryLock(self.repo):
            pass

    def test_invalid_repository_does_not_create_storage(self):
        (self.repo / '_config.yml').unlink()
        with self.assertRaises(server.StudioError):
            server.create_server(self.repo)
        self.assertFalse((self.repo / '.blog-studio').exists())

    def test_shutdown_waits_for_request_before_releasing_repository(self):
        http = server.create_server(self.repo)
        entered, finish, closed = threading.Event(), threading.Event(), threading.Event()
        def bootstrap():
            entered.set()
            finish.wait(5)
            (self.repo / '.blog-studio/completed').write_text('saved', encoding='utf-8')
            return {}
        http.studio.bootstrap = bootstrap
        worker = threading.Thread(target=http.serve_forever, kwargs={'poll_interval': 0.01})
        worker.start()
        def request():
            with urlopen(f'http://127.0.0.1:{http.server_port}/api/bootstrap', timeout=8) as response:
                response.read()
        client = threading.Thread(target=request)
        client.start()
        self.assertTrue(entered.wait(3))
        def close():
            http.shutdown()
            http.server_close()
            closed.set()
        closer = threading.Thread(target=close)
        closer.start()
        second_closed = threading.Event()
        second_closer = None
        try:
            self.assertFalse(closed.wait(0.15))
            def close_again():
                http.server_close()
                second_closed.set()
            second_closer = threading.Thread(target=close_again)
            second_closer.start()
            self.assertFalse(second_closed.wait(0.1))
            with self.assertRaises(runtime.RepositoryInUseError):
                runtime.RepositoryLock(self.repo).acquire()
        finally:
            finish.set()
            client.join(5)
            closer.join(5)
            if second_closer is not None:
                second_closer.join(5)
            worker.join(5)
        self.assertTrue(closed.is_set())
        self.assertTrue(second_closed.is_set())
        self.assertTrue((self.repo / '.blog-studio/completed').is_file())
        with runtime.RepositoryLock(self.repo):
            pass

    def test_external_environment_restores_only_child_paths(self):
        environment = {'LD_LIBRARY_PATH': '/bundle', 'LD_LIBRARY_PATH_ORIG': '/system',
                       'LIBPATH': '/bundle', 'PATH': os.pathsep.join(['/bundle', '/bundle/bin', '/usr/bin']),
                       'GIT_TERMINAL_PROMPT': '0'}
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, '_MEIPASS', '/bundle', create=True):
            cleaned = runtime.external_environment(environment)
        self.assertEqual(cleaned['LD_LIBRARY_PATH'], '/system')
        self.assertNotIn('LIBPATH', cleaned)
        self.assertEqual(cleaned['PATH'], '/usr/bin')
        self.assertEqual(cleaned['GIT_TERMINAL_PROMPT'], '0')
        self.assertEqual(environment['LD_LIBRARY_PATH'], '/bundle')

    def test_browser_environment_is_restored_even_after_error(self):
        with patch.dict(os.environ, {'LD_LIBRARY_PATH': '/bundle', 'LD_LIBRARY_PATH_ORIG': '/system'}):
            with patch.object(sys, 'frozen', True, create=True):
                with self.assertRaises(ValueError):
                    with runtime.external_program_environment():
                        self.assertEqual(os.environ['LD_LIBRARY_PATH'], '/system')
                        raise ValueError('browser unavailable')
            self.assertEqual(os.environ['LD_LIBRARY_PATH'], '/bundle')

    def test_nonfrozen_environment_is_unchanged(self):
        environment = {'LD_LIBRARY_PATH': '/custom'}
        with patch.object(sys, 'frozen', False, create=True):
            self.assertEqual(runtime.external_environment(environment), environment)


if __name__ == '__main__':
    unittest.main()
