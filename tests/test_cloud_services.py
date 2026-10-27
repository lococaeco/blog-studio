"""Cloud requests use mocks; never read or change a developer's authentication."""
from __future__ import annotations
import base64
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cloud_services import CloudError, CloudService, GitHubRedirects, repository_name


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='blog-studio-cloud-')
        self.addCleanup(self.folder.cleanup)
        self.repo = Path(self.folder.name)
        self.cloud = CloudService(SimpleNamespace(repo=self.repo))
        self.cloud._system_credentials = False

    def test_repository_names_reject_secrets_and_other_hosts(self):
        for value in ('owner/blog', 'https://github.com/owner/blog.git/', 'git@github.com:owner/blog.git', 'ssh://git@github.com/owner/blog.git'):
            self.assertEqual(repository_name(value), 'owner/blog')
        for value in ('https://token@github.com/owner/blog', 'https://github.com.evil.test/owner/blog', '../repo', 'owner/repo?x=1', '--upload-pack=evil'):
            with self.assertRaises(CloudError):
                repository_name(value)

    def test_token_is_memory_only_and_never_returned(self):
        secret = 'github_pat_' + 'x' * 35
        with patch.object(self.cloud, 'request', return_value={'login': 'fixture'}):
            result = self.cloud.connect_token({'token': secret})
        self.assertEqual(result['credential_mode'], 'session')
        self.assertNotIn(secret, repr(result))
        self.assertEqual(list(self.repo.iterdir()), [])
        env = self.cloud.git_environment(force_github=True)
        self.assertEqual(env['GIT_CONFIG_VALUE_0'], 'Authorization: Basic ' + base64.b64encode(('x-access-token:' + secret).encode()).decode())
        self.assertEqual(env['GIT_CONFIG_VALUE_1'], 'git@github.com:')
        self.cloud.disconnect()
        self.assertIsNone(self.cloud.token())

    def test_failed_token_connection_preserves_previous_session(self):
        self.cloud._token = 'previous-session-token'
        with patch.object(self.cloud, 'request', side_effect=CloudError('bad', 401)):
            with self.assertRaises(CloudError):
                self.cloud.connect_token({'token': 'x' * 25})
        self.assertEqual(self.cloud._token, 'previous-session-token')

    def test_api_redirect_cannot_send_credentials_to_other_host(self):
        request = Request('https://api.github.com/user', headers={'Authorization': 'Bearer secret'})
        with self.assertRaises(CloudError):
            GitHubRedirects().redirect_request(request, None, 302, '', {}, 'https://example.invalid/user')

    def test_git_token_is_not_injected_for_non_github_remote(self):
        self.cloud._token = 'x' * 25
        with patch('cloud_services.origin_repository', return_value=None):
            self.assertEqual(self.cloud.git_environment(), {})

    def test_clone_refuses_existing_folder_before_network(self):
        with patch.object(self.cloud, 'request') as request:
            with self.assertRaises(CloudError):
                self.cloud.clone({'repository': 'owner/blog', 'destination': str(self.repo)})
            request.assert_not_called()

    def test_clone_uses_clean_url_and_child_environment(self):
        self.cloud._token = 'x' * 25
        destination = self.repo / 'new-blog'
        def cloned(command, **kwargs):
            self.assertNotIn(self.cloud._token, repr(command))
            self.assertIn('Authorization: Basic ', kwargs['env']['GIT_CONFIG_VALUE_0'])
            destination.mkdir()
            (destination / '_posts').mkdir()
            (destination / '_config.yml').write_text('title: Fixture\n')
            return SimpleNamespace(returncode=0)
        with patch('cloud_services.shutil.which', return_value='/git'), patch.object(self.cloud, 'request', return_value={}), patch('cloud_services.run_external', side_effect=cloned):
            result = self.cloud.clone({'repository': 'owner/blog', 'destination': str(destination)})
        self.assertTrue(result['is_blog'])

    def deployment_request(self, paths):
        def respond(method, path, payload=None, **kwargs):
            for key, value in paths.items():
                if key in path:
                    return value
            return None
        return respond

    def test_successful_ci_is_not_mistaken_for_site_publication(self):
        commit = 'a' * 40
        paths = {'/actions/runs?': {'workflow_runs': [{'id': 10, 'head_sha': commit, 'status': 'completed', 'conclusion': 'success', 'name': 'Tests'}]},
                 '/deployments?': [], '/pages/builds/latest': {'commit': 'b' * 40, 'status': 'built'}}
        with patch('cloud_services.origin_repository', return_value='owner/blog'), patch.object(self.cloud, 'request', side_effect=self.deployment_request(paths)):
            result = self.cloud.deployment({'commit': commit})
        self.assertEqual(result['state'], 'awaiting_deployment')

    def test_exact_commit_pages_deployment_success(self):
        commit = 'a' * 40
        paths = {'/actions/runs?': {'workflow_runs': []}, '/deployments?': [{'id': 55, 'sha': commit}],
                 '/deployments/55/statuses': [{'state': 'success', 'environment_url': 'https://owner.github.io/'}]}
        with patch('cloud_services.origin_repository', return_value='owner/blog'), patch.object(self.cloud, 'request', side_effect=self.deployment_request(paths)):
            result = self.cloud.deployment({'commit': commit})
        self.assertEqual(result['state'], 'published')
        self.assertEqual(result['url'], 'https://owner.github.io/')

    def test_retry_only_completed_failures(self):
        with patch('cloud_services.origin_repository', return_value='owner/blog'), patch.object(self.cloud, 'request', return_value={'status': 'completed', 'conclusion': 'success'}) as request:
            with self.assertRaises(CloudError):
                self.cloud.retry({'run_id': 10})
            self.assertEqual(request.call_count, 1)


if __name__ == '__main__':
    unittest.main()
