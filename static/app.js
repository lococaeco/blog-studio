'use strict';

(() => {
  const $ = id => document.getElementById(id);
  const state = { bootstrap: null, token: '', draft: null, editor: null, originalBody: '', baseline: '', loading: false, dirty: false, revision: 0, saving: null, saveTimer: null, view: 'all', query: '', focus: false, settings: false, recovery: null, uploads: 0, busy: false, deletingId: null, check: null, reviewSequence: 0, renderTimer: null, storageWarned: false };
  const compactSidebar = window.matchMedia('(max-width: 900px)');
  const fieldIds = ['title', 'excerpt', 'category', 'tags', 'post-date', 'slug', 'teaser', 'toc', 'mathjax', 'comments', 'feature', 'published'];
  const flags = ['toc', 'mathjax', 'comments', 'feature', 'published'];

  function textElement(tag, text, className) {
    const node = document.createElement(tag);
    node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function notify(message, isError = false) {
    const toast = textElement('div', message, 'toast' + (isError ? ' error' : ''));
    const close = textElement('button', '×');
    close.setAttribute('aria-label', '알림 닫기');
    close.onclick = () => toast.remove();
    toast.append(close);
    $('toast-region').append(toast);
    setTimeout(() => toast.remove(), isError ? 12000 : 5000);
  }

  function setStatus(message, type = '') {
    $('save-status').className = 'save-status ' + type;
    $('save-status').replaceChildren(textElement('span', '', 'status-dot'), document.createTextNode(message));
    $('settings-save-status').className = 'settings-save-status ' + type;
    $('settings-save-status').textContent = message;
  }

  async function api(path, method = 'GET', body) {
    let response;
    try {
      response = await fetch(path, {
        method,
        headers: { ...(method !== 'GET' ? { 'X-Studio-Token': state.token } : {}), ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}) },
        ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
        cache: 'no-store',
      });
    } catch (_) {
      throw new Error('서버에 연결할 수 없어요. 실행 중인 Blog Studio를 확인해 주세요. 작성 내용은 이 브라우저에 보관합니다.');
    }
    const result = await response.json().catch(() => ({}));
    if (!response.ok) {
      const error = new Error(result.error || `요청을 처리하지 못했어요 (${response.status}).`);
      error.status = response.status;
      throw error;
    }
    return result;
  }

  function act(callback) {
    return async event => {
      try { await callback(event); }
      catch (error) { notify(error.message || '처리 중 오류가 발생했어요.', true); }
    };
  }

  function setBusy(value) {
    state.busy = value;
    // Keep a document immutable while an awaited transition is taking its snapshot.
    $('document').inert = value;
    $('metadata-panel').inert = value;
    $('document').setAttribute('aria-busy', String(value));
  }

  function protectsRawMarkdown(draft = state.draft) {
    return !!(draft?.source_path || draft?._imported || draft?.imported);
  }

  function storageKey(name) { return 'blog-studio.' + (state.bootstrap?.site?.id || 'local') + '.' + name; }
  function recoveryKey(id) { return storageKey('recovery.' + id); }
  function readRecovery(id) {
    try { return JSON.parse(localStorage.getItem(recoveryKey(id)) || 'null'); }
    catch (_) { return null; }
  }
  function storeRecovery() {
    if (!state.draft || state.loading) return;
    try { localStorage.setItem(recoveryKey(state.draft.id), JSON.stringify({ draft: collectDraft(), at: new Date().toISOString() })); }
    catch (_) {
      if (!state.storageWarned) { notify('브라우저 복구 공간을 사용할 수 없어요. 서버 자동 저장 상태를 확인해 주세요.', true); state.storageWarned = true; }
    }
  }
  function clearRecovery(id) {
    try { localStorage.removeItem(recoveryKey(id)); } catch (_) { /* Server saves still work when browser storage is unavailable. */ }
  }

  function currentBody() {
    if (!state.editor) return state.originalBody;
    const markdown = state.editor.getMarkdown();
    return markdown === state.baseline ? state.originalBody : markdown;
  }

  function collectDraft() {
    const draft = { id: state.draft.id, version: state.draft.version, title: $('title').value, excerpt: $('excerpt').value, body: currentBody(), date: $('post-date').value, category: $('category').value, tags: [...new Set($('tags').value.split(',').map(tag => tag.trim()).filter(Boolean))], slug: $('slug').value.trim(), teaser: $('teaser').value.trim() };
    for (const flag of flags) draft[flag] = $(flag).checked;
    return draft;
  }

  function resizeTextareas() {
    for (const id of ['title', 'excerpt']) { const field = $(id); field.style.height = 'auto'; field.style.height = field.scrollHeight + 'px'; }
  }

  function changed() {
    if (state.loading || !state.draft) return;
    state.dirty = true;
    state.revision += 1;
    setStatus('저장 대기 중', 'busy');
    storeRecovery();
    clearTimeout(state.saveTimer);
    state.saveTimer = setTimeout(() => saveDraft().catch(() => {}), 900);
    updateDocumentInfo();
  }

  async function saveDraft(manual = false) {
    clearTimeout(state.saveTimer);
    if (!state.draft || state.deletingId === state.draft.id) return;
    if (state.saving) {
      await state.saving;
      if (state.dirty) return saveDraft(manual);
      return state.draft;
    }
    if (!state.dirty) { if (manual) notify('최신 내용이 저장되어 있어요.'); return state.draft; }
    const revision = state.revision;
    const payload = collectDraft();
    setStatus('저장 중…', 'busy');
    state.saving = (async () => {
      try {
        const saved = await api('/api/drafts', 'POST', payload);
        state.draft = { ...state.draft, ...saved };
        if (revision === state.revision) {
          state.dirty = false;
          clearRecovery(saved.id);
          setStatus('모든 변경 저장됨');
        } else {
          storeRecovery();
          setStatus('저장 대기 중', 'busy');
        }
        updateDraftList(saved);
        if (manual) notify('초안을 저장했어요.');
        return saved;
      } catch (error) {
        storeRecovery();
        setStatus(error.status === 409 ? '저장 충돌 · 내용 보관됨' : '저장 실패 · 다시 시도', 'error');
        if (error.status === 409) error.message += ' 현재 작성 내용은 브라우저에 남아 있어요. Markdown으로 내려받아 보관한 뒤 페이지를 새로고침하면 최신 서버 초안과 복구할 내용을 확인할 수 있어요.';
        notify(error.message, true);
        throw error;
      } finally { state.saving = null; }
    })();
    const result = await state.saving;
    if (state.dirty) return saveDraft(false);
    return result;
  }

  function updateDraftList(draft) {
    if (!state.bootstrap) return;
    const item = { id: draft.id, title: draft.title, updated_at: draft.updated_at, source_path: draft.source_path };
    const index = state.bootstrap.drafts.findIndex(existing => existing.id === draft.id);
    if (index >= 0) state.bootstrap.drafts[index] = item;
    else state.bootstrap.drafts.unshift(item);
    renderList();
  }

  async function refreshBootstrap() {
    state.bootstrap = await api('/api/bootstrap');
    state.token = state.bootstrap.token;
    state.bootstrap.posts ||= [];
    state.bootstrap.drafts ||= [];
    state.bootstrap.templates ||= [];
    const currentCategory = $('category').value;
    $('category').replaceChildren();
    for (const category of state.bootstrap.categories || []) {
      const option = textElement('option', category.label);
      option.value = category.id;
      $('category').append(option);
    }
    if ([...$('category').options].some(option => option.value === currentCategory)) $('category').value = currentCategory;
    const site = state.bootstrap.site || {};
    $('repo-name').textContent = site.repository || site.title || '로컬 작업 공간';
    $('repo-name').title = site.repository || '';
    $('timezone-label').textContent = (site.timezone || 'Asia/Seoul') + ' 기준';
    renderList();
    $('connection-error').hidden = true;
  }

  function formatDate(value) {
    if (!value) return '';
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return String(value).slice(0, 10);
    return new Intl.DateTimeFormat('ko-KR', { year: 'numeric', month: '2-digit', day: '2-digit', timeZone: state.bootstrap?.site?.timezone || 'Asia/Seoul' }).format(parsed);
  }

  function renderList() {
    const data = state.bootstrap;
    if (!data) return;
    $('count-all').textContent = String(data.posts.length);
    $('count-drafts').textContent = String(data.drafts.length);
    $('list-heading').textContent = state.view === 'templates' ? '시작이 쉬워지는 틀' : state.view === 'drafts' ? '임시저장한 초안' : '내 블로그';
    for (const button of document.querySelectorAll('[data-view]')) button.classList.toggle('active', button.dataset.view === state.view);
    const query = state.query.trim().toLocaleLowerCase();
    let items = state.view === 'drafts' ? data.drafts.map(item => ({ ...item, kind: 'draft' })) : state.view === 'templates' ? data.templates.map(item => ({ ...item, kind: 'template', title: item.label })) : data.posts.map(item => ({ ...item, kind: 'post' }));
    items = items.filter(item => !query || [item.title, item.path, item.description, ...(item.tags || [])].filter(Boolean).join(' ').toLocaleLowerCase().includes(query));
    if (state.view === 'drafts') items.sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at)));
    const fragment = document.createDocumentFragment();
    for (const item of items) {
      const button = textElement('button', '', 'post-item');
      button.classList.toggle('active', !!state.draft && (item.kind === 'draft' ? item.id === state.draft.id : item.kind === 'post' && item.path === state.draft.source_path));
      button.append(textElement('span', item.title || '제목 없는 글', 'post-item-title'));
      const meta = textElement('span', '', 'post-item-meta');
      if (item.kind === 'draft') meta.append(textElement('span', '', 'draft-dot'));
      meta.append(document.createTextNode(item.kind === 'template' ? item.description || '템플릿으로 시작하기' : item.kind === 'draft' ? formatDate(item.updated_at) + ' 임시저장' : formatDate(item.date) + (item.published === false ? ' · 비공개' : '')));
      button.append(meta);
      button.onclick = act(async () => {
        if (state.busy) return;
        if (item.kind === 'template') await newDraft(item);
        else await openDraft(item.kind === 'post' ? '/api/posts?path=' + encodeURIComponent(item.path) : '/api/drafts/' + encodeURIComponent(item.id));
      });
      if (item.kind === 'draft') {
        const row = textElement('div', '', 'post-row');
        row.dataset.draftId = item.id;
        const remove = textElement('button', '삭제', 'draft-delete');
        remove.type = 'button';
        remove.title = '임시저장 초안 삭제';
        remove.setAttribute('aria-label', (item.title || '제목 없는 글') + ' 초안 삭제');
        remove.onclick = act(() => deleteDraft(item.id));
        row.append(button, remove);
        fragment.append(row);
      } else {
        fragment.append(button);
      }
    }
    if (!items.length) fragment.append(textElement('div', query ? '검색 결과가 없어요.' : state.view === 'drafts' ? '아직 작성 중인 글이 없어요.\n새 글로 이야기를 시작해 보세요.' : '아직 글이 없어요.', 'empty-list'));
    $('post-list').replaceChildren(fragment);
    renderHome();
  }

  function renderHome() {
    const data = state.bootstrap;
    if (!data) return;
    $('home-draft-count').textContent = String(data.drafts.length);
    const recent = [...data.drafts].sort((a, b) => String(b.updated_at).localeCompare(String(a.updated_at))).slice(0, 3);
    const cards = recent.map(draft => {
      const card = textElement('button', '', 'draft-card');
      card.append(textElement('span', draft.source_path ? '수정 중' : '임시저장', 'draft-card-tag'), textElement('strong', draft.title || '제목 없는 글'), textElement('small', formatDate(draft.updated_at) + ' 저장'));
      card.onclick = act(async () => {
        if (state.busy) return;
        state.view = 'drafts';
        await openDraft('/api/drafts/' + encodeURIComponent(draft.id));
      });
      return card;
    });
    if (!cards.length) cards.push(textElement('div', '아직 작성 중인 글이 없어요. 새 글을 시작하면 이곳에서 다시 이어 쓸 수 있습니다.', 'home-empty'));
    $('recent-drafts').replaceChildren(...cards);
    $('home-templates').replaceChildren(...data.templates.filter(template => template.body).slice(0, 3).map((template, index) => {
      const card = textElement('button', '', 'template-card');
      const icon = textElement('span', ['Aa', '</>', '✎'][index], 'template-icon');
      icon.setAttribute('aria-hidden', 'true');
      const label = textElement('span', '');
      label.append(textElement('strong', template.label), textElement('small', template.description || '템플릿으로 시작하기'));
      card.append(icon, label);
      card.onclick = act(() => newDraft(template));
      return card;
    }));
  }

  async function goHome() {
    if (state.busy) return;
    if (state.uploads) throw new Error('이미지 업로드가 끝나면 홈으로 이동해 주세요.');
    setBusy(true);
    try {
      await saveDraft();
      state.draft = null;
      state.settings = false;
      $('document').hidden = true;
      $('welcome').hidden = false;
      $('publish-button').disabled = true;
      $('document-label').textContent = '내 작업 공간';
      document.title = 'Blog Studio';
      updateSettings();
      renderList();
      closeSidebar();
      setStatus('준비 완료');
      window.scrollTo({ top: 0 });
    } finally { setBusy(false); }
  }

  async function openDraft(path) {
    if (state.busy) return;
    if (state.uploads) throw new Error('이미지 업로드가 끝나면 다른 글을 열어주세요.');
    setBusy(true);
    try {
      await saveDraft();
      const draft = await api(path);
      await loadDraft(draft);
      updateDraftList(draft);
    }
    finally { setBusy(false); }
  }

  async function newDraft(template) {
    if (state.busy) return;
    if (state.uploads) throw new Error('이미지 업로드가 끝나면 새 글을 만들어주세요.');
    setBusy(true);
    try {
      await saveDraft();
      const payload = template ? { body: template.body || '' } : {};
      const draft = await api('/api/drafts', 'POST', payload);
      await loadDraft(draft);
      state.view = 'drafts';
      updateDraftList(draft);
    } finally { setBusy(false); }
    $('title').focus();
  }

  function ensureCategory(value) {
    if (value && ![...$('category').options].some(option => option.value === value)) {
      const option = textElement('option', value);
      option.value = value;
      $('category').append(option);
    }
    $('category').value = value || $('category').options[0]?.value || '';
  }

  async function loadDraft(draft, skipRecovery = false) {
    clearTimeout(state.saveTimer);
    state.loading = true;
    state.draft = draft;
    state.originalBody = draft.body || '';
    state.dirty = false;
    state.revision = 0;
    $('welcome').hidden = true;
    $('document').hidden = false;
    $('publish-button').disabled = false;
    $('title').value = draft.title || '';
    $('excerpt').value = draft.excerpt || '';
    $('tags').value = (draft.tags || []).join(', ');
    $('post-date').value = String(draft.date || '').slice(0, 10);
    $('slug').value = draft.slug || '';
    $('teaser').value = draft.teaser || '';
    ensureCategory(draft.category);
    for (const flag of flags) $(flag).checked = draft[flag] !== undefined ? !!draft[flag] : ['toc', 'comments', 'published'].includes(flag);
    $('slug').readOnly = !!draft.source_path;
    $('post-date').readOnly = !!draft.source_path;
    $('category').disabled = !!draft.source_path;
    $('category').title = draft.source_path ? '기존 글의 주소를 유지하기 위해 카테고리를 고정해요.' : '';
    $('post-date').title = draft.source_path ? '기존 글의 날짜와 주소를 유지해요.' : '';
    $('slug-help').textContent = draft.source_path ? '기존 글의 주소는 유지해요.' : '비워두면 제목으로 자동 생성해요.';
    $('source-field').hidden = !draft.source_path;
    $('source-path').textContent = draft.source_path || '';
    if (!state.editor) createEditor();
    state.editor.changeMode(protectsRawMarkdown(draft) ? 'markdown' : 'wysiwyg', true);
    state.editor.setMarkdown(state.originalBody, false);
    state.baseline = state.editor.getMarkdown();
    state.loading = false;
    state.recovery = !skipRecovery ? readRecovery(draft.id) : null;
    if (state.recovery && (!state.recovery.draft || JSON.stringify(comparable(state.recovery.draft)) === JSON.stringify(comparable(draft)))) { clearRecovery(draft.id); state.recovery = null; }
    $('recovery-banner').hidden = !state.recovery;
    setStatus('모든 변경 저장됨');
    updateMode();
    updateDocumentInfo();
    updateSettings();
    resizeTextareas();
    renderList();
    closeSidebar();
    try { localStorage.setItem(storageKey('last-draft'), draft.id); } catch (_) { /* Optional convenience. */ }
    window.scrollTo({ top: 0 });
  }

  function comparable(draft) {
    const keys = ['title', 'excerpt', 'body', 'date', 'slug', 'category', 'tags', 'toc', 'mathjax', 'comments', 'feature', 'published', 'teaser'];
    return Object.fromEntries(keys.map(key => [key, draft[key]]));
  }

  function createEditor() {
    if (!window.toastui?.Editor) throw new Error('편집기를 불러오지 못했어요. vendor 파일이 준비되어 있는지 확인해 주세요.');
    state.editor = new toastui.Editor({
      el: $('editor'), height: 'auto', minHeight: '430px', initialEditType: 'wysiwyg', previewStyle: $('editor').clientWidth < 800 ? 'tab' : 'vertical', hideModeSwitch: true, usageStatistics: false, language: 'ko-KR', placeholder: '여기에 첫 문장을 적어보세요. 이미지를 붙여넣을 수도 있어요.', autofocus: false,
      linkAttributes: { target: '_blank', rel: 'noopener noreferrer' },
      customHTMLSanitizer: html => window.DOMPurify ? window.DOMPurify.sanitize(html, { USE_PROFILES: { html: true } }) : '',
      toolbarItems: [['heading', 'bold', 'italic', 'strike'], ['hr', 'quote'], ['ul', 'ol', 'task'], ['table', 'link', 'image'], ['code', 'codeblock']],
      hooks: { addImageBlobHook: (blob, callback) => {
        // Insert the repository URL when the asynchronous upload completes.
        uploadImage(blob)
          .then(result => callback(result.url, blob.name || '이미지'))
          .catch(error => notify(error.message, true));
        return false;
      } },
      events: { change: () => { changed(); queueRenderImages(); }, changeMode: () => updateMode() },
    });
    const observer = new MutationObserver(records => {
      if (records.some(record => record.type === 'childList' && [...record.addedNodes].some(node => node.nodeType === Node.ELEMENT_NODE))) queueRenderImages();
    });
    observer.observe($('editor'), { childList: true, subtree: true });
    let previewStyle = $('editor').clientWidth < 800 ? 'tab' : 'vertical';
    new ResizeObserver(entries => {
      const width = entries[0]?.contentRect.width;
      if (!width) return;
      const next = width < 800 ? 'tab' : 'vertical';
      if (next !== previewStyle) { state.editor.changePreviewStyle(next); previewStyle = next; }
    }).observe($('editor'));
    installClipboardImageHandlers();
  }

  function installClipboardImageHandlers() {
    const frame = $('editor');
    const imageFiles = transfer => {
      if (!transfer) return [];
      const items = [...(transfer.items || [])]
        .filter(item => item.kind === 'file' && item.type.startsWith('image/'))
        .map(item => item.getAsFile()).filter(Boolean);
      return items.length ? items : [...(transfer.files || [])].filter(file => file.type.startsWith('image/'));
    };
    const insert = (event, transfer) => {
      const files = imageFiles(transfer);
      if (!files.length) return;
      // Handle binary images once, before both Toast UI and ProseMirror's paste handlers.
      // Ordinary text/HTML continues through the editor's native clipboard pipeline.
      event.preventDefault();
      event.stopImmediatePropagation();
      if (!state.draft || state.busy) return;
      if (event.type === 'drop' && (event.clientX || event.clientY)) {
        const range = document.caretRangeFromPoint?.(event.clientX, event.clientY);
        if (range && frame.contains(range.startContainer) && range.startContainer.parentElement?.closest('[contenteditable="true"]')) {
          state.editor.focus();
          const selection = window.getSelection();
          selection.removeAllRanges();
          selection.addRange(range);
        }
      }
      insertImages(files).catch(error => notify(error.message, true));
    };
    frame.addEventListener('paste', event => insert(event, event.clipboardData), true);
    frame.addEventListener('drop', event => insert(event, event.dataTransfer), true);
    frame.addEventListener('dragover', event => {
      if ([...(event.dataTransfer?.types || [])].includes('Files')) event.preventDefault();
    }, true);
  }

  function imageURL(source) {
    if (!source) return '';
    let value = String(source).trim().replace(/\{\{\s*site\.(?:url|baseurl)\s*\}\}/g, '');
    if (/^(?:https?:|data:image\/|blob:|\/\/)/i.test(value)) return value;
    if (/^(?:data:|javascript:|vbscript:)/i.test(value)) return '';
    if (value.startsWith('/uploads/')) return value;
    if (value.startsWith('/assets/')) return value;
    if (value.startsWith('assets/')) return '/' + value;
    if (value.startsWith('/')) return '/assets/' + value.slice(1);
    const base = state.draft?.source_path ? state.draft.source_path.split('/').slice(0, -1).join('/') + '/' : '';
    const resolved = new URL(value, 'http://studio.local/' + base).pathname;
    return resolved.startsWith('/assets/') ? resolved : '/assets' + resolved;
  }

  function queueRenderImages() {
    clearTimeout(state.renderTimer);
    state.renderTimer = setTimeout(() => {
      // Alter only the read-only Markdown preview. Changing WYSIWYG image DOM could rewrite the document.
      for (const img of $('editor').querySelectorAll('.toastui-editor-md-preview img')) {
        const source = img.getAttribute('src');
        if (img.dataset.studioSource === source) continue;
        const resolved = imageURL(source);
        if (resolved && source !== resolved) { img.setAttribute('src', resolved); img.dataset.studioSource = resolved; }
      }
      if ($('mathjax').checked && window.renderMathInElement) {
        const preview = $('editor').querySelector('.toastui-editor-md-preview .toastui-editor-contents');
        if (preview) {
          try { window.renderMathInElement(preview, { delimiters: [{ left: '$$', right: '$$', display: true }, { left: '\\[', right: '\\]', display: true }, { left: '\\(', right: '\\)', display: false }, { left: '$', right: '$', display: false }], throwOnError: false, trust: false, strict: 'ignore', ignoredClasses: ['katex', 'katex-display'] }); }
          catch (_) { /* Raw source remains editable when a formula cannot be rendered. */ }
        }
      }
    }, 150);
  }

  function updateDocumentInfo() {
    if (!state.draft) return;
    const title = $('title').value.trim() || '제목 없는 글';
    $('document-label').textContent = title;
    document.title = title + ' · Blog Studio';
    $('document-kind').textContent = state.draft.source_path ? '기존 글 수정' : '임시저장 초안';
    $('document-category').textContent = $('category').selectedOptions[0]?.textContent || '나의 이야기';
    const body = currentBody();
    const chars = body.replace(/\s/g, '').length;
    $('word-count').textContent = chars.toLocaleString('ko-KR') + '자 · 읽는 시간 ' + Math.max(1, Math.ceil(chars / 500)) + '분';
    const teaser = imageURL($('teaser').value.trim());
    $('teaser-preview').hidden = !teaser;
    if (teaser && $('teaser-preview').getAttribute('src') !== teaser) $('teaser-preview').src = teaser;
    resizeTextareas();
  }

  function updateMode() {
    if (!state.editor) return;
    const visual = state.editor.isWysiwygMode();
    $('mode-visual').setAttribute('aria-pressed', String(visual));
    $('mode-markdown').setAttribute('aria-pressed', String(!visual));
    $('mode-notice').hidden = !protectsRawMarkdown();
    $('mode-notice').textContent = visual ? '시각 편집을 사용하면 Markdown 서식이 정리될 수 있어요. 수식·Liquid·복잡한 HTML은 Markdown 모드를 권장해요.' : '기존 글의 원문을 유지하며 편집 중이에요. 미리보기의 테마·Liquid·수식은 실제 사이트와 다를 수 있어요.';
    queueRenderImages();
  }

  async function changeMode(mode) {
    if (!state.editor) return;
    if (mode === 'wysiwyg' && !state.editor.isWysiwygMode() && protectsRawMarkdown()) {
      const accepted = await confirm('시각 편집으로 전환할까요?', '수식, Liquid 태그, 복잡한 HTML이 포함된 글은 서식이 달라질 수 있어요. 원문을 정확히 유지하려면 Markdown 모드에서 작성해 주세요.', '시각 편집으로 전환');
      if (!accepted) return;
    }
    state.editor.changeMode(mode, true);
    updateMode();
  }

  async function uploadImage(file) {
    if (!state.draft) throw new Error('먼저 글을 열어주세요.');
    if (!file.type.startsWith('image/')) throw new Error('이미지 파일을 선택해 주세요.');
    if (file.size > 12 * 1024 * 1024) throw new Error('이미지는 12 MB 이하로 올려주세요.');
    const draftId = state.draft.id;
    state.uploads += 1;
    $('upload-indicator').hidden = false;
    $('upload-indicator').textContent = '이미지 ' + state.uploads + '개 업로드 중…';
    try {
      const data = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result).split(',')[1]);
        reader.onerror = () => reject(new Error('이미지 파일을 읽지 못했어요.'));
        reader.readAsDataURL(file);
      });
      const result = await api('/api/upload', 'POST', { draft_id: draftId, name: file.name || 'image.png', data });
      if (state.draft.id !== draftId) throw new Error('이미지를 올리던 글이 변경됐어요. 해당 글에서 다시 넣어주세요.');
      return result;
    } finally {
      state.uploads -= 1;
      $('upload-indicator').hidden = state.uploads === 0;
      $('upload-indicator').textContent = '이미지 ' + state.uploads + '개 업로드 중…';
    }
  }

  async function insertImages(files) {
    for (const file of files) {
      const result = await uploadImage(file);
      state.editor.exec('addImage', { imageUrl: result.url, altText: file.name.replace(/\.[^.]+$/, '') || '이미지' });
    }
    changed();
  }

  function updateSettings() {
    const panel = $('metadata-panel');
    const open = !!state.draft && state.settings;
    panel.hidden = false;
    if (open && !panel.open) panel.showModal();
    else if (!open && panel.open) panel.close();
    $('metadata-button').disabled = !state.draft;
    $('metadata-button').setAttribute('aria-expanded', String(open));
  }
  function syncSidebar() {
    const sidebar = $('sidebar');
    const open = compactSidebar.matches && sidebar.classList.contains('open');
    sidebar.inert = compactSidebar.matches && !open;
    sidebar.setAttribute('aria-hidden', String(sidebar.inert));
    $('menu-toggle').setAttribute('aria-expanded', String(open));
    $('sidebar-backdrop').hidden = !open;
    document.querySelector('.workspace').inert = open;
    if (open) { sidebar.setAttribute('role', 'dialog'); sidebar.setAttribute('aria-modal', 'true'); }
    else { sidebar.removeAttribute('role'); sidebar.removeAttribute('aria-modal'); }
  }
  function closeSidebar(restoreFocus = true) {
    const wasOpen = $('sidebar').classList.contains('open');
    $('sidebar').classList.remove('open');
    syncSidebar();
    if (wasOpen && restoreFocus && compactSidebar.matches) $('menu-toggle').focus();
  }
  function openSidebar() {
    if (!compactSidebar.matches) return;
    if (state.focus) {
      state.focus = false;
      $('app').classList.remove('focus-mode');
      $('focus-button').setAttribute('aria-pressed', 'false');
    }
    $('sidebar').classList.add('open');
    syncSidebar();
    $('new-post').focus();
  }

  function showDialog(id) {
    const dialog = $(id);
    if (!dialog.open) dialog.showModal();
  }

  function confirm(title, description, accept = '계속') {
    return new Promise(resolve => {
      const dialog = $('confirm-dialog');
      $('confirm-title').textContent = title;
      $('confirm-description').textContent = description;
      $('confirm-accept').textContent = accept;
      let complete = false;
      const finish = value => { if (complete) return; complete = true; dialog.close(); dialog.oncancel = null; resolve(value); };
      $('confirm-cancel').onclick = () => finish(false);
      $('confirm-accept').onclick = () => finish(true);
      dialog.oncancel = event => { event.preventDefault(); finish(false); };
      showDialog('confirm-dialog');
    });
  }

  function callout(message, type = '') { return textElement('div', message, 'callout ' + type); }

  async function reviewPublish() {
    if (!state.draft || state.busy) return;
    if (state.uploads) throw new Error('이미지 업로드가 끝나면 발행 준비를 눌러주세요.');
    await saveDraft();
    const sequence = ++state.reviewSequence;
    const draftId = state.draft.id;
    $('publish-checks').replaceChildren(textElement('p', '본문과 변경 파일을 확인하고 있어요…', 'dialog-note'));
    $('publish-result').hidden = true;
    $('publish-options').hidden = false;
    $('confirm-publish').hidden = false;
    $('confirm-publish').disabled = true;
    state.check = null;
    showDialog('publish-dialog');
    try {
      const check = await api('/api/check', 'POST', { id: draftId });
      if (sequence !== state.reviewSequence || !$('publish-dialog').open || state.draft?.id !== draftId) return;
      state.check = check;
      const content = document.createDocumentFragment();
      for (const error of check.errors || []) content.append(callout(error, 'error'));
      for (const warning of check.warnings || []) content.append(callout(warning, 'warning'));
      if (!(check.errors || []).length) content.append(callout('글을 저장할 준비가 됐어요. 아래 경로와 적용 방법을 확인해 주세요.'));
      if (!$('published').checked) content.append(callout('사이트 공개가 꺼져 있어요. 저장하거나 전송해도 사이트에서 이 글을 표시하지 않아요.', 'warning'));
      const detail = textElement('dl', '', 'check-details');
      detail.append(textElement('dt', '글 파일'), textElement('dd', check.path || '아직 정해지지 않음'));
      if (check.url) {
        detail.append(textElement('dt', '예상 글 주소'));
        const dd = textElement('dd', check.url, 'check-url');
        detail.append(dd);
      }
      detail.append(textElement('dt', '함께 저장할 파일'));
      const files = textElement('ul', '', 'check-files');
      for (const file of check.files || []) files.append(textElement('li', file));
      const filesDD = textElement('dd', ''); filesDD.append(files); detail.append(filesDD);
      const git = check.git || {};
      detail.append(textElement('dt', 'Git 상태'), textElement('dd', git.available ? [git.branch || '브랜치 미확인', git.remote || '원격 저장소 없음', git.ahead ? `앞선 커밋 ${git.ahead}개` : '', git.behind ? `뒤처진 커밋 ${git.behind}개` : ''].filter(Boolean).join(' · ') : 'Git을 사용할 수 없어요. 파일로 저장할 수 있어요.'));
      content.append(detail);
      $('publish-checks').replaceChildren(content);
      const commitRadio = document.querySelector('[name="publish-mode"][value="commit"]');
      const pushRadio = document.querySelector('[name="publish-mode"][value="push"]');
      commitRadio.disabled = !git.available;
      pushRadio.disabled = !git.available || !git.remote;
      const selected = document.querySelector('[name="publish-mode"]:checked');
      if (!selected || selected.disabled) document.querySelector('[name="publish-mode"][value="save"]').checked = true;
      updatePublishAction();
      $('confirm-publish').disabled = !!(check.errors || []).length;
    } catch (error) {
      if (sequence !== state.reviewSequence || !$('publish-dialog').open) return;
      $('publish-checks').replaceChildren(callout(error.message, 'error'));
      throw error;
    }
  }

  function updatePublishAction() {
    const mode = document.querySelector('[name="publish-mode"]:checked')?.value || 'save';
    $('confirm-publish').textContent = { save: '파일로 저장', commit: '저장하고 커밋', push: 'GitHub에 전송' }[mode];
  }

  async function publish() {
    if (state.busy || !state.check) return;
    setBusy(true);
    const mode = document.querySelector('[name="publish-mode"]:checked').value;
    $('confirm-publish').disabled = true;
    $('confirm-publish').textContent = mode === 'push' ? 'GitHub에 전송 중…' : '적용 중…';
    for (const button of $('publish-dialog').querySelectorAll('[data-close]')) button.disabled = true;
    try {
      const result = await api('/api/publish', 'POST', { id: state.draft.id, version: state.check.version ?? state.draft.version, mode });
      if (result.draft) await loadDraft(result.draft, true);
      $('publish-options').hidden = true;
      $('confirm-publish').hidden = true;
      $('publish-result').hidden = false;
      const message = result.push_error ? '로컬 저장과 커밋을 마쳤지만 GitHub 전송에 실패했어요. 내용은 로컬에 보관되어 있어요.' : mode === 'push' && result.pushed ? 'GitHub에 전송했어요. 사이트 빌드와 게시 완료 여부를 확인해 주세요.' : mode === 'commit' ? '로컬 저장과 커밋을 마쳤어요. GitHub에는 아직 전송하지 않았어요.' : '블로그 파일에 저장했어요. GitHub에는 아직 전송하지 않았어요.';
      const resultBox = callout(message, result.push_error ? 'warning' : '');
      if (result.push_error) resultBox.append(textElement('div', result.push_error));
      if (result.commit) resultBox.append(textElement('div', '커밋 ' + result.commit.slice(0, 12)));
      if (result.pushed && result.url && /^https?:\/\//i.test(result.url)) {
        const link = textElement('a', '사이트 열기 ↗'); link.href = result.url; link.target = '_blank'; link.rel = 'noopener noreferrer'; resultBox.append(document.createElement('br'), link);
      }
      $('publish-result').replaceChildren(resultBox);
      await refreshBootstrap();
      notify(result.message || message, !!result.push_error);
    } catch (error) {
      $('publish-result').hidden = false;
      $('publish-result').replaceChildren(callout(error.message, 'error'));
      $('confirm-publish').disabled = false;
      updatePublishAction();
      // A failed Git push may still have produced a local commit. Fresh validation is required before retrying.
      state.check = null;
      $('confirm-publish').disabled = true;
      $('publish-result').append(textElement('p', '편집으로 돌아가 다시 발행 준비를 눌러 현재 저장소 상태를 확인해 주세요.', 'dialog-note'));
    } finally {
      setBusy(false);
      for (const button of $('publish-dialog').querySelectorAll('[data-close]')) button.disabled = false;
    }
  }

  async function showHistory() {
    if (!state.draft || state.busy) return;
    if (state.uploads) throw new Error('이미지 업로드가 끝나면 저장 기록을 열어주세요.');
    setBusy(true);
    try { await saveDraft(); }
    finally { setBusy(false); }
    const draftId = state.draft.id;
    $('history-list').replaceChildren(textElement('p', '저장 기록을 불러오고 있어요…', 'dialog-note'));
    showDialog('history-dialog');
    const result = await api('/api/history?id=' + encodeURIComponent(draftId));
    if (!$('history-dialog').open || state.draft?.id !== draftId) return;
    $('history-list').replaceChildren();
    for (const item of result.items || []) {
      const row = textElement('div', '', 'history-item');
      const description = textElement('div', '');
      description.append(textElement('strong', item.title || '제목 없는 글'), textElement('small', new Date(item.at).toLocaleString('ko-KR', { timeZone: state.bootstrap?.site?.timezone || 'Asia/Seoul' })));
      const button = textElement('button', '복원');
      button.onclick = act(async () => {
        if (state.draft?.id !== draftId || state.busy) return;
        if (state.uploads) throw new Error('이미지 업로드가 끝나면 이전 버전을 복원해 주세요.');
        const yes = await confirm('이 버전으로 돌아갈까요?', '선택한 저장 기록의 내용으로 초안을 복원합니다. 현재 내용도 저장 기록에 보관돼요.', '이 버전 복원');
        if (!yes) return;
        setBusy(true);
        try {
          const draft = await api('/api/restore', 'POST', { id: state.draft.id, history_id: item.id, version: state.draft.version });
          clearRecovery(draft.id);
          await loadDraft(draft, true);
          $('history-dialog').close();
          updateDraftList(draft);
          notify('이전 버전으로 복원했어요.');
        } finally { setBusy(false); }
      });
      row.append(description, button); $('history-list').append(row);
    }
    if (!(result.items || []).length) $('history-list').append(textElement('p', '아직 이전 저장 기록이 없어요.', 'empty-list'));
  }

  function fallbackMarkdown() {
    const draft = collectDraft();
    // JSON is valid YAML: keep unknown source metadata in the emergency export too.
    const metadata = { ...(state.draft._meta || {}), title: draft.title, excerpt: draft.excerpt, date: draft.date, categories: state.draft.source_path && state.draft._meta?.categories ? state.draft._meta.categories : (state.bootstrap.categories.find(category => category.id === draft.category)?.categories || [draft.category]), tags: draft.tags };
    for (const flag of flags) metadata[flag] = draft[flag];
    if (draft.teaser) metadata.teaser = draft.teaser;
    else delete metadata.teaser;
    return '---\n' + JSON.stringify(metadata, null, 2) + '\n---\n' + draft.body;
  }

  async function exportMarkdown() {
    if (!state.draft || state.busy) return;
    setBusy(true);
    try {
      let blob;
      try {
        await saveDraft();
        const response = await fetch('/api/export?id=' + encodeURIComponent(state.draft.id), { cache: 'no-store' });
        if (!response.ok) throw new Error('서버 내려받기 실패');
        blob = await response.blob();
      } catch (_) {
        blob = new Blob([fallbackMarkdown()], { type: 'text/markdown;charset=utf-8' });
        notify('현재 편집 내용을 복구용 Markdown으로 내려받아요. 본문과 메타데이터를 보관하며 YAML 서식은 정리될 수 있어요.', true);
      }
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url; link.download = (($('title').value.trim() || 'untitled').replace(/[\\/:*?"<>|]/g, '-') + '.md');
      document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } finally { setBusy(false); }
  }

  async function importMarkdown(file) {
    if (!file) return;
    if (state.busy) return;
    if (state.uploads) throw new Error('이미지 업로드가 끝나면 Markdown을 가져와 주세요.');
    if (file.size > 4 * 1024 * 1024) throw new Error('Markdown 파일은 4 MB 이하로 가져와 주세요.');
    setBusy(true);
    try {
      await saveDraft();
      const draft = await api('/api/import', 'POST', { name: file.name, text: await file.text() });
      await loadDraft({ ...draft, _imported: true });
      state.view = 'drafts'; updateDraftList(draft);
      notify('Markdown을 새 초안으로 가져왔어요.');
    } finally { setBusy(false); }
  }

  async function deleteDraft(id = state.draft?.id) {
    if (!id) return;
    if (state.busy) return;
    if (state.uploads) throw new Error('이미지 업로드가 끝나면 초안을 삭제해 주세요.');
    const isActive = state.draft?.id === id;
    const item = isActive ? state.draft : state.bootstrap.drafts.find(draft => draft.id === id);
    if (!item) return;
    const title = (isActive ? $('title').value : item.title) || '제목 없는 글';
    setBusy(true);
    try {
      const yes = await confirm('이 초안을 삭제할까요?', '“' + title + '”의 임시저장 초안을 삭제해요. 이미 블로그에 저장하거나 게시한 원본 글은 그대로 남아요.', '초안 삭제');
      if (!yes) return;
      state.deletingId = id;
      if (isActive) {
        clearTimeout(state.saveTimer);
        // A failed autosave must not prevent an explicit discard. Wait until it
        // settles so it cannot repopulate the list or restart saving after deletion.
        if (state.saving) await state.saving.catch(() => {});
        clearTimeout(state.saveTimer);
      }
      try { await api('/api/drafts/' + encodeURIComponent(id), 'DELETE'); }
      catch (error) { if (error.status !== 404) throw error; }
      clearRecovery(id);
      state.bootstrap.drafts = state.bootstrap.drafts.filter(draft => draft.id !== id);
      if (isActive) {
        state.loading = true; state.draft = null; state.dirty = false; state.recovery = null;
        $('document').hidden = true; state.settings = false; updateSettings(); $('welcome').hidden = false; $('publish-button').disabled = true;
        $('recovery-banner').hidden = true;
        $('document-label').textContent = '내 작업 공간'; document.title = 'Blog Studio';
        state.loading = false; setStatus('준비 완료');
      }
      try {
        if (localStorage.getItem(storageKey('last-draft')) === id) localStorage.removeItem(storageKey('last-draft'));
      } catch (_) { /* Optional convenience. */ }
      renderList();
      notify('임시저장 초안을 삭제했어요.');
    } finally { state.deletingId = null; setBusy(false); }
  }

  function wireEvents() {
    for (const id of fieldIds) $(id).addEventListener(['category', ...flags].includes(id) ? 'change' : 'input', changed);
    $('mathjax').addEventListener('change', queueRenderImages);
    $('new-post').onclick = act(() => newDraft());
    $('welcome-new').onclick = act(() => newDraft());
    $('welcome-import').onclick = () => $('import-file').click();
    $('home-button').onclick = act(async event => { event.preventDefault(); await goHome(); });
    $('home-drafts').onclick = () => { state.view = 'drafts'; renderList(); if (compactSidebar.matches) openSidebar(); else $('post-list').querySelector('button')?.focus(); };
    $('search').oninput = event => { state.query = event.target.value; renderList(); };
    for (const button of document.querySelectorAll('[data-view]')) button.onclick = () => { state.view = button.dataset.view; renderList(); };
    $('refresh').onclick = act(async () => { await refreshBootstrap(); notify('글 목록을 새로 불러왔어요.'); });
    $('menu-toggle').onclick = () => { if ($('sidebar').classList.contains('open')) closeSidebar(); else openSidebar(); };
    $('sidebar-backdrop').onclick = () => closeSidebar();
    compactSidebar.addEventListener('change', () => closeSidebar(false));
    syncSidebar();
    $('metadata-button').onclick = () => { state.settings = !state.settings; updateSettings(); };
    $('metadata-close').onclick = () => { state.settings = false; updateSettings(); };
    $('metadata-panel').addEventListener('close', () => { state.settings = false; $('metadata-button').setAttribute('aria-expanded', 'false'); });
    $('metadata-panel').addEventListener('click', event => { if (event.target === $('metadata-panel')) { const rect = event.target.getBoundingClientRect(); if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) { state.settings = false; updateSettings(); } } });
    $('focus-button').onclick = () => { state.focus = !state.focus; $('app').classList.toggle('focus-mode', state.focus); $('focus-button').setAttribute('aria-pressed', String(state.focus)); };
    $('mode-visual').onclick = act(() => changeMode('wysiwyg'));
    $('mode-markdown').onclick = act(() => changeMode('markdown'));
    $('insert-images').onclick = () => $('image-files').click();
    $('image-files').onchange = act(async event => { const files = [...event.target.files]; event.target.value = ''; await insertImages(files); });
    $('teaser-upload').onclick = () => $('teaser-file').click();
    $('teaser-file').onchange = act(async event => { const file = event.target.files[0]; event.target.value = ''; if (!file) return; const result = await uploadImage(file); $('teaser').value = result.url; changed(); });
    $('teaser-preview').onerror = () => { $('teaser-preview').hidden = true; };
    $('export-button').onclick = act(exportMarkdown);
    $('history-button').onclick = act(showHistory);
    $('publish-button').onclick = act(reviewPublish);
    $('confirm-publish').onclick = act(publish);
    for (const radio of document.querySelectorAll('[name="publish-mode"]')) radio.onchange = updatePublishAction;
    $('publish-dialog').addEventListener('cancel', event => { if (state.busy) event.preventDefault(); });
    $('publish-dialog').addEventListener('close', () => { state.reviewSequence += 1; state.check = null; });
    for (const button of document.querySelectorAll('[data-close]')) button.onclick = () => $(button.dataset.close).close();
    $('help-button').onclick = () => showDialog('help-dialog');
    $('import-button').onclick = () => $('import-file').click();
    $('import-file').onchange = act(async event => { const file = event.target.files[0]; event.target.value = ''; await importMarkdown(file); });
    $('delete-draft').onclick = act(() => deleteDraft());
    $('recover-button').onclick = act(async () => {
      if (!state.recovery) return;
      if (state.uploads) throw new Error('이미지 업로드가 끝나면 복구해 주세요.');
      const recovery = state.recovery.draft;
      setBusy(true);
      try {
        await saveDraft();
        const recovered = { ...state.draft, ...recovery, id: state.draft.id, version: state.draft.version, source_path: state.draft.source_path, source_revision: state.draft.source_revision };
        await loadDraft(recovered, true);
        changed();
        notify('브라우저에 남아 있던 내용을 복구했어요.');
      } finally { setBusy(false); }
    });
    $('discard-recovery').onclick = () => { clearRecovery(state.draft.id); state.recovery = null; $('recovery-banner').hidden = true; };
    document.addEventListener('keydown', event => {
      const modifier = event.ctrlKey || event.metaKey;
      if (modifier && event.key.toLowerCase() === 's') { event.preventDefault(); saveDraft(true).catch(() => {}); }
      if (document.querySelector('dialog[open]')) return;
      if (modifier && event.shiftKey && event.key.toLowerCase() === 'f') { event.preventDefault(); $('focus-button').click(); }
      if (compactSidebar.matches && $('sidebar').classList.contains('open')) {
        if (event.key === 'Escape') { event.preventDefault(); closeSidebar(); return; }
        if (event.key === 'Tab') {
          const controls = [...$('sidebar').querySelectorAll('a[href],button:not(:disabled),input:not(:disabled)')].filter(node => node.getClientRects().length && !node.hidden);
          const first = controls[0], last = controls[controls.length - 1];
          if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
        }
      }
      const editing = event.target.closest('input,textarea,select,[contenteditable="true"]');
      if (!editing && !modifier && !event.altKey) {
        if (event.key === '/') { event.preventDefault(); if (compactSidebar.matches) openSidebar(); $('search').focus(); }
        if (event.key.toLowerCase() === 'n') { event.preventDefault(); newDraft().catch(error => notify(error.message, true)); }
      }
    });
    window.addEventListener('beforeunload', event => {
      if (state.dirty || state.uploads || state.saving) { storeRecovery(); event.preventDefault(); event.returnValue = ''; }
    });
    document.addEventListener('visibilitychange', () => { if (document.hidden && state.dirty) { storeRecovery(); saveDraft().catch(() => {}); } });
    window.addEventListener('online', () => { if (state.dirty) saveDraft().catch(() => {}); });
  }

  async function start() {
    wireEvents();
    $('new-post').disabled = true; $('welcome-new').disabled = true;
    try {
      await refreshBootstrap();
      $('new-post').disabled = false; $('welcome-new').disabled = false;
      let last;
      try { last = localStorage.getItem(storageKey('last-draft')); } catch (_) { /* Optional convenience. */ }
      if (last && state.bootstrap.drafts.some(draft => draft.id === last)) await openDraft('/api/drafts/' + encodeURIComponent(last));
    } catch (error) {
      $('connection-error').hidden = false;
      $('connection-error').textContent = error.message + ' 페이지를 새로고침하면 다시 연결할 수 있어요.';
      setStatus('연결 확인 필요', 'error');
    }
  }
  start();
})();
