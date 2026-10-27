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
          const id = name === 'heading' ? (dom.getAttribute('data-studio-heading-id') || dom.getAttribute('id')) : null;
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
    const id = node.type.name === 'heading' && doc ? headingCollisionIds(doc).get(node) : null;
    if (id) node = node.type.create({ ...node.attrs, htmlAttrs: { ...node.attrs.htmlAttrs, ...headingIdAttrs(id) } }, node.content, node.marks);
    if (!nodeAlignment(node) && !(node.type.name === 'heading' && node.attrs.htmlAttrs?.id)) {
      return context.origin ? context.origin() : {};
    }
    const container = document.createElement('div');
    container.append(serializeNode(node));
    let html = window.DOMPurify.sanitize(container.innerHTML, { USE_PROFILES: { html: true } });
    const publicId = node.type.name === 'heading' && publicHeadingId(node);
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
    const followingBlock = nodeInfo.parent && nodeInfo.index < nodeInfo.parent.childCount - 1;
    return { rawHTML: [html + (node.type.name === 'heading' && followingBlock ? '\n\n' : ''), ''] };
  }

  function selectedBlocks(editor) {
    const session = sessions.get(editor);
    if (!session?.view || !editor.isWysiwygMode()) {
      return { enabled: false, value: null, reason: '시각 편집에서 문단 정렬을 사용할 수 있어요.', blocks: [] };
    }
    const { state } = session.view;
    // Code blocks use a nested editor whose selection can leave the outer
    // ProseMirror paragraph selection unchanged. Respect the actual DOM focus.
    const domSelection = window.getSelection();
    for (const endpoint of [domSelection?.anchorNode, domSelection?.focusNode]) {
      const element = endpoint?.nodeType === Node.ELEMENT_NODE ? endpoint : endpoint?.parentElement;
      if (element && session.view.dom.contains(element) && element.closest('pre, table, blockquote, ul, ol')) {
        return { enabled: false, value: null, reason: '일반 문단이나 제목에서 정렬을 사용할 수 있어요.', blocks: [] };
      }
    }
    const { from, to } = state.selection;
    const blocks = [];
    let unsupported = false;
    state.doc.nodesBetween(from, to, (node, position, parent) => {
      if (node.isTextblock) {
        if (!blockTypes.has(node.type.name) || parent.type.name !== 'doc') unsupported = true;
        else blocks.push({ node, position });
        return false;
      }
      if (node.isLeaf && !node.isText) unsupported = true;
      return true;
    });
    if (!blocks.length || unsupported) {
      return { enabled: false, value: null, reason: '일반 문단이나 제목에 커서를 놓거나 선택해 주세요. 목록·표·인용문·코드 블록은 지원하지 않아요.', blocks: [] };
    }
    const values = new Set(blocks.map(({ node }) => nodeAlignment(node) || 'left'));
    return { enabled: true, value: values.size === 1 ? [...values][0] : null, blocks };
  }

  function plugin(context) {
    const session = { view: null, markdownView: null };
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
        view(view) {
          session.view = view;
          return { destroy() { session.view = null; } };
        },
      })],
    };
  }

  function selection(editor) {
    const { enabled, value, reason } = selectedBlocks(editor);
    return { enabled, value, reason };
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
      if (node.type.name !== 'heading') return;
      if (publicHeadingId(node)) used.add(publicHeadingId(node));
      else if (!node.attrs.rawHTML) {
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
      if (node.type.name !== 'heading') return;
      headings.push({ node, position });
      if (publicHeadingId(node)) used.add(publicHeadingId(node));
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
