/* LAB05 shared native-app presentation. No synthetic installation events. */
(() => {
  const {n,icon}=LabUI;
  const paths={wifi:'M2 8a16 16 0 0 1 20 0M5 12a11 11 0 0 1 14 0M8 16a6 6 0 0 1 8 0M12 20h.01',send:'M12 20V4m-6 6 6-6 6 6',refresh:'M20 7v5h-5M4 17v-5h5M6 6a8 8 0 0 1 13 3M18 18A8 8 0 0 1 5 15',settings:'M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1 1-3ZM12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6',play:'m9 5 10 7-10 7V5',stop:'M6 6h12v12H6V6',usb:'M12 21V3m-3 3 3-3 3 3M12 16l-6-4V8m6 5 6-4V6'};
  LabUI.symbol=name=>{if(window.AppIcons)return AppIcons.icon(name);if(!paths[name])return icon(name);const x=icon('apps');x.querySelector('path').setAttribute('d',paths[name]);return x;};
  const cache=new Map(),pending=new Map();
  function paintIcon(node,src){if(!src)return;const img=n('img');img.alt='';img.src=src;img.onerror=()=>img.replaceWith(icon('apps'));node.replaceChildren(img);}
  LabUI.appIcon=(path,src)=>{
    const node=n('span','apk-icon');node.setAttribute('aria-hidden','true');node.dataset.path=path||'';node.append(icon('apps'));
    if(src){queueMicrotask(()=>paintIcon(node,src));return node;}
    if(cache.has(path)){queueMicrotask(()=>paintIcon(node,cache.get(path)));return node;}
    if(!path)return node;
    if(!pending.has(path)){
      pending.set(path,[]);
      if(window.AndroidBridge)Bridge.call('lab_apk_icon',{path});
      else if(window.pywebview?.api?.scanner_apk_icon)window.pywebview.api.scanner_apk_icon(path).then(src=>resolveIcon(path,src)).catch(()=>resolveIcon(path,null));
      else pending.delete(path);
    }
    pending.get(path)?.push(node);return node;
  };
  function resolveIcon(path,src){if(src)cache.set(path,src);(pending.get(path)||[]).forEach(node=>paintIcon(node,src));pending.delete(path);}
  window.events?.on('apk_icon',e=>resolveIcon(e.path,e.icon));
  LabUI.decorateLog=line=>{
    if(line.dataset.decorated)return;line.dataset.decorated='true';line.dataset.logText=line.textContent;
    const chat=line.matches('.chat-bubble,.chat-command-line');
    const time=n('time','entry-time',new Date().toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit'}));time.dateTime=new Date().toISOString();
    if(chat){const header=n('div','entry-header');const user=line.classList.contains('chat-user');header.append(n('span','entry-avatar',user?'Вы':'ИИ'),n('strong','',user?'Вы':'Помощник'),time);line.prepend(header);}
    else line.prepend(time);
  };
  LabUI.watchLog=()=>{
    const panel=document.getElementById('log-panel');if(!panel)return;
    const decorate=()=>panel.querySelectorAll('.log-line:not([data-decorated])').forEach(LabUI.decorateLog);
    decorate();new MutationObserver(decorate).observe(panel,{childList:true});
  };
  LabUI.connection=({port=5555,host='',title='Подключение по Wi-Fi',help='',scan,connect,onClose})=>{
    const modal=n('dialog','connection-dialog');modal.setAttribute('aria-label',title);
    const head=n('header','connection-heading'),badge=n('span','connection-symbol');badge.append(LabUI.symbol('wifi'));
    const close=n('button','icon-button');close.append(icon('close'));close.setAttribute('aria-label','Закрыть');
    head.append(badge,n('h2','',title),close);
    const fields=n('div','connection-fields');
    const hostLabel=n('label','','IP или имя устройства'),hostInput=n('input');hostInput.value=host;hostInput.placeholder='192.168.1.100';hostInput.autocomplete='off';hostInput.spellcheck=false;hostLabel.append(hostInput);
    const portLabel=n('label','','Порт'),portInput=n('input');portInput.type='number';portInput.min=1;portInput.max=65535;portInput.value=port||'';portInput.placeholder='5555';portLabel.append(portInput);fields.append(hostLabel,portLabel);
    const status=n('p','connection-status','Укажите адрес устройства или выберите его в сети.');status.setAttribute('role','status');
    const results=n('div','connection-results');const search=n('button','connection-search');search.append(LabUI.symbol('refresh'),n('span','','Найти устройства'));
    const actions=n('footer','dialog-actions'),cancel=n('button','','Отмена'),submit=n('button','accent','Подключиться');actions.append(cancel,submit);
    modal.append(head,n('p','connection-help',help||'Подключите оба устройства к одной сети Wi-Fi.'),fields,status,results,search,actions);document.body.append(modal);modal.showModal();
    let generation=0,busy=false;
    const validPort=()=>{const p=Number(portInput.value||portInput.placeholder);if(!Number.isInteger(p)||p<1||p>65535){status.textContent='Порт должен быть от 1 до 65535.';portInput.focus();return null;}return p;};
    const finish=()=>{generation++;onClose?.();modal.remove();};modal.addEventListener('close',finish,{once:true});
    const dismiss=()=>{if(!busy)modal.close();};close.onclick=cancel.onclick=dismiss;
    modal.addEventListener('cancel',e=>{if(busy)e.preventDefault();});
    modal.addEventListener('click',e=>{if(e.target===modal){const r=modal.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dismiss();}});
    async function searchNow(){const p=validPort();if(!p||busy)return;const g=++generation;search.disabled=true;status.textContent='Ищем устройства в сети…';status.dataset.state='searching';results.replaceChildren();
      try{const hosts=await scan(p);if(g!==generation||!modal.open)return;status.textContent=hosts.length?'Выберите устройство для подключения':'Устройства не найдены. Можно ввести адрес вручную.';hosts.forEach(item=>{const h=typeof item==='string'?item:item.host;const b=n('button','connection-device');b.append(LabUI.symbol('wifi'),n('span','',h),n('small','',item.port?String(item.port):'В сети'));b.onclick=()=>{hostInput.value=h;if(item.port)portInput.value=item.port;results.querySelectorAll('button').forEach(x=>x.classList.toggle('selected',x===b));hostInput.focus();};results.append(b);});}
      catch{if(g===generation)status.textContent='Не удалось просканировать сеть. Введите адрес вручную.';}
      finally{if(g===generation){search.disabled=false;status.dataset.state='idle';}}
    }
    search.onclick=searchNow;
    submit.onclick=async()=>{let h=hostInput.value.trim();const hp=h.match(/^([^:\s]+):(\d{1,5})$/);if(hp){h=hp[1];hostInput.value=h;portInput.value=hp[2];}const p=validPort();if(!p)return;if(!h||/\s/.test(h)){status.textContent='Введите IP или имя устройства без пробелов.';hostInput.focus();return;}if(/^\d+$/.test(h)){status.textContent='В адресе нет точек — IPv4 выглядит как 192.168.1.100.';hostInput.focus();return;}if(/^[\d.]+$/.test(h)&&!/^(25[0-5]|2[0-4]\d|1?\d?\d)(\.(25[0-5]|2[0-4]\d|1?\d?\d)){3}$/.test(h)){status.textContent='Некорректный IPv4-адрес — четыре числа от 0 до 255 через точку.';hostInput.focus();return;}generation++;busy=true;[close,cancel,submit,search,hostInput,portInput].forEach(x=>x.disabled=true);status.textContent='Подключаемся к устройству…';status.dataset.state='searching';
      try{const result=await connect(h,p);if(result?.ok===false)throw new Error(result.message||result.error||'Не удалось подключиться. Проверьте адрес и порт.');modal.close();}
      catch(e){status.textContent=e.message||'Не удалось подключиться.';status.dataset.state='error';}
      finally{busy=false;[close,cancel,submit,search,hostInput,portInput].forEach(x=>x.disabled=false);}
    };
    hostInput.onkeydown=e=>{if(e.key==='Enter')submit.click();};searchNow();return modal;
  };
  const originalBusy=LabUI.busy;
  LabUI.busy=(root,label,items)=>{
    const box=originalBusy(root,label,items);const graphic=n('div','install-graphic');graphic.innerHTML='<svg viewBox="0 0 140 140" aria-hidden="true"><circle class="install-track" cx="70" cy="70" r="61"/><circle class="install-meter" cx="70" cy="70" r="61"/></svg>';graphic.append(LabUI.symbol('apps'));const counter=n('span','install-count','0');graphic.append(counter);box.prepend(graphic);
    box.querySelectorAll('.run-queue li').forEach((row,i)=>{row.prepend(LabUI.appIcon(items[i]?.path));const mark=n('span','queue-state');mark.append(icon('check'));row.append(mark);});return box;
  };
  const originalProgress=LabUI.progress;
  LabUI.progress=e=>{originalProgress(e);const root=document.querySelector('.is-running');if(!root||String(e.stage_index)!==root.dataset.stageIndex)return;const total=Number(e.total),done=Number(e.completed);if(total>0){root.querySelector('.install-graphic')?.style.setProperty('--progress',Math.max(0,Math.min(1,done/total)));const count=root.querySelector('.install-count');if(count)count.textContent=`${done} / ${total}`;const row=[...root.querySelectorAll('.run-queue li')].find(r=>r.dataset.path===e.path);const event=root.querySelector('.run-event');if(row&&event)event.textContent=(row.querySelector('span:not(.apk-icon):not(.queue-state)')?.textContent||'Приложение')+' · '+({running:'устанавливается',done:'готово',error:'ошибка'}[e.state]||'ожидает');} };
})();
