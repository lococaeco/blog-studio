"""Portable replacement tests use synthetic bytes and temporary paths only."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import updater


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='blog-studio-updater-')
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.target = self.root / 'BlogStudio.AppImage'
        self.target.write_bytes(b'old executable fixture')
        self.target.chmod(0o755)
        self.update = updater.Updater(target=self.target, cache=self.root / 'cache', current='0.1.1')
        self.asset = b'\x7fELF\x02\x01\x01\x00AI\x02' + b'fixture new binary bytes'
        self.hash = hashlib.sha256(self.asset).hexdigest()
        self.asset_name = 'BlogStudio-x86_64.AppImage'
        self.prefix = updater.RELEASES_URL + '/download/v0.2.0/'
        self.release = {'tag_name': 'v0.2.0', 'draft': False, 'prerelease': False, 'assets': [
            {'name': self.asset_name, 'browser_download_url': self.prefix + self.asset_name, 'size': len(self.asset)},
            {'name': self.asset_name + '.sha256', 'browser_download_url': self.prefix + self.asset_name + '.sha256', 'size': 93}]}

    def fetch(self, url, limit, destination=None):
        if '/releases/latest' in url:
            return json.dumps(self.release).encode()
        if url.endswith('.sha256'):
            return (self.hash + '  ' + self.asset_name + '\n').encode()
        if destination:
            destination.write(self.asset)
            return len(self.asset)
        return self.asset

    def test_check_and_verified_download_leave_current_binary_unchanged(self):
        with patch('updater.asset_name', return_value=self.asset_name), patch('updater.download_bytes', side_effect=self.fetch):
            status = self.update.download()
        self.assertTrue(status['available'])
        self.assertTrue(status['ready'])
        self.assertEqual(self.target.read_bytes(), b'old executable fixture')
        self.assertEqual(Path(self.update.prepared['source']).read_bytes(), self.asset)

    def test_missing_checksum_or_foreign_asset_is_rejected(self):
        self.release['assets'][0]['browser_download_url'] = 'https://example.invalid/evil.exe'
        with patch('updater.asset_name', return_value=self.asset_name), patch('updater.download_bytes', side_effect=self.fetch):
            with self.assertRaises(updater.UpdateError):
                self.update.check()

    def test_bad_download_hash_does_not_replace_or_prepare(self):
        self.hash = '0' * 64
        with patch('updater.asset_name', return_value=self.asset_name), patch('updater.download_bytes', side_effect=self.fetch):
            with self.assertRaises(updater.UpdateError):
                self.update.download()
        self.assertIsNone(self.update.prepared)
        self.assertEqual(self.target.read_bytes(), b'old executable fixture')

    def manifest(self):
        source = self.root / 'new.AppImage'
        source.write_bytes(self.asset)
        manifest = self.root / 'manifest.json'
        payload = {'pid': 99999999, 'source': str(source), 'target': str(self.target),
                   'backup': str(self.root / 'old.AppImage'), 'result': str(self.root / 'result.json'),
                   'sha256': self.hash, 'old_sha256': updater.sha256(self.target), 'version': '0.2.0', 'repo': ''}
        manifest.write_text(json.dumps(payload))
        return manifest, payload

    def test_helper_waits_and_atomically_replaces_with_backup(self):
        manifest, payload = self.manifest()
        with patch('updater.pid_alive', return_value=False):
            result = updater.perform_update(manifest, launch=False)
        self.assertEqual(result, '0.2.0')
        self.assertEqual(self.target.read_bytes(), self.asset)
        self.assertEqual(Path(payload['backup']).read_bytes(), b'old executable fixture')
        self.assertTrue(json.loads(Path(payload['result']).read_text())['success'])

    def test_running_app_prevents_replacement(self):
        manifest, payload = self.manifest()
        with patch('updater.pid_alive', return_value=True):
            with self.assertRaises(updater.UpdateError):
                updater.perform_update(manifest, wait_seconds=0, launch=False)
        self.assertFalse(Path(payload['backup']).exists())
        self.assertEqual(self.target.read_bytes(), b'old executable fixture')

    def test_tampered_download_or_target_prevents_replacement(self):
        manifest, payload = self.manifest()
        Path(payload['source']).write_bytes(b'tampered')
        with patch('updater.pid_alive', return_value=False):
            with self.assertRaises(updater.UpdateError):
                updater.perform_update(manifest, launch=False)
        self.assertEqual(self.target.read_bytes(), b'old executable fixture')

    def test_existing_backup_is_never_overwritten(self):
        manifest, payload = self.manifest()
        Path(payload['backup']).write_bytes(b'keep me')
        with patch('updater.pid_alive', return_value=False):
            with self.assertRaises(updater.UpdateError):
                updater.perform_update(manifest, launch=False)
        self.assertEqual(Path(payload['backup']).read_bytes(), b'keep me')

    def test_launch_failure_restores_old_executable_and_retains_backup(self):
        manifest, payload = self.manifest()
        with patch('updater.pid_alive', return_value=False), patch('updater.subprocess.Popen', side_effect=OSError('cannot execute')):
            with self.assertRaisesRegex(updater.UpdateError, '복원했습니다'):
                updater.perform_update(manifest)
        self.assertEqual(self.target.read_bytes(), b'old executable fixture')
        self.assertEqual(Path(payload['backup']).read_bytes(), b'old executable fixture')
        self.assertFalse(Path(payload['result']).exists())

    def test_new_process_has_reset_environment_and_preserves_repo(self):
        manifest, payload = self.manifest()
        payload['repo'] = str(self.root / '한글 블로그')
        manifest.write_text(json.dumps(payload))
        with patch('updater.pid_alive', return_value=False), patch('updater.subprocess.Popen') as popen:
            updater.perform_update(manifest)
        self.assertEqual(popen.call_args.args[0], [str(self.target), '--repo', payload['repo']])
        self.assertEqual(popen.call_args.kwargs['env']['PYINSTALLER_RESET_ENVIRONMENT'], '1')
        self.assertTrue(popen.call_args.kwargs['close_fds'])

    def test_helper_dispatch_is_opt_in_and_semver_is_numeric(self):
        self.assertIsNone(updater.maybe_handle_helper(['--headless']))
        self.assertEqual(updater.maybe_handle_helper(['--apply-update-manifest']), 2)
        self.assertGreater(updater.version_tuple('v0.10.0'), updater.version_tuple('v0.2.9'))
        with self.assertRaises(updater.UpdateError):
            updater.version_tuple('v0.2.0-beta')


if __name__ == '__main__':
    unittest.main()
