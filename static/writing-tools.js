/* Writing tools for the pinned TOAST UI Editor 3.2.2 plugin interface. */
(() => {
  'use strict';
  const sessions = new WeakMap();
  const LIMIT = 5000;
  const empty = () => ({ matches: [], truncated: false });

  function plugin(context) {
    const session = { context, editor: context.instance, visual: null, markdown: null, query: '', caseSensitive: false, searchOpen: false, refresh: null, pending: false, cache: null };
    sessions.set(context.instance, session);
    const capture = mode => () => new context.pmState.Plugin({
      props: { decorations(state) {
        if (!session.searchOpen || !session.query) return null;
        const result = matches(session, state.doc);
        return context.pmView.DecorationSet.create(state.doc, result.matches.map(item => context.pmView.Decoration.inline(item.from, item.to, { class: 'studio-find-match' })));
      } },
      view(view) {
        session[mode] = view;
        return { update() { queue(session); }, destroy() { session[mode] = null; } };
      },
    });
    const inline = tag => (node, { entering }) => entering
      ? { type: 'openTag', tagName: tag, attributes: node.attrs || {} }
      : { type: 'closeTag', tagName: tag };
    // Registering these renderers creates the corresponding schema marks, as
    // in TOAST UI's official color-syntax plugin. No vendor code is modified.
    return {
      toHTMLRenderers: { htmlInline: { span: inline('span'), u: inline('u') } },
      wysiwygPlugins: [capture('visual')], markdownPlugins: [capture('markdown')],
    };
  }

  function queue(session) {
    if (session.pending) return;
    session.pending = true;
    requestAnimationFrame(() => { session.pending = false; session.refresh?.(); });
  }

  function viewFor(session) { return session.editor.isWysiwygMode() ? session.visual : session.markdown; }

  function commit(session, view, tr, focus = true) {
    if (tr.docChanged) {
      // Match the alignment adapter's isolated undo boundary in the pinned bundle.
      tr.setMeta('closeHistory$', true);
      view.dispatch(tr.scrollIntoView());
      view.dispatch(view.state.tr.setMeta('closeHistory$', true));
    } else view.dispatch(tr);
    if (focus) view.focus();
    queue(session);
  }

  function canFormat(session) {
    if (!session.editor.isWysiwygMode() || !session.visual) return false;
    const { state, dom } = session.visual;
    const selection = window.getSelection();
    for (const endpoint of [selection?.anchorNode, selection?.focusNode]) {
      const element = endpoint?.nodeType === Node.ELEMENT_NODE ? endpoint : endpoint?.parentElement;
      if (element && dom.contains(element) && element.closest('pre')) return false;
    }
    let allowed = true;
    state.doc.nodesBetween(state.selection.from, state.selection.to, node => {
      if (node.type.name === 'codeBlock' || node.type.name === 'customBlock') allowed = false;
    });
    return allowed && state.selection.$from.parent.inlineContent;
  }

  function styleMark(schema, existing, property, value) {
    const element = document.createElement('span');
    element.setAttribute('style', existing?.attrs.htmlAttrs?.style || '');
    element.style.setProperty(property, value);
    return schema.marks.span.create({ ...existing?.attrs, htmlAttrs: { ...existing?.attrs.htmlAttrs, style: element.getAttribute('style') } });
  }

  function applyStyle(editor, property, value) {
    const session = sessions.get(editor);
    if (!session || !canFormat(session) || !['color', 'background-color'].includes(property) || !/^#[0-9a-f]{6}$/i.test(value)) return false;
    const view = session.visual;
    const { selection, schema, doc } = view.state;
    const tr = view.state.tr;
    if (selection.empty) {
      const marks = view.state.storedMarks || selection.$from.marks();
      tr.addStoredMark(styleMark(schema, marks.find(mark => mark.type === schema.marks.span), property, value));
    } else {
      doc.nodesBetween(selection.from, selection.to, (node, position) => {
        if (!node.isText) return;
        const from = Math.max(selection.from, position), to = Math.min(selection.to, position + node.nodeSize);
        const mark = styleMark(schema, node.marks.find(item => item.type === schema.marks.span), property, value);
        tr.removeMark(from, to, schema.marks.span).addMark(from, to, mark);
      });
    }
    commit(session, view, tr);
    return true;
  }

  function underline(editor) {
    const session = sessions.get(editor);
    if (!session || !canFormat(session)) return false;
    const view = session.visual, { selection, schema, doc } = view.state, type = schema.marks.u;
    const active = selection.empty ? type.isInSet(view.state.storedMarks || selection.$from.marks()) : doc.rangeHasMark(selection.from, selection.to, type);
    const tr = view.state.tr;
    if (selection.empty) active ? tr.removeStoredMark(type) : tr.addStoredMark(type.create({ htmlAttrs: {} }));
    else active ? tr.removeMark(selection.from, selection.to, type) : tr.addMark(selection.from, selection.to, type.create({ htmlAttrs: {} }));
    commit(session, view, tr);
    return true;
  }

  function clearStyle(editor) {
    const session = sessions.get(editor);
    if (!session || !canFormat(session)) return false;
    const view = session.visual, { selection, schema } = view.state, tr = view.state.tr;
    for (const type of [schema.marks.span, schema.marks.u]) {
      if (selection.empty) tr.removeStoredMark(type);
      else tr.removeMark(selection.from, selection.to, type);
    }
    commit(session, view, tr);
    return true;
  }

  function matches(session, doc) {
    if (!session.query) return empty();
    if (session.cache?.doc === doc && session.cache.query === session.query && session.cache.caseSensitive === session.caseSensitive) return session.cache.result;
    const pattern = new RegExp(session.query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), session.caseSensitive ? 'gu' : 'giu');
    const result = empty();
    doc.descendants((node, position) => {
      if (result.truncated) return false;
      if (!node.isTextblock) return true;
      // Inline atoms occupy one document position. The placeholder keeps image
      // and hard-break offsets from shifting later text matches.
      const text = node.textBetween(0, node.content.size, '', '\ufffc');
      pattern.lastIndex = 0;
      let match;
      while ((match = pattern.exec(text))) {
        if (result.matches.length === LIMIT) { result.truncated = true; break; }
        result.matches.push({ from: position + 1 + match.index, to: position + 1 + match.index + match[0].length, text: match[0] });
      }
      return false;
    });
    session.cache = { doc, query: session.query, caseSensitive: session.caseSensitive, result };
    return result;
  }

  function setQuery(session, query, caseSensitive) {
    session.query = query.slice(0, 500);
    session.caseSensitive = caseSensitive;
    for (const view of [session.visual, session.markdown]) if (view) view.dispatch(view.state.tr.setMeta('studioFind', true));
    session.refresh?.();
  }

  function findNext(session, backward = false) {
    const view = viewFor(session), list = matches(session, view.state.doc).matches;
    if (!list.length) return null;
    const { from, to } = view.state.selection;
    const match = backward ? [...list].reverse().find(item => item.to <= from) || list[list.length - 1] : list.find(item => item.from >= to && (item.from !== from || item.to !== to)) || list[0];
    view.dispatch(view.state.tr.setSelection(session.context.pmState.TextSelection.create(view.state.doc, match.from, match.to)).scrollIntoView());
    view.focus();
    queue(session);
    return match;
  }

  function replace(session, value, all = false) {
    const view = viewFor(session), result = matches(session, view.state.doc);
    if (!result.matches.length) return 0;
    if (all && result.truncated) throw new Error('일치하는 내용이 5,000개를 넘어요. 검색어를 더 구체적으로 입력해 주세요.');
    let selected = result.matches;
    if (!all) {
      const current = selected.find(item => item.from === view.state.selection.from && item.to === view.state.selection.to);
      const match = current || findNext(session);
      if (!match) return 0;
      selected = [match];
    }
    const tr = view.state.tr;
    for (const item of [...selected].reverse()) tr.insertText(value, item.from, item.to);
    commit(session, view, tr);
    return selected.length;
  }

  function blocks(session) {
    const view = viewFor(session), result = [];
    if (session.editor.isWysiwygMode()) {
      view.state.doc.forEach((node, position, index) => result.push({ node, position, index, type: node.type.name, level: node.attrs.level, text: node.textContent.trim() }));
    } else {
      view.state.doc.forEach((node, position, index) => {
        const line = node.textContent, atx = line.match(/^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/), html = line.match(/^\s*<h([1-6])(?:\s[^>]*)?>(.*?)<\/h\1>\s*$/i);
        if (atx || html) result.push({ node, position, index, type: 'heading', level: atx ? atx[1].length : Number(html[1]), text: atx ? atx[2] : html[2].replace(/<[^>]*>/g, '') });
      });
    }
    return result;
  }

  function moveBlock(editor, fromIndex, toIndex) {
    const session = sessions.get(editor);
    if (!session || !editor.isWysiwygMode()) return false;
    const view = session.visual, nodes = [];
    view.state.doc.forEach(node => nodes.push(node));
    if (!Number.isInteger(fromIndex) || !Number.isInteger(toIndex) || fromIndex < 0 || toIndex < 0 || fromIndex >= nodes.length || toIndex >= nodes.length || fromIndex === toIndex) return false;
    // TOAST represents Markdown's blank separator lines as empty paragraphs.
    // Move them with their preceding block, so reordering does not silently
    // join two previously separate Markdown paragraphs.
    const adjacent = new Map();
    nodes.forEach((node, index) => { if (index + 1 < nodes.length) { if (!adjacent.has(node)) adjacent.set(node, new Set()); adjacent.get(node).add(nodes[index + 1]); } });
    const groups = [];
    for (let index = 0; index < nodes.length; index += 1) {
      const node = nodes[index];
      if (node.type.name === 'paragraph' && !node.content.size && groups.length) groups[groups.length - 1].nodes.push(node);
      else groups.push({ index, nodes: [node] });
    }
    const from = groups.findIndex(group => group.index === fromIndex), to = groups.findIndex(group => group.index === toIndex);
    if (from < 0 || to < 0) return false;
    const [group] = groups.splice(from, 1);
    groups.splice(to, 0, group);
    nodes.length = 0;
    let position = 0;
    for (const [index, item] of groups.entries()) {
      const previous = nodes[nodes.length - 1], next = item.nodes[0];
      if (previous?.type.name === 'paragraph' && previous.content.size && next.type.name === 'paragraph' && next.content.size && !adjacent.get(previous)?.has(next)) {
        nodes.push(view.state.schema.nodes.paragraph.create());
      }
      if (index === to) position = nodes.reduce((total, child) => total + child.nodeSize, 0);
      nodes.push(...item.nodes);
    }
    const tr = view.state.tr.replaceWith(0, view.state.doc.content.size, session.context.pmModel.Fragment.fromArray(nodes));
    tr.setSelection(session.context.pmState.Selection.near(tr.doc.resolve(Math.min(position + 1, tr.doc.content.size))));
    commit(session, view, tr);
    return true;
  }

  function moveSection(editor, fromIndex, toIndex) {
    const session = sessions.get(editor);
    if (!session || !editor.isWysiwygMode()) return false;
    const view = session.visual, nodes = [];
    view.state.doc.forEach(node => nodes.push(node));
    const source = nodes[fromIndex], target = nodes[toIndex];
    if (fromIndex === toIndex || source?.type.name !== 'heading' || target?.type.name !== 'heading') return false;
    if (source.attrs.level !== target.attrs.level) throw new Error('같은 단계의 제목으로 옮겨 주세요. 제목 아래의 내용도 함께 이동합니다.');
    const end = start => {
      let index = start + 1;
      while (index < nodes.length && !(nodes[index].type.name === 'heading' && nodes[index].attrs.level <= nodes[start].attrs.level)) index += 1;
      return index;
    };
    const sourceEnd = end(fromIndex), targetEnd = end(toIndex);
    const section = nodes.splice(fromIndex, sourceEnd - fromIndex);
    const destination = fromIndex < toIndex ? targetEnd - section.length : toIndex;
    nodes.splice(destination, 0, ...section);
    const position = nodes.slice(0, destination).reduce((total, node) => total + node.nodeSize, 0);
    const tr = view.state.tr.replaceWith(0, view.state.doc.content.size, session.context.pmModel.Fragment.fromArray(nodes));
    tr.setSelection(session.context.pmState.Selection.near(tr.doc.resolve(position + 1)));
    commit(session, view, tr);
    return true;
  }

  function withMarkdownParser(editor, body, callback) {
    const session = sessions.get(editor);
    const host = document.createElement('div');
    host.style.cssText = 'position:fixed;left:-10000px;top:0;width:800px;visibility:hidden;pointer-events:none';
    host.setAttribute('aria-hidden', 'true');
    document.body.append(host);
    let parser;
    try {
      const view = session.visual, sourceSchema = view.state.schema;
      // The pinned editor stores Markdown converters in a shared module. Registering
      // alignment/media converters twice recursively wraps their origin callbacks.
      // This disposable editor only parses: copy the main schema's import rules,
      // and register HTML marks without installing any Markdown converters again.
      const importRules = context => ({ wysiwygPlugins: [() => new context.pmState.Plugin({
        state: { init(_, state) {
          for (const group of ['nodes', 'marks']) for (const [name, type] of Object.entries(sourceSchema[group])) {
            if (state.schema[group][name] && type.spec.parseDOM) {
              state.schema[group][name].spec.parseDOM = type.spec.parseDOM.map(rule => ({ ...rule }));
            }
          }
          delete state.schema.cached.domParser;
          return null;
        }, apply: (_, value) => value },
      })] });
      parser = new toastui.Editor({ el: host, initialEditType: 'wysiwyg', initialValue: body,
        usageStatistics: false, autofocus: false, hideModeSwitch: true,
        customHTMLSanitizer: html => DOMPurify.sanitize(html, { USE_PROFILES: { html: true } }),
        plugins: [plugin, importRules] });
      return callback(parser, sessions.get(parser).visual);
    } finally {
      parser?.destroy();
      host.remove();
    }
  }

  function insertMarkdown(editor, body) {
    const session = sessions.get(editor);
    if (!session || typeof body !== 'string' || new TextEncoder().encode(body).length > 4 * 1024 * 1024) throw new Error('삽입할 내용은 4MB 이하로 입력해 주세요.');
    if (!body) return false;
    if (editor.isMarkdownMode()) { editor.replaceSelection(body); return true; }
    return withMarkdownParser(editor, body, (_, parsedView) => {
      const view = session.visual;
      const parsed = view.state.schema.nodeFromJSON(parsedView.state.doc.toJSON());
      const plainParagraph = parsed.childCount === 1 && parsed.firstChild.type.name === 'paragraph' && !Object.keys(parsed.firstChild.attrs.htmlAttrs || {}).length;
      let content = plainParagraph ? parsed.firstChild.content : parsed.content;
      if (!plainParagraph) {
        const { $from, $to } = view.state.selection, paragraph = view.state.schema.nodes.paragraph;
        const separator = session.context.pmModel.Fragment.from(paragraph.create());
        // This editor represents a Markdown blank line as an empty paragraph.
        // Separate inserted block paragraphs from split surrounding text so
        // publishing does not collapse them back into a single paragraph.
        if (parsed.firstChild?.type === paragraph && $from.depth === 1 && $from.parent.type === paragraph && $from.parentOffset) content = separator.append(content);
        if (parsed.lastChild?.type === paragraph && $to.depth === 1 && $to.parent.type === paragraph && $to.parentOffset < $to.parent.content.size) content = content.append(separator);
      }
      const Slice = view.state.selection.content().constructor;
      commit(session, view, view.state.tr.replaceSelection(new Slice(content, 0, 0)));
      return true;
    });
  }

  function selectedMarkdown(editor) {
    const session = sessions.get(editor);
    if (!session) return '';
    if (editor.isMarkdownMode()) return editor.getSelectedText();
    const selection = session.visual.state.selection;
    if (selection.empty) return '';
    const fragment = selection.content().content.toJSON();
    return withMarkdownParser(editor, '', (parser, view) => {
      const document = view.state.schema.nodeFromJSON({ type: 'doc', content: fragment });
      view.dispatch(view.state.tr.replaceWith(0, view.state.doc.content.size, document.content));
      return parser.getMarkdown();
    });
  }

  function install({ editor, host, notify = () => {} }) {
    const session = sessions.get(editor);
    if (!session || !host) throw new Error('Writing tools require their editor plugin and host.');
    host.classList.add('studio-writing-tools');
    host.innerHTML = `<div class="writing-action-row" role="group" aria-label="글쓰기 도구">
      <label class="writing-color" title="선택한 글자 색"><span>글자색</span><input type="color" id="writing-color" value="#147d73" aria-label="글자색"></label>
      <label class="writing-color" title="선택한 글자의 배경색"><span>형광펜</span><input type="color" id="writing-highlight" value="#fff0a3" aria-label="형광펜 색"></label>
      <button type="button" id="writing-underline" aria-label="밑줄" aria-pressed="false"><u>U</u></button>
      <button type="button" id="writing-clear-style">강조 해제</button>
      <span class="writing-spacer"></span><button type="button" id="writing-find" aria-expanded="false">찾기·바꾸기</button>
      <button type="button" id="writing-outline" aria-expanded="false">목차·문단 이동</button>
    </div>
    <section id="writing-find-panel" class="writing-panel" aria-label="본문 찾기와 바꾸기" hidden>
      <div class="writing-panel-heading"><strong>본문 찾기·바꾸기</strong><button type="button" data-writing-close="find" aria-label="찾기 닫기">×</button></div>
      <div class="writing-find-fields"><label>찾을 내용<input id="find-query" type="search" maxlength="500" autocomplete="off" placeholder="찾을 단어나 문장"></label><label>바꿀 내용<input id="find-replacement" autocomplete="off" placeholder="비워두면 삭제"></label></div>
      <div class="writing-panel-actions"><label class="writing-checkbox"><input id="find-case" type="checkbox">대소문자 구분</label><span id="find-result" role="status"></span><button type="button" id="find-prev" aria-label="이전 검색 결과">이전</button><button type="button" id="find-next" aria-label="다음 검색 결과">다음</button><button type="button" id="replace-one">하나 바꾸기</button><button type="button" id="replace-all">모두 바꾸기</button></div>
    </section>
    <section id="writing-outline-panel" class="writing-panel" aria-label="문서 목차와 문단 이동" hidden>
      <div class="writing-panel-heading"><strong>문서 목차</strong><label class="writing-checkbox"><input id="outline-all-blocks" type="checkbox">모든 문단 보기</label><button type="button" data-writing-close="outline" aria-label="목차 닫기">×</button></div>
      <p id="outline-help" class="writing-help">제목을 누르면 해당 위치로 이동합니다.</p><ol id="writing-outline-list" class="writing-outline-list"></ol>
    </section>`;
    const $ = id => host.querySelector('#' + id);
    const safe = action => event => { try { action(event); } catch (error) { notify(error.message, true); } };
    const richControls = ['writing-color', 'writing-highlight', 'writing-underline', 'writing-clear-style'];
    for (const id of ['writing-underline', 'writing-clear-style']) $(id).addEventListener('mousedown', event => event.preventDefault());
    $('writing-color').addEventListener('change', safe(event => applyStyle(editor, 'color', event.target.value)));
    $('writing-highlight').addEventListener('change', safe(event => applyStyle(editor, 'background-color', event.target.value)));
    $('writing-underline').onclick = safe(() => underline(editor));
    $('writing-clear-style').onclick = safe(() => clearStyle(editor));
    let outlineDoc = null, outlineMode = null, dragged = null;
    const close = name => {
      $('writing-' + name + '-panel').hidden = true;
      $('writing-' + name).setAttribute('aria-expanded', 'false');
      if (name === 'find') { session.searchOpen = false; setQuery(session, session.query, session.caseSensitive); }
    };
    const toggle = name => {
      const panel = $('writing-' + name + '-panel');
      if (!panel.hidden) { close(name); return; }
      panel.hidden = false;
      $('writing-' + name).setAttribute('aria-expanded', 'true');
      if (name === 'find') { session.searchOpen = true; setQuery(session, $('find-query').value, $('find-case').checked); $('find-query').focus(); }
      else { outlineDoc = null; session.refresh(); }
    };
    $('writing-find').onclick = () => toggle('find');
    $('writing-outline').onclick = () => toggle('outline');
    for (const button of host.querySelectorAll('[data-writing-close]')) button.onclick = () => { const name = button.dataset.writingClose; close(name); $('writing-' + name).focus(); };
    for (const id of ['find-query', 'find-case']) $(id).addEventListener('input', () => setQuery(session, $('find-query').value, $('find-case').checked));
    $('find-query').addEventListener('keydown', safe(event => { if (event.key === 'Enter') { event.preventDefault(); findNext(session, event.shiftKey); } }));
    $('find-prev').onclick = safe(() => findNext(session, true));
    $('find-next').onclick = safe(() => findNext(session));
    $('replace-one').onclick = safe(() => { const count = replace(session, $('find-replacement').value); if (count) notify('한 곳을 바꿨어요. Ctrl/⌘ + Z로 되돌릴 수 있어요.'); });
    $('replace-all').onclick = safe(() => { const count = replace(session, $('find-replacement').value, true); if (count) notify(`${count}곳을 바꿨어요. Ctrl/⌘ + Z로 한 번에 되돌릴 수 있어요.`); });
    $('outline-all-blocks').onchange = () => { outlineDoc = null; session.refresh(); };
    const names = { paragraph: '본문', heading: '제목', codeBlock: '코드', bulletList: '목록', orderedList: '번호 목록', blockQuote: '인용', table: '표', thematicBreak: '구분선' };
    const renderOutline = () => {
      const view = viewFor(session), visual = editor.isWysiwygMode();
      if ($('writing-outline-panel').hidden || (outlineDoc === view.state.doc && outlineMode === visual)) return;
      outlineDoc = view.state.doc; outlineMode = visual;
      const list = $('writing-outline-list'); list.replaceChildren();
      const all = $('outline-all-blocks').checked && visual;
      const entries = blocks(session).filter(item => item.type !== 'paragraph' || item.node.content.size), visible = entries.filter(item => all || item.type === 'heading');
      $('outline-all-blocks').disabled = !visual;
      $('outline-help').textContent = all ? '문단을 끌거나 위·아래 버튼으로 순서를 바꾸세요. Ctrl/⌘ + Z로 되돌릴 수 있어요.' : visual ? '제목을 누르면 이동합니다. 같은 단계의 제목을 끌거나 위·아래 버튼을 누르면 아래 내용도 함께 옮깁니다.' : '제목을 누르면 해당 위치로 이동합니다. 시각 편집에서 문단과 구역의 순서를 바꿀 수 있어요.';
      if (!visible.length) { const item = document.createElement('li'); item.textContent = '아직 본문 제목이 없어요.'; list.append(item); }
      const peersFor = new Map(), peerGroups = new Map(), ancestors = [];
      if (!all) for (const entry of visible) {
        while (ancestors.length && ancestors[ancestors.length - 1].level >= entry.level) ancestors.pop();
        const key = entry.level + ':' + (ancestors[ancestors.length - 1]?.index ?? -1);
        if (!peerGroups.has(key)) peerGroups.set(key, []);
        const items = peerGroups.get(key);
        peersFor.set(entry.index, { items, position: items.length }); items.push(entry); ancestors.push(entry);
      }
      for (const [visibleIndex, entry] of visible.entries()) {
        const row = document.createElement('li'); row.dataset.blockIndex = String(entry.index); row.className = 'writing-outline-item';
        const jump = document.createElement('button'); jump.type = 'button'; jump.className = 'writing-outline-jump';
        const kind = document.createElement('span'); kind.className = 'writing-block-kind'; kind.textContent = entry.type === 'heading' ? 'H' + entry.level : names[entry.type] || '블록';
        const text = document.createElement('span'); text.textContent = (entry.text || (entry.type === 'paragraph' ? '빈 문단 또는 이미지' : names[entry.type] || '블록')).slice(0, 100);
        jump.append(kind, text); jump.onclick = () => { const current = viewFor(session); current.dispatch(current.state.tr.setSelection(session.context.pmState.Selection.near(current.state.doc.resolve(Math.min(entry.position + 1, current.state.doc.content.size)))).scrollIntoView()); current.focus(); };
        row.append(jump);
        if (visual) {
          const moveItem = all ? moveBlock : moveSection;
          row.draggable = true;
          row.addEventListener('dragstart', event => { dragged = entry.index; event.dataTransfer.effectAllowed = 'move'; event.dataTransfer.setData('text/plain', String(entry.index)); row.classList.add('dragging'); });
          row.addEventListener('dragend', () => { dragged = null; row.classList.remove('dragging'); });
          row.addEventListener('dragover', event => { if (dragged !== null) { event.preventDefault(); event.dataTransfer.dropEffect = 'move'; } });
          row.addEventListener('drop', safe(event => { event.preventDefault(); const from = dragged; dragged = null; if (from !== null) moveItem(editor, from, entry.index); }));
          const peers = all ? visible : peersFor.get(entry.index).items;
          const peerIndex = all ? visibleIndex : peersFor.get(entry.index).position;
          for (const [delta, direction, symbol] of [[-1, '위로', '↑'], [1, '아래로', '↓']]) {
            const move = document.createElement('button'); move.type = 'button'; move.textContent = symbol; move.setAttribute('aria-label', `${all ? '문단' : '구역'} ${direction} 이동`); move.disabled = peerIndex + delta < 0 || peerIndex + delta >= peers.length; move.onclick = safe(() => moveItem(editor, entry.index, peers[peerIndex + delta].index)); row.append(move);
          }
        }
        list.append(row);
      }
    };
    session.refresh = () => {
      const enabled = canFormat(session);
      for (const id of richControls) { $(id).disabled = !enabled; $(id).title = enabled ? $(id).getAttribute('aria-label') || $(id).textContent : '시각 편집의 본문에서 사용할 수 있어요.'; }
      const view = session.visual;
      const selected = enabled && (view.state.selection.empty ? view.state.schema.marks.u.isInSet(view.state.storedMarks || view.state.selection.$from.marks()) : view.state.doc.rangeHasMark(view.state.selection.from, view.state.selection.to, view.state.schema.marks.u));
      $('writing-underline').setAttribute('aria-pressed', String(!!selected));
      if (session.searchOpen) {
        const result = matches(session, viewFor(session).state.doc);
        $('find-result').textContent = session.query ? (result.truncated ? '5,000개 이상 · 검색어를 좁혀 주세요' : `${result.matches.length}개 찾음`) : '찾을 내용을 입력하세요';
        for (const id of ['find-prev', 'find-next', 'replace-one', 'replace-all']) $(id).disabled = !result.matches.length || (id === 'replace-all' && result.truncated);
      }
      renderOutline();
    };
    editor.on('changeMode', () => { outlineDoc = null; queue(session); });
    document.addEventListener('selectionchange', () => queue(session));
    document.addEventListener('keydown', event => {
      if (host.closest('[hidden]') || document.querySelector('dialog[open]')) return;
      if ((event.ctrlKey || event.metaKey) && !event.altKey && ['f', 'h'].includes(event.key.toLowerCase())) {
        event.preventDefault(); if ($('writing-find-panel').hidden) toggle('find'); else $('find-query').focus();
      } else if (event.key === 'Escape' && host.contains(document.activeElement)) {
        if (!$('writing-find-panel').hidden) close('find');
        if (!$('writing-outline-panel').hidden) close('outline');
      }
    });
    session.refresh();
    return { refresh: session.refresh };
  }

  window.StudioWriting = { plugin, install, applyStyle, underline, clearStyle, moveBlock, moveSection, insertMarkdown, selectedMarkdown };
})();
