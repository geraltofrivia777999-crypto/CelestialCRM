/*
 * MetaAds v2 · «Обзор» деревом: аккаунт → кампании → адсеты → объявления.
 *
 * Числа Meta считаются из дневной статистики на каждом уровне отдельно.
 * Депозиты Keitaro привязаны к кампании: у адсетов и объявлений они неизвестны.
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
    heat: {},
    filters: { agents: null, geo: null },
    availableGeos: [],
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

  async function runAction(action, row) {
    if (!row.external_id) return;
    var names = { start: "Запустить", pause: "Остановить", duplicate: "Дублировать" };
    var confirmed = window.CelestialShell && window.CelestialShell.confirm
      ? await window.CelestialShell.confirm({
          title: names[action] + " объект Meta?",
          message: row.name, confirmLabel: names[action], danger: action === "pause"
        })
      : window.confirm(names[action] + " «" + row.name + "»?");
    if (!confirmed) return;
    try {
      var result = await api.post("/meta/entities/actions", {
        level: { campaign: "campaigns", adset: "adsets", ad: "ads" }[row.level],
        action: action, items: [{ id: row.external_id }]
      });
      var first = (result.results || [])[0];
      if (!first || !first.ok) throw new Error(first && first.error || "Meta не выполнила действие");
      await reload();
      notify({ title: "Готово", message: names[action] + ": " + row.name });
    } catch (error) {
      notify({ title: "Действие не выполнено", message: error.message || String(error) });
    }
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
      "<td>" + (row.budget != null ? money(row.budget, row.currency) : "—") + "</td>" +
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
    var accounts = visibleAccounts();
    var currencies = {};
    accounts.forEach(function (row) { currencies[row.currency || "USD"] = true; });
    var currencyNames = Object.keys(currencies);
    var oneCurrency = currencyNames.length === 1 ? currencyNames[0] : null;
    var sum = accounts.reduce(function (acc, row) {
      var part = totals(row);
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
      { label: "Clicks", value: num(sum.clicks || 0) },
      { label: "CTR", value: percent(ctr) },
      { label: "CPM", value: oneCurrency ? money(sum.impressions ? sum.spend / sum.impressions * 1000 : null, oneCurrency) : "—" },
      { label: "CPC", value: oneCurrency ? money(sum.clicks ? sum.spend / sum.clicks : null, oneCurrency) : "—" },
      { label: "Installs", value: num(sum.insts || 0), tone: "blue" },
      { label: "Registrations", value: num(sum.regs || 0), tone: "blue" },
      { label: "Deposits", value: sum.hasDeps ? num(sum.deps) : "—", tone: "blue" },
      { label: "Avg install", value: oneCurrency ? money(ratio(sum.spend, sum.insts), oneCurrency) : "—", tone: "avg" },
      { label: "Avg reg", value: oneCurrency ? money(ratio(sum.spend, sum.regs), oneCurrency) : "—", tone: "avg" },
      { label: "Avg deposit", value: oneCurrency && sum.hasDeps ? money(ratio(sum.spend, sum.deps), oneCurrency) : "—", tone: "avg" }
    ];
    byId("metaTreeCards").innerHTML = cards.map(function (card) {
      return '<div class="mt-card' + (card.tone ? " mt-card--" + card.tone : "") + '">' +
        "<span>" + escapeHtml(card.label) + "</span><b>" + card.value + "</b></div>";
    }).join("");
    byId("metaTreeNote").textContent = currencyNames.length > 1
      ? "В кабинетах разные валюты: денежный итог не суммируется. Депозиты ниже кампании не атрибутируются."
      : "Insts и Regs — события Meta; Deps — продажи Keitaro на уровне кампании.";
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
      (state.loading ? "Загружаем данные…" : state.rows.length ?
        "Под фильтры ничего не подошло" : "Кабинетов пока нет или данные не загружены") + "</td></tr>";
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
