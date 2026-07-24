(function () {
  "use strict";

  var pages = {
    "Dashboard": "/",
    "Медиаборд": "/Mediaboard.dc.html",
    "Финансы": "/Finance.dc.html",
    "Партнёрки": "/Partners.dc.html",
    "Оффера": "/Offers.dc.html",
    "Команда": "/Team.dc.html",
    "Настройки": "/Settings.dc.html"
  };

  function pageName(element) {
    var text = (element.textContent || "").replace(/\s+/g, " ").trim();
    return Object.keys(pages).find(function (name) {
      return text === name || text.indexOf(name + " ") === 0;
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    var candidates = document.querySelectorAll("nav a, nav > div");

    candidates.forEach(function (element) {
      var name = pageName(element);
      if (!name) return;

      var target = pages[name];
      if (element.tagName === "A") {
        element.setAttribute("href", target);
        return;
      }

      element.setAttribute("role", "link");
      element.setAttribute("tabindex", "0");
      element.style.cursor = "pointer";
      element.addEventListener("click", function () {
        window.location.href = target;
      });
      element.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          window.location.href = target;
        }
      });
    });
  });
})();
