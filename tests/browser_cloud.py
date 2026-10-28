"""Real browser cloud/update UI checks with every remote action mocked."""
from __future__ import annotations

import unittest

from playwright.sync_api import expect
from browser_toolbar_support import tool
import browser_smoke as smoke


class CloudBrowserTests(unittest.TestCase):
    setUpClass = classmethod(smoke.BrowserTests.setUpClass.__func__)
    tearDownClass = classmethod(smoke.BrowserTests.tearDownClass.__func__)
    setUp = smoke.BrowserTests.setUp
    tearDown = smoke.BrowserTests.tearDown
    active = smoke.BrowserTests.active
    saved = smoke.BrowserTests.saved
    new_post = smoke.BrowserTests.new_post

    def connection(self):
        self.page.route('**/api/cloud/status', lambda route: route.fulfill(json={
            'connected': False, 'login': '', 'git_available': True, 'gh_available': False,
            'repository': 'fixture/blog', 'credential_mode': 'session'}))

    def open(self):
        self.connection()
        tool(self.page, '#cloud-open').click()
        expect(self.page.locator('#cloud-dialog')).to_be_visible()
        expect(self.page.locator('#cloud-browser-login')).to_be_disabled()

    def test_token_is_cleared_and_clone_uses_reviewed_destination(self):
        requests = []
        self.page.route('**/api/cloud/connect', lambda route: (requests.append(route.request.post_data_json), route.fulfill(json={'message': '연결했습니다.'})))
        self.page.route('**/api/cloud/clone', lambda route: (requests.append(route.request.post_data_json), route.fulfill(json={'message': '가져왔습니다.', 'path': '/fixture/new-blog'})))
        self.open()
        self.page.get_by_text('개인 액세스 토큰으로 연결', exact=True).click()
        self.page.locator('#cloud-token').fill('fixture_fake_secret_1234567890')
        self.page.locator('#cloud-connect').click()
        expect(self.page.locator('#cloud-token')).to_have_value('')
        expect(self.page.locator('#cloud-message')).to_have_text('연결했습니다.')
        self.assertEqual(requests[0], {'token': 'fixture_fake_secret_1234567890'})
        self.assertNotIn('fixture_fake_secret', self.page.locator('body').inner_text())
        self.page.get_by_text('다른 저장소 가져오기', exact=True).click()
        self.page.locator('#cloud-clone-repo').fill('fixture/new-blog')
        self.page.locator('#cloud-clone-path').fill('/fixture/new-blog')
        self.page.locator('#cloud-clone').click()
        expect(self.page.locator('#cloud-clone-result')).to_contain_text('/fixture/new-blog')
        self.assertEqual(requests[1], {'repository': 'fixture/new-blog', 'destination': '/fixture/new-blog'})

    def test_workflow_success_waits_for_actual_publication_and_blocks_script_link(self):
        result = {'state': 'awaiting_deployment', 'message': '게시 확인 중', 'runs': [
            {'id': 1, 'name': '<img onerror=alert(1)>', 'url': 'javascript:alert(1)', 'status': 'completed', 'conclusion': 'success'}]}
        self.page.route('**/api/cloud/deployment', lambda route: route.fulfill(json=result))
        self.open()
        self.page.evaluate("window.StudioCloud.track('aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'https://example.invalid/post/')")
        expect(self.page.locator('#cloud-deployment-status')).to_have_attribute('data-state', 'awaiting_deployment')
        expect(self.page.locator('#cloud-published-link a')).to_have_count(0)
        expect(self.page.locator('#cloud-deployment-runs img')).to_have_count(0)
        expect(self.page.locator('#cloud-deployment-runs a')).to_have_count(0)
        result.update(state='published', message='게시 완료', runs=[])
        self.page.locator('#cloud-refresh-deployment').click()
        expect(self.page.locator('#cloud-published-link a')).to_have_attribute('href', 'https://example.invalid/post/')

    def test_update_saves_draft_before_scheduling_restart(self):
        requests = []
        self.page.route('**/api/update/check', lambda route: route.fulfill(json={
            'current': '0.2.0', 'latest': '0.3.0', 'available': True, 'can_apply': True,
            'message': '업데이트 가능', 'release_url': 'https://github.com/lococaeco/blog-studio/releases/tag/v0.3.0'}))
        self.page.route('**/api/update/download', lambda route: route.fulfill(json={'ready': True, 'can_apply': True, 'message': '검증 완료'}))
        self.page.route('**/api/update/apply', lambda route: (requests.append('apply'), route.fulfill(json={'scheduled': True, 'message': '종료 후 재시작'})))
        self.new_post('업데이트 전 초안')
        self.open()
        self.page.locator('#cloud-check-update').click()
        expect(self.page.locator('#cloud-download-update')).to_be_enabled()
        self.page.locator('#cloud-download-update').click()
        expect(self.page.locator('#cloud-apply-update')).to_be_enabled()
        self.page.locator('#cloud-close').click()
        self.page.route('**/api/drafts', lambda route: (requests.append('save'), route.continue_()))
        self.page.locator('#title').fill('종료 전에 저장한 제목')
        tool(self.page, '#cloud-open').click()
        self.page.once('dialog', lambda dialog: dialog.accept())
        self.page.locator('#cloud-apply-update').click()
        expect(self.page.locator('#cloud-update-status')).to_have_text('종료 후 재시작')
        expect(self.page.locator('#cloud-apply-update')).to_be_disabled()
        self.assertIn('save', requests)
        self.assertLess(requests.index('save'), requests.index('apply'))
        self.assertEqual(self.active()['title'], '종료 전에 저장한 제목')


if __name__ == '__main__':
    unittest.main()
