"""Sliding navigation preserves draft state and keyboard access."""
import unittest

from playwright.sync_api import expect
import browser_smoke as base


class SidebarTests(unittest.TestCase):
    setUpClass = classmethod(base.BrowserTests.setUpClass.__func__)
    tearDownClass = classmethod(base.BrowserTests.tearDownClass.__func__)
    setUp = base.BrowserTests.setUp
    tearDown = base.BrowserTests.tearDown
    active = base.BrowserTests.active
    saved = base.BrowserTests.saved
    new_post = base.BrowserTests.new_post

    def test_desktop_arrow_collapses_restores_and_remembers_without_changing_draft(self):
        draft = self.new_post('화살표로 넓히는 작업 공간')
        sidebar = self.page.locator('#sidebar')
        editor = self.page.locator('.toastui-editor-ww-container [contenteditable=true]')
        editor.fill('접은 뒤에도 그대로 남는 작성 내용입니다.')
        self.page.locator('#sidebar-collapse').click()
        expect(sidebar).to_have_attribute('aria-hidden', 'true')
        self.assertTrue(sidebar.evaluate('(node) => node.inert'))
        expect(self.page.locator('#menu-toggle')).to_be_focused()
        expect(self.page.locator('#focus-button')).to_have_attribute('aria-pressed', 'false')
        self.page.wait_for_function('() => document.querySelector("#sidebar").getBoundingClientRect().right <= 1')
        saved = self.saved()
        self.assertEqual(saved['id'], draft['id'])
        self.assertIn('그대로 남는 작성 내용', saved['body'])
        self.page.reload()
        expect(sidebar).to_have_attribute('aria-hidden', 'true')
        expect(editor).to_contain_text('그대로 남는 작성 내용')
        self.page.locator('#menu-toggle').click()
        expect(sidebar).to_have_attribute('aria-hidden', 'false')
        expect(self.page.locator('#new-post')).to_be_focused()
        self.page.wait_for_function('() => Math.abs(document.querySelector("#sidebar").getBoundingClientRect().left) < 1')
        self.page.reload()
        expect(sidebar).to_have_attribute('aria-hidden', 'false')
        self.assertEqual(self.active()['id'], draft['id'])

    def test_mobile_close_arrow_and_breakpoints_keep_navigation_accessible(self):
        self.page.locator('#sidebar-collapse').click()
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.page.locator('#menu-toggle').click()
        expect(self.page.locator('#sidebar')).to_have_attribute('aria-modal', 'true')
        self.page.locator('#sidebar-collapse').click()
        expect(self.page.locator('#sidebar')).to_have_attribute('aria-hidden', 'true')
        expect(self.page.locator('#menu-toggle')).to_be_focused()
        self.assertFalse(self.page.locator('.workspace').evaluate('(node) => node.inert'))
        self.page.set_viewport_size({'width': 1440, 'height': 1000})
        expect(self.page.locator('#sidebar')).to_have_attribute('aria-hidden', 'true')
        self.page.locator('#menu-toggle').click()
        expect(self.page.locator('#sidebar')).to_have_attribute('aria-hidden', 'false')
        self.assertFalse(self.page.locator('#sidebar').evaluate('(node) => node.inert'))

    def test_visual_mode_has_no_persistent_conversion_warning(self):
        self.page.locator('.post-item').filter(has_text='기존 Markdown 예제').click()
        expect(self.page.locator('#mode-markdown')).to_have_attribute('aria-pressed', 'true')
        self.page.locator('#mode-visual').click()
        confirm = self.page.locator('#confirm-dialog')
        expect(confirm).to_be_visible()
        self.page.locator('#confirm-accept').click()
        expect(self.page.locator('#mode-visual')).to_have_attribute('aria-pressed', 'true')
        expect(self.page.locator('#mode-notice')).not_to_be_visible()
        self.assertNotIn('시각 편집을 사용하면 Markdown 서식이 정리될 수 있어요.', self.page.locator('body').inner_text())


if __name__ == '__main__':
    unittest.main()
