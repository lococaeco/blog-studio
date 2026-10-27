"""Sync UI locking and draft-version regression using mocked cloud jobs."""
from __future__ import annotations

import unittest

from playwright.sync_api import expect
import browser_smoke as smoke


class SyncBrowserTests(unittest.TestCase):
    setUpClass = classmethod(smoke.BrowserTests.setUpClass.__func__)
    tearDownClass = classmethod(smoke.BrowserTests.tearDownClass.__func__)
    setUp = smoke.BrowserTests.setUp
    tearDown = smoke.BrowserTests.tearDown
    active = smoke.BrowserTests.active
    saved = smoke.BrowserTests.saved
    new_post = smoke.BrowserTests.new_post

    def prepare_sync(self, *, failure=False):
        draft = self.new_post('동기화 UI 검증')
        events, writes, saved_before_sync = [], [], []
        state = {'finish': False, 'merged': None}

        def draft_request(route):
            if route.request.method == 'POST':
                events.append('save')
                writes.append(route.request.post_data_json)
            route.continue_()

        def start(route):
            events.append('sync')
            saved_before_sync.append(self.http.studio.get_draft(draft['id']))
            route.fulfill(json={'job': 'mock-sync-job'})

        def poll(route):
            if not state['finish']:
                route.fulfill(json={'state': 'running'})
            elif failure:
                route.fulfill(json={'state': 'error', 'error': '동기화 검증용 연결 실패'})
            else:
                if state['merged'] is None:
                    current = self.http.studio.get_draft(draft['id'])
                    state['merged'] = self.http.studio.save_draft(dict(
                        current, title='원격에서 정리한 제목', body='원격에서 합쳐진 본문입니다.'))
                route.fulfill(json={'state': 'done', 'result': {'message': '검증용 동기화 완료'}})

        self.page.route('**/api/drafts', draft_request)
        self.page.route('**/api/sync', start)
        self.page.route('**/api/jobs?id=mock-sync-job', poll)
        self.page.locator('#title').fill('동기화 직전에 수정한 제목')
        self.page.locator('#backup-sync').click()
        dialog = self.page.get_by_role('dialog', name='백업과 초안 동기화', exact=True)
        expect(dialog).to_be_visible()
        self.page.locator('#draft-sync').click()
        expect(self.page.locator('#document')).to_have_attribute('aria-busy', 'true')
        self.assertTrue(self.page.locator('#document').evaluate('(node) => node.inert'))
        expect(dialog.get_by_role('button', name='닫기', exact=True)).to_be_disabled()
        expect(self.page.locator('#draft-sync')).to_be_disabled()
        expect(self.page.locator('#backup-import')).to_be_disabled()
        self.page.keyboard.press('Escape')
        expect(dialog).to_be_visible()
        self.assertLess(events.index('save'), events.index('sync'))
        self.assertEqual(saved_before_sync[0]['title'], '동기화 직전에 수정한 제목')
        return draft, state, writes, dialog

    def test_sync_blocks_edits_then_refreshes_current_draft_and_version(self):
        draft, state, writes, dialog = self.prepare_sync()
        state['finish'] = True
        expect(dialog.locator('.library-status')).to_have_text('검증용 동기화 완료')
        expect(self.page.locator('#document')).to_have_attribute('aria-busy', 'false')
        self.assertFalse(self.page.locator('#document').evaluate('(node) => node.inert'))
        expect(dialog.get_by_role('button', name='닫기', exact=True)).to_be_enabled()
        expect(self.page.locator('#draft-sync')).to_be_enabled()
        dialog.get_by_role('button', name='닫기', exact=True).click()
        expect(dialog).to_have_count(0)
        expect(self.page.locator('#title')).to_have_value('원격에서 정리한 제목')
        expect(self.page.locator('.toastui-editor-ww-container [contenteditable=true]')).to_contain_text('원격에서 합쳐진 본문입니다.')
        merged_version = state['merged']['version']
        self.page.locator('#title').fill('동기화 후에도 편집되는 제목')
        updated = self.saved()
        self.assertEqual(updated['id'], draft['id'])
        self.assertEqual(updated['title'], '동기화 후에도 편집되는 제목')
        self.assertEqual(updated['body'], '원격에서 합쳐진 본문입니다.')
        self.assertEqual(writes[-1]['version'], merged_version)
        self.assertEqual(updated['version'], merged_version + 1)

    def test_failed_sync_unlocks_ui_and_keeps_saved_contents_editable(self):
        draft, state, writes, dialog = self.prepare_sync(failure=True)
        before = self.http.studio.get_draft(draft['id'])
        state['finish'] = True
        expect(dialog.locator('.library-status')).to_have_text('동기화 검증용 연결 실패')
        expect(self.page.locator('#document')).to_have_attribute('aria-busy', 'false')
        self.assertFalse(self.page.locator('#document').evaluate('(node) => node.inert'))
        expect(dialog.get_by_role('button', name='닫기', exact=True)).to_be_enabled()
        expect(self.page.locator('#draft-sync')).to_be_enabled()
        self.page.keyboard.press('Escape')
        expect(dialog).to_have_count(0)
        expect(self.page.locator('#title')).to_have_value(before['title'])
        self.assertEqual(self.http.studio.get_draft(draft['id']), before)
        self.page.locator('#title').fill('연결 실패 후 계속 쓴 제목')
        updated = self.saved()
        self.assertEqual(updated['body'], before['body'])
        self.assertEqual(updated['title'], '연결 실패 후 계속 쓴 제목')
        self.assertEqual(writes[-1]['version'], before['version'])
        self.assertEqual(updated['version'], before['version'] + 1)


if __name__ == '__main__':
    unittest.main()
