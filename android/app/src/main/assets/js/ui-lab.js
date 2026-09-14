(() => {
  function boot(){
    window.events.on('apk_progress',LabUI.progress);
    const topLog=document.getElementById('top-log');topLog.replaceChildren(LabUI.icon('log'),LabUI.n('span','','Лог'));
    document.getElementById('log-collapse-btn').replaceChildren(LabUI.icon('close'));document.getElementById('log-collapse-btn').setAttribute('aria-label','Закрыть лог');
    document.getElementById('log-copy-btn').replaceChildren(LabUI.icon('copy'));document.getElementById('log-copy-btn').setAttribute('aria-label','Скопировать лог');
    document.getElementById('log-cmd-input').placeholder='Вопрос ИИ или команда…';document.getElementById('log-cmd-input').setAttribute('aria-label','Вопрос ИИ или команда');
    document.querySelector('.catalog-actions>a[href*=github]')?.remove();
    document.getElementById('top-back').replaceChildren(LabUI.icon('back'));document.getElementById('top-back').setAttribute('aria-label','Назад');
    document.getElementById('log-cmd-run').replaceChildren(LabUI.symbol('send'));document.getElementById('log-cmd-run').setAttribute('aria-label','Отправить');
    const context=LabUI.n('p','log-context','События установки и помощь ИИ');document.querySelector('.log-card-header').after(context);
    new MutationObserver(()=>{context.textContent=document.getElementById('top-title').textContent.trim()||'События установки и помощь ИИ';}).observe(document.getElementById('top-title'),{childList:true,subtree:true,characterData:true});
    LabUI.watchLog();
    document.getElementById('log-overlay').setAttribute('role','dialog');document.getElementById('log-overlay').setAttribute('aria-label','Лог и чат');
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);else boot();
})();
