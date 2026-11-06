// Тема ставится до отрисовки, чтобы не мигала светлая страница в тёмной теме
(function () {
  try {
    var t = localStorage.getItem("hh-panel-theme");
    if (t === "light" || t === "dark") document.documentElement.setAttribute("data-theme", t);
  } catch (e) { /* приватный режим: просто системная тема */ }
})();
