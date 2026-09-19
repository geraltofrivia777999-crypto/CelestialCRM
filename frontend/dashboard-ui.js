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
    var today = window.CelestialTime.today();
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

  /* Полоса слева под подписи шкалы: без неё цифры налезали бы на линии. */
  var PLOT_LEFT = 62;
  var PLOT_RIGHT = 720;
  /* Ширина картинки берётся у самого блока, а не из фиксированного viewBox:
     при 720 на 260 и растянутой на всю ширину рамке браузер вписывал график
     по высоте и центрировал его, оставляя пустые поля слева и справа. */
  var CHART_HEIGHT = 260;
  var lastChartSeries = null;

  function chartWidth(svg) {
    var box = svg.getBoundingClientRect();
    return Math.max(420, Math.round(box.width || PLOT_RIGHT));
  }

  /* Короткая подпись суммы: на шкале важен порядок величины, а не центы.
     «$86.7K» читается с одного взгляда, «$86,666.00» — нет. */
  function axisMoney(value) {
    var sign = value < 0 ? "-" : "";
    var abs = Math.abs(value);
    if (abs >= 1000000) return sign + "$" + trimZero(abs / 1000000) + "M";
    if (abs >= 1000) return sign + "$" + trimZero(abs / 1000) + "K";
    return sign + "$" + trimZero(abs);
  }

  function trimZero(value) {
    var text = value >= 100 || value === Math.round(value)
      ? String(Math.round(value))
      : value.toFixed(1);
    return text.replace(".0", "");
  }

  /* Круглый шаг шкалы: 1, 2 или 5 на своём порядке. Деления вида «17 333»
     формально верны, но по ним ничего не прикинуть на глаз. */
  function niceStep(rough) {
    var power = Math.pow(10, Math.floor(Math.log(rough) / Math.LN10));
    var scaled = rough / power;
    var step = scaled <= 1 ? 1 : scaled <= 2 ? 2 : scaled <= 5 ? 5 : 10;
    return step * power;
  }

  /* Границы и деления шкалы. Ноль всегда попадает в диапазон: без него линия
     расхода у нуля висела бы над краем и казалась бы отрицательной. */
  function axisScale(minValue, maxValue) {
    var low = Math.min(minValue, 0);
    var high = Math.max(maxValue, 0);
    if (low === high) high = low + 1;
    var step = niceStep((high - low) / 4);
    var from = Math.floor(low / step) * step;
    var to = Math.ceil(high / step) * step;
    var ticks = [];
    // Ограничение на случай странных данных: бесконечный цикл дороже кривой шкалы.
    for (var value = from; value <= to + step / 2 && ticks.length < 12; value += step) {
      ticks.push(Math.abs(value) < step / 1000 ? 0 : value);
    }
    return { min: from, max: to, ticks: ticks };
  }

  function buildLinePath(points, mapX, mapY, baseline) {
    if (!points.length) return "";
    var path = points.map(function (point, index) {
      return (index === 0 ? "M" : "L") + mapX(index).toFixed(1) + " " + mapY(point).toFixed(1);
    }).join(" ");
    // Заливка замыкается на нижнюю линию сетки, а не на дно картинки: иначе
    // она выходила бы за шкалу и висела ниже последнего деления.
    if (baseline != null) {
      path += " L" + mapX(points.length - 1).toFixed(1) + " " + baseline.toFixed(1) +
        " L" + mapX(0).toFixed(1) + " " + baseline.toFixed(1) + " Z";
    }
    return path;
  }

  /* Пустая сетка до появления данных: четыре линии на всю ширину без подписей —
     подписывать нечего, пока не известен диапазон. */
  function emptyGrid(right) {
    return [40, 100, 160, 220].map(function (y) {
      return '<line x1="' + PLOT_LEFT + '" y1="' + y + '" x2="' + right +
        '" y2="' + y + '" stroke="#F0EBEB" stroke-width="1"/>';
    }).join("");
  }

  function renderChart(series) {
    var svg = byId("dashboardChartSvg");
    var labels = byId("dashboardChartLabels");
    if (!svg) return;
    lastChartSeries = series;
    /* Первый рендер может прийтись на момент, когда блок ещё не разложен и
       ширина меряется неверно, — тогда перерисовываем на следующем кадре. */
    if (!svg.getBoundingClientRect().width && window.requestAnimationFrame) {
      window.requestAnimationFrame(function () { renderChart(series); });
    }
    var width = chartWidth(svg);
    var right = width - 8;
    svg.setAttribute("viewBox", "0 0 " + width + " " + CHART_HEIGHT);
    // Подписи дат стоят под своими точками, поэтому их поля равны полям графика.
    if (labels) {
      labels.style.paddingLeft = PLOT_LEFT + "px";
      labels.style.paddingRight = (width - right) + "px";
    }
    var defs = '<defs><linearGradient id="incFill" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0" stop-color="#B91414" stop-opacity=".16"/>' +
      '<stop offset="1" stop-color="#B91414" stop-opacity="0"/></linearGradient></defs>';
    var top = 24, bottom = 236;
    if (!series || !series.length) {
      svg.innerHTML = defs + emptyGrid(right) +
        '<text x="' + ((PLOT_LEFT + right) / 2) + '" y="135" text-anchor="middle" ' +
        'fill="#AFA6A6" font-family="Inter" font-size="14" font-weight="600">' +
        "Нет данных за выбранный период</text>";
      if (labels) labels.innerHTML = "";
      return;
    }
    var revenue = series.map(function (point) { return Number(point.revenue || 0); });
    var spend = series.map(function (point) { return Number(point.spend || 0); });
    var profit = series.map(function (point) { return Number(point.profit || 0); });
    var all = revenue.concat(spend).concat(profit);
    var scale = axisScale(Math.min.apply(null, all), Math.max.apply(null, all));
    var span = scale.max - scale.min || 1;
    var count = series.length;
    function mapX(index) {
      var plot = right - PLOT_LEFT;
      return count === 1 ? PLOT_LEFT + plot / 2 : PLOT_LEFT + index / (count - 1) * plot;
    }
    function mapY(value) { return bottom - (value - scale.min) / span * (bottom - top); }
    // Сетка рисуется по делениям шкалы, а не по четырём заданным высотам:
    // иначе подписи стояли бы не на линиях, а между ними.
    var grid = scale.ticks.map(function (value) {
      var y = mapY(value);
      var zero = value === 0 && scale.min < 0;
      return '<line x1="' + PLOT_LEFT + '" y1="' + y.toFixed(1) + '" x2="' + right +
        '" y2="' + y.toFixed(1) + '" stroke="' + (zero ? "#E0D8D8" : "#F0EBEB") +
        '" stroke-width="1"/>' +
        '<text x="' + (PLOT_LEFT - 10) + '" y="' + (y + 4).toFixed(1) + '" text-anchor="end" ' +
        'fill="#9B9292" font-family="Inter" font-size="11" font-weight="600">' +
        escapeHtml(axisMoney(value)) + "</text>";
    }).join("");
    var revenueArea = buildLinePath(revenue, mapX, mapY, mapY(scale.min));
    var revenueLine = buildLinePath(revenue, mapX, mapY, null);
    var spendLine = buildLinePath(spend, mapX, mapY, null);
    var profitLine = buildLinePath(profit, mapX, mapY, null);
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
    var line = buildLinePath(revenue, mapX, mapY, null);
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

  function renderWorkingOffers(offers, total) {
    var container = byId("dashboardWorkingOffers");
    var count = total == null ? (offers.length || 0) : Number(total) || 0;
    setText("dashboardOffersCount", count + " " + pluralOffers(count));
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
    renderWorkingOffers(data.working_offers || [], data.working_offers_total);
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

  /* Ширина графика зависит от окна, поэтому после изменения размера его
     нужно перерисовать — иначе он остаётся в старом viewBox. */
  var resizeTimer = null;
  window.addEventListener("resize", function () {
    if (!lastChartSeries) return;
    if (resizeTimer) window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(function () {
      renderChart(lastChartSeries);
    }, 150);
  });

  window.CelestialDashboard = { init: init };
})();
