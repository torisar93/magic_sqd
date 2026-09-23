/* Этап «Приложения»: вкладки вместо длинного списка. ОДИН И ТОТ ЖЕ файл на ПК
   (app/web/frontend/js/apps_tabs.js) и на Android (android/app/src/main/assets/js/
   apps_tabs.js) — копии совпадают побайтово (tests/test_shared_frontend_copies.py).
   Строки приложений строит сама платформа (у каждой своя разметка строки, свои
   галочки и обработчики) и отдаёт сюда группами. Здесь — только раскладка по
   вкладкам, поиск сразу по всем вкладкам, счётчики выбранного на вкладках и строка
   «Выбрано» для нижней панели с кнопкой «Начать установку». Владелец, 2026-09-23:
   «кнопку всегда видно, а вместо длинного списка — вкладки». */
(() => {
  const lower = (text) => String(text || '').toLocaleLowerCase('ru');
  const make = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  };

  /* container — куда разложить (содержимое заменяется); строки остаются внутри него,
     платформа ищет отмеченные именно там. groups — [{key, label, rows: [Element]}],
     пустые не показываются. options: active — какую вкладку открыть; onActiveChange(key);
     search — поле поиска (очищается при переходе на вкладку); listTag/listClass — тег и
     доп. класс списка строк ('ul' и 'stage-apps-list' для строк-<li> на Android — их
     прежние стили). Возвращает {apply(query), refresh(), select(key)}. */
  function mount(container, groups, options = {}) {
    const usable = groups.filter((group) => group.rows.length);
    const bar = make('div', 'apps-tabs');
    bar.setAttribute('role', 'tablist');
    const found = make('p', 'apps-tabs-found');
    found.hidden = true;
    const panes = make('div', 'apps-tab-panes');
    const parts = new Map();
    for (const group of usable) {
      const tab = make('button', 'apps-tab');
      tab.type = 'button';
      tab.setAttribute('role', 'tab');
      const picked = make('span', 'apps-tab-picked');
      tab.append(make('span', 'apps-tab-label', group.label), make('span', 'apps-tab-total', String(group.rows.length)), picked);
      tab.addEventListener('click', () => select(group.key));
      const pane = make('section', 'apps-tab-pane');
      pane.setAttribute('role', 'tabpanel');
      const list = make(options.listTag || 'div', options.listClass ? `apps-tab-list ${options.listClass}` : 'apps-tab-list');
      group.rows.forEach((row) => list.append(row));
      pane.append(make('h4', 'apps-tab-pane-title', group.label), list);
      bar.append(tab);
      panes.append(pane);
      parts.set(group.key, { group, tab, pane, picked });
    }
    container.replaceChildren(bar, found, panes);
    let active = parts.has(options.active) ? options.active : usable[0]?.key;
    let query = '';

    function refresh() {
      for (const { group, picked } of parts.values()) {
        const count = group.rows.filter((row) => row.querySelector('input[type=checkbox]')?.checked).length;
        picked.textContent = count ? `✓ ${count}` : '';
        picked.hidden = !count;
      }
    }

    // Поиск — сразу по всем вкладкам: совпадения показываются списками под названиями вкладок.
    function apply(nextQuery = query) {
      query = lower(String(nextQuery).trim());
      container.classList.toggle('apps-tabs-searching', !!query);
      let matches = 0;
      for (const [key, { group, tab, pane }] of parts) {
        let visible = 0;
        for (const row of group.rows) {
          const hit = !query || lower(row.textContent).includes(query);
          row.hidden = !hit;
          if (hit) visible += 1;
        }
        matches += visible;
        pane.hidden = query ? !visible : key !== active;
        const on = !query && key === active;
        tab.classList.toggle('on', on);
        tab.setAttribute('aria-selected', String(on));
      }
      found.hidden = !query;
      found.textContent = matches ? `Найдено во всех вкладках: ${matches}` : 'Ничего не найдено. Попробуйте другое название.';
    }

    function select(key) {
      if (!parts.has(key)) return;
      active = key;
      if (options.search) options.search.value = '';
      apply('');
      options.onActiveChange?.(key);
      // На телефоне вкладки — лента с прокруткой вбок: открытая не должна оставаться за краем.
      requestAnimationFrame(() => {
        const { tab } = parts.get(key);
        if (bar.scrollWidth > bar.clientWidth) bar.scrollLeft = Math.max(0, tab.offsetLeft - bar.offsetLeft - 16);
      });
    }

    refresh();
    if (active) select(active);
    else apply('');
    return { apply, refresh, select, get active() { return active; } };
  }

  /* Строка «Выбрано» нижней панели. mode 'names' — названия через запятую (ПК: места
     хватает), 'icons' — значки (Android: в узкую строку названия не влезают, владелец
     2026-09-23). entries — [{path, name}]. */
  function summary(target, entries, mode = 'names') {
    target.replaceChildren();
    target.classList.toggle('is-empty', !entries.length);
    if (!entries.length) {
      target.textContent = 'Отметьте приложения для установки';
      return;
    }
    target.append(make('strong', 'apps-dock-count', `Выбрано: ${entries.length}`));
    if (mode === 'icons') {
      const icons = make('span', 'apps-dock-icons');
      const limit = 7;
      for (const entry of entries.slice(0, limit)) {
        const icon = window.LabUI?.appIcon ? window.LabUI.appIcon(entry.path) : make('span', 'apk-icon');
        icon.title = entry.name || '';
        icons.append(icon);
      }
      if (entries.length > limit) icons.append(make('span', 'apps-dock-more', `+${entries.length - limit}`));
      target.append(icons);
    } else {
      target.append(make('span', 'apps-dock-names', entries.map((entry) => entry.name).join(', ')));
    }
  }

  window.AppTabs = { mount, summary };
})();
