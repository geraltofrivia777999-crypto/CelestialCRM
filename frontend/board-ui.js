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

  var DASH = '<span style="color:#C7CAD6">–</span>';

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
    return "<td" + (opts.title ? ' title="' + escapeHtml(opts.title) + '"' : "") +
      ' class="cs-cell" style="text-align:right;padding:12px;font-family:Space Grotesk;font-size:12.5px;' +
      "font-weight:" + (opts.bold ? "800" : "600") + ";border-bottom:1px solid #F2F3F8;white-space:nowrap;" +
      (opts.color ? "color:" + opts.color + ";" : "") + (opts.extra || "") + '">' + content + "</td>";
  }

  /* ---------- board factory ---------- */

  var LEAF_PAGE = 200;

  // Column groups in board order. `services` and `providers` are filled from the
  // workspace catalog; the rest come from each board's `metricColumns`.
  var COLUMN_GROUPS = [
    { key: "services", label: "Сервисы", background: "#F1F2FF", color: "#5A5FE0", border: "#E1E3F5" },
    { key: "providers", label: "Агенты и платёжки", background: "#FFF6E9", color: "#C9821F", border: "#F2E5CC" },
    { key: "funnel", label: null, background: "#E9F8F1", color: "#16B57F", border: "#D2EEE1" },
    { key: "costs", label: "Затраты", background: "#F4F5F9", color: "#6B7180", border: "#E4E6EF" },
    { key: "result", label: "Результат", background: "#1F2231", color: "#fff", border: "#1F2231" }
  ];

  function defaultDisplay() {
    return {
      compact: true,
      dense: false,
      groups: { services: true, providers: true, funnel: true, costs: true, result: true }
    };
  }

  function signTone(value) {
    return value >= 0 ? "#16B57F" : "#D94B61";
  }

  function amount(value) {
    return value == null ? 0 : Number(value);
  }

  function ensureViewStyles() {
    if (byId("celestialViewStyles")) return;
    var style = document.createElement("style");
    style.id = "celestialViewStyles";
    style.textContent =
      ".cs-view{position:relative;flex-shrink:0}" +
      ".cs-view-button{display:inline-flex;align-items:center;gap:7px;border:1px solid #E1E4ED;" +
      "background:#fff;border-radius:11px;padding:8px 14px;font:700 12.5px 'Manrope',sans-serif;" +
      "color:#5A5FE0;cursor:pointer;transition:border-color .18s,background .18s}" +
      ".cs-view-button:hover{border-color:#BFC3F0;background:#F8F9FF}" +
      '.cs-view-button[aria-expanded="true"]{border-color:#8589E9;background:#F1F2FF}' +
      // Anchored to the button's right edge, which is itself right-aligned in the card —
      // that keeps the panel on screen at every width without flipping sides.
      ".cs-view-panel{position:absolute;top:calc(100% + 8px);right:0;z-index:10050;" +
      "width:min(290px,calc(100vw - 32px));" +
      "background:#fff;border:1px solid #E7E9F1;border-radius:14px;padding:14px;" +
      "box-shadow:0 18px 44px rgba(31,34,49,.18)}" +
      ".cs-view-panel[hidden]{display:none}" +
      ".cs-view-row{display:flex;align-items:center;justify-content:space-between;gap:10px;" +
      "margin-bottom:11px}" +
      ".cs-view-row--stack{display:block;margin-bottom:0}" +
      ".cs-view-label{font:700 11px 'Manrope',sans-serif;color:#8A8FA3;text-transform:uppercase;" +
      "letter-spacing:.5px}" +
      ".cs-seg{display:inline-flex;background:#F4F5F9;border-radius:9px;padding:2px}" +
      ".cs-seg-button{border:none;background:transparent;border-radius:7px;padding:6px 11px;" +
      "font:700 11.5px 'Manrope',sans-serif;color:#8A8FA3;cursor:pointer;transition:background .16s,color .16s}" +
      '.cs-seg-button[aria-pressed="true"]{background:#fff;color:#171A26;' +
      "box-shadow:0 1px 3px rgba(31,34,49,.12)}" +
      ".cs-view-groups{display:grid;gap:7px;margin-top:9px}" +
      ".cs-view-check{display:flex;align-items:center;gap:9px;font:600 12.5px 'Manrope',sans-serif;" +
      "color:#3A3F4F;cursor:pointer}" +
      ".cs-view-check input{width:15px;height:15px;accent-color:#5A5FE0;cursor:pointer}";
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
      'tr[data-open="0"] > td .cs-arrow{transform:rotate(-90deg)}';
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

    /* The same filters as an object, so a node's own values can override the bar's. */
    function filterParams() {
      var params = {};
      filterQuery().replace(/^&/, "").split("&").forEach(function (pair) {
        if (!pair) return;
        var parts = pair.split("=");
        params[parts[0]] = decodeURIComponent(parts[1] || "");
      });
      return params;
    }

    function queryString(params) {
      return Object.keys(params).filter(function (key) {
        return params[key] !== "" && params[key] != null;
      }).map(function (key) {
        return key + "=" + encodeURIComponent(params[key]);
      }).join("&");
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
      var groups = COLUMN_GROUPS.map(function (group) {
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
        services: state.services.map(function (service) {
          return { label: service.name, kind: "num", bucket: "services", id: service.id };
        }),
        providers: state.providers.map(function (provider) {
          return { label: provider.name, kind: "money", bucket: "providers", id: provider.id };
        })
      };
      layout = COLUMN_GROUPS.map(function (group) {
        return Object.assign({}, group, {
          label: group.label || config.funnelTitle,
          columns: catalog[group.key] || byGroup[group.key] || []
        });
      }).filter(function (group) {
        return state.display.groups[group.key] !== false && group.columns.length;
      });
    }

    function renderHead() {
      var head = el("TableHead");
      if (!head) return;
      buildLayout();
      var row1 = "<tr>" +
        '<th rowspan="2" class="cs-head-structure" style="position:sticky;left:0;top:0;z-index:5;background:#F8F9FC;text-align:left;padding:14px 16px;border-bottom:1px solid #E7E9F1;border-right:1px solid #E7E9F1;font-size:11px;font-weight:700;color:#8A8FA3;text-transform:uppercase;letter-spacing:.6px">Структура</th>' +
        layout.map(function (group) {
          return groupTh(group.label, group.columns.length, group.background, group.color, group.border);
        }).join("") +
        (state.canManage ? '<th rowspan="2" style="position:sticky;top:0;z-index:4;background:#F8F9FC;border-bottom:1px solid #E7E9F1;width:44px"></th>' : "") +
        "</tr>";
      var row2 = "<tr>" +
        layout.map(function (group, groupIndex) {
          return group.columns.map(function (column, index) {
            return subTh(column.label,
              index === 0 && groupIndex === 0 ? ";border-left:1px solid #EEF0F7" : "");
          }).join("");
        }).join("") +
        "</tr>";
      head.innerHTML = row1 + row2;
    }

    function columnCount() {
      return layout.reduce(function (total, group) {
        return total + group.columns.length;
      }, 1) + (state.canManage ? 1 : 0);
    }

    /* ----- aggregation ----- */

    function newAggregate() {
      return {
        services: {}, providers: {},
        sums: {}, count: 0, records: 0
      };
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
          var value = column.bucket
            ? source[column.bucket][column.id]
            : column.get(source.sums);
          var formatted = formatCell(column.kind, value, compact);
          var numeric = value == null ? null : Number(value);
          cells += td(formatted.text, {
            bold: opts.bold || column.bold,
            title: formatted.title,
            color: column.tone && numeric != null ? column.tone(numeric) : undefined,
            extra: index === 0 && groupIndex === 0 ? "border-left:1px solid #F0F1F7;" : ""
          });
        });
      });
      return cells;
    }

    function nodePath(prefixPath, key) { return prefixPath + "|" + key; }

    function noticeRow(depth, owner, content) {
      return '<tr data-owner="' + escapeHtml(owner) + '" style="background:#fff">' +
        '<td colspan="' + columnCount() + '" style="padding:10px 16px 10px ' +
        (16 + depth * 22) + 'px;border-bottom:1px solid #F2F3F8;font-size:11.5px;' +
        'font-weight:700;color:#8A8FA3">' + content + "</td></tr>";
    }

    function renderNodeRows(node, depth, path, output) {
      var isLeafLevel = !node.order.length;
      // A leaf-level node hides raw records that are not loaded yet, so its arrow tracks
      // a separate flag: group nodes default to open, record lists default to closed.
      var isOpen = isLeafLevel ? !!state.leavesOpen[path] : !state.collapsed[path];
      var arrow = '<svg class="cs-arrow" width="14" height="14" viewBox="0 0 24 24" fill="none">' +
        '<path d="m6 9 6 6 6-6" stroke="' +
        (depth === 0 ? "#5A5FE0" : "#8A8FA3") + '" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>';
      var background = depth === 0 ? "#F5F6FF" : "#fff";
      var count = node.aggregate.records;
      var label = '<div style="display:flex;align-items:center;gap:9px">' + arrow +
        '<span style="font-weight:' + (depth === 0 ? "800" : "700") + ';font-size:' +
        (depth === 0 ? "13.5px" : "12.5px") + '">' + escapeHtml(node.name) + "</span>" +
        '<span style="font-size:10.5px;font-weight:700;color:#A2A7B5">' +
        escapeHtml(LEVELS[node.level].label) + "</span>" +
        (isLeafLevel
          ? '<span style="font-size:10.5px;font-weight:700;color:#C7CAD6">' +
            num(count) + " " + recordWord(count) + "</span>"
          : "") +
        "</div>";
      output.push('<tr data-node="' + escapeHtml(path) + '"' +
        (isLeafLevel ? ' data-leaf-node="1"' : "") +
        ' data-open="' + (isOpen ? "1" : "0") +
        '" style="background:' + background + ';cursor:pointer">' +
        '<td style="position:sticky;left:0;z-index:2;background:' + background +
        ';padding:12px 16px 12px ' + (16 + depth * 22) +
        'px;border-bottom:1px solid #EDEFF6;border-right:1px solid #E7E9F1">' + label + "</td>" +
        metricCells({ services: node.aggregate.services, providers: node.aggregate.providers, sums: node.aggregate.sums }, { bold: depth === 0 }) +
        (state.canManage ? td("", {}) : "") +
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

    function recordWord(count) {
      var tail = count % 100;
      if (tail > 10 && tail < 20) return "записей";
      switch (count % 10) {
        case 1: return "запись";
        case 2: case 3: case 4: return "записи";
        default: return "записей";
      }
    }

    function renderLeafRows(node, depth, path, output) {
      var leaf = state.leaves[path];
      if (!leaf) {
        // Nothing fetched yet: the placeholder only matters once the node is opened.
        if (state.leavesOpen[path]) output.push(noticeRow(depth, path, "Загружаю записи…"));
        return;
      }
      if (leaf.error) {
        output.push(noticeRow(depth, path, '<span style="color:#D94B61">' +
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
          'style="border:1px solid #E1E4ED;background:#F8F9FC;border-radius:9px;padding:6px 13px;' +
          "font:700 11.5px Manrope,sans-serif;color:#5A5FE0;cursor:pointer\">Показать ещё · " +
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
        '<span style="width:6px;height:6px;border-radius:50%;background:#C7CAD6;flex-shrink:0"></span>' +
        '<span style="font-size:12px;font-weight:600;color:#6B7180">' + escapeHtml(record.record_date) + "</span>" +
        '<span style="font-size:11px;color:#A2A7B5;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:150px">' +
        escapeHtml(config.leafLabel(record)) + "</span></div>";
      return '<tr data-owner="' + escapeHtml(owner || "") + '" style="background:#fff">' +
        '<td style="position:sticky;left:0;z-index:2;background:#fff;padding:10px 16px 10px ' +
        (16 + depth * 22) + 'px;border-bottom:1px solid #F2F3F8;border-right:1px solid #E7E9F1">' + label + "</td>" +
        metricCells({ services: recordServices, providers: recordProviders, sums: sums }, { bold: false, leaf: true, record: record }) +
        (state.canManage
          ? td('<button data-edit="' + escapeHtml(record.id) + '" title="Изменить данные" style="border:none;background:transparent;cursor:pointer;padding:2px">' +
              '<svg width="14" height="14" viewBox="0 0 24 24" fill="none"><path d="M4 20h4L19.5 8.5a2.1 2.1 0 0 0-3-3L5 17v3Z" stroke="#8A8FA3" stroke-width="2" stroke-linejoin="round"/></svg></button>', {})
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
      var count = el("ResultCount");
      if (count) {
        count.textContent = state.truncated
          ? "Показано " + num(state.groups.length) + " групп (срез ограничен) · записей: " + num(state.recordCount)
          : "Групп: " + num(state.groups.length) + " · записей: " + num(state.recordCount);
      }
      if (!state.groups.length) {
        currentTree = null;
        body.innerHTML = '<tr><td colspan="' + columnCount() +
          '" style="padding:44px 24px;text-align:center;color:#A2A7B5;font-size:13px">' +
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
        api.getAll("/offers"),
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

    async function loadRecords() {
      // One request for the whole board: the server returns rows already summed per
      // buyer × offer, which is every grouping the structure bar can ask for. The raw
      // records stay on the server until a branch is opened.
      var query = queryString(filterParams());
      var data = await api.get(config.groupsEndpoint + (query ? "?" + query : ""));
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
      ensureBoardTableStyles();
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
      bindViewControls();
      if (table && state.display.dense) {
        table.classList.add("celestial-board-table--dense");
      }
      renderHead();
      bindFilters();
      bindTableEvents();
      if (config.afterInit) config.afterInit(state, { loadRecords: loadRecords, fail: fail, filterQuery: filterQuery });
      await loadRecords();
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
    metricColumns: [
      { group: "funnel", label: "INST", kind: "num", get: function (s) { return s.installs; } },
      { group: "funnel", label: "REG", kind: "num", get: function (s) { return s.registrations; } },
      { group: "funnel", label: "FTD", kind: "num", get: function (s) { return s.ftd; } },
      { group: "costs", label: "RENT", kind: "money", get: function (s) { return s.rent; } },
      { group: "costs", label: "SPEND", kind: "money", get: function (s) { return s.spend; } },
      { group: "result", label: "Revenue", kind: "money", get: function (s) { return s.revenue; } },
      {
        group: "result", label: "Profit", kind: "money", bold: true, tone: signTone,
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
    sumFields: ["installs", "registrations", "ftd", "rent", "spend", "revenue"],
    emptyMessage: "Данные появятся после первой синхронизации Keitaro или ручного ввода",
    leafLabel: function (record) { return record.offer || ""; },
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
    groupsEndpoint: "/finance-records/groups",
    preferenceKey: "finance.structure",
    displayPreferenceKey: "finance.display",
    managePermission: "finance.manage",
    funnelTitle: "ПП",
    metricColumns: [
      { group: "funnel", label: "QUAL", kind: "money", get: function (s) { return s.qual; } },
      { group: "costs", label: "RENT", kind: "money", get: function (s) { return s.rent; } },
      { group: "costs", label: "SPEND", kind: "money", get: function (s) { return s.spend; } },
      { group: "result", label: "Revenue", kind: "money", get: function (s) { return s.revenue; } },
      {
        group: "result", label: "Profit", kind: "money", bold: true, tone: signTone,
        get: function (s) {
          return amount(s.revenue) - (amount(s.rent) + amount(s.spend));
        }
      },
      {
        group: "result", label: "ROI", kind: "percent", bold: true, tone: signTone,
        get: function (s) {
          var costs = amount(s.rent) + amount(s.spend);
          return costs > 0 ? (amount(s.revenue) - costs) / costs * 100 : null;
        }
      },
      { group: "result", label: "ЗП", kind: "money", get: function (s) { return s.salary; } }
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
    onData: function (state, tree) {
      // The root aggregate already covers every matching record, group by group.
      var sums = tree ? tree.aggregate.sums : {};
      var revenue = Number(sums.revenue || 0);
      var costs = Number(sums.rent || 0) + Number(sums.spend || 0);
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
