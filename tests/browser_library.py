"""Real UI checks for personal templates, revisions and portable ZIP backups."""
import json
from pathlib import Path
import unittest
import zipfile

from playwright.sync_api import expect
from browser_toolbar_support import tool
import browser_smoke as base
import browser_writing as writing


class LibraryTests(unittest.TestCase):
    setUpClass = classmethod(base.BrowserTests.setUpClass.__func__)
    tearDownClass = classmethod(base.BrowserTests.tearDownClass.__func__)
    setUp = base.BrowserTests.setUp
    tearDown = base.BrowserTests.tearDown
    active = base.BrowserTests.active
    saved = base.BrowserTests.saved
    new_post = base.BrowserTests.new_post
    select_paragraphs = base.BrowserTests.select_paragraphs
    content = writing.WritingTests.content

    def personal(self):
        tool(self.page, '#personal-templates').click()
        return self.page.get_by_role('dialog', name='내 템플릿과 재사용 문구', exact=True)

    def test_personal_template_crud_and_rich_selected_snippet(self):
        original = self.content('**굵게**와 [링크](https://example.invalid/template).')
        library = self.personal()
        library.get_by_role('button', name='현재 글을 템플릿으로', exact=True).click()
        form = self.page.get_by_role('dialog', name='새 항목 저장', exact=True)
        form.get_by_label('이름', exact=True).fill('검사한 글 틀')
        expect(form.get_by_label('내용 (Markdown)', exact=True)).to_have_value(original['body'])
        form.get_by_role('button', name='저장', exact=True).click()
        row = library.locator('.library-row').filter(has_text='검사한 글 틀')
        row.get_by_role('button', name='수정', exact=True).click()
        edit = self.page.get_by_role('dialog', name='저장한 항목 수정', exact=True)
        edit.get_by_label('이름', exact=True).fill('수정한 글 틀')
        edit.get_by_role('button', name='저장', exact=True).click()
        library.locator('.library-row').filter(has_text='수정한 글 틀').get_by_role('button', name='새 글 만들기', exact=True).click()
        expect(library).not_to_be_visible()
        created = self.saved()
        self.assertNotEqual(created['id'], original['id'])
        self.assertEqual(created['body'], original['body'])
        self.assertEqual(self.http.studio.get_draft(original['id'])['body'], original['body'])
        self.select_paragraphs('굵게와 링크.')
        self.page.locator('#align-center').click()
        self.saved()
        library = self.personal()
        library.get_by_role('button', name='선택한 내용을 문구로', exact=True).click()
        form = self.page.get_by_role('dialog', name='새 항목 저장', exact=True)
        selected = form.get_by_label('내용 (Markdown)', exact=True).input_value()
        self.assertIn('<strong>굵게</strong>', selected)
        self.assertIn('https://example.invalid/template', selected)
        self.assertIn('text-align: center', selected)
        expect(form.get_by_role('combobox')).to_have_value('snippet')
        form.get_by_label('이름', exact=True).fill('선택한 서식 문구')
        form.get_by_role('button', name='저장', exact=True).click()
        library.get_by_role('button', name='닫기', exact=True).click()
        self.page.reload()
        library = self.personal()
        expect(library.locator('.library-row').filter(has_text='선택한 서식 문구')).to_be_visible()
        row = library.locator('.library-row').filter(has_text='수정한 글 틀')
        row.get_by_role('button', name='삭제', exact=True).click()
        self.page.locator('#confirm-cancel').click()
        expect(row).to_be_visible()
        row.get_by_role('button', name='삭제', exact=True).click()
        self.page.locator('#confirm-accept').click()
        expect(row).to_have_count(0)
        self.assertTrue((self.repo / '.blog-studio/drafts' / (original['id'] + '.json')).is_file())

    def test_revision_diff_includes_metadata_and_restore_keeps_history(self):
        before = self.new_post('변경 비교 이전 글')
        self.page.locator('#excerpt').fill('비교할 요약 변경')
        self.page.locator('.toastui-editor-ww-container [contenteditable=true]').fill('변경된 본문 내용입니다.')
        after = self.saved()
        tool(self.page, '#history-button').click()
        versions = self.http.studio.history(before['id'])['items']
        index = next(index for index, item in enumerate(versions) if item['version'] == before['version'])
        row = self.page.locator('#history-list .history-item').nth(index)
        row.get_by_role('button', name='변경 비교', exact=True).click()
        comparison = self.page.get_by_role('dialog', name='저장 기록 변경 비교', exact=True)
        expect(comparison.locator('.library-diff')).to_contain_text('+변경된 본문 내용입니다.')
        expect(comparison.locator('.library-diff')).to_contain_text('비교할 요약 변경')
        expect(comparison.locator('.library-diff .added').first).to_be_visible()
        self.assertEqual(self.active()['body'], after['body'])
        comparison.get_by_role('button', name='닫기', exact=True).click()
        row.get_by_role('button', name='복원', exact=True).click()
        self.page.locator('#confirm-accept').click()
        expect(self.page.locator('#history-dialog')).not_to_be_visible()
        restored = self.saved()
        self.assertEqual(restored['body'], before['body'])
        self.assertEqual(restored['excerpt'], before['excerpt'])
        self.assertGreater(restored['version'], after['version'])
        self.assertIn(after['version'], [item['version'] for item in self.http.studio.history(before['id'])['items']])

    def test_backup_download_and_conflict_import_keep_both_drafts_and_history(self):
        before = self.content('백업에 보관할 **원래 본문**입니다.')
        tool(self.page, '#backup-sync').click()
        dialog = self.page.get_by_role('dialog', name='백업과 초안 동기화', exact=True)
        with self.page.expect_download() as download:
            self.page.locator('#backup-download').click()
        path = Path(download.value.path())
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            self.assertIn('blog-studio-backup.json', names)
            self.assertIn(f'.blog-studio/drafts/{before["id"]}.json', names)
            self.assertTrue(any(name.startswith(f'.blog-studio/history/{before["id"]}/') for name in names))
            self.assertTrue(any(name.startswith('_posts/') for name in names))
        dialog.get_by_role('button', name='닫기', exact=True).click()
        self.page.locator('.toastui-editor-ww-container [contenteditable=true]').fill('이 컴퓨터에서 변경한 본문입니다.')
        current = self.saved()
        tool(self.page, '#backup-sync').click()
        dialog = self.page.get_by_role('dialog', name='백업과 초안 동기화', exact=True)
        dialog.locator('input[type=file]').set_input_files({'name': 'blog-studio-backup.zip', 'mimeType': 'application/zip', 'buffer': path.read_bytes()})
        self.page.locator('#confirm-accept').click()
        expect(dialog.locator('.library-status')).to_contain_text('가져왔습니다.', timeout=10000)
        self.assertEqual(self.http.studio.get_draft(before['id'])['body'], current['body'])
        copies = [json.loads(item.read_text('utf-8')) for item in (self.repo / '.blog-studio/drafts').glob('*.json')]
        copy = next(item for item in copies if item['id'] != before['id'] and item['body'] == before['body'])
        self.assertTrue(self.http.studio.history(copy['id'])['items'])
        dialog.get_by_role('button', name='닫기', exact=True).click()
        imported = self.page.locator(f'[data-draft-id="{copy["id"]}"] .post-item')
        imported.click()
        expect(imported).to_have_class('post-item active')
        self.assertEqual(self.saved()['body'], before['body'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
