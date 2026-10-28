"""User-facing alignment scope and native paragraph continuation regressions."""
from pathlib import Path
import unittest

from playwright.sync_api import expect
import browser_smoke as base
import browser_writing as writing
from browser_toolbar_support import tool


class AlignmentContinuationTests(unittest.TestCase):
    setUpClass = classmethod(base.BrowserTests.setUpClass.__func__)
    tearDownClass = classmethod(base.BrowserTests.tearDownClass.__func__)
    setUp = base.BrowserTests.setUp
    tearDown = base.BrowserTests.tearDown
    active = base.BrowserTests.active
    saved = base.BrowserTests.saved
    new_post = base.BrowserTests.new_post
    content = writing.WritingTests.content
    select_paragraphs = base.BrowserTests.select_paragraphs
    paragraph = base.BrowserTests.paragraph
    select_text = writing.WritingTests.select_text

    def caret_end(self, locator):
        locator.evaluate('''node => {
            const editor = node.closest('[contenteditable=true]');
            editor.focus();
            const range = document.createRange(); range.selectNodeContents(node); range.collapse(false);
            const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
            document.dispatchEvent(new Event('selectionchange'));
        }''')

    def test_caret_aligns_entire_document_while_preserving_code_lists_and_tables(self):
        self.content('## 전체 제목\n\n첫 문단 **강조**.\n\n둘째 문단.\n\n- 목록 유지\n\n```python\nprint("untouched")\n```\n\n| 열 |\n| --- |\n| 표 유지 |')
        self.paragraph('첫 문단 강조.').click()
        self.page.locator('#align-center').click()
        expect(self.page.locator('.toastui-editor-ww-container h2')).to_have_css('text-align', 'center')
        for value in ('첫 문단 강조.', '둘째 문단.'):
            expect(self.paragraph(value)).to_have_css('text-align', 'center')
        expect(self.paragraph('첫 문단 강조.').locator('strong')).to_have_text('강조')
        body = self.saved()['body']
        self.assertIn('print("untouched")', body)
        self.assertIn('목록 유지', body)
        self.assertIn('표 유지', body)
        expect(self.page.locator('.toastui-editor-ww-container li')).to_contain_text('목록 유지')
        expect(self.page.locator('.toastui-editor-ww-container table')).to_contain_text('표 유지')
        self.page.locator('#title').click()
        self.page.locator('#align-right').click()
        expect(self.page.locator('.toastui-editor-ww-container h2')).to_have_css('text-align', 'right')
        for value in ('첫 문단 강조.', '둘째 문단.'):
            expect(self.paragraph(value)).to_have_css('text-align', 'right')
        self.page.keyboard.press('Control+z')
        for value in ('첫 문단 강조.', '둘째 문단.'):
            expect(self.paragraph(value)).to_have_css('text-align', 'center')

    def test_nonempty_drag_only_aligns_touched_paragraph_and_keeps_outside_text(self):
        self.content('첫 문단 바깥.\n\n선택할 가운데 **단어**와 링크.\n\n마지막 문단 바깥.')
        self.select_text('단어')
        self.page.locator('#align-center').click()
        expect(self.paragraph('선택할 가운데 단어와 링크.')).to_have_css('text-align', 'center')
        for value in ('첫 문단 바깥.', '마지막 문단 바깥.'):
            expect(self.paragraph(value)).not_to_have_css('text-align', 'center')
        self.assertEqual(self.page.evaluate('getSelection().toString()'), '단어')
        self.select_paragraphs('첫 문단 바깥.', '선택할 가운데 단어와 링크.')
        self.page.locator('#align-right').click()
        expect(self.paragraph('첫 문단 바깥.')).to_have_css('text-align', 'right')
        expect(self.paragraph('선택할 가운데 단어와 링크.')).to_have_css('text-align', 'right')
        expect(self.paragraph('마지막 문단 바깥.')).not_to_have_css('text-align', 'right')

    def test_native_select_all_preserves_mixed_block_structures(self):
        self.content('## 함께 선택할 제목\n\n첫 일반 문단.\n\n- 목록 첫 항목\n- 목록 둘째 항목\n\n```python\nprint("code stays")\n```\n\n| 열 |\n| --- |\n| 표 내용 |\n\n마지막 일반 문단.')
        protected = self.page.locator('.toastui-editor-ww-container ul, .toastui-editor-ww-container pre, .toastui-editor-ww-container table')
        original = protected.evaluate_all('(nodes) => nodes.map(node => node.outerHTML)')
        self.paragraph('첫 일반 문단.').click()
        self.page.keyboard.press('Control+a')
        self.assertIn('code stays', self.page.evaluate('getSelection().toString()'))
        self.assertIn('목록 둘째 항목', self.page.evaluate('getSelection().toString()'))
        expect(self.page.locator('#align-center')).to_be_enabled()
        self.page.locator('#align-center').click()
        expect(self.page.locator('.toastui-editor-ww-container h2')).to_have_css('text-align', 'center')
        for value in ('첫 일반 문단.', '마지막 일반 문단.'):
            expect(self.paragraph(value)).to_have_css('text-align', 'center')
        self.assertEqual(protected.evaluate_all('(nodes) => nodes.map(node => node.outerHTML)'), original)
        self.saved()
        self.page.locator('#mode-markdown').click()
        self.page.locator('#mode-visual').click()
        expect(self.page.locator('.toastui-editor-ww-container li')).to_have_count(2)
        expect(self.page.locator('.toastui-editor-ww-container pre')).to_contain_text('print("code stays")')
        expect(self.page.locator('.toastui-editor-ww-container table')).to_contain_text('표 내용')
        for value in ('첫 일반 문단.', '마지막 일반 문단.'):
            expect(self.paragraph(value)).to_have_css('text-align', 'center')

    def test_enter_continues_centered_paragraph_through_typing_reload_export_and_undo(self):
        self.content('첫 중앙 문단.')
        self.page.locator('#align-center').click()
        self.caret_end(self.paragraph('첫 중앙 문단.'))
        self.page.keyboard.press('Enter')
        self.page.keyboard.insert_text('둘째 중앙 문단 한글.')
        self.page.keyboard.press('Enter')
        self.page.keyboard.insert_text('셋째 중앙 문단.')
        for value in ('첫 중앙 문단.', '둘째 중앙 문단 한글.', '셋째 중앙 문단.'):
            expect(self.paragraph(value)).to_have_css('text-align', 'center')
        before = self.saved()['body']
        self.page.reload()
        for value in ('첫 중앙 문단.', '둘째 중앙 문단 한글.', '셋째 중앙 문단.'):
            expect(self.paragraph(value)).to_have_css('text-align', 'center')
        self.assertEqual(self.active()['body'], before)
        with self.page.expect_download() as download:
            tool(self.page, '#export-button').click()
        exported = Path(download.value.path()).read_text('utf-8')
        self.assertGreaterEqual(exported.count('text-align: center'), 3)
        self.caret_end(self.paragraph('셋째 중앙 문단.'))
        self.page.keyboard.press('Enter')
        self.page.keyboard.insert_text('되돌릴 새 문단')
        expect(self.paragraph('되돌릴 새 문단')).to_have_css('text-align', 'center')
        self.page.keyboard.press('Control+z')
        expect(self.page.locator('.toastui-editor-ww-container')).not_to_contain_text('되돌릴 새 문단')
        expect(self.paragraph('셋째 중앙 문단.')).to_have_css('text-align', 'center')
        self.page.keyboard.press('Control+Shift+Z')
        expect(self.paragraph('되돌릴 새 문단')).to_have_css('text-align', 'center')

    def test_heading_enter_keeps_alignment_without_duplicating_anchor_and_shift_enter_remains_native(self):
        self.content('## 정렬 제목')
        heading = self.page.locator('.toastui-editor-ww-container h2')
        self.page.locator('#align-right').click()
        original_id = heading.get_attribute('id')
        self.caret_end(heading)
        self.page.keyboard.press('Enter')
        self.page.keyboard.insert_text('오른쪽 본문.')
        paragraph = self.paragraph('오른쪽 본문.')
        expect(paragraph).to_have_css('text-align', 'right')
        self.assertFalse(paragraph.get_attribute('id'))
        self.assertEqual(heading.get_attribute('id'), original_id)
        self.page.keyboard.press('Shift+Enter')
        self.page.keyboard.insert_text('같은 문단의 다음 줄.')
        # The pinned editor's Shift+Enter creates another paragraph; the
        # adapter must retain that native behavior rather than consume the key.
        expect(self.paragraph('같은 문단의 다음 줄.')).to_have_css('text-align', 'right')
        expect(paragraph).to_have_text('오른쪽 본문.')
        self.saved()
        self.page.locator('#mode-markdown').click()
        self.page.locator('#mode-visual').click()
        expect(self.paragraph('오른쪽 본문.')).to_have_css('text-align', 'right')
        expect(heading).to_have_attribute('id', original_id)


    def test_repeated_whole_document_alignment_preserves_blank_separators(self):
        self.content('## 제목\n\n첫 문단.\n\n\n둘째 문단.\n\n> 인용 문단\n\n마지막 문단.')
        counts = []
        blank_blocks = []
        for index in range(4):
            self.page.locator('#title').click()
            self.page.locator('#align-center').click()
            body = self.saved()['body']
            blank_blocks.append(body.count('<p style="text-align: center;"></p>'))
            root = self.page.locator('.toastui-editor-ww-container [contenteditable=true]')
            counts.append(root.evaluate('(node) => [...node.children].filter(item => item.tagName === "P" && !item.textContent.trim()).length'))
            for value in ('첫 문단.', '둘째 문단.', '마지막 문단.'):
                expect(self.paragraph(value)).to_have_css('text-align', 'center')
            expect(self.page.locator('.toastui-editor-ww-container blockquote')).to_have_text('인용 문단')
            self.page.locator('#mode-markdown').click()
            self.page.locator('#mode-visual').click()
        self.assertTrue(counts[0], 'The intentionally blank separator must remain')
        self.assertEqual(counts, [counts[0]] * 4)
        self.assertEqual(blank_blocks, [blank_blocks[0]] * 4)

    def test_raw_html_heading_level_preserves_following_markdown_anchors(self):
        self.content('<h2><strong>Same</strong></h2>\n\n## Same\n\n## Same')
        self.select_text('Same')
        self.page.locator('#block-style-toggle').click()
        tool(self.page, '#block-heading-3').click()
        headings = self.page.locator('.toastui-editor-ww-container h2, .toastui-editor-ww-container h3')
        expect(headings).to_have_count(3)
        expect(self.page.locator('.toastui-editor-ww-container h3 strong')).to_have_text('Same')
        body = self.saved()['body']
        self.assertEqual(body, '<h3><strong>Same</strong></h3>\n\n## Same\n\n## Same',
                         'The existing HTML heading must not consume the implicit #same / #same-1 anchors')
        self.page.keyboard.press('Control+z')
        expect(self.page.locator('.toastui-editor-ww-container h2')).to_have_count(3)
        self.page.keyboard.press('Control+Shift+Z')
        expect(self.page.locator('.toastui-editor-ww-container h3')).to_have_count(1)
        self.page.locator('#mode-markdown').click()
        self.page.locator('#mode-visual').click()
        self.page.reload()
        expect(headings).to_have_count(3)
        expect(self.page.locator('.toastui-editor-ww-container h3 strong')).to_have_text('Same')
        self.assertEqual(self.active()['body'], body)

    def test_heading_demotion_retains_explicit_and_sanitized_anchors(self):
        for anchor, centered in (('stable-anchor', False), ('title', True)):
            with self.subTest(anchor=anchor):
                text = 'Title' if centered else 'Anchor text'
                if centered:
                    # Exercise the editor's safe alias for the generated
                    # #title anchor; direct unsafe HTML IDs are sanitized.
                    self.content('## **Title**\n\nOther text')
                    self.page.locator('#align-center').click()
                else:
                    self.content(f'<h2 id="{anchor}"><strong>{text}</strong></h2>\n\nOther text')
                heading = self.page.locator('.toastui-editor-ww-container h2')
                safe_id = heading.get_attribute('id')
                self.assertTrue(safe_id)
                self.select_text(text)
                self.page.locator('#block-style-toggle').click()
                tool(self.page, '#block-paragraph').click()
                paragraph = self.paragraph(text)
                expect(paragraph).to_have_attribute('id', safe_id)
                expect(paragraph.locator('strong')).to_have_text(text)
                body = self.saved()['body']
                self.assertIn(f'id="{anchor}"', body)
                self.page.keyboard.press('Control+z')
                expect(heading).to_have_attribute('id', safe_id)
                self.page.keyboard.press('Control+Shift+Z')
                expect(paragraph).to_have_attribute('id', safe_id)
                self.page.locator('#mode-markdown').click()
                self.page.locator('#mode-visual').click()
                self.page.reload()
                expect(paragraph).to_have_attribute('id', safe_id)
                expect(paragraph.locator('strong')).to_have_text(text)
                if centered:
                    expect(paragraph).to_have_css('text-align', 'center')
                self.assertIn(f'id="{anchor}"', self.active()['body'])

    def test_setext_heading_converts_to_higher_levels_without_reverting_on_reload(self):
        for level in (3, 6):
            with self.subTest(level=level):
                self.content('Setext **title**\n---\n\nFollowing paragraph.')
                self.select_text('title')
                self.page.locator('#block-style-toggle').click()
                tool(self.page, f'#block-heading-{level}').click()
                heading = self.page.locator(f'.toastui-editor-ww-container h{level}')
                expect(heading.locator('strong')).to_have_text('title')
                body = self.saved()['body']
                self.assertEqual(body, '#' * level + ' Setext **title**\n\nFollowing paragraph.')
                self.page.keyboard.press('Control+z')
                expect(self.page.locator('.toastui-editor-ww-container h2')).to_have_text('Setext title')
                self.page.keyboard.press('Control+Shift+Z')
                expect(heading).to_have_text('Setext title')
                self.page.locator('#mode-markdown').click()
                self.page.locator('#mode-visual').click()
                self.page.reload()
                expect(heading.locator('strong')).to_have_text('title')
                self.assertEqual(self.active()['body'], body)


if __name__ == '__main__':
    unittest.main(verbosity=2)
