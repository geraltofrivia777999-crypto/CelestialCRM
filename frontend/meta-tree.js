/*
 * MetaAds v2 · «Обзор» деревом: аккаунт → кампании → адсеты → объявления.
 *
 * Пока это прототип интерфейса: строки берутся из демонстрационного набора,
 * а кнопки только открывают свои окна. Все места, где появятся настоящие
 * данные и действия, помечены TODO и собраны в одном месте — loadTree() и
 * runAction(), чтобы подключение к API не задело верстку.
 *
 * Insts, Regs и Deps приходят из Keitaro и сходятся с кампанией по sub2/sub3/
 * sub4 (имя кампании, адсета и объявления), поэтому в строке хранятся именно
 * имена, а не только id.
 */
(function () {
  "use strict";

  var api = window.CelestialAPI;

  function byId(id) { return document.getElementById(id); }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function notify(options) {
    if (window.CelestialShell && window.CelestialShell.notify) {
      return window.CelestialShell.notify(options);
    }
    window.alert(options.message || options.title);
    return Promise.resolve(true);
  }

  var LEVELS = ["campaign", "adset", "ad"];
  var LEVEL_TITLES = {
    campaign: "Кампания", adset: "Адсет", ad: "Объявление"
  };
  var STATUS_LABELS = {
    ACTIVE: "Active", PAUSED: "Paused", DISABLED: "Disabled", ARCHIVED: "Archived"
  };

  var state = {
    rows: [],
    open: {},
    picked: {},
    hideOff: false,
    hideZero: false,
    sort: "",
    // Пороги подсветки: { GEO: { avg_inst: {green, red}, ... } }.
    heat: { IN: { avg_inst: { green: 0.35, red: 0.5 },
                  avg_reg: { green: 0.8, red: 1.1 },
                  avg_dep: { green: 12, red: 20 } } },
    filters: { agents: null, geo: null }
  };

  /* ---------- числа ---------- */

  function money(value) {
    if (value == null) return "—";
    return "$" + Number(value).toLocaleString("ru-RU", {
      minimumFractionDigits: 2, maximumFractionDigits: 2
    });
  }

  function num(value) {
    if (value == null) return "—";
    return Number(value).toLocaleString("ru-RU");
  }

  function percent(value) {
    if (value == null) return "—";
    return Number(value).toLocaleString("ru-RU", {
      minimumFractionDigits: 2, maximumFractionDigits: 2
    }) + "%";
  }

  function ratio(spend, count) {
    return count ? spend / count : null;
  }

  /* Цвет ячейки AvgInst/AvgReg/AvgDep по порогам своего GEO: меньше зелёного —
     зелёная, больше красного — красная, между ними жёлтая. */
  function heatClass(geo, metric, value) {
    var limits = (state.heat[geo] || {})[metric];
    if (!limits || value == null) return "";
    if (Number(value) <= Number(limits.green)) return " mt-heat mt-heat--good";
    if (Number(value) >= Number(limits.red)) return " mt-heat mt-heat--bad";
    return " mt-heat mt-heat--warn";
  }

  /* ---------- данные ---------- */

  /* TODO(api): заменить на GET /meta/tree?date_from=&date_to= — дерево одним
     ответом: аккаунты с агентом, валютой и таймзоной, внутри кампании, адсеты
     и объявления с числами Meta и подмешанными Insts/Regs/Deps из Keitaro. */
  function loadTree() {
    return Promise.resolve(demoRows());
  }

  /* TODO(api): POST /meta/entities/actions — старт, пауза и дубль уже есть на
     бэкенде, здесь останется передать id и уровень. */
  function runAction(action, row) {
    return notify({
      title: LEVEL_TITLES[row.level] + ": " + row.name,
      message: "Действие «" + action + "» появится вместе с подключением данных."
    });
  }

  function demoChildren(level, parentName, count, base) {
    var rows = [];
    for (var index = 1; index <= count; index += 1) {
      var spend = Math.round((base * (0.6 + index * 0.23)) * 100) / 100;
      var installs = Math.round(spend / (0.3 + index * 0.05));
      var regs = Math.round(installs * 0.45);
      var deps = index % 3 === 0 ? 0 : Math.max(1, Math.round(regs * 0.07));
      rows.push({
        id: parentName + "-" + level + "-" + index,
        level: level,
        name: parentName + (level === "campaign" ? "_" + index : " · " + index),
        geo: index % 4 === 0 ? "BD" : "IN",
        status: index % 3 === 0 ? "PAUSED" : "ACTIVE",
        items: level === "ad" ? 1 : (index % 2 ? 1 : 2),
        impressions: Math.round(spend * 480),
        clicks: Math.round(spend * 3.6),
        insts: installs,
        regs: regs,
        deps: deps,
        spend: spend,
        budget: level === "ad" ? null : (index % 2 ? 30 : 100),
        children: level === "ad" ? [] : demoChildren(
          level === "campaign" ? "adset" : "ad",
          parentName + " · " + index, level === "campaign" ? 2 : 2, base / 2
        )
      });
    }
    return rows;
  }

  function demoRows() {
    return [
      {
        id: "886996937382284",
        level: "account",
        name: "886996937382284",
        account_name: "SPX2 · IN",
        agent: "SPX2",
        status: "ACTIVE",
        currency: "USD",
        gmt: "GMT+3",
        children: demoChildren("campaign", "24_04_IN_SPX_in_8869_intw1.1", 4, 11)
      },
      {
        id: "774100294851122",
        level: "account",
        name: "774100294851122",
        account_name: "Rampage · BD",
        agent: "Rampage",
        status: "PAUSED",
        currency: "USD",
        gmt: "GMT+6",
        children: demoChildren("campaign", "24_05_BD_RMP_bd_7741_intw2.0", 2, 7)
      }
    ];
  }

  /* Числа аккаунта — сумма его кампаний: одна арифметика на всё дерево. */
  function totals(row) {
    if (!row.children || !row.children.length) {
      return {
        impressions: row.impressions || 0, clicks: row.clicks || 0,
        insts: row.insts || 0, regs: row.regs || 0, deps: row.deps || 0,
        spend: row.spend || 0
      };
    }
    return row.children.reduce(function (sum, child) {
      var part = totals(child);
      return {
        impressions: sum.impressions + part.impressions,
        clicks: sum.clicks + part.clicks,
        insts: sum.insts + part.insts,
        regs: sum.regs + part.regs,
        deps: sum.deps + part.deps,
        spend: sum.spend + part.spend
      };
    }, { impressions: 0, clicks: 0, insts: 0, regs: 0, deps: 0, spend: 0 });
  }

  function visibleChildren(row) {
    var agents = state.filters.agents ? state.filters.agents.values() : [];
    var geos = state.filters.geo ? state.filters.geo.values() : [];
    return (row.children || []).filter(function (child) {
      if (state.hideOff && child.status !== "ACTIVE") return false;
      if (state.hideZero && !child.budget) return false;
      if (geos.length && child.geo && geos.indexOf(child.geo) < 0) return false;
      if (agents.length && row.level === "account" && agents.indexOf(row.agent) < 0) return false;
      return true;
    }).sort(function (left, right) {
      if (state.sort === "spend") return (right.spend || 0) - (left.spend || 0);
      if (state.sort === "deps") return (right.deps || 0) - (left.deps || 0);
      if (state.sort === "impressions") return (right.impressions || 0) - (left.impressions || 0);
      if (state.sort === "avg_dep") {
        var a = ratio(left.spend, left.deps);
        var b = ratio(right.spend, right.deps);
        return (a == null ? Infinity : a) - (b == null ? Infinity : b);
      }
      return 0;
    });
  }

  function visibleAccounts() {
    var agents = state.filters.agents ? state.filters.agents.values() : [];
    return state.rows.filter(function (row) {
      if (agents.length && agents.indexOf(row.agent) < 0) return false;
      if (state.hideOff && row.status !== "ACTIVE") return false;
      return true;
    });
  }

  /* ---------- отрисовка ---------- */

  var COLUMNS = [
    "Структура", "Агент / GEO", "Статус", "Валюта / Элементы", "GMT", "Показы",
    "Клики", "CPC", "CPM", "CTR", "Insts", "Regs", "Deps", "AvgInst", "AvgReg",
    "AvgDep", "Спенд", "Бюджет", "Действия"
  ];

  var ICON_CHEVRON = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="m9 6 6 6-6 6"/></svg>';
  var ICON_EYE = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></svg>';
  var ICON_EYE_OFF = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">' +
    '<path d="M10.6 5.1A10.7 10.7 0 0 1 12 5c6.4 0 10 7 10 7a17.6 17.6 0 0 1-2.9 3.8M6.3 6.3C3.6 8 2 12 2 12s3.6 7 10 7c1.9 0 3.5-.6 4.9-1.4"/>' +
    '<path d="M9.9 9.9a3 3 0 0 0 4.2 4.2M3 3l18 18"/></svg>';

  function anyOpen() {
    return Object.keys(state.open).some(function (id) { return state.open[id]; });
  }

  /* Кнопки в шапке заменили отдельные кнопки панели: стрелка у «Структуры»
     сворачивает и разворачивает всё дерево, глазики прячут неактивные строки
     и строки без бюджета. */
  function headButton(kind, on, title, icon, extra) {
    return '<button class="mt-th-btn' + (on ? " is-on" : "") + (extra || "") + '" type="button" ' +
      'data-tree-head="' + kind + '" title="' + escapeHtml(title) + '" aria-label="' +
      escapeHtml(title) + '" aria-pressed="' + (on ? "true" : "false") + '">' + icon + "</button>";
  }

  function renderHead() {
    var open = anyOpen();
    byId("metaTreeHead").innerHTML = "<tr>" + COLUMNS.map(function (title) {
      if (title === "Структура") {
        return '<th><span class="mt-th">' +
          headButton("collapse", false, open ? "Свернуть всё" : "Развернуть всё", ICON_CHEVRON,
            open ? " is-open" : "") + escapeHtml(title) + "</span></th>";
      }
      if (title === "Статус") {
        return '<th><span class="mt-th">' + escapeHtml(title) +
          headButton("hideOff", state.hideOff,
            state.hideOff ? "Показать неактивные" : "Скрыть неактивные",
            state.hideOff ? ICON_EYE_OFF : ICON_EYE) + "</span></th>";
      }
      if (title === "Бюджет") {
        return '<th><span class="mt-th">' + escapeHtml(title) +
          headButton("hideZero", state.hideZero,
            state.hideZero ? "Показать с бюджетом 0" : "Скрыть с бюджетом 0",
            state.hideZero ? ICON_EYE_OFF : ICON_EYE) + "</span></th>";
      }
      return "<th>" + escapeHtml(title) + "</th>";
    }).join("") + "</tr>";
  }

  function statusCell(status) {
    var kind = status === "ACTIVE" ? "active" : status === "PAUSED" ? "paused" : "off";
    return '<span class="mt-status mt-status--' + kind + '">' +
      escapeHtml(STATUS_LABELS[status] || status || "—") + "</span>";
  }

  function toggleButton(row, open) {
    return '<button class="mt-toggle' + (open ? " is-open" : "") + '" type="button" ' +
      'data-tree-toggle="' + escapeHtml(row.id) + '" aria-label="Развернуть">' +
      ICON_CHEVRON + "</button>";
  }

  function metricCells(row, values, geo) {
    var ctr = values.impressions ? values.clicks / values.impressions * 100 : null;
    var cpc = values.clicks ? values.spend / values.clicks : null;
    var cpm = values.impressions ? values.spend / values.impressions * 1000 : null;
    var avgInst = ratio(values.spend, values.insts);
    var avgReg = ratio(values.spend, values.regs);
    var avgDep = ratio(values.spend, values.deps);
    return "<td>" + num(values.impressions) + "</td>" +
      "<td>" + num(values.clicks) + "</td>" +
      "<td>" + money(cpc) + "</td>" +
      "<td>" + money(cpm) + "</td>" +
      "<td>" + percent(ctr) + "</td>" +
      "<td>" + num(values.insts) + "</td>" +
      "<td>" + num(values.regs) + "</td>" +
      "<td>" + num(values.deps) + "</td>" +
      '<td><span class="' + heatClass(geo, "avg_inst", avgInst).trim() + '">' +
      money(avgInst) + "</span></td>" +
      '<td><span class="' + heatClass(geo, "avg_reg", avgReg).trim() + '">' +
      money(avgReg) + "</span></td>" +
      '<td><span class="' + heatClass(geo, "avg_dep", avgDep).trim() + '">' +
      money(avgDep) + "</span></td>" +
      "<td>" + money(values.spend) + "</td>";
  }

  function accountRow(row) {
    var open = !!state.open[row.id];
    var values = totals(row);
    return '<tr class="mt-row mt-row--account" data-tree-row="' + escapeHtml(row.id) + '">' +
      '<td><div class="mt-name">' + toggleButton(row, open) +
      '<span class="mt-title">' + escapeHtml(row.name) + "</span>" +
      '<span class="mt-chip mt-chip--geo">' + escapeHtml(row.account_name || "") +
      "</span></div></td>" +
      '<td><span class="mt-chip mt-chip--agent">' + escapeHtml(row.agent || "—") + "</span></td>" +
      "<td>" + statusCell(row.status) + "</td>" +
      "<td>" + escapeHtml(row.currency || "—") + "</td>" +
      "<td>" + escapeHtml(row.gmt || "—") + "</td>" +
      metricCells(row, values, null) +
      "<td>—</td><td>—</td></tr>";
  }

  function childHeadRow(level) {
    var title = level === "campaign" ? "Кампании" : level === "adset" ? "Адсеты" : "Объявления";
    return '<tr class="mt-row mt-row--head"><td>' + escapeHtml(title) + "</td>" +
      "<td>GEO</td><td>Статус</td><td>Элементы</td><td></td>" +
      "<td>Показы</td><td>Клики</td><td>CPC</td><td>CPM</td><td>CTR</td>" +
      "<td>Insts</td><td>Regs</td><td>Deps</td><td>AvgInst</td><td>AvgReg</td>" +
      "<td>AvgDep</td><td>Спенд</td><td>Бюджет</td><td>Действия</td></tr>";
  }

  function actionCell(row) {
    return '<td><span class="mt-act">' +
      '<button type="button" data-tree-action="start" data-tree-id="' + escapeHtml(row.id) +
      '" title="Старт"><svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor">' +
      '<path d="M7 4.5v15a1 1 0 0 0 1.5.86l12.5-7.5a1 1 0 0 0 0-1.72L8.5 3.64A1 1 0 0 0 7 4.5z"/>' +
      "</svg></button>" +
      '<button type="button" data-tree-action="pause" data-tree-id="' + escapeHtml(row.id) +
      '" title="Пауза"><svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor">' +
      '<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>' +
      "</svg></button>" +
      '<button type="button" data-tree-action="duplicate" data-tree-id="' + escapeHtml(row.id) +
      '" title="Дублировать"><svg width="12" height="12" viewBox="0 0 24 24" fill="none" ' +
      'stroke="currentColor" stroke-width="2"><rect x="9" y="9" width="11" height="11" rx="2"/>' +
      '<path d="M5 15V6a2 2 0 0 1 2-2h8"/></svg></button></span></td>';
  }

  function childRow(row, depth) {
    var open = !!state.open[row.id];
    var values = totals(row);
    var pad = 12 + depth * 18;
    var toggle = row.children && row.children.length ? toggleButton(row, open) :
      '<span style="width:20px;display:inline-block"></span>';
    return '<tr class="mt-row" data-tree-row="' + escapeHtml(row.id) + '">' +
      '<td><div class="mt-name" style="padding-left:' + pad + 'px">' + toggle +
      '<span class="mt-title">' + escapeHtml(row.name) + "</span></div></td>" +
      '<td><span class="mt-chip mt-chip--geo">' + escapeHtml(row.geo || "—") + "</span></td>" +
      "<td>" + statusCell(row.status) + "</td>" +
      "<td>" + (row.items ? row.items + (row.level === "campaign" ? " адсет" : " шт") : "—") +
      "</td><td></td>" +
      metricCells(row, values, row.geo) +
      "<td>" + (row.budget ? money(row.budget) : "—") + "</td>" +
      actionCell(row) + "</tr>";
  }

  function renderRows(rows, depth, into) {
    rows.forEach(function (row) {
      into.push(childRow(row, depth));
      if (state.open[row.id] && row.children && row.children.length) {
        into.push(childHeadRow(row.children[0].level));
        renderRows(visibleChildren(row), depth + 1, into);
      }
    });
  }

  function renderCards() {
    var sum = visibleAccounts().reduce(function (acc, row) {
      var part = totals(row);
      Object.keys(part).forEach(function (key) { acc[key] = (acc[key] || 0) + part[key]; });
      return acc;
    }, {});
    var ctr = sum.impressions ? sum.clicks / sum.impressions * 100 : null;
    var cards = [
      { label: "Total spend", value: money(sum.spend || 0), tone: "spend" },
      { label: "Impressions", value: num(sum.impressions || 0) },
      { label: "Clicks", value: num(sum.clicks || 0) },
      { label: "CTR", value: percent(ctr) },
      { label: "CPM", value: money(sum.impressions ? sum.spend / sum.impressions * 1000 : null) },
      { label: "CPC", value: money(sum.clicks ? sum.spend / sum.clicks : null) },
      { label: "Installs", value: num(sum.insts || 0), tone: "blue" },
      { label: "Registrations", value: num(sum.regs || 0), tone: "blue" },
      { label: "Deposits", value: num(sum.deps || 0), tone: "blue" },
      { label: "Avg install", value: money(ratio(sum.spend, sum.insts)), tone: "avg" },
      { label: "Avg reg", value: money(ratio(sum.spend, sum.regs)), tone: "avg" },
      { label: "Avg deposit", value: money(ratio(sum.spend, sum.deps)), tone: "avg" }
    ];
    byId("metaTreeCards").innerHTML = cards.map(function (card) {
      return '<div class="mt-card' + (card.tone ? " mt-card--" + card.tone : "") + '">' +
        "<span>" + escapeHtml(card.label) + "</span><b>" + card.value + "</b></div>";
    }).join("");
  }

  function render() {
    if (!byId("metaTreeBody")) return;
    renderHead();
    renderCards();
    var accounts = visibleAccounts();
    var html = [];
    accounts.forEach(function (row) {
      html.push(accountRow(row));
      if (state.open[row.id]) {
        html.push(childHeadRow("campaign"));
        renderRows(visibleChildren(row), 1, html);
      }
    });
    byId("metaTreeBody").innerHTML = html.join("") ||
      '<tr><td colspan="19" style="padding:40px;text-align:center;color:#9B9292">' +
      "Под фильтры ничего не подошло</td></tr>";
    var campaigns = accounts.reduce(function (count, row) {
      return count + (row.children || []).length;
    }, 0);
    byId("metaTreeCount").textContent = accounts.length + " кабинетов · " +
      campaigns + " кампаний";
  }

  /* ---------- подсветка ---------- */

  var GEO_CHOICES = ["IN", "BD", "PK", "BR", "MX", "CO", "PE", "AR", "PL", "GB"];
  var HEAT_ROWS = [
    { key: "avg_inst", label: "AvgInst" },
    { key: "avg_reg", label: "AvgReg" },
    { key: "avg_dep", label: "AvgDep" }
  ];

  /* Черновик — массив, чтобы новые блоки вставали в конец, а смена GEO в
     только что добавленном блоке не перетасовывала остальные. */
  var draft = null;

  function emptyLimits() {
    return {
      avg_inst: { green: "", red: "" },
      avg_reg: { green: "", red: "" },
      avg_dep: { green: "", red: "" }
    };
  }

  function heatDraft() {
    return Object.keys(state.heat).map(function (geo) {
      return { geo: geo, fresh: false, limits: JSON.parse(JSON.stringify(state.heat[geo])) };
    });
  }

  function geoOptions() {
    var known = {};
    GEO_CHOICES.concat(collectValues("geo")).forEach(function (geo) { known[geo] = true; });
    return Object.keys(known).sort();
  }

  function freeGeos(except) {
    var used = {};
    draft.forEach(function (block) { used[block.geo] = true; });
    return geoOptions().filter(function (geo) { return geo === except || !used[geo]; });
  }

  function shown(value) {
    return value === "" || value == null ? "—" : String(value).replace(".", ",");
  }

  function middleText(limits) {
    return "<span>" + shown(limits.green) + "</span><i>–</i><span>" +
      shown(limits.red) + "</span>";
  }

  function heatInput(index, key, edge, value) {
    return '<input type="text" inputmode="decimal" data-heat-index="' + index +
      '" data-heat-key="' + key + '" data-heat-edge="' + edge + '" value="' +
      escapeHtml(value == null ? "" : String(value).replace(".", ",")) + '" aria-label="' +
      (edge === "green" ? "Зелёный до" : "Красный от") + '">';
  }

  function heatTitle(block, index) {
    if (!block.fresh) return "<span>" + escapeHtml(block.geo) + "</span>";
    return '<select data-heat-geo="' + index + '" aria-label="GEO">' +
      freeGeos(block.geo).map(function (geo) {
        return '<option value="' + escapeHtml(geo) + '"' + (geo === block.geo ? " selected" : "") +
          ">" + escapeHtml(geo) + "</option>";
      }).join("") + "</select>";
  }

  function renderHeat() {
    byId("metaTreeHeatBody").innerHTML = draft.map(function (block, index) {
      return '<div class="mt-heat-block"><header>' + heatTitle(block, index) +
        '<button class="mt-heat-drop" type="button" data-heat-drop="' + index +
        '">Убрать</button></header>' +
        '<div class="mt-heat-grid"><span></span>' +
        '<span class="mt-heat-bar mt-heat-bar--good" title="Зелёный"></span>' +
        '<span class="mt-heat-bar mt-heat-bar--warn" title="Жёлтый"></span>' +
        '<span class="mt-heat-bar mt-heat-bar--bad" title="Красный"></span>' +
        HEAT_ROWS.map(function (row) {
          var limits = block.limits[row.key] || { green: "", red: "" };
          return "<b>" + row.label + "</b>" +
            '<label class="mt-heat-cell mt-heat-cell--good">' +
            heatInput(index, row.key, "green", limits.green) + "</label>" +
            '<div class="mt-heat-cell mt-heat-cell--warn" data-heat-mid="' + index + "-" +
            row.key + '">' + middleText(limits) + "</div>" +
            '<label class="mt-heat-cell mt-heat-cell--bad">' +
            heatInput(index, row.key, "red", limits.red) + "</label>";
        }).join("") + "</div></div>";
    }).join("");
    byId("metaTreeHeatAdd").disabled = !freeGeos(null).length;
  }

  function openHeat() {
    draft = heatDraft();
    renderHeat();
    byId("metaTreeHeatModal").classList.add("is-open");
  }

  function closeHeat() {
    byId("metaTreeHeatModal").classList.remove("is-open");
    draft = null;
  }

  function limitValue(value) {
    var text = String(value == null ? "" : value).replace(",", ".").trim();
    return text === "" || isNaN(Number(text)) ? "" : Number(text);
  }

  function saveHeat() {
    // TODO(api): сохранять пороги в /me/preferences/meta.tree.heat.
    var next = {};
    draft.forEach(function (block) {
      var limits = {};
      HEAT_ROWS.forEach(function (row) {
        var edge = block.limits[row.key] || {};
        limits[row.key] = { green: limitValue(edge.green), red: limitValue(edge.red) };
      });
      next[block.geo] = limits;
    });
    state.heat = next;
    closeHeat();
    render();
  }

  /* ---------- события ---------- */

  function toggleRow(id) {
    if (state.open[id]) delete state.open[id];
    else state.open[id] = true;
    render();
  }

  function findRow(rows, id) {
    for (var index = 0; index < rows.length; index += 1) {
      if (rows[index].id === id) return rows[index];
      var found = findRow(rows[index].children || [], id);
      if (found) return found;
    }
    return null;
  }

  function collectValues(key) {
    var found = {};
    function walk(rows) {
      rows.forEach(function (row) {
        if (row[key]) found[row[key]] = true;
        walk(row.children || []);
      });
    }
    walk(state.rows);
    return Object.keys(found).sort();
  }

  function bindFilters() {
    var factory = window.CelestialBoard && window.CelestialBoard.multiFilter;
    if (!factory) return;
    state.filters.agents = factory(byId("metaTreeAgents"), render);
    state.filters.geo = factory(byId("metaTreeGeo"), render);
    state.filters.agents.setItems(collectValues("agent").map(function (name) {
      return { value: name, label: name };
    }));
    state.filters.geo.setItems(collectValues("geo").map(function (name) {
      return { value: name, label: name };
    }));
  }

  function expandAll(rows) {
    rows.forEach(function (row) {
      if (row.children && row.children.length) {
        state.open[row.id] = true;
        expandAll(row.children);
      }
    });
  }

  function bind() {
    byId("metaTreeBody").addEventListener("click", function (event) {
      var toggle = event.target.closest("[data-tree-toggle]");
      if (toggle) return toggleRow(toggle.getAttribute("data-tree-toggle"));
      var action = event.target.closest("[data-tree-action]");
      if (!action) return;
      var row = findRow(state.rows, action.getAttribute("data-tree-id"));
      if (row) runAction(action.getAttribute("data-tree-action"), row);
    });
    byId("metaTreeHead").addEventListener("click", function (event) {
      var button = event.target.closest("[data-tree-head]");
      if (!button) return;
      var kind = button.getAttribute("data-tree-head");
      if (kind === "collapse") {
        if (anyOpen()) state.open = {};
        else expandAll(state.rows);
      } else {
        state[kind] = !state[kind];
      }
      render();
    });
    byId("metaTreeSort").addEventListener("change", function (event) {
      state.sort = event.target.value;
      render();
    });
    byId("metaTreeHeatOpen").addEventListener("click", openHeat);
    byId("metaTreeHeatClose").addEventListener("click", closeHeat);
    byId("metaTreeHeatCancel").addEventListener("click", closeHeat);
    byId("metaTreeHeatModal").addEventListener("click", function (event) {
      if (event.target === event.currentTarget) closeHeat();
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && draft) closeHeat();
    });
    byId("metaTreeHeatAdd").addEventListener("click", function () {
      var free = freeGeos(null);
      if (!free.length) return;
      draft.push({ geo: free[0], fresh: true, limits: emptyLimits() });
      renderHeat();
      var blocks = byId("metaTreeHeatBody").children;
      if (blocks.length) blocks[blocks.length - 1].scrollIntoView({ block: "nearest" });
    });
    byId("metaTreeHeatBody").addEventListener("click", function (event) {
      var drop = event.target.closest("[data-heat-drop]");
      if (!drop) return;
      draft.splice(Number(drop.getAttribute("data-heat-drop")), 1);
      renderHeat();
    });
    byId("metaTreeHeatBody").addEventListener("change", function (event) {
      var picker = event.target.closest("[data-heat-geo]");
      if (!picker) return;
      draft[Number(picker.getAttribute("data-heat-geo"))].geo = picker.value;
      renderHeat();
    });
    byId("metaTreeHeatBody").addEventListener("input", function (event) {
      var field = event.target;
      if (!field.hasAttribute("data-heat-index")) return;
      var index = Number(field.getAttribute("data-heat-index"));
      var key = field.getAttribute("data-heat-key");
      var limits = draft[index].limits[key];
      limits[field.getAttribute("data-heat-edge")] = field.value;
      var middle = byId("metaTreeHeatBody").querySelector(
        '[data-heat-mid="' + index + "-" + key + '"]');
      if (middle) middle.innerHTML = middleText(limits);
    });
    byId("metaTreeHeatSave").addEventListener("click", saveHeat);
    // Период: календарь тот же, что в остальных разделах.
    var to = byId("metaTreeTo");
    if (to) {
      to.addEventListener("change", function () {
        // TODO(api): перезагрузить дерево за выбранный период.
        render();
      });
    }
  }

  function defaultPeriod() {
    var today = window.CelestialTime && window.CelestialTime.today
      ? window.CelestialTime.today() : new Date();
    var iso = function (date) {
      return date.getFullYear() + "-" + ("0" + (date.getMonth() + 1)).slice(-2) +
        "-" + ("0" + date.getDate()).slice(-2);
    };
    if (byId("metaTreeFrom") && !byId("metaTreeFrom").value) {
      byId("metaTreeFrom").value = iso(today);
      byId("metaTreeTo").value = iso(today);
      if (window.CelestialDateRange) window.CelestialDateRange.refresh();
    }
  }

  async function init() {
    if (!byId("metaTreeBody")) return;
    defaultPeriod();
    state.rows = await loadTree();
    state.open[state.rows[0] ? state.rows[0].id : ""] = true;
    bind();
    bindFilters();
    render();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { init(); });
  } else {
    init();
  }

  window.CelestialMetaTree = { render: render, state: state };
})();
