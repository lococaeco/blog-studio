/* Selection-first text tools, sharing the editor's native marks and history. */
(() => {
  'use strict';
  const sessions = new WeakMap();
  const $ = id => document.getElementById(id);
  const colors = [
    ['먹색', '#202826'], ['회색', '#66716c'], ['갈색', '#996633'], ['빨강', '#c62828'],
    ['주황', '#c65b16'], ['노랑', '#aa7900'], ['초록', '#187b71'], ['파랑', '#2463be'],
    ['보라', '#8150b0'], ['분홍', '#bd4074'],
  ];
  const highlights = [
    ['연회색', '#edf0ee'], ['연갈색', '#eee0cd'], ['연빨강', '#ffdddd'], ['연주황', '#ffe6c8'],
    ['연노랑', '#fff0a3'], ['연초록', '#d6efe3'], ['연파랑', '#dbeaff'], ['연보라', '#ebddfa'],
    ['연분홍', '#f9ddec'], ['흰색', '#ffffff'],
  ];
  function element(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text) node.textContent = text;
    return node;
  }
  function queue(session) {
    if (session.queued) return;
    session.queued = true;
    requestAnimationFrame(() => { session.queued = false; session.refresh?.(); });
  }
  function plugin(context) {
    const session = { editor: context.instance, context, view: null, dismissed: null, dragging: false };
    sessions.set(context.instance, session);
    return { wysiwygPlugins: [() => new context.pmState.Plugin({
      view(view) {
        session.view = view;
        return { update() { queue(session); }, destroy() { session.view = null; session.root?.remove(); } };
      },
    })] };
  }
  function selectionKey(session) {
    const { from, to } = session.view.state.selection;
    return from + ':' + to;
  }
  function supported(session) {
    if (!session.editor.isWysiwygMode() || !session.view || session.view.state.selection.empty) return false;
    const { doc, selection } = session.view.state;
    if (selection.constructor !== session.context.pmState.TextSelection || !doc.textBetween(selection.from, selection.to, '').trim()) return false;
    let valid = true;
    doc.nodesBetween(selection.from, selection.to, node => {
      if (['codeBlock', 'customBlock'].includes(node.type.name)) valid = false;
    });
    return valid;
  }
  function hide(session, dismiss = false) {
    if (dismiss && session.view) { session.dismissed = selectionKey(session); session.dismissedDoc = session.view.state.doc; }
    if (session.root) session.root.hidden = true;
    session.palette = null;
    if (session.palettePanel) session.palettePanel.hidden = true;
    for (const button of session.paletteButtons || []) button.setAttribute('aria-expanded', 'false');
  }
  function restore(session) {
    if ($('document').inert || $('document').hidden || document.querySelector('dialog[open]')) return false;
    if (!session.bookmark || session.document !== session.view?.state.doc) return false;
    const view = session.view;
    view.dispatch(view.state.tr.setSelection(session.bookmark.resolve(view.state.doc)).setMeta('addToHistory', false));
    return supported(session);
  }
  function updateBookmark(session) {
    session.bookmark = session.view.state.selection.getBookmark();
    session.document = session.view.state.doc;
  }
  function mutate(session, callback) {
    if (!restore(session)) return hide(session);
    try {
      window.StudioToolbar?.closeMenus();
      callback();
      updateBookmark(session);
      session.dismissed = null;
      session.view.focus();
      queue(session);
    } catch (error) { session.notify(error.message || '서식을 적용하지 못했어요.', true); }
  }
  function markAction(session, names) {
    const view = session.view, { state } = view;
    let tr = state.tr;
    for (const name of names) if (state.schema.marks[name]) tr = tr.removeMark(state.selection.from, state.selection.to, state.schema.marks[name]);
    if (tr.docChanged) {
      view.dispatch(tr.setMeta('closeHistory$', true));
      view.dispatch(view.state.tr.setMeta('closeHistory$', true));
    }
  }
  function removeColor(session, property) {
    const view = session.view, { state } = view, type = state.schema.marks.span;
    const tr = state.tr;
    state.doc.nodesBetween(state.selection.from, state.selection.to, (node, position) => {
      if (!node.isText) return;
      const existing = node.marks.find(mark => mark.type === type);
      if (!existing) return;
      const span = document.createElement('span');
      span.setAttribute('style', existing.attrs.htmlAttrs?.style || '');
      span.style.removeProperty(property);
      const attrs = { ...existing.attrs.htmlAttrs };
      if (span.style.length) attrs.style = span.getAttribute('style'); else delete attrs.style;
      const from = Math.max(position, state.selection.from), to = Math.min(position + node.nodeSize, state.selection.to);
      tr.removeMark(from, to, type);
      if (Object.keys(attrs).length) tr.addMark(from, to, type.create({ ...existing.attrs, htmlAttrs: attrs }));
    });
    if (tr.docChanged) {
      view.dispatch(tr.setMeta('closeHistory$', true));
      view.dispatch(view.state.tr.setMeta('closeHistory$', true));
    }
  }
  function position(session) {
    const { view, root } = session;
    if (!view || root.hidden) return;
    let start, end;
    try {
      start = view.coordsAtPos(view.state.selection.from);
      end = view.coordsAtPos(view.state.selection.to);
    } catch (_) { return hide(session); }
    const dock = $('editor-dock').getBoundingClientRect();
    const safeTop = Math.max(8, dock.bottom + 8);
    // Keep the tools beside the selected text without covering the sticky dock.
    // If the selected text has left the viewport, it should not leave a ghost menu.
    if (end.bottom < safeTop || start.top > innerHeight - 12) return hide(session);
    const box = root.getBoundingClientRect();
    let top = start.top - box.height - 10;
    if (top < safeTop) top = Math.max(safeTop, end.bottom + 10);
    if (top + box.height > innerHeight - 8) top = Math.max(safeTop, innerHeight - box.height - 8);
    const middle = start.top === end.top ? (start.left + end.right) / 2 : start.left + 90;
    root.style.left = Math.max(8, Math.min(middle - box.width / 2, innerWidth - box.width - 8)) + 'px';
    root.style.top = top + 'px';
  }
  function makeButton(session, id, label, text, action) {
    const button = element('button', 'text-tool-button', text);
    button.id = id; button.type = 'button'; button.title = label; button.setAttribute('aria-label', label);
    button.addEventListener('mousedown', event => event.preventDefault());
    button.onclick = action;
    session.row.append(button);
    return button;
  }
  function linkIcon(button, broken = false) {
    const ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24'); svg.setAttribute('aria-hidden', 'true');
    const path = document.createElementNS(ns, 'path');
    path.setAttribute('d', broken ? 'm9 15-2 2a3.5 3.5 0 0 1-5-5l4-4a3.5 3.5 0 0 1 4-.7M15 9l2-2a3.5 3.5 0 0 1 5 5l-4 4a3.5 3.5 0 0 1-4 .7M3 3l18 18' : 'm9 15 6-6m-5-2a3.5 3.5 0 0 0-5 0l-3 3a3.5 3.5 0 0 0 5 5l2-2m5 4a3.5 3.5 0 0 0 5 0l3-3a3.5 3.5 0 0 0-5-5l-2 2');
    svg.append(path); button.replaceChildren(svg);
  }
  function openPalette(session, property) {
    if (!restore(session)) return hide(session);
    const wasOpen = session.palette === property;
    session.palette = wasOpen ? null : property;
    for (const button of session.paletteButtons) button.setAttribute('aria-expanded', String(!wasOpen && button.dataset.property === property));
    session.palettePanel.hidden = wasOpen;
    if (wasOpen) return position(session);
    const title = property === 'color' ? '글자 색' : '배경 색';
    session.palettePanel.replaceChildren();
    const heading = element('div', 'text-palette-heading', title);
    const reset = element('button', 'text-color-reset', '기본으로'); reset.type = 'button'; reset.id = 'text-color-reset';
    reset.onmousedown = event => event.preventDefault();
    reset.onclick = () => mutate(session, () => removeColor(session, property));
    heading.append(reset); session.palettePanel.append(heading);
    const grid = element('div', 'text-color-grid'); grid.setAttribute('role', 'group'); grid.setAttribute('aria-label', title + ' 팔레트');
    for (const [label, color] of property === 'color' ? colors : highlights) {
      const button = element('button', 'text-color-swatch'); button.type = 'button';
      button.dataset.color = color; button.style.setProperty('--swatch', color);
      button.setAttribute('aria-label', title + ' ' + label); button.title = label;
      button.onmousedown = event => event.preventDefault();
      button.onclick = () => mutate(session, () => window.StudioWriting.applyStyle(session.editor, property, color));
      grid.append(button);
    }
    const form = element('form', 'text-color-custom');
    const label = element('label', '', '직접 지정'); label.htmlFor = 'text-color-value';
    const input = element('input'); input.id = 'text-color-value'; input.type = 'text'; input.maxLength = 7;
    input.value = property === 'color' ? '#187b71' : '#fff0a3'; input.pattern = '#[0-9a-fA-F]{6}';
    input.required = true; input.spellcheck = false; input.setAttribute('aria-label', title + ' 코드'); input.title = '#으로 시작하는 여섯 자리 색상 코드';
    const apply = element('button', '', '적용'); apply.type = 'submit'; apply.id = 'text-color-apply';
    form.append(label, input, apply);
    form.onsubmit = event => { event.preventDefault(); if (form.reportValidity()) mutate(session, () => window.StudioWriting.applyStyle(session.editor, property, input.value)); };
    session.palettePanel.append(grid, form); position(session);
  }
  function install({ editor, notify }) {
    const session = sessions.get(editor);
    if (!session || session.root) return;
    session.notify = notify;
    const root = element('div', 'text-selection-tools'); root.id = 'text-selection-tools'; root.hidden = true;
    root.dataset.studioMenu = '';
    root.setAttribute('role', 'region'); root.setAttribute('aria-label', '선택한 글자 서식');
    root.setAttribute('aria-keyshortcuts', 'Alt+F10');
    const row = element('div', 'text-selection-row'); row.setAttribute('role', 'toolbar'); row.setAttribute('aria-label', '선택한 글자 꾸미기');
    const palettePanel = element('div', 'text-palette'); palettePanel.id = 'text-color-palette'; palettePanel.hidden = true;
    root.append(row, palettePanel); $('document').append(root);
    Object.assign(session, { root, row, palettePanel, paletteButtons: [] });
    const marks = [['bold', '굵게', 'B', 'strong'], ['italic', '기울임', 'I', 'emph'], ['underline', '밑줄', 'U', 'u'],
      ['strike', '취소선', 'S', 'strike'], ['code', '인라인 코드', '</>', 'code']];
    for (const [name, label, text, mark] of marks) {
      const button = makeButton(session, 'text-' + name, label, text, () => mutate(session, () => name === 'underline' ? window.StudioWriting.underline(editor) : editor.exec(name)));
      button.dataset.mark = mark; button.setAttribute('aria-pressed', 'false');
    }
    for (const [name, property, label, text] of [['color', 'color', '글자 색', 'A'], ['highlight', 'background-color', '배경 색', '가']]) {
      const button = makeButton(session, 'text-' + name, label, text, () => openPalette(session, property));
      button.dataset.property = property; button.setAttribute('aria-expanded', 'false'); button.setAttribute('aria-controls', palettePanel.id);
      session.paletteButtons.push(button);
    }
    const link = makeButton(session, 'text-link', '하이퍼링크', '', () => {
      if (!restore(session)) return hide(session);
      hide(session, true); $('toolbar-link').click();
    });
    const unlink = makeButton(session, 'text-unlink', '링크 해제', '', () => mutate(session, () => markAction(session, ['link'])));
    linkIcon(link); linkIcon(unlink, true);
    makeButton(session, 'text-clear', '글자 서식 지우기', 'T×', () => mutate(session, () => markAction(session, ['span', 'u', 'strong', 'emph', 'strike', 'code'])));
    root.addEventListener('pointerdown', event => event.stopPropagation());
    root.addEventListener('keydown', event => {
      if (event.key === 'Escape') {
        event.preventDefault(); event.stopPropagation();
        if (session.palette) { openPalette(session, session.palette); session.paletteButtons[0].focus(); }
        else { hide(session, true); session.view.focus(); }
      }
      if (['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key) && event.target.matches('button')) {
        const buttons = [...root.querySelectorAll('button:not(:disabled)')].filter(node => node.getClientRects().length);
        let index = buttons.indexOf(document.activeElement);
        if (event.key === 'Home') index = 0;
        else if (event.key === 'End') index = buttons.length - 1;
        else index = (index + (event.key === 'ArrowLeft' ? -1 : 1) + buttons.length) % buttons.length;
        event.preventDefault(); buttons[index].focus({ preventScroll: true });
      }
    });
    session.refresh = () => {
      const view = session.view, selection = getSelection();
      const inside = !!(selection?.anchorNode && selection.focusNode && view?.dom.contains(selection.anchorNode) && view.dom.contains(selection.focusNode));
      const controlFocused = root.contains(document.activeElement);
      const busy = $('document').hidden || $('document').inert || !!document.querySelector('dialog[open]');
      if (busy || session.dragging || !supported(session) || (session.dismissed === selectionKey(session) && session.dismissedDoc === view.state.doc) || (!inside && !controlFocused)) return hide(session);
      const { doc, schema, selection: range } = view.state;
      const key = selectionKey(session);
      if (session.range && session.range !== key) {
        session.palette = null; palettePanel.hidden = true;
        session.paletteButtons.forEach(button => button.setAttribute('aria-expanded', 'false'));
      }
      session.range = key;
      updateBookmark(session);
      for (const button of row.querySelectorAll('[data-mark]')) {
        const mark = schema.marks[button.dataset.mark];
        button.setAttribute('aria-pressed', String(!!mark && doc.rangeHasMark(range.from, range.to, mark)));
      }
      unlink.disabled = !schema.marks.link || !doc.rangeHasMark(range.from, range.to, schema.marks.link);
      if (root.hidden) document.dispatchEvent(new CustomEvent('studio:context-open', { detail: { source: 'text' } }));
      root.hidden = false; position(session);
    };
    document.addEventListener('pointerdown', event => {
      if (root.contains(event.target)) return;
      if (session.view?.dom.contains(event.target)) { session.dragging = true; session.dismissed = null; hide(session); }
      else hide(session, true);
    });
    document.addEventListener('pointerup', () => { session.dragging = false; queue(session); });
    document.addEventListener('pointercancel', () => { session.dragging = false; queue(session); });
    document.addEventListener('selectionchange', () => queue(session));
    document.addEventListener('focusin', event => { if (!root.contains(event.target) && !session.view?.dom.contains(event.target)) hide(session, true); });
    editor.on('changeMode', () => { session.dismissed = null; hide(session); });
    document.addEventListener('studio:context-open', event => { if (event.detail?.source !== 'text') hide(session, true); });
    addEventListener('resize', () => position(session));
    addEventListener('scroll', () => position(session), { capture: true, passive: true });
    const layoutObserver = new ResizeObserver(() => position(session));
    layoutObserver.observe($('editor')); layoutObserver.observe($('editor-dock'));
    new MutationObserver(() => { if ($('document').inert || $('document').hidden) hide(session); }).observe($('document'), { attributes: true, attributeFilter: ['inert', 'hidden'] });
    // Keyboard selection has the same tools; Alt+F10 moves to them without
    // discarding the editor's selected range.
    session.view.dom.addEventListener('keydown', event => {
      if (event.altKey && event.key === 'F10' && !root.hidden) {
        event.preventDefault(); row.querySelector('button').focus({ preventScroll: true });
      }
      if (event.key === 'Escape' && !root.hidden) { event.preventDefault(); hide(session, true); }
    });
  }
  window.StudioText = { plugin, install };
})();
