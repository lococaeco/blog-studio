"""Selection toolbar tests exercise real browser selections and persisted drafts."""
import unittest

from playwright.sync_api import expect
import browser_smoke as base
import browser_writing as writing


class TextSelectionTests(unittest.TestCase):
    setUpClass = classmethod(base.BrowserTests.setUpClass.__func__)
    tearDownClass = classmethod(base.BrowserTests.tearDownClass.__func__)
    setUp = base.BrowserTests.setUp
    tearDown = base.BrowserTests.tearDown
    active = base.BrowserTests.active
    saved = base.BrowserTests.saved
    new_post = base.BrowserTests.new_post
    content = writing.WritingTests.content
    select_text = writing.WritingTests.select_text

    def palette(self, kind, color):
        self.page.locator('#text-' + kind).click()
        self.page.locator('#text-color-palette [data-color="' + color + '"]').click()

    def test_selected_text_styles_preserve_outside_and_survive_modes_reload_undo(self):
        self.content('앞 문장. 꾸밀 글자와 **기존 강조**를 보존합니다.\n\n다른 문단.')
        self.select_text('꾸밀 글자')
        expect(self.page.locator('#text-selection-tools')).to_be_visible()
        self.page.locator('#text-bold').click()
        self.page.locator('#text-underline').click()
        self.palette('color', '#c62828')
        self.palette('highlight', '#fff0a3')
        paragraph = self.page.locator('.toastui-editor-ww-container p').filter(has_text='앞 문장.')
        target = paragraph.locator('span').filter(has_text='꾸밀 글자')
        expect(target).to_have_css('color', 'rgb(198, 40, 40)')
        expect(target).to_have_css('background-color', 'rgb(255, 240, 163)')
        expect(paragraph.locator('strong u')).to_have_text('꾸밀 글자')
        self.assertEqual(self.page.evaluate('getSelection().toString()'), '꾸밀 글자')
        self.page.keyboard.press('Control+z')
        expect(target).not_to_have_css('background-color', 'rgb(255, 240, 163)')
        expect(target).to_have_css('color', 'rgb(198, 40, 40)')
        self.page.keyboard.press('Control+Shift+Z')
        expect(target).to_have_css('background-color', 'rgb(255, 240, 163)')
        before = self.saved()['body']
        self.assertIn('앞 문장. ', before)
        self.assertIn('**기존 강조**', before)
        self.assertIn('다른 문단.', before)
        self.page.locator('#mode-markdown').click()
        expect(self.page.locator('#text-selection-tools')).not_to_be_visible()
        self.page.locator('#mode-visual').click()
        self.page.reload()
        expect(target).to_have_css('color', 'rgb(198, 40, 40)')
        expect(target).to_have_css('background-color', 'rgb(255, 240, 163)')
        expect(paragraph.locator('strong u')).to_have_text('꾸밀 글자')
        self.assertEqual(self.active()['body'], before)

    def test_link_clear_and_unlink_are_separate_and_keep_selected_text(self):
        self.content('새 링크를 만들고 바깥 문장을 지킵니다.')
        self.select_text('새 링크')
        self.page.locator('#text-link').click()
        expect(self.page.locator('#toolbar-link-dialog')).to_be_visible()
        self.page.locator('#toolbar-link-url').fill('https://example.invalid/reference')
        self.page.locator('#toolbar-link-insert').click()
        link = self.page.locator('.toastui-editor-ww-container a[href="https://example.invalid/reference"]')
        expect(link).to_have_text('새 링크')
        self.select_text('새 링크')
        self.page.locator('#text-bold').click()
        self.palette('color', '#2463be')
        self.page.locator('#text-clear').click()
        expect(link).to_have_text('새 링크')
        expect(self.page.locator('.toastui-editor-ww-container strong')).to_have_count(0)
        expect(self.page.locator('.toastui-editor-ww-container span[style]')).to_have_count(0)
        self.page.locator('#text-unlink').click()
        expect(link).to_have_count(0)
        self.assertIn('새 링크를 만들고 바깥 문장을 지킵니다.', self.saved()['body'])
        self.page.keyboard.press('Control+z')
        expect(link).to_have_text('새 링크')

    def test_custom_palette_reset_preserves_other_marks_and_blocks_invalid_color(self):
        self.content('선택 범위는 이곳입니다.')
        self.select_text('이곳')
        self.palette('highlight', '#dbeaff')
        self.page.locator('#text-color').click()
        self.page.locator('#text-color-value').fill('#123abc')
        self.page.locator('#text-color-apply').click()
        target = self.page.locator('.toastui-editor-ww-container span').filter(has_text='이곳')
        expect(target).to_have_css('color', 'rgb(18, 58, 188)')
        self.page.locator('#text-color-value').fill('invalid')
        self.page.locator('#text-color-apply').click()
        expect(target).to_have_css('color', 'rgb(18, 58, 188)')
        self.page.locator('#text-color-reset').click()
        expect(target).not_to_have_css('color', 'rgb(18, 58, 188)')
        expect(target).to_have_css('background-color', 'rgb(219, 234, 255)')
        self.assertNotIn('invalid', self.saved()['body'])

    def test_keyboard_selection_focus_escape_mobile_and_code_exclusion(self):
        self.content('키보드로 선택합니다.\n\n```python\nprint("code")\n```')
        self.page.set_viewport_size({'width': 390, 'height': 844})
        paragraph = self.page.locator('.toastui-editor-ww-container p').filter(has_text='키보드로 선택합니다.')
        paragraph.click()
        self.page.keyboard.press('Home')
        for _ in range(4):
            self.page.keyboard.press('Shift+ArrowRight')
        tools = self.page.locator('#text-selection-tools')
        expect(tools).to_be_visible()
        self.page.keyboard.press('Alt+F10')
        expect(self.page.locator('#text-bold')).to_be_focused()
        self.page.keyboard.press('Enter')
        expect(paragraph.locator('strong')).to_have_text('키보드로')
        self.page.locator('#text-highlight').click()
        box = tools.bounding_box()
        self.assertGreaterEqual(box['x'], 0)
        self.assertLessEqual(box['x'] + box['width'], 390)
        self.assertLessEqual(box['y'] + box['height'], 844)
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 390)
        self.page.keyboard.press('Escape')
        expect(self.page.locator('#text-color-palette')).not_to_be_visible()
        self.page.keyboard.press('Escape')
        expect(tools).not_to_be_visible()
        self.select_text('print')
        expect(tools).not_to_be_visible()
        self.select_text('선택합니다.')
        expect(tools).to_be_visible()
        self.page.locator('#title').click()
        expect(tools).not_to_be_visible()

    def test_styled_emphasis_inside_list_keeps_native_list_and_entire_mark_run(self):
        self.content('- **굵은 앞 꾸밀 중간 굵은 뒤**를 보존합니다.\n- 둘째 항목의 `코드`와 [주소](https://example.invalid).')
        self.select_text('꾸밀 중간')
        self.palette('color', '#8150b0')
        self.page.locator('#text-italic').click()
        self.page.locator('#text-strike').click()
        self.saved()
        self.page.locator('#mode-markdown').click()
        self.page.locator('#mode-visual').click()
        items = self.page.locator('.toastui-editor-ww-container li')
        expect(items).to_have_count(2)
        expect(items.first.locator('strong')).to_have_text('굵은 앞 꾸밀 중간 굵은 뒤')
        expect(items.first.locator('em')).to_have_text('꾸밀 중간')
        expect(items.first.locator('del')).to_have_text('꾸밀 중간')
        expect(items.first.locator('span')).to_have_css('color', 'rgb(129, 80, 176)')
        expect(items.last.locator('code')).to_have_text('코드')
        expect(items.last.locator('a')).to_have_attribute('href', 'https://example.invalid')
        body = self.saved()['body']
        self.assertNotIn('\\*\\*', body)
        self.page.reload()
        expect(items.first.locator('strong')).to_have_text('굵은 앞 꾸밀 중간 굵은 뒤')

    def test_table_cell_text_formatting_and_table_controls_handoff(self):
        self.content('| 제목 | 주소 |\n| --- | --- |\n| 표의 글자 | [원문 링크](https://example.invalid) |\n| 둘째 행 | 유지 |')
        self.select_text('원문 링크')
        self.page.locator('#text-bold').click()
        self.palette('color', '#2463be')
        cell = self.page.locator('.toastui-editor-ww-container td').filter(has_text='원문 링크')
        expect(cell.locator('strong')).to_have_text('원문 링크')
        expect(cell.locator('span')).to_have_css('color', 'rgb(36, 99, 190)')

        self.saved()
        self.page.locator('#mode-markdown').click()
        self.page.locator('#mode-visual').click()
        expect(cell.locator('strong')).to_have_text('원문 링크')
        expect(cell.locator('a')).to_have_attribute('href', 'https://example.invalid')
        expect(cell.locator('span')).to_have_css('color', 'rgb(36, 99, 190)')
        cell.click(position={'x': 10, 'y': 10})
        expect(self.page.locator('#text-selection-tools')).not_to_be_visible()
        expect(self.page.locator('#table-tools')).to_be_visible()
        self.page.locator('#align-center').click()
        expect(cell).to_have_css('text-align', 'center')
        self.saved()
        self.page.locator('#mode-markdown').click()
        self.page.locator('#mode-visual').click()
        self.page.reload()
        expect(cell).to_have_css('text-align', 'center')
        expect(cell.locator('strong')).to_have_text('원문 링크')
        expect(cell.locator('a')).to_have_attribute('href', 'https://example.invalid')
        expect(cell.locator('span')).to_have_css('color', 'rgb(36, 99, 190)')

    def test_inline_code_with_color_keeps_literal_punctuation_and_safe_saved_html(self):
        self.content('코드 `<img src=x> & **내용**`를 보존해요.\n\n앞뒤 `plain 중간 plain`도 남겨요.\n\n끝만 `plain <tag> 끝부분`도 남겨요.\n\n처음만 `첫부분 <tag> plain`도 남겨요.')
        self.select_text('<img src=x> & **내용**')
        self.palette('color', '#c62828')
        self.select_text('중간')
        self.palette('highlight', '#fff0a3')
        self.select_text('끝부분')
        self.palette('color', '#2463be')
        self.select_text('첫부분')
        self.palette('color', '#187b71')
        body = self.saved()['body']
        self.assertNotIn('<img src=x>', body)
        self.assertIn('&#60;img', body)
        for _ in range(2):
            self.page.locator('#mode-markdown').click()
            self.page.locator('#mode-visual').click()
            expect(self.page.locator('.toastui-editor-ww-container code').filter(has_text='<img src=x> & **내용**')).to_have_text('<img src=x> & **내용**')
            expect(self.page.locator('.toastui-editor-ww-container span').filter(has_text='<img src=x>')).to_have_css('color', 'rgb(198, 40, 40)')
            expect(self.page.locator('.toastui-editor-ww-container img')).to_have_count(0)
            expect(self.page.locator('.toastui-editor-ww-container span').filter(has_text='중간')).to_have_css('background-color', 'rgb(255, 240, 163)')
            expect(self.page.locator('.toastui-editor-ww-container p').filter(has_text='끝만')).to_have_text('끝만 plain <tag> 끝부분도 남겨요.')
            expect(self.page.locator('.toastui-editor-ww-container p').filter(has_text='처음만')).to_have_text('처음만 첫부분 <tag> plain도 남겨요.')
            expect(self.page.locator('.toastui-editor-ww-container span').filter(has_text='끝부분')).to_have_css('color', 'rgb(36, 99, 190)')
            expect(self.page.locator('.toastui-editor-ww-container span').filter(has_text='첫부분')).to_have_css('color', 'rgb(24, 123, 113)')
            expect(self.page.locator('.toastui-editor-ww-container tag')).to_have_count(0)
        self.assertEqual(self.saved()['body'], body)



if __name__ == '__main__':
    unittest.main()
