/* A single, extensible authoring dock. Commands keep the editor selection. */
(() => {
  'use strict';
  const sessions = new WeakMap();
  const menus = new Set();
  const $ = id => document.getElementById(id);
  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text) node.textContent = text;
    if (className) node.className = className;
    return node;
  };
  function plugin(context) {
    const session = { context, editor: context.instance, visual: null, markdown: null, refresh: null, scheduled: false };
    sessions.set(context.instance, session);
    const capture = mode => () => new context.pmState.Plugin({
      view(view) {
        session[mode] = view;
        return { update() { queue(session); }, destroy() { session[mode] = null; } };
      },
    });
    return { wysiwygPlugins: [capture('visual')], markdownPlugins: [capture('markdown')] };
  }
  function queue(session) {
    if (session.scheduled) return;
    session.scheduled = true;
    requestAnimationFrame(() => { session.scheduled = false; session.refresh?.(); });
  }
  function closeMenus(restoreFocus = false) {
    for (const menu of menus) if (!menu.panel.hidden) {
      menu.panel.hidden = true;
      menu.button.setAttribute('aria-expanded', 'false');
      if (restoreFocus) menu.button.focus({ preventScroll: true });
    }
  }
  function position(menu) {
    const box = menu.button.getBoundingClientRect(), panel = menu.panel;
    const width = Math.min(Number(panel.dataset.width) || 300, innerWidth - 24);
    panel.style.width = width + 'px';
    const left = Math.max(12, Math.min(box.left, innerWidth - width - 12));
    let top = box.bottom + 8;
    if (innerHeight - top < 180 && box.top > 240) top = Math.max(12, box.top - Math.min(panel.scrollHeight, innerHeight - 24) - 8);
    panel.style.left = left + 'px'; panel.style.top = top + 'px';
    panel.style.maxHeight = Math.max(80, innerHeight - top - 12) + 'px';
  }
  function menu(parent, id, label, options = {}) {
    const wrapper = element('div', '', 'toolbar-menu-wrap');
    const button = element('button', '', 'toolbar-button toolbar-menu-toggle');
    button.type = 'button'; button.id = id + '-toggle'; button.dataset.toolbarToggle = '';
    button.setAttribute('aria-expanded', 'false'); button.setAttribute('aria-controls', id);
    button.setAttribute('aria-haspopup', 'true'); button.setAttribute('aria-label', label);
    const name = element('span', label); name.className = 'toolbar-menu-label';
    const chevron = element('span', '⌄', 'toolbar-chevron'); chevron.setAttribute('aria-hidden', 'true');
    button.append(name, chevron);
    const panel = element('div', '', 'toolbar-popover'); panel.id = id; panel.hidden = true;
    panel.dataset.studioMenu = ''; panel.dataset.width = options.width || 300;
    panel.setAttribute('role', 'group'); panel.setAttribute('aria-label', label);
    wrapper.append(button, panel); parent.append(wrapper);
    const item = { button, panel, name };
    menus.add(item);
    const open = (keyboard = false) => {
      const wasOpen = !panel.hidden; closeMenus();
      if (wasOpen) return;
      options.beforeOpen?.(); panel.hidden = false; button.setAttribute('aria-expanded', 'true'); position(item);
      if (keyboard) panel.querySelector('button:not(:disabled),input:not(:disabled),select:not(:disabled)')?.focus({ preventScroll: true });
    };
    button.addEventListener('mousedown', event => event.preventDefault());
    button.addEventListener('click', event => open(event.detail === 0));
    button.addEventListener('keydown', event => {
      if (event.key === 'ArrowDown') { event.preventDefault(); if (panel.hidden) open(true); else panel.querySelector('button:not(:disabled),input')?.focus(); }
    });
    panel.addEventListener('mousedown', event => { if (event.target.closest('button')) event.preventDefault(); });
    panel.addEventListener('click', event => {
      const action = event.target.closest('button');
      if (action && !action.hasAttribute('data-keep-menu') && !action.hasAttribute('data-toolbar-toggle')) closeMenus();
    });
    panel.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.preventDefault(); event.stopPropagation(); closeMenus(true); }
      if (event.key === 'Tab') setTimeout(() => { if (!panel.contains(document.activeElement) && document.activeElement !== button) closeMenus(); }, 0);
      if (['ArrowDown','ArrowUp','Home','End'].includes(event.key) && !event.target.matches('input,select,textarea')) {
        const actions = [...panel.querySelectorAll('button:not(:disabled),input:not(:disabled),select:not(:disabled)')].filter(node => node.getClientRects().length);
        if (!actions.length) return;
        event.preventDefault();
        let index = actions.indexOf(document.activeElement);
        if (event.key === 'Home') index = 0;
        else if (event.key === 'End') index = actions.length - 1;
        else index = (index + (event.key === 'ArrowDown' ? 1 : -1) + actions.length) % actions.length;
        actions[index].focus({ preventScroll: true });
      }
    });
    return item;
  }
  document.addEventListener('pointerdown', event => {
    if (![...menus].some(item => item.button.contains(event.target) || item.panel.contains(event.target))) closeMenus();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && [...menus].some(item => !item.panel.hidden)) { event.preventDefault(); closeMenus(true); }
  });
  addEventListener('resize', () => { for (const item of menus) if (!item.panel.hidden) position(item); });
  addEventListener('scroll', () => { for (const item of menus) if (!item.panel.hidden) position(item); }, { capture: true, passive: true });
  function group(parent, title, className = '') {
    const section = element('section', '', 'toolbar-section ' + className);
    section.append(element('h3', title)); const content = element('div', '', 'toolbar-section-actions'); section.append(content); parent.append(section); return content;
  }
  function move(id, parent, label, description) {
    const node = $(id); if (!node) return;
    parent.append(node); node.classList.add('toolbar-action');
    if (label) { node.replaceChildren(element('span', label)); if (description) node.append(element('small', description)); }
    return node;
  }
  function command(parent, id, label, callback, options = {}) {
    const button = element('button', '', options.compact ? 'toolbar-button toolbar-icon-control' : 'toolbar-action');
    button.append(element('span', label));
    button.id = id; button.type = 'button'; button.setAttribute('aria-label', options.aria || label);
    button.title = options.aria || label; button.dataset.command = options.command || '';
    if (options.description) button.append(element('small', options.description));
    button.addEventListener('mousedown', event => event.preventDefault());
    button.onclick = callback; parent.append(button); return button;
  }
  function selectedBlocks(session) {
    if (!session.editor.isWysiwygMode() || !session.visual) return [];
    const { state } = session.visual, blocks = [];
    let invalid = false;
    state.doc.nodesBetween(state.selection.from, state.selection.to, (node, position, parent) => {
      if (node.isTextblock) {
        if (parent.type.name === 'doc' && ['paragraph','heading'].includes(node.type.name)) blocks.push({ node, position });
        else invalid = true;
        return false;
      }
    });
    return invalid ? [] : blocks;
  }
  function changeHeading(session, level) {
    const editor = session.editor;
    window.StudioAlignment.flushPendingChanges(editor);
    if (editor.isMarkdownMode()) {
      if (level) editor.exec('heading', { level });
      else {
        // TOAST's level=0 command inserts an unwanted leading space. Remove
        // heading syntax in one native transaction, retaining the selection.
        const view = session.markdown, { state } = view, edits = [], lines = [];
        state.doc.forEach((node, position) => lines.push({ node, position }));
        for (const [index, { node, position }] of lines.entries()) {
          if (position + node.nodeSize <= state.selection.from || position >= state.selection.to && !state.selection.empty) continue;
          if (state.selection.empty && (state.selection.from < position || state.selection.from >= position + node.nodeSize)) continue;
          // Ask the editor's Markdown AST before removing syntax: a '#'
          // inside a fenced code block is content, not a heading.
          if (!node.textContent.trim() || editor.getRangeInfoOfNode([index + 1, 1]).type !== 'heading') continue;
          const prefix = node.textContent.match(/^ {0,3}#{1,6}(?:[ \t]+|$)/);
          if (prefix) {
            edits.push([position + 1, position + 1 + prefix[0].length]);
            const closing = node.textContent.slice(prefix[0].length).match(/[ \t]+#+[ \t]*$/);
            if (closing) edits.push([position + 1 + node.textContent.length - closing[0].length, position + 1 + node.textContent.length]);
          } else if (node.textContent.trim() && /^ {0,3}(?:=+|-+)[ \t]*$/.test(lines[index + 1]?.node.textContent || '')) {
            const underline = lines[index + 1];
            edits.push([underline.position, underline.position + underline.node.nodeSize]);
          }
        }
        if (edits.length) {
          let tr = state.tr;
          for (const [from, to] of edits.sort((a, b) => b[0] - a[0])) tr = tr.delete(from, to);
          view.dispatch(tr.setMeta('closeHistory$', true).scrollIntoView());
          view.dispatch(view.state.tr.setMeta('closeHistory$', true));
        }
      }
      editor.focus(); return;
    }
    const blocks = selectedBlocks(session);
    if (!blocks.length) return;
    const view = session.visual, { state } = view;
    let tr = state.tr;
    for (const { node, position } of blocks) {
      const type = level ? state.schema.nodes.heading : state.schema.nodes.paragraph;
      const attrs = { ...node.attrs, htmlAttrs: { ...node.attrs.htmlAttrs }, rawHTML: node.attrs.rawHTML ? (level ? 'h' + level : 'p') : null, ...(level && { level, headingType: 'atx' }) };
      if (node.type !== type || (level && node.attrs.level !== level)) tr = tr.setNodeMarkup(position, type, attrs, node.marks);
    }
    if (tr.docChanged) {
      tr.setMeta('closeHistory$', true);
      view.dispatch(tr.scrollIntoView()); view.dispatch(view.state.tr.setMeta('closeHistory$', true));
    }
    view.focus(); queue(session);
  }
  function resourceDialog(session, kind) {
    const image = kind === 'image', prefix = 'toolbar-' + kind;
    const dialog = element('dialog', '', 'dialog small-dialog toolbar-resource-dialog');
    dialog.id = prefix + '-dialog'; dialog.setAttribute('aria-labelledby', prefix + '-title');
    const form = element('form'), title = element('h2', image ? '이미지 주소로 넣기' : '일반 링크 넣기');
    title.id = prefix + '-title'; form.append(title);
    const introduction = element('p', image ? '웹 이미지 주소나 블로그 안의 이미지 경로를 입력하세요.' : '선택한 글자에 링크를 달거나 새 링크를 넣으세요.');
    form.append(introduction);
    const fields = {};
    for (const [name, label] of [['url', image ? '이미지 주소' : '연결할 주소'], ['text', image ? '이미지 설명' : '표시할 글자']]) {
      const item = element('label', label), input = element('input');
      input.id = image ? (name === 'url' ? 'toolbar-image-url-input' : 'toolbar-image-alt') : prefix + '-' + name;
      input.type = 'text'; input.maxLength = name === 'url' ? 3000 : 1000;
      if (name === 'url') { input.required = true; input.inputMode = 'url'; input.placeholder = image ? 'https://… 또는 /assets/images/…' : 'https://… 또는 /블로그-글/'; }
      item.append(input); form.append(item); fields[name] = input;
    }
    const error = element('p', '', 'callout error'); error.id = prefix + '-error'; error.hidden = true; error.setAttribute('role', 'alert'); form.append(error);
    const actions = element('div', '', 'dialog-actions');
    const cancel = element('button', '취소', 'button subtle'); cancel.type = 'button'; cancel.onclick = () => dialog.close();
    const submit = element('button', image ? '이미지 넣기' : '링크 넣기', 'button primary'); submit.type = 'submit'; submit.id = prefix + '-insert';
    actions.append(cancel, submit); form.append(actions); dialog.append(form); $('document').append(dialog);
    let selection = null, view = null, inserted = false;
    const normalizedURL = value => {
      const decoded = document.createElement('textarea'); decoded.innerHTML = value;
      const url = decoded.value.trim();
      if (!url || /[\u0000-\u001f\u007f]/.test(url)) throw new Error('올바른 주소를 입력해 주세요.');
      let parsed;
      try { parsed = new URL(url, location.href); } catch (_) { throw new Error('올바른 주소를 입력해 주세요.'); }
      const allowed = image ? ['https:', 'http:'] : ['https:', 'http:', 'mailto:', 'tel:'];
      if (!allowed.includes(parsed.protocol)) throw new Error(image ? '웹 이미지 주소 또는 블로그 이미지 경로를 입력해 주세요.' : '웹 주소, 이메일, 전화번호 또는 블로그 경로를 입력해 주세요.');
      // Preserve relative paths and existing escapes; protect Markdown's URL
      // delimiters without re-encoding a user's already encoded URL.
      return url.replace(/[\s<>"`\\()]/g, value => /[()]/.test(value) ? '%' + value.charCodeAt(0).toString(16).toUpperCase() : encodeURIComponent(value));
    };
    form.onsubmit = event => {
      event.preventDefault();
      try {
        const url = normalizedURL(fields.url.value), text = fields.text.value || (image ? '' : fields.url.value.trim());
        view.dispatch(view.state.tr.setSelection(selection.resolve(view.state.doc)).setMeta('addToHistory', false));
        session.editor.exec(image ? 'addImage' : 'addLink', image ? { imageUrl: url, altText: text } : { linkUrl: url, linkText: text });
        inserted = true; dialog.close(); session.editor.focus(); queue(session);
      } catch (failure) { error.textContent = failure.message || '주소를 확인해 주세요.'; error.hidden = false; }
    };
    dialog.addEventListener('close', () => { if (inserted) session.editor.focus(); else $('insert-menu-toggle').focus({ preventScroll: true }); });
    return () => {
      window.StudioAlignment.flushPendingChanges(session.editor);
      view = session.editor.isWysiwygMode() ? session.visual : session.markdown;
      selection = view.state.selection.getBookmark();
      const selectedText = view.state.doc.textBetween(view.state.selection.from, view.state.selection.to, ' ');
      fields.url.value = ''; fields.text.value = selectedText;
      fields.text.readOnly = !image && session.editor.isWysiwygMode() && !view.state.selection.empty;
      inserted = false; error.hidden = true; closeMenus(); dialog.showModal(); fields.url.focus();
    };
  }
  function installWorkspace() {
    const root = $('workspace-menu-root');
    if (!root || root.children.length) return;
    const workspace = menu(root, 'workspace-menu', '작업 메뉴');
    const writing = group(workspace.panel, '나의 글쓰기 공간');
    move('personal-templates', writing, '내 템플릿·문구', '자주 쓰는 글의 틀과 문장');
    move('backup-sync', writing, '백업·동기화', '글과 초안을 보관하고 다른 PC로 이동');
    move('cloud-open', group(workspace.panel, '연결과 프로그램'), 'GitHub · 업데이트', '계정 연결, 게시 상태, 새 버전');
    new ResizeObserver(entries => {
      document.documentElement.style.setProperty('--studio-topbar-height', entries[0].target.getBoundingClientRect().height + 'px');
    }).observe(document.querySelector('.topbar'));
  }
  function install({ editor, notify }) {
    const session = sessions.get(editor), dock = $('editor-dock');
    if (!session || !dock || dock.children.length) return;
    const safe = fn => () => { try { fn(); } catch (error) { notify(error.message || '서식을 적용하지 못했어요.', true); } };
    const execute = (name, payload) => safe(() => { window.StudioAlignment.flushPendingChanges(editor); editor.exec(name, payload); editor.focus(); queue(session); });
    const row = element('div', '', 'editor-dock-row'); dock.append(row);
    const mode = document.querySelector('.mode-switch'); mode.classList.add('dock-mode'); row.append(mode);
    const heading = menu(row, 'block-style-menu', '본문', { width: 220 });
    heading.button.id = 'block-style-toggle'; heading.button.setAttribute('aria-label', '본문과 제목 스타일');
    for (const [level, label, note] of [[0,'본문','일반 문단'],[1,'제목 1','가장 큰 본문 제목'],[2,'제목 2','주요 구역 제목'],[3,'제목 3','하위 구역 제목'],[4,'제목 4','작은 구역 제목'],[5,'제목 5','세부 구역 제목'],[6,'제목 6','가장 작은 제목']]) {
      const button = command(heading.panel, level ? 'block-heading-' + level : 'block-paragraph', label, safe(() => changeHeading(session, level)), { description: note });
      button.dataset.headingLevel = level; button.setAttribute('aria-pressed', 'false');
    }
    const inline = element('div', '', 'toolbar-control-group'); inline.setAttribute('role','group'); inline.setAttribute('aria-label','기본 글자 서식'); row.append(inline);
    command(inline, 'toolbar-bold', 'B', execute('bold'), { compact: true, aria: '굵게', command: 'bold' });
    command(inline, 'toolbar-italic', 'I', execute('italic'), { compact: true, aria: '기울임', command: 'italic' });
    const underline = move('writing-underline', inline); underline.classList.add('toolbar-button','toolbar-icon-control');
    const alignment = document.querySelector('.alignment-tools'); alignment.classList.add('toolbar-control-group'); row.append(alignment);
    alignment.querySelector('.alignment-label').remove();
    const format = menu(row, 'format-menu', '서식');
    const colors = group(format.panel, '글자 꾸미기', 'toolbar-colors');
    for (const id of ['writing-color','writing-highlight']) colors.append($(id).closest('label'));
    move('writing-clear-style', colors);
    const extra = group(format.panel, '기타 서식', 'toolbar-inline-actions');
    command(extra, 'toolbar-strike', '취소선', execute('strike'), { command: 'strike' });
    command(extra, 'toolbar-code', '인라인 코드', execute('code'), { command: 'code' });
    const insert = menu(row, 'insert-menu', '삽입', { width: 320 });
    const media = group(insert.panel, '이미지와 링크');
    move('insert-images', media, '이미지 넣기', '여러 파일 선택 · 붙여넣기도 가능');
    const openLink = resourceDialog(session, 'link');
    command(media, 'toolbar-link', '일반 링크', openLink, { description: '선택한 글자 · Ctrl/⌘ + K' });
    command(media, 'toolbar-image-url', '이미지 주소', resourceDialog(session, 'image'), { description: '웹 이미지 또는 블로그 이미지 경로' });
    $('editor').addEventListener('keydown', event => {
      if ((event.ctrlKey || event.metaKey) && !event.altKey && !event.shiftKey && event.key.toLowerCase() === 'k' && event.target.closest('[contenteditable="true"]') && !$('document').inert && !document.querySelector('dialog[open]')) {
        event.preventDefault(); event.stopPropagation(); openLink();
      }
    }, true);
    const mediaHost = $('media-tools-root'); media.append(mediaHost);
    move('media-add-image', mediaHost, '이미지 다듬어 넣기', '자르기 · 크기 · 압축 · 설명');
    move('media-edit-image', mediaHost, '선택한 이미지 편집');
    move('media-link-card', mediaHost, '링크 카드 · 내 글 연결');
    const blocks = group(insert.panel, '글의 구성', 'toolbar-grid-actions');
    const commands = [ ['bulletList','글머리 목록'],['orderedList','번호 목록'],['taskList','체크 목록'],['blockQuote','인용문'],['codeBlock','코드 블록'],['hr','구분선'] ];
    for (const [name, label] of commands) command(blocks, 'toolbar-' + name, label, execute(name), { command: name });
    const table = group(insert.panel, '표');
    const tableFields = element('div', '', 'toolbar-table-fields');
    for (const [id,label,value] of [['toolbar-table-rows','행',3],['toolbar-table-columns','열',3]]) {
      const item = element('label', label), input = element('input'); input.type='number'; input.id=id; input.value=value; input.min=1; input.max=20; item.append(input); tableFields.append(item);
    }
    table.append(tableFields);
    command(table, 'toolbar-table', '표 넣기', safe(() => {
      const rowCount=Number($('toolbar-table-rows').value),columnCount=Number($('toolbar-table-columns').value);
      if (![rowCount,columnCount].every(value=>Number.isInteger(value)&&value>=1&&value<=20)) throw new Error('행과 열은 1부터 20까지 입력해 주세요.');
      window.StudioAlignment.flushPendingChanges(editor); editor.exec('addTable',{rowCount,columnCount}); editor.focus();
    }), { command: 'addTable' });
    const edit = menu(row, 'edit-menu', '편집');
    move('writing-find', group(edit.panel,'찾고 정리하기'), '찾기·바꾸기', 'Ctrl/⌘ + F');
    move('writing-outline', edit.panel.querySelector('.toolbar-section-actions'), '목차·문단 이동', '제목으로 이동 · 문단 순서 변경');
    const history = group(edit.panel,'되돌리기', 'toolbar-grid-actions');
    command(history,'toolbar-undo','실행 취소',execute('undo'),{command:'undo'});
    command(history,'toolbar-redo','다시 실행',execute('redo'),{command:'redo'});
    const documentMenu = menu(row, 'document-menu', '문서');
    const documentActions=group(documentMenu.panel,'현재 글');
    move('theme-preview',documentActions,'테마 미리보기','PC · 태블릿 · 모바일');
    move('history-button',documentActions,'기록','저장 기록과 변경 비교');
    move('export-button',documentActions,'내려받기','Markdown 파일로 보관');
    const detail = element('div', '', 'dock-detail');
    const scope=element('span','선택 없음 · 정렬은 글 전체에 적용','alignment-scope');scope.id='alignment-scope';detail.append(scope);
    detail.append(element('span','Ctrl/⌘ S 저장','dock-keyboard-hint'));dock.append(detail);
    const panels=element('div','','dock-edit-panels');dock.append(panels);
    panels.append($('writing-find-panel'),$('writing-outline-panel'));
    const source=$('writing-tools-root');source.hidden=true;
    session.refresh = () => {
      const visual=editor.isWysiwygMode(), selected=selectedBlocks(session);
      const values=new Set(selected.map(({node})=>node.type.name==='heading'?node.attrs.level:0));
      const level=values.size===1?[...values][0]:null;
      heading.name.textContent=visual?(level===null?'문단 스타일':level?'제목 '+level:'본문'):'제목';
      heading.button.disabled=visual&&!selected.length;
      for(const button of heading.panel.querySelectorAll('[data-heading-level]'))button.setAttribute('aria-pressed',String(visual&&level!==null&&Number(button.dataset.headingLevel)===level));
      const alignmentState=window.StudioAlignment.selection(editor);
      scope.textContent=visual?(alignmentState.scope==='table'?'선택한 표의 셀에 정렬 적용':alignmentState.scope==='selection'?'선택한 문단에 정렬 적용':'선택 없음 · 정렬은 글 전체에 적용'):'Markdown 원문 편집';
      for(const id of ['align-left','align-center','align-right']) $(id).title=($(id).getAttribute('aria-label')||'정렬')+' · '+(alignmentState.scope==='table'?'선택한 표의 셀':alignmentState.scope==='selection'?'선택한 문단':'글 전체');
      if(visual&&session.visual){
        const view=session.visual,{selection,schema,doc}=view.state;
        for(const [id,name] of [['toolbar-bold','strong'],['toolbar-italic','emph'],['toolbar-strike','strike'],['toolbar-code','code']]){
          const mark=schema.marks[name]; if(!mark)continue;
          $(id).setAttribute('aria-pressed',String(!!(selection.empty?mark.isInSet(view.state.storedMarks||selection.$from.marks()):doc.rangeHasMark(selection.from,selection.to,mark))));
        }
      }
    };
    editor.on('changeMode',()=>{closeMenus();queue(session);});
    document.addEventListener('selectionchange',()=>queue(session));
    session.refresh();
  }
  window.StudioToolbar={plugin,install,installWorkspace,closeMenus};
})();
