/* Progress reflects native measurements. Unknown package-manager work remains indeterminate. */
(() => {
  const {n}=LabUI;
  const icon=name=>window.AppIcons?AppIcons.icon(name):LabUI.symbol(name);
  const normalize=path=>String(path||'').split('\\').join('/').toLowerCase();
  const phaseNames={download:'Скачивание приложений',transfer:'Передача приложения',install:'Установка приложения',prepare:'Подготовка',done:'Готово'};
  function setProgress(box, measurement) {
    const graphic=box.querySelector('.install-graphic'),bar=box.querySelector('.run-progress'),count=box.querySelector('.install-count');
    const phase=measurement.phase||'install';
    let percent=Number(measurement.percent);
    if(Number(measurement.bytes_total)>0)percent=Number(measurement.bytes_done)/Number(measurement.bytes_total)*100;
    const known=measurement.determinate===true&&Number.isFinite(percent);
    box.dataset.phase=phase;box.dataset.determinate=String(known);
    const title=box.querySelector('h2');
    title.textContent=phaseNames[phase]||'Выполняется установка';
    if(known){
      percent=Math.max(0,Math.min(100,percent));
      graphic.style.setProperty('--progress',percent/100);
      bar.max=100;bar.value=percent;bar.setAttribute('aria-valuetext',`${Math.floor(percent)}%, ${title.textContent}`);
      count.textContent=`${Math.floor(percent)}%`;
    }else{
      graphic.style.removeProperty('--progress');bar.removeAttribute('value');bar.removeAttribute('aria-valuetext');count.textContent='';
    }
    const hint=box.querySelector('.install-phase-detail');
    if(known&&Number(measurement.bytes_total)>0){
      const megabytes=value=>(Number(value)/1048576).toLocaleString('ru-RU',{maximumFractionDigits:1});
      hint.textContent=`${megabytes(measurement.bytes_done)} из ${megabytes(measurement.bytes_total)} МБ`;
    }else hint.textContent=phase==='install'?'Ожидаем подтверждение устройства':known?'':'Получаем данные о ходе операции';
  }
  LabUI.busy=(root,label,items=[])=>{
    root.querySelector('.run-status')?.remove();
    const box=n('section','run-status progress08');box.setAttribute('role','status');box.dataset.determinate='false';
    const graphic=n('div','install-graphic');
    graphic.innerHTML='<svg viewBox="0 0 140 140" aria-hidden="true"><circle class="install-track" cx="70" cy="70" r="61"/><circle class="install-meter" cx="70" cy="70" r="61"/></svg>';
    const center=n('div','install-center');center.append(icon('apps'),n('span','install-count'));graphic.append(center);
    const progress=n('progress','run-progress');progress.setAttribute('aria-label','Ход текущей операции');
    box.append(graphic,n('h2','',label||'Установка приложений'),n('p','install-phase-detail','Подготавливаем файлы'),n('p','run-event','Ожидаем ответ устройства'),progress);
    const unique=[...new Map(items.map(item=>[normalize(item.path),item])).values()];
    if(unique.length){
      box.append(n('p','queue-summary',`Готово 0 из ${unique.length}`));
      const queue=n('ul','run-queue');
      unique.forEach(item=>{const row=n('li');row.dataset.path=item.path||'';row.dataset.state='pending';const mark=n('span','queue-state');mark.append(icon('check'));row.append(LabUI.appIcon(item.path),n('span','run-app-name',item.name||'Приложение'),n('small','','Ожидает'),mark);queue.append(row);});
      box.append(queue);
    }
    root.prepend(box);root.classList.add('is-running');return box;
  };
  LabUI.progress=e=>{
    const root=[...document.querySelectorAll('.is-running')].find(node=>String(e.stage_index)===node.dataset.stageIndex);
    const box=root?.querySelector('.progress08');if(!box)return;
    const rows=[...box.querySelectorAll('.run-queue li')];
    const row=rows.find(node=>normalize(node.dataset.path)===normalize(e.path));
    if(row){
      // Downloads are sequential preparation, not completed installations.
      // A different current file must not leave the previous download spinning.
      rows.filter(node=>node!==row&&node.dataset.phase==='download'&&node.dataset.state==='running').forEach(node=>{
        const ready=node.dataset.downloadComplete==='true';
        node.dataset.state=ready?'queued':'pending';
        node.querySelector('small').textContent=ready?'Готов к передаче':'Ожидает';
      });
      if(e.phase==='download'){
        const total=Number(e.bytes_total),done=Number(e.bytes_done);
        if(Number.isFinite(total)&&total>0&&Number.isFinite(done))row.dataset.downloadComplete=String(done>=total);
        else if(row.dataset.phase!=='download')delete row.dataset.downloadComplete;
      }
      if(e.phase)row.dataset.phase=e.phase;
      else if(e.state==='running')row.dataset.phase='install';
      if(['running','done','error'].includes(e.state)){
        row.dataset.state=e.state;row.querySelector('small').textContent={running:'Установка…',done:'Готово',error:'Ошибка'}[e.state];
      }
      if(e.phase==='download'&&e.state!=='error'){
        if(row.dataset.downloadComplete==='true'){
          row.dataset.state='queued';row.querySelector('small').textContent='Готов к передаче';
        }else if(e.state==='done'){
          row.dataset.state='pending';row.querySelector('small').textContent='Ожидает';
        }
      }
    }
    const completed=rows.filter(node=>node.dataset.state==='done').length;
    const summary=box.querySelector('.queue-summary');if(summary)summary.textContent=`Готово ${completed} из ${rows.length}`;
    if(e.phase){
      setProgress(box,e);
      if(row){const label={download:'Скачивание…',transfer:'Передача…',install:'Установка…'}[e.phase];if(row.dataset.state==='running'&&label)row.querySelector('small').textContent=label;}
    }else if(e.state==='running')setProgress(box,{phase:'install',determinate:false});
    if(row)box.querySelector('.run-event').textContent=row.querySelector('.run-app-name').textContent;
    if(rows.length&&completed===rows.length){
      setProgress(box,{phase:'done',determinate:true,percent:100});
      box.querySelector('.install-phase-detail').textContent='Все приложения установлены';
      box.querySelector('.install-center>.ui-icon').replaceWith(icon('check'));
    }
  };
})();
