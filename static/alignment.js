/* Paragraph alignment adapter for the pinned TOAST UI Editor 3.2.2 bundle. */
(() => {
  'use strict';

  const sessions = new WeakMap();
  const patchedSchemas = new WeakSet();
  const collisionCache = new WeakMap();
  const directions = new Set(['left', 'center', 'right']);
  const blockTypes = new Set(['paragraph', 'heading']);

  function alignmentFromStyle(style) {
    const element = document.createElement('span');
    element.setAttribute('style', style || '');
    return directions.has(element.style.textAlign) ? element.style.textAlign : null;
  }

  function nodeAlignment(node) {
    return alignmentFromStyle(node.attrs.htmlAttrs?.style);
  }

  function preserveAlignmentOnImport(schema) {
    if (patchedSchemas.has(schema)) return;
    patchedSchemas.add(schema);
    // These built-in nodes already have htmlAttrs and render them in toDOM.
    // 3.2.2's paragraph/heading parse rules omit those attributes. Extend only
    // their alignment import; do not replace the schema or mutate vendor files.
    for (const name of blockTypes) {
      const type = schema.nodes[name];
      type.spec.parseDOM = type.spec.parseDOM.map(rule => ({
        ...rule,
        getAttrs(dom) {
          const original = rule.getAttrs ? rule.getAttrs(dom) : rule.attrs;
          if (original === false) return false;
          const direction = alignmentFromStyle(dom.getAttribute('style'));
          // A heading can become ordinary prose without invalidating links to
          // its existing anchor. Keep the same sanitized ID on both blocks.
          const id = dom.getAttribute('data-studio-heading-id') || dom.getAttribute('id');
          return direction || id ? {
            ...original,
            htmlAttrs: { ...original?.htmlAttrs, ...(direction && { style: `text-align: ${direction};` }), ...(id && headingIdAttrs(id)) },
          } : original || null;
        },
      }));
    }
    // A schema-local DOMParser may have been cached by a preceding plugin.
    delete schema.cached.domParser;
  }

  // Render the schema's DOMOutputSpec, rather than copying the live editor DOM:
  // live image URLs can be local previews and must never enter saved Markdown.
  function renderSpec(spec, content) {
    if (typeof spec === 'string') return document.createTextNode(spec);
    if (spec?.nodeType) return spec.cloneNode(true);
    if (spec?.dom) return spec.dom.cloneNode(true);
    const [tag, ...parts] = spec;
    const element = document.createElement(tag);
    if (parts[0] && typeof parts[0] === 'object' && !Array.isArray(parts[0]) && !parts[0].nodeType) {
      const attrs = parts.shift();
      for (const [name, value] of Object.entries(attrs)) {
        if (value !== null && value !== undefined) element.setAttribute(name, String(value));
      }
    }
    for (const part of parts) {
      if (part === 0) element.append(content());
      else element.append(renderSpec(part, content));
    }
    return element;
  }

  function serializeNode(node) {
    let output = node.isText ? document.createTextNode(node.text) : renderSpec(node.type.spec.toDOM(node), () => {
      const fragment = document.createDocumentFragment();
      node.forEach(child => fragment.append(serializeNode(child)));
      return fragment;
    });
    for (let index = node.marks.length - 1; index >= 0; index -= 1) {
      const mark = node.marks[index];
      const inner = output;
      output = renderSpec(mark.type.spec.toDOM(mark, true), () => inner);
      // Mark specs may omit a content hole; ProseMirror then uses the root.
      if (!output.contains(inner)) output.append(inner);
    }
    return output;
  }

  function markdownBlock(nodeInfo, context, doc) {
    let node = nodeInfo.node;
    const previous = nodeInfo.parent && nodeInfo.index > 0 ? nodeInfo.parent.child(nodeInfo.index - 1) : null;
    const hasCaption = item => item?.type.name === 'paragraph' && item.content.content.some(child => child.type.name === 'image' && Object.hasOwn(child.attrs.htmlAttrs || {}, 'data-studio-caption'));
    // The raw-block writer already closes an HTML paragraph with a blank line.
    // Consume the editor's following separator paragraph without adding another
    // blank line that its Markdown importer would remove on the next round trip.
    if (node.type.name === 'paragraph' && !node.content.size && !nodeAlignment(node) && !node.attrs.htmlAttrs?.id &&
        previous?.type.name === 'paragraph' && (nodeAlignment(previous) || previous.attrs.htmlAttrs?.id || hasCaption(previous))) {
      return { rawHTML: ['', ''] };
    }
    const id = node.type.name === 'heading' && doc ? headingCollisionIds(doc).get(node) : null;
    if (id) node = node.type.create({ ...node.attrs, htmlAttrs: { ...node.attrs.htmlAttrs, ...headingIdAttrs(id) } }, node.content, node.marks);
    const captionImage = hasCaption(node);
    const rawHeading = node.type.name === 'heading' && node.attrs.rawHTML;
    if (!nodeAlignment(node) && !captionImage && !node.attrs.htmlAttrs?.id && !rawHeading) {
      return context.origin ? context.origin() : {};
    }
    const container = document.createElement('div');
    container.append(serializeNode(node));
    for (const element of container.querySelectorAll('[data-raw-html]')) element.removeAttribute('data-raw-html');
    let html = window.DOMPurify.sanitize(container.innerHTML, { USE_PROFILES: { html: true } });
    const publicId = publicHeadingId(node);
    if (publicId && node.attrs.htmlAttrs?.['data-studio-heading-id']) {
      // Restore only the escaped public anchor in the saved string. The live
      // editor/preview still passes through DOMPurify and uses the safe ID.
      container.innerHTML = html;
      container.firstElementChild.setAttribute('id', publicId);
      html = container.innerHTML;
    }
    // The 3.2.2 Markdown writer otherwise serializes inline marks as Markdown
    // inside raw HTML, where Jekyll does not parse them. Supply complete HTML
    // and an empty *conversion-local copy*, leaving the editor document intact.
    nodeInfo.node = node.copy();
    const previousHtmlHeading = previous?.type.name === 'heading' &&
      (previous.attrs.rawHTML || nodeAlignment(previous) || previous.attrs.htmlAttrs?.id || (doc && headingCollisionIds(doc).has(previous)));
    // The pinned writer turns on stopNewline before writing a raw paragraph.
    // That suppresses the preceding Markdown block's pending closeBlock(),
    // yielding e.g. "## Heading<p>..." unless we provide its separator here.
    // Paragraphs write their own newline; our HTML headings already end in two.
    const before = node.type.name === 'paragraph' && previous && previous.type.name !== 'paragraph' && !previousHtmlHeading ? '\n\n' : '';
    const followingBlock = nodeInfo.parent && nodeInfo.index < nodeInfo.parent.childCount - 1;
    return { rawHTML: [before + html + (node.type.name === 'heading' && followingBlock ? '\n\n' : ''), ''] };
  }

  function selectedBlocks(editor) {
    const session = sessions.get(editor);
    if (!session?.view || !editor.isWysiwygMode()) {
      return { enabled: false, value: null, scope: null, reason: '시각 편집에서 정렬을 사용할 수 있어요.', blocks: [] };
    }
    const { state, dom } = session.view;
    const browserSelection = window.getSelection();
    const inEditor = browserSelection?.anchorNode && browserSelection?.focusNode &&
      dom.contains(browserSelection.anchorNode) && dom.contains(browserSelection.focusNode);
    const active = document.activeElement;
    const outsideField = active?.matches('input, textarea, [contenteditable="true"]') && !dom.contains(active);
    const hasRange = !outsideField && inEditor && !browserSelection.isCollapsed && !state.selection.empty;
    const scope = hasRange ? 'selection' : 'document';
    const blocks = [];
    // A caret or focus outside the editor means whole-document alignment.
    // Code, lists and tables keep their structure; only root prose blocks are
    // candidates. A dragged range includes only the prose blocks it touches.
    state.doc.forEach((node, position) => {
      if (!blockTypes.has(node.type.name)) return;
      if (hasRange && (position >= state.selection.to || position + node.nodeSize <= state.selection.from)) return;
      blocks.push({ node, position });
    });
    if (!blocks.length) {
      return { enabled: false, value: null, scope, reason: '선택한 범위에 정렬할 일반 문단이나 제목이 없어요.', blocks: [] };
    }
    const values = new Set(blocks.map(({ node }) => nodeAlignment(node) || 'left'));
    return { enabled: true, value: values.size === 1 ? [...values][0] : null, scope, blocks };
  }

  function rememberParagraphBreak(session, event) {
    const view = session.view;
    if (!view || event.isComposing || view.composing || event.keyCode === 229) return;
    const paragraphBreak = event.type === 'keydown'
      ? event.key === 'Enter' && !event.ctrlKey && !event.metaKey && !event.altKey
      : event.inputType === 'insertParagraph';
    if (!paragraphBreak) return;
    const { selection } = view.state;
    if (selection.$from.depth !== 1 || !blockTypes.has(selection.$from.parent.type.name)) return;
    const direction = nodeAlignment(selection.$from.parent);
    if (!direction) return;
    // Start a fresh native history group before the split. The inherited
    // attributes are appended to that split, so a single undo removes the new
    // paragraph instead of undoing the entire document loaded moments earlier.
    view.dispatch(view.state.tr.setMeta('closeHistory$', true));
    session.paragraphBreak = { direction, position: selection.$from.before(1),
      document: view.state.doc, at: performance.now() };
  }

  function inheritParagraphAlignment(session, transactions, oldState, state) {
    const pending = session.paragraphBreak;
    if (!pending || !transactions.some(transaction => transaction.docChanged)) return null;
    session.paragraphBreak = null;
    const { $from } = state.selection;
    if (performance.now() - pending.at > 1000 || oldState.doc !== pending.document ||
        $from.depth !== 1 || !blockTypes.has($from.parent.type.name)) return null;
    const position = $from.before(1);
    // A split can create a following block or convert an empty heading into a
    // paragraph. Do not carry formatting into unrelated commands/navigation.
    if (position < pending.position) return null;
    const node = $from.parent;
    const htmlAttrs = { ...node.attrs.htmlAttrs };
    if (position > pending.position || node.type.name === 'paragraph') {
      delete htmlAttrs.id;
      delete htmlAttrs['data-studio-heading-id'];
    }
    htmlAttrs.style = `text-align: ${pending.direction};`;
    if (JSON.stringify(htmlAttrs) === JSON.stringify(node.attrs.htmlAttrs || {})) return null;
    const transaction = state.tr.setNodeMarkup(position, null, { ...node.attrs, htmlAttrs }, node.marks);
    if (state.storedMarks) transaction.setStoredMarks(state.storedMarks);
    return transaction;
  }

  function plugin(context) {
    const session = { view: null, markdownView: null, paragraphBreak: null };
    sessions.set(context.instance, session);
    return {
      toMarkdownRenderers: {
        paragraph: (info, convertor) => markdownBlock(info, convertor, session.view?.state.doc),
        heading: (info, convertor) => markdownBlock(info, convertor, session.view?.state.doc),
      },
      markdownPlugins: [() => new context.pmState.Plugin({
        view(view) {
          session.markdownView = view;
          return { destroy() { session.markdownView = null; } };
        },
      })],
      wysiwygPlugins: [() => new context.pmState.Plugin({
        state: { init: (_, state) => preserveAlignmentOnImport(state.schema), apply: (_, value) => value },
        appendTransaction: (transactions, oldState, state) => inheritParagraphAlignment(session, transactions, oldState, state),
        view(view) {
          session.view = view;
          const remember = event => rememberParagraphBreak(session, event);
          view.dom.addEventListener('keydown', remember, true);
          view.dom.addEventListener('beforeinput', remember, true);
          return { destroy() {
            view.dom.removeEventListener('keydown', remember, true);
            view.dom.removeEventListener('beforeinput', remember, true);
            session.view = null;
          } };
        },
      })],
    };
  }

  function selection(editor) {
    const { enabled, value, reason, scope } = selectedBlocks(editor);
    return { enabled, value, reason, scope };
  }

  function headingSlug(text) {
    // kramdown-parser-gfm 1.1.0's generated_header_id (the blog renderer).
    return text.toLowerCase().replace(/[^\p{L}\p{M}\p{N}\p{Pc}\- \t]/gu, '').replace(/[ \t]/g, '-');
  }

  function safeHeadingId(value) {
    // DOMPurify rejects named properties such as title/location/name to prevent
    // DOM clobbering. Allocate a safe ID instead of weakening that protection.
    const probe = document.createElement('h2');
    let candidate = value || 'blog-studio-heading';
    for (;;) {
      probe.id = candidate;
      const clean = window.DOMPurify.sanitize(probe.outerHTML, { RETURN_DOM_FRAGMENT: true });
      if (clean.firstElementChild?.id === candidate) return candidate;
      candidate = `blog-studio-heading-${candidate}`;
    }
  }

  function publicHeadingId(node) {
    return node.attrs.htmlAttrs?.['data-studio-heading-id'] || node.attrs.htmlAttrs?.id;
  }

  function headingIdAttrs(publicId) {
    const id = safeHeadingId(publicId);
    return { id, ...(id !== publicId && { 'data-studio-heading-id': publicId }) };
  }

  function headingCollisionIds(doc) {
    if (collisionCache.has(doc)) return collisionCache.get(doc);
    const used = new Set();
    const implicit = [];
    const counts = new Map();
    const affected = new Set();
    doc.descendants(node => {
      if (blockTypes.has(node.type.name) && publicHeadingId(node)) used.add(publicHeadingId(node));
      if (node.type.name !== 'heading') return;
      if (!publicHeadingId(node) && !node.attrs.rawHTML) {
        const base = headingSlug(node.textContent);
        const suffix = counts.get(base) || 0;
        counts.set(base, suffix + 1);
        implicit.push({ node, base, suffix, id: base + (suffix ? `-${suffix}` : '') });
      }
    });
    for (const item of implicit) if (used.has(item.id)) affected.add(item.base);
    for (const item of implicit) if (!affected.has(item.base)) used.add(item.id);
    const result = new Map();
    // New headings may be typed after an earlier group was frozen. Give only
    // the colliding same-slug group explicit IDs on export; do not rewrite the
    // live document or combine this normalization with the user's undo steps.
    for (const item of implicit) {
      if (!affected.has(item.base)) continue;
      let suffix = item.suffix;
      let id = item.id;
      while (used.has(id)) { suffix += 1; id = `${item.base || 'blog-studio-heading'}-${suffix}`; }
      used.add(id);
      result.set(item.node, id);
    }
    doc.descendants((node, position) => {
      if (node.type.name !== 'heading' || node.attrs.htmlAttrs?.id || !nodeAlignment(node) || result.has(node)) return;
      let id = `blog-studio-heading-${headingSlug(node.textContent) || position}`;
      while (used.has(id)) id += '-1';
      used.add(id);
      result.set(node, id);
    });
    collisionCache.set(doc, result);
    return result;
  }

  function headingIds(doc, selected, direction) {
    const affected = new Set(selected.filter(({ node }) => node.type.name === 'heading'
      && !node.attrs.htmlAttrs?.id && !node.attrs.rawHTML && (direction !== 'left' || nodeAlignment(node)))
      .map(({ node }) => headingSlug(node.textContent)));
    const result = new Map();
    const counts = new Map();
    const used = new Set();
    const headings = [];
    doc.descendants((node, position) => {
      if (blockTypes.has(node.type.name) && publicHeadingId(node)) used.add(publicHeadingId(node));
      if (node.type.name !== 'heading') return;
      headings.push({ node, position });
    });
    for (const { node, position } of selected) {
      if (node.type.name !== 'heading' || !node.attrs.rawHTML || node.attrs.htmlAttrs?.id
          || (direction === 'left' && !nodeAlignment(node))) continue;
      let id = `blog-studio-heading-${position}`;
      while (used.has(id)) id += '-1';
      used.add(id);
      result.set(position, { node, id });
    }
    // Raw HTML headings do not advance GFM's implicit-ID counter. Freeze the
    // affected same-slug group together so existing #name / #name-1 links keep
    // working when just one of those headings becomes aligned HTML.
    const implicit = [];
    for (const { node, position } of headings) {
      if (node.attrs.htmlAttrs?.id || node.attrs.rawHTML) continue;
      const base = headingSlug(node.textContent);
      const suffix = counts.get(base) || 0;
      counts.set(base, suffix + 1);
      const id = base ? base + (suffix ? `-${suffix}` : '') : `blog-studio-heading-${position}`;
      implicit.push({ node, position, base, suffix, id });
      if (!affected.has(base)) used.add(id);
    }
    for (const item of implicit) {
      if (!affected.has(item.base)) continue;
      let suffix = item.suffix;
      let id = item.id;
      while (used.has(id)) { suffix += 1; id = `${item.base || 'blog-studio-heading'}-${suffix}`; }
      used.add(id);
      result.set(item.position, { node: item.node, id });
    }
    return result;
  }

  function apply(editor, direction) {
    if (!directions.has(direction)) return false;
    sessions.get(editor)?.view?.domObserver.flush();
    const selected = selectedBlocks(editor);
    if (!selected.enabled) return false;
    const { view } = sessions.get(editor);
    const transaction = view.state.tr;
    const ids = headingIds(view.state.doc, selected.blocks, direction);
    for (const { node, position } of selected.blocks) {
      const assigned = ids.get(position);
      ids.delete(position);
      if ((nodeAlignment(node) || 'left') === direction && !assigned) continue;
      const htmlAttrs = { ...node.attrs.htmlAttrs, ...(assigned && headingIdAttrs(assigned.id)), style: `text-align: ${direction};` };
      transaction.setNodeMarkup(position, null, { ...node.attrs, htmlAttrs }, node.marks);
    }
    for (const [position, { node, id }] of ids) {
      transaction.setNodeMarkup(position, null, { ...node.attrs, htmlAttrs: { ...node.attrs.htmlAttrs, ...headingIdAttrs(id) } }, node.marks);
    }
    if (transaction.docChanged) {
      // The pinned bundle's prosemirror-history uses this PluginKey. Close
      // both sides so undo never combines alignment with adjacent typing.
      transaction.setMeta('closeHistory$', true);
      view.dispatch(transaction.scrollIntoView());
      view.dispatch(view.state.tr.setMeta('closeHistory$', true));
    }
    view.focus();
    return true;
  }

  function flushPendingChanges(editor) {
    const session = sessions.get(editor);
    // TOAST UI 3.2.2 changeMode reads ToastMark immediately. Flush the pinned
    // ProseMirror DOMObserver first so recent paste/composition edits reach it.
    const view = editor?.isMarkdownMode() ? session?.markdownView : null;
    view?.domObserver.flush();
  }

  window.StudioAlignment = { plugin, apply, selection, flushPendingChanges };
})();
