// Общие поля формы этапа (тип-специфичные + условная видимость) —
// используется панелью свойств узла графа (graph_wizard.js). Изначально
// было извлечено из старого текстового мастера car_wizard.js (список+форма,
// удалён после сравнения с визуальным редактором), чтобы не дублировать
// ~350 строк логики работы с файлами/вариантами/условиями — отсюда фабрика
// createStepFieldsController() с параметрами вместо module-level состояния.
(function () {
  const { el, clear } = window.dom;

  const ADB_HELP_TEXT =
    'Можно вставить кусок .bat/.sh как есть: строки "adb ..."/"TIMEOUT /T N" распознаются ' +
    "автоматически, декоративный batch-мусор (@echo/cls/pause/rem/метки) пропускается сам.\n\n" +
    "Спецкоманды (тоже по одной на строку, среди обычных команд):\n" +
    "#sleep 5 — пауза 5 секунд\n" +
    "#reboot — перезагрузить магнитолу и дождаться загрузки\n" +
    '#reboot_nowait — перезагрузить, не дожидаясь (= "adb reboot" как в .bat)\n' +
    "#wait_device — дождаться устройства (можно с таймаутом: #wait_device 60)\n" +
    "#root — adb root\n#disable_verity — adb disable-verity\n#remount — adb remount\n" +
    "#push ИмяФайла /remote/path — закачать прикреплённый файл (см. ниже)\n" +
    "#install ИмяФайла — установить прикреплённый APK\n" +
    "#install_stream ИмяФайла — установить прикреплённый APK через " +
    '"cat файл | pm install -S размер" вместо обычного adb install — для магнитол, где ' +
    "штатный install не работает (например Jetour Dashing на Android 9, Soueast S09); " +
    "размер в -S подставляется автоматически по текущему файлу\n" +
    "#ask Введите IP-адрес — спросить у пользователя во время установки; ответ можно " +
    "подставить в следующую команду через {ask}, например:\nconnect {ask}:5555\n\n" +
    "Из .bat/.sh распознаются как есть (без переписывания в #-спецкоманды): " +
    "adb root/remount/disable-verity/reboot/wait-for-device, adb shell <команда>, " +
    "adb push <файл> <путь>, adb install <apk>, cat файл | pm install -S размер, TIMEOUT /T N.";

  // container — куда добавлять поля (вызывающий сам чистит его перед
  // вызовом renderTypeFields/renderConditionFields); getSteps() — актуальный
  // массив steps редактируемой машины (нужен renderConditionFields, чтобы
  // найти check-этап по имени переменной); getBrandModel() -> {brand, model}
  // (для шаблона инструкции по умолчанию); rerender() — полная перерисовка
  // панели текущего этапа (после изменений, которые требуют перестроить
  // форму, например переключение вариантов). options.hideCheckVarField —
  // редактор-граф выражает condition_var проводами, а не текстовым полем:
  // имя переменной генерируется автоматически (см. graph_wizard.js:
  // generateCheckVar) и никогда не показывается пользователю.
  function createStepFieldsController(container, getSteps, getBrandModel, rerender, options = {}) {
    const { hideCheckVarField = false } = options;
    let editingVariantIndex = 0;

    function buildSpoiler(title, bodyText) {
      let collapsed = true;
      const body = el("div", { class: "spoiler-body collapsed", text: bodyText });
      const toggle = el("span", { text: "▸ " + title });
      const header = el("div", { class: "spoiler-header" }, [toggle]);
      header.addEventListener("click", () => {
        collapsed = !collapsed;
        body.classList.toggle("collapsed", collapsed);
        toggle.textContent = (collapsed ? "▸ " : "▾ ") + title;
      });
      return el("div", {}, [header, body]);
    }

    // -- общий список файлов (используется adb_files и одиночными usb/apps) --
    function buildFileList(fileArray, pickKind, multiple, onChange) {
      const wrap = el("div");
      const addBtn = el("button", {
        text: pickKind === "apk" ? "Добавить APK..." : "Добавить файлы...",
        onclick: async () => {
          const picked = await window.pywebview.api.car_pick_files(pickKind, multiple);
          if (!picked.length) return;
          fileArray.push(...picked);
          onChange();
        },
      });
      // Папку целиком — только там, где это осмысленно (usb_files/adb_files,
      // не отдельные APK/.exe) — car_generator.py копирует её рекурсивно
      // (см. _copy_path). Отдельный нативный диалог — ОС не даёт выбирать
      // вперемешку файлы и папки в одном окне.
      const addFolderBtn = pickKind === "any" ? el("button", {
        text: "Добавить папку...",
        onclick: async () => {
          const picked = await window.pywebview.api.car_pick_files("folder", false);
          if (!picked.length) return;
          fileArray.push(...picked);
          onChange();
        },
      }) : null;
      const list = el("ul", { class: "picker-list", style: "margin: 6px 0; max-height: 140px" });
      const selected = new Set();
      fileArray.forEach((f, i) => {
        const li = el("li", { text: f.name });
        li.addEventListener("click", () => {
          li.classList.toggle("selected");
          if (selected.has(i)) selected.delete(i); else selected.add(i);
        });
        list.appendChild(li);
      });
      const removeBtn = el("button", {
        class: "danger",
        text: "Убрать выбранное",
        onclick: () => {
          for (const i of Array.from(selected).sort((a, b) => b - a)) fileArray.splice(i, 1);
          onChange();
        },
      });
      wrap.appendChild(addBtn);
      if (addFolderBtn) wrap.appendChild(addFolderBtn);
      wrap.appendChild(list);
      wrap.appendChild(removeBtn);
      return wrap;
    }

    // -- обязательные/необязательные APK этапа "Выбор приложений" — как
    // buildFileList, но с полями "красивого" названия/описания на каждый
    // APK (см. app/car_generator.py: StandardApkSpec) — их видит техник
    // во время установки вместо голого имени файла (см.
    // stage_wizard.js: buildAppRow), тот же смысл, что у карточки APK в
    // общей библиотеке apk/ (см. dialogs.js: adminApkDialog).
    function buildApkList(apkArray, onChange) {
      const wrap = el("div");
      const addBtn = el("button", {
        text: "Добавить APK...",
        onclick: async () => {
          const picked = await window.pywebview.api.car_pick_files("apk", true);
          if (!picked.length) return;
          for (const p of picked) apkArray.push({ ...p, display_name: "", description: "" });
          onChange();
        },
      });
      const list = el("div", { style: "margin: 6px 0" });
      apkArray.forEach((apk, i) => {
        const row = el("div", { class: "card", style: "padding: 6px 8px; margin-bottom: 6px" });
        const top = el("div", { style: "display: flex; align-items: center; gap: 6px" });
        top.appendChild(el("span", {
          style: "flex: 1; font-family: var(--font-mono); font-size: 12px; color: var(--text-dim)",
          text: apk.name,
        }));
        top.appendChild(el("button", {
          class: "danger", text: "Убрать",
          onclick: () => { apkArray.splice(i, 1); onChange(); },
        }));
        row.appendChild(top);
        const nameInput = el("input", { type: "text", placeholder: "Красивое название (по умолчанию — имя файла)" });
        nameInput.value = apk.display_name || "";
        nameInput.addEventListener("input", () => { apk.display_name = nameInput.value; });
        const descInput = el("input", {
          type: "text", placeholder: "Описание (необязательно)", style: "margin-top: 4px",
        });
        descInput.value = apk.description || "";
        descInput.addEventListener("input", () => { apk.description = descInput.value; });
        row.appendChild(nameInput);
        row.appendChild(descInput);
        list.appendChild(row);
      });
      wrap.appendChild(addBtn);
      wrap.appendChild(list);
      return wrap;
    }

    // -- adb --------------------------------------------------------------
    function renderAdbFields(step) {
      container.appendChild(el("span", { class: "field-label", text: "Команды (по одной на строку, по порядку)" }));
      const commandsArea = el("textarea", { style: "min-height: 140px; font-family: var(--font-mono)" });
      commandsArea.value = step.commands.join("\n");
      commandsArea.addEventListener("input", () => {
        step.commands = commandsArea.value.split("\n").map((l) => l.trim()).filter(Boolean);
      });
      container.appendChild(commandsArea);
      container.appendChild(buildSpoiler("Справка по командам", ADB_HELP_TEXT));

      container.appendChild(el("span", { class: "field-label", text: "Прикреплённые файлы (для #push/#install и adb push/adb install)" }));
      container.appendChild(buildFileList(step.adb_files, "any", true, () => rerender()));

      const installRow = el("label", { class: "row", style: "margin-top: 8px" });
      const installCheckbox = el("input", { type: "checkbox" });
      installCheckbox.checked = step.adb_install_selected_apks;
      installCheckbox.addEventListener("change", () => { step.adb_install_selected_apks = installCheckbox.checked; });
      installRow.appendChild(installCheckbox);
      installRow.appendChild(document.createTextNode("Установить отмеченные галочками приложения после команд"));
      container.appendChild(installRow);
    }

    // -- uart ---------------------------------------------------------------
    // Справочное поле порта Wi-Fi ADB — для UART: этот тип этапа
    // технически подключается иначе (через COM-порт)
    // и нигде это поле не читают, оно просто на память автору модели, если
    // порт всё же известен (например для другого этапа той же машины).
    function renderInfoPortRow(step, portKey) {
      const row = el("div", { class: "row", style: "margin-bottom: 10px" });
      row.appendChild(el("span", { text: "Порт Wi-Fi ADB (справочно, ни на что не влияет):" }));
      const input = el("input", { type: "text", style: "width: 90px", placeholder: "неизвестен" });
      input.value = step[portKey] != null ? String(step[portKey]) : "";
      input.addEventListener("input", () => {
        const v = input.value.trim();
        step[portKey] = v ? (Number(v) || null) : null;
      });
      row.appendChild(input);
      container.appendChild(row);
    }

    function renderUartFields(step) {
      container.appendChild(el("p", {
        class: "app-desc",
        text: "Подключение по UART (последовательный порт, например через USB-UART переходник) — "
          + "аналог PuTTY. COM-порт выбирается техником на месте во время установки (или "
          + "определяется сам, если он один) — здесь настраивается только скорость порта.",
      }));
      renderInfoPortRow(step, "uart_wifi_port");
      container.appendChild(el("span", { class: "field-label", text: "Скорость порта (бод)" }));
      const baudInput = el("input", { type: "number", style: "width: 120px; margin-bottom: 10px" });
      baudInput.value = String(step.uart_baudrate);
      baudInput.addEventListener("input", () => {
        step.uart_baudrate = parseInt(baudInput.value, 10) || 115200;
      });
      container.appendChild(baudInput);

      container.appendChild(el("span", {
        class: "field-label",
        text: "Команды (по одной на строку, по порядку) — отправляются как есть, без обработки",
      }));
      const commandsArea = el("textarea", { style: "min-height: 100px; font-family: var(--font-mono)" });
      commandsArea.value = step.commands.join("\n");
      commandsArea.addEventListener("input", () => {
        step.commands = commandsArea.value.split("\n").map((l) => l.trim()).filter(Boolean);
      });
      container.appendChild(commandsArea);
      container.appendChild(el("p", {
        class: "app-desc",
        text: "Каждая строка отправляется в порт как есть (с добавлением \\r\\n в конце), "
          + "ответ устройства (если есть) выводится в лог установки.",
      }));
    }

    // -- telnet ---------------------------------------------------------------
    function renderTelnetFields(step) {
      container.appendChild(el("p", {
        class: "app-desc",
        text: "Подключение по telnet к IPv6-адресу магнитолы (для моделей, где ADB изначально "
          + "скрыт) — адрес находится автоматически (сканирование соседей в сети) или "
          + "предлагается выбрать/ввести на месте во время установки.",
      }));
      container.appendChild(el("span", {
        class: "field-label",
        text: "Команды (по одной на строку) — каждая отправляется отдельным telnet-подключением",
      }));
      const commandsArea = el("textarea", { style: "min-height: 80px; font-family: var(--font-mono)" });
      commandsArea.value = step.commands.join("\n");
      commandsArea.addEventListener("input", () => {
        step.commands = commandsArea.value.split("\n").map((l) => l.trim()).filter(Boolean);
      });
      container.appendChild(commandsArea);
    }

    // -- actions --------------------------------------------------------
    const ACTION_KIND_LABELS = {
      command: "Команда(ы) ADB",
      grant_permissions: "Выдать разрешения приложению",
      mock_location: "Приложение для фиктивных местоположений",
      disable_app: "Отключить приложение",
      enable_app: "Включить приложение",
    };

    const ACTION_COMMAND_HELP_TEXT =
      'По одной команде на строку — обычные "adb shell"-команды. Также доступны спецкоманды:\n' +
      "#sleep 5 — пауза 5 секунд\n#reboot — перезагрузить магнитолу и дождаться загрузки\n" +
      "#reboot_nowait — перезагрузить, не дожидаясь\n#root — adb root\n" +
      "#push ИмяФайла /remote/path — закачать прикреплённый файл (см. ниже)\n" +
      "#install ИмяФайла — установить прикреплённый APK\n" +
      "#ask Введите значение — спросить у пользователя, ответ можно подставить в следующую команду " +
      "через {ask}.";

    function renderActionsFields(step) {
      renderConnectionRow(step, "actions_connection", "actions_wifi_port");

      container.appendChild(el("p", {
        class: "app-desc",
        text: "Кнопки, которые техник сможет нажимать в любом порядке и по несколько раз на этом "
          + "этапе (например запустить приложение, выдать ему разрешения, назначить приложение для "
          + "фиктивных местоположений) — не обязательны для перехода «Далее».",
      }));
      const listWrap = el("div");
      container.appendChild(listWrap);
      renderActionsList(step, listWrap);
      container.appendChild(el("button", {
        text: "Добавить действие",
        onclick: () => { step.actions.push({ label: "", kind: "command", commands: [], files: [] }); rerender(); },
      }));
    }

    function renderActionsList(step, wrap) {
      clear(wrap);
      if (!step.actions.length) {
        wrap.appendChild(el("p", { class: "app-desc", text: "Действий пока нет — добавьте хотя бы одно ниже." }));
        return;
      }
      step.actions.forEach((action, i) => {
        const card = el("div", { class: "instruction-block-row" });
        card.appendChild(el("div", { class: "block-row-header" }, [
          el("span", { text: `Действие ${i + 1}` }),
          el("button", {
            class: "danger icon-btn", text: "✕",
            onclick: () => { step.actions.splice(i, 1); rerender(); },
          }),
        ]));

        card.appendChild(el("span", { class: "field-label", text: "Название кнопки (что увидит техник)" }));
        const labelInput = el("input", { type: "text", placeholder: 'например "Запустить приложение"' });
        labelInput.value = action.label;
        labelInput.addEventListener("input", () => { action.label = labelInput.value; });
        card.appendChild(labelInput);

        card.appendChild(el("span", { class: "field-label", style: "margin-top: 6px", text: "Тип действия" }));
        const kindSelect = el("select", {}, Object.entries(ACTION_KIND_LABELS).map(([value, text]) =>
          el("option", { value, text, selected: value === action.kind ? "" : null })));
        kindSelect.addEventListener("change", () => { action.kind = kindSelect.value; rerender(); });
        card.appendChild(kindSelect);

        if (action.kind === "command") {
          card.appendChild(el("span", { class: "field-label", style: "margin-top: 6px", text: "Команды (по одной на строку)" }));
          const commandsArea = el("textarea", { style: "min-height: 70px; font-family: var(--font-mono)" });
          commandsArea.value = action.commands.join("\n");
          commandsArea.addEventListener("input", () => {
            action.commands = commandsArea.value.split("\n").map((l) => l.trim()).filter(Boolean);
          });
          card.appendChild(commandsArea);
          card.appendChild(buildSpoiler("Справка по командам", ACTION_COMMAND_HELP_TEXT));

          card.appendChild(el("span", {
            class: "field-label", style: "margin-top: 6px",
            text: "Прикреплённые файлы (для #push/#install и adb push/adb install)",
          }));
          if (!action.files) action.files = [];
          card.appendChild(buildFileList(action.files, "any", true, () => rerender()));
        } else if (action.kind === "grant_permissions") {
          card.appendChild(el("p", {
            class: "app-desc", style: "margin-top: 4px",
            text: "Во время установки техник выберет приложение из списка, установленного на магнитоле "
              + "— программа сама выдаст ему все доступные через ADB разрешения, включая специальные "
              + "(изменение системных настроек, показ поверх других окон, спецвозможности, установка "
              + "APK и активация VPN без системных диалогов) — плюс освободит от ограничений "
              + "энергосбережения, чтобы Android не убивал его в фоне.",
          }));
        } else if (action.kind === "mock_location") {
          card.appendChild(el("p", {
            class: "app-desc", style: "margin-top: 4px",
            text: "Во время установки техник выберет приложение из списка, установленного на магнитоле "
              + "— оно будет назначено приложением для фиктивных местоположений (имитация GPS), и эта "
              + "возможность будет включена.",
          }));
        } else if (action.kind === "disable_app") {
          card.appendChild(el("p", {
            class: "app-desc", style: "margin-top: 4px",
            text: "Во время установки техник выберет приложение из полного списка на магнитоле "
              + "(включая системные) — оно будет отключено: пропадёт из лаунчера и перестанет "
              + "запускаться, но останется установленным (не удаление — для системных приложений, "
              + "которые нельзя просто удалить, например конкурирующая навигация или голосовой "
              + "ассистент). Обратимо кнопкой «Включить приложение».",
          }));
        } else if (action.kind === "enable_app") {
          card.appendChild(el("p", {
            class: "app-desc", style: "margin-top: 4px",
            text: "Во время установки техник выберет приложение из полного списка на магнитоле "
              + "(включая отключённые) и снова включит его.",
          }));
        }
        wrap.appendChild(card);
      });
    }

    // -- usb/apps: одиночный набор ИЛИ несколько именованных вариантов -------
    function renderUsbFields(step) {
      renderVariantToggle(step, "usb_files", "Файлы всех вариантов будут потеряны.");
      if (step.variants.length) {
        renderVariantSelector(step);
        renderVariantFileList(step, "usb_files", "any", 'Файлы варианта «{name}» в корень флешки');
      } else {
        container.appendChild(el("span", { class: "field-label", text: "Файлы в корень флешки" }));
        container.appendChild(buildFileList(step.usb_files, "any", true, () => rerender()));
      }
      const copyRow = el("label", { class: "row", style: "margin-top: 8px" });
      const copyCheckbox = el("input", { type: "checkbox" });
      copyCheckbox.checked = step.usb_copy_selected_apks;
      copyCheckbox.addEventListener("change", () => { step.usb_copy_selected_apks = copyCheckbox.checked; rerender(); });
      copyRow.appendChild(copyCheckbox);
      copyRow.appendChild(document.createTextNode("Добавить выбор APK из общей библиотеки"));
      container.appendChild(copyRow);

      if (step.usb_copy_selected_apks) {
        container.appendChild(el("span", { class: "field-label", style: "margin-top: 4px", text: "Папка на флешке для этих APK (пусто — корень флешки)" }));
        const destInput = el("input", { type: "text", placeholder: "например apps" });
        destInput.value = step.usb_apks_dest;
        destInput.addEventListener("input", () => { step.usb_apks_dest = destInput.value.trim(); });
        container.appendChild(destInput);
      }

      renderSharedUsbFolderField(step);
    }

    // Общий набор файлов из cars/_shared/ (см. app/install_context.py:
    // ctx.shared_dir, app/car_generator.py: StepSpec.usb_shared_folder) —
    // один и тот же набор можно использовать сразу в МНОГИХ моделях, не
    // копируя его в usb_files каждой отдельно (не дублируется ни на
    // сервере, ни у техника). Работает одновременно с обычными файлами
    // выше, если заданы оба. Имя — текстовое поле с автодополнением из уже
    // существующих наборов (тот же приём, что и марка в шапке мастера, см.
    // renderHeader в car_wizard.js/graph_wizard.js) — впишите новое имя,
    // чтобы создать набор, или выберите существующее, чтобы переиспользовать.
    function renderSharedUsbFolderField(step) {
      container.appendChild(el("span", {
        class: "field-label", style: "margin-top: 8px",
        text: "Общий набор файлов из _shared/ (необязательно, для многих моделей сразу)",
      }));
      const listId = "shared-usb-folders-" + Math.random().toString(36).slice(2, 8);
      const nameInput = el("input", { type: "text", list: listId, placeholder: "имя общего набора" });
      nameInput.value = step.usb_shared_folder;
      nameInput.addEventListener("input", () => { step.usb_shared_folder = nameInput.value.trim(); });
      const datalist = el("datalist", { id: listId });
      window.pywebview.api.car_list_shared_usb_folders().then((folders) => {
        for (const name of folders) datalist.appendChild(el("option", { value: name }));
      });
      container.appendChild(nameInput);
      container.appendChild(datalist);

      const addToShared = async (pickKind, multiple) => {
        const name = nameInput.value.trim();
        if (!name) {
          await window.notice("Сначала впишите имя общего набора (новое или уже существующее).");
          return;
        }
        const picked = await window.pywebview.api.car_pick_files(pickKind, multiple);
        if (!picked.length) return;
        const result = await window.pywebview.api.car_save_shared_usb_files(name, picked);
        if (!result.ok) {
          await window.notice(result.error, { title: "Общий набор файлов", danger: true });
          return;
        }
        step.usb_shared_folder = result.name;
        nameInput.value = result.name;
      };
      const sharedButtons = el("div", { class: "row", style: "margin-top: 4px" });
      sharedButtons.appendChild(el("button", { text: "Добавить файлы в набор...", onclick: () => addToShared("any", true) }));
      sharedButtons.appendChild(el("button", { text: "Добавить папку в набор...", onclick: () => addToShared("folder", false) }));
      container.appendChild(sharedButtons);
    }

    const APPS_CONNECTION_LABELS = {
      wired: "Провод (USB)", wifi: "Wi-Fi", ask: "Спросить технику на месте",
    };

    // Общий блок "способ подключения + порт" — используется и "Установкой
    // приложений" (apps_connection/apps_wifi_port), и "ADB-командами"
    // (actions_connection/actions_wifi_port): у КАЖДОГО этапа свой
    // независимый выбор, одна и та же модель может ставить приложения по
    // Wi-Fi на одном этапе, а выполнять ADB-команды по проводу на другом.
    // Порт — пусто, если заранее не известен: техник впишет его сам при
    // подключении на самом этапе установки.
    function renderConnectionRow(step, connectionKey, portKey) {
      container.appendChild(el("span", { class: "field-label", text: "Способ подключения" }));
      const connectionSelect = el("select", {}, Object.entries(APPS_CONNECTION_LABELS).map(([value, text]) =>
        el("option", { value, text, selected: value === (step[connectionKey] || "wired") ? "" : null })));
      container.appendChild(connectionSelect);

      const portRow = el("div", { class: "row", style: "margin-top: 4px" });
      portRow.appendChild(el("span", { text: "Порт Wi-Fi:" }));
      const portInput = el("input", {
        type: "text", style: "width: 90px",
        placeholder: "неизвестен — впишет техник",
      });
      portInput.value = step[portKey] != null ? String(step[portKey]) : "";
      portInput.addEventListener("input", () => {
        const v = portInput.value.trim();
        step[portKey] = v ? (Number(v) || null) : null;
      });
      portRow.appendChild(portInput);
      function updatePortVisibility() {
        portRow.style.display = connectionSelect.value === "wired" ? "none" : "flex";
      }
      connectionSelect.addEventListener("change", () => {
        step[connectionKey] = connectionSelect.value;
        updatePortVisibility();
      });
      updatePortVisibility();
      container.appendChild(portRow);
    }

    // Ключи те же, что INSTALL_METHOD_KEYS в app/install_context.py — если
    // список способов там изменится, поправить и здесь.
    const APPS_INSTALL_METHOD_LABELS = {
      "": "Автоматически (перебор по порядку)",
      adb_install: "adb install",
      pm_install: "adb push + pm install",
      pm_install_stream: "adb push + pm install -S (поток)",
      localinstall: "app_process + localinstall.apk (Chery DesaySV)",
    };

    // Подсказка "начни перебор способов установки APK с этого" — не
    // отменяет перебор остальных, только меняет порядок (см. car_generator.py:
    // StepSpec.apps_install_method/install_context.py: install_apk_auto) —
    // полезно, когда автор модели уже знает рабочий способ на конкретной
    // платформе (например Chery/Jaecoo/Exeed/Tenet на DesaySV — там всегда
    // срабатывает "localinstall") и не хочет, чтобы техник каждый раз ждал,
    // пока программа перепробует предыдущие способы впустую.
    function renderInstallMethodRow(step) {
      container.appendChild(el("span", { class: "field-label", text: "Способ установки APK" }));
      const select = el("select", {}, Object.entries(APPS_INSTALL_METHOD_LABELS).map(([value, text]) =>
        el("option", { value, text, selected: value === (step.apps_install_method || "") ? "" : null })));
      select.addEventListener("change", () => { step.apps_install_method = select.value; });
      container.appendChild(select);
      container.appendChild(el("p", {
        style: "font-size: 12px; color: var(--text-dim); margin: 4px 0 0;",
        text: "Если заранее известно, какой способ работает на этой магнитоле — программа попробует его первым; "
          + "остальные всё равно пробуются по порядку следом, если он не сработает.",
      }));
    }

    function renderAppsFields(step) {
      renderConnectionRow(step, "apps_connection", "apps_wifi_port");
      renderInstallMethodRow(step);

      renderVariantToggle(step, "standard_apks", "APK всех вариантов будут потеряны.");
      if (step.variants.length) {
        renderVariantSelector(step);
        renderVariantFileList(step, "standard_apks", "apk", "Обязательные APK варианта «{name}»");
        renderVariantFileList(step, "standard_apks_optional", "apk", "Необязательные APK варианта «{name}» (техник выбирает сам)");
      } else {
        container.appendChild(el("span", {
          class: "field-label",
          text: "Обязательные APK (ставятся всегда, без чекбокса и права отключить)",
        }));
        container.appendChild(buildApkList(step.standard_apks, () => rerender()));
        container.appendChild(el("span", {
          class: "field-label", style: "margin-top: 8px",
          text: "Необязательные APK (техник выбирает сам при установке)",
        }));
        container.appendChild(buildApkList(step.standard_apks_optional, () => rerender()));
      }
    }

    function renderVariantToggle(step, singleField, warnText) {
      const row = el("label", { class: "row", style: "margin-bottom: 8px" });
      const checkbox = el("input", { type: "checkbox" });
      checkbox.checked = step.variants.length > 0;
      checkbox.addEventListener("change", async () => {
        if (checkbox.checked && !step.variants.length) {
          const variant = { name: "Вариант 1", usb_files: [], standard_apks: [], standard_apks_optional: [] };
          if (singleField === "usb_files") {
            variant.usb_files = step.usb_files;
          } else if (singleField === "standard_apks") {
            variant.standard_apks = step.standard_apks;
            variant.standard_apks_optional = step.standard_apks_optional;
            step.standard_apks_optional = [];
          }
          step.variants = [variant];
          step[singleField] = [];
        } else if (!checkbox.checked && step.variants.length) {
          if (!(await window.confirmDialog(`Убрать варианты и вернуться к одному набору файлов? ${warnText}`))) {
            checkbox.checked = true;
            return;
          }
          step.variants = [];
        }
        editingVariantIndex = 0;
        rerender();
      });
      row.appendChild(checkbox);
      row.appendChild(document.createTextNode("Несколько вариантов (например Full/Lite) — техник выбирает при установке"));
      container.appendChild(row);
    }

    function renderVariantSelector(step) {
      if (editingVariantIndex >= step.variants.length) editingVariantIndex = 0;
      const row = el("div", { class: "row", style: "margin-bottom: 4px" });
      const select = el("select", {}, step.variants.map((v, i) => el("option", { value: i, text: v.name, selected: i === editingVariantIndex ? "" : null })));
      select.addEventListener("change", () => { editingVariantIndex = Number(select.value); rerender(); });
      row.appendChild(select);
      row.appendChild(el("button", { text: "Добавить вариант", onclick: () => addVariant(step) }));
      row.appendChild(el("button", { text: "Переименовать", onclick: () => renameVariant(step) }));
      row.appendChild(el("button", { class: "danger", text: "Удалить вариант", onclick: () => removeVariant(step) }));
      container.appendChild(row);
    }

    // Список файлов текущего выбранного варианта для одного поля (usb_files
    // ИЛИ standard_apks) — вызывается отдельно для каждого поля, которое
    // нужно показать для варианта (см. renderUsbFields — у usb-варианта их
    // два: файлы флешки и APK), renderVariantSelector рисуется один раз.
    function renderVariantFileList(step, field, pickKind, headingTpl) {
      const variant = step.variants[editingVariantIndex];
      container.appendChild(el("span", { class: "field-label", text: headingTpl.replace("{name}", variant.name) }));
      container.appendChild(pickKind === "apk"
        ? buildApkList(variant[field], () => rerender())
        : buildFileList(variant[field], pickKind, true, () => rerender()));
    }

    async function addVariant(step) {
      const name = (await window.promptDialog("Название варианта (например Full):"))?.trim();
      if (!name) return;
      if (step.variants.some((v) => v.name === name)) {
        await window.notice("Название должно быть непустым и уникальным.");
        return;
      }
      step.variants.push({ name, usb_files: [], standard_apks: [], standard_apks_optional: [] });
      editingVariantIndex = step.variants.length - 1;
      rerender();
    }

    async function renameVariant(step) {
      const variant = step.variants[editingVariantIndex];
      const name = (await window.promptDialog("Новое название варианта:", { initialValue: variant.name }))?.trim();
      if (!name) return;
      if (step.variants.some((v) => v !== variant && v.name === name)) {
        await window.notice("Название должно быть непустым и уникальным.");
        return;
      }
      variant.name = name;
      rerender();
    }

    async function removeVariant(step) {
      if (step.variants.length <= 1) {
        await window.notice('Должен остаться хотя бы один вариант (или уберите галочку «Несколько вариантов»).');
        return;
      }
      step.variants.splice(editingVariantIndex, 1);
      editingVariantIndex = Math.max(0, editingVariantIndex - 1);
      rerender();
    }

    // -- exe --------------------------------------------------------------
    function renderExeFields(step) {
      container.appendChild(el("p", {
        class: "app-desc",
        text: "Готовый установщик от производителя — пользователь просто запустит его и завершит установку сам (для машин, для которых нет доступа к исходным скриптам/инструкциям).",
      }));
      const row = el("div", { class: "row" });
      row.appendChild(el("button", {
        text: "Выбрать .exe файл...",
        onclick: async () => {
          const picked = await window.pywebview.api.car_pick_files("exe", false);
          if (!picked.length) return;
          step.exe_file = picked[0];
          rerender();
        },
      }));
      row.appendChild(el("button", { class: "danger", text: "Убрать", onclick: () => { step.exe_file = null; rerender(); } }));
      container.appendChild(row);
      container.appendChild(el("p", { class: "app-desc", text: step.exe_file ? step.exe_file.name : "(не выбран)" }));
    }

    // -- check --------------------------------------------------------------
    function renderCheckFields(step) {
      container.appendChild(el("p", {
        class: "app-desc",
        text: "Техник сам сверяется с магнитолой (версия аппаратного обеспечения, прошивки и т.п.) и выбирает подходящий вариант из списка ниже во время установки — опишите, как её проверить, в поле «Описание» выше.",
      }));
      if (!hideCheckVarField) {
        container.appendChild(el("span", { class: "field-label", text: "Имя переменной (короткое, латиницей, например hw_version)" }));
        const varInput = el("input", { type: "text", style: "margin-bottom: 10px" });
        varInput.value = step.check_var;
        varInput.addEventListener("input", () => { step.check_var = varInput.value.trim(); });
        container.appendChild(varInput);
      }

      container.appendChild(el("span", { class: "field-label", text: "Варианты выбора" }));
      const optionsWrap = el("div");
      container.appendChild(optionsWrap);
      renderCheckOptions(step, optionsWrap);

      // CSS Grid (1fr/auto), а не flex:1 в .row — на это поле не подействовал
      // ни flex:1, ни min-width:0 (см. правку .row > input в tokens.css),
      // сообщено пользователем как "поле узкое, а уже добавленный вариант
      // widе" — grid-колонка 1fr однозначно отдаёт полю всё свободное место,
      // без каких-либо допущений про flex-basis/min-width.
      const addRow = el("div", { style: "display: grid; grid-template-columns: 1fr auto; gap: 6px; margin-top: 4px" });
      const newOptionInput = el("input", { type: "text", placeholder: "Новый вариант" });
      const addOption = () => {
        const value = newOptionInput.value.trim();
        if (!value) return;
        if (step.check_options.includes(value)) {
          window.notice("Такой вариант уже есть.");
          return;
        }
        step.check_options.push(value);
        newOptionInput.value = "";
        rerender();
      };
      newOptionInput.addEventListener("keydown", (e) => { if (e.key === "Enter") addOption(); });
      addRow.appendChild(newOptionInput);
      addRow.appendChild(el("button", { text: "Добавить вариант", onclick: addOption }));
      container.appendChild(addRow);
    }

    function renderCheckOptions(step, optionsContainer) {
      clear(optionsContainer);
      if (!step.check_options.length) {
        optionsContainer.appendChild(el("p", { class: "app-desc", text: "Вариантов пока нет — добавьте хотя бы один ниже." }));
        return;
      }
      step.check_options.forEach((option, i) => {
        optionsContainer.appendChild(el("div", { class: "option-card" }, [
          el("span", { text: option }),
          el("button", {
            class: "danger icon-btn", text: "✕",
            onclick: () => { step.check_options.splice(i, 1); rerender(); },
          }),
        ]));
      });
    }

    // -- instruction ------------------------------------------------------
    function renderInstructionFields(step) {
      container.appendChild(el("p", {
        class: "app-desc",
        text: "Отдельная часть инструкции — заголовки, шаги, важные плашки, фото. Покажется технику отдельной страницей на этом месте в последовательности этапов (а не только один раз в начале).",
      }));
      container.appendChild(el("button", {
        class: "accent",
        text: "Написать инструкцию...",
        onclick: () => window.instructionEditor.open(
          step.instruction_blocks.length ? step.instruction_blocks : null,
          (blocks) => { step.instruction_blocks = blocks; rerender(); }
        ),
      }));
      if (!step.instruction_blocks.length) {
        // Пустой шаблон с маркой/моделью в заголовке — как instruction_html.default_blocks в старом коде.
        const { brand, model } = getBrandModel();
        window.pywebview.api.car_instruction_default_blocks(brand, model).then((blocks) => {
          step.instruction_blocks = blocks;
        });
      }
      container.appendChild(el("p", {
        class: "app-desc",
        text: step.instruction_blocks.length ? `Готово (${step.instruction_blocks.length} блок(ов))` : "(пока не написана)",
      }));
    }

    // -- условная видимость (для любого типа этапа) ------------------------
    function availableCheckVars(excludeStep) {
      const names = [];
      for (const step of getSteps()) {
        if (step === excludeStep) continue;
        if (step.type === "check" && step.check_var && !names.includes(step.check_var)) names.push(step.check_var);
      }
      return names;
    }

    function renderConditionFields(step) {
      const ALWAYS = "(всегда)";
      container.appendChild(el("span", { class: "field-label", text: "Показывать этап только если (необязательно)", style: "margin-top: 12px" }));
      const values = [ALWAYS, ...availableCheckVars(step)];
      const current = step.condition_var || ALWAYS;
      if (!values.includes(current)) values.push(current);
      const select = el("select", { style: "margin-bottom: 4px" }, values.map((v) => el("option", { value: v, text: v, selected: v === current ? "" : null })));
      const valuesWrap = el("div");

      select.addEventListener("change", () => {
        step.condition_var = select.value === ALWAYS ? "" : select.value;
        step.condition_values = [];
        rerender();
      });
      container.appendChild(select);
      container.appendChild(el("span", { class: "field-label", text: "Значения, при которых этап нужен" }));
      container.appendChild(valuesWrap);
      renderConditionValues(step, valuesWrap);
    }

    function renderConditionValues(step, valuesContainer) {
      clear(valuesContainer);
      if (!step.condition_var) {
        valuesContainer.appendChild(el("p", { class: "app-desc", text: "Выберите переменную выше" }));
        return;
      }
      const owner = getSteps().find((s) => s.type === "check" && s.check_var === step.condition_var);
      const options = owner ? owner.check_options : [];
      if (!options.length) {
        valuesContainer.appendChild(el("p", { class: "app-desc", text: "У этого этапа-переменной пока нет вариантов выбора." }));
        return;
      }
      for (const option of options) {
        const checkbox = el("input", { type: "checkbox" });
        checkbox.checked = step.condition_values.includes(option);
        checkbox.addEventListener("change", () => {
          if (checkbox.checked) step.condition_values.push(option);
          else step.condition_values = step.condition_values.filter((v) => v !== option);
          rerender();
        });
        valuesContainer.appendChild(el("label", { class: "row" }, [checkbox, option]));
      }
    }

    const typeBuilders = {
      adb: renderAdbFields, usb: renderUsbFields, apps: renderAppsFields,
      exe: renderExeFields, check: renderCheckFields, instruction: renderInstructionFields,
      uart: renderUartFields, telnet: renderTelnetFields, actions: renderActionsFields,
    };

    function renderTypeFields(step) {
      if (typeBuilders[step.type]) {
        typeBuilders[step.type](step);
      } else if (step.type === "manual") {
        container.appendChild(el("p", {
          class: "app-desc",
          text: "Для «Ручного шага» дополнительных полей нет — пользователь просто прочитает описание выше и отметит этап выполненным.",
        }));
      } else if (step.type === "qr_adb") {
        container.appendChild(el("p", {
          class: "app-desc",
          text: "Дополнительных полей нет — этап самостоятельно находит флешку с папкой logs_* и считает пароль (см. app/qr_adb_password.py). Магнитола на этом этапе не обязана быть подключена.",
        }));
      }
    }

    function resetVariantIndex() {
      editingVariantIndex = 0;
    }

    return { renderTypeFields, renderConditionFields, resetVariantIndex };
  }

  window.carStepFields = { createStepFieldsController };
})();
