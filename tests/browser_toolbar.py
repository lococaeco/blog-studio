"""Actual pointer/keyboard checks for the unified authoring toolbar."""
import unittest

from playwright.sync_api import expect
import browser_smoke as base
import browser_writing as writing
from browser_toolbar_support import tool


class ToolbarTests(unittest.TestCase):
    setUpClass = classmethod(base.BrowserTests.setUpClass.__func__)
    tearDownClass = classmethod(base.BrowserTests.tearDownClass.__func__)
    setUp = base.BrowserTests.setUp
    tearDown = base.BrowserTests.tearDown
    active = base.BrowserTests.active
    saved = base.BrowserTests.saved
    new_post = base.BrowserTests.new_post
    content = writing.WritingTests.content
    select_text = writing.WritingTests.select_text
    select_paragraphs = base.BrowserTests.select_paragraphs

    def heading(self, level):
        self.page.locator('#block-style-toggle').click()
        menu = self.page.locator('#block-style-menu')
        expect(menu).to_be_visible()
        self.assertTrue(menu.evaluate('''panel => { const box=panel.getBoundingClientRect();
            return panel.contains(document.elementFromPoint(box.x+box.width/2,box.y+box.height/2)); }'''),
            'Editor text must not paint above the heading menu')
        self.page.screenshot(path='/tmp/blog-studio-toolbar-heading-popup.png')
        menu.locator('#block-heading-' + str(level)).click()
        expect(menu).not_to_be_visible()

    def test_heading_one_two_preserve_center_marks_selection_and_undo(self):
        self.content('제목이 될 **중요한 문장**과 [연결](https://example.invalid/title).\n\n그대로 남을 문단입니다.')
        self.select_text('중요한 문장')
        self.page.locator('#align-center').click()
        self.heading(1)
        editor = self.page.locator('.toastui-editor-ww-container [contenteditable=true]')
        expect(editor.locator('h1')).to_have_text('제목이 될 중요한 문장과 연결.')
        expect(editor.locator('h1')).to_have_css('text-align', 'center')
        expect(editor.locator('h1 strong')).to_have_text('중요한 문장')
        expect(editor.locator('h1 a')).to_have_attribute('href', 'https://example.invalid/title')
        expect(editor.locator('p').filter(has_text='그대로 남을 문단입니다.')).to_have_count(1)
        self.heading(2)
        expect(editor.locator('h2')).to_have_text('제목이 될 중요한 문장과 연결.')
        expect(editor.locator('h2')).to_have_css('text-align', 'center')
        self.page.keyboard.press('Control+z')
        expect(editor.locator('h1')).to_have_count(1)
        self.heading(2)
        saved = self.saved()
        self.page.reload()
        expect(editor.locator('h2')).to_have_text('제목이 될 중요한 문장과 연결.')
        expect(editor.locator('h2')).to_have_css('text-align', 'center')
        self.assertEqual(self.active()['body'], saved['body'])
        self.page.locator('#mode-markdown').click()
        self.page.locator('#mode-visual').click()
        expect(editor.locator('h2 strong')).to_have_text('중요한 문장')
        self.select_text('중요한 문장')
        self.page.locator('#block-style-toggle').click()
        self.page.locator('#block-paragraph').click()
        expect(editor.locator('h1,h2,h3')).to_have_count(0)
        expect(editor.locator('p').filter(has_text='제목이 될')).to_have_css('text-align', 'center')

    def test_grouped_menus_keep_editor_selection_and_keyboard_access(self):
        self.content('앞에 선택한 단어 뒤에 그대로.\n\n다른 문단도 그대로.')
        self.select_text('선택한 단어')
        self.page.locator('#format-menu-toggle').click()
        expect(self.page.locator('#format-menu')).to_be_visible()
        tool(self.page, '#writing-color').evaluate('(node)=>{node.value="#c62828";node.dispatchEvent(new Event("change",{bubbles:true}));}')
        if self.page.locator('#format-menu').is_visible():
            self.page.locator('#format-menu-toggle').click()
        editor = self.page.locator('.toastui-editor-ww-container [contenteditable=true]')
        expect(editor.locator('span[style]')).to_have_text('선택한 단어')
        expect(editor.locator('span[style]')).to_have_css('color', 'rgb(198, 40, 40)')
        expect(editor.locator('p').filter(has_text='다른 문단도 그대로.').locator('span')).to_have_count(0)
        trigger = self.page.locator('#edit-menu-toggle')
        trigger.focus(); trigger.press('Enter')
        expect(self.page.locator('#edit-menu')).to_be_visible()
        self.page.keyboard.press('Escape')
        expect(self.page.locator('#edit-menu')).not_to_be_visible()
        expect(trigger).to_be_focused()
        self.page.locator('#insert-menu-toggle').click()
        expect(self.page.locator('#insert-menu')).to_be_visible()
        self.page.locator('#document-menu-toggle').click()
        expect(self.page.locator('#insert-menu')).not_to_be_visible()
        expect(self.page.locator('#document-menu')).to_be_visible()
        self.page.locator('#title').click()
        expect(self.page.locator('#document-menu')).not_to_be_visible()

    def test_relocated_inline_and_block_commands_operate_on_selected_content(self):
        self.content('앞의 굵은 단어 뒤의 문장.\n\n바뀌지 않는 문단.')
        self.select_text('굵은 단어')
        self.page.locator('#toolbar-bold').click()
        self.page.locator('#toolbar-italic').click()
        editor = self.page.locator('.toastui-editor-ww-container [contenteditable=true]')
        expect(editor.locator('strong em,em strong')).to_have_text('굵은 단어')
        expect(editor.locator('p').filter(has_text='바뀌지 않는 문단.').locator('strong,em')).to_have_count(0)
        self.page.locator('#format-menu-toggle').click()
        self.page.locator('#toolbar-strike').click()
        expect(editor.locator('del,s')).to_have_text('굵은 단어')
        self.content('앞의 코드 단어 뒤의 문장.\n\n바뀌지 않는 문단.')
        self.select_text('코드 단어')
        self.page.locator('#format-menu-toggle').click()
        self.page.locator('#toolbar-code').click()
        expect(editor.locator('code')).to_have_text('코드 단어')
        for command, selector in [('bulletList','ul li'),('orderedList','ol li'),
                                  ('taskList','ul li.task-list-item'),('blockQuote','blockquote'),
                                  ('codeBlock','pre')]:
            with self.subTest(command=command):
                self.content('구조를 바꿀 문장.\n\n바뀌지 않는 문단.')
                self.select_text('구조를 바꿀 문장.')
                self.page.locator('#insert-menu-toggle').click()
                self.page.locator('#toolbar-' + command).click()
                expect(editor.locator(selector)).to_contain_text('구조를 바꿀 문장.')
                expect(editor.locator(':scope > p').filter(has_text='바뀌지 않는 문단.')).to_have_count(1)
                self.page.locator('#edit-menu-toggle').click()
                self.page.locator('#toolbar-undo').click()
                expect(editor.locator(selector)).to_have_count(0)
                expect(editor.locator(':scope > p').filter(has_text='구조를 바꿀 문장.')).to_have_count(1)

    def test_find_and_outline_share_one_panel_and_keep_keyboard_access(self):
        before = self.content('## 첫 제목\n\n찾을 문장입니다.\n\n## 둘째 제목\n\n끝 문장입니다.')['body']
        tool(self.page, '#writing-find').click()
        expect(self.page.locator('#writing-find-panel')).to_be_visible()
        self.page.locator('#find-query').fill('찾을')
        tool(self.page, '#writing-outline').click()
        expect(self.page.locator('#writing-outline-panel')).to_be_visible()
        expect(self.page.locator('#writing-find-panel')).not_to_be_visible()
        self.page.locator('[data-writing-close=outline]').click()
        expect(self.page.locator('#writing-outline-panel')).not_to_be_visible()
        expect(self.page.locator('#edit-menu-toggle')).to_be_focused()
        self.page.keyboard.press('Control+f')
        expect(self.page.locator('#writing-find-panel')).to_be_visible()
        expect(self.page.locator('#find-query')).to_be_focused()
        expect(self.page.locator('#find-query')).to_have_value('찾을')
        self.page.keyboard.press('Escape')
        expect(self.page.locator('#writing-find-panel')).not_to_be_visible()
        self.assertEqual(self.saved()['body'], before)

    def test_markdown_paragraph_reset_preserves_fenced_code_and_is_undoable(self):
        original = '## 실제 제목\n\n```python\n# 코드 안 주석\n```\n\n# 끝 제목'
        self.content(original)
        self.page.locator('#mode-markdown').click()
        self.page.locator('.toastui-editor-md-container [contenteditable=true]').click()
        self.page.keyboard.press('Control+a')
        self.page.locator('#block-style-toggle').click()
        self.page.locator('#block-paragraph').click()
        self.assertEqual(self.saved()['body'], '실제 제목\n\n```python\n# 코드 안 주석\n```\n\n끝 제목')
        self.page.keyboard.press('Control+z')
        self.assertEqual(self.saved()['body'], original)

    def test_link_and_remote_image_dialogs_preserve_selected_position_in_both_modes(self):
        for mode in ('visual', 'markdown'):
            with self.subTest(mode=mode):
                before = self.content('앞 링크 자리 뒤.\n\n이미지 위치 뒤.\n\n끝 문장.')['body']
                if mode == 'markdown':
                    self.page.locator('#mode-markdown').click()
                editor = self.page.locator('.toastui-editor-' + ('md' if mode == 'markdown' else 'ww') + '-container [contenteditable=true]')

                def select(text):
                    editor.evaluate('''(editor, text) => {
                        editor.focus(); const walker=document.createTreeWalker(editor,NodeFilter.SHOW_TEXT);
                        let node; while(node=walker.nextNode()) {
                            const offset=node.textContent.indexOf(text); if(offset<0) continue;
                            const range=document.createRange(); range.setStart(node,offset); range.setEnd(node,offset+text.length);
                            const selected=getSelection(); selected.removeAllRanges(); selected.addRange(range);
                            document.dispatchEvent(new Event('selectionchange')); return;
                        } throw new Error('Missing selected link/image fixture text');
                    }''', text)

                select('링크 자리')
                self.page.locator('#insert-menu-toggle').click()
                self.page.locator('#toolbar-link').click()
                dialog = self.page.locator('#toolbar-link-dialog')
                expect(dialog).to_be_visible()
                expect(self.page.locator('#toolbar-link-text')).to_have_value('링크 자리')
                self.page.locator('#toolbar-link-url').fill('javascript:alert(1)')
                self.page.locator('#toolbar-link-insert').click()
                expect(dialog).to_be_visible()
                self.assertEqual(self.active()['body'], before)
                self.page.locator('#toolbar-link-url').fill('/guide/')
                label = '링크 자리'
                if mode == 'markdown':
                    label = '선택한 링크'
                    self.page.locator('#toolbar-link-text').fill(label)
                else:
                    expect(self.page.locator('#toolbar-link-text')).to_have_attribute('readonly', '')
                self.page.locator('#toolbar-link-insert').click()
                expect(dialog).not_to_be_visible()
                linked = self.saved()['body']
                self.assertEqual(linked, f'앞 [{label}](/guide/) 뒤.\n\n이미지 위치 뒤.\n\n끝 문장.')
                select('이미지 위치')
                self.page.locator('#insert-menu-toggle').click()
                self.page.locator('#toolbar-image-url').click()
                self.page.locator('#toolbar-image-url-input').fill('/assets/images/fixture.png')
                self.page.locator('#toolbar-image-alt').fill('설명 있는 이미지')
                self.page.locator('#toolbar-image-insert').click()
                expect(self.page.locator('#toolbar-image-dialog')).not_to_be_visible()
                stored = self.saved()['body']
                self.assertEqual(stored, f'앞 [{label}](/guide/) 뒤.\n\n![설명 있는 이미지](/assets/images/fixture.png) 뒤.\n\n끝 문장.')
                self.page.reload()
                expect(self.page.locator('#title')).to_be_visible()
                self.assertEqual(self.active()['body'], stored)

    def test_table_fields_keep_insertion_position_and_markdown_heading_controls_work(self):
        self.content('표 앞 문장.\n\n마지막 문장.')
        editor = self.page.locator('.toastui-editor-ww-container [contenteditable=true]')
        editor.locator('p').filter(has_text='표 앞 문장.').click()
        self.page.keyboard.press('End')
        self.page.locator('#insert-menu-toggle').click()
        self.page.locator('#toolbar-table-rows').fill('3')
        self.page.locator('#toolbar-table-columns').fill('2')
        self.page.locator('#toolbar-table').click()
        expect(editor.locator('table')).to_have_count(1)
        expect(editor.locator('table tr')).to_have_count(3)
        expect(editor.locator('table tr').first.locator('th')).to_have_count(2)
        children = editor.locator(':scope > *').evaluate_all('(nodes)=>nodes.map(n=>{const content=n.matches(".studio-table-scroll")?n.querySelector("table"):n;return {tag:content.nodeName,text:content.textContent};})')
        self.assertEqual(children[0], {'tag':'P','text':'표 앞 문장.'})
        self.assertEqual(children[1]['tag'], 'TABLE')
        self.assertEqual(children[-1], {'tag':'P','text':'마지막 문장.'})
        self.assertTrue(all(node == {'tag':'P','text':''} for node in children[2:-1]),
                        'Native table insertion may add only an empty paragraph after its table')
        self.content('원문 제목\n\n다음 문단.')
        self.page.locator('#mode-markdown').click()
        markdown = self.page.locator('.toastui-editor-md-container [contenteditable=true]')
        markdown.click()
        self.page.keyboard.press('Control+Home')
        self.heading(1)
        self.assertEqual(self.saved()['body'], '# 원문 제목\n\n다음 문단.')
        self.heading(2)
        self.assertEqual(self.saved()['body'], '## 원문 제목\n\n다음 문단.')
        self.heading(6)
        self.assertEqual(self.saved()['body'], '###### 원문 제목\n\n다음 문단.')
        self.page.locator('#block-style-toggle').click()
        self.page.locator('#block-paragraph').click()
        self.assertEqual(self.saved()['body'], '원문 제목\n\n다음 문단.')

    def test_long_document_controls_stay_reachable_on_desktop_and_mobile(self):
        self.content('\n\n'.join(f'긴 글의 {index}번째 문단입니다. 아래로 내려가도 도구를 사용할 수 있어야 합니다.' for index in range(100)))
        for width, height in ((1440, 1000), (390, 844)):
            with self.subTest(width=width):
                self.page.set_viewport_size({'width': width, 'height': height})
                self.page.evaluate('window.scrollTo(0,document.documentElement.scrollHeight*.65)')
                self.page.wait_for_timeout(150)
                self.assertGreater(self.page.evaluate('window.scrollY'), 1000)
                self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), width)
                dock = self.page.locator('#editor-dock').bounding_box()
                self.assertIsNotNone(dock)
                self.assertGreaterEqual(dock['y'], 0)
                self.assertLess(dock['y'] + dock['height'], height * .5)
                for target in ('#block-style-toggle', '#align-center', '#format-menu-toggle', '#insert-menu-toggle', '#edit-menu-toggle', '#document-menu-toggle'):
                    box = self.page.locator(target).bounding_box()
                    self.assertGreaterEqual(box['y'], 0, target)
                    self.assertLessEqual(box['y'] + box['height'], height, target)
                for menu in ('format','insert','edit','document'):
                    self.page.locator('#' + menu + '-menu-toggle').click()
                    popup = self.page.locator('#' + menu + '-menu')
                    expect(popup).to_be_visible()
                    box = popup.bounding_box()
                    self.assertGreaterEqual(box['x'], 0)
                    self.assertLessEqual(box['x'] + box['width'], width + 1)
                    self.assertGreaterEqual(box['y'], 0)
                    self.assertLessEqual(box['y'] + box['height'], height + 1)
                    self.assertTrue(popup.evaluate('''panel => { const box=panel.getBoundingClientRect();
                        return panel.contains(document.elementFromPoint(box.x+box.width/2,box.y+box.height/2)); }'''),
                        'Editor content must not cover ' + menu)
                    if menu == 'insert':
                        self.page.screenshot(path=f'/tmp/blog-studio-toolbar-insert-{width}.png')
                    self.page.keyboard.press('Escape')
                    expect(popup).not_to_be_visible()
                self.page.screenshot(path=f'/tmp/blog-studio-toolbar-long-{width}.png', full_page=False)
                self.page.locator('#focus-button').click()
                expect(self.page.locator('#focus-button')).to_have_attribute('aria-pressed', 'true')
                self.page.evaluate('window.scrollTo(0,document.documentElement.scrollHeight*.65)')
                focused = self.page.locator('#editor-dock').bounding_box()
                header = self.page.locator('.topbar').bounding_box()
                self.assertGreaterEqual(focused['y'], header['y'] + header['height'])
                self.assertLess(focused['y'] + focused['height'], height * .5)
                self.page.locator('#insert-menu-toggle').click()
                expect(self.page.locator('#insert-menu')).to_be_visible()
                self.page.keyboard.press('Escape')
                self.page.locator('#focus-button').click()

    def test_workspace_menu_remains_available_from_home(self):
        self.page.locator('#workspace-menu-toggle').click()
        expect(self.page.locator('#workspace-menu')).to_be_visible()
        expect(self.page.locator('#personal-templates')).to_be_visible()
        expect(self.page.locator('#backup-sync')).to_be_visible()
        expect(self.page.locator('#cloud-open')).to_be_visible()
        self.page.keyboard.press('Escape')
        expect(self.page.locator('#workspace-menu')).not_to_be_visible()
        self.new_post('일관된 도구 크기')
        controls = ('#align-left','#align-center','#align-right','#block-style-toggle','#format-menu-toggle','#insert-menu-toggle','#edit-menu-toggle','#document-menu-toggle')
        heights = [self.page.locator(selector).bounding_box()['height'] for selector in controls]
        self.assertLessEqual(max(heights) - min(heights), 4)
        self.assertGreaterEqual(min(heights), 28)
        self.assertLessEqual(max(heights), 48)


if __name__ == '__main__':
    unittest.main(verbosity=2)
