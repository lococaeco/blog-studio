"""Backup, sync and URL boundaries using disposable data and mocked GitHub."""
from __future__ import annotations

import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import socket
import zipfile
from unittest.mock import patch

from test_server import RepositoryCase, server, POST_PATH
from studio_features import FeatureError, Features, public_address


class FakeGitHub:
    def __init__(self, private=True):
        self.private = private
        self.files = {}
        self.calls = []

    def request(self, method, path, payload=None, allow_missing=False):
        self.calls.append((method, path, copy.deepcopy(payload)))
        if path == '/user':
            return {'login': 'fixture-user'}
        if path == '/repos/fixture-user/blog-studio-drafts':
            return None if self.private is None else {'private': self.private}
        if path == '/user/repos' and method == 'POST':
            self.private = payload['private']
            return {'private': self.private}
        if '/contents/' in path:
            if method == 'GET':
                if path in self.files:
                    return copy.deepcopy(self.files[path])
                if allow_missing:
                    return None
                raise AssertionError('Missing fixture content: ' + path)
            if method == 'PUT':
                self.files[path] = {'content': payload['content'], 'sha': hashlib.sha256(payload['content'].encode()).hexdigest()}
                return {'content': self.files[path]}
        raise AssertionError('Unexpected mocked GitHub call: ' + method + ' ' + path)


