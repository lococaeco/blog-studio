"""Open the same grouped menus a user opens before accessing a tool."""

from playwright.sync_api import expect

MENUS = {
    '#writing-color': 'format-menu-toggle',
    '#writing-highlight': 'format-menu-toggle',
    '#writing-clear-style': 'format-menu-toggle',
    '#insert-images': 'insert-menu-toggle',
    '#media-add-image': 'insert-menu-toggle',
    '#media-edit-image': 'insert-menu-toggle',
    '#media-link-card': 'insert-menu-toggle',
    '#writing-find': 'edit-menu-toggle',
    '#writing-outline': 'edit-menu-toggle',
    '#export-button': 'document-menu-toggle',
    '#history-button': 'document-menu-toggle',
    '#theme-preview': 'document-menu-toggle',
    '#personal-templates': 'workspace-menu-toggle',
    '#backup-sync': 'workspace-menu-toggle',
    '#cloud-open': 'workspace-menu-toggle',
}


def tool(page, selector):
    """Return a visible tool after a real pointer click on its menu button."""
    target = page.locator(selector)
    if not target.is_visible() and selector in MENUS:
        page.locator('#' + MENUS[selector]).click()
    expect(target).to_be_visible()
    return target
