/* LAB12: bounded pointer light and one reveal per brand entry/scroll, no idle loop. */
(() => {
  const installed = Symbol.for('magicsqd.catalog11.brands');
  if (window[installed]) return;
  window[installed] = true;
  const finePointer = matchMedia('(hover:hover) and (pointer:fine)');
  const motionPreference = matchMedia('(prefers-reduced-motion:reduce)');
  const reduced = () => motionPreference.matches || document.documentElement.classList.contains('reduce-motion');
  const grids = new WeakMap(), records = new WeakMap(), tracked = new Set(), reveals = new Set();
  let active = null, pending = null, frame = 0;
  const visualProperties = ['--catalog11-light-x','--catalog11-light-y','--catalog11-light-strength'];
  const cardProperties = ['--catalog12-lift','--catalog12-tilt-x','--catalog12-tilt-y','--catalog12-mark-x','--catalog12-mark-y','--catalog12-card-x','--catalog12-card-y'];
  const clamp = (n,min,max) => Math.max(min,Math.min(max,n));
  const percent = n => clamp(n,0,100).toFixed(2)+'%';

  function releaseActive() {
    if (!active) return;
    active.visual.classList.remove('catalog11-lit');
    active.card.classList.remove('catalog12-pointer');
    visualProperties.forEach(p=>active.visual.style.removeProperty(p));
    cardProperties.forEach(p=>active.card.style.removeProperty(p));
    active = null;
  }
  function resetPointer() { pending = null; releaseActive(); }
  function schedule() { if (!frame) frame = requestAnimationFrame(flush); }
  function ready(card) {
    const v=card.querySelector('.cat-visual');
    return v && (v.classList.contains('catalog09-ready') || v.querySelector('.ui-icon,.cat-monogram'));
  }
  function revealNow(card) {
    const record=records.get(card);
    if (!record) return;
    record.seen=true;reveals.delete(record);intersection?.unobserve(card);
    card.classList.remove('catalog12-wait','catalog12-entering');
    card.classList.add('catalog12-immediate');
    card.style.removeProperty('--catalog12-entry-delay');
  }
  function queueReveal(record) {
    if (!record.seen && record.visible && ready(record.card)) { reveals.add(record); schedule(); }
  }
  const intersection = window.IntersectionObserver ? new IntersectionObserver(entries=>{
    for(const entry of entries){const record=records.get(entry.target);if(!record||record.seen)continue;record.visible=entry.isIntersecting;if(record.visible)queueReveal(record);}
  },{threshold:.08,rootMargin:'0px 0px -12px 0px'}) : null;

  function paintLight(next) {
    if (!next || !finePointer.matches || !next.visual.isConnected || !next.visual.classList.contains('catalog10-vector')) { releaseActive(); return; }
    if (active?.visual!==next.visual) releaseActive();
    const {card,visual}=next,r=visual.getBoundingClientRect(),cr=card.getBoundingClientRect();
    if(r.width<=0||r.height<=0||cr.width<=0||cr.height<=0){releaseActive();return;}
    const style=getComputedStyle(visual),value=(name,fallback)=>{const n=parseFloat(style.getPropertyValue(name));return Number.isFinite(n)?n:fallback;};
    const w=value('--catalog09-width',visual.clientWidth),h=value('--catalog09-height',visual.clientHeight);
    if(w<=0||h<=0){releaseActive();return;}
    const x=((next.x-r.left)*visual.clientWidth/r.width-value('--catalog09-left',0))/w;
    const y=((next.y-r.top)*visual.clientHeight/r.height-value('--catalog09-top',0))/h;
    const cx=clamp((next.x-cr.left)/cr.width,0,1),cy=clamp((next.y-cr.top)/cr.height,0,1);
    visual.style.setProperty('--catalog11-light-x',percent(x*100));
    visual.style.setProperty('--catalog11-light-y',percent(y*100));
    visual.style.setProperty('--catalog11-light-strength','.92');
    card.style.setProperty('--catalog12-card-x',percent(cx*100));card.style.setProperty('--catalog12-card-y',percent(cy*100));
    if(!reduced()){
      card.style.setProperty('--catalog12-lift','-3px');
      card.style.setProperty('--catalog12-tilt-x',((.5-cy)*3).toFixed(2)+'deg');
      card.style.setProperty('--catalog12-tilt-y',((cx-.5)*4).toFixed(2)+'deg');
      card.style.setProperty('--catalog12-mark-x',((cx-.5)*3.6).toFixed(2)+'px');
      card.style.setProperty('--catalog12-mark-y',((cy-.5)*2.8).toFixed(2)+'px');
    }
    visual.classList.add('catalog11-lit');card.classList.add('catalog12-pointer');active={card,visual};
  }
  function flush() {
    frame=0;
    if(pending){const next=pending;pending=null;paintLight(next);}
    const batch=[...reveals].filter(r=>r.card.isConnected&&!r.seen&&r.visible&&ready(r.card));reveals.clear();
    batch.sort((a,b)=>a.card.compareDocumentPosition(b.card)&Node.DOCUMENT_POSITION_FOLLOWING?-1:1);
    const ranks=new Map();
    for(const record of batch){
      if(reduced()){revealNow(record.card);continue;}
      const rank=ranks.get(record.grid)||0;ranks.set(record.grid,rank+1);
      record.card.style.setProperty('--catalog12-entry-delay',Math.min(rank*48,240)+'ms');
      record.card.classList.add('catalog12-entering');record.card.classList.remove('catalog12-wait');
      record.seen=true;intersection?.unobserve(record.card);
    }
  }
  function processGrid(grid) {
    let state=grids.get(grid);if(!state){state={level:null,intro:false};grids.set(grid,state);}
    const cards=[...grid.querySelectorAll(':scope > .cat-brand')];
    const level=grid.dataset.level || (cards.length?'brand':'other');
    if(level!==state.level){state.intro=level==='brand';state.level=level;}
    if(level!=='brand')return;
    for(const card of cards){
      let record=records.get(card);
      if(!record){
        record={card,grid,seen:false,visible:!intersection};records.set(card,record);tracked.add(record);card.classList.add('catalog12-brand');
        if(!state.intro||reduced())revealNow(card);
        else{card.classList.add('catalog12-wait');intersection?.observe(card);queueReveal(record);}
      }else queueReveal(record);
    }
    if(cards.length)state.intro=false;
  }
  function tidy() {
    for(const r of tracked)if(!r.card.isConnected){intersection?.unobserve(r.card);reveals.delete(r);tracked.delete(r);}
    if(active&&!active.card.isConnected)resetPointer();
  }
  function motionChanged() { resetPointer();if(reduced())for(const r of tracked)revealNow(r.card); }
  function start() {
    document.querySelectorAll('.cat-grid').forEach(processGrid);
    const observer=new MutationObserver(changes=>{
      const dirty=new Set();let removed=false;
      for(const change of changes){
        const target=change.target;if(!(target instanceof Element))continue;
        if(target===document.documentElement){motionChanged();continue;}
        if(change.type==='attributes'){
          if(change.attributeName==='data-level'&&target.matches('.cat-grid'))dirty.add(target);
          else {const card=target.closest('.cat-brand'),r=card&&records.get(card);if(r)queueReveal(r);}
          continue;
        }
        const grid=target.closest('.cat-grid');if(grid)dirty.add(grid);
        for(const node of change.addedNodes)if(node instanceof Element){if(node.matches('.cat-grid'))dirty.add(node);node.querySelectorAll('.cat-grid').forEach(g=>dirty.add(g));}
        removed = removed || change.removedNodes.length>0;
      }
      dirty.forEach(processGrid);if(removed)tidy();
    });
    observer.observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['class','data-level']});
    observer.observe(document.documentElement,{attributes:true,attributeFilter:['class']});
  }

  document.addEventListener('pointermove',event=>{
    if(!finePointer.matches||event.pointerType!=='mouse'){resetPointer();return;}
    const card=event.target instanceof Element?event.target.closest('.cat-brand'):null,visual=card?.querySelector('.cat-visual.catalog10-vector');
    if(!visual){resetPointer();return;}pending={card,visual,x:event.clientX,y:event.clientY};schedule();
  },{passive:true});
  document.addEventListener('pointerout',event=>{const card=pending?.card||active?.card;if(card&&(!(event.relatedTarget instanceof Node)||!card.contains(event.relatedTarget)))resetPointer();},{passive:true});
  document.addEventListener('pointerdown',event=>{if(event.pointerType!=='mouse')resetPointer();const card=event.target instanceof Element?event.target.closest('.cat-brand'):null;if(card)revealNow(card);},{passive:true});
  document.addEventListener('focusin',event=>{const card=event.target instanceof Element?event.target.closest('.cat-brand'):null;if(card)revealNow(card);});
  document.addEventListener('transitionend',event=>{if(event.propertyName==='translate'&&event.target instanceof Element&&event.target.matches('.cat-brand'))event.target.classList.remove('catalog12-entering');});
  document.addEventListener('pointercancel',resetPointer,{passive:true});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)resetPointer();});window.addEventListener('blur',resetPointer);
  finePointer.addEventListener('change',resetPointer);motionPreference.addEventListener('change',motionChanged);
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
