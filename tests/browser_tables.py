"""Native visual table editing, export, history, and responsive controls."""
import unittest
from playwright.sync_api import expect
import browser_smoke as base
import browser_writing as writing


class TableToolsTests(unittest.TestCase):
    setUpClass = classmethod(base.BrowserTests.setUpClass.__func__)
    tearDownClass = classmethod(base.BrowserTests.tearDownClass.__func__)
    setUp = base.BrowserTests.setUp
    tearDown = base.BrowserTests.tearDown
    active = base.BrowserTests.active
    saved = base.BrowserTests.saved
    new_post = base.BrowserTests.new_post
    content = writing.WritingTests.content

    def table_fixture(self):
        self.content('앞 문단.\n\n| 첫 열 | 둘째 열 | 셋째 열 |\n| --- | --- | --- |\n| **강조** | [링크](https://example.invalid) | 셋 |\n| 넷 | 다섯 | 여섯 |\n\n뒤 문단.')
        return self.page.locator('.toastui-editor-ww-container table')

    def body_cell(self, text='강조'):
        return self.page.locator('.toastui-editor-ww-container td').filter(has_text=text).first

    def menu(self):
        self.page.locator('#table-actions-toggle').click()
        expect(self.page.locator('#table-actions-menu')).to_be_visible()

    def test_position_width_cell_alignment_roundtrip_and_undo(self):
        table = self.table_fixture()
        self.assertGreater(table.bounding_box()['width'], 500)
        expect(table.locator('th').first).not_to_have_css('background-color', 'rgb(85, 85, 85)')
        before_count = self.page.locator('#word-count').inner_text()
        self.body_cell().click()
        expect(self.page.locator('#table-tools')).to_be_visible()
        self.page.locator('#table-width').select_option('50')
        self.page.locator('#table-position').select_option('center')
        self.page.locator('#align-center').click()
        expect(self.body_cell()).to_have_css('text-align', 'center')
        expect(self.body_cell('링크')).not_to_have_css('text-align', 'center')
        expect(self.body_cell().locator('strong')).to_have_text('강조')
        body = self.saved()['body']
        self.assertIn('data-studio-table-align="center"', body)
        self.assertIn('width: 50%', body)
        self.assertIn('margin-left: auto', body)
        expect(self.page.locator('#word-count')).to_have_text(before_count)
        self.assertIn('<strong>강조</strong>', body)
        self.page.keyboard.press('Control+z')
        expect(self.body_cell()).not_to_have_css('text-align', 'center')
        self.page.keyboard.press('Control+Shift+Z')
        expect(self.body_cell()).to_have_css('text-align', 'center')
        for _ in range(3):
            self.page.locator('#mode-markdown').click()
            self.page.locator('#mode-visual').click()
            expect(table).to_have_count(1)
            expect(table).to_have_attribute('data-studio-table-width', '50')
            expect(table).to_have_attribute('data-studio-table-align', 'center')
            expect(self.body_cell()).to_have_css('text-align', 'center')
            expect(self.body_cell('링크').locator('a')).to_have_attribute('href', 'https://example.invalid')
        self.saved()
        self.page.reload()
        expect(table).to_have_attribute('data-studio-table-width', '50')
        expect(self.body_cell()).to_have_css('text-align', 'center')
        self.assertIn('뒤 문단.', self.saved()['body'])

    def test_row_column_actions_and_table_delete_are_undoable(self):
        table = self.table_fixture()
        self.body_cell('다섯').click()
        self.menu(); self.page.locator('#table-row-before').click()
        expect(table.locator('tr')).to_have_count(4)
        self.menu(); self.page.locator('#table-column-after').click()
        expect(table.locator('th')).to_have_count(4)
        expect(table.locator('td')).to_have_count(12)
        self.menu(); self.page.locator('#table-column-center').click()
        expect(table.locator('th').nth(1)).to_have_css('text-align', 'center')
        self.menu(); self.page.locator('#table-column-delete').click()
        expect(table.locator('th')).to_have_count(3)
        self.page.keyboard.press('Control+z')
        expect(table.locator('th')).to_have_count(4)
        self.menu(); self.page.locator('#table-remove').click()
        expect(table).to_have_count(0)
        self.page.keyboard.press('Control+z')
        expect(table).to_have_count(1)
        expect(table).to_contain_text('강조')
        self.saved()

    def test_resize_live_commit_roundtrip_keyboard_and_cancel(self):
        table = self.table_fixture()
        self.body_cell().click()
        handle = self.page.locator('#table-column-handles button').first
        expect(handle).to_be_visible()
        before = table.locator('th').first.bounding_box()['width']
        box = handle.bounding_box()
        self.page.mouse.move(box['x']+5, box['y']+8)
        self.page.mouse.down(); self.page.mouse.move(box['x']+55, box['y']+8, steps=8)
        self.assertGreater(table.locator('th').first.bounding_box()['width'], before+30)
        self.page.mouse.up()
        changed = table.locator('th').first.bounding_box()['width']
        self.assertIn('width:', self.saved()['body'])
        self.page.keyboard.press('Control+z')
        self.assertAlmostEqual(table.locator('th').first.bounding_box()['width'], before, delta=2)
        self.page.keyboard.press('Control+Shift+Z')
        self.assertAlmostEqual(table.locator('th').first.bounding_box()['width'], changed, delta=2)
        self.page.locator('#mode-markdown').click(); self.page.locator('#mode-visual').click()
        self.assertAlmostEqual(table.locator('th').first.bounding_box()['width'], changed, delta=3)
        self.body_cell().click(); expect(handle).to_be_visible(); handle.focus(); self.page.keyboard.press('ArrowLeft')
        self.assertLess(table.locator('th').first.bounding_box()['width'], changed-5)
        after = table.locator('th').first.bounding_box()['width']
        self.body_cell().click(); box = handle.bounding_box()
        self.page.mouse.move(box['x']+5, box['y']+8); self.page.mouse.down()
        self.page.mouse.move(box['x']+50, box['y']+8, steps=5); self.page.keyboard.press('Escape'); self.page.mouse.up()
        self.assertAlmostEqual(table.locator('th').first.bounding_box()['width'], after, delta=2)
        self.saved(); self.page.reload()
        self.assertAlmostEqual(table.locator('th').first.bounding_box()['width'], after, delta=3)

    def test_many_columns_scroll_mobile_and_widths_normalize_after_column_changes(self):
        titles=' | '.join('열 '+str(index) for index in range(10))
        row=' | '.join('내용 '+str(index) for index in range(10))
        self.content('앞 문단.\n\n| '+titles+' |\n| '+' | '.join(['---']*10)+' |\n| '+row+' |\n\n뒤 문단.')
        table=self.page.locator('.toastui-editor-ww-container table')
        self.body_cell('내용 0').click(); self.menu(); self.page.locator('#table-even-widths').click()
        self.menu(); self.page.locator('#table-column-after').click()
        expect(table.locator('th')).to_have_count(11)
        self.menu(); self.page.locator('#table-column-delete').click()
        expect(table.locator('th')).to_have_count(10)
        values=table.locator('th').evaluate_all('(cells)=>cells.map(cell=>parseFloat(cell.style.width))')
        self.assertAlmostEqual(sum(values),100,delta=.1)
        self.page.set_viewport_size({'width':390,'height':844})
        wrapper=self.page.locator('.studio-table-scroll')
        self.assertGreater(wrapper.evaluate('(node)=>node.scrollWidth'),wrapper.bounding_box()['width'])
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),390)
        wrapper.evaluate('(node)=>node.scrollLeft=node.scrollWidth')
        expect(self.body_cell('내용 9')).to_be_visible()
        self.saved(); self.page.reload()
        expect(table.locator('th')).to_have_count(10)
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),390)

    def test_context_menu_mobile_bounds_and_header_constraints(self):
        table = self.table_fixture()
        table.locator('th').first.click(button='right')
        expect(self.page.locator('#table-actions-menu')).to_be_visible()
        expect(self.page.locator('#table-row-before')).to_be_disabled()
        expect(self.page.locator('#table-column-after')).to_be_enabled()
        expect(self.page.locator('#table-row-after')).to_be_enabled()
        self.page.locator('#table-row-after').click()
        expect(table.locator('tr')).to_have_count(4)
        expect(table.locator('th')).to_have_count(3)
        self.body_cell('다섯').click(button='right')
        self.page.keyboard.press('Escape')
        expect(self.page.locator('#table-actions-menu')).not_to_be_visible()
        self.page.set_viewport_size({'width':390,'height':844})
        self.body_cell('다섯').click(button='right')
        panel = self.page.locator('#table-actions-menu'); expect(panel).to_be_visible()
        box=panel.bounding_box()
        self.assertGreaterEqual(box['x'], 0); self.assertLessEqual(box['x']+box['width'],390)
        self.assertGreaterEqual(box['y'], 0); self.assertLessEqual(box['y']+box['height'],844)
        self.assertLessEqual(self.page.evaluate('document.documentElement.scrollWidth'),390)
        self.page.keyboard.press('Escape')
        self.page.locator('#title').click()
        expect(self.page.locator('#table-tools')).not_to_be_visible()
        expect(self.page.locator('#table-column-handles')).not_to_be_visible()

if __name__ == '__main__': unittest.main()
