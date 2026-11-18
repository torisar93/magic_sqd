/* LAB08: use Chromium's customizable select, rather than a duplicated proxy.
   Tested in the actual Windows WebView2 (Edge 152). The original select remains
   the only focusable/accessibility/form control. Programmatic value, options,
   optgroup/disabled mutations and native change events therefore need no mirror.
   Older engines and multi-select listboxes retain their native dark fallback. */
(() => {
  // Свой список WebKit/Chromium держится на Popover API (top layer). В WKWebView программы на macOS 27 (собрана с SDK
  // 12.1) base-select заявлен, а Popover API выключен — ':popover-open' «не селектор»: список открывался невидимым —
  // стрелка вверх, пунктов нет (29.10 — выбор приложения, Jolion 2026; 18.11 — «Выберите накопитель», владелец,
  // Geely Okavango, 1.1.26). Тогда — свой выпадающий список (js/select-polyfill.js), как на старых macOS.
  const popoverWorks = !!(window.CSS?.supports && CSS.supports('selector(:popover-open)'));
  const supported = popoverWorks && !!(window.CSS?.supports('appearance', 'base-select') &&
    CSS.supports('selector(::picker(select))'));
  document.documentElement.classList.toggle('select08-customizable', supported);
  document.documentElement.classList.toggle('select08-native-fallback', !supported);
  window.Select08 = Object.freeze({ mode: supported ? 'native-customizable' : 'native-dark' });
})();
