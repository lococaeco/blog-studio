#!/usr/bin/env python3
"""Local, private writing studio for a Jekyll repository.

Only this process's loopback UI can mutate drafts. Posts and Git are changed only
by an explicit save/commit/push action. No shell commands or cloud credentials.
"""
from __future__ import annotations

import argparse
import base64
import copy
import datetime as dt
import hashlib
import json
import logging
import mimetypes
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, quote, unquote, urlparse
import uuid
import webbrowser

import yaml

HERE = Path(__file__).resolve().parent
# Also support importlib loading from test runners outside this directory.
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
from runtime import RepositoryLock, RepositoryInUseError, run_external, external_program_environment
from studio_features import Features, FeatureError
from theme_preview import ThemePreview
from cloud_services import CloudService, CloudError
from updater import Updater, UpdateError

logger = logging.getLogger('blog_studio.server')
KST = dt.timezone(dt.timedelta(hours=9))
MAX_BODY = 4 * 1024 * 1024
MAX_IMAGE = 12 * 1024 * 1024
MAX_REQUEST = 18 * 1024 * 1024
EDITABLE = ('title', 'excerpt', 'body', 'date', 'slug', 'category', 'tags', 'toc',
            'mathjax', 'comments', 'feature', 'published', 'teaser')
BOOLS = ('toc', 'mathjax', 'comments', 'feature', 'published')
FRONT_FIELDS = ('title', 'excerpt', 'tags', 'toc', 'mathjax', 'comments', 'feature',
                'published', 'teaser')
TEMPLATES = [
    {'id': 'blank', 'label': '빈 글', 'description': '생각을 자유롭게 기록하세요.', 'body': ''},
    {'id': 'research', 'label': '논문 리뷰', 'description': '문제부터 실험, 나의 해석까지', 'body': '## 한눈에 보기\n\n> 이 논문의 핵심을 한 문장으로 정리합니다.\n\n## 풀고자 하는 문제\n\n\n## 핵심 아이디어\n\n\n## 방법과 수식\n\n$$\ny = Wx + b\n$$\n\n## 실험 결과\n\n| 실험 | 결과 | 해석 |\n| --- | --- | --- |\n| 기준 모델 | | |\n\n## 나의 생각\n\n\n## 참고 자료\n\n- [논문 제목](https://arxiv.org/)\n'},
    {'id': 'dev', 'label': '개발 노트', 'description': '문제와 해결 과정을 차근차근', 'body': '## 오늘의 문제\n\n\n## 환경\n\n- 언어 / 버전:\n- 실행 환경:\n\n## 해결 과정\n\n```python\n# 핵심 코드를 남겨 보세요.\n```\n\n## 배운 점\n\n\n## 참고 자료\n\n'},
    {'id': 'til', 'label': '오늘 배운 것', 'description': '작은 배움을 오래 남기는 기록', 'body': '## 오늘 배운 것\n\n\n## 직접 해보기\n\n\n## 다음에 알아볼 것\n\n- [ ] \n'},
]


class StudioError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def now():
    return dt.datetime.now(KST).isoformat(timespec='seconds')


def digest(data: bytes):
    return hashlib.sha256(data).hexdigest()


