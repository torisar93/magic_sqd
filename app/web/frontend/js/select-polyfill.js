// Полифилл кастомизированных <select> для WebKit (macOS) — только там, где
// браузер ещё не умеет CSS Customizable Select (appearance:base-select +
// ::picker(select)). js/select08.js уже определяет поддержку
// (window.Select08.mode) и сознательно НЕ строил JS-прокси поверх select —
// принимал "нативный тёмный fallback" как приемлемый для не-Chromium
// движков (см. его докстринг). На практике этот fallback на macOS
// (Safari/WebKit, pywebview) — обычное системное меню ОС с синим
// выделением, визуально ломающее тёмную тему приложения, поэтому здесь всё
// же добавляем прокси, но ТОЛЬКО когда window.Select08 говорит, что
// нативной стилизации нет. DOM/API самого <select> (value, options,
// событие change) не трогаем вообще — поверх него рисуется кнопка +
// список вариантов, полностью управляемые через существующий select, так
// что ни одно из мест, где он создаётся/заполняется (app.js, stage_wizard.
// js, car_step_fields.js и т.д.) менять не пришлось.
//
// Триггер и список вариантов лежат внутри одного <span class="csel-wrap">
// (position:relative) — сам wrap встаёт в DOM ровно туда, где раньше был
// select, и остаётся ЕДИНСТВЕННЫМ flex/grid-элементом в исходной строке
// (два отдельных соседних узла сломали бы соседние колонки/кнопки в
// .adb-console-row/.usb06-drive-strip). Список вариантов — position:
// absolute относительно ЭТОГО wrap (top:100%), никакого position:fixed и
// вычисления координат от viewport: то же самое внутри открытого <dialog>
// ломалось в WebKit — top layer диалога не даёт зафиксированному потомку
// показаться поверх, независимо от z-index.
(() => {
  let nativeSupported;
  if (window.Select08) {
    nativeSupported = window.Select08.mode !== 'native-dark';
  } else {
    try {
      nativeSupported = CSS.supports('(appearance: base-select) and selector(::picker(select))');
    } catch (_) {
      nativeSupported = false;
    }
  }
  if (nativeSupported) return;

  function closeAllExcept(exceptPopup) {
    document.querySelectorAll('.csel-popup:not([hidden])').forEach((popup) => {
      if (popup === exceptPopup) return;
      popup.hidden = true;
      popup.previousElementSibling?.classList.remove('csel-open');
    });
  }
  document.addEventListener('mousedown', (e) => {
    if (e.target.closest('.csel-trigger, .csel-popup')) return;
    closeAllExcept(null);
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeAllExcept(null); });

  function currentLabel(select) {
    const opt = select.selectedOptions && select.selectedOptions[0];
    return opt ? opt.textContent : '';
  }

  function renderOptions(select, popup) {
    popup.replaceChildren();
    const options = Array.from(select.querySelectorAll('option'));
    if (!options.length) {
      const empty = document.createElement('div');
      empty.className = 'csel-empty';
      empty.textContent = 'Нет вариантов';
      popup.appendChild(empty);
      return;
    }
    for (const opt of options) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'csel-option' + (opt.selected ? ' csel-selected' : '');
      b.textContent = opt.textContent;
      b.disabled = opt.disabled;
      b.addEventListener('click', () => {
        select.value = opt.value;
        // MutationObserver ниже следит за childList/атрибутами select, но
        // .value= — это JS-свойство, а не атрибут DOM: сама смена
        // "выбранности" опции им не ловится (реальный найденный баг:
        // подпись на кнопке оставалась старой после выбора) — обновляем
        // подпись здесь явно, а не полагаемся только на sync().
        const trigger = popup.previousElementSibling;
        if (trigger) trigger.querySelector('.csel-label').textContent = currentLabel(select);
        select.dispatchEvent(new Event('input', { bubbles: true }));
        select.dispatchEvent(new Event('change', { bubbles: true }));
        popup.hidden = true;
        trigger?.classList.remove('csel-open');
      });
      popup.appendChild(b);
    }
  }

  function sync(select, wrap, trigger, popup) {
    const hidden = select.hidden || getComputedStyle(select).display === 'none';
    wrap.style.display = hidden ? 'none' : '';
    if (hidden) popup.hidden = true;
    trigger.disabled = select.disabled;
    trigger.querySelector('.csel-label').textContent = currentLabel(select);
    if (!popup.hidden) renderOptions(select, popup);
  }

  function enhance(select) {
    if (select.dataset.cselDone) return;
    select.dataset.cselDone = '1';
    const originalClassName = select.className; // до добавления csel-native-hidden ниже
    select.classList.add('csel-native-hidden');

    const wrap = document.createElement('span');
    wrap.className = `csel-wrap${originalClassName ? ' ' + originalClassName : ''}`;
    if (select.id) wrap.dataset.for = select.id;
    if (select.getAttribute('style')) wrap.setAttribute('style', select.getAttribute('style'));

    const trigger = document.createElement('button');
    trigger.type = 'button';
    trigger.className = 'csel-trigger';
    const label = document.createElement('span');
    label.className = 'csel-label';
    trigger.appendChild(label);
    const chevron = document.createElement('span');
    chevron.className = 'csel-chevron';
    chevron.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true">'
      + '<path d="M6 9l6 6 6-6" fill="none" stroke="currentColor" stroke-width="2" '
      + 'stroke-linecap="round" stroke-linejoin="round"/></svg>';
    trigger.appendChild(chevron);

    const popup = document.createElement('div');
    popup.className = 'csel-popup';
    popup.hidden = true;

    wrap.append(trigger, popup);
    select.after(wrap);

    trigger.addEventListener('click', () => {
      if (trigger.disabled) return;
      const willOpen = popup.hidden;
      closeAllExcept(willOpen ? popup : null);
      popup.hidden = !willOpen;
      trigger.classList.toggle('csel-open', willOpen);
      if (willOpen) {
        renderOptions(select, popup);
        // Триггер у нижней панели (например выбор ADB-устройства в логе)
        // может стоять у самого низа окна — попап, всегда открывающийся
        // вниз (top:100%), там обрезается краем окна (реальный найденный
        // баг). Меряем ПОСЛЕ renderOptions (нужна реальная высота с уже
        // вставленными опциями) и открываем вверх, если снизу не хватает
        // места, но хватает сверху.
        const triggerRect = trigger.getBoundingClientRect();
        const popupHeight = popup.getBoundingClientRect().height;
        const roomBelow = window.innerHeight - triggerRect.bottom;
        const roomAbove = triggerRect.top;
        popup.classList.toggle('csel-above', popupHeight > roomBelow && roomAbove > roomBelow);
      }
    });

    new MutationObserver(() => sync(select, wrap, trigger, popup)).observe(select, {
      childList: true, subtree: true, attributes: true,
      attributeFilter: ['style', 'class', 'hidden', 'disabled'],
    });
    sync(select, wrap, trigger, popup);
  }

  function scan(root) {
    if (root.nodeType !== 1) return;
    if (root.tagName === 'SELECT') enhance(root);
    root.querySelectorAll?.('select').forEach(enhance);
  }

  scan(document.body);
  new MutationObserver((muts) => {
    for (const m of muts) for (const node of m.addedNodes) scan(node);
  }).observe(document.body, { childList: true, subtree: true });
})();
