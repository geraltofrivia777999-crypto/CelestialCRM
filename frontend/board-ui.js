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
    return new Intl.NumberFormat("ru-RU", {
      minimumFractionDigits: 0, maximumFractionDigits: 1
    }).format(Number(value)) + "%";
  }

  function toast(message, kind) {
    var current = byId("celestialLiveToast");
    if (current) current.remove();
    var element = document.createElement("div");
    element.id = "celestialLiveToast";
    element.textContent = message;
    element.style.cssText =
      "position:fixed;right:24px;bottom:24px;z-index:99999;max-width:420px;" +
      "padding:13px 17px;border-radius:11px;color:#fff;font:700 12px Manrope,sans-serif;" +
      "box-shadow:0 14px 38px rgba(31,34,49,.22);background:" +
      (kind === "error" ? "#D94B61" : kind === "info" ? "#5A5FE0" : "#16B57F");
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
      "overflow:hidden!important;font-family:'Manrope',-apple-system,'Helvetica Neue',sans-serif}" +
      ".board-edit-overlay--media .board-edit-card{width:min(880px,calc(100vw - 48px))!important;" +
      "max-width:none!important;max-height:calc(100vh - 48px);padding:0!important;overflow:hidden;" +
      "display:flex;flex-direction:column;border:1px solid rgba(225,228,237,.82);" +
      "box-shadow:0 28px 80px rgba(23,26,38,.28)!important}" +
      ".board-edit-overlay--media .board-edit-card:focus{outline:none}" +
      ".board-edit-overlay--media .board-edit-header{padding:21px 24px 18px!important;" +
      "margin:0!important;border-bottom:1px solid #ECEEF3;flex-shrink:0}" +
      ".board-edit-overlay--media .board-edit-title{font-family:'Space Grotesk','Manrope',sans-serif!important;" +
      "font-size:20px!important;line-height:1.2;letter-spacing:-.25px}" +
      ".board-edit-overlay--media .board-edit-subtitle{display:block!important;color:#A2A7B5;" +
      "font-size:11px;font-weight:600;line-height:1.45;margin-top:5px}" +
      ".board-edit-overlay--media .board-edit-close{display:flex;align-items:center;justify-content:center;" +
      "flex-shrink:0;color:#8A8FA3!important;transition:background .18s,color .18s}" +
      ".board-edit-overlay--media .board-edit-close:hover{background:#ECEEF5!important;color:#555B6D!important}" +
      ".board-edit-overlay--media .board-edit-body{padding:20px 24px 22px!important;overflow-y:auto;" +
      "overscroll-behavior:contain;scrollbar-gutter:stable}" +
      ".board-edit-overlay--media .board-edit-context{padding:14px;background:#F8F9FC;" +
      "border:1px solid #ECEEF3;border-radius:14px}" +
      ".board-edit-overlay--media .board-edit-main-grid{grid-template-columns:150px 190px minmax(0,1fr)!important;" +
      "gap:12px!important;margin:0!important}" +
      ".board-edit-overlay--media .board-edit-field{min-width:0;gap:6px!important;" +
      "font-size:10.5px!important;color:#6B7180!important}" +
      ".board-edit-overlay--media .board-edit-input,.board-edit-overlay--media .board-edit-select{" +
      "display:block;width:100%;min-width:0;height:42px;border:1px solid #DFE2EB!important;" +
      "border-radius:10px!important;padding:0 12px!important;background:#fff;color:#292D3B!important;" +
      "font-family:'Manrope',sans-serif!important;font-size:12.5px!important;font-weight:600!important;" +
      "outline:none;text-overflow:ellipsis;transition:border-color .18s,box-shadow .18s}" +
      ".board-edit-overlay--media .board-edit-input:focus,.board-edit-overlay--media .board-edit-select:focus{" +
      "border-color:#8589E9!important;box-shadow:0 0 0 3px rgba(90,95,224,.09)}" +
      ".board-edit-overlay--media .board-edit-section{margin-top:14px;padding:14px;" +
      "border:1px solid #ECEEF3;border-radius:14px;background:#fff}" +
      ".board-edit-overlay--media .board-edit-section-heading{display:flex;align-items:center;" +
      "justify-content:space-between;gap:12px;margin:0 0 11px!important}" +
      ".board-edit-overlay--media .board-edit-section-title{font-family:'Space Grotesk','Manrope',sans-serif;" +
      "font-size:11px;font-weight:700!important;letter-spacing:.25px!important;text-transform:none!important;" +
      "color:#4D5263!important}" +
      ".board-edit-overlay--media .board-edit-section--services .board-edit-section-title{color:#5A5FE0!important}" +
      ".board-edit-overlay--media .board-edit-section--providers .board-edit-section-title{color:#C9821F!important}" +
      ".board-edit-overlay--media .board-edit-section-note{color:#A2A7B5;font-size:10px;font-weight:600}" +
      ".board-edit-overlay--media .board-edit-grid{display:grid!important;" +
      "grid-template-columns:repeat(auto-fit,minmax(145px,1fr))!important;gap:10px!important}" +
      ".board-edit-overlay--media .board-edit-footer{display:flex;align-items:center;" +
      "justify-content:flex-end;gap:10px;margin:0!important;padding:15px 24px 18px!important;" +
      "border-top:1px solid #ECEEF3;background:#fff;flex-shrink:0}" +
      ".board-edit-overlay--media .board-edit-button{height:42px;padding:0 18px!important;" +
      "border-radius:10px!important;font-family:'Manrope',sans-serif!important;font-size:12px!important}" +
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
      "@media(max-width:480px){" +
      ".board-edit-overlay--media .board-edit-main-grid{grid-template-columns:1fr!important}" +
      ".board-edit-overlay--media .board-edit-offer-field{grid-column:auto}" +
      ".board-edit-overlay--media .board-edit-grid{grid-template-columns:repeat(2,minmax(0,1fr))!important}" +
      ".board-edit-overlay--media .board-edit-section{padding:12px}" +
      ".board-edit-overlay--media .board-edit-section-heading{align-items:flex-start;flex-direction:column;gap:3px}" +
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
      ".cs-sep{display:inline-flex;align-items:center;color:#C7CAD6;flex-shrink:0}" +
      // touch-action:none lets a finger drag the chip instead of scrolling the page.
      ".cs-chip{display:inline-flex;align-items:center;gap:7px;border:1px solid transparent;" +
      "border-radius:10px;padding:6px 11px 6px 8px;font:700 12.5px 'Manrope',sans-serif;" +
      "background:#EEF0FF;color:#5A5FE0;cursor:grab;user-select:none;touch-action:none;" +
      "transition:background .18s,color .18s,border-color .18s,box-shadow .18s,transform .12s}" +
      '.cs-chip[data-on="0"]{background:#F4F5F9;color:#A2A7B5}' +
      ".cs-chip:hover{border-color:rgba(90,95,224,.28)}" +
      '.cs-chip[data-on="0"]:hover{border-color:#DDE0EA}' +
      ".cs-chip:focus-visible{outline:none;border-color:#8589E9;box-shadow:0 0 0 3px rgba(90,95,224,.16)}" +
      ".cs-grip{display:inline-flex;flex-shrink:0;opacity:.5;transition:opacity .18s}" +
      ".cs-chip:hover .cs-grip{opacity:.95}" +
      // Pressing feedback: the chip dips a touch before it lifts off.
      ".cs-chip--pressed{transform:scale(.96)}" +
      // What stays behind in the row while the clone follows the pointer.
      ".cs-chip--ghost{background:#F1F2F7;border:1px dashed #C9CDDB;color:transparent;box-shadow:none}" +
      ".cs-chip--ghost .cs-grip{visibility:hidden}" +
      ".cs-chip--flying{position:fixed;z-index:10060;margin:0;pointer-events:none;cursor:grabbing;" +
      "border-color:rgba(90,95,224,.4);box-shadow:0 18px 38px rgba(31,34,49,.24);" +
      "transform:scale(1.06) rotate(-1.5deg)}" +
      "body.cs-dragging{cursor:grabbing}" +
      "body.cs-dragging .cs-chip{cursor:grabbing}" +
      "@media(prefers-reduced-motion:reduce){.cs-chip,.cs-slot{transition:none!important}}";
    document.head.appendChild(style);
  }

  var LEVELS = {
    buyer: { label: "Баер", key: function (r) { return r.buyer_id; }, name: function (r) { return r.buyer; } },
    geo: { label: "GEO", key: function (r) { return r.geo || "—"; }, name: function (r) { return r.geo || "Без GEO"; } },
    partner: { label: "Партнёрка", key: function (r) { return r.partner || "—"; }, name: function (r) { return r.partner || "Без ПП"; } },
    offer: { label: "Оффер", key: function (r) { return r.offer_id; }, name: function (r) { return r.offer; } }
  };

  var TH_SUB = 'style="position:sticky;top:41px;z-index:4;background:#FBFBFD;text-align:right;' +
    'padding:9px 12px;border-bottom:1px solid #E7E9F1;font-size:11px;font-weight:700;' +
    'color:#8A8FA3;font-family:Space Grotesk;white-space:nowrap{extra}"';

  function subTh(label, extra) {
    return '<th title="' + escapeHtml(label) + '" ' +
      TH_SUB.replace("{extra}", extra || "") + ">" + escapeHtml(label) + "</th>";
  }

  function groupTh(label, colspan, background, color, borderColor) {
    if (!colspan) return "";
    return '<th colspan="' + colspan + '" style="position:sticky;top:0;z-index:4;background:' + background +
      ';text-align:center;padding:11px;border-bottom:1px solid ' + (borderColor || "#E7E9F1") +
      ';border-right:1px solid #E7E9F1;font-size:11px;font-weight:800;color:' + color +
      ';text-transform:uppercase;letter-spacing:.7px">' + escapeHtml(label) + "</th>";
  }

  function td(content, opts) {
    opts = opts || {};
    return '<td style="text-align:right;padding:12px;font-family:Space Grotesk;font-size:12.5px;' +
      "font-weight:" + (opts.bold ? "800" : "600") + ";border-bottom:1px solid #F2F3F8;white-space:nowrap;" +
      (opts.color ? "color:" + opts.color + ";" : "") + (opts.extra || "") + '">' + content + "</td>";
  }

  /* ---------- board factory ---------- */

  function createBoard(config) {
    var state = {
      user: null,
      services: [],
      providers: [],
      buyers: [],
      offers: [],
      partners: [],
      records: [],
      total: 0,
      structure: null,
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

    function filterQuery() {
      var params = [];
      function add(name, id) {
        var element = el(id);
        if (element && element.value) params.push(name + "=" + encodeURIComponent(element.value));
      }
      add("buyer_id", "FilterBuyer");
      add("date_from", "FilterDateFrom");
      add("date_to", "FilterDateTo");
      add("geo", "FilterGeo");
      add("partner_id", "FilterPartner");
      add("offer_id", "FilterOffer");
      if (config.extraFilters) config.extraFilters(params);
      return params.length ? "&" + params.join("&") : "";
    }

    function fillSelect(id, items, valueKey, labelKey) {
      var element = el(id);
      if (!element) return;
      var current = element.value;
      element.innerHTML = '<option value="">Все</option>' + items.map(function (item) {
        return '<option value="' + escapeHtml(item[valueKey]) + '">' +
          escapeHtml(item[labelKey]) + "</option>";
      }).join("");
      element.value = current;
    }

    function bindFilters() {
      ["FilterBuyer", "FilterDateFrom", "FilterDateTo", "FilterGeo", "FilterPartner", "FilterOffer"]
        .forEach(function (id) {
          var element = el(id);
          if (element) element.addEventListener("change", function () { loadRecords().catch(fail); });
        });
      var reset = el("FilterReset");
      if (reset) reset.addEventListener("click", function () {
        ["FilterBuyer", "FilterDateFrom", "FilterDateTo", "FilterGeo", "FilterPartner", "FilterOffer"]
          .forEach(function (id) { var element = el(id); if (element) element.value = ""; });
        if (config.resetExtraFilters) config.resetExtraFilters();
        loadRecords().catch(fail);
      });
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
        renderTable();
        persistStructure();
      }
    }

    function persistStructure() {
      api.put("/me/preferences/" + config.preferenceKey, { value: state.structure })
        .catch(function () { toast("Не удалось сохранить структуру", "error"); });
    }

    function saveStructure(focusKey) {
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

    /* ----- table header ----- */

    function renderHead() {
      var head = el("TableHead");
      if (!head) return;
      var funnel = config.funnelColumns;
      var results = config.resultColumns;
      var row1 = "<tr>" +
        '<th rowspan="2" style="position:sticky;left:0;top:0;z-index:5;background:#F8F9FC;text-align:left;padding:14px 16px;min-width:290px;width:290px;border-bottom:1px solid #E7E9F1;border-right:1px solid #E7E9F1;font-size:11px;font-weight:700;color:#8A8FA3;text-transform:uppercase;letter-spacing:.6px">Структура</th>' +
        groupTh("Сервисы", state.services.length, "#F1F2FF", "#5A5FE0", "#E1E3F5") +
        groupTh("Агенты и платёжки", state.providers.length, "#FFF6E9", "#C9821F", "#F2E5CC") +
        groupTh(config.funnelTitle, funnel.length, "#E9F8F1", "#16B57F", "#D2EEE1") +
        groupTh("Затраты", 2, "#F4F5F9", "#6B7180", "#E4E6EF") +
        groupTh("Результат", results.length, "#1F2231", "#fff", "#1F2231") +
        (state.canManage ? '<th rowspan="2" style="position:sticky;top:0;z-index:4;background:#F8F9FC;border-bottom:1px solid #E7E9F1;width:44px"></th>' : "") +
        "</tr>";
      var row2 = "<tr>" +
        state.services.map(function (service, index) {
          return subTh(service.name, index === 0 ? ";border-left:1px solid #EEF0F7" : "");
        }).join("") +
        state.providers.map(function (provider) { return subTh(provider.name); }).join("") +
        funnel.map(function (column) { return subTh(column.label); }).join("") +
        subTh("RENT") + subTh("SPEND") +
        results.map(function (column) { return subTh(column.label); }).join("") +
        "</tr>";
      head.innerHTML = row1 + row2;
    }

    function columnCount() {
      return 1 + state.services.length + state.providers.length +
        config.funnelColumns.length + 2 + config.resultColumns.length +
        (state.canManage ? 1 : 0);
    }

    /* ----- aggregation ----- */

    function newAggregate() {
      return {
        services: {}, providers: {},
        sums: {}, count: 0
      };
    }

    function accumulate(aggregate, record) {
      aggregate.count += 1;
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
      config.sumFields.forEach(function (field) {
        var value = record[field];
        if (value != null) aggregate.sums[field] = (aggregate.sums[field] || 0) + Number(value);
      });
    }

    function groupRecords(records, levels) {
      var root = { children: {}, order: [], aggregate: newAggregate(), records: [] };
      records.forEach(function (record) {
        accumulate(root.aggregate, record);
        var node = root;
        levels.forEach(function (levelKey) {
          var level = LEVELS[levelKey];
          var key = String(level.key(record));
          if (!node.children[key]) {
            node.children[key] = {
              key: key, level: levelKey, name: level.name(record),
              children: {}, order: [], aggregate: newAggregate(), records: []
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
      var cells = "";
      state.services.forEach(function (service, index) {
        var value = source.services[service.id];
        cells += td(value == null ? '<span style="color:#C7CAD6">–</span>' : num(value),
          { bold: opts.bold, extra: index === 0 ? "border-left:1px solid #F0F1F7;" : "" });
      });
      state.providers.forEach(function (provider) {
        var value = source.providers[provider.id];
        cells += td(value == null ? '<span style="color:#C7CAD6">–</span>' : money(value), { bold: opts.bold });
      });
      cells += config.metricCells(source.sums, opts);
      return cells;
    }

    function nodePath(prefixPath, key) { return prefixPath + "|" + key; }

    function renderNodeRows(node, depth, path, output) {
      var isCollapsed = !!state.collapsed[path];
      var arrow = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" style="transform:rotate(' +
        (isCollapsed ? "-90deg" : "0deg") + ');transition:transform .15s"><path d="m6 9 6 6 6-6" stroke="' +
        (depth === 0 ? "#5A5FE0" : "#8A8FA3") + '" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>';
      var background = depth === 0 ? "#F5F6FF" : "#fff";
      var label = '<div style="display:flex;align-items:center;gap:9px">' + arrow +
        '<span style="font-weight:' + (depth === 0 ? "800" : "700") + ';font-size:' +
        (depth === 0 ? "13.5px" : "12.5px") + '">' + escapeHtml(node.name) + "</span>" +
        '<span style="font-size:10.5px;font-weight:700;color:#A2A7B5">' +
        escapeHtml(LEVELS[node.level].label) + "</span></div>";
      output.push('<tr data-node="' + escapeHtml(path) + '" style="background:' + background + ';cursor:pointer">' +
        '<td style="position:sticky;left:0;z-index:2;background:' + background +
        ';padding:12px 16px 12px ' + (16 + depth * 22) +
        'px;border-bottom:1px solid #EDEFF6;border-right:1px solid #E7E9F1">' + label + "</td>" +
        metricCells({ services: node.aggregate.services, providers: node.aggregate.providers, sums: node.aggregate.sums }, { bold: depth === 0 }) +
        (state.canManage ? td("", {}) : "") +
        "</tr>");
      if (isCollapsed) return;
      node.order.forEach(function (key) {
        renderNodeRows(node.children[key], depth + 1, nodePath(path, key), output);
      });
      node.records.forEach(function (record) {
        output.push(renderLeafRow(record, depth + 1));
      });
    }

    function renderLeafRow(record, depth) {
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
        '<span style="width:6px;height:6px;border-radius:50%;background:#C7CAD6;flex-shrink:0"></span>' +
        '<span style="font-size:12px;font-weight:600;color:#6B7180">' + escapeHtml(record.record_date) + "</span>" +
        '<span style="font-size:11px;color:#A2A7B5;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:150px">' +
        escapeHtml(config.leafLabel(record)) + "</span></div>";
      return '<tr style="background:#fff">' +
        '<td style="position:sticky;left:0;z-index:2;background:#fff;padding:10px 16px 10px ' +
        (16 + depth * 22) + 'px;border-bottom:1px solid #F2F3F8;border-right:1px solid #E7E9F1">' + label + "</td>" +
        metricCells({ services: recordServices, providers: recordProviders, sums: sums }, { bold: false, leaf: true, record: record }) +
        (state.canManage
          ? td('<button data-edit="' + escapeHtml(record.id) + '" title="Изменить данные" style="border:none;background:transparent;cursor:pointer;padding:2px">' +
              '<svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M4 20h4L19.5 8.5a2.1 2.1 0 0 0-3-3L5 17v3Z" stroke="#8A8FA3" stroke-width="2" stroke-linejoin="round"/></svg></button>', {})
          : "") +
        "</tr>";
    }

    function renderTable() {
      var body = el("TableBody");
      if (!body) return;
      var count = el("ResultCount");
      if (count) {
        count.textContent = "Показано записей: " + state.records.length + " из " + state.total;
      }
      if (!state.records.length) {
        body.innerHTML = '<tr><td colspan="' + columnCount() +
          '" style="padding:44px 24px;text-align:center;color:#A2A7B5;font-size:13px">' +
          escapeHtml(config.emptyMessage) + "</td></tr>";
        return;
      }
      var tree = groupRecords(state.records, activeLevels());
      var output = [];
      tree.order.forEach(function (key) {
        renderNodeRows(tree.children[key], 0, key, output);
      });
      tree.records.forEach(function (record) { output.push(renderLeafRow(record, 0)); });
      body.innerHTML = output.join("");
      body.querySelectorAll("[data-node]").forEach(function (row) {
        row.addEventListener("click", function (event) {
          if (event.target.closest("[data-edit]")) return;
          var path = row.getAttribute("data-node");
          state.collapsed[path] = !state.collapsed[path];
          renderTable();
        });
      });
      body.querySelectorAll("[data-edit]").forEach(function (button) {
        button.addEventListener("click", function (event) {
          event.stopPropagation();
          var record = state.records.find(function (item) {
            return item.id === button.getAttribute("data-edit");
          });
          openEditModal(record || null);
        });
      });
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
        api.getAll("/offers"),
        api.getAll("/partners"),
        api.get("/me/preferences/" + config.preferenceKey)
      ]);
      state.services = (results[0].items || []).filter(function (s) { return s.status === "active"; });
      state.providers = (results[1].items || []).filter(function (s) { return s.status === "active"; });
      state.buyers = results[2] || [];
      state.offers = results[3].items || [];
      state.partners = (results[4].items || []);
      var preference = results[5] && results[5].value;
      state.structure = preference && Array.isArray(preference.items) && preference.items.length
        ? preference : defaultStructure();
      fillSelect("FilterBuyer", state.buyers, "id", "name");
      fillSelect("FilterPartner", state.partners, "id", "name");
      fillSelect("FilterOffer", state.offers, "id", "name");
      var geos = [];
      state.offers.forEach(function (offer) {
        if (offer.geo && geos.indexOf(offer.geo) < 0) geos.push(offer.geo);
      });
      geos.sort();
      fillSelect("FilterGeo", geos.map(function (geo) { return { id: geo, name: geo }; }), "id", "name");
    }

    function endpointWithFilters() {
      var query = filterQuery().replace(/^&/, "");
      return config.endpoint + (query ? "?" + query : "");
    }

    async function loadRecords() {
      // Every matching row is needed: the group totals are summed on the client,
      // so a truncated page would render wrong aggregates.
      var page = await api.getAll(endpointWithFilters(), 1000);
      state.records = page.items || [];
      state.total = page.total || 0;
      renderTable();
      if (config.onData) config.onData(state);
    }

    /* ----- edit modal ----- */

    function modalInput(label, id, type, value, attrs) {
      var inputType = type || "text";
      var inputMode = inputType === "number" ? ' inputmode="decimal"' : "";
      var fieldClass = id === "financeEditLink" ? " board-edit-field--wide" : "";
      return '<label class="board-edit-field' + fieldClass + '" for="' + id + '" style="display:flex;flex-direction:column;gap:5px;font-size:11.5px;font-weight:700;color:#6B7180">' +
        escapeHtml(label) +
        '<input class="board-edit-input" id="' + id + '" type="' + inputType + '" value="' +
        escapeHtml(value == null ? "" : value) + '"' + inputMode + " " + (attrs || "") +
        ' autocomplete="off" style="border:1px solid #ECEEF3;border-radius:9px;padding:9px 11px;font:600 13px Manrope,sans-serif;outline:none;color:#171A26">' +
        "</label>";
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
        "position:fixed;inset:0;z-index:9999;background:rgba(23,26,38,.45);display:flex;" +
        "align-items:flex-start;justify-content:center;padding:40px 16px;overflow-y:auto";
      var buyersOptions = state.buyers.map(function (buyer) {
        return '<option value="' + escapeHtml(buyer.id) + '"' +
          (record && record.buyer_id === buyer.id ? " selected" : "") + ">" +
          escapeHtml(buyer.name) + "</option>";
      }).join("");
      var offersOptions = state.offers.map(function (offer) {
        return '<option value="' + escapeHtml(offer.id) + '"' +
          (record && record.offer_id === offer.id ? " selected" : "") + ">" +
          escapeHtml(offer.name + (offer.geo ? " · " + offer.geo : "")) + "</option>";
      }).join("");
      var servicesInputs = state.services.map(function (service) {
        var value = record && record.services && record.services[service.id];
        return modalInput(service.name, p + "EditService_" + service.id, "number",
          value ? Number(value.quantity) : "", 'step="any" min="0"');
      }).join("");
      var providersInputs = state.providers.map(function (provider) {
        var value = record && record.providers && record.providers[provider.id];
        return modalInput(provider.name + " (до комиссии)", p + "EditProvider_" + provider.id, "number",
          value ? Number(value.base_amount) : "", 'step="any" min="0"');
      }).join("");
      overlay.innerHTML =
        '<div class="board-edit-card" role="dialog" aria-modal="true" tabindex="-1" aria-labelledby="' + p +
        'EditTitle" style="background:#fff;border-radius:18px;max-width:720px;width:100%;padding:26px 28px;box-shadow:0 24px 70px rgba(23,26,38,.3)">' +
        '<div class="board-edit-header" style="display:flex;align-items:flex-start;justify-content:space-between;margin-bottom:18px;gap:18px">' +
        '<div><h2 class="board-edit-title" id="' + p +
        'EditTitle" style="font-family:\'Space Grotesk\',\'Manrope\',sans-serif;font-size:19px;font-weight:700">' +
        (record ? "Изменить данные" : "Добавить данные") + "</h2>" +
        '<span class="board-edit-subtitle" style="display:none">' +
        (p === "finance" ? "Финансовые показатели и распределение затрат" :
          "Ручные показатели и распределение затрат") +
        "</span></div>" +
        '<button class="board-edit-close" id="' + p +
        'EditClose" type="button" aria-label="Закрыть" style="border:none;background:#F4F5F9;border-radius:9px;width:32px;height:32px;cursor:pointer;font-size:15px;font-weight:700;color:#6B7180">' +
        '<svg width="17" height="17" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>' +
        "</button></div>" +
        '<div class="board-edit-body" style="min-width:0">' +
        '<div class="board-edit-context">' +
        '<div class="board-edit-main-grid" style="display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:16px">' +
        modalInput("Дата", p + "EditDate", "date", record ? record.record_date : new Date().toISOString().slice(0, 10)) +
        '<label class="board-edit-field" for="' + p +
        'EditBuyer" style="display:flex;flex-direction:column;gap:5px;font-size:11.5px;font-weight:700;color:#6B7180">Баер' +
        '<select class="board-edit-select" id="' + p +
        'EditBuyer" style="border:1px solid #ECEEF3;border-radius:9px;padding:9px 11px;font:600 13px Manrope,sans-serif;outline:none">' +
        buyersOptions + "</select></label>" +
        '<label class="board-edit-field board-edit-offer-field" for="' + p +
        'EditOffer" style="display:flex;flex-direction:column;gap:5px;font-size:11.5px;font-weight:700;color:#6B7180">Оффер' +
        '<select class="board-edit-select" id="' + p +
        'EditOffer" style="border:1px solid #ECEEF3;border-radius:9px;padding:9px 11px;font:600 13px Manrope,sans-serif;outline:none;max-width:100%">' +
        offersOptions + "</select></label>" +
        "</div></div>" +
        (state.services.length
          ? '<section class="board-edit-section board-edit-section--services">' +
            '<div class="board-edit-section-heading" style="font-size:11px;font-weight:800;color:#5A5FE0;text-transform:uppercase;letter-spacing:.6px;margin:14px 0 8px">' +
            '<span class="board-edit-section-title">Сервисы</span><span class="board-edit-section-note">Количество инсталлов</span></div>' +
            '<div class="board-edit-grid" style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px">' +
            servicesInputs + "</div></section>"
          : "") +
        (state.providers.length
          ? '<section class="board-edit-section board-edit-section--providers">' +
            '<div class="board-edit-section-heading" style="font-size:11px;font-weight:800;color:#C9821F;text-transform:uppercase;letter-spacing:.6px;margin:14px 0 8px">' +
            '<span class="board-edit-section-title">Агенты и платёжки</span><span class="board-edit-section-note">Сумма до комиссии, USD</span></div>' +
            '<div class="board-edit-grid" style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px">' +
            providersInputs + "</div></section>"
          : "") +
        '<section class="board-edit-section board-edit-section--metrics">' +
        '<div class="board-edit-section-heading" style="font-size:11px;font-weight:800;color:#6B7180;text-transform:uppercase;letter-spacing:.6px;margin:14px 0 8px">' +
        '<span class="board-edit-section-title">Показатели</span><span class="board-edit-section-note">Ручные значения и override</span></div>' +
        '<div class="board-edit-grid board-edit-grid--metrics" style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px">' +
        config.modalMetricInputs(record, modalInput) +
        "</div></section></div>" +
        '<div class="board-edit-footer" style="display:flex;justify-content:flex-end;gap:10px;margin-top:22px">' +
        '<button class="board-edit-button board-edit-cancel" id="' + p +
        'EditCancel" type="button" style="border:1px solid #ECEEF3;background:#fff;border-radius:11px;padding:11px 18px;font:700 13px Manrope,sans-serif;color:#6B7180;cursor:pointer">Отмена</button>' +
        '<button class="board-edit-button board-edit-save" id="' + p +
        'EditSave" type="button" style="border:none;background:#5A5FE0;color:#fff;border-radius:11px;padding:11px 22px;font:700 13px Manrope,sans-serif;cursor:pointer;box-shadow:0 8px 18px rgba(90,95,224,.28)">Сохранить</button>' +
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
      var services = [];
      state.services.forEach(function (service) {
        var value = numberValue(p + "EditService_" + service.id);
        if (value != null && value > 0) {
          services.push({ service_id: service.id, quantity: value });
        }
      });
      var providers = [];
      state.providers.forEach(function (provider) {
        var value = numberValue(p + "EditProvider_" + provider.id);
        if (value != null && value > 0) {
          providers.push({ provider_id: provider.id, base_amount: value });
        }
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
      var tableHead = el("TableHead");
      var table = tableHead && tableHead.closest("table");
      if (table) {
        table.classList.add("celestial-board-table");
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
      await loadRefs();
      renderStructureBar();
      renderHead();
      bindFilters();
      if (config.afterInit) config.afterInit(state, { loadRecords: loadRecords, fail: fail, filterQuery: filterQuery });
      await loadRecords();
    }

    return { init: init, state: state, openEditModal: openEditModal };
  }

  /* ---------- Mediaboard config ---------- */

  var mediaBoard = createBoard({
    prefix: "media",
    endpoint: "/media-records",
    preferenceKey: "mediaboard.structure",
    managePermission: "media.manage",
    funnelTitle: "Воронка",
    funnelColumns: [
      { label: "INST" }, { label: "REG" }, { label: "FTD" }
    ],
    resultColumns: [
      { label: "Revenue" }, { label: "Profit" }, { label: "ROI" }, { label: "CPD" }
    ],
    sumFields: ["installs", "registrations", "ftd", "rent", "spend", "revenue"],
    emptyMessage: "Данные появятся после первой синхронизации Keitaro или ручного ввода",
    leafLabel: function (record) { return record.offer || ""; },
    metricCells: function (sums, opts) {
      function dash(value, formatter) {
        return value == null ? '<span style="color:#C7CAD6">–</span>' : formatter(value);
      }
      var spend = sums.spend == null ? 0 : sums.spend;
      var revenue = sums.revenue == null ? 0 : sums.revenue;
      var profit = revenue - spend;
      var roi = spend > 0 ? profit / spend * 100 : null;
      var cpd = sums.ftd > 0 && spend > 0 ? spend / sums.ftd : null;
      return td(dash(sums.installs, num), { bold: opts.bold }) +
        td(dash(sums.registrations, num), { bold: opts.bold }) +
        td(dash(sums.ftd, num), { bold: opts.bold }) +
        td(dash(sums.rent, money), { bold: opts.bold }) +
        td(dash(sums.spend, money), { bold: opts.bold }) +
        td(dash(sums.revenue, money), { bold: opts.bold }) +
        td(money(profit), { bold: true, color: profit >= 0 ? "#16B57F" : "#D94B61" }) +
        td(percent(roi), { bold: true, color: roi == null ? undefined : roi >= 0 ? "#16B57F" : "#D94B61" }) +
        td(cpd == null ? "–" : money(cpd), { bold: opts.bold });
    },
    modalMetricInputs: function (record, modalInput) {
      return modalInput("INST (инсталлы)", "mediaEditInstalls", "number",
          record && record.installs != null ? record.installs : "", 'step="1" min="0"') +
        modalInput("REG", "mediaEditRegistrations", "number",
          record && record.registrations != null ? record.registrations : "", 'step="1" min="0"') +
        modalInput("FTD", "mediaEditFtd", "number",
          record && record.ftd != null ? record.ftd : "", 'step="1" min="0"') +
        modalInput("Revenue, USD", "mediaEditRevenue", "number",
          record && record.revenue != null ? Number(record.revenue) : "", 'step="any" min="0"') +
        modalInput("SPEND вручную (override)", "mediaEditSpendOverride", "number",
          record && record.spend_override != null ? Number(record.spend_override) : "", 'step="any" min="0"');
    },
    save: async function (form) {
      var payload = {
        record_date: form.record_date,
        buyer_id: form.buyer_id,
        offer_id: form.offer_id,
        installs: form.numberValue("mediaEditInstalls"),
        registrations: form.numberValue("mediaEditRegistrations"),
        ftd: form.numberValue("mediaEditFtd"),
        revenue: form.numberValue("mediaEditRevenue"),
        spend_override: form.numberValue("mediaEditSpendOverride"),
        source: "manual"
      };
      var saved = await api.post("/media-records", payload);
      await api.put("/media-records/" + saved.id + "/values", {
        services: form.services,
        spend_providers: form.providers
      });
    }
  });

  /* ---------- Finance config ---------- */

  var financeBoard = createBoard({
    prefix: "finance",
    endpoint: "/finance-records",
    preferenceKey: "finance.structure",
    managePermission: "finance.manage",
    funnelTitle: "ПП",
    funnelColumns: [{ label: "QUAL" }],
    resultColumns: [
      { label: "Revenue" }, { label: "Profit" }, { label: "ROI" }, { label: "ЗП" }
    ],
    sumFields: ["qual", "rent", "spend", "revenue", "salary"],
    emptyMessage: "Финансовых записей пока нет — добавьте вручную или импортируйте файл",
    leafLabel: function (record) { return record.link || record.offer || ""; },
    extraFilters: function (params) {
      var element = byId("financeFilterLink");
      if (element && element.value) params.push("link=" + encodeURIComponent(element.value));
    },
    resetExtraFilters: function () {
      var element = byId("financeFilterLink");
      if (element) element.value = "";
    },
    metricCells: function (sums, opts) {
      function dash(value, formatter) {
        return value == null ? '<span style="color:#C7CAD6">–</span>' : formatter(value);
      }
      var rent = sums.rent == null ? 0 : sums.rent;
      var spend = sums.spend == null ? 0 : sums.spend;
      var revenue = sums.revenue == null ? 0 : sums.revenue;
      var costs = rent + spend;
      var profit = revenue - costs;
      var roi = costs > 0 ? profit / costs * 100 : null;
      return td(dash(sums.qual, money), { bold: opts.bold }) +
        td(dash(sums.rent, money), { bold: opts.bold }) +
        td(dash(sums.spend, money), { bold: opts.bold }) +
        td(dash(sums.revenue, money), { bold: opts.bold }) +
        td(money(profit), { bold: true, color: profit >= 0 ? "#16B57F" : "#D94B61" }) +
        td(percent(roi), { bold: true, color: roi == null ? undefined : roi >= 0 ? "#16B57F" : "#D94B61" }) +
        td(dash(sums.salary, money), { bold: opts.bold });
    },
    modalMetricInputs: function (record, modalInput) {
      return modalInput("QUAL (доход ПП), USD", "financeEditQual", "number",
          record && record.qual != null ? Number(record.qual) : "", 'step="any" min="0"') +
        modalInput("Revenue, USD", "financeEditRevenue", "number",
          record && record.revenue != null ? Number(record.revenue) : "", 'step="any" min="0"') +
        modalInput("ЗП, USD", "financeEditSalary", "number",
          record && record.salary != null ? Number(record.salary) : "", 'step="any" min="0"') +
        modalInput("SPEND вручную (override)", "financeEditSpendOverride", "number",
          record && record.spend_override != null ? Number(record.spend_override) : "", 'step="any" min="0"') +
        modalInput("Ссылка", "financeEditLink", "text",
          record && record.link ? record.link : "");
    },
    save: async function (form) {
      var linkElement = byId("financeEditLink");
      var payload = {
        record_date: form.record_date,
        buyer_id: form.buyer_id,
        offer_id: form.offer_id,
        link: linkElement && linkElement.value ? linkElement.value : null,
        rent: form.record ? Number(form.record.rent || 0) : 0,
        spend: form.record ? Number(form.record.spend || 0) : 0,
        qual: form.numberValue("financeEditQual") || 0,
        revenue: form.numberValue("financeEditRevenue") || 0,
        salary: form.numberValue("financeEditSalary") || 0,
        spend_override: form.numberValue("financeEditSpendOverride"),
        source: "manual"
      };
      var saved = await api.post("/finance-records", payload);
      await api.put("/finance-records/" + saved.id + "/values", {
        services: form.services,
        spend_providers: form.providers,
        qual: payload.qual,
        spend_override: payload.spend_override
      });
    },
    afterInit: function (state, board) {
      bindFinanceImportExport(state, board);
    },
    onData: function (state) {
      var revenue = 0, costs = 0;
      state.records.forEach(function (record) {
        revenue += Number(record.revenue || 0);
        costs += Number(record.rent || 0) + Number(record.spend || 0);
      });
      var profit = revenue - costs;
      var kpi = {
        financeKpiRevenue: money(revenue),
        financeKpiCosts: money(costs),
        financeKpiProfit: money(profit),
        financeKpiRoi: costs > 0 ? percent(profit / costs * 100) : "–"
      };
      Object.keys(kpi).forEach(function (id) {
        var element = byId(id);
        if (element) element.textContent = kpi[id];
      });
      var profitElement = byId("financeKpiProfit");
      if (profitElement) profitElement.style.color = profit >= 0 ? "#16B57F" : "#D94B61";
    }
  });

  /* ---------- Finance import/export ---------- */

  function bindFinanceImportExport(state, board) {
    var exportCsv = byId("financeExportCsv");
    var exportXlsx = byId("financeExportXlsx");
    var canExport = hasPermission(state.user, "finance.export");
    if (!canExport) {
      if (exportCsv) exportCsv.style.display = "none";
      if (exportXlsx) exportXlsx.style.display = "none";
      exportCsv = exportXlsx = null;
    }
    if (!state.canManage) {
      var hiddenImport = byId("financeImportBtn");
      if (hiddenImport) hiddenImport.style.display = "none";
    }
    function exportUrl(format) {
      return "/api/v1/exports/finance?format=" + format + board.filterQuery();
    }
    if (exportCsv) exportCsv.addEventListener("click", function () {
      window.location.href = exportUrl("csv");
    });
    if (exportXlsx) exportXlsx.addEventListener("click", function () {
      window.location.href = exportUrl("xlsx");
    });
    var importButton = byId("financeImportBtn");
    var importInput = byId("financeImportFile");
    if (!importButton || !importInput) return;
    importButton.addEventListener("click", function () { importInput.click(); });
    importInput.addEventListener("change", async function () {
      var file = importInput.files && importInput.files[0];
      importInput.value = "";
      if (!file) return;
      try {
        var formData = new FormData();
        formData.append("file", file);
        var preview = await api.request("/imports/finance/preview", {
          method: "POST", body: formData
        });
        var errorsText = preview.errors && preview.errors.length
          ? "\nОшибки в строках: " + preview.errors.map(function (e) { return e.row; }).slice(0, 15).join(", ")
          : "";
        var proceed = window.confirm(
          "Файл: " + file.name + "\nВсего строк: " + preview.total_rows +
          "\nВалидных: " + preview.valid_rows + errorsText +
          "\n\nИмпортировать валидные строки?");
        if (!proceed || !preview.valid_rows) return;
        var confirmData = new FormData();
        confirmData.append("file", file);
        var idempotencyKey = "finimp-" + Date.now() + "-" + Math.random().toString(36).slice(2, 10);
        var result = await api.request("/imports/finance", {
          method: "POST",
          body: confirmData,
          headers: { "Idempotency-Key": idempotencyKey }
        });
        toast("Импорт: создано " + result.created + ", обновлено " + result.updated +
          (result.errors && result.errors.length ? ", ошибок " + result.errors.length : ""));
        await board.loadRecords();
      } catch (error) {
        board.fail(error);
      }
    });
  }

  window.CelestialBoard = {
    initMedia: function (user) { return mediaBoard.init(user); },
    initFinance: function (user) { return financeBoard.init(user); }
  };
})();
