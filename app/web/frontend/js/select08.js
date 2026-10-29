/* LAB08: use Chromium's customizable select, rather than a duplicated proxy.
   Tested in the actual Windows WebView2 (Edge 152). The original select remains
   the only focusable/accessibility/form control. Programmatic value, options,
   optgroup/disabled mutations and native change events therefore need no mirror.
   Older engines and multi-select listboxes retain their native dark fallback. */
(() => {
  // WebKit (macOS 27) тоже умеет base-select, но в окне выбора приложения («Запустить приложения», «Выдать
  // разрешения»…) его список открывался невидимым — стрелка повернулась, пунктов нет (жалоба техника на Jolion 2026,
  // 29.10.2026; на Windows те же кнопки работают). Проверено только в Chromium — в WebKit оставляем свою замену
  // (js/select-polyfill.js), как до macOS 27.
  const webkit = /AppleWebKit/.test(navigator.userAgent) && !/Chrome|Chromium|Edg\//.test(navigator.userAgent);
  const supported = !webkit && !!(window.CSS?.supports('appearance', 'base-select') &&
    CSS.supports('selector(::picker(select))'));
  document.documentElement.classList.toggle('select08-customizable', supported);
  document.documentElement.classList.toggle('select08-native-fallback', !supported);
  window.Select08 = Object.freeze({ mode: supported ? 'native-customizable' : 'native-dark' });
})();
