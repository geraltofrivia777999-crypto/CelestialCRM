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
      // У Safari значение даты — отдельный внутренний блок. Выравниваем его
      // по высоте поля (42px минус рамки), сохраняя текст у левого края.
      '.board-edit-overlay--media .board-edit-input[type="date"]{text-align:left;line-height:40px!important}' +
      '.board-edit-overlay--media .board-edit-input[type="date"]::-webkit-datetime-edit{padding:0;' +
      "text-align:left}" +
      '.board-edit-overlay--media .board-edit-input[type="date"]::-webkit-date-and-time-value{' +
      "margin:0;text-align:left;line-height:40px}" +
      '.board-edit-overlay--media .board-edit-input[type="date"]::-webkit-calendar-picker-indicator{' +
      "margin-left:auto;opacity:.5;cursor:pointer}" +
      // Агенты добавляются строками, поэтому у секции своя раскладка.
      ".board-edit-overlay--media .board-edit-agents{display:grid;gap:9px}" +
      ".board-edit-overlay--media .board-edit-agent{display:grid;align-items:end;" +
      "column-gap:10px;row-gap:4px;grid-template-columns:minmax(0,1fr) 190px 38px}" +
      ".board-edit-overlay--media .board-edit-agent-hint{grid-column:2/3;" +
      "font:600 10.5px 'Inter',sans-serif;color:#9B9292;min-height:13px}" +
      ".board-edit-overlay--media .board-edit-agent-hint:empty{min-height:0}" +
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
      // Блоки без подписи иначе стоят вплотную: отступ между ними даёт
      // подпись, а у блока агентов её нет.
      ".board-edit-overlay--media .board-edit-context + .board-edit-section," +
      ".board-edit-overlay--media .board-edit-section + .board-edit-section{margin-top:14px}" +
      // Подпись блока стоит над рамкой и выглядит как подписи полей рядом:
      // внутри рамки она читалась как часть содержимого, а не как её название.
      ".board-edit-overlay--media .board-edit-section-label{margin:16px 0 7px;" +
      "font:700 11px 'Inter',sans-serif;letter-spacing:.6px;text-transform:uppercase;" +
      "color:#857D7D}" +
      ".board-edit-overlay--media .board-edit-grid{display:grid!important;" +
      "grid-template-columns:repeat(auto-fit,minmax(145px,1fr))!important;gap:10px!important}" +
      // Блок ввода: в окне их может быть несколько, и между ними нужна не
      // просто щель, а видимая граница — иначе поля соседних дней сливаются
      // в одну простыню.
      // Закреплённая шапка: те же подписи, что у полей, но значения текстом —
      // менять их в этом окне нельзя, и поле только обещало бы обратное.
      ".board-edit-overlay--media .board-edit-locked{display:grid;" +
      "grid-template-columns:150px 190px minmax(0,1fr);gap:12px}" +
      ".board-edit-overlay--media .board-edit-locked__item{display:flex;flex-direction:column;" +
      "gap:6px;min-width:0}" +
      ".board-edit-overlay--media .board-edit-locked__item b{display:block;min-height:42px;" +
      "padding:11px 12px;border:1px solid #EBE6E6;border-radius:10px;background:#fff;" +
      "font:600 12.5px 'Inter',sans-serif;color:#2A2020;overflow:hidden;text-overflow:ellipsis;" +
      "white-space:nowrap}" +
      // Окно тира: в выбранный календарём день у тира может не быть офферов.
      ".board-edit-overlay--media .board-edit-day-hint{margin-top:8px;font:600 11px 'Inter',sans-serif;" +
      "color:#B91414}" +
      ".board-edit-overlay--media .board-edit-day-hint[hidden]{display:none}" +
      "@media(max-width:760px){" +
      ".board-edit-overlay--media .board-edit-locked{grid-template-columns:1fr 1fr}" +
      ".board-edit-overlay--media .board-edit-locked__item:last-child{grid-column:1/-1}" +
      "}" +
      ".board-edit-overlay--media .board-edit-blocks{display:grid;gap:14px}" +
      ".board-edit-overlay--media .board-edit-block{padding:0;border:0;background:none}" +
      ".board-edit-overlay--media .board-edit-blocks .board-edit-block + .board-edit-block{" +
      "padding-top:14px;border-top:1px dashed #E5DFDF}" +
      ".board-edit-overlay--media .board-edit-block__head{display:flex;align-items:center;" +
      "justify-content:space-between;gap:10px;margin-bottom:9px;font:700 11px 'Inter',sans-serif;" +
      "letter-spacing:.6px;text-transform:uppercase;color:#857D7D}" +
      ".board-edit-overlay--media .board-edit-block__drop{border:0;background:none;cursor:pointer;" +
      "font:700 11px 'Inter',sans-serif;letter-spacing:.4px;text-transform:uppercase;color:#B91414}" +
      ".board-edit-overlay--media .board-edit-block__drop[hidden]{display:none}" +
      // Кнопка следующего блока — полосой во всю ширину под последним блоком:
      // так она читается как «здесь появится ещё один», а не как действие в
      // ряду с полями.
      ".board-edit-overlay--media .board-edit-block-add{display:flex;align-items:center;" +
      "justify-content:center;gap:7px;width:100%;margin-top:14px;height:42px;padding:0 14px;" +
      "border:1px dashed #E0D8D8;border-radius:12px;background:#FBF9F9;" +
      "font:700 12px 'Inter',sans-serif;color:#6A6161;cursor:pointer;" +
      "transition:border-color .18s,background .18s,color .18s}" +
      ".board-edit-overlay--media .board-edit-block-add:hover{border-color:#D06060;" +
      "background:#FCF7F7;color:#B91414}" +
      ".board-edit-overlay--media .board-edit-footer{display:flex;align-items:center;" +
      "justify-content:flex-end;gap:10px;margin:0!important;padding:15px 24px 18px!important;" +
      "border-top:1px solid #EBE6E6;background:#fff;flex-shrink:0}" +
      ".board-edit-overlay--media .board-edit-button{height:40px;padding:0 16px!important;" +
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
      ".board-edit-overlay--media .board-edit-agent-hint{grid-area:3/1}" +
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

    /* Панель — как поле офферов в CapAlert: выбранное плашками над поиском,
       в списке только невыбранное и без флажков. С флажками отмеченный вариант
       терялся среди сотни строк, и понять, что уже выбрано, можно было только
       пролистав весь список. */
    function rowsHtml() {
      if (!items.length) {
        return '<div class="csel-empty">Нет вариантов</div>';
      }
      return items.map(function (item) {
        var on = chosen.indexOf(String(item.value)) >= 0;
        return '<button type="button" role="option" aria-selected="false" ' +
          'class="csel-option cmf-option" data-value="' + escapeHtml(item.value) +
          '" data-search="' + escapeHtml((item.label + " " + (item.hint || "")).toLowerCase()) + '"' +
          (on ? " hidden" : "") + ">" +
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
      var first = null;
      Array.prototype.forEach.call(panel.querySelectorAll(".cmf-option"), function (node) {
        var hit = chosen.indexOf(node.getAttribute("data-value")) < 0 &&
          (!needle || node.getAttribute("data-search").indexOf(needle) >= 0);
        node.hidden = !hit;
        if (!hit) node.classList.remove("csel-option--active");
        if (hit) {
          shown += 1;
          if (!first) first = node;
        }
      });
      var empty = panel.querySelector(".cmf-none");
      if (empty) {
        empty.hidden = shown > 0 || !items.length;
        empty.textContent = !needle && chosen.length === items.length
          ? "Все варианты выбраны" : "Ничего не найдено";
      }
      // Ищут, чтобы выбрать: первое совпадение сразу под Enter.
      if (needle && first && !panel.querySelector(".cmf-option.csel-option--active")) {
        first.classList.add("csel-option--active");
      }
    }

    function moveActive(step) {
      if (!panel) return;
      var options = Array.prototype.filter.call(
        panel.querySelectorAll(".cmf-option"),
        function (node) { return !node.hidden; }
      );
      if (!options.length) return;
      var current = panel.querySelector(".cmf-option.csel-option--active");
      var index = options.indexOf(current);
      var next = index < 0
        ? options[step > 0 ? 0 : options.length - 1]
        : options[(index + step + options.length) % options.length];
      if (current) current.classList.remove("csel-option--active");
      next.classList.add("csel-option--active");
      if (next.scrollIntoView) next.scrollIntoView({ block: "nearest" });
    }

    /* Поиск после выбора не сбрасывается: в фильтре часто берут подряд
       несколько похожих значений — все офферы «1Win EVS», — и набирать запрос
       заново после каждого было бы мучением. */
    function refreshPanel() {
      if (!panel) return;
      var search = panel.querySelector(".csel-input");
      applySearch(search.value);
      place();
      search.focus();
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
      refreshPanel();
      if (onChange) onChange();
    }

    function open() {
      if (panel) { closeFilterPanel(); return; }
      closeFilterPanel();
      panel = document.createElement("div");
      panel.className = "csel-panel cmf-panel";
      // Поиск есть во всех фильтрах, даже коротких: человек ищет одинаково во
      // всех, и «здесь ищется, а здесь нет» само по себе сбивает.
      // Выбранное видно чипами в самом поле фильтра — дублировать его в
      // панели незачем: там только поиск и то, что ещё можно выбрать.
      panel.innerHTML =
        '<div class="csel-search"><input type="text" class="csel-input" ' +
        'placeholder="Поиск" autocomplete="off"></div>' +
        '<div class="csel-list" role="listbox" aria-multiselectable="true">' + rowsHtml() +
        '<div class="csel-empty cmf-none" hidden>Ничего не найдено</div></div>';
      document.body.appendChild(panel);
      openFilter = { close: close, owns: owns };
      renderField();
      var search = panel.querySelector(".csel-input");
      applySearch("");
      place();
      search.focus();
      // Клик по варианту и крестикам не уводит фокус из поиска.
      panel.addEventListener("mousedown", function (event) {
        if (event.target.closest(".cmf-option")) event.preventDefault();
      });
      panel.addEventListener("click", function (event) {
        var option = event.target.closest(".cmf-option");
        if (option) toggle(option.getAttribute("data-value"));
      });
      search.addEventListener("input", function () { applySearch(search.value); });
      search.addEventListener("keydown", function (event) {
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          moveActive(event.key === "ArrowDown" ? 1 : -1);
          return;
        }
        if (event.key === "Enter") {
          var active = panel.querySelector(".cmf-option.csel-option--active");
          if (active && !active.hidden) {
            event.preventDefault();
            toggle(active.getAttribute("data-value"));
          }
          return;
        }
        if (event.key === "Backspace" && !search.value && chosen.length) {
          toggle(chosen[chosen.length - 1]);
        }
      });
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
    tier: {
      /* Тир приходит с сервера посчитанным по справочнику «Тиры стран»: сам
         тир не фильтруется отдельным полем — он производная от GEO. */
      label: "Тир", filter: null,
      key: function (r) { return r.tier || "unassigned"; },
      name: function (r) { return TIER_NAMES[r.tier] || "Без тира"; },
      value: function (r) { return r.tier || null; },
      matches: function (r, value) { return (r.tier || null) === (value || null); }
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
    },
    agent: {
      /* Агент — разрез по тому, через кого внесён спенд. Строки для него
         раскладывает доска (agentRows): у одной записи агентов бывает
         несколько, а воронка и доход к агенту не привязаны — они остаются в
         строке «Без агента» и видны на уровнях выше. */
      label: "Агент", filter: null,
      key: function (r) { return r.agent_key || "none"; },
      name: function (r) { return r.agent_name || "Без агента"; },
      value: function (r) { return r.agent_key || "none"; },
      matches: function (r, value) {
        var ids = Object.keys(r.providers || {});
        return value === "none" ? !ids.length : ids.indexOf(value) >= 0;
      }
    },
    date: {
      /* День приходит в строках только когда уровень включён (by_date): в
         остальное время сервер суммирует период целиком. Своим параметром
         фильтра день не сужается — сужают границы периода, поэтому у уровня
         собственный `narrow`. */
      label: "Дата", filter: null, byDate: true,
      key: function (r) { return r.date || "—"; },
      name: function (r) { return dayLabel(r.date); },
      value: function (r) { return r.date || null; },
      narrow: function (params, value) {
        params.date_from = value;
        params.date_to = value;
      },
      matches: function (r, value) { return (r.record_date || null) === (value || null); }
    }
  };

  /* ISO-день в привычный вид: 2026-09-05 → 05.09.2026. */
  function dayLabel(day) {
    var parts = String(day || "").split("-");
    return parts.length === 3 ? parts[2] + "." + parts[1] + "." + parts[0] : "Без даты";
  }

  var TIER_NAMES = { T1: "Tier1", T23: "Tier2/3", unassigned: "Без тира" };

  /* Стрелка у названия колонки: показывает, по какой из них сейчас
     отсортирована таблица. Место под неё занято всегда, иначе заголовок
     дёргался бы по ширине при каждом клике. */
  function sortCaret(active) {
    return '<span style="display:inline-block;width:9px;margin-left:5px;color:' +
      (active ? "#B91414" : "transparent") + '">▼</span>';
  }

  function subTh(label, extra, background, color, drag, sort) {
    return '<th title="' + escapeHtml(label) +
      (sort ? (sort.active ? " · сортировка по убыванию" : " · нажмите, чтобы отсортировать") : "") +
      '" draggable="true"' +
      (drag ? ' data-col-drag="' + escapeHtml(drag.group) + '" data-col-label="' +
        escapeHtml(label) + '"' : "") +
      (sort ? ' data-sort-label="' + escapeHtml(label) + '"' +
        ' aria-sort="' + (sort.active ? "descending" : "none") + '"' : "") +
      ' style="position:sticky;top:41px;z-index:4;cursor:grab;background:' + (background || "#FBF9F9") +
      ';text-align:right;padding:9px 12px;border-bottom:1px solid #E8E2E2;font-size:11px;' +
      'font-weight:700;color:' + (sort && sort.active ? "#B91414" : (color || "#857D7D")) +
      ';font-family:Inter;white-space:nowrap;' + (extra || "") + '">' +
      escapeHtml(label) + (sort ? sortCaret(sort.active) : "") + "</th>";
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
      (opts.attrs || "") +
      ' class="cs-cell' + (opts.cls || "") +
      '" style="text-align:right;padding:12px;font-family:Inter;font-size:12.5px;' +
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
      background: "#E7EFE8", subBackground: "#F4F8F4",
      cellBackground: "#FAFCFA", rootBackground: "#EDF3EE",
      color: "#25835E", border: "#D6E2D8", divider: "#A8C0AE"
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

  /* Знак — цветами своих блоков: плюс как заголовки «Результата», минус как
     «Затраты». Чистые #16B57F и #FF0000 выбивались из палитры таблицы и тянули
     взгляд сильнее самих цифр. */
  function signTone(value) {
    return value >= 0 ? "#25835E" : "#C3536E";
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
      ".cs-table--dividers tbody td + td{border-left:1px solid #EFE9E9}" +
      // Итоговая строка не сворачивается вместе с ветками и держится внизу,
      // пока таблицу листают.
      ".cs-total{position:sticky;bottom:0;z-index:3}" +
      ".cs-total > td{background:#F8F5F5;border-top:2px solid #E8E2E2;" +
      "font:800 12.5px Inter,sans-serif;color:#070505}" +
      // Расход дня правится по клику. Никакой рамки: ячейка стоит в ряду
      // чисел, и обводка под курсором делала из неё поле ввода. Что строка
      // кликабельна, видно по курсору и подсказке.
      ".cs-cell--day{cursor:pointer}";
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
      leaves: {},
      leavesOpen: {},
      structure: null,
      // Пришли ли строки разрезанными по дням — от этого зависит, хватит ли
      // их для той структуры, которую человек только что собрал.
      groupsByDate: false,
      display: defaultDisplay(),
      collapsed: {},
      canManage: false,
      lastKeitaroRefreshAt: null
    };

    var p = config.prefix; // "media" | "finance"

    function el(name) { return byId(p + name); }

    function defaultStructure() {
      return { items: [
        { key: "buyer", on: true },
        { key: "tier", on: false },
        { key: "geo", on: true },
        { key: "partner", on: true },
        { key: "offer", on: true },
        { key: "date", on: false },
        { key: "agent", on: false }
      ] };
    }

    /* Уровни, добавленные после того, как человек сохранил свой порядок,
       дописываются в конец выключенными: иначе новый уровень увидели бы только
       те, кто структуру ни разу не трогал. */
    function withNewLevels(preference) {
      var known = {};
      preference.items.forEach(function (item) { known[item.key] = true; });
      var items = preference.items.filter(function (item) { return LEVELS[item.key]; });
      defaultStructure().items.forEach(function (item) {
        if (!known[item.key]) items.push({ key: item.key, on: false });
      });
      return { items: items };
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

    /* Фильтры живут дольше визита: раздел открывают по десять раз на дню, и
       каждый раз заново выставлять период и баера — работа впустую. Хранятся
       там же, где структура и вид, — в настройках пользователя, поэтому
       переносятся на другую машину вместе с ним. */
    var filtersRestored = false;

    async function restoreFilters() {
      if (!config.filtersPreferenceKey) return;
      var saved = null;
      try {
        var stored = await api.get("/me/preferences/" + config.filtersPreferenceKey);
        // Ненайденная настройка приходит пустым объектом, а не null: пустой
        // здесь означает «человек ещё ничего не выбирал».
        var value = stored && stored.value;
        saved = value && Object.keys(value).length ? value : null;
      } catch (error) {
        // Не сохранились настройки — не повод не открыть раздел.
        saved = null;
      }
      var from = el("FilterDateFrom");
      var to = el("FilterDateTo");
      if (saved) {
        // Пустые даты у сохранённых настроек — это осознанное «за всё время»,
        // и подставлять поверх них сегодняшний день нельзя.
        if (from) from.value = saved.date_from || "";
        if (to) to.value = saved.date_to || "";
        LIST_FILTERS.forEach(function (item) {
          var values = saved[item.param];
          if (values && values.length && filters[item.id]) {
            filters[item.id].setValues(values);
          }
        });
      } else {
        // Первый заход: показываем текущий день, а не всю историю доски.
        var today = window.CelestialTime.todayISO();
        if (from) from.value = today;
        if (to) to.value = today;
      }
      // Плашка периода читает скрытые поля и сама о правке не узнает.
      if (window.CelestialDateRange) window.CelestialDateRange.refresh();
      filtersRestored = true;
    }

    function persistFilters() {
      if (!config.filtersPreferenceKey || !filtersRestored) return;
      var from = el("FilterDateFrom");
      var to = el("FilterDateTo");
      var value = {
        date_from: from ? from.value : "",
        date_to: to ? to.value : ""
      };
      LIST_FILTERS.forEach(function (item) {
        value[item.param] = filterValues(item.id);
      });
      // Молча: настройка вспомогательная, а ругаться тостом на каждый щелчок
      // по плашке — шум поверх работы.
      api.put("/me/preferences/" + config.filtersPreferenceKey, { value: value })
        .catch(function () { /* в следующий раз откроется с прежними */ });
    }

    // Плашки снимают и добавляют пачками, по одному клику на значение. Без
    // паузы каждый клик уходил бы отдельным запросом за той же таблицей.
    var reloadTimer = null;

    function reloadSoon() {
      if (reloadTimer) window.clearTimeout(reloadTimer);
      reloadTimer = window.setTimeout(function () {
        reloadTimer = null;
        persistFilters();
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
        if (!reloadIfByDateChanged()) renderTable();
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
      // Свёртки адресуются теми же путями: после смены структуры они указывают
      // в никуда, и держать их — значит однажды свернуть чужую ветку.
      state.collapsed = {};
      persistFolds();
    }

    /* Свёрнутые ветки переживают уход из раздела: человек сворачивает доску
       под себя, и разворачивать её заново на каждом заходе — работа впустую.
       Хранятся списком путей: словарь с `false` рос бы от каждого щелчка. */
    var foldsTimer = null;

    function persistFolds() {
      if (!config.foldsPreferenceKey) return;
      if (foldsTimer) window.clearTimeout(foldsTimer);
      foldsTimer = window.setTimeout(function () {
        foldsTimer = null;
        var paths = Object.keys(state.collapsed).filter(function (path) {
          return state.collapsed[path];
        });
        // Молча: свёртка — вспомогательная настройка, а тост на каждый щелчок
        // по стрелке был бы шумом поверх работы.
        api.put("/me/preferences/" + config.foldsPreferenceKey, { value: { paths: paths } })
          .catch(function () { /* в следующий раз откроется развёрнутой */ });
      }, 400);
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
      if (!reloadIfByDateChanged()) renderTable();
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

    /* Выгрузка идёт с сервера, а не собирается в браузере: числа те же, что в
       таблице, а Excel-файл руками из JS не собрать без сторонней библиотеки. */
    function bindExport() {
      var button = el("Export");
      var menu = el("ExportMenu");
      if (!button || !menu || button.dataset.boardBound) return;
      button.dataset.boardBound = "1";
      button.addEventListener("click", function (event) {
        event.stopPropagation();
        menu.hidden = !menu.hidden;
      });
      menu.addEventListener("click", function (event) {
        var pick = event.target.closest("[data-export]");
        if (!pick) return;
        menu.hidden = true;
        var params = filterParams();
        params.format = pick.getAttribute("data-export");
        // Выгрузка повторяет структуру доски: те же уровни в том же порядке.
        params.levels = activeLevels().join(",");
        // Скачивание — переход по адресу: сессия уезжает cookie, и лишний
        // запрос через fetch с ручным Blob здесь ничего не добавляет.
        window.location.href = config.exportEndpoint + "?" + queryString(params);
      });
      document.addEventListener("click", function (event) {
        if (menu.hidden) return;
        if (!event.target.closest(".board-export")) menu.hidden = true;
      });
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
      // Новая колонка в сохранённом порядке не значится. Её ставим туда, где
      // она объявлена, а не в конец блока: INST2DEP нужен сразу за INST, и
      // уезжать в хвост только потому, что человек однажды переставил колонки,
      // он не должен.
      columns.forEach(function (column, index) {
        if (result.indexOf(column) < 0) {
          result.splice(Math.min(index, result.length), 0, column);
        }
      });
      return result;
    }

    function saveColumnOrder(groupKey, labels) {
      state.display.columnOrder = state.display.columnOrder || {};
      state.display.columnOrder[groupKey] = labels;
      persistDisplay();
    }

    /* Свёрнута ли доска целиком: считаем по верхнему уровню — он и есть то,
       что человек видит, когда всё закрыто. */
    function allFolded() {
      if (!currentTree || !currentTree.order.length) return false;
      return currentTree.order.every(function (key) { return state.collapsed[key]; });
    }

    function foldPaths(node, path, paths) {
      node.order.forEach(function (key) {
        var childPath = nodePath(path, key);
        paths.push(childPath);
        foldPaths(node.children[key], childPath, paths);
      });
    }

    function toggleFoldAll() {
      if (!currentTree) return;
      if (allFolded()) {
        state.collapsed = {};
      } else {
        var paths = [];
        currentTree.order.forEach(function (key) {
          paths.push(key);
          foldPaths(currentTree.children[key], key, paths);
        });
        state.collapsed = {};
        paths.forEach(function (item) { state.collapsed[item] = true; });
        // Списки записей тоже закрываются: иначе раскрытый день остался бы
        // висеть под свёрнутым баером.
        Object.keys(state.leavesOpen).forEach(function (item) {
          state.leavesOpen[item] = false;
        });
      }
      renderHead();
      renderTable();
      persistFolds();
    }

    /* ----- сортировка по колонке -----

       Клик по названию числовой колонки выстраивает строки от большего к
       меньшему: и группы внутри своего уровня, и записи внутри группы.
       Повторный клик по той же колонке возвращает порядок доски. */
    var sortLabel = null;

    function sortColumn() {
      if (!sortLabel) return null;
      var found = null;
      layout.forEach(function (group) {
        group.columns.forEach(function (column) {
          // Сортируем только по вычислимым колонкам: у сервисов и агентов
          // значение лежит в своей корзине и к строке-группе не приводится.
          if (!found && column.label === sortLabel && column.get) found = column;
        });
      });
      return found;
    }

    function sortValue(column, sums) {
      var value = column.get(sums || {});
      return value == null || !isFinite(Number(value)) ? null : Number(value);
    }

    // Пустое значение всегда внизу: прочерк — это «нет данных», а не ноль.
    function byValueDesc(left, right) {
      if (left == null && right == null) return 0;
      if (left == null) return 1;
      if (right == null) return -1;
      return right - left;
    }

    function sortedOrder(node) {
      var column = sortColumn();
      if (!column) return node.order;
      return node.order.slice().sort(function (leftKey, rightKey) {
        return byValueDesc(
          sortValue(column, node.children[leftKey].aggregate.sums),
          sortValue(column, node.children[rightKey].aggregate.sums)
        );
      });
    }

    function recordSums(record) {
      var sums = {};
      config.sumFields.forEach(function (field) {
        sums[field] = record[field] == null ? null : Number(record[field]);
      });
      return sums;
    }

    function sortedRecords(items) {
      var column = sortColumn();
      if (!column) return items;
      return items.slice().sort(function (left, right) {
        return byValueDesc(
          sortValue(column, recordSums(left)),
          sortValue(column, recordSums(right))
        );
      });
    }

    function toggleSort(label) {
      sortLabel = sortLabel === label ? null : label;
      renderHead();
      renderTable();
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
      // Стрелка у подписи сворачивает и разворачивает доску целиком: иначе
      // свернуть десяток баеров можно только по одному.
      var folded = allFolded();
      var row1 = "<tr>" +
        '<th rowspan="2" class="cs-head-structure" style="position:sticky;left:0;top:0;z-index:5;background:#F8F5F5;text-align:left;padding:14px 16px;border-bottom:1px solid #E8E2E2;border-right:1px solid #E8E2E2;font-size:11px;font-weight:700;color:#857D7D;text-transform:uppercase;letter-spacing:.6px">' +
        '<span style="display:flex;align-items:center;gap:9px">' +
        '<button type="button" data-fold-all aria-expanded="' + (folded ? "false" : "true") +
        '" title="' + (folded ? "Развернуть всё" : "Свернуть всё") +
        '" aria-label="' + (folded ? "Развернуть всё" : "Свернуть всё") +
        '" style="border:0;background:none;padding:0;line-height:0;cursor:pointer;color:#857D7D">' +
        '<svg class="cs-arrow" width="14" height="14" viewBox="0 0 24 24" fill="none"' +
        (folded ? ' style="transform:rotate(-90deg)"' : "") + '>' +
        '<path d="m6 9 6 6 6-6" stroke="currentColor" stroke-width="2.4" ' +
        'stroke-linecap="round" stroke-linejoin="round"/></svg></button>Структура</span></th>' +
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
              { group: group.key },
              column.get ? { active: column.label === sortLabel } : null
            );
          }).join("");
        }).join("") +
        "</tr>";
      head.innerHTML = row1 + row2;
      bindHeadDrag(head);
      bindHeadSort(head);
    }

    /* Клик и перетаскивание живут на одном заголовке: клик считается только
       тогда, когда мышь не уехала — иначе каждая попытка переставить колонку
       заодно меняла бы сортировку. */
    function bindHeadSort(head) {
      if (head.getAttribute("data-sort-bound") === "1") return;
      head.setAttribute("data-sort-bound", "1");
      var start = null;
      head.addEventListener("mousedown", function (event) {
        start = { x: event.clientX, y: event.clientY };
      });
      head.addEventListener("click", function (event) {
        if (event.target.closest && event.target.closest("[data-fold-all]")) {
          start = null;
          return toggleFoldAll();
        }
        var cell = event.target.closest
          ? event.target.closest("[data-sort-label]")
          : null;
        if (!cell) return;
        var moved = start &&
          (Math.abs(event.clientX - start.x) > 4 || Math.abs(event.clientY - start.y) > 4);
        start = null;
        if (moved) return;
        toggleSort(cell.getAttribute("data-sort-label"));
      });
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

    /* Уровень «Агент»: строка «баер × оффер» раскладывается на строку каждого
       агента с его спендом и строку «Без агента» — с воронкой, доходом и
       спендом, внесённым не через агентов. Суммы выше по дереву от этого не
       меняются: строки агентов несут только спенд. */
    function rowsByAgent(groups) {
      if (activeLevels().indexOf("agent") < 0) return groups;
      var names = {};
      state.providers.forEach(function (provider) { names[provider.id] = provider.name; });
      var rows = [];
      groups.forEach(function (row) {
        var providers = row.providers || {};
        var attributed = 0;
        Object.keys(providers).forEach(function (id) {
          var amount = Number((providers[id] || {}).amount || 0);
          attributed += amount;
          var own = {};
          own[id] = providers[id];
          rows.push(Object.assign({}, row, {
            agent_key: id, agent_name: names[id] || "Агент",
            spend: amount, installs: 0, registrations: 0, ftd: 0, revenue: 0,
            rent: 0, records: 0, services: {}, providers: own
          }));
        });
        rows.push(Object.assign({}, row, {
          agent_key: "none", agent_name: "Без агента",
          spend: Number(row.spend || 0) - attributed, providers: {}
        }));
      });
      return rows;
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
          // Под агентом воронка и доход не делятся: они относятся к офферу,
          // и цифра у агента была бы выдумкой.
          var blanked = opts.agentScoped &&
            (column.group === "funnel" || column.group === "result");
          var formatted = blanked
            ? { text: "—", title: "Не делится по агентам — относится к офферу" }
            : formatCell(column.kind, value, compact);
          var numeric = blanked || value == null ? null : Number(value);
          /* Расход на строке дня правится прямо в таблице: баер вводит день
             целиком, а сервер раскладывает сумму по офферам этого дня. */
          var editable = !blanked && opts.daySpend && column.editKey === "spend";
          cells += td(formatted.text, {
            bold: opts.bold || column.bold,
            title: editable
              ? opts.daySpend.span
                ? "Расход тира за день — откроется окно, сумма разделится по офферам " +
                  TIER_NAMES[opts.daySpend.tier]
                : "Расход за день — откроется окно, сумма разделится по офферам" +
                  (opts.daySpend.tier ? " " + TIER_NAMES[opts.daySpend.tier] : "")
              : formatted.title,
            color: column.tone && numeric != null ? column.tone(numeric) : undefined,
            attrs: editable
              ? ' data-day-spend="' + escapeHtml(opts.daySpend.buyer) +
                '" data-day-date="' + escapeHtml(opts.daySpend.date) +
                '" data-day-tier="' + escapeHtml(opts.daySpend.tier || "") + '"' +
                (opts.daySpend.span ? ' data-day-span="1"' : "")
              : "",
            // Ячейка дня остаётся обычной ячейкой: рамка появляется только под
            // курсором. Постоянная обводка делала из неё поле ввода, которое
            // и по размеру, и по виду выбивалось из ряда соседних чисел.
            cls: editable ? " cs-cell--day" : "",
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

    /* День одного баера — единственная строка, в которую можно вписать расход:
       сумма делится между офферами этого дня, а значит день должен быть один,
       баер один, и оффер ещё не выбран. Строка «Баер → Оффер → Дата» под это
       не подходит: там расход относится к одному офферу, а не ко дню. */
    function daySpendScope(node) {
      if (!config.dayLevelSpend || !state.canManage) return null;
      var scope = node.scope || {};
      // Под агентом строка показывает долю одного агента — вписывать туда
      // расход всего дня нельзя.
      if (scope.agent !== undefined) return null;
      /* Строка тира баера — расход тира за один день. Строка объединяет весь
         период фильтра, поэтому день выбирают в окне календарём (стоит день
         выше по дереву — он и подставится). Оффер выше тира делает строку
         расходом одного оффера, а «Без тира» отнести не к чему. */
      if (node.level === "tier") {
        if (!scope.buyer || scope.offer) return null;
        if (scope.tier !== "T1" && scope.tier !== "T23") return null;
        return {
          buyer: String(scope.buyer),
          date: scope.date ? String(scope.date) : "",
          tier: String(scope.tier),
          span: true
        };
      }
      if (node.level !== "date") return null;
      if (!scope.buyer || scope.offer || !scope.date) return null;
      /* Тир из ветки едет вместе с днём: в финансах у баера книга на каждый
         тир, и расход дня, размазанный по офферам обоих, приезжал бы туда не
         тем, чем был. День внутри ветки «Tier1» правит только её офферы. */
      return {
        buyer: String(scope.buyer),
        date: String(scope.date),
        tier: scope.tier ? String(scope.tier) : ""
      };
    }

    /* Клик по расходу на строке дня открывает то же окно, что и кнопка
       «Изменить данные», но с закреплённой шапкой: день и баер берутся из
       строки, оффер — «весь день». Правка прямо в ячейке этого не давала:
       агенты у расхода свои, а в ячейку помещается только итог.

       Суммы агентов за день собираем из записей этого дня: раскладка ровная,
       поэтому сумма долей и есть то, что человек вводил. */
    async function openDaySpend(cell) {
      if (cell.getAttribute("data-day-span")) return openTierSpend(cell);
      var buyerId = cell.getAttribute("data-day-spend");
      var day = cell.getAttribute("data-day-date");
      var tier = cell.getAttribute("data-day-tier") || "";
      var buyer = state.buyers.filter(function (row) { return row.id === buyerId; })[0];
      var loaded = await dayAgents(buyerId, day, tier);
      openEditModal(null, {
        record_date: day,
        buyer_id: buyerId,
        buyer: buyer ? buyer.name : "",
        offer_id: "",
        tier: tier,
        // Строка дня внутри ветки тира правит расход только этого тира.
        offer: tier ? "Весь день · " + TIER_NAMES[tier] : "",
        providers: loaded.providers
      });
    }

    /* Суммы агентов за день — из записей этого дня: раскладка ровная, поэтому
       сумма долей и есть то, что человек вводил. `offers` — сколько записей
       нашлось: ноль значит, что делить расход в этот день не по чему. */
    async function dayAgents(buyerId, day, tier) {
      var providers = {};
      var offers = 0;
      try {
        var page = await api.get("/media-records?limit=1000&date_from=" +
          encodeURIComponent(day) + "&date_to=" + encodeURIComponent(day) +
          "&buyer_id=" + encodeURIComponent(buyerId));
        /* Складываем доли без промежуточного округления и округляем один раз в
           конце. День делится между офферами до сотых долей цента, и копейка,
           отброшенная на каждой из трёх десятков записей, превращала введённые
           1200 в 1199.86 — и сумма уползала при каждом открытии окна. */
        (page.items || []).forEach(function (item) {
          // Записи чужого тира к этому окну не относятся: их суммы правят из
          // строки своей ветки.
          if (tier && item.tier !== tier) return;
          offers += 1;
          Object.keys(item.providers || {}).forEach(function (providerId) {
            var base = Number((item.providers[providerId] || {}).base_amount || 0);
            if (!isFinite(base) || base <= 0) return;
            var known = providers[providerId] || { base_amount: 0 };
            known.base_amount = Number(known.base_amount) + base;
            providers[providerId] = known;
          });
        });
        Object.keys(providers).forEach(function (providerId) {
          providers[providerId].base_amount = roundMoney(providers[providerId].base_amount);
        });
        return { providers: providers, offers: offers };
      } catch (error) {
        // Не смогли поднять текущие суммы — окно всё равно откроем пустым:
        // ввести день заново дешевле, чем разбираться, почему не открылось.
        return { providers: {}, offers: null };
      }
    }

    /* Клик по расходу на строке тира: то же окно, что у строки дня, но день
       выбирают календарём в самом окне. Строка тира объединяет весь период
       фильтра, а расход вносят ровно за один день — сервер делит его поровну
       между офферами этого тира в выбранный день. */
    async function openTierSpend(cell) {
      var buyerId = cell.getAttribute("data-day-spend");
      var tier = cell.getAttribute("data-day-tier");
      var day = cell.getAttribute("data-day-date") || tierDefaultDay();
      var buyer = state.buyers.filter(function (row) { return row.id === buyerId; })[0];
      var loaded = await dayAgents(buyerId, day, tier);
      openEditModal(null, {
        record_date: day,
        pickDate: true,
        dayOffers: loaded.offers,
        buyer_id: buyerId,
        buyer: buyer ? buyer.name : "",
        offer_id: "",
        tier: tier,
        offer: "Все офферы · " + TIER_NAMES[tier],
        providers: loaded.providers
      });
    }

    /* День по умолчанию — последний день периода в фильтре, но не позже
       сегодняшнего: расход вносят за вчера или за сегодня, а не за будущее. */
    function tierDefaultDay() {
      var period = filterParams();
      var today = window.CelestialTime.todayISO();
      if (period.date_to && period.date_to < today) return period.date_to;
      if (period.date_from && period.date_from > today) return period.date_from;
      return today;
    }

    function dayHintText(offers, tier) {
      return offers === 0
        ? "В этот день у " + (TIER_NAMES[tier] || "баера") + " нет офферов — делить расход не по чему"
        : "";
    }

    function paintDayHint(block, offers, tier) {
      var hint = block.querySelector("[data-day-hint]");
      if (!hint) return;
      hint.textContent = dayHintText(offers, tier);
      hint.hidden = !hint.textContent;
    }

    /* Сменили день в окне тира — суммы агентов подтягиваются за новый день.
       Иначе в него молча записались бы суммы того дня, с которого окно
       открыли. Пока суммы грузятся, сохранить нельзя — по той же причине. */
    async function reloadDayAgents(block, day) {
      var token = String(Math.random());
      block.setAttribute("data-day-loading", token);
      var tier = block.getAttribute("data-locked-tier") || "";
      var loaded = day
        ? await dayAgents(block.getAttribute("data-locked-buyer"), day, tier)
        : { providers: {}, offers: null };
      if (block.getAttribute("data-day-loading") !== token || !block.isConnected) return;
      block.removeAttribute("data-day-loading");
      agentRows(block).forEach(function (row) { row.remove(); });
      fillAgentRows(block, { providers: loaded.providers });
      paintDayHint(block, loaded.offers, tier);
    }

    function renderNodeRows(node, depth, path, output) {
      // «Без агента» без спенда — служебная строка с воронкой: её цифры уже
      // видны уровнем выше, а пустой узел только путал бы.
      if (node.level === "agent" && node.key === "none" &&
        Math.abs(Number(node.aggregate.sums.spend || 0)) < 0.005) return;
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
        }, {
          bold: depth === 0, root: depth === 0, daySpend: daySpendScope(node),
          agentScoped: (node.scope || {}).agent !== undefined
        }) +
        (hasActionColumn() ? td("", {}) : "") +
        "</tr>");
      // Descendants are always emitted — collapsing hides them, so reopening a branch
      // never costs a rebuild. Only records that have not been fetched are missing.
      if (isLeafLevel) {
        renderLeafRows(node, depth + 1, path, output);
        return;
      }
      sortedOrder(node).forEach(function (key) {
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
      sortedRecords(leaf.items).forEach(function (record) {
        output.push(renderLeafRow(record, depth, path, (node.scope || {}).agent));
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

    function renderLeafRow(record, depth, owner, agentKey) {
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
      // Запись под агентом — это доля агента: спенд, внесённый через него.
      var agentScoped = agentKey !== undefined;
      if (agentScoped) {
        var shares = record.providers || {};
        var attributed = 0;
        Object.keys(shares).forEach(function (id) {
          attributed += Number((shares[id] || {}).amount || 0);
        });
        sums.spend = agentKey === "none"
          ? Number(record.spend || 0) - attributed
          : Number((shares[agentKey] || {}).amount || 0);
      }
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
        }, { bold: false, leaf: true, record: record, agentScoped: agentScoped }) +
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
      currentTree = groupRecords(rowsByAgent(state.groups), activeLevels());
      var output = [];
      sortedOrder(currentTree).forEach(function (key) {
        renderNodeRows(currentTree.children[key], 0, key, output);
      });
      output.push(totalRow(currentTree.aggregate));
      body.innerHTML = output.join("");
      applyVisibility();
      syncFoldAllArrow();
    }

    /* Итог по всему, что сейчас на доске. Считается по корню дерева, а не
       складыванием видимых строк: сумма зависит от фильтров и набора колонок,
       но не от того, какие ветки человек развернул. */
    function totalRow(aggregate) {
      return '<tr class="cs-total">' +
        '<td style="position:sticky;left:0;z-index:2;padding:12px 16px;' +
        'border-top:2px solid #E8E2E2;border-right:1px solid #E8E2E2">Общая</td>' +
        metricCells(aggregate, { bold: true, total: true }) +
        (hasActionColumn()
          ? '<td style="border-top:2px solid #E8E2E2"></td>'
          : "") + "</tr>";
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
        var daySpendCell = event.target.closest("[data-day-spend]");
        if (daySpendCell) {
          event.stopPropagation();
          openDaySpend(daySpendCell).catch(fail);
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
        syncFoldAllArrow();
        persistFolds();
      });
    }

    /* Стрелку в шапке двигает не только она сама: свернули последнюю ветку
       руками — она тоже должна показать «всё свёрнуто». */
    function syncFoldAllArrow() {
      var head = el("TableHead");
      var button = head ? head.querySelector("[data-fold-all]") : null;
      if (!button) return;
      var folded = allFolded();
      var icon = button.querySelector("svg");
      if (icon) icon.style.transform = folded ? "rotate(-90deg)" : "";
      button.setAttribute("aria-expanded", folded ? "false" : "true");
      button.setAttribute("title", folded ? "Развернуть всё" : "Свернуть всё");
      button.setAttribute("aria-label", folded ? "Развернуть всё" : "Свернуть всё");
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
        api.get("/me/preferences/" + config.displayPreferenceKey),
        // Свёрнутые ветки нужны только к отрисовке, поэтому едут в общей пачке
        // со справочниками, а не отдельным походом перед запросом строк.
        config.foldsPreferenceKey
          ? api.get("/me/preferences/" + config.foldsPreferenceKey)
          : Promise.resolve(null)
      ]);
      state.services = (results[0].items || []).filter(function (s) { return s.status === "active"; });
      state.providers = (results[1].items || []).filter(function (s) { return s.status === "active"; });
      state.buyers = results[2] || [];
      state.offers = results[3].items || [];
      state.partners = (results[4].items || []);
      var preference = results[5] && results[5].value;
      state.structure = preference && Array.isArray(preference.items) && preference.items.length
        ? withNewLevels(preference) : defaultStructure();
      var display = (results[6] && results[6].value) || {};
      // Merged rather than replaced, so a preference saved before a new switch existed
      // still gets that switch's default instead of `undefined`.
      state.display = Object.assign(defaultDisplay(), display, {
        groups: Object.assign(defaultDisplay().groups, display.groups || {})
      });
      var folds = results[7] && results[7].value;
      state.collapsed = folds && folds.paths
        ? folds.paths.reduce(function (map, path) { map[path] = true; return map; }, {})
        : {};
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
      // Если у человека нет группы и назначений, показывать ему весь справочник
      // нельзя: именно так администратору случайно подвязали чужой оффер.
      return scoped;
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
      var params = filterParams();
      if (byDateWanted()) params.by_date = "true";
      var query = queryString(params);
      return api.get(config.groupsEndpoint + (query ? "?" + query : ""));
    }

    /* Разбивку по дням сервер отдаёт только по просьбе: это единственный
       уровень, ради которого строки приходится перезапрашивать. Пока структура
       не загружена (первый запрос уходит параллельно со справочниками), дней не
       просим — за них отвечает loadRecords, когда структура станет известна. */
    function byDateWanted() {
      return !!state.structure &&
        activeLevels().some(function (key) { return LEVELS[key].byDate; });
    }

    /* Уровень «Дата» включили или выключили — прежние строки для новой
       структуры не годятся, нужен новый запрос. */
    function reloadIfByDateChanged() {
      if (byDateWanted() === state.groupsByDate) return false;
      loadRecords().catch(fail);
      return true;
    }

    async function loadRecords(pending) {
      // On the first load the request is already in flight next to the references —
      // waiting for those first would cost another round trip on every navigation.
      var wanted = byDateWanted();
      // Запрос, ушедший до загрузки структуры, ничего не знает про дни: если
      // они в структуре есть, его ответ не годится и нужен новый.
      if (pending && wanted) pending.catch(function () { /* заменён */ });
      var data = await (pending && !wanted ? pending : fetchGroups());
      state.groupsByDate = wanted;
      state.groups = data.groups || [];
      state.recordCount = data.record_count || 0;
      state.leaves = {};
      renderTable();
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
        var level = LEVELS[levelKey];
        var value = node.scope[levelKey];
        if (value == null || value === "") {
          unfiltered.push(levelKey);
        } else if (level.narrow) {
          level.narrow(params, value);
        } else if (level.filter) {
          params[level.filter] = value;
        } else {
          unfiltered.push(levelKey);
        }
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
    /* Суммы приходят из float-арифметики (раскладка дня по офферам, сложение
       долей) и выглядят как 999.9999999999993. До копеек — на границе
       сохранения и показа: данные точнее копейки не содержат. */
    function roundMoney(value) {
      var amount = Number(value);
      if (!isFinite(amount)) return amount;
      return Math.round((amount + Number.EPSILON) * 100) / 100;
    }

    /* Сумма из базы в поле ввода: хвостовые нули убираем, а значащие знаки
       оставляем. Доля дня приходит с четырьмя знаками (35.2941), и округление
       её до копейки теряло бы центы на каждом пересохранении. */
    function exactMoney(value) {
      var amount = Number(value);
      if (!isFinite(amount)) return "";
      return String(Math.round((amount + Number.EPSILON) * 10000) / 10000);
    }

    function agentLabel(provider) {
      var percent = Number(provider.commission_pct || 0);
      if (!isFinite(percent) || percent <= 0) return provider.name;
      // 7 вместо 7.0000, но 7.5 остаётся 7.5.
      var shown = String(Number(percent.toFixed(2)));
      return provider.name + " (" + shown + "%)";
    }

    function agentRows(block) {
      var host = block.querySelector("[data-agent-host]");
      return host
        ? Array.prototype.slice.call(host.querySelectorAll("[data-agent-row]"))
        : [];
    }

    function agentPick(row) {
      return row.querySelector("[data-agent-select]");
    }

    function takenProviders(block, except) {
      return agentRows(block).filter(function (row) { return row !== except; })
        .map(function (row) { return agentPick(row).value; });
    }

    /* Один агент в записи может быть только один раз, поэтому занятые варианты
     * гасим прямо в списке — так видно, что агент уже добавлен строкой выше. */
    /* Подсказка под суммой: сколько это будет с комиссией агента. Доска
       показывает сумму с комиссией, поле вводит до неё — без подсказки люди
       вводили одно вместо другого, и цифры росли на каждом пересохранении. */
    function agentHint(row) {
      var hint = row.querySelector("[data-agent-hint]");
      if (!hint) return;
      var provider = state.providers.filter(function (item) {
        return item.id === agentPick(row).value;
      })[0];
      var percent = Number((provider || {}).commission_pct || 0);
      var amount = Number((row.querySelector("[data-agent-amount]") || {}).value);
      if (!isFinite(amount) || amount <= 0) {
        hint.textContent = "";
        return;
      }
      // Коротко: процент агента и так стоит в его названии в списке рядом.
      var total = amount * (percent / 100 + 1);
      hint.textContent = "в SPEND: $" + total.toFixed(2);
    }

    function refreshAgentRows(block) {
      var rows = agentRows(block);
      rows.forEach(function (row) {
        var taken = takenProviders(block, row);
        Array.prototype.forEach.call(agentPick(row).options, function (option) {
          option.disabled = taken.indexOf(option.value) >= 0;
        });
        agentHint(row);
      });
      var button = block.querySelector("[data-agent-add]");
      if (button) button.disabled = rows.length >= state.providers.length;
      var empty = block.querySelector("[data-agent-empty]");
      if (empty) empty.hidden = rows.length > 0;
    }

    function addAgentRow(block, providerId, amount) {
      var host = block.querySelector("[data-agent-host]");
      if (!host) return null;
      var taken = takenProviders(block, null);
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
        'aria-label="Убрать агента">' + AGENT_DROP_SVG + "</button>" +
        /* Подсказка стоит отдельной строкой под полем, а не внутри его подписи:
           внутри она растягивала колонку, и поле уезжало вверх относительно
           списка агентов. */
        '<span class="board-edit-agent-hint" data-agent-hint></span>';
      host.appendChild(row);
      refreshAgentRows(block);
      return row;
    }

    /* Суммы агентов записи — строками в её блоке. Порядок берём из справочника,
       а не из записи: так строки не прыгают между открытиями одной и той же
       записи. */
    function fillAgentRows(block, record) {
      state.providers.forEach(function (provider) {
        var value = record && record.providers && record.providers[provider.id];
        if (!value) return;
        var amount = Number(value.base_amount);
        if (!isFinite(amount) || amount <= 0) return;
        addAgentRow(block, provider.id, exactMoney(amount));
      });
      refreshAgentRows(block);
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

    /* Блок ввода: день, баер, оффер и агенты этого дня. Блоков может быть
       несколько — байер закрывает несколько дней или несколько офферов за один
       заход, и открывать окно на каждую строку значило бы вводить одно и то же
       по кругу.

       Пустой оффер — это «весь день»: сумма ложится на день целиком и
       раскладывается сервером поровну по офферам этого дня. */
    function modalOfferOptions(buyerId, selectedOfferId) {
      var selected = selectedOfferId || "";
      var dayOptions = config.dayLevelSpend
        ? '<option value=""' + (!selected ? " selected" : "") +
          ">Весь день · все офферы</option>" +
          '<option value="tier:T1"' + (selected === "tier:T1" ? " selected" : "") +
          ">Весь день · Tier1</option>" +
          '<option value="tier:T23"' + (selected === "tier:T23" ? " selected" : "") +
          ">Весь день · Tier2/3</option>"
        : "";
      return dayOptions + offersForBuyers(buyerId ? [buyerId] : []).map(function (offer) {
        return '<option value="' + escapeHtml(offer.id) + '"' +
          (selected === offer.id ? " selected" : "") + ">" +
          escapeHtml(offer.name) + "</option>";
      }).join("");
    }

    function blockHtml(index, record, removable, locked) {
      var buyersOptions = state.buyers.map(function (buyer) {
        return '<option value="' + escapeHtml(buyer.id) + '"' +
          (record && record.buyer_id === buyer.id ? " selected" : "") + ">" +
          escapeHtml(buyer.name) + "</option>";
      }).join("");
      var selectedBuyerId = record && record.buyer_id ||
        (state.buyers.length ? state.buyers[0].id : "");
      var offersOptions = modalOfferOptions(
        selectedBuyerId,
        record && record.offer_id
      );
      var servicesInputs = state.services.map(function (service) {
        var value = record && record.services && record.services[service.id];
        return modalInput(service.name, p + "EditService_" + service.id + "_" + index,
          "number", value ? Number(value.quantity) : "", 'step="any" min="0"');
      }).join("");
      /* День можно отнести к тиру: в финансах у баера книга на каждый тир, и
         расход, размазанный по офферам обоих, приезжает туда не тем, чем был.
         «Все офферы» остаётся для дней, которые и правда были общим котлом. */
      /* Окно, открытое из таблицы, правит одну конкретную клетку: день, баер и
         оффер в нём уже выбраны самой строкой. Показываем их полями только для
         чтения — так видно, куда уйдёт сумма, и нельзя случайно переписать
         соседний день. */
      if (locked) {
        return '<section class="board-edit-block" data-block="' + index + '" ' +
          'data-locked-date="' + escapeHtml(locked.record_date) + '" ' +
          (locked.pickDate ? "data-pick-date " : "") +
          'data-locked-buyer="' + escapeHtml(locked.buyer_id) + '" ' +
          'data-locked-offer="' + escapeHtml(locked.offer_id || "") + '" ' +
          'data-locked-tier="' + escapeHtml(locked.tier || "") + '">' +
          '<div class="board-edit-context"><div class="board-edit-locked">' +
          // Окно тира: день выбирают календарём, баер и офферы — из строки.
          (locked.pickDate
            ? modalInput("Дата", p + "EditDate_" + index, "date", locked.record_date, "data-pick-day")
            : lockedField("Дата", dayLabel(locked.record_date))) +
          lockedField("Баер", locked.buyer) +
          lockedField("Оффер", locked.offer || "Весь день · все офферы") +
          "</div>" +
          (locked.pickDate
            ? '<div class="board-edit-day-hint" data-day-hint' +
              (dayHintText(locked.dayOffers, locked.tier) ? "" : " hidden") + ">" +
              escapeHtml(dayHintText(locked.dayOffers, locked.tier)) + "</div>"
            : "") +
          "</div>" +
          (state.providers.length
            ? '<section class="board-edit-section board-edit-section--providers">' +
              '<div class="board-edit-agents" data-agent-host></div>' +
              '<div class="board-edit-agents-empty" data-agent-empty>' +
              "Ни одного агента ещё не добавлено</div>" +
              '<button type="button" class="board-edit-agent-add" data-agent-add>' +
              '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
              '<path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2.4" ' +
              'stroke-linecap="round"/></svg>Добавить агента</button>' +
              "</section>"
            : "") +
          "</section>";
      }
      return '<section class="board-edit-block" data-block="' + index + '">' +
        (removable
          ? '<div class="board-edit-block__head"><span>Блок ' + (index + 1) + "</span>" +
            '<button type="button" class="board-edit-block__drop" data-block-drop>' +
            "Убрать</button></div>"
          : "") +
        '<div class="board-edit-context">' +
        '<div class="board-edit-main-grid" style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:16px">' +
        modalInput("Дата", p + "EditDate_" + index, "date",
          record ? record.record_date : window.CelestialTime.todayISO()) +
        '<label class="board-edit-field" for="' + p + "EditBuyer_" + index +
        '" style="display:flex;flex-direction:column;gap:5px;font-size:11.5px;font-weight:700;color:#6A6161">Баер' +
        '<select class="board-edit-select" data-block-buyer id="' + p + "EditBuyer_" + index +
        '" style="border:1px solid #EBE6E6;border-radius:9px;padding:9px 11px;font:600 13px Inter,sans-serif;outline:none">' +
        buyersOptions + "</select></label>" +
        '<label class="board-edit-field board-edit-offer-field" for="' + p + "EditOffer_" + index +
        '" style="display:flex;flex-direction:column;gap:5px;font-size:11.5px;font-weight:700;color:#6A6161">Оффер' +
        '<select class="board-edit-select" data-block-offer id="' + p + "EditOffer_" + index +
        '" style="border:1px solid #EBE6E6;border-radius:9px;padding:9px 11px;font:600 13px Inter,sans-serif;outline:none;max-width:100%">' +
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
            '<div class="board-edit-agents" data-agent-host></div>' +
            '<div class="board-edit-agents-empty" data-agent-empty>' +
            "Ни одного агента ещё не добавлено</div>" +
            '<button type="button" class="board-edit-agent-add" data-agent-add>' +
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
          : "") +
        "</section>";
    }

    function lockedField(label, value) {
      return '<div class="board-edit-field board-edit-locked__item"><span>' +
        escapeHtml(label) + "</span><b>" + escapeHtml(value || "—") + "</b></div>";
    }

    function blocks() {
      var host = byId(p + "EditBlocks");
      return host
        ? Array.prototype.slice.call(host.querySelectorAll("[data-block]"))
        : [];
    }

    /* Номера блоков и доступность «Убрать» — после каждого добавления и
       удаления: единственный блок убирать нечем, а нумерация иначе разъезжается. */
    function refreshBlocks() {
      var rows = blocks();
      rows.forEach(function (block, index) {
        var head = block.querySelector(".board-edit-block__head span");
        if (head) head.textContent = "Блок " + (index + 1);
        var drop = block.querySelector("[data-block-drop]");
        if (drop) drop.hidden = rows.length < 2;
      });
    }

    function addBlock(record) {
      var host = byId(p + "EditBlocks");
      if (!host) return null;
      var wrapper = document.createElement("div");
      wrapper.innerHTML = blockHtml(blocks().length, record, true);
      var block = wrapper.firstChild;
      host.appendChild(block);
      fillAgentRows(block, record);
      refreshBlocks();
      return block;
    }

    function openEditModal(record, locked) {
      closeModal();
      if (p === "media") ensureMediaModalStyles();
      if (p === "finance") ensureFinanceModalStyles();
      var overlay = document.createElement("div");
      overlay.id = p + "EditModal";
      overlay.className = "board-edit-overlay board-edit-overlay--" + p;
      overlay.style.cssText =
        "position:fixed;inset:0;z-index:9999;background:rgba(18,12,12,.45);display:flex;" +
        "align-items:flex-start;justify-content:center;padding:40px 16px;overflow-y:auto";
      /* Несколько блоков — только когда окно открыли кнопкой «Изменить
         данные». У записи, открытой из таблицы, блок ровно один: правят
         конкретную клетку, а не заводят новые. */
      var multi = !record && !locked && config.multiBlock;
      overlay.innerHTML =
        '<div class="board-edit-card" role="dialog" aria-modal="true" tabindex="-1" aria-labelledby="' + p +
        'EditTitle" style="background:#fff;border-radius:18px;max-width:720px;width:100%;padding:26px 28px;box-shadow:0 24px 70px rgba(18,12,12,.3)">' +
        '<div class="board-edit-header" style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:18px;gap:18px">' +
        '<div><h2 class="board-edit-title" id="' + p +
        'EditTitle" style="font-family:\'Alumni Sans\',\'Inter\',sans-serif;font-size:19px;font-weight:700">' +
        (record || locked ? "Изменить данные" : "Добавить данные") + "</h2>" +
        '<span class="board-edit-subtitle" style="display:none">' +
        (p === "finance" ? "Финансовые показатели и распределение затрат" :
          "Ручные показатели и распределение затрат") +
        "</span></div>" +
        '<button class="board-edit-close" id="' + p +
        'EditClose" type="button" aria-label="Закрыть" style="border:none;background:#F7F4F4;border-radius:9px;width:32px;height:32px;cursor:pointer;font-size:15px;font-weight:700;color:#6A6161">' +
        '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>' +
        "</button></div>" +
        '<div class="board-edit-body" style="min-width:0">' +
        '<div class="board-edit-blocks" id="' + p + 'EditBlocks">' +
        blockHtml(0, record, multi, locked) + "</div>" +
        (multi
          ? '<button type="button" class="board-edit-block-add" id="' + p + 'EditBlockAdd">' +
            '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
            '<path d="M12 5v14M5 12h14" stroke="currentColor" stroke-width="2.4" ' +
            'stroke-linecap="round"/></svg>Добавить блок</button>'
          : "") + "</div>" +
        '<div class="board-edit-footer" style="display:flex;justify-content:flex-end;gap:10px;margin-top:22px">' +
        '<button class="board-edit-button board-edit-cancel" id="' + p +
        'EditCancel" type="button" style="height:40px;padding:0 16px;border:1px solid #EBE6E6;border-radius:10px;background:#fff;font:700 12px Inter,sans-serif;color:#6A6161;cursor:pointer">Отмена</button>' +
        '<button class="board-edit-button board-edit-save" id="' + p +
        'EditSave" type="button" style="height:40px;padding:0 16px;border:0;border-radius:10px;background:#B91414;color:#fff;font-family:Inter,-apple-system,Helvetica Neue,sans-serif;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.02em;cursor:pointer;box-shadow:0 8px 18px rgba(185,20,20,.24)">Сохранить</button>' +
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
        saveModal(record, locked).catch(fail);
      });
      bindBlocks();
      var add = byId(p + "EditBlockAdd");
      if (add) add.addEventListener("click", function () { addBlock(null); });
      var first = blocks()[0];
      if (first) fillAgentRows(first, locked ? { providers: locked.providers } : record);
      refreshBlocks();
      var dialog = overlay.querySelector(".board-edit-card");
      if (dialog) dialog.focus();
    }

    /* Один слушатель на все блоки: блоки добавляют и убирают на ходу, и вешать
       обработчики на каждый значило бы плодить их при каждом добавлении. */
    function bindBlocks() {
      var host = byId(p + "EditBlocks");
      if (!host) return;
      host.addEventListener("click", function (event) {
        var drop = event.target.closest("[data-agent-drop]");
        if (drop) {
          var block = drop.closest("[data-block]");
          drop.closest("[data-agent-row]").remove();
          refreshAgentRows(block);
          return;
        }
        var add = event.target.closest("[data-agent-add]");
        if (add) {
          var row = addAgentRow(add.closest("[data-block]"), null, null);
          if (row) row.querySelector("[data-agent-amount]").focus();
          return;
        }
        var blockDrop = event.target.closest("[data-block-drop]");
        if (blockDrop) {
          blockDrop.closest("[data-block]").remove();
          refreshBlocks();
        }
      });
      host.addEventListener("change", function (event) {
        var buyerSelect = event.target.closest("[data-block-buyer]");
        if (buyerSelect) {
          var buyerBlock = buyerSelect.closest("[data-block]");
          var offerSelect = buyerBlock.querySelector("[data-block-offer]");
          if (offerSelect) {
            offerSelect.innerHTML = modalOfferOptions(buyerSelect.value, offerSelect.value);
          }
        }
        if (event.target.closest("[data-agent-select]")) {
          refreshAgentRows(event.target.closest("[data-block]"));
        }
        var pickDay = event.target.closest("[data-pick-day]");
        if (pickDay) reloadDayAgents(pickDay.closest("[data-block]"), pickDay.value).catch(fail);
      });
      host.addEventListener("input", function (event) {
        if (event.target.closest("[data-agent-amount]")) {
          var block = event.target.closest("[data-block]");
          var rows = agentRows(block);
          rows.forEach(agentHint);
        }
      });
    }

    function numberValue(id) {
      var element = byId(id);
      if (!element || element.value === "") return null;
      var value = Number(element.value);
      return isFinite(value) ? value : null;
    }

    /* Один блок формы в то, что уходит на сервер. Пустой оффер оставляем
       пустым: это и есть «на весь день». */
    function blockForm(block, index, record) {
      var pinned = block.hasAttribute("data-locked-date");
      // В окне тира день выбирают календарём — берём его из поля.
      var recordDate = pinned && !block.hasAttribute("data-pick-date")
        ? block.getAttribute("data-locked-date")
        : byId(p + "EditDate_" + index).value;
      var buyerId = pinned
        ? block.getAttribute("data-locked-buyer")
        : block.querySelector("[data-block-buyer]").value;
      var offerId = pinned
        ? block.getAttribute("data-locked-offer")
        : block.querySelector("[data-block-offer]").value;
      // «tier:T1» — это не оффер, а день одного тира.
      var tier = pinned ? block.getAttribute("data-locked-tier") || "" : "";
      if (offerId.indexOf("tier:") === 0) {
        tier = offerId.slice(5);
        offerId = "";
      }
      if (block.hasAttribute("data-day-loading")) {
        return "Подождите — загружаются суммы за выбранный день";
      }
      if (!recordDate || !buyerId) return "Заполните дату и баера в каждом блоке";
      if (!offerId && !config.dayLevelSpend) return "Выберите оффер";
      // `null` (not `[]`) when the modal has no services block: the API then leaves
      // the record's existing service values alone instead of deleting them.
      var services = null;
      if (!config.hideServices && !pinned) {
        services = [];
        state.services.forEach(function (service) {
          var value = numberValue(p + "EditService_" + service.id + "_" + index);
          if (value != null && value > 0) {
            services.push({ service_id: service.id, quantity: value });
          }
        });
      }
      /* Пустое поле суммы — строка недозаполнена, её пропускаем; убранная
         строка тем самым и снимает сумму агента с записи.

         Ноль при этом — обычная сумма, а не «ничего не ввели»: им закрывают
         день, у которого расхода не было. Раньше такая строка отбрасывалась, и
         форма отвечала «укажите агента и сумму» на заполненном блоке. */
      var providers = [];
      var seenProviders = {};
      agentRows(block).forEach(function (row) {
        var providerId = agentPick(row).value;
        var input = row.querySelector("[data-agent-amount]");
        var value = input.value.trim() === "" ? null : Number(input.value);
        if (!providerId || seenProviders[providerId]) return;
        if (value == null || !isFinite(value) || value < 0) return;
        seenProviders[providerId] = true;
        providers.push({ provider_id: providerId, base_amount: value });
      });
      /* Пустой блок «на весь день» в общей форме сохранять нечего, а
         промолчать нельзя: пустой список стёр бы расход дня, который только
         что ввели соседним блоком или прямо в таблице.

         Окно, открытое из таблицы, — другое дело: оно правит расход именно
         этого дня, и убранная строка агента там означает «этой суммы больше
         нет». Убрали все — день закрывается нулём, как удаление записи. */
      if (!offerId && !providers.length && !pinned) {
        return "В блоке за весь день укажите агента и сумму";
      }
      return {
        record: record,
        record_date: recordDate,
        buyer_id: buyerId,
        offer_id: offerId || null,
        tier: tier || null,
        services: services,
        providers: providers,
        numberValue: function (name) { return numberValue(name + "_" + index); }
      };
    }

    /* «Обновлено: N мин назад ⟳» в конце строки фильтров. Время — из того же
       статуса, что у карточки Keitaro в меню; кнопка ставит синхронизацию через
       /integrations/keitaro/refresh, доступную с правами Медиаборда (ручной
       запуск в настройках — только у администратора). Когда синхронизация
       закончилась, строки перечитываются здесь же, без перезагрузки страницы. */
    var keitaroRefresh = { host: null, status: null, busy: false };

    var REFRESH_SVG =
      '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
      '<path d="M20 11a8 8 0 0 0-14.9-3M4 13a8 8 0 0 0 14.9 3" stroke="currentColor" ' +
      'stroke-width="2" stroke-linecap="round"/><path d="M5 3v5h5M19 21v-5h-5" ' +
      'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';

    function refreshedAgo(value) {
      if (!value) return "ещё не обновлялось";
      var seconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000));
      if (seconds < 45) return "только что";
      if (seconds < 3600) return Math.floor(seconds / 60) + " мин назад";
      if (seconds < 86400) return Math.floor(seconds / 3600) + " ч назад";
      return Math.floor(seconds / 86400) + " дн назад";
    }

    function paintKeitaroRefresh() {
      var host = keitaroRefresh.host;
      var status = keitaroRefresh.status;
      if (!host) return;
      // Без подключения Keitaro обновлять нечего — элемента нет вовсе.
      if (!status || !status.configured) {
        host.hidden = true;
        return;
      }
      host.hidden = false;
      var queued = status.state === "queued";
      var syncing = keitaroRefresh.busy || status.state === "syncing" || queued;
      var failed = !syncing && status.state === "error";
      var label = host.querySelector("[data-kr-label]");
      var button = host.querySelector("[data-kr-button]");
      host.classList.toggle("is-syncing", syncing);
      host.classList.toggle("is-error", failed);
      if (queued && !keitaroRefresh.busy) {
        label.textContent = "Ожидает запуска";
      } else if (syncing) {
        label.textContent = "Обновляем" +
          (Number(status.progress_pct) > 0 ? " · " + Number(status.progress_pct) + "%" : "…");
      } else if (failed) {
        label.textContent = "Ошибка синхронизации";
      } else {
        label.textContent = "Обновлено: " + refreshedAgo(status.last_sync_at);
      }
      host.title = failed && status.error ? status.error
        : status.state === "inactive" ? "Подключение Keitaro выключено" : "";
      button.disabled = syncing || status.state === "inactive";
      button.setAttribute("aria-label", syncing ? "Данные Keitaro обновляются"
        : "Обновить данные Keitaro");
    }

    async function loadKeitaroStatus() {
      try {
        keitaroRefresh.status = await api.get("/integrations/keitaro/sidebar-status");
      } catch (error) {
        if (error && error.status === 401) fail(error);
      }
      paintKeitaroRefresh();
      return keitaroRefresh.status;
    }

    async function runKeitaroRefresh() {
      if (keitaroRefresh.busy) return;
      keitaroRefresh.busy = true;
      paintKeitaroRefresh();
      try {
        var result = await api.post("/integrations/keitaro/refresh", {});
        if (result.state === "fresh") {
          // Данные моложе минуты: трекер заново не тянем, но строки перечитываем —
          // человек нажал «обновить» и ждёт актуальную таблицу.
          await loadRecords();
          return;
        }
        for (var attempt = 0; attempt < 120; attempt += 1) {
          await new Promise(function (resolve) { window.setTimeout(resolve, 2000); });
          var status = await loadKeitaroStatus();
          if (!status || status.state === "syncing" || status.state === "queued") continue;
          if (status.state === "error") {
            toast(status.error || "Синхронизация Keitaro завершилась с ошибкой", "error");
            return;
          }
          // Опрос в меню пришлёт о той же синхронизации событие — отмечаем её,
          // чтобы строки не перечитывались второй раз.
          if (status.last_sync_at) state.lastKeitaroRefreshAt = status.last_sync_at;
          await loadRecords();
          return;
        }
        toast("Синхронизация продолжается в фоне", "info");
      } catch (error) {
        fail(error);
      } finally {
        keitaroRefresh.busy = false;
        await loadKeitaroStatus();
      }
    }

    function mountKeitaroRefresh() {
      if (keitaroRefresh.host) return;
      // Рядом с «Экспортом» и «Видом»: в строке фильтров кнопка терялась среди
      // полей и была не того размера, что остальные действия над таблицей.
      var exportButton = el("Export");
      var anchor = exportButton && exportButton.closest(".board-export");
      var bar = anchor ? anchor.parentElement : el("Filters");
      if (!bar) return;
      var host = document.createElement("div");
      host.className = "kt-refresh";
      host.hidden = true;
      host.innerHTML = '<button type="button" class="board-export__btn kt-refresh__btn" ' +
        'data-kr-button aria-label="Обновить данные Keitaro">' + REFRESH_SVG +
        '<span class="kt-refresh__label" data-kr-label></span></button>';
      if (anchor) bar.insertBefore(host, anchor);
      else bar.appendChild(host);
      keitaroRefresh.host = host;
      host.querySelector("[data-kr-button]").addEventListener("click", function () {
        runKeitaroRefresh().catch(fail);
      });
      loadKeitaroStatus();
      // «N мин назад» стареет само, а статус меняют и планировщик, и другие вкладки.
      window.setInterval(function () {
        if (!keitaroRefresh.busy) loadKeitaroStatus();
      }, 30000);
      window.addEventListener("celestial:keitaro-synced", function () {
        if (!keitaroRefresh.busy) loadKeitaroStatus();
      });
    }

    async function saveModal(record, locked) {
      var forms = [];
      var problem = "";
      blocks().forEach(function (block, index) {
        var form = blockForm(block, index, record);
        if (typeof form === "string") problem = problem || form;
        else forms.push(form);
      });
      if (problem || !forms.length) {
        toast(problem || "Заполните дату, баера и оффер", "error");
        return;
      }
      var saveButton = byId(p + "EditSave");
      saveButton.disabled = true;
      saveButton.textContent = "Сохраняю…";
      try {
        // Блоки сохраняются по очереди: они могут попасть в один и тот же день
        // одного баера, а раскладка расхода по дню перезаписывает весь день.
        for (var index = 0; index < forms.length; index += 1) {
          await config.save(forms[index]);
        }
        toast(record || locked ? "Запись обновлена"
          : forms.length > 1 ? "Сохранено блоков: " + forms.length : "Запись создана");
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
      // Раньше ссылок: справочники наполняют уже созданные поля фильтров.
      bindFilters();
      // Строки просим уже с сохранённым периодом, иначе первый запрос ушёл бы
      // за всю историю доски, а через миг его пришлось бы повторять.
      await restoreFilters();
      // Both requests leave together: the board data does not depend on the catalog,
      // and running them back to back added a whole round trip to every navigation.
      var pendingGroups = fetchGroups();
      var pendingQuery = queryString(filterParams());
      // A rejection handled later still counts as unhandled until then.
      pendingGroups.catch(function () { /* surfaced by loadRecords below */ });
      await loadRefs();
      renderStructureBar();
      bindViewControls();
      if (config.exportEndpoint) bindExport();
      if (table && state.display.dense) {
        table.classList.add("celestial-board-table--dense");
      }
      renderHead();
      bindTableEvents();
      // Статус Keitaro опрашивается общей навигацией. Если синхронизация
      // завершилась, пока доска открыта, перечитываем строки без перезагрузки
      // страницы, чтобы новые продажи сразу появились в таблице.
      window.addEventListener("celestial:keitaro-synced", function (event) {
        var stamp = event && event.detail && event.detail.lastSyncAt;
        if (stamp && stamp === state.lastKeitaroRefreshAt) return;
        state.lastKeitaroRefreshAt = stamp || String(Date.now());
        loadRecords().catch(fail);
      });
      if (config.keitaroRefresh) mountKeitaroRefresh();
      if (config.afterInit) config.afterInit(state, { loadRecords: loadRecords, fail: fail, filterQuery: filterQuery });
      // Справочник мог не подтвердить сохранённое значение — баера убрали из
      // команды, оффер сняли. Тогда фильтр сузился уже после запроса, и ответ
      // на руках не тот, который показывают поля.
      if (pendingQuery !== queryString(filterParams())) pendingGroups = null;
      await loadRecords(pendingGroups);
    }

    return { init: init, state: state, openEditModal: openEditModal };
  }

  /* ---------- Mediaboard config ---------- */

  var mediaBoard = createBoard({
    prefix: "media",
    endpoint: "/media-records",
    groupsEndpoint: "/media-records/groups",
    exportEndpoint: "/api/v1/exports/media",
    preferenceKey: "mediaboard.structure",
    displayPreferenceKey: "mediaboard.display",
    filtersPreferenceKey: "mediaboard.filters",
    foldsPreferenceKey: "mediaboard.folds",
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
    // Баер закрывает день целиком: несколько блоков за один заход и расход на
    // день без выбора оффера — сервер разложит его по офферам этого дня.
    multiBlock: true,
    dayLevelSpend: true,
    // «Обновлено: N мин назад» и ручное обновление данных Keitaro в фильтрах.
    keitaroRefresh: true,
    metricColumns: [
      { group: "funnel", label: "INST", kind: "num", get: function (s) { return s.installs; } },
      {
        // Доля установок, дошедших до депозита.
        group: "funnel", label: "INST2DEP", kind: "percent",
        get: function (s) {
          var installs = amount(s.installs);
          return installs > 0 ? amount(s.ftd) / installs * 100 : null;
        }
      },
      { group: "funnel", label: "REG", kind: "num", get: function (s) { return s.registrations; } },
      {
        group: "funnel", label: "REG2DEP", kind: "percent",
        get: function (s) {
          var registrations = amount(s.registrations);
          return registrations > 0 ? amount(s.ftd) / registrations * 100 : null;
        }
      },
      { group: "funnel", label: "FTD", kind: "num", get: function (s) { return s.ftd; } },
      {
        // Сколько дохода принесла одна установка.
        group: "funnel", label: "EPI", kind: "money",
        get: function (s) {
          var installs = amount(s.installs);
          return installs > 0 ? amount(s.revenue) / installs : null;
        }
      },
      {
        group: "costs", label: "SPEND", kind: "money", editKey: "spend",
        get: function (s) { return s.spend; }
      },
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
      /* Без оффера расход относится ко дню целиком: сервер делит его поровну
         между офферами этого дня. Создавать запись тут нечего — делить можно
         только то, что уже принесла синхронизация. */
      if (!form.offer_id) {
        await api.post("/media-records/day-spend", {
          record_date: form.record_date,
          buyer_id: form.buyer_id,
          tier: form.tier || null,
          providers: form.providers
        });
        return;
      }
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
    initMedia: function (user) { return mediaBoard.init(user); },
    // Тот же фильтр с поиском, что над Медиабордом: его просят и другие
    // разделы, а второй такой же в соседнем файле разошёлся бы с этим.
    multiFilter: createMultiFilter,
    // Окно расхода дня в Финансах выглядит как окно правки Медиаборда —
    // стили берёт отсюда же, чтобы окна не расходились.
    modalStyles: ensureFinanceModalStyles
  };
})();
