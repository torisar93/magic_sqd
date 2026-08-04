// Перетаскиваемый разделитель левой/правой панели — замена ttk.Panedwindow
// (единственного оставшегося ttk-виджета в старом интерфейсе). Ширина левой
// колонки хранится в localStorage, чтобы не сбрасываться между запусками.
(function () {
  const STORAGE_KEY = "magicsqd.leftPanelWidth";
  const MIN_WIDTH = 240;

  function initResizer(shellEl, handleEl) {
    const saved = parseInt(localStorage.getItem(STORAGE_KEY), 10);
    if (!Number.isNaN(saved)) {
      shellEl.style.gridTemplateColumns = `${saved}px 4px 1fr`;
    }

    let dragging = false;

    handleEl.addEventListener("mousedown", (e) => {
      dragging = true;
      shellEl.classList.add("resizing");
      e.preventDefault();
    });

    window.addEventListener("mousemove", (e) => {
      if (!dragging) return;
      const width = Math.max(MIN_WIDTH, e.clientX);
      shellEl.style.gridTemplateColumns = `${width}px 4px 1fr`;
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
