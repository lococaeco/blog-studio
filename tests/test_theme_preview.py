"""Theme builds use disposable repositories; no developer blog or gems change."""
from __future__ import annotations

import base64
import copy
import http.client
import os
from pathlib import Path
import shutil
import subprocess
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

import yaml
import test_server as fixtures
import theme_preview


class ThemePreviewTests(fixtures.RepositoryCase):
    def setUp(self):
        super().setUp()
        self.preview = self.studio.preview
        self.addCleanup(self.preview.close)
        self.builds = []

    def draft(self):
        draft = self.studio.save_draft({'title': '미래의 비공개 초안', 'date': '2099-04-05',
                                        'category': 'ai/llm', 'published': False, 'body': '본문'})
        image = self.studio.upload({'draft_id': draft['id'], 'name': 'preview.png',
                                    'data': base64.b64encode(fixtures.PNG).decode()})
        return self.update(draft, body=f'실제 테마 본문\n\n![그림]({image["url"]})\n\n<img src="{image["url"]}">'), image

    def fake_build(self, command, **kwargs):
        source = Path(kwargs['cwd'])
        output = Path(command[command.index('--destination') + 1])
        config = yaml.safe_load((source / '_config.yml').read_text('utf-8'))
        post = next(path for path in (source / '_posts').rglob('*.md') if path.name.startswith('2099-'))
        content = post.read_text('utf-8')
        self.builds.append({'source': source, 'output': output, 'config': config, 'post_path': post,
                            'post': content, 'environment': kwargs['env']})
        (output / '__studio_preview__').mkdir(parents=True)
        (output / '__studio_preview__/index.html').write_text('<h1>미리보기</h1>', 'utf-8')
        shutil.copytree(source / 'assets', output / 'assets')
        return SimpleNamespace(returncode=0, stdout='', stderr='')

    def build(self, draft):
        with patch('theme_preview.shutil.which', side_effect=lambda name: '/fake/' + name), \
                patch('theme_preview.run_external', side_effect=self.fake_build):
            return self.preview.build(draft['id'])

    def request(self, url, method='GET', host=None):
        parsed = urlsplit(url)
        connection = http.client.HTTPConnection('127.0.0.1', parsed.port, timeout=3)
        try:
            connection.request(method, parsed.path, headers={'Host': host or parsed.netloc})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_future_private_draft_and_upload_build_only_in_copy(self):
        draft, image = self.draft()
        original_config = (self.repo / '_config.yml').read_bytes()
        original_draft = copy.deepcopy(self.studio.get_draft(draft['id']))
        self.write('vendor/cache/ignored.txt', 'ignored')
        self.write('node_modules/dependency/ignored.txt', 'ignored')
        result = self.build(draft)
        build = self.builds[-1]
        self.assertNotEqual(build['source'], self.repo)
        self.assertTrue(build['config']['future'])
        self.assertEqual(build['config']['analytics'], {'provider': False})
        self.assertTrue(build['config']['baseurl'].startswith('/'))
        metadata, _, body = fixtures.server.split_post(build['post'])
        self.assertTrue(metadata['published'])
        self.assertEqual(metadata['permalink'], '/__studio_preview__/')
        self.assertTrue(build['post_path'].name.startswith('2099-04-05-'))
        prefix = build['config']['baseurl']
        self.assertIn(f']({prefix}{image["url"]})', body)
        self.assertIn(f'src="{prefix}{image["url"]}"', body)
        copied_image = build['source'] / image['url'].lstrip('/')
        self.assertEqual(copied_image.read_bytes(), fixtures.PNG)
        self.assertFalse((build['source'] / '.blog-studio').exists())
        self.assertFalse((build['source'] / '.git').exists())
        self.assertFalse((build['source'] / 'vendor').exists())
        self.assertFalse((build['source'] / 'node_modules').exists())
        self.assertEqual((self.repo / '_config.yml').read_bytes(), original_config)
        self.assertEqual(self.studio.get_draft(draft['id']), original_draft)
        self.assertFalse((self.repo / self.studio._target(draft)).exists())
        self.assertEqual(self.request(result['url'])[0], 200)
        preview_image = result['url'].split(prefix)[0] + prefix + image['url']
        self.assertEqual(self.request(preview_image)[2], fixtures.PNG)

    def test_get_and_head_require_both_token_and_host(self):
        draft, _ = self.draft()
        result = self.build(draft)
        parsed = urlsplit(result['url'])
        for method in ('GET', 'HEAD'):
            good = self.request(result['url'], method)
            self.assertEqual(good[0], 200)
            self.assertEqual(good[1]['Referrer-Policy'], 'no-referrer')
            self.assertNotIn('Access-Control-Allow-Origin', good[1])
            if method == 'HEAD':
                self.assertEqual(good[2], b'')
            self.assertEqual(self.request(result['url'], method, 'evil.example')[0], 404)
            self.assertEqual(self.request(f'http://{parsed.netloc}/__studio_preview__/', method)[0], 404)
            self.assertEqual(self.request(f'http://{parsed.netloc}/wrong/__studio_preview__/', method)[0], 404)

    def test_timeout_closes_unserved_socket_and_keeps_previous_preview(self):
        draft, _ = self.draft()
        previous = self.build(draft)
        previous_http, previous_folder = self.preview.http, self.preview.folder.name
        created = []
        original_server = theme_preview.ThreadingHTTPServer

        def server(*args, **kwargs):
            value = original_server(*args, **kwargs)
            created.append(value)
            return value

        def fail(command, **kwargs):
            created[-1].temporary_source = Path(kwargs['cwd'])
            raise subprocess.TimeoutExpired(command, 300)

        with patch('theme_preview.shutil.which', return_value='/fake/jekyll'), \
                patch('theme_preview.ThreadingHTTPServer', side_effect=server), \
                patch('theme_preview.run_external', side_effect=fail):
            with self.assertRaisesRegex(theme_preview.FeatureError, '시간이 초과'):
                self.preview.build(draft['id'])
        self.assertEqual(created[0].socket.fileno(), -1)
        self.assertFalse(created[0].temporary_source.exists())
        self.assertIs(self.preview.http, previous_http)
        self.assertTrue(Path(previous_folder).exists())
        self.assertEqual(self.request(previous['url'])[0], 200)

    def test_prepare_never_changes_original_lockfile(self):
        self.write('Gemfile', 'source "https://rubygems.org"\ngem "jekyll"\n')
        self.write('Gemfile.lock', 'original lockfile\n')
        sources = []

        def install(command, **kwargs):
            self.assertEqual(command[-1], 'install')
            source = Path(kwargs['cwd'])
            sources.append(source)
            self.assertNotEqual(source, self.repo)
            self.assertEqual((source / 'Gemfile.lock').read_text('utf-8'), 'original lockfile\n')
            (source / 'Gemfile.lock').write_text('changed lockfile\n', 'utf-8')
            self.assertEqual(Path(kwargs['env']['BUNDLE_PATH']), self.repo / '.blog-studio/preview-gems')
            self.assertEqual(Path(kwargs['env']['BUNDLE_GEMFILE']), source / 'Gemfile')
            return SimpleNamespace(returncode=0, stdout='', stderr='')

        with patch.dict(os.environ, {'BUNDLE_GEMFILE': str(self.repo / 'Gemfile')}), \
                patch('theme_preview.shutil.which', return_value='/fake/bundle'), \
                patch('theme_preview.run_external', side_effect=install):
            self.preview.prepare()
        self.assertEqual((self.repo / 'Gemfile.lock').read_text('utf-8'), 'original lockfile\n')
        self.assertFalse(sources[0].exists())

    def test_copy_prunes_ignored_directories_and_symlinks(self):
        self.write('node_modules/a/b/c/ignored', 'ignore')
        self.write('.blog-studio/preview-gems/a/b/c/ignored', 'ignore')
        external = self.repo.parent / 'outside'
        external.mkdir()
        (external / 'secret').write_text('do not copy', 'utf-8')
        try:
            (self.repo / 'symlink-dir').symlink_to(external, target_is_directory=True)
            (self.repo / 'symlink-file').symlink_to(external / 'secret')
        except OSError:
            pass  # Creating symlinks may require extra privileges on Windows.
        visited = []
        original_walk = os.walk

        def walk(*args, **kwargs):
            for directory, dirs, files in original_walk(*args, **kwargs):
                visited.append(Path(directory).relative_to(self.repo))
                yield directory, dirs, files

        destination = self.repo.parent / 'copy'
        with patch('theme_preview.os.walk', side_effect=walk):
            self.preview._copy_repository(destination)
        self.assertFalse(any('node_modules' in path.parts or '.blog-studio' in path.parts for path in visited))
        self.assertFalse((destination / 'symlink-dir').exists())
        self.assertFalse((destination / 'symlink-file').exists())

    def test_rebuild_and_concurrent_close_release_every_preview(self):
        draft, _ = self.draft()
        self.build(draft)
        first_http, first_folder = self.preview.http, Path(self.preview.folder.name)
        self.build(draft)
        self.assertEqual(first_http.socket.fileno(), -1)
        self.assertFalse(first_folder.exists())
        second_http, second_folder = self.preview.http, Path(self.preview.folder.name)
        closers = [threading.Thread(target=self.preview.close) for _ in range(2)]
        for closer in closers:
            closer.start()
        for closer in closers:
            closer.join(4)
            self.assertFalse(closer.is_alive())
        self.assertIsNone(self.preview.http)
        self.assertEqual(second_http.socket.fileno(), -1)
        self.assertFalse(second_folder.exists())

    @unittest.skipUnless(os.environ.get('STUDIO_JEKYLL_INTEGRATION') == '1', 'Optional real Jekyll integration')
    def test_real_jekyll_renders_future_private_draft_and_image(self):
        self.write('_layouts/post.html', '<!doctype html><title>{{ page.title }}</title><main class="real-theme">{{ content }}</main>')
        self.write('_includes/notice_box.html', '<p>fixture include</p>')
        self.studio.config['defaults'] = [{'scope': {'path': ''}, 'values': {'layout': 'post'}}]
        draft, image = self.draft()
        before = self.studio.get_draft(draft['id'])
        result = self.preview.build(draft['id'])
        status, _, raw = self.request(result['url'])
        self.assertEqual(status, 200)
        html = raw.decode('utf-8')
        self.assertIn('class="real-theme"', html)
        self.assertIn('실제 테마 본문', html)
        self.assertIn('미래의 비공개 초안', html)
        origin = result['url'].split('/__studio_preview__/')[0]
        self.assertIn(origin.split(urlsplit(origin).netloc)[1] + image['url'], html)
        self.assertEqual(self.request(origin + image['url'])[2], fixtures.PNG)
        self.assertEqual(self.studio.get_draft(draft['id']), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
