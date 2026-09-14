/* USB stage presentation; device operations stay in the native stage controllers. */
(function () {
  const n = (tag, cls, text) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  };
  const paths = {
    file: '<path d="M14 3H6v18h12V7zM14 3v5h4M9 12h6M9 16h4"/>',
    car: '<path d="m5 9 2-5h10l2 5M4 9h16l1 4v6h-3v-2H6v2H3v-6zM7 12h2m6 0h2M8 15h8"/>',
    key: '<path d="M14 3a7 7 0 0 0-6.4 9.8L2 18.4V22h4v-3h3v-3l2.2-2.2A7 7 0 1 0 14 3Z"/><circle cx="16" cy="7" r="1"/>',
    download: '<path d="M12 3v12m-4-4 4 4 4-4M4 15v6h16v-6"/>',
  };
  function icon(name) {
    if (window.AppIcons) return AppIcons.icon(name);
    if (!paths[name]) return LabUI.symbol(name);
    const span = n('span', 'ui-icon');
    span.setAttribute('aria-hidden', 'true');
    span.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">' + paths[name] + '</svg>';
    return span;
  }
  function button(id, label, symbol, accent = false) {
    const b = n('button', accent ? 'usb06-action accent' : 'usb06-action');
    b.id = id;
    b.type = 'button';
    b.append(icon(symbol), n('span', '', label));
    return b;
  }
  function heading(panel, description) {
    panel.classList.add('usb06-flow');
    panel.querySelector(':scope > .action-chip')?.remove();
    const head = n('div', 'usb06-intro');
    head.append(n('p', '', description));
    const art = n('img', 'usb06-art');
    art.src = 'img/usb-drive-graphite.png'; art.alt = '';
    head.append(art); panel.append(head);
  }
  function step(number, symbol, title, description, action) {
    const card = n('section', 'usb06-step');
    card.dataset.step = number;
    const num = n('span', 'usb06-number', number);
    num.setAttribute('aria-hidden', 'true');
    const pictogram = n('div', 'usb06-pictogram'); pictogram.append(icon(symbol));
    const body = n('div', 'usb06-step-copy');
    body.append(n('h2', '', title), n('p', '', description));
    card.append(num, pictogram, body);
    if (action) card.append(action);
    return card;
  }
  function instruction(title, content) {
    const dialog = n('dialog', 'usb06-instruction-dialog');
    const head = n('header', 'usb06-instruction-heading');
    const close = button('', 'Закрыть', 'close');
    close.className = 'icon-button'; close.title = 'Закрыть';
    close.setAttribute('aria-label', 'Закрыть'); close.lastChild.remove();
    head.append(n('h2', '', title), close);
    dialog.append(head, content);
    document.body.append(dialog);
    close.onclick = () => dialog.close();
    dialog.addEventListener('click', e => { if (e.target === dialog) { const r=dialog.getBoundingClientRect(); if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom) dialog.close(); }});
    dialog.addEventListener('close', () => dialog.remove(), { once: true });
    dialog.showModal();
  }
  window.UsbUI = { n, icon, button, heading, step, instruction };
})();
