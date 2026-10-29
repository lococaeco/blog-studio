"""Native pointer/keyboard image controls against disposable draft fixtures."""
import unittest

from playwright.sync_api import expect
import browser_smoke as smoke
import browser_media as media
import browser_writing as writing


class ImageInteractionTests(unittest.TestCase):
    setUpClass = classmethod(smoke.BrowserTests.setUpClass.__func__)
    tearDownClass = classmethod(smoke.BrowserTests.tearDownClass.__func__)
    setUp = smoke.BrowserTests.setUp
    tearDown = smoke.BrowserTests.tearDown
    active = smoke.BrowserTests.active
    saved = smoke.BrowserTests.saved
    new_post = smoke.BrowserTests.new_post
    image_file = media.MediaTests.image_file

    def image(self):
        return self.page.locator('.toastui-editor-ww-container img[src]').first

    def add_image(self):
        self.new_post('이미지 직접 조작 검증')
        self.page.locator('#image-files').set_input_files(self.image_file())
        image = self.image()
        expect(image).to_be_visible()
        image.evaluate('img => img.decode()')
        self.saved()
        image.click()
        expect(self.page.locator('#media-image-bar')).to_be_visible()
        return image

    def right_click(self):
        self.image().click(button='right')
        expect(self.page.locator('#media-image-context')).to_be_visible()

    def test_drag_live_resize_is_one_undo_step_and_survives_modes_reload(self):
        image = self.add_image()
        original = image.bounding_box()
        handle = self.page.locator('#media-resize-se').bounding_box()
        self.page.mouse.move(handle['x'] + handle['width'] / 2, handle['y'] + handle['height'] / 2)
        self.page.mouse.down()
        self.page.mouse.move(handle['x'] + handle['width'] / 2 + 70, handle['y'] + handle['height'] / 2 + 35, steps=8)
        expect(image).to_have_attribute('width', '470')
        self.assertAlmostEqual(image.bounding_box()['height'], 235, delta=1)
        self.page.mouse.up()
        self.saved()
        self.page.locator('.toastui-editor-ww-container [contenteditable=true]').focus()
        self.page.keyboard.press('Control+z')
        self.assertAlmostEqual(image.bounding_box()['width'], original['width'], delta=1)
        self.page.keyboard.press('Control+Shift+Z')
        expect(image).to_have_attribute('width', '470')
        body = self.saved()['body']
        self.assertIn('width="470"', body)
        self.assertNotIn('media-image-outline', body)
        self.page.locator('#mode-markdown').click()
        self.page.locator('#mode-visual').click()
        expect(self.image()).to_have_attribute('width', '470')
        self.page.reload()
        expect(self.image()).to_have_attribute('width', '470')
        self.assertIn('첫 문장입니다.', self.active()['body'])

    def test_context_alignment_caption_reset_delete_and_undo(self):
        image = self.add_image()
        self.page.locator('#media-display-width').fill('220')
        self.page.locator('#media-display-width').press('Enter')
        expect(image).to_have_attribute('width', '220')
        self.right_click()
        self.page.locator('#media-image-align-center').click()
        expect(image).to_have_attribute('data-studio-image-align', 'center')
        box = image.bounding_box()
        parent = image.evaluate('img => { const r=img.parentElement.getBoundingClientRect(); return {x:r.x,width:r.width}; }')
        self.assertAlmostEqual(box['x'] + box['width'] / 2, parent['x'] + parent['width'] / 2, delta=1)
        self.right_click()
        self.page.locator('#media-context-caption').click()
        self.page.locator('#media-details-alt').fill('파랑과 빨강 사진')
        self.page.locator('#media-details-caption').fill('사진 아래 설명')
        self.page.locator('#media-details-apply').click()
        expect(image).to_have_attribute('alt', '파랑과 빨강 사진')
        expect(self.page.locator('.toastui-editor-ww-container')).to_contain_text('사진 아래 설명')
        self.saved()
        self.page.reload()
        expect(self.image()).to_have_attribute('width', '220')
        expect(self.image()).to_have_attribute('data-studio-image-align', 'center')
        self.right_click()
        self.page.locator('#media-image-reset-size').click()
        expect(self.image()).not_to_have_attribute('width', '220')
        self.assertAlmostEqual(self.image().bounding_box()['width'], 400, delta=1)
        self.right_click()
        self.page.locator('#media-image-delete').click()
        expect(self.image()).to_have_count(0)
        expect(self.page.locator('.toastui-editor-ww-container')).not_to_contain_text('사진 아래 설명')
        self.page.keyboard.press('Control+z')
        expect(self.image()).to_have_count(1)
        expect(self.page.locator('.toastui-editor-ww-container')).to_contain_text('사진 아래 설명')
        expect(self.image()).to_have_attribute('alt', '파랑과 빨강 사진')

    def test_crop_context_keeps_original_upload_and_size(self):
        image = self.add_image()
        original_url = image.get_attribute('src')
        self.page.locator('#media-display-width').fill('200')
        self.page.locator('#media-display-width').press('Enter')
        self.right_click()
        self.page.locator('#media-context-crop').click()
        expect(self.page.locator('#media-image-apply')).to_be_enabled()
        self.page.locator('#media-crop-x').fill('200')
        self.page.locator('#media-crop-width').fill('200')
        self.page.locator('#media-output-width').fill('200')
        self.page.locator('#media-output-format').select_option('image/png')
        self.page.locator('#media-image-apply').click()
        expect(self.page.locator('#media-image-dialog')).not_to_be_visible()
        expect(self.image()).to_have_attribute('width', '200')
        self.image().evaluate('img => img.decode()')
        self.assertNotEqual(self.image().get_attribute('src'), original_url)
        pixels = self.image().evaluate('''img => {
            const c=document.createElement('canvas');c.width=img.naturalWidth;c.height=img.naturalHeight;
            const ctx=c.getContext('2d');ctx.drawImage(img,0,0);return [c.width,c.height,...ctx.getImageData(10,10,1,1).data];
        }''')
        self.assertEqual(pixels, [200, 200, 0, 0, 255, 255])
        self.assertEqual(self.page.request.get(self.base + original_url).status, 200)
        self.saved()

    def test_mobile_controls_fit_keyboard_resize(self):
        self.add_image()
        self.page.set_viewport_size({'width': 390, 'height': 844})
        self.image().scroll_into_view_if_needed()
        self.image().click()
        expect(self.page.locator('#media-image-bar')).to_be_visible()
        bar = self.page.locator('#media-image-bar').bounding_box()
        self.assertGreaterEqual(bar['x'], 0)
        self.assertLessEqual(bar['x'] + bar['width'], 390)
        self.page.locator('#media-display-width').fill('160')
        self.page.locator('#media-display-width').press('Enter')
        self.page.locator('#media-resize-e').focus()
        self.page.keyboard.press('ArrowRight')
        expect(self.image()).to_have_attribute('width', '170')
        self.page.keyboard.press('Shift+F10')
        expect(self.page.locator('#media-image-context')).to_be_visible()
        menu = self.page.locator('#media-image-context').bounding_box()
        self.assertGreaterEqual(menu['x'], 0)
        self.assertLessEqual(menu['x'] + menu['width'], 390)
        self.assertGreaterEqual(menu['y'], 0)
        self.assertLessEqual(menu['y'] + menu['height'], 844)
        self.page.keyboard.press('Escape')
        expect(self.page.locator('#media-image-context')).not_to_be_visible()
        self.assertEqual(self.page.evaluate('document.documentElement.scrollWidth'), 390)

    def test_inline_image_layout_preserves_bold_link_and_repeated_mode_roundtrips(self):
        self.add_image()
        image_path = self.image().get_attribute('src')
        self.page.locator('#mode-markdown').click()
        body = '앞쪽 **굵은 글자**와 [참고 링크](https://example.invalid/read) ![삽입 사진](' + image_path + ') 뒤쪽 내용.\n\n다음 문단 유지.'
        self.page.locator('.toastui-editor-md-container [contenteditable=true]').fill(body)
        self.page.locator('#mode-visual').click()
        self.image().click()
        expect(self.page.locator('#media-image-bar')).to_be_visible()
        self.page.locator('#media-display-width').fill('240')
        self.page.locator('#media-display-width').press('Enter')
        self.right_click()
        self.page.locator('#media-image-align-center').click()
        for _ in range(3):
            self.page.locator('#mode-markdown').click()
            self.page.locator('#mode-visual').click()
            expect(self.image()).to_have_attribute('width', '240')
            expect(self.image()).to_have_attribute('data-studio-image-align', 'center')
            expect(self.page.locator('.toastui-editor-ww-container strong')).to_have_text('굵은 글자')
            expect(self.page.locator('.toastui-editor-ww-container a')).to_have_attribute('href', 'https://example.invalid/read')
            expect(self.page.locator('.toastui-editor-ww-container')).to_contain_text('뒤쪽 내용.')
        saved = self.saved()['body']
        self.assertIn('<strong>굵은 글자</strong>', saved)
        self.assertEqual(saved.count('<img '), 1)
        self.assertIn('다음 문단 유지.', saved)
        self.page.reload()
        expect(self.image()).to_have_attribute('width', '240')
        expect(self.page.locator('.toastui-editor-ww-container strong')).to_have_text('굵은 글자')

    def test_linked_remote_image_metadata_needs_no_cross_origin_file_read(self):
        image_file = self.image_file()
        remote = 'https://example.invalid/photo.png'
        file_reads = []
        self.page.on('request', lambda request: file_reads.append(request.url) if request.url == remote and request.resource_type in ('fetch', 'xhr') else None)
        self.page.route(remote, lambda route: route.fulfill(status=200, content_type='image/png', body=image_file['buffer']))
        self.new_post('외부 링크 이미지 설명 검증')
        self.page.locator('#mode-markdown').click()
        self.page.locator('.toastui-editor-md-container [contenteditable=true]').fill('[![외부 사진](' + remote + ')](https://example.invalid/gallery)')
        self.page.locator('#mode-visual').click()
        self.image().evaluate('img => img.decode()')
        self.image().click()
        expect(self.page.locator('#media-image-bar')).to_be_visible()
        self.page.locator('#media-display-width').fill('500')
        self.page.locator('#media-display-width').press('Enter')
        expect(self.image()).to_have_attribute('width', '500')
        self.right_click()
        self.page.locator('#media-context-caption').click()
        self.page.locator('#media-details-alt').fill('접근 가능한 사진 설명')
        self.page.locator('#media-details-apply').click()
        expect(self.image()).to_have_attribute('alt', '접근 가능한 사진 설명')
        self.saved()
        self.page.reload()
        expect(self.image()).to_have_attribute('width', '500')
        expect(self.image()).to_have_attribute('alt', '접근 가능한 사진 설명')
        expect(self.page.locator('.toastui-editor-ww-container a')).to_have_attribute('href', 'https://example.invalid/gallery')
        self.assertEqual(file_reads, [], 'Metadata edits must not need a cross-origin fetch or recompression')

    def test_image_inside_table_handoffs_and_roundtrip_preserve_all_cells(self):
        self.add_image()
        src = self.image().get_attribute('src')
        self.page.locator('#mode-markdown').click()
        self.page.locator('.toastui-editor-md-container [contenteditable=true]').fill('선택할 텍스트.\n\n| 사진 | 내용 |\n| --- | --- |\n| ![사진](' + src + ') | 셀 텍스트 |')
        self.page.locator('#mode-visual').click()
        writing.WritingTests.select_text(self, '선택할 텍스트')
        expect(self.page.locator('#text-selection-tools')).to_be_visible()
        self.image().click()
        expect(self.page.locator('#text-selection-tools')).not_to_be_visible()
        expect(self.page.locator('#media-image-bar')).to_be_visible()
        expect(self.page.locator('#table-column-handles')).not_to_be_visible()
        self.right_click()
        expect(self.page.locator('#table-actions-menu')).not_to_be_visible()
        self.page.locator('#media-image-align-center').click()
        self.image().click()
        self.page.locator('#media-display-width').fill('120')
        self.page.locator('#media-display-width').press('Enter')
        self.right_click()
        self.page.locator('#media-context-caption').click()
        self.page.locator('#media-details-alt').fill('표 안의 사진 설명')
        self.page.locator('#media-details-caption').fill('셀 안의 캡션')
        self.page.locator('#media-details-apply').click()
        for _ in range(2):
            self.saved()
            self.page.locator('#mode-markdown').click()
            self.page.locator('#mode-visual').click()
            expect(self.image()).to_have_attribute('width', '120')
            expect(self.image()).to_have_attribute('data-studio-image-align', 'center')
            expect(self.page.locator('.toastui-editor-ww-container td')).to_have_count(2)
            expect(self.page.locator('.toastui-editor-ww-container td').first).to_contain_text('셀 안의 캡션')
            expect(self.image()).to_have_attribute('alt', '표 안의 사진 설명')
            expect(self.page.locator('.toastui-editor-ww-container td').last).to_have_text('셀 텍스트')
        self.saved()
        self.page.reload()
        expect(self.image()).to_have_attribute('width', '120')
        self.image().click()
        self.page.locator('#media-image-crop').click()
        expect(self.page.locator('#media-image-dialog')).to_be_visible()
        expect(self.page.locator('#media-image-bar')).not_to_be_visible()
        expect(self.page.locator('#media-image-outline')).not_to_be_visible()

    def test_nested_image_sizes_keep_heading_list_quote_and_outside_paragraph(self):
        self.add_image()
        src = self.image().get_attribute('src')
        for prefix, selector in [('## 제목 ', 'h2'), ('- 목록 사진 ', 'ul'), ('3. 순서 사진 ', 'ol'), ('- [x] 할 일 사진 ', 'ul'), ('> 인용 사진 ', 'blockquote')]:
            with self.subTest(prefix=prefix):
                self.page.locator('#mode-markdown').click()
                self.page.locator('.toastui-editor-md-container [contenteditable=true]').fill(prefix + '![사진](' + src + ') 뒤쪽 유지.\n\n일반문단.')
                self.page.locator('#mode-visual').click()
                self.image().click()
                expect(self.page.locator('#media-image-bar')).to_be_visible()
                self.page.locator('#media-display-width').fill('140')
                self.page.locator('#media-display-width').press('Enter')
                for _ in range(2):
                    self.saved()
                    self.page.locator('#mode-markdown').click()
                    self.page.locator('#mode-visual').click()
                    expect(self.image()).to_have_attribute('width', '140')
                    expect(self.page.locator('.toastui-editor-ww-container ' + selector)).to_contain_text('뒤쪽 유지.')
                    expect(self.page.locator('.toastui-editor-ww-container [contenteditable=true] > p').filter(has_text='일반문단.')).to_have_count(1)
                if prefix.startswith('3.'):
                    expect(self.page.locator('.toastui-editor-ww-container ol')).to_have_attribute('start', '3')
                if '[x]' in prefix:
                    expect(self.page.locator('.toastui-editor-ww-container li')).to_have_class('task-list-item checked')

    def test_resizing_image_at_end_of_long_document_keeps_viewport_near_image(self):
        self.add_image()
        src = self.image().get_attribute('src')
        self.page.locator('#mode-markdown').click()
        self.page.locator('.toastui-editor-md-container [contenteditable=true]').fill(('긴 글의 문단입니다.\n\n' * 50) + '![사진](' + src + ')')
        self.page.locator('#mode-visual').click()
        self.page.locator('.toastui-editor-ww-container p').first.click()
        self.image().scroll_into_view_if_needed()
        self.image().click()
        expect(self.page.locator('#media-image-bar')).to_be_visible()
        before = self.page.evaluate('scrollY')
        self.page.locator('#media-display-width').fill('200')
        self.page.locator('#media-display-width').press('Enter')
        expect(self.image()).to_have_attribute('width', '200')
        after = self.page.evaluate('scrollY')
        self.assertLess(abs(after - before), 200, 'Image editing must not jump to an old text caret near the document top')
        self.assertLess(self.image().bounding_box()['y'], 1000)
