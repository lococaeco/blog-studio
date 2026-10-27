"""Build the selected draft with the blog's real Jekyll theme in a temporary copy."""
from __future__ import annotations
import copy
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import tempfile
import threading
from urllib.parse import urlsplit
import yaml
from runtime import run_external, external_environment, _link_or_reparse
from studio_features import FeatureError


class ThemePreview:
    def __init__(self, studio):
        self.studio = studio
        self.folder = None
        self.http = None
        self.lock = threading.RLock()

    def _environment(self, source):
        env = external_environment()
        env['BUNDLE_PATH'] = str(self.studio.safe_path('.blog-studio/preview-gems'))
        env['BUNDLE_APP_CONFIG'] = str(self.studio.safe_path('.blog-studio/preview-bundle'))
        # A launcher started from `bundle exec` can inherit another project's
        # Gemfile. Pin Bundler to this disposable source, never the original.
        if (source / 'Gemfile').is_file():
            env['BUNDLE_GEMFILE'] = str(source / 'Gemfile')
        else:
            env.pop('BUNDLE_GEMFILE', None)
        return env

    def _copy_repository(self, source):
        source.mkdir()
        skip = {'.git', '.blog-studio', '_site', 'node_modules', '.venv', 'venv', 'vendor', '.bundle', '.build', 'dist', 'tools', '__pycache__'}
        count = total = 0
        for directory, directories, filenames in os.walk(self.studio.repo, followlinks=False):
            current = Path(directory)
            # Prune before descent: ignored caches can contain hundreds of
            # thousands of files, and symlinks must never widen the copy.
            directories[:] = [name for name in directories if name not in skip and not _link_or_reparse(current / name)]
            for name in filenames:
                path = current / name
                if name in skip or _link_or_reparse(path) or not path.is_file() or path.suffix.lower() in ('.exe', '.appimage'):
                    continue
                count += 1
                total += path.stat().st_size
                if count > 30000 or total > 1024 * 1024 * 1024:
                    raise FeatureError('미리보기 복사 범위가 너무 큽니다. 블로그 외의 큰 파일을 별도 폴더에 보관해 주세요.')
                target = source / path.relative_to(self.studio.repo)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)

    def status(self):
        return {'ruby': bool(shutil.which('ruby')), 'bundle': bool(shutil.which('bundle') or shutil.which('bundler')),
                'jekyll': bool(shutil.which('jekyll')), 'gemfile': (self.studio.repo / 'Gemfile').is_file(),
                'installation_url': 'https://jekyllrb.com/docs/installation/'}

    def prepare(self):
        with self.lock:
            bundle = shutil.which('bundle') or shutil.which('bundler')
            if not bundle:
                raise FeatureError('Ruby와 Bundler를 먼저 설치해 주세요. 설치 안내 버튼에서 운영체제별 방법을 볼 수 있습니다.')
            if not (self.studio.repo / 'Gemfile').is_file():
                raise FeatureError('블로그에 Gemfile이 없습니다. Jekyll을 설치하거나 테마의 Gemfile을 준비해 주세요.')
            # Bundler may create/update Gemfile.lock. Run against a copy while
            # sharing only downloaded gems/config in ignored app storage.
            with tempfile.TemporaryDirectory(prefix='blog-studio-theme-setup-') as temporary:
                source = Path(temporary) / 'source'
                self._copy_repository(source)
                try:
                    result = run_external([bundle, 'install'], cwd=source, env=self._environment(source),
                                          capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=600)
                except subprocess.TimeoutExpired as exc:
                    raise FeatureError('미리보기 환경 준비 시간이 초과됐어요. 네트워크 연결을 확인한 뒤 다시 시도해 주세요.') from exc
                if result.returncode:
                    raise FeatureError('미리보기 환경 준비에 실패했습니다.\n' + (result.stderr or result.stdout)[-2000:])
            return {'message': '실제 테마 미리보기 환경을 준비했습니다.'}

    def build(self, draft_id):
        with self.lock:
            draft = copy.deepcopy(self.studio.get_draft(draft_id))
            bundle = shutil.which('bundle') or shutil.which('bundler')
            jekyll = shutil.which('jekyll')
            use_bundle = (self.studio.repo / 'Gemfile').is_file()
            if (use_bundle and not bundle) or (not use_bundle and not jekyll):
                raise FeatureError('실제 테마 미리보기에는 Ruby/Jekyll이 필요합니다. 미리보기 환경 준비 또는 설치 안내를 이용해 주세요.')
            folder = tempfile.TemporaryDirectory(prefix='blog-studio-theme-')
            source, output = Path(folder.name) / 'source', Path(folder.name) / 'output'
            http = None
            try:
                self._copy_repository(source)
                prefix = '/' + secrets.token_hex(24)
                preview_root = output

                class PreviewHandler(SimpleHTTPRequestHandler):
                    def log_message(self, *args):
                        pass

                    def _authorize(self):
                        parsed = urlsplit(self.path)
                        if self.headers.get('Host') != f'127.0.0.1:{self.server.server_port}' or not parsed.path.startswith(prefix + '/'):
                            self.send_error(404)
                            return False
                        # The base handler performs URL decoding once and
                        # confines normalized file paths to preview_root.
                        self.path = parsed.path[len(prefix):]
                        return True

                    def do_GET(self):
                        if self._authorize():
                            return super().do_GET()

                    def do_HEAD(self):
                        if self._authorize():
                            return super().do_HEAD()

                    def end_headers(self):
                        self.send_header('Cache-Control', 'no-store')
                        self.send_header('Referrer-Policy', 'no-referrer')
                        self.send_header('X-Content-Type-Options', 'nosniff')
                        super().end_headers()

                    def list_directory(self, path):
                        self.send_error(404)

                handler = functools.partial(PreviewHandler, directory=str(preview_root))
                http = ThreadingHTTPServer(('127.0.0.1', 0), handler)
                config = copy.deepcopy(self.studio.config)
                config.update(url=f'http://127.0.0.1:{http.server_port}', baseurl=prefix, destination=str(output), future=True,
                              google_analytics=None, analytics={'provider': False})
                excluded = config.get('exclude') or []
                if not isinstance(excluded, list):
                    excluded = [str(excluded)]
                config['exclude'] = list(set(excluded + ['.blog-studio', 'Gemfile', 'Gemfile.lock', '.bundle']))
                (source / '_config.yml').write_text(yaml.safe_dump(config, allow_unicode=True), 'utf-8')
                # Only the copy receives an alternate permalink and draft content.
                draft['_meta'] = dict(draft.get('_meta', {}), permalink='/__studio_preview__/', published=True)
                draft['published'] = True
                draft['_header'] = ''
                draft['body'] = draft['body'].replace('](/assets/', '](' + prefix + '/assets/').replace('src="/assets/', 'src="' + prefix + '/assets/').replace("src='/assets/", "src='" + prefix + '/assets/')
                relative = self.studio._target(draft)
                target = source / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(self.studio._render(draft), 'utf-8')
                for original, relative in self.studio._images(draft):
                    target = source / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(original, target)
                env = self._environment(source)
                env['JEKYLL_ENV'] = 'development'
                command = [bundle, 'exec', 'jekyll'] if use_bundle else [jekyll]
                result = run_external(command + ['build', '--source', str(source), '--destination', str(output), '--future'],
                                      cwd=source, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=300)
                if result.returncode or not (output / '__studio_preview__/index.html').is_file():
                    raise FeatureError('테마 빌드에 실패했습니다. 환경 준비 후 다시 시도해 주세요.\n' + (result.stderr or result.stdout)[-2000:])
                self.close()
                threading.Thread(target=http.serve_forever, daemon=True).start()
                self.folder, self.http = folder, http
                return {'url': f'http://127.0.0.1:{http.server_port}{prefix}/__studio_preview__/',
                        'message': '실제 Jekyll 테마로 미리보기를 만들었습니다.'}
            except BaseException as exc:
                if http is not None and http is not self.http:
                    http.server_close()
                folder.cleanup()
                if isinstance(exc, subprocess.TimeoutExpired):
                    raise FeatureError('테마 빌드 시간이 초과됐어요. 테마 설정과 플러그인을 확인한 뒤 다시 시도해 주세요.') from exc
                raise

    def close(self):
        # Builds, environment setup and duplicate app shutdowns share a lock.
        # RLock permits a successful build to replace its previous preview.
        with self.lock:
            if self.http:
                self.http.shutdown()
                self.http.server_close()
                self.http = None
            if self.folder:
                self.folder.cleanup()
                self.folder = None
