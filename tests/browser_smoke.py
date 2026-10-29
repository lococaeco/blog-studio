"""Real Chromium regression tests against a disposable synthetic blog.

Requires Playwright (development only) and Chromium/Chrome:
  python -m pip install playwright
  python tests/browser_smoke.py
Set STUDIO_BROWSER to an installed Chromium executable if needed.
"""
from __future__ import annotations
import base64
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

from playwright.sync_api import sync_playwright, expect
from browser_toolbar_support import tool

APP = Path(__file__).resolve().parents[1]
FIXTURE_BLOG = Path(__file__).resolve().parent / 'fixtures' / 'blog'
spec = importlib.util.spec_from_file_location('studio_browser_server', APP / 'server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)
PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=')


class BrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='blog-studio-browser-')
        cls.repo = Path(cls.temp.name).resolve()
        shutil.copytree(FIXTURE_BLOG, cls.repo, dirs_exist_ok=True)
        for args in (('init', '-b', 'main'), ('config', 'user.name', 'Browser Test'),
                     ('config', 'user.email', 'browser@example.invalid'), ('add', '.'), ('commit', '-m', 'fixture')):
            subprocess.run(['git', '-C', str(cls.repo), *args], check=True, capture_output=True)
        cls.http = server.create_server(cls.repo)
        cls.worker = threading.Thread(target=cls.http.serve_forever, daemon=True)
        cls.worker.start()
        cls.base = f'http://127.0.0.1:{cls.http.server_port}'
        cls.pw = sync_playwright().start()
        executable = os.environ.get('STUDIO_BROWSER')
        # CI installs the browser pinned by requirements-test.txt. Prefer it
        # over an unrelated Chrome version preinstalled on the runner.
        if not executable and not Path(cls.pw.chromium.executable_path).is_file():
            executable = shutil.which('google-chrome') or shutil.which('chromium')
        cls.browser = cls.pw.chromium.launch(**({'executable_path': executable} if executable else {}), headless=True, args=['--no-sandbox'])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.http.shutdown()
        cls.http.server_close()
        cls.temp.cleanup()

    def setUp(self):
        self.context = self.browser.new_context(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
        self.page = self.context.new_page()
        self.errors = []
        self.page.on('pageerror', lambda e: self.errors.append(str(e)))
        self.page.goto(self.base)
        expect(self.page.locator('.post-item').first).to_be_visible()

    def tearDown(self):
        self.context.close()
        self.assertEqual(self.errors, [], 'Uncaught browser errors')

    def active(self):
        key = self.page.evaluate("Object.keys(localStorage).find(key => key.endsWith('last-draft'))")
        draft_id = self.page.evaluate('(key) => localStorage.getItem(key)', key)
        return self.http.studio.get_draft(draft_id)

    def saved(self):
        expect(self.page.locator('#save-status')).to_contain_text('모든 변경 저장됨', timeout=10000)
        return self.active()

    def new_post(self, title):
        self.page.locator('#new-post').click()
        expect(self.page.locator('.toastui-editor-defaultUI')).to_be_visible()
        expect(self.page.locator('#document')).to_have_attribute('aria-busy', 'false')
        self.page.locator('#title').fill(title)
        self.page.locator('.toastui-editor-ww-container [contenteditable=true]').fill('첫 문장입니다.\n안전하게 자동 저장되는 본문입니다.')
        return self.saved()

    def open_settings(self):
        expect(self.page.locator('#metadata-panel')).not_to_be_visible()
        self.page.locator('#metadata-button').click()
        expect(self.page.get_by_role('dialog', name='글 설정', exact=True)).to_be_visible()
        expect(self.page.locator('#metadata-button')).to_have_attribute('aria-expanded', 'true')

    def close_settings(self):
        self.page.locator('#metadata-close').click()
        expect(self.page.locator('#metadata-panel')).not_to_be_visible()
        expect(self.page.locator('#metadata-button')).to_have_attribute('aria-expanded', 'false')

    def visual_markdown(self, title, body):
        self.new_post(title)
        self.page.locator('#mode-markdown').click()
        self.page.locator('.toastui-editor-md-container [contenteditable=true]').fill(body)
        self.page.locator('#mode-visual').click()
        expect(self.page.locator('#mode-visual')).to_have_attribute('aria-pressed', 'true')
        return self.saved()

    def select_paragraphs(self, first, last=None):
        # Exercise the browser's real selection, including inline marks, rather
        # than reaching into the editor implementation or invoking its command.
        self.page.locator('.toastui-editor-ww-container [contenteditable=true]').evaluate('''(editor, texts) => {
            const paragraphs = [...editor.querySelectorAll('p')];
            const first = paragraphs.find(node => node.textContent === texts[0]);
            const last = paragraphs.find(node => node.textContent === texts[1]);
            if (!first || !last) throw new Error('Missing alignment fixture paragraph');
            editor.focus();
            const range = document.createRange();
            range.setStart(first, 0);
            range.setEnd(last, last.childNodes.length);
            const selection = window.getSelection();
            selection.removeAllRanges();
            selection.addRange(range);
            document.dispatchEvent(new Event('selectionchange'));
        }''', [first, last or first])

    def paragraph(self, text, preview=False):
        container = '.toastui-editor-md-preview' if preview else '.toastui-editor-ww-container'
        return self.page.locator(container + ' p').filter(has_text=text).first

    def test_existing_body_and_hidden_state_survive_metadata_edit(self):
        post = '_posts/ai/llm/2025-01-01-sample-private.md'
        original = (self.repo / post).read_text('utf-8')
        self.page.locator('.post-item').filter(has_text='비공개 Markdown 예제').click()
        expect(self.page.locator('#mode-markdown')).to_have_attribute('aria-pressed', 'true')
        self.open_settings()
        expect(self.page.locator('#published')).not_to_be_checked()
        expect(self.page.locator('#category')).to_be_disabled()
        self.close_settings()
        self.page.locator('#excerpt').fill('브라우저에서 요약만 수정')
        draft = self.saved()
        self.assertEqual(draft['body'], server.split_post(original)[2])
        self.assertFalse(draft['published'])
        self.assertEqual((self.repo / post).read_text('utf-8'), original)
        self.page.reload()
        expect(self.page.locator('#excerpt')).to_have_value('브라우저에서 요약만 수정')
        self.assertEqual(self.active()['body'], draft['body'])

    def test_delete_other_draft_from_list_preserves_current_work_and_cancel(self):
        first = self.new_post('목록에서 삭제할 초안')
        current = self.new_post('계속 편집할 초안')
        row = self.page.locator(f'[data-draft-id="{first["id"]}"]')
        row.locator('.draft-delete').click()
        expect(self.page.locator('#confirm-description')).to_contain_text('목록에서 삭제할 초안')
        self.page.locator('#confirm-cancel').click()
        self.assertEqual(self.http.studio.get_draft(first['id'])['id'], first['id'])
        expect(self.page.locator('#title')).to_have_value('계속 편집할 초안')
        self.page.locator('#title').fill('현재 초안의 마지막 수정')
        row.locator('.draft-delete').click()
        self.page.locator('#confirm-accept').click()
        expect(row).to_have_count(0)
        expect(self.page.locator('#title')).to_have_value('현재 초안의 마지막 수정')
        self.assertEqual(self.saved()['id'], current['id'])
        self.assertEqual(self.active()['title'], '현재 초안의 마지막 수정')
        self.assertFalse((self.repo / '.blog-studio/drafts' / (first['id'] + '.json')).exists())
        self.page.reload()
        expect(self.page.locator('#title')).to_have_value('현재 초안의 마지막 수정')

    def test_delete_active_draft_after_inflight_autosave_fails(self):
        draft = self.new_post('저장 오류 이후 삭제')
        pending = []
        self.page.route('**/api/drafts', lambda route: pending.append(route))
        self.page.locator('#title').fill('아직 저장 중인 변경')
        expect(self.page.locator('#save-status')).to_contain_text('저장 중…', timeout=10000)
        self.page.wait_for_timeout(100)
        self.assertEqual(len(pending), 1)
        self.page.locator(f'[data-draft-id="{draft["id"]}"] .draft-delete').click()
        self.page.locator('#confirm-accept').click()
        pending[0].fulfill(status=409, content_type='application/json', body='{"error":"test save conflict"}')
        expect(self.page.locator('#welcome')).to_be_visible()
        expect(self.page.locator(f'[data-draft-id="{draft["id"]}"]')).to_have_count(0)
        self.page.wait_for_timeout(1200)
        self.assertEqual(len(pending), 1, 'Deleting must not restart a queued autosave')
        self.assertFalse((self.repo / '.blog-studio/drafts' / (draft['id'] + '.json')).exists())
        recovery = self.page.evaluate('(id) => Object.keys(localStorage).filter(k => k.endsWith("recovery." + id))', draft['id'])
        self.assertEqual(recovery, [])

    def test_delete_existing_post_draft_preserves_original(self):
        path = self.repo / '_posts/ai/llm/2025-01-02-sample-existing.md'
        original = path.read_bytes()
        self.page.locator('.post-item').filter(has_text='기존 Markdown 예제').click()
        expect(self.page.locator('#title')).to_have_value('기존 Markdown 예제')
        draft = self.active()
        self.page.locator('[data-view="drafts"]').click()
        self.page.locator(f'[data-draft-id="{draft["id"]}"] .draft-delete').click()
        self.page.locator('#confirm-accept').click()
        expect(self.page.locator('#welcome')).to_be_visible()
        self.assertEqual(path.read_bytes(), original)
        self.page.reload()
        expect(self.page.locator('#welcome')).to_be_visible()
        self.assertFalse((self.repo / '.blog-studio/drafts' / (draft['id'] + '.json')).exists())

    def test_visual_write_upload_preview_and_file_publication(self):
        draft = self.new_post('브라우저 통합 검증 글')
        self.open_settings()
        self.page.locator('#category').select_option('ai/deep_learning')
        self.page.locator('#tags').fill('Python, 테스트, Python')
        self.close_settings()
        self.page.locator('#image-files').set_input_files({'name': '한글 이미지.png', 'mimeType': 'image/png', 'buffer': PNG})
        expect(self.page.locator('.toastui-editor-ww-container img')).to_be_visible()
        draft = self.saved()
        self.assertIn('/assets/images/posts/studio-', draft['body'])
        self.assertEqual(draft['tags'], ['Python', '테스트'])
        self.page.locator('#publish-button').click()
        expect(self.page.locator('#confirm-publish')).to_be_enabled()
        pending = []
        self.page.route('**/api/publish', lambda route: pending.append(route))
        self.page.locator('#confirm-publish').click()
        self.page.wait_for_timeout(100)
        self.page.keyboard.press('Escape')
        expect(self.page.locator('#publish-dialog')).to_be_visible()
        self.assertTrue(self.page.locator('#document').evaluate('(node) => node.inert'))
        self.assertEqual(len(pending), 1)
        pending[0].continue_()
        expect(self.page.locator('#publish-result')).to_contain_text('블로그 파일에 저장했어요', timeout=15000)
        draft = self.active()
        text = (self.repo / draft['source_path']).read_text('utf-8')
        self.assertEqual(server.split_post(text)[0]['categories'], ['AI', 'Deep-Learning'])
        self.assertTrue(list((self.repo / f'assets/images/posts/studio-{draft["id"]}').glob('*.png')))
        self.assertEqual(subprocess.check_output(['git', '-C', str(self.repo), 'log', '-1', '--format=%s'], text=True).strip(), 'fixture')

    def test_alignment_preserves_formatting_across_modes_reload_export_and_publication(self):
        first = '첫 굵은 글씨와 링크.'
        untouched = '정렬을 바꾸지 않는 다음 문단.'
        self.visual_markdown('문단 정렬 저장 검증',
                             '첫 **굵은 글씨**와 [링크](https://example.invalid).\n\n' + untouched)
        self.select_paragraphs(first)
        self.page.locator('#align-center').click()
        expect(self.paragraph(first)).to_have_css('text-align', 'center')
        expect(self.paragraph(first).locator('strong')).to_have_text('굵은 글씨')
        expect(self.paragraph(first).locator('a')).to_have_attribute('href', 'https://example.invalid')
        self.assertNotEqual(self.paragraph(untouched).evaluate('(node) => getComputedStyle(node).textAlign'), 'center')
        centered = self.saved()
        self.assertRegex(centered['body'], r'text-align:\s*center')
        self.assertIn('<strong>굵은 글씨</strong>', centered['body'])
        self.assertIn(untouched, centered['body'])

        self.page.locator('#mode-markdown').click()
        expect(self.page.locator('#align-center')).to_be_disabled()
        preview = self.page.locator('.toastui-editor-md-preview')
        if not preview.is_visible():
            self.page.locator('.toastui-editor-md-tab-container .tab-item').filter(has_text='미리보기').click()
        expect(self.paragraph(first, preview=True)).to_have_css('text-align', 'center')
        expect(self.paragraph(first, preview=True).locator('strong')).to_have_text('굵은 글씨')
        self.page.locator('#mode-visual').click()
        expect(self.paragraph(first)).to_have_css('text-align', 'center')
        self.select_paragraphs(first)
        self.page.locator('#align-right').click()
        expect(self.paragraph(first)).to_have_css('text-align', 'right')
        right = self.saved()
        self.page.reload()
        expect(self.page.locator('#title')).to_have_value('문단 정렬 저장 검증')
        expect(self.paragraph(first)).to_have_css('text-align', 'right')
        expect(self.paragraph(first).locator('strong')).to_have_text('굵은 글씨')
        self.assertEqual(self.active()['body'], right['body'])
        with self.page.expect_download() as download:
            tool(self.page, '#export-button').click()
        exported = Path(download.value.path()).read_text('utf-8')
        self.assertEqual(server.split_post(exported)[2], right['body'])
        self.page.locator('#publish-button').click()
        expect(self.page.locator('#confirm-publish')).to_be_enabled()
        self.page.locator('#confirm-publish').click()
        expect(self.page.locator('#publish-result')).to_contain_text('블로그 파일에 저장했어요', timeout=15000)
        published = self.active()
        post = (self.repo / published['source_path']).read_text('utf-8')
        self.assertEqual(server.split_post(post)[2], right['body'])

    def test_alignment_multi_paragraph_undo_left_reset_and_code_block(self):
        first, second = '함께 정렬할 첫 문단.', '함께 정렬할 둘째 문단.'
        untouched = '선택 밖의 문단.'
        self.visual_markdown('정렬 선택과 실행 취소 검증',
                             first + '\n\n' + second + '\n\n' + untouched + '\n\n```python\nprint("keep code")\n```')
        self.select_paragraphs(first, second)
        self.page.locator('#align-center').click()
        for text in (first, second):
            expect(self.paragraph(text)).to_have_css('text-align', 'center')
        self.assertNotEqual(self.paragraph(untouched).evaluate('(node) => getComputedStyle(node).textAlign'), 'center')
        self.page.keyboard.press('ArrowRight')
        self.page.keyboard.insert_text(' 나중에 입력한 문장')
        expect(self.paragraph(second)).to_contain_text('나중에 입력한 문장')
        self.page.keyboard.press('Control+z')
        expect(self.paragraph(second)).to_have_text(second)
        expect(self.paragraph(first)).to_have_css('text-align', 'center')
        expect(self.paragraph(second)).to_have_css('text-align', 'center')
        self.page.keyboard.press('Control+z')
        expect(self.paragraph(first)).to_be_visible()
        expect(self.paragraph(second)).to_be_visible()
        expect(self.paragraph(first)).not_to_have_css('text-align', 'center')
        expect(self.paragraph(second)).not_to_have_css('text-align', 'center')
        self.page.keyboard.press('Control+Shift+Z')
        expect(self.paragraph(first)).to_have_css('text-align', 'center')
        expect(self.paragraph(second)).to_have_css('text-align', 'center')
        self.select_paragraphs(first, second)
        self.page.locator('#align-left').click()
        for text in (first, second):
            self.assertIn(self.paragraph(text).evaluate('(node) => getComputedStyle(node).textAlign'), ('left', 'start'))
        reset = self.saved()
        self.assertNotRegex(reset['body'], r'text-align:\s*(center|right)')
        self.page.set_viewport_size({'width': 390, 'height': 844})
        for direction in ('left', 'center', 'right'):
            button = self.page.locator('#align-' + direction)
            expect(button).to_be_visible()
            bounds = button.bounding_box()
            self.assertGreaterEqual(bounds['x'], 0)
            self.assertLessEqual(bounds['x'] + bounds['width'], 390)
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 390)
        code = self.page.locator('.toastui-editor-ww-container pre').filter(has_text='keep code').first
        code.click()
        for direction in ('left', 'center', 'right'):
            expect(self.page.locator('#align-' + direction)).to_be_enabled()
        self.page.locator('#align-center').click()
        for text in (first, second, untouched):
            expect(self.paragraph(text)).to_have_css('text-align', 'center')
        expect(code).to_contain_text('print("keep code")')

    def test_alignment_heading_and_unsupported_list_keep_blocks_separate(self):
        heading, item, following = '정렬할 제목', '굵은 목록 항목', '목록 다음 일반 문단.'
        self.visual_markdown('제목과 목록 정렬 검증',
                             '## ' + heading + '\n\n- **굵은 목록** 항목\n\n' + following + '\n\n## ' + heading)
        titles = self.page.locator('.toastui-editor-ww-container h2').filter(has_text=heading)
        title = titles.first
        title.evaluate("""node => {
            node.closest('[contenteditable=true]').focus();
            const range = document.createRange(); range.selectNodeContents(node);
            const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
            document.dispatchEvent(new Event('selectionchange'));
        }""")
        self.page.locator('#align-right').click()
        expect(title).to_have_css('text-align', 'right')
        heading_aligned = self.saved()
        identifiers = titles.evaluate_all('(nodes) => nodes.map(node => node.id)')
        self.assertEqual(len(identifiers), 2)
        self.assertTrue(all(identifiers))
        self.assertEqual(len(set(identifiers)), 2, 'Repeated heading text must retain distinct blog anchors')
        self.select_paragraphs(item)
        for direction in ('left', 'center', 'right'):
            expect(self.page.locator('#align-' + direction)).to_be_disabled()
        self.assertEqual(self.saved()['body'], heading_aligned['body'])
        self.select_paragraphs(item, following)
        expect(self.page.locator('#align-center')).to_be_enabled()
        self.page.locator('#align-center').click()
        self.page.locator('#mode-markdown').click()
        self.page.locator('#mode-visual').click()
        expect(titles.first).to_have_css('text-align', 'right')
        self.assertNotEqual(titles.nth(1).evaluate('(node) => getComputedStyle(node).textAlign'), 'right')
        self.assertEqual(titles.evaluate_all('(nodes) => nodes.map(node => node.id)'), identifiers)
        expect(self.paragraph(item).locator('strong')).to_have_text('굵은 목록')
        self.assertTrue(self.paragraph(item).evaluate('(node) => !!node.closest("li")'))
        self.assertFalse(self.paragraph(following).evaluate('(node) => !!node.closest("li, h2")'))
        expect(self.paragraph(following)).to_have_css('text-align', 'center')

    def test_alignment_preserves_mixed_html_heading_anchors_after_later_edit(self):
        self.visual_markdown('HTML 제목과 기존 목차 주소 검증', '<h2>Same</h2>\n\n## Same\n\n## Same')
        titles = self.page.locator('.toastui-editor-ww-container h2').filter(has_text='Same')
        expect(titles).to_have_count(3)
        titles.first.evaluate("""node => {
            node.closest('[contenteditable=true]').focus();
            const range = document.createRange(); range.selectNodeContents(node);
            const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
            document.dispatchEvent(new Event('selectionchange'));
        }""")
        self.page.locator('#align-center').click()
        raw_aligned = self.saved()
        self.assertEqual(raw_aligned['body'].count('## Same'), 2,
                         'An existing HTML heading must not renumber following Markdown anchors')
        html_identifier = titles.first.get_attribute('id')
        self.assertTrue(html_identifier)
        self.assertNotIn(html_identifier, ('same', 'same-1'))
        titles.nth(1).evaluate("""node => {
            node.closest('[contenteditable=true]').focus();
            const range = document.createRange(); range.selectNodeContents(node);
            const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
            document.dispatchEvent(new Event('selectionchange'));
        }""")
        self.page.locator('#align-right').click()
        self.saved()
        self.assertEqual(titles.evaluate_all('(nodes) => nodes.map(node => node.id)'),
                         [html_identifier, 'same', 'same-1'])
        self.page.locator('#mode-markdown').click()
        self.context.grant_permissions(['clipboard-read', 'clipboard-write'])
        self.page.evaluate('(text) => navigator.clipboard.writeText(text)', '\n\n## Same')
        self.page.locator('.toastui-editor-md-container [contenteditable=true]').click()
        self.page.keyboard.press('Control+End')
        self.page.keyboard.press('Control+v')
        # A real paste followed immediately by a mode switch must retain the
        # new heading, without waiting for autosave or a rendering delay.
        self.page.locator('#mode-visual').click()
        expect(titles).to_have_count(4)
        appended = self.saved()
        self.assertIn('id="same-2"', appended['body'], 'A later duplicate must not reuse an existing blog anchor')
        self.page.reload()
        expect(titles).to_have_count(4)
        self.assertEqual(titles.evaluate_all('(nodes) => nodes.map(node => node.id)'),
                         [html_identifier, 'same', 'same-1', 'same-2'])
        expect(titles.first).to_have_css('text-align', 'center')
        expect(titles.nth(1)).to_have_css('text-align', 'right')

        # "title" is a named DOM property. Sanitization must not silently drop
        # the anchor selected for a heading with that common English title.
        self.visual_markdown('예약 이름을 사용하는 제목 검증', '## Title\n\n제목 아래의 일반 문단.')
        reserved_title = self.page.locator('.toastui-editor-ww-container h2').filter(has_text='Title')
        reserved_title.evaluate("""node => {
            node.closest('[contenteditable=true]').focus();
            const range = document.createRange(); range.selectNodeContents(node);
            const selection = getSelection(); selection.removeAllRanges(); selection.addRange(range);
            document.dispatchEvent(new Event('selectionchange'));
        }""")
        self.page.locator('#align-center').click()
        reserved = self.saved()
        identifier = reserved_title.get_attribute('id')
        self.assertTrue(identifier)
        self.assertRegex(reserved['body'], r'<h2\b[^>]*\sid="title"',
                         'The published heading must retain its original #title anchor')
        self.page.reload()
        expect(reserved_title).to_have_attribute('id', identifier)
        expect(reserved_title).to_have_css('text-align', 'center')

    def test_offline_recovery_and_download_use_latest_unsaved_text(self):
        draft = self.new_post('오프라인 복구')
        blocked_writes = []

        def block_draft_write(route):
            blocked_writes.append(route.request.post_data_json)
            route.abort()

        self.page.route('**/api/drafts', block_draft_write)
        self.page.route('**/api/export?*', lambda route: route.abort())
        self.page.locator('#title').fill('저장 실패해도 남는 마지막 제목')
        self.page.locator('.toastui-editor-ww-container [contenteditable=true]').fill('서버에 아직 저장되지 않은 마지막 문장')
        expect(self.page.locator('#save-status')).to_contain_text('저장 실패', timeout=10000)
        with self.page.expect_download() as download:
            tool(self.page, '#export-button').click()
        self.assertIn('서버에 아직 저장되지 않은 마지막 문장', Path(download.value.path()).read_text('utf-8'))
        self.assertTrue(any(payload['body'] == '서버에 아직 저장되지 않은 마지막 문장'
                            for payload in blocked_writes), 'The latest draft must actually fail to reach the server')
        # Keep writes offline through navigation. A dirty page may save again
        # during visibilitychange; restoring the network before reload can
        # legitimately save the recovery copy and remove the need for a banner.
        # Startup reads /api/drafts/{id}, so this write-only route permits loading
        # the original server draft while preserving the offline precondition.
        self.page.reload()
        expect(self.page.locator('#recovery-banner')).to_be_visible()
        expect(self.page.locator('#title')).to_have_value(draft['title'])
        self.assertEqual(self.active()['body'], draft['body'])
        self.page.unroute('**/api/drafts')
        self.page.unroute('**/api/export?*')
        with self.page.expect_response(lambda response: response.url.endswith('/api/drafts')
                                       and response.request.method == 'POST') as restored:
            self.page.locator('#recover-button').click()
        self.assertEqual(restored.value.status, 200)
        self.assertIn('서버에 아직 저장되지 않은 마지막 문장', restored.value.request.post_data_json['body'])
        expect(self.page.locator('#title')).to_have_value('저장 실패해도 남는 마지막 제목')
        recovered = self.saved()
        self.assertIn('서버에 아직 저장되지 않은 마지막 문장', recovered['body'])
        self.assertEqual(recovered['id'], draft['id'])

    def test_import_stays_markdown_and_sanitizes_preview(self):
        body = '# 원문\n\n$$x^2+y^2$$\n\n<script>window.studioPwned=1</script>\n\n<img src="/missing.png" onerror="window.studioPwned=2">\n\n[evil](javascript:alert(1))\n\n{% include custom.html %}\n'
        original = '---\ntitle: 가져오기 검증\ncustom:\n  keep: true\n---\n' + body
        self.page.locator('#import-file').set_input_files({'name': 'import-test.md', 'mimeType': 'text/markdown', 'buffer': original.encode()})
        expect(self.page.locator('#title')).to_have_value('가져오기 검증')
        expect(self.page.locator('#mode-markdown')).to_have_attribute('aria-pressed', 'true')
        self.assertEqual(self.active()['body'], body)
        preview = self.page.locator('.toastui-editor-md-preview')
        if not preview.is_visible():
            self.page.locator('.toastui-editor-md-tab-container .tab-item').filter(has_text='미리보기').click()
        expect(preview).to_be_visible()
        expect(preview.locator('.katex').first).to_be_visible()
        # Resize the same Markdown document: a narrow editor uses a preview tab
        # while a wide editor can display source and preview side by side.
        self.page.set_viewport_size({'width': 390, 'height': 844})
        preview_tab = self.page.locator('.toastui-editor-md-tab-container .tab-item').filter(has_text='미리보기')
        expect(preview_tab).to_be_visible()
        preview_tab.click()
        expect(preview).to_be_visible()
        expect(preview.locator('.katex').first).to_be_visible()
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 390)
        self.assertIsNone(self.page.evaluate('window.studioPwned'))
        self.assertEqual(self.page.locator('.toastui-editor-md-preview [onerror], .toastui-editor-md-preview script, .toastui-editor-md-preview a[href^="javascript:"]').count(), 0)
        self.assertGreater(self.page.locator('.toastui-editor-md-preview .katex').count(), 0)

    def test_home_saves_latest_text_and_reopens_recent_draft_and_template(self):
        draft = self.new_post('홈 이동 전 초안')
        pending = []
        self.page.route('**/api/drafts', lambda route: pending.append(route))
        self.page.locator('#title').fill('홈에서 이어 쓸 마지막 제목')
        self.page.locator('.toastui-editor-ww-container [contenteditable=true]').fill('자동 저장을 기다리지 않고 홈으로 이동한 마지막 문장')
        with self.page.expect_request('**/api/drafts'):
            self.page.locator('#home-button').click()
        expect(self.page.locator('#document')).to_have_attribute('aria-busy', 'true')
        expect(self.page.locator('#welcome')).not_to_be_visible()
        self.assertEqual(len(pending), 1)
        pending[0].continue_()
        expect(self.page.locator('#welcome')).to_be_visible()
        self.page.unroute('**/api/drafts')
        stored = self.http.studio.get_draft(draft['id'])
        self.assertEqual(stored['title'], '홈에서 이어 쓸 마지막 제목')
        self.assertIn('자동 저장을 기다리지 않고 홈으로 이동한 마지막 문장', stored['body'])
        self.page.locator('#recent-drafts .draft-card').filter(has_text=stored['title']).click()
        expect(self.page.locator('#title')).to_have_value(stored['title'])
        self.assertEqual(self.active()['id'], draft['id'])
        self.assertEqual(self.active()['body'], stored['body'])
        self.page.locator('#home-button').click()
        expect(self.page.locator('#welcome')).to_be_visible()
        template = next(item for item in self.http.studio.bootstrap()['templates'] if item['body'])
        self.page.locator('#home-templates .template-card').filter(has_text=template['label']).click()
        expect(self.page.locator('#title')).to_be_focused()
        template_draft = self.active()
        self.assertNotEqual(template_draft['id'], draft['id'])
        self.assertEqual(template_draft['body'], template['body'])
        self.assertEqual(self.http.studio.get_draft(draft['id'])['body'], stored['body'])

    def test_narrow_viewport_and_switch_during_slow_load(self):
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.page.reload()
        self.page.locator('#welcome-new').click()
        expect(self.page.locator('#title')).to_be_visible()
        self.page.locator('#title').fill('모바일 화면 검증')
        self.page.locator('.toastui-editor-ww-container [contenteditable=true]').fill('좁은 화면에서도 입력합니다.')
        self.saved()
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 390)
        self.open_settings()
        self.close_settings()
        self.page.set_viewport_size({'width': 1440, 'height': 1000})
        self.page.locator('[data-view="all"]').click()
        pending = []
        self.page.route('**/api/posts?*', lambda route: pending.append(route))
        # Trigger the request then inspect the immediate, synchronous interaction lock.
        self.page.locator('.post-item').filter(has_text='기존 Markdown 예제').click()
        self.assertTrue(self.page.locator('#document').evaluate('(node) => node.inert'))
        self.page.wait_for_timeout(100)
        self.assertEqual(len(pending), 1)
        pending[0].continue_()
        expect(self.page.locator('#title')).to_have_value('기존 Markdown 예제')
        self.assertFalse(self.page.locator('#document').evaluate('(node) => node.inert'))

    def test_settings_keyboard_focus_escape_and_autosave(self):
        draft = self.new_post('설정 창 키보드 검증')
        original_body = draft['body']
        self.open_settings()
        self.assertTrue(self.page.locator('#metadata-panel').evaluate('(node) => node.contains(document.activeElement)'))
        self.page.locator('#category').select_option('ai/deep_learning')
        self.page.locator('#tags').fill('키보드, 접근성')
        self.page.locator('#metadata-close').focus()
        self.page.keyboard.press('Shift+Tab')
        self.assertTrue(self.page.locator('#metadata-panel').evaluate('(node) => node.contains(document.activeElement)'), 'Reverse Tab must stay in the modal settings')
        self.page.keyboard.press('Tab')
        self.assertTrue(self.page.locator('#metadata-panel').evaluate('(node) => node.contains(document.activeElement)'), 'Tab must stay in the modal settings')
        self.page.keyboard.press('Escape')
        expect(self.page.locator('#metadata-panel')).not_to_be_visible()
        expect(self.page.locator('#metadata-button')).to_have_attribute('aria-expanded', 'false')
        expect(self.page.locator('#metadata-button')).to_be_focused()
        saved = self.saved()
        self.assertEqual(saved['id'], draft['id'])
        self.assertEqual(saved['category'], 'ai/deep_learning')
        self.assertEqual(saved['tags'], ['키보드', '접근성'])
        self.assertEqual(saved['body'], original_body)
        self.open_settings()
        expect(self.page.locator('#tags')).to_have_value('키보드, 접근성')
        self.close_settings()
        self.page.locator('#publish-button').click()
        expect(self.page.locator('#confirm-publish')).to_be_enabled()
        self.page.keyboard.press('Escape')
        expect(self.page.locator('#publish-dialog')).not_to_be_visible()
        expect(self.page.locator('#publish-button')).to_be_focused()
        expect(self.page.locator('#title')).to_have_value('설정 창 키보드 검증')

    def test_mobile_sidebar_keyboard_focus_and_escape(self):
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.page.reload()
        sidebar = self.page.locator('#sidebar')
        menu = self.page.locator('#menu-toggle')
        expect(sidebar).to_have_attribute('aria-hidden', 'true')
        self.assertTrue(sidebar.evaluate('(node) => node.inert'))
        expect(menu).to_have_attribute('aria-expanded', 'false')
        menu.focus()
        for _ in range(5):
            self.page.keyboard.press('Tab')
            self.assertFalse(sidebar.evaluate('(node) => node.contains(document.activeElement)'), 'Closed mobile navigation must not receive keyboard focus')
        menu.focus()
        self.page.keyboard.press('Enter')
        expect(menu).to_have_attribute('aria-expanded', 'true')
        expect(sidebar).to_have_attribute('aria-hidden', 'false')
        self.assertFalse(sidebar.evaluate('(node) => node.inert'))
        self.assertTrue(sidebar.evaluate('(node) => node.contains(document.activeElement)'), 'Opening mobile navigation should move focus inside')
        self.page.keyboard.press('Escape')
        expect(menu).to_have_attribute('aria-expanded', 'false')
        expect(sidebar).to_have_attribute('aria-hidden', 'true')
        expect(menu).to_be_focused()
        menu.click()
        self.page.locator('#new-post').click()
        expect(self.page.locator('#title')).to_be_focused()
        expect(sidebar).to_have_attribute('aria-hidden', 'true')
        self.page.locator('#title').fill('모바일 키보드 작성')
        self.page.locator('.toastui-editor-ww-container [contenteditable=true]').fill('메뉴를 닫고 글쓰기를 이어갑니다.')
        self.saved()
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'), 390)
        self.page.locator('#focus-button').click()
        expect(self.page.locator('#focus-button')).to_have_attribute('aria-pressed', 'true')
        menu.click()
        expect(self.page.locator('#new-post')).to_be_visible()
        expect(menu).to_have_attribute('aria-expanded', 'true')
        expect(self.page.locator('#focus-button')).to_have_attribute('aria-pressed', 'false')
        self.assertTrue(sidebar.evaluate('(node) => node.contains(document.activeElement)'), 'Opening navigation from focus mode must expose the navigation, not an empty overlay')
        self.page.keyboard.press('Escape')
        expect(menu).to_be_focused()
        expect(self.page.locator('#title')).to_have_value('모바일 키보드 작성')
        self.page.set_viewport_size({'width': 1440, 'height': 1000})
        expect(sidebar).not_to_have_attribute('aria-hidden', 'true')
        self.assertFalse(sidebar.evaluate('(node) => node.inert'), 'Desktop navigation must become keyboard accessible after resize')

    def test_two_tabs_report_conflict_without_losing_latest_server_draft(self):
        draft = self.new_post('여러 탭에서 편집')
        other = self.context.new_page()
        other.goto(self.base)
        expect(other.locator('#title')).to_have_value('여러 탭에서 편집')
        self.page.locator('#title').fill('첫 탭에서 저장한 최신 제목')
        self.saved()
        other.locator('#title').fill('충돌한 두 번째 탭 내용')
        expect(other.locator('#save-status')).to_contain_text('저장 충돌', timeout=10000)
        self.assertEqual(self.http.studio.get_draft(draft['id'])['title'], '첫 탭에서 저장한 최신 제목')
        with other.expect_download() as download:
            tool(other, '#export-button').click()
        self.assertIn('충돌한 두 번째 탭 내용', Path(download.value.path()).read_text('utf-8'))
        other.close()

    def test_clipboard_image_paste_and_drop_insert_local_images(self):
        self.new_post('이미지 붙여넣기와 드롭')
        self.context.grant_permissions(['clipboard-read', 'clipboard-write'])
        data = base64.b64encode(PNG).decode()
        self.page.evaluate('''async (encoded) => {
            const bytes = Uint8Array.from(atob(encoded), c => c.charCodeAt(0));
            await navigator.clipboard.write([new ClipboardItem({'image/png': new Blob([bytes], {type:'image/png'})})]);
        }''', data)
        editor = self.page.locator('.toastui-editor-ww-container [contenteditable=true]')
        editor.click()
        self.page.keyboard.press('Control+End')
        self.page.keyboard.press('Control+V')
        # ProseMirror may add an img.ProseMirror-separator caret helper with no src.
        expect(self.page.locator('.toastui-editor-ww-container img[src]')).to_have_count(1)
        transfer = self.page.evaluate_handle('''encoded => {
            const dt = new DataTransfer();
            dt.items.add(new File([Uint8Array.from(atob(encoded), c => c.charCodeAt(0))], 'drop-image.png', {type:'image/png'}));
            return dt;
        }''', data)
        editor.dispatch_event('drop', {'dataTransfer': transfer})
        expect(self.page.locator('.toastui-editor-ww-container img[src]')).to_have_count(2)
        draft = self.saved()
        self.assertEqual(draft['body'].count('/assets/images/posts/studio-'), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
