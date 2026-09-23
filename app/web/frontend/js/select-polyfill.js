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
// .adb-console-row/.usb06-drive-strip).
//
// Список вариантов — popover="manual" (WebKit 17+): открытый, он лежит в
// top layer над любым <dialog>, и его не режут ни overflow диалога, ни
// прокручиваемые панели — как родной ::picker(select) на Windows. Раньше
// он был position:absolute внутри wrap, и в любом всплывающем окне
// (overflow:auto у dialog) показывалась только часть списка до нижней
// кромки окна — «Выберите приложение» у кнопок «Выдать разрешения»/
// «Запустить приложение»: полторы строки из сотни (жалоба владельца,
// 2026-09-23). position:fixed без top layer тут не помогал: backdrop-filter
// у dialog делает его containing block и для fixed-потомков, а узел,
// вынесенный в body, модальный диалог перекрывает и делает inert.
// Координаты считаются от триггера при открытии; прокрутка/ресайз
// закрывают список, как родной. Без Popover API (WebKit 16 и старше) —
// прежнее поведение (position:absolute, .csel-above).
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

  const topLayer = typeof HTMLElement !== 'undefined' && 'popover' in HTMLElement.prototype;
  const GAP = 4;
  const EDGE = 8;

  function hidePopup(popup) {
    popup.hidden = true;
    if (topLayer && popup.matches(':popover-open')) popup.hidePopover();
    popup.previousElementSibling?.classList.remove('csel-open');
  }

  function closeAllExcept(exceptPopup) {
    let closed = false;
    document.querySelectorAll('.csel-popup:not([hidden])').forEach((popup) => {
      if (popup === exceptPopup) return;
      hidePopup(popup);
      closed = true;
    });
    return closed;
  }
  document.addEventListener('mousedown', (e) => {
    if (e.target.closest('.csel-trigger, .csel-popup')) return;
    closeAllExcept(null);
  });
  document.addEventListener('keydown', (e) => {
    // Esc закрывает только открытый список, а не заодно и окно под ним.
    if (e.key === 'Escape' && closeAllExcept(null)) e.preventDefault();
  });
  if (topLayer) {
    // Список в top layer не едет вместе с триггером — прокрутка окна/панели,
    // в которой лежит ЕГО триггер, и ресайз закрывают его. Чужая прокрутка
    // (самого списка, автопрокрутка лога в окне этапа под ним) — нет. Закрытие
    // диалога — тоже закрывает: иначе список остался бы висеть поверх пустого места.
    document.addEventListener('scroll', (e) => {
      document.querySelectorAll('.csel-popup:not([hidden])').forEach((popup) => {
        const trigger = popup.previousElementSibling;
        if (e.target === document || (e.target instanceof Node && trigger && e.target.contains(trigger))) hidePopup(popup);
      });
    }, true);
    window.addEventListener('resize', () => closeAllExcept(null));
    document.addEventListener('close', () => closeAllExcept(null), true);
  }

  // Под триггером, если хватает места; иначе туда, где его больше, и не выше
  // этого места — чтобы список целиком оставался в окне.
  function placeTopLayer(trigger, popup) {
    const rect = trigger.getBoundingClientRect();
    popup.style.left = `${Math.max(EDGE, rect.left)}px`;
    popup.style.width = `${Math.min(rect.width, window.innerWidth - 2 * EDGE)}px`;
    popup.style.top = `${rect.bottom + GAP}px`;
    popup.style.bottom = '';
    popup.style.maxHeight = '';
    const height = popup.getBoundingClientRect().height;
    const roomBelow = window.innerHeight - rect.bottom - GAP - EDGE;
    const roomAbove = rect.top - GAP - EDGE;
    if (height <= roomBelow) return;
    if (roomAbove > roomBelow) {
      popup.style.top = '';
      popup.style.bottom = `${window.innerHeight - rect.top + GAP}px`;
      if (height > roomAbove) popup.style.maxHeight = `${roomAbove}px`;
    } else {
      popup.style.maxHeight = `${roomBelow}px`;
    }
  }

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
        hidePopup(popup);
      });
      popup.appendChild(b);
    }
  }

  function sync(select, wrap, trigger, popup) {
    const hidden = select.hidden || getComputedStyle(select).display === 'none';
    wrap.style.display = hidden ? 'none' : '';
    if (hidden && !popup.hidden) hidePopup(popup);
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
    popup.className = topLayer ? 'csel-popup csel-top-layer' : 'csel-popup';
    if (topLayer) popup.popover = 'manual';
    popup.hidden = true;

    wrap.append(trigger, popup);
    select.after(wrap);

    trigger.addEventListener('click', () => {
      if (trigger.disabled) return;
      const willOpen = popup.hidden;
      closeAllExcept(willOpen ? popup : null);
      if (!willOpen) {
        hidePopup(popup);
        return;
      }
      popup.hidden = false;
      trigger.classList.add('csel-open');
      renderOptions(select, popup);
      if (topLayer) {
        popup.showPopover();
        placeTopLayer(trigger, popup);
        // Выбранный вариант — в видимой части длинного списка. Не scrollIntoView:
        // тот может прокрутить и окно под списком, а это его закрывает.
        const selected = popup.querySelector('.csel-selected');
        if (selected) popup.scrollTop = selected.offsetTop - (popup.clientHeight - selected.offsetHeight) / 2;
      } else {
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
