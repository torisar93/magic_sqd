/* LAB09: optional bundled catalogue imagery. Native catalogue data stays intact. */
(() => {
  const scriptUrl = document.currentScript?.src || new URL('js/catalog09.js', document.baseURI).href;
  const assetRoot = new URL('../img/catalog09/', scriptUrl);
  const mobile = location.hostname === 'appassets.androidplatform.net' || !!window.AndroidBridge;
  document.documentElement.classList.toggle('catalog09-mobile', mobile);
  const normalize = value => String(value ?? '').normalize('NFC').trim().replace(/\s+/g, ' ').toLowerCase();
  const key = (brand, model) => `${normalize(brand)}/${normalize(model)}`;
  let manifest = { brands: {}, models: {} };
  const records = new Set(), bindings = new WeakMap();
  let cleanupQueued = false, manifestSettled = false;
  const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)');

  function originalUrl(value) {
    if (!value) return '';
    const text = String(value);
    if (/^(?:data:|blob:|https?:|file:)/i.test(text)) return text;
    // Android's selected version.logo is data-relative; group src is already a URL.
    // Generated assets are resolved separately and never pass through this branch.
    if (mobile && /^cars\//i.test(text.split('\\').join('/'))) {
      return 'https://appassets.androidplatform.net/data/' + text.split('\\').join('/').split('/').map(encodeURIComponent).join('/');
    }
    return text;
  }

  function entryFor(spec) {
    if (spec.kind === 'brand') return manifest.brands[normalize(spec.brand)];
    const group = manifest.models[key(spec.brand, spec.model)];
    return group?.variants?.[normalize(spec.modification)] || group;
  }

  function entryUrl(entry, environment = false) {
    if (!entry || entry.approved === false || typeof entry.src !== 'string') return '';
    // Only packaged assets from this dedicated directory can override the source.
    const src = entry.src.split('\\').join('/');
    const allowed = environment ? /^environment\/[a-z0-9][a-z0-9._-]*\.(?:png|webp)$/i : /^(?:brands\/[a-z0-9][a-z0-9._-]*\.(?:png|webp|svg)|models\/[a-z0-9][a-z0-9._-]*\.(?:png|webp))$/i;
    if (!allowed.test(src)) return '';
    return new URL(src, assetRoot).href;
  }

  function resetFraming(wrapper) {
    wrapper.classList.remove('catalog09-generated', 'catalog09-framed', 'catalog09-layered', 'catalog09-car-framed', 'catalog09-ready', 'catalog10-vector');
    for (const prop of ['--catalog09-width','--catalog09-height','--catalog09-left','--catalog09-top','--catalog09-environment','--catalog10-mark']) wrapper.style.removeProperty(prop);
  }

  function frameBrand(record, entry) {
    if (record.spec.kind !== 'brand') return;
    const size = entry.size || [entry.width, entry.height];
    const [w,h] = size, b = entry.alphaBounds;
    if (!Array.isArray(b) || b.length !== 4 || ![w,h,...b].every(Number.isFinite)) return;
    const [left,top,right,bottom] = b, bw = right-left, bh = bottom-top;
    if (bw <= 0 || bh <= 0 || left < 0 || top < 0 || right > w || bottom > h) return;
    const box = record.wrapper.getBoundingClientRect();
    if (box.width <= 0 || box.height <= 0) return;
    // Match visible alpha bounds to the same box without editing the generator PNG.
    const scale = Math.min(.94*box.width / bw, .86*box.height / bh) * (entry.opticalScale || 1);
    const x = (box.width-bw*scale)/2-left*scale, y = (box.height-bh*scale)/2-top*scale;
    record.wrapper.classList.add('catalog09-framed');
    for (const [name,value] of [['width',w*scale],['height',h*scale],['left',x],['top',y]]) record.wrapper.style.setProperty('--catalog09-'+name,value+'px');
  }

  function frameCar(record, entry) {
    if (record.spec.kind !== 'model' || entry.transparent !== true) return;
    const [w,h] = entry.size || [], alpha = entry.alphaBounds, body = entry.bodyBounds;
    const valid = b => Array.isArray(b) && b.length === 4 && [w,h,...b].every(Number.isFinite) && w > 0 && h > 0 && b[0] >= 0 && b[1] >= 0 && b[2] <= w && b[3] <= h && b[2] > b[0] && b[3] > b[1];
    if (!valid(alpha) || !valid(body) || body[0] < alpha[0] || body[1] < alpha[1] || body[2] > alpha[2] || body[3] > alpha[3]) return;
    const box = record.wrapper.getBoundingClientRect();
    if (box.width <= 0 || box.height <= 0) return;
    const aw=alpha[2]-alpha[0], ah=alpha[3]-alpha[1], bw=body[2]-body[0], bh=body[3]-body[1];
    // Match the visible body, while retaining the complete soft shadow and every alpha edge.
    // One virtual stage and one body width for every model; no per-car zoom.
    const targetWidth = Math.min(.94*box.width, 1.6*box.height);
    const scale = targetWidth / bw;
    const clamp = (v,min,max) => Math.max(min,Math.min(max,v));
    const x = clamp(box.width/2-(body[0]+bw/2)*scale, .02*box.width-alpha[0]*scale, .98*box.width-alpha[2]*scale);
    const y = clamp(.94*box.height-body[3]*scale, .02*box.height-alpha[1]*scale, .98*box.height-alpha[3]*scale);
    record.wrapper.classList.add('catalog09-car-framed');
    for (const [name,value] of [['width',w*scale],['height',h*scale],['left',x],['top',y]]) record.wrapper.style.setProperty('--catalog09-'+name,value+'px');
  }

  function applyEnvironment(record, url, current) {
    if (!url) return;
    const {wrapper} = record;
    wrapper.classList.add('catalog09-layered');
    wrapper.style.setProperty('--catalog09-environment', `url("${url}")`);
    const scene = wrapper.closest('.model-detail');
    if (scene) {
      scene.classList.add('catalog10-scene');
      scene.style.setProperty('--catalog09-environment', `url("${url}")`);
    }
    const background = new Image();
    background.onerror = () => {
      if (!current()) return;
      wrapper.classList.remove('catalog09-layered');
      wrapper.style.removeProperty('--catalog09-environment');
      if (scene?.contains(wrapper)) { scene.classList.remove('catalog10-scene'); scene.style.removeProperty('--catalog09-environment'); }
    };
    background.src = url;
  }

  const identity = spec => spec.selectionKey || [key(spec.brand,spec.model),normalize(spec.modification),spec.original||''].join('\n');

  function idle(record) {
    record.state = 'idle';
    record.wrapper.dataset.catalog11State = 'idle';
    const pending = record.pending;
    record.pending = null;
    if (!record.wrapper.isConnected || bindings.get(record.wrapper) !== record) return;
    if (pending && identity(pending) !== identity(record.spec)) transition(record.wrapper,pending);
    else if (record.refreshAfterMotion) {
      record.refreshAfterMotion = false;
      paint(record,false);
    }
  }

  function paint(record, animate = false) {
    const {wrapper,spec} = record;
    // Never briefly display a server logo while the packaged mark is being resolved.
    if (spec.kind === 'brand' && !manifestSettled) {
      wrapper.classList.add('catalog11-brand-pending');
      return;
    }
    wrapper.classList.remove('catalog11-brand-pending');
    const entry = entryFor(spec), generated = entryUrl(entry), original = originalUrl(spec.original);
    const environment = spec.kind === 'model' && entry?.transparent === true ? entryUrl(manifest.environment,true) : '';
    const signature = generated+'\n'+original+'\n'+environment;
    if (!animate && record.signature === signature) return;
    record.signature = signature;
    const revision = ++record.revision;
    const previous = animate ? wrapper.querySelector('img') : null;
    const preserve = !!(previous?.complete && previous.naturalWidth > 0 && wrapper.classList.contains('catalog09-ready'));
    record.state = 'loading'; record.animateRequest = animate;
    if (spec.kind === 'model') wrapper.dataset.catalog11State = 'loading';
    if (!preserve) resetFraming(wrapper);
    wrapper.classList.toggle('catalog09-model', spec.kind === 'model');
    const sources = [...new Set([generated,original].filter(Boolean))];
    let index = 0;
    if (!sources.length) {
      resetFraming(wrapper); wrapper.replaceChildren(record.empty()); record.entry = null; idle(record); return;
    }
    const img = document.createElement('img');
    img.alt = spec.alt || ''; img.draggable = false; img.decoding = 'async';
    if (generated && entry?.transparent === true && spec.kind === 'model') img.classList.add('catalog09-car');
    if (!preserve) wrapper.replaceChildren(img);
    const current = () => bindings.get(wrapper) === record && record.revision === revision;

    function present() {
      resetFraming(wrapper);
      if (sources[index] === generated) {
        wrapper.classList.add('catalog09-generated');
        img.dataset.catalog09Source = 'generated';
        frameBrand(record,entry);
        if (spec.kind === 'brand' && (entry.src.endsWith('.svg') || entry.officialContour)) {
          wrapper.classList.add('catalog10-vector');
          wrapper.style.setProperty('--catalog10-mark', `url("${generated}")`);
        }
        frameCar(record,entry);
        record.entry = entry;
        applyEnvironment(record,environment,() => current() && record.entry === entry);
      } else {
        img.dataset.catalog09Source = 'original'; img.classList.remove('catalog09-car'); record.entry = null;
      }
      wrapper.classList.add('catalog09-ready');
    }

    img.onload = async () => {
      if (!current()) return;
      try { await img.decode(); } catch {} // A loaded fallback still remains usable.
      if (!current()) return;
      const sliding = preserve && wrapper.isConnected && !reducedMotion.matches && typeof img.animate === 'function';
      if (!sliding) {
        wrapper.replaceChildren(img);
        if (animate) img.style.setProperty('animation','none','important');
        present(); idle(record); return;
      }

      // Freeze the displayed pixels before changing the shared framing variables.
      const stage = wrapper.getBoundingClientRect(), old = previous.getBoundingClientRect();
      for (const [name,value] of [['left',old.left-stage.left],['top',old.top-stage.top],['width',old.width],['height',old.height]]) previous.style.setProperty(name,value+'px','important');
      previous.style.setProperty('animation','none','important');
      previous.style.setProperty('transform','none');
      previous.dataset.catalog11Role = 'outgoing'; previous.setAttribute('aria-hidden','true');
      img.dataset.catalog11Role = 'incoming'; img.style.setProperty('animation','none','important');
      wrapper.classList.add('catalog11-sliding');
      wrapper.replaceChildren(previous,img);
      present();
      record.state = 'sliding'; wrapper.dataset.catalog11State = 'sliding';
      const distance = Math.ceil(stage.width + 48);
      const outgoing = previous.animate([
        {transform:'translateX(0)'},{transform:`translateX(-${distance}px)`}
      ],{duration:320,easing:'cubic-bezier(.42,0,.7,.35)',fill:'forwards'});
      const incoming = img.animate([
        {transform:`translateX(${distance}px)`},{transform:'translateX(0)'}
      ],{duration:380,delay:240,easing:'cubic-bezier(.18,.65,.22,1)',fill:'both'});
      record.animations = [outgoing,incoming];
      Promise.allSettled(record.animations.map(a=>a.finished)).then(() => {
        if (!current()) return;
        previous.remove(); delete img.dataset.catalog11Role;
        record.animations.forEach(a=>a.cancel()); record.animations = [];
        wrapper.classList.remove('catalog11-sliding');
        idle(record);
      });
    };
    img.onerror = () => {
      if (!current()) return;
      img.classList.remove('catalog09-car');
      index += 1;
      if (index < sources.length) img.src = sources[index];
      else {
        resetFraming(wrapper); record.entry = null;
        wrapper.replaceChildren(record.empty()); idle(record);
      }
    };
    img.src = sources[index];
  }

  function transition(wrapper,spec) {
    const record = bindings.get(wrapper);
    if (!record || record.spec.kind !== 'model') return;
    const next = {...record.spec,...spec};
    if (record.state === 'sliding') {
      // Complete the physical movement, keeping only the latest requested destination.
      record.pending = identity(next) === identity(record.spec) ? null : next;
      return;
    }
    if (identity(next) === identity(record.spec)) return;
    record.spec = next;
    record.pending = null;
    paint(record,true);
  }

  const resize = window.ResizeObserver ? new ResizeObserver(entries => {
    for (const {target} of entries) { const r=bindings.get(target); if (r?.entry && target.classList.contains('catalog09-generated')) { frameBrand(r,r.entry); frameCar(r,r.entry); } }
  }) : null;

  function bind(wrapper, spec, empty) {
    const old = bindings.get(wrapper);
    if (old) { old.revision++; old.pending = null; old.animations?.forEach(a=>a.cancel()); records.delete(old); }
    wrapper.classList.remove('catalog11-sliding');
    delete wrapper.dataset.catalog11State;
    const record = { wrapper,spec,empty,revision:0,signature:null,entry:null,state:'idle',pending:null,animations:[] };
    records.add(record); bindings.set(wrapper,record);
    resize?.observe(wrapper);
    paint(record);
    if (!cleanupQueued) {
      cleanupQueued = true;
      queueMicrotask(() => {
        cleanupQueued = false;
        for (const r of records) if (!r.wrapper.isConnected) { records.delete(r); resize?.unobserve(r.wrapper); }
      });
    }
    return wrapper;
  }

  const ready = fetch(new URL('catalog09-manifest.json',assetRoot), {cache:'no-cache'})
    .then(response => response.ok ? response.json() : null)
    .then(data => {
      manifestSettled = true;
      if ([1,2].includes(data?.version) && data.brands && data.models) manifest = data;
      for (const r of records) { if (r.wrapper.isConnected) { if (r.state === 'sliding') r.refreshAfterMotion = true; else paint(r,r.animateRequest); } else { records.delete(r); resize?.unobserve(r.wrapper); } }
      return manifest;
    }).catch(() => { manifestSettled = true; for (const r of records) if (r.wrapper.isConnected) paint(r); return manifest; }); // Missing bundles retain a neutral original fallback.

  window.Catalog09 = Object.freeze({ normalize,key,bind,ready,originalUrl,transition });
})();
