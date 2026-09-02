/*
 * Единый вид календарей.
 *
 * Само поле `<input type="date">` мы стилизуем: рамка, скругление, шрифт. А
 * календарь, который браузер открывает по клику, не стилизуется вообще — это
 * окно операционной системы. На фоне CRM он выглядит чужим: другой шрифт,
 * синяя подсветка, свои кнопки «Удалить» и «Сегодня».
 *
 * Поэтому здесь заменяется только всплывающий календарь, а не поле. Ни одна
 * страница не переписывается: поле остаётся `type="date"` со своим значением
 * в формате `ГГГГ-ММ-ДД`, весь существующий код продолжает читать `.value` и
 * слушать `change`. Мы лишь перехватываем открытие и рисуем свой месяц.
 */
(function () {
  "use strict";

  var MONTHS = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
  ];
  // Неделя начинается с понедельника: рабочий календарь, а не американский.
  var WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

  var open = null;

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function skip(input) {
    return !input || input.disabled || input.hasAttribute("data-native-date");
  }

  /* Родной календарь глушится не отменой события, а `readonly`: у такого поля
     Chromium свой пикер не открывает ни по иконке, ни по клику в поле, а
     значение и формат показа остаются прежними. Правку значения забирает наша
     панель, так что печатать в поле всё равно нечего. */
  function tame(input) {
    if (skip(input) || input.dataset.cdateTamed) return;
    input.dataset.cdateTamed = "1";
    input.readOnly = true;
  }

  function tameAll(root) {
    var host = root && root.querySelectorAll ? root : document;
    Array.prototype.forEach.call(host.querySelectorAll('input[type="date"]'), tame);
  }

  function pad(value) {
    return (value < 10 ? "0" : "") + value;
  }

  function iso(date) {
    return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate());
  }

  /* Дату разбираем руками, а не `new Date(value)`: строку «2026-09-02» браузер
     читает как UTC, и в минусовых поясах календарь открывался на день раньше. */
  function parseISO(value) {
    var match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || "").trim());
    if (!match) return null;
    var date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
    return isNaN(date.getTime()) ? null : date;
  }

  function startOfMonth(date) {
    return new Date(date.getFullYear(), date.getMonth(), 1);
  }

  function addMonths(date, delta) {
    return new Date(date.getFullYear(), date.getMonth() + delta, 1);
  }

  function limitDate(input, name) {
    return parseISO(input.getAttribute(name));
  }

  function outOfRange(input, date) {
    var min = limitDate(input, "min");
    var max = limitDate(input, "max");
    if (min && date < min) return true;
    return !!(max && date > max);
  }

  function gridHtml(input, month, selected) {
    var today = new Date();
    today.setHours(0, 0, 0, 0);
    var first = startOfMonth(month);
    // getDay(): воскресенье это 0, а у нас неделя с понедельника.
    var lead = (first.getDay() + 6) % 7;
    var cursor = new Date(first.getFullYear(), first.getMonth(), 1 - lead);
    var cells = [];
    for (var index = 0; index < 42; index += 1) {
      var day = new Date(cursor.getFullYear(), cursor.getMonth(), cursor.getDate() + index);
      var outside = day.getMonth() !== month.getMonth();
      var disabled = outOfRange(input, day);
      var classes = ["cdate-day"];
      if (outside) classes.push("cdate-day--outside");
      if (selected && day.getTime() === selected.getTime()) classes.push("cdate-day--on");
      if (day.getTime() === today.getTime()) classes.push("cdate-day--today");
      cells.push('<button type="button" class="' + classes.join(" ") +
        '" data-cdate-day="' + iso(day) + '"' + (disabled ? " disabled" : "") +
        ' tabindex="-1">' + day.getDate() + "</button>");
    }
    return cells.join("");
  }

  function panelHtml(input, month, selected) {
    return '<div class="cdate-head">' +
      '<button type="button" class="cdate-nav" data-cdate-step="-1" tabindex="-1" ' +
      'aria-label="Предыдущий месяц">' +
      '<svg width="15" height="15" viewBox="0 0 24 24" fill="none"><path d="m14 6-6 6 6 6" ' +
      'stroke="currentColor" stroke-width="2.2" stroke-linecap="round" ' +
      'stroke-linejoin="round"/></svg></button>' +
      '<span class="cdate-title">' + escapeHtml(MONTHS[month.getMonth()]) + " " +
      month.getFullYear() + "</span>" +
      '<button type="button" class="cdate-nav" data-cdate-step="1" tabindex="-1" ' +
      'aria-label="Следующий месяц">' +
      '<svg width="15" height="15" viewBox="0 0 24 24" fill="none"><path d="m10 6 6 6-6 6" ' +
      'stroke="currentColor" stroke-width="2.2" stroke-linecap="round" ' +
      'stroke-linejoin="round"/></svg></button></div>' +
      '<div class="cdate-week">' +
      WEEKDAYS.map(function (day) { return "<span>" + day + "</span>"; }).join("") +
      "</div>" +
      '<div class="cdate-grid">' + gridHtml(input, month, selected) + "</div>" +
      '<div class="cdate-foot">' +
      '<button type="button" class="cdate-link" data-cdate-clear tabindex="-1">Очистить</button>' +
      '<button type="button" class="cdate-link cdate-link--accent" data-cdate-today ' +
      'tabindex="-1">Сегодня</button></div>';
  }

  function place(panel, input) {
    /* Панель прибита к окну, а не к полю: поля дат стоят и в модалках с
       прокруткой, где панель обрезалась бы их краем. */
    var box = input.getBoundingClientRect();
    panel.style.left = Math.max(
      8, Math.min(box.left, window.innerWidth - panel.offsetWidth - 8)
    ) + "px";
    var below = window.innerHeight - box.bottom - 10;
    if (below >= panel.offsetHeight || below >= box.top - 10) {
      panel.style.top = box.bottom + 6 + "px";
    } else {
      panel.style.top = Math.max(8, box.top - panel.offsetHeight - 6) + "px";
    }
  }

  function close() {
    if (!open) return;
    open.panel.remove();
    open.input.removeAttribute("aria-expanded");
    open = null;
  }

  function redraw() {
    if (!open) return;
    open.panel.innerHTML = panelHtml(open.input, open.month, open.selected);
    place(open.panel, open.input);
  }

  /* Значение пишем сами и сами же сообщаем об изменении: поле меняют не
     клавиатурой, а браузер шлёт `change` только на своё редактирование. */
  function apply(value) {
    if (!open) return;
    var input = open.input;
    input.value = value;
    close();
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function openPanel(input) {
    close();
    var selected = parseISO(input.value);
    var panel = document.createElement("div");
    panel.className = "cdate-panel";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "Календарь");
    document.body.appendChild(panel);
    open = {
      input: input,
      panel: panel,
      selected: selected,
      month: startOfMonth(selected || new Date())
    };
    input.setAttribute("aria-expanded", "true");
    redraw();

    panel.addEventListener("mousedown", function (event) {
      // Клик по панели не должен уводить фокус с поля: иначе на каждом нажатии
      // страница дёргалась бы, а поле теряло рамку выбранного.
      event.preventDefault();
    });
    panel.addEventListener("click", function (event) {
      var step = event.target.closest("[data-cdate-step]");
      if (step) {
        open.month = addMonths(open.month, Number(step.getAttribute("data-cdate-step")));
        redraw();
        return;
      }
      if (event.target.closest("[data-cdate-clear]")) return apply("");
      if (event.target.closest("[data-cdate-today]")) {
        var today = new Date();
        today.setHours(0, 0, 0, 0);
        if (outOfRange(input, today)) return;
        return apply(iso(today));
      }
      var day = event.target.closest("[data-cdate-day]");
      if (day && !day.disabled) apply(day.getAttribute("data-cdate-day"));
    });
  }

  function move(days) {
    if (!open) return;
    var base = open.selected || new Date();
    var next = new Date(base.getFullYear(), base.getMonth(), base.getDate() + days);
    if (outOfRange(open.input, next)) return;
    open.selected = next;
    open.month = startOfMonth(next);
    redraw();
  }

  /* Поля появляются и после загрузки — модалки и строки таблиц собираются на
     ходу, поэтому новые узлы приходится ловить наблюдателем. */
  function watch() {
    tameAll(document);
    if (!window.MutationObserver) return;
    new MutationObserver(function (records) {
      records.forEach(function (record) {
        Array.prototype.forEach.call(record.addedNodes, function (node) {
          if (node.nodeType !== 1) return;
          if (node.matches && node.matches('input[type="date"]')) tame(node);
          else tameAll(node);
        });
      });
    }).observe(document.documentElement, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", watch);
  } else {
    watch();
  }

  document.addEventListener("mousedown", function (event) {
    var input = event.target.closest ? event.target.closest('input[type="date"]') : null;
    if (input && !skip(input)) {
      // Поле могло появиться между двумя тиками наблюдателя.
      tame(input);
      event.preventDefault();
      if (open && open.input === input) return close();
      openPanel(input);
      input.focus({ preventScroll: true });
      return;
    }
    if (open && !event.target.closest(".cdate-panel")) close();
  }, true);

  document.addEventListener("keydown", function (event) {
    if (!open) {
      var input = event.target;
      if (!input || input.tagName !== "INPUT" || input.type !== "date" || skip(input)) return;
      if (event.key === "Enter" || event.key === " " ||
        (event.key === "ArrowDown" && event.altKey)) {
        event.preventDefault();
        openPanel(input);
      }
      return;
    }
    if (event.key === "Escape") { event.preventDefault(); return close(); }
    if (event.key === "Enter") {
      event.preventDefault();
      return open.selected ? apply(iso(open.selected)) : close();
    }
    if (event.key === "ArrowLeft") { event.preventDefault(); return move(-1); }
    if (event.key === "ArrowRight") { event.preventDefault(); return move(1); }
    if (event.key === "ArrowUp") { event.preventDefault(); return move(-7); }
    if (event.key === "ArrowDown") { event.preventDefault(); return move(7); }
    if (event.key === "PageUp" || event.key === "PageDown") {
      event.preventDefault();
      open.month = addMonths(open.month, event.key === "PageUp" ? -1 : 1);
      redraw();
    }
  }, true);

  window.addEventListener("scroll", function (event) {
    if (!open || (event.target.closest && event.target.closest(".cdate-panel"))) return;
    var box = open.input.getBoundingClientRect();
    // Поле уехало за край экрана — панели больше не к чему прижиматься.
    if (box.bottom < 0 || box.top > window.innerHeight) return close();
    place(open.panel, open.input);
  }, true);
  window.addEventListener("resize", function () { close(); });
})();
