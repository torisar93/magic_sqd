/* Настройки и правила с сервера (app/client_config.py, content/config/client.json; владелец, 2026-10-06: «чтобы
   обновлений приложения стало поменьше»). Интерфейс берёт копию у программы при запуске и свежую — событием
   client_config_updated после фоновой загрузки. Отсюда — правила окон «что сделать» (user_errors.js:
   UserErrors.setRemote), а также тексты, флажки и настройки (ClientConfig.text/flag/setting) для правок без выпуска.

   Правило можно ограничить версиями и платформой ("min_app", "max_app", "platforms": ["pc"|"android"]) — так же,
   как в app/client_config.py. ПК отдаёт уже отобранные правила, Android — файл как есть, поэтому отбор и здесь.

   Файл ОДИНАКОВЫЙ на ПК (app/web/frontend/js/client_config.js) и Android
   (android/app/src/main/assets/js/client_config.js) — см. tests/test_shared_frontend_copies.py. */
(() => {
  let config = {};
  let androidVersion = null;
  const listeners = [];

  function platform() {
    return window.AndroidBridge ? "android" : "pc";
  }

  function appVersion() {
    if (window.AndroidBridge) {
      if (androidVersion === null) {
        try { androidVersion = String((window.Bridge.call("app_version", {}) || {}).version || ""); }
        catch (err) { androidVersion = ""; }
      }
      return androidVersion;
    }
    return String((window.appInfo && window.appInfo.app_version) || "");
  }

  function versionTuple(text) {
    return String(text).trim().replace(/^[vV]/, "").split(".").map((chunk) => {
      const digits = /^\d+/.exec(chunk);
      return digits ? Number(digits[0]) : 0;
    });
  }

  function compareVersions(a, b) {
    const x = versionTuple(a), y = versionTuple(b);
    for (let i = 0; i < Math.max(x.length, y.length); i += 1) {
      const d = (x[i] || 0) - (y[i] || 0);
      if (d) return d < 0 ? -1 : 1;
    }
    return 0;
  }

  function applies(rule) {
    if (!rule || typeof rule !== "object") return false;
    if (Array.isArray(rule.platforms) && rule.platforms.length && !rule.platforms.includes(platform())) return false;
    const version = appVersion();
    if (version) {
      if (typeof rule.min_app === "string" && compareVersions(version, rule.min_app) < 0) return false;
      if (typeof rule.max_app === "string" && compareVersions(version, rule.max_app) > 0) return false;
    }
    return true;
  }

  function apply(next) {
    config = next && typeof next === "object" && !Array.isArray(next) ? next : {};
    if (window.UserErrors && window.UserErrors.setRemote) {
      window.UserErrors.setRemote(Array.isArray(config.user_errors) ? config.user_errors.filter(applies) : []);
    }
    for (const listener of listeners) {
      try { listener(config); } catch (err) { console.error("client_config listener failed", err); }
    }
  }

  function typed(section, key, fallback) {
    const values = config[section];
    if (!values || typeof values !== "object" || Array.isArray(values) || !(key in values)) return fallback;
    const value = values[key];
    if (fallback !== undefined && fallback !== null && typeof value !== typeof fallback) return fallback;
    return value;
  }

  window.ClientConfig = {
    get: () => config,
    setting: (key, fallback) => typed("settings", key, fallback),
    flag: (key, fallback) => !!typed("flags", key, !!fallback),
    text: (key, fallback) => typed("texts", key, fallback),
    onChange: (listener) => { listeners.push(listener); },
    applies,
    apply,
  };

  function load() {
    if (window.AndroidBridge) {
      try { apply(window.Bridge.call("client_config", {})); } catch (err) { /* нет копии — встроенные правила */ }
      return;
    }
    const api = window.pywebview && window.pywebview.api;
    if (api && api.client_config) api.client_config().then(apply, () => {});
  }

  if (window.events && window.events.on) window.events.on("client_config_updated", (event) => apply(event.config));
  if (window.AndroidBridge || (window.pywebview && window.pywebview.api)) load();
  else window.addEventListener("pywebviewready", load, { once: true });
})();
