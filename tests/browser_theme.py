"""Actual preview iframe/server with local webfonts and parent-origin isolation."""
from pathlib import Path
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from playwright.sync_api import expect
from browser_toolbar_support import tool
import yaml

import browser_smoke as base


class ThemeBrowserTests(unittest.TestCase):
    setUpClass = classmethod(base.BrowserTests.setUpClass.__func__)
    tearDownClass = classmethod(base.BrowserTests.tearDownClass.__func__)
    setUp = base.BrowserTests.setUp
    tearDown = base.BrowserTests.tearDown
    active = base.BrowserTests.active
    saved = base.BrowserTests.saved
    new_post = base.BrowserTests.new_post

    def test_preview_font_loads_while_parent_document_stays_isolated(self):
        self.new_post('별도 테마 미리보기')
        fonts = []
        self.page.on('response', lambda response: fonts.append(response) if response.url.endswith('/fixture.woff2') else None)

        def compile_fixture(command, **kwargs):
            source = Path(kwargs['cwd'])
            output = Path(command[command.index('--destination') + 1])
            prefix = yaml.safe_load((source / '_config.yml').read_text('utf-8'))['baseurl']
            (output / '__studio_preview__').mkdir(parents=True)
            (output / 'assets').mkdir()
            shutil.copy2(base.APP / 'static/vendor/katex/fonts/KaTeX_Main-Regular.woff2', output / 'assets/fixture.woff2')
            (output / '__studio_preview__/index.html').write_text('''<!doctype html><meta charset="utf-8">
                <style>@font-face{font-family:PreviewFont;src:url("''' + prefix + '''/assets/fixture.woff2") format("woff2")}
                .themed{font-family:PreviewFont;font-size:18px}</style><p class="themed">Theme preview 123</p>
                <script>
                try { parent.document.body.dataset.previewAccess = 'unsafe'; document.body.dataset.parentBlocked='false'; }
                catch(error) { document.body.dataset.parentBlocked=String(error.name === 'SecurityError'); }
                document.fonts.ready.then(()=>document.body.dataset.fontLoaded=String(document.fonts.check('18px PreviewFont')));
                </script>''', 'utf-8')
            return SimpleNamespace(returncode=0, stdout='', stderr='')

        with patch('theme_preview.shutil.which', return_value='/fixture/jekyll'), \
                patch('theme_preview.run_external', side_effect=compile_fixture):
            tool(self.page, '#theme-preview').click()
            dialog = self.page.get_by_role('dialog', name='실제 블로그 미리보기', exact=True)
            self.page.locator('#theme-preview-build').click()
            iframe = dialog.locator('iframe')
            expect(iframe).to_be_visible(timeout=15000)
            self.assertNotEqual(urlsplit(iframe.get_attribute('src')).netloc, urlsplit(self.base).netloc)
            frame = dialog.frame_locator('iframe')
            expect(frame.locator('.themed')).to_have_text('Theme preview 123')
            expect(frame.locator('body')).to_have_attribute('data-font-loaded', 'true')
            expect(frame.locator('body')).to_have_attribute('data-parent-blocked', 'true')
            self.assertIsNone(self.page.locator('body').get_attribute('data-preview-access'))
            self.assertEqual(len(fonts), 1)
            self.assertEqual(fonts[0].status, 200)
            self.assertNotIn('access-control-allow-origin', fonts[0].headers)


if __name__ == '__main__':
    unittest.main(verbosity=2)