def jsonable(value):
    if isinstance(value, (dt.datetime, dt.date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [jsonable(x) for x in value]
    return value


def atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.studio-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def split_post(text: str):
    match = re.match(r'\A---\r?\n(.*?)\r?\n---(?:\r?\n|$)', text, re.S)
    if not match:
        return {}, '', text
    try:
        meta = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as e:
        raise StudioError('글의 YAML 설정을 읽을 수 없습니다. 원본 파일을 확인해 주세요.') from e
    if not isinstance(meta, dict) or any(not isinstance(k, str) for k in meta):
        raise StudioError('글의 YAML 설정은 문자열 키를 사용하는 객체여야 합니다.')
    return meta, match.group(0), text[match.end():]


def slugify(text: str):
    slug = re.sub(r'[^\w\-\s]', '', text.lower(), flags=re.UNICODE)
    slug = re.sub(r'[\s_]+', '-', slug).strip('-')
    return slug[:90].strip('-') or 'untitled'


class Studio:
    def __init__(self, repo: Path):
        self.repo = Path(repo).resolve()
        if not (self.repo / '_config.yml').is_file() or not (self.repo / '_posts').is_dir():
            raise StudioError('Jekyll 블로그 폴더를 선택해 주세요. _config.yml과 _posts가 필요합니다.')
        self.lock = threading.RLock()
        self.storage = self.safe_path('.blog-studio')
        for folder in ('drafts', 'history', 'uploads', 'backups'):
            self.safe_path(f'.blog-studio/{folder}').mkdir(parents=True, exist_ok=True)
        self.config = yaml.safe_load(self.safe_path('_config.yml').read_text('utf-8')) or {}
        self.categories = self._categories()
        self.cloud = CloudService(self)
        self.features = Features(self, self.cloud)
        self.preview = ThemePreview(self)
        self.updater = Updater(self)

    def safe_path(self, value: str):
        if not isinstance(value, str) or not value or '\\' in value or '\x00' in value:
            raise StudioError('올바르지 않은 파일 경로입니다.')
        parts = PurePosixPath(value)
        if parts.is_absolute() or any(p in ('.', '..') for p in value.split('/')):
            raise StudioError('블로그 폴더 밖의 파일에는 접근할 수 없습니다.', 403)
        path = self.repo.joinpath(*parts.parts)
        current = self.repo
        for part in parts.parts:
            current = current / part
            if current.is_symlink():
                raise StudioError('심볼릭 링크 파일에는 접근할 수 없습니다.', 403)
        if not path.resolve().is_relative_to(self.repo):
            raise StudioError('블로그 폴더 밖의 파일에는 접근할 수 없습니다.', 403)
        return path

    def _categories(self):
        path = self.safe_path('_data/categories.yml')
        source = yaml.safe_load(path.read_text('utf-8')) if path.exists() else {}
        # Menu display labels and Jekyll's actual category keys differ in this blog.
        # The archive page is authoritative (e.g. “Deep Learning” → “Deep-Learning”).
        archive_keys = {}
        for page in self.repo.joinpath('_pages/categories').glob('*.html'):
            text = self.safe_path(page.relative_to(self.repo).as_posix()).read_text('utf-8')
            metadata, _, content = split_post(text)
            match = re.search(r"site\.categories\[['\"]([^'\"]+)['\"]\]", content)
            if match and isinstance(metadata.get('permalink'), str):
                archive_keys[metadata['permalink'].strip('/')] = match[1]
        result = []
        for group in (source or {}).get('categories', []):
            for item in group.get('lv2', []):
                key = item.get('url', '').removeprefix('/category/').strip('/')
                if re.fullmatch(r'[a-zA-Z0-9_/-]+', key) and '..' not in key:
                    leaf = archive_keys.get('category/' + key, item['lv2_category'])
                    result.append({'id': key, 'label': group['lv1_category'] + ' / ' + item['lv2_category'],
                                   'categories': [group['lv1_category'], leaf]})
        return result

    def git(self, *args, env=None, timeout=25, check=True):
        settings = os.environ.copy()
        settings['GIT_TERMINAL_PROMPT'] = '0'
        settings['GCM_INTERACTIVE'] = 'never'
        if args and args[0] in ('push', 'fetch', 'ls-remote') and getattr(self, 'cloud', None):
            settings.update(self.cloud.git_environment())
        if env:
            settings.update(env)
        try:
            p = run_external(['git', '-C', str(self.repo), *args], capture_output=True,
                             encoding='utf-8', errors='replace', timeout=timeout, env=settings)
        except FileNotFoundError as e:
            raise StudioError('Git이 설치되어 있지 않습니다. 파일 저장은 계속 사용할 수 있습니다.') from e
        except subprocess.TimeoutExpired as e:
            raise StudioError('Git 작업 시간이 초과되었습니다. 네트워크와 Git 인증을 확인해 주세요.', 504) from e
        if check and p.returncode:
            # Never include remote URLs, which can contain an embedded credential.
            detail = (p.stderr.strip() or p.stdout.strip())[:1200]
            detail = re.sub(r'https?://[^\s]+', '[remote]', detail)
            raise StudioError('Git 작업을 완료하지 못했습니다. ' + detail, 409)
        return p

    def git_status(self):
        try:
            branch = self.git('symbolic-ref', '--short', 'HEAD', check=False).stdout.strip()
            remote = self.git('remote', 'get-url', 'origin', check=False).stdout.strip()
            # Credentials must never be shown or persisted by this application.
            remote = re.sub(r'(https?://)[^/@]+@', r'\1', remote)
            staged = self.git('diff', '--cached', '--name-only', '-z').stdout.split('\0')
            upstream = self.git('rev-parse', '--abbrev-ref', '@{upstream}', check=False).stdout.strip()
            compare = upstream or (f'origin/{branch}' if branch else '')
            counts = self.git('rev-list', '--left-right', '--count', f'HEAD...{compare}', check=False)
            ahead, behind = (map(int, counts.stdout.split()) if counts.returncode == 0 else (0, 0))
            return {'available': True, 'branch': branch or '(detached HEAD)', 'remote': remote,
                    'staged': [x for x in staged if x], 'ahead': ahead, 'behind': behind, 'upstream': upstream}
        except (StudioError, ValueError):
            return {'available': False, 'branch': '', 'remote': '', 'staged': [], 'ahead': 0, 'behind': 0}

    def bootstrap(self):
        posts, unreadable = [], []
        for path in sorted((self.repo / '_posts').rglob('*')):
            if path.suffix.lower() not in ('.md', '.markdown', '.mkd', '.mkdown', '.mkdn'):
                continue
            rel = path.relative_to(self.repo).as_posix()
            try:
                data = self._read_post(rel)
                posts.append({k: data[k] for k in ('title', 'excerpt', 'category', 'date', 'published', 'tags')} | {'path': rel, 'url': self.post_url(data)})
            except (StudioError, OSError, UnicodeError) as e:
                unreadable.append({'path': rel, 'error': str(e)})
        drafts = []
        for path in self.storage.joinpath('drafts').glob('*.json'):
            try:
                d = self.get_draft(path.stem)
                drafts.append({k: d.get(k) for k in ('id', 'title', 'updated_at', 'source_path', 'version')})
            except (StudioError, OSError, ValueError):
                pass
        return {'site': {'title': str(self.config.get('title', '블로그')),
                         'url': str(self.config.get('url', '')).rstrip('/') + str(self.config.get('baseurl', '')).rstrip('/'),
                         'timezone': str(self.config.get('timezone', 'Asia/Seoul')),
                         'repository': str(self.config.get('repository', '')),
                         'id': digest(str(self.repo).encode())[:16]},
                'categories': self.categories, 'posts': sorted(posts, key=lambda p: p['date'], reverse=True),
                'drafts': sorted(drafts, key=lambda d: d['updated_at'], reverse=True),
                'git': self.git_status(), 'templates': TEMPLATES + [dict(t, description='내 템플릿') for t in self.features.templates()['items'] if t['kind'] == 'template'], 'unreadable': unreadable}

    def post_url(self, draft):
        custom = draft.get('_meta', {}).get('permalink')
        if custom:
            return self.bootstrap_site_url() + '/' + str(custom).lstrip('/')
        categories = draft.get('_meta', {}).get('categories') or []
        if isinstance(categories, str):
            categories = [categories]
        path = '/'.join(slugify(str(c)) for c in categories)
        return self.bootstrap_site_url() + '/' + (path + '/' if path else '') + quote(draft['slug']) + '/'

    def _defaults(self):
        return {'title': '', 'excerpt': '', 'body': '', 'date': now()[:10], 'slug': '',
                'category': self.categories[0]['id'] if self.categories else '', 'tags': [],
                'toc': True, 'mathjax': True, 'comments': True, 'feature': False,
                'published': True, 'teaser': '', 'source_path': None, 'source_revision': None}

    def _read_post(self, rel):
        if not isinstance(rel, str) or not rel.startswith('_posts/'):
            raise StudioError('글 경로는 _posts 안에 있어야 합니다.', 403)
        path = self.safe_path(rel)
        if not path.is_file() or path.suffix.lower() not in ('.md', '.markdown', '.mkd', '.mkdown', '.mkdn'):
            raise StudioError('글을 찾을 수 없습니다.', 404)
        raw = path.read_bytes()
        meta, header, body = split_post(raw.decode('utf-8'))
        file_match = re.match(r'(\d{4}-\d{2}-\d{2})-(.*)', path.stem)
        d = self._defaults()
        d.update({k: jsonable(meta[k]) for k in FRONT_FIELDS if k in meta})
        d.update(body=body, source_path=rel, source_revision=digest(raw), _header=header,
                 _meta=jsonable(meta), date=file_match[1] if file_match else now()[:10],
                 slug=file_match[2] if file_match else path.stem,
                 category=path.parent.relative_to(self.repo / '_posts').as_posix())
        if d['category'] == '.':
            d['category'] = ''
        if isinstance(d['tags'], str):
            d['tags'] = [d['tags']]
        if not isinstance(d['tags'], list):
            d['tags'] = []
        for key in ('title', 'excerpt', 'teaser'):
            d[key] = str(d[key] or '')
        # Apply actual theme defaults for absent fields; retain absent YAML keys on write.
        for default in self.config.get('defaults', []):
            if default.get('scope', {}).get('path') == '':
                for key in BOOLS:
                    if key not in meta and key in default.get('values', {}):
                        d[key] = bool(default['values'][key])
        d['_baseline'] = {k: copy.deepcopy(d[k]) for k in EDITABLE}
        return d

    def _draft_path(self, draft_id):
        if not isinstance(draft_id, str) or not re.fullmatch(r'[0-9a-f]{32}', draft_id):
            raise StudioError('올바르지 않은 초안 ID입니다.', 400)
        return self.safe_path(f'.blog-studio/drafts/{draft_id}.json')

    def get_draft(self, draft_id):
        path = self._draft_path(draft_id)
        if not path.is_file():
            raise StudioError('초안을 찾을 수 없습니다.', 404)
        return json.loads(path.read_text('utf-8'))

    def _persist(self, draft, snapshot=True):
        draft['version'] = draft.get('version', 0) + 1
        draft['updated_at'] = now()
        data = json.dumps(draft, ensure_ascii=False, indent=2).encode('utf-8')
        atomic_write(self._draft_path(draft['id']), data)
        if snapshot:
            folder = self.safe_path(f'.blog-studio/history/{draft["id"]}')
            atomic_write(folder / f'{draft["version"]:010}.json', data)
            snapshots = sorted(folder.glob('*.json'))
            for old in snapshots[:-50]:
                old.unlink()
        return draft

    def open_post(self, path):
        with self.lock:
            self._read_post(path)  # Validate requested path before matching private drafts.
            for item in self.storage.joinpath('drafts').glob('*.json'):
                d = self.get_draft(item.stem)
                if d.get('source_path') == path:
                    return d
            d = self._read_post(path)
            d['id'] = uuid.uuid4().hex
            return self._persist(d)

    def _validate_fields(self, data):
        for key in EDITABLE:
            if key not in data:
                continue
            value = data[key]
            if key in BOOLS:
                if not isinstance(value, bool):
                    raise StudioError(f'{key} 값은 true 또는 false여야 합니다.')
            elif key == 'tags':
                if not isinstance(value, list) or len(value) > 50 or any(not isinstance(t, str) or len(t) > 100 for t in value):
                    raise StudioError('태그는 100자 이내의 문자열을 최대 50개 입력할 수 있습니다.')
            else:
                if not isinstance(value, str):
                    raise StudioError(f'{key} 값은 문자열이어야 합니다.')
                limit = MAX_BODY if key == 'body' else (4000 if key == 'excerpt' else 500)
                if len(value.encode('utf-8')) > limit:
                    raise StudioError(f'{key} 입력이 너무 큽니다.')
        if 'date' in data:
            try:
                dt.date.fromisoformat(data['date'])
            except ValueError as e:
                raise StudioError('날짜 형식은 YYYY-MM-DD여야 합니다.') from e
        if data.get('slug') and (not re.fullmatch(r'[\w-]+', data['slug']) or len(data['slug']) > 100):
            raise StudioError('글 주소에는 글자, 숫자, 밑줄, 하이픈만 사용할 수 있습니다.')

    def save_draft(self, payload):
        with self.lock:
            if not isinstance(payload, dict):
                raise StudioError('JSON 객체가 필요합니다.')
            self._validate_fields(payload)
            if payload.get('id'):
                d = self.get_draft(payload['id'])
                if payload.get('version') != d['version']:
                    raise StudioError('다른 탭에서 이 초안이 변경되었습니다. 현재 내용을 내보낸 뒤 최신 초안을 열어 주세요.', 409)
            else:
                d = self._defaults() | {'id': uuid.uuid4().hex, 'version': 0, '_meta': {}, '_header': '', '_baseline': {}}
            for key in EDITABLE:
                if key in payload:
                    d[key] = payload[key]
            # The original path and URL do not silently change on title/category/date edits.
            if d.get('source_path'):
                base = d['_baseline']
                d['date'], d['slug'], d['category'] = base['date'], base['slug'], base['category']
            return self._persist(d)

    def history(self, draft_id):
        self.get_draft(draft_id)
        folder = self.safe_path(f'.blog-studio/history/{draft_id}')
        result = []
        for f in sorted(folder.glob('*.json'), reverse=True):
            item = json.loads(self.safe_path(f.relative_to(self.repo).as_posix()).read_text('utf-8'))
            result.append({'id': f.stem, 'at': item['updated_at'], 'title': item['title'], 'version': item['version']})
        return {'items': result}

    def restore(self, payload):
        with self.lock:
            d = self.get_draft(payload.get('id'))
            if payload.get('version') != d['version']:
                raise StudioError('초안이 변경되었습니다. 최신 버전에서 복원해 주세요.', 409)
            history_id = payload.get('history_id', '')
            if not re.fullmatch(r'\d{10}', history_id):
                raise StudioError('올바르지 않은 이력 ID입니다.')
            path = self.safe_path(f'.blog-studio/history/{d["id"]}/{history_id}.json')
            if not path.exists():
                raise StudioError('저장 이력을 찾을 수 없습니다.', 404)
            old = json.loads(path.read_text('utf-8'))
            # Never restore stale Git/source revisions from an old history entry.
            for key in EDITABLE:
                if key not in ('date', 'slug', 'category') or not d.get('source_path'):
                    d[key] = old[key]
            return self._persist(d)

    def delete_draft(self, draft_id):
        with self.lock:
            self.get_draft(draft_id)
            self._draft_path(draft_id).unlink()
            # Preserve history/uploads as recovery material; published files are never deleted.
            return {'ok': True}

    def import_markdown(self, payload):
        text = payload.get('text')
        if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_BODY:
            raise StudioError('4MB 이하의 UTF-8 Markdown 파일을 선택해 주세요.')
        meta, header, body = split_post(text)
        d = self._defaults()
        d.update({k: jsonable(meta[k]) for k in FRONT_FIELDS if k in meta})
        if isinstance(d['tags'], str):
            d['tags'] = [d['tags']]
        d['body'] = body
        name = str(payload.get('name', '가져온 글.md')).replace('\\', '/').split('/')[-1]
        stem = Path(name).stem
        match = re.match(r'(\d{4}-\d{2}-\d{2})-(.*)', stem)
        if match:
            d['date'], stem = match.groups()
        d['title'] = str(d['title'] or stem)
        d['slug'] = slugify(stem)
        for c in self.categories:
            if c['categories'] == meta.get('categories'):
                d['category'] = c['id']
        self._validate_fields(d)
        with self.lock:
            d.update(id=uuid.uuid4().hex, version=0, _imported=True, _meta=jsonable(meta), _header=header,
                     _baseline={k: copy.deepcopy(d[k]) for k in EDITABLE})
            return self._persist(d)

    def upload(self, payload):
        with self.lock:
            d = self.get_draft(payload.get('draft_id'))
            name = payload.get('name', '')
            if not isinstance(name, str) or not isinstance(payload.get('data'), str):
                raise StudioError('이미지 이름과 base64 데이터가 필요합니다.')
            try:
                raw = base64.b64decode(payload['data'], validate=True)
            except (ValueError, base64.binascii.Error) as e:
                raise StudioError('이미지 데이터를 읽을 수 없습니다.') from e
            if not raw or len(raw) > MAX_IMAGE:
                raise StudioError('이미지는 한 장당 12MB 이하로 첨부해 주세요.')
            ext = Path(name).suffix.lower()
            valid = ((ext == '.png' and raw.startswith(b'\x89PNG\r\n\x1a\n')) or
                     (ext in ('.jpg', '.jpeg') and raw.startswith(b'\xff\xd8\xff')) or
                     (ext == '.gif' and raw[:6] in (b'GIF87a', b'GIF89a')) or
                     (ext == '.webp' and raw[:4] == b'RIFF' and raw[8:12] == b'WEBP') or
                     (ext == '.avif' and raw[4:8] == b'ftyp' and raw[8:12] in (b'avif', b'avis')))
            if not valid:
                raise StudioError('PNG, JPEG, GIF, WebP, AVIF 이미지 파일만 첨부할 수 있습니다.')
            filename = slugify(Path(name.replace('\\', '/').split('/')[-1]).stem)[:55] + '-' + digest(raw)[:16] + ext
            path = self.safe_path(f'.blog-studio/uploads/{d["id"]}/{filename}')
            atomic_write(path, raw)
            rel = f'assets/images/posts/studio-{d["id"]}/{filename}'
            return {'url': '/' + quote(rel), 'name': filename, 'size': len(raw)}

    def _target(self, d):
        if d.get('source_path'):
            return d['source_path']
        if not any(c['id'] == d['category'] for c in self.categories):
            raise StudioError('카테고리를 선택해 주세요.')
        slug = d['slug'] or slugify(d['title'])
        return f'_posts/{d["category"]}/{d["date"]}-{slug}.md'

    def _render(self, d):
        # Parse raw YAML again to retain YAML types (dates/nested settings) during a metadata edit.
        original, _, _ = split_post(d.get('_header', ''))
        meta = copy.deepcopy(original or d.get('_meta', {}))
        baseline = d.get('_baseline', {})
        changed = False
        for key in FRONT_FIELDS:
            if key not in baseline or d[key] != baseline.get(key):
                if key == 'teaser' and not d[key]:
                    meta.pop('teaser', None)
                elif key == 'feature' and not d[key] and key not in meta:
                    continue
                else:
                    meta[key] = d[key]
                changed = True
        if not d.get('source_path'):
            category = next((c['categories'] for c in self.categories if c['id'] == d['category']), [])
            if meta.get('categories') != category:
                meta['categories'] = category
                changed = True
        if not meta.get('title'):
            meta['title'] = d['title']
            changed = True
        header = d.get('_header', '')
        if not header or changed:
            header = '---\n' + yaml.safe_dump(meta, allow_unicode=True, sort_keys=False, width=1000).rstrip() + '\n---\n'
        return header + d['body']

    def export(self, draft_id):
        return self._render(self.get_draft(draft_id))

    def _images(self, d):
        content = d['body'] + '\n' + d.get('teaser', '')
        prefix = f'assets/images/posts/studio-{d["id"]}/'
        folder = self.safe_path(f'.blog-studio/uploads/{d["id"]}')
        result = []
        decoded = unquote(content)
        if folder.exists():
            for path in folder.iterdir():
                if prefix + path.name in decoded:
                    result.append((self.safe_path(path.relative_to(self.repo).as_posix()), prefix + path.name))
        return result

    def check(self, draft_id):
        with self.lock:
            d = self.get_draft(draft_id)
            errors, warnings = [], []
            if not d['title'].strip():
                errors.append('제목을 입력해 주세요.')
            if not d['body'].strip():
                errors.append('본문을 입력해 주세요.')
            if d['date'] > now()[:10]:
                errors.append('미래 날짜의 글은 기본 Jekyll 설정에서 표시되지 않습니다. 오늘 또는 이전 날짜를 선택해 주세요.')
            try:
                rel = self._target(d)
                target = self.safe_path(rel)
                if d.get('source_path'):
                    if not target.exists() or digest(target.read_bytes()) != d.get('source_revision'):
                        errors.append('원본 글이 외부에서 변경되었습니다. 초안을 Markdown으로 내보내 보관하고 초안을 삭제한 뒤, 글 목록에서 원본을 다시 열어 내용을 병합해 주세요.')
                elif target.exists():
                    errors.append('같은 날짜와 주소의 글이 이미 있습니다. 글 주소를 변경해 주세요.')
            except StudioError as e:
                rel = ''
                errors.append(str(e))
            if not d['published']:
                warnings.append('비공개 글입니다. 파일을 GitHub로 보내도 블로그에는 표시되지 않습니다.')
            if not d['excerpt'].strip():
                warnings.append('요약을 입력하면 글 목록에서 내용을 쉽게 알아볼 수 있습니다.')
            if any(marker in d['body'] for marker in ('{% ', '{{ ', '{:', '$$')):
                warnings.append('수식·Liquid·Jekyll 확장 문법은 편집 미리보기와 실제 블로그에서 다르게 보일 수 있습니다.')
            if re.search(r'!\[[^\]]*\]\((?:\.\.?/)', d['body']):
                warnings.append('상대경로 이미지가 있습니다. 가져온 글이라면 이미지도 첨부하고 게시 후 경로를 확인해 주세요.')
            # Resolve local Markdown/HTML image paths without requesting external resources.
            sources = re.findall(r'!\[[^\]]*\]\(([^\s)]+)', d['body'])
            sources += re.findall(r'<img\b[^>]*\bsrc=[\"\']([^\"\']+)', d['body'], re.I)
            if d.get('teaser'):
                sources.append(d['teaser'])
            images = self._images(d)
            planned = {dest for _, dest in images}
            for src in sources:
                src = unquote(src.strip('<>'))
                if src.startswith(('http://', 'https://', 'data:')):
                    continue
                if src.startswith('//') or urlparse(src).scheme:
                    warnings.append('지원하지 않는 이미지 URL이 있습니다: ' + src[:100])
                    continue
                base = str(PurePosixPath(rel).parent) if rel else '_posts'
                normal = os.path.normpath(src.lstrip('/') if src.startswith('/') else base + '/' + src).replace('\\', '/')
                try:
                    if normal not in planned and not self.safe_path(normal).is_file():
                        warnings.append('찾을 수 없는 이미지: ' + src[:130])
                except StudioError:
                    warnings.append('블로그 밖을 가리키는 이미지: ' + src[:130])
            git = self.git_status()
            if git['staged']:
                warnings.append('Git에 이미 스테이징된 변경이 있어 커밋·전송을 잠시 막습니다. 먼저 기존 작업을 정리해 주세요.')
            if git['ahead']:
                warnings.append(f'아직 전송하지 않은 커밋 {git["ahead"]}개가 있습니다. 이 초안에서 만든 커밋만 함께 전송할 수 있습니다.')
            if git['behind']:
                warnings.append('원격 저장소에 새 커밋이 있습니다. 터미널에서 변경을 동기화해 주세요.')
            if git['branch'] not in ('main', 'master'):
                warnings.append('현재 브랜치가 main/master가 아닙니다. GitHub Pages 게시 브랜치인지 확인해 주세요.')
            filename = Path(rel).stem[11:] if rel else ''
            categories = (d.get('_meta', {}).get('categories') if d.get('source_path') else
                          next((c['categories'] for c in self.categories if c['id'] == d['category']), [])) or []
            if isinstance(categories, str):
                categories = [categories]
            cats = '/'.join(slugify(str(c)) for c in categories)
            url = self.bootstrap_site_url() + '/' + (cats + '/' if cats else '') + quote(filename) + '/'
            custom = d.get('_meta', {}).get('permalink')
            if custom:
                url = self.bootstrap_site_url() + '/' + str(custom).lstrip('/')
            return {'errors': errors, 'warnings': list(dict.fromkeys(warnings)), 'path': rel, 'url': url,
                    'files': [rel] + [dest for _, dest in images] if rel else [], 'git': git,
                    'word_count': len(d['body'].split()), 'version': d['version']}

    def bootstrap_site_url(self):
        return str(self.config.get('url', '')).rstrip('/') + str(self.config.get('baseurl', '')).rstrip('/')

    def _ensure_push_ready(self, d):
        state = self.git_status()
        branch = state['branch']
        if not state['remote'] or not branch or branch.startswith('('):
            raise StudioError('origin 원격 저장소와 현재 브랜치를 먼저 설정해 주세요.', 409)
        # Fetch only the branch being sent; never pull/rebase/force-push another person's work.
        self.git('fetch', 'origin', f'refs/heads/{branch}:refs/remotes/origin/{branch}', timeout=40)
        compare = self.git('rev-list', '--left-right', '--count', f'HEAD...refs/remotes/origin/{branch}').stdout.split()
        ahead, behind = map(int, compare)
        if behind:
            raise StudioError('원격에 새 변경이 있습니다. 먼저 Git에서 동기화한 뒤 다시 전송해 주세요.', 409)
        if ahead:
            commits = self.git('rev-list', f'refs/remotes/origin/{branch}..HEAD').stdout.split()
            owned = set(d.get('studio_commits', [])) | {d.get('last_commit')}
            if any(commit not in owned for commit in commits):
                raise StudioError('이 글과 무관한 미전송 커밋이 있습니다. 터미널에서 먼저 확인·전송해 주세요.', 409)
        return branch

    def _commit(self, files, title):
        head = self.git('rev-parse', 'HEAD').stdout.strip()
        index_path = Path(self.git('rev-parse', '--git-path', 'index').stdout.strip())
        if not index_path.is_absolute():
            index_path = self.repo / index_path
        index_lock = Path(str(index_path) + '.lock')
        try:
            lock_fd = os.open(index_lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError as e:
            raise StudioError('다른 Git 작업이 진행 중입니다. 완료된 뒤 다시 시도해 주세요.', 409) from e
        os.close(lock_fd)
        fd, index_file = tempfile.mkstemp(prefix='studio-index-', dir=self.storage)
        os.close(fd)
        os.unlink(index_file)
        env = {'GIT_INDEX_FILE': index_file}
        try:
            if self.git('diff', '--cached', '--name-only').stdout.strip():
                raise StudioError('다른 작업에서 파일을 스테이징했습니다. 기존 작업을 먼저 정리해 주세요.', 409)
            self.git('read-tree', head, env=env)
            self.git('add', '--', *files, env=env)
            tree = self.git('write-tree', env=env).stdout.strip()
            oldtree = self.git('rev-parse', f'{head}^{{tree}}').stdout.strip()
            if tree == oldtree:
                return None
            commit = self.git('commit-tree', tree, '-p', head, '-m', 'blog: ' + title, env=env).stdout.strip()
            # Prepare the real index before updating HEAD, holding Git's own index lock.
            shutil.copyfile(index_file, index_lock)
            # Compare-and-swap prevents overwriting a concurrent Git commit.
            self.git('update-ref', '-m', 'Blog Studio publish', 'HEAD', commit, head)
            try:
                os.replace(index_lock, index_path)
            except OSError as e:
                # HEAD is already committed: never roll back its corresponding files.
                error = StudioError('커밋은 완료됐지만 Git 인덱스 갱신에 실패했습니다. Git 상태를 확인해 주세요.', 500)
                error.installed_commit = commit
                raise error from e
            return commit
        finally:
            if os.path.exists(index_file):
                os.unlink(index_file)
            index_lock.unlink(missing_ok=True)

    def publish(self, payload):
        with self.lock:
            d = self.get_draft(payload.get('id'))
            if payload.get('version') != d['version']:
                raise StudioError('검토 후 초안이 변경되었습니다. 다시 확인해 주세요.', 409)
            mode = payload.get('mode')
            if mode not in ('save', 'commit', 'push'):
                raise StudioError('지원하지 않는 저장 방식입니다.')
            report = self.check(d['id'])
            if report['errors']:
                raise StudioError('\n'.join(report['errors']), 409)
            if mode != 'save':
                if not report['git']['available']:
                    raise StudioError('Git 저장소를 사용할 수 없습니다.', 409)
                if report['git']['staged']:
                    raise StudioError('기존 스테이징 변경을 먼저 정리해 주세요. 이 글과 함께 커밋하지 않습니다.', 409)
                for setting in ('user.name', 'user.email'):
                    if not self.git('config', setting, check=False).stdout.strip():
                        raise StudioError('Git 작성자 정보가 없습니다. git config user.name / user.email을 설정해 주세요.', 409)
            branch = self._ensure_push_ready(d) if mode == 'push' else None
            rel = report['path']
            post = self.safe_path(rel)
            previous = post.read_bytes() if post.exists() else None
            data = self._render(d).encode('utf-8')
            images = self._images(d)
            created_images = []
            if previous is not None and previous != data:
                backup = self.safe_path(f'.blog-studio/backups/{d["id"]}/{dt.datetime.now(KST).strftime("%Y%m%d-%H%M%S-%f")}.md')
                atomic_write(backup, previous)
            commit = None
            try:
                for source, dest in images:
                    target = self.safe_path(dest)
                    if target.exists():
                        if target.read_bytes() != source.read_bytes():
                            raise StudioError('같은 경로에 다른 이미지가 있습니다. 파일을 확인해 주세요.', 409)
                    else:
                        atomic_write(target, source.read_bytes())
                        created_images.append(target)
                atomic_write(post, data)
                if mode != 'save':
                    commit = self._commit(report['files'], d['title'])
            except Exception as error:
                # If commit was already installed, do not rewrite its working tree.
                if not commit and not getattr(error, 'installed_commit', None):
                    if previous is None:
                        post.unlink(missing_ok=True)
                    else:
                        atomic_write(post, previous)
                    for image in created_images:
                        image.unlink(missing_ok=True)
                raise
            refreshed = self._read_post(rel)
            d.update({k: refreshed[k] for k in ('source_path', 'source_revision', '_header', '_meta', '_baseline', 'date', 'slug', 'category')})
            if commit:
                d['last_commit'] = commit
                d['studio_commits'] = d.get('studio_commits', []) + [commit]
            self._persist(d)
            pushed, push_error = False, None
            if mode == 'push':
                try:
                    self.git('push', 'origin', f'HEAD:refs/heads/{branch}', timeout=50)
                    pushed = True
                    d['studio_commits'] = []
                    self._persist(d)
                except StudioError as e:
                    push_error = str(e)
            message = ('GitHub 전송 완료. 실제 웹 게시 상태는 GitHub Pages에서 확인해 주세요.' if pushed else
                       ('로컬 커밋 완료. GitHub 전송은 아직 완료되지 않았습니다.' if mode in ('commit', 'push') else
                        '블로그 파일에 저장했습니다. GitHub에는 아직 전송하지 않았습니다.'))
            if push_error:
                message += '\n' + push_error + '\n내용은 로컬에 안전하게 저장되어 있습니다. 같은 글에서 전송을 다시 시도할 수 있습니다.'
            return {'message': message, 'path': rel, 'url': report['url'], 'commit': commit or d.get('last_commit'),
                    'pushed': pushed, 'push_error': push_error, 'draft': d}


class StudioHTTPServer(ThreadingHTTPServer):
    # server_close waits for saved files and in-flight Git operations to finish.
    daemon_threads = False
    block_on_close = True
    allow_reuse_port = False
    def __init__(self, repo, port):
        repo = Path(repo).resolve()
        if not (repo / '_config.yml').is_file() or not (repo / '_posts').is_dir():
            raise StudioError('Jekyll 블로그 폴더를 선택해 주세요. _config.yml과 _posts가 필요합니다.')
        self._repo_lock = RepositoryLock(repo)
        self._close_lock = threading.Lock()
        try:
            self._repo_lock.acquire()
        except RuntimeError as exc:
            raise StudioError(str(exc), 409 if isinstance(exc, RepositoryInUseError) else 403) from exc
        try:
            self.studio = Studio(repo)
            self.token = secrets.token_urlsafe(32)
            super().__init__(('127.0.0.1', port), Handler)
        except BaseException:
            self._repo_lock.release()
            raise

    def server_bind(self):
        if os.name == 'nt':
            # Windows SO_REUSEADDR may attach a second app to an occupied port,
            # causing its browser to reach another repository's server.
            self.allow_reuse_address = False
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def get_request(self):
        connection, address = super().get_request()
        # Incomplete browser connections must not leave shutdown waiting forever.
        connection.settimeout(10)
        return connection, address

    def server_close(self):
        # Serialize duplicate stop requests: ThreadingMixIn removes its thread
        # list before joining, so a second close must not release the lock early.
        with self._close_lock:
            super().server_close()
            self.studio.features.close()
            self.studio.preview.close()
            self._repo_lock.release()


class Handler(BaseHTTPRequestHandler):
    server_version = 'BlogStudio/1.0'
    def log_message(self, fmt, *args):
        # No query strings, post contents, images or CSRF tokens in logs.
        if args and isinstance(args[0], str):
            logger.info('%s %s %s', self.command, urlparse(self.path).path, args[1] if len(args) > 1 else '')

    def _guard(self, mutate=False):
        port = self.server.server_port
        if self.headers.get('Host') not in (f'127.0.0.1:{port}', f'localhost:{port}'):
            raise StudioError('로컬 주소로만 접근할 수 있습니다.', 403)
        origin = self.headers.get('Origin')
        if origin and origin not in (f'http://127.0.0.1:{port}', f'http://localhost:{port}'):
            raise StudioError('다른 웹사이트에서는 접근할 수 없습니다.', 403)
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            raise StudioError('다른 웹사이트에서는 접근할 수 없습니다.', 403)
        if mutate and not secrets.compare_digest(self.headers.get('X-Studio-Token', ''), self.server.token):
            raise StudioError('편집 세션이 만료되었습니다. 페이지를 새로고침해 주세요.', 403)

    def _send(self, data, content_type='application/json; charset=utf-8', status=200, download=None):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Cross-Origin-Resource-Policy', 'same-origin')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https: http:; font-src 'self' data:; connect-src 'self'; object-src 'none'; frame-src http://127.0.0.1:*; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        if download:
            self.send_header('Content-Disposition', "attachment; filename*=UTF-8''" + quote(download))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, value, status=200):
        self._send(json.dumps(value, ensure_ascii=False, default=str).encode('utf-8'), status=status)

    def _run(self, fn):
        try:
            fn()
        except (StudioError, FeatureError, CloudError, UpdateError) as e:
            self._json({'error': str(e)}, getattr(e, 'status', 400))
        except (FileNotFoundError, IsADirectoryError):
            self._json({'error': '파일을 찾을 수 없습니다.'}, 404)
        except (ValueError, UnicodeError, yaml.YAMLError):
            self._json({'error': '입력 데이터를 읽을 수 없습니다.'}, 400)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            logger.error('Request failed: %s', type(e).__name__)
            self._json({'error': '작업을 완료하지 못했습니다. 로컬 터미널의 오류를 확인해 주세요. 초안은 보존됩니다.'}, 500)

    def do_GET(self):
        self._run(self._get)

    def _get(self):
        self._guard()
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        studio = self.server.studio
        arg = lambda name: query.get(name, [''])[0]
        if path == '/api/bootstrap':
            self._json(studio.bootstrap() | {'token': self.server.token})
        elif path == '/api/posts':
            self._json(studio.open_post(arg('path')))
        elif path.startswith('/api/drafts/'):
            self._json(studio.get_draft(path.split('/')[-1]))
        elif path == '/api/history':
            self._json(studio.history(arg('id')))
        elif path == '/api/templates':
            self._json(studio.features.templates())
        elif path == '/api/jobs':
            self._json(studio.features.job(arg('id')))
        elif path == '/api/backup':
            self._send(studio.features.archive(), 'application/zip', download='blog-studio-backup.zip')
        elif path == '/api/preview/status':
            self._json(studio.preview.status())
        elif path == '/api/cloud/status':
            self._json(studio.cloud.status())
        elif path == '/api/cloud/login-status':
            self._json(studio.cloud.login_status())
        elif path == '/api/cloud/repositories':
            self._json(studio.cloud.repositories())
        elif path == '/api/update/check':
            self._json(studio.updater.check())
        elif path == '/api/export':
            d = studio.get_draft(arg('id'))
            self._send(studio.export(d['id']).encode('utf-8'), 'text/markdown; charset=utf-8', download=f'{d["date"]}-{d["slug"] or slugify(d["title"])}.md')
        elif path.startswith('/api/'):
            raise StudioError('API를 찾을 수 없습니다.', 404)
        elif path.startswith('/assets/'):
            file = studio.safe_path(path.lstrip('/'))
            # Return only passive image media, never arbitrary source files from the repo.
            if file.suffix.lower() not in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.avif', '.svg', '.ico'):
                raise StudioError('이미지 파일만 열 수 있습니다.', 403)
            if not file.is_file():
                match = re.fullmatch(r'/assets/images/posts/studio-([0-9a-f]{32})/([^/]+)', path)
                if match:
                    file = studio.safe_path(f'.blog-studio/uploads/{match[1]}/{match[2]}')
            self._send(file.read_bytes(), mimetypes.guess_type(file.name)[0] or 'application/octet-stream')
        else:
            relative = path.removeprefix('/static/').lstrip('/') or 'index.html'
            if '\\' in relative or '..' in relative.split('/'):
                raise StudioError('허용되지 않은 경로입니다.', 403)
            static = HERE / 'static'
            file = static.joinpath(relative)
            if not file.resolve().is_relative_to(static.resolve()) or file.is_symlink():
                raise StudioError('허용되지 않은 경로입니다.', 403)
            self._send(file.read_bytes(), mimetypes.guess_type(file.name)[0] or 'application/octet-stream')

    def do_POST(self):
        self._run(self._post)

    def _post(self):
        self._guard(mutate=True)
        if self.headers.get('Content-Type', '').split(';')[0].strip() != 'application/json':
            raise StudioError('application/json 요청이 필요합니다.', 415)
        try:
            size = int(self.headers.get('Content-Length', '0'))
        except ValueError as e:
            raise StudioError('올바르지 않은 요청 크기입니다.') from e
        limit = 172 * 1024 * 1024 if urlparse(self.path).path == '/api/backup/restore' else MAX_REQUEST
        if size < 0 or size > limit:
            raise StudioError('요청이 너무 큽니다.', 413)
        payload = json.loads(self.rfile.read(size))
        if not isinstance(payload, dict):
            raise StudioError('JSON 객체가 필요합니다.')
        studio = self.server.studio
        routes = {'/api/drafts': studio.save_draft, '/api/upload': studio.upload,
                  '/api/check': lambda p: studio.check(p.get('id')), '/api/publish': studio.publish,
                  '/api/restore': studio.restore, '/api/import': studio.import_markdown}
        routes.update({
            '/api/templates/save': studio.features.save_template, '/api/templates/delete': studio.features.delete_template,
            '/api/history/diff': studio.features.history_diff, '/api/link-preview': studio.features.link_preview,
            '/api/backup/restore': studio.features.restore_upload,
            '/api/sync': lambda p: studio.features.start_job('sync', studio.features.sync),
            '/api/preview/build': lambda p: studio.features.start_job('preview', lambda: studio.preview.build(p.get('id'))),
            '/api/preview/prepare': lambda p: studio.features.start_job('preview', studio.preview.prepare),
            '/api/cloud/connect': studio.cloud.connect_token, '/api/cloud/login': lambda p: studio.cloud.start_login(),
            '/api/cloud/disconnect': lambda p: studio.cloud.disconnect(), '/api/cloud/author': studio.cloud.configure_author,
            '/api/cloud/clone': studio.cloud.clone, '/api/cloud/deployment': studio.cloud.deployment, '/api/cloud/retry': studio.cloud.retry,
            '/api/update/download': lambda p: studio.updater.download(), '/api/update/apply': lambda p: studio.updater.apply(),
        })
        fn = routes.get(urlparse(self.path).path)
        if not fn:
            raise StudioError('API를 찾을 수 없습니다.', 404)
        self._json(fn(payload))

    def do_DELETE(self):
        def run():
            self._guard(mutate=True)
            path = urlparse(self.path).path
            if not path.startswith('/api/drafts/'):
                raise StudioError('API를 찾을 수 없습니다.', 404)
            self._json(self.server.studio.delete_draft(path.split('/')[-1]))
        self._run(run)


def create_server(repo: Path, port=0):
    return StudioHTTPServer(repo, port)


def main():
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    parser = argparse.ArgumentParser(description='Blog Studio — 로컬 Jekyll 글쓰기 도구')
    parser.add_argument('--repo', type=Path, required=True, help='편집할 실제 Jekyll 블로그 폴더')
    parser.add_argument('--port', type=int, default=4310)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    try:
        server = create_server(args.repo, args.port)
    except (StudioError, OSError) as e:
        parser.exit(1, f'Blog Studio: {e}\n')
    url = f'http://127.0.0.1:{server.server_port}'
    logger.info('\n  Blog Studio  ·  %s\n  블로그: %s\n  종료: Ctrl+C\n', url, server.studio.repo)
    if not args.no_browser:
        def open_browser():
            with external_program_environment():
                webbrowser.open(url)
        threading.Timer(0.6, open_browser).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info('\nBlog Studio를 종료합니다. 초안은 로컬에 저장되어 있습니다.')
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
