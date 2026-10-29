/* Editable tables with native transactions and portable HTML formatting. */
(() => {
  'use strict';
  const sessions = new WeakMap(), patched = new WeakSet();
  const directions = new Set(['left', 'center', 'right']);
  const cellNames = new Set(['tableHeadCell', 'tableBodyCell']);
  const element = (tag, text, cls) => {
    const node = document.createElement(tag); if (text) node.textContent = text; if (cls) node.className = cls; return node;
  };
  const styleOf = value => { const node = element('span'); node.setAttribute('style', value || ''); return node.style; };
  function tableAttrs(attrs, values = {}) {
    const previous = attrs || {}, style = styleOf(previous.style);
    const align = directions.has(values.align) ? values.align : directions.has(previous['data-studio-table-align']) ? previous['data-studio-table-align'] : 'left';
    const width = Math.min(100, Math.max(25, Number(values.width ?? previous['data-studio-table-width']) || parseFloat(style.width) || 100));
    return { ...previous, 'data-studio-table-align': align, 'data-studio-table-width': String(width),
      style: `width: ${width}%; table-layout: fixed; border-collapse: collapse; margin-left: ${align === 'left' ? '0' : 'auto'}; margin-right: ${align === 'right' ? '0' : 'auto'};` };
  }
  function preserve(schema) {
    if (patched.has(schema)) return; patched.add(schema);
    for (const name of ['table', 'tableHeadCell', 'tableBodyCell']) {
      const type = schema.nodes[name];
      if (name === 'tableHeadCell') {
        const originalDOM = type.spec.toDOM;
        type.spec.toDOM = node => { const spec = originalDOM(node); delete spec[1].htmlAttrs; delete spec[1].classNames; return spec; };
      }
      type.spec.parseDOM = type.spec.parseDOM.map(rule => ({ ...rule, getAttrs(dom) {
        const original = rule.getAttrs ? rule.getAttrs(dom) : rule.attrs;
        if (original === false) return false;
        const attrs = { ...original }, style = styleOf(dom.getAttribute('style'));
        if (name === 'table' && dom.hasAttribute('data-studio-table-width')) {
          attrs.htmlAttrs = tableAttrs({}, { width: dom.getAttribute('data-studio-table-width'), align: dom.getAttribute('data-studio-table-align') });
        } else if (cellNames.has(name)) {
          const align = dom.getAttribute('align') || style.textAlign;
          if (directions.has(align)) attrs.align = align;
          if (name === 'tableHeadCell' && /^\d+(\.\d+)?%$/.test(style.width)) {
            attrs.htmlAttrs = { ...attrs.htmlAttrs, style: `width: ${Math.min(100, Math.max(1, parseFloat(style.width)))}%;` };
          }
        }
        return attrs;
      } }));
    }
    delete schema.cached.domParser;
  }
  function specDOM(spec, content) {
    if (typeof spec === 'string') return document.createTextNode(spec);
    if (spec?.nodeType) return spec.cloneNode(true);
    if (spec?.dom) return spec.dom.cloneNode(true);
    const [tag, ...parts] = spec, node = element(tag);
    if (parts[0] && typeof parts[0] === 'object' && !Array.isArray(parts[0])) {
      for (const [name, value] of Object.entries(parts.shift())) if (value !== null && value !== undefined && typeof value !== 'object') node.setAttribute(name, String(value));
    }
    for (const part of parts) node.append(part === 0 ? content() : specDOM(part, content));
    return node;
  }
  function nodeDOM(node) {
    let result = node.isText ? document.createTextNode(node.text) : specDOM(node.type.spec.toDOM(node), () => {
      const fragment = document.createDocumentFragment(); node.forEach(child => fragment.append(nodeDOM(child))); return fragment;
    });
    for (const mark of [...node.marks].reverse()) { const inner = result; result = specDOM(mark.type.spec.toDOM(mark, true), () => inner); if (!result.contains(inner)) result.append(inner); }
    return result;
  }
  function tableMarkdown(info, convertor) {
    let formatted = !!info.node.attrs.htmlAttrs?.['data-studio-table-width'];
    info.node.descendants(node => {
      const attrs = node.attrs.htmlAttrs || {};
      if (node.type.name === 'image' && (attrs.width || attrs['data-studio-image-align'] || Object.hasOwn(attrs, 'data-studio-caption'))) formatted = true;
    });
    if (!formatted) return convertor.origin();
    const table = nodeDOM(info.node);
    for (const [name,value] of Object.entries(tableAttrs(info.node.attrs.htmlAttrs))) table.setAttribute(name,value);
    for (const cell of table.querySelectorAll('th,td')) {
      cell.style.border = '1px solid #dce4df'; cell.style.padding = '10px 12px'; cell.style.verticalAlign = 'top';
      if (cell.tagName === 'TH') { cell.style.backgroundColor = '#f0f5f1'; cell.style.color = '#26342e'; }
      const direction = cell.getAttribute('align'); if (directions.has(direction)) cell.style.textAlign = direction;
      cell.removeAttribute('htmlattrs'); cell.removeAttribute('classnames');
    }
    for (const node of table.querySelectorAll('[data-raw-html]')) node.removeAttribute('data-raw-html');
    const html = DOMPurify.sanitize(table.outerHTML, { USE_PROFILES: { html: true } });
    info.node = info.node.copy();
    return { rawHTML: ['\n\n' + html + '\n\n', ''] };
  }
  function plugin(context) {
    const session = { context, editor: context.instance, view: null, refresh: null, queued: false };
    sessions.set(context.instance, session);
    return { toMarkdownRenderers: { table: tableMarkdown }, wysiwygPlugins: [() => new context.pmState.Plugin({
      state: { init: (_, state) => preserve(state.schema), apply: (_, value) => value },
      appendTransaction(transactions, oldState, state) {
        const pending = session.pendingNative;
        if (!pending || !transactions.some(tr => tr.docChanged)) return null;
        session.pendingNative = null;
        const node = state.doc.nodeAt(pending.position);
        if (node?.type.name !== 'table') return null;
        let tr = state.tr;
        if (pending.name === 'alignColumn') tr = tr.setNodeMarkup(pending.position, null, { ...node.attrs, htmlAttrs: tableAttrs(node.attrs.htmlAttrs) }, node.marks);
        if (/Column/.test(pending.name) && pending.name !== 'alignColumn' && node.attrs.htmlAttrs?.['data-studio-table-width']) {
          const cells = headers({node, position: pending.position});
          const values = cells.map(cell => parseFloat(styleOf(cell.node.attrs.htmlAttrs?.style).width) || 100 / cells.length);
          const total = values.reduce((a,b)=>a+b,0);
          cells.forEach(({node:cell,position},index) => { tr = tr.setNodeMarkup(position,null,{...cell.attrs,htmlAttrs:{...cell.attrs.htmlAttrs,style:`width: ${(values[index]/total*100).toFixed(3)}%;`}},cell.marks); });
        }
        return tr.docChanged ? tr : null;
      },
      props: { nodeViews: { table(node) {
        const wrapper = element('div', '', 'studio-table-scroll'), table = element('table'); wrapper.append(table);
        const update = next => {
          if (next.type.name !== 'table') return false;
          for (const attribute of [...table.attributes]) table.removeAttribute(attribute.name);
          for (const [name,value] of Object.entries(next.attrs.htmlAttrs || {})) if(value!=null)table.setAttribute(name,String(value));
          const count = next.firstChild?.firstChild?.childCount || 1;
          table.style.minWidth = Math.max(160,count*84)+'px';
          return true;
        };
        update(node);
        return { dom:wrapper,contentDOM:table,update,ignoreMutation:mutation=>mutation.type==='attributes'&&(mutation.target===wrapper||mutation.target===table) };
      } }, decorations(state) {
        const current = currentTable(session, state);
        return current ? context.pmView.DecorationSet.create(state.doc, [context.pmView.Decoration.node(current.position, current.position + current.node.nodeSize, { class: 'studio-table-active' })]) : null;
      } },
      view(view) { session.view = view; return { update() { queue(session); }, destroy() { session.view = null; } }; },
    })] };
  }
  function queue(session) { if (session.queued) return; session.queued = true; requestAnimationFrame(() => { session.queued = false; session.refresh?.(); }); }
  function currentTable(session, state = session?.view?.state) {
    if (!state || !session.editor.isWysiwygMode()) return null;
    for (let depth = state.selection.$from.depth; depth > 0; depth--) {
      const node = state.selection.$from.node(depth);
      if (node.type.name === 'table') return { node, position: state.selection.$from.before(depth), depth };
    }
    return null;
  }
  function selectedCells(session, table) {
    const state = session.view.state, cells = [], ranges = state.selection.ranges || [{ $from: state.selection.$from, $to: state.selection.$to }];
    table.node.descendants((node, offset) => {
      if (!cellNames.has(node.type.name)) return true;
      const position = table.position + 1 + offset;
      if (ranges.some(range => position < range.$to.pos + 1 && position + node.nodeSize > range.$from.pos)) cells.push({ node, position });
      return false;
    });
    return cells;
  }
  function selection(editor) {
    const session = sessions.get(editor), table = currentTable(session);
    if (!table || document.activeElement?.matches('input,textarea') && !document.activeElement.closest('#table-tools')) return null;
    const cells = selectedCells(session, table), values = new Set(cells.map(cell => cell.node.attrs.align || 'left'));
    return { enabled: !!cells.length, value: values.size === 1 ? [...values][0] : null, scope: 'table' };
  }
  function commit(session, tr) {
    if (tr.docChanged) {
      session.view.dispatch(tr.setMeta('closeHistory$', true));
      session.view.dispatch(session.view.state.tr.setMeta('closeHistory$', true));
    }
    session.view.focus(); queue(session);
  }
  function formatTable(session, values) {
    const table = currentTable(session); if (!table) return false;
    const tr = session.view.state.tr.setNodeMarkup(table.position, null, { ...table.node.attrs, htmlAttrs: tableAttrs(table.node.attrs.htmlAttrs, values) }, table.node.marks);
    commit(session, tr); return true;
  }
  function alignCells(editor, direction) {
    const session = sessions.get(editor), table = currentTable(session);
    if (!table || !directions.has(direction)) return false;
    const tr = session.view.state.tr.setNodeMarkup(table.position, null, { ...table.node.attrs, htmlAttrs: tableAttrs(table.node.attrs.htmlAttrs) }, table.node.marks);
    for (const {node, position} of selectedCells(session, table)) tr.setNodeMarkup(position, null, { ...node.attrs, align: direction }, node.marks);
    commit(session, tr); return true;
  }
  function headers(table) {
    const result = [];
    table.node.descendants((node, offset) => { if (node.type.name === 'tableHeadCell') { result.push({ node, position: table.position + 1 + offset }); return false; } return true; });
    return result;
  }
  function widths(session, values) {
    const table = currentTable(session); if (!table) return;
    const cells = headers(table), total = values.reduce((a,b) => a+b, 0);
    if (!cells.length || cells.length !== values.length || !total) return;
    let tr = session.view.state.tr.setNodeMarkup(table.position, null, { ...table.node.attrs, htmlAttrs: tableAttrs(table.node.attrs.htmlAttrs) }, table.node.marks);
    cells.forEach(({node,position},index) => { tr = tr.setNodeMarkup(position, null, { ...node.attrs, htmlAttrs: { ...node.attrs.htmlAttrs, style: `width: ${(values[index] / total * 100).toFixed(3)}%;` } }, node.marks); });
    commit(session, tr);
  }
  function install({ editor, notify }) {
    const session = sessions.get(editor); if (!session) return;
    const host = element('div', '', 'table-tools'); host.id = 'table-tools'; host.hidden = true; host.setAttribute('role','group'); host.setAttribute('aria-label','선택한 표 편집');
    host.dataset.studioMenu = '';
    document.getElementById('editor-dock').append(host);
    host.append(element('span','표','table-tools-label'));
    const controls = {}, buttons = [];
    const invoke = fn => { try { session.view?.domObserver.flush(); fn(); } catch (error) { notify(error.message || '표 편집을 완료하지 못했어요.', 'error'); } };
    function button(parent,id,label,action) {
      const item = element('button',label); item.type='button'; item.id=id; item.setAttribute('aria-label',label); item.title=label;
      item.addEventListener('mousedown',event => event.preventDefault()); item.onclick=() => invoke(action); parent.append(item); buttons.push(item); return item;
    }
    function selectControl(id,label,options,action) {
      const wrap = element('label',label), select = element('select'); select.id=id; select.setAttribute('aria-label',label);
      for(const [value,text] of options) { const option = element('option',text); option.value=value; select.append(option); }
      select.onchange=() => invoke(() => action(select.value)); wrap.append(select); host.append(wrap); return select;
    }
    controls.position = selectControl('table-position','표 위치',[['left','왼쪽'],['center','가운데'],['right','오른쪽']],align => formatTable(session,{align}));
    controls.width = selectControl('table-width','표 너비',[['100','전체'],['75','75%'],['50','50%'],['25','25%']],width => formatTable(session,{width}));
    const alignGroup=element('div','','table-cell-alignment'); alignGroup.setAttribute('role','group');alignGroup.setAttribute('aria-label','선택한 셀 정렬');
    alignGroup.append(element('span','셀'));
    for(const [direction,label] of [['left','왼쪽'],['center','가운데'],['right','오른쪽']]) {
      const item=button(alignGroup,'table-cell-'+direction,'셀 '+label+' 정렬',()=>alignCells(editor,direction));
      item.innerHTML=`<svg class="icon" aria-hidden="true"><use href="#i-align-${direction}"/></svg>`;
    }
    host.append(alignGroup);
    const more=button(host,'table-actions-toggle','행·열 편집',()=>togglePanel()); more.setAttribute('aria-expanded','false'); more.setAttribute('aria-controls','table-actions-menu');
    const panel=element('div','','table-actions-menu');panel.id='table-actions-menu';panel.hidden=true;panel.setAttribute('role','group');panel.setAttribute('aria-label','표 행과 열 편집');document.getElementById('document').append(panel);
    panel.dataset.studioMenu = '';
    const group=label=>{const section=element('section');section.append(element('h3',label));const content=element('div');section.append(content);panel.append(section);return content;};
    function native(name,payload) {
      const before=currentTable(session); if(!before)return;
      if(name==='addRowToDown'&&selectedCells(session,before).some(cell=>cell.node.type.name==='tableHeadCell')) {
        const firstCell=before.position+1+before.node.firstChild.nodeSize+2;
        session.view.dispatch(session.view.state.tr.setSelection(contextSelection(firstCell)).setMeta('addToHistory',false));
        name='addRowToUp';
      }
      session.view.dispatch(session.view.state.tr.setMeta('closeHistory$',true)); session.pendingNative={name,position:before.position}; editor.exec(name,payload); session.pendingNative=null;
      session.view.dispatch(session.view.state.tr.setMeta('closeHistory$',true));
      session.view.focus(); closePanel(); queue(session);
    }
    const row=group('행');
    button(row,'table-row-before','위에 행 추가',()=>native('addRowToUp'));
    button(row,'table-row-after','아래에 행 추가',()=>native('addRowToDown'));
    button(row,'table-row-delete','현재 행 삭제',()=>native('removeRow'));
    const column=group('열');
    button(column,'table-column-before','왼쪽에 열 추가',()=>native('addColumnToLeft'));
    button(column,'table-column-after','오른쪽에 열 추가',()=>native('addColumnToRight'));
    button(column,'table-column-delete','현재 열 삭제',()=>native('removeColumn'));
    const arrange=group('열 정돈');
    button(arrange,'table-even-widths','열 너비 균등하게',()=>{const table=currentTable(session);if(table)widths(session,headers(table).map(()=>1));closePanel();});
    for(const [align,label]of [['left','왼쪽'],['center','가운데'],['right','오른쪽']])button(arrange,'table-column-'+align,'현재 열 '+label+' 정렬',()=>native('alignColumn',{align}));
    const remove=group('표');button(remove,'table-remove','표 삭제',()=>native('removeTable')).className='table-danger';
    const hint=element('p','열 사이의 손잡이를 드래그하면 너비가 바뀌어요.');panel.append(hint);
    const handles=element('div','','table-column-handles');handles.id='table-column-handles';document.getElementById('document').append(handles);
    const previewStyle=element('style');document.head.append(previewStyle);
    let activePosition=null, drag=null, panelPoint=null, dismissed=false, imageActive=false;
    function closePanel(){panel.hidden=true;more.setAttribute('aria-expanded','false');panelPoint=null;}
    function togglePanel(point) {
      if(!panel.hidden&&!point){closePanel();return;}
      window.StudioToolbar?.closeMenus();document.dispatchEvent(new CustomEvent('studio:context-open',{detail:{source:'table'}}));
      panel.hidden=false;more.setAttribute('aria-expanded','true');panelPoint=point||null;positionPanel();
    }
    function positionPanel(){if(panel.hidden)return;const box=more.getBoundingClientRect(),left=panelPoint?.x??box.left,top=panelPoint?.y??box.bottom+6;panel.style.left=Math.max(8,Math.min(left,innerWidth-panel.offsetWidth-8))+'px';panel.style.top=Math.max(8,Math.min(top,innerHeight-panel.offsetHeight-8))+'px';panel.style.maxHeight=(innerHeight-16)+'px';}
    function tableDOM(){const table=currentTable(session),node=table?session.view.nodeDOM(table.position):null;return node?.tagName==='TABLE'?node:node?.querySelector('table');}
    function inTable(){const table=currentTable(session);return table&&session.view.dom.contains(tableDOM())?table:null;}
    function endDrag(event,cancel=false){if(!drag)return;const current=drag;drag=null;previewStyle.textContent='';document.documentElement.classList.remove('table-resizing');if(!cancel)widths(session,current.values);queue(session);}
    function startDrag(event,index){
      if(event.button!==0)return;const dom=tableDOM();if(!dom)return;const rect=dom.getBoundingClientRect(),cells=[...dom.querySelectorAll('thead tr:first-child>th')];
      if(cells.length<2||cells.some(cell=>cell.colSpan>1))return;
      event.preventDefault();event.stopPropagation();closePanel();
      const values=cells.map(cell=>cell.getBoundingClientRect().width/rect.width*100);
      drag={index,startX:event.clientX,total:rect.width,initial:[...values],values};event.currentTarget.setPointerCapture(event.pointerId);document.documentElement.classList.add('table-resizing');
    }
    handles.addEventListener('pointermove',event=>{if(!drag)return;const index=drag.index,pair=drag.initial[index]+drag.initial[index+1],min=Math.min(10,3000/drag.total,pair/3);
      const value=Math.max(min,Math.min(pair-min,drag.initial[index]+(event.clientX-drag.startX)/drag.total*100));drag.values[index]=value;drag.values[index+1]=pair-value;
      previewStyle.textContent=drag.values.map((width,i)=>`.studio-table-active>table>thead>tr>th:nth-child(${i+1}){width:${width}%!important}`).join('');positionHandles(false);
    });
    handles.addEventListener('pointerup',event=>endDrag(event));handles.addEventListener('pointercancel',event=>endDrag(event,true));
    handles.addEventListener('lostpointercapture',event=>endDrag(event,true));addEventListener('blur',event=>endDrag(event,true));
    function positionHandles(rebuild=true){
      const dom=tableDOM();if(!dom||host.hidden||imageActive){handles.hidden=true;return;}
      const box=dom.getBoundingClientRect(),dock=document.getElementById('editor-dock').getBoundingClientRect(),cells=[...dom.querySelectorAll('thead tr:first-child>th')];
      handles.hidden=box.bottom<dock.bottom||box.top>innerHeight||cells.length<2||cells.some(cell=>cell.colSpan>1);
      if(handles.hidden)return;
      if(rebuild&&handles.childElementCount!==cells.length-1){handles.replaceChildren();for(let i=0;i<cells.length-1;i++){
        const handle=element('button','','table-column-handle');handle.type='button';handle.setAttribute('aria-label',`${i+1}열 너비 조절`);handle.title='드래그하여 열 너비 조절 · 방향키로 미세 조절';handle.dataset.index=i;
        handle.addEventListener('pointerdown',event=>startDrag(event,i));handle.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight'].includes(event.key))return;event.preventDefault();const currentDOM=tableDOM();if(!currentDOM)return;const currentBox=currentDOM.getBoundingClientRect(),currentCells=[...currentDOM.querySelectorAll('thead tr:first-child>th')];const values=currentCells.map(cell=>cell.getBoundingClientRect().width/currentBox.width*100),delta=event.key==='ArrowLeft'?-2:2; if(values[i]+delta>=5&&values[i+1]-delta>=5){values[i]+=delta;values[i+1]-=delta;widths(session,values);handle.focus({preventScroll:true});} });handles.append(handle);
      }}
      [...handles.children].forEach((handle,i)=>{const cell=cells[i].getBoundingClientRect(),clip=dom.parentElement.getBoundingClientRect();handle.hidden=cell.right<clip.left+5||cell.right>clip.right-5;handle.style.left=(cell.right-5)+'px';handle.style.top=Math.max(box.top,dock.bottom+3)+'px';handle.style.height=Math.max(0,Math.min(box.bottom,innerHeight)-Math.max(box.top,dock.bottom+3))+'px';});
    }
    session.refresh=()=>{
      const table=inTable(),outsideField=document.activeElement?.matches('input,textarea')&&!host.contains(document.activeElement);
      const unavailable=!table||outsideField||document.getElementById('document').inert||!!document.querySelector('dialog[open]')||dismissed;
      host.hidden=unavailable;if(unavailable){closePanel();handles.hidden=true;return;}
      if(activePosition!==table.position){closePanel();activePosition=table.position;if(session.view.state.selection.empty&&!imageActive)document.dispatchEvent(new CustomEvent('studio:context-open',{detail:{source:'table'}}));}
      controls.position.value=table.node.attrs.htmlAttrs?.['data-studio-table-align']||'left';controls.width.value=table.node.attrs.htmlAttrs?.['data-studio-table-width']||'100';
      const cells=selectedCells(session,table),inHeader=cells.some(cell=>cell.node.type.name==='tableHeadCell'),rows=table.node.child(1).childCount;
      document.getElementById('table-row-before').disabled=inHeader;
      document.getElementById('table-row-after').disabled=false;
      document.getElementById('table-row-delete').disabled=inHeader||rows<=1;
      document.getElementById('table-column-delete').disabled=headers(table).length<=1;
      for(const direction of directions)document.getElementById('table-cell-'+direction).setAttribute('aria-pressed',String(cells.length>0&&cells.every(cell=>(cell.node.attrs.align||'left')===direction)));
      positionHandles();positionPanel();
    };
    session.view.dom.addEventListener('contextmenu',event=>{const cell=event.target.closest('th,td');if(!cell)return;event.preventDefault();event.stopImmediatePropagation();
      const position=session.view.posAtDOM(cell,0),state=session.view.state;
      const table=currentTable(session),alreadySelected=table&&!state.selection.empty&&selectedCells(session,table).some(item=>position>=item.position&&position<item.position+item.node.nodeSize);
      if(!alreadySelected)session.view.dispatch(state.tr.setSelection(contextSelection(position)).setMeta('addToHistory',false));
      dismissed=false;session.refresh();togglePanel({x:event.clientX,y:event.clientY});
    },true);
    function contextSelection(position){return session.context.pmState.TextSelection.near(session.view.state.doc.resolve(position+1));}
    document.addEventListener('pointerdown',event=>{if(event.target.closest('#editor-dock')||panel.contains(event.target)||handles.contains(event.target))return;if(session.view.dom.contains(event.target)){dismissed=false;if(!event.target.closest('img'))imageActive=false;queue(session);}else{closePanel();dismissed=true;host.hidden=true;handles.hidden=true;}});
    document.addEventListener('keydown',event=>{if(event.key==='Escape'){if(drag){event.preventDefault();endDrag(event,true);}if(!panel.hidden){event.preventDefault();closePanel();more.focus();}}});
    more.addEventListener('click',event=>{if(event.detail===0&&!panel.hidden)panel.querySelector('button:not(:disabled)')?.focus();});
    more.addEventListener('keydown',event=>{if(event.key==='ArrowDown'){event.preventDefault();if(panel.hidden)togglePanel();panel.querySelector('button:not(:disabled)')?.focus();}});
    panel.addEventListener('keydown',event=>{if(event.key==='Tab')setTimeout(()=>{if(!panel.contains(document.activeElement)&&document.activeElement!==more)closePanel();},0);if(!['ArrowDown','ArrowUp','Home','End'].includes(event.key))return;event.preventDefault();const items=[...panel.querySelectorAll('button:not(:disabled)')];let index=items.indexOf(document.activeElement);index=event.key==='Home'?0:event.key==='End'?items.length-1:(index+(event.key==='ArrowUp'?-1:1)+items.length)%items.length;items[index]?.focus();});
    document.addEventListener('studio:context-open',event=>{if(event.detail?.source!=='table')closePanel();imageActive=event.detail?.source==='image';positionHandles();});
    editor.on('changeMode',()=>{dismissed=false;imageActive=false;closePanel();queue(session);});
    document.addEventListener('selectionchange',()=>{if(session.view.dom.contains(getSelection()?.anchorNode))dismissed=false;queue(session);});
    addEventListener('scroll',event=>{if(panel.contains(event.target))return;if(!drag)positionHandles();if(panelPoint)closePanel();else positionPanel();},{capture:true,passive:true});
    addEventListener('resize',()=>{positionHandles();positionPanel();});
    new ResizeObserver(()=>{if(!drag)positionHandles();positionPanel();}).observe(document.getElementById('editor'));
    new MutationObserver(()=>queue(session)).observe(document.getElementById('document'),{attributes:true,attributeFilter:['inert','hidden']});
    session.refresh();
  }
  window.StudioTable={plugin,install,selection,alignCells};
})();
