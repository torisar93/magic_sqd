/* Presentation of the original instruction document; author content and scripts stay intact. */
(() => {
  const readerStyle = `
    :root{color-scheme:dark;--read-text:#eaf2f3;--read-dim:#adc2ca;--read-mint:#b4f0dd}
    html{min-height:100%;background:radial-gradient(ellipse 100% 450px at 5% 0,#30464e80,transparent),#162128!important;color:var(--read-text)!important;scrollbar-color:#58727c #162128;scrollbar-width:thin}
    html,body{box-sizing:border-box!important;font-family:'Segoe UI',Roboto,Arial,sans-serif!important;font-size:16px!important;line-height:1.65!important}
    body{width:100%!important;max-width:960px!important;min-width:0!important;margin:0 auto!important;padding:32px 36px 40px!important;background:transparent!important;color:var(--read-text)!important;border:0!important;box-shadow:none!important;overflow-wrap:anywhere}
    *,*::before,*::after{box-sizing:border-box}
    h1{font-size:clamp(25px,3.2vw,32px)!important;line-height:1.22!important;font-weight:600!important;letter-spacing:-.6px!important;color:var(--read-text)!important;border:0!important;padding:0!important;margin:0 0 28px!important;text-wrap:pretty}
    h2{font-size:21px!important;line-height:1.35!important;font-weight:600!important;letter-spacing:-.25px;color:var(--read-text)!important;border:0!important;padding:0!important;margin:30px 0 14px!important}
    h3{font-size:18px;line-height:1.4;color:var(--read-text);margin:24px 0 12px}
    p{margin:12px 0!important;line-height:1.65!important}
    p,li{max-width:100%;overflow-wrap:anywhere}
    a{color:var(--read-mint)!important;text-underline-offset:4px;text-decoration-thickness:1px}
    a:hover{color:#e4fff7!important}
    a:focus-visible,button:focus-visible,input:focus-visible,select:focus-visible,summary:focus-visible{outline:2px solid var(--read-mint);outline-offset:3px}
    ol{list-style:none!important;padding:0!important;margin:20px 0!important;border:1px solid #cee9e62c!important;border-radius:18px!important;overflow:hidden;background:radial-gradient(ellipse at 0 0,#b6e6dd14,transparent 75%),linear-gradient(135deg,#30454cbd,#1b2b33ba)!important;box-shadow:inset 0 1px 0 #e9fff414,0 12px 30px #06131b24!important;counter-reset:revert!important}
    ol>li{display:list-item!important;position:relative!important;list-style:none!important;counter-increment:list-item!important;padding:18px 20px 18px 66px!important;margin:0!important;min-height:68px;border:0!important;border-bottom:1px solid #cce5e212!important;background:transparent!important;line-height:1.6!important;color:var(--read-text)!important}
    ol>li:last-child{border-bottom:0!important}
    ol>li::before{content:counter(list-item,decimal-leading-zero)!important;position:absolute!important;left:18px!important;top:18px!important;width:30px!important;height:30px!important;display:grid!important;place-items:center!important;margin:0!important;padding:0!important;border:1px solid #c4f7e84a!important;border-radius:10px!important;background:linear-gradient(135deg,#b5e9dc24,#7bbdad0f)!important;color:var(--read-mint)!important;font:600 12px/1 'Segoe UI',Roboto,Arial,sans-serif!important;letter-spacing:.1px!important;box-shadow:inset 0 1px 0 #edfff41c}
    ol>li p:first-child{margin-top:0!important}ol>li p:last-child{margin-bottom:0!important}
    ol ol{margin:14px 0 0!important;background:#10212950!important;box-shadow:none!important}
    ul{padding-left:22px!important;margin:14px 0!important;list-style:disc!important}
    ul>li{padding:0 0 0 3px!important;margin:8px 0!important;line-height:1.6!important;counter-increment:none!important}
    ul>li::before{content:none!important}ul>li::marker{color:var(--read-mint)}
    .warn,.danger,.callout{display:block!important;position:relative;padding:16px 18px!important;border:1px solid #d3b87838!important;border-radius:14px!important;margin:20px 0!important;background:linear-gradient(130deg,#66543135,#3b372424)!important;color:#ebd9b8!important;line-height:1.6!important;box-shadow:inset 0 1px 0 #f6e5b70c}
    .danger,.callout.danger{border-color:#e3a09a3d!important;background:linear-gradient(130deg,#683b3b40,#412c3026)!important;color:#ffd0c7!important}
    .warn::before,.danger::before{content:none!important}
    .path,code{display:inline;box-decoration-break:clone;background:#07182055!important;border:1px solid #b7e5d822;border-radius:6px!important;padding:2px 6px!important;color:#beeadd!important;font-size:.93em}
    pre{max-width:100%;padding:16px;background:#0b1921a1;border:1px solid #c9eae41c;border-radius:12px;line-height:1.6}
    pre,code{white-space:pre-wrap!important;overflow-wrap:anywhere!important}
    pre code{padding:0!important;border:0;background:none!important}
    img,video{max-width:100%!important;height:auto!important}
    img.screenshot,body>img,body>figure>img{display:block!important;width:auto!important;max-width:100%!important;max-height:none!important;height:auto!important;margin:22px auto!important;border:0!important;border-radius:14px!important;background:transparent!important;box-shadow:0 8px 24px #08131940!important;cursor:zoom-in;object-fit:contain!important}
    img.screenshot:hover{transform:none!important;box-shadow:0 10px 28px #08131955!important}
    figure{max-width:100%;margin:22px 0}figure img{margin-bottom:8px!important}
    .caption,figcaption{font-size:13px!important;line-height:1.5!important;color:var(--read-dim)!important;margin:8px 0 20px!important;text-align:center}
    video{display:block;margin:22px auto;border-radius:14px;background:#0b151a}
    table{display:block;max-width:100%!important;overflow-x:auto;border-collapse:collapse;font-size:14px}
    th,td{border-color:#bfd9d32b!important;padding:10px 12px}th{background:#b5e6d910;color:var(--read-mint)}
    hr{border:0;border-top:1px solid #cce7df24;margin:24px 0}
    button,input,select,textarea{font:inherit;max-width:100%;border-radius:10px;color:var(--read-text);background:#1a2d35;border:1px solid #d0eade33;padding:10px 14px;min-height:44px;box-sizing:border-box}
    button{cursor:pointer;transition:background .2s,border-color .2s}button:hover{background:#29463f;border-color:#b4e9d780}
    details{padding:14px 16px;border:1px solid #cce7df24;border-radius:12px;margin:16px 0;background:#15262c80}summary{cursor:pointer;color:var(--read-mint)}
    ::-webkit-scrollbar{width:8px;height:8px}::-webkit-scrollbar-track,::-webkit-scrollbar-corner{background:#162128}::-webkit-scrollbar-thumb{background:#58727c;border:2px solid #162128;border-radius:8px}
    #magicsqd-lightbox{padding:22px!important}
    #magicsqd-lightbox img{width:auto!important;height:auto!important;max-width:100%!important;max-height:100%!important;margin:0!important;box-shadow:0 12px 40px #0008!important}
    @media(max-width:600px){body{padding:22px 16px 28px!important;font-size:16px!important}h1{font-size:25px!important;margin-bottom:22px!important}h2{font-size:20px!important;margin-top:26px!important}ol{border-radius:16px!important;margin:18px 0!important}ol>li{padding:15px 14px 15px 55px!important;min-height:60px}ol>li::before{left:14px!important;top:15px!important;width:28px!important;height:28px!important;border-radius:9px!important;font-size:11px!important}.warn,.danger,.callout{padding:14px 15px!important}img.screenshot,body>img,body>figure>img{border-radius:12px!important}#magicsqd-lightbox{padding:12px!important}}
    @media(prefers-reduced-motion:no-preference){body>h1,body>h2,body>ol,body>p,body>img,body>figure{animation:reader12-enter .45s cubic-bezier(.2,.7,.2,1) both}body>ol{animation-delay:45ms}body>img,body>figure{animation-delay:90ms}@keyframes reader12-enter{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}}
    @media(prefers-reduced-motion:reduce){*,*::before,*::after{scroll-behavior:auto!important;animation:none!important;transition:none!important}}
  `;
  function reader(html, options = {}) {
    const doc = new DOMParser().parseFromString(String(html || ''), 'text/html');
    if (!doc.querySelector('meta[name="viewport"]')) {
      const viewport = doc.createElement('meta');
      viewport.name = 'viewport'; viewport.content = 'width=device-width, initial-scale=1';
      doc.head.append(viewport);
    }
    doc.getElementById('instruction12-style')?.remove();
    const style = doc.createElement('style');
    style.id = 'instruction12-style'; style.textContent = readerStyle;
    if (document.documentElement.classList.contains('reduce-motion')) style.textContent += '\n*,*::before,*::after{animation:none!important;transition:none!important}';
    doc.head.append(style);
    if (options.title && !doc.body.querySelector('h1')) {
      const title = doc.createElement('h1');title.textContent = options.title;doc.body.prepend(title);
    }
    if (options.description) {
      const description = doc.createElement('p');
      description.textContent = options.description;description.style.whiteSpace = 'pre-wrap';
      const heading = doc.body.querySelector('h1');
      if (heading) heading.after(description);else doc.body.prepend(description);
    }
    return '<!DOCTYPE html>\n' + doc.documentElement.outerHTML;
  }
  function textDocument(text) {
    const doc = document.implementation.createHTMLDocument('');
    String(text || '').split(/\n\s*\n/).forEach(part => {
      const p = doc.createElement('p');p.textContent = part;p.style.whiteSpace = 'pre-wrap';doc.body.append(p);
    });
    return doc.documentElement.outerHTML;
  }
  window.Instructions12 = { reader, textDocument };
  // Existing help dialogs use the same document skin, including source links and lightbox scripts.
  window.LabUI.reader = reader;
})();
