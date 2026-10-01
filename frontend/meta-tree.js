/*
 * MetaAds v2 · «Обзор» деревом: аккаунт → кампании → адсеты → объявления.
 *
 * Расход и показы — Meta; клики, уникальные клики, лиды и продажи — Keitaro.
 * Сопоставление по sub2/sub3/sub4 и часовому поясу рекламного кабинета.
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
  var API_LEVELS = { campaign: "campaigns", adset: "adsets", ad: "ads" };
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
    busy: false,
    menu: null,
    hideOff: false,
    hideZero: false,
    sort: "",
    heat: {},
    filters: { agents: null, geo: null },
    availableGeos: [],
    attribution: {},
    keitaroUnavailable: false,
    loading: false,
    requestId: 0,
    bound: false
  };

  /* ---------- числа ---------- */

  function money(value, currency) {
    if (value == null) return "—";
    try {
      return new Intl.NumberFormat("ru-RU", {
        style: "currency", currency: currency || "USD",
        minimumFractionDigits: 2, maximumFractionDigits: 2
      }).format(Number(value));
    } catch (error) { return Number(value).toFixed(2) + " " + (currency || ""); }
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
    if (!limits || value == null || limits.green === "" || limits.red === "") return "";
    if (Number(value) <= Number(limits.green)) return " mt-heat mt-heat--good";
    if (Number(value) >= Number(limits.red)) return " mt-heat mt-heat--bad";
    return " mt-heat mt-heat--warn";
  }

  /* ---------- данные ---------- */

  async function loadTree() {
    var from = byId("metaTreeFrom").value;
    var to = byId("metaTreeTo").value;
    var query = "?date_from=" + encodeURIComponent(from) + "&date_to=" + encodeURIComponent(to);
    var geos = state.filters.geo ? state.filters.geo.values() : [];
    geos.forEach(function (geo) { query += "&geo=" + encodeURIComponent(geo); });
    return api.get("/meta/tree" + query);
  }

  var ACTIONS = {
    start: {
      title: "Запустить", label: "Запустить",
      warn: "Объекты начнут откручиваться и тратить бюджет."
    },
    pause: { title: "Поставить на паузу", label: "Пауза" },
    duplicate: {
      title: "Дублировать в FB", label: "Дублировать",
      warn: "Копии создаются вместе с вложенными объектами и сразу стоят на паузе."
    },
    "delete": {
      title: "Удалить в FB", label: "Удалить", danger: true,
      warn: "Объекты удалятся в Facebook безвозвратно — восстановить их не получится."
    }
  };

  function askConfirm(options) {
    if (window.CelestialShell && window.CelestialShell.confirm) {
      return window.CelestialShell.confirm(options);
    }
    return Promise.resolve(window.confirm(options.message || options.title));
  }

  function actionable(rows) {
    return rows.filter(function (row) { return row && row.external_id && API_LEVELS[row.level]; });
  }

  function objectsNoun(count) {
    var tens = count % 100;
    var ones = count % 10;
    if (tens > 10 && tens < 20) return count + " объектов";
    if (ones === 1) return count + " объект";
    if (ones > 1 && ones < 5) return count + " объекта";
    return count + " объектов";
  }

  function levelsOf(rows) {
    var found = {};
    rows.forEach(function (row) { found[row.level] = true; });
    return Object.keys(found);
  }

  /* Бюджет и название меняются в окнах meta-ui.js — тех же, что в Meta Ads.
     Они работают на одном уровне, поэтому смешанный выбор не открываем. */
  function openEditor(kind, rows) {
    if (kind === "budget") rows = rows.filter(function (row) { return row.level !== "ad"; });
    if (!rows.length) return;
    var levels = levelsOf(rows);
    if (levels.length > 1) {
      notify({
        title: "Отметьте один уровень",
        message: "Бюджет и название меняются у объектов одного уровня: только кампании, " +
          "только адсеты или только объявления."
      });
      return;
    }
    var meta = window.CelestialMeta;
    if (!meta || !meta.openEntityEditor) {
      notify({ title: "Окно недоступно", message: "Модуль Meta Ads ещё не загрузился." });
      return;
    }
    meta.openEntityEditor(kind, API_LEVELS[levels[0]], rows.map(function (row) {
      return { id: row.external_id, name: row.name };
    }), reload);
  }

  async function runAction(action, rows) {
    rows = actionable(rows);
    if (!rows.length || state.busy) return;
    if (action === "budget" || action === "rename") return openEditor(action, rows);
    var meta = ACTIONS[action];
    var names = rows.slice(0, 5).map(function (row) { return "«" + row.name + "»"; });
    if (rows.length > 5) names.push("и ещё " + (rows.length - 5));
    if (!(await askConfirm({
      title: meta.title + (rows.length > 1 ? ": " + objectsNoun(rows.length) : "") + "?",
      message: names.join(", ") + (meta.warn ? "\n\n" + meta.warn : ""),
      confirmLabel: meta.label,
      danger: !!meta.danger
    }))) return;
    var groups = {};
    rows.forEach(function (row) {
      (groups[row.level] = groups[row.level] || []).push(row);
    });
    var failures = [];
    var done = 0;
    state.busy = true;
    renderBulk();
    try {
      for (var level in groups) {
        var group = groups[level];
        var result = await api.post("/meta/entities/actions", {
          level: API_LEVELS[level], action: action,
          items: group.map(function (row) { return { id: row.external_id }; })
        });
        (result.results || []).forEach(function (item) {
          var row = group.find(function (entry) { return entry.external_id === String(item.id); });
          if (item.ok) {
            done += 1;
            if (action === "delete" && row) delete state.picked[row.id];
          } else {
            failures.push("«" + (row ? row.name : item.id) + "»: " + (item.error || "ошибка"));
          }
        });
      }
    } catch (error) {
      failures.push(error.message || String(error));
    } finally {
      state.busy = false;
    }
    await reload();
    if (!failures.length) return;
    notify({
      title: done ? "Готово частично: " + done + " из " + rows.length : "Meta не выполнила действие",
      message: failures.slice(0, 8).join("\n") +
        (failures.length > 8 ? "\nи ещё " + (failures.length - 8) : "")
    });
  }

  function totals(row) {
    return row;
  }

  function visibleChildren(row) {
    var agents = state.filters.agents ? state.filters.agents.values() : [];
    return (row.children || []).filter(function (child) {
      if (state.hideOff && child.status !== "ACTIVE") return false;
      if (state.hideZero && child.level !== "ad" && !child.budget) return false;
      if (state.filters.geo && state.filters.geo.values().length &&
          !(child.geos || []).length && !visibleChildren(child).length) return false;
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
      if (state.filters.geo && state.filters.geo.values().length &&
          !(row.geos || []).length && !visibleChildren(row).length) return false;
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

  var ICON_SLIDERS = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" ' +
    'stroke="currentColor" stroke-width="2.2" stroke-linecap="round">' +
    '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/>' +
    '<circle cx="10" cy="17" r="2"/></svg>';
  var ICON_MORE = '<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor">' +
    '<circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>';
  var ICON_PLAY = '<svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor">' +
    '<path d="M7 4.5v15a1 1 0 0 0 1.5.86l12.5-7.5a1 1 0 0 0 0-1.72L8.5 3.64A1 1 0 0 0 7 4.5z"/></svg>';
  var ICON_PAUSE = '<svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor">' +
    '<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/></svg>';
  var HEAT_COLUMNS = { AvgInst: true, AvgReg: true, AvgDep: true };

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
      if (HEAT_COLUMNS[title]) {
        // Подсветка настраивается прямо из заголовков колонок, которые она красит.
        return '<th><button class="mt-th-link" type="button" data-tree-head="heat" ' +
          'title="Настроить подсветку">' + escapeHtml(title) + ICON_SLIDERS + "</button></th>";
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
      "<td>" + money(cpc, row.currency) + "</td>" +
      "<td>" + money(cpm, row.currency) + "</td>" +
      "<td>" + percent(ctr) + "</td>" +
      "<td>" + num(values.insts) + "</td>" +
      "<td>" + num(values.regs) + "</td>" +
      "<td>" + num(values.deps) + "</td>" +
      '<td><span class="' + heatClass(geo, "avg_inst", avgInst).trim() + '">' +
      money(avgInst, row.currency) + "</span></td>" +
      '<td><span class="' + heatClass(geo, "avg_reg", avgReg).trim() + '">' +
      money(avgReg, row.currency) + "</span></td>" +
      '<td><span class="' + heatClass(geo, "avg_dep", avgDep).trim() + '">' +
      money(avgDep, row.currency) + "</span></td>" +
      "<td>" + money(values.spend, row.currency) + "</td>";
  }

  function accountRow(row) {
    var open = !!state.open[row.id];
    var values = totals(row);
    var agent = row.connection_id
      ? '<button class="mt-chip mt-chip--agent mt-chip--link" type="button" data-tree-agent="' +
        escapeHtml(row.connection_id) + '" title="Открыть подключение">' +
        escapeHtml(row.agent || "—") + "</button>"
      : '<span class="mt-chip mt-chip--agent">' + escapeHtml(row.agent || "—") + "</span>";
    return '<tr class="mt-row mt-row--account" data-tree-row="' + escapeHtml(row.id) + '">' +
      '<td><div class="mt-name"><span class="mt-check-gap"></span>' + toggleButton(row, open) +
      '<span class="mt-title">' + escapeHtml(row.name) + "</span>" +
      '<span class="mt-chip mt-chip--geo">' + escapeHtml(row.account_name || "") +
      "</span></div></td>" +
      "<td>" + agent + "</td>" +
      "<td>" + statusCell(row.status) + "</td>" +
      "<td>" + escapeHtml(row.currency || "—") + "</td>" +
      "<td>" + escapeHtml(row.gmt || "—") + "</td>" +
      metricCells(row, values, null) +
      '<td>—</td><td><span class="mt-act"><button type="button" data-tree-account-menu="' +
      escapeHtml(row.id) + '" title="Действия с кабинетом" aria-label="Действия с кабинетом" ' +
      'aria-haspopup="menu">' + ICON_MORE + "</button></span></td></tr>";
  }

  /* Галочка в строке «Кампании / Адсеты / Объявления» отмечает всю группу. */
  function childHeadRow(parent, level, depth) {
    var title = level === "campaign" ? "Кампании" : level === "adset" ? "Адсеты" : "Объявления";
    var group = actionable(visibleChildren(parent));
    var picked = group.filter(function (row) { return state.picked[row.id]; }).length;
    var box = group.length
      ? '<input class="mt-check" type="checkbox" data-tree-pick-group="' + escapeHtml(parent.id) +
        '"' + (picked && picked === group.length ? " checked" : "") +
        (picked && picked < group.length ? " data-indeterminate" : "") +
        ' aria-label="Отметить все: ' + escapeHtml(title) + '">'
      : '<span class="mt-check-gap"></span>';
    // Отступ как у строк группы — галочка встаёт в одну колонку с их галочками.
    return '<tr class="mt-row mt-row--head"><td><div class="mt-name" style="padding-left:' +
      (12 + depth * 18) + 'px">' + box + "<span>" + escapeHtml(title) +
      "</span></div></td>" +
      "<td>GEO</td><td>Статус</td><td>Элементы</td><td></td>" +
      "<td>Показы</td><td>Клики</td><td>CPC</td><td>CPM</td><td>CTR</td>" +
      "<td>Insts</td><td>Regs</td><td>Deps</td><td>AvgInst</td><td>AvgReg</td>" +
      "<td>AvgDep</td><td>Спенд</td><td>Бюджет</td><td>Действия</td></tr>";
  }

  function actionCell(row) {
    var id = escapeHtml(row.id);
    return '<td><span class="mt-act">' +
      '<button type="button" data-tree-action="start" data-tree-id="' + id +
      '" title="Старт" aria-label="Старт">' + ICON_PLAY + "</button>" +
      '<button type="button" data-tree-action="pause" data-tree-id="' + id +
      '" title="Пауза" aria-label="Пауза">' + ICON_PAUSE + "</button>" +
      '<button type="button" data-tree-menu="' + id + '" title="Ещё" aria-label="Ещё" ' +
      'aria-haspopup="menu">' + ICON_MORE + "</button></span></td>";
  }

  function budgetCell(row) {
    if (row.budget == null) return "<td>—</td>";
    return '<td><button class="mt-budget" type="button" data-tree-budget="' + escapeHtml(row.id) +
      '" title="Изменить бюджет в FB">' + money(row.budget, row.currency) + "</button></td>";
  }

  function childRow(row, depth) {
    var open = !!state.open[row.id];
    var values = totals(row);
    var pad = 12 + depth * 18;
    var toggle = row.children && row.children.length ? toggleButton(row, open) :
      '<span style="width:20px;display:inline-block"></span>';
    var picked = !!state.picked[row.id];
    var box = row.external_id
      ? '<input class="mt-check" type="checkbox" data-tree-pick="' + escapeHtml(row.id) + '"' +
        (picked ? " checked" : "") + ' aria-label="Отметить ' + escapeHtml(row.name) + '">'
      : '<span class="mt-check-gap"></span>';
    return '<tr class="mt-row' + (picked ? " is-picked" : "") + '" data-tree-row="' +
      escapeHtml(row.id) + '">' +
      '<td><div class="mt-name" style="padding-left:' + pad + 'px">' + box + toggle +
      '<span class="mt-title">' + escapeHtml(row.name) + "</span></div></td>" +
      '<td><span class="mt-chip mt-chip--geo">' + escapeHtml(row.geo || "—") + "</span></td>" +
      "<td>" + statusCell(row.status) + "</td>" +
      "<td>" + (row.items ? row.items + (row.level === "campaign" ? " адсет" : " шт") : "—") +
      "</td><td></td>" +
      metricCells(row, values, row.geo) +
      budgetCell(row) + actionCell(row) + "</tr>";
  }

  function renderRows(rows, depth, into) {
    rows.forEach(function (row) {
      into.push(childRow(row, depth));
      if (state.open[row.id] && row.children && row.children.length) {
        into.push(childHeadRow(row, row.children[0].level, depth + 1));
        renderRows(visibleChildren(row), depth + 1, into);
      }
    });
  }

  function renderCards() {
    var accounts = visibleAccounts();
    var currencies = {};
    accounts.forEach(function (row) { currencies[row.currency || "USD"] = true; });
    var currencyNames = Object.keys(currencies);
    var oneCurrency = currencyNames.length === 1 ? currencyNames[0] : null;
    var sum = accounts.reduce(function (acc, row) {
      var part = totals(row);
      if (part.clicks != null) acc.hasKt = true;
      ["impressions", "clicks", "insts", "regs", "spend"].forEach(function (key) {
        acc[key] = (acc[key] || 0) + (part[key] || 0);
      });
      if (part.deps != null) {
        acc.deps = (acc.deps || 0) + part.deps;
        acc.hasDeps = true;
      }
      return acc;
    }, {});
    var ctr = sum.impressions ? sum.clicks / sum.impressions * 100 : null;
    var cards = [
      { label: "Total spend", value: oneCurrency ? money(sum.spend || 0, oneCurrency) : "—", tone: "spend" },
      { label: "Impressions", value: num(sum.impressions || 0) },
      { label: "Clicks", value: sum.hasKt ? num(sum.clicks || 0) : "—" },
      { label: "CTR", value: percent(ctr) },
      { label: "CPM", value: oneCurrency ? money(sum.impressions ? sum.spend / sum.impressions * 1000 : null, oneCurrency) : "—" },
      { label: "CPC", value: oneCurrency ? money(sum.clicks ? sum.spend / sum.clicks : null, oneCurrency) : "—" },
      { label: "Insts (KT unique)", value: sum.hasKt ? num(sum.insts || 0) : "—", tone: "blue" },
      { label: "Regs (KT leads)", value: sum.hasKt ? num(sum.regs || 0) : "—", tone: "blue" },
      { label: "Deposits", value: sum.hasDeps ? num(sum.deps) : "—", tone: "blue" },
      { label: "Avg install", value: oneCurrency ? money(ratio(sum.spend, sum.insts), oneCurrency) : "—", tone: "avg" },
      { label: "Avg reg", value: oneCurrency ? money(ratio(sum.spend, sum.regs), oneCurrency) : "—", tone: "avg" },
      { label: "Avg deposit", value: oneCurrency && sum.hasDeps ? money(ratio(sum.spend, sum.deps), oneCurrency) : "—", tone: "avg" }
    ];
    byId("metaTreeCards").innerHTML = cards.map(function (card) {
      return '<div class="mt-card' + (card.tone ? " mt-card--" + card.tone : "") + '">' +
        "<span>" + escapeHtml(card.label) + "</span><b>" + card.value + "</b></div>";
    }).join("");
    var note = "Clicks, Insts (уникальные клики), Regs (лиды), Deps (продажи) — Keitaro по sub2/sub3/sub4 и TZ кабинета.";
    if (currencyNames.length > 1) note += " Денежный итог по разным валютам не суммируется.";
    if (state.attribution.ambiguous) note += " Неоднозначных строк: " + state.attribution.ambiguous + ". Добавьте уникальный ID кабинета в ссылку.";
    if (state.attribution.unmatched) note += " Без совпадения: " + state.attribution.unmatched + ".";
    if ((state.attribution.missing_timezones || []).length) note += " У части кабинетов не задан корректный TZ.";
    if (state.keitaroUnavailable) note += " Keitaro временно недоступен: его метрики не показаны.";
    byId("metaTreeNote").textContent = note;
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
        html.push(childHeadRow(row, "campaign", 1));
        renderRows(visibleChildren(row), 1, html);
      }
    });
    byId("metaTreeBody").innerHTML = html.join("") ||
      '<tr><td colspan="19" style="padding:40px;text-align:center;color:#9B9292">' +
      (state.loading ? "Загружаем данные…" : state.rows.length ?
        "Под фильтры ничего не подошло" : "Кабинетов пока нет или данные не загружены") + "</td></tr>";
    var campaigns = accounts.reduce(function (count, row) {
      return count + (row.children || []).length;
    }, 0);
    byId("metaTreeCount").textContent = accounts.length + " кабинетов · " +
      campaigns + " кампаний";
    Array.prototype.forEach.call(
      byId("metaTreeBody").querySelectorAll("[data-indeterminate]"),
      function (box) { box.indeterminate = true; }
    );
    renderBulk();
  }

  /* ---------- отмеченные строки и панель действий (в строке фильтров) ---------- */

  function pickedRows() {
    return Object.keys(state.picked).map(function (id) {
      return findRow(state.rows, id);
    }).filter(Boolean);
  }

  function treeVisible() {
    var body = byId("metaTreeBody");
    return !!(body && body.offsetParent);
  }

  function ensureBulk() {
    if (byId("metaTreeBulk")) return;
    var bar = document.createElement("div");
    bar.className = "mt-bulk";
    bar.id = "metaTreeBulk";
    bar.setAttribute("role", "toolbar");
    bar.setAttribute("aria-label", "Действия с отмеченными");
    bar.innerHTML = '<span class="mt-bulk__count" id="metaTreeBulkCount"></span>' +
      '<span class="mt-bulk__sep"></span>' +
      '<button class="mt-bulk__btn" type="button" data-bulk="start">' + ICON_PLAY + "Старт</button>" +
      '<button class="mt-bulk__btn" type="button" data-bulk="pause">' + ICON_PAUSE + "Пауза</button>" +
      '<button class="mt-bulk__btn" type="button" data-bulk="budget">Бюджет</button>' +
      '<button class="mt-bulk__btn" type="button" data-bulk="more" aria-haspopup="menu">' +
      ICON_MORE + "Ещё</button>" +
      '<span class="mt-bulk__sep"></span>' +
      '<button class="mt-bulk__btn mt-bulk__btn--clear" type="button" data-bulk="clear">' +
      "Снять выделение</button>";
    // Панель живёт в строке фильтров, сразу за сортировкой.
    var host = byId("metaTreeSort");
    host.parentNode.insertBefore(bar, host.nextSibling);
    bar.addEventListener("click", function (event) {
      var button = event.target.closest("[data-bulk]");
      if (!button || button.disabled) return;
      var kind = button.getAttribute("data-bulk");
      if (kind === "clear") {
        state.picked = {};
        closeMenu();
        return render();
      }
      if (kind === "more") return toggleMenu(button, { rows: pickedRows() }, false);
      closeMenu();
      runAction(kind, pickedRows());
    });
  }

  function renderBulk() {
    ensureBulk();
    var rows = pickedRows();
    var bar = byId("metaTreeBulk");
    var open = rows.length > 0 && treeVisible();
    bar.classList.toggle("is-open", open);
    if (!open) closeMenu();
    byId("metaTreeBulkCount").textContent = "Выбрано: " + rows.length;
    var levels = levelsOf(rows.filter(function (row) { return row.level !== "ad"; }));
    Array.prototype.forEach.call(bar.querySelectorAll("[data-bulk]"), function (button) {
      var kind = button.getAttribute("data-bulk");
      var off = state.busy && kind !== "clear";
      if (kind === "budget") {
        off = off || levels.length !== 1;
        button.title = levels.length > 1 ? "Бюджет меняется у объектов одного уровня" :
          !levels.length ? "У объявлений нет бюджета" : "";
      }
      button.disabled = off;
    });
  }

  /* ---------- меню «⋮» ---------- */

  var ENTITY_MENU = [
    { action: "rename", label: "Переименовать в FB" },
    { action: "duplicate", label: "Дублировать в FB" },
    { action: "delete", label: "Удалить в FB", danger: true }
  ];
  var NO_CARD_API = "Meta не даёт привязывать карты через API";
  var ACCOUNT_MENU = [
    { action: "rename", label: "Переименовать" },
    { action: "card", label: "Привязать банковскую карту", off: NO_CARD_API },
    { action: "bm-card", label: "Привязать карту БМа", off: NO_CARD_API },
    { action: "pixel", label: "Создать пиксель" },
    { action: "spend_cap", label: "Установить лимит затрат" }
  ];

  function ensureMenu() {
    if (byId("metaTreeMenu")) return;
    var menu = document.createElement("div");
    menu.className = "mt-menu";
    menu.id = "metaTreeMenu";
    menu.setAttribute("role", "menu");
    menu.hidden = true;
    document.body.appendChild(menu);
    menu.addEventListener("click", function (event) {
      var item = event.target.closest("[data-menu-action]");
      if (!item || item.disabled || !state.menu) return;
      var context = state.menu;
      closeMenu();
      if (context.account) openAccountDialog(item.getAttribute("data-menu-action"), context.account);
      else runAction(item.getAttribute("data-menu-action"), context.rows);
    });
  }

  /* `context`: { rows } — объекты структуры, { account } — строка кабинета. */
  function toggleMenu(anchor, context, above) {
    ensureMenu();
    if (state.menu && state.menu.anchor === anchor) return closeMenu();
    closeMenu();
    var items = context.account ? ACCOUNT_MENU : ENTITY_MENU;
    if (!context.account && !actionable(context.rows).length) return;
    var menu = byId("metaTreeMenu");
    menu.innerHTML = items.map(function (item) {
      return '<button type="button" role="menuitem" data-menu-action="' + item.action + '"' +
        (item.danger ? " data-danger" : "") + (item.off ? ' disabled title="' +
        escapeHtml(item.off) + '"' : "") + ">" + escapeHtml(item.label) + "</button>";
    }).join("");
    state.menu = { anchor: anchor, rows: context.rows || [], account: context.account || null };
    menu.hidden = false;
    anchor.setAttribute("aria-expanded", "true");
    var rect = anchor.getBoundingClientRect();
    var width = menu.offsetWidth;
    var height = menu.offsetHeight;
    var left = Math.max(8, Math.min(rect.right - width, window.innerWidth - width - 8));
    var top = above || rect.bottom + height + 8 > window.innerHeight
      ? rect.top - height - 6 : rect.bottom + 6;
    menu.style.left = left + "px";
    menu.style.top = Math.max(8, top) + "px";
  }

  function closeMenu() {
    var menu = byId("metaTreeMenu");
    if (!state.menu || !menu) return;
    if (state.menu.anchor) state.menu.anchor.setAttribute("aria-expanded", "false");
    state.menu = null;
    menu.hidden = true;
  }

  /* ---------- действия с кабинетом ---------- */

  var ACCOUNT_DIALOGS = {
    rename: { title: "Переименовать кабинет", save: "Сохранить" },
    pixel: { title: "Создать пиксель", save: "Создать" },
    spend_cap: { title: "Лимит затрат", save: "Сохранить" }
  };

  var accountDialog = null;

  function ensureAccountModal() {
    if (byId("metaTreeAccountModal")) return;
    var modal = document.createElement("div");
    modal.className = "mt-modal";
    modal.id = "metaTreeAccountModal";
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-labelledby", "metaTreeAccountTitle");
    modal.innerHTML = '<div class="mt-modal__card mt-modal__card--narrow">' +
      '<div class="mt-modal__head"><div style="min-width:0">' +
      '<h2 id="metaTreeAccountTitle" class="mt-modal__title"></h2>' +
      '<div class="mt-modal__sub" id="metaTreeAccountSub"></div></div>' +
      '<button class="mt-modal__x" type="button" data-account-close aria-label="Закрыть">' +
      '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/>' +
      "</svg></button></div>" +
      '<div class="mt-modal__body" id="metaTreeAccountBody"></div>' +
      '<div class="mt-modal__foot"><span class="mt-foot-extra" id="metaTreeAccountExtra"></span>' +
      '<button class="mt-btn" type="button" data-account-close>Отмена</button>' +
      '<button class="mt-primary" type="button" id="metaTreeAccountSave"></button></div></div>';
    document.body.appendChild(modal);
    modal.addEventListener("click", function (event) {
      if (event.target === modal || event.target.closest("[data-account-close]")) {
        return closeAccountDialog();
      }
      var extra = event.target.closest("[data-cap-action]");
      if (extra) saveAccountDialog(extra.getAttribute("data-cap-action"));
    });
    byId("metaTreeAccountSave").addEventListener("click", function () {
      saveAccountDialog("set");
    });
    modal.addEventListener("keydown", function (event) {
      if (event.key === "Enter" && event.target.tagName === "INPUT") saveAccountDialog("set");
    });
  }

  function openAccountDialog(kind, account) {
    if (!ACCOUNT_DIALOGS[kind]) return;
    ensureAccountModal();
    accountDialog = {
      kind: kind, account: account, busy: false, error: "",
      loading: kind !== "rename", billing: null,
      value: kind === "rename" ? (account.account_name || "") : ""
    };
    byId("metaTreeAccountTitle").textContent = ACCOUNT_DIALOGS[kind].title;
    byId("metaTreeAccountSub").textContent = account.name +
      (account.account_name ? " · " + account.account_name : "");
    byId("metaTreeAccountModal").classList.add("is-open");
    renderAccountDialog();
    if (kind !== "rename") loadAccountBilling(accountDialog);
  }

  function closeAccountDialog() {
    accountDialog = null;
    var modal = byId("metaTreeAccountModal");
    if (modal) modal.classList.remove("is-open");
  }

  async function loadAccountBilling(dialog) {
    try {
      var billing = await api.get("/meta/accounts/" + dialog.account.account_id + "/billing");
      if (accountDialog !== dialog) return;
      dialog.billing = billing;
    } catch (error) {
      if (accountDialog !== dialog) return;
      dialog.error = error.message || String(error);
    }
    dialog.loading = false;
    renderAccountDialog();
  }

  function renderAccountDialog() {
    var dialog = accountDialog;
    if (!dialog) return;
    var body = byId("metaTreeAccountBody");
    var extra = byId("metaTreeAccountExtra");
    var save = byId("metaTreeAccountSave");
    save.textContent = dialog.busy ? "Сохраняем…" : ACCOUNT_DIALOGS[dialog.kind].save;
    save.disabled = dialog.busy || dialog.loading;
    extra.innerHTML = "";
    var error = dialog.error
      ? '<div class="mt-form-error" role="alert">' + escapeHtml(dialog.error) + "</div>" : "";
    if (dialog.loading) {
      body.innerHTML = '<div class="mt-note" style="padding:18px 0;text-align:center">' +
        "Получаем данные из Meta…</div>";
      return;
    }
    var lock = dialog.busy ? " disabled" : "";
    var billing = dialog.billing || {};
    var currency = billing.currency || dialog.account.currency || "USD";
    if (dialog.kind === "rename") {
      body.innerHTML = '<label class="mt-field"><span>Название</span>' +
        '<input class="meta-control" id="metaTreeAccountInput" maxlength="300" value="' +
        escapeHtml(dialog.value) + '"' + lock + "></label>" + error;
    } else if (dialog.kind === "pixel") {
      var pixels = billing.pixels || [];
      body.innerHTML = (pixels.length
        ? '<div class="mt-field"><span>Пиксели кабинета</span><div class="mt-pixels">' +
          pixels.map(function (pixel) {
            return "<div><b>" + escapeHtml(pixel.name || "Без названия") + "</b><i>" +
              escapeHtml(pixel.id) + "</i></div>";
          }).join("") + "</div></div>"
        : "") +
        '<label class="mt-field"><span>Название нового пикселя</span>' +
        '<input class="meta-control" id="metaTreeAccountInput" maxlength="120" value="' +
        escapeHtml(dialog.value) + '"' + lock + "></label>" + error;
    } else {
      var hasCap = billing.spend_cap != null;
      body.innerHTML = '<div class="mt-stats">' +
        "<div><span>Потрачено</span><b>" + money(billing.amount_spent || 0, currency) + "</b></div>" +
        "<div><span>Лимит</span><b>" + (hasCap ? money(billing.spend_cap, currency) : "Без лимита") +
        "</b></div></div>" +
        '<label class="mt-field"><span>Новый лимит, ' + escapeHtml(currency) + "</span>" +
        '<input class="meta-control" id="metaTreeAccountInput" type="text" inputmode="decimal" ' +
        'value="' + escapeHtml(dialog.value) + '"' + lock + "></label>" + error;
      if (hasCap && !dialog.error) {
        extra.innerHTML = '<button class="mt-btn" type="button" data-cap-action="reset"' + lock +
          ">Обнулить потраченное</button>" +
          '<button class="mt-btn mt-btn--danger" type="button" data-cap-action="delete"' + lock +
          ">Снять лимит</button>";
      }
    }
    var input = byId("metaTreeAccountInput");
    if (input) {
      input.addEventListener("input", function () { dialog.value = input.value; });
      if (!dialog.busy) input.focus();
    }
  }

  async function saveAccountDialog(mode) {
    var dialog = accountDialog;
    if (!dialog || dialog.busy || dialog.loading) return;
    var payload = { action: dialog.kind };
    var value = String(dialog.value || "").trim();
    if (dialog.kind === "spend_cap") {
      payload.spend_cap_action = mode;
      if (mode === "set") {
        var amount = Number(value.replace(",", "."));
        if (!value || !isFinite(amount) || amount <= 0) {
          dialog.error = "Укажите лимит больше нуля";
          return renderAccountDialog();
        }
        payload.spend_cap = amount;
      } else if (!(await askConfirm({
        title: mode === "delete" ? "Снять лимит затрат?" : "Обнулить потраченное?",
        message: mode === "delete"
          ? "Кабинет сможет тратить без ограничения."
          : "Счётчик трат под лимитом начнётся с нуля, лимит останется прежним.",
        confirmLabel: mode === "delete" ? "Снять лимит" : "Обнулить",
        danger: mode === "delete"
      }))) return;
    } else {
      if (!value) {
        dialog.error = dialog.kind === "pixel" ? "Укажите название пикселя" : "Укажите название";
        return renderAccountDialog();
      }
      payload.name = value;
    }
    dialog.busy = true;
    dialog.error = "";
    renderAccountDialog();
    var result;
    try {
      result = await api.post("/meta/accounts/" + dialog.account.account_id + "/actions", payload);
    } catch (error) {
      if (accountDialog !== dialog) return;
      dialog.busy = false;
      dialog.error = error.message || String(error);
      return renderAccountDialog();
    }
    if (accountDialog !== dialog) return;
    closeAccountDialog();
    if (result && result.warning) {
      notify({ title: "Проверьте лимит", message: result.warning });
    } else if (dialog.kind === "pixel") {
      notify({ title: "Пиксель создан", message: value + (result.pixel_id ? " · " + result.pixel_id : "") });
    }
    reload();
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
    GEO_CHOICES.concat(state.availableGeos).forEach(function (geo) { known[geo] = true; });
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
    return text === "" ? "" : Number(text);
  }

  async function saveHeat() {
    var next = {};
    draft.forEach(function (block) {
      var limits = {};
      HEAT_ROWS.forEach(function (row) {
        var edge = block.limits[row.key] || {};
        limits[row.key] = { green: limitValue(edge.green), red: limitValue(edge.red) };
      });
      next[block.geo] = limits;
    });
    var invalid = Object.keys(next).some(function (geo) {
      return HEAT_ROWS.some(function (row) {
        var limits = next[geo][row.key];
        return [limits.green, limits.red].some(function (value) {
          return value !== "" && (!isFinite(value) || value < 0);
        }) || (limits.green !== "" && limits.red !== "" && limits.green > limits.red);
      });
    });
    if (invalid) {
      notify({ title: "Проверьте пороги", message: "Нужны неотрицательные числа; зелёный порог не должен превышать красный." });
      return;
    }
    try {
      await api.put("/me/preferences/meta.tree.heat", { value: next });
    } catch (error) {
      notify({ title: "Не удалось сохранить подсветку", message: error.message || String(error) });
      return;
    }
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
    state.filters.geo = factory(byId("metaTreeGeo"), reload);
    refreshFilters();
  }

  function refreshFilters() {
    if (!state.filters.agents || !state.filters.geo) return;
    state.filters.agents.setItems(collectValues("agent").map(function (name) {
      return { value: name, label: name };
    }));
    state.filters.geo.setItems(state.availableGeos.map(function (name) {
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
      var menuButton = event.target.closest("[data-tree-menu]");
      if (menuButton) {
        var menuRow = findRow(state.rows, menuButton.getAttribute("data-tree-menu"));
        return toggleMenu(menuButton, { rows: menuRow ? [menuRow] : [] }, false);
      }
      var accountMenu = event.target.closest("[data-tree-account-menu]");
      if (accountMenu) {
        var account = findRow(state.rows, accountMenu.getAttribute("data-tree-account-menu"));
        return account && toggleMenu(accountMenu, { account: account }, false);
      }
      var budget = event.target.closest("[data-tree-budget]");
      if (budget) {
        var budgetRow = findRow(state.rows, budget.getAttribute("data-tree-budget"));
        return budgetRow && openEditor("budget", [budgetRow]);
      }
      var agent = event.target.closest("[data-tree-agent]");
      if (agent) {
        var meta = window.CelestialMeta;
        if (meta && meta.openConnection) meta.openConnection(agent.getAttribute("data-tree-agent"));
        return;
      }
      var action = event.target.closest("[data-tree-action]");
      if (!action) return;
      var row = findRow(state.rows, action.getAttribute("data-tree-id"));
      if (row) runAction(action.getAttribute("data-tree-action"), [row]);
    });
    byId("metaTreeBody").addEventListener("change", function (event) {
      var box = event.target;
      if (box.hasAttribute("data-tree-pick")) {
        var id = box.getAttribute("data-tree-pick");
        if (box.checked) state.picked[id] = true;
        else delete state.picked[id];
        return render();
      }
      if (box.hasAttribute("data-tree-pick-group")) {
        var parent = findRow(state.rows, box.getAttribute("data-tree-pick-group"));
        actionable(parent ? visibleChildren(parent) : []).forEach(function (row) {
          if (box.checked) state.picked[row.id] = true;
          else delete state.picked[row.id];
        });
        render();
      }
    });
    document.addEventListener("click", function (event) {
      if (!state.menu) return;
      if (event.target.closest("#metaTreeMenu") || event.target.closest("[data-tree-menu]") ||
          event.target.closest("[data-tree-account-menu]") ||
          event.target.closest('[data-bulk="more"]')) return;
      closeMenu();
    });
    window.addEventListener("resize", function () { closeMenu(); });
    byId("metaTreeTable").closest(".mt-scroll").addEventListener("scroll", function () {
      closeMenu();
    });
    // Вкладки переключает meta-ui.js; панель отмеченных видна только на «Обзоре».
    Array.prototype.forEach.call(document.querySelectorAll(".meta-tab"), function (tab) {
      tab.addEventListener("click", function () { window.setTimeout(renderBulk, 0); });
    });
    byId("metaTreeHead").addEventListener("click", function (event) {
      var button = event.target.closest("[data-tree-head]");
      if (!button) return;
      var kind = button.getAttribute("data-tree-head");
      if (kind === "heat") return openHeat();
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
    byId("metaTreeHeatClose").addEventListener("click", closeHeat);
    byId("metaTreeHeatCancel").addEventListener("click", closeHeat);
    byId("metaTreeHeatModal").addEventListener("click", function (event) {
      if (event.target === event.currentTarget) closeHeat();
    });
    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape") return;
      if (state.menu) return closeMenu();
      if (accountDialog) return closeAccountDialog();
      if (draft) closeHeat();
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
    [byId("metaTreeFrom"), byId("metaTreeTo")].forEach(function (field) {
      if (field) field.addEventListener("change", reload);
    });
    window.addEventListener("celestial:meta-refreshed", reload);
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

  async function reload() {
    var requestId = ++state.requestId;
    state.loading = true;
    render();
    try {
      var payload = await loadTree();
      if (requestId !== state.requestId) return;
      state.rows = payload.rows || [];
      state.availableGeos = payload.available_geos || [];
      state.attribution = payload.attribution || {};
      state.keitaroUnavailable = !!payload.keitaro_unavailable;
      Object.keys(state.picked).forEach(function (id) {
        if (!findRow(state.rows, id)) delete state.picked[id];
      });
      if (!Object.keys(state.open).length && state.rows[0]) state.open[state.rows[0].id] = true;
      refreshFilters();
      state.loading = false;
      render();
    } catch (error) {
      if (requestId !== state.requestId) return;
      state.loading = false;
      render();
      byId("metaTreeNote").textContent = "Ошибка загрузки: " + (error.message || String(error));
      notify({ title: "Не удалось загрузить структуру Meta Ads", message: error.message || String(error) });
    }
  }

  async function init() {
    if (!byId("metaTreeBody")) return;
    defaultPeriod();
    if (!state.bound) {
      state.bound = true;
      bind();
      bindFilters();
      try {
        var saved = await api.get("/me/preferences/meta.tree.heat");
        if (saved && saved.value && typeof saved.value === "object" && !Array.isArray(saved.value)) {
          state.heat = saved.value;
        }
      } catch (error) { /* Подсветка опциональна. */ }
    }
    await reload();
  }

  window.CelestialMetaTree = { init: init, reload: reload, render: render, state: state };
})();
