/* Shared presentation for the isolated Windows and Android LAB builds. */
(() => {
  const n=(tag,cls,text)=>{const x=document.createElement(tag);x.className=cls||'';if(text)x.textContent=text;return x;};
  const icons={log:'M4 4h16v12H9l-5 4V4Zm4 5h8M8 12h5',back:'m14 5-7 7 7 7',chevron:'m9 5 7 7-7 7',book:'M12 5v15M3 4c4-1 6 0 9 1 3-1 5-2 9-1v15c-4-1-6 0-9 1-3-1-5-2-9-1V4Z',copy:'M8 8h12v13H8V8ZM4 16V3h12',close:'m6 6 12 12M6 18 18 6',check:'m5 12 4 4L19 6',apps:'M3 3h7v7H3V3Zm11 0h7v7h-7V3ZM3 14h7v7H3v-7Zm11 0h7v7h-7v-7Z'};
  const icon=(name)=>{if(window.AppIcons)return AppIcons.icon(name);const x=n('span','ui-icon');x.setAttribute('aria-hidden','true');x.innerHTML=`<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="${icons[name]||icons.apps}"/></svg>`;return x;};
  function image(src,name,cls,spec){const wrap=n('div',cls);if(spec&&window.Catalog09)return Catalog09.bind(wrap,{...spec,original:src,alt:name},()=>icon('apps'));if(src){const i=n('img');i.src=window.Catalog09?.originalUrl(src)||src;i.alt=name;i.draggable=false;i.addEventListener('error',()=>{i.replaceWith(icon('apps'));},{once:true});wrap.append(i);}else wrap.append(icon('apps'));return wrap;}
  const statusNames={green:'Актуально',yellow:'Требует проверки',red:'Способ не работает',blue:'Обновлено'};
  function card(item){const b=n('button','cat-card cat-'+(item.kind||'model'));b.type='button';b.setAttribute('aria-label',`${item.name}. ${item.meta||'Открыть'}`);b.append(image(item.image,'','cat-visual',item.kind==='brand'?{kind:'brand',brand:item.name}:null));const copy=n('span','cat-copy');copy.append(n('span','cat-name',item.name),n('span','cat-meta',item.meta||''));b.append(copy);const foot=n('span','cat-foot');(item.colors||[]).filter(c=>statusNames[c]).forEach(c=>{const dot=n('i','cat-dot cat-dot-'+c);dot.title=statusNames[c];dot.setAttribute('aria-label',statusNames[c]);foot.append(dot)});if(item.kind!=='brand')foot.append(icon('chevron'));b.append(foot);b.addEventListener('click',item.onClick);return b;}
  function detail({brand,group,src,heroSrc,versions,onOpen,selectedKey}){
    const choices=n('div','model-versions');choices.setAttribute('role','radiogroup');choices.setAttribute('aria-label','Модификация автомобиля');let chosen=versions.find(v=>v.key===selectedKey)||versions[0];
    const makeHero=()=>image(window.Catalog09?(chosen?.logo||src):src,group,'model-hero',{kind:'model',brand,model:group,modification:chosen?.modification||'',selectionKey:chosen?.key||'',heroSrc:chosen?.hero||heroSrc||''});
    const wrap=n('section','model-detail');let hero=makeHero();wrap.append(n('h1','model-detail-title',`${brand} ${group}`.trim()),hero);
    versions.forEach(v=>{const row=n('label','model-version');const input=n('input');input.type='radio';input.name='model-version';input.value=v.key;input.checked=v===chosen;const text=n('span','');text.append(n('strong','',v.modification||'Основная версия'));if(statusNames[v.status_color])text.append(n('small','version-state '+v.status_color,statusNames[v.status_color]));const preview=image(v.logo||src,'','version-visual');row.append(input,preview,text);input.addEventListener('change',()=>{chosen=v;if(window.Catalog09?.transition){Catalog09.transition(hero,{kind:'model',brand,model:group,modification:v.modification||'',selectionKey:v.key,original:v.logo||src,heroSrc:v.hero||heroSrc||'',alt:group})}else{const next=makeHero();hero.replaceWith(next);hero=next}});choices.append(row)});
    wrap.append(choices);const open=n('button','accent model-open');open.type='button';open.append(icon('book'),n('span','','Открыть инструкцию'),icon('chevron'));open.disabled=!chosen;open.addEventListener('click',()=>onOpen(chosen));wrap.append(open);return wrap;
  }
  function summary(root){
    let box=root.querySelector('.selection-summary');if(!box){box=n('div','selection-summary');box.setAttribute('aria-live','polite');root.append(box)}
    const rows=[...root.querySelectorAll('.app-row,.app-choice')];const checked=rows.filter(r=>r.querySelector('input[type=checkbox]:checked'));const required=checked.filter(r=>r.querySelector('input:disabled')).length;
    box.replaceChildren(n('strong','','К установке'),n('span','',`Обязательные: ${required}`),n('span','',`Дополнительно: ${checked.length-required}`),n('b','',`Всего: ${checked.length}`));
  }
  function busy(root,label,items){
    let box=root.querySelector('.run-status');if(box)box.remove();box=n('section','run-status');box.setAttribute('role','status');box.append(n('h2','',label||'Выполняется установка'),n('p','run-event','Ожидаем ответ устройства…'));
    const progress=n('progress','run-progress');box.append(progress);
    if(items&&items.length){box.append(n('p','',`В очереди: ${items.length}`));const list=n('ul','run-queue');items.forEach(item=>{const row=n('li');row.dataset.path=item.path||'';row.dataset.state='pending';row.append(n('span','',item.name||String(item)),n('small','','Ожидает'));list.append(row)});box.append(list)}
    root.prepend(box);root.classList.add('is-running');return box;
  }
  const readerStyle='<style id="approved-reader">html,body{background:#171f25!important;color:#e9eff2!important;font-family:Segoe UI,Roboto,Arial,sans-serif!important;font-size:16px!important;line-height:1.65!important}body{box-sizing:border-box;max-width:920px;margin:0 auto!important;padding:20px!important}h1{font-size:26px!important;line-height:1.25!important}h2{font-size:21px!important;line-height:1.35!important}p,li{max-width:100%;overflow-wrap:anywhere}img,video{max-width:100%!important;height:auto!important}a{color:#9debd7!important}pre,code{white-space:pre-wrap;overflow-wrap:anywhere}table{max-width:100%}button,input,select{font:inherit}</style>';
  function reader(html){return /<\/head>/i.test(html)?html.replace(/<\/head>/i,readerStyle+'</head>'):readerStyle+html;}
  function progress(event){
    const root=document.querySelector('.is-running');if(!root||String(event.stage_index)!==root.dataset.stageIndex)return;
    const normalize=p=>String(p||'').split('\\').join('/').toLowerCase();
    const row=[...root.querySelectorAll('.run-queue li')].find(r=>normalize(r.dataset.path)===normalize(event.path));
    if(row){row.dataset.state=event.state;row.querySelector('small').textContent={running:'Установка…',done:'Готово',error:'Ошибка'}[event.state]||'Ожидает';}
    const bar=root.querySelector('progress');if(bar&&event.total>0){bar.max=event.total;bar.value=event.completed;}
    const title=root.querySelector('.run-status h2');if(title&&event.total>0)title.textContent=`Установка · ${event.completed} из ${event.total}`;
  }
  window.CatalogUI={card,detail};window.LabUI={n,icon,summary,busy,reader,progress};
})();
