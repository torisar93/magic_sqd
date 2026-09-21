// Портировано с app/stage_wizard.py — постраничный мастер "инструкция +
// этапы установки" в одном виджете. Разница с прежним интерфейсом: условная
// видимость этапов и HTML-инструкция остались как были, а сам рендеринг —
// обычные DOM-узлы/CSS вместо CTk-виджетов и tkinterweb HtmlFrame (см. план
// миграции). Один экземпляр состояния на выбранную модель — open() полностью
// сбрасывает его, как раньше пересоздавался StageWizard при смене модели.
(function () {
  const { el, clear } = window.dom;

  const TYPE_LABELS = {
    usb: "USB-флешка", adb: "ADB", manual: "Вручную на магнитоле",
    apps: "Установка приложений", exe: "Готовый установщик (.exe)",
    check: "Проверка/выбор", instruction: "Инструкция", uart: "UART", telnet: "Telnet",
    actions: "ADB-команды", qr_adb: "Пароль ADB по QR-коду",
  };

  // Каким типам этапов реально нужен ADB — верхний бар подключения (см.
  // buildTransportBar) показывается только для них, а не постоянно на весь
  // мастер (раньше "Подключить Wi-Fi" висела внизу окна вообще всегда, не
  // привязана к этапу) — тот же приём, что уже есть в мобильном приложении
  // (android/app/src/main/assets/js/app.js: ADB_STAGE_TYPES, #adb-bar над
  // #wizard-content).
  const TRANSPORT_STAGE_TYPES = new Set(["adb", "apps", "actions"]);

  let containerEl, contentEl, navBackBtn, navNextBtn, navLabelEl, navVideoBtn;
  let mounted = false;
  let renderRevision=0;
  let logFn = () => {};

  let model = null;
  let stages = [];
  let loadError = null;
  let loadErrorNeedsUpdate = false;
  // Показываем один раз за сессию программы, не при каждом открытии модели
  // (см. install_api.py: write_permission_warning) — иначе техник видел бы
  // одно и то же модальное окно на каждой второй открытой модели.
  let writePermissionWarningShown = false;
  let hasIntro = false;
  let currentIndex = 0;
  const done = new Set();
  let chosenVariants = {};
  let appSelection = {};
  const sectionCollapsed = {};
  // Свои APK, добавленные пользователем прямо на этапе (см. buildAppsTree/
  // pickPersonalApks ниже) — произвольные файлы с диска, а не из apk/, живут
  // только в памяти этого сеанса работы с моделью (не сохраняются, не
  // публикуются никуда) — appSelection всё равно собирает финальный список
  // на установку по пути файла, откуда бы он ни был (см. selectedApkPaths).
  let personalApks = [];
  let nextAction = () => advanceAfter(currentIndex);
  let sharedApksPromise = null;
  let runnerBusy = false;
  // Окно «этап выполняется → итог» (js/components/stage_run.js) — одно на все
  // типы этапов с процессом. Перерисовку страницы/переход к следующему этапу
  // (afterRunClose) выполняем только когда техник закрыл окно с итогом, иначе
  // render() сотрёт страницу прямо под ним.
  let activeRun = null;
  let afterRunClose = null;
  let activeAppPicker = null;
  let activeCommand = null;
  const commandResults = new Map();
  let modelWifiPort = 5555;
  // Wi-Fi-модель (см. install_api.load_stages: "wifi"): файлы adb/actions-этапов докачиваем при показе
  // этапа, пока интернет ещё есть (см. prefetchStageFiles), и запускаем с prefetched=true.
  let modelWifi = false;
  const prefetchedStages = new Set();
  let appDescriptionTooltip = null;
  // Показываем "Готово!"+Boosty один раз за сеанс работы с моделью — сброс
  // при каждом open() (см. android app.js: installCompletedShown, тот же
  // приём).
  let installCompletedShown = false;
  // Этапы, которые в этом сеансе завершились ошибкой и с тех пор не были
  // пройдены успешно (индекс этапа -> заголовок). Нужен, чтобы в конце мастера
  // не писать «Все этапы установки выполнены», если обязательное приложение
  // так и не встало, а техник просто нажал «Далее» (логи #361/#362/#365:
  // Simple Control не установился, а лог заканчивался «успешно»). Этапы типа
  // "actions" не считаем — их кнопки необязательны, ошибка одной не срывает
  // установку. Сбрасывается при каждом open().
  const failedStages = new Map();

  // Автоматический лог одной попытки установки (см. server/backend.py:
  // POST /install_log) — весь текст, что видел техник в log-панели за этот
  // сеанс работы с моделью, плюс отдельный флаг "было ли что-то, кроме
  // чтения инструкции" (sessionHasActivity — взводится событием install_log
  // БЕЗ пометки passive, т.е. реальным действием бэкенда: ADB/установка/
  // действия. Фоновая докачка контента для показа экрана — превью
  // инструкции при открытии модели, список приложений этапа apps, см.
  // install_api.py: _on_log_passive — приходит тем же событием, но с
  // event.passive=true, и активность не взводит: раньше взводила (реальный
  // случай — открытие модели само по себе слало на сервер логи вида
  // "Скачано файлов (.../instruction_1): 3." без единого реального
  // действия техника, чистый шум). Сбрасывается в open() на каждую новую
  // модель; предыдущая сессия (если была активность и её ещё не отправили)
  // при этом флашится как "брошена" — см. flush().
  let sessionLog = [];
  let sessionHasActivity = false;
  let sessionSent = false;
  // Токен текущей сессии (см. open() — приходит из install_load_stages) для
  // прочного журнала на диске (app/pending_install_logs.py): install_log_append
  // на каждую строку лога, install_log_send с этим же токеном при завершении.
  let sessionToken = "";

  function log(message) {
    sessionLog.push(message);
    logFn(`[${model.display_label}] ${message}`);
    // Дозапись на диск (см. app/web/bridge.py: install_log_append) — переживает
    // и обрыв сети, и вылет процесса; не ждём промис (best-effort, как и
    // остальная телеметрия здесь).
    window.pywebview.api.install_log_append(sessionToken, message, sessionHasActivity).catch(() => {});
  }

  // success=true — все этапы пройдены (см. advanceAfter); false — техник
  // явно ушёл из мастера (см. app.js: returnToCatalog) или открыл другую
  // модель, не долистав эту. Не шлём, если реальной активности не было
  // (открыл/пролистал инструкцию и ушёл) — незачем копить мусор.
  function flushSessionLog(success) {
    if (sessionSent || !sessionHasActivity || !sessionLog.length) return;
    sessionSent = true;
    window.pywebview.api.install_log_send(
      model.brand || "", model.display_label || model.name || "", model.modification || "",
      success, sessionLog.join("\n"), sessionToken,
    );
  }

  // -- инициализация экрана (один раз, до выбора модели) ------------------
  function init(container, logCallback) {
    containerEl = container;
    logFn = logCallback;

    // Глобальные события фонового InstallRunner — на всё приложение, а не
    // при каждом open()/render(): воркер-поток на стороне Python один на
    // всю программу (см. app/web/api/install_api.py), слушатель тоже нужен
    // только один, иначе он задваивался бы при каждом выборе модели.
    window.events.on("install_log", (event) => {
      if (!event.passive) sessionHasActivity = true;
      log(event.text);
      if (!event.passive && activeRun) activeRun.detail(event.text);
    });
    window.events.on("install_finished", onInstallFinished);
    window.events.on("ask_input", (event) => showAskInputDialog(event));
    window.events.on("sync_progress", (event) => updateSyncProgress(
      event.done, event.total, event.files_done, event.files_total,
    ));
  }

  // -- прогресс синхронизации файлов модели перед показом инструкции -------
  // (см. app/content_sync.py: sync_tree on_progress) — раньше вместо этого
  // в лог сыпалась строка "Скачиваю <файл>..." на КАЖДЫЙ файл модели (сотни
  // на первой синхронизации), теперь — обычный прогресс-бар с "N из M".
  function updateSyncProgress(done, total, filesDone, filesTotal) {
    const bar = document.getElementById("main-progress");
    const fill = document.getElementById("main-progress-fill");
    const label = document.getElementById("main-progress-label");
    if (total <= 0) {
      bar.style.display = "none";
      label.style.display = "none";
      return;
    }
    const percent = Math.min(100, Math.round((done / total) * 100));
    // Процент отображается одной строкой ВНУТРИ лога ниже. Старый бар и
    // подпись над логом больше не используем, чтобы не было двух индикаторов.
    bar.style.display = "none";
    label.style.display = "none";

    // Одна живая строка внутри самого лога вместо десятков сообщений о
    // каждом файле. appendChild переносит её в конец после новых записей,
    // поэтому актуальный процент всегда остаётся виден.
    const logPanel = document.getElementById("log-panel");
    let progressLine = document.getElementById("sync-progress-log-line");
    if (!progressLine) {
      progressLine = document.createElement("div");
      progressLine.id = "sync-progress-log-line";
      progressLine.className = "log-line log-line-progress";
    }
    const width = 16;
    const filled = Math.round((percent / 100) * width);
    const suffix = Number.isFinite(filesDone) && Number.isFinite(filesTotal)
      ? ` · ${filesDone} из ${filesTotal} файлов`
      : "";
    progressLine.textContent = `[${"#".repeat(filled)}${".".repeat(width - filled)}] ${percent}% скачано${suffix}`;
    logPanel.appendChild(progressLine);
    logPanel.scrollTop = logPanel.scrollHeight;
  }

  // -- построение разметки (один раз, лениво — при первом open(), чтобы
  // плейсхолдер "выберите марку и модель" не пропадал раньше времени) -----
  function ensureMounted() {
    if (mounted) return;
    mounted = true;
    const container = containerEl;
    container.innerHTML = `
      <div class="wizard">
        <div class="wizard-content" id="wizard-content"></div>
        <div class="wizard-nav">
          <button id="wizard-back">Назад</button>
          <button id="wizard-video" style="display: none">Смотреть видео</button>
          <span class="page-label" id="wizard-page-label"></span>
          <button id="wizard-next" class="accent">Далее</button>
        </div>
      </div>
      <dialog id="ask-input-dialog">
        <form method="dialog" id="ask-input-form">
          <p id="ask-input-prompt"></p>
          <select id="ask-input-choices" style="width: 100%; margin-bottom: 10px; display: none"></select>
          <input type="text" id="ask-input-value" style="width: 100%; margin-bottom: 10px" />
          <div style="display: flex; justify-content: flex-end; gap: 6px">
            <button type="button" id="ask-input-cancel">Отмена</button>
            <button type="submit" class="accent">OK</button>
          </div>
        </form>
      </dialog>
    `;
    contentEl = container.querySelector("#wizard-content");
    navBackBtn = container.querySelector("#wizard-back");
    navNextBtn = container.querySelector("#wizard-next");
    navLabelEl = container.querySelector("#wizard-page-label");
    navVideoBtn = container.querySelector("#wizard-video");
    navBackBtn.replaceChildren(UsbUI.icon('back'), el('span', {text:'Назад'}));
    navBackBtn.addEventListener("click", goBack);
    navNextBtn.addEventListener("click", () => {if(!runnerBusy)nextAction();});
    navVideoBtn.addEventListener("click", async () => {
      const stage = currentIndex >= 0 ? stages[currentIndex] : null;
      if (!stage || !stage.video_path || navVideoBtn.disabled) return;
      // Файл может докачиваться с сервера (до 150 МБ) — на это время кнопка занята, а неудача больше
      // не молчит (раньше результат open_video вообще не читался).
      navVideoBtn.disabled = true;
      try {
        const result = await window.pywebview.api.install_open_video(stage.video_path);
        if (result && result.ok === false) {
          window.notice(result.error || "Не удалось открыть видео.", { title: "Видео", danger: true });
        }
      } catch (error) {
        window.notice(`Не удалось открыть видео: ${error.message || error}`, { title: "Видео", danger: true });
      } finally {
        navVideoBtn.disabled = false;
      }
    });
    setupAskInputDialog(container);
  }

  // Значение <option>, ведущее к ручному вводу — рядом со списком найденных
  // сканом вариантов (см. ctx.ask_choice в install_context.py) всегда есть
  // возможность вписать своё, на случай если нужного нет в списке.
  const MANUAL_CHOICE_VALUE = "__manual__";

  function setupAskInputDialog(container) {
    const dialog = container.querySelector("#ask-input-dialog");
    const form = container.querySelector("#ask-input-form");
    const cancelBtn = container.querySelector("#ask-input-cancel");
    const select = container.querySelector("#ask-input-choices");
    const valueInput = container.querySelector("#ask-input-value");
    select.addEventListener("change", () => {
      const manual = select.value === MANUAL_CHOICE_VALUE;
      valueInput.style.display = manual ? "" : "none";
      if (manual) valueInput.focus();
    });
    form.addEventListener("submit", (e) => {
      e.preventDefault();
      const usingChoices = select.style.display !== "none";
      const value = (usingChoices && select.value !== MANUAL_CHOICE_VALUE)
        ? select.value
        : valueInput.value;
      const reqId = dialog.dataset.reqId;
      dialog.close();
      window.pywebview.api.install_answer_input(reqId, value || null);
    });
    cancelBtn.addEventListener("click", () => {
      const reqId = dialog.dataset.reqId;
      dialog.close();
      window.pywebview.api.install_answer_input(reqId, null);
    });
  }

  function showAskInputDialog(event) {
    const dialog = document.getElementById("ask-input-dialog");
    dialog.dataset.reqId = event.id;
    document.getElementById("ask-input-prompt").textContent = event.prompt;
    const select = document.getElementById("ask-input-choices");
    const valueInput = document.getElementById("ask-input-value");
    valueInput.value = "";
    const choices = event.choices || [];
    if (choices.length) {
      select.innerHTML = "";
      for (const choice of choices) {
        const opt = document.createElement("option");
        opt.value = choice;
        opt.textContent = choice;
        select.appendChild(opt);
      }
      if (event.allow_manual !== false) {
        const manualOpt = document.createElement("option");
        manualOpt.value = MANUAL_CHOICE_VALUE;
        manualOpt.textContent = "Ввести вручную...";
        select.appendChild(manualOpt);
      }
      select.style.display = "";
      select.value = choices[0];
      valueInput.style.display = "none";
    } else {
      select.style.display = "none";
      select.innerHTML = "";
      valueInput.style.display = "";
    }
    dialog.showModal();
  }

  // Закрыть окно этапа с итогом; последующая перерисовка/переход — в after()
  // (запускается при закрытии окна техником). Без открытого окна (этап
  // запущен не через него) — сразу.
  function finishRun(outcome, after) {
    const run = activeRun;
    if (!run || run.finished) { activeRun = null; after(); return; }
    afterRunClose = after;
    run.finish(outcome);
  }

  function onRunClosed() {
    activeRun = null;
    const after = afterRunClose;
    afterRunClose = null;
    if (after) after();
  }

  function openStageRun(options) {
    activeRun = window.StageRun.open({ ...options, onClose: onRunClosed });
    return activeRun;
  }

  function onInstallFinished(event) {
    runnerBusy = false;
    log(event.message);
    trackStageResult(event);
    finishRun({ success: !!event.success, message: event.message }, () => afterStageFinished(event));
  }

  function trackStageResult(event) {
    const stage = stages[event.stage_index];
    if (!stage || stage.type === "actions") return;
    if (event.success) failedStages.delete(event.stage_index);
    else failedStages.set(event.stage_index, stage.title || `этап ${event.stage_index + 1}`);
  }

  function afterStageFinished(event) {
    contentEl.classList.remove('installing-apps');
    contentEl.parentElement.classList.remove('installing-apps');
    if (event.stage_index !== currentIndex) return; // ушли с этой страницы, пока этап работал в фоне
    // "actions" — кнопки необязательны и нажимаются в любом порядке/сколько
    // угодно раз, поэтому в отличие от остальных типов этапов ни успех, ни
    // ошибка одного действия не переводят на следующий этап сами по себе —
    // технику решает об этом сам, нажав "Далее".
    if (stages[currentIndex] && stages[currentIndex].type === "actions") {
      if (activeCommand?.stageIndex === currentIndex) {
        commandResults.set(activeCommand.key, {success:!!event.success, message:event.message});
        activeCommand = null;
      }
      render();
      return;
    }
    if (event.success) {
      const wasLast=stages[currentIndex]?.next==null;
      advanceAfter(currentIndex);
      if(wasLast){
        document.querySelector('.stage-primary-actions')?.remove();clear(contentEl);
        contentEl.append(el('h1',{class:'workflow-title',text:failedStages.size?'Установка завершена с ошибками':'Установка завершена'}),el('p',{text:failedStages.size?`Этот этап выполнен, но не выполнено: ${[...failedStages.values()].join(', ')}. Вернитесь к ним и повторите.`:'Этап выполнен успешно. Можно вернуться к выбору автомобиля.'}));
        navNextBtn.style.display='';navNextBtn.textContent='К моделям';nextAction=()=>returnToCatalog();
      }
    } else {
      render(); // перерисовать текущий этап заново — Start/Stop вернутся в состояние "не выполняется"
    }
  }

  // -- открытие модели --------------------------------------------------
  async function open(selectedModel) {
    ensureMounted();
    if (activeRun) { activeRun.dispose(); activeRun = null; afterRunClose = null; }
    // Модель сменили, не долистав предыдущую (см. app.js: returnToCatalog
    // — обычный уход обрабатывается ТАМ, до вызова open() заново; этот флаш
    // — на случай прямого перехода к другой модели из каталога/поиска, минуя
    // "Назад к каталогу") — если там была реальная активность, шлём её как
    // брошенную, прежде чем затереть буфер под новую модель.
    if (model) flushSessionLog(false);
    sessionLog = [];
    sessionHasActivity = false;
    sessionSent = false;
    model = selectedModel;
    activeCommand = null; commandResults.clear();
    done.clear();
    historyStack.length = 0;
    chosenVariants = {};
    appSelection = {};
    personalApks = [];
    hasIntro = model.no_instruction;
    loadError = null;
    loadErrorNeedsUpdate = false;
    stages = [];
    modelWifiPort = 5555;
    installCompletedShown = false;
    failedStages.clear();

    const result = await window.pywebview.api.install_load_stages(model.key);
    // Токен прочного журнала сессии на диске (см. app/pending_install_logs.py,
    // install_api.py:load_stages) — ДО версии-шапки ниже, а не после: append_current
    // на несовпадающий/пустой токен молча ничего не пишет (защита от гонки
    // потоков pywebview, см. докстринг pending_install_logs.py), так что шапка
    // без свежего токена просто пропала бы из прочного журнала.
    sessionToken = result.install_log_token || "";
    // Даёт events.js прочно дописать в этот же журнал JS-ошибку, если она
    // случится прямо во время установки (см. events.js) — иначе падение на
    // чистом JS осталось бы только в локальном js_errors.log, а не ушло бы
    // на сервер вместе с остальным логом сессии.
    window.__installLogSessionToken = sessionToken;
    // Диагностическая шапка лога (см. app.js: window.appInfo) — версия и
    // сборка программы должны быть видны прямо в присланном логе установки,
    // а не только в самой программе технику: разбор без этого начинается с
    // вопроса "а какая у него вообще версия" (реальный случай, Volga C50).
    // log(), не событие "install_log" — не должно само по себе взводить
    // sessionHasActivity (см. её докстring выше), иначе КАЖДОЕ открытие
    // модели снова стало бы "реальной активностью", ту же ошибку недавно
    // уже чинили (v0.9.7).
    if (window.appInfo) {
      const build = window.appInfo.is_win7 ? "Win7/x86" : "x64";
      const warn = window.appInfo.under_program_files ? " · ВНИМАНИЕ: установлено в Program Files" : "";
      log(`Magic SQD v${window.appInfo.app_version} (${build}) · client=${window.appInfo.client_id}${warn}`);
    }
    if (result.error) {
      loadError = result.error;
      loadErrorNeedsUpdate = Boolean(result.needs_update);
    } else {
      stages = result.stages;
      modelWifiPort = result.wifi_port || 5555;
      modelWifi = !!result.wifi;
      prefetchedStages.clear();
      await initAppSelectionDefaults();
      if (result.write_permission_warning && !writePermissionWarningShown) {
        writePermissionWarningShown = true;
        window.notice(
          "Программа установлена в защищённую системную папку (например, Program Files) " +
          "и не может сама обновлять содержимое моделей — инструкции и список машин " +
          "останутся устаревшими. Переустановите программу: в установщике НЕ выбирайте " +
          "«для всех пользователей» и не запускайте его через «Запуск от имени администратора».",
          { title: "Не удаётся обновить содержимое", danger: true },
        );
      }
    }

    currentIndex = hasIntro ? -1 : 0;
    // Сброс на дефолтный обработчик — иначе, если до этого была открыта
    // другая модель и текник дошёл до этапа "check" (см. renderCheckStage
    // ниже, единственный, кто переопределяет nextAction на свой
    // stage.index), этот чужой nextAction оставался бы активным и здесь:
    // "Далее" на первом же этапе НОВОЙ модели срабатывал бы с индексом
    // СТАРОЙ модели — advanceAfter(старый_index) на новом, часто более
    // коротком stages, сразу считал бы все этапы пройденными. show() ниже
    // уже делает такой сброс при обычной навигации внутри одной модели —
    // здесь то же самое нужно при смене самой модели.
    nextAction = () => advanceAfter(currentIndex);
    render();
  }

  async function initAppSelectionDefaults() {
    for (const stage of stages) {
      if (stage.type !== "apps") continue;
      const standard = await window.pywebview.api.install_standard_apks(model.key, stage.index, null);
      // required — не чекбокс вовсе (см. buildAppRow), но appSelection всё
      // равно держит их как true — этим же словарём собирается финальный
      // список APK на установку (см. selectedApkPaths), не отдельным путём.
      for (const apk of standard.required) appSelection[apk.path] = true;
      for (const apk of standard.optional) {
        if (!(apk.path in appSelection)) appSelection[apk.path] = false;
      }
    }
    for (const apk of await sharedApks()) {
      if (!(apk.path in appSelection)) appSelection[apk.path] = false;
    }
  }

  function sharedApks() {
    if (!sharedApksPromise) sharedApksPromise = window.pywebview.api.scanner_list_apks();
    return sharedApksPromise;
  }

  // -- навигация ----------------------------------------------------------
  // Граф исполнения — полностью явный (см. car_generator.py: StepSpec.next/
  // next_options): каждый этап хранит id следующего этапа (или, для
  // "check", отдельный id на каждый вариант) — никаких отдельных "условий"
  // и переменных здесь больше нет, "куда дальше" известно СРАЗУ в момент
  // выбора, а не вычисляется заново разбором накопленных где-то ответов.
  // historyStack — реально пройденный путь (что конкретно привело сюда),
  // а не "предыдущий индекс по порядку массива" — раньше "Назад" был
  // способен привести на этап, к которому текущий путь на самом деле не
  // имеет отношения (см. отчёт пользователя про несброшенные vars).
  const historyStack = [];

  function indexById(id) {
    return stages.findIndex((s) => s.id === id);
  }

  function goBack() {
    if(runnerBusy)return;
    if (!historyStack.length) return;
    show(historyStack.pop());
  }

  // optionIndex — только для стадии типа "check": какой вариант выбрал
  // техник (см. renderCheckStage) — next для остальных типов уже известен
  // без выбора.
  function advanceAfter(index, optionIndex) {
    if (index >= 0) done.add(index);
    if (index === -1) {
      // Интро (см. hasIntro) -> первый реальный этап — steps[0] всегда
      // точка входа, по графу переходить ещё не от чего.
      if (!stages.length) { renderNav(); return; }
      historyStack.push(-1);
      show(0);
      return;
    }
    const stage = stages[index];
    const nextId = stage.type === "check" ? (stage.next_options || [])[optionIndex] : stage.next;
    if (nextId == null) {
      renderNav();
      if (stages.length) {
        if (failedStages.size) {
          // Честный итог вместо «выполнены» + окна «Готово!»: часть этапов
          // не прошла (см. failedStages выше) — лог всё равно уходит на сервер.
          log(`Установка завершена с ошибками — не выполнено: ${[...failedStages.values()].join(", ")}.`);
          flushSessionLog(true);
        } else {
          log("Все этапы установки выполнены.");
          flushSessionLog(true);
          if (!installCompletedShown) {
            installCompletedShown = true;
            window.boostyDialogs.showCompletionDialog();
          }
        }
      }
      return;
    }
    const nextIndex = indexById(nextId);
    if (nextIndex === -1) {
      // Ссылка на несуществующий id — не должно происходить у корректно
      // сохранённой модели, но лучше тихо завершить установку, чем упасть.
      renderNav();
      return;
    }
    historyStack.push(index);
    show(nextIndex);
  }

  function show(index) {
    if(runnerBusy)return;
    currentIndex = index;
    nextAction = () => advanceAfter(currentIndex);
    render();
  }

  // -- рендеринг ------------------------------------------------------
  function render() {
    renderRevision++;
    contentEl.classList.remove('installing-apps');
    contentEl.parentElement.classList.remove('installing-apps');
    clear(contentEl);
    document.querySelector('.stage-primary-actions')?.remove();
    contentEl.dataset.stageType=stages[currentIndex]?.type||'instruction';
    if (loadError) {
      contentEl.appendChild(el("div", { class: "callout danger", text: loadError }));
      if (loadErrorNeedsUpdate) {
        contentEl.appendChild(el("button", {
          class: "accent",
          text: "Проверить обновления",
          onclick: async (event) => {
            event.currentTarget.disabled = true;
            const found = await checkForUpdate();
            if (!found) {
              await window.notice(
                "У вас уже установлена последняя доступная версия — подходящий "
                  + "релиз для этой модели ещё не вышел. Попробуйте позже.",
              );
            }
            event.currentTarget.disabled = false;
          },
        }));
      }
      renderNav();
      return;
    }
    if (currentIndex === -1) {
      renderIntroPage();
    } else if (stages.length === 0) {
      contentEl.appendChild(el("p", { class: "placeholder-text", text: "Для этой модели нет заданных этапов установки." }));
    } else {
      renderStagePage(stages[currentIndex]);
    }
    renderNav();
  }

  function renderNav() {
    navNextBtn.disabled = runnerBusy;
    navBackBtn.disabled = runnerBusy || !historyStack.length;
    navBackBtn.hidden = !historyStack.length;
    if (stages.length === 0) {
      navNextBtn.style.display = "none";
    } else {
      navNextBtn.style.display = "";
      // "check" — у разных вариантов может быть разное продолжение (или
      // вовсе никакого), заранее неизвестно, пока техник не выбрал —
      // всегда "Далее →", "Готово" тут не показываем. Клик по кнопке-
      // варианту продвигает сразу (см. renderCheckStage), а "Далее" —
      // на случай, когда техник и так знает, куда идти, и просто
      // пролистывает мастер, не глядя на сами варианты (по умолчанию
      // первый вариант — тот же выбор, что раньше был у select).
      const stage = currentIndex >= 0 ? stages[currentIndex] : null;
      const isLast = stage && stage.type !== "check" && stage.next == null;
      navNextBtn.replaceChildren(el('span',{text:isLast ? 'Готово' : 'Далее'}));
      if (!isLast) navNextBtn.append(window.AppIcons ? AppIcons.icon('chevron') : UsbUI.icon('chevron'));
      if (stage?.type === 'check' && stage.check_options?.length) navNextBtn.style.display = 'none';
    }
    if (!stages.length) navLabelEl.textContent = "";
    else if (currentIndex === -1) navLabelEl.textContent = "Инструкция";
    else navLabelEl.textContent = `Этап ${currentIndex + 1} из ${stages.length}`;

    const videoStage = currentIndex >= 0 ? stages[currentIndex] : null;
    if (videoStage && videoStage.video_path) {
      navVideoBtn.style.display = "";
      navVideoBtn.replaceChildren(window.AppIcons ? AppIcons.icon('play') : UsbUI.icon('play'), el('span',{text:videoStage.video_label || 'Смотреть видео'}));
    } else {
      navVideoBtn.style.display = "none";
    }
  }

  function renderIntroPage() {
    contentEl.appendChild(el("div", { class: "instruction-block" }, [
      el("div", { class: "plain-text" }, [
        el("p", { text: "Для этой машины пока нет известных способов установки." }),
        el("p", {
          style: "color: var(--text-dim)",
          text: "Если вы знаете рабочий способ получить доступ к ADB или поставить приложения — нажмите «Сообщить о проблеме» в углу и опишите его, мы добавим инструкцию.",
        }),
      ]),
    ]));
  }

  function renderStagePage(stage) {
    contentEl.appendChild(el('h1',{class:'workflow-title',text:stage.type==='apps'?'Приложения':['usb','qr_adb'].includes(stage.type)?'Подготовка флешки':stage.title||TYPE_LABELS[stage.type]||'Инструкция'}));
    if (stage.type === "instruction") {
      contentEl.appendChild(buildInstructionBlock(stage, true));
      return;
    }

    // Бар подключения (устройство/Wi-Fi) — ТОЛЬКО для этапов, которым он
    // реально нужен (см. TRANSPORT_STAGE_TYPES), и НАД остальным
    // содержимым этапа — раньше был общей строкой внизу окна на весь
    // мастер, независимо от текущего этапа.
    let getDevice = () => null;
    let transportCtl = null;
    if (TRANSPORT_STAGE_TYPES.has(stage.type)) {
      const transport = buildTransportBar(stage);
      contentEl.appendChild(transport.element);
      getDevice = transport.getDevice;
      transportCtl = transport;
    }

    if ((stage.instruction_html || stage.description) && !["qr_adb", "usb"].includes(stage.type)) {
      if(stage.type==='apps'){
        const help=el('details',{class:'lab-stage-help'},[el('summary',{text:'Инструкция к этапу'}),buildInstructionBlock(stage,false)]);contentEl.appendChild(help);
      }else contentEl.appendChild(buildInstructionBlock(stage, false));
    }

    const panel = buildActionPanel(stage.type);
    panel.dataset.stageIndex=stage.index;

    const builders = {
      check: renderCheckStage, apps: renderAppsStage, manual: renderManualStage,
      usb: renderUsbStage, exe: renderExeStage, adb: renderAdbStage, uart: renderUartStage,
      telnet: renderTelnetStage, actions: renderActionsStage, qr_adb: renderQrAdbStage,
    };
    (builders[stage.type] || (() => {}))(panel, stage, getDevice, transportCtl);
    contentEl.appendChild(panel);
  }

  // -- верхний бар подключения (устройство/Wi-Fi) --------------------------
  // Провод — выпадающий список "adb devices" (как раньше). Wi-Fi — тот же
  // коннект, что раньше жил в app.js:connectAdbWifi под нижней консолью:
  // сначала автоопределение IP по шлюзу, если не вышло — скан сети,
  // если и это не нашло — ручной ввод. apps-этап сам решает, что показывать
  // (stage.apps_connection: "wired"/"wifi"/"ask" — см. car_generator.py);
  // adb/actions всегда только провод (Wi-Fi для них решается на уровне
  // всей модели через spec.wifi, см. _with_connect в car_generator.py).
  function buildTransportBar(stage) {
    // "action-panel", не "card" — .card это display:flex;flex-direction:
    // column для панелей верхнего уровня самого окна (лог, инструкция), тут
    // ломало высоту/обтекание при вложении внутрь обычного потока этапа.
    // action-panel — просто закруглённый фон+паддинг, без flex-эффектов.
    const bar = el("div", { class: "action-panel transport-bar", style: "margin-bottom: 12px" });
    let deviceByLabel = {};
    let wifiSerial = null;

    const select = el("select", { style: "flex: 1" });
    const refreshBtn = el("button", { text: "Обновить" });
    async function refreshDevices() {
      const devices = await window.pywebview.api.install_list_devices();
      deviceByLabel = {};
      clear(select);
      for (const d of devices) {
        let label = d.serial;
        if (d.model) label += `  (${d.model})`;
        if (d.state !== "device") label += `  [${d.state}]`;
        deviceByLabel[label] = d.state === "device" ? d.serial : null;
        select.appendChild(el("option", { value: label, text: label }));
      }
    }
    refreshBtn.addEventListener("click", refreshDevices);
    const wiredRow = el("div", { class: "row" }, [select, refreshBtn]);

    const wifiStatus = el("span", { style: "color: var(--text-dim)", text: "Wi-Fi: не подключено" });
    // Порт САМОГО ЭТАПА (apps_wifi_port/actions_wifi_port, задаётся в
    // редакторе прямо на этапе — не общий на модель modelWifiPort, тот
    // остался только для легаси-типа "adb") — если известен заранее,
    // предзаполняем; если нет, поле пустое и техник вписывает порт сам,
    // увидев его на магнитоле.
    const stagePort = stage.type === "apps" ? stage.apps_wifi_port
      : stage.type === "actions" ? stage.actions_wifi_port : null;
    const portInput = el("input", {
      type: "text", style: "width: 60px",
      placeholder: stagePort == null ? String(modelWifiPort) : undefined,
    });
    portInput.value = stagePort != null ? String(stagePort) : "";
    const wifiConnectBtn = el("button", { text: "Подключить Wi-Fi" });
    // Wi-Fi ADB при установке приложений: сначала скачиваем, потом подключаемся (у компьютера в сети
    // магнитолы интернета обычно нет) — окно подключения открывается ИЗ запуска установки и возвращает
    // адрес подключённой магнитолы ("ip:порт") либо null, если окно закрыли (см. buildStartStopButtons).
    function askWifi(help) {
      return new Promise((resolve) => {
        let settled = false;
        const settle = (value) => { if (!settled) { settled = true; resolve(value); } };
        LabUI.connection({port:Number(portInput.value)||modelWifiPort, help,
          host: wifiSerial ? wifiSerial.split(':')[0] : '',
          scan:p=>window.pywebview.api.install_scan_wifi(p),
          connect:async(ip,port)=>{
            const result=await window.pywebview.api.install_wifi_connect(port,ip);
            if(result.ok){wifiSerial=`${result.ip||ip}:${port}`;portInput.value=port;wifiStatus.textContent=`Wi-Fi: подключено (${wifiSerial})`;settle(wifiSerial);}
            else {wifiSerial=null;wifiStatus.textContent='Wi-Fi: не подключено';}
            return result;
          },
          onClose:()=>settle(null)});
      });
    }
    async function doWifiConnect() {
      LabUI.connection({port:Number(portInput.value)||modelWifiPort,
        scan:p=>window.pywebview.api.install_scan_wifi(p),
        connect:async(ip,port)=>{
          const result=await window.pywebview.api.install_wifi_connect(port,ip);
          if(result.ok){wifiSerial=`${result.ip||ip}:${port}`;portInput.value=port;wifiStatus.textContent=`Wi-Fi: подключено (${wifiSerial})`;}
          else {wifiSerial=null;wifiStatus.textContent='Wi-Fi: не подключено';}
          return result;
        }
      });
    }
    wifiConnectBtn.addEventListener("click", doWifiConnect);
    const wifiRow = el("div", { class: "row" }, [wifiStatus, wifiConnectBtn]);

    const connection = stage.type === "apps" ? (stage.apps_connection || "wired")
      : stage.type === "actions" ? (stage.actions_connection || "wired") : "wired";
    let askMode = "wired"; // для connection === "ask": что выбрал техник на переключателе
    if (connection === "wired") {
      bar.appendChild(wiredRow);
      refreshDevices();
    } else if (connection === "wifi") {
      bar.appendChild(wifiRow);
    } else {
      // "ask" — технику предлагается выбрать способ прямо здесь.
      const toggleRow = el("div", { class: "row" });
      const wiredBtn = el("button", { class: "accent", text: "Провод" });
      const wifiBtn = el("button", { text: "Wi-Fi" });
      const body = el("div", { style: "margin-top: 8px" });
      function showWired() {
        askMode = "wired";
        wiredBtn.className = "accent"; wifiBtn.className = "";
        clear(body); body.appendChild(wiredRow); refreshDevices();
      }
      function showWifi() {
        askMode = "wifi";
        wifiBtn.className = "accent"; wiredBtn.className = "";
        clear(body); body.appendChild(wifiRow);
      }
      wiredBtn.addEventListener("click", showWired);
      wifiBtn.addEventListener("click", showWifi);
      toggleRow.appendChild(wiredBtn);
      toggleRow.appendChild(wifiBtn);
      bar.appendChild(toggleRow);
      bar.appendChild(body);
      showWired();
    }

    return {
      element: bar, getDevice: () => wifiSerial || deviceByLabel[select.value] || null,
      mode: () => (connection === "ask" ? askMode : connection), askWifi,
    };
  }

  function buildInstructionBlock(stage, fullPage) {
    const html = stage.instruction_html || (fullPage ? Instructions12.textDocument(stage.description || "Для этого этапа нет отдельной инструкции.") : '');
    const block = el("div", { class: "instruction-block" + (fullPage ? " instruction12-surface" : ""), style: fullPage ? "flex: 1; display: flex; flex-direction: column" : "" });
    if (html) {
      // allow-popups(-to-escape-sandbox) — чтобы ссылки на источники
      // ("Источники: drive2.ru/...", см. app/instruction_html.py:_linkify)
      // открывались в системном браузере по клику, а не заменяли собой
      // саму инструкцию в этом iframe. allow-scripts — чтобы работали
      // "html"-блоки со своим JS (см. instruction_editor.js: калькулятор
      // кода инженерного меню по текущей дате во freetuga-моделях и т.п.)
      // — БЕЗ allow-same-origin, поэтому у srcdoc-документа всегда opaque
      // origin: скрипт может делать что угодно ВНУТРИ себя, но не видит
      // window.parent/pywebview.api, куки или что-либо ещё хоста. Формы и
      // top-navigation по прежнему запрещены.
      const iframe = el("iframe", { title: stage.title || "Инструкция", sandbox: "allow-scripts allow-popups allow-popups-to-escape-sandbox" });
      if (fullPage) iframe.style.height = "100%";
      block.appendChild(iframe);
      // srcdoc не всегда успевает попасть в атрибут при быстрой пересборке — пишем через contentWindow.
      iframe.addEventListener("load", () => {}, { once: true });
      iframe.srcdoc = LabUI.reader(html, { title: fullPage ? stage.title || "Инструкция" : "" });
    } else if (stage.description) {
      block.appendChild(el("div", { class: "plain-text", text: stage.description }));
    } else {
      block.appendChild(el("div", { class: "plain-text", text: "Для этого этапа нет отдельной инструкции." }));
    }
    return block;
  }

  function buildActionPanel(stageType) {
    const panel = el("div", { class: "action-panel" });
    panel.appendChild(el("div", { class: "action-chip", text: (TYPE_LABELS[stageType] || "").toUpperCase() }));
    return panel;
  }

  function buildVariantPicker(panel, stage, index) {
    const variantNames = stage.variant_names || [];
    if (!variantNames.length) return;
    const current = chosenVariants[index] || variantNames[0];
    chosenVariants[index] = current;

    const wrap = el("div", { style: "margin-bottom: 10px" });
    wrap.appendChild(el("span", { class: "field-label", text: "Вариант" }));
    const select = el("select", { style: "width: 100%" },
      variantNames.map((name) => el("option", { value: name, text: name, selected: name === current ? "" : null })));
    select.addEventListener("change", async () => {
      chosenVariants[index] = select.value;
      if (stage.type === "apps") {
        const standard = await window.pywebview.api.install_standard_apks(model.key, index, select.value);
        for (const apk of standard.required) appSelection[apk.path] = true;
        for (const apk of standard.optional) {
          if (!(apk.path in appSelection)) appSelection[apk.path] = false;
        }
      }
      render();
    });
    wrap.appendChild(select);
    panel.appendChild(wrap);
  }

  // -- manual ----------------------------------------------------------
  function stageInfo(panel, symbol, title, description) {
    const card = el('section', {class:'stage06-info'}, [
      el('span', {class:'stage06-symbol'}, [UsbUI.icon(symbol)]),
      el('div', {}, [el('h2', {text:title}), el('p', {text:description})])
    ]);
    panel.append(card);
    return card;
  }

  function renderManualStage(panel) {
    stageInfo(panel, 'car', 'Действия в автомобиле', 'Выполните инструкцию на магнитоле. Когда закончите, нажмите «Далее».');
  }

  // -- check -------------------------------------------------------------
  function renderCheckStage(panel, stage) {
    const options = stage.check_options || [];
    panel.appendChild(el("span", { class: "field-label", text: "Выберите вариант" }));
    const list = el("div", { class: "check-options-list" });
    options.forEach((opt, i) => {
      const btn = el("button", { class: "stage06-choice" }, [
        el('span', {class:'stage06-choice-number',text:String(i+1)}),el('span',{text:opt}),UsbUI.icon('chevron')
      ]);
      // Клик сразу продвигает по выбранной ветке (см. car_generator.py:
      // StepSpec.next_options).
      btn.addEventListener("click", () => advanceAfter(stage.index, i));
      list.appendChild(btn);
    });
    panel.appendChild(list);
    // "Далее" — для техника, который и так знает нужную ветку и просто
    // пролистывает мастер, не читая варианты: по умолчанию первый (тот же
    // выбор, что раньше был у select с selectedIndex по умолчанию 0).
    nextAction = () => advanceAfter(stage.index, 0);
  }

  // -- apps ----------------------------------------------------------------
  async function renderAppsStage(panel, stage, getDevice, transport) {
    panel.classList.add("apps-panel", "apps08-inline");
    buildVariantPicker(panel, stage, stage.index);
    Object.keys(sectionCollapsed).forEach(key => { sectionCollapsed[key] = false; });
    const choose = UsbUI.button('apps07-choose', 'Выбрать приложения', 'apps');
    const copy = el('p', {class:'apps08-total',text:'Загружаем список приложений…'});
    const preview = el('div', {class:'apps07-preview'});
    const search = el('input', {type:'search',placeholder:'Найти приложение','aria-label':'Поиск приложений'});
    const toolbar = el('div', {class:'apps08-toolbar'}, [copy]);
    const searchBox = el('label',{class:'apps07-search apps08-search'},[UsbUI.icon('search'),search]);
    const body = el('div',{class:'apps07-body apps08-body'});
    panel.append(toolbar, searchBox, body);
    const chooser = createAppChooser(panel, stage, choose, copy, preview, panel);
    if (!await chooser.load() || !panel.isConnected) return;
    body.replaceChildren(chooser.tree);
    body.querySelectorAll('.apps-section-body').forEach(section => section.classList.remove('collapsed'));
    body.querySelectorAll('.apps-section-header').forEach(header => { header.setAttribute('aria-expanded','true');header.firstChild.replaceWith(UsbUI.icon('down')); });
    body.addEventListener('change', () => chooser.update());
    search.addEventListener('input', () => {
      const query = search.value.trim().toLocaleLowerCase('ru');
      body.querySelectorAll('.app-row').forEach(row => row.hidden = !!query && !row.textContent.toLocaleLowerCase('ru').includes(query));
      [...body.querySelectorAll('.apps-section')].reverse().forEach(section => {
        section.hidden = !!query && ![...section.querySelectorAll('.app-row')].some(row => !row.hidden);
        const content = section.querySelector(':scope>.apps-section-body');
        if (!content) return;
        if (query) { if (!content.hasAttribute('data-search-collapsed')) content.dataset.searchCollapsed=String(content.classList.contains('collapsed'));content.classList.remove('collapsed'); }
        else if (content.hasAttribute('data-search-collapsed')) {content.classList.toggle('collapsed',content.dataset.searchCollapsed==='true');delete content.dataset.searchCollapsed;}
      });
    });
    // Раньше этап apps был только выбором галочек, а установку делал
    // отдельный следующий "adb"-этап — теперь ставит сам, тем же блоком
    // "Начать/Стоп", что и adb-этап (см. buildStartStopButtons ниже и
    // install_api.py:start_stage, который для apps без своего run в
    // stages.py подставляет ctx.install_selected_apks() по умолчанию).
    // Устройство/Wi-Fi — уже выбраны в баре над этапом (см.
    // buildTransportBar/getDevice), здесь их не выбирают заново.
    buildStartStopButtons(panel, stage, getDevice, { startLabel: "Начать установку", transport });
  }

  function createAppChooser(panel, stage, choose, copy, preview, host, ready = () => {}) {
    const revision = renderRevision;
    const errorBox = el('div', {class:'apps07-load-error',role:'status'});
    host.append(errorBox);
    const controller = {
      tree: null,
      rows() { return this.tree ? [...this.tree.querySelectorAll('.app-row')] : []; },
      entries() {
        const unique = new Map();
        for (const row of this.rows()) {
          const input = row.querySelector('input');
          if (!input?.checked) continue;
          const item = {path:row.dataset.apkPath,name:row.querySelector('label').textContent.trim(),row};
          const previous = unique.get(item.path);
          if (!previous || (input.disabled && !previous.row.querySelector('input').disabled)) unique.set(item.path,item);
        }
        return [...unique.values()];
      },
      paths() { return [...new Set(this.entries().map(item => item.path))]; },
      update() {
        const entries = this.entries();
        const required = entries.filter(item => item.row.querySelector('input:disabled')).length;
        copy.textContent = `Выбрано: ${this.paths().length}${required ? ` · Обязательных: ${required}` : ''}`;
        preview.replaceChildren();
        const seen = new Set();
        for (const item of entries) {
          if (seen.has(item.path)) continue;
          seen.add(item.path);
          if (seen.size <= 4) { const icon = item.row.querySelector('.apk-icon'); if (icon) preview.append(icon.cloneNode(true)); }
        }
        if (entries.length) preview.append(el('span',{text:'Набор можно изменить перед установкой'}));
      },
      async load() {
        choose.disabled = true; ready(false); errorBox.replaceChildren();
        try {
          this.tree = await buildAppsTree(stage);
          if (revision !== renderRevision) return false;
          this.update(); choose.disabled = false; ready(true); return true;
        } catch (error) {
          if (revision !== renderRevision) return false;
          sharedApksPromise = null;
          copy.textContent = 'Не удалось загрузить приложения.';
          const retry = UsbUI.button('', 'Повторить загрузку', 'refresh');
          retry.onclick = async () => { if (await this.load() && stage.type === 'apps' && !document.querySelector('.stage-primary-actions')) render(); };
          errorBox.append(el('span', {text:error.message || String(error)}),retry);
          return false;
        }
      },
    };
    panel._appChooser = controller;
    choose.setAttribute('aria-haspopup','dialog');
    choose.onclick = () => { if (!runnerBusy && controller.tree) openAppPicker(stage, controller, choose); };
    return controller;
  }

  function openAppPicker(stage, controller, trigger) {
    if (activeAppPicker) return;
    Object.keys(sectionCollapsed).forEach(key => { sectionCollapsed[key] = false; });
    const originalSelection = appSelection;
    const originalPersonal = personalApks.slice();
    appSelection = {...appSelection};
    const dialog = el('dialog', {class:'apps07-dialog','aria-labelledby':'apps07-title'});
    const close = UsbUI.button('apps07-close','','close'); close.setAttribute('aria-label','Закрыть без сохранения');
    const search = el('input',{type:'search',placeholder:'Найти приложение','aria-label':'Поиск приложений'});
    const body = el('div',{class:'apps07-body'});
    const count = el('span',{class:'apps07-count','aria-live':'polite'});
    const cancel = UsbUI.button('apps07-cancel','Отмена','back');
    const apply = UsbUI.button('apps07-apply','Готово','check',true);
    dialog.append(el('header',{class:'apps07-header'},[el('h2',{id:'apps07-title',text:'Выбор приложений'}),close]),
      el('div',{class:'apps07-tools'},[el('label',{class:'apps07-search'},[UsbUI.icon('search'),search])]),body,
      el('footer',{class:'apps07-footer'},[count,cancel,apply]));
    const session = {dialog,closed:false,tree:null,revision:0,loading:false,reload:null,imported:new Map(),picking:false};
    activeAppPicker = session;
    function updateCount() {
      const paths = new Set([...body.querySelectorAll('.app-row')].filter(row => row.querySelector('input:checked')).map(row => row.dataset.apkPath));
      count.textContent = `Выбрано: ${paths.size}`;
    }
    function filter() {
      const query = search.value.trim().toLocaleLowerCase('ru');
      body.querySelectorAll('.app-row').forEach(row => row.hidden = !!query && !row.textContent.toLocaleLowerCase('ru').includes(query));
      [...body.querySelectorAll('.apps-section')].reverse().forEach(section => {
        section.hidden = !!query && ![...section.querySelectorAll('.app-row')].some(row => !row.hidden);
        const sectionBody = section.querySelector(':scope > .apps-section-body');
        if (!sectionBody) return;
        if (query) { if (!sectionBody.hasAttribute('data-before-search')) sectionBody.dataset.beforeSearch = String(sectionBody.classList.contains('collapsed')); sectionBody.classList.remove('collapsed'); }
        else if (sectionBody.hasAttribute('data-before-search')) { sectionBody.classList.toggle('collapsed',sectionBody.dataset.beforeSearch==='true'); delete sectionBody.dataset.beforeSearch; }
      });
      body.querySelector('.apps07-empty')?.remove();
      if (query && ![...body.querySelectorAll('.app-row')].some(row => !row.hidden)) body.append(el('p',{class:'apps07-empty',text:'Приложения не найдены'}));
    }
    session.reload = async () => {
      const request = ++session.revision;
      session.loading = true; apply.disabled = true;
      const position = body.scrollTop;
      try {
        const tree = await buildAppsTree(stage);
        if (session.closed || request !== session.revision) return;
        session.tree = tree; body.replaceChildren(tree); body.scrollTop = position;
        updateCount(); filter(); apply.disabled = false;
      } catch (error) {
        if (session.closed || request !== session.revision) return;
        sharedApksPromise = null;
        const retry = UsbUI.button('', 'Повторить загрузку','refresh'); retry.onclick = session.reload;
        body.replaceChildren(el('p',{class:'apps07-status','data-error':'true',text:error.message||String(error)}),retry);
      } finally { if (request === session.revision) session.loading = false; }
    };
    function finish(save) {
      if (session.closed || (save && (session.loading || !session.tree))) return;
      session.closed = true;
      if (save) controller.tree = session.tree;
      else {
        appSelection = originalSelection;
        // Imported APKs remain available in this session, but Cancel never adds them to the queue.
        const restored = new Map();
        for (const apk of [...originalPersonal, ...personalApks, ...session.imported.values()]) {
          if (!restored.has(apk.path)) restored.set(apk.path,apk);
        }
        personalApks = [...restored.values()];
      }
      hideAppDescription();
      if (appDescriptionTooltip) document.body.append(appDescriptionTooltip);
      activeAppPicker = null; controller.update(); dialog.close(); dialog.remove(); trigger.focus();
    }
    close.onclick = cancel.onclick = () => finish(false);
    apply.onclick = () => finish(true);
    dialog.addEventListener('cancel', event => { event.preventDefault(); finish(false); });
    dialog.addEventListener('click', event => { if (event.target !== dialog) return; const r=dialog.getBoundingClientRect(); if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)finish(false); });
    search.oninput = filter; body.addEventListener('change',updateCount);
    body.append(el('p',{class:'apps07-status',text:'Загружаем приложения…'}));
    document.body.append(dialog); dialog.showModal(); session.reload();
  }

  // Общее дерево "Стандартные приложения этого этапа" + "Дополнительные
  // приложения" (вся общая библиотека apk/, по категориям) — используется и
  // "apps"-этапом (единственный способ установить что-либо), и "usb"-этапом
  // с usb_copy_selected_apks (технику нужно видеть и отмечать те же самые
  // галочки, чтобы выбрать, что скопировать на флешку вместе со скриптом).
  async function buildAppsTree(stage) {
    const revision=renderRevision;
    const selection = appSelection;
    const tree = el("div", { class: "apps-tree" });

    // Свои APK — пользователь сам выбирает файл(ы) с компьютера, минуя
    // общую библиотеку apk/ (та наполняется только администратором, см.
    // admin_apk_dialog.js). Кнопка всегда видна сверху, вне свёрнутых
    // секций — иначе тонула бы среди остальных разделов.
    tree.appendChild(el("button", {
      class: "app-personal-apk-add",
      type: "button",
      text: "Добавить свой APK...",
      onclick: () => pickPersonalApks(),
    }));
    if (personalApks.length) {
      const body = el("div", { class: "apps-grid" });
      for (const apk of personalApks) body.appendChild(buildPersonalAppRow(apk));
      tree.appendChild(buildCollapsibleSection("personal", "Свои APK", null, body));
    }

    // Сверху вниз: обязательные (всегда ставятся, без чекбокса) →
    // необязательные этой машины (чекбоксом, техник решает сам) →
    // дополнительные из общей библиотеки apk/ (см. buildAppRow/
    // buildCollapsibleSection ниже — required=true рисует уже отмеченный и
    // задизейбленный чекбокс, чтобы визуально было видно, что это тоже
    // приложение, просто без права его снять).
    const standard = await window.pywebview.api.install_standard_apks(model.key, stage.index, chosenVariants[stage.index]);
    if(revision!==renderRevision)return tree;
    for (const apk of standard.required) selection[apk.path] = true;
    for (const apk of standard.optional) {
      if (!(apk.path in selection)) selection[apk.path] = false;
    }
    if (standard.required.length) {
      tree.appendChild(buildCollapsibleSection("standard-required", "Обязательные приложения", standard.required, null, true));
    }
    if (standard.optional.length) {
      tree.appendChild(buildCollapsibleSection("standard-optional", "Дополнительно", standard.optional));
    }

    const shared = await sharedApks();
    if(revision!==renderRevision)return tree;
    const byCategory = {};
    for (const apk of shared) {
      // Не ||= — см. events.js за тем же обоснованием (старый Chromium в
      // Qt5/PySide2-сборке не умеет логические операторы присваивания).
      if (!byCategory[apk.category]) byCategory[apk.category] = [];
      byCategory[apk.category].push(apk);
    }
    const extraBody = el("div");
    if (!Object.keys(byCategory).length) {
      extraBody.appendChild(el("p", { class: "app-desc", text: "Нет APK в папке apk/" }));
    }
    const categories = Object.keys(byCategory).sort((a, b) => (a === "") - (b === "") || a.localeCompare(b));
    for (const category of categories) {
      extraBody.appendChild(buildCollapsibleSection(`extra:${category}`, category || "Без категории", byCategory[category]));
    }
    tree.appendChild(buildCollapsibleSection("extra", "Библиотека приложений", null, extraBody));
    tree.append(tree.querySelector(".app-personal-apk-add"));
    const requiredPaths = new Set(standard.required.map(apk => apk.path));
    function syncSelection() {
      for (const row of tree.querySelectorAll('.app-row')) {
        const input = row.querySelector('input');
        const mandatory = requiredPaths.has(row.dataset.apkPath);
        input.disabled = mandatory;
        input.checked = mandatory || !!selection[row.dataset.apkPath];
      }
    }
    tree.addEventListener('change', event => {
      const row = event.target.closest('.app-row');
      if (!row || event.target.type !== 'checkbox') return;
      const path = row.dataset.apkPath;
      selection[path] = requiredPaths.has(path) || event.target.checked;
      syncSelection();
    });
    syncSelection();
    return tree;
  }

  function buildCollapsibleSection(key, title, apks, presetBody, required) {
    // По умолчанию свёрнуто (не встречалось в sectionCollapsed ещё) — раньше
    // все разделы открывались сразу, и на моделях с большим списком
    // приложений (см. "Яндекс" на скриншоте пользователя) этап превращался
    // в длинную простыню чекбоксов ещё до того, как техник вообще решил,
    // какой раздел ему нужен. Ручной выбор пользователя (клик по заголовку)
    // по-прежнему запоминается в sectionCollapsed на время работы с моделью.
    const collapsed = key in sectionCollapsed ? sectionCollapsed[key] : stages[currentIndex]?.type === 'apps' ? false : key !== "standard-optional";
    sectionCollapsed[key]=collapsed;
    const sectionKind = required ? " apps-section-required"
      : key === "standard-optional" ? " apps-section-optional" : "";
    const wrap = el("section", { class: `apps-section${sectionKind}` });
    const header = el("button", { class: "apps-section-header", type:"button", "aria-expanded":String(!collapsed) }, [
      UsbUI.icon(collapsed ? 'chevron' : 'down'),
      el("span", { text: title }),
    ]);
    const body = presetBody || el("div");
    body.classList.add("apps-section-body");
    if (apks) body.classList.add("apps-grid");
    if (collapsed) body.classList.add("collapsed");
    header.addEventListener("click", () => {
      sectionCollapsed[key] = !sectionCollapsed[key];
      body.classList.toggle("collapsed");
      header.setAttribute("aria-expanded",String(!sectionCollapsed[key]));
      header.firstChild.replaceWith(UsbUI.icon(sectionCollapsed[key] ? 'chevron' : 'down'));
    });
    wrap.appendChild(header);
    wrap.appendChild(body);
    if (apks) {
      for (const apk of apks) body.appendChild(buildAppRow(apk, required));
    }
    return wrap;
  }

  // required — обязательное приложение этой машины (StepSpec.standard_apks,
  // см. car_generator.py): чекбокс показывается уже отмеченным и
  // задизейбленным — техник видит, что оно будет установлено, но не может
  // его снять (appSelection для него и так всегда true — выставляется в
  // buildAppsTree/initAppSelectionDefaults, не через этот чекбокс).
  function buildAppRow(apk, required) {
    const row = el("div", { class: "app-row", "data-apk-path":apk.path });
    if (apk.description) {
      row.addEventListener("mouseenter", () => showAppDescription(row, apk.description));
      row.addEventListener("mouseleave", hideAppDescription);
      row.addEventListener("focusin", () => showAppDescription(row, apk.description));
      row.addEventListener("focusout", hideAppDescription);
    }
    const checkbox = el("input", { type: "checkbox" });
    if (required) {
      checkbox.checked = true;
      checkbox.disabled = true;
    } else {
      checkbox.checked = !!appSelection[apk.path];
      checkbox.addEventListener("change", () => { appSelection[apk.path] = checkbox.checked; });
    }
    const label = apk.name;
    const wrap = el("div", {}, [
      el("label", { class: "row" }, [
        checkbox, LabUI.appIcon(apk.path,apk.icon),
        el("span", { text: label, style: apk.remote_only ? "color: var(--text-dim)" : "" }),
      ]),
    ]);
    row.appendChild(wrap);
    return row;
  }

  async function pickPersonalApks() {
    const session = activeAppPicker;
    if (session?.picking) return;
    const originModel = model;
    if (session) {
      session.picking = true;
      session.dialog.querySelector('.app-personal-apk-add').disabled = true;
    }
    try {
      const picked = await window.pywebview.api.car_pick_files("apk", true);
      if (!Array.isArray(picked) || !picked.length || model !== originModel) return;
      for (const file of picked) {
        if (!file.path) continue;
        const apk = { path: file.path, name: file.name };
        session?.imported.set(file.path,apk);
        if (!personalApks.some((item) => item.path === file.path)) personalApks.push(apk);
        if (!session || (!session.closed && activeAppPicker === session)) appSelection[file.path] = true;
      }
      if (session && !session.closed) await session.reload();
      else if (!session) render();
    } catch (error) {
      const message = `Не удалось выбрать APK: ${error.message || error}`;
      log(message);
      if (session && !session.closed) session.dialog.querySelector('.apps07-body').append(el('p',{class:'apps07-status','data-error':'true',text:message}));
    } finally {
      if (session) {
        session.picking = false;
        const button = session.dialog.querySelector('.app-personal-apk-add');
        if (button) button.disabled = false;
      }
    }
  }

  // Свой APK — тот же чекбокс-ряд, что и обычный buildAppRow, плюс кнопка
  // "убрать" (не из чего снимать галочку — файл либо в списке на установку,
  // либо его вообще не должно быть видно, раз это случайный локальный выбор,
  // а не запись из управляемой библиотеки apk/).
  function buildPersonalAppRow(apk) {
    const row = el("div", { class: "app-row", "data-apk-path":apk.path });
    const checkbox = el("input", { type: "checkbox" });
    checkbox.checked = !!appSelection[apk.path];
    checkbox.addEventListener("change", () => { appSelection[apk.path] = checkbox.checked; });
    const removeBtn = el("button", {
      type: "button", class: "app-personal-apk-remove",
      html: '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"><path d="M6 6l12 12M18 6L6 18"/></svg>',
      "aria-label": `Убрать ${apk.name}`,
      onclick: () => {
        personalApks = personalApks.filter((a) => a.path !== apk.path);
        delete appSelection[apk.path];
        if (activeAppPicker) activeAppPicker.reload(); else render();
      },
    });
    removeBtn.replaceChildren(UsbUI.icon('close'));
    const wrap = el("div", { class: "row", style: "justify-content: space-between; align-items: center" }, [
      el("label", { class: "row" }, [checkbox, LabUI.appIcon(apk.path,apk.icon), el("span", { text: apk.name })]),
      removeBtn,
    ]);
    row.appendChild(wrap);
    return row;
  }

  // Подсказка живёт прямо в document.body, а не внутри прокручиваемого
  // списка приложений. Поэтому её не обрезают границы карточек/секций.
  function showAppDescription(row, description) {
    if (!appDescriptionTooltip) {
      appDescriptionTooltip = document.createElement("div");
      appDescriptionTooltip.className = "app-description-tooltip";
      appDescriptionTooltip.setAttribute("role", "tooltip");
      document.body.appendChild(appDescriptionTooltip);
    }
    (activeAppPicker?.dialog || document.body).append(appDescriptionTooltip);
    appDescriptionTooltip.textContent = description;
    appDescriptionTooltip.hidden = false;
    appDescriptionTooltip.style.visibility = "hidden";
    const rect = row.getBoundingClientRect();
    const tipRect = appDescriptionTooltip.getBoundingClientRect();
    const sidePadding = 12;
    const left = Math.max(sidePadding, Math.min(rect.left, window.innerWidth - tipRect.width - sidePadding));
    const below = rect.bottom + 8;
    const top = below + tipRect.height <= window.innerHeight - sidePadding
      ? below
      : Math.max(sidePadding, rect.top - tipRect.height - 8);
    appDescriptionTooltip.style.left = `${Math.round(left)}px`;
    appDescriptionTooltip.style.top = `${Math.round(top)}px`;
    appDescriptionTooltip.style.visibility = "visible";
  }

  function hideAppDescription() {
    if (appDescriptionTooltip) appDescriptionTooltip.hidden = true;
  }

  function selectedApkPaths() {
    return Object.entries(appSelection).filter(([, checked]) => checked).map(([path]) => path);
  }

  // USB preparation uses the approved cards; all operations remain native.
  function showUsbInstruction(stage, qr = false) {
    const wrap = el('div');
    if (qr) {
      const list = el('ol', {class: 'usb06-procedure'});
      [
        'На магнитоле откройте инженерное меню и экран с QR-кодом для ADB. Не закрывайте этот экран.',
        'Вставьте в магнитолу флешку с записанным файлом svlog.flag.',
        'Дождитесь надписи «QNX OK» на экране магнитолы, затем извлеките флешку.',
        'Подключите эту же флешку к компьютеру, обновите список накопителей и нажмите «Получить пароль». Введите полученный код на магнитоле.'
      ].forEach(text => list.append(el('li', {text})));
      wrap.append(list);
    }
    if (stage.instruction_html || stage.description) wrap.append(buildInstructionBlock(stage, false));
    UsbUI.instruction('Действия на магнитоле', wrap);
  }

  // Только для qr_adb_engineering_menu=true (Haval Jolion 2026 и родня, Desay x9h) —
  // отдельный диалог, НЕ showUsbInstruction выше: тот общий для Geely/VOLGA и его
  // содержимое трогать нельзя. Точные названия ("客制化"/Customization, "Adb Switch") —
  // не с самой магнитолы Haval (скриншотов нет), а с фото инженерного меню Geely
  // Monjaro SE (files/instruction_1/images/ — тот же поставщик платформы Desay x9h/
  // Semidrive, то же меню под капотом, см. app/qr_adb_password.py и совпадение фразы
  // "QNX OK" — на входе в меню способы разные: Geely — 10 тапов по названию модели +
  // 2 по версии прошивки в "Об автомобиле", Haval — файл svengmode.flag).
  function showQrAdbPrepInstruction() {
    const wrap = el('div');
    const list = el('ol', {class: 'usb06-procedure'});
    [
      'Вставьте флешку в магнитолу.',
      'Откроется инженерное меню.',
      'Нажмите на нижний правый пункт меню — подписан «客制化» (китайскими иероглифами) либо «Customization» (зависит от прошивки).',
      'В открывшемся разделе найдите строку «Adb Switch» и нажмите рядом с ней «Open».',
      'Откроется экран с QR-кодом.',
      'Извлеките флешку из магнитолы и вернитесь к этому окну — запишите второй файл (следующий шаг).',
    ].forEach(text => list.append(el('li', {text})));
    wrap.append(list);
    UsbUI.instruction('Инженерное меню магнитолы', wrap);
  }

  async function renderUsbStage(panel, stage) {
    UsbUI.heading(panel, 'Подготовьте USB-накопитель с файлами для вашей магнитолы.');
    buildVariantPicker(panel, stage, stage.index);
    const write = UsbUI.button('usb06-open-writer', 'Подготовить флешку', 'download', true);
    let chooser;
    if (stage.usb_copy_selected_apks) {
      const choose = UsbUI.button('usb06-choose-apps', 'Выбрать приложения', 'apps');
      const selectionCard = UsbUI.step(1, 'apps', 'Приложения на флешке', 'Загружаем список приложений…', choose);
      const preview = el('div', {class:'apps07-preview'});
      selectionCard.append(preview); panel.append(selectionCard);
      chooser = createAppChooser(panel, stage, choose, selectionCard.querySelector('.usb06-step-copy p'), preview, selectionCard, loaded => write.disabled = !loaded);
      write.disabled = true;
    }
    const card = UsbUI.step(stage.usb_copy_selected_apks ? 2 : 1, 'file', 'Запишите файлы на флешку', stage.usb_copy_selected_apks
      ? 'На флешку будут скопированы файлы этапа и выбранные приложения.'
      : 'На флешку будут скопированы файлы этого этапа.', write);
    card.dataset.state = 'active'; panel.append(card);
    write.onclick = () => {
      if (runnerBusy) return;
      window.usbDialog.open({
        modelKey: model.key, stageIndex: stage.index, variant: chosenVariants[stage.index],
        selectedApkPaths: stage.usb_copy_selected_apks
          ? chooser.paths()
          : selectedApkPaths(), titleSuffix: `${model.display_label} — ${stage.title}`,
        onFinished: success => { if (success) advanceAfter(stage.index); },
      });
    };
    const instructionStage = stage.instruction_html || stage.description ? stage
      : stages.find(next => next.id === stage.next && next.type === 'instruction');
    if (instructionStage) {
      const help = UsbUI.button('usb06-instruction', 'Открыть инструкцию', 'book');
      help.onclick = () => showUsbInstruction(instructionStage);
      panel.append(UsbUI.step(stage.usb_copy_selected_apks ? 3 : 2, 'car', 'Выполните шаги на магнитоле', 'Следуйте инструкции для выбранной модели автомобиля.', help));
    }
    if (chooser) chooser.load();
  }

  function renderQrAdbStage(panel, stage) {
    // Desay x9h (Haval Jolion 2026 и родня, см. car_generator.py: StepSpec.
    // qr_adb_engineering_menu) — перед обычным svlog.flag нужно ОТДЕЛЬНЫМ файлом
    // svengmode.flag открыть инженерное меню и вручную дойти в нём до раздела с
    // QR-кодом; обычный флоу Geely/VOLGA (needsPrep=false) не меняется вообще.
    const needsPrep = !!stage.qr_adb_engineering_menu;
    UsbUI.heading(panel, needsPrep
      ? 'Сначала откройте инженерное меню магнитолы, затем запишите файл и получите пароль для ADB.'
      : 'Запишите файл на USB-накопитель, выполните шаги на магнитоле и получите пароль для ADB.');
    const revision = renderRevision;
    const live = () => renderRevision === revision;
    const driveSelect = el('select', {id:'usb06-drive', 'aria-label':'USB-накопитель'});
    const refreshBtn = UsbUI.button('usb06-refresh', '', 'refresh');
    refreshBtn.className = 'usb06-refresh'; refreshBtn.title = 'Обновить список накопителей';
    refreshBtn.setAttribute('aria-label', 'Обновить список накопителей');
    const strip = el('div', {class:'usb06-drive-strip'}, [UsbUI.icon('usb'), el('label', {for:'usb06-drive', text:'USB-накопитель'}), driveSelect, refreshBtn]);
    const showAllCheckbox = el('input', {type:'checkbox', id:'usb06-show-all'});
    const options = el('details', {class:'usb06-drive-options'}, [
      el('summary', {text:'Нужной флешки нет в списке?'}),
      el('label', {}, [showAllCheckbox, document.createTextNode('Показать все локальные диски')]),
      el('p', {text:'Системный диск и диск программы исключены. Проверьте выбранный накопитель перед записью.'})
    ]);
    const driveStatus = el('p', {class:'usb06-step-status', role:'status', 'aria-live':'polite'});

    // -- доп. фаза "инженерное меню" (только needsPrep) --------------------
    let prepWriteBtn, prepInstructionBtn, prepStatus, prepOne, prepTwo;
    if (needsPrep) {
      prepWriteBtn = UsbUI.button('usb06-prep-write', 'Записать файл', 'download', true);
      prepInstructionBtn = UsbUI.button('usb06-prep-instruction', 'Открыть инструкцию', 'book');
      prepStatus = el('p', {class:'usb06-step-status', role:'status', 'aria-live':'polite'});
      prepOne = UsbUI.step(1, 'file', 'Запишите файл на флешку',
        'Будет создан файл svengmode.flag — он открывает инженерное меню магнитолы.', prepWriteBtn);
      // "готово" у этой карточки — как и у соседней "Выполните шаги на магнитоле" ниже
      // (two): не отдельная кнопка-подтверждение, а по факту успеха следующего шага
      // (записи svlog.flag) — см. writeBtn.onclick.
      prepTwo = UsbUI.step(2, 'car', 'Откройте раздел с QR-кодом',
        'Подключите флешку к магнитоле — откроется инженерное меню.', prepInstructionBtn);
      prepOne.dataset.state = 'active'; prepOne.append(prepStatus);
    }
    const stepOffset = needsPrep ? 2 : 0;
    const writeBtn = UsbUI.button('usb06-write', 'Записать файл', 'download', true);
    const helpBtn = UsbUI.button('usb06-instruction', 'Открыть инструкцию', 'book');
    const getBtn = UsbUI.button('usb06-password', 'Получить пароль', 'key');
    const writeStatus = el('p', {class:'usb06-step-status', role:'status', 'aria-live':'polite'});
    const readStatus = el('p', {class:'usb06-step-status', role:'status', 'aria-live':'polite'});
    const one = UsbUI.step(1 + stepOffset, 'file', needsPrep ? 'Запишите второй файл на флешку' : 'Запишите файл на флешку',
      'Будет создан файл svlog.flag для получения кода ADB.', writeBtn);
    const two = UsbUI.step(2 + stepOffset, 'car', 'Выполните шаги на магнитоле',
      needsPrep ? 'Вставьте флешку ещё раз и дождитесь надписи «QNX OK».'
                : 'Откройте экран с QR-кодом и дождитесь надписи «QNX OK».', helpBtn);
    const three = UsbUI.step(3 + stepOffset, 'key', 'Подключите флешку снова', 'Верните её в компьютер и получите пароль из сохранённых логов.', getBtn);
    one.dataset.state = needsPrep ? '' : 'active'; one.append(writeStatus); three.append(readStatus);
    const resultBox = el('div', {class:'usb06-result'}); resultBox.hidden = true;
    const codeEl = el('div', {class:'usb06-code', 'aria-label':'Пароль ADB'});
    const copyBtn = UsbUI.button('usb06-copy', 'Скопировать', 'copy');
    const meta = el('p');
    resultBox.append(codeEl, copyBtn, meta); three.append(resultBox);
    if (needsPrep) panel.append(strip, options, driveStatus, prepOne, prepTwo, one, two, three);
    else panel.append(strip, options, driveStatus, one, two, three);
    let drives = [], busy = false, loading = false, request = 0, writeDrive = '', resultDrive = '', prepDrive = '';

    function syncControls() {
      const locked = busy || loading;
      [driveSelect, refreshBtn, showAllCheckbox, writeBtn, getBtn].forEach(c => c.disabled = locked);
      if (needsPrep) { prepWriteBtn.disabled = locked; prepInstructionBtn.disabled = locked; }
      helpBtn.disabled = busy;
      if (live()) {
        navNextBtn.disabled = runnerBusy;
        navBackBtn.disabled = runnerBusy || !historyStack.length;
      }
    }
    function setBusy(value) { busy = value; runnerBusy = value; syncControls(); }
    function status(node, text, error = false) { node.textContent = text; node.dataset.error = String(error); }
    function currentDrive() { return drives.find(d => d.letter === driveSelect.value); }
    async function refreshDrives() {
      const id = ++request;
      const previous = driveSelect.value;
      loading = true; syncControls();
      try {
        const result = await window.pywebview.api.usb_list_drives(showAllCheckbox.checked);
        if (!live() || id !== request) return false;
        drives = Array.isArray(result) ? result : [];
        driveSelect.replaceChildren(el('option', {value:'', text:drives.length ? 'Выберите USB-накопитель' : 'Подключите USB-накопитель'}));
        for (const d of drives) driveSelect.append(el('option', {value:d.letter, text:d.display}));
        driveSelect.value = drives.some(d => d.letter === previous) ? previous : '';
        const lost = previous && !driveSelect.value;
        status(driveStatus, lost ? 'Выбранная флешка отключена. Подключите её и обновите список.' : '');
        resetDifferentDrive();
        return true;
      } catch (err) {
        if (live() && id === request) {
          drives = []; driveSelect.replaceChildren(el('option', {value:'', text:'Не удалось прочитать список'}));
          status(driveStatus, err.message || String(err), true); resetDifferentDrive();
        }
        return false;
      } finally { if (id === request) { loading = false; syncControls(); } }
    }
    function resetDifferentDrive() {
      if (needsPrep && prepDrive && driveSelect.value && prepDrive !== driveSelect.value) {
        prepDrive = ''; prepOne.dataset.state = 'active'; prepTwo.dataset.state = ''; status(prepStatus, '');
        // Другая флешка — значит и записанный на неё svlog.flag (если был) больше не в счёт.
        writeDrive = ''; one.dataset.state = ''; two.dataset.state = ''; status(writeStatus, '');
      } else if (writeDrive && driveSelect.value && writeDrive !== driveSelect.value) {
        writeDrive = ''; one.dataset.state = needsPrep ? (prepDrive ? 'active' : '') : 'active';
        two.dataset.state = ''; status(writeStatus, '');
      }
      if (resultDrive && resultDrive !== driveSelect.value) { resultBox.hidden = true; three.dataset.state = ''; resultDrive = ''; }
      syncControls();
    }
    driveSelect.onchange = () => { resetDifferentDrive(); status(driveStatus, ''); };
    refreshBtn.onclick = () => { if (!busy && !loading) refreshDrives(); };
    showAllCheckbox.onchange = () => { if (!busy && !loading) refreshDrives(); };
    helpBtn.onclick = () => showUsbInstruction(stage, true);
    if (needsPrep) {
      prepWriteBtn.onclick = async () => {
        if (busy || loading || !live()) return;
        const drive = currentDrive();
        if (!drive) { status(driveStatus, 'Выберите флешку из списка.', true); driveSelect.focus(); return; }
        setBusy(true); prepOne.dataset.state = 'busy'; status(prepStatus, 'Записываем svengmode.flag…');
        prepWriteBtn.lastChild.textContent = 'Записываем…';
        const run = window.StageRun.open({
          title: 'Запись файла на флешку', icon: 'usb',
          detail: 'Не отключайте флешку, пока идёт запись.',
          retry: () => prepWriteBtn.click(),
        });
        try {
          const result = await window.pywebview.api.qr_adb_write_prep_flag(drive.letter);
          if (!live()) { run.dispose(); return; }
          if (!result.ok) throw new Error(result.error || 'Не удалось записать файл.');
          prepDrive = drive.letter; prepOne.dataset.state = 'done'; prepTwo.dataset.state = 'active';
          status(prepStatus, 'Файл записан. Подключите флешку к магнитоле и откройте раздел с QR-кодом.');
          // Раньше вся процедура QR ADB была невидима в постоянном журнале
          // сессии — жалобы «пароль неверный» нельзя было разобрать без
          // доступа к самому компьютеру техника (см. getBtn.onclick ниже).
          sessionHasActivity = true; log(`QR ADB: файл svengmode.flag записан на ${drive.letter}.`);
          run.finish({ success: true, message: 'Файл записан на флешку. Подключите её к магнитоле.' });
        } catch (err) {
          if (live()) {
            prepOne.dataset.state = 'active'; status(prepStatus, err.message || String(err), true);
            sessionHasActivity = true; log(`QR ADB: не удалось записать svengmode.flag — ${err.message || err}`);
            run.finish({ success: false, message: err.message || String(err) });
          } else run.dispose();
        }
        finally { setBusy(false); prepWriteBtn.lastChild.textContent = 'Записать файл'; syncControls(); }
      };
      prepInstructionBtn.onclick = () => showQrAdbPrepInstruction();
    }
    writeBtn.onclick = async () => {
      if (busy || loading || !live()) return;
      const drive = currentDrive();
      if (!drive) { status(driveStatus, 'Выберите флешку из списка.', true); driveSelect.focus(); return; }
      setBusy(true); one.dataset.state = 'busy'; status(writeStatus, 'Записываем svlog.flag…');
      writeBtn.lastChild.textContent = 'Записываем…';
      const run = window.StageRun.open({
        title: 'Запись файла на флешку', icon: 'usb',
        detail: 'Не отключайте флешку, пока идёт запись.',
        retry: () => writeBtn.click(),
      });
      try {
        const result = await window.pywebview.api.qr_adb_write_flag(drive.letter);
        if (!live()) { run.dispose(); return; }
        if (!result.ok) throw new Error(result.error || 'Не удалось записать файл.');
        writeDrive = drive.letter; one.dataset.state = 'done'; two.dataset.state = 'active';
        if (needsPrep) prepTwo.dataset.state = 'done';
        status(writeStatus, 'Файл записан. Теперь подключите эту флешку к магнитоле.');
        sessionHasActivity = true; log(`QR ADB: файл svlog.flag записан на ${drive.letter}.`);
        run.finish({ success: true, message: 'Файл записан на флешку. Теперь подключите её к магнитоле.' });
      } catch (err) {
        if (live()) {
          one.dataset.state = 'active'; status(writeStatus, err.message || String(err), true);
          sessionHasActivity = true; log(`QR ADB: не удалось записать svlog.flag — ${err.message || err}`);
          run.finish({ success: false, message: err.message || String(err) });
        } else run.dispose();
      }
      finally { setBusy(false); writeBtn.lastChild.textContent = 'Записать файл'; }
    };
    getBtn.onclick = async () => {
      if (busy || loading || !live()) return;
      setBusy(true); resultBox.hidden = true; three.dataset.state = 'busy';
      status(readStatus, ''); getBtn.lastChild.textContent = 'Читаем флешку…';
      const run = window.StageRun.open({
        title: 'Получение пароля ADB', icon: 'key',
        detail: 'Читаем сохранённые логи с флешки.',
        retry: () => getBtn.click(),
      });
      try {
        if (!await refreshDrives()) {
          if (live()) run.finish({ success: false, message: 'Не удалось прочитать список накопителей.' }); else run.dispose();
          return;
        }
        if (!live()) { run.dispose(); return; }
        const drive = currentDrive();
        if (!drive) {
          status(readStatus, 'Подключите флешку и выберите её в списке.', true);
          run.finish({ success: false, message: 'Подключите флешку и выберите её в списке.' });
          return;
        }
        const result = await window.pywebview.api.qr_adb_get_password(drive.letter);
        if (!live()) { run.dispose(); return; }
        if (!result.ok) throw new Error(result.error || 'Не удалось получить пароль.');
        codeEl.textContent = result.code;
        meta.textContent = `SN: ${result.sn} · ${result.logs_folder}/${result.zip_name}`;
        resultDrive = drive.letter; resultBox.hidden = false; three.dataset.state = 'done';
        two.dataset.state = 'done'; status(readStatus, 'Пароль готов. Введите его на экране магнитолы.');
        // Единственное место, откуда видно, что реально попало в формулу
        // (жалобы клиентов на неверный пароль, 2026-09-21) — код+SN+источник
        // в постоянном журнале сессии, полная копия zip — на диске техника
        // (result.debug_copy, см. app/qr_adb_password.py:save_debug_copy),
        // пока не накоплена уверенность в 100% надёжности формулы.
        sessionHasActivity = true;
        log(`QR ADB: пароль получен — код ${result.code}, SN ${result.sn}, источник ${result.logs_folder}/${result.zip_name}` +
          (result.debug_copy ? ', копия дампа сохранена.' : '.'));
        run.finish({ success: true, message: 'Пароль готов. Введите его на экране магнитолы.' });
      } catch (err) {
        if (live()) {
          status(readStatus, err.message || String(err), true);
          sessionHasActivity = true; log(`QR ADB: не удалось получить пароль — ${err.message || err}`);
          run.finish({ success: false, message: err.message || String(err) });
        } else run.dispose();
      }
      finally { if (three.dataset.state === 'busy') three.dataset.state = ''; setBusy(false); getBtn.lastChild.textContent = 'Получить пароль'; }
    };
    copyBtn.onclick = async () => {
      try { await navigator.clipboard.writeText(codeEl.textContent); copyBtn.lastChild.textContent = 'Скопировано'; setTimeout(() => { copyBtn.lastChild.textContent = 'Скопировать'; }, 1500); }
      catch (_) { window.notice(codeEl.textContent, {title:'Пароль ADB'}); }
    };
    refreshDrives();
  }

  // -- exe --------------------------------------------------------------
  function renderExeStage(panel, stage) {
    const card = stageInfo(panel, 'file', 'Внешний установщик', `${stage.exe_name || 'Установщик'} откроется отдельным окном. Завершите установку в нём, затем вернитесь и нажмите «Далее».`);
    if (!stage.exe_exists) {
      panel.appendChild(el("div", { class: "callout danger", text: `Файл не найден: ${stage.exe_path}` }));
    }
    const button = UsbUI.button('stage06-run-exe', 'Запустить установщик', 'play', true);
    button.disabled = !stage.exe_exists;
    const feedback = el('p', {class:'stage06-action-status',role:'status'});
    card.append(button, feedback);
    button.onclick = async () => {
      if (runnerBusy) return;
      button.disabled = true; runnerBusy = true; navBackBtn.disabled = true; navNextBtn.disabled = true;
      feedback.textContent = 'Открываем установщик…';
      try {
        const result = await window.pywebview.api.install_run_exe(stage.exe_path);
        if (!result.ok) throw new Error(result.error || 'Не удалось открыть установщик.');
        feedback.textContent = 'Установщик открыт. Продолжите в его окне.'; feedback.dataset.error = 'false';
      } catch (error) { feedback.textContent = error.message || String(error); feedback.dataset.error = 'true'; }
      finally { runnerBusy = false; button.disabled = false; renderNav(); }
    };
  }

  // -- adb ------------------------------------------------------------------
  // Общий блок "Начать/Стоп" — используется и здесь, и apps-этапом (см.
  // renderAppsStage выше). Устройство/Wi-Fi выбираются в баре НАД этапом
  // (см. buildTransportBar/renderStagePage), сюда приходит готовым через
  // getDevice() — этот блок больше не строит свой собственный список
  // устройств (раньше дублировался в каждом типе этапа по отдельности).
  function buildStartStopButtons(panel, stage, getDevice, { startLabel, requiresDevice = true, transport = null } = {}) {
    const btnRow = el("div", { class: "stage-primary-actions" });
    const startBtn = el("button", { class: "accent", text: startLabel || "Начать этот этап" });
    if (runnerBusy) startBtn.disabled = true;
    btnRow.appendChild(startBtn);
    panel.appendChild(btnRow);
    if (stage.type === 'apps') {
      panel.querySelector('.apps08-toolbar').append(btnRow);
    }
    // Раньше (для uart/telnet/adb/actions) кнопка запуска подменяла собой
    // "Далее" в навигации (скрывала её и переезжала на её место) — техник
    // не мог пропустить этап при повторной установке, если этот шаг уже не
    // нужен (например, telnet/UART уже включили в прошлый раз). Кнопка
    // запуска остаётся прямо в блоке этапа (см. panel.appendChild(btnRow)
    // выше), "Далее" в навигации — как обычно, отдельно.

    startBtn.addEventListener("click", async () => {
      if (runnerBusy) return;
      const selected = panel._appChooser ? panel._appChooser.paths() : selectedApkPaths();
      if(stage.type==="apps"&&!selected.length){window.notice("Отметьте приложения, которые нужно установить.",{title:"Выберите приложения"});return;}
      // Wi-Fi ADB на этапе приложений: подключение НЕ требуется заранее — сначала скачиваем выбранное
      // (пока у компьютера есть интернет), затем показываем окно подключения и только потом ставим.
      const wifiFirst = stage.type === "apps" && !!transport && transport.mode() === "wifi";
      let device = getDevice();
      if (!wifiFirst && requiresDevice && !device && !(await window.confirmDialog("Не выбрано подключённое устройство ADB. Продолжить всё равно?"))) return;
      if (runnerBusy) return;
      startBtn.disabled = true;
      runnerBusy = true;
      navBackBtn.disabled=true; navNextBtn.disabled=true;
      const items=panel._appChooser ? panel._appChooser.entries() : [];
      let stopped = false; // техник нажал «Остановить» в окне (до запуска самой установки)
      const runTitle = {
        apps: 'Установка приложений', adb: 'Выполнение команд на магнитоле',
        uart: 'Подключение через UART', telnet: 'Подключение по сети',
      }[stage.type] || 'Выполняется этап';
      openStageRun({
        title: runTitle, stageIndex: stage.index, items, cancellable: true,
        icon: stage.type === 'telnet' ? 'wifi' : 'settings',
        detail: wifiFirst ? 'Сначала скачиваем приложения, затем предложим подключиться к Wi-Fi магнитолы.'
          : 'Ожидаем результат выполнения. Подробности появляются в логе.',
        onCancel: () => {
          stopped = true;
          window.pywebview.api.install_cancel_stage();
          if (wifiFirst) document.querySelector('dialog.connection-dialog')?.close(); // ждём подключения — снимаем окно
        },
        retry: () => document.querySelector('.stage-primary-actions>.accent')?.click(),
      });
      const failToStart = (message) => {
        runnerBusy = false;
        log(message);
        finishRun({ success: false, message }, () => render());
      };
      try {
        let prefetched = false;
        if (wifiFirst) {
          const pre = await window.pywebview.api.install_prefetch_apks(model.key, stage.index, selected);
          if (!pre.ok) { failToStart(pre.error || "Не удалось скачать приложения."); return; }
          device = stopped ? null : await transport.askWifi(
            "Приложения скачаны — интернет больше не нужен. Подключите компьютер к Wi-Fi магнитолы и подключитесь по ADB.");
          if (!device) {
            failToStart(stopped ? "Установка остановлена пользователем."
              : "Подключение отменено. Приложения уже скачаны — повторная установка будет быстрой.");
            return;
          }
          prefetched = true;
        }
        const result = await window.pywebview.api.install_start_stage(model.key, stage.index, device, selected,
          prefetched || prefetchedStages.has(String(stage.index)));
        if (result.ok) return;
        failToStart(result.error || "Не удалось запустить этап.");
      } catch (err) {
        failToStart(`Не удалось запустить этап: ${err.message || err}`);
      }
    });
  }

  // Wi-Fi: заранее докачать свои файлы этапа (см. install_api.prefetch_stage). Ключ — этап целиком.
  async function prefetchStageFiles(stage) {
    const key = String(stage.index);
    if (prefetchedStages.has(key)) return;
    try {
      const result = await window.pywebview.api.install_prefetch_stage(model.key, stage.index, null);
      if (result.ok) prefetchedStages.add(key);
      else if (result.error) log(`Файлы этапа заранее не скачались: ${result.error}`);
    } catch (error) { log(`Файлы этапа заранее не скачались: ${error.message || error}`); }
  }

  function renderAdbStage(panel, stage, getDevice) {
    stageInfo(panel, 'settings', 'Выполнение команд', 'Команды этого этапа выполнятся на выбранном устройстве. Ход выполнения будет показан здесь и в логе.');
    if (modelWifi) prefetchStageFiles(stage);
    buildStartStopButtons(panel, stage, getDevice);
  }

  // -- uart -------------------------------------------------------------
  // В отличие от adb-этапа, тут не нужен выбор ADB-устройства — подключение
  // идёт по последовательному порту (COM), который сам этап (см.
  // cars/_shared/uart_adb.py:open_uart, ctx.ask_choice) находит
  // автоматически или предлагает выбрать во время выполнения.
  function renderUartStage(panel, stage) {
    stageInfo(panel, 'usb', 'Подключение через UART', 'После запуска программа найдёт COM-порт или предложит выбрать его. Следуйте подсказкам во время выполнения.');
    if (stage.uart_wifi_port != null) panel.append(el('p', {class:'app-desc',text:`Порт Wi-Fi ADB для этой модели: ${stage.uart_wifi_port}`}));
    buildStartStopButtons(panel, stage, () => null, {requiresDevice:false, startLabel:'Начать подключение'});
  }

  function renderTelnetStage(panel, stage) {
    stageInfo(panel, 'wifi', 'Подключение по сети', 'Программа найдёт адрес магнитолы или предложит ввести его. Команды выполнятся после запуска этапа.');
    buildStartStopButtons(panel, stage, () => null, {requiresDevice:false, startLabel:'Начать подключение'});
  }

  // -- actions --------------------------------------------------------------
  // В отличие от остальных этапов с run — тут не одна кнопка "Начать этап", а
  // по кнопке на каждое действие (StepSpec.actions в car_generator.py),
  // технику можно нажимать их в любом порядке и по несколько раз (см.
  // onInstallFinished — успех/ошибка действия не переводит на следующий этап
  // сами по себе). Нужно ADB-устройство, как и у "adb"-этапа — команды/выдача
  // разрешений/фиктивные местоположения все идут через ctx.shell.
  function renderActionsStage(panel, stage, getDevice) {
    if ((stage.actions_connection || 'wired') === 'wifi') prefetchStageFiles(stage);
    const actions = stage.actions || [];
    const list = el('div', {class:'stage06-commands'});
    if (!actions.length) stageInfo(panel, 'settings', 'Нет доступных действий', 'Для этого этапа пока не добавлены команды.');
    const buttons = [];
    actions.forEach((action, i) => {
      const key = `${stage.index}:${i}`;
      const result = commandResults.get(key);
      const card = el('section', {class:'stage06-command','data-state':result ? (result.success?'done':'error') : 'idle'});
      const button = UsbUI.button('', 'Выполнить', 'play');
      button.disabled = runnerBusy; buttons.push(button);
      const feedback = el('p', {class:'stage06-action-status',role:'status','data-error':String(result?.success===false),text:result?.message||''});
      card.append(el('span',{class:'stage06-symbol'},[UsbUI.icon(result?.success?'check':'settings')]),el('h3',{text:action.label||`Действие ${i+1}`}),button,feedback);
      const runAction = async () => {
        if (runnerBusy) return;
        const device = getDevice();
        if (!device && !(await window.confirmDialog('Не выбрано подключённое устройство ADB. Продолжить всё равно?'))) return;
        if (runnerBusy) return;
        runnerBusy = true; activeCommand = {key,stageIndex:stage.index};
        buttons.forEach(b=>b.disabled=true); navBackBtn.disabled=true; navNextBtn.disabled=true;
        card.dataset.state='busy'; feedback.dataset.error='false'; feedback.textContent='Выполняется… Подробности — в логе.';
        button.lastChild.textContent='Выполняется…';
        openStageRun({
          title: action.label || `Действие ${i+1}`, stageIndex: stage.index, icon: 'settings', cancellable: true,
          detail: 'Выполняем команды на магнитоле. Подробности появляются в логе.',
          onCancel: () => window.pywebview.api.install_cancel_stage(),
          retry: runAction,
        });
        try {
          const result = await window.pywebview.api.install_run_action(model.key, stage.index, i, device, selectedApkPaths(),
            prefetchedStages.has(String(stage.index)));
          if (!result.ok) throw new Error(result.error||'Не удалось выполнить действие.');
        } catch (error) {
          runnerBusy=false; activeCommand=null;
          const message=error.message||String(error);
          commandResults.set(key,{success:false,message}); log(message);
          finishRun({ success: false, message }, () => render());
        }
      };
      button.onclick = runAction;
      list.append(card);
    });
    panel.append(list,el('p',{class:'app-desc',text:'Действия можно выполнять по отдельности. После завершения нажмите «Далее».'}));
  }

  // Явный уход из мастера (см. app.js: returnToCatalog, "Назад к каталогу")
  // — считается брошенной попыткой, если была реальная активность.
  function flushAbandoned() {
    flushSessionLog(false);
  }

  window.stageWizard = { init, open, flushAbandoned, isBusy:()=>runnerBusy, goBack, canGoBack:()=>historyStack.length>0 };
})();
