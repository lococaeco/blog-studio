/* Local image editing and portable link cards for TOAST UI Editor 3.2.2. */
(() => {
  'use strict';

  const sessions = new WeakMap();
  const schemas = new WeakSet();
  const preparedFiles = new WeakSet();
  const CARD_STYLE = 'border: 1px solid #d8e2dd; border-radius: 8px; padding: 16px; margin: 20px 0;';
  const $ = id => document.getElementById(id);

  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }

  function safeURL(value) {
    if (typeof value !== 'string' || !/^https?:\/\//i.test(value.trim())) return '';
    try {
      const url = new URL(value, location.href);
      return ['http:', 'https:'].includes(url.protocol) && !url.username && !url.password ? url.href : '';
    } catch (_) { return ''; }
  }

  function renderSpec(spec, content) {
    if (typeof spec === 'string') return document.createTextNode(spec);
    if (spec?.nodeType) return spec.cloneNode(true);
    if (spec?.dom) return spec.dom.cloneNode(true);
    const [tag, ...parts] = spec;
    const node = document.createElement(tag);
    if (parts[0] && typeof parts[0] === 'object' && !Array.isArray(parts[0])) {
      for (const [name, value] of Object.entries(parts.shift())) {
        if (value !== null && value !== undefined) node.setAttribute(name, String(value));
      }
    }
    for (const part of parts) node.append(part === 0 ? content() : renderSpec(part, content));
    return node;
  }

  function renderNode(node) {
    let output = node.isText ? document.createTextNode(node.text) : renderSpec(node.type.spec.toDOM(node), () => {
      const fragment = document.createDocumentFragment();
      node.forEach(child => fragment.append(renderNode(child)));
      return fragment;
    });
    for (let index = node.marks.length - 1; index >= 0; index -= 1) {
      const inner = output;
      output = renderSpec(node.marks[index].type.spec.toDOM(node.marks[index], true), () => inner);
      if (!output.contains(inner)) output.append(inner);
    }
    return output;
  }

  function preserveMedia(schema) {
    if (schemas.has(schema)) return;
    schemas.add(schema);
    for (const name of ['image', 'blockQuote']) {
      const node = schema.nodes[name];
      node.spec.parseDOM = node.spec.parseDOM.map(rule => ({ ...rule, getAttrs(dom) {
        const original = rule.getAttrs ? rule.getAttrs(dom) : rule.attrs;
        if (original === false) return false;
        const attrs = { ...original?.htmlAttrs };
        if (name === 'image' && dom.hasAttribute('data-studio-caption')) {
          attrs['data-studio-caption'] = dom.getAttribute('data-studio-caption').slice(0, 1000);
        }
        if (name === 'blockQuote' && dom.getAttribute('data-studio-card') === 'link') {
          attrs['data-studio-card'] = 'link';
          attrs.style = CARD_STYLE;
        }
        return Object.keys(attrs).length ? { ...original, htmlAttrs: attrs } : original || null;
      } }));
    }
    delete schema.cached.domParser;
  }

  function plugin(context) {
    const session = { view: null, selected: null, controller: null };
    sessions.set(context.instance, session);
    return {
      toMarkdownRenderers: {
        image(info, convertor) {
          if (!Object.hasOwn(info.node.attrs.htmlAttrs || {}, 'data-studio-caption')) return convertor.origin();
          return { rawHTML: DOMPurify.sanitize(renderNode(info.node).outerHTML, { USE_PROFILES: { html: true } }) };
        },
        blockQuote(info, convertor) {
          if (info.node.attrs.htmlAttrs?.['data-studio-card'] !== 'link') return convertor.origin();
          const html = DOMPurify.sanitize(renderNode(info.node).outerHTML, { USE_PROFILES: { html: true } });
          info.node = info.node.copy();
          // Raw block writers suppress the normal preceding block separator.
          // Explicit boundaries keep the card a block when following prose.
          return { rawHTML: ['\n\n' + html + '\n\n', ''] };
        },
      },
      wysiwygPlugins: [() => new context.pmState.Plugin({
        state: { init: (_, state) => preserveMedia(state.schema), apply: (_, value) => value },
        props: { handleClickOn(view, pos, node, nodePos) {
          if (node.type.name === 'image') {
            session.selected = { position: nodePos, url: node.attrs.imageUrl };
            requestAnimationFrame(() => session.controller?.refresh());
          }
          return false;
        } },
        view(view) {
          session.view = view;
          return { update() { session.controller?.refresh(); }, destroy() { session.view = null; } };
        },
      })],
    };
  }

  async function bitmap(file) {
    if (!file.type.startsWith('image/')) throw new Error('이미지 파일을 선택해 주세요.');
    if (file.size > 32 * 1024 * 1024) throw new Error('편집할 이미지는 32 MB 이하로 선택해 주세요.');
    let result;
    try { result = await createImageBitmap(file); }
    catch (_) { throw new Error('이 이미지 형식을 읽을 수 없어요. PNG, JPEG 또는 WebP 파일을 선택해 주세요.'); }
    if (result.width * result.height > 40_000_000 || result.width > 16000 || result.height > 16000) {
      result.close();
      throw new Error('이미지 해상도가 너무 큽니다. 4천만 화소 이하의 이미지를 선택해 주세요.');
    }
    return result;
  }

  function encode(canvas, type, quality) {
    return new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('이미지를 변환하지 못했어요.')), type, quality));
  }

  async function animated(file) {
    if (file.type === 'image/gif' || file.type === 'image/apng') return true;
    if (file.type === 'image/webp') {
      const bytes = new Uint8Array(await file.slice(0, 21).arrayBuffer());
      // WebP's extended header carries the animation flag (VP8X, bit 1).
      return bytes.length >= 21 && String.fromCharCode(...bytes.slice(12, 16)) === 'VP8X' && Boolean(bytes[20] & 2);
    }
    if (file.type !== 'image/png') return false;
    if (file.size > 32 * 1024 * 1024) return true;
    const bytes = new Uint8Array(await file.arrayBuffer());
    const view = new DataView(bytes.buffer);
    // APNG's animation control precedes its first IDAT chunk. Walk chunks
    // rather than scanning pixel bytes for a coincidental string match.
    for (let offset = 8; offset + 12 <= bytes.length;) {
      const length = view.getUint32(offset);
      const kind = String.fromCharCode(...bytes.slice(offset + 4, offset + 8));
      if (kind === 'acTL') return true;
      if (kind === 'IDAT' || kind === 'IEND') return false;
      offset += 12 + length;
    }
    return false;
  }

  async function optimize(file) {
    // Preserve animation, AVIF, SVG and small files. Native browser codecs run
    // locally; AVIF sequences are deliberately left in their original format.
    if (preparedFiles.has(file) || !['image/jpeg', 'image/png', 'image/webp'].includes(file.type) || await animated(file)) return file;
    const source = await bitmap(file);
    try {
      if (Math.max(source.width, source.height) <= 1600 && file.size <= 1024 * 1024) return file;
      const ratio = Math.min(1, 1600 / Math.max(source.width, source.height));
      const canvas = document.createElement('canvas');
      canvas.width = Math.max(1, Math.round(source.width * ratio));
      canvas.height = Math.max(1, Math.round(source.height * ratio));
      canvas.getContext('2d').drawImage(source, 0, 0, canvas.width, canvas.height);
      const output = await encode(canvas, 'image/webp', .84);
      if (output.size >= file.size && ratio === 1) return file;
      const extension = output.type === 'image/png' ? '.png' : '.webp';
      return new File([output], (file.name || 'image').replace(/\.[^.]+$/, '') + extension, { type: output.type });
    } finally { source.close(); }
  }

  function selectedImage(editor) {
    const session = sessions.get(editor);
    if (!session?.view || !editor.isWysiwygMode()) return null;
    const { state } = session.view;
    if (state.selection.node?.type.name === 'image') return { node: state.selection.node, position: state.selection.from };
    const last = session.selected;
    const node = last && state.doc.nodeAt(last.position);
    return node?.type.name === 'image' && node.attrs.imageUrl === last.url ? { node, position: last.position } : null;
  }

  function dispatch(session, transaction) {
    transaction.setMeta('closeHistory$', true);
    session.view.dispatch(transaction.scrollIntoView());
    session.view.dispatch(session.view.state.tr.setMeta('closeHistory$', true));
    session.view.focus();
  }

  function insertBlocks(editor, blocks) {
    const session = sessions.get(editor);
    const { state } = session.view;
    const cursor = state.selection.$from;
    const position = cursor.depth ? cursor.after(1) : state.selection.from;
    dispatch(session, state.tr.insert(position, blocks));
  }

  function insertImage(editor, target, url, alt, caption) {
    const session = sessions.get(editor);
    const { state } = session.view;
    const schema = state.schema;
    const captionNode = () => schema.nodes.paragraph.create(null, schema.text(caption, [schema.marks.emph.create()]));
    if (!target) {
      const image = schema.nodes.image.create({ imageUrl: url, altText: alt,
        htmlAttrs: caption ? { 'data-studio-caption': caption } : null });
      const blocks = [schema.nodes.paragraph.create(null, image)];
      if (caption) blocks.push(captionNode());
      blocks.push(schema.nodes.paragraph.create());
      insertBlocks(editor, blocks);
      return;
    }
    const current = state.doc.nodeAt(target.position);
    if (!current || current.type.name !== 'image' || current.attrs.imageUrl !== target.node.attrs.imageUrl) {
      throw new Error('편집하던 이미지가 변경됐어요. 이미지를 다시 선택해 주세요.');
    }
    const attrs = { ...current.attrs.htmlAttrs };
    const oldCaption = attrs['data-studio-caption'] || '';
    delete attrs['data-studio-caption'];
    if (caption) attrs['data-studio-caption'] = caption;
    const transaction = state.tr.setNodeMarkup(target.position, null, { ...current.attrs, imageUrl: url, altText: alt, htmlAttrs: Object.keys(attrs).length ? attrs : null });
    const resolved = state.doc.resolve(target.position);
    if (resolved.depth) {
      const after = resolved.after(resolved.depth);
      const next = state.doc.nodeAt(after);
      // Replace only a caption that we created and that the user has not edited
      // independently. Never erase an unrelated neighboring paragraph.
      const knownCaption = oldCaption && next?.type.name === 'paragraph' && next.textContent === oldCaption
        && next.content.content.every(child => child.isText && child.marks.some(mark => mark.type.name === 'emph'));
      if (knownCaption) {
        if (caption) transaction.replaceWith(after, after + next.nodeSize, captionNode());
        else transaction.delete(after, after + next.nodeSize);
      } else if (caption && caption !== oldCaption) transaction.insert(after, captionNode());
    }
    dispatch(session, transaction);
    session.selected = { position: target.position, url };
  }

  function install(context) {
    const { editor, api, notify, getDraft, upload, imageURL, getBootstrap } = context;
    const session = sessions.get(editor);
    if (!session) throw new Error('이미지 편집 플러그인을 먼저 등록해 주세요.');
    const host = $('media-tools-root');
    host.classList.add('media-tools');
    host.innerHTML = '<button type="button" id="media-add-image">이미지 편집하여 넣기</button><button type="button" id="media-edit-image" disabled>선택한 이미지 편집</button><button type="button" id="media-link-card">링크 카드 · 내 글 연결</button>';
    const imageDialog = element('dialog', undefined, 'media-dialog');
    imageDialog.id = 'media-image-dialog';
    imageDialog.setAttribute('aria-labelledby', 'media-image-title');
    imageDialog.innerHTML = `<div class="media-dialog-heading"><h2 id="media-image-title">이미지 다듬기</h2><button type="button" data-media-close aria-label="이미지 편집 닫기">×</button></div>
      <p class="media-note">자르기·크기·용량을 조절한 뒤 글에 넣습니다. 새 파일로 저장되어 원본 이미지는 유지돼요. 움직이는 이미지를 편집하면 첫 프레임만 저장됩니다.</p>
      <label class="media-file">이미지 선택 <input id="media-image-file" type="file" accept="image/*"></label>
      <div class="media-image-workspace"><div class="media-canvas-area"><canvas id="media-image-canvas" aria-label="자를 영역을 드래그로 선택하는 이미지 미리보기"></canvas><p id="media-image-info" role="status">파일을 선택해 주세요.</p></div>
      <div class="media-image-controls"><fieldset><legend>자르기 영역 (픽셀)</legend><div class="media-number-grid"><label>왼쪽<input id="media-crop-x" type="number" min="0" value="0"></label><label>위쪽<input id="media-crop-y" type="number" min="0" value="0"></label><label>너비<input id="media-crop-width" type="number" min="1" value="1"></label><label>높이<input id="media-crop-height" type="number" min="1" value="1"></label></div><button type="button" id="media-crop-reset">전체 이미지</button></fieldset>
      <label>저장 너비 (비율 유지)<input id="media-output-width" type="number" min="1" max="4096" value="1600"></label>
      <label>파일 형식<select id="media-output-format"><option value="image/webp">WebP · 작은 용량</option><option value="image/jpeg">JPEG · 사진</option><option value="image/png">PNG · 무손실</option></select></label>
      <label>품질 <span id="media-quality-value">84%</span><input id="media-quality" type="range" min="30" max="100" value="84"></label>
      <label>대체 텍스트<input id="media-image-alt" maxlength="500" placeholder="이미지를 설명하는 문장"></label>
      <label>사진 설명 (캡션)<input id="media-image-caption" maxlength="1000" placeholder="이미지 아래에 표시할 설명"></label></div></div>
      <p id="media-image-error" class="media-error" role="alert"></p><div class="media-dialog-footer"><button type="button" data-media-close>취소</button><button type="button" id="media-image-apply" class="button primary" disabled>이미지 저장하고 넣기</button></div>`;
    const linkDialog = element('dialog', undefined, 'media-dialog media-link-dialog');
    linkDialog.id = 'media-link-dialog';
    linkDialog.setAttribute('aria-labelledby', 'media-link-title');
    linkDialog.innerHTML = `<div class="media-dialog-heading"><h2 id="media-link-title">링크 카드 · 내 글 연결</h2><button type="button" data-media-close aria-label="링크 창 닫기">×</button></div>
      <label>웹 주소<div class="media-url-row"><input id="media-link-url" type="url" placeholder="https://…"><button type="button" id="media-link-fetch">정보 가져오기</button></div></label>
      <label>제목<input id="media-link-label" maxlength="500" placeholder="독자에게 보여줄 제목"></label><label>설명<textarea id="media-link-description" rows="3" maxlength="1500"></textarea></label>
      <label class="media-check"><input id="media-link-use-image" type="checkbox" checked> 대표 이미지 포함</label><div id="media-link-preview" class="media-card-preview" hidden></div>
      <div class="media-internal"><label>내 블로그 글 찾기<input id="media-internal-search" type="search" placeholder="제목이나 태그 검색"></label><div id="media-internal-results"></div></div>
      <p id="media-link-error" class="media-error" role="alert"></p><div class="media-dialog-footer"><button type="button" data-media-close>취소</button><button type="button" id="media-insert-link">일반 링크로 넣기</button><button type="button" id="media-insert-card" class="button primary">카드로 넣기</button></div>`;
    document.body.append(imageDialog, linkDialog);
    let imageState = null;
    let imageTarget = null;
    let imageSequence = 0;
    let cardImage = '';
    let dialogDraft = '';
    let busy = false;
    let cropDrag = null;

    const error = (id, message) => { $(id).textContent = message || ''; };
    const action = (fn, errorId) => async event => { try { await fn(event); } catch (e) { error(errorId, e.message || '작업을 완료하지 못했어요.'); } };
    const ready = () => {
      if (!getDraft()) throw new Error('먼저 글을 열어 주세요.');
      if (!editor.isWysiwygMode()) throw new Error('시각 편집으로 전환한 뒤 사용해 주세요.');
    };
    const checkDraft = () => { if (getDraft()?.id !== dialogDraft) throw new Error('다른 글이 열렸어요. 창을 닫고 다시 시도해 주세요.'); };
    for (const dialog of [imageDialog, linkDialog]) {
      dialog.querySelectorAll('[data-media-close]').forEach(button => { button.onclick = () => { if (!busy) dialog.close(); }; });
      dialog.addEventListener('cancel', event => { if (busy) event.preventDefault(); });
    }
    imageDialog.addEventListener('close', () => { imageSequence += 1; imageState?.source.close(); imageState = null; imageTarget = null; });

    function refresh() {
      const disabled = !getDraft() || !editor.isWysiwygMode();
      $('media-add-image').disabled = disabled;
      $('media-link-card').disabled = disabled;
      $('media-edit-image').disabled = disabled || !selectedImage(editor);
      host.title = disabled ? '시각 편집에서 사용할 수 있어요.' : '';
    }

    function crop() {
      if (!imageState) return null;
      const source = imageState.source;
      const x = Math.max(0, Math.min(source.width - 1, Number($('media-crop-x').value) || 0));
      const y = Math.max(0, Math.min(source.height - 1, Number($('media-crop-y').value) || 0));
      const width = Math.max(1, Math.min(source.width - x, Number($('media-crop-width').value) || 1));
      const height = Math.max(1, Math.min(source.height - y, Number($('media-crop-height').value) || 1));
      return { x: Math.round(x), y: Math.round(y), width: Math.round(width), height: Math.round(height) };
    }

    function draw() {
      if (!imageState) return;
      const canvas = $('media-image-canvas');
      const source = imageState.source;
      const scale = Math.min(1, 650 / source.width, 480 / source.height);
      canvas.width = Math.max(1, Math.round(source.width * scale));
      canvas.height = Math.max(1, Math.round(source.height * scale));
      const drawing = canvas.getContext('2d');
      drawing.drawImage(source, 0, 0, canvas.width, canvas.height);
      const region = crop();
      drawing.fillStyle = 'rgba(20,35,27,.38)';
      drawing.fillRect(0, 0, canvas.width, canvas.height);
      drawing.drawImage(source, region.x, region.y, region.width, region.height, region.x * scale, region.y * scale, region.width * scale, region.height * scale);
      drawing.strokeStyle = '#26b7a0';
      drawing.lineWidth = 2;
      drawing.strokeRect(region.x * scale + 1, region.y * scale + 1, Math.max(1, region.width * scale - 2), Math.max(1, region.height * scale - 2));
      const width = Math.max(1, Math.min(4096, Number($('media-output-width').value) || region.width));
      const height = Math.max(1, Math.round(width * region.height / region.width));
      $('media-image-info').textContent = `${source.width} × ${source.height} → ${width} × ${height} · 원본 ${(imageState.file.size / 1024).toFixed(0)} KB${imageState.file.type === 'image/gif' ? ' · GIF는 첫 프레임으로 저장됩니다.' : ''}`;
      $('media-quality-value').textContent = $('media-quality').value + '%';
    }

    async function load(file, target = null, sequence = imageSequence, replacement = false) {
      const source = await bitmap(file);
      if (sequence !== imageSequence || !imageDialog.open) { source.close(); return; }
      imageState?.source.close();
      imageState = { source, file, target, hasImageEdits: !target || replacement };
      $('media-crop-x').value = 0;
      $('media-crop-y').value = 0;
      $('media-crop-width').value = source.width;
      $('media-crop-height').value = source.height;
      $('media-output-width').value = Math.min(target ? 4096 : 1600, source.width);
      if (target && ['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) $('media-output-format').value = file.type;
      $('media-image-alt').value = target?.node.attrs.altText || file.name.replace(/\.[^.]+$/, '');
      $('media-image-caption').value = target?.node.attrs.htmlAttrs?.['data-studio-caption'] || '';
      $('media-image-apply').disabled = false;
      $('media-image-apply').textContent = target ? '이미지에 적용' : '이미지 저장하고 넣기';
      error('media-image-error', '');
      draw();
    }

    async function openImage(existing = false) {
      ready();
      const sequence = ++imageSequence;
      dialogDraft = getDraft().id;
      error('media-image-error', '');
      $('media-image-file').value = '';
      $('media-image-apply').disabled = true;
      $('media-image-info').textContent = '파일을 선택해 주세요. 미리보기에서 자를 영역을 끌어 선택할 수 있습니다.';
      $('media-image-canvas').width = 1;
      $('media-image-canvas').height = 1;
      imageTarget = existing ? selectedImage(editor) : null;
      imageDialog.showModal();
      if (existing) {
        const target = imageTarget;
        if (!target) throw new Error('본문에서 이미지를 먼저 클릭해 주세요.');
        const src = imageURL ? imageURL(target.node.attrs.imageUrl) : target.node.attrs.imageUrl;
        let response;
        try { response = await fetch(src); } catch (_) { throw new Error('외부 이미지를 읽을 수 없어요. 파일로 내려받은 뒤 이미지 선택으로 불러와 주세요.'); }
        if (!response.ok) throw new Error('이미지를 읽을 수 없어요. 원본 파일을 선택해 주세요.');
        const blob = await response.blob();
        if (sequence !== imageSequence || !imageDialog.open) return;
        await load(new File([blob], 'edited-image', { type: blob.type }), target, sequence);
      }
    }

    $('media-image-file').onchange = action(async event => {
      if (busy) return;
      const file = event.target.files[0];
      if (file) await load(file, imageTarget, ++imageSequence, true);
    }, 'media-image-error');
    for (const id of ['media-crop-x', 'media-crop-y', 'media-crop-width', 'media-crop-height', 'media-output-width', 'media-quality', 'media-output-format']) $(id).oninput = () => {
      if (imageState) imageState.hasImageEdits = true;
      draw();
    };
    $('media-crop-reset').onclick = () => {
      if (!imageState) return;
      imageState.hasImageEdits = true;
      $('media-crop-x').value = 0; $('media-crop-y').value = 0;
      $('media-crop-width').value = imageState.source.width; $('media-crop-height').value = imageState.source.height;
      draw();
    };
    const canvas = $('media-image-canvas');
    const point = event => {
      const rect = canvas.getBoundingClientRect();
      return { x: Math.max(0, Math.min(imageState.source.width - 1, (event.clientX - rect.left) / rect.width * imageState.source.width)),
        y: Math.max(0, Math.min(imageState.source.height - 1, (event.clientY - rect.top) / rect.height * imageState.source.height)) };
    };
    canvas.onpointerdown = event => {
      if (!imageState || busy) return;
      event.preventDefault(); cropDrag = point(event); canvas.setPointerCapture(event.pointerId);
    };
    canvas.onpointermove = event => {
      if (!cropDrag || !imageState) return;
      imageState.hasImageEdits = true;
      const next = point(event);
      $('media-crop-x').value = Math.round(Math.min(cropDrag.x, next.x));
      $('media-crop-y').value = Math.round(Math.min(cropDrag.y, next.y));
      $('media-crop-width').value = Math.max(1, Math.round(Math.abs(next.x - cropDrag.x)));
      $('media-crop-height').value = Math.max(1, Math.round(Math.abs(next.y - cropDrag.y)));
      draw();
    };
    canvas.onpointerup = canvas.onpointercancel = () => { cropDrag = null; };
    $('media-image-apply').onclick = action(async () => {
      if (!imageState || busy) return;
      checkDraft();
      busy = true; $('media-image-apply').disabled = true;
      imageDialog.querySelectorAll('input,select').forEach(input => { input.disabled = true; });
      try {
        if (imageState.target && !imageState.hasImageEdits) {
          insertImage(editor, imageState.target, imageState.target.node.attrs.imageUrl, $('media-image-alt').value.trim(), $('media-image-caption').value.trim());
          notify('이미지 설명을 저장했어요.');
          imageDialog.close();
          return;
        }
        const region = crop();
        const result = document.createElement('canvas');
        result.width = Math.max(1, Math.min(4096, Number($('media-output-width').value) || region.width));
        result.height = Math.max(1, Math.round(result.width * region.height / region.width));
        if (result.width * result.height > 16_000_000 || result.height > 8192) throw new Error('저장 크기가 너무 큽니다. 너비를 줄이거나 자르기 영역을 조절해 주세요.');
        const drawing = result.getContext('2d');
        const type = $('media-output-format').value;
        if (type === 'image/jpeg') { drawing.fillStyle = '#fff'; drawing.fillRect(0, 0, result.width, result.height); }
        drawing.drawImage(imageState.source, region.x, region.y, region.width, region.height, 0, 0, result.width, result.height);
        const blob = await encode(result, type, Number($('media-quality').value) / 100);
        const suffix = blob.type === 'image/jpeg' ? 'jpg' : blob.type === 'image/webp' ? 'webp' : 'png';
        const file = new File([blob], (imageState.file.name || 'image').replace(/\.[^.]+$/, '') + '.' + suffix, { type: blob.type });
        preparedFiles.add(file);
        const uploaded = await upload(file);
        checkDraft();
        insertImage(editor, imageState.target, uploaded.url, $('media-image-alt').value.trim(), $('media-image-caption').value.trim());
        notify(`이미지를 저장했어요. ${result.width} × ${result.height} · ${(blob.size / 1024).toFixed(0)} KB`);
        imageDialog.close();
      } finally { busy = false; $('media-image-apply').disabled = false; imageDialog.querySelectorAll('input,select').forEach(input => { input.disabled = false; }); }
    }, 'media-image-error');

    function cardPreview() {
      const preview = $('media-link-preview');
      preview.replaceChildren();
      const url = safeURL($('media-link-url').value);
      if (!url) { preview.hidden = true; return; }
      preview.hidden = false;
      if (cardImage && $('media-link-use-image').checked) {
        const img = element('img'); img.src = cardImage; img.alt = ''; preview.append(img);
      }
      preview.append(element('strong', $('media-link-label').value || new URL(url).hostname), element('p', $('media-link-description').value), element('small', new URL(url).hostname));
    }

    function internalPosts() {
      const query = $('media-internal-search').value.trim().toLowerCase();
      const results = $('media-internal-results');
      results.replaceChildren();
      const posts = (getBootstrap()?.posts || []).filter(post => post.published !== false && (!query || `${post.title} ${(post.tags || []).join(' ')}`.toLowerCase().includes(query))).slice(0, 30);
      for (const post of posts) {
        const button = element('button', post.title || post.path, 'media-internal-post'); button.type = 'button';
        button.onclick = () => {
          if (!safeURL(post.url)) { error('media-link-error', '이 글의 공개 주소를 확인할 수 없어요. 웹 주소를 직접 입력해 주세요.'); return; }
          $('media-link-url').value = post.url; $('media-link-label').value = post.title;
          $('media-link-description').value = post.excerpt || ''; cardImage = safeURL(post.teaser || '');
          error('media-link-error', ''); cardPreview();
        };
        results.append(button);
      }
      if (!posts.length) results.append(element('p', '일치하는 공개 글이 없어요.', 'media-note'));
    }

    function openCard() {
      ready(); dialogDraft = getDraft().id; cardImage = '';
      for (const id of ['media-link-url', 'media-link-label', 'media-link-description', 'media-internal-search']) $(id).value = '';
      $('media-link-preview').hidden = true; error('media-link-error', ''); internalPosts(); linkDialog.showModal(); $('media-link-url').focus();
    }

    $('media-link-fetch').onclick = action(async () => {
      const url = safeURL($('media-link-url').value);
      if (!url) throw new Error('http 또는 https 웹 주소를 입력해 주세요.');
      busy = true; $('media-link-fetch').disabled = true; error('media-link-error', '정보를 가져오고 있어요…');
      $('media-insert-link').disabled = true; $('media-insert-card').disabled = true;
      linkDialog.querySelectorAll('input,textarea,.media-internal-post').forEach(input => { input.disabled = true; });
      try {
        const metadata = await api('/api/link-preview', 'POST', { url });
        checkDraft();
        $('media-link-url').value = safeURL(metadata.url) || url;
        $('media-link-label').value = metadata.title || new URL(url).hostname;
        $('media-link-description').value = metadata.description || '';
        cardImage = metadata.image ? safeURL(metadata.image) : '';
        error('media-link-error', ''); cardPreview();
      } finally { busy = false; $('media-link-fetch').disabled = false; $('media-insert-link').disabled = false; $('media-insert-card').disabled = false; linkDialog.querySelectorAll('input,textarea,.media-internal-post').forEach(input => { input.disabled = false; }); }
    }, 'media-link-error');
    for (const id of ['media-link-url', 'media-link-label', 'media-link-description', 'media-link-use-image']) $(id).addEventListener('input', cardPreview);
    $('media-internal-search').oninput = internalPosts;

    function insertLink(card) {
      ready(); checkDraft();
      const url = safeURL($('media-link-url').value);
      if (!url) throw new Error('올바른 웹 주소를 입력해 주세요.');
      const title = $('media-link-label').value.trim() || new URL(url).hostname;
      const { schema } = session.view.state;
      const link = schema.marks.link.create({ linkUrl: url });
      if (!card) {
        dispatch(session, session.view.state.tr.replaceSelectionWith(schema.text(title, [link]), false));
      } else {
        const nodes = [schema.nodes.paragraph.create(null, schema.text(title, [link, schema.marks.strong.create()]))];
        const description = $('media-link-description').value.trim();
        if (description) nodes.push(schema.nodes.paragraph.create(null, schema.text(description)));
        if (cardImage && $('media-link-use-image').checked) nodes.push(schema.nodes.paragraph.create(null, schema.nodes.image.create({ imageUrl: cardImage, altText: title })));
        const quote = schema.nodes.blockQuote.create({ htmlAttrs: { 'data-studio-card': 'link', style: CARD_STYLE } }, nodes);
        insertBlocks(editor, [quote, schema.nodes.paragraph.create()]);
      }
      linkDialog.close(); notify(card ? '링크 카드를 넣었어요.' : '본문에 링크를 넣었어요.');
    }
    $('media-insert-link').onclick = action(() => insertLink(false), 'media-link-error');
    $('media-insert-card').onclick = action(() => insertLink(true), 'media-link-error');
    $('media-add-image').onclick = () => openImage(false).catch(e => error('media-image-error', e.message));
    $('media-edit-image').onclick = () => openImage(true).catch(e => error('media-image-error', e.message));
    $('media-link-card').onclick = () => { try { openCard(); } catch (e) { notify(e.message, true); } };
    host.querySelectorAll('button').forEach(button => button.addEventListener('mousedown', event => event.preventDefault()));
    // TOAST UI's image node view consumes its click before ProseMirror's
    // handleClickOn. Capture the DOM click, then map it through the public view
    // API so existing images remain editable after reopening a saved draft.
    $('editor').addEventListener('click', event => {
      const view = session.view;
      if (!view?.dom.contains(event.target)) return;
      const image = event.target.closest('img[src]');
      session.selected = null;
      if (image) {
        const position = view.posAtDOM(image, 0);
        for (const candidate of [position, position - 1]) {
          const node = candidate >= 0 && view.state.doc.nodeAt(candidate);
          if (node?.type.name === 'image') {
            session.selected = { position: candidate, url: node.attrs.imageUrl };
            break;
          }
        }
      }
      requestAnimationFrame(refresh);
    }, true);
    $('editor').addEventListener('keydown', event => {
      if (session.view?.dom.contains(event.target)) {
        session.selected = null;
        requestAnimationFrame(refresh);
      }
    }, true);
    document.addEventListener('selectionchange', () => requestAnimationFrame(refresh));
    editor.on('changeMode', refresh);
    const controller = { refresh, openImage, openCard };
    session.controller = controller;
    refresh();
    return controller;
  }

  window.StudioMedia = { plugin, install, optimize };
})();
