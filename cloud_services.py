"""GitHub connection and deployment status, invoked only by local user actions.

Personal tokens live in memory. Existing GitHub CLI credentials remain managed
by GitHub CLI; neither API responses nor Git configuration expose their value.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, build_opener, HTTPRedirectHandler

from runtime import external_environment, external_program_environment, run_external, subprocess_options

API = 'https://api.github.com'
REPOSITORY = re.compile(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z')


class CloudError(RuntimeError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class GitHubRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urlparse(newurl)
        if parsed.scheme != 'https' or parsed.hostname != 'api.github.com' or parsed.port not in (None, 443):
            raise CloudError('GitHub API가 허용되지 않은 주소로 이동했습니다.')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def repository_name(value):
    value = str(value or '').strip()
    if value.startswith('git@github.com:'):
        value = value[len('git@github.com:'):]
    elif value.startswith('ssh://git@github.com/'):
        parsed = urlparse(value)
        if parsed.query or parsed.fragment or parsed.password or parsed.port not in (None, 22):
            raise CloudError('GitHub 저장소 주소를 확인해 주세요.')
        value = parsed.path.lstrip('/')
    elif value.startswith('https://github.com/'):
        parsed = urlparse(value)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.port not in (None, 443):
            raise CloudError('인증 정보가 없는 GitHub 저장소 주소를 입력해 주세요.')
        value = parsed.path.lstrip('/')
    value = value.rstrip('/').removesuffix('.git')
    if not REPOSITORY.fullmatch(value) or any(part in ('.', '..') for part in value.split('/')):
        raise CloudError('GitHub 저장소는 소유자/저장소 형식으로 입력해 주세요.')
    return value


def origin_repository(repo):
    if repo is None:
        return None
    try:
        result = run_external(['git', '-C', str(repo), 'remote', 'get-url', 'origin'],
                              capture_output=True, text=True, timeout=10)
        remote = result.stdout.strip()
        if result.returncode or not remote.startswith(('https://github.com/', 'git@github.com:', 'ssh://git@github.com/')):
            return None
        return repository_name(remote)
    except (OSError, subprocess.SubprocessError, CloudError):
        return None


class CloudService:
    def __init__(self, studio=None):
        self.studio = studio
        self._token = None
        self._system_credentials = True
        self._lock = threading.RLock()
        self._login = {'running': False, 'code': '', 'message': ''}
        self._login_process = None
        self._login_generation = 0

    def token(self):
        if self._token:
            return self._token
        if self._system_credentials and shutil.which('gh'):
            try:
                result = run_external(['gh', 'auth', 'token', '--hostname', 'github.com'],
                                      capture_output=True, text=True, timeout=10)
                if result.returncode == 0:
                    return result.stdout.strip() or None
            except (OSError, subprocess.SubprocessError):
                pass
        return None

    def request(self, method, path, payload=None, allow_missing=False):
        if not isinstance(path, str) or not path.startswith('/') or path.startswith('//') or '\\' in path or '\x00' in path:
            raise CloudError('GitHub API 경로가 올바르지 않습니다.')
        headers = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28',
                   'User-Agent': 'BlogStudio/0.4.0'}
        token = self.token()
        if token:
            headers['Authorization'] = 'Bearer ' + token
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
        if data is not None:
            headers['Content-Type'] = 'application/json'
        try:
            opener = build_opener(GitHubRedirects())
            with opener.open(Request(API + path, data=data, headers=headers, method=method), timeout=25) as response:
                raw = response.read(32 * 1024 * 1024 + 1)
                if len(raw) > 32 * 1024 * 1024:
                    raise CloudError('GitHub 응답이 너무 큽니다.')
                return json.loads(raw) if raw else {}
        except HTTPError as error:
            if error.code == 404 and allow_missing:
                return None
            messages = {401: 'GitHub 연결을 다시 확인해 주세요.', 403: 'GitHub 권한 또는 API 사용 한도를 확인해 주세요.',
                        404: '저장소나 작업을 찾을 수 없습니다. 주소와 접근 권한을 확인해 주세요.',
                        409: 'GitHub 내용이 변경됐습니다. 최신 상태를 확인한 뒤 다시 시도해 주세요.',
                        422: 'GitHub가 요청을 처리하지 못했습니다. 입력 내용과 권한을 확인해 주세요.'}
            raise CloudError(messages.get(error.code, f'GitHub 요청에 실패했습니다 (HTTP {error.code}).'), error.code) from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise CloudError('GitHub에 연결하지 못했습니다. 인터넷 연결을 확인해 주세요.', 502) from None

    def status(self):
        result = {'connected': False, 'login': '', 'git_available': bool(shutil.which('git')),
                  'gh_available': bool(shutil.which('gh')), 'credential_mode': 'session' if self._token else 'github-cli',
                  'repository': origin_repository(getattr(self.studio, 'repo', None)), 'login_flow': self.login_status()}
        if self.token():
            try:
                account = self.request('GET', '/user')
                result.update(connected=True, login=account.get('login', ''), avatar_url=account.get('avatar_url', ''))
            except CloudError as error:
                result['message'] = str(error)
        return result

    def connect_token(self, payload):
        value = payload.get('token')
        if not isinstance(value, str) or not 20 <= len(value.strip()) <= 512 or re.search(r'\s', value.strip()):
            raise CloudError('GitHub 개인 액세스 토큰을 입력해 주세요.')
        previous = self._token
        self._token = value.strip()
        try:
            account = self.request('GET', '/user')
        except Exception:
            self._token = previous
            raise
        return {'connected': True, 'login': account.get('login', ''), 'credential_mode': 'session',
                'message': '연결했습니다. 토큰은 앱을 종료하면 지워집니다.'}

    def disconnect(self):
        with self._lock:
            self._token = None
            self._system_credentials = False
            self._login_generation += 1
            self._login = {'running': False, 'code': '', 'message': '이 앱의 연결을 해제했습니다.'}
            if self._login_process is not None:
                try:
                    self._login_process.terminate()
                except OSError:
                    pass
                self._login_process = None
        return {'connected': False, 'message': '이 앱의 연결을 해제했습니다. GitHub CLI의 기존 로그인은 유지됩니다.'}

    def start_login(self):
        if not shutil.which('gh'):
            raise CloudError('GitHub CLI가 없어 브라우저 로그인을 시작할 수 없습니다. 아래 토큰 연결을 사용하거나 GitHub CLI를 설치해 주세요.')
        with self._lock:
            if self._login['running']:
                return self.login_status()
            self._login = {'running': True, 'code': '', 'message': 'GitHub 브라우저 로그인을 준비하고 있어요.',
                           'url': 'https://github.com/login/device'}
            self._login_generation += 1
            generation = self._login_generation
        def worker():
            try:
                env = external_environment()
                # A supplied process token would suppress the browser flow.
                for key in ('GH_TOKEN', 'GITHUB_TOKEN', 'GH_ENTERPRISE_TOKEN', 'GITHUB_ENTERPRISE_TOKEN'):
                    env.pop(key, None)
                env['GH_PROMPT_DISABLED'] = '1'
                with external_program_environment():
                    process = subprocess.Popen(['gh', 'auth', 'login', '--hostname', 'github.com', '--git-protocol', 'https', '--web'],
                                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                               text=True, encoding='utf-8', errors='replace', env=env, **subprocess_options())
                with self._lock:
                    if generation != self._login_generation:
                        process.terminate()
                        return
                    self._login_process = process
                process.stdin.write('\n')
                process.stdin.flush()
                process.stdin.close()
                deadline = threading.Timer(600, process.kill)
                deadline.daemon = True
                deadline.start()
                try:
                    for line in process.stdout:
                        match = re.search(r'\b([A-Z0-9]{4}-[A-Z0-9]{4})\b', line)
                        if match:
                            with self._lock:
                                if generation == self._login_generation:
                                    self._login.update(code=match.group(1), message='브라우저에서 코드를 입력하고 GitHub 연결을 허용해 주세요.')
                    code = process.wait()
                finally:
                    deadline.cancel()
                if code:
                    raise CloudError('로그인이 완료되지 않았습니다. 다시 시도하거나 토큰으로 연결해 주세요.')
                with self._lock:
                    if generation != self._login_generation:
                        return
                    self._system_credentials = True
                    self._token = None
                    run_external(['gh', 'auth', 'setup-git', '--hostname', 'github.com'], capture_output=True, text=True, timeout=15)
                    self._login.update(running=False, code='', message='GitHub 로그인을 완료했습니다.')
            except Exception:
                with self._lock:
                    if generation == self._login_generation:
                        self._login.update(running=False, message='로그인이 완료되지 않았습니다. 다시 시도하거나 토큰으로 연결해 주세요.')
            finally:
                with self._lock:
                    if generation == self._login_generation:
                        self._login_process = None
        threading.Thread(target=worker, name='GitHubLogin', daemon=True).start()
        return self.login_status()

    def login_status(self):
        with self._lock:
            return dict(self._login)

    def git_environment(self, force_github=False):
        if not force_github and not origin_repository(getattr(self.studio, 'repo', None)):
            return {}
        # Tokens travel in an inherited child environment, never URL/argv/config.
        token = self._token
        if not token:
            return {}
        encoded = base64.b64encode(('x-access-token:' + token).encode()).decode('ascii')
        return {'GIT_CONFIG_COUNT': '3', 'GIT_CONFIG_KEY_0': 'http.https://github.com/.extraheader',
                'GIT_CONFIG_VALUE_0': 'Authorization: Basic ' + encoded, 'GIT_TERMINAL_PROMPT': '0',
                'GIT_CONFIG_KEY_1': 'url.https://github.com/.insteadOf', 'GIT_CONFIG_VALUE_1': 'git@github.com:',
                'GIT_CONFIG_KEY_2': 'url.https://github.com/.insteadOf', 'GIT_CONFIG_VALUE_2': 'ssh://git@github.com/'}

    def repositories(self):
        items = self.request('GET', '/user/repos?sort=updated&per_page=100&affiliation=owner,collaborator,organization_member')
        return {'repositories': [{'name': item['full_name'], 'private': bool(item.get('private')),
                                  'url': item.get('html_url', '')} for item in items]}

    def configure_author(self, payload):
        if self.studio is None:
            raise CloudError('먼저 블로그 폴더를 연결해 주세요.')
        name, email = str(payload.get('name', '')).strip(), str(payload.get('email', '')).strip()
        if not name or len(name) > 100 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email) or len(email) > 200 or '\n' in name:
            raise CloudError('Git 작성자 이름과 이메일을 입력해 주세요.')
        self.studio.git('config', '--local', 'user.name', name)
        self.studio.git('config', '--local', 'user.email', email)
        return {'message': '이 블로그의 Git 작성자 정보를 저장했습니다.'}

    def clone(self, payload):
        repository = repository_name(payload.get('repository'))
        destination = Path(str(payload.get('destination', ''))).expanduser()
        if not destination.is_absolute() or destination.exists() or destination.is_symlink() or not destination.parent.is_dir():
            raise CloudError('기존 폴더를 덮어쓰지 않도록, 새 폴더의 전체 경로를 입력해 주세요.')
        if not shutil.which('git'):
            raise CloudError('Git 설치가 필요합니다. Git을 설치한 뒤 다시 시도해 주세요.')
        # Check authorization/repository first; private-token values never enter the URL.
        self.request('GET', '/repos/' + repository)
        env = {**os.environ, **self.git_environment(force_github=True), 'GIT_TERMINAL_PROMPT': '0'}
        try:
            result = run_external(['git', 'clone', '--', 'https://github.com/' + repository + '.git', str(destination)],
                                  capture_output=True, text=True, timeout=180, env=env)
        except (OSError, subprocess.SubprocessError):
            raise CloudError('저장소를 가져오지 못했습니다. 네트워크와 Git 설치를 확인해 주세요.') from None
        if result.returncode:
            raise CloudError('저장소를 가져오지 못했습니다. Git 설치, 저장소 권한과 네트워크를 확인해 주세요.')
        is_blog = (destination / '_config.yml').is_file() and (destination / '_posts').is_dir()
        return {'path': str(destination.resolve()), 'is_blog': is_blog,
                'message': '블로그를 가져왔습니다. 이 폴더를 선택해 작성할 수 있어요.' if is_blog else '저장소를 가져왔지만 Jekyll 블로그 구조가 없습니다. _config.yml과 _posts를 확인해 주세요.'}

    def deployment(self, payload):
        repo = origin_repository(getattr(self.studio, 'repo', None))
        if not repo:
            raise CloudError('이 블로그의 origin을 GitHub 저장소에 연결해 주세요.')
        commit = str(payload.get('commit', ''))
        if not re.fullmatch(r'[0-9a-fA-F]{40}', commit):
            raise CloudError('게시 상태를 확인할 전체 커밋 주소가 필요합니다.')
        prefix = '/repos/' + repo
        query = urlencode({'head_sha': commit, 'per_page': 20})
        response = self.request('GET', prefix + '/actions/runs?' + query)
        runs = [{'id': run['id'], 'name': run.get('name', 'Workflow'), 'status': run.get('status'),
                 'conclusion': run.get('conclusion'), 'url': run.get('html_url', ''), 'sha': run.get('head_sha')}
                for run in response.get('workflow_runs', []) if run.get('head_sha') == commit]
        result = {'commit': commit, 'state': 'waiting', 'message': 'GitHub의 게시 작업이 시작되기를 기다리고 있어요.',
                  'runs': runs, 'url': '', 'repository_url': 'https://github.com/' + repo}
        def optional(path):
            try:
                return self.request('GET', path, allow_missing=True)
            except CloudError as error:
                if error.status in (403, 404):
                    result['permission_note'] = '정확한 게시 확인에는 Pages와 Deployments 읽기 권한이 필요할 수 있습니다.'
                    return None
                raise
        deployments = optional(prefix + '/deployments?' + urlencode({'sha': commit, 'environment': 'github-pages', 'per_page': 10})) or []
        for deployment in deployments:
            if deployment.get('sha') != commit:
                continue
            states = optional(prefix + '/deployments/' + str(int(deployment['id'])) + '/statuses?per_page=1')
            if states:
                latest = states[0]
                state = latest.get('state')
                if state == 'success':
                    return {**result, 'state': 'published', 'message': '이 커밋의 사이트 게시가 완료됐습니다.',
                            'url': latest.get('environment_url') or latest.get('target_url') or ''}
                if state in ('failure', 'error'):
                    return {**result, 'state': 'failed', 'message': '사이트 게시가 실패했습니다. 아래 작업 로그에서 원인을 확인해 주세요.'}
                if state in ('pending', 'queued', 'in_progress'):
                    result.update(state='building', message='사이트를 빌드하고 게시하고 있어요.')
        # Legacy Pages builds provide their own exact commit result.
        pages = optional(prefix + '/pages/builds/latest')
        if pages and pages.get('commit') == commit:
            if pages.get('status') == 'built':
                info = optional(prefix + '/pages') or {}
                return {**result, 'state': 'published', 'message': '이 커밋의 사이트 게시가 완료됐습니다.', 'url': info.get('html_url', '')}
            if pages.get('status') == 'errored':
                return {**result, 'state': 'failed', 'message': 'GitHub Pages 빌드가 실패했습니다.'}
        if any(run['conclusion'] in ('failure', 'timed_out', 'cancelled', 'action_required') for run in runs):
            result.update(state='failed', message='GitHub 작업이 완료되지 못했습니다. 실패한 작업의 로그를 확인해 주세요.')
        elif runs and all(run['status'] == 'completed' and run['conclusion'] in ('success', 'skipped', 'neutral') for run in runs):
            result.update(state='awaiting_deployment', message='작업 검사는 끝났습니다. 실제 Pages 게시 완료를 확인하고 있어요.')
        elif runs:
            result.update(state='building', message='GitHub에서 빌드 작업을 진행하고 있어요.')
        return result

    def retry(self, payload):
        repo = origin_repository(getattr(self.studio, 'repo', None))
        if not repo or type(payload.get('run_id')) is not int or payload['run_id'] <= 0:
            raise CloudError('다시 실행할 GitHub 작업을 선택해 주세요.')
        run = self.request('GET', f'/repos/{repo}/actions/runs/{payload["run_id"]}')
        if run.get('status') != 'completed' or run.get('conclusion') not in ('failure', 'cancelled', 'timed_out', 'action_required'):
            raise CloudError('실패하거나 취소된 작업만 다시 실행할 수 있습니다.')
        self.request('POST', f'/repos/{repo}/actions/runs/{payload["run_id"]}/rerun-failed-jobs')
        return {'message': '실패한 GitHub 작업을 다시 실행했습니다.'}
