(() => {
  const {n,icon}=LabUI;
  const dialog=n('dialog');dialog.id='lab-log-dialog';dialog.setAttribute('aria-label','Лог и чат');
  const card=document.getElementById('log-card');dialog.append(card);document.body.append(dialog);
  const title=card.querySelector('.section-label');title.textContent='Лог';
  const subtitle=n('p','log-context','События установки и помощь ИИ в одном окне');card.querySelector('.card-header').after(subtitle);
  const close=document.getElementById('log-toggle');close.replaceChildren(icon('close'));close.setAttribute('aria-label','Закрыть лог');close.title='Закрыть';
  const copy=n('button','log-copy');copy.type='button';copy.append(icon('copy'));copy.setAttribute('aria-label','Скопировать лог');copy.title='Скопировать лог';
  copy.onclick=async()=>{try{await navigator.clipboard.writeText(document.getElementById('log-panel').innerText);copy.title='Скопировано';}catch{copy.title='Не удалось скопировать';}};close.before(copy);
  dialog.addEventListener('click',e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)setLogExpanded(false)}});
  dialog.addEventListener('close',()=>card.classList.remove('is-expanded'));
  document.getElementById('adb-console-input').placeholder='Вопрос ИИ или команда ADB…';
  document.getElementById('adb-console-input').setAttribute('aria-label','Вопрос ИИ или команда ADB');
  const nav=document.getElementById('workspace-nav');nav.querySelector('.workspace-model>span').textContent='';
  const logo=n('img','workspace-logo');logo.src='img/logo-full-dark.svg';logo.alt='Magic SQD';nav.prepend(logo);
  const log=n('button','header-log');log.append(icon('log'),n('span','','Лог'));log.onclick=()=>setLogExpanded(true);
  const settings=n('button');settings.title='Настройки';settings.setAttribute('aria-label','Настройки');settings.replaceChildren(LabUI.symbol('settings'));settings.onclick=()=>window.settingsDialog.open();
  nav.append(log,settings);
  const menu=n('div','lab-model-tools');menu.hidden=true;
  menu.append(nav.querySelector('.workspace-actions'),document.getElementById('report-btn'));document.body.append(menu);
  const section=document.getElementById('install-content').closest('.card');section.classList.add('workflow-card');section.querySelector('.card-header').hidden=true;
  document.getElementById('back-to-catalog').textContent='‹ Модели';
  const completion=document.getElementById('completion-dialog');completion.querySelector('.modal-logo').replaceWith(n('div','completion-check','✓'));completion.querySelector('h2').textContent='Всё готово';
  const events=window.events;if(events)events.on('apk_progress',LabUI.progress);if(events)events.on('install_log',e=>{const line=document.querySelector('.run-event');if(line&&!line.closest('.progress08'))line.textContent=e.message||e.line||e.text||'Выполняется…';});
  LabUI.watchLog();
  const send=document.getElementById('adb-console-send');if(send){send.replaceChildren(LabUI.symbol('send'));send.setAttribute('aria-label','Отправить');}
  document.getElementById('adb-console-refresh').replaceChildren(LabUI.symbol('refresh'));
  function mountHeader(){
    const header=document.querySelector('.cat-topbar');if(!header||header.id==='global-header')return !!header;
    const shell=document.getElementById('app-shell');header.id='global-header';header.classList.add('catalog');shell.prepend(header);
    const account=document.getElementById('catalog-admin-popover');if(account)document.body.append(account);
    const back=n('button','global-back');back.id='global-back';back.append(icon('back'));back.title='Назад';back.setAttribute('aria-label','Назад');header.prepend(back);
    const modelActions=header.querySelector('.cat-top-actions');menu.hidden=false;modelActions.prepend(menu);
    const shapes={'workspace-add-car':['Добавить машину','M12 5v14M5 12h14'],'workspace-edit-car':['Редактировать автомобиль','m4 16 11-11 4 4L8 20H4v-4Zm9-9 4 4'],'report-btn':['Сообщить об ошибке','M6 21V3m0 1h12l-3 5 3 5H6']};
    for(const [id,[label,path]] of Object.entries(shapes)){const button=document.getElementById(id);const image=window.AppIcons?AppIcons.icon({'workspace-add-car':'plus','workspace-edit-car':'edit','report-btn':'report'}[id]):icon('apps');if(!window.AppIcons)image.querySelector('path').setAttribute('d',path);button.replaceChildren(image);button.title=label;button.setAttribute('aria-label',label);button.classList.add('icon-button');}
    const context=document.getElementById('workspace-model-title');context.classList.add('global-context');header.querySelector('.cat-tools').before(context);
    back.onclick=()=>{if(shell.classList.contains('workspace-open')){if(stageWizard.isBusy())return;if(stageWizard.canGoBack())stageWizard.goBack();else returnToCatalog();}else mainPicker.goBack();update();};
    function update(){const workspace=shell.classList.contains('workspace-open');const visible=workspace||mainPicker.canGoBack();const visibility=visible?'visible':'hidden';if(back.style.visibility!==visibility)back.style.visibility=visibility;const disabled=workspace&&stageWizard.isBusy();if(back.disabled!==disabled)back.disabled=disabled;const model=context.textContent.trim();const text=model&&workspace?model+' · Лог и чат':'События установки и помощь ИИ';if(subtitle.textContent!==text)subtitle.textContent=text;}
    new MutationObserver(update).observe(shell,{childList:true,subtree:true,attributes:true,attributeFilter:['class','disabled','data-level']});update();return true;
  }
  if(!mountHeader()){const observer=new MutationObserver(()=>{if(mountHeader())observer.disconnect();});observer.observe(document.getElementById('picker'),{childList:true,subtree:true});}
})();
