(function () {
  "use strict";

  var api = window.CelestialAPI;
  if (!api) return;

  /* ---------- helpers ---------- */

  function byId(id) { return document.getElementById(id); }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
  }

  function num(value) {
    if (value == null || value === "") return "–";
    return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(Number(value));
  }

  function money(value) {
    if (value == null || value === "") return "–";
    return new Intl.NumberFormat("en-US", {
      style: "currency", currency: "USD", maximumFractionDigits: 2
    }).format(Number(value));
  }

  function percent(value) {
    if (value == null || !isFinite(value)) return "–";
    // Past a few hundred percent the decimal is noise, and it costs two characters
    // in a column that has none to spare.
    var digits = Math.abs(Number(value)) >= 1000 ? 0 : 1;
    return new Intl.NumberFormat("ru-RU", {
      minimumFractionDigits: 0, maximumFractionDigits: digits
    }).format(Number(value)) + "%";
  }

  /* Drops a trailing ".0" / ".00" so 1.00M reads as 1M. */
  function scaled(value, digits) {
    return String(Number(value.toFixed(digits)));
  }

  function compactNumber(value) {
    var abs = Math.abs(value);
    if (abs >= 1e9) return scaled(value / 1e9, 2) + "B";
    if (abs >= 1e6) return scaled(value / 1e6, 2) + "M";
    return num(value);
  }

  function compactMoney(value) {
    var abs = Math.abs(value);
    if (abs >= 1e9) return "$" + scaled(value / 1e9, 2) + "B";
    if (abs >= 1e6) return "$" + scaled(value / 1e6, 2) + "M";
    // Below $10k the exact figure still fits, and cents matter there (CPD, small spend).
    if (abs >= 1e4) return "$" + scaled(value / 1e3, 1) + "K";
    return money(value);
  }

  var DASH = '<span style="color:#C9BFBF">–</span>';

  /* Returns the cell text plus the exact value for a tooltip, whenever the two differ. */
  function formatCell(kind, value, compact) {
    if (value == null || value === "" || !isFinite(Number(value))) {
      return { text: DASH, title: "" };
    }
    var amount = Number(value);
    if (kind === "percent") {
      var rendered = percent(amount);
      return { text: rendered, title: rendered };
    }
    var full = kind === "money" ? money(amount) : num(amount);
    if (!compact) return { text: full, title: full };
    var short = kind === "money" ? compactMoney(amount) : compactNumber(amount);
    return { text: short, title: full };
  }

  function toast(message, kind) {
    var current = byId("celestialLiveToast");
    if (current) current.remove();
    var element = document.createElement("div");
    element.id = "celestialLiveToast";
    element.textContent = message;
    element.style.cssText =
      "position:fixed;right:24px;bottom:24px;z-index:99999;max-width:420px;" +
      "padding:13px 17px;border-radius:11px;color:#fff;font:700 12px Inter,sans-serif;" +
      "box-shadow:0 14px 38px rgba(23,17,17,.22);background:" +
      (kind === "error" ? "#FF0000" : kind === "info" ? "#B91414" : "#16B57F");
    document.body.appendChild(element);
    window.setTimeout(function () { element.remove(); }, 5000);
  }

  function hasPermission(user, code) {
    if (!user || !user.role) return false;
    return (user.role.permissions || []).some(function (permission) {
      return permission.code === "*" || permission.code === code;
    });
  }

  function ensureMediaModalStyles() {
    if (byId("mediaEditModalStyles")) return;
    var style = document.createElement("style");
    style.id = "mediaEditModalStyles";
    style.textContent =
      ".board-edit-overlay--media{align-items:center!important;padding:24px!important;" +
      "overflow:hidden!important;font-family:'Inter',-apple-system,'Helvetica Neue',sans-serif}" +
      ".board-edit-overlay--media .board-edit-card{width:min(880px,calc(100vw - 48px))!important;" +
      "max-width:none!important;max-height:calc(100vh - 48px);padding:0!important;overflow:hidden;" +
      "display:flex;flex-direction:column;border:1px solid rgba(225,228,237,.82);" +
      "box-shadow:0 28px 80px rgba(18,12,12,.28)!important}" +
      ".board-edit-overlay--media .board-edit-card:focus{outline:none}" +
      ".board-edit-overlay--media .board-edit-header{padding:21px 24px 18px!important;" +
      "margin:0!important;border-bottom:1px solid #EBE6E6;flex-shrink:0}" +
      ".board-edit-overlay--media .board-edit-title{font-family:'Alumni Sans','Inter',sans-serif!important;" +
      "font-size:20px!important;line-height:1.2;letter-spacing:-.25px}" +
      ".board-edit-overlay--media .board-edit-subtitle{display:block!important;color:#9B9292;" +
      "font-size:11px;font-weight:600;line-height:1.45;margin-top:5px}" +
      ".board-edit-overlay--media .board-edit-close{display:flex;align-items:center;justify-content:center;" +
      "flex-shrink:0;color:#857D7D!important;transition:background .18s,color .18s}" +
      ".board-edit-overlay--media .board-edit-close:hover{background:#EBE6E6!important;color:#5A5050!important}" +
      ".board-edit-overlay--media .board-edit-body{padding:20px 24px 22px!important;overflow-y:auto;" +
      "overscroll-behavior:contain;scrollbar-gutter:stable}" +
      ".board-edit-overlay--media .board-edit-context{padding:14px;background:#F8F5F5;" +
      "border:1px solid #EBE6E6;border-radius:14px}" +
      ".board-edit-overlay--media .board-edit-main-grid{grid-template-columns:150px 190px minmax(0,1fr)!important;" +
      "gap:12px!important;margin:0!important}" +
      ".board-edit-overlay--media .board-edit-field{min-width:0;gap:6px!important;" +
      "font-size:10.5px!important;color:#6A6161!important}" +
      ".board-edit-overlay--media .board-edit-input,.board-edit-overlay--media .board-edit-select{" +
      "display:block;width:100%;min-width:0;height:42px;border:1px solid #E5DFDF!important;" +
      "border-radius:10px!important;padding:0 12px!important;background:#fff;color:#2A2020!important;" +
      "font-family:'Inter',sans-serif!important;font-size:12.5px!important;font-weight:600!important;" +
      "outline:none;text-overflow:ellipsis;transition:border-color .18s,box-shadow .18s}" +
      ".board-edit-overlay--media .board-edit-input:focus,.board-edit-overlay--media .board-edit-select:focus{" +
      "border-color:#D06060!important;box-shadow:0 0 0 3px rgba(185,20,20,.09)}" +
      // Safari ставит значение даты по центру поля, и «01.09.2026» висело
      // отдельно от подписи и от соседних полей.
      '.board-edit-overlay--media .board-edit-input[type="date"]{text-align:left}' +
      '.board-edit-overlay--media .board-edit-input[type="date"]::-webkit-datetime-edit{padding:0;' +
      "text-align:left}" +
      '.board-edit-overlay--media .board-edit-input[type="date"]::-webkit-date-and-time-value{' +
      "margin:0;text-align:left}" +
      '.board-edit-overlay--media .board-edit-input[type="date"]::-webkit-calendar-picker-indicator{' +
      "margin-left:auto;opacity:.5;cursor:pointer}" +
      // Агенты добавляются строками, поэтому у секции своя раскладка.
      ".board-edit-overlay--media .board-edit-agents{display:grid;gap:9px}" +
      ".board-edit-overlay--media .board-edit-agent{display:grid;align-items:end;gap:10px;" +
      "grid-template-columns:minmax(0,1fr) 190px 38px}" +
      ".board-edit-overlay--media .board-edit-agent-drop{display:flex;align-items:center;" +
      "justify-content:center;width:38px;height:42px;border:1px solid #EBE6E6;border-radius:10px;" +
      "background:#fff;color:#9B9292;cursor:pointer;transition:border-color .18s,background .18s,color .18s}" +
      ".board-edit-overlay--media .board-edit-agent-drop:hover{border-color:#E7C9C9;" +
      "background:#FCF1F1;color:#B91414}" +
      ".board-edit-overlay--media .board-edit-agents-empty{color:#9B9292;font-size:11.5px;font-weight:600}" +
      ".board-edit-overlay--media .board-edit-agents-empty[hidden]{display:none}" +
      ".board-edit-overlay--media .board-edit-agent-add{display:inline-flex;align-items:center;gap:7px;" +
      "margin-top:11px;height:38px;padding:0 14px;border:1px dashed #D9CFCF;border-radius:10px;" +
      "background:#fff;font:700 12px 'Inter',sans-serif;color:#B91414;cursor:pointer;" +
      "transition:border-color .18s,background .18s}" +
      ".board-edit-overlay--media .board-edit-agent-add:hover{border-color:#D06060;background:#FCF7F7}" +
      ".board-edit-overlay--media .board-edit-agent-add:disabled{border-color:#EBE6E6;" +
      "background:#FBF9F9;color:#9B9292;cursor:default}" +
      ".board-edit-overlay--media .board-edit-section{margin-top:0;padding:14px;" +
      "border:1px solid #EBE6E6;border-radius:14px;background:#fff}" +
      // Подпись блока стоит над рамкой и выглядит как подписи полей рядом:
      // внутри рамки она читалась как часть содержимого, а не как её название.
      ".board-edit-overlay--media .board-edit-section-label{margin:16px 0 7px;" +
      "font:700 11px 'Inter',sans-serif;letter-spacing:.6px;text-transform:uppercase;" +
      "color:#857D7D}" +
      ".board-edit-overlay--media .board-edit-grid{display:grid!important;" +
      "grid-template-columns:repeat(auto-fit,minmax(145px,1fr))!important;gap:10px!important}" +
      ".board-edit-overlay--media .board-edit-footer{display:flex;align-items:center;" +
      "justify-content:flex-end;gap:10px;margin:0!important;padding:15px 24px 18px!important;" +
      "border-top:1px solid #EBE6E6;background:#fff;flex-shrink:0}" +
      ".board-edit-overlay--media .board-edit-button{height:42px;padding:0 18px!important;" +
      "border-radius:10px!important;font-family:'Inter',sans-serif!important;font-size:12px!important}" +
      ".board-edit-overlay--media .board-edit-save:disabled{cursor:wait;opacity:.68}" +
      "@media(max-width:760px){" +
      ".board-edit-overlay--media{padding:14px!important}" +
      ".board-edit-overlay--media .board-edit-card{width:calc(100vw - 28px)!important;" +
      "max-height:calc(100vh - 28px)}" +
      ".board-edit-overlay--media .board-edit-main-grid{grid-template-columns:1fr 1fr!important}" +
      ".board-edit-overlay--media .board-edit-offer-field{grid-column:1/-1}" +
      ".board-edit-overlay--media .board-edit-body{padding:16px!important}" +
      ".board-edit-overlay--media .board-edit-header{padding:18px 18px 15px!important}" +
      ".board-edit-overlay--media .board-edit-footer{padding:13px 16px 15px!important}" +
      "}" +
      "@media(max-width:620px){" +
      ".board-edit-overlay--media .board-edit-agent{grid-template-columns:minmax(0,1fr) 38px}" +
      ".board-edit-overlay--media .board-edit-agent > label:first-child{grid-area:1/1}" +
      ".board-edit-overlay--media .board-edit-agent > label:nth-child(2){grid-area:2/1}" +
      ".board-edit-overlay--media .board-edit-agent-drop{grid-area:1/2/3/3;height:100%}" +
      "}" +
      "@media(max-width:480px){" +
      ".board-edit-overlay--media .board-edit-main-grid{grid-template-columns:1fr!important}" +
      ".board-edit-overlay--media .board-edit-offer-field{grid-column:auto}" +
      ".board-edit-overlay--media .board-edit-grid{grid-template-columns:repeat(2,minmax(0,1fr))!important}" +
      ".board-edit-overlay--media .board-edit-section{padding:12px}" +
      ".board-edit-overlay--media .board-edit-footer{display:grid;grid-template-columns:1fr 1fr}" +
      ".board-edit-overlay--media .board-edit-button{width:100%;padding:0 10px!important}" +
      "}";
    document.head.appendChild(style);
  }

  function ensureFinanceModalStyles() {
    if (byId("financeEditModalStyles")) return;
    ensureMediaModalStyles();
    var mediaStyles = byId("mediaEditModalStyles");
    var style = document.createElement("style");
    style.id = "financeEditModalStyles";
    style.textContent = mediaStyles.textContent.replace(/--media/g, "--finance") +
      ".board-edit-overlay--finance .board-edit-grid--metrics{" +
      "grid-template-columns:repeat(4,minmax(0,1fr))!important}" +
      ".board-edit-overlay--finance .board-edit-field--wide{grid-column:span 2}" +
      "@media(max-width:760px){" +
      ".board-edit-overlay--finance .board-edit-grid--metrics{" +
      "grid-template-columns:repeat(2,minmax(0,1fr))!important}" +
      ".board-edit-overlay--finance .board-edit-field--wide{grid-column:1/-1}" +
      "}";
    document.head.appendChild(style);
  }

  function ensureStructureStyles() {
    if (byId("celestialStructureStyles")) return;
    var style = document.createElement("style");
    style.id = "celestialStructureStyles";
    style.textContent =
      ".cs-slot{display:inline-flex;align-items:center;gap:8px;flex-shrink:0}" +
      ".cs-slot:first-child > .cs-sep{display:none}" +
      ".cs-sep{display:inline-flex;align-items:center;color:#C9BFBF;flex-shrink:0}" +
      // touch-action:none lets a finger drag the chip instead of scrolling the page.
      ".cs-chip{display:inline-flex;align-items:center;gap:7px;border:1px solid transparent;" +
      "border-radius:10px;padding:6px 11px 6px 8px;font:700 12.5px 'Inter',sans-serif;" +
      "background:#FCF1F1;color:#B91414;cursor:grab;user-select:none;touch-action:none;" +
      "transition:background .18s,color .18s,border-color .18s,box-shadow .18s,transform .12s}" +
      '.cs-chip[data-on="0"]{background:#F7F4F4;color:#9B9292}' +
      ".cs-chip:hover{border-color:rgba(185,20,20,.28)}" +
      '.cs-chip[data-on="0"]:hover{border-color:#E3DBDB}' +
      ".cs-chip:focus-visible{outline:none;border-color:#D06060;box-shadow:0 0 0 3px rgba(185,20,20,.16)}" +
      ".cs-grip{display:inline-flex;flex-shrink:0;opacity:.5;transition:opacity .18s}" +
      ".cs-chip:hover .cs-grip{opacity:.95}" +
      // Pressing feedback: the chip dips a touch before it lifts off.
      ".cs-chip--pressed{transform:scale(.96)}" +
      // What stays behind in the row while the clone follows the pointer.
      ".cs-chip--ghost{background:#F1F2F7;border:1px dashed #C9BFBF;color:transparent;box-shadow:none}" +
      ".cs-chip--ghost .cs-grip{visibility:hidden}" +
      ".cs-chip--flying{position:fixed;z-index:10060;margin:0;pointer-events:none;cursor:grabbing;" +
      "border-color:rgba(185,20,20,.4);box-shadow:0 18px 38px rgba(23,17,17,.24);" +
      "transform:scale(1.06) rotate(-1.5deg)}" +
      "body.cs-dragging{cursor:grabbing}" +
      "body.cs-dragging .cs-chip{cursor:grabbing}" +
      "@media(prefers-reduced-motion:reduce){.cs-chip,.cs-slot{transition:none!important}}";
    document.head.appendChild(style);
  }

  /* ---------- фильтр с несколькими значениями ---------- */

  /* Обычный <select> держит одно значение, а на медиаборде так не смотрят:
   * «эти три баера», «Аргентина и Австралия» — обычный вопрос, и раньше на него
   * приходилось отвечать тремя заходами подряд. Поле держит набор значений:
   * выбранное показывается плашкой с крестиком, снять можно точечно.
   *
   * Панель списка — та же csel-*, что у одиночных списков, поэтому выпадашки
   * по всей CRM выглядят одинаково.
   */

  var MF_CARET =
    '<svg class="cmf-caret" width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
    '<path d="m7 10 5 5 5-5" stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
    'stroke-linejoin="round"/></svg>';
  var MF_CROSS =
    '<svg width="9" height="9" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
    '<path d="m6 6 12 12M18 6 6 18" stroke="currentColor" stroke-width="3.4" ' +
    'stroke-linecap="round"/></svg>';
  var MF_TICK =
    '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
    '<path d="m5 12.5 5 5 9-11" stroke="currentColor" stroke-width="3" stroke-linecap="round" ' +
    'stroke-linejoin="round"/></svg>';

  var openFilter = null;

  function closeFilterPanel() {
    if (!openFilter) return;
    var current = openFilter;
    openFilter = null;
    current.close();
  }

  document.addEventListener("pointerdown", function (event) {
    if (!openFilter) return;
    if (openFilter.owns(event.target)) return;
    closeFilterPanel();
  }, true);

  document.addEventListener("keydown", function (event) {
    if (openFilter && event.key === "Escape") {
      event.stopPropagation();
      closeFilterPanel();
    }
  }, true);

  window.addEventListener("resize", function () { closeFilterPanel(); });
  window.addEventListener("scroll", function (event) {
    // Прокрутка внутри самой панели её не закрывает.
    if (openFilter && openFilter.owns(event.target)) return;
    closeFilterPanel();
  }, true);

  function createMultiFilter(host, onChange) {
    var items = [];
    var chosen = [];
    var panel = null;

    host.classList.add("cmf");
    host.setAttribute("role", "button");
    host.setAttribute("tabindex", "0");
    host.setAttribute("aria-haspopup", "listbox");

    function labelOf(value) {
      var found = null;
      items.forEach(function (item) {
        if (String(item.value) === String(value)) found = item;
      });
      return found ? found.label : String(value);
    }

    function renderField() {
      var caption = host.getAttribute("data-label") || "";
      var body;
      if (!chosen.length) {
        body = '<span class="cmf-empty">' +
          escapeHtml(host.getAttribute("data-placeholder") || "Все") + "</span>";
      } else {
        body = '<span class="cmf-chips">' + chosen.map(function (value) {
          var text = labelOf(value);
          return '<span class="cmf-chip"><span class="cmf-chip-text">' + escapeHtml(text) +
            '</span><button type="button" class="cmf-x" data-drop="' + escapeHtml(value) +
            '" aria-label="Убрать: ' + escapeHtml(text) + '">' + MF_CROSS + "</button></span>";
        }).join("") + "</span>";
      }
      host.innerHTML = '<span class="cmf-label">' + escapeHtml(caption) + ":</span>" +
        body + MF_CARET;
      host.setAttribute("aria-expanded", panel ? "true" : "false");
    }

    function rowsHtml() {
      if (!items.length) {
        return '<div class="csel-empty">Нет вариантов</div>';
      }
      return items.map(function (item) {
        var on = chosen.indexOf(String(item.value)) >= 0;
        return '<button type="button" role="option" aria-selected="' + (on ? "true" : "false") +
          '" class="csel-option cmf-option' + (on ? " csel-option--on" : "") +
          '" data-value="' + escapeHtml(item.value) +
          '" data-search="' + escapeHtml((item.label + " " + (item.hint || "")).toLowerCase()) + '">' +
          '<span class="cmf-box">' + (on ? MF_TICK : "") + "</span>" +
          '<span class="csel-option-text">' + escapeHtml(item.label) +
          (item.hint ? '<span class="csel-option-hint">' + escapeHtml(item.hint) + "</span>" : "") +
          "</span></button>";
      }).join("");
    }

    function place() {
      if (!panel) return;
      var box = host.getBoundingClientRect();
      var width = Math.max(box.width, 240);
      panel.style.width = Math.min(width, window.innerWidth - 16) + "px";
      panel.style.left = Math.max(
        8, Math.min(box.left, window.innerWidth - panel.offsetWidth - 8)
      ) + "px";
      var below = window.innerHeight - box.bottom - 10;
      var above = box.top - 10;
      // Панель прибита к окну, а не к полю: фильтры стоят вверху страницы, и
      // обычно места хватает снизу — но у нижнего края разворачиваем вверх.
      if (below >= 220 || below >= above) {
        panel.style.top = box.bottom + 6 + "px";
        panel.style.maxHeight = Math.min(320, below) + "px";
      } else {
        panel.style.maxHeight = Math.min(320, above) + "px";
        panel.style.top = Math.max(8, box.top - 6 - panel.offsetHeight) + "px";
      }
    }

    function applySearch(query) {
      if (!panel) return;
      var needle = query.trim().toLowerCase();
      var shown = 0;
      Array.prototype.forEach.call(panel.querySelectorAll(".cmf-option"), function (node) {
        var hit = !needle || node.getAttribute("data-search").indexOf(needle) >= 0;
        node.hidden = !hit;
        if (hit) shown += 1;
      });
      var empty = panel.querySelector(".cmf-none");
      if (empty) empty.hidden = shown > 0;
    }

    function close() {
      if (panel) panel.remove();
      panel = null;
      renderField();
    }

    function toggle(value) {
      var index = chosen.indexOf(String(value));
      if (index >= 0) chosen.splice(index, 1);
      else chosen.push(String(value));
      renderField();
      if (panel) {
        var list = panel.querySelector(".csel-list");
        var search = panel.querySelector(".csel-input");
        var scrolled = list ? list.scrollTop : 0;
        if (list) list.innerHTML = rowsHtml() +
          '<div class="csel-empty cmf-none" hidden>Ничего не найдено</div>';
        if (search) applySearch(search.value);
        if (list) list.scrollTop = scrolled;
        place();
      }
      if (onChange) onChange();
    }

    function open() {
      if (panel) { closeFilterPanel(); return; }
      closeFilterPanel();
      panel = document.createElement("div");
      panel.className = "csel-panel cmf-panel";
      // Поиск есть во всех фильтрах, даже коротких: человек ищет одинаково во
      // всех, и «здесь ищется, а здесь нет» само по себе сбивает.
      panel.innerHTML =
        '<div class="csel-search"><input type="text" class="csel-input" ' +
        'placeholder="Поиск" autocomplete="off"></div>' +
        '<div class="csel-list" role="listbox" aria-multiselectable="true">' + rowsHtml() +
        '<div class="csel-empty cmf-none" hidden>Ничего не найдено</div></div>';
      document.body.appendChild(panel);
      place();
      openFilter = { close: close, owns: owns };
      renderField();
      var search = panel.querySelector(".csel-input");
      if (search) search.focus();
      panel.addEventListener("click", function (event) {
        var option = event.target.closest(".cmf-option");
        if (!option) return;
        toggle(option.getAttribute("data-value"));
      });
      if (search) {
        search.addEventListener("input", function () { applySearch(search.value); });
      }
    }

    function owns(node) {
      return !!(node && ((panel && panel.contains(node)) || host.contains(node)));
    }

    host.addEventListener("click", function (event) {
      var drop = event.target.closest(".cmf-x");
      if (drop) {
        event.stopPropagation();
        toggle(drop.getAttribute("data-drop"));
        return;
      }
      open();
    });
    host.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        open();
      }
    });

    renderField();

    return {
      values: function () { return chosen.slice(); },
      setValues: function (list) {
        chosen = (list || []).map(String);
        renderField();
      },
      setItems: function (list) {
        items = list || [];
        // Значение, которого в новом списке нет, молча оставлять нельзя: фильтр
        // продолжил бы резать выдачу по невидимому значению.
        chosen = chosen.filter(function (value) {
          return items.some(function (item) { return String(item.value) === value; });
        });
        renderField();
        if (panel) close();
      },
      clear: function () {
        if (!chosen.length) return false;
        chosen = [];
        renderField();
        return true;
      }
    };
  }

  // `filter` names the query parameter that narrows the leaf request down to this node.
  // A null value (an offer with no GEO or no partner) cannot be expressed as a filter,
  // so those nodes fall back to matching the fetched page client-side — see loadLeaves.
  var LEVELS = {
    buyer: {
      label: "Баер", filter: "buyer_id",
      key: function (r) { return r.buyer_id; },
      name: function (r) { return r.buyer; },
      value: function (r) { return r.buyer_id; },
      matches: function (r, value) { return r.buyer_id === value; }
    },
    geo: {
      label: "GEO", filter: "geo",
      key: function (r) { return r.geo || "—"; },
      name: function (r) { return r.geo || "Без GEO"; },
      value: function (r) { return r.geo; },
      matches: function (r, value) { return (r.geo || null) === (value || null); }
    },
    partner: {
      label: "Партнёрка", filter: "partner_id",
      key: function (r) { return r.partner || "—"; },
      name: function (r) { return r.partner || "Без ПП"; },
      value: function (r) { return r.partner_id; },
      matches: function (r, value) { return (r.partner_id || null) === (value || null); }
    },
    offer: {
      label: "Оффер", filter: "offer_id",
      key: function (r) { return r.offer_id; },
      name: function (r) { return r.offer; },
      value: function (r) { return r.offer_id; },
      matches: function (r, value) { return r.offer_id === value; }
    }
  };

  function subTh(label, extra, background, color, drag) {
    return '<th title="' + escapeHtml(label) + '" draggable="true"' +
      (drag ? ' data-col-drag="' + escapeHtml(drag.group) + '" data-col-label="' +
        escapeHtml(label) + '"' : "") +
      ' style="position:sticky;top:41px;z-index:4;cursor:grab;background:' + (background || "#FBF9F9") +
      ';text-align:right;padding:9px 12px;border-bottom:1px solid #E8E2E2;font-size:11px;' +
      'font-weight:700;color:' + (color || "#857D7D") +
      ';font-family:Inter;white-space:nowrap;' + (extra || "") + '">' +
      escapeHtml(label) + "</th>";
  }

  /* Последний блок доводит заливку до края карточки: правая граница там лишняя,
   * её роль играет рамка самой карточки. */
  function groupTh(label, colspan, background, color, borderColor, divider, last, key) {
    if (!colspan) return "";
    return '<th colspan="' + colspan + '" draggable="true"' +
      (key ? ' data-group-drag="' + escapeHtml(key) + '"' : "") +
      ' style="position:sticky;top:0;z-index:4;cursor:grab;background:' + background +
      ';text-align:center;padding:11px;border-bottom:1px solid ' + (borderColor || "#E8E2E2") +
      ';border-left:2px solid ' + (divider || "#E8E2E2") +
      (last ? ";border-right:0" : ";border-right:1px solid #E8E2E2") +
      ';font-size:11px;font-weight:800;color:' + color +
      ';text-transform:uppercase;letter-spacing:.7px">' + escapeHtml(label) + "</th>";
  }

  function td(content, opts) {
    opts = opts || {};
    return "<td" + (opts.title ? ' title="' + escapeHtml(opts.title) + '"' : "") +
      ' class="cs-cell" style="text-align:right;padding:12px;font-family:Inter;font-size:12.5px;' +
      "font-weight:" + (opts.bold ? "800" : "600") + ";border-bottom:1px solid #F2EEEE;white-space:nowrap;" +
      (opts.color ? "color:" + opts.color + ";" : "") + (opts.extra || "") + '">' + content + "</td>";
  }

  /* ---------- board factory ---------- */

  var LEAF_PAGE = 200;

  // Column groups in board order. `services` and `providers` are filled from the
  // workspace catalog; the rest come from each board's `metricColumns`.
  // `divider` is the line that opens the group and runs down the whole table, so it
  // has to read against white cells — a shade darker than the header's own `border`.
  var COLUMN_GROUPS = [
    {
      key: "services", label: "Сервисы",
      background: "#EAF3FA", subBackground: "#F3F8FC",
      cellBackground: "#FAFCFE", rootBackground: "#EDF6FA",
      color: "#4E78A0", border: "#D5E5F1", divider: "#9FC1DB"
    },
    {
      key: "providers", label: "Агенты и платёжки",
      background: "#FDF4EA", subBackground: "#FFF9F2",
      cellBackground: "#FFFCF8", rootBackground: "#FAF5EC",
      color: "#A66A32", border: "#F0DECA", divider: "#D8AE7C"
    },
    {
      key: "funnel", label: null,
      background: "#F4F9FA", subBackground: "#F8FAFC",
      cellBackground: "#FCFDFE", rootBackground: "#EDF3F7",
      color: "#5F7582", border: "#DEE8ED", divider: "#AFC5D0"
    },
    {
      key: "costs", label: "Затраты",
      background: "#FCEEEE", subBackground: "#FFF6F7",
      cellBackground: "#FFFBFC", rootBackground: "#F8F0F2",
      color: "#C3536E", border: "#F2D9DF", divider: "#DF9FB0"
    },
    {
      key: "result", label: "Результат",
      background: "#E6FAF1", subBackground: "#F1FCF7",
      cellBackground: "#F9FDFB", rootBackground: "#E9F8F1",
      color: "#25835E", border: "#D1EDDF", divider: "#84CBAA"
    }
  ];

  function defaultDisplay() {
    return {
      compact: true,
      dense: false,
      groups: { services: true, providers: true, funnel: true, costs: true, result: true },
      // Порядок блоков и колонок внутри них — перетаскивается за заголовок.
      groupOrder: [],
      columnOrder: {}
    };
  }

  function signTone(value) {
    return value >= 0 ? "#16B57F" : "#FF0000";
  }

  function amount(value) {
    return value == null ? 0 : Number(value);
  }

  function ensureViewStyles() {
    if (byId("celestialViewStyles")) return;
    var style = document.createElement("style");
    style.id = "celestialViewStyles";
    style.textContent =
      // Кнопка прижата к правому краю карточки сама: распорку между ней и
      // уровнями убрали вместе с подписью, которая там стояла.
      ".cs-view{position:relative;flex-shrink:0;margin-left:auto}" +
      ".cs-view-button{display:inline-flex;align-items:center;gap:7px;border:1px solid #E5DFDF;" +
      "background:#fff;border-radius:11px;padding:8px 14px;font:700 12.5px 'Inter',sans-serif;" +
      "color:#B91414;cursor:pointer;transition:border-color .18s,background .18s}" +
      ".cs-view-button:hover{border-color:#EDD5D5;background:#FCF9F9}" +
      '.cs-view-button[aria-expanded="true"]{border-color:#D06060;background:#FCF1F1}' +
      // Anchored to the button's right edge, which is itself right-aligned in the card —
      // that keeps the panel on screen at every width without flipping sides.
      ".cs-view-panel{position:absolute;top:calc(100% + 8px);right:0;z-index:10050;" +
      "width:min(290px,calc(100vw - 32px));" +
      "background:#fff;border:1px solid #E8E2E2;border-radius:14px;padding:14px;" +
      "box-shadow:0 18px 44px rgba(23,17,17,.18)}" +
      ".cs-view-panel[hidden]{display:none}" +
      ".cs-view-row{display:flex;align-items:center;justify-content:space-between;gap:10px;" +
      "margin-bottom:11px}" +
      ".cs-view-row--stack{display:block;margin-bottom:0}" +
      ".cs-view-label{font:700 11px 'Inter',sans-serif;color:#857D7D;text-transform:uppercase;" +
      "letter-spacing:.5px}" +
      ".cs-seg{display:inline-flex;background:#F7F4F4;border-radius:9px;padding:2px}" +
      ".cs-seg-button{border:none;background:transparent;border-radius:7px;padding:6px 11px;" +
      "font:700 11.5px 'Inter',sans-serif;color:#857D7D;cursor:pointer;transition:background .16s,color .16s}" +
      '.cs-seg-button[aria-pressed="true"]{background:#fff;color:#070505;' +
      "box-shadow:0 1px 3px rgba(23,17,17,.12)}" +
      ".cs-view-groups{display:grid;gap:7px;margin-top:9px}" +
      ".cs-view-check{display:flex;align-items:center;gap:9px;font:600 12.5px 'Inter',sans-serif;" +
      "color:#3A3030;cursor:pointer}" +
      ".cs-view-check input{width:15px;height:15px;accent-color:#B91414;cursor:pointer}";
    document.head.appendChild(style);
  }

  function ensureBoardTableStyles() {
    if (byId("celestialBoardTableStyles")) return;
    var style = document.createElement("style");
    style.id = "celestialBoardTableStyles";
    // Collapsing is a visibility change, not a re-render: every group row is in the DOM
    // already, so folding a branch costs a class toggle instead of rebuilding the table.
    style.textContent =
      ".cs-row--hidden{display:none}" +
      ".cs-arrow{transition:transform .15s}" +
      'tr[data-open="0"] > td .cs-arrow{transform:rotate(-90deg)}' +
      // A rule per cell pair, so the divider follows the columns however the
      // catalog and the visible groups change.
      ".cs-table--dividers thead th + th{border-left:1px solid #E4DDDD}" +
      ".cs-table--dividers tbody td + td{border-left:1px solid #EFE9E9}";
    document.head.appendChild(style);
  }

  function createBoard(config) {
    var state = {
      user: null,
      services: [],
      providers: [],
      buyers: [],
      offers: [],
      partners: [],
      // Pre-aggregated rows, one per buyer × offer. The raw records behind them are
      // fetched only for the branch the user actually opens (see loadLeaves).
      groups: [],
      recordCount: 0,
      truncated: false,
      leaves: {},
      leavesOpen: {},
      structure: null,
      display: defaultDisplay(),
      collapsed: {},
      canManage: false
    };

    var p = config.prefix; // "media" | "finance"

    function el(name) { return byId(p + name); }

    function defaultStructure() {
      return { items: [
        { key: "buyer", on: true },
        { key: "geo", on: true },
        { key: "partner", on: true },
        { key: "offer", on: true }
      ] };
    }

    function activeLevels() {
      return state.structure.items.filter(function (item) { return item.on; })
        .map(function (item) { return item.key; });
    }

    /* ----- filters ----- */

    // Каждый из этих фильтров держит набор значений, а не одно.
    var LIST_FILTERS = [
      { id: "FilterBuyer", param: "buyer_id" },
      { id: "FilterGeo", param: "geo" },
      { id: "FilterPartner", param: "partner_id" },
      { id: "FilterOffer", param: "offer_id" }
    ];

    var filters = {};

    function filterValues(id) {
      return filters[id] ? filters[id].values() : [];
    }

    /* Фильтры объектом, чтобы ветка дерева могла перебить значение бара своим. */
    function filterParams() {
      var params = {};
      var from = el("FilterDateFrom");
      var to = el("FilterDateTo");
      if (from && from.value) params.date_from = from.value;
      if (to && to.value) params.date_to = to.value;
      LIST_FILTERS.forEach(function (item) {
        var values = filterValues(item.id);
        if (values.length) params[item.param] = values;
      });
      if (config.extraFilters) config.extraFilters(params);
      return params;
    }

    function filterQuery() {
      var query = queryString(filterParams());
      return query ? "&" + query : "";
    }

    function queryString(params) {
      var parts = [];
      Object.keys(params).forEach(function (key) {
        var value = params[key];
        if (value == null || value === "") return;
        // Набор уходит повторяющимся параметром — сервер принимает и один, и много.
        (Array.isArray(value) ? value : [value]).forEach(function (single) {
          if (single == null || single === "") return;
          parts.push(key + "=" + encodeURIComponent(single));
        });
      });
      return parts.join("&");
    }

    function bindFilters() {
      LIST_FILTERS.forEach(function (item) {
        var host = el(item.id);
        if (!host) return;
        filters[item.id] = createMultiFilter(host, function () {
          // Смена баеров меняет и список их офферов — иначе в фильтре остались
          // бы чужие.
          if (item.id === "FilterBuyer") fillOfferFilter();
          reloadSoon();
        });
      });
      ["FilterDateFrom", "FilterDateTo"].forEach(function (id) {
        var element = el(id);
        if (!element) return;
        element.addEventListener("change", function () { reloadSoon(); });
      });
    }

    // Плашки снимают и добавляют пачками, по одному клику на значение. Без
    // паузы каждый клик уходил бы отдельным запросом за той же таблицей.
    var reloadTimer = null;

    function reloadSoon() {
      if (reloadTimer) window.clearTimeout(reloadTimer);
      reloadTimer = window.setTimeout(function () {
        reloadTimer = null;
        loadRecords().catch(fail);
      }, 220);
    }

    /* ----- structure bar ----- */

    var SEP_SVG = '<span class="cs-sep"><svg width="15" height="15" viewBox="0 0 24 24" fill="none">' +
      '<path d="M5 12h14M13 6l6 6-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" ' +
      'stroke-linejoin="round"/></svg></span>';
    var GRIP_SVG = '<span class="cs-grip"><svg width="9" height="14" viewBox="0 0 9 14" fill="currentColor">' +
      '<circle cx="2" cy="2" r="1.35"/><circle cx="7" cy="2" r="1.35"/><circle cx="2" cy="7" r="1.35"/>' +
      '<circle cx="7" cy="7" r="1.35"/><circle cx="2" cy="12" r="1.35"/><circle cx="7" cy="12" r="1.35"/>' +
      "</svg></span>";

    function renderStructureBar() {
      var bar = el("StructureBar");
      if (!bar) return;
      ensureStructureStyles();
      // Each slot carries the separator that precedes its chip, so reordering slots keeps
      // the arrows correct without touching them (the first one hides its separator in CSS).
      bar.innerHTML = state.structure.items.map(function (item) {
        return '<span class="cs-slot" data-key="' + escapeHtml(item.key) + '">' + SEP_SVG +
          '<span class="cs-chip" tabindex="0" role="button" aria-pressed="' + (item.on ? "true" : "false") +
          '" data-on="' + (item.on ? "1" : "0") +
          '" title="Перетащите, чтобы изменить порядок · клик включает или скрывает уровень">' +
          GRIP_SVG + "<span>" + escapeHtml(LEVELS[item.key].label) + "</span></span></span>";
      }).join("");
      bindStructureBar(bar);
    }

    function toggleLevel(key) {
      var item = null;
      var enabled = 0;
      state.structure.items.forEach(function (candidate) {
        if (candidate.key === key) item = candidate;
        if (candidate.on) enabled += 1;
      });
      if (!item) return;
      if (item.on && enabled <= 1) {
        toast("Должен остаться хотя бы один уровень структуры", "error");
        return;
      }
      item.on = !item.on;
      saveStructure(key);
    }

    function moveLevel(key, offset) {
      var items = state.structure.items;
      var index = items.findIndex(function (item) { return item.key === key; });
      var target = index + offset;
      if (index < 0 || target < 0 || target >= items.length) return;
      items.splice(target, 0, items.splice(index, 1)[0]);
      saveStructure(key);
    }

    /* Slides every slot from where it used to be to where it is now (FLIP). */
    function flipSlots(slots, before) {
      var reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion:reduce)").matches;
      slots.forEach(function (slot, index) {
        var after = slot.getBoundingClientRect();
        var dx = before[index].left - after.left;
        var dy = before[index].top - after.top;
        if (!dx && !dy) return;
        if (reduced) return;
        slot.style.transition = "none";
        slot.style.transform = "translate(" + dx + "px," + dy + "px)";
        window.requestAnimationFrame(function () {
          slot.style.transition = "transform 200ms cubic-bezier(.22,.61,.36,1)";
          slot.style.transform = "";
        });
      });
    }

    function bindStructureBar(bar) {
      // The bar is re-rendered on every structure change but the element itself stays,
      // so binding per render stacked a second handler that undid the first one's toggle.
      if (bar.dataset.structureBound) return;
      bar.dataset.structureBound = "1";
      var drag = null;

      bar.addEventListener("pointerdown", function (event) {
        if (event.button) return;
        var chip = event.target.closest(".cs-chip");
        if (!chip || drag) return;
        drag = {
          chip: chip,
          slot: chip.parentElement,
          key: chip.parentElement.getAttribute("data-key"),
          startX: event.clientX,
          startY: event.clientY,
          fromIndex: slotIndex(chip.parentElement),
          touch: event.pointerType === "touch",
          lifted: false,
          hold: 0
        };
        chip.classList.add("cs-chip--pressed");
        // A finger cannot "move a little to start a drag" without feeling like a slip,
        // so touch lifts the chip on a short hold instead.
        if (drag.touch) drag.hold = window.setTimeout(lift, 180);
        window.addEventListener("pointermove", onMove);
        window.addEventListener("pointerup", onUp);
        window.addEventListener("pointercancel", onUp);
      });

      bar.addEventListener("click", function (event) {
        var chip = event.target.closest(".cs-chip");
        // A drag ends over the chip too; only a real click may toggle the level.
        if (!chip || chip.dataset.csDragged) return;
        toggleLevel(chip.parentElement.getAttribute("data-key"));
      });

      bar.addEventListener("keydown", function (event) {
        var chip = event.target.closest(".cs-chip");
        if (!chip) return;
        var key = chip.parentElement.getAttribute("data-key");
        if (event.key === "Enter" || event.key === " " || event.key === "Spacebar") {
          event.preventDefault();
          toggleLevel(key);
        } else if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
          event.preventDefault();
          moveLevel(key, event.key === "ArrowLeft" ? -1 : 1);
        }
      });

      function slotIndex(slot) {
        return Array.prototype.indexOf.call(bar.children, slot);
      }

      function lift() {
        if (!drag || drag.lifted) return;
        drag.lifted = true;
        window.clearTimeout(drag.hold);
        var rect = drag.chip.getBoundingClientRect();
        drag.grabX = drag.startX - rect.left;
        drag.grabY = drag.startY - rect.top;
        var flyer = drag.chip.cloneNode(true);
        flyer.classList.remove("cs-chip--pressed");
        flyer.classList.add("cs-chip--flying");
        flyer.removeAttribute("tabindex");
        flyer.style.width = rect.width + "px";
        flyer.style.height = rect.height + "px";
        flyer.style.left = rect.left + "px";
        flyer.style.top = rect.top + "px";
        // Start flat and let the class's lift transform animate in.
        flyer.style.transform = "none";
        document.body.appendChild(flyer);
        window.requestAnimationFrame(function () { flyer.style.transform = ""; });
        drag.flyer = flyer;
        drag.chip.classList.remove("cs-chip--pressed");
        drag.chip.classList.add("cs-chip--ghost");
        document.body.classList.add("cs-dragging");
        if (drag.touch && navigator.vibrate) {
          try { navigator.vibrate(8); } catch (ignored) { /* opt-in only */ }
        }
      }

      function onMove(event) {
        if (!drag) return;
        if (!drag.lifted) {
          if (Math.abs(event.clientX - drag.startX) + Math.abs(event.clientY - drag.startY) < 5) return;
          lift();
        }
        event.preventDefault();
        drag.flyer.style.left = event.clientX - drag.grabX + "px";
        drag.flyer.style.top = event.clientY - drag.grabY + "px";
        reorderTo(event.clientX, event.clientY);
      }

      /* Walks the row and drops the dragged slot wherever the pointer has passed a midpoint. */
      function reorderTo(x, y) {
        var slots = Array.prototype.slice.call(bar.children);
        var current = slots.indexOf(drag.slot);
        var target = current;
        for (var i = 0; i < slots.length; i += 1) {
          if (i === current) continue;
          var rect = slots[i].getBoundingClientRect();
          // The bar wraps on narrow screens, so a slot only counts once the pointer is on its line.
          if (y < rect.top - 6 || y > rect.bottom + 6) continue;
          var middle = rect.left + rect.width / 2;
          if (i < current && x < middle) { target = i; break; }
          if (i > current && x > middle) target = i;
        }
        if (target === current) return;
        var before = slots.map(function (slot) { return slot.getBoundingClientRect(); });
        bar.insertBefore(drag.slot, target < current ? slots[target] : slots[target].nextSibling);
        flipSlots(slots, before);
      }

      function onUp() {
        if (!drag) return;
        var finished = drag;
        drag = null;
        window.clearTimeout(finished.hold);
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        window.removeEventListener("pointercancel", onUp);
        finished.chip.classList.remove("cs-chip--pressed");
        if (!finished.lifted) return;

        // Fly the clone home, then hand the row back to the real chip.
        var landing = finished.chip.getBoundingClientRect();
        var flyer = finished.flyer;
        flyer.style.transition = "left 200ms cubic-bezier(.22,.61,.36,1)," +
          "top 200ms cubic-bezier(.22,.61,.36,1),transform 200ms cubic-bezier(.22,.61,.36,1)," +
          "box-shadow 200ms";
        flyer.style.left = landing.left + "px";
        flyer.style.top = landing.top + "px";
        flyer.style.transform = "none";
        flyer.style.boxShadow = "none";
        window.setTimeout(function () {
          flyer.remove();
          finished.chip.classList.remove("cs-chip--ghost");
        }, 210);
        document.body.classList.remove("cs-dragging");

        // Suppress the click this drag is about to fire on the chip underneath.
        finished.chip.dataset.csDragged = "1";
        window.setTimeout(function () { delete finished.chip.dataset.csDragged; }, 0);

        if (slotIndex(finished.slot) !== finished.fromIndex) commitOrder();
      }

      function commitOrder() {
        var byKey = {};
        state.structure.items.forEach(function (item) { byKey[item.key] = item; });
        state.structure.items = Array.prototype.map.call(bar.children, function (slot) {
          return byKey[slot.getAttribute("data-key")];
        });
        // Deliberately not re-rendering the bar: the landing animation is still running.
        resetLeafState();
        renderTable();
        persistStructure();
      }
    }

    function persistStructure() {
      api.put("/me/preferences/" + config.preferenceKey, { value: state.structure })
        .catch(function () { toast("Не удалось сохранить структуру", "error"); });
    }

    /* Node paths are built from the level order, so any change invalidates them. */
    function resetLeafState() {
      state.leaves = {};
      state.leavesOpen = {};
    }

    function saveStructure(focusKey) {
      resetLeafState();
      renderStructureBar();
      if (focusKey) {
        var bar = el("StructureBar");
        var slot = bar && bar.querySelector('[data-key="' + focusKey + '"] .cs-chip');
        // Re-rendering drops focus, which would strand a keyboard user mid-reorder.
        if (slot && bar.contains(document.activeElement) === false) slot.focus();
      }
      renderTable();
      persistStructure();
    }

    /* ----- display settings ----- */

    function persistDisplay() {
      api.put("/me/preferences/" + config.displayPreferenceKey, { value: state.display })
        .catch(function () { toast("Не удалось сохранить настройки вида", "error"); });
    }

    function applyDisplay(rerenderHead) {
      var table = el("TableHead") && el("TableHead").closest("table");
      if (table) table.classList.toggle("celestial-board-table--dense", !!state.display.dense);
      if (rerenderHead) renderHead();
      renderTable();
    }

    function segmented(name, options, active) {
      return '<div class="cs-seg">' + options.map(function (option) {
        return '<button type="button" class="cs-seg-button" data-view="' + name +
          '" data-value="' + option.value + '" aria-pressed="' +
          (option.value === active ? "true" : "false") + '">' +
          escapeHtml(option.label) + "</button>";
      }).join("") + "</div>";
    }

    function renderViewPanel(panel) {
      // Выключателю нечего выключать, когда колонок группы на этой доске нет.
      var groups = COLUMN_GROUPS.filter(function (group) {
        if (config.hideServices && group.key === "services") return false;
        return !(config.hideProviders && group.key === "providers");
      }).map(function (group) {
        var on = state.display.groups[group.key] !== false;
        return '<label class="cs-view-check"><input type="checkbox" data-group="' + group.key +
          '"' + (on ? " checked" : "") + '><span>' +
          escapeHtml(group.label || config.funnelTitle) + "</span></label>";
      }).join("");
      panel.innerHTML =
        '<div class="cs-view-row"><span class="cs-view-label">Числа</span>' +
        segmented("compact", [
          { value: "1", label: "Компактные" },
          { value: "0", label: "Полные" }
        ], state.display.compact ? "1" : "0") + "</div>" +
        '<div class="cs-view-row"><span class="cs-view-label">Плотность</span>' +
        segmented("dense", [
          { value: "0", label: "Обычная" },
          { value: "1", label: "Плотная" }
        ], state.display.dense ? "1" : "0") + "</div>" +
        '<div class="cs-view-row cs-view-row--stack"><span class="cs-view-label">Колонки</span>' +
        '<div class="cs-view-groups">' + groups + "</div></div>";
    }

    function bindViewControls() {
      var bar = el("StructureBar");
      var card = bar && bar.parentElement;
      if (!card || byId(p + "ViewButton")) return;
      ensureViewStyles();
      card.classList.add("cs-structure-card");
      var host = document.createElement("div");
      host.className = "cs-view";
      host.innerHTML =
        '<button type="button" class="cs-view-button" id="' + p + 'ViewButton" aria-expanded="false">' +
        '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
        '<path d="M4 6h16M4 12h16M4 18h9" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>' +
        "</svg>Вид</button>" +
        '<div class="cs-view-panel" id="' + p + 'ViewPanel" hidden></div>';
      card.appendChild(host);

      var button = byId(p + "ViewButton");
      var panel = byId(p + "ViewPanel");

      function close() {
        panel.hidden = true;
        button.setAttribute("aria-expanded", "false");
        document.removeEventListener("click", onOutside, true);
        document.removeEventListener("keydown", onKey);
      }
      function onOutside(event) {
        if (!host.contains(event.target)) close();
      }
      function onKey(event) {
        if (event.key === "Escape") { close(); button.focus(); }
      }
      button.addEventListener("click", function () {
        if (!panel.hidden) { close(); return; }
        renderViewPanel(panel);
        panel.hidden = false;
        button.setAttribute("aria-expanded", "true");
        document.addEventListener("click", onOutside, true);
        document.addEventListener("keydown", onKey);
      });
      panel.addEventListener("click", function (event) {
        var segment = event.target.closest("[data-view]");
        if (!segment) return;
        var on = segment.getAttribute("data-value") === "1";
        state.display[segment.getAttribute("data-view")] = on;
        renderViewPanel(panel);
        applyDisplay(false);
        persistDisplay();
      });
      panel.addEventListener("change", function (event) {
        var checkbox = event.target.closest("[data-group]");
        if (!checkbox) return;
        var key = checkbox.getAttribute("data-group");
        var enabled = COLUMN_GROUPS.filter(function (group) {
          return state.display.groups[group.key] !== false;
        });
        if (!checkbox.checked && enabled.length <= 1) {
          checkbox.checked = true;
          toast("Должна остаться хотя бы одна группа колонок", "error");
          return;
        }
        state.display.groups[key] = checkbox.checked;
        applyDisplay(true);
        persistDisplay();
      });
    }

    /* ----- table header ----- */

    // Rebuilt whenever the catalog or the visible groups change; every row renders
    // against this one description instead of re-deriving the column list.
    var layout = [];

    function buildLayout() {
      var byGroup = { funnel: [], costs: [], result: [] };
      config.metricColumns.forEach(function (column) {
        byGroup[column.group].push(column);
      });
      var catalog = {
        // В Медиаборде нет ни блока сервисов, ни колонки агентов: разбивка по
        // агентам живёт в модалке записи, а в таблице она занимала место, не
        // отвечая ни на один вопрос — расход и так виден в SPEND.
        services: config.hideServices
          ? []
          : state.services.map(function (service) {
              return { label: service.name, kind: "num", bucket: "services", id: service.id };
            }),
        providers: config.hideProviders
          ? []
          : state.providers.map(function (provider) {
              return { label: provider.name, kind: "money", bucket: "providers", id: provider.id };
            })
      };
      layout = orderedGroups().map(function (group) {
        var columns = catalog[group.key] || byGroup[group.key] || [];
        return Object.assign({}, group, {
          label: group.label || config.funnelTitle,
          columns: orderColumns(group.key, columns)
        });
      }).filter(function (group) {
        return state.display.groups[group.key] !== false && group.columns.length;
      });
    }

    /* Порядок блоков и колонок внутри них — дело вкуса и задачи: кто-то смотрит
       сначала затраты, кто-то воронку. Поэтому он не зашит, а перетаскивается
       за заголовок и запоминается вместе с остальным видом доски. */
    function orderedGroups() {
      var saved = state.display.groupOrder || [];
      var known = {};
      COLUMN_GROUPS.forEach(function (group) { known[group.key] = group; });
      var result = [];
      saved.forEach(function (key) {
        if (known[key] && result.indexOf(known[key]) < 0) result.push(known[key]);
      });
      COLUMN_GROUPS.forEach(function (group) {
        if (result.indexOf(group) < 0) result.push(group);
      });
      return result;
    }

    // Колонки переименовали в верхний регистр, а в сохранённом порядке остались
    // прежние подписи — без этого две колонки уезжали бы в конец своей группы.
    var RENAMED_COLUMNS = { Revenue: "REVENUE", Profit: "PROFIT" };

    function orderColumns(groupKey, columns) {
      var saved = (state.display.columnOrder || {})[groupKey];
      if (!saved || !saved.length) return columns;
      var byLabel = {};
      columns.forEach(function (column) { byLabel[column.label] = column; });
      var result = [];
      saved.forEach(function (label) {
        var current = RENAMED_COLUMNS[label] || label;
        // Колонка могла исчезнуть — например, агента удалили из справочника.
        if (byLabel[current] && result.indexOf(byLabel[current]) < 0) {
          result.push(byLabel[current]);
        }
      });
      columns.forEach(function (column) {
        if (result.indexOf(column) < 0) result.push(column);
      });
      return result;
    }

    function saveColumnOrder(groupKey, labels) {
      state.display.columnOrder = state.display.columnOrder || {};
      state.display.columnOrder[groupKey] = labels;
      persistDisplay();
    }

    /* The trailing column exists only to hold the pencil button. Where a click on the
     * row already opens the modal the button is redundant, and the column stays empty
     * on every group row — so the whole column goes instead. */
    function hasActionColumn() {
      return state.canManage && !config.rowOpensModal;
    }

    /* The line that opens a block of columns. On a board with dividers it carries the
     * block's own colour and runs the full height of the table, so Платёжки, Воронка,
     * Затраты and Результат read as separate blocks instead of one wall of numbers.
     * Elsewhere only the very first metric column keeps its old hairline. */
    function columnOpener(group, groupIndex, index) {
      if (config.columnDividers) {
        return index === 0 ? "border-left:2px solid " + group.divider + ";" : "";
      }
      return index === 0 && groupIndex === 0 ? "border-left:1px solid #F0EBEB;" : "";
    }

    function renderHead() {
      var head = el("TableHead");
      if (!head) return;
      buildLayout();
      var row1 = "<tr>" +
        '<th rowspan="2" class="cs-head-structure" style="position:sticky;left:0;top:0;z-index:5;background:#F8F5F5;text-align:left;padding:14px 16px;border-bottom:1px solid #E8E2E2;border-right:1px solid #E8E2E2;font-size:11px;font-weight:700;color:#857D7D;text-transform:uppercase;letter-spacing:.6px">Структура</th>' +
        layout.map(function (group, groupIndex) {
          return groupTh(
            group.label, group.columns.length,
            group.background, group.color, group.border,
            config.columnDividers ? group.divider : null,
            !hasActionColumn() && groupIndex === layout.length - 1,
            group.key
          );
        }).join("") +
        (hasActionColumn() ? '<th rowspan="2" style="position:sticky;top:0;z-index:4;background:#F8F5F5;border-bottom:1px solid #E8E2E2;width:44px"></th>' : "") +
        "</tr>";
      var row2 = "<tr>" +
        layout.map(function (group, groupIndex) {
          return group.columns.map(function (column, index) {
            var opener = columnOpener(group, groupIndex, index);
            return subTh(
              column.label,
              opener,
              group.subBackground,
              group.color,
              { group: group.key }
            );
          }).join("");
        }).join("") +
        "</tr>";
      head.innerHTML = row1 + row2;
      bindHeadDrag(head);
    }

    /* Перетаскивание заголовков: за верхний ряд переставляются блоки целиком,
       за нижний — колонки внутри своего блока. Между блоками колонка не
       переезжает: «Revenue» в «Затратах» означала бы не то, что написано. */
    function bindHeadDrag(head) {
      // Заголовок перерисовывается на каждый чих, а слушатели висят на самом
      // `thead` и переживают смену его содержимого. Без этой отметки после
      // первого же перетаскивания их становилось два, и второй обработчик
      // двигал колонку ещё раз — уже от нового порядка.
      if (head.getAttribute("data-drag-bound") === "1") return;
      head.setAttribute("data-drag-bound", "1");
      var dragged = null;
      head.addEventListener("dragstart", function (event) {
        var cell = event.target.closest
          ? event.target.closest("[data-group-drag],[data-col-drag]")
          : null;
        if (!cell) return;
        dragged = cell;
        cell.style.opacity = ".45";
        event.dataTransfer.effectAllowed = "move";
        // Safari не начинает перетаскивание без данных в буфере.
        event.dataTransfer.setData("text/plain", cell.textContent);
      });
      head.addEventListener("dragend", function () {
        if (dragged) dragged.style.opacity = "";
        dragged = null;
      });
      head.addEventListener("dragover", function (event) {
        if (!dragged) return;
        var target = event.target.closest
          ? event.target.closest("[data-group-drag],[data-col-drag]")
          : null;
        if (!target || target === dragged) return;
        var sameKind = dragged.hasAttribute("data-group-drag") ===
          target.hasAttribute("data-group-drag");
        var sameGroup = !dragged.hasAttribute("data-col-drag") ||
          dragged.getAttribute("data-col-drag") === target.getAttribute("data-col-drag");
        if (!sameKind || !sameGroup) return;
        event.preventDefault();
      });
      head.addEventListener("drop", function (event) {
        if (!dragged) return;
        var target = event.target.closest
          ? event.target.closest("[data-group-drag],[data-col-drag]")
          : null;
        if (!target || target === dragged) return;
        event.preventDefault();
        if (dragged.hasAttribute("data-group-drag")) {
          return moveGroup(
            dragged.getAttribute("data-group-drag"),
            target.getAttribute("data-group-drag")
          );
        }
        moveColumn(
          dragged.getAttribute("data-col-drag"),
          dragged.getAttribute("data-col-label"),
          target.getAttribute("data-col-label")
        );
      });
    }

    function reorder(list, from, to) {
      var source = list.indexOf(from);
      var destination = list.indexOf(to);
      if (source < 0 || destination < 0) return list;
      list.splice(destination, 0, list.splice(source, 1)[0]);
      return list;
    }

    function moveGroup(from, to) {
      var order = layout.map(function (group) { return group.key; });
      state.display.groupOrder = reorder(order, from, to);
      persistDisplay();
      renderHead();
      renderTable();
    }

    function moveColumn(groupKey, from, to) {
      var group = layout.filter(function (item) { return item.key === groupKey; })[0];
      if (!group) return;
      var labels = group.columns.map(function (column) { return column.label; });
      saveColumnOrder(groupKey, reorder(labels, from, to));
      renderHead();
      renderTable();
    }

    function columnCount() {
      return layout.reduce(function (total, group) {
        return total + group.columns.length;
      }, 1) + (hasActionColumn() ? 1 : 0);
    }

    /* ----- aggregation ----- */

    function newAggregate() {
      return {
        services: {}, providers: {}, providersTotal: 0,
        sums: {}, count: 0, records: 0
      };
    }

    /* Every agent/payment on the record, not just the ones still in the catalog —
     * a deactivated provider keeps counting towards SPEND on the server, so the
     * "Агенты и платёжки" total has to include it too. */
    function providersSum(record) {
      var values = record.providers || {};
      return Object.keys(values).reduce(function (total, key) {
        return total + Number((values[key] || {}).amount || 0);
      }, 0);
    }

    function accumulate(aggregate, record) {
      aggregate.count += 1;
      // A group row stands for many records; a raw record stands for itself.
      aggregate.records += Number(record.records == null ? 1 : record.records);
      state.services.forEach(function (service) {
        var value = (record.services || {})[service.id];
        if (value) {
          aggregate.services[service.id] =
            (aggregate.services[service.id] || 0) + Number(value.quantity || 0);
        }
      });
      state.providers.forEach(function (provider) {
        var value = (record.providers || {})[provider.id];
        if (value) {
          aggregate.providers[provider.id] =
            (aggregate.providers[provider.id] || 0) + Number(value.amount || 0);
        }
      });
      aggregate.providersTotal += providersSum(record);
      config.sumFields.forEach(function (field) {
        var value = record[field];
        if (value != null) aggregate.sums[field] = (aggregate.sums[field] || 0) + Number(value);
      });
    }

    function groupRecords(records, levels) {
      var root = {
        children: {}, order: [], aggregate: newAggregate(), records: [], scope: {}
      };
      records.forEach(function (record) {
        accumulate(root.aggregate, record);
        var node = root;
        levels.forEach(function (levelKey) {
          var level = LEVELS[levelKey];
          var key = String(level.key(record));
          if (!node.children[key]) {
            // `scope` is this node's slice of the data, inherited down the branch, and
            // is what turns a node back into a request for its own records.
            var scope = Object.assign({}, node.scope);
            scope[levelKey] = level.value(record);
            node.children[key] = {
              key: key, level: levelKey, name: level.name(record),
              children: {}, order: [], aggregate: newAggregate(), records: [],
              scope: scope
            };
            node.order.push(key);
          }
          node = node.children[key];
          accumulate(node.aggregate, record);
        });
        node.records.push(record);
      });
      return root;
    }

    /* ----- rendering ----- */

    function metricCells(source, opts) {
      var compact = state.display.compact;
      var cells = "";
      layout.forEach(function (group, groupIndex) {
        group.columns.forEach(function (column, index) {
          var value = column.pick
            ? column.pick(source)
            : column.bucket
              ? source[column.bucket][column.id]
              : column.get(source.sums);
          var formatted = formatCell(column.kind, value, compact);
          var numeric = value == null ? null : Number(value);
          cells += td(formatted.text, {
            bold: opts.bold || column.bold,
            title: formatted.title,
            color: column.tone && numeric != null ? column.tone(numeric) : undefined,
            extra: "background:" +
              (opts.root ? group.rootBackground : group.cellBackground) + ";" +
              columnOpener(group, groupIndex, index)
          });
        });
      });
      return cells;
    }

    function nodePath(prefixPath, key) { return prefixPath + "|" + key; }

    function noticeRow(depth, owner, content) {
      return '<tr data-owner="' + escapeHtml(owner) + '" style="background:#fff">' +
        '<td colspan="' + columnCount() + '" style="padding:10px 16px 10px ' +
        (16 + depth * 22) + 'px;border-bottom:1px solid #F2EEEE;font-size:11.5px;' +
        'font-weight:700;color:#857D7D">' + content + "</td></tr>";
    }

    function renderNodeRows(node, depth, path, output) {
      var isLeafLevel = !node.order.length;
      // A leaf-level node hides raw records that are not loaded yet, so its arrow tracks
      // a separate flag: group nodes default to open, record lists default to closed.
      var isOpen = isLeafLevel ? !!state.leavesOpen[path] : !state.collapsed[path];
      var arrow = '<svg class="cs-arrow" width="14" height="14" viewBox="0 0 24 24" fill="none">' +
        '<path d="m6 9 6 6 6-6" stroke="' +
        (depth === 0 ? "#B91414" : "#857D7D") + '" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>';
      var background = depth === 0 ? "#FCF1F1" : "#fff";
      // Ни уровня строки, ни числа записей рядом с названием: и то и другое
      // видно по отступу и по стрелке, а в столбце они только шумели.
      var label = '<div style="display:flex;align-items:center;gap:9px">' + arrow +
        '<span style="font-weight:' + (depth === 0 ? "800" : "700") + ';font-size:' +
        (depth === 0 ? "13.5px" : "12.5px") + '">' + escapeHtml(node.name) + "</span></div>";
      output.push('<tr data-node="' + escapeHtml(path) + '"' +
        (isLeafLevel ? ' data-leaf-node="1"' : "") +
        ' data-open="' + (isOpen ? "1" : "0") +
        '" style="background:' + background + ';cursor:pointer">' +
        '<td style="position:sticky;left:0;z-index:2;background:' + background +
        ';padding:12px 16px 12px ' + (16 + depth * 22) +
        'px;border-bottom:1px solid #EDE8E8;border-right:1px solid #E8E2E2">' + label + "</td>" +
        metricCells({
          services: node.aggregate.services,
          providers: node.aggregate.providers,
          providersTotal: node.aggregate.providersTotal,
          sums: node.aggregate.sums
        }, { bold: depth === 0, root: depth === 0 }) +
        (hasActionColumn() ? td("", {}) : "") +
        "</tr>");
      // Descendants are always emitted — collapsing hides them, so reopening a branch
      // never costs a rebuild. Only records that have not been fetched are missing.
      if (isLeafLevel) {
        renderLeafRows(node, depth + 1, path, output);
        return;
      }
      node.order.forEach(function (key) {
        renderNodeRows(node.children[key], depth + 1, nodePath(path, key), output);
      });
    }

    function renderLeafRows(node, depth, path, output) {
      var leaf = state.leaves[path];
      if (!leaf) {
        // Nothing fetched yet: the placeholder only matters once the node is opened.
        if (state.leavesOpen[path]) output.push(noticeRow(depth, path, "Загружаю записи…"));
        return;
      }
      if (leaf.error) {
        output.push(noticeRow(depth, path, '<span style="color:#FF0000">' +
          escapeHtml(leaf.error) + "</span>"));
        return;
      }
      leaf.items.forEach(function (record) {
        output.push(renderLeafRow(record, depth, path));
      });
      if (leaf.loading) {
        output.push(noticeRow(depth, path, "Загружаю записи…"));
        return;
      }
      if (!leaf.done) {
        output.push(noticeRow(depth, path,
          '<button type="button" data-more="' + escapeHtml(path) + '" ' +
          'style="border:1px solid #E5DFDF;background:#F8F5F5;border-radius:9px;padding:6px 13px;' +
          "font:700 11.5px Inter,sans-serif;color:#B91414;cursor:pointer\">Показать ещё · " +
          num(leaf.items.length) + " из " + num(node.aggregate.records) + "</button>"));
      } else if (!leaf.items.length) {
        output.push(noticeRow(depth, path, "Записей нет"));
      }
    }

    function renderLeafRow(record, depth, owner) {
      var recordServices = {};
      var recordProviders = {};
      state.services.forEach(function (service) {
        var value = (record.services || {})[service.id];
        if (value) recordServices[service.id] = Number(value.quantity || 0);
      });
      state.providers.forEach(function (provider) {
        var value = (record.providers || {})[provider.id];
        if (value) recordProviders[provider.id] = Number(value.amount || 0);
      });
      var sums = {};
      config.sumFields.forEach(function (field) {
        sums[field] = record[field] == null ? null : Number(record[field]);
      });
      var label = '<div style="display:flex;align-items:center;gap:8px">' +
        '<span style="width:6px;height:6px;border-radius:50%;background:#C9BFBF;flex-shrink:0"></span>' +
        '<span style="font-size:12px;font-weight:600;color:#6A6161">' + escapeHtml(record.record_date) + "</span>" +
        '<span style="font-size:11px;color:#9B9292;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:150px">' +
        escapeHtml(config.leafLabel(record)) + "</span></div>";
      // The whole record row opens the modal when editing is allowed (ТЗ 5).
      var clickable = state.canManage && config.rowOpensModal;
      return '<tr data-owner="' + escapeHtml(owner || "") + '"' +
        (clickable ? ' data-record="' + escapeHtml(record.id) + '"' : "") +
        ' style="background:#fff' + (clickable ? ";cursor:pointer" : "") + '">' +
        '<td style="position:sticky;left:0;z-index:2;background:#fff;padding:10px 16px 10px ' +
        (16 + depth * 22) + 'px;border-bottom:1px solid #F2EEEE;border-right:1px solid #E8E2E2">' + label + "</td>" +
        metricCells({
          services: recordServices,
          providers: recordProviders,
          providersTotal: providersSum(record),
          sums: sums
        }, { bold: false, leaf: true, record: record }) +
        (hasActionColumn()
          ? td('<button data-edit="' + escapeHtml(record.id) + '" title="Изменить данные" style="border:none;background:transparent;cursor:pointer;padding:2px">' +
              '<svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M4 20h4L19.5 8.5a2.1 2.1 0 0 0-3-3L5 17v3Z" stroke="#857D7D" stroke-width="2" stroke-linejoin="round"/></svg></button>', {})
          : "") +
        "</tr>";
    }

    // The tree the last render drew, so a click can resolve a path without rebuilding it.
    var currentTree = null;

    function findNode(path) {
      if (!currentTree) return null;
      var node = currentTree;
      var keys = path.split("|");
      for (var i = 0; i < keys.length; i += 1) {
        node = node.children[keys[i]];
        if (!node) return null;
      }
      return node;
    }

    function renderTable() {
      var body = el("TableBody");
      if (!body) return;
      if (!state.groups.length) {
        currentTree = null;
        body.innerHTML = '<tr><td colspan="' + columnCount() +
          '" style="padding:44px 24px;text-align:center;color:#9B9292;font-size:13px">' +
          escapeHtml(config.emptyMessage) + "</td></tr>";
        return;
      }
      currentTree = groupRecords(state.groups, activeLevels());
      var output = [];
      currentTree.order.forEach(function (key) {
        renderNodeRows(currentTree.children[key], 0, key, output);
      });
      body.innerHTML = output.join("");
      applyVisibility();
    }

    /* A row is visible unless one of its ancestors is folded; leaf rows additionally
     * require their own node to be open. Pure class work — no HTML is rebuilt. */
    function isPathVisible(path) {
      var keys = path.split("|");
      var prefix = "";
      for (var i = 0; i < keys.length - 1; i += 1) {
        prefix = i ? prefix + "|" + keys[i] : keys[i];
        if (state.collapsed[prefix]) return false;
      }
      return true;
    }

    function applyVisibility() {
      var body = el("TableBody");
      if (!body) return;
      var rows = body.children;
      for (var i = 0; i < rows.length; i += 1) {
        var row = rows[i];
        var path = row.getAttribute("data-node");
        var visible;
        if (path) {
          visible = isPathVisible(path);
        } else {
          var owner = row.getAttribute("data-owner");
          visible = owner
            ? isPathVisible(owner) && !!state.leavesOpen[owner]
            : true;
        }
        row.classList.toggle("cs-row--hidden", !visible);
      }
    }

    /* One listener for the whole table: rows are re-rendered constantly, and binding
     * per row leaked a handler per row on every collapse. */
    function bindTableEvents() {
      var body = el("TableBody");
      if (!body || body.dataset.boardBound) return;
      body.dataset.boardBound = "1";
      body.addEventListener("click", function (event) {
        var more = event.target.closest("[data-more]");
        if (more) {
          event.stopPropagation();
          loadLeaves(more.getAttribute("data-more"), true).catch(fail);
          return;
        }
        var edit = event.target.closest("[data-edit]");
        if (edit) {
          event.stopPropagation();
          openEditModal(findLoadedRecord(edit.getAttribute("data-edit")));
          return;
        }
        var recordRow = event.target.closest("[data-record]");
        if (recordRow) {
          openEditModal(findLoadedRecord(recordRow.getAttribute("data-record")));
          return;
        }
        var row = event.target.closest("[data-node]");
        if (!row) return;
        var path = row.getAttribute("data-node");
        if (row.getAttribute("data-leaf-node")) {
          var open = !state.leavesOpen[path];
          state.leavesOpen[path] = open;
          row.setAttribute("data-open", open ? "1" : "0");
          // Records already fetched are still in the DOM, so reopening costs nothing.
          if (open && !state.leaves[path]) loadLeaves(path, false).catch(fail);
          else applyVisibility();
          return;
        }
        state.collapsed[path] = !state.collapsed[path];
        row.setAttribute("data-open", state.collapsed[path] ? "0" : "1");
        applyVisibility();
      });
    }

    function findLoadedRecord(id) {
      var found = null;
      Object.keys(state.leaves).some(function (path) {
        found = (state.leaves[path].items || []).find(function (item) {
          return item.id === id;
        });
        return !!found;
      });
      return found || null;
    }

    /* ----- data loading ----- */

    function fail(error) {
      if (error && error.status === 401) {
        window.location.replace("/login.html?next=" + encodeURIComponent(
          window.location.pathname + window.location.search));
        return;
      }
      toast(error && error.message ? error.message : "Ошибка запроса", "error");
    }

    async function loadRefs() {
      var results = await Promise.all([
        api.getAll("/services"),
        api.getAll("/spend-providers"),
        api.get("/users/options"),
        api.getAll("/offers" + (config.offersQuery || "")),
        api.getAll("/partners"),
        api.get("/me/preferences/" + config.preferenceKey),
        api.get("/me/preferences/" + config.displayPreferenceKey)
      ]);
      state.services = (results[0].items || []).filter(function (s) { return s.status === "active"; });
      state.providers = (results[1].items || []).filter(function (s) { return s.status === "active"; });
      state.buyers = results[2] || [];
      state.offers = results[3].items || [];
      state.partners = (results[4].items || []);
      var preference = results[5] && results[5].value;
      state.structure = preference && Array.isArray(preference.items) && preference.items.length
        ? preference : defaultStructure();
      var display = (results[6] && results[6].value) || {};
      // Merged rather than replaced, so a preference saved before a new switch existed
      // still gets that switch's default instead of `undefined`.
      state.display = Object.assign(defaultDisplay(), display, {
        groups: Object.assign(defaultDisplay().groups, display.groups || {})
      });
      setFilterItems("FilterBuyer", state.buyers.map(nameItem));
      setFilterItems("FilterPartner", state.partners.map(nameItem));
      fillOfferFilter();
    }

    function nameItem(row) {
      return { value: row.id, label: row.name };
    }

    function setFilterItems(id, items) {
      if (filters[id]) filters[id].setItems(items);
    }

    /* Офферы выбранных баеров: группа Keitaro каждого плюс то, что назначено
     * лично ему. Без выбранного баера остаётся весь список, который сервер и так
     * сузил до видимой ветки — баер видит свои офферы, тимлид — офферы своих
     * баеров. */
    function offersForBuyers(buyerIds) {
      if (!buyerIds.length) return state.offers;
      var known = state.buyers.filter(function (item) {
        return buyerIds.indexOf(item.id) >= 0;
      });
      if (!known.length) return state.offers;
      var groups = known.map(function (buyer) {
        return (buyer.keitaro_offer_group || "").trim().toLowerCase();
      }).filter(Boolean);
      var scoped = state.offers.filter(function (offer) {
        var assigned = (offer.buyers || []).some(function (item) {
          return buyerIds.indexOf(item.id) >= 0;
        });
        return assigned ||
          groups.indexOf((offer.group_name || "").trim().toLowerCase()) >= 0;
      });
      // У баеров без группы и без назначений сузить не по чему — прячем весь
      // список только тогда, когда сужение действительно что-то нашло.
      return scoped.length || groups.length ? scoped : state.offers;
    }

    function fillOfferFilter() {
      var offers = offersForBuyers(filterValues("FilterBuyer"));
      setFilterItems("FilterOffer", offers.map(nameItem));
      var geos = [];
      offers.forEach(function (offer) {
        if (offer.geo && geos.indexOf(offer.geo) < 0) geos.push(offer.geo);
      });
      geos.sort();
      setFilterItems("FilterGeo", geos.map(function (geo) {
        return { value: geo, label: geo };
      }));
    }

    /* One request for the whole board: the server returns rows already summed per
     * buyer × offer, which is every grouping the structure bar can ask for. The raw
     * records stay on the server until a branch is opened. */
    function fetchGroups() {
      var query = queryString(filterParams());
      return api.get(config.groupsEndpoint + (query ? "?" + query : ""));
    }

    async function loadRecords(pending) {
      // On the first load the request is already in flight next to the references —
      // waiting for those first would cost another round trip on every navigation.
      var data = await (pending || fetchGroups());
      state.groups = data.groups || [];
      state.recordCount = data.record_count || 0;
      state.truncated = !!data.truncated;
      state.leaves = {};
      renderTable();
      if (state.truncated) {
        toast("Слишком много групп — показан срез. Сузьте период или фильтры", "info");
      }
      if (config.onData) config.onData(state, currentTree);
      // Branches the user had open must not be left showing a spinner forever.
      await Promise.all(Object.keys(state.leavesOpen)
        .filter(function (path) { return state.leavesOpen[path] && findNode(path); })
        .map(function (path) {
          return loadLeaves(path, false).catch(function () { /* shown in the row */ });
        }));
    }

    async function loadLeaves(path, append) {
      var node = findNode(path);
      if (!node) return;
      var leaf = state.leaves[path];
      if (leaf && leaf.loading) return;
      if (append && !leaf) return;
      // `offset` counts server rows, not kept ones: when a dimension cannot be filtered
      // server-side, part of a page is dropped here and paging by kept rows would
      // request the same page forever.
      var offset = append ? leaf.offset : 0;
      state.leaves[path] = {
        items: leaf && append ? leaf.items : [],
        offset: offset,
        done: false,
        loading: true,
        error: null
      };
      renderTable();

      var params = filterParams();
      // The node is narrower than the filter bar, so its own values win. A dimension
      // with no value (no GEO, no partner) has no filter — those pages get matched
      // against the node client-side below.
      var unfiltered = [];
      Object.keys(node.scope).forEach(function (levelKey) {
        var value = node.scope[levelKey];
        if (value == null || value === "") unfiltered.push(levelKey);
        else params[LEVELS[levelKey].filter] = value;
      });
      params.limit = LEAF_PAGE;
      params.offset = offset;
      try {
        var page = await api.get(config.endpoint + "?" + queryString(params));
        var fetched = page.items || [];
        var items = fetched.filter(function (record) {
          return unfiltered.every(function (levelKey) {
            return LEVELS[levelKey].matches(record, node.scope[levelKey]);
          });
        });
        var target = state.leaves[path];
        target.items = target.items.concat(items);
        target.offset = offset + fetched.length;
        target.done = fetched.length < LEAF_PAGE;
        target.loading = false;
      } catch (error) {
        state.leaves[path] = {
          items: leaf && append ? leaf.items : [],
          offset: offset,
          done: false,
          loading: false,
          error: error && error.message ? error.message : "Не удалось загрузить записи"
        };
        renderTable();
        throw error;
      }
      renderTable();
    }

    /* ----- edit modal ----- */

    function modalInput(label, id, type, value, attrs) {
      var inputType = type || "text";
      var inputMode = inputType === "number" ? ' inputmode="decimal"' : "";
      var fieldClass = id === "financeEditLink" ? " board-edit-field--wide" : "";
      return '<label class="board-edit-field' + fieldClass + '" for="' + id + '" style="display:flex;flex-direction:column;gap:5px;font-size:11.5px;font-weight:700;color:#6A6161">' +
        escapeHtml(label) +
        '<input class="board-edit-input" id="' + id + '" type="' + inputType + '" value="' +
        escapeHtml(value == null ? "" : value) + '"' + inputMode + " " + (attrs || "") +
        ' autocomplete="off" style="border:1px solid #EBE6E6;border-radius:9px;padding:9px 11px;font:600 13px Inter,sans-serif;outline:none;color:#070505">' +
        "</label>";
    }

    /* ----- агенты и платёжки ----- */

    /* Раньше секция раскладывала сразу всех агентов сеткой полей, а заполняли
     * из них один-два: остальные стояли пустыми и только мешали читать форму.
     * Теперь агента добавляют строкой — сам агент и сумма. */

    var AGENT_DROP_SVG =
      '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
      '<path d="m6 6 12 12M18 6 6 18" stroke="currentColor" stroke-width="2" ' +
      'stroke-linecap="round"/></svg>';

    /* Процент агента прямо в списке: сумму вводят до комиссии, и без него
       непонятно, во что она превратится. Ноль не пишем — скобка «(0%)» только
       занимает место. */
    function agentLabel(provider) {
      var percent = Number(provider.commission_pct || 0);
      if (!isFinite(percent) || percent <= 0) return provider.name;
      // 7 вместо 7.0000, но 7.5 остаётся 7.5.
      var shown = String(Number(percent.toFixed(2)));
      return provider.name + " (" + shown + "%)";
    }

    function agentRows() {
      var host = byId(p + "EditAgents");
      return host
        ? Array.prototype.slice.call(host.querySelectorAll("[data-agent-row]"))
        : [];
    }

    function agentPick(row) {
      return row.querySelector("[data-agent-select]");
    }

    function takenProviders(except) {
      return agentRows().filter(function (row) { return row !== except; })
        .map(function (row) { return agentPick(row).value; });
    }

    /* Один агент в записи может быть только один раз, поэтому занятые варианты
     * гасим прямо в списке — так видно, что агент уже добавлен строкой выше. */
    function refreshAgentRows() {
      var rows = agentRows();
      rows.forEach(function (row) {
        var taken = takenProviders(row);
        Array.prototype.forEach.call(agentPick(row).options, function (option) {
          option.disabled = taken.indexOf(option.value) >= 0;
        });
      });
      var button = byId(p + "EditAgentAdd");
      if (button) button.disabled = rows.length >= state.providers.length;
      var empty = byId(p + "EditAgentsEmpty");
      if (empty) empty.hidden = rows.length > 0;
    }

    function addAgentRow(providerId, amount) {
      var host = byId(p + "EditAgents");
      if (!host) return null;
      var taken = takenProviders(null);
      var free = state.providers.filter(function (provider) {
        return taken.indexOf(provider.id) < 0;
      });
      var chosen = providerId || (free.length ? free[0].id : null);
      if (!chosen) return null;
      var row = document.createElement("div");
      row.className = "board-edit-agent";
      row.setAttribute("data-agent-row", "1");
      row.innerHTML =
        '<label class="board-edit-field" style="display:flex;flex-direction:column;gap:5px;' +
        'font-size:11.5px;font-weight:700;color:#6A6161">Агент' +
        '<select class="board-edit-select" data-agent-select style="border:1px solid #EBE6E6;' +
        'border-radius:9px;padding:9px 11px;font:600 13px Inter,sans-serif;outline:none">' +
        state.providers.map(function (provider) {
          return '<option value="' + escapeHtml(provider.id) + '"' +
            (provider.id === chosen ? " selected" : "") + ">" +
            escapeHtml(agentLabel(provider)) + "</option>";
        }).join("") +
        "</select></label>" +
        '<label class="board-edit-field" style="display:flex;flex-direction:column;gap:5px;' +
        'font-size:11.5px;font-weight:700;color:#6A6161">Сумма до комиссии, USD' +
        '<input class="board-edit-input" type="number" data-agent-amount inputmode="decimal" ' +
        'step="any" min="0" autocomplete="off" value="' +
        escapeHtml(amount == null ? "" : amount) +
        '" style="border:1px solid #EBE6E6;border-radius:9px;padding:9px 11px;' +
        'font:600 13px Inter,sans-serif;outline:none;color:#070505"></label>' +
        '<button type="button" class="board-edit-agent-drop" data-agent-drop ' +
        'aria-label="Убрать агента">' + AGENT_DROP_SVG + "</button>";
      host.appendChild(row);
      refreshAgentRows();
      return row;
    }

    function bindAgentSection(record) {
      var host = byId(p + "EditAgents");
      if (!host) return;
      host.addEventListener("click", function (event) {
        var drop = event.target.closest("[data-agent-drop]");
        if (!drop) return;
        drop.closest("[data-agent-row]").remove();
        refreshAgentRows();
      });
      host.addEventListener("change", function (event) {
        if (event.target.closest("[data-agent-select]")) refreshAgentRows();
      });
      var add = byId(p + "EditAgentAdd");
      if (add) add.addEventListener("click", function () {
        var row = addAgentRow(null, null);
        if (row) row.querySelector("[data-agent-amount]").focus();
      });
      // Порядок берём из справочника, а не из записи: так строки не прыгают
      // между открытиями одной и той же записи.
      state.providers.forEach(function (provider) {
        var value = record && record.providers && record.providers[provider.id];
        if (!value) return;
        var amount = Number(value.base_amount);
        if (!isFinite(amount) || amount <= 0) return;
        addAgentRow(provider.id, amount);
      });
      refreshAgentRows();
    }

    var modalKeydownHandler = null;

    function closeModal() {
      var modal = byId(p + "EditModal");
      if (modal) modal.remove();
      if (modalKeydownHandler) {
        document.removeEventListener("keydown", modalKeydownHandler);
        modalKeydownHandler = null;
      }
      document.body.style.overflow = "";
    }

    function openEditModal(record) {
      closeModal();
      if (p === "media") ensureMediaModalStyles();
      if (p === "finance") ensureFinanceModalStyles();
      var overlay = document.createElement("div");
      overlay.id = p + "EditModal";
      overlay.className = "board-edit-overlay board-edit-overlay--" + p;
      overlay.style.cssText =
        "position:fixed;inset:0;z-index:9999;background:rgba(18,12,12,.45);display:flex;" +
        "align-items:flex-start;justify-content:center;padding:40px 16px;overflow-y:auto";
      var buyersOptions = state.buyers.map(function (buyer) {
        return '<option value="' + escapeHtml(buyer.id) + '"' +
          (record && record.buyer_id === buyer.id ? " selected" : "") + ">" +
          escapeHtml(buyer.name) + "</option>";
      }).join("");
      var offersOptions = state.offers.map(function (offer) {
        // Только название: GEO у оффера одно, и приписка к нему ничего не
        // различала — оффер и так уникален по имени.
        return '<option value="' + escapeHtml(offer.id) + '"' +
          (record && record.offer_id === offer.id ? " selected" : "") + ">" +
          escapeHtml(offer.name) + "</option>";
      }).join("");
      var servicesInputs = state.services.map(function (service) {
        var value = record && record.services && record.services[service.id];
        return modalInput(service.name, p + "EditService_" + service.id, "number",
          value ? Number(value.quantity) : "", 'step="any" min="0"');
      }).join("");
      overlay.innerHTML =
        '<div class="board-edit-card" role="dialog" aria-modal="true" tabindex="-1" aria-labelledby="' + p +
        'EditTitle" style="background:#fff;border-radius:18px;max-width:720px;width:100%;padding:26px 28px;box-shadow:0 24px 70px rgba(18,12,12,.3)">' +
        '<div class="board-edit-header" style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:18px;gap:18px">' +
        '<div><h2 class="board-edit-title" id="' + p +
        'EditTitle" style="font-family:\'Alumni Sans\',\'Inter\',sans-serif;font-size:19px;font-weight:700">' +
        (record ? "Изменить данные" : "Добавить данные") + "</h2>" +
        '<span class="board-edit-subtitle" style="display:none">' +
        (p === "finance" ? "Финансовые показатели и распределение затрат" :
          "Ручные показатели и распределение затрат") +
        "</span></div>" +
        '<button class="board-edit-close" id="' + p +
        'EditClose" type="button" aria-label="Закрыть" style="border:none;background:#F7F4F4;border-radius:9px;width:32px;height:32px;cursor:pointer;font-size:15px;font-weight:700;color:#6A6161">' +
        '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>' +
        "</button></div>" +
        '<div class="board-edit-body" style="min-width:0">' +
        '<div class="board-edit-context">' +
        '<div class="board-edit-main-grid" style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:16px">' +
        modalInput("Дата", p + "EditDate", "date", record ? record.record_date : new Date().toISOString().slice(0, 10)) +
        '<label class="board-edit-field" for="' + p +
        'EditBuyer" style="display:flex;flex-direction:column;gap:5px;font-size:11.5px;font-weight:700;color:#6A6161">Баер' +
        '<select class="board-edit-select" id="' + p +
        'EditBuyer" style="border:1px solid #EBE6E6;border-radius:9px;padding:9px 11px;font:600 13px Inter,sans-serif;outline:none">' +
        buyersOptions + "</select></label>" +
        '<label class="board-edit-field board-edit-offer-field" for="' + p +
        'EditOffer" style="display:flex;flex-direction:column;gap:5px;font-size:11.5px;font-weight:700;color:#6A6161">Оффер' +
        '<select class="board-edit-select" id="' + p +
        'EditOffer" style="border:1px solid #EBE6E6;border-radius:9px;padding:9px 11px;font:600 13px Inter,sans-serif;outline:none;max-width:100%">' +
        offersOptions + "</select></label>" +
        "</div></div>" +
        (!config.hideServices && state.services.length
          ? '<div class="board-edit-section-label">Сервисы</div>' +
            '<section class="board-edit-section board-edit-section--services">' +
            '<div class="board-edit-grid" style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px">' +
            servicesInputs + "</div></section>"
          : "") +
        (state.providers.length
          // Подписи у блока нет: в карточке он один, и «Агенты и платёжки»
          // повторяли то, что и так написано в самой строке — «Агент».
          ? '<section class="board-edit-section board-edit-section--providers">' +
            '<div class="board-edit-agents" id="' + p + 'EditAgents"></div>' +
            '<div class="board-edit-agents-empty" id="' + p +
            'EditAgentsEmpty">Ни одного агента ещё не добавлено</div>' +
            '<button type="button" class="board-edit-agent-add" id="' + p + 'EditAgentAdd">' +
            '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
            '<path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2.4" ' +
            'stroke-linecap="round"/></svg>Добавить агента</button>' +
            "</section>"
          : "") +
        (config.modalMetricInputs
          ? '<div class="board-edit-section-label">Показатели</div>' +
            '<section class="board-edit-section board-edit-section--metrics">' +
            '<div class="board-edit-grid board-edit-grid--metrics" style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px">' +
            config.modalMetricInputs(record, modalInput) +
            "</div></section>"
          : "") + "</div>" +
        '<div class="board-edit-footer" style="display:flex;justify-content:flex-end;gap:10px;margin-top:22px">' +
        '<button class="board-edit-button board-edit-cancel" id="' + p +
        'EditCancel" type="button" style="border:1px solid #EBE6E6;background:#fff;border-radius:11px;padding:11px 18px;font:700 13px Inter,sans-serif;color:#6A6161;cursor:pointer">Отмена</button>' +
        '<button class="board-edit-button board-edit-save" id="' + p +
        'EditSave" type="button" style="border:none;background:#B91414;color:#fff;font-family:Alumni Sans,Inter,sans-serif;text-transform:uppercase;letter-spacing:.02em;border-radius:11px;padding:11px 22px;font:600 15px Alumni Sans,Inter,sans-serif;cursor:pointer;box-shadow:0 8px 18px rgba(185,20,20,.28)">Сохранить</button>' +
        "</div></div>";
      document.body.appendChild(overlay);
      document.body.style.overflow = "hidden";
      overlay.addEventListener("click", function (event) {
        if (event.target === overlay) closeModal();
      });
      modalKeydownHandler = function (event) {
        if (event.key === "Escape") closeModal();
      };
      document.addEventListener("keydown", modalKeydownHandler);
      byId(p + "EditClose").addEventListener("click", closeModal);
      byId(p + "EditCancel").addEventListener("click", closeModal);
      byId(p + "EditSave").addEventListener("click", function () {
        saveModal(record).catch(fail);
      });
      bindAgentSection(record);
      var dialog = overlay.querySelector(".board-edit-card");
      if (dialog) dialog.focus();
    }

    function numberValue(id) {
      var element = byId(id);
      if (!element || element.value === "") return null;
      var value = Number(element.value);
      return isFinite(value) ? value : null;
    }

    async function saveModal(record) {
      var recordDate = byId(p + "EditDate").value;
      var buyerId = byId(p + "EditBuyer").value;
      var offerId = byId(p + "EditOffer").value;
      if (!recordDate || !buyerId || !offerId) {
        toast("Заполните дату, баера и оффер", "error");
        return;
      }
      // `null` (not `[]`) when the modal has no services block: the API then leaves
      // the record's existing service values alone instead of deleting them.
      var services = null;
      if (!config.hideServices) {
        services = [];
        state.services.forEach(function (service) {
          var value = numberValue(p + "EditService_" + service.id);
          if (value != null && value > 0) {
            services.push({ service_id: service.id, quantity: value });
          }
        });
      }
      // Строка без суммы — недозаполненная, а не нулевая: сохранять её нечем.
      // Убранная строка тем самым и снимает сумму агента с записи.
      var providers = [];
      var seenProviders = {};
      agentRows().forEach(function (row) {
        var providerId = agentPick(row).value;
        var input = row.querySelector("[data-agent-amount]");
        var value = input.value === "" ? null : Number(input.value);
        if (!providerId || seenProviders[providerId]) return;
        if (value == null || !isFinite(value) || value <= 0) return;
        seenProviders[providerId] = true;
        providers.push({ provider_id: providerId, base_amount: value });
      });
      var saveButton = byId(p + "EditSave");
      saveButton.disabled = true;
      saveButton.textContent = "Сохраняю…";
      try {
        await config.save({
          record: record,
          record_date: recordDate,
          buyer_id: buyerId,
          offer_id: offerId,
          services: services,
          providers: providers,
          numberValue: numberValue
        });
        toast(record ? "Запись обновлена" : "Запись создана");
        closeModal();
        await loadRecords();
      } finally {
        saveButton.disabled = false;
        saveButton.textContent = "Сохранить";
      }
    }

    /* ----- init ----- */

    async function init(user) {
      state.user = user;
      state.canManage = hasPermission(user, config.managePermission);
      document.body.classList.add(
        "celestial-board-page",
        "celestial-board-page--" + p
      );
      ensureBoardTableStyles();
      var tableHead = el("TableHead");
      var table = tableHead && tableHead.closest("table");
      if (table) {
        table.classList.add("celestial-board-table");
        if (config.columnDividers) table.classList.add("cs-table--dividers");
        if (table.parentElement) {
          table.parentElement.classList.add("celestial-board-scroll");
        }
      }
      var editButton = el("EditBtn");
      if (editButton) {
        if (state.canManage) {
          editButton.addEventListener("click", function () { openEditModal(null); });
        } else {
          editButton.style.display = "none";
        }
      }
      // Both requests leave together: the board data does not depend on the catalog,
      // and running them back to back added a whole round trip to every navigation.
      var pendingGroups = fetchGroups();
      // A rejection handled later still counts as unhandled until then.
      pendingGroups.catch(function () { /* surfaced by loadRecords below */ });
      // Раньше ссылок: справочники наполняют уже созданные поля фильтров.
      bindFilters();
      await loadRefs();
      renderStructureBar();
      bindViewControls();
      if (table && state.display.dense) {
        table.classList.add("celestial-board-table--dense");
      }
      renderHead();
      bindTableEvents();
      if (config.afterInit) config.afterInit(state, { loadRecords: loadRecords, fail: fail, filterQuery: filterQuery });
      await loadRecords(pendingGroups);
    }

    return { init: init, state: state, openEditModal: openEditModal };
  }

  /* ---------- Mediaboard config ---------- */

  var mediaBoard = createBoard({
    prefix: "media",
    endpoint: "/media-records",
    groupsEndpoint: "/media-records/groups",
    preferenceKey: "mediaboard.structure",
    displayPreferenceKey: "mediaboard.display",
    managePermission: "media.manage",
    funnelTitle: "Воронка",
    // Медиаборд only: no services anywhere, agents and payments as one column in
    // the table (the split stays in the modal), no funnel metrics in the modal,
    // column dividers, and a click anywhere on a record opens the modal.
    hideServices: true,
    // Группа «Оффера» — витрина одноимённого модуля, в Медиаборде её нет; сам
    // список сервер сужает до офферов видимой ветки.
    offersQuery: "?exclude_offers_group=true&scope_offers=true",
    hideProviders: true,
    columnDividers: true,
    rowOpensModal: true,
    metricColumns: [
      { group: "funnel", label: "INST", kind: "num", get: function (s) { return s.installs; } },
      { group: "funnel", label: "REG", kind: "num", get: function (s) { return s.registrations; } },
      { group: "funnel", label: "FTD", kind: "num", get: function (s) { return s.ftd; } },
      { group: "costs", label: "SPEND", kind: "money", get: function (s) { return s.spend; } },
      { group: "result", label: "REVENUE", kind: "money", get: function (s) { return s.revenue; } },
      {
        group: "result", label: "PROFIT", kind: "money", bold: true, tone: signTone,
        get: function (s) { return amount(s.revenue) - amount(s.spend); }
      },
      {
        group: "result", label: "ROI", kind: "percent", bold: true, tone: signTone,
        get: function (s) {
          var spend = amount(s.spend);
          return spend > 0 ? (amount(s.revenue) - spend) / spend * 100 : null;
        }
      },
      {
        group: "result", label: "CPD", kind: "money",
        get: function (s) {
          var spend = amount(s.spend);
          return s.ftd > 0 && spend > 0 ? spend / s.ftd : null;
        }
      }
    ],
    sumFields: ["installs", "registrations", "ftd", "spend", "revenue"],
    emptyMessage: "Данные появятся после первой синхронизации Keitaro или ручного ввода",
    leafLabel: function (record) { return record.offer || ""; },
    // No `modalMetricInputs`: the modal no longer edits INST/REG/FTD/Revenue/SPEND.
    save: async function (form) {
      /* The modal owns the record's identity and its agents/payments split, nothing
       * else. INST/REG/FTD/Revenue and the SPEND override are left out of the
       * payload on purpose, so what Keitaro synced survives a manual save, and
       * `services` is absent so the record keeps its service values. */
      var saved = await api.post("/media-records", {
        record_date: form.record_date,
        buyer_id: form.buyer_id,
        offer_id: form.offer_id,
        source: "manual"
      });
      await api.put("/media-records/" + saved.id + "/values", {
        spend_providers: form.providers
      });
    }
  });

  // Финансы переехали в собственный модуль (finance-ui.js) — там книга баера по
  // дням, а не построчные записи, и общей с Медиабордом механики уже нет.
  window.CelestialBoard = {
    initMedia: function (user) { return mediaBoard.init(user); }
  };
})();
