/* LAB08: use Chromium's customizable select, rather than a duplicated proxy.
   Tested in the actual Windows WebView2 (Edge 152). The original select remains
   the only focusable/accessibility/form control. Programmatic value, options,
   optgroup/disabled mutations and native change events therefore need no mirror.
   Older engines and multi-select listboxes retain their native dark fallback. */
(() => {
  const supported = !!(window.CSS?.supports('appearance', 'base-select') &&
    CSS.supports('selector(::picker(select))'));
  document.documentElement.classList.toggle('select08-customizable', supported);
  document.documentElement.classList.toggle('select08-native-fallback', !supported);
  window.Select08 = Object.freeze({ mode: supported ? 'native-customizable' : 'native-dark' });
})();
