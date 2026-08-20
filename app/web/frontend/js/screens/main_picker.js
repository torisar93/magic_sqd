// 3-шаговый выбор марка -> модель -> (опционально) модификация — портирован
// с _show_brand_step/_show_model_step/_show_modification_step из старого
// app/gui.py. Отличие от старого интерфейса: "назад" — это настоящий
// breadcrumb над списком, а не подделанная первая строка списка (см. план
// миграции) — сама модель навигации (шаги/данные) перенесена как есть.
(function () {
  // Подсказка при наведении на цветную метку (см. app/scanner.py:
  // model_status_color) — сама метка ставится вручную в редакторе,
  // здесь только текст всплывающей подсказки по уже готовому цвету.
  const STATUS_COLOR_TITLES = {
    green: "Актуально",
    yellow: "Требует обновления — способ не проверен/черновой",
    blue: "Недавно обновлено",
    red: "Способ не работает",
  };

  let data = null; // {brands: [...]}, см. app/web/api/scanner_api.py
  let step = "brand"; // "brand" | "model" | "modification"
  let selectedBrand = null;
  let selectedGroup = null;
  let onModelSelected = null;
  let listEl, crumbEl;

  async function init(container, callbacks) {
    onModelSelected = callbacks.onModelSelected;
    container.innerHTML = `
      <div class="breadcrumb" id="picker-breadcrumb"></div>
      <ul class="picker-list" id="picker-list"></ul>
    `;
    crumbEl = container.querySelector("#picker-breadcrumb");
    listEl = container.querySelector("#picker-list");
    await reload();
  }

  async function reload() {
    data = await window.pywebview.api.scanner_list_cars();
    showBrandStep();
  }

  function showBrandStep() {
    step = "brand";
    selectedBrand = null;
    selectedGroup = null;
    renderBreadcrumb();
    renderList(
      data.brands.map((b) => ({ label: b.name, logo: b.logo, color: b.status_color, onClick: () => showModelStep(b) }))
    );
  }

  function showModelStep(brand) {
    step = "model";
    selectedBrand = brand;
    selectedGroup = null;
    renderBreadcrumb();
    renderList(
      brand.groups.map((group) => ({
        label: group.has_modifications ? `${group.name}  ▸` : group.name,
        color: group.status_color,
        onClick: () => (group.has_modifications ? showModificationStep(brand, group) : selectModel(group.leaf)),
      }))
    );
  }

  function showModificationStep(brand, group) {
    step = "modification";
    selectedBrand = brand;
    selectedGroup = group;
    renderBreadcrumb();
    renderList(
      group.modifications.map((mod) => ({ label: mod.modification, color: mod.status_color, onClick: () => selectModel(mod) }))
    );
  }

  async function selectModel(modelSummary) {
    const model = await window.pywebview.api.scanner_select_model(modelSummary.key);
    if (onModelSelected) onModelSelected(model);
  }

  function renderBreadcrumb() {
    const parts = [{ text: "Марка", onClick: showBrandStep }];
    if (selectedBrand) {
      parts.push({ text: selectedBrand.name, onClick: () => showModelStep(selectedBrand) });
    }
    if (selectedGroup) {
      parts.push({ text: selectedGroup.name, onClick: null });
    }
    crumbEl.innerHTML = "";
    parts.forEach((part, i) => {
      if (i > 0) {
        const sep = document.createElement("span");
        sep.className = "sep";
        sep.textContent = "›";
        crumbEl.appendChild(sep);
      }
      const btn = document.createElement("button");
      btn.className = "crumb" + (part.onClick ? "" : " current");
      btn.textContent = part.text;
      if (part.onClick) btn.addEventListener("click", part.onClick);
      else btn.disabled = true;
      crumbEl.appendChild(btn);
    });
  }

  function renderList(items) {
    listEl.innerHTML = "";
    if (items.length === 0) {
      const li = document.createElement("li");
      li.className = "empty";
      li.textContent = "Пусто";
      listEl.appendChild(li);
      return;
    }
    for (const item of items) {
      const li = document.createElement("li");
      if (item.logo) {
        const img = document.createElement("img");
        img.className = "logo";
        img.src = item.logo;
        li.appendChild(img);
      }
      const span = document.createElement("span");
      span.style.flex = "1";
      span.textContent = item.label;
      li.appendChild(span);
      if (item.color) {
        const dot = document.createElement("span");
        dot.className = `status-dot status-dot-${item.color}`;
        dot.title = STATUS_COLOR_TITLES[item.color] || "";
        li.appendChild(dot);
      }
      li.addEventListener("click", item.onClick);
      listEl.appendChild(li);
    }
  }

  function getBrands() {
    return data ? data.brands.map((b) => b.name) : [];
  }

  window.mainPicker = { init, reload, getBrands };
})();
