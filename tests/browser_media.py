"""Real browser image/card checks using only disposable synthetic blog data."""
from __future__ import annotations

import base64
import re
import unittest

from playwright.sync_api import expect
import browser_smoke as smoke


class MediaTests(unittest.TestCase):
    setUpClass = classmethod(smoke.BrowserTests.setUpClass.__func__)
    tearDownClass = classmethod(smoke.BrowserTests.tearDownClass.__func__)
    setUp = smoke.BrowserTests.setUp
    tearDown = smoke.BrowserTests.tearDown
    active = smoke.BrowserTests.active
    saved = smoke.BrowserTests.saved
    new_post = smoke.BrowserTests.new_post

    def image_file(self):
        encoded = self.page.evaluate('''() => {
            const canvas = document.createElement('canvas'); canvas.width=400; canvas.height=200;
            const ctx=canvas.getContext('2d'); ctx.fillStyle='#ff0000'; ctx.fillRect(0,0,200,200);
            ctx.fillStyle='#0000ff'; ctx.fillRect(200,0,200,200);
            return canvas.toDataURL('image/png').split(',')[1];
        }''')
        return {'name': 'two-colors.png', 'mimeType': 'image/png', 'buffer': base64.b64decode(encoded)}

    def test_crop_resize_caption_survive_reload_and_edit(self):
        self.new_post('이미지 편집 검증')
        self.page.locator('#media-add-image').click()
        self.page.locator('#media-image-file').set_input_files(self.image_file())
        expect(self.page.locator('#media-image-apply')).to_be_enabled()
        for selector, value in [('media-crop-x', '200'), ('media-crop-y', '0'),
                                ('media-crop-width', '200'), ('media-crop-height', '200'),
                                ('media-output-width', '100')]:
            self.page.locator('#' + selector).fill(value)
        self.page.locator('#media-output-format').select_option('image/png')
        self.page.locator('#media-image-alt').fill('파란 정사각형')
        self.page.locator('#media-image-caption').fill('첫 번째 사진 설명')
        self.page.locator('#media-image-apply').click()
        expect(self.page.locator('#media-image-dialog')).not_to_be_visible()
        draft = self.saved()
        self.assertIn('첫 문장입니다.', draft['body'])
        self.assertIn('data-studio-caption="첫 번째 사진 설명"', draft['body'])
        self.assertIn('*첫 번째 사진 설명*', draft['body'])
        image = self.page.locator('.toastui-editor-ww-container img[src]').first
        original_url = image.get_attribute('src')
        expect(image).to_have_attribute('alt', '파란 정사각형')
        pixels = image.evaluate('''async img => { await img.decode(); const c=document.createElement('canvas');
            c.width=img.naturalWidth;c.height=img.naturalHeight;const ctx=c.getContext('2d');ctx.drawImage(img,0,0);
            return [c.width,c.height,...ctx.getImageData(25,25,1,1).data]; }''')
        self.assertEqual(pixels, [100, 100, 0, 0, 255, 255])
        self.page.reload()
        image = self.page.locator('.toastui-editor-ww-container img[src]').first
        image.click()
        expect(self.page.locator('#media-edit-image')).to_be_enabled()
        self.page.locator('#media-edit-image').click()
        expect(self.page.locator('#media-image-caption')).to_have_value('첫 번째 사진 설명')
        self.page.locator('#media-image-caption').fill('수정한 사진 설명')
        self.page.locator('#media-image-apply').click()
        expect(self.page.locator('#media-image-dialog')).not_to_be_visible()
        updated = self.saved()['body']
        self.assertNotIn('첫 번째 사진 설명', updated)
        self.assertIn('*수정한 사진 설명*', updated)
        self.assertEqual(updated.count('<img '), 1)
        self.assertEqual(self.page.locator('.toastui-editor-ww-container img[src]').first.get_attribute('src'), original_url,
                         'Caption-only edits must not recompress or upload the image again')
        self.assertIn('첫 문장입니다.', updated)

    def test_link_card_and_internal_link_persist(self):
        self.new_post('링크 카드 검증')
        self.page.route('**/api/link-preview', lambda route: route.fulfill(json={
            'url': 'https://example.invalid/paper', 'title': '읽어볼 논문',
            'description': '문맥을 설명하는 요약입니다.', 'image': self.base + '/assets/images/fixture.png',
        }))
        self.page.locator('#media-link-card').click()
        self.page.locator('#media-link-url').fill('https://example.invalid/paper')
        self.page.locator('#media-link-fetch').click()
        expect(self.page.locator('#media-link-label')).to_have_value('읽어볼 논문')
        self.page.locator('#media-insert-card').click()
        expect(self.page.locator('#media-link-dialog')).not_to_be_visible()
        saved = self.saved()['body']
        self.assertIn('data-studio-card="link"', saved)
        self.assertIn('https://example.invalid/paper', saved)
        self.assertIn('문맥을 설명하는 요약입니다.', saved)
        self.assertIn('첫 문장입니다.', saved)
        self.page.reload()
        card = self.page.locator('.toastui-editor-ww-container blockquote[data-studio-card=link]')
        expect(card).to_have_count(1)
        expect(card).to_contain_text('읽어볼 논문')
        self.page.locator('#media-link-card').click()
        self.page.locator('#media-internal-search').fill('기존 Markdown')
        self.page.locator('.media-internal-post').filter(has_text='기존 Markdown 예제').click()
        expect(self.page.locator('#media-link-url')).to_have_value(re.compile(r'^https?://'))
        self.page.locator('#media-insert-link').click()
        expect(self.page.locator('#media-link-dialog')).not_to_be_visible()
        self.assertIn('기존 Markdown 예제', self.saved()['body'])
        self.page.locator('#mode-markdown').click()
        expect(self.page.locator('#media-add-image')).to_be_disabled()
        expect(self.page.locator('#media-link-card')).to_be_disabled()

    def test_automatic_optimization_keeps_animation_and_small_images(self):
        self.new_post('자동 최적화 검증')
        result = self.page.evaluate('''async () => {
            const c=document.createElement('canvas');c.width=2400;c.height=1200;
            const ctx=c.getContext('2d');ctx.fillStyle='#4a87bf';ctx.fillRect(0,0,c.width,c.height);
            const blob=await new Promise(resolve=>c.toBlob(resolve,'image/png'));
            const file=new File([blob],'large.png',{type:'image/png'});
            const optimized=await StudioMedia.optimize(file);const decoded=await createImageBitmap(optimized);
            const size=[decoded.width,decoded.height,optimized.type];decoded.close();
            const gif=new File([new Uint8Array([71,73,70,56,57,97])],'animation.gif',{type:'image/gif'});
            const webpBytes=new Uint8Array(21);webpBytes.set(new TextEncoder().encode('VP8X'),12);webpBytes[20]=2;
            const webp=new File([webpBytes],'animation.webp',{type:'image/webp'});
            const pngBytes=new Uint8Array(20);pngBytes.set(new TextEncoder().encode('acTL'),12);
            const apng=new File([pngBytes],'animation.png',{type:'image/png'});
            c.width=64;c.height=32;
            const small=new File([await new Promise(resolve=>c.toBlob(resolve,'image/png'))],'small.png',{type:'image/png'});
            return {size,gifSame:await StudioMedia.optimize(gif)===gif,
                webpSame:await StudioMedia.optimize(webp)===webp,
                apngSame:await StudioMedia.optimize(apng)===apng,
                smallSame:await StudioMedia.optimize(small)===small};
        }''')
        self.assertEqual(result['size'], [1600, 800, 'image/webp'])
        self.assertTrue(result['gifSame'])
        self.assertTrue(result['webpSame'])
        self.assertTrue(result['apngSame'])
        self.assertTrue(result['smallSame'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
