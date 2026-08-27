// Перетаскиваемый разделитель левой/правой панели — замена ttk.Panedwindow
// (единственного оставшегося ttk-виджета в старом интерфейсе). Ширина левой
// колонки хранится в localStorage, чтобы не сбрасываться между запусками.
(function () {
  const STORAGE_KEY = "magicsqd.leftPanelWidth";
  const MIN_WIDTH = 380;
  const DEFAULT_WIDTH = 480;

  function initResizer(shellEl, handleEl) {
    const saved = parseInt(localStorage.getItem(STORAGE_KEY), 10);
    const initialWidth = Number.isNaN(saved) ? DEFAULT_WIDTH : Math.max(MIN_WIDTH, saved);
    // На стартовом экране каталог намеренно занимает всё окно. Inline-стиль
    // здесь сильнее CSS-класса и раньше возвращал ему старую узкую колонку.
    if (!shellEl.classList.contains("catalog-home")) {
      shellEl.style.gridTemplateColumns = `${initialWidth}px 6px 1fr`;
    }

    let dragging = false;

    handleEl.addEventListener("mousedown", (e) => {
      dragging = true;
      shellEl.classList.add("resizing");
      e.preventDefault();
    });

    window.addEventListener("mousemove", (e) => {
      if (!dragging) return;
      const width = Math.min(Math.max(MIN_WIDTH, e.clientX), window.innerWidth - 420);
      shellEl.style.gridTemplateColumns = `${width}px 6px 1fr`;
    });

    window.addEventListener("mouseup", () => {
      if (!dragging) return;
      dragging = false;
      shellEl.classList.remove("resizing");
      const width = shellEl.style.gridTemplateColumns.split(" ")[0].replace("px", "");
      localStorage.setItem(STORAGE_KEY, width);
    });
  }

  window.initResizer = initResizer;
})();
