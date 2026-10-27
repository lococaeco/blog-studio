"""Portable authoring services. All writes stay inside the selected blog."""
from __future__ import annotations

import base64
import copy
import difflib
import hashlib
from html.parser import HTMLParser
import http.client
import io
import ipaddress
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import ssl
import stat
import tempfile
import threading
import time
from urllib.parse import urljoin, urlsplit, quote
import uuid
import zipfile

MAX_ARCHIVE = 128 * 1024 * 1024
MAX_SYNC_ARCHIVE = 32 * 1024 * 1024
MAX_EXPANDED = 256 * 1024 * 1024


class FeatureError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def atomic(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(content)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def public_address(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise FeatureError('http 또는 https 링크를 입력해 주세요.')
    port = parsed.port or (443 if parsed.scheme == 'https' else 80)
    if port not in (80, 443):
        raise FeatureError('표준 웹 주소만 미리 볼 수 있습니다.')
    addresses = socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)
    resolved = [ipaddress.ip_address(item[4][0]) for item in addresses]
    if not resolved or any(not address.is_global or address.is_multicast for address in resolved):
        raise FeatureError('인터넷에 공개된 웹페이지의 링크만 미리 볼 수 있습니다.')
    return parsed, port, addresses[0][4][0]


def fetch_public_html(url):
    # Pin the validated DNS result for each connection, including redirects.
    for _ in range(5):
        parsed, port, address = public_address(url)
        connection = http.client.HTTPConnection(parsed.hostname, port, timeout=8)
        sock = socket.create_connection((address, port), timeout=8)
        if parsed.scheme == 'https':
            sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parsed.hostname)
        connection.sock = sock
        try:
            target = (parsed.path or '/') + ('?' + parsed.query if parsed.query else '')
            connection.request('GET', target, headers={
                'Host': parsed.netloc, 'User-Agent': 'BlogStudio-LinkPreview/0.2', 'Accept': 'text/html',
            })
            response = connection.getresponse()
            if response.status in (301, 302, 303, 307, 308):
                url = urljoin(url, response.getheader('Location', ''))
                continue
            if response.status != 200 or 'html' not in response.getheader('Content-Type', '').lower():
                raise FeatureError('이 주소에서 웹페이지 정보를 읽을 수 없습니다.')
            raw = response.read(2 * 1024 * 1024 + 1)
            if len(raw) > 2 * 1024 * 1024:
                raise FeatureError('페이지가 너무 커서 링크 정보만 자동으로 가져올 수 없습니다.')
            charset = re.search(r'charset=([\w-]+)', response.getheader('Content-Type', ''))
            try:
                return url, raw.decode(charset[1] if charset else 'utf-8', errors='replace')
            except LookupError:
                return url, raw.decode('utf-8', errors='replace')
        finally:
            connection.close()
    raise FeatureError('주소 이동이 너무 많습니다.')


