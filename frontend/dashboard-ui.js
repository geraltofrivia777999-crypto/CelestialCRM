(function () {
  "use strict";

  var api = window.CelestialAPI;
  if (!api) return;

  /* ---------- helpers ---------- */

  function byId(id) { return document.getElementById(id); }

  function setText(id, value) {
    var element = byId(id);
    if (element) element.textContent = value;
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
  }

  function number(value) {
    return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 })
      .format(Number(value || 0));
  }

  function money(value) {
    return new Intl.NumberFormat("en-US", {
      style: "currency", currency: "USD", maximumFractionDigits: 2
    }).format(Number(value || 0));
  }

  function percent(value) {
    if (value == null || !isFinite(value)) return "—";
    return new Intl.NumberFormat("ru-RU", {
      minimumFractionDigits: 0, maximumFractionDigits: 1
    }).format(Number(value)) + "%";
  }

  function initials(value) {
    return String(value || "?").trim().split(/\s+/).slice(0, 2)
      .map(function (part) { return part.charAt(0).toUpperCase(); }).join("") || "?";
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

  function fail(error) {
    if (error && error.status === 401) {
      window.location.replace("/login.html?next=" + encodeURIComponent(
        window.location.pathname + window.location.search));
      return;
    }
    toast(error && error.message ? error.message : "Ошибка запроса", "error");
  }

  function iso(date) {
    var year = date.getFullYear();
    var month = String(date.getMonth() + 1).padStart(2, "0");
    var day = String(date.getDate()).padStart(2, "0");
    return year + "-" + month + "-" + day;
  }

  function parseIso(value) {
    var parts = String(value).split("-");
    return new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
  }

  /* ---------- period computation ---------- */

  function periodRange(preset) {
    var today = new Date();
    today.setHours(0, 0, 0, 0);
    var to = new Date(today);
    var from = new Date(today);
    if (preset === "today") { /* один день: from и to уже сегодняшние */ }
    else if (preset === "yesterday") {
      from.setDate(from.getDate() - 1);
      to.setDate(to.getDate() - 1);
    }
    else if (preset === "7") from.setDate(from.getDate() - 6);
    else if (preset === "30") from.setDate(from.getDate() - 29);
    else if (preset === "90") from.setDate(from.getDate() - 89);
    else if (preset === "month") from = new Date(today.getFullYear(), today.getMonth(), 1);
    else return { from: null, to: null };
    return { from: iso(from), to: iso(to) };
  }


  function queryString(range, buyerId) {
    var params = [];
    if (range.from) params.push("date_from=" + range.from);
    if (range.to) params.push("date_to=" + range.to);
    if (buyerId) params.push("buyer_id=" + encodeURIComponent(buyerId));
    return params.length ? "?" + params.join("&") : "";
  }

  /* ---------- chart ---------- */

  function buildLinePath(points, mapX, mapY, close) {
    if (!points.length) return "";
    var path = points.map(function (point, index) {
      return (index === 0 ? "M" : "L") + mapX(index).toFixed(1) + " " + mapY(point).toFixed(1);
    }).join(" ");
    if (close && points.length) {
      path += " L" + mapX(points.length - 1).toFixed(1) + " 240 L" + mapX(0).toFixed(1) + " 240 Z";
    }
    return path;
  }

  function renderChart(series) {
    var svg = byId("dashboardChartSvg");
    var labels = byId("dashboardChartLabels");
    if (!svg) return;
    var defs = '<defs><linearGradient id="incFill" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0" stop-color="#B91414" stop-opacity=".16"/>' +
      '<stop offset="1" stop-color="#B91414" stop-opacity="0"/></linearGradient></defs>';
    var grid = "";
    [40, 100, 160, 220].forEach(function (y) {
      grid += '<line x1="0" y1="' + y + '" x2="720" y2="' + y + '" stroke="#F0EBEB" stroke-width="1"/>';
    });
    if (!series || !series.length) {
      svg.innerHTML = defs + grid +
        '<text x="360" y="135" text-anchor="middle" fill="#AFA6A6" font-family="Inter" ' +
        'font-size="14" font-weight="600">Нет данных за выбранный период</text>';
      if (labels) labels.innerHTML = "";
      return;
    }
    var revenue = series.map(function (point) { return Number(point.revenue || 0); });
    var spend = series.map(function (point) { return Number(point.spend || 0); });
    var profit = series.map(function (point) { return Number(point.profit || 0); });
    var all = revenue.concat(spend).concat(profit);
    var maxValue = Math.max.apply(null, all);
    var minValue = Math.min.apply(null, all, 0);
    var top = 24, bottom = 236;
    var span = maxValue - minValue || 1;
    var count = series.length;
    function mapX(index) { return count === 1 ? 360 : index / (count - 1) * 720; }
    function mapY(value) { return bottom - (value - minValue) / span * (bottom - top); }
    var revenueArea = buildLinePath(revenue, mapX, mapY, true);
    var revenueLine = buildLinePath(revenue, mapX, mapY, false);
    var spendLine = buildLinePath(spend, mapX, mapY, false);
    var profitLine = buildLinePath(profit, mapX, mapY, false);
    var lastX = mapX(count - 1);
    var lastY = mapY(revenue[count - 1]);
    svg.innerHTML = defs + grid +
      '<path d="' + revenueArea + '" fill="url(#incFill)"/>' +
      '<path d="' + revenueLine + '" fill="none" stroke="#B91414" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>' +
      '<path d="' + spendLine + '" fill="none" stroke="#F5A524" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>' +
      '<path d="' + profitLine + '" fill="none" stroke="#16B57F" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"/>' +
      '<circle cx="' + lastX.toFixed(1) + '" cy="' + lastY.toFixed(1) + '" r="5" fill="#B91414" stroke="#fff" stroke-width="2.5"/>';
    if (labels) {
      var step = Math.max(1, Math.ceil(count / 7));
      var marks = [];
      for (var index = 0; index < count; index += step) marks.push(series[index].date);
      if (marks[marks.length - 1] !== series[count - 1].date) marks.push(series[count - 1].date);
      labels.innerHTML = marks.map(function (dateText) {
        var parts = String(dateText).split("-");
        var label = parts.length === 3 ? parts[2] + "." + parts[1] : dateText;
        return '<span style="font-size:11.5px;color:#9B9292;font-weight:600">' + escapeHtml(label) + "</span>";
      }).join("");
    }
  }

  function renderHeroSpark(series) {
    var svg = byId("dashboardHeroSpark");
    if (!svg) return;
    var defs = '<defs><linearGradient id="heroFill" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0" stop-color="#fff" stop-opacity=".38"/>' +
      '<stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient></defs>';
    if (!series || !series.length) { svg.innerHTML = defs; return; }
    var revenue = series.map(function (point) { return Number(point.revenue || 0); });
    var maxValue = Math.max.apply(null, revenue);
    var minValue = Math.min.apply(null, revenue);
    var span = maxValue - minValue || 1;
    var count = revenue.length;
    function mapX(index) { return count === 1 ? 160 : index / (count - 1) * 320; }
    function mapY(value) { return 78 - (value - minValue) / span * 62; }
    var line = buildLinePath(revenue, mapX, mapY, false);
    var area = revenue.map(function (value, index) {
      return (index === 0 ? "M" : "L") + mapX(index).toFixed(1) + " " + mapY(value).toFixed(1);
    }).join(" ") + " L" + mapX(count - 1).toFixed(1) + " 90 L" + mapX(0).toFixed(1) + " 90 Z";
    svg.innerHTML = defs +
      '<path d="' + area + '" fill="url(#heroFill)"/>' +
      '<path d="' + line + '" fill="none" stroke="#fff" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>' +
      '<circle cx="' + mapX(count - 1).toFixed(1) + '" cy="' + mapY(revenue[count - 1]).toFixed(1) + '" r="4.5" fill="#fff"/>';
  }

  /* ---------- working offers ---------- */

  var OFFER_PALETTE = [
    { bg: "#FCF1F1", color: "#B91414" },
    { bg: "#E4F7F0", color: "#16B57F" },
    { bg: "#FFF2E0", color: "#E8912B" },
    { bg: "#FDE8EC", color: "#FF1A1A" },
    { bg: "#FCF1F1", color: "#C41616" }
  ];

  /* Виджет показывает не «все активные», а то, что ждёт именно этого человека:
     администратору — нераспределённые офферы, тимлиду — его собственные,
     баеру — те, что тимлид отдал ему в работу. Выборку делает сервер, здесь
     остаётся только назвать её так же, как она называется на доске. */
  var OFFER_WIDGET = {
    free: {
      title: "Оффера",
      empty: "Офферов пока нет — заведите их в разделе «Оффера»"
    },
    active: {
      title: "Ваши оффера",
      empty: "На вас пока нет офферов"
    },
    working: {
      title: "Ваши оффера",
      empty: "На вас пока нет офферов"
    }
  };

  function offerWidgetKind(user) {
    var codes = ((user && user.role && user.role.permissions) || [])
      .map(function (permission) { return permission.code; });
    // Кто видит весь справочник — тому и заголовок общий, остальным «Ваши».
    if (codes.indexOf("*") >= 0 || codes.indexOf("offers.view_all") >= 0) return "free";
    if (codes.indexOf("offers.manage") >= 0) return "active";
    return "working";
  }

  function renderOfferWidgetLabels(user) {
    var copy = OFFER_WIDGET[offerWidgetKind(user)];
    setText("dashboardOffersTitle", copy.title);
  }

  function renderWorkingOffers(offers) {
    var container = byId("dashboardWorkingOffers");
    setText("dashboardOffersCount", (offers.length || 0) + " " + pluralOffers(offers.length));
    if (!container) return;
    if (!offers.length) {
      container.innerHTML =
        '<div style="padding:34px 6px;text-align:center;color:#9B9292;font-size:13px">' +
        escapeHtml(OFFER_WIDGET[offerWidgetKind(state.user)].empty) + "</div>";
      return;
    }
    container.innerHTML = offers.map(function (offer, index) {
      var palette = OFFER_PALETTE[index % OFFER_PALETTE.length];
      var isLast = index === offers.length - 1;
      return '<div style="display:grid;grid-template-columns:1fr auto auto;gap:12px;align-items:center;padding:13px 6px;' +
        (isLast ? "" : "border-bottom:1px solid #F7F4F4") + '">' +
        '<div style="display:flex;align-items:center;gap:11px;min-width:0">' +
        '<div style="width:34px;height:34px;border-radius:10px;flex-shrink:0;background:' + palette.bg +
        ';display:flex;align-items:center;justify-content:center;color:' + palette.color +
        ';font-weight:700;font-size:13px;font-family:Inter">' + escapeHtml(initials(offer.name)) + "</div>" +
        '<div style="min-width:0"><div style="font-weight:700;font-size:13.5px;overflow:hidden;' +
        'text-overflow:ellipsis;white-space:nowrap" title="' + escapeHtml(offer.name) + '">' +
        escapeHtml(offer.name) + "</div>" +
        (offer.cap
          ? '<div style="font-size:11px;color:#9B9292;margin-top:2px;overflow:hidden;' +
            'text-overflow:ellipsis;white-space:nowrap">Капа: ' + escapeHtml(offer.cap) + "</div>"
          : "") +
        "</div></div>" +
        '<span style="font-size:12px;font-weight:700;color:#070505;background:#F7F4F4;padding:4px 9px;border-radius:7px">' +
        escapeHtml(offer.geo || "—") + "</span>" +
        '<span style="font-size:12.5px;color:#6A6161;font-weight:600;width:82px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="' +
        escapeHtml(offer.partner || "") + '">' + escapeHtml(offer.partner || "—") + "</span></div>";
    }).join("");
  }

  function pluralOffers(count) {
    var mod10 = count % 10;
    var mod100 = count % 100;
    if (mod10 === 1 && mod100 !== 11) return "оффер";
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return "оффера";
    return "офферов";
  }

  /* ---------- state & loading ---------- */

  var state = { user: null, buyers: [], data: null, range: { from: null, to: null } };

  function currentRange() {
    var select = byId("dashboardPeriod");
    var preset = select ? select.value : "30";
    if (preset === "custom") {
      var from = byId("dashboardDateFrom");
      var to = byId("dashboardDateTo");
      return {
        from: from && from.value ? from.value : null,
        to: to && to.value ? to.value : null
      };
    }
    return periodRange(preset);
  }

  function renderKpi(data) {
    setText("dashboardRevenue", money(data.revenue));
    setText("dashboardSpend", money(data.spend));
    setText("dashboardProfit", money(data.profit));
    setText("dashboardRoi", percent(data.roi));
    setText("dashboardLeads", number(data.leads));
    setText("dashboardSales", number(data.sales));
    setText("dashboardEpl", data.epl == null ? "—" : money(data.epl));
    var profitElement = byId("dashboardProfit");
    if (profitElement) profitElement.style.color = Number(data.profit) >= 0 ? "#16B57F" : "#FF1A1A";
  }

  async function load() {
    var range = currentRange();
    state.range = range;
    var buyerId = byId("dashboardBuyer") ? byId("dashboardBuyer").value : "";
    var data = await api.get("/dashboard" + queryString(range, buyerId));
    state.data = data;
    renderKpi(data);
    renderChart(data.series || []);
    renderHeroSpark(data.series || []);
    renderWorkingOffers(data.working_offers || []);
  }

  function exportSummary() {
    var data = state.data;
    if (!data) return;
    var rows = [
      ["metric", "value"],
      ["period_from", state.range.from || "all"],
      ["period_to", state.range.to || "all"],
      ["leads", data.leads],
      ["sales", data.sales],
      ["epl", data.epl == null ? "" : data.epl],
      ["revenue", data.revenue],
      ["spend", data.spend],
      ["profit", data.profit],
      ["roi", data.roi == null ? "" : data.roi]
    ];
    var csv = rows.map(function (row) {
      return row.map(function (cell) { return '"' + String(cell).replace(/"/g, '""') + '"'; }).join(",");
    }).join("\r\n");
    var blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var link = document.createElement("a");
    link.href = url;
    link.download = "dashboard-" + (state.range.to || "all") + ".csv";
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
    toast("KPI выгружены в CSV");
  }

  function renderIdentity(user) {
    setText("dashboardUserName", user.name || user.login);
    setText("dashboardUserRole", user.role ? user.role.name : "");
    setText("dashboardUserAvatar", initials(user.name || user.login));
  }

  function bindControls() {
    var period = byId("dashboardPeriod");
    var customRange = byId("dashboardCustomRange");
    if (period) {
      period.addEventListener("change", function () {
        if (customRange) customRange.style.display = period.value === "custom" ? "flex" : "none";
        if (period.value !== "custom") load().catch(fail);
      });
    }
    ["dashboardDateFrom", "dashboardDateTo"].forEach(function (id) {
      var element = byId(id);
      if (element) element.addEventListener("change", function () {
        if (byId("dashboardDateFrom").value && byId("dashboardDateTo").value) load().catch(fail);
      });
    });
    var buyer = byId("dashboardBuyer");
    if (buyer) buyer.addEventListener("change", function () { load().catch(fail); });
    var exportButton = byId("dashboardExport");
    if (exportButton) exportButton.addEventListener("click", exportSummary);
  }

  async function init(user) {
    state.user = user;
    renderIdentity(user);
    renderOfferWidgetLabels(user);
    bindControls();
    try {
      var options = await api.get("/users/options");
      state.buyers = options || [];
      var select = byId("dashboardBuyer");
      if (select) {
        select.innerHTML = '<option value="">Вся команда</option>' + state.buyers.map(function (buyer) {
          return '<option value="' + escapeHtml(buyer.id) + '">' + escapeHtml(buyer.name) + "</option>";
        }).join("");
      }
    } catch (error) { /* фильтр по пользователю не критичен */ }
    await load();
  }

  window.CelestialDashboard = { init: init };
})();
