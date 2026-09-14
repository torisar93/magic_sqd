/* LAB14: stable hit areas for list rows and full-width actions, shared pointer light. */
(() => {
  const installed=Symbol.for('magicsqd.buttons13');
  if(window[installed]) return;
  window[installed]=true;
  const selector='button:not(.cat-brand),[role="button"]:not(.cat-brand),a.catalog-link,a.catalog-topbar-link,a.accent';
  const stableSelector='.apps-section-header,.app-personal-apk-add,.model-list>.cat-card';
  const pointer=matchMedia('(hover:hover) and (pointer:fine)');
  const motion=matchMedia('(prefers-reduced-motion:reduce)');
  const reduced=()=>motion.matches||document.documentElement.matches('.reduce-motion,.low-perf');
  const unavailable=el=>!el?.isConnected||el.matches(':disabled,[aria-disabled="true"],[aria-busy="true"]')||!!el.closest('[inert],[hidden],dialog:not([open])');
  const props=['--button13-background','--button13-strength','--button13-x','--button13-y','--button13-lift','--button13-rx','--button13-ry','--button14-mark-x','--button14-transform-time','--button14-press-scale'];
  const leaving=new Map();
  let active=null,pending=null,frame=0;
  const clamp=value=>Math.max(0,Math.min(1,value));
  const signature=el=>[...el.classList].filter(name=>!['button13','button13-pointer','button13-stable'].includes(name)).sort().join(' ');
  const inRestRect=event=>active&&event.clientX>=active.rect.left&&event.clientX<=active.rect.right&&event.clientY>=active.rect.top&&event.clientY<=active.rect.bottom;

  function restoreHitArea(el) {
    // Restore the actual clickable surface when a fast move crosses its lifted edge.
    el.style.setProperty('--button14-transform-time','0ms');
    el.style.setProperty('--button13-lift','0px');
    el.style.setProperty('--button13-rx','0deg');
    el.style.setProperty('--button13-ry','0deg');
    el.style.setProperty('--button14-press-scale','1');
  }

  function clear(el) {
    clearTimeout(leaving.get(el));leaving.delete(el);
    el.classList.remove('button13-pointer');
    props.forEach(name=>el.style.removeProperty(name));
  }
  function release(immediate=false) {
    pending=null;
    if(!active) return;
    const el=active.el;active=null;
    if(immediate||unavailable(el)||reduced()) { clear(el);return; }
    el.style.setProperty('--button13-strength','0');
    el.style.setProperty('--button13-lift','0px');
    el.style.setProperty('--button13-rx','0deg');
    el.style.setProperty('--button13-ry','0deg');
    el.style.setProperty('--button14-mark-x','0px');
    leaving.set(el,setTimeout(()=>clear(el),360));
  }
  function reset() {
    release(true);for(const el of [...leaving.keys()])clear(el);
    if(frame)cancelAnimationFrame(frame);frame=0;
  }
  function paint() {
    frame=0;
    const next=pending;pending=null;
    if(!next||!pointer.matches||unavailable(next.el)){release(true);return;}
    const el=next.el;
    if(active?.el!==el) {
      release();clear(el);
      const rect=el.getBoundingClientRect();
      if(!rect.width||!rect.height) return;
      // Read the current semantic surface: primary, danger, selected, or neutral.
      const background=getComputedStyle(el).backgroundImage;
      active={el,rect,signature:signature(el)};
      el.style.setProperty('--button13-background',background);
      el.classList.add('button13-pointer');
    }
    const {rect}=active,x=clamp((next.x-rect.left)/rect.width),y=clamp((next.y-rect.top)/rect.height);
    el.style.setProperty('--button13-x',(x*100).toFixed(2)+'%');
    el.style.setProperty('--button13-y',(y*100).toFixed(2)+'%');
    el.style.setProperty('--button13-strength','1');
    const still=reduced()||el.matches(stableSelector);
    const edge=Math.min(x*rect.width,(1-x)*rect.width,y*rect.height,(1-y)*rect.height);
    const proximity=clamp((edge-4)/8),amount=still?0:proximity*proximity*(3-2*proximity);
    // At the rim, geometry follows the pointer directly so easing cannot uncover it.
    el.style.setProperty('--button14-transform-time',edge<12?'0ms':'340ms');
    el.style.setProperty('--button14-press-scale',(1-.015*amount).toFixed(4));
    el.style.setProperty('--button13-lift',(-2*amount).toFixed(2)+'px');
    el.style.setProperty('--button13-rx',((.5-y)*3*amount).toFixed(2)+'deg');
    // Keep very wide actions calm, with the same light travelling across them.
    const angle=Math.min(4,650/rect.width);
    el.style.setProperty('--button13-ry',((x-.5)*angle*amount).toFixed(2)+'deg');
    el.style.setProperty('--button14-mark-x',reduced()?'0px':((x-.5)*4).toFixed(2)+'px');
  }
  function move(event) {
    if(!pointer.matches||event.pointerType!=='mouse'||event.buttons){reset();return;}
    let target=event.target instanceof Element?event.target:null;
    if(active&&inRestRect(event)&&target!==active.el&&target?.contains(active.el)) {
      restoreHitArea(active.el);
      target=document.elementFromPoint(event.clientX,event.clientY);
    }
    const el=target?.closest(selector);
    if(unavailable(el)){release();return;}
    pending={el,x:event.clientX,y:event.clientY};
    if(!frame)frame=requestAnimationFrame(paint);
  }
  function register(node) {
    if(node.matches(selector)) {
      if(!node.classList.contains('button13'))node.classList.add('button13');
      const stable=node.matches(stableSelector);
      if(node.classList.contains('button13-stable')!==stable)node.classList.toggle('button13-stable',stable);
    }else if(node.classList.contains('button13')) {
      if(active?.el===node)reset();else clear(node);
      node.classList.remove('button13','button13-stable');
    }
  }
  function collect(node) {
    if(!(node instanceof Element))return;
    register(node);
    node.querySelectorAll(selector).forEach(register);
  }
  function start() {
    collect(document.body);
    const observer=new MutationObserver(changes=>{
      for(const change of changes){
        if(change.type==='childList')change.addedNodes.forEach(collect);
        if(change.type==='attributes'&&change.attributeName==='class'){
          const target=change.target;
          if(active&&target===active.el&&(signature(target)!==active.signature||!target.classList.contains('button13-pointer')))reset();
          register(target);
          // Pointer classes are ours; do not reset for their own mutation records.
          if(target!==document.documentElement&&target===active?.el)continue;
          if(target!==document.documentElement&&!target.contains(active?.el))continue;
        }else if(change.type==='attributes'&&change.attributeName==='role')register(change.target);
        if(active&&(unavailable(active.el)||change.target===document.documentElement||
          (change.type==='attributes'&&(change.target===active.el||change.target.contains(active.el))))) reset();
      }
      for(const el of leaving.keys())if(unavailable(el))clear(el);
    });
    observer.observe(document.body,{subtree:true,childList:true,attributes:true,attributeFilter:['class','disabled','aria-disabled','aria-busy','hidden','inert','role','open']});
    observer.observe(document.documentElement,{attributes:true,attributeFilter:['class']});
  }
  document.addEventListener('pointerover',move,{passive:true});
  document.addEventListener('pointermove',move,{passive:true});
  document.addEventListener('pointerout',event=>{
    const el=active?.el||pending?.el;
    if(active&&inRestRect(event)&&event.relatedTarget!==active.el&&event.relatedTarget instanceof Element&&event.relatedTarget.contains(active.el)) {
      restoreHitArea(active.el);
      return;
    }
    if(el&&(!(event.relatedTarget instanceof Node)||!el.contains(event.relatedTarget)))release();
  },{passive:true});
  document.addEventListener('pointercancel',reset,{passive:true});
  document.addEventListener('pointerdown',event=>{if(event.pointerType!=='mouse')reset();},{passive:true});
  document.addEventListener('keydown',event=>{if(['Tab','Escape','Enter',' '].includes(event.key))reset();});
  document.addEventListener('scroll',reset,{capture:true,passive:true});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)reset();});
  window.addEventListener('blur',reset);window.addEventListener('resize',reset);
  pointer.addEventListener('change',reset);motion.addEventListener('change',reset);
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',start,{once:true});else start();
})();
