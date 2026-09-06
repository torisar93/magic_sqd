// Каталог автомобилей: марка -> модель -> (при необходимости) модификация.
// Данные и переход к текущему мастеру остаются прежними; меняется только
// представление — вместо узкого списка используются крупные карточки.
(function () {
  const STATUS_COLOR_TITLES = {
    green: "Актуально",
    yellow: "Требует проверки",
    blue: "Недавно обновлено",
    red: "Способ не работает",
  };

  let data = null;
  let step = "brand";
  let selectedBrand = null;
  let selectedGroup = null;
  let onModelSelected = null;
  let gridEl, crumbEl, searchEl, emptyEl, settingsEl, backEl;
  let adminToggleEl, adminPopoverEl, adminActionsEl, adminPendingEl;
  let adminModeReady = false;
  let startupOverlayEl, startupProgressFillEl, startupProgressLabelEl;

  async function init(container, callbacks) {
    onModelSelected = callbacks.onModelSelected;
    container.innerHTML = `
      <section class="catalog" aria-label="Выбор автомобиля">
        <header class="catalog-topbar">
          <button class="catalog-global-back" id="catalog-back" type="button" hidden>Назад</button>
          <img class="catalog-full-logo" src="img/logo-full-dark.svg" alt="Magic SQD" />
          <nav class="catalog-topbar-actions" aria-label="Ссылки проекта">
            <label class="catalog-search" aria-label="Поиск в каталоге">
              <span class="catalog-search-icon" aria-hidden="true"></span>
              <input id="catalog-search" type="search" autocomplete="off" placeholder="Поиск" />
            </label>
            <a class="catalog-topbar-link catalog-topbar-boosty" id="catalog-boosty" href="https://boosty.to/magic_sqd" target="_blank" rel="noopener">Boosty</a>
            <a class="catalog-topbar-link" id="catalog-github" href="https://github.com/torisar93/magic_sqd" target="_blank" rel="noopener">GitHub</a>
            <button class="catalog-settings" id="catalog-settings" type="button" aria-label="Настройки приложения" title="Настройки">
              <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M9.7 3.7h4.6l.7 2.1c.5.2 1 .5 1.4.8l2.1-.4 2.3 4-1.4 1.6v1.6l1.4 1.6-2.3 4-2.1-.4c-.4.3-.9.6-1.4.8l-.7 2.1H9.7L9 19.4c-.5-.2-1-.5-1.4-.8l-2.1.4-2.3-4 1.4-1.6v-1.6L3.2 10l2.3-4 2.1.4c.4-.3.9-.6 1.4-.8l.7-2.1Z"/><circle cx="12" cy="12" r="3.1"/></svg>
            </button>
            <button class="catalog-topbar-link catalog-admin-trigger" id="catalog-admin-toggle" type="button" aria-expanded="false">Вход</button>
          </nav>
        </header>
        <aside class="catalog-admin-popover" id="catalog-admin-popover" hidden aria-label="Аккаунт">
          <header><strong id="catalog-account-title">Вход</strong><span id="catalog-account-subtitle">Аккаунт техника — email и пароль</span></header>
          <div id="catalog-account-guest">
            <div class="field">
              <span class="field-label">Email</span>
              <input type="text" inputmode="email" id="catalog-account-email-input" autocomplete="email" />
            </div>
            <div class="field">
              <span class="field-label">Пароль</span>
              <input type="password" id="catalog-account-password-input" autocomplete="current-password" />
            </div>
            <button id="catalog-account-forgot" class="link-btn" hidden>Забыли пароль?</button>
            <p id="catalog-account-status" style="color: var(--text-dim); font-size: 12px"></p>
            <div class="dialog-actions spread">
              <button id="catalog-account-submit" class="accent">Войти</button>
              <button id="catalog-account-switch">Нет аккаунта? Зарегистрироваться</button>
            </div>
          </div>
          <div id="catalog-account-loggedin" hidden>
            <p id="catalog-account-loggedin-email" style="color: var(--text-dim); font-size: 12px"></p>
            <button id="catalog-account-change-password-toggle" class="link-btn">Сменить пароль</button>
            <div id="catalog-account-change-password" hidden>
              <div class="field">
                <span class="field-label">Текущий пароль</span>
                <input type="password" id="catalog-account-current-password" autocomplete="current-password" />
              </div>
              <div class="field">
                <span class="field-label">Новый пароль</span>
                <input type="password" id="catalog-account-new-password" autocomplete="new-password" />
              </div>
              <div class="field">
                <span class="field-label">Повторите новый пароль</span>
                <input type="password" id="catalog-account-new-password-repeat" autocomplete="new-password" />
              </div>
              <p id="catalog-account-change-password-status" style="color: var(--text-dim); font-size: 12px"></p>
              <button id="catalog-account-change-password-submit" class="accent">Сохранить пароль</button>
            </div>
            <button id="catalog-account-logout" class="danger">Выйти</button>
          </div>
          <div id="catalog-admin-actions"></div>
          <div id="catalog-admin-pending"></div>
        </aside>
        <nav class="breadcrumb catalog-breadcrumb" id="picker-breadcrumb" aria-label="Путь в каталоге"></nav>
        <div class="catalog-grid" id="picker-grid"></div>
        <p class="catalog-empty" id="catalog-empty" hidden>Ничего не найдено. Попробуйте другой запрос.</p>
        <div class="catalog-startup-overlay" id="catalog-startup-overlay" hidden aria-live="polite">
          <div class="catalog-startup-progress">
            <div class="catalog-startup-spinner" aria-hidden="true"></div>
            <strong>Обновляем каталог</strong>
            <span id="catalog-startup-progress-label">Подготавливаем список моделей…</span>
            <div class="catalog-startup-progress-track" role="progressbar" aria-label="Загрузка каталога">
              <div id="catalog-startup-progress-fill"></div>
            </div>
          </div>
        </div>
      </section>
    `;
    gridEl = container.querySelector("#picker-grid");
    crumbEl = container.querySelector("#picker-breadcrumb");
    searchEl = container.querySelector("#catalog-search");
    emptyEl = container.querySelector("#catalog-empty");
    settingsEl = container.querySelector("#catalog-settings");
    backEl = container.querySelector("#catalog-back");
    adminToggleEl = container.querySelector("#catalog-admin-toggle");
    adminPopoverEl = container.querySelector("#catalog-admin-popover");
    adminActionsEl = container.querySelector("#catalog-admin-actions");
    adminPendingEl = container.querySelector("#catalog-admin-pending");
    // Одна и та же кнопка/попап — и для входа в аккаунт (email+пароль,
    // регистрация), и (если у аккаунта есть права) для админ-функций (см.
    // auth_dialog.js: attach — владеет содержимым попапа, здесь только
    // открытие/закрытие самого попапа, всегда доступно, не только в
    // admin_mode).
    window.authDialog.attach({
      toggleEl: adminToggleEl,
      popoverEl: adminPopoverEl,
      titleEl: container.querySelector("#catalog-account-title"),
      guestEl: container.querySelector("#catalog-account-guest"),
      loggedinEl: container.querySelector("#catalog-account-loggedin"),
    });
    const closeAccountPopover = () => {
      adminPopoverEl.hidden = true;
      adminToggleEl.setAttribute("aria-expanded", "false");
    };
    adminToggleEl.addEventListener("click", (event) => {
      event.stopPropagation();
      const willOpen = adminPopoverEl.hidden;
      adminPopoverEl.hidden = !willOpen;
      adminToggleEl.setAttribute("aria-expanded", String(willOpen));
    });
    document.addEventListener("click", (event) => {
      if (!adminPopoverEl.hidden && !adminPopoverEl.contains(event.target) && event.target !== adminToggleEl) {
        closeAccountPopover();
      }
    });
    document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeAccountPopover(); });
    startupOverlayEl = container.querySelector("#catalog-startup-overlay");
    startupProgressFillEl = container.querySelector("#catalog-startup-progress-fill");
    startupProgressLabelEl = container.querySelector("#catalog-startup-progress-label");
    // Дебаунс — без него каждое нажатие клавиши перестраивает всю сетку
    // карточек синхронно; на слабом одноядерном CPU (см. createCard/
    // renderCatalog) быстрый набор текста ощутимо подвисает, ждём паузу
    // в наборе вместо перерисовки на каждый символ.
    let searchDebounceTimer = null;
    searchEl.addEventListener("input", () => {
      clearTimeout(searchDebounceTimer);
      searchDebounceTimer = setTimeout(renderCurrentStep, 150);
    });
    backEl.addEventListener("click", goBackOneStep);
    settingsEl.addEventListener("click", () => window.settingsDialog.open());
    await reload();
  }

  // Кнопка/попап сама теперь ВСЕГДА видна (см. init() — общий вход в
  // аккаунт техника, не только админ-функции) — эта функция только
  // показывает/прячет ВНУТРИ уже открытого попапа блок админ-действий
  // (см. auth_dialog.js — она же управляет блоками входа/аккаунта рядом).
  function setAdminMode(enabled) {
    if (!adminToggleEl) return;
    // Разовый перенос .left-actions/#pending-section внутрь поповера — при
    // ПЕРВОМ включении за этот запуск программы; переносить обратно при
    // выходе незачем, достаточно спрятать сам блок (см. ниже).
    if (enabled && !adminModeReady) {
      adminModeReady = true;
      adminToggleEl.closest(".catalog").classList.add("is-admin-mode");
      const actions = document.querySelector("#left-panel > .left-actions");
      const pending = document.querySelector("#left-panel > #pending-section");
      if (actions) adminActionsEl.appendChild(actions);
      if (pending) adminPendingEl.appendChild(pending);
    }
    adminActionsEl.hidden = !enabled;
    adminPendingEl.hidden = !enabled;
    if (!enabled) adminToggleEl.closest(".catalog").classList.remove("is-admin-mode");
  }

  async function reload() {
    data = await window.pywebview.api.scanner_list_cars();
    showBrandStep();
  }

  function showStartupLoading() {
    if (!startupOverlayEl) return;
    startupOverlayEl.hidden = false;
    startupProgressFillEl.style.width = "12%";
    startupProgressLabelEl.textContent = "Подготавливаем список моделей…";
  }

  function setStartupProgress(done, total, filesDone, filesTotal) {
    if (!startupOverlayEl || startupOverlayEl.hidden || total <= 0) return;
    const percent = Math.max(4, Math.min(100, Math.round((done / total) * 100)));
    startupProgressFillEl.style.width = `${percent}%`;
    const suffix = Number.isFinite(filesDone) && Number.isFinite(filesTotal)
      ? ` · ${filesDone} из ${filesTotal} файлов`
      : "";
    startupProgressLabelEl.textContent = `${percent}% скачано${suffix}`;
  }

  function hideStartupLoading() {
    if (!startupOverlayEl) return;
    startupProgressFillEl.style.width = "100%";
    startupOverlayEl.hidden = true;
  }

  function showBrandStep() {
    step = "brand";
    selectedBrand = null;
    selectedGroup = null;
    searchEl.value = "";
    renderCurrentStep();
  }

  function showModelStep(brand) {
    step = "model";
    selectedBrand = brand;
    selectedGroup = null;
    searchEl.value = "";
    renderCurrentStep();
  }

  function showModificationStep(brand, group) {
    step = "modification";
    selectedBrand = brand;
    selectedGroup = group;
    searchEl.value = "";
    renderCurrentStep();
  }

  function goBackOneStep() {
    if (step === "modification") showModelStep(selectedBrand);
    else showBrandStep();
  }

  function showModelListFor(model) {
    const brand = data && data.brands.find((item) => item.name === model?.brand);
    if (brand) showModelStep(brand);
    else showBrandStep();
  }

  // Марки — только синяя метка ("недавно обновлено"), остальные цвета
  // (зелёный/жёлтый/красный) на этом уровне не показываем: одна марка
  // объединяет много непохожих друг на друга моделей, и сводный по всем
  // сразу цвет тут больше путает, чем помогает технику (например одна
  // сломанная модификация красила бы всю марку красным, хотя остальные
  // ноль модели у неё рабочие).
  function brandCardStatus(brand) {
    return brand.status_color === "blue" ? "blue" : null;
  }

  // Модель с модификациями — сразу все точки по каждой модификации, а не
  // один сведённый цвет (см. rollup_status_color в app/scanner.py) — иначе
  // карточка красилась красным целиком, даже если сломана только ОДНА из
  // нескольких модификаций, а остальные рабочие. Без модификаций (leaf) —
  // как раньше, один цвет.
  function groupCardStatus(group) {
    if (group.has_modifications) {
      return { statuses: group.modifications.map((m) => m.status_color) };
    }
    return { status: group.status_color };
  }

  function renderCurrentStep() {
    if (step === "brand") {
      const query = searchEl.value.trim().toLocaleLowerCase();
      if (query) {
        const results = buildBrandSearchResults(query);
        renderCatalog({
          items: results,
        });
        return;
      }
      renderCatalog({
        items: data.brands.map((brand) => ({
          kind: "brand", title: brand.name, logo: brand.logo, status: brandCardStatus(brand),
          meta: `${brand.groups.length} ${plural(brand.groups.length, "модель", "модели", "моделей")}`,
          onClick: () => showModelStep(brand),
        })),
      });
      return;
    }

    if (step === "model") {
      renderCatalog({
        items: selectedBrand.groups.map((group) => ({
          kind: "model", title: group.name, logo: group.logo || group.leaf?.logo, ...groupCardStatus(group),
          meta: group.has_modifications
            ? `${group.modifications.length} ${plural(group.modifications.length, "версия", "версии", "версий")}`
            : group.leaf.no_instruction ? "Способ уточняется" : "Открыть инструкцию",
          action: group.has_modifications ? "Выбрать версию" : "Открыть",
          hasVariants: group.has_modifications,
          onClick: () => (group.has_modifications ? showModificationStep(selectedBrand, group) : selectModel(group.leaf)),
        })),
      });
      return;
    }

    renderCatalog({
      items: selectedGroup.modifications.map((modification) => ({
        kind: "variant", title: modification.modification, logo: modification.logo || selectedGroup.logo, status: modification.status_color,
        meta: modification.no_instruction ? "Способ уточняется" : "Открыть инструкцию",
        action: "Открыть", onClick: () => selectModel(modification),
      })),
    });
  }

  function buildBrandSearchResults(query) {
    const results = [];
    for (const brand of data.brands) {
      if (brand.name.toLocaleLowerCase().includes(query)) {
        results.push({
          kind: "brand", title: brand.name, logo: brand.logo, status: brandCardStatus(brand),
          meta: `${brand.groups.length} ${plural(brand.groups.length, "модель", "модели", "моделей")}`,
          action: "Открыть марку", onClick: () => showModelStep(brand),
        });
      }
      for (const group of brand.groups) {
        if (group.name.toLocaleLowerCase().includes(query)) {
          results.push({
            kind: "model", title: group.name, logo: group.logo || group.leaf?.logo, ...groupCardStatus(group),
            meta: brand.name,
            action: group.has_modifications ? "Выбрать версию" : "Открыть",
            hasVariants: group.has_modifications,
            onClick: () => (group.has_modifications
              ? showModificationStep(brand, group)
              : selectModel(group.leaf)),
          });
        }
        if (!group.has_modifications) continue;
        for (const modification of group.modifications) {
          if (!(modification.modification || "").toLocaleLowerCase().includes(query)) continue;
          results.push({
            kind: "variant", title: `${group.name} — ${modification.modification}`,
            logo: modification.logo || group.logo, status: modification.status_color, meta: brand.name, action: "Открыть",
            onClick: () => selectModel(modification),
          });
        }
      }
    }
    return results;
  }

  function renderCatalog(view) {
    searchEl.placeholder = "Поиск";
    renderBreadcrumb();
    const query = searchEl.value.trim().toLocaleLowerCase();
    const visibleItems = query
      ? view.items.filter((item) => `${item.title} ${item.meta}`.toLocaleLowerCase().includes(query))
      : view.items;
    gridEl.innerHTML = "";
    // Показываем "Ничего не найдено" только когда пусто именно из-за
    // введённого поискового запроса — иначе тот же текст ошибочно всплывал
    // при первом запуске (каталог ещё не синхронизирован, items пуст без
    // всякого поиска) поверх/рядом с "Обновляем каталог" (см.
    // catalog-startup-overlay ниже), что выглядело как баг поиска.
    emptyEl.hidden = visibleItems.length > 0 || !query;
    // DocumentFragment вместо N отдельных appendChild — на слабом
    // одноядерном CPU (реальный случай: техник тестировал на eMachines
    // E510, Celeron 900 2009 года) каждая вставка в живой DOM даёт браузеру
    // повод пересчитать стили/раскладку; один appendChild фрагмента вместо
    // десятков карточек — это один такой повод вместо N.
    const fragment = document.createDocumentFragment();
    visibleItems.forEach((item, index) => fragment.appendChild(createCard(item, index)));
    gridEl.appendChild(fragment);
  }

  function createCard(item, index) {
    const card = document.createElement("button");
    card.type = "button";
    card.className = `catalog-card catalog-card-${item.kind}`;
    card.style.setProperty("--card-order", Math.min(index, 10));
    card.setAttribute("aria-label", `${item.title}. ${item.meta}`);

    const visual = document.createElement("span");
    visual.className = "catalog-card-visual";
    if (item.logo) {
      const logo = document.createElement("img");
      logo.className = item.kind === "brand" ? "catalog-brand-logo" : "catalog-model-logo";
      logo.src = item.logo;
      logo.alt = `Логотип ${item.title}`;
      if (item.kind === "model") {
        logo.addEventListener("error", () => {
          logo.replaceWith(defaultModelLogo(item.title));
        }, { once: true });
      }
      visual.appendChild(logo);
    } else if (item.kind === "brand") {
        const monogram = document.createElement("span");
        monogram.className = "catalog-monogram";
        monogram.textContent = item.title.slice(0, 2).toLocaleUpperCase();
        visual.appendChild(monogram);
    } else {
      visual.appendChild(defaultModelLogo(item.title));
    }
    card.appendChild(visual);

    const content = document.createElement("span");
    content.className = "catalog-card-content";
    const title = document.createElement("span");
    title.className = "catalog-card-title";
    title.textContent = item.title;
    const meta = document.createElement("span");
    meta.className = "catalog-card-meta";
    meta.textContent = item.meta;
    content.append(title, meta);
    card.appendChild(content);

    const footer = document.createElement("span");
    footer.className = "catalog-card-footer";
    if (item.statuses && item.statuses.length) {
      const group = document.createElement("span");
      group.className = "catalog-status-group";
      for (const color of item.statuses) group.appendChild(statusBadge(color));
      footer.appendChild(group);
    } else if (item.status) {
      footer.appendChild(statusBadge(item.status));
    }
    const action = document.createElement("span");
    action.className = "catalog-card-action";
    action.textContent = item.action || "Открыть";
    footer.appendChild(action);
    card.appendChild(footer);
    card.addEventListener("click", () => {
      card.classList.add("is-selected");
      item.onClick();
    });
    return card;
  }

  function defaultModelLogo(title) {
    const logo = document.createElement("img");
    logo.className = "catalog-model-logo catalog-model-logo-default";
    logo.src = "img/default-model-logo.png";
    logo.alt = `Логотип ${title}`;
    logo.addEventListener("error", () => logo.replaceWith(vehicleIcon()), { once: true });
    return logo;
  }

  function vehicleIcon() {
    const icon = document.createElement("span");
    icon.className = "catalog-vehicle";
    icon.innerHTML = `<svg viewBox="0 0 120 64" aria-hidden="true" focusable="false">
      <path d="M20 42h80l-5-16c-1.3-4-5-7-9.2-7H46c-4.4 0-8.4 2.5-10.4 6.4L29 38H20c-4.4 0-8 3.6-8 8v5h8v-9Z" />
      <path d="M34 27h22v11H29l5-11Zm26 0h24c2.8 0 5.2 1.8 6.1 4.4L92 38H60V27Z" class="catalog-vehicle-window" />
      <circle cx="33" cy="48" r="8" /><circle cx="87" cy="48" r="8" />
    </svg>`;
    return icon;
  }

  function statusBadge(color) {
    const badge = document.createElement("span");
    badge.className = `catalog-status status-dot-${color}`;
    badge.title = STATUS_COLOR_TITLES[color] || "Статус модели";
    badge.setAttribute("aria-label", STATUS_COLOR_TITLES[color] || "Статус модели");
    return badge;
  }

  function renderBreadcrumb() {
    crumbEl.innerHTML = "";
    crumbEl.hidden = true;
    backEl.hidden = step === "brand";
  }

  async function selectModel(modelSummary) {
    const model = await window.pywebview.api.scanner_select_model(modelSummary.key);
    if (onModelSelected && !model.error) await onModelSelected(model);
  }

  function plural(number, one, few, many) {
    const mod10 = number % 10;
    const mod100 = number % 100;
    return mod10 === 1 && mod100 !== 11 ? one : mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14) ? few : many;
  }

  function getBrands() {
    return data ? data.brands.map((brand) => brand.name) : [];
  }

  window.mainPicker = {
    init, reload, getBrands, showHome: showBrandStep, showModelListFor,
    showStartupLoading, setStartupProgress, hideStartupLoading, setAdminMode,
  };
})();
