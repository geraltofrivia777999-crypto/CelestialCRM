/*
 * Период одной плашкой: пресеты слева, два месяца справа.
 *
 * Два поля «С:» и «по:» заставляли открывать календарь дважды и держать в
 * голове, какое из них сейчас правишь. Здесь период выбирается одним жестом:
 * «Последние 7 дней» — щелчок по строке, произвольный отрезок — два щелчка по
 * дням, а видно оба месяца сразу.
 *
 * Значения по-прежнему живут в скрытых `input[type="date"]`, которые страница
 * уже читает: доска, сохранение фильтров и перезагрузка данных остаются
 * прежними, меняется только способ выбрать даты.
 */
(function () {
  "use strict";

  // Короткие месяцы в родительном падеже: в дате читают «1 июля», а не «1 июль».
  var MONTHS_SHORT = [
    "янв.", "февр.", "мар.", "апр.", "мая", "июн.",
    "июл.", "авг.", "сент.", "окт.", "нояб.", "дек."
  ];
  var MONTHS = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"
  ];
  var WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];

  var open = null;

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function pad(value) { return (value < 10 ? "0" : "") + value; }

  function iso(date) {
    return date.getFullYear() + "-" + pad(date.getMonth() + 1) + "-" + pad(date.getDate());
  }

  /* «2026-09-02» разбираем руками: `new Date(value)` читает строку как UTC, и
     в минусовых поясах день уезжает назад. */
  function parseISO(value) {
    var match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || "").trim());
    if (!match) return null;
    var date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
    return isNaN(date.getTime()) ? null : date;
  }

  function today() {
    return window.CelestialTime && window.CelestialTime.today
      ? window.CelestialTime.today()
      : new Date();
  }

  function shift(date, days) {
    return new Date(date.getFullYear(), date.getMonth(), date.getDate() + days);
  }

  function startOfMonth(date) {
    return new Date(date.getFullYear(), date.getMonth(), 1);
  }

  function addMonths(date, delta) {
    return new Date(date.getFullYear(), date.getMonth() + delta, 1);
  }

  function endOfMonth(date) {
    return new Date(date.getFullYear(), date.getMonth() + 1, 0);
  }

  function label(date) {
    return date.getDate() + " " + MONTHS_SHORT[date.getMonth()] + " " + date.getFullYear();
  }

  /* Быстрый выбор. Каждый пресет отдаёт пару дат или null — «за всё время»,
     когда период не ограничен вовсе. */
  var PRESETS = [
    { key: "today", label: "Сегодня", range: function () { var d = today(); return [d, d]; } },
    { key: "yesterday", label: "Вчера",
      range: function () { var d = shift(today(), -1); return [d, d]; } },
    { key: "last7", label: "Последние 7 дней",
      range: function () { var d = today(); return [shift(d, -6), d]; } },
    { key: "last14", label: "Последние 14 дней",
      range: function () { var d = today(); return [shift(d, -13), d]; } },
    { key: "last30", label: "Последние 30 дней",
      range: function () { var d = today(); return [shift(d, -29), d]; } },
    { key: "last90", label: "Последние 90 дней",
      range: function () { var d = today(); return [shift(d, -89), d]; } },
    { key: "month", label: "Текущий месяц",
      range: function () { var d = today(); return [startOfMonth(d), d]; } },
    { key: "prevMonth", label: "Предыдущий месяц",
      range: function () {
        var previous = addMonths(today(), -1);
        return [startOfMonth(previous), endOfMonth(previous)];
      } },
    { key: "year", label: "Этот год",
      range: function () {
        var d = today();
        return [new Date(d.getFullYear(), 0, 1), d];
      } },
    { key: "prevYear", label: "Предыдущий год",
      range: function () {
        var year = today().getFullYear() - 1;
        return [new Date(year, 0, 1), new Date(year, 11, 31)];
      } },
    { key: "all", label: "Всё время", range: function () { return null; } }
  ];

  function sameDay(left, right) {
    return !!left && !!right && left.getTime() === right.getTime();
  }

  function within(day, from, to) {
    return !!from && !!to && day >= from && day <= to;
  }

  /* Какой пресет описывает выбранный период — чтобы строка была подсвечена,
     когда период ей и соответствует. */
  function activePreset(from, to) {
    for (var index = 0; index < PRESETS.length; index += 1) {
      var range = PRESETS[index].range();
      if (!range) {
        if (!from && !to) return PRESETS[index].key;
        continue;
      }
      if (sameDay(range[0], from) && sameDay(range[1], to)) return PRESETS[index].key;
    }
    return "";
  }

  function rangeLabel(from, to) {
    if (!from && !to) return "За всё время";
    if (from && to) {
      return sameDay(from, to) ? label(from) : label(from) + " — " + label(to);
    }
    return from ? "с " + label(from) : "по " + label(to);
  }

  /* Правый месяц листается своими стрелками, но всегда стоит позже левого:
     без своего значения — сразу следом за левым. */
  function rightMonth(state) {
    return state.right && state.right > state.month ? state.right : addMonths(state.month, 1);
  }

  function monthHtml(month, state, side) {
    var first = startOfMonth(month);
    // getDay(): воскресенье это 0, а у нас неделя с понедельника.
    var lead = (first.getDay() + 6) % 7;
    var now = today();
    var cells = [];
    for (var index = 0; index < 42; index += 1) {
      var day = new Date(first.getFullYear(), first.getMonth(), 1 - lead + index);
      var outside = day.getMonth() !== month.getMonth();
      var classes = ["cdr-day"];
      if (outside) classes.push("cdr-day--outside");
      if (sameDay(day, now)) classes.push("cdr-day--today");
      if (sameDay(day, state.from) || sameDay(day, state.to)) classes.push("cdr-day--edge");
      else if (within(day, state.from, state.to)) classes.push("cdr-day--in");
      cells.push('<button type="button" class="' + classes.join(" ") +
        '" data-cdr-day="' + iso(day) + '" tabindex="-1">' + day.getDate() + "</button>");
    }
    return '<div class="cdr-month">' +
      '<div class="cdr-month__head">' +
      '<button type="button" class="cdr-nav" data-cdr-step="-1" data-cdr-side="' + side +
      '" tabindex="-1" ' +
      'aria-label="Предыдущий месяц">' +
      '<svg width="15" height="15" viewBox="0 0 24 24" fill="none"><path d="m14 6-6 6 6 6" ' +
      'stroke="currentColor" stroke-width="2.2" stroke-linecap="round" ' +
      'stroke-linejoin="round"/></svg></button>' +
      '<span class="cdr-title">' + escapeHtml(MONTHS[month.getMonth()]) + " " +
      month.getFullYear() + "</span>" +
      '<button type="button" class="cdr-nav" data-cdr-step="1" data-cdr-side="' + side +
      '" tabindex="-1" ' +
      'aria-label="Следующий месяц">' +
      '<svg width="15" height="15" viewBox="0 0 24 24" fill="none"><path d="m10 6 6 6-6 6" ' +
      'stroke="currentColor" stroke-width="2.2" stroke-linecap="round" ' +
      'stroke-linejoin="round"/></svg></button></div>' +
      '<div class="cdr-week">' +
      WEEKDAYS.map(function (day) { return "<span>" + day + "</span>"; }).join("") +
      "</div>" +
      '<div class="cdr-grid">' + cells.join("") + "</div></div>";
  }

  /* Страница может убрать пресеты, которые ей не по силам: Meta Ads, например,
     не отдаёт статистику дальше полугода, и «Всё время» там было бы обманом. */
  function presetsFor(host) {
    var excluded = String((host && host.getAttribute("data-exclude")) || "").split(",");
    return PRESETS.filter(function (preset) { return excluded.indexOf(preset.key) < 0; });
  }

  function panelHtml(state) {
    var active = activePreset(state.from, state.to);
    return '<div class="cdr-presets">' +
      '<div class="cdr-presets__title">Быстрый выбор</div>' +
      presetsFor(state.host).map(function (preset) {
        return '<button type="button" class="cdr-preset' +
          (preset.key === active ? " is-on" : "") + '" data-cdr-preset="' + preset.key +
          '" tabindex="-1">' + escapeHtml(preset.label) + "</button>";
      }).join("") + "</div>" +
      '<div class="cdr-body">' +
      '<div class="cdr-value">' + escapeHtml(rangeLabel(state.from, state.to)) +
      (state.from && !state.to ? '<i class="cdr-value__hint">выберите конец периода</i>' : "") +
      "</div>" +
      '<div class="cdr-months">' + monthHtml(state.month, state, "left") +
      monthHtml(rightMonth(state), state, "right") + "</div>" +
      '<div class="cdr-foot">' +
      '<button type="button" class="cdr-btn" data-cdr-cancel tabindex="-1">Отмена</button>' +
      '<button type="button" class="cdr-btn cdr-btn--primary" data-cdr-apply ' +
      'tabindex="-1">Применить</button></div></div>';
  }

  function place(panel, anchor) {
    var box = anchor.getBoundingClientRect();
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
    open.host.classList.remove("is-open");
    open = null;
  }

  function redraw() {
    if (!open) return;
    open.panel.innerHTML = panelHtml(open);
    place(open.panel, open.host);
  }

  function inputs(host) {
    return {
      from: document.getElementById(host.getAttribute("data-from")),
      to: document.getElementById(host.getAttribute("data-to"))
    };
  }

  /* Значение пишем сами и сами же сообщаем об изменении: поля скрытые, и
     браузер шлёт `change` только на своё редактирование. */
  function commit(host, from, to) {
    var fields = inputs(host);
    if (fields.from) fields.from.value = from ? iso(from) : "";
    if (fields.to) fields.to.value = to ? iso(to) : "";
    paint(host);
    [fields.from, fields.to].forEach(function (field) {
      if (!field) return;
      field.dispatchEvent(new Event("input", { bubbles: true }));
      field.dispatchEvent(new Event("change", { bubbles: true }));
    });
  }

  function paint(host) {
    var fields = inputs(host);
    var from = fields.from ? parseISO(fields.from.value) : null;
    var to = fields.to ? parseISO(fields.to.value) : null;
    var button = host.querySelector("[data-cdr-open]");
    if (!button) {
      host.innerHTML = '<button type="button" class="cdr-field" data-cdr-open>' +
        '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
        '<rect x="3.5" y="5" width="17" height="15" rx="3" stroke="currentColor" ' +
        'stroke-width="1.7"/><path d="M8 3v4M16 3v4M3.5 10h17" stroke="currentColor" ' +
        'stroke-width="1.7" stroke-linecap="round"/></svg>' +
        '<span class="cdr-field__text"></span></button>';
      button = host.querySelector("[data-cdr-open]");
    }
    button.querySelector(".cdr-field__text").textContent = rangeLabel(from, to);
  }

  function openPanel(host) {
    close();
    var fields = inputs(host);
    var from = fields.from ? parseISO(fields.from.value) : null;
    var to = fields.to ? parseISO(fields.to.value) : null;
    var panel = document.createElement("div");
    panel.className = "cdr-panel";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "Период");
    document.body.appendChild(panel);
    open = {
      host: host,
      panel: panel,
      from: from,
      to: to,
      // Левый месяц — тот, где начало периода: правый показывает следующий.
      month: startOfMonth(from || to || today()),
      // Период на несколько месяцев открывается началом слева и концом справа.
      right: to ? startOfMonth(to) : null
    };
    host.classList.add("is-open");
    redraw();

    panel.addEventListener("mousedown", function (event) { event.preventDefault(); });
    panel.addEventListener("click", function (event) {
      var step = event.target.closest("[data-cdr-step]");
      if (step) {
        var delta = Number(step.getAttribute("data-cdr-step"));
        if (step.getAttribute("data-cdr-side") === "right") {
          var right = addMonths(rightMonth(open), delta);
          // Правый не встаёт на левый и раньше него: левый уходит на месяц назад.
          if (right <= open.month) open.month = addMonths(right, -1);
          open.right = right;
        } else {
          open.month = addMonths(open.month, delta);
          // Левый догнал правый — правый сдвигается следом, месяцы не совпадают.
          if (open.right && open.right <= open.month) open.right = addMonths(open.month, 1);
        }
        return redraw();
      }
      var preset = event.target.closest("[data-cdr-preset]");
      if (preset) {
        var found = PRESETS.filter(function (row) {
          return row.key === preset.getAttribute("data-cdr-preset");
        })[0];
        var range = found ? found.range() : null;
        open.from = range ? range[0] : null;
        open.to = range ? range[1] : null;
        if (open.from) open.month = startOfMonth(open.from);
        open.right = open.to ? startOfMonth(open.to) : null;
        return redraw();
      }
      var day = event.target.closest("[data-cdr-day]");
      if (day) {
        var picked = parseISO(day.getAttribute("data-cdr-day"));
        /* Первый щелчок задаёт начало, второй — конец. Щелчок раньше начала
           переносит начало: так проще, чем требовать «сначала левую дату». */
        if (!open.from || open.to || picked < open.from) {
          open.from = picked;
          open.to = null;
        } else {
          open.to = picked;
        }
        return redraw();
      }
      if (event.target.closest("[data-cdr-cancel]")) return close();
      if (event.target.closest("[data-cdr-apply]")) {
        // Начало без конца — период из одного дня: человек выбрал день и нажал
        // «Применить», и требовать второй щелчок ради того же значения незачем.
        var start = open.from;
        var end = open.to || open.from;
        var host2 = open.host;
        close();
        commit(host2, start, end);
      }
    });
  }

  function attach(host) {
    if (host.dataset.cdrReady) return;
    host.dataset.cdrReady = "1";
    paint(host);
    var fields = inputs(host);
    // Период меняют и мимо плашки — восстановлением сохранённых фильтров.
    [fields.from, fields.to].forEach(function (field) {
      if (!field) return;
      field.addEventListener("change", function () { paint(host); });
    });
  }

  function attachAll(root) {
    var host = root && root.querySelectorAll ? root : document;
    Array.prototype.forEach.call(host.querySelectorAll("[data-date-range]"), attach);
  }

  function watch() {
    attachAll(document);
    if (!window.MutationObserver) return;
    new MutationObserver(function () { attachAll(document); })
      .observe(document.documentElement, { childList: true, subtree: true });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", watch);
  } else {
    watch();
  }

  document.addEventListener("mousedown", function (event) {
    var trigger = event.target.closest ? event.target.closest("[data-cdr-open]") : null;
    if (trigger) {
      event.preventDefault();
      var host = trigger.closest("[data-date-range]");
      if (open && open.host === host) return close();
      return openPanel(host);
    }
    if (open && !event.target.closest(".cdr-panel")) close();
  }, true);

  document.addEventListener("keydown", function (event) {
    if (open && event.key === "Escape") { event.preventDefault(); close(); }
  }, true);

  window.addEventListener("scroll", function (event) {
    if (!open || (event.target.closest && event.target.closest(".cdr-panel"))) return;
    var box = open.host.getBoundingClientRect();
    if (box.bottom < 0 || box.top > window.innerHeight) return close();
    place(open.panel, open.host);
  }, true);
  window.addEventListener("resize", function () { close(); });

  /* Даты выставляют и мимо плашки — восстановлением сохранённых фильтров.
     Событий у скрытых полей при этом нет, поэтому подпись обновляют явно. */
  window.CelestialDateRange = {
    refresh: function () {
      Array.prototype.forEach.call(
        document.querySelectorAll("[data-date-range]"),
        function (host) { attach(host); paint(host); }
      );
    }
  };
})();
