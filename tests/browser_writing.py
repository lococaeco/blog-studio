"""Real Chromium checks for writing tools, using only the disposable blog fixture."""
from pathlib import Path
import unittest

from playwright.sync_api import expect
import browser_smoke as base


class WritingTests(unittest.TestCase):
    setUpClass = classmethod(base.BrowserTests.setUpClass.__func__)
    tearDownClass = classmethod(base.BrowserTests.tearDownClass.__func__)
    setUp = base.BrowserTests.setUp
    tearDown = base.BrowserTests.tearDown
    active = base.BrowserTests.active
    saved = base.BrowserTests.saved
    new_post = base.BrowserTests.new_post
    select_paragraphs = base.BrowserTests.select_paragraphs

    def content(self, body):
        self.new_post('글쓰기 도구 검사')
        self.page.locator('#mode-markdown').click()
        editable = self.page.locator('.toastui-editor-md-container [contenteditable=true]')
        editable.fill(body)
        self.saved()
        self.page.locator('#mode-visual').click()
        return self.saved()

    def select_text(self, text):
        self.page.locator('.toastui-editor-ww-container [contenteditable=true]').evaluate('''(editor, text) => {
            const walker = document.createTreeWalker(editor, NodeFilter.SHOW_TEXT);
            let node;
            while (node = walker.nextNode()) {
                const offset = node.textContent.indexOf(text);
                if (offset < 0) continue;
                const range = document.createRange(); range.setStart(node, offset); range.setEnd(node, offset + text.length);
                editor.focus(); const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
                document.dispatchEvent(new Event('selectionchange')); return;
            }
            throw new Error('Missing fixture text: ' + text);
        }''', text)

    def color(self, selector, value):
        # The operating system's color picker is outside Chromium automation;
        # dispatch its standard change event with the selected color value.
        self.page.locator(selector).evaluate('''(input, value) => { input.value = value; input.dispatchEvent(new Event('change', {bubbles: true})); }''', value)

    def test_rich_color_highlight_underline_preserve_marks_alignment_and_reopen(self):
        self.content('## 문단 제목\n\n일반 **강조**와 [링크](https://example.invalid)를 유지합니다.\n\n다른 문단입니다.')
        self.select_text('강조')
        expect(self.page.locator('#writing-underline')).to_be_enabled()
        self.color('#writing-color', '#c62828')
        self.color('#writing-highlight', '#fff0a3')
        self.page.locator('#writing-underline').click()
        self.page.locator('#align-center').click()
        paragraph = self.page.locator('.toastui-editor-ww-container p').filter(has_text='일반 강조와 링크를 유지합니다.').first
        expect(paragraph).to_have_css('text-align', 'center')
        expect(paragraph.locator('strong u')).to_have_text('강조')
        expect(paragraph.locator('strong span')).to_have_css('color', 'rgb(198, 40, 40)')
        expect(paragraph.locator('strong span')).to_have_css('background-color', 'rgb(255, 240, 163)')
        before = self.saved()['body']
        self.assertIn('다른 문단입니다.', before)
        self.assertIn('https://example.invalid', before)
        self.page.locator('#mode-markdown').click()
        expect(self.page.locator('#writing-underline')).to_be_disabled()
        self.page.locator('#mode-visual').click()
        self.page.reload()
        expect(self.page.locator('.toastui-editor-ww-container strong u')).to_have_text('강조')
        expect(self.page.locator('.toastui-editor-ww-container strong span')).to_have_css('background-color', 'rgb(255, 240, 163)')
        self.assertEqual(self.active()['body'], before)
        with self.page.expect_download() as download:
            self.page.locator('#export-button').click()
        exported = Path(download.value.path()).read_text('utf-8')
        self.assertIn('<u>강조</u>', exported)
        self.assertIn('text-align: center', exported)
        self.select_text('강조')
        self.page.locator('#writing-clear-style').click()
        expect(self.page.locator('.toastui-editor-ww-container strong u')).to_have_count(0)
        expect(self.page.locator('.toastui-editor-ww-container strong')).to_have_text('강조')

    def test_find_replace_all_is_literal_case_aware_and_undoable(self):
        self.content('**cat** Cat cat.\n\n찾을 [점.](https://example.invalid)과 a.b를 보존합니다.')
        self.page.locator('#writing-find').click()
        self.page.locator('#find-query').fill('cat')
        expect(self.page.locator('#find-result')).to_have_text('3개 찾음')
        self.page.locator('#find-case').check()
        expect(self.page.locator('#find-result')).to_have_text('2개 찾음')
        self.page.locator('#find-replacement').fill('dog')
        self.page.locator('#replace-all').click()
        changed = self.saved()['body']
        self.assertIn('**dog** Cat dog.', changed)
        self.assertIn('https://example.invalid', changed)
        self.page.keyboard.press('Control+z')
        original = self.saved()['body']
        self.assertIn('**cat** Cat cat.', original)
        self.page.locator('#find-query').fill('a.b')
        expect(self.page.locator('#find-result')).to_have_text('1개 찾음')
        self.page.locator('#find-replacement').fill('literal')
        self.page.locator('#replace-one').click()
        self.assertIn('literal', self.saved()['body'])
        self.page.locator('#mode-markdown').click()
        self.page.locator('#find-query').fill('literal')
        self.page.locator('#find-replacement').fill('원문 치환')
        self.page.locator('#replace-all').click()
        self.assertIn('원문 치환', self.saved()['body'])
        self.page.keyboard.press('Control+z')
        self.assertIn('literal', self.saved()['body'])

    def test_outline_navigation_block_movement_and_drag_preserve_paragraphs(self):
        self.content('## 첫 제목\n\n첫 문단 **굵게**.\n\n둘째 문단 [링크](https://example.invalid).\n\n### 하위 제목\n\n따라가는 하위 내용.\n\n## 끝 제목\n\n마지막 문단.')
        self.page.locator('#writing-outline').click()
        expect(self.page.locator('#writing-outline-list li')).to_have_count(3)
        self.page.locator('#writing-outline-list button').filter(has_text='끝 제목').click()
        self.assertIn('끝 제목', self.page.evaluate('getSelection().anchorNode.parentElement.textContent'))
        self.page.locator('#writing-outline-list li').filter(has_text='끝 제목').get_by_role('button', name='구역 위로 이동', exact=True).click()
        section_body = self.saved()['body']
        self.assertLess(section_body.index('끝 제목'), section_body.index('마지막 문단'))
        self.assertLess(section_body.index('마지막 문단'), section_body.index('첫 제목'))
        self.assertLess(section_body.index('첫 제목'), section_body.index('하위 제목'))
        self.assertIn('따라가는 하위 내용.', section_body)
        self.page.keyboard.press('Control+z')
        self.saved()
        self.page.locator('#outline-all-blocks').check()
        row = self.page.locator('#writing-outline-list li').filter(has_text='둘째 문단 링크.')
        row.get_by_role('button', name='문단 위로 이동', exact=True).click()
        body = self.saved()['body']
        self.assertLess(body.index('둘째 문단'), body.index('첫 문단'))
        self.assertIn('\n\n첫 문단', body)
        self.assertIn('**굵게**', body)
        self.assertIn('https://example.invalid', body)
        self.page.keyboard.press('Control+z')
        body = self.saved()['body']
        self.assertLess(body.index('첫 문단'), body.index('둘째 문단'))
        source = self.page.locator('#writing-outline-list li').filter(has_text='마지막 문단.')
        target = self.page.locator('#writing-outline-list li').filter(has_text='첫 문단 굵게.')
        source.drag_to(target)
        body = self.saved()['body']
        self.assertLess(body.index('마지막 문단'), body.index('첫 문단'))
        self.page.locator('#mode-markdown').click()
        expect(self.page.locator('#outline-all-blocks')).to_be_disabled()
        expect(self.page.locator('#writing-outline-list li')).to_have_count(3)

    def test_writing_tools_fit_mobile_and_code_blocks_disable_formatting(self):
        self.content('일반 문장입니다.\n\n```python\nprint("코드")\n```')
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.page.wait_for_timeout(250)
        expect(self.page.locator('#writing-find')).to_be_visible()
        expect(self.page.locator('#writing-outline')).to_be_visible()
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 390)
        self.page.locator('.toastui-editor-ww-container pre').click()
        expect(self.page.locator('#writing-underline')).to_be_disabled()
        self.page.locator('#writing-find').click()
        self.page.locator('#find-query').fill('일반')
        expect(self.page.locator('#find-result')).to_have_text('1개 찾음')
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 390)
        self.page.keyboard.press('Escape')
        expect(self.page.locator('#writing-find-panel')).not_to_be_visible()

    def test_reusable_markdown_snippet_keeps_formatting_selection_and_undo(self):
        self.content('앞 **기존 강조** 삽입위치 뒤.')
        self.select_text('삽입위치')
        self.page.locator('#personal-templates').click()
        self.page.get_by_role('button', name='빈 항목 추가', exact=True).click()
        form = self.page.get_by_role('dialog', name='새 항목 저장', exact=True)
        form.get_by_label('이름', exact=True).fill('서식 있는 문구')
        form.get_by_role('combobox').select_option('snippet')
        form.get_by_label('내용 (Markdown)', exact=True).fill('**새 강조**와 [삽입 링크](https://example.invalid/snippet)')
        form.get_by_role('button', name='저장', exact=True).click()
        self.page.get_by_role('dialog', name='내 템플릿과 재사용 문구', exact=True).locator('.library-row').filter(has_text='서식 있는 문구').get_by_role('button', name='본문에 넣기', exact=True).click()
        changed = self.saved()['body']
        self.assertIn('앞 **기존 강조** **새 강조**와 [삽입 링크](https://example.invalid/snippet) 뒤.', changed)
        expect(self.page.locator('.toastui-editor-ww-container a').filter(has_text='삽입 링크')).to_have_attribute('href', 'https://example.invalid/snippet')
        self.page.locator('.toastui-editor-ww-container [contenteditable=true]').focus()
        self.page.keyboard.press('Control+z')
        self.assertEqual(self.saved()['body'], '앞 **기존 강조** 삽입위치 뒤.')
        # Recreating the parser must not recursively wrap the global Markdown
        # converters in TOAST UI's pinned bundle or disturb existing alignment.
        self.select_text('삽입위치')
        self.page.locator('#align-center').click()
        self.page.locator('#personal-templates').click()
        self.page.get_by_role('dialog', name='내 템플릿과 재사용 문구', exact=True).locator('.library-row').filter(has_text='서식 있는 문구').get_by_role('button', name='본문에 넣기', exact=True).click()
        expect(self.page.locator('.toastui-editor-ww-container p').filter(has_text='앞 기존 강조 새 강조와 삽입 링크 뒤.')).to_have_css('text-align', 'center')
        self.assertIn('https://example.invalid/snippet', self.saved()['body'])
        self.page.reload()
        expect(self.page.locator('.toastui-editor-ww-container strong')).to_have_count(2)

    def test_multiblock_snippet_preserves_paragraph_boundaries_and_alignment(self):
        self.content('앞 문장 삽입위치 뒤 문장')
        self.select_text('삽입위치')
        self.page.locator('#personal-templates').click()
        self.page.get_by_role('button', name='빈 항목 추가', exact=True).click()
        form = self.page.get_by_role('dialog', name='새 항목 저장', exact=True)
        form.get_by_label('이름', exact=True).fill('문단 구분 문구')
        form.get_by_role('combobox').select_option('snippet')
        form.get_by_label('내용 (Markdown)', exact=True).fill('첫 삽입 문단\n\n<p style="text-align: right;"><strong>둘째 삽입 문단</strong></p>')
        form.get_by_role('button', name='저장', exact=True).click()
        library = self.page.get_by_role('dialog', name='내 템플릿과 재사용 문구', exact=True)
        library.locator('.library-row').filter(has_text='문단 구분 문구').get_by_role('button', name='본문에 넣기', exact=True).click()
        expect(library).not_to_be_visible()
        body = self.saved()['body']
        self.assertIn('앞 문장 \n\n첫 삽입 문단\n', body)
        self.assertRegex(body, r'</p>\n{2,} 뒤 문장')
        paragraph = self.page.locator('.toastui-editor-ww-container p').filter(has_text='둘째 삽입 문단')
        expect(paragraph).to_have_css('text-align', 'right')
        expect(paragraph.locator('strong')).to_have_text('둘째 삽입 문단')
        self.page.locator('#mode-markdown').click()
        self.page.locator('#mode-visual').click()
        expect(paragraph).to_have_css('text-align', 'right')


if __name__ == '__main__':
    unittest.main(verbosity=2)
