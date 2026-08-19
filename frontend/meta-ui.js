/*
 * Meta Ads: кабинеты, статистика, заливы, шаблоны, креативы и автоправила.
 *
 * Расход, показы и клики приходят из Meta Insights; лиды, продажи и доход
 * подставляются из трекера по ID кампании, который баер кладёт в sub_id ссылки.
 * Пока этот sub_id не настроен, доход и ROI показываются прочерком, а не нулём:
 * ноль здесь означал бы «слили в минус», хотя на деле мы просто не знаем.
 *
 * Всё, что тратит деньги — публикация залива, пауза, изменение бюджета — живёт
 * за правом meta.launch и всегда требует подтверждения. Кампания создаётся на
 * паузе; снятие с паузы — отдельное осознанное действие.
 */
(function () {
  "use strict";

  /* ---------- подтверждения ----------
   *
   * Окно браузера рисуется у верхней кромки и подписано адресом сервера — на
   * фоне интерфейса это выглядит как сообщение постороннего сайта. Спрашиваем
   * модалкой CRM; контракт тот же, только ответ приходит промисом.
   */

  function askConfirm(options) {
    if (window.CelestialShell && window.CelestialShell.confirm) {
      return window.CelestialShell.confirm(options);
    }
    return Promise.resolve(window.confirm(options.message || options.title));
  }

  function notify(options) {
    if (window.CelestialShell && window.CelestialShell.notify) {
      return window.CelestialShell.notify(options);
    }
    window.alert(options.message || options.title);
    return Promise.resolve(true);
  }


  var api = window.CelestialAPI;
  var POLL_DELAY = 4000;

  var state = {
    user: null,
    canManage: false,
    canLaunch: false,
    canFixSpend: false,
    connections: [],
    accounts: [],
    campaigns: [],
    buyers: [],
    period: "7",
    accountId: "",
    ownerId: "",
    level: "users",
    levelSearch: "",
    hour: null,
    levelRows: [],
    // Фиксация расхода за отрезок дня: что отмечено в таблице кампаний и что
    // насчитала Meta по этим кампаниям за выбранное окно.
    spendPick: {},
    spend: { rows: [], commits: [], loading: false },
    spendRefs: null,
    busy: false,
    pollTimer: null,
    tab: "overview",
    reference: null,
    launches: [],
    launchStatus: "",
    selected: [],
    // Связки: список, что в нём отмечено и мастер, в котором её собирают.
    templates: [],
    bundleSearch: "",
    bundlePick: [],
    bundle: null,
    macro: null,
    launchView: "bundles",
    upload: null,
    queue: [],
    rules: [],
    events: [],
    wizard: null,
    form: null,
    // Браузерная сессия Meta (токен EAAB): sessionId, полученный токен и таймер
    // опроса. Одна на страницу — одновременно открыт только один диалог.
    session: null
  };

  function byId(id) {
    return document.getElementById(id);
  }

  function escapeHtml(value) {
    return String(value === null || value === undefined ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function hasPermission(user, code) {
    var codes = (user && user.role && user.role.permissions) || [];
    return codes.some(function (item) {
      return item.code === code || item.code === "*";
    });
  }

  /* Прочерк там, где значения нет. Ноль — это ответ, null — его отсутствие. */
  function num(value) {
    if (value === null || value === undefined) return "–";
    return Number(value).toLocaleString("ru-RU");
  }

  function money(value) {
    if (value === null || value === undefined) return "–";
    return "$ " + Number(value).toLocaleString("ru-RU", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    });
  }

  function percent(value) {
    if (value === null || value === undefined) return "–";
    return Number(value).toLocaleString("ru-RU", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    }) + " %";
  }

  function iso(date) {
    return date.toISOString().slice(0, 10);
  }

  function periodRange(period) {
    var now = new Date();
    var to = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    var from = new Date(to);
    if (period === "today") return { from: iso(to), to: iso(to) };
    if (period === "yesterday") {
      from.setDate(from.getDate() - 1);
      return { from: iso(from), to: iso(from) };
    }
    if (period === "month") {
      from = new Date(now.getFullYear(), now.getMonth(), 1);
      return { from: iso(from), to: iso(to) };
    }
    if (period === "prev_month") {
      from = new Date(now.getFullYear(), now.getMonth() - 1, 1);
      var last = new Date(now.getFullYear(), now.getMonth(), 0);
      return { from: iso(from), to: iso(last) };
    }
    from.setDate(from.getDate() - (Number(period) - 1));
    return { from: iso(from), to: iso(to) };
  }

  function statusTone(value) {
    if (value === "ACTIVE") return { color: "#16B57F", background: "#E4F7F0" };
    if (value === "PAUSED" || value === "IN_GRACE_PERIOD") {
      return { color: "#C9821F", background: "#FFF6E9" };
    }
    if (!value) return { color: "#9B9292", background: "#F7F4F4" };
    return { color: "#C41616", background: "#FCF1F1" };
  }

  function chip(value) {
    var tone = statusTone(value);
    return '<span class="meta-chip" style="color:' + tone.color + ";background:" +
      tone.background + '"><span class="meta-dot" style="background:' + tone.color +
      '"></span>' + escapeHtml(value || "—") + "</span>";
  }

  function toneForNumber(value) {
    if (value === null || value === undefined) return "#9B9292";
    if (value > 0) return "#16B57F";
    if (value < 0) return "#C41616";
    return "#3A3030";
  }

  function renderKpi(payload) {
    var totals = payload.totals || {};
    byId("metaSpend").textContent = money(totals.spend);
    var currencies = payload.currencies || [];
    byId("metaSpendHint").textContent = currencies.length > 1
      // Складывать евровый кабинет с долларовым нельзя молча — пусть видно, что итог смешанный.
      ? "внимание: кабинеты в разных валютах (" + currencies.join(", ") + ")"
      : "за период" + (currencies.length ? " · " + currencies[0] : "");
    byId("metaClicks").textContent = num(totals.clicks);
    byId("metaCtr").textContent = "CTR " + percent(totals.ctr) +
      " · CPC " + money(totals.cpc);
    byId("metaLeads").textContent = num(totals.leads);
    byId("metaCpl").textContent = "CPL " + money(totals.cpl);
    var profit = byId("metaProfit");
    profit.textContent = money(totals.profit);
    profit.style.color = toneForNumber(totals.profit);
    byId("metaRoi").textContent = totals.roi === null || totals.roi === undefined
      ? "ROI считается по доходу из Keitaro"
      : "ROI " + percent(totals.roi);
  }

  /* Плашек про доход здесь больше нет: команда не тянет доход из трекера в
     Meta Ads, и сообщения «ID не доехал до трекера» висели постоянно, ничего
     не требуя. Осталось предупреждение о самом подключении — без него модуль
     пустой, и это стоит сказать. */
  function renderAttribution(payload) {
    var host = byId("metaAttribution");
    if (payload.configured) {
      host.style.display = "none";
      host.innerHTML = "";
      return;
    }
    host.style.display = "";
    host.innerHTML = notice(
      "#FCF1F1", "#B91414",
      "Meta Ads ещё не подключена",
      "Добавьте подключение с токеном системного пользователя Business Manager — " +
      "после этого кабинеты и статистика подтянутся сами."
    );
  }

  function notice(background, color, title, text) {
    return '<div style="background:' + background + ";border:1px solid " + color +
      '22;border-radius:14px;padding:14px 18px">' +
      '<div style="font-size:13px;font-weight:700;color:' + color + '">' +
      escapeHtml(title) + "</div>" +
      '<div style="font-size:12px;color:#6A6161;font-weight:500;margin-top:5px;line-height:1.55">' +
      escapeHtml(text) + "</div></div>";
  }

  /* ==========================================================
     СТРУКТУРА: восемь уровней одной и той же статистики

     Метрики одинаковые на всех уровнях, меняются только опознавательные
     колонки слева. Поэтому одна таблица и одно описание набора колонок,
     а не восемь почти одинаковых рендеров.
     ========================================================== */

  var LEVEL_HEADS = {
    users: [
      { key: "name", label: "Пользователь", wide: true }
    ],
    socials: [
      { key: "name", label: "Аккаунт", wide: true },
      { key: "businesses", label: "БМы", count: true },
      { key: "fan_pages", label: "Фан-пейджи", count: true },
      { key: "ad_accounts", label: "Кабинеты", count: true }
    ],
    fanpages: [
      { key: "name", label: "Фан-пейдж", wide: true },
      { key: "category", label: "Категория", text: true },
      { key: "business", label: "БМ", text: true }
    ],
    businesses: [
      { key: "name", label: "БМ", wide: true },
      { key: "fan_pages", label: "Фан-пейджи", count: true },
      { key: "ad_accounts", label: "Кабинеты", count: true }
    ],
    accounts: [
      { key: "name", label: "Кабинет", wide: true },
      { key: "kind", label: "Тип", kind: true },
      { key: "account_status", label: "Статус", status: true },
      { key: "business", label: "БМ", text: true },
      { key: "owner", label: "Ответственный", owner: true }
    ],
    campaigns: [
      { key: "name", label: "Кампания", wide: true },
      { key: "account", label: "Кабинет", text: true },
      { key: "status", label: "Статус", status: true }
    ],
    adsets: [
      { key: "name", label: "Адсет", wide: true },
      { key: "account", label: "Кабинет", text: true },
      { key: "status", label: "Статус", status: true }
    ],
    ads: [
      { key: "name", label: "Объявление", wide: true },
      { key: "fan_page", label: "Фан-пейдж", text: true },
      { key: "account", label: "Кабинет", text: true },
      { key: "status", label: "Статус", status: true }
    ]
  };

  // Общий хвост метрик. `revenue` показываем только там, где доход вообще
  // существует: ниже кампании атрибуции Keitaro нет, и пустой столбец врал бы.
  var LEVEL_METRICS = [
    { key: "spend", label: "Расход", money: true },
    { key: "impressions", label: "Показы", num: true },
    { key: "link_clicks", label: "Клики по ссылке", num: true },
    { key: "link_ctr", label: "Клики, CR", percent: true },
    { key: "cpc", label: "CPC", money: true },
    { key: "cpm", label: "CPM", money: true },
    { key: "results", label: "Результаты", num: true },
    { key: "cpa", label: "CPA", money: true }
  ];
  var LEVEL_REVENUE = [
    { key: "leads", label: "Лиды", num: true },
    { key: "revenue", label: "Доход", money: true },
    { key: "profit", label: "Профит", money: true, tone: true },
    { key: "roi", label: "ROI", percent: true, tone: true }
  ];

  var KIND_LABELS = { business: "БМ", personal: "Личный" };

  function levelColumns(payload) {
    var head = LEVEL_HEADS[payload.level] || LEVEL_HEADS.accounts;
    return head.concat(LEVEL_METRICS).concat(payload.has_revenue ? LEVEL_REVENUE : []);
  }

  function cellValue(column, row) {
    var value = row[column.key];
    if (column.money) return money(value);
    if (column.percent) return percent(value);
    if (column.num) return num(value);
    if (column.count) {
      return value ? num(value) : '<span style="color:#C9BFBF">—</span>';
    }
    if (column.status) return chip(value);
    if (column.kind) {
      return '<span class="meta-chip" style="background:#F2EDED;color:#6A6161">' +
        escapeHtml(KIND_LABELS[value] || "—") + "</span>";
    }
    if (column.owner) return ownerControl(row);
    if (column.wide) {
      return '<div style="font-weight:700;font-size:13px">' + escapeHtml(value) + "</div>";
    }
    return value
      ? escapeHtml(String(value))
      : '<span style="color:#C9BFBF">—</span>';
  }


  /* ==========================================================
     РАСХОД ПО ЧАСАМ

     Открывается кликом по строке кампании, адсета или объявления. Данные не
     хранятся у нас: они запрашиваются в Meta по требованию и живут в кеше
     несколько минут — почасовую статистику смотрят точечно.
     ========================================================== */

  // Уровни, у которых есть объект в Meta и, значит, почасовая разбивка.
  var HOUR_LEVELS = { campaigns: "кампании", adsets: "адсета", ads: "объявления" };

  function canOpenHours(level) {
    return Object.prototype.hasOwnProperty.call(HOUR_LEVELS, level);
  }

  function openHourModal(level, externalId) {
    if (!canOpenHours(level)) return;
    state.hour = { level: level, externalId: externalId };
    byId("metaHourModal").style.display = "flex";
    byId("metaHourTitle").textContent = "Расход по часам";
    byId("metaHourSubtitle").textContent = "Загружаем…";
    byId("metaHourTotals").innerHTML = "";
    byId("metaHourChart").innerHTML = "";
    byId("metaHourAxis").innerHTML = "";
    byId("metaHourBody").innerHTML = "";
    byId("metaHourNote").textContent = "";
    loadHours(false).catch(function (error) {
      byId("metaHourSubtitle").textContent = "";
      byId("metaHourNote").className = "meta-note meta-note--warn";
      byId("metaHourNote").textContent = error && error.message
        ? error.message
        : "Не удалось получить почасовую статистику";
    });
  }

  function closeHourModal() {
    byId("metaHourModal").style.display = "none";
    state.hour = null;
  }

  async function loadHours(refresh) {
    if (!state.hour) return;
    var range = periodRange(state.period);
    var payload = await api.get("/meta/insights/hourly?external_id=" +
      encodeURIComponent(state.hour.externalId) + "&level=" + state.hour.level +
      "&date_from=" + range.from + "&date_to=" + range.to +
      (refresh ? "&refresh=true" : ""));
    renderHours(payload);
  }

  function renderHours(payload) {
    var hours = payload.hours || [];
    var totals = payload.totals || {};
    byId("metaHourTitle").textContent = payload.name || "Расход по часам";
    byId("metaHourSubtitle").textContent =
      HOUR_LEVELS[payload.level] + " · " + payload.account_name +
      " · " + payload.period.from + " — " + payload.period.to;

    byId("metaHourTotals").innerHTML = [
      { label: "Расход", value: money(totals.spend) },
      { label: "Показы", value: num(totals.impressions) },
      { label: "Клики по ссылке", value: num(totals.link_clicks) },
      {
        label: "Пиковый час",
        // Пик без денег — это просто первая корзина, а не факт.
        value: totals.peak_hour === null || totals.peak_hour === undefined
          ? "—"
          : hourLabel(totals.peak_hour) + " · " + money(totals.peak_spend)
      }
    ].map(function (card) {
      return '<div style="background:#FAF8F8;border:1px solid #F0EBEB;border-radius:13px;' +
        'padding:12px 14px"><div style="font-size:10.5px;color:#9B9292;font-weight:700;' +
        'text-transform:uppercase;letter-spacing:.5px">' + escapeHtml(card.label) + "</div>" +
        '<div style="font-size:17px;font-weight:700;margin-top:5px;letter-spacing:-.3px">' +
        card.value + "</div></div>";
    }).join("");

    var peak = hours.reduce(function (best, row) {
      return row.spend > best ? row.spend : best;
    }, 0);
    byId("metaHourChart").innerHTML = hours.map(function (row) {
      // Ненулевой, но крошечный час всё равно должен быть виден полоской.
      var height = peak > 0 && row.spend > 0
        ? Math.max(Math.round((row.spend / peak) * 100), 3)
        : 2;
      return '<div class="meta-hour-bar' + (row.spend > 0 ? "" : " meta-hour-bar--empty") +
        '" style="height:' + height + '%" title="' + hourLabel(row.hour) + " · " +
        escapeHtml(money(row.spend)) + '"></div>';
    }).join("");
    byId("metaHourAxis").innerHTML = hours.map(function (row) {
      // Подписываем каждый третий час — иначе на узком окне цифры слипаются.
      return '<span class="meta-hour-tick">' +
        (row.hour % 3 === 0 ? String(row.hour) : "") + "</span>";
    }).join("");

    var note = byId("metaHourNote");
    note.className = "meta-note";
    note.textContent = payload.timezone
      ? "Часы указаны в таймзоне кабинета — " + payload.timezone +
        ". Meta считает почасовую разбивку только по ней." +
        (payload.days > 1 ? " Дни периода сложены в одни сутки." : "")
      : "Часы указаны в таймзоне рекламного кабинета: Meta считает разбивку только по ней." +
        (payload.days > 1 ? " Дни периода сложены в одни сутки." : "");

    var active = hours.filter(function (row) { return row.spend > 0; });
    byId("metaHourBody").innerHTML = active.length
      ? active.map(function (row) {
        return '<tr class="meta-row" style="border-bottom:1px solid #F7F4F4">' +
          '<td class="meta-cell meta-cell--left" style="font-weight:700">' +
          hourLabel(row.hour) + "</td>" +
          '<td class="meta-cell">' + money(row.spend) + "</td>" +
          '<td class="meta-cell">' + num(row.impressions) + "</td>" +
          '<td class="meta-cell">' + num(row.link_clicks) + "</td></tr>";
      }).join("")
      : '<tr><td colspan="4" style="padding:26px;text-align:center;color:#9B9292;' +
        'font-size:12.5px">За период расхода не было</td></tr>';
  }

  function hourLabel(hour) {
    return (hour < 10 ? "0" + hour : String(hour)) + ":00";
  }

  /* Кампании отмечают прямо в таблице: одна кампания за день успевает полить
     разные офферы, а один оффер — набраться из нескольких кампаний. Поэтому
     сначала выбор строк, и только потом «на что и за какие часы относим». */
  function hasPickColumn(level) {
    return level === "campaigns" && state.canFixSpend;
  }

  function renderLevelTable(payload) {
    var columns = levelColumns(payload);
    var rows = payload.rows || [];
    var pick = hasPickColumn(payload.level);
    // Первая колонка отступает от края таблицы. С чекбоксами первый — он.
    var lead = function (index) { return index === 0 && !pick ? "padding-left:24px;" : ""; };
    var allPicked = pick && rows.length && rows.every(function (row) {
      return !!state.spendPick[row.id];
    });
    byId("metaLevelHead").innerHTML =
      '<tr style="border-top:1px solid #F0EBEB;border-bottom:1px solid #F0EBEB">' +
      (pick
        ? '<th class="meta-th meta-check" style="padding-left:24px">' +
          '<input type="checkbox" data-spend-all aria-label="Выбрать все кампании"' +
          (allPicked ? " checked" : "") + "></th>"
        : "") +
      columns.map(function (column, index) {
        var align = column.wide || column.text || column.status || column.kind
          ? " meta-th--left" : "";
        var tail = index === columns.length - 1 ? ';padding-right:24px' : "";
        return '<th class="meta-th' + align + '" style="' + lead(index) + tail + '">' +
          escapeHtml(column.label) + "</th>";
      }).join("") + "</tr>";

    var body = byId("metaTableBody");
    if (!rows.length) {
      body.innerHTML = '<tr><td colspan="' + (columns.length + (pick ? 1 : 0)) +
        '" style="padding:44px 24px;text-align:center;color:#9B9292;font-size:13px">' +
        escapeHtml(emptyLevelText(payload.level)) + "</td></tr>";
      byId("metaLevelFoot").innerHTML = "";
      byId("metaResultCount").textContent = "Строк: 0";
      renderSpendButton();
      return;
    }
    var clickable = canOpenHours(payload.level);
    body.innerHTML = rows.map(function (row) {
      return '<tr class="meta-row' + (clickable ? " meta-row--clickable" : "") + '"' +
        (clickable ? ' data-hour-row="' + escapeHtml(row.id) + '" tabindex="0" ' +
          'title="Расход по часам"' : "") +
        ' style="border-bottom:1px solid #F7F4F4">' +
        (pick
          ? '<td class="meta-cell meta-check" style="padding-left:24px">' +
            '<input type="checkbox" data-spend-pick="' + escapeHtml(row.id) + '"' +
            ' data-spend-name="' + escapeHtml(row.name) + '"' +
            (state.spendPick[row.id] ? " checked" : "") +
            ' aria-label="Выбрать кампанию"></td>'
          : "") +
        columns.map(function (column, index) {
          var align = column.wide || column.text || column.status || column.kind
            ? " meta-cell--left" : "";
          var style = lead(index);
          if (index === columns.length - 1) style += "padding-right:24px;";
          if (column.tone) style += "color:" + toneForNumber(row[column.key]) + ";";
          return '<td class="meta-cell' + align + '" style="' + style + '">' +
            cellValue(column, row) + "</td>";
        }).join("") + "</tr>";
    }).join("");

    var totals = payload.totals || {};
    byId("metaLevelFoot").innerHTML = '<tr class="meta-foot">' +
      (pick ? '<td class="meta-check" style="padding-left:24px"></td>' : "") +
      columns.map(function (column, index) {
        var style = lead(index);
        if (index === columns.length - 1) style += "padding-right:24px;";
        if (index === 0) return '<td style="' + style + '">Итого</td>';
        // Итог по строкам-именам не суммируется — там прочерк, а не ноль.
        if (!(column.money || column.num || column.percent)) {
          return '<td style="' + style + ';color:#C9BFBF">—</td>';
        }
        if (column.tone) style += "color:" + toneForNumber(totals[column.key]) + ";";
        return '<td style="' + style + '">' + cellValue(column, totals) + "</td>";
      }).join("") + "</tr>";
    byId("metaResultCount").textContent = "Строк: " + rows.length;
    renderSpendButton();
  }

  function emptyLevelText(level) {
    // Пустой поиск и пустой уровень — разные вещи: во втором случае человеку
    // надо объяснить, чего не хватает, в первом — просто что ничего не нашлось.
    if (state.levelSearch) return "Ничего не найдено — измените запрос";
    if (level === "users") {
      return "Ни за кем не закреплён кабинет — назначьте ответственных на уровне «Кабинеты»";
    }
    if (level === "socials" || level === "businesses" || level === "fanpages") {
      return "Пусто. Аккаунты, БМы и фан-пейджи приходят из Meta — " +
        "токену нужны права business_management и pages_show_list";
    }
    return "За выбранный период открутки не было";
  }

  function renderLevelHint(payload) {
    var host = byId("metaLevelHint");
    var level = payload.level;
    var text = "";
    if (!payload.has_revenue && ["adsets", "ads"].indexOf(level) >= 0) {
      text = "Доход Keitaro привязан к ID кампании, поэтому ниже кампании его нет — " +
        "здесь только числа кабинета.";
    } else if (level === "fanpages") {
      text = "Строка собирается по объявлениям: фан-пейдж получает расход той рекламы, " +
        "которая крутится от его лица.";
    } else if (level === "users") {
      text = "Считается по кабинетам, закреплённым за человеком.";
    }
    host.style.display = text ? "" : "none";
    host.textContent = text;
  }

  function renderLevelTabs() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-level]"), function (button) {
      var active = button.getAttribute("data-level") === state.level;
      button.classList.toggle("meta-level--active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
    renderSpendButton();
  }

  async function loadLevel() {
    var range = periodRange(state.period);
    var query = "?date_from=" + range.from + "&date_to=" + range.to +
      (state.accountId ? "&account_id=" + encodeURIComponent(state.accountId) : "") +
      (state.ownerId ? "&owner_id=" + encodeURIComponent(state.ownerId) : "") +
      (state.levelSearch ? "&search=" + encodeURIComponent(state.levelSearch) : "");
    var payload = await api.get("/meta/overview/levels/" + state.level + query);
    state.levelRows = payload.rows || [];
    renderLevelTabs();
    renderLevelHint(payload);
    renderLevelTable(payload);
  }

  /* Ответственный — единственное поле кабинета, которое принадлежит CRM, а не Meta,
     поэтому назначается прямо в строке уровня «Кабинеты». */
  function ownerControl(row) {
    if (!state.canManage) {
      return row.owner
        ? escapeHtml(row.owner)
        : '<span style="color:#C9821F">Не назначен</span>';
    }
    var options = ['<option value="">Не назначен</option>'].concat(
      state.buyers.map(function (buyer) {
        return '<option value="' + escapeHtml(buyer.id) + '"' +
          (buyer.name === row.owner ? " selected" : "") + ">" +
          escapeHtml(buyer.name) + "</option>";
      })
    );
    return '<select class="meta-control meta-select" data-meta-owner="' +
      escapeHtml(row.id) + '" aria-label="Ответственный за кабинет" ' +
      'style="height:32px;font-size:12px;min-width:168px">' + options.join("") + "</select>";
  }

  async function assignOwner(accountId, ownerId) {
    try {
      await api.patch("/meta/accounts/" + accountId, { owner_id: ownerId || null });
      await load();
    } catch (error) {
      notify({
        title: "Не удалось назначить ответственного",
        message: error && error.message ? error.message : ""
      });
      await load();
    }
  }


  /* ---------- фиксация расхода за отрезок дня (ТЗ 2.4.4) ---------- */

  /*
   * Баер льёт один оффер с 12:00 до 16:00, потом другой. Дневная статистика
   * Meta про это не знает — она отдаёт сумму за сутки, поэтому окно берётся из
   * почасовой разбивки.
   *
   * Окно — полуинтервал: «с 12:00 по 16:00» это часы 12, 13, 14 и 15. Иначе
   * шестнадцатый час попадал бы и в это окно, и в следующее, и расход
   * задваивался бы на стыке.
   */

  function hourOptions(host, values, selected) {
    host.innerHTML = values.map(function (hour) {
      return '<option value="' + hour + '"' + (hour === selected ? " selected" : "") +
        ">" + (hour < 10 ? "0" + hour : hour) + ":00</option>";
    }).join("");
  }

  /* День и часы задаются в фильтрах, рядом с таблицей: расход относят на оффер
     по часам, и выбирают их до того, как отмечают кампании. Модалка эти
     значения только показывает. */
  function spendDay() {
    var input = byId("metaWindowDate");
    return input && input.value ? input.value : periodRange(state.period).to;
  }

  function spendWindow() {
    var from = Number(byId("metaWindowFrom").value);
    var to = Number(byId("metaWindowTo").value);
    return { from: from, to: to };
  }

  function renderWindowBox() {
    var box = byId("metaWindowBox");
    if (!box) return;
    box.hidden = !hasPickColumn(state.level);
    if (box.hidden) return;
    if (!byId("metaWindowDate").value) {
      byId("metaWindowDate").value = periodRange(state.period).to;
    }
    if (!byId("metaWindowFrom").options.length) {
      hourOptions(byId("metaWindowFrom"), rangeOf(0, 23), 12);
      hourOptions(byId("metaWindowTo"), rangeOf(1, 24), 16);
    }
  }

  function windowLabel() {
    var window_ = spendWindow();
    return spendDay() + " · " + pad(window_.from) + ":00–" + pad(window_.to) + ":00";
  }

  function pad(hour) {
    return hour < 10 ? "0" + hour : String(hour);
  }

  function selectedIds() {
    return Object.keys(state.spendPick);
  }

  function renderSpendButton() {
    renderWindowBox();
    var button = byId("metaSpendOpen");
    if (!button) return;
    var visible = hasPickColumn(state.level);
    button.style.display = visible ? "" : "none";
    if (!visible) return;
    var count = selectedIds().length;
    // Без выбранных кампаний фиксировать нечего, поэтому кнопка ждёт отметок,
    // а не открывает форму, в которой нечего показать.
    button.disabled = !count;
    button.style.opacity = count ? "" : ".5";
    button.textContent = count ? "Фиксация расхода · " + count : "Фиксация расхода";
  }

  /* Отметки в таблице и состояние — одно и то же: чекбоксы перерисовываются из
     state, иначе «убрать» в форме оставляло бы строку отмеченной в таблице. */
  function syncTableChecks() {
    var boxes = document.querySelectorAll("[data-spend-pick]");
    Array.prototype.forEach.call(boxes, function (box) {
      box.checked = !!state.spendPick[box.getAttribute("data-spend-pick")];
    });
    var all = document.querySelector("[data-spend-all]");
    if (all) {
      all.checked = boxes.length > 0 && Array.prototype.every.call(boxes, function (box) {
        return box.checked;
      });
    }
  }

  function toggleSpendPick(box, on) {
    var id = box.getAttribute("data-spend-pick");
    if (on) state.spendPick[id] = box.getAttribute("data-spend-name") || id;
    else delete state.spendPick[id];
  }

  function openSpendModal() {
    if (!selectedIds().length) return;
    var window_ = spendWindow();
    if (!(window_.from < window_.to)) {
      return notify({
        title: "Проверьте окно",
        message: "Конец окна должен быть позже начала."
      });
    }
    byId("metaSpendWindowValue").textContent = windowLabel();
    state.spend = { rows: [], commits: [], loading: false };
    byId("metaSpendCommits").innerHTML = "";
    byId("metaSpendWindowHint").textContent =
      "Расход берётся по часам кабинета — Meta других вариантов не даёт.";
    spendError("");
    renderSpendRows();
    renderSpendTotal();
    byId("metaSpendModal").style.display = "flex";
    // Справочники и часы независимы друг от друга: оффер можно выбирать, пока
    // Meta считает окно.
    renderSpendAssign().catch(function (error) {
      spendError(error && error.message ? error.message : "Справочники недоступны");
    });
    loadSpendWindow();
  }

  /* Оффер, агент и баер — прямо в форме: ради них её и открывают. */
  async function renderSpendAssign() {
    var host = byId("metaSpendAssign");
    host.innerHTML = '<div style="font-size:12px;color:#9B9292;font-weight:600">' +
      "Загружаем справочники…</div>";
    if (!state.spendRefs) {
      // Офферы берём все, а не только заведённые руками: расход относят и на
      // офферы Keitaro — по ним и льют. Постранично, потому что справочник
      // трекера длиннее любого разумного лимита.
      var loaded = await Promise.all([
        api.getAll("/offers?scope_offers=true"),
        api.get("/spend-providers?status=active&limit=200")
      ]);
      state.spendRefs = {
        offers: loaded[0].items || [],
        providers: loaded[1].items || []
      };
    }
    var refs = state.spendRefs;
    // Баер фиксирует расход на себя. Отнести на другого человека может только
    // тот, кто и так ведёт чужие кабинеты, — сервер проверяет это же право.
    var people = state.canManage ? state.buyers : state.buyers.filter(function (buyer) {
      return state.user && buyer.id === state.user.id;
    });
    if (!people.length && state.user) people = [{ id: state.user.id, name: state.user.name }];
    host.innerHTML =
      '<label class="spend-field"><span>Оффер</span>' +
      '<select class="meta-control meta-select" data-spend-field="offer_id">' +
      '<option value="">Не выбран</option>' +
      refs.offers.map(function (offer) {
        return '<option value="' + escapeHtml(offer.id) + '">' +
          escapeHtml(offer.name) + "</option>";
      }).join("") + "</select></label>" +
      '<label class="spend-field"><span>Агент</span>' +
      '<select class="meta-control meta-select" data-spend-field="provider_id">' +
      '<option value="">Не выбран</option>' +
      refs.providers.map(function (provider) {
        var percent = Number(provider.commission_pct || 0);
        return '<option value="' + escapeHtml(provider.id) + '">' +
          escapeHtml(provider.name) + (percent ? " · " + percent + " %" : "") +
          "</option>";
      }).join("") + "</select></label>" +
      '<label class="spend-field"><span>Баер</span>' +
      '<select class="meta-control meta-select" data-spend-field="buyer_id"' +
      (people.length > 1 ? "" : " disabled") + ">" +
      people.map(function (buyer) {
        return '<option value="' + escapeHtml(buyer.id) + '"' +
          (state.user && buyer.id === state.user.id ? " selected" : "") + ">" +
          escapeHtml(buyer.name) + "</option>";
      }).join("") + "</select></label>";
  }

  function rangeOf(first, last) {
    var values = [];
    for (var hour = first; hour <= last; hour += 1) values.push(hour);
    return values;
  }

  function closeSpendModal() {
    byId("metaSpendModal").style.display = "none";
  }

  async function loadSpendWindow(refresh) {
    var ids = selectedIds();
    if (!ids.length) return;
    var window_ = spendWindow();
    if (!(window_.from < window_.to)) {
      byId("metaSpendWindowHint").textContent = "Конец окна должен быть позже начала.";
      return;
    }
    var host = byId("metaSpendRows");
    state.spend.loading = true;
    renderSpendTotal();
    host.innerHTML = '<div style="padding:22px;text-align:center;color:#9B9292;' +
      'font-size:12.5px;font-weight:600">Спрашиваем Meta по часам…</div>';
    try {
      // Часы спрашиваем только по отмеченным кампаниям: остальные к этой
      // фиксации отношения не имеют, а каждый лишний поход — запрос в Meta.
      var query = "?day=" + encodeURIComponent(spendDay()) +
        "&hour_from=" + window_.from + "&hour_to=" + window_.to +
        ids.map(function (id) {
          return "&campaign_ids=" + encodeURIComponent(id);
        }).join("") +
        (refresh ? "&refresh=true" : "");
      var payload = await api.get("/meta/spend/window" + query);
      state.spend.rows = payload.rows || [];
      var commits = await api.get("/meta/spend/commits?day=" + encodeURIComponent(spendDay()));
      state.spend.commits = commits.items || [];
      byId("metaSpendWindowHint").textContent = (payload.timezones || []).length
        ? "Часы считает Meta по таймзоне кабинета: " + payload.timezones.join(", ") +
          ". Окно берёт часы " + window_.from + "–" + (window_.to - 1) + " включительно."
        : "За этот день у выбранных кампаний расхода не было.";
      renderSpendRows();
      renderSpendCommits();
    } catch (error) {
      state.spend.rows = [];
      host.innerHTML = '<div style="padding:22px;text-align:center;color:#B91414;' +
        'font-size:12.5px;font-weight:700">' +
        escapeHtml(error && error.message ? error.message : "Не удалось получить расход") +
        "</div>";
    } finally {
      state.spend.loading = false;
      renderSpendTotal();
    }
  }

  // Часы, уже отнесённые на оффер: иначе про занятое окно узнаёшь только из
  // отказа при сохранении.
  function spendClash(row, window_) {
    return (row.taken_hours || []).some(function (hour) {
      return hour >= window_.from && hour < window_.to;
    });
  }

  function renderSpendRows() {
    var host = byId("metaSpendRows");
    var ids = selectedIds();
    if (!ids.length) {
      host.innerHTML = "";
      return;
    }
    var window_ = spendWindow();
    var known = {};
    state.spend.rows.forEach(function (row) { known[row.campaign_id] = row; });
    host.innerHTML = '<div class="spend-title">Выбранные кампании</div>' +
      ids.map(function (id) {
        // Пока Meta не ответила, строка уже на месте — с именем из таблицы.
        var row = known[id] ||
          { campaign_id: id, name: state.spendPick[id] || id, spend: null, day_spend: null };
        var clash = spendClash(row, window_);
        return '<div class="spend-row' + (clash ? " spend-row--taken" : "") + '">' +
          '<span style="flex:1;min-width:0"><span class="spend-row__name">' +
          escapeHtml(row.name) + "</span>" +
          '<span class="spend-row__meta">' + escapeHtml(row.account_name || "") +
          (row.status ? " · " + escapeHtml(row.status) : "") +
          (clash ? " · окно уже занято" : "") + "</span></span>" +
          '<span><span class="spend-row__money">' + money(row.spend) +
          '</span><span class="spend-row__day">за день ' +
          money(row.day_spend) + "</span></span>" +
          '<button class="spend-drop" type="button" data-spend-remove="' +
          escapeHtml(id) + '" aria-label="Убрать кампанию">×</button></div>';
      }).join("");
  }

  function renderSpendCommits() {
    var host = byId("metaSpendCommits");
    if (!state.spend.commits.length) {
      host.innerHTML = "";
      return;
    }
    host.innerHTML = '<div class="spend-title">Уже зафиксировано за этот день</div>' +
      state.spend.commits.map(function (commit) {
        return '<div class="spend-commit"><span style="flex:1;min-width:0">' +
          "<b>" + escapeHtml(commit.campaign_name || commit.campaign_id) + "</b> · " +
          escapeHtml(commit.window) + " → " + escapeHtml(commit.offer || "—") +
          "</span><span>" + money(commit.base_amount) + "</span>" +
          '<button class="spend-drop" type="button" data-spend-drop="' +
          escapeHtml(commit.id) + '" aria-label="Снять привязку">×</button></div>';
      }).join("");
  }

  function renderSpendTotal() {
    var rows = state.spend.rows || [];
    var window_ = spendWindow();
    var total = rows.reduce(function (sum, row) { return sum + Number(row.spend || 0); }, 0);
    var taken = rows.filter(function (row) { return spendClash(row, window_); });
    byId("metaSpendTotal").textContent =
      "Выбрано: " + selectedIds().length + " · " + money(total);
    // Пересечение окон сервер и так отклонит — лучше сказать об этом до клика,
    // назвав кампанию, из-за которой отказ.
    if (taken.length) {
      spendError("«" + taken[0].name + "» уже отнесена на эти часы — " +
        "выберите другое окно или уберите её из списка");
    } else {
      spendError("");
    }
    byId("metaSpendCommit").disabled =
      state.spend.loading || !selectedIds().length || taken.length > 0;
  }

  async function dropCommit(commitId) {
    if (!(await askConfirm({
      title: "Снять привязку?",
      message: "Расход вернётся из Медиаборда — запись пересчитается.",
      confirmLabel: "Снять",
      danger: true
    }))) return;
    try {
      await api.delete("/meta/spend/commits/" + commitId);
      await loadSpendWindow();
    } catch (error) {
      notify({
        title: "Не удалось снять привязку",
        message: error && error.message ? error.message : ""
      });
    }
  }

  function spendError(message) {
    var host = byId("metaSpendError");
    if (!host) return;
    host.textContent = message || "";
    host.style.display = message ? "" : "none";
  }

  async function saveSpend() {
    var values = {};
    Array.prototype.forEach.call(
      byId("metaSpendAssign").querySelectorAll("[data-spend-field]"),
      function (input) { values[input.getAttribute("data-spend-field")] = input.value; }
    );
    if (!values.offer_id) return spendError("Выберите оффер");
    if (!values.provider_id) return spendError("Выберите агента — от него считается процент");
    if (!values.buyer_id) return spendError("Не удалось определить баера");
    spendError("");
    var window_ = spendWindow();
    var button = byId("metaSpendCommit");
    button.disabled = true;
    try {
      var result = await api.post("/meta/spend/commit", {
        record_date: spendDay(),
        hour_from: window_.from,
        hour_to: window_.to,
        campaign_ids: selectedIds(),
        offer_id: values.offer_id,
        buyer_id: values.buyer_id,
        provider_id: values.provider_id
      });
      // Отметки сняты: этот расход уже отнесён, и повторный клик по той же
      // кнопке иначе ушёл бы вторым окном на те же кампании.
      state.spendPick = {};
      syncTableChecks();
      renderSpendButton();
      closeSpendModal();
      notify({
        title: "Расход зафиксирован",
        message: "В Медиаборд ушло " + money(result.spend) + " — " +
          money(result.base_amount) + " плюс " + result.commission_pct + " % агента."
      });
    } catch (requestError) {
      spendError(requestError && requestError.message
        ? requestError.message : "Не удалось зафиксировать");
      button.disabled = false;
    }
  }

  function bindSpend() {
    var open = byId("metaSpendOpen");
    if (!open) return;
    open.addEventListener("click", openSpendModal);
    byId("metaSpendClose").addEventListener("click", closeSpendModal);
    byId("metaSpendModal").addEventListener("click", function (event) {
      if (event.target === byId("metaSpendModal")) closeSpendModal();
    });
    byId("metaSpendLoad").addEventListener("click", function () {
      loadSpendWindow(true).catch(function () {});  // ошибку рисует сам загрузчик
    });
    // День и часы живут в фильтрах. Если их поменяли при открытой форме,
    // пересчитываем: иначе на экране остаются числа прежнего окна, а кнопка
    // отправляет уже новое.
    ["metaWindowDate", "metaWindowFrom", "metaWindowTo"].forEach(function (id) {
      byId(id).addEventListener("change", function () {
        renderSpendButton();
        if (byId("metaSpendModal").style.display !== "flex") return;
        byId("metaSpendWindowValue").textContent = windowLabel();
        loadSpendWindow().catch(function () {});
      });
    });
    byId("metaSpendRows").addEventListener("click", function (event) {
      var drop = event.target.closest ? event.target.closest("[data-spend-remove]") : null;
      if (!drop) return;
      delete state.spendPick[drop.getAttribute("data-spend-remove")];
      syncTableChecks();
      renderSpendButton();
      if (!selectedIds().length) return closeSpendModal();
      renderSpendRows();
      renderSpendTotal();
    });
    byId("metaSpendCommits").addEventListener("click", function (event) {
      var drop = event.target.closest ? event.target.closest("[data-spend-drop]") : null;
      if (drop) dropCommit(drop.getAttribute("data-spend-drop"));
    });
    byId("metaSpendCommit").addEventListener("click", function () {
      saveSpend().catch(function (error) {
        spendError(error && error.message ? error.message : "Не удалось зафиксировать");
      });
    });
  }

  function renderFilters(payload) {
    var accountSelect = byId("metaAccountFilter");
    var ownerSelect = byId("metaOwnerFilter");
    var accounts = payload.accounts || [];
    // Список кабинетов приходит уже отфильтрованным по правам, поэтому при
    // выбранном кабинете его нельзя перерисовывать из ответа — останется один пункт.
    if (!state.accountId) {
      accountSelect.innerHTML = '<option value="">Все кабинеты</option>' +
        accounts.map(function (account) {
          return '<option value="' + escapeHtml(account.id) + '">' +
            escapeHtml(account.name) + "</option>";
        }).join("");
      var owners = [];
      accounts.forEach(function (account) {
        if (account.owner_id && !owners.some(function (item) {
          return item.id === account.owner_id;
        })) {
          owners.push({ id: account.owner_id, name: account.owner_name });
        }
      });
      ownerSelect.innerHTML = '<option value="">Все ответственные</option>' +
        owners.map(function (owner) {
          return '<option value="' + escapeHtml(owner.id) + '">' +
            escapeHtml(owner.name) + "</option>";
        }).join("");
    }
    accountSelect.value = state.accountId;
    ownerSelect.value = state.ownerId;
  }

  function renderSyncState() {
    var host = byId("metaSyncState");
    var run = null;
    state.connections.forEach(function (connection) {
      if (connection.last_run && (!run || (connection.last_run.started_at || "") >
        (run.started_at || ""))) {
        run = connection.last_run;
      }
    });
    var tone = "#C9BFBF";
    var text = state.connections.length ? "Не синхронизировалось" : "Нет подключения";
    if (run && (run.status === "queued" || run.status === "running")) {
      tone = "#C9821F";
      text = "Синхронизация " + (run.progress_pct || 0) + " %";
    } else if (run && run.status === "failed") {
      tone = "#C41616";
      text = "Ошибка синхронизации";
    } else if (run && run.status === "success") {
      tone = "#16B57F";
      text = "Обновлено " + formatMoment(run.finished_at);
    }
    host.innerHTML = '<span style="width:7px;height:7px;border-radius:50%;background:' +
      tone + '"></span><span style="font-size:12px;color:#6A6161;font-weight:700">' +
      escapeHtml(text) + "</span>";
    host.title = run && run.error ? run.error : "";

    var button = byId("metaSync");
    button.style.display = state.canManage && state.connections.length ? "" : "none";
    button.disabled = state.busy || !!(run && (run.status === "queued" || run.status === "running"));
    button.style.opacity = button.disabled ? ".6" : "1";
  }

  function formatMoment(value) {
    if (!value) return "—";
    var moment = new Date(value);
    if (isNaN(moment.getTime())) return "—";
    return moment.toLocaleString("ru-RU", {
      day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit"
    });
  }

  async function loadConnections() {
    try {
      var page = await api.get("/meta/connections");
      state.connections = page.items || [];
    } catch (error) {
      // Право meta.view есть, а подключений может не быть вовсе — это не ошибка экрана.
      if (!error || error.status !== 403) throw error;
      state.connections = [];
    }
  }

  async function load() {
    var range = periodRange(state.period);
    var query = "?date_from=" + range.from + "&date_to=" + range.to +
      (state.accountId ? "&account_id=" + encodeURIComponent(state.accountId) : "") +
      (state.ownerId ? "&owner_id=" + encodeURIComponent(state.ownerId) : "");
    var payload = await api.get("/meta/overview" + query);
    state.accounts = payload.accounts || [];
    state.campaigns = payload.campaigns || [];
    renderKpi(payload);
    renderAttribution(payload);
    renderFilters(payload);
    renderSyncState();
    await loadLevel();
  }

  function schedulePoll() {
    if (state.pollTimer) window.clearTimeout(state.pollTimer);
    var active = state.connections.some(function (connection) {
      return connection.last_run &&
        (connection.last_run.status === "queued" || connection.last_run.status === "running");
    });
    if (!active) return;
    state.pollTimer = window.setTimeout(async function () {
      await loadConnections();
      renderSyncState();
      if (state.connections.some(function (connection) {
        return connection.last_run && connection.last_run.status === "success";
      })) {
        await load();
      }
      schedulePoll();
    }, POLL_DELAY);
  }

  async function startSync() {
    if (!state.connections.length || state.busy) return;
    state.busy = true;
    renderSyncState();
    try {
      for (var index = 0; index < state.connections.length; index += 1) {
        await api.post("/meta/connections/" + state.connections[index].id + "/sync", {});
      }
      await loadConnections();
      schedulePoll();
    } catch (error) {
      notify({
        title: "Не удалось запустить синхронизацию",
        message: error && error.message ? error.message : ""
      });
    } finally {
      state.busy = false;
      renderSyncState();
    }
  }

  /* Способ получения токена. На запросы к Graph API он не влияет — влияет на
     то, сколько токен проживёт и что сказать человеку, когда он умрёт. */
  var AUTH_METHODS = [
    {
      code: "system_user",
      label: "System User (рекомендуется)",
      hint: "Business Settings → Users → System users → Generate token. Бессрочный, " +
        "если при выпуске выбрать «Never». Не зависит от сессии человека."
    },
    {
      code: "session",
      label: "Токен сессии (EAAB)",
      hint: "Получается автоматически из браузерной сессии с cookies и прокси аккаунта. " +
        "Живёт, пока жива сессия, и обновляется сам при синхронизации."
    },
    {
      code: "app_token",
      label: "Токен приложения",
      hint: "Панель приложения → Инструменты → Получить маркер. Живёт часы — " +
        "годится проверить доступ, но не для постоянной синхронизации."
    }
  ];

  function renderAuthMethods(selected) {
    byId("metaAuthMethods").innerHTML = AUTH_METHODS.map(function (method) {
      return '<label class="meta-choice"><input type="radio" name="metaAuthMethod" ' +
        'value="' + method.code + '"' + (method.code === selected ? " checked" : "") +
        '><span><span class="meta-choice__title">' + escapeHtml(method.label) + "</span>" +
        '<span class="meta-choice__hint">' + escapeHtml(method.hint) + "</span></span></label>";
    }).join("");
    renderSessionAuthUI("modal");
  }

  function authMethodValue() {
    var picked = byId("metaAuthMethods").querySelector("input:checked");
    return picked ? picked.value : "system_user";
  }

  function openModal() {
    var connection = state.connections[0] || null;
    var editing = !!connection;
    sessionStop();
    byId("metaModalTitle").textContent = editing
      ? "Подключение Meta Ads"
      : "Новое подключение Meta Ads";
    byId("metaFieldName").value = editing ? connection.name : "";
    byId("metaFieldToken").value = "";
    byId("metaFieldBusiness").value = editing ? (connection.business_id || "") : "";
    byId("metaFieldProxy").value = editing ? (connection.proxy_url || "") : "";
    byId("metaFieldUserAgent").value = editing ? (connection.user_agent || "") : "";
    byId("metaFieldSub").value = editing && connection.attribution_sub_id
      ? String(connection.attribution_sub_id)
      : "";
    byId("metaModalCookies").value = "";
    byId("metaModalSessionToken").value = "";
    sessionResetUi("modal");
    renderAuthMethods(editing ? (connection.auth_method || "system_user") : "system_user");
    byId("metaFieldInterval").value = editing ? connection.sync_interval_minutes : 30;
    byId("metaFieldLookback").value = editing ? connection.lookback_days : 3;
    byId("metaTokenHint").style.display = editing ? "" : "none";
    byId("metaModalDelete").style.display = editing ? "" : "none";
    byId("metaModalCheck").style.display = editing ? "" : "none";
    byId("metaModalChecks").style.display = "none";
    modalError("");
    byId("metaModalStatus").textContent = "";
    byId("metaModal").style.display = "flex";
    byId("metaFieldName").focus();
  }

  function closeModal() {
    sessionStop();
    byId("metaModal").style.display = "none";
  }

  function modalError(message) {
    var host = byId("metaModalError");
    host.textContent = message || "";
    host.style.display = message ? "" : "none";
  }

  /* Что этим токеном реально доступно. Проверяем пробными вызовами, а не
     debug_token: у токена из сессии приложение чужое, его секрета у нас нет, и
     посмотреть права официальным способом нельзя. */
  var CHECK_LABELS = {
    accounts: "Рекламные кабинеты",
    businesses: "Business Manager",
    pages: "Фан-пейджи",
    entities: "Кампании кабинета",
    pixels: "Пиксели кабинета"
  };

  async function checkConnection() {
    var connection = state.connections[0];
    if (!connection) return;
    var host = byId("metaModalChecks");
    var button = byId("metaModalCheck");
    button.disabled = true;
    byId("metaModalStatus").textContent = "Спрашиваем Meta…";
    modalError("");
    try {
      var result = await api.post("/meta/connections/" + connection.id + "/check", {});
      host.innerHTML = '<div style="font-weight:700;margin-bottom:8px">' +
        escapeHtml(result.auth_method_label || "") + "</div>" +
        Object.keys(CHECK_LABELS).map(function (key) {
          var row = (result.checks || {})[key] || {};
          return '<div style="display:flex;gap:8px;align-items:baseline">' +
            '<span style="color:' + (row.ok ? "#0E7350" : "#B91414") + '">' +
            (row.ok ? "✓" : "✕") + "</span><span>" + escapeHtml(CHECK_LABELS[key]) +
            (row.ok
              ? ' <span style="color:#9B9292">' + (row.count || 0) + "</span>"
              : ' <span style="color:#9B9292">' + escapeHtml(row.error || "") + "</span>") +
            "</span></div>";
        }).join("") +
        '<div style="margin-top:8px;color:#6A6161;font-weight:500">' +
        escapeHtml(result.auth_method_hint || "") + "</div>";
      host.style.display = "";
    } catch (error) {
      modalError(error && error.message ? error.message : "Meta не ответила");
    } finally {
      button.disabled = false;
      byId("metaModalStatus").textContent = "";
    }
  }

  async function saveConnection() {
    var connection = state.connections[0] || null;
    var method = authMethodValue();
    var token = byId("metaFieldToken").value.trim();
    var name = byId("metaFieldName").value.trim();
    var proxy = byId("metaFieldProxy").value.trim();
    if (!name) {
      modalError("Укажите название подключения");
      return;
    }
    if (method === "session") {
      // Токен сессии живёт только со своим прокси, а получается только из
      // браузерной сессии — вставка готовой строки здесь не допускается.
      if (!proxy) {
        modalError("Токен сессии (EAAB) требует прокси — без него Meta заблокирует сессию и аккаунт");
        return;
      }
      token = byId("metaModalSessionToken").value;
      if (!connection && !token) {
        modalError("Получите токен через браузер (блок «Браузер Facebook»)");
        return;
      }
    } else if (!connection && !token) {
      modalError("Вставьте токен");
      return;
    }
    var sub = byId("metaFieldSub").value;
    var payload = {
      name: name,
      business_id: byId("metaFieldBusiness").value.trim() || null,
      attribution_sub_id: sub ? Number(sub) : null,
      auth_method: method,
      proxy_url: proxy || null,
      user_agent: byId("metaFieldUserAgent").value.trim() || null,
      sync_interval_minutes: Number(byId("metaFieldInterval").value) || 30,
      lookback_days: Number(byId("metaFieldLookback").value) || 3
    };
    if (token) payload.access_token = token;

    modalError("");
    // Токен проверяется на сервере запросом в Meta — это заметная пауза, и без
    // подписи кажется, что кнопка не нажалась.
    byId("metaModalStatus").textContent = "Проверяем токен в Meta…";
    byId("metaModalSave").disabled = true;
    try {
      if (connection) {
        await api.patch("/meta/connections/" + connection.id, payload);
      } else {
        await api.post("/meta/connections", payload);
      }
      await loadConnections();
      closeModal();
      await load();
    } catch (error) {
      modalError(error && error.message ? error.message : "Не удалось сохранить подключение");
    } finally {
      byId("metaModalSave").disabled = false;
      byId("metaModalStatus").textContent = "";
    }
  }

  async function deleteConnection() {
    var connection = state.connections[0];
    if (!connection) return;
    var confirmed = await askConfirm({
      title: "Удалить подключение «" + connection.name + "»?",
      message: "Кабинеты, кампании и загруженная статистика Meta будут удалены " +
        "вместе с ним. Ручных данных в них нет — после нового подключения всё " +
        "вычитается заново.",
      confirmLabel: "Удалить",
      danger: true
    });
    if (!confirmed) return;
    try {
      await api.delete("/meta/connections/" + connection.id);
      await loadConnections();
      closeModal();
      await load();
    } catch (error) {
      modalError(error && error.message ? error.message : "Не удалось удалить подключение");
    }
  }

  /* ---------- вкладки ---------- */

  var TABS = ["overview", "launches", "rules"];

  function setTab(name) {
    state.tab = name;
    TABS.forEach(function (tab) {
      var section = byId("metaTab" + tab.charAt(0).toUpperCase() + tab.slice(1));
      if (section) section.style.display = tab === name ? "" : "none";
    });
    Array.prototype.forEach.call(document.querySelectorAll(".meta-tab"), function (button) {
      var active = button.getAttribute("data-tab") === name;
      button.classList.toggle("meta-tab--active", active);
    });
    // Возврат на «Заливы» всегда открывает список: мастер на середине шага
    // и очередь — это состояния, в которых человек не ожидает оказаться,
    // просто переключив верхнюю вкладку.
    if (name === "launches" && state.launchView !== "bundles") setLaunchView("bundles");
    var loader = {
      launches: loadTemplates,
      rules: loadRules
    }[name];
    if (loader) loader().catch(showFailure);
  }

  async function loadReference() {
    if (state.reference) return state.reference;
    state.reference = await api.get("/meta/reference");
    return state.reference;
  }

  function referenceAccounts() {
    return (state.reference && state.reference.accounts) || [];
  }

  function dictOptions(dict, selected) {
    return Object.keys(dict || {}).map(function (key) {
      return { value: key, label: dict[key], selected: key === selected };
    });
  }

  /* ---------- универсальная форма ---------- */

  function fieldHtml(field) {
    var span = field.half ? "" : "grid-column:1/-1;";
    var label = '<span>' + escapeHtml(field.label) + "</span>";
    var body;
    if (field.type === "select") {
      body = '<select class="meta-control meta-select" data-field="' + escapeHtml(field.name) +
        '">' + (field.options || []).map(function (option) {
          return '<option value="' + escapeHtml(option.value) + '"' +
            (option.selected ? " selected" : "") + ">" + escapeHtml(option.label) + "</option>";
        }).join("") + "</select>";
    } else if (field.type === "textarea") {
      body = '<textarea class="meta-control" data-field="' + escapeHtml(field.name) + '">' +
        escapeHtml(field.value || "") + "</textarea>";
    } else if (field.type === "checkbox") {
      return '<label class="meta-pick" style="' + span + 'align-items:center">' +
        '<input type="checkbox" data-field="' + escapeHtml(field.name) + '"' +
        (field.value ? " checked" : "") + ">" +
        '<span style="font-size:12.5px;font-weight:600;color:#3A3030">' +
        escapeHtml(field.label) +
        (field.hint ? '<span style="display:block;font-size:11px;color:#9B9292;font-weight:500;margin-top:3px">' +
          escapeHtml(field.hint) + "</span>" : "") + "</span></label>";
    } else if (field.type === "conditions") {
      body = '<div data-conditions="' + escapeHtml(field.name) + '" style="display:grid;gap:8px">' +
        conditionRowsHtml(field.value) + "</div>" +
        '<button type="button" class="meta-action" data-condition-add style="margin-top:9px;' +
        'align-self:flex-start">+ Условие</button>';
    } else if (field.type === "checklist") {
      body = '<div data-checklist="' + escapeHtml(field.name) + '" style="display:grid;gap:8px;' +
        'max-height:210px;overflow:auto">' + checklistHtml(field) + "</div>";
    } else {
      body = '<input class="meta-control" type="' + (field.type || "text") + '" data-field="' +
        escapeHtml(field.name) + '" value="' + escapeHtml(field.value === null ||
        field.value === undefined ? "" : field.value) + '"' +
        (field.placeholder ? ' placeholder="' + escapeHtml(field.placeholder) + '"' : "") +
        (field.min !== undefined ? ' min="' + field.min + '"' : "") +
        (field.max !== undefined ? ' max="' + field.max + '"' : "") +
        (field.step ? ' step="' + field.step + '"' : "") + ">";
    }
    return '<label class="meta-field" style="' + span + '">' + label + body +
      (field.hint ? '<span style="display:block;font-size:11px;color:#9B9292;font-weight:600;' +
        'margin-top:5px;line-height:1.5">' + escapeHtml(field.hint) + "</span>" : "") + "</label>";
  }

  /* Условия правила: метрика, оператор, значение. Соединяются И, поэтому
     порядок значения не имеет и строки можно удалять любыми. */
  function conditionRowsHtml(list) {
    return (list || []).map(conditionRowHtml).join("");
  }

  function conditionRowHtml(condition) {
    var reference = state.reference || {};
    var value = condition || { metric: "roi", operator: "lt", value: "0" };
    return '<div class="meta-condition" data-condition-row>' +
      '<select class="meta-control meta-select" data-condition-metric>' +
      dictOptions(reference.metrics, value.metric).map(function (option) {
        return '<option value="' + escapeHtml(option.value) + '"' +
          (option.selected ? " selected" : "") + ">" + escapeHtml(option.label) + "</option>";
      }).join("") + "</select>" +
      '<select class="meta-control meta-select" data-condition-operator>' +
      dictOptions(reference.rule_operators, value.operator).map(function (option) {
        return '<option value="' + escapeHtml(option.value) + '"' +
          (option.selected ? " selected" : "") + ">" + escapeHtml(option.label) + "</option>";
      }).join("") + "</select>" +
      '<input class="meta-control" type="number" step="0.01" data-condition-value value="' +
      escapeHtml(value.value === null || value.value === undefined ? "0" : value.value) + '">' +
      '<button type="button" class="meta-action meta-action--danger" data-condition-remove ' +
      'aria-label="Убрать условие" style="padding:0;height:38px">✕</button></div>';
  }

  function checklistHtml(field) {
    if (!(field.options || []).length) {
      return '<div style="font-size:11.5px;color:#9B9292;font-weight:600">' +
        escapeHtml(field.empty || "Нечего выбрать") + "</div>";
    }
    return field.options.map(function (option) {
      return '<label class="meta-pick" style="align-items:center;padding:9px 12px">' +
        '<input type="checkbox" data-check="' + escapeHtml(field.name) + '" value="' +
        escapeHtml(option.value) + '"' + (option.selected ? " checked" : "") + ">" +
        '<span style="font-size:12.5px;font-weight:600;color:#3A3030">' +
        escapeHtml(option.label) + "</span></label>";
    }).join("");
  }

  function openForm(config) {
    state.form = config;
    byId("metaFormTitle").textContent = config.title;
    byId("metaFormSubtitle").textContent = config.subtitle || "";
    var body = byId("metaFormBody");
    body.style.gridTemplateColumns = "1fr 1fr";
    body.innerHTML = config.fields.map(fieldHtml).join("") +
      (config.note ? '<div class="meta-note" style="grid-column:1/-1">' + config.note + "</div>" : "");
    byId("metaFormDelete").style.display = config.onDelete ? "" : "none";
    formError("");
    byId("metaFormStatus").textContent = "";
    byId("metaFormModal").style.display = "flex";
    var first = body.querySelector("[data-field]");
    if (first) first.focus();
  }

  function closeForm() {
    byId("metaFormModal").style.display = "none";
    state.form = null;
  }

  function formError(message) {
    var host = byId("metaFormError");
    host.textContent = message || "";
    host.style.display = message ? "" : "none";
  }

  function readForm() {
    var values = {};
    Array.prototype.forEach.call(
      byId("metaFormBody").querySelectorAll("[data-field]"),
      function (input) {
        values[input.getAttribute("data-field")] =
          input.type === "checkbox" ? input.checked : input.value;
      }
    );
    Array.prototype.forEach.call(
      byId("metaFormBody").querySelectorAll("[data-conditions]"),
      function (host) {
        values[host.getAttribute("data-conditions")] = Array.prototype.map.call(
          host.querySelectorAll("[data-condition-row]"),
          function (row) {
            return {
              metric: row.querySelector("[data-condition-metric]").value,
              operator: row.querySelector("[data-condition-operator]").value,
              value: row.querySelector("[data-condition-value]").value || "0"
            };
          }
        );
      }
    );
    Array.prototype.forEach.call(
      byId("metaFormBody").querySelectorAll("[data-checklist]"),
      function (host) {
        var name = host.getAttribute("data-checklist");
        values[name] = Array.prototype.map.call(
          host.querySelectorAll("input[data-check]:checked"),
          function (input) { return input.value; }
        );
      }
    );
    return values;
  }

  async function submitForm() {
    if (!state.form) return;
    formError("");
    byId("metaFormSave").disabled = true;
    byId("metaFormStatus").textContent = state.form.busyText || "Сохраняем…";
    try {
      await state.form.onSave(readForm());
      closeForm();
    } catch (error) {
      formError(error && error.message ? error.message : "Не удалось сохранить");
    } finally {
      byId("metaFormSave").disabled = false;
      byId("metaFormStatus").textContent = "";
    }
  }

  /* ---------- заливы (ТЗ 3.3, 3.4) ---------- */

  var LAUNCH_STATUS_LABEL = {
    draft: "Черновик",
    publishing: "Публикуется",
    paused: "На паузе",
    active: "Активен",
    stopped: "Остановлен",
    failed: "Ошибка"
  };
  var LAUNCH_STATUS_TONE = {
    draft: { color: "#6A6161", background: "#F2EDED" },
    publishing: { color: "#2C4E77", background: "#EFF5FE" },
    paused: { color: "#C9821F", background: "#FFF6E9" },
    active: { color: "#16B57F", background: "#E4F7F0" },
    stopped: { color: "#6A6161", background: "#F2EDED" },
    failed: { color: "#C41616", background: "#FCF1F1" }
  };

  function launchChip(status) {
    var tone = LAUNCH_STATUS_TONE[status] || LAUNCH_STATUS_TONE.draft;
    return '<span class="meta-chip" style="color:' + tone.color + ";background:" +
      tone.background + '"><span class="meta-dot" style="background:' + tone.color +
      '"></span>' + escapeHtml(LAUNCH_STATUS_LABEL[status] || status) + "</span>";
  }

  async function loadLaunches() {
    await loadReference();
    var query = state.launchStatus ? "?status=" + encodeURIComponent(state.launchStatus) : "";
    var page = await api.get("/meta/launches" + query);
    state.launches = page.items || [];
    state.selected = state.selected.filter(function (id) {
      return state.launches.some(function (launch) { return launch.id === id; });
    });
    renderLaunches();
  }

  function renderLaunches() {
    var body = byId("metaLaunchBody");
    byId("metaLaunchCreate").style.display = state.canLaunch ? "" : "none";
    if (!state.launches.length) {
      body.innerHTML = '<tr><td colspan="9" style="padding:44px 20px;text-align:center;' +
        'color:#9B9292;font-size:13px">Заливов пока нет' +
        (state.canLaunch ? ". Создайте первый — он сохранится черновиком и никуда не уйдёт, " +
          "пока вы не нажмёте «Опубликовать»." : "") + "</td></tr>";
      byId("metaLaunchCount").textContent = "Заливов: 0";
      renderBulkBar();
      return;
    }
    body.innerHTML = state.launches.map(function (launch) {
      return '<tr class="meta-row" style="border-bottom:1px solid #F7F4F4">' +
        '<td class="meta-cell meta-cell--left" style="padding-left:20px">' +
        (state.canLaunch ? '<input type="checkbox" data-launch-pick="' + escapeHtml(launch.id) +
          '"' + (state.selected.indexOf(launch.id) >= 0 ? " checked" : "") +
          ' aria-label="Выбрать залив">' : "") + "</td>" +
        '<td class="meta-cell meta-cell--left">' +
        '<div style="font-weight:700;font-size:13px">' + escapeHtml(launch.name) + "</div>" +
        '<div style="font-size:10.5px;color:#9B9292;margin-top:2px">' +
        (launch.campaign_external_id
          ? "кампания " + escapeHtml(launch.campaign_external_id)
          : "не опубликован") + "</div>" +
        (launch.last_error
          ? '<div style="font-size:10.5px;color:#C41616;margin-top:3px;max-width:320px;' +
            'white-space:normal">' + escapeHtml(launch.last_error) + "</div>"
          : "") + "</td>" +
        '<td class="meta-cell meta-cell--left" style="color:#6A6161">' +
        escapeHtml(launch.account_name) + "</td>" +
        '<td class="meta-cell meta-cell--left">' + escapeHtml(launch.geo || "—") + "</td>" +
        '<td class="meta-cell meta-cell--left" style="color:#6A6161">' +
        escapeHtml(launch.owner_name || "—") + "</td>" +
        '<td class="meta-cell">' + money(launch.daily_budget) + "</td>" +
        '<td class="meta-cell">' + num(launch.creatives) + "</td>" +
        '<td class="meta-cell meta-cell--left">' + launchChip(launch.status) + "</td>" +
        '<td class="meta-cell meta-cell--left" style="padding-right:20px">' +
        launchActions(launch) + "</td></tr>";
    }).join("");
    byId("metaLaunchCount").textContent = "Заливов: " + state.launches.length;
    renderBulkBar();
  }

  function launchActions(launch) {
    if (!state.canLaunch) {
      return '<button class="meta-action" data-launch-open="' + escapeHtml(launch.id) +
        '">Открыть</button>';
    }
    var buttons = ['<button class="meta-action" data-launch-open="' + escapeHtml(launch.id) +
      '">Изменить</button>'];
    if (!launch.campaign_external_id) {
      buttons.push('<button class="meta-action meta-action--primary" data-launch-publish="' +
        escapeHtml(launch.id) + '">Опубликовать</button>');
    } else if (launch.status === "active") {
      buttons.push('<button class="meta-action" data-launch-pause="' + escapeHtml(launch.id) +
        '">Пауза</button>');
    } else {
      buttons.push('<button class="meta-action" data-launch-resume="' + escapeHtml(launch.id) +
        '">Запустить</button>');
    }
    return '<div style="display:flex;gap:6px">' + buttons.join("") + "</div>";
  }

  function renderBulkBar() {
    var bar = byId("metaBulkBar");
    if (!state.canLaunch || !state.selected.length) {
      bar.style.display = "none";
      return;
    }
    bar.style.display = "flex";
    byId("metaBulkCount").textContent = "Выбрано заливов: " + state.selected.length;
  }

  function launchFields(launch, creatives) {
    var accounts = referenceAccounts();
    var reference = state.reference || {};
    var accountId = launch.account_id || (accounts[0] && accounts[0].id) || "";
    return [
      { name: "name", label: "Название залива", value: launch.name || "",
        placeholder: "DE | Nervio | broad" },
      { name: "account_id", label: "Рекламный кабинет", type: "select", half: true,
        options: accounts.map(function (account) {
          return { value: account.id, label: account.name, selected: account.id === accountId };
        }) },
      { name: "template_id", label: "Шаблон", type: "select", half: true,
        options: [{ value: "", label: "Без шаблона" }].concat(
          state.templates.map(function (template) {
            return { value: template.id, label: template.name,
              selected: template.id === launch.template_id };
          })) },
      { name: "offer_id", label: "Оффер", type: "select", half: true,
        options: [{ value: "", label: "Не указан" }].concat(
          (reference.offers || []).map(function (offer) {
            return { value: offer.id, label: offer.name, selected: offer.id === launch.offer_id };
          })) },
      { name: "partner_id", label: "Партнёрка", type: "select", half: true,
        options: [{ value: "", label: "Не указана" }].concat(
          (reference.partners || []).map(function (partner) {
            return { value: partner.id, label: partner.name,
              selected: partner.id === launch.partner_id };
          })) },
      { name: "geo", label: "GEO", value: launch.geo || "", half: true, placeholder: "DE" },
      { name: "owner_id", label: "Ответственный", type: "select", half: true,
        options: [{ value: "", label: "Я" }].concat(state.buyers.map(function (buyer) {
          return { value: buyer.id, label: buyer.name, selected: buyer.id === launch.owner_id };
        })) },
      { name: "daily_budget", label: "Дневной бюджет", type: "number", half: true, min: 0,
        step: "0.01", value: launch.daily_budget || "" },
      { name: "spend_limit", label: "Лимит на кампанию", type: "number", half: true, min: 0,
        step: "0.01", value: launch.spend_limit === null ? "" : launch.spend_limit,
        hint: "необязательно" },
      { name: "start_date", label: "Дата старта", type: "date", half: true,
        value: launch.start_date || "" },
      { name: "end_date", label: "Дата остановки", type: "date", half: true,
        value: launch.end_date || "" },
      { name: "link_url", label: "Ссылка объявления", value: launch.link_url || "",
        placeholder: "https://track.example/click?...",
        hint: "sub_id с ID кампании CRM допишет сама при публикации" },
      { name: "primary_text", label: "Основной текст", type: "textarea",
        value: launch.primary_text || "" },
      { name: "headline", label: "Заголовок", half: true, value: launch.headline || "" },
      { name: "description", label: "Описание", half: true, value: launch.description || "" },
      { name: "call_to_action", label: "Кнопка", type: "select", half: true,
        options: dictOptions(reference.call_to_actions,
          launch.call_to_action || "LEARN_MORE") },
      { name: "page_id", label: "ID страницы Facebook", half: true, value: launch.page_id || "",
        hint: "если не задан в шаблоне" },
      { name: "pixel_id", label: "ID пикселя", half: true, value: launch.pixel_id || "" },
      { name: "creative_ids", label: "Креативы", type: "checklist",
        empty: "В этом кабинете ещё нет загруженных креативов",
        options: creatives.map(function (creative) {
          return { value: creative.id,
            label: creative.name + " · " + (creative.kind === "video" ? "видео" : "картинка"),
            selected: (launch.creative_ids || []).indexOf(creative.id) >= 0 };
        }) },
      { name: "activate_on_publish", label: "Снять с паузы сразу после публикации",
        type: "checkbox", value: !!launch.activate_on_publish,
        hint: "Иначе кампания останется на паузе и деньги тратиться не начнут" }
    ];
  }


  /* ==========================================================
     ЗАЛИТЬ СВЯЗКУ — мастер из трёх шагов

     Связка (шаблон) описывает таргет, шаг «Кабинеты» задаёт, куда её лить, а
     креативы выбираются отдельно для каждого кабинета: один и тот же файл в
     другом кабинете имеет другой хэш, общего списка у пачки быть не может.
     ========================================================== */

  var UPLOAD_STEPS = ["Настройки", "Кабинеты", "Креативы"];

  function setLaunchView(view) {
    state.launchView = view;
    Array.prototype.forEach.call(document.querySelectorAll("[data-launch-view]"),
      function (button) {
        var active = button.getAttribute("data-launch-view") === view;
        button.classList.toggle("meta-level--active", active);
        button.setAttribute("aria-pressed", active ? "true" : "false");
      });
    byId("metaBundleList").style.display = view === "bundles" ? "" : "none";
    // Таблица заливов живёт вместе с очередью: очередь — это и есть «что залито
    // и что с ним происходит», а отдельной вкладкой она дублировала бы журнал.
    byId("metaLaunchList").style.display = view === "queue" ? "" : "none";
    byId("metaLaunchWizard").style.display = view === "wizard" ? "" : "none";
    byId("metaLaunchQueue").style.display = view === "queue" ? "" : "none";
    if (view === "bundles") loadTemplates().catch(showFailure);
    if (view === "wizard") startUpload();
    if (view === "queue") {
      loadLaunches().catch(showFailure);
      loadQueue().catch(showFailure);
    }
  }

  function startUpload() {
    if (state.upload) return renderUpload();
    state.upload = {
      step: 1,
      accountIds: [],
      creatives: {},
      catalog: {},
      advanced: false,
      // Что задано на конкретный кабинет и что Meta про него рассказала.
      perAccount: {},
      assets: {},
      accountSearch: "",
      onlyPicked: false,
      // Объявления пачки: тексты и файлы у них общие, хэши в кабинетах — свои.
      ads: [],
      languages: false,
      split: false,
      unique: false,
      values: {
        name: "", template_id: "", offer_id: "", partner_id: "", owner_id: "",
        geo: "", daily_budget: "", spend_limit: "", start_date: "", end_date: "",
        link_url: "", primary_text: "", headline: "", description: "",
        call_to_action: "LEARN_MORE", page_id: "", pixel_id: "",
        url_tags: "", display_link: "",
        activate_on_publish: true,
        // Блок «Время»: когда стартовать, когда заливать и что оставить на паузе.
        start_mode: "now", start_at: "",
        publish_mode: "now", publish_at: "",
        pause_campaigns: false, pause_adsets: false, pause_ads: false,
        account_delay_seconds: "0", adset_count: 1
      }
    };
    loadReference()
      .then(function () { return state.templates.length ? null : loadTemplates(true); })
      .then(renderUpload)
      .catch(showFailure);
  }

  function uploadStepsHtml(step) {
    return UPLOAD_STEPS.map(function (label, index) {
      var number = index + 1;
      var cls = number === step ? " meta-up-step--active"
        : number < step ? " meta-up-step--done" : "";
      return '<div class="meta-up-step' + cls + '">' +
        '<span class="meta-up-dot">' + (number < step ? "\u2713" : number) + "</span>" +
        '<span class="meta-up-label">' + escapeHtml(label) + "</span></div>" +
        (number < UPLOAD_STEPS.length ? '<span class="meta-up-line"></span>' : "");
    }).join("");
  }

  function uploadField(name, label, attrs, hint) {
    var value = state.upload.values[name];
    return '<label class="meta-field"><span>' + escapeHtml(label) + "</span>" +
      '<input class="meta-control" data-up-field="' + name + '" ' + (attrs || "") +
      ' value="' + escapeHtml(value == null ? "" : String(value)) + '">' +
      (hint ? '<span style="display:block;font-size:11px;color:#9B9292;font-weight:600;' +
        'margin-top:5px">' + escapeHtml(hint) + "</span>" : "") + "</label>";
  }

  function uploadSelect(name, label, options, hint) {
    var value = String(state.upload.values[name] || "");
    return '<label class="meta-field"><span>' + escapeHtml(label) + "</span>" +
      '<select class="meta-control meta-select" data-up-field="' + name + '">' +
      options.map(function (option) {
        return '<option value="' + escapeHtml(option.value) + '"' +
          (String(option.value) === value ? " selected" : "") + ">" +
          escapeHtml(option.label) + "</option>";
      }).join("") + "</select>" +
      (hint ? '<span style="display:block;font-size:11px;color:#9B9292;font-weight:600;' +
        'margin-top:5px">' + escapeHtml(hint) + "</span>" : "") + "</label>";
  }

  function uploadTextarea(name, label) {
    return '<label class="meta-field"><span>' + escapeHtml(label) + "</span>" +
      '<textarea class="meta-control" data-up-field="' + name + '">' +
      escapeHtml(state.upload.values[name] || "") + "</textarea></label>";
  }

  function ctaOptions() {
    var list = (state.reference && state.reference.call_to_actions) || ["LEARN_MORE"];
    return Object.keys(list).map(function (code) { return { value: code, label: list[code] }; });
  }

  function namedOptions(list, placeholder) {
    return [{ value: "", label: placeholder }].concat(
      (list || []).map(function (row) { return { value: row.id, label: row.name }; })
    );
  }

  function uploadBundle() {
    return state.templates.filter(function (row) {
      return row.id === state.upload.values.template_id;
    })[0] || null;
  }

  /* Выбранная связка заполняет залив собой: мастер нужен, чтобы залить готовое,
     а не набирать те же поля второй раз. Расширенный режим оставлен для случая,
     когда под конкретный кабинет что-то надо поменять. */
  function applyBundleToUpload(bundle) {
    var values = state.upload.values;
    if (!bundle) return;
    var blocks = bundleSettings(bundle);
    values.name = bundle.name || values.name;
    values.geo = (bundle.geo || [])[0] || values.geo;
    values.daily_budget = bundle.daily_budget || bundle.lifetime_budget || values.daily_budget;
    values.link_url = blocks.ad.link_url || values.link_url;
    values.headline = blocks.ad.headline || values.headline;
    values.primary_text = blocks.ad.primary_text || values.primary_text;
    values.description = blocks.ad.description || values.description;
    values.call_to_action = bundle.call_to_action || values.call_to_action;
    values.page_id = bundle.page_id || values.page_id;
  }

  function bundleSummaryHtml() {
    var bundle = uploadBundle();
    if (!bundle) {
      return '<div style="display:flex;align-items:center;justify-content:center;height:100%;' +
        'min-height:220px;text-align:center;color:#9B9292;font-size:12.5px;font-weight:600;' +
        'line-height:1.6">Выберите связку —<br>здесь появится информация</div>';
    }
    var blocks = bundleSettings(bundle);
    var reference = bundleReference();
    var attribution = (reference.attribution_windows || []).filter(function (row) {
      return row.code === blocks.adset.attribution;
    })[0];
    var location = (reference.location_types || []).filter(function (row) {
      return row.code === blocks.adset.location_type;
    })[0];
    var interests = (bundle.interests || []).map(function (item) {
      return item.name || item.id;
    }).join(", ");
    var excluded = (blocks.adset.excluded_interests || []).map(function (item) {
      return item.name || item.id;
    }).join(", ");
    var budget = bundle.daily_budget || bundle.lifetime_budget;
    var rows = [
      ["Цель кампании", '<span class="meta-tag">' + escapeHtml(goalLabel(blocks.campaign.goal)) +
        "</span>"],
      ["Бюджет", budget
        ? escapeHtml(money(budget) + " " + (blocks.campaign.budget_currency || "USD") +
          (blocks.campaign.budget_kind === "lifetime" ? " на весь срок" : " в день"))
        : "—"],
      ["Окно конверсии", escapeHtml(attribution ? attribution.label : "—")],
      ["Возраст", (bundle.age_min || 18) + "–" + (bundle.age_max || 65)],
      ["Пол", (bundle.genders || []).length
        ? ((bundle.genders || [])[0] === 1 ? "Мужчины" : "Женщины") : "Любой"],
      ["Гео", escapeHtml((bundle.geo || []).join(", ") || "—") +
        (location ? ' <span style="color:#9B9292">(' + escapeHtml(location.label) + ")</span>" : "")],
      ["Исключить гео", escapeHtml((blocks.adset.excluded_geo || []).join(", ") || "—")],
      ["Языки", escapeHtml((bundle.languages || []).join(", ") || "—")],
      ["Интересы", escapeHtml(interests || "—")],
      ["Исключить интересы", escapeHtml(excluded || "—")],
      ["Плейсменты", blocks.adset.auto_placements === false
        ? escapeHtml(((bundle.placements || {}).publisher_platforms || []).join(", ") || "—")
        : '<span class="meta-tag">Авто</span>']
    ];
    return '<div style="font-weight:700;font-size:15px;padding-bottom:12px;border-bottom:' +
      '1px solid #EBE6E6;margin-bottom:14px;overflow-wrap:anywhere">' +
      escapeHtml(bundle.name) + "</div>" +
      '<dl class="meta-summary">' + rows.map(function (row) {
        return "<dt>" + escapeHtml(row[0]) + "</dt><dd>" + row[1] + "</dd>";
      }).join("") + "</dl>";
  }

  function timeCardHtml() {
    var values = state.upload.values;
    var seg = function (name, options) {
      return '<div class="meta-seg">' + options.map(function (option) {
        return '<button type="button" data-up-seg="' + name + '" data-up-value="' +
          escapeHtml(option.value) + '" class="' +
          (values[name] === option.value ? "meta-seg--on" : "") + '">' +
          escapeHtml(option.label) + "</button>";
      }).join("") + "</div>";
    };
    var toggle = function (name, label) {
      return '<label class="meta-switch"><input type="checkbox" data-up-field="' + name + '"' +
        (values[name] ? " checked" : "") + '><span class="meta-switch__box"></span><span>' +
        escapeHtml(label) + "</span></label>";
    };
    return '<div class="meta-card"><div class="meta-card__title">Время</div>' +
      '<div style="display:grid;gap:14px">' +
      '<div class="meta-line"><span>Запускать:</span><div style="display:flex;' +
      'align-items:center;gap:10px;flex-wrap:wrap">' +
      seg("start_mode", [
        { value: "now", label: "Сразу" },
        { value: "midnight", label: "Ближайшая полночь" },
        { value: "custom", label: "Своё время" }
      ]) +
      (values.start_mode === "custom"
        ? '<input class="meta-control" style="padding:0 13px" type="datetime-local" ' +
          'data-up-field="start_at" value="' + escapeHtml(values.start_at) + '">'
        : "") + "</div></div>" +
      '<div class="meta-line"><span>Дата залива:</span><div style="display:flex;' +
      'align-items:center;gap:10px;flex-wrap:wrap">' +
      seg("publish_mode", [
        { value: "now", label: "Сразу" },
        { value: "custom", label: "Своё время" }
      ]) +
      (values.publish_mode === "custom"
        ? '<input class="meta-control" style="padding:0 13px" type="datetime-local" ' +
          'data-up-field="publish_at" value="' + escapeHtml(values.publish_at) + '">'
        : "") + "</div></div>" +
      '<div style="display:grid;gap:11px;padding-top:4px">' +
      toggle("pause_ads", "Поставить объявления на паузу") +
      toggle("pause_adsets", "Поставить адсеты на паузу") +
      toggle("pause_campaigns", "Поставить кампании на паузу") +
      '<label class="meta-switch"><input type="checkbox" data-up-pause-gap' +
      (Number(state.upload.values.account_delay_seconds) > 0 ? " checked" : "") +
      '><span class="meta-switch__box"></span><span>Пауза между кабинетами' +
      '<span class="meta-switch__hint">Заливы уходят в очередь не одновременно — так пачка ' +
      "не выглядит одним движением.</span></span></label>" +
      (Number(values.account_delay_seconds) > 0
        ? '<div class="meta-line"><span>Пауза, секунд</span><input class="meta-control" ' +
          'style="width:120px;padding:0 13px" type="number" min="1" max="3600" ' +
          'data-up-field="account_delay_seconds" value="' +
          escapeHtml(String(values.account_delay_seconds)) + '"></div>'
        : "") +
      "</div></div></div>";
  }

  function advancedCardHtml() {
    var reference = state.reference || {};
    return '<div class="meta-card"><div class="meta-card__title">Параметры залива</div>' +
      '<div class="meta-up-grid">' +
      uploadField("name", "Название залива", 'maxlength="240"') +
      uploadSelect("offer_id", "Оффер", namedOptions(reference.offers, "Не выбран")) +
      uploadSelect("partner_id", "Партнёрка", namedOptions(reference.partners, "Не выбрана")) +
      uploadField("geo", "GEO", 'maxlength="12" placeholder="DE"') +
      uploadField("daily_budget", "Бюджет", 'type="number" min="0" step="0.01"') +
      uploadField("spend_limit", "Лимит расхода", 'type="number" min="0" step="0.01"',
        "Необязательно") +
      uploadSelect("call_to_action", "Кнопка", ctaOptions()) +
      uploadField("page_id", "Fan page ID", 'maxlength="60"', "Пусто — возьмётся из связки") +
      "</div>" +
      '<div style="margin-top:14px">' +
      uploadField("link_url", "Ссылка", 'placeholder="https://..."',
        "Пусто — возьмётся из связки") + "</div>" +
      '<div class="meta-up-grid" style="margin-top:14px">' +
      uploadField("url_tags", "Параметры URL", 'placeholder="utm_source=fb&utm_campaign={{campaign.id}}"',
        "Уходят в url_tags объявления — Meta допишет их к ссылке сама") +
      uploadField("display_link", "Отображаемый URL", 'maxlength="240" placeholder="example.com"',
        "Что видно в объявлении вместо ссылки на трекер") + "</div>" +
      '<div class="meta-up-grid" style="margin-top:14px">' +
      uploadField("headline", "Заголовок", 'maxlength="240"') +
      uploadField("description", "Описание", 'maxlength="240"') + "</div>" +
      '<div style="margin-top:14px">' + uploadTextarea("primary_text", "Основной текст") +
      "</div></div>";
  }

  function renderUploadSettings() {
    var options = [{ value: "", label: "Выберите связку или создайте новую" }].concat(
      state.templates.map(function (bundle) {
        return { value: bundle.id, label: bundle.name };
      })
    );
    var left = '<div class="meta-card">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;gap:14px;' +
      'flex-wrap:wrap;margin-bottom:14px">' +
      '<div class="meta-card__title" style="margin:0">Связка</div>' +
      '<label class="meta-switch"><input type="checkbox" data-up-advanced' +
      (state.upload.advanced ? " checked" : "") +
      '><span class="meta-switch__box"></span><span>Расширенный режим</span></label></div>' +
      '<div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap">' +
      '<select class="meta-control meta-select" style="flex:1;min-width:220px" ' +
      'data-up-field="template_id">' +
      options.map(function (option) {
        return '<option value="' + escapeHtml(option.value) + '"' +
          (option.value === state.upload.values.template_id ? " selected" : "") + ">" +
          escapeHtml(option.label) + "</option>";
      }).join("") + "</select>" +
      '<button type="button" class="meta-action meta-action--primary" data-up-bundle-new ' +
      'style="height:40px;padding:0 16px">+ Создать связку</button></div></div>' +
      (state.upload.values.template_id ? timeCardHtml() : "") +
      (state.upload.advanced ? advancedCardHtml() : "");

    return '<div class="meta-up-split"><div class="meta-up-col">' + left + "</div>" +
      '<div class="meta-card" style="align-self:start">' + bundleSummaryHtml() + "</div></div>";
  }

  /* ----- шаг 2: кабинеты -----

     На кабинет приходится своё: страница, пиксель, ссылка и бюджет. Общее у
     пачки — связка и время, поэтому в таблице только то, что действительно
     различается. Страницы и пиксели спрашиваем у Meta, когда кабинет отметили:
     запрашивать их на все видимые кабинеты значило бы полсотни запросов ради
     трёх выбранных. */

  function accountRow(account) {
    if (!state.upload.perAccount[account.id]) {
      state.upload.perAccount[account.id] = {
        page_id: "", pixel_id: "", link_url: "", daily_budget: ""
      };
    }
    return state.upload.perAccount[account.id];
  }

  function accountAssets(id) {
    return state.upload.assets[id] || { pages: [], pixels: [], loading: false };
  }

  async function loadAccountAssets(id) {
    if (state.upload.assets[id]) return;
    state.upload.assets[id] = { pages: [], pixels: [], loading: true };
    renderUpload();
    try {
      var data = await api.get("/meta/accounts/" + id + "/assets");
      state.upload.assets[id] = {
        pages: data.pages || [], pixels: data.pixels || [], loading: false
      };
    } catch (error) {
      state.upload.assets[id] = {
        pages: [], pixels: [], loading: false,
        error: error && error.message ? error.message : "Meta не ответила"
      };
    }
    renderUpload();
  }

  function assetSelect(accountId, field, list, placeholder, loading, error) {
    var value = accountRow({ id: accountId })[field];
    if (loading) {
      return '<span style="font-size:11.5px;color:#9B9292;font-weight:600">Загружаем…</span>';
    }
    if (error) {
      return '<span style="font-size:11.5px;color:#B91414;font-weight:600">' +
        escapeHtml(error) + "</span>";
    }
    if (!list.length) {
      return '<span style="font-size:11.5px;color:#9B9292;font-weight:600">' +
        escapeHtml(placeholder) + "</span>";
    }
    return '<select class="meta-control meta-select" style="width:100%;height:36px" ' +
      'data-up-account-field="' + field + '" data-up-account-id="' + escapeHtml(accountId) +
      '"><option value="">' + escapeHtml(placeholder) + "</option>" +
      list.map(function (row) {
        return '<option value="' + escapeHtml(row.id) + '"' +
          (row.id === value ? " selected" : "") + ">" + escapeHtml(row.name) + "</option>";
      }).join("") + "</select>";
  }

  function renderUploadAccounts() {
    var accounts = referenceAccounts();
    if (!accounts.length) {
      return '<div class="meta-note meta-note--warn">Кабинетов не видно. ' +
        "Подключите Business Manager — заливать некуда.</div>";
    }
    var picked = state.upload.accountIds;
    var search = (state.upload.accountSearch || "").trim().toLowerCase();
    var rows = accounts.filter(function (account) {
      if (state.upload.onlyPicked && picked.indexOf(account.id) < 0) return false;
      return !search || account.name.toLowerCase().indexOf(search) >= 0 ||
        String(account.external_id || "").indexOf(search) >= 0;
    });

    var head = '<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;' +
      'margin-bottom:14px">' +
      '<button class="meta-action" type="button" data-up-accounts="all">Выбрать все</button>' +
      '<button class="meta-action" type="button" data-up-accounts="none">Снять выбор</button>' +
      '<label class="meta-switch"><input type="checkbox" data-up-only-picked' +
      (state.upload.onlyPicked ? " checked" : "") +
      '><span class="meta-switch__box"></span><span>Только выбранные</span></label>' +
      '<div style="flex:1"></div>' +
      '<button class="meta-action" type="button" data-up-link-all>Ссылка для всех</button>' +
      '<input class="meta-control" style="width:220px;padding:0 13px" type="search" ' +
      'placeholder="Поиск по названию" data-up-account-search value="' +
      escapeHtml(state.upload.accountSearch || "") + '">' +
      '<span style="font-size:12px;color:#9B9292;font-weight:700">Выбрано: ' + picked.length +
      "</span></div>";

    var body = rows.map(function (account) {
      var on = picked.indexOf(account.id) >= 0;
      var own = accountRow(account);
      var assets = accountAssets(account.id);
      return '<tr class="meta-row" style="border-bottom:1px solid #F4F0F0">' +
        '<td class="meta-cell meta-cell--left" style="padding-left:16px;width:36px">' +
        '<input type="checkbox" data-up-account="' + escapeHtml(account.id) + '"' +
        (on ? " checked" : "") +
        ' style="width:16px;height:16px;accent-color:#B91414"></td>' +
        '<td class="meta-cell meta-cell--left" style="min-width:200px">' +
        '<div style="font-weight:700;font-size:12.5px">' + escapeHtml(account.name) + "</div>" +
        '<div style="font-size:11px;color:#9B9292;font-weight:600;margin-top:3px">ID: ' +
        escapeHtml(String(account.external_id || "")) + " · " +
        escapeHtml(account.currency || "USD") + "</div></td>" +
        '<td class="meta-cell meta-cell--left">' +
        '<span class="meta-chip" style="background:' +
        (account.status === "active" ? "#E4F7F0;color:#0E7350" : "#FCF1F1;color:#B91414") +
        '">' + escapeHtml(account.status === "active" ? "ACTIVE" : "STOP") + "</span></td>" +
        '<td class="meta-cell meta-cell--left" style="min-width:230px">' +
        (on
          ? '<div style="display:grid;gap:6px">' +
            assetSelect(account.id, "page_id", assets.pages, "Выберите ФП",
              assets.loading, assets.error) +
            assetSelect(account.id, "pixel_id", assets.pixels, "Выберите пиксель",
              assets.loading, assets.error) + "</div>"
          : '<span style="font-size:11.5px;color:#C6BDBD">отметьте кабинет</span>') +
        "</td>" +
        '<td class="meta-cell meta-cell--left" style="min-width:200px">' +
        '<input class="meta-control" style="width:100%;height:36px;padding:0 11px" ' +
        'placeholder="Ссылка" data-up-account-field="link_url" data-up-account-id="' +
        escapeHtml(account.id) + '" value="' + escapeHtml(own.link_url || "") + '"></td>' +
        '<td class="meta-cell meta-cell--left" style="min-width:130px">' +
        '<input class="meta-control" style="width:100%;height:36px;padding:0 11px" ' +
        'type="number" min="0" step="0.01" placeholder="Бюджет" ' +
        'data-up-account-field="daily_budget" data-up-account-id="' +
        escapeHtml(account.id) + '" value="' + escapeHtml(String(own.daily_budget || "")) +
        '"></td></tr>';
    }).join("");

    return head +
      '<div style="background:#fff;border:1px solid #EBE6E6;border-radius:16px;overflow:hidden">' +
      '<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;' +
      'min-width:940px"><thead><tr style="border-bottom:1px solid #F0EBEB">' +
      '<th class="meta-th meta-th--left" style="padding-left:16px"></th>' +
      '<th class="meta-th meta-th--left">Кабинет</th>' +
      '<th class="meta-th meta-th--left">Статус</th>' +
      '<th class="meta-th meta-th--left">Фан-пейдж и пиксель</th>' +
      '<th class="meta-th meta-th--left">Ссылка</th>' +
      '<th class="meta-th meta-th--left">Бюджет</th></tr></thead><tbody>' +
      (body || '<tr><td colspan="6" style="padding:26px;text-align:center;color:#9B9292;' +
        'font-size:12.5px;font-weight:600">Ничего не нашлось</td></tr>') +
      "</tbody></table></div></div>";
  }

  /* ----- шаг 3: креативы и объявления -----

     Объявления задаются один раз на всю пачку: у кабинетов разные хэши одного
     и того же файла, но тексты и языки у них общие. Файл при заливе уходит в
     каждый выбранный кабинет отдельно — иначе Meta его не примет.

     Языки — это не отдельные объявления, а один креатив с набором текстов:
     Meta показывает зрителю текст на его языке сама. */

  var LANGUAGE_PRESETS = [
    { code: "en_US", label: "English (US)" },
    { code: "en_GB", label: "English (UK)" },
    { code: "de_DE", label: "Deutsch" },
    { code: "fr_FR", label: "Français" },
    { code: "es_ES", label: "Español" },
    { code: "it_IT", label: "Italiano" },
    { code: "pt_BR", label: "Português (BR)" },
    { code: "pl_PL", label: "Polski" },
    { code: "ru_RU", label: "Русский" },
    { code: "tr_TR", label: "Türkçe" }
  ];

  function uploadAds() {
    if (!state.upload.ads.length) state.upload.ads = [emptyAd()];
    return state.upload.ads;
  }

  function emptyAd() {
    return { files: [], texts: [emptyText("")] };
  }

  function emptyText(language) {
    return {
      language: language, headline: "", description: "", primary_text: "",
      link_url: "", call_to_action: "LEARN_MORE"
    };
  }

  function languageLabel(code) {
    var found = LANGUAGE_PRESETS.filter(function (row) { return row.code === code; })[0];
    return found ? found.label : code;
  }

  function adTextFields(adIndex, textIndex, text) {
    var suffix = state.upload.languages && text.language
      ? " " + text.language.split("_")[0].toUpperCase()
      : "";
    var field = function (name, label, attrs) {
      return '<label class="meta-field" style="flex:1;min-width:150px">' +
        "<span>" + escapeHtml(label + suffix) + "</span>" +
        '<input class="meta-control" style="width:100%;padding:0 11px" ' + (attrs || "") +
        ' data-up-text="' + name + '" data-up-ad="' + adIndex + '" data-up-text-index="' +
        textIndex + '" value="' + escapeHtml(String(text[name] || "")) + '"></label>';
    };
    return '<div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end">' +
      (state.upload.languages
        ? '<label class="meta-field" style="width:190px"><span>Язык</span>' +
          '<select class="meta-control meta-select" style="width:100%" data-up-text="language" ' +
          'data-up-ad="' + adIndex + '" data-up-text-index="' + textIndex + '">' +
          LANGUAGE_PRESETS.map(function (row) {
            return '<option value="' + row.code + '"' +
              (row.code === text.language ? " selected" : "") + ">" +
              escapeHtml(row.label) + "</option>";
          }).join("") + "</select></label>"
        : "") +
      field("headline", "Заголовок", 'maxlength="240"') +
      field("description", "Описание", 'maxlength="240"') +
      field("primary_text", "Текст") +
      field("link_url", "Ссылка", 'placeholder="https://..."') +
      '<label class="meta-field" style="width:170px"><span>Кнопка' + escapeHtml(suffix) +
      "</span>" +
      '<select class="meta-control meta-select" style="width:100%" data-up-text="call_to_action" ' +
      'data-up-ad="' + adIndex + '" data-up-text-index="' + textIndex + '">' +
      ctaOptions().map(function (option) {
        return '<option value="' + escapeHtml(option.value) + '"' +
          (option.value === text.call_to_action ? " selected" : "") + ">" +
          escapeHtml(option.label) + "</option>";
      }).join("") + "</select></label>" +
      (state.upload.languages && textIndex > 0
        ? '<button class="meta-action meta-action--danger" type="button" data-up-text-drop="' +
          adIndex + ':' + textIndex + '" style="height:42px">Убрать язык</button>'
        : "") + "</div>";
  }

  function adFilesHtml(adIndex, ad) {
    return '<div style="border:1px dashed #E2DADA;border-radius:14px;padding:16px;' +
      'margin-top:12px">' +
      '<button class="meta-action" type="button" data-up-files="' + adIndex + '">' +
      "+ Добавить креативы</button>" +
      '<span style="margin-left:10px;font-size:11.5px;color:#9B9292;font-weight:600">' +
      "jpg, png, gif, mp4 — файл уйдёт в каждый выбранный кабинет</span>" +
      (ad.files.length
        ? '<div style="display:flex;flex-wrap:wrap;gap:8px;margin-top:12px">' +
          ad.files.map(function (file, index) {
            return '<span class="meta-tag">' + escapeHtml(file.name) +
              '<button type="button" data-up-file-drop="' + adIndex + ":" + index +
              '" aria-label="Убрать">×</button></span>';
          }).join("") + "</div>"
        : '<div style="margin-top:10px;font-size:11.5px;color:#C6BDBD;font-weight:600">' +
          "Пока пусто — без креатива объявление не создастся</div>") +
      "</div>";
  }

  function renderUploadCreatives() {
    var accounts = referenceAccounts().filter(function (account) {
      return state.upload.accountIds.indexOf(account.id) >= 0;
    });
    if (!accounts.length) {
      return '<div class="meta-note meta-note--warn">Вернитесь на шаг «Кабинеты» и ' +
        "отметьте хотя бы один.</div>";
    }
    var panel = '<div class="meta-card" style="margin-bottom:16px">' +
      '<div style="display:flex;align-items:center;gap:18px;flex-wrap:wrap">' +
      '<label class="meta-field" style="width:150px"><span>Кол-во адсетов</span>' +
      '<input class="meta-control" style="width:100%;padding:0 11px" type="number" min="1" ' +
      'max="20" data-up-field="adset_count" value="' +
      escapeHtml(String(state.upload.values.adset_count || 1)) + '"></label>' +
      '<label class="meta-switch"><input type="checkbox" data-up-languages' +
      (state.upload.languages ? " checked" : "") +
      '><span class="meta-switch__box"></span><span>Языки' +
      '<span class="meta-switch__hint">Один креатив с текстами на нескольких языках — ' +
      "Meta покажет зрителю его язык сама.</span></span></label>" +
      '<label class="meta-switch"><input type="checkbox" data-up-split' +
      (state.upload.split ? " checked" : "") +
      '><span class="meta-switch__box"></span><span>В кампаниях разные крео' +
      '<span class="meta-switch__hint">Файлы раздаются по кабинетам по очереди, ' +
      "а не копируются в каждый.</span></span></label>" +
      '<label class="meta-switch"><input type="checkbox" data-up-unique' +
      (state.upload.unique ? " checked" : "") +
      '><span class="meta-switch__box"></span><span>Уникализировать креативы' +
      '<span class="meta-switch__hint">В файл дописывается случайная метка, ' +
      "поэтому в каждом кабинете у него свой хэш.</span></span></label>" +
      "</div></div>";

    var ads = uploadAds().map(function (ad, index) {
      return '<div class="meta-card" style="margin-bottom:14px">' +
        '<div style="display:flex;align-items:center;gap:12px;margin-bottom:12px">' +
        '<div class="meta-card__title" style="margin:0">Объявление №' + (index + 1) + "</div>" +
        '<div style="flex:1"></div>' +
        '<button class="meta-action" type="button" data-up-ad-copy="' + index +
        '">Дублировать</button>' +
        (uploadAds().length > 1
          ? '<button class="meta-action meta-action--danger" type="button" ' +
            'data-up-ad-drop="' + index + '">Убрать</button>'
          : "") + "</div>" +
        ad.texts.map(function (text, textIndex) {
          return adTextFields(index, textIndex, text);
        }).join('<div style="height:10px"></div>') +
        (state.upload.languages
          ? '<button class="meta-action" type="button" data-up-text-add="' + index +
            '" style="margin-top:10px">+ Добавить язык</button>'
          : "") +
        adFilesHtml(index, ad) + "</div>";
    }).join("");

    return panel + ads +
      '<button class="meta-action" type="button" data-up-ad-add>+ Добавить объявление</button>' +
      '<div class="meta-note" style="margin-top:14px">Кабинетов выбрано: ' + accounts.length +
      ". Каждый файл загрузится в каждый из них — один и тот же файл в другом кабинете " +
      "имеет другой хэш, и чужой Meta не примет.</div>";
  }

  function renderUpload() {
    var upload = state.upload;
    if (!upload) return;
    byId("metaUpSteps").innerHTML = uploadStepsHtml(upload.step);
    var body = byId("metaUpBody");
    if (upload.step === 1) body.innerHTML = renderUploadSettings();
    if (upload.step === 2) body.innerHTML = renderUploadAccounts();
    if (upload.step === 3) body.innerHTML = renderUploadCreatives();
    byId("metaUpBack").style.visibility = upload.step === 1 ? "hidden" : "";
    byId("metaUpNext").textContent = upload.step === 3 ? "Залить" : "Далее";
    byId("metaUpHint").textContent = upload.step === 3
      ? "Будет создано заливов: " + upload.accountIds.length +
        ", адсетов в каждом: " + (Number(upload.values.adset_count) || 1)
      : "";
    byId("metaUpError").style.display = "none";
  }

  function uploadError(message) {
    var host = byId("metaUpError");
    host.textContent = message;
    host.style.display = "";
  }

  async function uploadNext() {
    var upload = state.upload;
    if (upload.step === 1) {
      if (!upload.values.template_id) return uploadError("Выберите связку или создайте новую");
      if (!String(upload.values.name || "").trim()) {
        return uploadError("Укажите название залива в расширенном режиме");
      }
      if (upload.values.start_mode === "custom" && !upload.values.start_at) {
        return uploadError("Укажите время запуска");
      }
      if (upload.values.publish_mode === "custom" && !upload.values.publish_at) {
        return uploadError("Укажите время залива");
      }
      upload.step = 2;
      return renderUpload();
    }
    if (upload.step === 2) {
      if (!upload.accountIds.length) return uploadError("Выберите хотя бы один кабинет");
      var missing = upload.accountIds.filter(function (id) {
        return !(upload.perAccount[id] || {}).link_url && !upload.values.link_url;
      });
      if (missing.length) return uploadError("У каждого кабинета должна быть ссылка");
      // Цель на конверсии без пикселя Meta не примет, а узнать об этом на
      // публикации — значит потерять весь заход по мастеру.
      var bundle = uploadBundle();
      if (bundle && bundle.optimization_goal === "OFFSITE_CONVERSIONS") {
        var noPixel = upload.accountIds.filter(function (id) {
          return !(upload.perAccount[id] || {}).pixel_id;
        });
        if (noPixel.length) {
          return uploadError("Цель связки считает конверсии — выберите пиксель " +
            "у каждого кабинета");
        }
      }
      upload.step = 3;
      return renderUpload();
    }
    var empty = uploadAds().filter(function (ad) { return !ad.files.length; });
    if (empty.length) return uploadError("У каждого объявления должен быть креатив");
    if (state.upload.languages) {
      var noLanguage = uploadAds().some(function (ad) {
        return ad.texts.some(function (text) { return !text.language; });
      });
      if (noLanguage) return uploadError("У каждого языкового варианта выберите язык");
    }
    await submitUpload();
  }

  /* Файл уходит в каждый выбранный кабинет: один и тот же файл в другом
     кабинете имеет другой хэш, и чужой Meta не примет. «Разные крео» вместо
     этого раздаёт файлы по кабинетам по очереди. */
  async function uploadCreativeFiles() {
    var upload = state.upload;
    var byAccount = {};
    var adsByAccount = {};
    upload.accountIds.forEach(function (id) {
      byAccount[id] = [];
      adsByAccount[id] = [];
    });

    for (var adIndex = 0; adIndex < uploadAds().length; adIndex += 1) {
      var ad = uploadAds()[adIndex];
      for (var slot = 0; slot < upload.accountIds.length; slot += 1) {
        var accountId = upload.accountIds[slot];
        var files = upload.split
          ? ad.files.filter(function (_file, position) {
            return position % upload.accountIds.length === slot;
          })
          : ad.files;
        var ids = [];
        for (var index = 0; index < files.length; index += 1) {
          var form = new FormData();
          form.append("file", files[index]);
          form.append("name", files[index].name);
          var created = await api.upload(
            "/meta/creatives?account_id=" + encodeURIComponent(accountId) +
              (upload.unique ? "&unique=true" : ""),
            form
          );
          ids.push(created.id);
          byAccount[accountId].push(created.id);
        }
        if (ids.length) {
          adsByAccount[accountId].push({ texts: ad.texts, creative_ids: ids });
        }
      }
    }
    return { creatives: byAccount, ads: adsByAccount };
  }

  async function submitUpload() {
    var upload = state.upload;
    var values = upload.values;
    var payload = {
      name: values.name.trim(),
      template_id: values.template_id || null,
      offer_id: values.offer_id || null,
      partner_id: values.partner_id || null,
      owner_id: values.owner_id || null,
      geo: (values.geo || "").trim().toUpperCase() || null,
      daily_budget: values.daily_budget || "0",
      spend_limit: values.spend_limit || null,
      start_date: values.start_date || null,
      end_date: values.end_date || null,
      link_url: (values.link_url || "").trim() || null,
      primary_text: values.primary_text || null,
      headline: values.headline || null,
      description: values.description || null,
      call_to_action: values.call_to_action,
      page_id: (values.page_id || "").trim() || null,
      pixel_id: null,
      activate_on_publish: values.activate_on_publish,
      publish_at: values.publish_mode === "custom" ? localMoment(values.publish_at) : null,
      start_at: startMoment(values),
      url_tags: (values.url_tags || "").trim() || null,
      display_link: (values.display_link || "").trim() || null,
      pause_campaigns: values.pause_campaigns,
      pause_adsets: values.pause_adsets,
      pause_ads: values.pause_ads,
      account_delay_seconds: Number(values.account_delay_seconds) || 0,
      adset_count: Number(values.adset_count) || 1,
      account_ids: upload.accountIds,
      publish: true
    };
    var button = byId("metaUpNext");
    button.disabled = true;
    button.textContent = "Загружаю креативы…";
    try {
      var uploaded = await uploadCreativeFiles();
      payload.creatives_by_account = uploaded.creatives;
      payload.ads_by_account = uploaded.ads;
      payload.overrides = {};
      upload.accountIds.forEach(function (id) {
        var own = upload.perAccount[id] || {};
        payload.overrides[id] = {
          page_id: own.page_id || null,
          pixel_id: own.pixel_id || null,
          link_url: own.link_url || null,
          daily_budget: own.daily_budget || null
        };
      });
      button.textContent = "Заливаю…";
      var result = await api.post("/meta/launches/batch", payload);
      var failed = (result.results || []).filter(function (row) { return !row.ok; });
      notify({
        title: "Заливов создано: " + result.created + ", в очередь ушло " + result.queued +
          (result.scheduled ? ", запланировано " + result.scheduled : ""),
        message: failed.length
          ? "Не прошли проверку: " + failed.map(function (row) {
            return row.account_name + " — " + row.error;
          }).join("; ") + ". Эти заливы сохранены черновиками."
          : "Кампании создаются в Meta" +
            (payload.activate_on_publish ? "" : " на паузе") + "."
      });
      state.upload = null;
      await loadLaunches();
      setLaunchView("queue");
    } catch (error) {
      uploadError(error && error.message ? error.message : "Не удалось залить");
    } finally {
      button.disabled = false;
      button.textContent = "Залить";
    }
  }

  /* Время из браузера в ISO. `datetime-local` отдаёт местное время без зоны, а
     сервер считает всё в UTC — без явного перевода залив уехал бы на несколько
     часов в сторону. */
  function localMoment(value) {
    if (!value) return null;
    var parsed = new Date(value);
    return isNaN(parsed.getTime()) ? null : parsed.toISOString();
  }

  function startMoment(values) {
    if (values.start_mode === "custom") return localMoment(values.start_at);
    if (values.start_mode === "midnight") {
      var midnight = new Date();
      midnight.setHours(24, 0, 0, 0);
      return midnight.toISOString();
    }
    return null;
  }

  function onUploadInput(event) {
    var search = event.target.closest ? event.target.closest("[data-up-account-search]") : null;
    if (search) {
      state.upload.accountSearch = search.value;
      // Точечно: перерисовка шага увела бы курсор из поля на каждом символе.
      var host = byId("metaUpBody");
      var scroll = host.scrollTop;
      host.innerHTML = renderUploadAccounts();
      host.scrollTop = scroll;
      var next = host.querySelector("[data-up-account-search]");
      if (next) {
        next.focus();
        next.setSelectionRange(next.value.length, next.value.length);
      }
      return;
    }
    var accountField = event.target.closest
      ? event.target.closest("[data-up-account-field]") : null;
    if (accountField) {
      accountRow({ id: accountField.getAttribute("data-up-account-id") })[
        accountField.getAttribute("data-up-account-field")
      ] = accountField.value;
      return;
    }
    var adText = event.target.closest ? event.target.closest("[data-up-text]") : null;
    if (adText) {
      uploadAds()[Number(adText.getAttribute("data-up-ad"))]
        .texts[Number(adText.getAttribute("data-up-text-index"))][
          adText.getAttribute("data-up-text")
        ] = adText.value;
      return;
    }
    var field = event.target.closest ? event.target.closest("[data-up-field]") : null;
    if (!field || field.type === "checkbox") return;
    // Поля пишем в состояние, но не перерисовываем: перерисовка на каждом
    // символе уводила бы курсор в начало строки.
    state.upload.values[field.getAttribute("data-up-field")] = field.value;
  }

  function onUploadChange(event) {
    var target = event.target;
    var field = target.closest ? target.closest("[data-up-field]") : null;
    if (field) {
      var name = field.getAttribute("data-up-field");
      state.upload.values[name] = field.type === "checkbox" ? field.checked : field.value;
      if (name === "template_id") {
        applyBundleToUpload(uploadBundle());
        return renderUpload();
      }
      return;
    }
    var accountField = target.closest ? target.closest("[data-up-account-field]") : null;
    if (accountField) {
      accountRow({ id: accountField.getAttribute("data-up-account-id") })[
        accountField.getAttribute("data-up-account-field")
      ] = accountField.value;
      return;
    }
    var adText = target.closest ? target.closest("[data-up-text]") : null;
    if (adText) {
      uploadAds()[Number(adText.getAttribute("data-up-ad"))]
        .texts[Number(adText.getAttribute("data-up-text-index"))][
          adText.getAttribute("data-up-text")
        ] = adText.value;
      return;
    }
    if (target.closest && target.closest("[data-up-only-picked]")) {
      state.upload.onlyPicked = target.checked;
      return renderUpload();
    }
    if (target.closest && target.closest("[data-up-languages]")) {
      state.upload.languages = target.checked;
      // Включили языки — первому тексту нужен язык, иначе Meta не поймёт,
      // кому его показывать.
      uploadAds().forEach(function (ad) {
        if (target.checked && !ad.texts[0].language) ad.texts[0].language = "en_US";
        if (!target.checked) ad.texts = [ad.texts[0]];
      });
      return renderUpload();
    }
    if (target.closest && target.closest("[data-up-split]")) {
      state.upload.split = target.checked;
      return renderUpload();
    }
    if (target.closest && target.closest("[data-up-unique]")) {
      state.upload.unique = target.checked;
      return renderUpload();
    }
    if (target.closest && target.closest("[data-up-advanced]")) {
      state.upload.advanced = target.checked;
      return renderUpload();
    }
    if (target.closest && target.closest("[data-up-pause-gap]")) {
      // Пауза включается тумблером, а величину спрашиваем следом: ноль секунд
      // при включённом тумблере — это включённая настройка, которая ничего не
      // делает.
      state.upload.values.account_delay_seconds = target.checked ? "60" : "0";
      return renderUpload();
    }
    var account = target.closest ? target.closest("[data-up-account]") : null;
    if (account) {
      var id = account.getAttribute("data-up-account");
      var picked = state.upload.accountIds.filter(function (value) { return value !== id; });
      if (account.checked) {
        picked.push(id);
        // Страницы и пиксели спрашиваем у Meta только для отмеченного: на все
        // видимые кабинеты это были бы десятки запросов ради трёх нужных.
        loadAccountAssets(id);
      }
      state.upload.accountIds = picked;
      return renderUpload();
    }
    var creative = target.closest ? target.closest("[data-up-creative]") : null;
    if (creative) {
      var accountId = creative.getAttribute("data-up-creative-account");
      var creativeId = creative.getAttribute("data-up-creative");
      var list = (state.upload.creatives[accountId] || []).filter(function (value) {
        return value !== creativeId;
      });
      if (creative.checked) list.push(creativeId);
      state.upload.creatives[accountId] = list;
      renderUpload();
    }
  }

  function onUploadClick(event) {
    var target = event.target;
    var closest = function (selector) {
      return target.closest ? target.closest(selector) : null;
    };
    if (closest("[data-up-bundle-new]")) return openBundleWizard(null);
    var seg = closest("[data-up-seg]");
    if (seg) {
      state.upload.values[seg.getAttribute("data-up-seg")] = seg.getAttribute("data-up-value");
      return renderUpload();
    }
    var accounts = closest("[data-up-accounts]");
    if (accounts) {
      var all = referenceAccounts().map(function (account) { return account.id; });
      var pick = accounts.getAttribute("data-up-accounts") === "all";
      state.upload.accountIds = pick ? all : [];
      if (pick) all.forEach(function (id) { loadAccountAssets(id); });
      return renderUpload();
    }
    if (closest("[data-up-link-all]")) return spreadLink();

    var adAdd = closest("[data-up-ad-add]");
    if (adAdd) {
      uploadAds().push(emptyAd());
      return renderUpload();
    }
    var adCopy = closest("[data-up-ad-copy]");
    if (adCopy) {
      var source = uploadAds()[Number(adCopy.getAttribute("data-up-ad-copy"))];
      // Файлы копии те же: это те же File из браузера, и грузиться они будут
      // каждый в свой кабинет так же, как у оригинала.
      uploadAds().push({
        files: source.files.slice(),
        texts: source.texts.map(function (text) { return Object.assign({}, text); })
      });
      return renderUpload();
    }
    var adDrop = closest("[data-up-ad-drop]");
    if (adDrop) {
      var index = Number(adDrop.getAttribute("data-up-ad-drop"));
      state.upload.ads = uploadAds().filter(function (_ad, position) {
        return position !== index;
      });
      return renderUpload();
    }
    var textAdd = closest("[data-up-text-add]");
    if (textAdd) {
      var ad = uploadAds()[Number(textAdd.getAttribute("data-up-text-add"))];
      var used = ad.texts.map(function (text) { return text.language; });
      var free = LANGUAGE_PRESETS.filter(function (row) {
        return used.indexOf(row.code) < 0;
      })[0];
      ad.texts.push(emptyText(free ? free.code : ""));
      return renderUpload();
    }
    var textDrop = closest("[data-up-text-drop]");
    if (textDrop) {
      var parts = textDrop.getAttribute("data-up-text-drop").split(":");
      var owner = uploadAds()[Number(parts[0])];
      owner.texts = owner.texts.filter(function (_text, position) {
        return position !== Number(parts[1]);
      });
      return renderUpload();
    }
    var files = closest("[data-up-files]");
    if (files) return pickCreativeFiles(Number(files.getAttribute("data-up-files")));
    var fileDrop = closest("[data-up-file-drop]");
    if (fileDrop) {
      var where = fileDrop.getAttribute("data-up-file-drop").split(":");
      var host = uploadAds()[Number(where[0])];
      host.files = host.files.filter(function (_file, position) {
        return position !== Number(where[1]);
      });
      return renderUpload();
    }
  }

  /* Ссылка для всех: её вводят один раз и раздают по кабинетам. Разные ссылки
     на кабинет нужны редко, а набирать одну и ту же двадцать раз — всегда. */
  function spreadLink() {
    var first = state.upload.accountIds.map(function (id) {
      return (state.upload.perAccount[id] || {}).link_url;
    }).filter(Boolean)[0] || state.upload.values.link_url || "";
    if (!first) {
      return uploadError("Заполните ссылку хотя бы у одного кабинета");
    }
    state.upload.accountIds.forEach(function (id) {
      accountRow({ id: id }).link_url = first;
    });
    renderUpload();
  }

  function pickCreativeFiles(adIndex) {
    var input = byId("metaUploadFiles");
    input.value = "";
    input.onchange = function () {
      var chosen = Array.prototype.slice.call(input.files || []);
      if (chosen.length) {
        uploadAds()[adIndex].files = uploadAds()[adIndex].files.concat(chosen);
        renderUpload();
      }
      input.onchange = null;
    };
    input.click();
  }

  /* ----- очередь заливки ----- */

  var QUEUE_KINDS = {
    campaign_create: "Кампания",
    adset_create: "Группа объявлений",
    creative_create: "Креатив",
    ad_create: "Объявление",
    campaign_pause: "Остановка кампании",
    campaign_resume: "Возобновление кампании",
    pause: "Правило: остановить",
    resume: "Правило: возобновить",
    increase_budget: "Правило: поднять бюджет",
    decrease_budget: "Правило: снизить бюджет"
  };
  var QUEUE_STATUS = {
    pending: { label: "В работе", color: "#C9821F", background: "#FFF6E9" },
    success: { label: "Успешно", color: "#0E7350", background: "#E4F7F0" },
    failed: { label: "Ошибка", color: "#C41616", background: "#FCF1F1" }
  };

  async function loadQueue() {
    var status = byId("metaQueueStatus").value;
    var page = await api.get("/meta/operations" + (status ? "?status=" + status : ""));
    state.queue = page.items || [];
    renderQueue();
  }

  function renderQueue() {
    var body = byId("metaQueueBody");
    if (!state.queue.length) {
      body.innerHTML = '<tr><td colspan="7" style="padding:44px 20px;text-align:center;' +
        'color:#9B9292;font-size:13px">Очередь пуста — ни одной публикации ещё не было</td></tr>';
      byId("metaQueueCount").textContent = "Операций: 0";
      return;
    }
    body.innerHTML = state.queue.map(function (row) {
      var tone = QUEUE_STATUS[row.status] || QUEUE_STATUS.pending;
      var info = row.error
        ? '<span style="color:#C41616">' + escapeHtml(row.error) + "</span>"
        : row.target_external_id
          ? '<span style="color:#9B9292">ID ' + escapeHtml(row.target_external_id) + "</span>"
          : '<span style="color:#C9BFBF">—</span>';
      return '<tr class="meta-row" style="border-bottom:1px solid #F7F4F4">' +
        '<td class="meta-cell meta-cell--left" style="padding-left:20px;font-weight:700">' +
        escapeHtml(QUEUE_KINDS[row.kind] || row.kind) + "</td>" +
        '<td class="meta-cell meta-cell--left">' + escapeHtml(row.launch_name) + "</td>" +
        '<td class="meta-cell meta-cell--left" style="color:#6A6161">' +
        escapeHtml(row.account_name) + "</td>" +
        '<td class="meta-cell meta-cell--left" style="max-width:320px;white-space:normal">' +
        info + "</td>" +
        '<td class="meta-cell meta-cell--left" style="color:#6A6161">' +
        escapeHtml(formatMoment(row.created_at)) + "</td>" +
        '<td class="meta-cell meta-cell--left"><span class="meta-chip" style="color:' +
        tone.color + ";background:" + tone.background + '">' + escapeHtml(tone.label) +
        "</span></td>" +
        '<td class="meta-cell meta-cell--left" style="padding-right:20px">' +
        (row.status === "failed" && state.canLaunch
          ? '<button class="meta-action" type="button" data-queue-retry="' +
            escapeHtml(row.launch_id) + '">Повторить</button>'
          : '<span style="color:#C9BFBF">—</span>') +
        "</td></tr>";
    }).join("");
    byId("metaQueueCount").textContent = "Операций: " + state.queue.length;
  }

  async function retryQueue(launchId) {
    try {
      // Публикация идемпотентна по записанным ID: повтор продолжает с того
      // шага, на котором всё встало, и второй кампании не создаёт.
      await api.post("/meta/launches/" + launchId + "/publish", {});
      notify({ title: "Публикация перезапущена", message: "Залив снова в очереди." });
      await loadQueue();
      await loadLaunches();
    } catch (error) {
      notify({
        title: "Не удалось перезапустить",
        message: error && error.message ? error.message : ""
      });
    }
  }

  async function openLaunchForm(launchId) {
    await loadReference();
    if (!state.templates.length) await loadTemplates(true);
    var launch = launchId
      ? state.launches.filter(function (row) { return row.id === launchId; })[0]
      : {};
    if (!launch) return;
    var accountId = launch.account_id || (referenceAccounts()[0] || {}).id || "";
    if (!accountId) {
      notify({
        title: "Нет ни одного кабинета",
        message: "Сначала подключите Business Manager — заливать некуда."
      });
      return;
    }
    var creatives = await creativesFor(accountId);
    var published = !!launch.campaign_external_id;
    openForm({
      title: launchId ? "Залив" : "Новый залив",
      subtitle: published
        ? "Залив опубликован: кабинет, ссылка и креативы дальше меняются только в Ads Manager."
        : "Кампания, группа объявлений и объявления создадутся в Meta на паузе.",
      fields: launchFields(launch, creatives),
      note: published
        ? null
        : "Публикация — отдельная кнопка в списке. Сохранение здесь ничего в Meta не создаёт.",
      onSave: async function (values) {
        var payload = {
          name: values.name.trim(),
          account_id: values.account_id,
          template_id: values.template_id || null,
          offer_id: values.offer_id || null,
          partner_id: values.partner_id || null,
          owner_id: values.owner_id || null,
          geo: values.geo.trim().toUpperCase() || null,
          daily_budget: values.daily_budget || "0",
          spend_limit: values.spend_limit || null,
          start_date: values.start_date || null,
          end_date: values.end_date || null,
          link_url: values.link_url.trim() || null,
          primary_text: values.primary_text || null,
          headline: values.headline || null,
          description: values.description || null,
          call_to_action: values.call_to_action,
          page_id: values.page_id.trim() || null,
          pixel_id: values.pixel_id.trim() || null,
          activate_on_publish: values.activate_on_publish,
          creative_ids: values.creative_ids
        };
        if (!payload.name) throw new Error("Укажите название залива");
        if (launchId) {
          if (published) {
            // Опубликованный залив принимает только то, что живёт в CRM.
            payload = {
              name: payload.name, owner_id: payload.owner_id, offer_id: payload.offer_id,
              partner_id: payload.partner_id, geo: payload.geo, spend_limit: payload.spend_limit,
              end_date: payload.end_date
            };
          }
          await api.patch("/meta/launches/" + launchId, payload);
        } else {
          await api.post("/meta/launches", payload);
        }
        await loadLaunches();
      },
      onDelete: launchId && !published ? async function () {
        if (!(await askConfirm({
          title: "Удалить залив?",
          message: "«" + launch.name + "» ещё не опубликован — в Meta ничего не создано.",
          confirmLabel: "Удалить",
          danger: true
        }))) return;
        await api.delete("/meta/launches/" + launchId);
        await loadLaunches();
        closeForm();
      } : null
    });
  }

  async function onLaunchAccountChange(event) {
    var select = event.target.closest
      ? event.target.closest('[data-field="account_id"]')
      : null;
    if (!select) return;
    var host = byId("metaFormBody").querySelector('[data-checklist="creative_ids"]');
    if (!host) return;
    var creatives = await creativesFor(select.value);
    host.innerHTML = checklistHtml({
      name: "creative_ids",
      empty: "В этом кабинете ещё нет загруженных креативов",
      options: creatives.map(function (creative) {
        return { value: creative.id,
          label: creative.name + " · " + (creative.kind === "video" ? "видео" : "картинка") };
      })
    });
  }

  async function creativesFor(accountId) {
    if (!accountId) return [];
    var page = await api.get("/meta/creatives?account_id=" + encodeURIComponent(accountId));
    return page.items || [];
  }

  async function publishLaunch(launchId) {
    var launch = state.launches.filter(function (row) { return row.id === launchId; })[0];
    if (!launch) return;
    var check = await api.post("/meta/launches/" + launchId + "/validate", {});
    if (!check.ok) {
      notify({
        title: "Залив нельзя опубликовать",
        message: "· " + check.problems.join("\n· ")
      });
      return;
    }
    var warning = launch.activate_on_publish
      ? "Кампания будет создана и СРАЗУ ЗАПУЩЕНА — Meta начнёт тратить бюджет " +
        money(launch.daily_budget) + " в день."
      : "Кампания будет создана на паузе. Деньги тратиться не начнут, пока вы её не запустите.";
    if (!(await askConfirm({
      title: "Опубликовать залив «" + launch.name + "»?",
      message: "Кабинет: " + launch.account_name + ".\n\n" + warning,
      confirmLabel: "Опубликовать",
      danger: !!launch.activate_on_publish
    }))) return;
    try {
      await api.post("/meta/launches/" + launchId + "/publish", {});
      await loadLaunches();
      window.setTimeout(function () { loadLaunches().catch(function () {}); }, 5000);
    } catch (error) {
      notify({
        title: "Не удалось опубликовать залив",
        message: error && error.message ? error.message : ""
      });
    }
  }

  async function launchAction(launchId, action) {
    var launch = state.launches.filter(function (row) { return row.id === launchId; })[0];
    if (!launch) return;
    var text = action === "pause"
      ? "Остановить кампанию «" + launch.name + "» в Meta?"
      : "Запустить кампанию «" + launch.name + "»? Meta начнёт тратить бюджет.";
    if (!(await askConfirm({
      title: action === "pause" ? "Остановить кампанию?" : "Запустить кампанию?",
      message: text,
      confirmLabel: action === "pause" ? "Остановить" : "Запустить",
      danger: action !== "pause"
    }))) return;
    try {
      await api.post("/meta/launches/" + launchId + "/status?action=" + action, {});
      await loadLaunches();
    } catch (error) {
      notify({
        title: "Meta не приняла команду",
        message: error && error.message ? error.message : ""
      });
    }
  }

  async function bulkAction(action) {
    if (!state.selected.length) return;
    var labels = { publish: "опубликовать", pause: "остановить", resume: "запустить" };
    if (!(await askConfirm({
      title: "Действие «" + labels[action] + "» для " + state.selected.length +
        " заливов",
      message: "Оно будет применено к каждому выбранному заливу.",
      confirmLabel: "Применить",
      danger: action !== "pause"
    }))) return;
    try {
      var result = await api.post("/meta/launches/bulk?action=" + action, state.selected);
      var failed = (result.results || []).filter(function (row) { return !row.ok; });
      if (failed.length) {
        notify({
          title: "Не получилось: " + failed.length + " из " + result.results.length,
          message: failed.map(function (row) { return "· " + row.error; }).join("\n")
        });
      }
      state.selected = [];
      await loadLaunches();
    } catch (error) {
      notify({
        title: "Не удалось выполнить действие",
        message: error && error.message ? error.message : ""
      });
    }
  }

  /* ---------- шаблоны (ТЗ 3.5) ---------- */

  /* ---------- связки (ТЗ 3.5) ----------

     Связка описывает залив целиком: кампанию, адсет и объявление. Мастер
     повторяет тот же порядок тремя шагами — так его и держат в голове: сначала
     решают, за что платим, потом кому показываем, потом что показываем. */

  async function loadTemplates(quiet) {
    // Справочник нужен раньше карточек: без него у связки вместо цели
    // показывался бы её код.
    await loadReference();
    var page = await api.get("/meta/templates");
    state.templates = page.items || [];
    if (!quiet) renderBundles();
  }

  function bundleReference() {
    return (state.reference && state.reference.bundle) || {};
  }

  function goalLabel(code) {
    var found = (bundleReference().goals || []).filter(function (row) {
      return row.code === code;
    })[0];
    return found ? found.label : code || "—";
  }

  function bundleSettings(bundle) {
    var settings = (bundle && bundle.settings) || {};
    return {
      campaign: settings.campaign || {},
      adset: settings.adset || {},
      ad: settings.ad || {}
    };
  }

  function bundleRows() {
    var query = (state.bundleSearch || "").trim().toLowerCase();
    if (!query) return state.templates;
    return state.templates.filter(function (bundle) {
      return String(bundle.name || "").toLowerCase().indexOf(query) >= 0;
    });
  }

  function bundleCell(list) {
    // Пустую ячейку рисуем прочерком, а не пустотой: в строке из семи колонок
    // пустое место читается как «не догрузилось».
    if (!list.length) return '<span style="color:#C6BDBD">—</span>';
    return '<div style="display:flex;flex-wrap:wrap;gap:5px">' + list.map(function (item) {
      return '<span class="meta-tag" style="font-weight:600">' + escapeHtml(item) + "</span>";
    }).join("") + "</div>";
  }

  function bundleDevices(blocks) {
    var adset = blocks.adset || {};
    var devices = adset.devices || "all";
    var os = adset.os || "all";
    var list = [];
    if (devices !== "mobile") list.push("Десктоп");
    if (devices !== "desktop") list.push("Мобайл");
    if (devices !== "desktop") {
      if (os !== "ios") list.push("Android " + (adset.android_min || "любая"));
      if (os !== "android") list.push("iOS " + (adset.ios_min || "любая"));
    }
    if (adset.wifi_only) list.push("Wi-Fi");
    return list;
  }

  function renderBundles() {
    var host = byId("metaBundlesGrid");
    if (!host) return;
    byId("metaBundleCreate").style.display = state.canLaunch ? "" : "none";
    var rows = bundleRows();
    var reference = bundleReference();
    if (!rows.length) {
      host.innerHTML = '<tr><td colspan="8" style="padding:32px 20px;text-align:center;' +
        'color:#9B9292;font-size:12.5px;font-weight:600">' +
        (state.templates.length
          ? "По этому запросу связок нет"
          : "Связок нет. Связка хранит цель, бюджет, таргет и тексты — заливается на любой " +
            "кабинет в один клик.") + "</td></tr>";
    } else {
      host.innerHTML = rows.map(function (bundle) {
        var blocks = bundleSettings(bundle);
        var picked = (state.bundlePick || []).indexOf(bundle.id) >= 0;
        var event = (reference.pixel_events || {})[bundle.custom_event_type];
        var budget = bundle.daily_budget || bundle.lifetime_budget;
        var placements = blocks.adset.auto_placements === false
          ? ((bundle.placements || {}).publisher_platforms || []).map(function (key) {
            return ((state.reference || {}).publisher_platforms || {})[key] || key;
          })
          : ["Авто"];
        var demography = [(bundle.age_min || 18) + " – " + (bundle.age_max || 65)];
        if ((bundle.genders || []).length) {
          demography.push(bundle.genders[0] === 1 ? "Мужчины" : "Женщины");
        }
        return '<tr class="meta-row" style="border-bottom:1px solid #F4F0F0">' +
          '<td class="meta-cell meta-cell--left" style="padding-left:20px">' +
          '<input type="checkbox" data-bundle-pick="' + escapeHtml(bundle.id) + '"' +
          (picked ? " checked" : "") +
          ' style="width:16px;height:16px;accent-color:#B91414" aria-label="Выбрать связку"></td>' +

          '<td class="meta-cell meta-cell--left" style="min-width:240px">' +
          '<div style="font-weight:700;font-size:13px;overflow-wrap:anywhere">' +
          escapeHtml(bundle.name) + "</div>" +
          '<div style="display:flex;flex-wrap:wrap;gap:5px;margin-top:6px">' +
          '<span class="meta-tag" style="font-weight:600">' +
          escapeHtml(goalLabel(blocks.campaign.goal)) + "</span>" +
          (event ? '<span class="meta-tag" style="font-weight:600">' + escapeHtml(event) +
            "</span>" : "") +
          (budget ? '<span class="meta-tag" style="font-weight:600">' +
            escapeHtml(money(budget) + " " + (blocks.campaign.budget_currency || "USD")) +
            "</span>" : "") + "</div>" +
          (bundle.created_by
            ? '<div style="font-size:11px;color:#9B9292;font-weight:600;margin-top:6px">' +
              "Владелец: " + escapeHtml(bundle.created_by) + "</div>"
            : "") + "</td>" +

          '<td class="meta-cell meta-cell--left">' + bundleCell(bundle.geo || []) + "</td>" +
          '<td class="meta-cell meta-cell--left">' +
          bundleCell((bundle.languages || []).map(String)) + "</td>" +
          '<td class="meta-cell meta-cell--left">' + bundleCell(placements) + "</td>" +
          '<td class="meta-cell meta-cell--left">' + bundleCell(demography) + "</td>" +
          '<td class="meta-cell meta-cell--left">' + bundleCell(bundleDevices(blocks)) + "</td>" +

          '<td class="meta-cell meta-cell--left" style="padding-right:20px">' +
          (state.canLaunch
            ? '<div style="display:flex;gap:7px;flex-wrap:wrap">' +
              '<button class="meta-action meta-action--primary" data-bundle-pour="' +
              escapeHtml(bundle.id) + '">Залить</button>' +
              '<button class="meta-action" data-bundle-open="' + escapeHtml(bundle.id) +
              '">Изменить</button></div>'
            : '<span style="color:#C6BDBD">—</span>') + "</td></tr>";
      }).join("");
    }

    var picked = (state.bundlePick || []).length;
    byId("metaBundleCount").textContent = rows.length
      ? "Связок: " + rows.length + (picked ? ", выбрано " + picked : "")
      : "Связок нет";
    var bulk = byId("metaBundleBulk");
    bulk.style.display = picked && state.canLaunch ? "flex" : "none";
    byId("metaBundleBulkCount").textContent = "Выбрано: " + picked;
    var all = byId("metaBundleAll");
    all.checked = !!rows.length && rows.every(function (bundle) {
      return (state.bundlePick || []).indexOf(bundle.id) >= 0;
    });
  }

  /* ----- мастер связки ----- */

  var BUNDLE_STEPS = ["Кампании", "Адсеты", "Объявления"];

  function bundleDefaults() {
    return {
      name: "",
      goal: "leads",
      advantage: false,
      campaign_name: "{{bundle.name}}",
      budget: "",
      budget_currency: "USD",
      budget_randomize: false,
      budget_kind: "daily",
      budget_level: "campaign",
      adset_budget_limit: "",
      bid_strategy: "LOWEST_COST_WITHOUT_CAP",
      bid_amount: "",
      accelerated_delivery: false,
      special_ad_categories: [],
      adset_name: "adset #{{adset.number}}",
      custom_event_type: "LEAD",
      attribution: "7d_click_1d_view",
      engaged_view: "1d",
      advantage_audience: false,
      age_min: 18,
      age_max: 65,
      age_randomize: false,
      age_randomize_years: 3,
      genders: "",
      location_type: "home",
      geo_mode: "countries",
      geo: [],
      geo_regions: [],
      geo_cities: [],
      excluded_geo: [],
      languages: [],
      interests: [],
      excluded_interests: [],
      targeting_expansion: true,
      auto_placements: true,
      placements: [],
      devices: "all",
      os: "all",
      android_smartphone: true,
      android_tablet: true,
      android_min: "",
      ios_iphone: true,
      ios_ipad: true,
      ios_ipod: true,
      ios_min: "",
      wifi_only: false,
      ad_name: "ad #{{ad.number}}",
      multilingual: false,
      headline: "",
      primary_text: "",
      description: "",
      link_url: "",
      multi_advertiser: false,
      advantage_creative: false,
      call_to_action: "LEARN_MORE",
      page_id: "",
      pixel_id: "",
      notes: ""
    };
  }

  function bundleToValues(bundle) {
    var values = bundleDefaults();
    if (!bundle) return values;
    var blocks = bundleSettings(bundle);
    values.name = bundle.name || "";
    values.geo = (bundle.geo || []).slice();
    values.age_min = bundle.age_min || 18;
    values.age_max = bundle.age_max || 65;
    values.genders = (bundle.genders || []).length ? String(bundle.genders[0]) : "";
    values.languages = (blocks.adset.language_labels || []).length
      ? blocks.adset.language_labels.slice()
      : (bundle.languages || []).map(function (id) {
        return { id: String(id), name: String(id) };
      });
    values.interests = (bundle.interests || []).slice();
    values.placements = ((bundle.placements || {}).publisher_platforms || []).slice();
    values.budget = bundle.daily_budget || bundle.lifetime_budget || "";
    values.page_id = bundle.page_id || "";
    values.pixel_id = bundle.pixel_id || "";
    values.custom_event_type = bundle.custom_event_type || "LEAD";
    values.call_to_action = bundle.call_to_action || "LEARN_MORE";
    values.bid_strategy = bundle.bid_strategy || "LOWEST_COST_WITHOUT_CAP";
    values.notes = bundle.notes || "";
    Object.keys(values).forEach(function (key) {
      ["campaign", "adset", "ad"].forEach(function (block) {
        if (blocks[block][key] !== undefined && blocks[block][key] !== null) {
          values[key] = blocks[block][key];
        }
      });
    });
    if (values.geo_regions.length) values.geo_mode = "regions";
    if (values.geo_cities.length) values.geo_mode = "cities";
    return values;
  }

  function openBundleWizard(bundleId, copy) {
    var bundle = bundleId
      ? state.templates.filter(function (row) { return row.id === bundleId; })[0]
      : null;
    if (bundleId && !bundle) return;
    var values = bundleToValues(bundle);
    if (copy) values.name = values.name + " (копия)";
    state.bundle = { id: copy ? null : bundleId, step: 1, values: values };
    byId("metaBundleTitle").textContent = state.bundle.id ? "Связка" : "Создание связки";
    byId("metaBundleModal").style.display = "flex";
    renderBundleWizard();
  }

  function closeBundleWizard() {
    byId("metaBundleModal").style.display = "none";
    state.bundle = null;
  }

  function bundleError(message) {
    var host = byId("metaBundleError");
    host.textContent = message || "";
    host.style.display = message ? "" : "none";
  }

  function bundleValue(name) {
    return state.bundle.values[name];
  }

  /* Поля мастера. Все пишут в state по data-b-field, поэтому перерисовка шага
     не теряет введённое. */

  function bField(name, label, attrs, hint) {
    var value = bundleValue(name);
    return '<label class="meta-field" style="display:block"><span style="display:block;' +
      'font-size:12.5px;font-weight:600;color:#3A3030;margin-bottom:6px">' +
      escapeHtml(label) + "</span>" +
      '<input class="meta-control" style="width:100%;padding:0 13px" data-b-field="' + name +
      '" ' + (attrs || "") + ' value="' + escapeHtml(value == null ? "" : String(value)) + '">' +
      (hint ? '<span class="meta-switch__hint">' + escapeHtml(hint) + "</span>" : "") +
      "</label>";
  }

  function bArea(name, label, hint) {
    return '<label class="meta-field" style="display:block"><span style="display:block;' +
      'font-size:12.5px;font-weight:600;color:#3A3030;margin-bottom:6px">' +
      escapeHtml(label) + "</span>" +
      '<textarea class="meta-control" style="width:100%;height:auto;min-height:78px;padding:10px 13px;' +
      'line-height:1.5;resize:vertical" data-b-field="' + name + '">' +
      escapeHtml(bundleValue(name) || "") + "</textarea>" +
      (hint ? '<span class="meta-switch__hint">' + escapeHtml(hint) + "</span>" : "") +
      "</label>";
  }

  function bSelect(name, label, options, hint) {
    var value = String(bundleValue(name) == null ? "" : bundleValue(name));
    return '<label class="meta-field" style="display:block">' +
      (label ? '<span style="display:block;font-size:12.5px;font-weight:600;color:#3A3030;' +
        'margin-bottom:6px">' + escapeHtml(label) + "</span>" : "") +
      '<select class="meta-control meta-select" style="width:100%" data-b-field="' + name + '">' +
      options.map(function (option) {
        return '<option value="' + escapeHtml(option.value) + '"' +
          (String(option.value) === value ? " selected" : "") + ">" +
          escapeHtml(option.label) + "</option>";
      }).join("") + "</select>" +
      (hint ? '<span class="meta-switch__hint">' + escapeHtml(hint) + "</span>" : "") +
      "</label>";
  }

  function bSwitch(name, label, hint) {
    return '<label class="meta-switch"><input type="checkbox" data-b-field="' + name + '"' +
      (bundleValue(name) ? " checked" : "") + '><span class="meta-switch__box"></span>' +
      "<span>" + escapeHtml(label) +
      (hint ? '<span class="meta-switch__hint">' + escapeHtml(hint) + "</span>" : "") +
      "</span></label>";
  }

  function bSeg(name, options) {
    var value = String(bundleValue(name));
    return '<div class="meta-seg">' + options.map(function (option) {
      return '<button type="button" data-b-seg="' + name + '" data-b-value="' +
        escapeHtml(option.value) + '" class="' +
        (String(option.value) === value ? "meta-seg--on" : "") + '">' +
        escapeHtml(option.label) + "</button>";
    }).join("") + "</div>";
  }

  function bTags(name, label, placeholder, hint) {
    var list = bundleValue(name) || [];
    return '<div class="meta-field"><span style="display:block;font-size:12.5px;font-weight:600;' +
      'color:#3A3030;margin-bottom:6px">' + escapeHtml(label) + "</span>" +
      '<div class="meta-tags" data-b-tags="' + name + '">' +
      list.map(function (item, index) {
        return '<span class="meta-tag">' + escapeHtml(tagLabel(item)) +
          '<button type="button" data-b-tag-drop="' + name + '" data-b-tag-index="' + index +
          '" aria-label="Убрать">×</button></span>';
      }).join("") +
      '<input data-b-tag-input="' + name + '" placeholder="' + escapeHtml(placeholder || "") +
      '"></div>' +
      (hint ? '<span class="meta-switch__hint">' + escapeHtml(hint) + "</span>" : "") +
      "</div>";
  }

  function tagLabel(item) {
    if (item && typeof item === "object") return item.name || item.id || "";
    return String(item);
  }

  function bNameField(name, label, title) {
    // Поле и кнопка в строку: макросов два десятка, и списком под полем они
    // занимали бы пол-экрана в каждом из трёх шагов.
    return '<div class="meta-field"><span style="display:block;font-size:12.5px;' +
      'font-weight:600;color:#3A3030;margin-bottom:6px">' + escapeHtml(label) + "</span>" +
      '<div style="display:flex;align-items:center;gap:10px">' +
      '<input class="meta-control" style="flex:1;min-width:0;padding:0 13px" maxlength="200" ' +
      'data-b-field="' + name + '" value="' +
      escapeHtml(String(bundleValue(name) || "")) + '">' +
      '<button type="button" class="meta-action" style="height:42px;padding:0 14px" ' +
      'data-b-macro-open="' + name + '" data-b-macro-title="' + escapeHtml(title) + '" ' +
      'data-b-macro-label="' + escapeHtml(label) + '">Макросы</button></div></div>';
  }

  /* ----- выпадающий список с поиском -----

     Нужен там, где вариантов сотни и они живут в Meta: интересы и языки
     приходят из её же справочника по мере ввода, страны — из нашего, потому
     что коды стран не меняются и гонять за ними в Meta незачем. */

  function comboState(name) {
    if (!state.bundle.combos) state.bundle.combos = {};
    if (!state.bundle.combos[name]) {
      state.bundle.combos[name] = { query: "", items: [], open: false, loading: false };
    }
    return state.bundle.combos[name];
  }

  var COMBO_SOURCES = {
    geo: "country", excluded_geo: "country",
    languages: "locale",
    interests: "interest", excluded_interests: "interest"
  };

  function comboLabel(name, value) {
    if (value && typeof value === "object") return value.name || value.id;
    if (COMBO_SOURCES[name] === "country") {
      var found = (bundleReference().countries || []).filter(function (row) {
        return row.code === value;
      })[0];
      return found ? countryLabel(found) : String(value);
    }
    return String(value);
  }

  function comboValues(name) {
    return state.bundle.values[name] || [];
  }

  function comboHtml(name, label, placeholder, hint) {
    return '<div class="meta-field"' + (label ? "" : ' style="display:block"') + ">" +
      (label ? '<span style="display:block;font-size:12.5px;font-weight:600;color:#3A3030;' +
        'margin-bottom:6px">' + escapeHtml(label) + "</span>" : "") +
      '<div class="meta-combo" data-b-combo="' + name + '">' +
      '<div class="meta-tags" data-b-chips="' + name + '">' + comboChipsHtml(name) +
      '<input data-b-combo-input="' + name + '" placeholder="' +
      escapeHtml(placeholder || "Поиск") + '" autocomplete="off"></div>' +
      '<div class="meta-combo__list" data-b-options="' + name + '" hidden></div></div>' +
      (hint ? '<span class="meta-switch__hint">' + escapeHtml(hint) + "</span>" : "") +
      "</div>";
  }

  function comboChipsHtml(name) {
    return comboValues(name).map(function (item, index) {
      return '<span class="meta-tag">' + escapeHtml(comboLabel(name, item)) +
        '<button type="button" data-b-tag-drop="' + name + '" data-b-tag-index="' + index +
        '" aria-label="Убрать">×</button></span>';
    }).join("");
  }

  function comboOptionsHtml(name) {
    var combo = comboState(name);
    if (combo.loading) return '<div class="meta-combo__empty">Ищем в Meta…</div>';
    if (combo.error) return '<div class="meta-combo__empty">' + escapeHtml(combo.error) + "</div>";
    if (!combo.items.length) {
      return '<div class="meta-combo__empty">' +
        (combo.query.length < 2 && COMBO_SOURCES[name] !== "country"
          ? "Введите хотя бы два символа"
          : "Ничего не нашлось") + "</div>";
    }
    return combo.items.map(function (item) {
      return '<button type="button" class="meta-combo__option" data-b-option="' + name +
        '" data-b-option-id="' + escapeHtml(item.id) + '" data-b-option-name="' +
        escapeHtml(item.name) + '">' + escapeHtml(item.name) +
        (item.path ? "<span>" + escapeHtml(item.path) + "</span>" : "") + "</button>";
    }).join("");
  }

  function redrawCombo(name) {
    var host = byId("metaBundleBody");
    var chips = host.querySelector('[data-b-chips="' + name + '"]');
    if (chips) {
      var input = chips.querySelector("[data-b-combo-input]");
      var text = input ? input.value : "";
      var focused = document.activeElement === input;
      // Перерисовываем только чипы и список: перерисовка шага целиком уводила
      // бы курсор из поля поиска на каждом символе.
      chips.innerHTML = comboChipsHtml(name) +
        '<input data-b-combo-input="' + name + '" placeholder="Поиск" autocomplete="off">';
      var next = chips.querySelector("[data-b-combo-input]");
      next.value = text;
      if (focused) next.focus();
    }
    var options = host.querySelector('[data-b-options="' + name + '"]');
    if (options) {
      options.innerHTML = comboOptionsHtml(name);
      options.hidden = !comboState(name).open;
    }
  }

  function countryLabel(row) {
    return row.code + " — " + (row.ru || row.name);
  }

  function localCountries(query) {
    // Ищем и по коду, и по обоим названиям: страну набирают то «DE», то
    // «Герм», то «Germ» — и все три раза имеют в виду одно.
    var text = query.trim().toLowerCase();
    var list = bundleReference().countries || [];
    return list.filter(function (row) {
      return !text || row.code.toLowerCase().indexOf(text) === 0 ||
        row.name.toLowerCase().indexOf(text) >= 0 ||
        (row.ru || "").toLowerCase().indexOf(text) >= 0;
    }).slice(0, 40).map(function (row) {
      return { id: row.code, name: countryLabel(row) };
    });
  }

  async function comboSearch(name) {
    var combo = comboState(name);
    var source = COMBO_SOURCES[name];
    combo.error = "";
    if (source === "country") {
      combo.items = localCountries(combo.query);
      combo.loading = false;
      return redrawCombo(name);
    }
    if (combo.query.trim().length < 2) {
      combo.items = [];
      combo.loading = false;
      return redrawCombo(name);
    }
    combo.loading = true;
    redrawCombo(name);
    var token = (combo.token || 0) + 1;
    combo.token = token;
    try {
      var page = await api.get("/meta/targeting?kind=" + source + "&q=" +
        encodeURIComponent(combo.query.trim()));
      // Ответ на устаревший запрос игнорируем: пользователь успел дописать.
      if (combo.token !== token) return;
      combo.items = page.items || [];
    } catch (error) {
      if (combo.token !== token) return;
      combo.items = [];
      combo.error = error && error.message ? error.message : "Meta не ответила";
    } finally {
      if (combo.token === token) {
        combo.loading = false;
        redrawCombo(name);
      }
    }
  }

  function comboPick(name, id, label) {
    var source = COMBO_SOURCES[name];
    var list = comboValues(name).slice();
    var exists = list.some(function (item) {
      return String(item && item.id ? item.id : item) === String(id);
    });
    if (!exists) {
      list.push(source === "country" ? id : { id: id, name: label });
      state.bundle.values[name] = list;
    }
    var combo = comboState(name);
    combo.query = "";
    combo.items = source === "country" ? localCountries("") : [];
    redrawCombo(name);
  }

  /* ----- окно макросов ----- */

  function openMacroModal(field, title, label) {
    state.macro = { field: field, level: MACRO_LEVELS[field] };
    byId("metaMacroTitle").textContent = title;
    byId("metaMacroLabel").textContent = label;
    byId("metaMacroInput").value = state.bundle.values[field] || "";
    renderMacroList();
    byId("metaMacroModal").style.display = "flex";
    byId("metaMacroInput").focus();
  }

  var MACRO_LEVELS = {
    campaign_name: "campaign",
    adset_name: "adset",
    ad_name: "ad"
  };

  function renderMacroList() {
    var list = (bundleReference().macros || {})[state.macro.level] || [];
    var text = byId("metaMacroInput").value || "";
    byId("metaMacroList").innerHTML = list.map(function (macro) {
      var used = text.indexOf(macro.code.replace(/\.\d+\}\}$/, "")) >= 0;
      return '<div class="meta-macro-row' + (used ? " meta-macro-row--on" : "") + '">' +
        '<button type="button" data-macro-code="' + escapeHtml(macro.code) + '">' +
        escapeHtml(macro.code) + "</button><span>— " + escapeHtml(macro.label) + "</span></div>";
    }).join("");
  }

  function closeMacroModal() {
    byId("metaMacroModal").style.display = "none";
    state.macro = null;
  }

  function saveMacroModal() {
    if (!state.macro) return;
    state.bundle.values[state.macro.field] = byId("metaMacroInput").value;
    closeMacroModal();
    renderBundleWizard();
  }

  function bLine(label, control) {
    return '<div class="meta-line"><span>' + escapeHtml(label) + "</span><div>" + control +
      "</div></div>";
  }

  function dictList(dict, empty) {
    var source = dict || {};
    var options = empty ? [{ value: "", label: empty }] : [];
    return options.concat(Object.keys(source).map(function (key) {
      return { value: key, label: source[key] };
    }));
  }

  function renderBundleStepOne() {
    var reference = bundleReference();
    var groups = reference.goal_groups || {};
    var goals = reference.goals || [];
    var byGroup = Object.keys(groups).map(function (group) {
      var tiles = goals.filter(function (goal) { return goal.group === group; });
      return '<div><div class="meta-card__sub">' + escapeHtml(groups[group]) + "</div>" +
        '<div class="meta-goal-row">' + tiles.map(function (goal) {
          return '<button type="button" class="meta-goal' +
            (goal.code === bundleValue("goal") ? " meta-goal--on" : "") +
            '" data-b-goal="' + escapeHtml(goal.code) + '"><span>' +
            escapeHtml(goal.label) + "</span></button>";
        }).join("") + "</div></div>";
    }).join("");

    var budgetRow = '<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">' +
      '<input class="meta-control" style="flex:1;min-width:180px;padding:0 13px" type="number" ' +
      'min="0" step="0.01" data-b-field="budget" value="' +
      escapeHtml(String(bundleValue("budget") == null ? "" : bundleValue("budget"))) + '">' +
      '<select class="meta-control meta-select" style="width:104px" data-b-field="budget_currency">' +
      ["USD", "EUR", "RUB", "UAH", "KZT", "GBP", "BRL", "TRY"].map(function (code) {
        return '<option value="' + code + '"' +
          (code === bundleValue("budget_currency") ? " selected" : "") + ">" + code + "</option>";
      }).join("") + "</select>" +
      bSwitch("budget_randomize", "Рандомизировать") + "</div>";

    return '<div class="meta-card"><div class="meta-card__title">Цель кампании</div>' +
      '<div class="meta-goals">' + byGroup + "</div></div>" +

      '<div class="meta-card"><div class="meta-card__title">Основное</div>' +
      '<div style="display:grid;gap:16px">' +
      bSwitch("advantage", "Кампания Advantage+",
        "Meta берёт плейсменты и стратегию ставок на себя — ручные настройки ниже она " +
        "будет игнорировать.") +
      bField("name", "Название связки", 'maxlength="160" placeholder="DE | Nervio | broad"') +
      bNameField("campaign_name", "Название кампании в Fb",
        "Выберите макросы для названия кампании") +
      "</div></div>" +

      '<div class="meta-card"><div class="meta-card__title">Бюджет</div>' +
      '<div style="display:grid;gap:14px">' +
      bLine("Бюджет", budgetRow) +
      bLine("Тип бюджета",
        '<div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap">' +
        bSeg("budget_kind", [
          { value: "daily", label: "Дневной" }, { value: "lifetime", label: "На весь срок" }
        ]) + "</div>") +
      bLine("Уровень бюджета", bSeg("budget_level", [
        { value: "campaign", label: "Кампания" }, { value: "adset", label: "Адсет" }
      ])) +
      bLine("Лимит адсета",
        '<input class="meta-control" style="width:100%;padding:0 13px" type="number" min="0" ' +
        'step="0.01" placeholder="без лимита" data-b-field="adset_budget_limit" value="' +
        escapeHtml(String(bundleValue("adset_budget_limit") || "")) + '">') +
      '<div class="meta-note">Бюджет на кампании Meta распределяет между адсетами сама. ' +
      "Бюджет на адсете держит расход там, где вы его поставили.</div>" +
      "</div></div>" +

      '<div class="meta-card"><div class="meta-card__title">Стратегия ставок</div>' +
      '<div style="display:grid;gap:14px">' +
      '<div class="meta-card__sub" style="margin-bottom:0">Выберите стратегию</div>' +
      '<div style="display:grid">' +
      Object.keys(reference.bid_strategies || {}).map(function (code) {
        var on = bundleValue("bid_strategy") === code;
        return '<label class="meta-choice"><input type="radio" name="bidStrategy" ' +
          'data-b-radio="bid_strategy" data-b-value="' + escapeHtml(code) + '"' +
          (on ? " checked" : "") + '><span><span class="meta-choice__title">' +
          escapeHtml(reference.bid_strategies[code]) + "</span>" +
          '<span class="meta-choice__hint">' +
          escapeHtml((reference.bid_strategy_hints || {})[code] || "") +
          "</span></span></label>";
      }).join("") + "</div>" +
      (bundleValue("bid_strategy") === "LOWEST_COST_WITHOUT_CAP" ? "" :
        bField("bid_amount",
          (reference.bid_amount_labels || {})[bundleValue("bid_strategy")] || "Ставка",
          'type="number" min="0" step="0.01"', "В валюте кабинета")) +
      bSwitch("accelerated_delivery", "Ускоренный показ",
        "Расходовать бюджет и получать результаты максимально быстро.") +
      "</div></div>" +

      '<div class="meta-card"><div class="meta-card__title">Особые категории рекламы</div>' +
      '<div class="meta-card__sub" style="margin-bottom:4px">Выберите категорию(-и)</div>' +
      Object.keys(reference.special_ad_categories || {}).map(function (code) {
        var on = (bundleValue("special_ad_categories") || []).indexOf(code) >= 0;
        return '<label class="meta-choice"><input type="checkbox" data-b-category="' +
          escapeHtml(code) + '"' + (on ? " checked" : "") + ">" +
          '<span><span class="meta-choice__title">' +
          escapeHtml(reference.special_ad_categories[code]) + "</span>" +
          '<span class="meta-choice__hint">' +
          escapeHtml((reference.special_ad_category_hints || {})[code] || "") +
          "</span></span></label>";
      }).join("") +
      '<div class="meta-note" style="margin-top:10px">Пусто — обычная реклама. Категория ' +
      "сужает таргетинг по требованиям Meta: гео, возраст и интересы она урежет сама.</div>" +
      "</div>";
  }

  /* Возраст выбирается из списка, а не набирается: границы у Meta жёсткие —
     13 и 65, — а «65» означает «65 и старше», и в поле ввода этого не видно. */
  function agePicker(name) {
    var value = String(bundleValue(name));
    var options = [];
    for (var age = 13; age <= 65; age += 1) {
      options.push('<option value="' + age + '"' + (String(age) === value ? " selected" : "") +
        ">" + (age === 65 ? "65+" : age) + "</option>");
    }
    return '<select class="meta-control meta-select" style="width:104px" data-b-field="' +
      name + '">' + options.join("") + "</select>";
  }

  /* Разброс возраста. Пример в подсказке не для красоты: «± 3 года» звучит
     однозначно только пока не дошло до границ — ниже 18 и выше 65 Meta не
     таргетирует, и разброс там односторонний. */
  var AGE_RANDOM_HINT = "Пример: возраст 25–45 и разброс 3 года — система выберет " +
    "возраст в рамках 22–28 снизу и 42–48 сверху. Ниже 18 и выше 65 разброс не " +
    "уходит: при возрасте 18–65 и разбросе в 3 года это 18–21 снизу и 62–65 сверху.";

  function ageRandomHtml() {
    var value = String(bundleValue("age_randomize_years") || 3);
    var options = [];
    for (var years = 1; years <= 10; years += 1) {
      options.push('<option value="' + years + '"' + (String(years) === value ? " selected" : "") +
        ">" + years + "</option>");
    }
    return '<span style="display:inline-flex;align-items:center;gap:7px">±' +
      '<select class="meta-control meta-select" style="width:78px" ' +
      'data-b-field="age_randomize_years">' + options.join("") + "</select>" +
      '<span style="font-size:12.5px;color:#6A6161;font-weight:600">лет</span>' +
      '<span title="' + escapeHtml(AGE_RANDOM_HINT) + '" style="width:18px;height:18px;' +
      "border-radius:50%;border:1px solid #DCD4D4;color:#9B9292;font-size:11px;" +
      'font-weight:700;display:inline-flex;align-items:center;justify-content:center;' +
      'cursor:help">?</span></span>';
  }

  function osVersionPicker(name, family) {
    var list = (bundleReference().os_versions || {})[family] || [];
    var value = String(bundleValue(name) || "");
    return '<select class="meta-control meta-select" style="width:158px" data-b-field="' +
      name + '"><option value="">любая версия</option>' +
      list.map(function (version) {
        return '<option value="' + version + '"' +
          (version === value ? " selected" : "") + ">от " + version + "</option>";
      }).join("") + "</select>";
  }

  function renderBundleStepTwo() {
    var reference = bundleReference();
    var manualGeo = bundleValue("geo_mode");
    return '<div class="meta-card"><div class="meta-card__title">Основное</div>' +
      bNameField("adset_name", "Название адсета", "Выберите макросы для названия адсета") +
      "</div>" +

      '<div class="meta-card"><div class="meta-card__title">Конверсии</div>' +
      '<div style="display:grid;gap:14px">' +
      bSelect("custom_event_type", "Событие пикселя", dictList(reference.pixel_events)) +
      bSelect("attribution", "Окно конверсии",
        (reference.attribution_windows || []).map(function (row) {
          return { value: row.code, label: row.label };
        })) +
      bSelect("engaged_view", "Вовлеченные просмотры (только для видео)", [
        { value: "none", label: "Отключено" },
        { value: "1d", label: "1 день" },
        { value: "7d", label: "7 дней" }
      ]) +
      '<div class="meta-note">Пиксель выбирается на кабинет — на шаге ' +
      "«Кабинеты» при заливе: он принадлежит кабинету, и один на всю пачку " +
      "означал бы, что все они шлют события в чужой.</div>" +
      "</div></div>" +

      '<div class="meta-card"><div class="meta-card__title">Аудитория</div>' +
      bLine("Advantage+ аудитория", bSwitch("advantage_audience", "Активировать",
        "Meta ищет шире заданного таргета. Вместе с ней ручное расширение не отправляется.")) +
      "</div>" +

      '<div class="meta-card"><div class="meta-card__title">Демография</div>' +
      '<div style="display:grid;gap:14px">' +
      bLine("Возраст", '<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">' +
        agePicker("age_min") + agePicker("age_max") +
        bSwitch("age_randomize", "Рандомизировать") +
        (bundleValue("age_randomize") ? ageRandomHtml() : "") + "</div>") +
      bLine("Пол", bSeg("genders", [
        { value: "", label: "Любой" },
        { value: "1", label: "Мужчины" },
        { value: "2", label: "Женщины" }
      ])) + "</div></div>" +

      '<div class="meta-card"><div class="meta-card__title">Гео</div>' +
      '<div style="display:grid;gap:14px">' +
      bSelect("location_type", "Локации",
        (reference.location_types || []).map(function (row) {
          return { value: row.code, label: row.label };
        })) +
      bSeg("geo_mode", [
        { value: "countries", label: "Страны" },
        { value: "regions", label: "Регионы" },
        { value: "cities", label: "Города" }
      ]) +
      (manualGeo === "countries"
        ? comboHtml("geo", "Местоположение", "Начните вводить страну")
        : manualGeo === "regions"
          ? bTags("geo_regions", "Местоположение", "3847",
            "Ключи регионов из Meta (targeting search)")
          : bTags("geo_cities", "Местоположение", "2420605",
            "Ключи городов из Meta (targeting search)")) +
      comboHtml("excluded_geo", "Исключить гео", "Начните вводить страну") +
      "</div></div>" +

      '<div class="meta-card"><div class="meta-card__title">Интересы и поведение</div>' +
      '<div style="display:grid;gap:14px">' +
      comboHtml("languages", "Языки", "Начните вводить язык",
        "Пусто — язык не ограничен") +
      '<div style="border:1px solid #EBE6E6;border-radius:13px;padding:14px 16px">' +
      '<div style="font-weight:700;font-size:13px;margin-bottom:10px">Интересы, поведение, ' +
      "демография</div>" +
      comboHtml("interests", "", "Поиск") + "</div>" +
      '<div style="border:1px solid #EBE6E6;border-radius:13px;padding:14px 16px">' +
      '<div style="font-weight:700;font-size:13px;margin-bottom:10px">Исключить интересы, ' +
      "поведение, демографию</div>" +
      comboHtml("excluded_interests", "", "Поиск") + "</div>" +
      bSwitch("targeting_expansion", "Включить расширение таргетинга",
        "Meta показывает шире заданных интересов, если так дешевле результат.") +
      "</div></div>" +

      '<div class="meta-card"><div class="meta-card__title">Плейсменты и устройства</div>' +
      '<div style="display:grid;gap:14px">' +
      bSwitch("auto_placements", "Авто-плейсменты",
        "Обычно лучший вариант: Meta сама решает, где показывать.") +
      (bundleValue("auto_placements") ? "" :
        '<div class="meta-field"><span style="display:block;font-size:12.5px;font-weight:600;' +
        'margin-bottom:6px">Плейсменты</span><div style="display:flex;flex-wrap:wrap;gap:8px">' +
        Object.keys((state.reference || {}).publisher_platforms || {}).map(function (key) {
          var on = (bundleValue("placements") || []).indexOf(key) >= 0;
          return '<button type="button" class="meta-macro" data-b-placement="' + escapeHtml(key) +
            '" style="' + (on ? "border-color:#B91414;color:#B91414;background:#FCF1F1" : "") +
            '">' + escapeHtml(state.reference.publisher_platforms[key]) + "</button>";
        }).join("") + "</div></div>") +
      bLine("Девайсы", '<div style="display:flex;flex-direction:column;gap:8px">' +
        bSeg("devices", [
          { value: "all", label: "Все" },
          { value: "desktop", label: "Десктоп" },
          { value: "mobile", label: "Мобайл" }
        ]) +
        bSeg("os", [
          { value: "all", label: "Все" },
          { value: "android", label: "Android" },
          { value: "ios", label: "iOS" }
        ]) + "</div>") +
      (bundleValue("devices") === "desktop" ? "" :
        bLine("Android", '<div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap">' +
          bSwitch("android_smartphone", "Смартфоны") +
          bSwitch("android_tablet", "Планшеты") +
          osVersionPicker("android_min", "android") + "</div>") +
        bLine("iOS", '<div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap">' +
          bSwitch("ios_iphone", "iPhone") + bSwitch("ios_ipad", "iPad") +
          bSwitch("ios_ipod", "iPod") +
          osVersionPicker("ios_min", "ios") + "</div>")) +
      bSwitch("wifi_only", "Только при подключении к Wi-Fi") +
      "</div></div>";
  }

  function renderBundleStepThree() {
    var reference = state.reference || {};
    return '<div class="meta-card"><div class="meta-card__title">Основное</div>' +
      '<div style="display:grid;gap:16px">' +
      bNameField("ad_name", "Название объявления",
        "Выберите макросы для названия объявления") +
      bField("headline", "Заголовок", 'maxlength="600"', "Доступен spintax: {Купи|Закажи}") +
      bArea("primary_text", "Текст объявления", "Доступен spintax") +
      bArea("description", "Описание", "Доступен spintax") +
      bField("link_url", "Ссылка", 'placeholder="https://..."',
        "Сюда подставится ID кампании, если в подключении задан sub_id") +
      bSwitch("multi_advertiser",
        "Объявления в рекламном блоке с несколькими рекламодателями") +
      bSwitch("advantage_creative", "Включить оптимизации Advantage+ для креативов",
        "Meta может подправить яркость, обрезку и порядок текста.") +
      bSelect("call_to_action", "Призыв к действию",
        dictList(reference.call_to_actions)) +
      bField("page_id", "ID страницы Facebook", 'maxlength="60"',
        "Без страницы Meta не примет объявление") +
      "</div></div>";
  }

  function renderBundleWizard() {
    var wizard = state.bundle;
    if (!wizard) return;
    byId("metaBundleSteps").innerHTML = BUNDLE_STEPS.map(function (label, index) {
      var number = index + 1;
      var cls = number === wizard.step ? " meta-step--active"
        : number < wizard.step ? " meta-step--done" : "";
      return '<div class="meta-step' + cls + '"><span class="meta-step-dot">' +
        (number < wizard.step ? "✓" : number) + '</span><span class="meta-step-label">' +
        escapeHtml(label) + "</span></div>" +
        (number < BUNDLE_STEPS.length ? '<span class="meta-step-line"></span>' : "");
    }).join("");
    var body = byId("metaBundleBody");
    if (wizard.step === 1) body.innerHTML = renderBundleStepOne();
    if (wizard.step === 2) body.innerHTML = renderBundleStepTwo();
    if (wizard.step === 3) body.innerHTML = renderBundleStepThree();
    byId("metaBundleBack").style.visibility = wizard.step === 1 ? "hidden" : "";
    byId("metaBundleNext").textContent = wizard.step === 3
      ? (wizard.id ? "Сохранить" : "Создать")
      : "Продолжить";
    bundleError("");
  }

  function bundlePayload() {
    var values = state.bundle.values;
    var numeric = function (value) {
      var text = String(value == null ? "" : value).trim();
      return text === "" ? null : text;
    };
    return {
      name: String(values.name || "").trim(),
      geo: values.geo.map(function (code) { return String(code).trim().toUpperCase(); }),
      age_min: Number(values.age_min) || 18,
      age_max: Number(values.age_max) || 65,
      genders: values.genders ? [Number(values.genders)] : [],
      languages: values.languages.map(function (item) {
        return Number(item && item.id ? item.id : item);
      }).filter(function (id) { return !!id; }),
      interests: values.interests.map(tagObject),
      placements: values.placements.length
        ? { publisher_platforms: values.placements }
        : {},
      daily_budget: numeric(values.budget),
      bid_strategy: values.bid_strategy,
      page_id: String(values.page_id || "").trim() || null,
      pixel_id: null,
      custom_event_type: values.custom_event_type || null,
      call_to_action: values.call_to_action,
      notes: values.notes || null,
      settings: {
        campaign: {
          goal: values.goal,
          advantage: values.advantage,
          campaign_name: values.campaign_name,
          budget_kind: values.budget_kind,
          budget_level: values.budget_level,
          budget_currency: values.budget_currency,
          budget_randomize: values.budget_randomize,
          adset_budget_limit: numeric(values.adset_budget_limit),
          bid_amount: numeric(values.bid_amount),
          accelerated_delivery: values.accelerated_delivery,
          special_ad_categories: values.special_ad_categories
        },
        adset: {
          adset_name: values.adset_name,
          attribution: values.attribution,
          engaged_view: values.engaged_view,
          advantage_audience: values.advantage_audience,
          age_randomize: values.age_randomize,
          age_randomize_years: Number(values.age_randomize_years) || 3,
          location_type: values.location_type,
          geo_regions: values.geo_regions,
          geo_cities: values.geo_cities,
          excluded_geo: values.excluded_geo.map(function (code) {
            return String(code).trim().toUpperCase();
          }),
          excluded_interests: values.excluded_interests.map(tagObject),
          // Названия языков сохраняем рядом с ID: иначе при следующем открытии
          // связки в поле оказалась бы голая «6».
          language_labels: values.languages.map(tagObject),
          targeting_expansion: values.targeting_expansion,
          auto_placements: values.auto_placements,
          devices: values.devices,
          os: values.os,
          android_smartphone: values.android_smartphone,
          android_tablet: values.android_tablet,
          android_min: values.android_min,
          ios_iphone: values.ios_iphone,
          ios_ipad: values.ios_ipad,
          ios_ipod: values.ios_ipod,
          ios_min: values.ios_min,
          wifi_only: values.wifi_only
        },
        ad: {
          ad_name: values.ad_name,
          multilingual: values.multilingual,
          headline: values.headline || null,
          primary_text: values.primary_text || null,
          description: values.description || null,
          link_url: String(values.link_url || "").trim() || null,
          multi_advertiser: values.multi_advertiser,
          advantage_creative: values.advantage_creative
        }
      }
    };
  }

  function tagObject(item) {
    if (item && typeof item === "object") return item;
    var parts = String(item).split("|");
    return { id: parts[0].trim(), name: (parts[1] || "").trim() };
  }

  async function bundleNext() {
    var wizard = state.bundle;
    if (wizard.step === 1) {
      if (!String(wizard.values.name || "").trim()) {
        return bundleError("Укажите название связки");
      }
      wizard.step = 2;
      return renderBundleWizard();
    }
    if (wizard.step === 2) {
      if (Number(wizard.values.age_min) > Number(wizard.values.age_max)) {
        return bundleError("Возраст «от» больше, чем «до»");
      }
      wizard.step = 3;
      return renderBundleWizard();
    }
    var payload = bundlePayload();
    var button = byId("metaBundleNext");
    button.disabled = true;
    try {
      if (wizard.id) {
        await api.patch("/meta/templates/" + wizard.id, payload);
      } else {
        await api.post("/meta/templates", payload);
      }
      await loadTemplates();
      closeBundleWizard();
    } catch (error) {
      bundleError(error && error.message ? error.message : "Не удалось сохранить связку");
    } finally {
      button.disabled = false;
    }
  }

  function bundleCopyName(name) {
    // Название связки уникально в воркспейсе, поэтому копию нумеруем сразу:
    // иначе вторая копия подряд отбивалась бы ошибкой сервера.
    var taken = state.templates.map(function (row) { return row.name; });
    var candidate = name + " (копия)";
    var index = 2;
    while (taken.indexOf(candidate) >= 0) {
      candidate = name + " (копия " + index + ")";
      index += 1;
    }
    return candidate;
  }

  function bundleCopyPayload(bundle) {
    return {
      name: bundleCopyName(bundle.name),
      geo: bundle.geo || [],
      age_min: bundle.age_min || 18,
      age_max: bundle.age_max || 65,
      genders: bundle.genders || [],
      languages: bundle.languages || [],
      interests: bundle.interests || [],
      placements: bundle.placements || {},
      daily_budget: bundle.daily_budget || bundle.lifetime_budget || null,
      bid_strategy: bundle.bid_strategy,
      page_id: bundle.page_id,
      pixel_id: bundle.pixel_id,
      custom_event_type: bundle.custom_event_type,
      call_to_action: bundle.call_to_action,
      notes: bundle.notes,
      settings: bundle.settings || {}
    };
  }

  async function bundleBulk(action) {
    var picked = (state.bundlePick || []).map(function (id) {
      return state.templates.filter(function (row) { return row.id === id; })[0];
    }).filter(Boolean);
    if (!picked.length) return;

    if (action === "copy") {
      for (var i = 0; i < picked.length; i += 1) {
        var created = await api.post("/meta/templates", bundleCopyPayload(picked[i]));
        // Дописываем в список сразу: следующая копия должна видеть занятое имя.
        state.templates.push(Object.assign({}, picked[i], created));
      }
      state.bundlePick = [];
      return loadTemplates();
    }

    if (!(await askConfirm({
      title: picked.length === 1 ? "Удалить связку?" : "Удалить связки?",
      message: picked.map(function (row) { return "«" + row.name + "»"; }).join(", ") +
        ". Уже опубликованные заливы это не затронет.",
      confirmLabel: "Удалить",
      danger: true
    }))) return;
    for (var index = 0; index < picked.length; index += 1) {
      await api.delete("/meta/templates/" + picked[index].id);
    }
    state.bundlePick = [];
    await loadTemplates();
  }

  function onBundleInput(event) {
    var combo = event.target.closest ? event.target.closest("[data-b-combo-input]") : null;
    if (combo) {
      var name = combo.getAttribute("data-b-combo-input");
      var box = comboState(name);
      box.query = combo.value;
      box.open = true;
      // Ждём паузы в наборе: Meta считает каждый запрос, а поиск по интересам
      // на каждом символе — это десять запросов на одно слово.
      window.clearTimeout(box.timer);
      box.timer = window.setTimeout(function () {
        comboSearch(name).catch(function () {});
      }, COMBO_SOURCES[name] === "country" ? 0 : 320);
      return;
    }
    var field = event.target.closest ? event.target.closest("[data-b-field]") : null;
    if (field && field.type !== "checkbox") {
      // Пишем в state без перерисовки: перерисовка на каждом символе уводила бы
      // курсор в начало строки.
      state.bundle.values[field.getAttribute("data-b-field")] = field.value;
    }
  }

  function onBundleChange(event) {
    var radio = event.target.closest ? event.target.closest("[data-b-radio]") : null;
    if (radio) {
      state.bundle.values[radio.getAttribute("data-b-radio")] =
        radio.getAttribute("data-b-value");
      return renderBundleWizard();
    }
    var category = event.target.closest ? event.target.closest("[data-b-category]") : null;
    if (category) {
      var code = category.getAttribute("data-b-category");
      var list = (state.bundle.values.special_ad_categories || []).filter(function (item) {
        return item !== code;
      });
      if (category.checked) list.push(code);
      state.bundle.values.special_ad_categories = list;
      return;
    }
    var field = event.target.closest ? event.target.closest("[data-b-field]") : null;
    if (!field) return;
    var name = field.getAttribute("data-b-field");
    state.bundle.values[name] = field.type === "checkbox" ? field.checked : field.value;
    // Перерисовываем только то, от чего зависит состав формы.
    if (["auto_placements", "devices", "bid_strategy", "advantage_audience",
      "age_randomize"].indexOf(name) >= 0) {
      renderBundleWizard();
    }
  }

  function onBundleClick(event) {
    var target = event.target;
    var closest = function (selector) {
      return target.closest ? target.closest(selector) : null;
    };
    var macro = closest("[data-b-macro-open]");
    if (macro) {
      return openMacroModal(
        macro.getAttribute("data-b-macro-open"),
        macro.getAttribute("data-b-macro-title"),
        macro.getAttribute("data-b-macro-label")
      );
    }
    var option = closest("[data-b-option]");
    if (option) {
      return comboPick(
        option.getAttribute("data-b-option"),
        option.getAttribute("data-b-option-id"),
        option.getAttribute("data-b-option-name")
      );
    }
    var comboInput = closest("[data-b-combo-input]");
    if (comboInput) {
      var comboName = comboInput.getAttribute("data-b-combo-input");
      comboState(comboName).open = true;
      comboSearch(comboName).catch(function () {});
      return;
    }
    // Клик мимо любого списка его закрывает — открытый список перекрывает
    // поля под собой.
    Object.keys(state.bundle.combos || {}).forEach(function (name) {
      if (!closest('[data-b-combo="' + name + '"]')) {
        state.bundle.combos[name].open = false;
        redrawCombo(name);
      }
    });
    var goal = closest("[data-b-goal]");
    if (goal) {
      state.bundle.values.goal = goal.getAttribute("data-b-goal");
      return renderBundleWizard();
    }
    var seg = closest("[data-b-seg]");
    if (seg) {
      state.bundle.values[seg.getAttribute("data-b-seg")] = seg.getAttribute("data-b-value");
      return renderBundleWizard();
    }
    var macro = closest("[data-b-macro]");
    if (macro) {
      var field = macro.getAttribute("data-b-macro");
      state.bundle.values[field] = String(state.bundle.values[field] || "") +
        macro.getAttribute("data-b-macro-code");
      return renderBundleWizard();
    }
    var placement = closest("[data-b-placement]");
    if (placement) {
      var key = placement.getAttribute("data-b-placement");
      var list = (state.bundle.values.placements || []).filter(function (item) {
        return item !== key;
      });
      if (list.length === (state.bundle.values.placements || []).length) list.push(key);
      state.bundle.values.placements = list;
      return renderBundleWizard();
    }
    var add = closest("[data-b-tag-add]");
    if (add) {
      var addName = add.getAttribute("data-b-tag-add");
      var addValue = add.getAttribute("data-b-tag-value");
      var current = state.bundle.values[addName] || [];
      if (current.indexOf(addValue) < 0) current.push(addValue);
      state.bundle.values[addName] = current;
      return renderBundleWizard();
    }
    var drop = closest("[data-b-tag-drop]");
    if (drop) {
      var dropName = drop.getAttribute("data-b-tag-drop");
      var index = Number(drop.getAttribute("data-b-tag-index"));
      state.bundle.values[dropName] = (state.bundle.values[dropName] || [])
        .filter(function (_item, position) { return position !== index; });
      return COMBO_SOURCES[dropName] ? redrawCombo(dropName) : renderBundleWizard();
    }
  }

  function onBundleKeydown(event) {
    var input = event.target.closest ? event.target.closest("[data-b-tag-input]") : null;
    if (!input) return;
    if (event.key !== "Enter" && event.key !== ",") return;
    event.preventDefault();
    var value = input.value.trim();
    if (!value) return;
    var name = input.getAttribute("data-b-tag-input");
    var list = (state.bundle.values[name] || []).slice();
    list.push(name === "interests" || name === "excluded_interests" ? tagObject(value) : value);
    state.bundle.values[name] = list;
    renderBundleWizard();
  }

  /* ---------- креативы (ТЗ 3.6) ---------- */

  /* ---------- автоправила (ТЗ 3.8) ---------- */

  async function loadRules() {
    await loadReference();
    var page = await api.get("/meta/rules");
    state.rules = page.items || [];
    var events = await api.get("/meta/rule-events?limit=50");
    state.events = events.items || [];
    renderRules();
    renderEvents();
  }

  /* «Данные» одной строкой: с чем работает правило, где и за какой период. */
  function ruleScope(rule) {
    var reference = state.reference || {};
    var parts = [
      (reference.rule_levels || {})[rule.level] || rule.level,
      (reference.rule_statuses || {})[rule.entity_status] || rule.entity_status,
      (reference.rule_windows || {})[rule.window] || rule.window
    ];
    return '<div style="font-weight:700;font-size:12.5px">' +
      escapeHtml(parts.join(" · ")) + "</div>" +
      '<div style="font-size:10.5px;color:#9B9292;margin-top:3px">' +
      escapeHtml(rule.account_name || "Все кабинеты") +
      " · мин. расход " + money(rule.min_spend) + "</div>";
  }

  function ruleConditions(rule) {
    var reference = state.reference || {};
    var list = rule.conditions || [];
    if (!list.length) {
      return '<span style="color:#C9821F;font-weight:700">Без условий — сработает на всех</span>';
    }
    return list.map(function (item) {
      var metric = (reference.metrics || {})[item.metric] || item.metric;
      var sign = (reference.rule_operators || {})[item.operator] || item.operator;
      return '<span class="meta-chip" style="background:#F4F0F0;color:#3A3030;margin:2px 4px 2px 0">' +
        escapeHtml(metric + " " + sign + " " + item.value) + "</span>";
    }).join("");
  }

  function frequencyLabel(minutes) {
    var reference = (state.reference || {}).rule_frequencies || {};
    return reference[String(minutes)] || (minutes + " мин");
  }

  function renderRules() {
    var body = byId("metaRulesBody");
    var reference = state.reference || {};
    byId("metaRuleCreate").style.display = state.canLaunch ? "" : "none";
    byId("metaRulesRun").style.display = state.canLaunch ? "" : "none";
    if (!state.rules.length) {
      body.innerHTML = '<tr><td colspan="8" style="padding:44px 20px;text-align:center;' +
        'color:#9B9292;font-size:13px">Правил нет. Начните с уведомления — оно ничего ' +
        "не выключает, но покажет, как правило вело бы себя на реальных цифрах.</td></tr>";
      byId("metaRulesCount").textContent = "Правил: 0";
      return;
    }
    body.innerHTML = state.rules.map(function (rule) {
      var action = (reference.rule_actions || {})[rule.action] || rule.action;
      return '<tr class="meta-row" style="border-bottom:1px solid #F7F4F4">' +
        '<td class="meta-cell meta-cell--left" style="padding-left:20px;font-weight:700">' +
        escapeHtml(rule.name) + "</td>" +
        '<td class="meta-cell meta-cell--left" style="color:#6A6161">' +
        ruleScope(rule) + "</td>" +
        '<td class="meta-cell meta-cell--left" style="max-width:300px;white-space:normal">' +
        ruleConditions(rule) + "</td>" +
        '<td class="meta-cell">' + escapeHtml(frequencyLabel(rule.frequency_minutes)) +
        "</td>" +
        '<td class="meta-cell">' + money(rule.min_spend) + "</td>" +
        '<td class="meta-cell meta-cell--left">' + escapeHtml(action) +
        (rule.action_value ? " " + rule.action_value + " %" : "") + "</td>" +
        '<td class="meta-cell meta-cell--left">' +
        (rule.is_enabled
          ? '<span class="meta-chip" style="color:#16B57F;background:#E4F7F0">' +
            '<span class="meta-dot" style="background:#16B57F"></span>включено</span>'
          : '<span class="meta-chip" style="color:#9B9292;background:#F7F4F4">' +
            '<span class="meta-dot" style="background:#9B9292"></span>выключено</span>') +
        '<div style="font-size:10.5px;color:#9B9292;margin-top:4px">' +
        (rule.last_triggered_at ? "сработало " + formatMoment(rule.last_triggered_at) : "не срабатывало") +
        "</div></td>" +
        '<td class="meta-cell meta-cell--left" style="padding-right:20px">' +
        '<div style="display:flex;gap:6px">' +
        '<button class="meta-action" data-rule-preview="' + escapeHtml(rule.id) +
        '">Что сработает</button>' +
        (state.canLaunch ? '<button class="meta-action" data-rule-open="' +
          escapeHtml(rule.id) + '">Изменить</button>' : "") +
        "</div></td></tr>";
    }).join("");
    byId("metaRulesCount").textContent = "Правил: " + state.rules.length;
  }

  function renderEvents() {
    var host = byId("metaEventsList");
    var unread = state.events.filter(function (event) { return !event.acknowledged; }).length;
    var badge = byId("metaRulesBadge");
    badge.style.display = unread ? "" : "none";
    badge.textContent = unread;
    byId("metaEventsAck").style.display = unread ? "" : "none";
    if (!state.events.length) {
      host.innerHTML = '<div style="padding:34px 22px;text-align:center;color:#9B9292;' +
        'font-size:12.5px;font-weight:600">Правила ещё не срабатывали</div>';
      return;
    }
    host.innerHTML = state.events.map(function (event) {
      var tone = event.error ? "#C41616" : event.applied ? "#16B57F" : "#C9821F";
      return '<div style="display:flex;gap:12px;padding:14px 22px;border-bottom:1px solid #F7F4F4' +
        (event.acknowledged ? ";opacity:.55" : "") + '">' +
        '<span class="meta-dot" style="background:' + tone + ';margin-top:6px;flex-shrink:0"></span>' +
        '<div style="min-width:0;flex:1">' +
        '<div style="font-size:12.5px;font-weight:600;color:#3A3030;line-height:1.5">' +
        escapeHtml(event.message) + "</div>" +
        (event.error
          ? '<div style="font-size:11.5px;color:#C41616;font-weight:600;margin-top:4px">' +
            escapeHtml(event.error) + "</div>"
          : "") +
        '<div style="font-size:10.5px;color:#9B9292;margin-top:4px">' +
        formatMoment(event.created_at) +
        (event.applied ? " · действие выполнено" : " · только уведомление") + "</div>" +
        "</div></div>";
    }).join("");
  }

  function openRuleForm(ruleId) {
    var reference = state.reference || {};
    var rule = ruleId
      ? state.rules.filter(function (row) { return row.id === ruleId; })[0]
      : { level: "campaign", entity_status: "active", window: "today",
          conditions: [{ metric: "roi", operator: "lt", value: "0" }],
          min_spend: 10, action: "notify", frequency_minutes: 60,
          cooldown_minutes: 180, is_enabled: true };
    if (!rule) return;
    openForm({
      title: ruleId ? "Автоправило" : "Новое автоправило",
      subtitle: "Условие проверяется по статистике CRM: расход из Meta, доход из Keitaro.",
      fields: [
        { name: "name", label: "Название", value: rule.name || "" },
        { name: "account_id", label: "Кабинет", type: "select", half: true,
          options: [{ value: "", label: "Все кабинеты" }].concat(
            referenceAccounts().map(function (account) {
              return { value: account.id, label: account.name,
                selected: account.id === rule.account_id };
            })) },
        { name: "level", label: "С чем работать", type: "select", half: true,
          options: dictOptions(reference.rule_levels, rule.level),
          hint: "Действие применится к объекту этого уровня" },
        { name: "entity_status", label: "Какие статусы брать", type: "select", half: true,
          options: dictOptions(reference.rule_statuses, rule.entity_status) },
        { name: "window", label: "Период статы", type: "select", half: true,
          options: dictOptions(reference.rule_windows, rule.window) },
        { name: "frequency_minutes", label: "Частота проверки", type: "select", half: true,
          options: dictOptions(reference.rule_frequencies, String(rule.frequency_minutes)) },
        { name: "conditions", label: "Условия", type: "conditions",
          value: rule.conditions || [],
          hint: "Соединяются И. Без условий правило сработает на всех объектах области" },
        { name: "min_spend", label: "Минимальный расход", type: "number", half: true, min: 0,
          step: "0.01", value: rule.min_spend,
          hint: "Ниже этой суммы правило молчит" },
        { name: "action", label: "Что делать", type: "select", half: true,
          options: dictOptions(reference.rule_actions, rule.action) },
        { name: "action_value", label: "Процент изменения бюджета", type: "number", half: true,
          min: 0, max: 500, step: "1",
          value: rule.action_value === null || rule.action_value === undefined
            ? "" : rule.action_value },
        { name: "cooldown_minutes", label: "Пауза между срабатываниями, мин", type: "number",
          half: true, min: 0, max: 10080, value: rule.cooldown_minutes },
        { name: "is_enabled", label: "Правило включено", type: "checkbox",
          value: !!rule.is_enabled }
      ],
      note: "Правило с действием само останавливает кампании и меняет бюджеты. " +
        "Проверьте его кнопкой «Что сработает», прежде чем включать.",
      onSave: async function (values) {
        var payload = {
          name: values.name.trim(),
          account_id: values.account_id || null,
          level: values.level,
          entity_status: values.entity_status,
          window: values.window,
          conditions: values.conditions || [],
          min_spend: values.min_spend || "0",
          action: values.action,
          action_value: values.action_value === "" ? null : values.action_value,
          frequency_minutes: Number(values.frequency_minutes),
          cooldown_minutes: Number(values.cooldown_minutes),
          is_enabled: values.is_enabled
        };
        if (!payload.name) throw new Error("Укажите название правила");
        if (!payload.conditions.length && payload.action !== "notify") {
          // Правило без условий имеет смысл, но «остановить всё» стоит
          // подтвердить осознанно, а не проскочить по невнимательности.
          if (!(await askConfirm({
            title: "Правило без условий",
            message: "Оно сработает на всех объектах области и выполнит «" +
              ((reference.rule_actions || {})[payload.action] || payload.action) +
              "». Это точно то, что нужно?",
            confirmLabel: "Да, сохранить",
            danger: true
          }))) {
            // Не `return`: тогда форма закрылась бы как после успешного
            // сохранения, и набранное правило пропало бы молча.
            throw new Error("Не сохранено. Добавьте условие или подтвердите ещё раз.");
          }
        }
        if (ruleId) {
          await api.patch("/meta/rules/" + ruleId, payload);
        } else {
          await api.post("/meta/rules", payload);
        }
        await loadRules();
      },
      onDelete: ruleId ? async function () {
        if (!(await askConfirm({
          title: "Удалить правило?",
          message: "«" + rule.name + "» перестанет срабатывать.",
          confirmLabel: "Удалить",
          danger: true
        }))) return;
        await api.delete("/meta/rules/" + ruleId);
        await loadRules();
        closeForm();
      } : null
    });
  }

  async function previewRule(ruleId) {
    try {
      var result = await api.post("/meta/rules/" + ruleId + "/preview", {});
      var levels = (state.reference || {}).rule_levels || {};
      var noun = (levels[result.level] || "объект").toLowerCase();
      if (!result.matched) {
        notify({
          title: "Сейчас правило ни на кого не сработает",
          message: "Проверено объектов (" + noun + "): " + result.scanned + "."
        });
        return;
      }
      notify({
        title: "Сработает на " + result.matched + " из " + result.scanned,
        message: result.objects.map(function (row) {
          var values = Object.keys(row.values || {}).map(function (metric) {
            return metric + " " + row.values[metric];
          }).join(", ");
          return "· " + row.name + (values ? " — " + values : "") +
            " (расход " + row.spend + ")";
        }).join("\n")
      });
    } catch (error) {
      notify({
        title: "Не удалось проверить правило",
        message: error && error.message ? error.message : ""
      });
    }
  }

  async function runRules() {
    if (!(await askConfirm({
      title: "Прогнать все включённые правила сейчас?",
      message: "Правила с действием остановят кампании или изменят бюджеты " +
        "по-настоящему.",
      confirmLabel: "Прогнать",
      danger: true
    }))) return;
    var button = byId("metaRulesRun");
    button.disabled = true;
    try {
      var result = await api.post("/meta/rules/run", {});
      await loadRules();
      notify({
        title: "Правила прогнаны",
        message: "Проверено правил: " + result.rules + "\nСработало: " +
          result.triggered + "\nДействий выполнено: " + result.applied
      });
    } catch (error) {
      notify({
        title: "Не удалось прогнать правила",
        message: error && error.message ? error.message : ""
      });
    } finally {
      button.disabled = false;
    }
  }

  async function ackEvents() {
    try {
      await api.post("/meta/rule-events/ack", []);
      await loadRules();
    } catch (error) {
      notify({
        title: "Не удалось отметить события",
        message: error && error.message ? error.message : ""
      });
    }
  }

  /* ---------- браузерная сессия для токена EAAB ----------
   *
   * Токен сессии нельзя вставлять готовой строкой: вытащенный в одном месте и
   * использованный с другого IP, он мгновенно отзывается, а при повторах Meta
   * банит сам аккаунт. Поэтому токен получается из живого браузера, который
   * открывается с cookies аккаунта и его прокси — ровно как заходит человек.
   */

  var SESSION_STATUS_LABELS = {
    idle: "Ожидание запуска",
    starting: "Запуск браузера…",
    waiting_login: "Ожидание входа…",
    saved: "Сессия сохранена",
    restoring: "Восстановление сессии…",
    token: "Токен получен",
    error: "Ошибка"
  };

  function sessionStatus(scope, text) {
    var host = scope === "wizard" ? byId("metaWizSessionStatus") : byId("metaModalSessionStatus");
    host.innerHTML = text;
  }

  function sessionError(scope, text) {
    var host = scope === "wizard" ? byId("metaWizSessionStatus") : byId("metaModalSessionStatus");
    host.innerHTML = '<span style="color:#B91414">' + escapeHtml(text) + "</span>";
  }

  function sessionTokenPreview(token) {
    return escapeHtml(token.slice(0, 14)) + "…" +
      '<span style="color:#9B9292"> (' + token.length + " симв.)</span>";
  }

  function setSessionButtons(scope, running) {
    var start = scope === "wizard" ? byId("metaWizSessionStart") : byId("metaModalSessionStart");
    var close = scope === "wizard" ? byId("metaWizSessionClose") : byId("metaModalSessionClose");
    var cookies = scope === "wizard" ? byId("metaWizCookies") : byId("metaModalCookies");
    if (running) {
      start.style.display = "none";
      close.style.display = "";
      cookies.disabled = true;
    } else {
      start.style.display = "";
      close.style.display = "none";
      cookies.disabled = false;
    }
  }

  function sessionStop() {
    if (state.session && state.session.timer) {
      window.clearInterval(state.session.timer);
      state.session.timer = null;
    }
    if (state.session && state.session.sessionId) {
      var id = state.session.sessionId;
      api.post("/meta/session/" + id + "/close", {}).catch(function () {});
    }
    state.session = null;
  }

  function sessionResetUi(scope) {
    sessionStatus(scope, "");
    setSessionButtons(scope, false);
  }

  /* Показывает или прячет блок браузера в зависимости от выбранного способа. */
  function renderSessionAuthUI(scope) {
    var method = scope === "wizard" ? wizardMethodValue() : authMethodValue();
    var isSession = method === "session";
    if (scope === "wizard") {
      byId("metaWizTokenField").style.display = isSession ? "none" : "";
      byId("metaWizSessionBlock").style.display = isSession ? "" : "none";
      byId("metaWizProxyHint").innerHTML = isSession
        ? '<b style="color:#B91414">Обязателен:</b> токен сессии живёт только со своим прокси — без него Meta заблокирует сессию и аккаунт'
        : "Нужен, если кабинеты живут за своим прокси. Поддерживаются http, https и socks5";
    } else {
      byId("metaModalTokenField").style.display = isSession ? "none" : "";
      byId("metaModalSessionBlock").style.display = isSession ? "" : "none";
      byId("metaFieldProxyHint").innerHTML = isSession
        ? '<b style="color:#B91414">Обязателен:</b> токен сессии живёт только со своим прокси — без него Meta заблокирует сессию и аккаунт'
        : "Через него пойдут все запросы этого подключения — синхронизация, заливы и автоправила";
    }
    if (!isSession) {
      sessionStop();
      sessionResetUi(scope);
      // Токен, полученный в старой браузерной сессии, без неё уже не тот: при
      // возврате на способ «Токен сессии» придётся запускать браузер заново.
      if (scope === "wizard" && state.wizard) state.wizard.sessionToken = null;
      if (scope === "modal") byId("metaModalSessionToken").value = "";
    }
  }

  async function wizardCheckProxy() {
    var proxy = byId("metaWizProxy").value.trim();
    if (!proxy) {
      sessionError("wizard", "Укажите прокси");
      return;
    }
    sessionStatus("wizard", "Проверяем прокси…");
    try {
      var result = await api.post("/meta/proxy/check", { proxy_url: proxy });
      sessionStatus("wizard", "Прокси работает: IP " + escapeHtml(result.ip || "—") +
        ", пинг " + (result.latency_ms || "—") + " мс");
    } catch (error) {
      sessionError("wizard", error && error.message ? error.message : "Прокси не работает");
    }
  }

  async function sessionStart(scope) {
    var proxy = (scope === "wizard" ? byId("metaWizProxy") : byId("metaFieldProxy")).value.trim();
    var cookies = (scope === "wizard" ? byId("metaWizCookies") : byId("metaModalCookies")).value.trim();
    var userAgent = (scope === "wizard" ? byId("metaWizUserAgent") : byId("metaFieldUserAgent")).value.trim();
    var connectionId = scope === "modal" && state.connections.length
      ? state.connections[0].id : null;
    if (!proxy) {
      sessionError(scope, "Укажите прокси — без него браузер не запустится (защита от бана)");
      return;
    }
    sessionStop();
    if (scope === "wizard") state.wizard.sessionToken = null;
    if (scope === "modal") byId("metaModalSessionToken").value = "";
    state.session = { scope: scope, sessionId: null, token: null, timer: null };
    sessionStatus(scope, "Запускаем браузер…");
    setSessionButtons(scope, true);
    try {
      var payload = { cookies: cookies, proxy_url: proxy, user_agent: userAgent || null };
      if (connectionId) payload.connection_id = connectionId;
      var res = await api.post("/meta/session/start", payload);
      state.session.sessionId = res.session_id;
      if (res.status === "error") {
        sessionError(scope, res.error || "Ошибка запуска");
        setSessionButtons(scope, false);
        return;
      }
      sessionPoll(scope);
    } catch (error) {
      sessionError(scope, error && error.message ? error.message : "Не удалось запустить браузер");
      setSessionButtons(scope, false);
    }
  }

  function sessionPoll(scope) {
    if (!state.session || !state.session.sessionId) return;
    if (state.session.timer) window.clearInterval(state.session.timer);
    state.session.timer = window.setInterval(async function () {
      var current = state.session;
      if (!current || !current.sessionId) return;
      var id = current.sessionId;
      try {
        var st = await api.get("/meta/session/" + id + "/status");
        if (!state.session || state.session.sessionId !== id) return;
        if (st.status === "error") {
          window.clearInterval(current.timer);
          current.timer = null;
          sessionError(scope, st.error || "Ошибка сессии");
          setSessionButtons(scope, false);
          return;
        }
        if (st.status === "waiting_login" || st.status === "restoring") {
          sessionStatus(scope, escapeHtml(SESSION_STATUS_LABELS[st.status] || st.status) +
            " — если требуется ручной вход, выполните его через VNC (порт 5900)");
          return;
        }
        if (st.status === "saved" || st.token_ready) {
          var tokenRes = await api.post("/meta/session/" + id + "/token", {});
          if (!state.session || state.session.sessionId !== id) return;
          window.clearInterval(current.timer);
          current.timer = null;
          current.token = tokenRes.token;
          onSessionToken(scope, tokenRes.token);
          return;
        }
        sessionStatus(scope, escapeHtml(SESSION_STATUS_LABELS[st.status] || st.status));
      } catch (error) {
        if (!state.session || state.session.sessionId !== id) return;
        window.clearInterval(current.timer);
        current.timer = null;
        sessionError(scope, error && error.message ? error.message : "Опрос статуса не удался");
        setSessionButtons(scope, false);
      }
    }, 3000);
  }

  function onSessionToken(scope, token) {
    if (!token) {
      sessionError(scope, "Токен не найден — попробуйте зайти в Ads Manager в окне браузера и повторить");
      return;
    }
    if (scope === "wizard") {
      state.wizard.sessionToken = token;
    } else {
      byId("metaModalSessionToken").value = token;
    }
    sessionStatus(scope, '<span style="color:#0E7350;font-weight:700">✓ EAAB-токен получен:</span> ' +
      sessionTokenPreview(token));
    setSessionButtons(scope, false);
    byId("metaWizNext").disabled = false;
  }

  /* ---------- мастер подключения ---------- */

  var WIZARD_STEPS = ["Инструкции", "Токен", "Проверка", "Импорт", "Готово"];

  /* Инструкция под выбранный способ. Держать одну на всех нельзя: у токена из
     панели приложения и у токена сессии шаги вообще не пересекаются, а общий
     текст «создайте приложение» для второго просто неверен. */
  var AUTH_GUIDES = {
    system_user: {
      needs: [
        "аккаунт разработчика Facebook",
        "права Admin или Employee в Business Manager",
        "приложение, созданное на developers.facebook.com"
      ],
      steps: [
        {
          title: "Создайте приложение в Facebook",
          text: "developers.facebook.com → My Apps → Create App. Тип приложения — Business.",
          link: { url: "https://developers.facebook.com/apps", label: "Открыть Facebook Apps" }
        },
        {
          title: "Привяжите приложение к Business Manager",
          text: "Настройки приложения → App Roles → Add Business Manager. Это даст " +
            "приложению доступ к рекламным кабинетам вашего BM."
        },
        {
          title: "Заведите системного пользователя и выдайте ему кабинеты",
          text: "Business Settings → Users → System users → Add. Затем Assign assets: " +
            "рекламные кабинеты с ролью «Управление кампаниями», страницы и пиксели."
        },
        {
          title: "Выпустите токен",
          text: "Generate New Token → выберите приложение и разрешения. Срок токена " +
            "поставьте Never: с «60 days» синхронизация встанет через два месяца.",
          scopes: ["ads_read", "ads_management", "business_management", "pages_show_list"]
        }
      ],
      note: "ads_read достаточно для статистики. ads_management нужен для заливов и " +
        "автоправил с действиями — он даёт право тратить бюджет."
    },
    session: {
      needs: [
        "доступ к своему аккаунту Facebook с правами в Business Manager",
        "cookies из браузера, в котором вы вошли в Ads Manager",
        "прокси этого аккаунта — обязателен, иначе Meta заблокирует сессию"
      ],
      steps: [
        {
          title: "Достаньте cookies своей сессии Facebook",
          text: "Войдите в Ads Manager в своём браузере и скопируйте cookies facebook.com " +
            "(через DevTools или расширение). CRM откроет браузер с этими cookies и вашим " +
            "прокси — для Meta это выглядит как обычный заход с вашего устройства."
        },
        {
          title: "Вставьте cookies и укажите прокси",
          text: "На следующем шаге укажите прокси, с которого обычно работаете, и нажмите " +
            "«Запустить браузер». Прокси проверяется до запуска: если он не работает, " +
            "браузер не откроется, и аккаунт не пострадает."
        },
        {
          title: "Подтвердите вход, если нужно",
          text: "Если cookies валидны, вход произойдёт сам. При капче или чекпойнте " +
            "войдите вручную через VNC (порт 5900) — CRM сама увидит вход, сохранит " +
            "сессию и извлечёт EAAB-токен."
        },
        {
          title: "Дальше всё автоматически",
          text: "Все запросы CRM пойдут через тот же прокси. Когда токен умрёт, " +
            "синхронизация восстановит сохранённую сессию и получит новый EAAB сама."
        }
      ],
      note: "Токен сессии живёт, пока жива сессия аккаунта: смена пароля, выход из " +
        "устройств или запрос подтверждения личности его обнуляют. С сохранённой " +
        "браузерной сессией токен обновляется автоматически, но при бане аккаунта " +
        "придётся подключать заново."
    },
    app_token: {
      needs: [
        "аккаунт разработчика Facebook",
        "приложение, созданное на developers.facebook.com"
      ],
      steps: [
        {
          title: "Откройте панель приложения",
          text: "developers.facebook.com → ваше приложение → Инструменты → Marketing API.",
          link: { url: "https://developers.facebook.com/apps", label: "Открыть Facebook Apps" }
        },
        {
          title: "Отметьте разрешения и получите маркер",
          text: "Отметьте все нужные права и нажмите «Получить маркер». Предыдущий " +
            "маркер при этом отзывается.",
          scopes: ["ads_read", "ads_management", "business_management", "pages_show_list"]
        },
        {
          title: "Имейте в виду срок",
          text: "Такой токен живёт часы. Он годится, чтобы проверить доступ к кабинетам, " +
            "но для постоянной синхронизации нужен токен системного пользователя."
        }
      ],
      note: "Если приложение в статусе development_access, реальные кабинеты через него " +
        "не видны — доступен только кабинет-песочница."
    }
  };

  function renderWizardGuide(method) {
    var guide = AUTH_GUIDES[method] || AUTH_GUIDES.system_user;
    byId("metaWizGuide").innerHTML =
      '<div class="meta-note meta-note--info" style="margin-bottom:16px">' +
      '<b style="display:block;margin-bottom:6px">Что понадобится</b>' +
      guide.needs.map(function (item) { return "· " + escapeHtml(item); }).join("<br>") +
      "</div>" +
      '<ol style="display:grid;gap:14px;padding-left:0;list-style:none">' +
      guide.steps.map(function (step, index) {
        return '<li style="display:flex;gap:12px">' +
          '<span style="flex-shrink:0;width:24px;height:24px;border-radius:50%;' +
          "background:#F0EBEB;color:#6A6161;font-size:11.5px;font-weight:700;display:flex;" +
          'align-items:center;justify-content:center">' + (index + 1) + "</span><div>" +
          '<div style="font-size:13px;font-weight:700">' + escapeHtml(step.title) + "</div>" +
          '<div style="font-size:11.5px;color:#6A6161;font-weight:500;margin-top:4px;' +
          'line-height:1.6">' + escapeHtml(step.text) + "</div>" +
          (step.link
            ? '<a href="' + escapeHtml(step.link.url) + '" target="_blank" ' +
              'rel="noopener noreferrer" style="display:inline-block;font-size:11.5px;' +
              'font-weight:700;margin-top:6px">' + escapeHtml(step.link.label) + " ↗</a>"
            : "") +
          (step.scopes
            ? '<div style="display:flex;gap:6px;margin-top:8px;flex-wrap:wrap">' +
              step.scopes.map(function (scope) {
                return '<span style="font-size:11px;font-weight:700;background:#F0EBEB;' +
                  'border-radius:7px;padding:4px 9px">' + escapeHtml(scope) + "</span>";
              }).join("") + "</div>"
            : "") + "</div></li>";
      }).join("") + "</ol>" +
      '<div style="font-size:11.5px;color:#9B9292;font-weight:500;margin-top:12px;' +
      'line-height:1.6">' + escapeHtml(guide.note) + "</div>";
  }

  function renderWizardMethods(selected) {
    byId("metaWizMethods").innerHTML = AUTH_METHODS.map(function (method) {
      return '<label class="meta-choice"><input type="radio" name="metaWizMethod" ' +
        'value="' + method.code + '"' + (method.code === selected ? " checked" : "") +
        '><span><span class="meta-choice__title">' + escapeHtml(method.label) + "</span>" +
        '<span class="meta-choice__hint">' + escapeHtml(method.hint) + "</span></span></label>";
    }).join("");
    renderWizardGuide(selected);
    renderSessionAuthUI("wizard");
  }

  function wizardMethodValue() {
    var picked = byId("metaWizMethods").querySelector("input:checked");
    return picked ? picked.value : "system_user";
  }

  function openWizard() {
    sessionStop();
    state.wizard = { step: 1, accounts: [], picked: [], payload: null, sessionToken: null };
    renderWizardMethods("system_user");
    byId("metaWizName").value = "";
    byId("metaWizToken").value = "";
    byId("metaWizCookies").value = "";
    byId("metaWizBusiness").value = "";
    byId("metaWizProxy").value = "";
    byId("metaWizUserAgent").value = "";
    byId("metaWizSub").value = "";
    byId("metaWizInterval").value = 30;
    byId("metaWizLookback").value = 3;
    sessionResetUi("wizard");
    wizardError("");
    renderWizard();
    byId("metaWizard").style.display = "flex";
  }

  function closeWizard() {
    sessionStop();
    byId("metaWizard").style.display = "none";
    state.wizard = null;
  }

  function wizardError(message) {
    var host = byId("metaWizardError");
    host.textContent = message || "";
    host.style.display = message ? "" : "none";
  }

  function renderWizard() {
    var step = state.wizard.step;
    byId("metaWizardSteps").innerHTML = WIZARD_STEPS.map(function (label, index) {
      var number = index + 1;
      var cls = number === step ? " meta-step--active" : number < step ? " meta-step--done" : "";
      return '<div class="meta-step' + cls + '">' +
        '<span class="meta-step-dot">' + (number < step ? "✓" : number) + "</span>" +
        '<span class="meta-step-label">' + escapeHtml(label) + "</span></div>" +
        (number < WIZARD_STEPS.length ? '<span class="meta-step-line"></span>' : "");
    }).join("");
    Array.prototype.forEach.call(
      document.querySelectorAll(".meta-wizard-panel"),
      function (panel) {
        panel.style.display = Number(panel.getAttribute("data-step")) === step ? "" : "none";
      }
    );
    byId("metaWizBack").style.visibility = step === 1 || step === 5 ? "hidden" : "";
    byId("metaWizNext").textContent = step === 3
      ? "Импорт →"
      : step === 4 ? "Подключить" : step === 5 ? "Готово" : "Далее →";
  }

  async function wizardNext() {
    var wizard = state.wizard;
    if (!wizard) return;
    wizardError("");
    if (wizard.step === 1) {
      wizard.step = 2;
      renderWizard();
      byId("metaWizName").focus();
      return;
    }
    if (wizard.step === 2) {
      var name = byId("metaWizName").value.trim();
      var token = byId("metaWizToken").value.trim();
      var method = wizardMethodValue();
      var proxy = byId("metaWizProxy").value.trim();
      if (!name) return wizardError("Укажите название подключения");
      if (method === "session") {
        // Токен сессии приходит только из браузерной сессии: без прокси браузер
        // не запускается вовсе, и вставка готовой строки здесь не допускается.
        if (!proxy) return wizardError(
          "Токен сессии (EAAB) требует прокси — без него Meta заблокирует сессию и аккаунт"
        );
        if (!wizard.sessionToken) return wizardError(
          "Сначала получите токен через браузер: cookies → прокси → «Запустить браузер»"
        );
        token = wizard.sessionToken;
      } else if (token.length < 20) {
        return wizardError("Вставьте токен");
      }
      wizard.payload = {
        name: name,
        access_token: token,
        auth_method: method,
        proxy_url: proxy || null,
        user_agent: byId("metaWizUserAgent").value.trim() || null,
        business_id: byId("metaWizBusiness").value.trim() || null,
        attribution_sub_id: byId("metaWizSub").value
          ? Number(byId("metaWizSub").value) : null,
        sync_interval_minutes: Number(byId("metaWizInterval").value) || 30,
        lookback_days: Number(byId("metaWizLookback").value) || 3
      };
      await wizardCheck();
      return;
    }
    if (wizard.step === 3) {
      wizard.step = 4;
      renderWizardAccounts();
      renderWizard();
      return;
    }
    if (wizard.step === 4) {
      await wizardConnect();
      return;
    }
    closeWizard();
  }

  async function wizardCheck() {
    var wizard = state.wizard;
    byId("metaWizNext").disabled = true;
    byId("metaWizStatus").textContent = "Спрашиваем Meta…";
    try {
      var result = await api.post("/meta/connections/preview", {
        access_token: wizard.payload.access_token,
        business_id: wizard.payload.business_id,
        // Проверка идёт тем же маршрутом, что и работа: токен за прокси без
        // него кабинетов не покажет.
        proxy_url: wizard.payload.proxy_url,
        user_agent: wizard.payload.user_agent
      });
      wizard.accounts = result.accounts || [];
      wizard.picked = wizard.accounts.filter(function (account) {
        // По умолчанию отмечаем рабочие кабинеты: отключённый вряд ли нужен в CRM.
        return account.account_status === "ACTIVE";
      }).map(function (account) { return account.external_id; });
      byId("metaWizCheck").innerHTML = wizardCheckHtml(wizard.accounts);
      wizard.step = 3;
      renderWizard();
    } catch (error) {
      wizardError(error && error.message ? error.message : "Meta отклонила токен");
    } finally {
      byId("metaWizNext").disabled = false;
      byId("metaWizStatus").textContent = "";
    }
  }

  function wizardCheckHtml(accounts) {
    var active = accounts.filter(function (account) {
      return account.account_status === "ACTIVE";
    }).length;
    return notice("#E4F7F0", "#16B57F", "Токен принят",
      "Через него видно кабинетов: " + accounts.length + ", из них рабочих: " + active + ".") +
      '<div style="display:grid;gap:8px;margin-top:14px;max-height:280px;overflow:auto">' +
      accounts.map(function (account) {
        return '<div style="display:flex;align-items:center;justify-content:space-between;' +
          'gap:12px;border:1px solid #EBE6E6;border-radius:12px;padding:11px 14px">' +
          '<div style="min-width:0"><div style="font-size:12.5px;font-weight:700">' +
          escapeHtml(account.name) + "</div>" +
          '<div style="font-size:10.5px;color:#9B9292;margin-top:2px">' +
          escapeHtml(account.external_id) + " · " + escapeHtml(account.currency) +
          (account.timezone_name ? " · " + escapeHtml(account.timezone_name) : "") + "</div></div>" +
          chip(account.account_status) + "</div>";
      }).join("") + "</div>" +
      '<div class="meta-note" style="margin-top:14px">Таймзона кабинета важнее, чем кажется: ' +
      "Meta считает день по ней, а Keitaro — по своей. Если они разные, расход и доход за " +
      "«вчера» не сойдутся.</div>";
  }

  function renderWizardAccounts() {
    var wizard = state.wizard;
    byId("metaWizAccounts").innerHTML = wizard.accounts.map(function (account) {
      return '<label class="meta-pick">' +
        '<input type="checkbox" data-wizard-account="' + escapeHtml(account.external_id) + '"' +
        (wizard.picked.indexOf(account.external_id) >= 0 ? " checked" : "") + ">" +
        '<span style="min-width:0"><span style="display:block;font-size:12.5px;font-weight:700">' +
        escapeHtml(account.name) + "</span>" +
        '<span style="display:block;font-size:10.5px;color:#9B9292;margin-top:2px">' +
        escapeHtml(account.external_id) + " · " + escapeHtml(account.currency) + " · " +
        escapeHtml(account.account_status || "—") + "</span></span></label>";
    }).join("");
  }

  async function wizardConnect() {
    var wizard = state.wizard;
    if (!wizard.picked.length) {
      wizardError("Отметьте хотя бы один кабинет");
      return;
    }
    byId("metaWizNext").disabled = true;
    byId("metaWizStatus").textContent = "Подключаем…";
    try {
      var payload = Object.assign({}, wizard.payload, { import_accounts: wizard.picked });
      var connection = await api.post("/meta/connections", payload);
      // Сохраняем браузерную сессию за подключением: когда EAAB умрёт,
      // синхронизация восстановит её и получит новый токен сама.
      if (wizard.payload.auth_method === "session" && state.session && state.session.sessionId) {
        await api.post("/meta/session/" + state.session.sessionId + "/attach", {
          connection_id: connection.id
        }).catch(function () {});
        sessionStop();
      }
      await api.post("/meta/connections/" + connection.id + "/sync?mode=backfill", {});
      byId("metaWizDone").innerHTML = notice("#E4F7F0", "#16B57F",
        "Подключено: " + wizard.payload.name,
        "Импортировано кабинетов: " + wizard.picked.length +
        ". Запущена загрузка статистики за 90 дней — она идёт в фоне, страницу можно " +
        "закрыть.") +
        (wizard.payload.attribution_sub_id
          ? '<div class="meta-note" style="margin-top:14px">Не забудьте добавить в ссылку ' +
            "трекера <b>sub_id_" + wizard.payload.attribution_sub_id +
            "={{campaign.id}}</b> — без этого параметра дохода и ROI по кампаниям не будет.</div>"
          : '<div class="meta-note meta-note--warn" style="margin-top:14px">sub_id с ID ' +
            "кампании не указан, поэтому доход и ROI считаться не будут. Его можно " +
            "добавить позже в настройках подключения.</div>");
      wizard.step = 5;
      renderWizard();
      state.reference = null;
      await loadConnections();
      await load();
      schedulePoll();
    } catch (error) {
      wizardError(error && error.message ? error.message : "Не удалось подключить");
    } finally {
      byId("metaWizNext").disabled = false;
      byId("metaWizStatus").textContent = "";
    }
  }

  function bind() {
    byId("metaPeriod").addEventListener("change", function (event) {
      state.period = event.target.value;
      load().catch(showFailure);
    });
    byId("metaAccountFilter").addEventListener("change", function (event) {
      state.accountId = event.target.value;
      load().catch(showFailure);
    });
    byId("metaOwnerFilter").addEventListener("change", function (event) {
      state.ownerId = event.target.value;
      load().catch(showFailure);
    });
    byId("metaSync").addEventListener("click", startSync);
    // Мастер — для первого подключения, обычная форма — для правки существующего.
    byId("metaConnect").addEventListener("click", function () {
      if (state.connections.length) openModal(); else openWizard();
    });
    byId("metaModalClose").addEventListener("click", closeModal);
    byId("metaModalSave").addEventListener("click", saveConnection);
    byId("metaModalDelete").addEventListener("click", deleteConnection);
    byId("metaModalCheck").addEventListener("click", function () {
      checkConnection().catch(showFailure);
    });
    byId("metaModal").addEventListener("click", function (event) {
      if (event.target === event.currentTarget) closeModal();
    });
    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape") return;
      if (byId("metaSpendModal").style.display === "flex") return closeSpendModal();
      if (byId("metaHourModal").style.display === "flex") return closeHourModal();
      if (byId("metaFormModal").style.display === "flex") return closeForm();
      if (byId("metaWizard").style.display === "flex") return closeWizard();
      if (byId("metaModal").style.display === "flex") closeModal();
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-level]"), function (button) {
      button.addEventListener("click", function () {
        var next = button.getAttribute("data-level");
        if (next === state.level) return;
        state.level = next;
        // Уходим с кампаний — отметки теряют смысл: относить на оффер можно
        // только кампанию, и на другом уровне этих строк уже нет.
        state.spendPick = {};
        renderLevelTabs();
        loadLevel().catch(showFailure);
      });
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-launch-view]"),
      function (button) {
        button.addEventListener("click", function () {
          setLaunchView(button.getAttribute("data-launch-view"));
        });
      });
    byId("metaUpBack").addEventListener("click", function () {
      if (!state.upload || state.upload.step <= 1) return;
      state.upload.step -= 1;
      renderUpload();
    });
    byId("metaUpNext").addEventListener("click", function () {
      uploadNext().catch(function (error) {
        uploadError(error && error.message ? error.message : "Не получилось");
      });
    });
    byId("metaUpBody").addEventListener("input", onUploadInput);
    byId("metaUpBody").addEventListener("change", onUploadChange);
    byId("metaUpBody").addEventListener("click", onUploadClick);
    byId("metaTableBody").addEventListener("click", function (event) {
      // Селект ответственного живёт в той же строке — клик по нему окно не открывает.
      if (event.target.closest && event.target.closest("select,button,input,a")) return;
      var row = event.target.closest ? event.target.closest("[data-hour-row]") : null;
      if (row) openHourModal(state.level, row.getAttribute("data-hour-row"));
    });
    byId("metaTableBody").addEventListener("change", function (event) {
      var box = event.target.closest ? event.target.closest("[data-spend-pick]") : null;
      if (!box) return;
      toggleSpendPick(box, box.checked);
      syncTableChecks();
      renderSpendButton();
    });
    byId("metaLevelHead").addEventListener("change", function (event) {
      var all = event.target.closest ? event.target.closest("[data-spend-all]") : null;
      if (!all) return;
      Array.prototype.forEach.call(
        document.querySelectorAll("[data-spend-pick]"),
        function (box) { toggleSpendPick(box, all.checked); }
      );
      syncTableChecks();
      renderSpendButton();
    });
    byId("metaTableBody").addEventListener("keydown", function (event) {
      if (event.key !== "Enter" && event.key !== " ") return;
      var row = event.target.closest ? event.target.closest("[data-hour-row]") : null;
      if (!row || event.target !== row) return;
      event.preventDefault();
      openHourModal(state.level, row.getAttribute("data-hour-row"));
    });
    bindSpend();
    byId("metaHourClose").addEventListener("click", closeHourModal);
    byId("metaHourModal").addEventListener("click", function (event) {
      if (event.target === event.currentTarget) closeHourModal();
    });
    byId("metaHourRefresh").addEventListener("click", function () {
      byId("metaHourSubtitle").textContent = "Обновляем…";
      loadHours(true).catch(function (error) {
        byId("metaHourNote").className = "meta-note meta-note--warn";
        byId("metaHourNote").textContent = error && error.message
          ? error.message : "Не удалось обновить";
      });
    });
    byId("metaQueueStatus").addEventListener("change", function () {
      loadQueue().catch(showFailure);
    });
    byId("metaQueueRefresh").addEventListener("click", function () {
      loadQueue().catch(showFailure);
    });
    byId("metaQueueBody").addEventListener("click", function (event) {
      var retry = event.target.closest ? event.target.closest("[data-queue-retry]") : null;
      if (retry) retryQueue(retry.getAttribute("data-queue-retry"));
    });
    var levelSearch = byId("metaLevelSearch");
    var searchTimer = null;
    levelSearch.addEventListener("input", function (event) {
      state.levelSearch = event.target.value.trim();
      if (searchTimer) window.clearTimeout(searchTimer);
      // Поиск уходит на сервер: ждём, пока человек допечатает.
      searchTimer = window.setTimeout(function () {
        loadLevel().catch(showFailure);
      }, 250);
    });
    document.addEventListener("change", function (event) {
      var select = event.target.closest ? event.target.closest("[data-meta-owner]") : null;
      if (select) assignOwner(select.getAttribute("data-meta-owner"), select.value);
    });

    Array.prototype.forEach.call(document.querySelectorAll(".meta-tab"), function (button) {
      button.addEventListener("click", function () {
        setTab(button.getAttribute("data-tab"));
      });
    });

    byId("metaWizardClose").addEventListener("click", closeWizard);
    byId("metaWizMethods").addEventListener("change", function (event) {
      if (event.target && event.target.name === "metaWizMethod") {
        renderWizardGuide(event.target.value);
        renderSessionAuthUI("wizard");
      }
    });
    byId("metaAuthMethods").addEventListener("change", function (event) {
      if (event.target && event.target.name === "metaAuthMethod") {
        renderSessionAuthUI("modal");
      }
    });
    byId("metaWizProxyCheck").addEventListener("click", function () {
      wizardCheckProxy().catch(showFailure);
    });
    byId("metaWizSessionStart").addEventListener("click", function () {
      sessionStart("wizard").catch(showFailure);
    });
    byId("metaWizSessionClose").addEventListener("click", function () {
      sessionStop();
      sessionResetUi("wizard");
    });
    byId("metaModalSessionStart").addEventListener("click", function () {
      sessionStart("modal").catch(showFailure);
    });
    byId("metaModalSessionClose").addEventListener("click", function () {
      sessionStop();
      sessionResetUi("modal");
    });
    byId("metaWizNext").addEventListener("click", function () {
      wizardNext().catch(function (error) {
        wizardError(error && error.message ? error.message : "Не получилось");
      });
    });
    byId("metaWizBack").addEventListener("click", function () {
      if (!state.wizard || state.wizard.step <= 1) return;
      state.wizard.step -= 1;
      wizardError("");
      renderWizard();
    });
    byId("metaWizPickAll").addEventListener("click", function () {
      var wizard = state.wizard;
      var all = wizard.picked.length === wizard.accounts.length;
      wizard.picked = all ? [] : wizard.accounts.map(function (a) { return a.external_id; });
      renderWizardAccounts();
    });
    byId("metaWizAccounts").addEventListener("change", function (event) {
      var input = event.target.closest ? event.target.closest("[data-wizard-account]") : null;
      if (!input) return;
      var id = input.getAttribute("data-wizard-account");
      var index = state.wizard.picked.indexOf(id);
      if (input.checked && index < 0) state.wizard.picked.push(id);
      if (!input.checked && index >= 0) state.wizard.picked.splice(index, 1);
    });

    byId("metaFormClose").addEventListener("click", closeForm);
    byId("metaFormModal").addEventListener("click", function (event) {
      if (event.target === event.currentTarget) closeForm();
    });
    byId("metaFormSave").addEventListener("click", function () {
      submitForm().catch(function () {});
    });
    // Сменили кабинет в форме залива — список креативов должен перестроиться:
    // хэш картинки принадлежит кабинету, чужой креатив Meta не примет.
    byId("metaFormBody").addEventListener("click", function (event) {
      var add = event.target.closest ? event.target.closest("[data-condition-add]") : null;
      if (add) {
        var host = add.parentElement.querySelector("[data-conditions]");
        if (host) host.insertAdjacentHTML("beforeend", conditionRowHtml(null));
        return;
      }
      var remove = event.target.closest ? event.target.closest("[data-condition-remove]") : null;
      if (remove) remove.closest("[data-condition-row]").remove();
    });
    byId("metaFormBody").addEventListener("change", function (event) {
      onLaunchAccountChange(event).catch(function () {});
    });
    byId("metaFormDelete").addEventListener("click", function () {
      if (state.form && state.form.onDelete) {
        state.form.onDelete().catch(function (error) {
          formError(error && error.message ? error.message : "Не удалось удалить");
        });
      }
    });

    byId("metaLaunchCreate").addEventListener("click", function () {
      openLaunchForm(null).catch(showFailure);
    });
    byId("metaLaunchStatusFilter").addEventListener("change", function (event) {
      state.launchStatus = event.target.value;
      state.selected = [];
      loadLaunches().catch(showFailure);
    });
    byId("metaLaunchAll").addEventListener("change", function (event) {
      state.selected = event.target.checked
        ? state.launches.map(function (launch) { return launch.id; })
        : [];
      renderLaunches();
    });
    byId("metaLaunchBody").addEventListener("change", function (event) {
      var pick = event.target.closest ? event.target.closest("[data-launch-pick]") : null;
      if (!pick) return;
      var id = pick.getAttribute("data-launch-pick");
      var index = state.selected.indexOf(id);
      if (pick.checked && index < 0) state.selected.push(id);
      if (!pick.checked && index >= 0) state.selected.splice(index, 1);
      renderBulkBar();
    });
    byId("metaLaunchBody").addEventListener("click", function (event) {
      var target = event.target.closest ? event.target : null;
      if (!target) return;
      var open = target.closest("[data-launch-open]");
      if (open) return openLaunchForm(open.getAttribute("data-launch-open")).catch(showFailure);
      var publish = target.closest("[data-launch-publish]");
      if (publish) return publishLaunch(publish.getAttribute("data-launch-publish"));
      var pause = target.closest("[data-launch-pause]");
      if (pause) return launchAction(pause.getAttribute("data-launch-pause"), "pause");
      var resume = target.closest("[data-launch-resume]");
      if (resume) return launchAction(resume.getAttribute("data-launch-resume"), "resume");
    });
    byId("metaBulkBar").addEventListener("click", function (event) {
      var button = event.target.closest ? event.target.closest(".meta-bulk") : null;
      if (button) bulkAction(button.getAttribute("data-action"));
    });

    byId("metaBundleCreate").addEventListener("click", function () {
      openBundleWizard(null);
    });
    byId("metaBundleSearch").addEventListener("input", function (event) {
      state.bundleSearch = event.target.value;
      renderBundles();
    });
    byId("metaBundleAll").addEventListener("change", function (event) {
      // «Выбрать все» отмечает то, что видно сейчас: с поиском это ровно те
      // строки, на которые человек смотрит.
      state.bundlePick = event.target.checked
        ? bundleRows().map(function (bundle) { return bundle.id; })
        : [];
      renderBundles();
    });
    byId("metaBundlesGrid").addEventListener("change", function (event) {
      var box = event.target.closest ? event.target.closest("[data-bundle-pick]") : null;
      if (!box) return;
      var id = box.getAttribute("data-bundle-pick");
      var picked = (state.bundlePick || []).filter(function (value) { return value !== id; });
      if (box.checked) picked.push(id);
      state.bundlePick = picked;
      renderBundles();
    });
    byId("metaBundleBulk").addEventListener("click", function (event) {
      var button = event.target.closest ? event.target.closest("[data-bundle-bulk]") : null;
      if (button) bundleBulk(button.getAttribute("data-bundle-bulk")).catch(showFailure);
    });
    byId("metaBundlesGrid").addEventListener("click", function (event) {
      var closest = function (selector) {
        return event.target.closest ? event.target.closest(selector) : null;
      };
      var open = closest("[data-bundle-open]");
      if (open) return openBundleWizard(open.getAttribute("data-bundle-open"));
      var pour = closest("[data-bundle-pour]");
      if (pour) {
        // «Залить» открывает мастер уже с выбранной связкой: это то же самое,
        // что зайти в него и выбрать её из списка, только без лишнего шага.
        state.upload = null;
        setLaunchView("wizard");
        state.upload.values.template_id = pour.getAttribute("data-bundle-pour");
        applyBundleToUpload(uploadBundle());
        renderUpload();
      }
    });
    byId("metaBundleClose").addEventListener("click", closeBundleWizard);
    byId("metaBundleCancel").addEventListener("click", closeBundleWizard);
    byId("metaBundleCancel2").addEventListener("click", closeBundleWizard);
    byId("metaBundleBack").addEventListener("click", function () {
      if (state.bundle && state.bundle.step > 1) {
        state.bundle.step -= 1;
        renderBundleWizard();
      }
    });
    byId("metaBundleNext").addEventListener("click", function () {
      bundleNext().catch(function (error) {
        bundleError(error && error.message ? error.message : "Не получилось");
      });
    });
    byId("metaBundleBody").addEventListener("input", onBundleInput);
    byId("metaBundleBody").addEventListener("change", onBundleChange);
    byId("metaBundleBody").addEventListener("click", onBundleClick);
    byId("metaBundleBody").addEventListener("keydown", onBundleKeydown);
    byId("metaMacroClose").addEventListener("click", closeMacroModal);
    byId("metaMacroCancel").addEventListener("click", closeMacroModal);
    byId("metaMacroSave").addEventListener("click", saveMacroModal);
    byId("metaMacroModal").addEventListener("click", function (event) {
      if (event.target === event.currentTarget) closeMacroModal();
    });
    byId("metaMacroInput").addEventListener("input", renderMacroList);
    byId("metaMacroList").addEventListener("click", function (event) {
      var button = event.target.closest ? event.target.closest("[data-macro-code]") : null;
      if (!button) return;
      var input = byId("metaMacroInput");
      // Дописываем в конец: макрос почти всегда добавляют к тому, что уже
      // набрано, а не заменяют им строку.
      input.value = input.value + button.getAttribute("data-macro-code");
      input.focus();
      renderMacroList();
    });

    byId("metaRuleCreate").addEventListener("click", function () {
      openRuleForm(null);
    });
    byId("metaRulesRun").addEventListener("click", runRules);
    byId("metaEventsAck").addEventListener("click", ackEvents);
    byId("metaRulesBody").addEventListener("click", function (event) {
      var target = event.target.closest ? event.target : null;
      if (!target) return;
      var preview = target.closest("[data-rule-preview]");
      if (preview) return previewRule(preview.getAttribute("data-rule-preview"));
      var open = target.closest("[data-rule-open]");
      if (open) openRuleForm(open.getAttribute("data-rule-open"));
    });
  }

  function showFailure(error) {
    var message = error && error.message ? error.message : "Не удалось загрузить данные";
    if (window.CelestialShell && window.CelestialShell.showLoadFailure) {
      window.CelestialShell.showLoadFailure(message);
      return;
    }
    notify({ title: "Не удалось загрузить данные", message: message });
  }

  async function init(user) {
    if (!byId("metaTableBody")) return;
    state.user = user;
    state.canManage = hasPermission(user, "meta.manage");
    state.canLaunch = hasPermission(user, "meta.launch");
    // Фиксацию делает баер со своего аккаунта, а `meta.launch` — про заливы и
    // деньги в кабинете, и у баера его нет. Расход он и так заводит руками в
    // Медиаборде: это то же самое право, только считает сумму Meta.
    state.canFixSpend = state.canLaunch || hasPermission(user, "media.manage");
    byId("metaConnect").style.display = state.canManage ? "" : "none";
    bind();
    if (state.canManage || state.canLaunch || state.canFixSpend) {
      try {
        state.buyers = await api.get("/users/options");
      } catch (error) {
        // Назначать ответственных не выйдет, но сама статистика важнее — не роняем экран.
        state.buyers = [];
      }
    }
    await loadConnections();
    await load();
    schedulePoll();
  }

  window.CelestialMeta = { init: init, reload: load };
})();