class Metadata(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta, self.title, self.in_title = {}, '', False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'title':
            self.in_title = True
        if tag == 'meta':
            key = attrs.get('property', attrs.get('name', '')).lower()
            self.meta[key] = attrs.get('content', '')[:2000]

    def handle_endtag(self, tag):
        if tag == 'title':
            self.in_title = False

    def handle_data(self, text):
        if self.in_title:
            self.title = (self.title + text)[:500]


class Features:
    def __init__(self, studio, cloud=None):
        self.studio, self.cloud = studio, cloud
        self.jobs, self.job_lock = {}, threading.Lock()
        self.workers = []
        self.closing = False

    def start_job(self, kind, callback):
        with self.job_lock:
            if self.closing:
                raise FeatureError('앱을 종료하고 있습니다.', 409)
            if any(j['kind'] == kind and j['state'] == 'running' for j in self.jobs.values()):
                raise FeatureError('같은 작업을 진행 중입니다. 잠시 기다려 주세요.', 409)
            identifier = uuid.uuid4().hex
            self.jobs[identifier] = {'id': identifier, 'kind': kind, 'state': 'running'}

        def run():
            try:
                result = callback()
                with self.job_lock:
                    self.jobs[identifier].update(state='done', result=result)
            except Exception as error:
                with self.job_lock:
                    self.jobs[identifier].update(state='error', error=str(error)[:1500])
        with self.job_lock:
            # Keep a bounded record and wait for writers before releasing the
            # repository lock at shutdown.
            self.workers = [t for t in self.workers if t.is_alive()]
            for key in list(self.jobs):
                if len(self.jobs) <= 100:
                    break
                if self.jobs[key]['state'] != 'running':
                    del self.jobs[key]
            worker = threading.Thread(target=run, name='BlogStudio-' + kind, daemon=True)
            self.workers.append(worker)
            worker.start()
        return {'job': identifier}

    def close(self):
        with self.job_lock:
            self.closing = True
            workers = list(self.workers)
        for worker in workers:
            worker.join()

    def job(self, identifier):
        with self.job_lock:
            if identifier not in self.jobs:
                raise FeatureError('작업을 찾을 수 없습니다.', 404)
            return copy.deepcopy(self.jobs[identifier])

    def templates(self):
        path = self.studio.safe_path('.blog-studio/templates.json')
        return {'items': json.loads(path.read_text('utf-8')) if path.exists() else []}

    def save_template(self, payload):
        label, body = payload.get('label', ''), payload.get('body', '')
        if not isinstance(label, str) or not label.strip() or len(label) > 100 or not isinstance(body, str) or len(body.encode()) > 4 * 1024 * 1024:
            raise FeatureError('이름과 4MB 이하의 내용을 입력해 주세요.')
        label = label.strip()
        with self.studio.lock:
            items = self.templates()['items']
            identifier = payload.get('id') or uuid.uuid4().hex
            if not isinstance(identifier, str) or not re.fullmatch(r'[0-9a-f]{32}', identifier):
                raise FeatureError('템플릿 ID가 올바르지 않습니다.')
            item = {'id': identifier, 'label': label, 'body': body, 'kind': 'snippet' if payload.get('kind') == 'snippet' else 'template'}
            items = [old for old in items if old['id'] != identifier] + [item]
            if len(items) > 100:
                raise FeatureError('템플릿과 재사용 문구는 합계 100개까지 저장할 수 있습니다.')
            atomic(self.studio.safe_path('.blog-studio/templates.json'), json.dumps(items, ensure_ascii=False).encode())
            return item

    def delete_template(self, payload):
        with self.studio.lock:
            items = [item for item in self.templates()['items'] if item['id'] != payload.get('id')]
            atomic(self.studio.safe_path('.blog-studio/templates.json'), json.dumps(items, ensure_ascii=False).encode())
        return {'ok': True}

    def history_diff(self, payload):
        current = self.studio.get_draft(payload.get('id'))
        version = payload.get('history_id', '')
        if not isinstance(version, str) or not re.fullmatch(r'\d{10}', version):
            raise FeatureError('저장 기록을 선택해 주세요.')
        path = self.studio.safe_path(f'.blog-studio/history/{current["id"]}/{version}.json')
        if not path.is_file():
            raise FeatureError('저장 기록을 찾을 수 없습니다.', 404)
        old = json.loads(path.read_text('utf-8'))
        def comparable(draft):
            keys = ('title', 'excerpt', 'tags', 'date', 'slug', 'category', 'teaser',
                    'published', 'toc', 'mathjax', 'comments', 'feature')
            metadata = '\n'.join(key + ': ' + json.dumps(draft.get(key), ensure_ascii=False) for key in keys)
            return metadata + '\n\n' + draft.get('body', '')
        before, after = comparable(old), comparable(current)
        lines = list(difflib.unified_diff(before.splitlines(), after.splitlines(), fromfile='선택한 기록', tofile='현재 초안', lineterm=''))
        return {'diff': '\n'.join(lines), 'before': before, 'after': after,
                'changed': bool(lines), 'history_id': version}

    def link_preview(self, payload):
        url = payload.get('url', '')
        if not isinstance(url, str) or len(url) > 4000:
            raise FeatureError('올바른 링크를 입력해 주세요.')
        try:
            final, html = fetch_public_html(url)
            parser = Metadata()
            parser.feed(html)
            image = urljoin(final, parser.meta.get('og:image', '')) if parser.meta.get('og:image') else ''
            if image:
                try:
                    public_address(image)
                except (FeatureError, OSError, ValueError):
                    image = ''
            return {'url': final, 'title': parser.meta.get('og:title') or parser.title or urlsplit(final).hostname,
                    'description': parser.meta.get('og:description') or parser.meta.get('description', ''), 'image': image}
        except (OSError, ValueError, http.client.HTTPException) as error:
            raise FeatureError('링크 정보를 가져오지 못했습니다. 제목과 설명을 직접 입력할 수 있습니다.') from error

    @staticmethod
    def allowed_archive_path(name, drafts_only=False):
        if not isinstance(name, str) or '\\' in name or '\x00' in name or any(p in ('', '.', '..') for p in name.split('/')):
            return False
        path = PurePosixPath(name)
        if path.is_absolute():
            return False
        return (name == '.blog-studio/templates.json' or
                re.fullmatch(r'\.blog-studio/(drafts/[0-9a-f]{32}\.json|history/[0-9a-f]{32}/[0-9]{10}\.json|uploads/[0-9a-f]{32}/[^/]+)', name) is not None or
                (not drafts_only and (name.startswith('_posts/') and path.suffix.lower() in ('.md', '.markdown') or
                                     name.startswith('assets/images/') and path.suffix.lower() in ('.png', '.jpg', '.jpeg', '.webp', '.gif', '.avif', '.svg', '.ico'))))

    def archive(self, drafts_only=False):
        output = io.BytesIO()
        with self.studio.lock, zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
            roots = ['.blog-studio/drafts', '.blog-studio/history', '.blog-studio/uploads']
            if not drafts_only:
                roots += ['_posts', 'assets/images']
            candidates = [self.studio.safe_path('.blog-studio/templates.json')]
            for root in roots:
                candidates.extend(self.studio.safe_path(root).rglob('*'))
            total = 0
            for path in candidates:
                if path.is_symlink() or not path.is_file():
                    continue
                relative = path.relative_to(self.studio.repo).as_posix()
                if not self.allowed_archive_path(relative, drafts_only):
                    continue
                path = self.studio.safe_path(relative)
                total += path.stat().st_size
                if total > MAX_EXPANDED or len(archive.infolist()) >= 10000:
                    raise FeatureError('백업 범위가 너무 큽니다. 파일을 나누어 보관해 주세요.')
                archive.write(path, relative)
            archive.writestr('blog-studio-backup.json', json.dumps({'format': 1, 'kind': 'drafts' if drafts_only else 'full', 'created': time.time()}))
        data = output.getvalue()
        limit = MAX_SYNC_ARCHIVE if drafts_only else MAX_ARCHIVE
        if len(data) > limit:
            raise FeatureError(f'백업 ZIP이 {limit // (1024 * 1024)}MB를 초과합니다. 큰 이미지를 별도로 보관해 주세요.')
        return data

    def validate_archive(self, raw):
        if len(raw) > MAX_ARCHIVE:
            raise FeatureError('128MB 이하의 Blog Studio 백업 ZIP을 선택해 주세요.')
        result = {}
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                infos = archive.infolist()
                if len(infos) > 10001 or sum(i.file_size for i in infos) > MAX_EXPANDED:
                    raise FeatureError('압축을 푼 파일의 용량이나 개수가 너무 큽니다.')
                for info in infos:
                    # ZipInfo normalizes backslashes on Windows and truncates
                    # NULs on every OS. Check the original archive name too.
                    if info.orig_filename != info.filename:
                        raise FeatureError('백업에 허용되지 않은 경로가 있습니다.')
                    if info.filename in result or stat.S_ISLNK(info.external_attr >> 16) or info.flag_bits & 1:
                        raise FeatureError('중복 파일, 링크, 암호화 파일이 들어 있는 백업은 사용할 수 없습니다.')
                    if info.filename != 'blog-studio-backup.json' and not self.allowed_archive_path(info.filename):
                        raise FeatureError('백업에 허용되지 않은 경로가 있습니다.')
                    result[info.filename] = archive.read(info)
                manifest = json.loads(result.get('blog-studio-backup.json', b'{}'))
                if not isinstance(manifest, dict) or manifest.get('format') != 1:
                    raise FeatureError('Blog Studio에서 만든 백업 ZIP을 선택해 주세요.')
        except (zipfile.BadZipFile, KeyError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise FeatureError('백업 ZIP을 읽을 수 없습니다.') from error
        return result

    def restore_archive(self, raw, drafts_only=False, baseline=None):
        files = self.validate_archive(raw)
        drafts, histories, templates = {}, {}, []
        # Validate every structured file and destination before the first write.
        try:
            for name, content in files.items():
                if name != 'blog-studio-backup.json':
                    self.studio.safe_path(name)
                draft_match = re.fullmatch(r'\.blog-studio/drafts/([0-9a-f]{32})\.json', name)
                history_match = re.fullmatch(r'\.blog-studio/history/([0-9a-f]{32})/([0-9]{10})\.json', name)
                if draft_match or history_match:
                    match = draft_match or history_match
                    item = json.loads(content)
                    if not isinstance(item, dict) or item.get('id') != match[1]:
                        raise FeatureError('백업의 초안 ID가 올바르지 않습니다.')
                    self.studio._validate_fields(item)
                    if not all(key in item for key in ('body', 'title', 'date', 'category', 'slug')):
                        raise FeatureError('필수 내용이 빠진 초안입니다.')
                    if type(item.get('version')) is not int or not 0 <= item['version'] < 9999999999:
                        raise FeatureError('백업의 저장 버전이 올바르지 않습니다.')
                    if history_match and item['version'] != int(history_match[2]):
                        raise FeatureError('저장 기록의 버전과 파일 이름이 일치하지 않습니다.')
                    item = self.studio._defaults() | item
                    item['_meta'] = item.get('_meta') if isinstance(item.get('_meta'), dict) else {}
                    item['_baseline'] = item.get('_baseline') if isinstance(item.get('_baseline'), dict) else {}
                    item['_header'] = item.get('_header') if isinstance(item.get('_header'), str) else ''
                    item['updated_at'] = str(item.get('updated_at', ''))
                    if item.get('source_path'):
                        if not isinstance(item['source_path'], str) or not item['source_path'].startswith('_posts/'):
                            raise FeatureError('백업의 원본 글 경로가 올바르지 않습니다.')
                        self.studio.safe_path(item['source_path'])
                    if draft_match:
                        drafts[match[1]] = item
                    else:
                        histories[name] = item
                elif name == '.blog-studio/templates.json':
                    templates = json.loads(content)
                    if not isinstance(templates, list) or len(templates) > 100:
                        raise FeatureError('백업의 템플릿 목록이 올바르지 않습니다.')
                    for item in templates:
                        if (not isinstance(item, dict) or not isinstance(item.get('id'), str)
                                or not re.fullmatch(r'[0-9a-f]{32}', item['id'])
                                or not isinstance(item.get('label'), str) or not item['label'].strip()
                                or len(item['label']) > 100 or not isinstance(item.get('body'), str)
                                or len(item['body'].encode()) > 4 * 1024 * 1024
                                or item.get('kind') not in ('template', 'snippet')):
                            raise FeatureError('백업의 템플릿 내용이 올바르지 않습니다.')
        except (ValueError, UnicodeDecodeError, TypeError) as error:
            raise FeatureError('백업의 초안이나 템플릿을 읽을 수 없습니다.') from error
        imported = conflicts = added = 0
        id_map, new_ids = {}, set()
        with self.studio.lock:
            merged_templates = {item['id']: item for item in self.templates()['items']}
            for item in templates:
                while item['id'] in merged_templates and merged_templates[item['id']] != item:
                    item = dict(item, id=uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(item, sort_keys=True)).hex)
                merged_templates[item['id']] = item
            if len(merged_templates) > 100:
                raise FeatureError('합친 템플릿이 100개를 넘습니다. 불필요한 항목을 정리한 뒤 다시 가져와 주세요.')

            for old_id, incoming in drafts.items():
                identifier = old_id
                existing_version = 0
                if self.studio._draft_path(old_id).exists():
                    existing = self.studio.get_draft(old_id)
                    local_sig, remote_sig = self.draft_signature(existing), self.draft_signature(incoming)
                    base_sig = (baseline or {}).get(old_id)
                    if local_sig == remote_sig or remote_sig == base_sig:
                        id_map[old_id] = old_id
                        continue
                    if local_sig == base_sig:
                        existing_version = existing['version']
                    else:
                        identifier = uuid.uuid5(uuid.NAMESPACE_URL, old_id + remote_sig).hex
                        while self.studio._draft_path(identifier).exists():
                            if self.draft_signature(self.studio.get_draft(identifier)) == remote_sig:
                                break
                            identifier = uuid.uuid5(uuid.NAMESPACE_URL, identifier + remote_sig).hex
                    id_map[old_id] = identifier
                    if identifier != old_id and self.studio._draft_path(identifier).exists():
                        continue
                    if identifier != old_id:
                        conflicts += 1
                id_map[old_id] = identifier
                new_ids.add(identifier)
                draft = copy.deepcopy(incoming)
                versions = [v['version'] for v in histories.values() if v['id'] == old_id]
                draft.update(id=identifier, version=max([draft['version'], existing_version] + versions), studio_commits=[])
                draft.pop('last_commit', None)
                source = draft.get('source_path')
                source_matches = False
                if isinstance(source, str) and source.startswith('_posts/'):
                    try:
                        source_path = self.studio.safe_path(source)
                        source_bytes = source_path.read_bytes() if source_path.exists() else (files.get(source) if not drafts_only else None)
                        source_matches = source_bytes is not None and hashlib.sha256(source_bytes).hexdigest() == draft.get('source_revision')
                    except (OSError, ValueError):
                        source_matches = False
                baseline = draft['_baseline']
                if identifier != old_id or not source_matches or not all(k in baseline for k in ('date', 'slug', 'category')):
                    draft.update(source_path=None, source_revision=None, _header='', _baseline={}, _imported=True)
                if identifier != old_id:
                    for key in ('body', 'teaser'):
                        draft[key] = draft[key].replace('studio-' + old_id + '/', 'studio-' + identifier + '/')
                self.studio._persist(draft)
                imported += 1

            for name, content in files.items():
                if name in ('blog-studio-backup.json', '.blog-studio/templates.json') or name.startswith('.blog-studio/drafts/'):
                    continue
                if drafts_only and not self.allowed_archive_path(name, True):
                    continue
                history = histories.get(name)
                if history:
                    old_id = history['id']
                    identifier = id_map.get(old_id, old_id)
                    history = copy.deepcopy(history)
                    history.update(id=identifier)
                    for key in ('body', 'teaser'):
                        history[key] = history[key].replace('studio-' + old_id + '/', 'studio-' + identifier + '/')
                    name = f'.blog-studio/history/{identifier}/{history["version"]:010}.json'
                    content = json.dumps(history, ensure_ascii=False, indent=2).encode()
                    # Existing active branches keep their own version counter.
                    # Newer remote history is retained as recovery material.
                    path = self.studio._draft_path(identifier)
                    if identifier not in new_ids and path.exists() and history['version'] > self.studio.get_draft(identifier)['version']:
                        name = '.blog-studio/backups/import-history/' + name
                upload = re.fullmatch(r'\.blog-studio/uploads/([0-9a-f]{32})/([^/]+)', name)
                if upload:
                    name = f'.blog-studio/uploads/{id_map.get(upload[1], upload[1])}/{upload[2]}'
                target = self.studio.safe_path(name)
                if target.exists():
                    same = target.read_bytes() == content
                    if history and not same:
                        try:
                            same = json.loads(target.read_bytes()) == history
                        except (ValueError, UnicodeDecodeError):
                            pass
                    if not same:
                        held = self.studio.safe_path('.blog-studio/backups/import-' + hashlib.sha256(content).hexdigest()[:16] + '/' + name)
                        atomic(held, content)
                        conflicts += 1
                    continue
                atomic(target, content)
                added += 1
            if templates:
                atomic(self.studio.safe_path('.blog-studio/templates.json'), json.dumps(list(merged_templates.values()), ensure_ascii=False).encode())
        return {'drafts': imported, 'files': added, 'conflicts': conflicts,
                'message': f'초안 {imported}개, 파일 {added}개를 가져왔습니다. 충돌 {conflicts}개는 별도로 보관했습니다.'}

    @staticmethod
    def draft_signature(draft):
        keys = ('title', 'body', 'excerpt', 'tags', 'date', 'slug', 'category', 'teaser', 'published', 'toc', 'mathjax', 'comments', 'feature')
        content = {k: draft.get(k) for k in keys}
        for key in ('body', 'teaser'):
            if isinstance(content[key], str) and draft.get('id'):
                content[key] = content[key].replace('studio-' + draft['id'] + '/', 'studio-SELF/')
        return hashlib.sha256(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    def restore_upload(self, payload):
        try:
            raw = base64.b64decode(payload.get('data', ''), validate=True)
        except (ValueError, TypeError) as error:
            raise FeatureError('백업 파일을 읽을 수 없습니다.') from error
        return self.restore_archive(raw)

    def sync(self):
        if self.cloud is None:
            raise FeatureError('GitHub에 먼저 연결해 주세요.')
        account = self.cloud.request('GET', '/user')
        owner = account['login']
        repo = owner + '/blog-studio-drafts'
        remote = self.cloud.request('GET', '/repos/' + repo, allow_missing=True)
        if remote is None:
            remote = self.cloud.request('POST', '/user/repos', {'name': 'blog-studio-drafts', 'private': True, 'auto_init': True,
                'description': 'Private Blog Studio drafts and attachments'})
        if remote.get('private') is not True:
            raise FeatureError('blog-studio-drafts가 공개 저장소입니다. 초안을 보내지 않았습니다. 비공개 저장소로 설정해 주세요.')
        space = str(self.studio.config.get('repository') or self.studio.config.get('url') or self.studio.config.get('title', 'blog'))
        namespace = hashlib.sha256(space.encode()).hexdigest()[:20]
        state_key = hashlib.sha256((repo + '/' + namespace).encode()).hexdigest()[:20]
        state_path = self.studio.safe_path('.blog-studio/sync-' + state_key + '.json')
        try:
            sync_state = json.loads(state_path.read_text('utf-8'))
            if not isinstance(sync_state, dict) or not isinstance(sync_state.get('signatures'), dict):
                sync_state = {}
        except (OSError, ValueError):
            sync_state = {}
        prefix = '/repos/' + repo + '/contents/snapshots/' + namespace
        manifest = self.cloud.request('GET', prefix + '/latest.json', allow_missing=True)
        restored = {'drafts': 0, 'conflicts': 0}
        if manifest:
            previous = json.loads(base64.b64decode(manifest['content']))
            if (not isinstance(previous, dict) or previous.get('format') != 1
                    or not isinstance(previous.get('digest'), str) or not re.fullmatch(r'[0-9a-f]{64}', previous['digest'])
                    or type(previous.get('chunks')) is not int or not 0 < previous['chunks'] <= 80):
                raise FeatureError('동기화 기록을 읽을 수 없습니다.')
            data = bytearray()
            for index in range(previous['chunks']):
                part = self.cloud.request('GET', prefix + '/' + previous['digest'] + f'/{index}.bin')
                data.extend(base64.b64decode(part['content']))
                if len(data) > MAX_SYNC_ARCHIVE:
                    raise FeatureError('동기화 파일이 너무 큽니다.')
            if hashlib.sha256(data).hexdigest() != previous['digest']:
                raise FeatureError('동기화 파일 검증에 실패했습니다. 로컬 초안은 유지됩니다.')
            if previous['digest'] != sync_state.get('digest'):
                restored = self.restore_archive(bytes(data), drafts_only=True, baseline=sync_state.get('signatures'))
        with self.studio.lock:
            raw = self.archive(drafts_only=True)
            signatures = {path.stem: self.draft_signature(self.studio.get_draft(path.stem))
                          for path in self.studio.safe_path('.blog-studio/drafts').glob('*.json')}
        digest = hashlib.sha256(raw).hexdigest()
        chunks = [raw[i:i + 450000] for i in range(0, len(raw), 450000)]
        for index, chunk in enumerate(chunks):
            path = prefix + '/' + digest + f'/{index}.bin'
            existing = self.cloud.request('GET', path, allow_missing=True)
            if not existing:
                self.cloud.request('PUT', path, {'message': 'Sync Blog Studio draft snapshot', 'content': base64.b64encode(chunk).decode()})
        document = base64.b64encode(json.dumps({'format': 1, 'digest': digest, 'chunks': len(chunks)}).encode()).decode()
        payload = {'message': 'Update Blog Studio draft snapshot', 'content': document}
        if manifest:
            payload['sha'] = manifest['sha']
        self.cloud.request('PUT', prefix + '/latest.json', payload)
        atomic(state_path, json.dumps({'digest': digest, 'signatures': signatures}).encode())
        return {'repository': repo, 'drafts': restored['drafts'], 'conflicts': restored['conflicts'],
                'message': f'비공개 저장소에 동기화했습니다. 가져온 초안 {restored["drafts"]}개, 충돌 복사본 {restored["conflicts"]}개.'}
