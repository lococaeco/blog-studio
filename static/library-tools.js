/* Templates, backups, private draft sync, revisions and real-theme previews. */
(() => {
  'use strict';
  let context;
  const el = (tag, text, className) => { const node = document.createElement(tag); if (text) node.textContent = text; if (className) node.className = className; return node; };
  const safe = fn => async event => { try { await fn(event); } catch (error) { context.notify(error.message || String(error), true); } };
  function button(text, fn, parent, id) { const node = el('button', text); node.type = 'button'; if (id) node.id = id; node.addEventListener('click', safe(fn)); parent.append(node); return node; }
  function dialog(title, wide = false) {
    const node = el('dialog', '', 'library-dialog' + (wide ? ' preview-dialog' : ''));
    const heading = el('h2', title); heading.id = 'library-title-' + crypto.randomUUID(); node.setAttribute('aria-labelledby', heading.id);
    node.append(heading); document.body.append(node);
    node.addEventListener('close', () => node.remove(), {once:true}); node.showModal();
    return node;
  }
  function actions(parent) { const row = el('div', '', 'library-actions'); parent.append(row); return row; }
  function close(parent, row) { button('닫기', () => parent.close(), row); }
  function field(parent, title, type = 'input', value = '') { const label = el('label', title); const input = el(type); input.setAttribute('aria-label', title); input.value = value; label.append(input); parent.append(label); return input; }
  function needDraft() { if (!context.getDraft()) throw new Error('먼저 글을 열어 주세요.'); }
  async function job(path, payload, status) {
    const started = await context.api(path, 'POST', payload || {});
    status.textContent = '작업 중입니다. 이 창을 열어 두세요…';
    for (;;) {
      await new Promise(resolve => setTimeout(resolve, 600));
      const result = await context.api('/api/jobs?id=' + started.job);
      if (result.state === 'error') { status.textContent = result.error; throw new Error(result.error); }
      if (result.state === 'done') { status.textContent = result.result.message || '완료했습니다.'; return result.result; }
    }
  }

  async function templates() {
    const d = dialog('내 템플릿과 재사용 문구');
    d.append(el('p', '글 전체의 틀은 템플릿으로, 자주 쓰는 문단은 재사용 문구로 보관하세요.', 'library-help'));
    const list = el('div'); d.append(list);
    const rows = actions(d);
    const refresh = async () => {
      list.replaceChildren();
      const result = await context.api('/api/templates');
      if (!result.items.length) list.append(el('p', '아직 저장한 항목이 없습니다.', 'library-help'));
      for (const item of result.items) {
        const row = el('div', '', 'library-row'); row.append(el('strong', (item.kind === 'snippet' ? '문구 · ' : '템플릿 · ') + item.label));
        button(item.kind === 'snippet' ? '본문에 넣기' : '새 글 만들기', async () => {
          if (item.kind === 'snippet') { needDraft(); window.StudioWriting.insertMarkdown(context.getEditor(), item.body); }
          else await context.newDraft(item);
          d.close();
        }, row);
        button('수정', () => edit(item), row);
        button('삭제', async () => {
          if (!await context.confirm('저장한 항목을 삭제할까요?', item.label, '삭제')) return;
          await context.api('/api/templates/delete', 'POST', {id:item.id}); await refresh(); await context.refresh();
        }, row);
        list.append(row);
      }
    };
    function edit(item = {}) {
      const form = dialog(item.id ? '저장한 항목 수정' : '새 항목 저장');
      const name = field(form, '이름', 'input', item.label || '');
      const kind = field(form, '종류', 'select');
      for (const [value, label] of [['template','글 템플릿'],['snippet','재사용 문구']]) { const option = el('option',label); option.value=value; kind.append(option); }
      kind.value = item.kind || 'template';
      const body = field(form, '내용 (Markdown)', 'textarea', item.body || '');
      const row = actions(form);
      button('저장', async () => {
        await context.api('/api/templates/save', 'POST', {id:item.id, label:name.value, kind:kind.value, body:body.value});
        form.close(); await refresh(); await context.refresh();
      }, row, 'save-personal-template'); close(form,row);
    }
    button('빈 항목 추가', () => edit(), rows);
    button('현재 글을 템플릿으로', async () => { needDraft(); await context.saveDraft(); edit({label:context.getDraft().title || '나의 글 양식',body:context.currentBody(),kind:'template'}); }, rows, 'template-from-draft');
    button('선택한 내용을 문구로', () => { needDraft(); const body=window.StudioWriting.selectedMarkdown(context.getEditor()); if (!body) throw new Error('본문에서 저장할 문구를 먼저 선택해 주세요.'); edit({body,kind:'snippet'}); }, rows);
    close(d,rows); await refresh();
  }

  async function backup() {
    await context.saveDraft();
    const d = dialog('백업과 초안 동기화');
    d.append(el('p', 'ZIP에는 글, 이미지, 초안, 저장 기록과 내 템플릿이 들어갑니다. 가져올 때 기존 파일은 덮어쓰지 않고 충돌 내용을 따로 보관합니다. 최대 128MB ZIP을 지원합니다.', 'library-help'));
    const status = el('p','','library-status'); d.append(status);
    const row=actions(d);
    button('전체 ZIP 내려받기', async () => {
      await context.saveDraft();
      const response=await fetch('/api/backup'); if (!response.ok) throw new Error((await response.json()).error);
      const url=URL.createObjectURL(await response.blob()); const a=el('a'); a.href=url;a.download='blog-studio-backup.zip';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
      status.textContent='백업 파일을 내려받았습니다.';
    }, row, 'backup-download');
    const input=el('input');input.type='file';input.accept='.zip,application/zip';input.hidden=true;d.append(input);
    input.addEventListener('change',safe(async()=>{
      const file=input.files[0]; if(!file)return;
      if(file.size>128*1024*1024)throw new Error('128MB 이하의 백업 ZIP을 선택해 주세요.');
      if(!await context.confirm('백업을 가져올까요?','기존 파일은 유지하고, 다른 내용의 초안은 복사본으로 추가합니다.','가져오기'))return;
      const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result).split(',')[1]);reader.onerror=reject;reader.readAsDataURL(file);});
      const result=await context.api('/api/backup/restore','POST',{data});status.textContent=result.message;await context.refresh();
    }));
    button('백업 ZIP 가져오기',()=>input.click(),row,'backup-import');
    d.append(el('hr'));
    d.append(el('p','GitHub에 연결한 뒤 동기화하면 계정의 비공개 blog-studio-drafts 저장소에서 다른 컴퓨터의 초안을 가져오고 현재 초안을 보관합니다. 같은 블로그 주소를 사용하는 컴퓨터끼리 동기화됩니다. 충돌은 복사본으로 남습니다. 최대 32MB의 초안을 합치며, 삭제는 다른 컴퓨터로 전파하지 않습니다.','library-help'));
    const syncRow=actions(d);
    const syncButton=button('비공개 초안 동기화',async()=>{
      await context.saveDraft();
      const activeId=context.getDraft()?.id;
      const controls=[...d.querySelectorAll('button')];controls.forEach(node=>node.disabled=true);
      const preventClose=event=>event.preventDefault();d.addEventListener('cancel',preventClose);
      context.setBusy(true);
      try{
        await job('/api/sync',{},status);await context.refresh();
        if(activeId)await context.loadDraft(await context.api('/api/drafts/'+activeId));
      }finally{
        context.setBusy(false);controls.forEach(node=>node.disabled=false);d.removeEventListener('cancel',preventClose);
      }
    },syncRow,'draft-sync');close(d,syncRow);
  }

  async function preview() {
    needDraft();await context.saveDraft();
    const d=dialog('실제 블로그 미리보기',true);
    d.append(el('p','현재 초안을 블로그의 Jekyll 테마로 빌드합니다. 처음에는 Ruby/Jekyll 환경을 준비해야 합니다.','library-help'));
    const status=el('p','','library-status');d.append(status);
    const row=actions(d), shell=el('div','','preview-shell'); shell.hidden=true;d.append(shell);
    let frame;
    const build=button('미리보기 만들기',async()=>{
      await context.saveDraft();build.disabled=true;
      try{const result=await job('/api/preview/build',{id:context.getDraft().id},status);
        frame=el('iframe');frame.title='실제 블로그 미리보기';frame.sandbox='allow-scripts allow-same-origin allow-popups';frame.referrerPolicy='no-referrer';frame.src=result.url;
        shell.replaceChildren(frame);shell.hidden=false;
      }finally{build.disabled=false;}
    },row,'theme-preview-build');
    button('환경 준비',async()=>{build.disabled=true;try{await job('/api/preview/prepare',{},status);}finally{build.disabled=false;}},row);
    const help=el('a','Ruby/Jekyll 설치 안내');help.href='https://jekyllrb.com/docs/installation/';help.target='_blank';help.rel='noopener noreferrer';row.append(help);
    for(const [text,width] of [['PC','100%'],['태블릿','768px'],['모바일','390px']])button(text,()=>{if(frame)frame.style.width=width;},row);
    close(d,row);
    const check=await context.api('/api/preview/status');status.textContent=check.bundle||check.jekyll?'미리보기를 만들 수 있습니다. 의존성이 없다면 환경 준비를 눌러 주세요.':'Ruby/Jekyll 설치 안내를 확인해 주세요.';
  }

  async function compareHistory(id,history_id){
    const result=await context.api('/api/history/diff','POST',{id,history_id});
    const d=dialog('저장 기록 변경 비교');
    const output=el('pre','','library-diff');
    for(const line of (result.diff||'현재 초안과 같은 내용입니다.').split('\n')){const span=el('span',line+'\n',line.startsWith('+')?'added':line.startsWith('-')?'removed':'');output.append(span);}
    d.append(output);close(d,actions(d));
  }

  function install(options){
    context=options;const host=document.getElementById('library-tools-root');host.replaceChildren();
    button('내 템플릿·문구',templates,host,'personal-templates');
    button('테마 미리보기',preview,host,'theme-preview');
    button('백업·동기화',backup,host,'backup-sync');
  }
  window.StudioLibrary={install,compareHistory};
})();