class FeatureTests(RepositoryCase):
    @property
    def features(self):
        return self.studio.features

    def draft(self, **fields):
        return self.studio.save_draft({'title': '보존할 초안', 'body': '비공개 초안 내용', 'slug': 'private-draft', **fields})

    def packed(self, files):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('blog-studio-backup.json', json.dumps({'format': 1}))
            for path, content in files.items():
                info = zipfile.ZipInfo()
                # Preserve hostile archive bytes instead of letting ZipInfo's
                # constructor sanitize them on the runner's operating system.
                info.filename = info.orig_filename = path
                archive.writestr(info, json.dumps(content, ensure_ascii=False) if isinstance(content, (dict, list)) else content)
        return stream.getvalue()

    def test_archive_excludes_credentials_and_repository_configuration(self):
        draft = self.draft()
        self.write('.env', 'FIXTURE_SECRET=not-for-backup')
        self.write('.blog-studio/token.txt', 'not-for-backup')
        data = self.features.archive()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = archive.namelist()
            self.assertIn(POST_PATH, names)
            self.assertIn(f'.blog-studio/drafts/{draft["id"]}.json', names)
            self.assertNotIn('.git/config', names)
            self.assertNotIn('_config.yml', names)
            self.assertNotIn('.env', names)
            self.assertNotIn('.blog-studio/token.txt', names)
        with zipfile.ZipFile(io.BytesIO(self.features.archive(drafts_only=True))) as archive:
            self.assertFalse(any(name.startswith(('_posts/', 'assets/images/')) for name in archive.namelist()))

    def test_full_restore_keeps_draft_linked_to_matching_restored_post(self):
        draft = self.studio.open_post(POST_PATH)
        backup = self.features.archive()
        original = (self.repo / POST_PATH).read_bytes()
        (self.repo / POST_PATH).unlink()
        self.studio._draft_path(draft['id']).unlink()
        self.features.restore_archive(backup)
        restored = self.studio.get_draft(draft['id'])
        self.assertEqual(restored['source_path'], POST_PATH)
        self.assertEqual(restored['source_revision'], hashlib.sha256(original).hexdigest())
        self.assertEqual((self.repo / POST_PATH).read_bytes(), original)
        self.assertEqual(restored['_meta'], draft['_meta'])

    def test_invalid_structured_content_rejects_entire_restore_before_write(self):
        new_post = '_posts/ai/llm/2025-01-01-should-not-exist.md'
        raw = self.packed({new_post: '# Would write without prevalidation',
                           '.blog-studio/drafts/' + 'a' * 32 + '.json': {'id': 'a' * 32, 'body': []}})
        with self.assertRaises((FeatureError, server.StudioError)):
            self.features.restore_archive(raw)
        self.assertFalse((self.repo / new_post).exists())
        self.assertEqual(list((self.repo / '.blog-studio/drafts').glob('*.json')), [])

    def test_archive_traversal_is_rejected_without_writing(self):
        for path in ('../outside.md', '_posts/../../outside.md', '/tmp/outside.md', '.git/config', '_posts\\outside.md'):
            with self.subTest(path=path):
                with self.assertRaises(FeatureError):
                    self.features.restore_archive(self.packed({path: 'malicious fixture'}))

    def test_conflicting_drafts_and_uploads_are_copied_and_repeat_restore_is_idempotent(self):
        first = self.draft()
        old_id = first['id']
        first = self.update(first, body=f'![설명](/assets/images/studio-{old_id}/photo.png)')
        self.write(f'.blog-studio/uploads/{old_id}/photo.png', b'original fixture image')
        backup = self.features.archive(drafts_only=True)
        changed = self.update(first, body='이 컴퓨터에서 계속 작성한 내용')
        result = self.features.restore_archive(backup, drafts_only=True)
        self.assertEqual(result['drafts'], 1)
        self.assertEqual(result['conflicts'], 1)
        self.assertEqual(self.studio.get_draft(old_id)['body'], changed['body'])
        ids = [p.stem for p in (self.repo / '.blog-studio/drafts').glob('*.json') if p.stem != old_id]
        self.assertEqual(len(ids), 1)
        imported = self.studio.get_draft(ids[0])
        self.assertIn('studio-' + ids[0] + '/photo.png', imported['body'])
        self.assertEqual((self.repo / f'.blog-studio/uploads/{ids[0]}/photo.png').read_bytes(), b'original fixture image')
        self.assertEqual(self.features.restore_archive(backup, drafts_only=True)['drafts'], 0)
        self.assertEqual(len(list((self.repo / '.blog-studio/drafts').glob('*.json'))), 2)

    def test_conflicting_published_file_preserves_local_and_keeps_import_backup(self):
        local = (self.repo / POST_PATH).read_bytes()
        incoming = b'# Incoming published content'
        result = self.features.restore_archive(self.packed({POST_PATH: incoming}))
        self.assertEqual(result['conflicts'], 1)
        self.assertEqual((self.repo / POST_PATH).read_bytes(), local)
        kept = list((self.repo / '.blog-studio/backups').rglob(Path(POST_PATH).name))
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].read_bytes(), incoming)

    def test_edited_derived_draft_is_preserved_when_original_archive_is_reimported(self):
        first = self.draft()
        first = self.update(first, body=f'원본 ![사진](/assets/images/studio-{first["id"]}/photo.png)')
        archive = self.features.archive(drafts_only=True)
        self.update(first, body='원래 초안의 로컬 수정')
        self.features.restore_archive(archive, drafts_only=True)
        copied_id = next(path.stem for path in (self.repo / '.blog-studio/drafts').glob('*.json') if path.stem != first['id'])
        copied = self.studio.get_draft(copied_id)
        copied = self.update(copied, body=copied['body'] + '\n충돌 복사본에서 추가 수정')
        self.features.restore_archive(archive, drafts_only=True)
        self.assertEqual(self.studio.get_draft(copied_id)['body'], copied['body'])
        self.assertEqual(self.studio.get_draft(first['id'])['body'], '원래 초안의 로컬 수정')
        self.assertEqual(len(list((self.repo / '.blog-studio/drafts').glob('*.json'))), 3)
        self.assertEqual(self.features.restore_archive(archive, drafts_only=True)['drafts'], 0)
        self.assertEqual(len(list((self.repo / '.blog-studio/drafts').glob('*.json'))), 3)

    def test_newer_remote_history_cannot_overwrite_next_local_save(self):
        draft = self.draft()
        later = dict(draft, version=draft['version'] + 1, body='다른 컴퓨터의 저장 기록')
        path = f'.blog-studio/history/{draft["id"]}/{later["version"]:010}.json'
        self.features.restore_archive(self.packed({f'.blog-studio/drafts/{draft["id"]}.json': draft, path: later}), drafts_only=True)
        held = self.repo / '.blog-studio/backups/import-history' / path
        self.assertTrue(held.exists())
        updated = self.update(self.studio.get_draft(draft['id']), body='새 로컬 저장')
        self.assertEqual(updated['version'], later['version'])
        self.assertEqual(json.loads(held.read_text('utf-8'))['body'], later['body'])
        self.assertEqual(json.loads((self.repo / path).read_text('utf-8'))['body'], '새 로컬 저장')

    def test_history_diff_is_read_only(self):
        draft = self.draft(body='처음 본문')
        updated = self.update(draft, body='바뀐 본문')
        result = self.features.history_diff({'id': draft['id'], 'history_id': f'{draft["version"]:010}'})
        self.assertTrue(result['changed'])
        self.assertIn('-처음 본문', result['diff'])
        self.assertIn('+바뀐 본문', result['diff'])
        self.assertEqual(self.studio.get_draft(draft['id'])['version'], updated['version'])

    def test_public_drafts_repository_receives_no_private_content(self):
        self.draft()
        cloud = FakeGitHub(private=False)
        self.features.cloud = cloud
        with self.assertRaisesRegex(FeatureError, '공개 저장소'):
            self.features.sync()
        self.assertEqual([method for method, _, _ in cloud.calls], ['GET', 'GET'])
        self.assertEqual(cloud.files, {})

    def test_sync_creates_private_repo_and_uploads_verified_draft_archive_only(self):
        draft = self.draft()
        cloud = FakeGitHub(private=None)
        self.features.cloud = cloud
        result = self.features.sync()
        self.assertEqual(result['repository'], 'fixture-user/blog-studio-drafts')
        creation = next(payload for method, path, payload in cloud.calls if path == '/user/repos')
        self.assertIs(creation['private'], True)
        manifest_path, document = next((path, item) for path, item in cloud.files.items() if path.endswith('/latest.json'))
        manifest = json.loads(base64.b64decode(document['content']))
        prefix = manifest_path.removesuffix('/latest.json')
        raw = b''.join(base64.b64decode(cloud.files[prefix + '/' + manifest['digest'] + f'/{index}.bin']['content']) for index in range(manifest['chunks']))
        self.assertEqual(hashlib.sha256(raw).hexdigest(), manifest['digest'])
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            self.assertIn(f'.blog-studio/drafts/{draft["id"]}.json', archive.namelist())
            self.assertFalse(any(name.startswith('_posts/') for name in archive.namelist()))
        self.assertEqual(self.features.sync()['conflicts'], 0)
        manifest_writes = [payload for method, path, payload in cloud.calls if method == 'PUT' and path.endswith('/latest.json')]
        self.assertNotIn('sha', manifest_writes[0])
        self.assertIn('sha', manifest_writes[1])

    def test_sync_hash_failure_preserves_local_draft_and_does_not_upload(self):
        draft = self.draft()
        cloud = FakeGitHub()
        self.features.cloud = cloud
        self.features.sync()
        chunk = next(item for path, item in cloud.files.items() if path.endswith('/0.bin'))
        chunk['content'] = base64.b64encode(b'corrupted fixture').decode()
        receiver = self.second_device(cloud)
        local = receiver.save_draft({'title': '받는 컴퓨터의 초안', 'body': '보존해야 하는 로컬 내용'})
        cloud.calls.clear()
        # Even the device that already knows the manifest must validate bytes.
        with self.assertRaisesRegex(FeatureError, '검증에 실패'):
            self.features.sync()
        with self.assertRaisesRegex(FeatureError, '검증에 실패'):
            receiver.features.sync()
        self.assertEqual(receiver.get_draft(local['id'])['body'], local['body'])
        self.assertEqual(self.studio.get_draft(draft['id'])['body'], draft['body'])
        self.assertFalse(any(method == 'PUT' for method, _, _ in cloud.calls))

    def test_sync_after_local_edit_does_not_fork_our_own_previous_snapshot(self):
        draft = self.draft()
        cloud = FakeGitHub()
        self.features.cloud = cloud
        self.features.sync()
        self.update(draft, body='같은 컴퓨터에서 이어 쓴 내용')
        # A fresh Features object also exercises durable sync state after restart.
        restarted = Features(self.studio, cloud)
        result = restarted.sync()
        self.assertEqual(result['conflicts'], 0)
        self.assertEqual(len(list((self.repo / '.blog-studio/drafts').glob('*.json'))), 1)
        self.assertEqual(self.studio.get_draft(draft['id'])['body'], '같은 컴퓨터에서 이어 쓴 내용')

    def test_reimport_template_never_overwrites_a_previously_edited_conflict_copy(self):
        original = self.features.save_template({'label': '템플릿', 'body': '원본 내용'})
        archive = self.packed({'.blog-studio/templates.json': [original]})
        self.features.save_template(dict(original, body='이 컴퓨터의 수정'))
        self.features.restore_archive(archive)
        copied = next(item for item in self.features.templates()['items'] if item['id'] != original['id'])
        self.features.save_template(dict(copied, body='충돌 복사본에서도 추가 수정'))
        self.features.restore_archive(archive)
        templates = self.features.templates()['items']
        self.assertEqual(next(item['body'] for item in templates if item['id'] == copied['id']), '충돌 복사본에서도 추가 수정')
        self.assertTrue(any(item['body'] == '원본 내용' for item in templates))

    def second_device(self, cloud):
        location = self.repo.parent / 'second-computer'
        shutil.copytree(self.repo, location, ignore=shutil.ignore_patterns('.git', '.blog-studio'))
        second = server.Studio(location)
        second.features.cloud = cloud
        return second

    def test_remote_edit_updates_unchanged_local_draft_without_conflict_copy(self):
        draft = self.draft()
        cloud = FakeGitHub()
        self.features.cloud = cloud
        self.features.sync()
        second = self.second_device(cloud)
        second.features.sync()
        remote = second.get_draft(draft['id'])
        second.save_draft(dict(remote, body='다른 컴퓨터에서 이어 쓴 내용'))
        second.features.sync()
        result = self.features.sync()
        self.assertEqual(result['conflicts'], 0)
        self.assertEqual(len(list((self.repo / '.blog-studio/drafts').glob('*.json'))), 1)
        self.assertEqual(self.studio.get_draft(draft['id'])['body'], '다른 컴퓨터에서 이어 쓴 내용')

    def test_true_concurrent_edits_keep_both_bodies_and_do_not_duplicate_on_retry(self):
        draft = self.draft()
        cloud = FakeGitHub()
        self.features.cloud = cloud
        self.features.sync()
        second = self.second_device(cloud)
        second.features.sync()
        self.update(draft, body='첫 번째 컴퓨터의 독립 수정')
        second.save_draft(dict(second.get_draft(draft['id']), body='두 번째 컴퓨터의 독립 수정'))
        second.features.sync()
        result = self.features.sync()
        self.assertGreaterEqual(result['conflicts'], 1)
        drafts = [json.loads(path.read_text('utf-8')) for path in (self.repo / '.blog-studio/drafts').glob('*.json')]
        self.assertEqual({item['body'] for item in drafts}, {'첫 번째 컴퓨터의 독립 수정', '두 번째 컴퓨터의 독립 수정'})
        self.assertEqual(self.studio.get_draft(draft['id'])['body'], '첫 번째 컴퓨터의 독립 수정')
        restarted = Features(self.studio, cloud)
        self.assertEqual(restarted.sync()['conflicts'], 0)
        self.assertEqual(len(list((self.repo / '.blog-studio/drafts').glob('*.json'))), 2)

    def test_public_address_rejects_private_mixed_dns_and_special_schemes(self):
        for address in ('127.0.0.1', '10.0.0.1', '169.254.169.254', '::1', 'fc00::1'):
            with self.subTest(address=address), patch('studio_features.socket.getaddrinfo', return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, '', (address, 443))]):
                with self.assertRaises(FeatureError):
                    public_address('https://example.invalid/page')
        mixed = [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('1.1.1.1', 443)), (socket.AF_INET, socket.SOCK_STREAM, 6, '', ('192.168.1.1', 443))]
        with patch('studio_features.socket.getaddrinfo', return_value=mixed):
            with self.assertRaises(FeatureError):
                public_address('https://example.invalid/')
        for url in ('file:///etc/passwd', 'https://user:secret@example.invalid/', 'http://example.invalid:4310/'):
            with self.subTest(url=url):
                with self.assertRaises(FeatureError):
                    public_address(url)
        with patch('studio_features.socket.getaddrinfo', return_value=mixed[:1]):
            parsed, port, address = public_address('https://example.invalid/page')
        self.assertEqual((parsed.hostname, port, address), ('example.invalid', 443, '1.1.1.1'))


if __name__ == '__main__':
    import unittest
    unittest.main()
