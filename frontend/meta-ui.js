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
    connectionId: "",
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
    // Каскад структуры: владельцы подключений сужают Meta-аккаунты, а выбранные
    // Meta-аккаунты — все уровни под ними.
    levelFilterPick: {},
    // Фиксация расхода за отрезок времени: что отмечено в таблице кампаний и
    // что насчитала Meta по этим кампаниям за выбранное окно.
    spendPick: {},
    spend: { rows: [], commits: [], loading: false },
    spendRefs: null,
    busy: false,
    pollTimer: null,
    // Опрос статусов заливов: пока есть «публикуется»/«в очереди», таблица и
    // журнал публикаций обновляются сами — без перезагрузки страницы.
    launchPollTimer: null,
    launchPollBusy: false,
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

  // Локальные object URL живут столько же, сколько открытый мастер. Один и тот
  // же File может попасть в несколько объявлений, поэтому освобождаем ссылки
  // только при закрытии всего мастера, а не при удалении одной карточки.
  var uploadPreviewUrls = new Map();

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
    var spend = byId("metaSpend");
    spend.textContent = money(totals.spend);
    var currencies = payload.currencies || [];
    // Складывать евровый кабинет с долларовым нельзя молча. Подписи под числом
    // больше нет, поэтому оговорка живёт подсказкой на самой сумме: экран
    // остаётся чистым, а смешанный итог не выдаёт себя за одну валюту.
    spend.title = currencies.length > 1
      ? "Внимание: кабинеты в разных валютах (" + currencies.join(", ") + ")"
      : "Расход за период" + (currencies.length ? " · " + currencies[0] : "");
    byId("metaClicks").textContent = num(totals.clicks);
    byId("metaLeads").textContent = num(totals.leads);
    var profit = byId("metaProfit");
    profit.textContent = money(totals.profit);
    profit.style.color = toneForNumber(totals.profit);
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

  /* Уровни обзора сверху вниз. Порядок здесь — не украшение: отметки на любом
     уровне сужают всё, что ниже него, и ничего выше. Пользователь → его
     аккаунты → БМы этих аккаунтов → фан-пейджи этих БМов → кабинеты, в которых
     крутилась реклама с этих страниц, → кампании и дальше. */
  var LEVEL_CHAIN = ["users", "socials", "businesses", "fanpages", "accounts",
    "campaigns", "adsets", "ads"];

  /* Уровни, строки которых отмечают галочкой, и параметр, которым отметка
     уезжает на сервер. Кампании и ниже в фильтр не входят: там галочка уже
     занята фиксацией расхода. */
  var FILTER_LEVELS = ["users", "socials", "businesses", "fanpages", "accounts"];
  var FILTER_PARAM = {
    users: "connection_owner_id",
    socials: "social_id",
    businesses: "business_id",
    fanpages: "page_id",
    accounts: "ad_account_id"
  };
  var FILTER_TITLES = {
    users: "пользователей",
    socials: "аккаунтов",
    businesses: "БМов",
    fanpages: "фан-пейджей",
    accounts: "кабинетов"
  };

  function levelBelow(level, source) {
    return LEVEL_CHAIN.indexOf(level) > LEVEL_CHAIN.indexOf(source);
  }

  var LEVEL_HEADS = {
    users: [
      { key: "name", label: "Пользователь", wide: true }
    ],
    socials: [
      { key: "name", label: "Аккаунт", wide: true },
      { key: "connection", label: "Подключение", text: true },
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
      // Под именем показываем ID объекта в Meta: по нему ищут и его же копируют,
      // когда из комментариев или чужой ссылки известно только его число.
      return '<div style="font-weight:700;font-size:13px">' + escapeHtml(value) + "</div>" +
        (row.external_id
          ? '<div style="font-size:10.5px;color:#9B9292;font-weight:600;margin-top:3px;' +
            'font-variant-numeric:tabular-nums">' + escapeHtml(row.external_id) + "</div>"
          : "");
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

  function hasFilterPickColumn(level) {
    return FILTER_LEVELS.indexOf(level) >= 0;
  }

  function filterPicks(level) {
    if (!state.levelFilterPick[level]) state.levelFilterPick[level] = {};
    return state.levelFilterPick[level];
  }

  function renderLevelTable(payload) {
    var columns = levelColumns(payload);
    var rows = payload.rows || [];
    var spendPick = hasPickColumn(payload.level);
    var filterPick = hasFilterPickColumn(payload.level);
    var pick = spendPick || filterPick;
    // Первая колонка отступает от края таблицы. С чекбоксами первый — он.
    var lead = function (index) { return index === 0 && !pick ? "padding-left:24px;" : ""; };
    var pickedRows = filterPick ? filterPicks(payload.level) : state.spendPick;
    var allPicked = pick && rows.length && rows.every(function (row) {
      return !!pickedRows[row.id];
    });
    byId("metaLevelHead").innerHTML =
      '<tr style="border-top:1px solid #F0EBEB;border-bottom:1px solid #F0EBEB">' +
      (spendPick
        ? '<th class="meta-th meta-check" style="padding-left:24px">' +
          '<input type="checkbox" data-spend-all aria-label="Выбрать все кампании"' +
          (allPicked ? " checked" : "") + "></th>"
        : filterPick
          ? '<th class="meta-th meta-check" style="padding-left:24px">' +
            '<input type="checkbox" data-level-filter-all="' + payload.level + '" ' +
            'aria-label="Выбрать все строки"' + (allPicked ? " checked" : "") + "></th>"
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
    // Подключения живут здесь же: строка аккаунта открывает сохранённые
    // настройки того подключения, через которое этот аккаунт виден.
    var opensConnection = payload.level === "socials";
    body.innerHTML = rows.map(function (row) {
      var link = opensConnection && row.connection_id;
      return '<tr class="meta-row' + (clickable || link ? " meta-row--clickable" : "") + '"' +
        (link ? ' data-connection-row="' + escapeHtml(row.connection_id) + '" tabindex="0" ' +
          'title="Настройки подключения"' : "") +
        (clickable ? ' data-hour-row="' + escapeHtml(row.id) + '" tabindex="0" ' +
          'title="Расход по часам"' : "") +
        ' style="border-bottom:1px solid #F7F4F4">' +
        (spendPick
          ? '<td class="meta-cell meta-check" style="padding-left:24px">' +
            '<input type="checkbox" data-spend-pick="' + escapeHtml(row.id) + '"' +
            ' data-spend-name="' + escapeHtml(row.name) + '"' +
            (state.spendPick[row.id] ? " checked" : "") +
            ' aria-label="Выбрать кампанию"></td>'
          : filterPick
            ? '<td class="meta-cell meta-check" style="padding-left:24px">' +
              '<input type="checkbox" data-level-filter-pick="' +
              escapeHtml(row.id) + '" data-level-filter-kind="' + payload.level + '"' +
              ' data-level-filter-name="' + escapeHtml(row.name) + '"' +
              (filterPicks(payload.level)[row.id] ? " checked" : "") +
              ' aria-label="Добавить в фильтр"></td>'
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
      return "Нет пользователей с подключением Meta Ads";
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
        "которая крутится от его лица. Отметьте страницы — ниже останутся " +
        "кабинеты, в которых такая реклама была.";
    } else if (level === "users") {
      text = "Здесь пользователи, которые создали подключение Meta Ads. " +
        "Отметьте нужных — следующие уровни покажут только их данные.";
    } else if (level === "socials") {
      text = "Отметьте аккаунты — БМы, фан-пейджи, кабинеты и реклама ниже " +
        "останутся только для выбранных аккаунтов.";
    } else if (level === "businesses") {
      text = "Отметьте БМы — ниже останутся только их фан-пейджи и кабинеты.";
    } else if (level === "accounts") {
      text = "Отметьте кабинеты — кампании, адсеты и объявления ниже " +
        "останутся только по ним.";
    }
    FILTER_LEVELS.forEach(function (source) {
      if (!levelBelow(level, source)) return;
      var count = Object.keys(filterPicks(source)).length;
      if (!count) return;
      text += (text ? " " : "") + "Выбрано " + FILTER_TITLES[source] + ": " + count + ".";
    });
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
    FILTER_LEVELS.forEach(function (source) {
      if (!levelBelow(state.level, source)) return;
      Object.keys(filterPicks(source)).forEach(function (id) {
        query += "&" + FILTER_PARAM[source] + "=" + encodeURIComponent(id);
      });
    });
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


  /* ---------- фиксация расхода за отрезок времени (ТЗ 2.4.4) ---------- */

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

  /* Начало, конец и часы задаются в фильтрах, рядом с таблицей: окно может
     переходить через полночь — от часа одного дня до часа другого. Модалка эти
     значения только показывает, а расход относят по часам кабинета. */
  function defaultWindowDay() {
    return periodRange(state.period).to;
  }

  function spendRange() {
    var fromInput = byId("metaWindowDate");
    var toInput = byId("metaWindowDateTo");
    var day = fromInput && fromInput.value ? fromInput.value : defaultWindowDay();
    var lastDay = toInput && toInput.value ? toInput.value : day;
    if (lastDay < day) lastDay = day;
    var from = Number(byId("metaWindowFrom").value);
    var to = Number(byId("metaWindowTo").value);
    return { date_from: day, hour_from: from, date_to: lastDay, hour_to: to };
  }

  /* Точка во времени для сравнения окон: час 24 сам перекатывается в полночь
     следующего дня, поэтому «до 24:00» и «полночь следующего дня» совпадают. */
  function windowStamp(day, hour) {
    var parts = String(day).split("-");
    return new Date(
      Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]),
      Number(hour) || 0
    ).getTime();
  }

  function windowOpens(range_) {
    return windowStamp(range_.date_from, range_.hour_from) <
      windowStamp(range_.date_to, range_.hour_to);
  }

  function renderWindowBox() {
    var box = byId("metaWindowBox");
    if (!box) return;
    box.hidden = !hasPickColumn(state.level);
    if (box.hidden) return;
    if (!byId("metaWindowDate").value) {
      byId("metaWindowDate").value = defaultWindowDay();
    }
    if (!byId("metaWindowDateTo").value) {
      // Конец по умолчанию совпадает с началом: чаще фиксируют один день,
      // а второй нужен только когда окно действительно длинное.
      byId("metaWindowDateTo").value = byId("metaWindowDate").value;
    }
    if (!byId("metaWindowFrom").options.length) {
      hourOptions(byId("metaWindowFrom"), rangeOf(0, 23), 12);
      hourOptions(byId("metaWindowTo"), rangeOf(1, 24), 16);
    }
  }

  function windowLabel(range_) {
    var value = range_ || spendRange();
    var start = value.date_from + " · " + pad(value.hour_from) + ":00";
    if (value.date_from === value.date_to) {
      return start + "–" + pad(value.hour_to) + ":00";
    }
    return start + " – " + value.date_to + " · " + pad(value.hour_to) + ":00";
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

  function syncLevelFilterChecks(level) {
    var boxes = document.querySelectorAll('[data-level-filter-kind="' + level + '"]');
    Array.prototype.forEach.call(boxes, function (box) {
      box.checked = !!filterPicks(level)[box.getAttribute("data-level-filter-pick")];
    });
    var all = document.querySelector('[data-level-filter-all="' + level + '"]');
    if (all) {
      all.checked = boxes.length > 0 && Array.prototype.every.call(boxes, function (box) {
        return box.checked;
      });
    }
  }

  function toggleLevelFilterPick(box, on) {
    var level = box.getAttribute("data-level-filter-kind");
    var id = box.getAttribute("data-level-filter-pick");
    if (!level || !id) return;
    if (on) {
      filterPicks(level)[id] = box.getAttribute("data-level-filter-name") || id;
    } else {
      delete filterPicks(level)[id];
    }
    // Состав нижних уровней зависит от верхних: после смены отметки выше
    // прежние отметки ниже указывают на строки, которых в списке уже нет.
    FILTER_LEVELS.forEach(function (source) {
      if (levelBelow(source, level)) state.levelFilterPick[source] = {};
    });
  }

  function resetLevelFilters() {
    state.levelFilterPick = {};
  }

  function openSpendModal() {
    if (!selectedIds().length) return;
    var range_ = spendRange();
    if (!windowOpens(range_)) {
      return notify({
        title: "Проверьте окно",
        message: "Конец окна должен быть позже начала."
      });
    }
    byId("metaSpendWindowValue").textContent = windowLabel(range_);
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
      // Справочник для фиксирования: сервер сам сужает офферы по роли
      // (баер — свои, тимлид — своих баеров, СМО и админ — все) и убирает
      // служебную группу OFFERS — это витрина модуля «Оффера», к расходу
      // она отношения не имеет. Постранично, потому что справочник трекера
      // длиннее любого разумного лимита.
      var loaded = await Promise.all([
        api.getAll("/offers?for_spend=true"),
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
      '<label class="spend-field"><span>Баер</span>' +
      '<select class="meta-control meta-select" data-spend-field="buyer_id"' +
      (people.length > 1 ? "" : " disabled") + ">" +
      people.map(function (buyer) {
        return '<option value="' + escapeHtml(buyer.id) + '"' +
          (state.user && buyer.id === state.user.id ? " selected" : "") + ">" +
          escapeHtml(buyer.name) + "</option>";
      }).join("") + "</select></label>" +
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
    var range_ = spendRange();
    if (!windowOpens(range_)) {
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
      var query = "?from=" + encodeURIComponent(range_.date_from) +
        "&to=" + encodeURIComponent(range_.date_to) +
        "&hour_from=" + range_.hour_from + "&hour_to=" + range_.hour_to +
        ids.map(function (id) {
          return "&campaign_ids=" + encodeURIComponent(id);
        }).join("") +
        (refresh ? "&refresh=true" : "");
      var payload = await api.get("/meta/spend/window" + query);
      state.spend.rows = payload.rows || [];
      var commits = await api.get("/meta/spend/commits?from=" +
        encodeURIComponent(range_.date_from) + "&to=" + encodeURIComponent(range_.date_to));
      state.spend.commits = commits.items || [];
      byId("metaSpendWindowHint").textContent = (payload.timezones || []).length
        ? "Часы считает Meta по таймзоне кабинета: " + payload.timezones.join(", ") +
          ". Окно: " + windowLabel(range_) +
          (range_.date_from === range_.date_to
            ? " — часы " + range_.hour_from + "–" + (range_.hour_to - 1) + " включительно."
            : ".")
        : "За этот период у выбранных кампаний расхода не было.";
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
  // отказа при сохранении. Пересечение сравнивается точками во времени —
  // и новое окно, и занятые отрезки могут переходить через полночь.
  function spendClash(row, range_) {
    return (row.taken_windows || []).some(function (taken) {
      var takenStart = windowStamp(taken.date, taken.from);
      var takenEnd = windowStamp(taken.date, taken.to);
      return takenStart < windowStamp(range_.date_to, range_.hour_to) &&
        windowStamp(range_.date_from, range_.hour_from) < takenEnd;
    });
  }

  function renderSpendRows() {
    var host = byId("metaSpendRows");
    var ids = selectedIds();
    if (!ids.length) {
      host.innerHTML = "";
      return;
    }
    var range_ = spendRange();
    var known = {};
    state.spend.rows.forEach(function (row) { known[row.campaign_id] = row; });
    host.innerHTML = '<div class="spend-title">Выбранные кампании</div>' +
      ids.map(function (id) {
        // Пока Meta не ответила, строка уже на месте — с именем из таблицы.
        var row = known[id] ||
          { campaign_id: id, name: state.spendPick[id] || id, spend: null, day_spend: null };
        var clash = spendClash(row, range_);
        return '<div class="spend-row' + (clash ? " spend-row--taken" : "") + '">' +
          '<span style="flex:1;min-width:0"><span class="spend-row__name">' +
          escapeHtml(row.name) + "</span>" +
          '<span class="spend-row__meta">' + escapeHtml(row.account_name || "") +
          (row.status ? " · " + escapeHtml(row.status) : "") +
          (clash ? " · окно уже занято" : "") + "</span></span>" +
          '<span><span class="spend-row__money">' + money(row.spend) +
          '</span><span class="spend-row__day">' +
          (range_.date_from === range_.date_to ? "за день " : "за дни окна ") +
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
    host.innerHTML = '<div class="spend-title">Уже зафиксировано за этот период</div>' +
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
    var range_ = spendRange();
    var total = rows.reduce(function (sum, row) { return sum + Number(row.spend || 0); }, 0);
    var taken = rows.filter(function (row) { return spendClash(row, range_); });
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
    var range_ = spendRange();
    var button = byId("metaSpendCommit");
    button.disabled = true;
    try {
      var result = await api.post("/meta/spend/commit", {
        date_from: range_.date_from,
        hour_from: range_.hour_from,
        date_to: range_.date_to,
        hour_to: range_.hour_to,
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
    // Окно живёт в фильтрах. Если его поменяли при открытой форме,
    // пересчитываем: иначе на экране остаются числа прежнего окна, а кнопка
    // отправляет уже новое.
    ["metaWindowDate", "metaWindowFrom", "metaWindowDateTo", "metaWindowTo"].forEach(
      function (id) {
        // Safari после закрытия списка «вписывает» поле в видимую область и
        // докручивает страницу — как у нативных селектов в мастере залива.
        // Запоминаем позицию на клик и возвращаем её после выбора.
        byId(id).addEventListener("mousedown", function () {
          byId(id)._windowScrollY = window.scrollY;
        });
        byId(id).addEventListener("change", function () {
          var field = byId(id);
          var saved = field._windowScrollY;
          field._windowScrollY = undefined;
          var restore = function () {
            if (saved !== undefined && window.scrollY !== saved) {
              window.scrollTo(0, saved);
            }
          };
          restore();
          window.setTimeout(restore, 50);
          // Конец не бывает раньше начала: короткое окно смысла не имеет,
          // и сервер такое отклонил бы, поэтому поправляем сразу.
          var fromInput = byId("metaWindowDate");
          var toInput = byId("metaWindowDateTo");
          if (toInput.value && toInput.value < fromInput.value) {
            toInput.value = fromInput.value;
          }
          renderSpendButton();
          if (byId("metaSpendModal").style.display !== "flex") return;
          byId("metaSpendWindowValue").textContent = windowLabel();
          loadSpendWindow().catch(function () {});
        });
      }
    );
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
          return accountOptionHtml(account, "");
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
    // Синхронизацию своего подключения может запустить любой пользователь
    // Meta Ads; тимлид также может обновить видимые подключения команды.
    button.style.display = state.connections.length ? "" : "none";
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
      if (!state.connections.some(function (connection) {
        return connection.id === state.connectionId;
      })) {
        var own = state.connections.find(function (connection) {
          return connection.owner_id === (state.user && state.user.id);
        });
        state.connectionId = (own || state.connections[0] || {}).id || "";
      }
    } catch (error) {
      // Право meta.view есть, а подключений может не быть вовсе — это не ошибка экрана.
      if (!error || error.status !== 403) throw error;
      state.connections = [];
      state.connectionId = "";
    }
  }

  function currentConnection() {
    return state.connections.find(function (connection) {
      return connection.id === state.connectionId;
    }) || state.connections[0] || null;
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

  function openModal(connectionId) {
    // Список подключений переехал в уровень «Аккаунты»: сюда приходят уже за
    // конкретным подключением, а не выбирать его из выпадающего списка.
    if (connectionId) state.connectionId = connectionId;
    var connection = currentConnection();
    if (!connection) return openWizard();
    var editing = !!connection;
    sessionStop();
    byId("metaModalTitle").textContent = connection.name || "Подключение Meta Ads";
    state.connectionId = connection.id;
    byId("metaFieldName").value = editing ? connection.name : "";
    byId("metaFieldToken").value = "";
    byId("metaFieldProxy").value = editing ? (connection.proxy_url || "") : "";
    byId("metaFieldUserAgent").value = editing ? (connection.user_agent || "") : "";
    byId("metaModalCookies").value = "";
    byId("metaModalSessionToken").value = "";
    sessionResetUi("modal");
    renderAuthMethods(editing ? (connection.auth_method || "system_user") : "system_user");
    byId("metaTokenHint").style.display = editing ? "" : "none";
    var editable = connection.can_edit !== false;
    byId("metaConnectionOwner").textContent = connection.owner_name
      ? "Владелец: " + connection.owner_name : "Старое подключение без владельца";
    byId("metaConnectionReadonly").style.display = editable ? "none" : "";
    byId("metaModalSave").style.display = editable ? "" : "none";
    byId("metaModalDelete").style.display = editing && editable ? "" : "none";
    byId("metaModalCheck").style.display = editing ? "" : "none";
    Array.prototype.forEach.call(
      byId("metaModal").querySelectorAll("input, textarea, select"),
      function (field) { field.disabled = !editable; }
    );
    ["metaModalSessionStart", "metaModalSessionClose"].forEach(function (id) {
      byId(id).disabled = !editable;
    });
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
    var connection = currentConnection();
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
    var connection = currentConnection();
    if (connection && connection.can_edit === false) return;
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
    // Business ID, sub_id, интервал и глубина перечитывания из карточки убраны,
    // и в payload их нет намеренно: PATCH меняет только присланные поля, так
    // что у существующих подключений эти значения остаются прежними.
    var payload = {
      name: name,
      auth_method: method,
      proxy_url: proxy || null,
      user_agent: byId("metaFieldUserAgent").value.trim() || null
    };
    if (token) payload.access_token = token;
    if (method === "session" && state.session && state.session.sessionId) {
      // Проверка нового токена сессии на сервере идёт через живую сессию браузера.
      payload.session_id = state.session.sessionId;
    }

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
    var connection = currentConnection();
    if (!connection) return;
    if (connection.can_edit === false) return;
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

  var TABS = ["overview", "launches", "rules", "comments"];
  var TAB_KEY = "celestial.meta.tab";

  /* Открытая вкладка переживает перезагрузку. Раньше F5 на «Автоправилах»
     возвращал на «Обзор», и блок правил выглядел так, будто не загрузился. */
  function rememberTab(name) {
    try {
      window.sessionStorage.setItem(TAB_KEY, name);
    } catch (error) {
      // Приватный режим Safari запрещает запись — вкладка просто не запомнится.
    }
    if (window.history && window.history.replaceState) {
      window.history.replaceState(null, "", name === "overview"
        ? window.location.pathname + window.location.search
        : "#" + name);
    }
  }

  function restoreTab() {
    var hash = String(window.location.hash || "").replace("#", "");
    if (TABS.indexOf(hash) >= 0) return setTab(hash);
    var saved = null;
    try {
      saved = window.sessionStorage.getItem(TAB_KEY);
    } catch (error) {
      saved = null;
    }
    setTab(TABS.indexOf(saved) >= 0 ? saved : "overview");
  }

  function setTab(name) {
    state.tab = name;
    rememberTab(name);
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
      rules: loadRules,
      comments: loadComments
    }[name];
    if (loader) loader().catch(showFailure);
  }

  async function loadReference() {
    if (state.reference) return state.reference;
    state.reference = await api.get("/meta/reference");
    return state.reference;
  }

  /* Кабинет в выпадающем списке: имя подписью, ID — технической строкой ниже.
     Поиск в списке (select-ui) смотрит и в неё, поэтому кабинет находится по
     `act_123`: в комментариях и в чужих ссылках имени часто нет, а ID есть. */
  function accountOptionHtml(account, selected) {
    /* Если имени нет, синк кладёт в name числовой ID — и в списке вариант
       выглядел как склейка «1026… act_1026…». Такой кабинет показываем один
       раз как act_…, без дублирующей подписи. */
    var name = String(account.name || "").trim();
    var ext = String(account.external_id || "").trim();
    var digits = ext.replace(/^act_/, "");
    var unnamed = !name || name === digits || name === ext;
    var label = unnamed ? (ext || name || "Кабинет без ID") : name;
    return '<option value="' + escapeHtml(account.id) + '"' +
      (account.id === selected ? " selected" : "") +
      (!unnamed && ext ? ' data-hint="' + escapeHtml(ext) + '"' : "") +
      ">" + escapeHtml(label) + "</option>";
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
            (option.selected ? " selected" : "") +
            (option.hint ? ' data-hint="' + escapeHtml(option.hint) + '"' : "") +
            ">" + escapeHtml(option.label) + "</option>";
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

  /* Подпись креатива в списке: у части файлов имя не сохранилось — не
     оставлять строку пустой, иначе модалка показывает голые чекбоксы. */
  function creativeLabel(creative) {
    var base = creative && (creative.name || creative.file_name || "");
    if (!base) base = "Креатив " + String(creative && creative.id || "").slice(0, 6);
    var kind = creative && creative.kind === "video" ? "видео" : "картинка";
    return base + " · " + kind;
  }

  function checklistHtml(field) {
    if (!(field.options || []).length) {
      return '<div style="font-size:11.5px;color:#9B9292;font-weight:600">' +
        escapeHtml(field.empty || "Нечего выбрать") + "</div>";
    }
    return field.options.map(function (option) {
      var label = option.label || "Креатив " + String(option.value || "").slice(0, 6);
      return '<label class="meta-pick" style="align-items:center;padding:9px 12px">' +
        '<input type="checkbox" data-check="' + escapeHtml(field.name) + '" value="' +
        escapeHtml(option.value) + '"' + (option.selected ? " checked" : "") + ">" +
        '<span style="font-size:12.5px;font-weight:600;color:#3A3030">' +
        escapeHtml(label) + "</span></label>";
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
    if (!state.reference) await loadReference();
    var query = state.launchStatus ? "?status=" + encodeURIComponent(state.launchStatus) : "";
    var page = await api.get("/meta/launches" + query);
    state.launches = page.items || [];
    state.selected = state.selected.filter(function (id) {
      return state.launches.some(function (launch) { return launch.id === id; });
    });
    renderLaunches();
    scheduleLaunchPoll();
  }

  /* Опрос статусов публикации: пока хоть один залив «в очереди»/«публикуется»
     (или в журнале есть pending-операция), перечитываем заливы и журнал каждые
     несколько секунд. Как только всё завершилось — опрос сам останавливается. */
  function scheduleLaunchPoll() {
    if (state.launchPollTimer) window.clearTimeout(state.launchPollTimer);
    state.launchPollTimer = null;
    var inflight =
      (state.launches || []).some(function (launch) {
        return launch.status === "queued" || launch.status === "publishing";
      }) ||
      (state.queue || []).some(function (row) { return row.status === "pending"; });
    if (!inflight) return;
    state.launchPollTimer = window.setTimeout(async function () {
      if (state.launchPollBusy) {
        scheduleLaunchPoll();
        return;
      }
      state.launchPollBusy = true;
      try {
        await loadLaunches();
        if (state.launchView === "queue") await loadQueue();
      } catch (error) {
        // Тихий опрос: одна неудача не должна гасить обновление насовсем.
      } finally {
        state.launchPollBusy = false;
        scheduleLaunchPoll();
      }
    }, POLL_DELAY);
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
          return { value: account.id, label: account.name, hint: account.external_id,
            selected: account.id === accountId };
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
            label: creativeLabel(creative),
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
      // Активный язык объявления на шаге «Креативы» — чипсы-табы.
      textTabs: {},
      // Дополнительные поля шага «Кабинеты»: включаются свитчерами сверху.
      // Бенефициар тянется из подсказок самого кабинета (DSA-прозрачность).
      switches: { naming: true, urlTags: true, displayLink: true, beneficiary: true },
      beneficiaries: {},
      accountSearch: "",
      onlyPicked: false,
      // Объявления пачки: тексты и файлы у них общие, хэши в кабинетах — свои.
      ads: [],
      languages: false,
      // Названия языков, добавленных через поиск Meta: код «hr_HR» без имени
      // в чипсах выглядел бы как мусор.
      languageNames: {},
      // Модалка «Добавить языки»: открытость, поиск, отметки.
      langPicker: null,
      split: false,
      unique: false,
      values: {
        name: "", template_id: "", offer_id: "", partner_id: "", owner_id: "",
        geo: "", daily_budget: "", spend_limit: "", start_date: "", end_date: "",
        link_url: "", primary_text: "", headline: "", description: "",
        call_to_action: "LEARN_MORE", page_id: "", pixel_id: "",
        url_tags: "", display_link: "",
        // Расширенный режим: кампании, цель (4 селекта), бюджет и ставка,
        // автоправила, теги.
        campaign_count: 1,
        objective: "",
        custom_event_type: "",
        attribution: "",
        engaged_view: "",
        budget_level: "",
        budget_kind: "",
        budget_randomize: false,
        adset_budget_limit: "",
        budget_limit_on: false,
        budget_limit_min: "",
        budget_limit_max: "",
        budget_increase_on: false,
        budget_increases: [],
        bid_strategy: "",
        rules_on: true,
        rule_group_on: false,
        rule_ids: [],
        rule_group: "",
        tag_level: "campaign",
        tag_names: "",
        tag_mode: "add",
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

  /* Активация бюджета в extra-режиме не нужна — блок «Параметры залива» убран
     (его в Dolphin нет), путь расширенного остался только для остальных карточек. */

  /* Кампании и адсеты: сколько кампаний и копий адсетов взять в залив. */
  function campaignsCardHtml() {
    var values = state.upload.values;
    return '<div class="meta-card"><div class="meta-card__title">Кампании и адсеты</div>' +
      '<div style="display:grid;gap:14px">' +
      '<div class="meta-line"><span>Количество кампаний</span>' +
      '<input class="meta-control" style="width:100px;padding:0 13px" type="number" min="1" ' +
      'max="20" data-up-field="campaign_count" value="' +
      escapeHtml(String(values.campaign_count || 1)) + '">' +
      '<span class="meta-switch__hint">Каждая со своими адсетами и своим номером в имени.</span></div>' +
      '<div class="meta-line"><span>Количество адсетов</span>' +
      '<input class="meta-control" style="width:100px;padding:0 13px" type="number" min="1" ' +
      'max="20" data-up-field="adset_count" value="' +
      escapeHtml(String(state.upload.values.adset_count || 1)) + '">' +
      '<span class="meta-switch__hint">Копии адсета в каждой кампании — Meta учится на каждой отдельно.</span></div>' +
      "</div></div>";
  }

  /* Цель кампании: 4 селекта поверх связки — цель, событие пикселя,
     окно конверсии и вовлечённые просмотры. */
  function goalCardHtml() {
    var values = state.upload.values;
    var bundle = state.reference || {};
    var reference = bundle.bundle || {};
    var goals = (reference.goals || []).filter(function (goal) {
      return goal.group !== "placement" && goal.group !== "app";
    }).map(function (goal) {
      return { value: goal.code, label: goal.label || goal.code };
    });
    var events = Object.keys(reference.pixel_events || {}).map(function (code) {
      return { value: code, label: reference.pixel_events[code] };
    });
    var windows = (reference.attribution_windows || []).map(function (row) {
      return { value: row.code, label: row.label };
    });
    var goalSelect = '<label class="meta-field"><select class="meta-control meta-select" ' +
      'style="width:100%" data-up-field="objective">' +
      plainOptions(goals, "Оставьте текущую цель или поставьте новую", values.objective) +
      "</select></label>";
    // Событие пикселя и окна показываем всегда: они имеют смысл и для
    // конверсий (пиксель), где цель переопределена, и для остальных целей —
    // где не заданы, работает связка.
    return '<div class="meta-card"><div class="meta-card__title">Цель кампании</div>' +
      '<div style="display:grid;gap:14px">' + goalSelect +
      '<label class="meta-field"><span>Событие пикселя</span>' +
      '<select class="meta-control meta-select" style="width:100%" data-up-field="custom_event_type">' +
      plainOptions(events, "Из связки", values.custom_event_type) + "</select></label>" +
      '<label class="meta-field"><span>Окно конверсии</span>' +
      '<select class="meta-control meta-select" style="width:100%" data-up-field="attribution">' +
      plainOptions(windows, "Из связки", values.attribution) + "</select></label>" +
      '<label class="meta-field"><span>Вовлечённые просмотры (только для видео)</span>' +
      '<select class="meta-control meta-select" style="width:100%" data-up-field="engaged_view">' +
      plainOptions([
        { value: "none", label: "Отсутствует" },
        { value: "1d", label: "1 день" },
        { value: "7d", label: "7 дней" }
      ], "Из связки", values.engaged_view) + "</select></label>" +
      "</div></div>";
  }

  function plainOptions(items, placeholder, selected) {
    return '<option value="">' + escapeHtml(placeholder || "—") + "</option>" +
      items.map(function (row) {
        return '<option value="' + escapeHtml(row.value) + '"' +
          (String(row.value) === String(selected || "") ? " selected" : "") + ">" +
          escapeHtml(row.label) + "</option>";
      }).join("");
  }

  /* Бюджет и ставка: уровень, тип, разброс, лимит адсета (мин/макс),
     запланированное увеличение бюджета, стратегия ставок. */
  function budgetCardHtml() {
    var values = state.upload.values;
    var reference = state.reference || {};
    var strategies = reference.bid_strategies || {};
    return '<div class="meta-card"><div class="meta-card__title">Бюджет и ставка</div>' +
      '<div style="display:grid;gap:14px">' +
      '<div class="meta-line"><span>Уровень бюджета</span>' +
      segHtml("budget_level", [
        { value: "", label: "Из связки" },
        { value: "campaign", label: "Кампания" },
        { value: "adset", label: "Адсет" }
      ]) + "</div>" +
      '<div class="meta-line"><span>Тип бюджета</span>' +
      segHtml("budget_kind", [
        { value: "", label: "Из связки" },
        { value: "daily", label: "Дневной" },
        { value: "lifetime", label: "На весь срок" }
      ]) + "</div>" +
      '<div class="meta-line"><span>Бюджет</span>' +
      '<input class="meta-control" style="width:130px;padding:0 13px" type="number" min="0" ' +
      'step="0.01" data-up-field="daily_budget" value="' +
      escapeHtml(String(values.daily_budget || "")) + '">' +
      '<span style="font-size:12px;color:#9B9292;font-weight:700">' +
      escapeHtml(values.budget_currency || "USD") + "</span>" +
      '<label class="meta-switch"><input type="checkbox" data-up-field="budget_randomize"' +
      (values.budget_randomize ? " checked" : "") +
      '><span class="meta-switch__box"></span><span>Рандомизировать</span></label></div>' +
      budgetLimitHtml() +
      budgetIncreaseHtml() +
      '<div class="meta-line"><span>Стратегия ставок</span>' +
      '<select class="meta-control meta-select" style="width:240px" data-up-field="bid_strategy">' +
      '<option value="">Из связки</option>' +
      Object.keys(strategies).map(function (code) {
        return '<option value="' + escapeHtml(code) + '"' +
          (code === values.bid_strategy ? " selected" : "") + ">" +
          escapeHtml(strategies[code]) + "</option>";
      }).join("") + "</select></div></div></div>";
  }

  /* Лимит адсета: переключатель «Установить лимит адсета» — при включении
     поле минимум и максимум (USD). */
  function budgetLimitHtml() {
    var values = state.upload.values;
    var on = !!values.budget_limit_on;
    return '<div class="meta-line"><span>Лимит расхода адсета</span>' +
      '<label class="meta-switch"><input type="checkbox" data-up-field="budget_limit_on"' +
      (on ? " checked" : "") +
      '><span class="meta-switch__box"></span><span>Установить лимит адсета</span></label></div>' +
      (on
        ? '<div class="meta-line"><span></span>' +
          '<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">' +
          '<label class="meta-field" style="width:150px"><span>Минимум</span>' +
          '<input class="meta-control" style="width:100%;padding:0 13px" type="number" min="0" ' +
          'step="0.01" data-up-field="budget_limit_min" value="' +
          escapeHtml(String(values.budget_limit_min || "")) + '"></label>' +
          '<span style="font-size:12px;color:#9B9292;font-weight:700">USD</span>' +
          '<label class="meta-field" style="width:150px"><span>Максимум</span>' +
          '<input class="meta-control" style="width:100%;padding:0 13px" type="number" min="0" ' +
          'step="0.01" data-up-field="budget_limit_max" value="' +
          escapeHtml(String(values.budget_limit_max || "")) + '"></label></div></div>'
        : "");
  }

  /* Запланированное увеличение бюджета: периоды [начало, завершение, тип, сумма]. */
  function budgetIncreaseHtml() {
    var values = state.upload.values;
    var on = !!values.budget_increase_on;
    var periods = values.budget_increases || [];
    var cards = periods.map(function (period, index) {
      var number = index + 1;
      return '<div style="border:1px solid #EBE6E6;border-radius:13px;padding:14px;' +
        'margin-top:10px;background:#FAF8F8">' +
        '<div style="display:flex;align-items:center;justify-content:space-between;gap:10px">' +
        '<div style="font-size:13px;font-weight:700">Период ' + number + "</div>" +
        '<button class="meta-action meta-action--danger" type="button" ' +
        'data-up-period-drop="' + index + '">Удалить период</button></div>' +
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px">' +
        '<label class="meta-field"><span>Начало</span>' +
        '<input class="meta-control" style="width:100%;padding:0 13px" type="datetime-local" ' +
        'data-up-period="' + index + '" data-up-period-field="start_at" value="' +
        escapeHtml(period.start_at || "") + '"></label>' +
        '<label class="meta-field"><span>Завершение</span>' +
        '<input class="meta-control" style="width:100%;padding:0 13px" type="datetime-local" ' +
        'data-up-period="' + index + '" data-up-period-field="end_at" value="' +
        escapeHtml(period.end_at || "") + '"></label></div>' +
        '<div style="display:flex;align-items:center;gap:12px;margin-top:12px;flex-wrap:wrap">' +
        '<select class="meta-control meta-select" style="width:280px" data-up-period="' + index +
        '" data-up-period-field="kind">' +
        '<option value="sum"' + (period.kind === "sum" ? " selected" : "") +
        '>Увеличить дневной бюджет на сумму ($)</option>' +
        '<option value="pct"' + (period.kind === "pct" ? " selected" : "") +
        '>Увеличить дневной бюджет на процент (%)</option></select>' +
        '<input class="meta-control" style="width:140px;padding:0 13px" type="number" min="0" ' +
        'step="0.01" data-up-period="' + index + '" data-up-period-field="amount" value="' +
        escapeHtml(String(period.amount || "")) + '">' +
        '<span style="font-size:12px;color:#9B9292;font-weight:700">' +
        (period.kind === "pct" ? "%" : "USD") + "</span></div></div>";
    }).join("");
    return '<div class="meta-line" style="align-items:flex-start"><span></span>' +
      '<div style="flex:1;min-width:0">' +
      '<label class="meta-switch"><input type="checkbox" data-up-field="budget_increase_on"' +
      (on ? " checked" : "") +
      '><span class="meta-switch__box"></span><span>Запланировать увеличение бюджета' +
      '<span class="meta-switch__hint">В определённые дни и периоды времени, когда ' +
      "ожидается высокий спрос.</span></span></label>" +
      (on
        ? '<div style="margin-top:12px">' + cards +
          '<button class="meta-action" type="button" data-up-period-add ' +
          'style="margin-top:10px">+ Добавить период</button>' +
          '<span style="margin-left:10px;font-size:11.5px;color:#9B9292;font-weight:700">' +
          "Период " + periods.length + " из 50</span></div>"
        : "") +
      "</div></div>";
  }

  function segHtml(name, options) {
    return '<div class="meta-seg">' + options.map(function (option) {
      return '<button type="button" data-up-seg="' + name + '" data-up-value="' +
        escapeHtml(option.value) + '" class="' +
        (state.upload.values[name] === option.value ? "meta-seg--on" : "") + '">' +
        escapeHtml(option.label) + "</button>";
    }).join("") + "</div>";
  }

  /* Авто-правила: два переключателя — применить правила и применить группу
     (логика групп появится позже, пока сохраняем). */
  function rulesCardHtml() {
    var values = state.upload.values;
    var rules = state.rules || [];
    return '<div class="meta-card"><div class="meta-card__title">Авто-правила</div>' +
      '<div style="display:grid;gap:14px">' +
      '<div class="meta-line"><span></span><div><label class="meta-switch">' +
      '<input type="checkbox" data-up-field="rules_on"' + (values.rules_on ? " checked" : "") +
      '><span class="meta-switch__box"></span><span>Применить автоправила</span></label>' +
      (values.rules_on
        ? '<select class="meta-control meta-select" style="width:100%;margin-top:10px" ' +
          'multiple size="4" data-up-field="rule_ids">' +
          (rules.length
            ? rules.map(function (rule) {
              return '<option value="' + escapeHtml(rule.id) + '"' +
                ((values.rule_ids || []).indexOf(rule.id) >= 0 ? " selected" : "") + ">" +
                escapeHtml(rule.name) + " · " + escapeHtml(rule.level || "") + "</option>";
            }).join("")
            : '<option value="">Автоправил пока не создано — заведите их на вкладке «Автоправила»</option>') +
          "</select>" +
          '<span class="meta-switch__hint">Правила будут смотреть только на объекты этого залива.</span>'
        : "") +
      "</div></div>" +
      '<div class="meta-line"><span></span><div><label class="meta-switch">' +
      '<input type="checkbox" data-up-field="rule_group_on"' +
      (values.rule_group_on ? " checked" : "") +
      '><span class="meta-switch__box"></span><span>Применить группу автоправил</span></label>' +
      (values.rule_group_on
        ? '<input class="meta-control" style="width:100%;margin-top:10px;padding:0 13px" ' +
          'type="text" placeholder="Название группы" data-up-field="rule_group" value="' +
          escapeHtml(values.rule_group || "") + '">'
        : "") +
      "</div></div></div></div>";
  }

  /* Дополнительно: теги после создания объявлений — 5 уровней, теги, режим. */
  function tagsCardHtml() {
    var values = state.upload.values;
    return '<div class="meta-card"><div class="meta-card__title">Дополнительно</div>' +
      '<span style="font-size:11.5px;color:#857D7D;font-weight:700">Присвойте теги ' +
      "после создания объявлений</span>" +
      '<div style="display:flex;gap:2px;flex-wrap:wrap;margin-top:12px">' +
      segHtml("tag_level", [
        { value: "account", label: "Аккаунты" },
        { value: "cabinet", label: "Кабинеты" },
        { value: "campaign", label: "Кампании" },
        { value: "adset", label: "Адсеты" },
        { value: "ad", label: "Объявления" }
      ]) + "</div>" +
      '<div class="meta-up-grid" style="margin-top:14px">' +
      uploadField("tag_names", "Теги", 'placeholder="Новая волна, Горячий"',
        "Через запятую — adlabels Meta на созданных объектах") +
      "</div>" +
      '<div class="meta-line" style="margin-top:14px"><span>Режим</span>' +
      segHtml("tag_mode", [
        { value: "add", label: "Добавить теги" },
        { value: "remove", label: "Убрать теги" }
      ]) + "</div></div>";
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
      (state.upload.advanced ? campaignsCardHtml() : "") +
      (state.upload.advanced ? goalCardHtml() : "") +
      (state.upload.advanced ? budgetCardHtml() : "") +
      (state.upload.advanced ? rulesCardHtml() : "") +
      (state.upload.advanced ? tagsCardHtml() : "");

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
        page_id: "", pixel_id: "", link_url: "", daily_budget: "",
        campaign_name: "", url_tags: "", display_link: "", beneficiary: ""
      };
    }
    return state.upload.perAccount[account.id];
  }

  function accountAssets(id) {
    return state.upload.assets[id] || { pages: [], pixels: [], loading: false };
  }

  function accountBeneficiaries(id) {
    return state.upload.beneficiaries[id] || { items: [], loading: false };
  }

  async function loadAccountBeneficiaries(id) {
    if (state.upload.beneficiaries[id]) return;
    state.upload.beneficiaries[id] = { items: [], loading: true };
    renderUpload();
    try {
      var data = await api.get("/meta/accounts/" + id + "/dsa-recommendations");
      state.upload.beneficiaries[id] = {
        items: data.recommendations || [], loading: false
      };
    } catch (error) {
      // Без подсказок поле остаётся доступным для ручного ввода: Meta даёт
      // рекомендации не всем кабинетам, а DSA-поля заполнять всё равно нужно.
      state.upload.beneficiaries[id] = { items: [], loading: false };
    }
    renderUpload();
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
      uploadSwitch("naming", "Кастомный нейминг") +
      uploadSwitch("urlTags", "Параметры URL") +
      uploadSwitch("displayLink", "Отображаемый URL") +
      uploadSwitch("beneficiary", "Бенефициар") +
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
        '"></td>' +
        '<td class="meta-cell meta-cell--left" style="min-width:230px">' +
        (on ? accountExtras(account.id) : "") + "</td></tr>";
    }).join("");

    return head +
      '<div style="background:#fff;border:1px solid #EBE6E6;border-radius:16px;overflow:hidden">' +
      '<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;' +
      'min-width:1170px"><thead><tr style="border-bottom:1px solid #F0EBEB">' +
      '<th class="meta-th meta-th--left" style="padding-left:16px"></th>' +
      '<th class="meta-th meta-th--left">Кабинет</th>' +
      '<th class="meta-th meta-th--left">Статус</th>' +
      '<th class="meta-th meta-th--left">Фан-пейдж и пиксель</th>' +
      '<th class="meta-th meta-th--left">Ссылка</th>' +
      '<th class="meta-th meta-th--left">Бюджет</th>' +
      '<th class="meta-th meta-th--left">Дополнительно</th></tr></thead><tbody>' +
      (body || '<tr><td colspan="7" style="padding:26px;text-align:center;color:#9B9292;' +
        'font-size:12.5px;font-weight:600">Ничего не нашлось</td></tr>') +
      "</tbody></table></div></div>";
  }

  function uploadSwitch(key, label) {
    return '<label class="meta-switch"><input type="checkbox" data-up-switch="' + key + '"' +
      (state.upload.switches[key] ? " checked" : "") +
      '><span class="meta-switch__box"></span><span>' + label + "</span></label>";
  }

  /* Дополнительные поля кабинета. Состав зависит от свитчеров: выключенный
     свитчер убирает поле — и его значение не уходит в залив. Бенефициар —
     селект с подсказками самого кабинета; если кабинет подсказок не дал,
     поле превращается в ручной ввод. */
  function accountExtras(accountId) {
    var own = accountRow({ id: accountId });
    var switches = state.upload.switches;
    var extra = "";
    if (switches.beneficiary) {
      var refs = accountBeneficiaries(accountId);
      if (refs.loading) {
        extra += '<div style="font-size:11.5px;color:#9B9292;font-weight:600">' +
          "Загружаем бенефициара…</div>";
      } else if (refs.items.length) {
        extra += '<select class="meta-control meta-select" style="width:100%;height:36px"' +
          ' data-up-account-field="beneficiary" data-up-account-id="' +
          escapeHtml(accountId) + '"><option value="">Бенефициар / Плательщик</option>' +
          refs.items.map(function (item) {
            return '<option value="' + escapeHtml(item) + '"' +
              (own.beneficiary === item ? " selected" : "") + ">" +
              escapeHtml(item) + "</option>";
          }).join("") + "</select>";
      } else {
        extra += '<input class="meta-control" style="width:100%;height:36px;padding:0 11px"' +
          ' placeholder="Бенефициар / Плательщик" data-up-account-field="beneficiary"' +
          ' data-up-account-id="' + escapeHtml(accountId) + '" value="' +
          escapeHtml(own.beneficiary || "") + '">';
      }
    }
    if (switches.naming) {
      extra += '<input class="meta-control" style="width:100%;height:36px;padding:0 11px;' +
        (extra ? "margin-top:6px;" : "") + '" placeholder="Кастомный нейминг"' +
        ' data-up-account-field="campaign_name" data-up-account-id="' +
        escapeHtml(accountId) + '" value="' + escapeHtml(own.campaign_name || "") + '">';
    }
    if (switches.urlTags) {
      extra += '<input class="meta-control" style="width:100%;height:36px;padding:0 11px;' +
        (extra ? "margin-top:6px;" : "") + '" placeholder="Параметры URL"' +
        ' data-up-account-field="url_tags" data-up-account-id="' +
        escapeHtml(accountId) + '" value="' + escapeHtml(own.url_tags || "") + '">';
    }
    if (switches.displayLink) {
      extra += '<input class="meta-control" style="width:100%;height:36px;padding:0 11px;' +
        (extra ? "margin-top:6px;" : "") + '" placeholder="Отображаемый URL"' +
        ' data-up-account-field="display_link" data-up-account-id="' +
        escapeHtml(accountId) + '" value="' + escapeHtml(own.display_link || "") + '">';
    }
    return extra || '<span style="font-size:11.5px;color:#C6BDBD">включите свитчер сверху</span>';
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

  function emptyText(language, languageName) {
    return {
      language: language,
      // Человеческое имя сохраняем в тексте объявления: по нему публикация
      // найдёт в Meta числовой ID локали для правил показа на языке.
      language_name: languageName || "",
      // Свой креатив языка («на каждый язык свой»).
      files: [],
      headline: "", description: "", primary_text: "",
      call_to_action: "LEARN_MORE"
    };
  }

  function uploadPreviewUrl(file) {
    if (!file || !window.URL || !URL.createObjectURL) return "";
    var cached = uploadPreviewUrls.get(file);
    if (cached) return cached;
    var url = URL.createObjectURL(file);
    uploadPreviewUrls.set(file, url);
    return url;
  }

  function clearUploadPreviews() {
    uploadPreviewUrls.forEach(function (url) {
      URL.revokeObjectURL(url);
    });
    uploadPreviewUrls.clear();
  }

  function creativePreviewHtml(adIndex, file, index, textIndex) {
    var name = String(file && file.name || "Креатив");
    var type = String(file && file.type || "").toLowerCase();
    var extension = name.split(".").pop().toLowerCase();
    var isVideo = type.indexOf("video/") === 0 ||
      ["avi", "mov", "mp4", "webm"].indexOf(extension) >= 0;
    var isImage = type.indexOf("image/") === 0 ||
      ["gif", "jpeg", "jpg", "png", "webp"].indexOf(extension) >= 0;
    var url = uploadPreviewUrl(file);
    var media = isVideo && url
      ? '<video src="' + escapeHtml(url) + '" controls muted playsinline ' +
        'preload="metadata" style="width:100%;height:150px;object-fit:contain;background:#F7F4F4"></video>'
      : isImage && url
        ? '<img src="' + escapeHtml(url) + '" alt="' + escapeHtml(name) +
          '" style="width:100%;height:150px;object-fit:contain;background:#F7F4F4">'
        : '<div style="height:150px;display:flex;align-items:center;justify-content:center;' +
          'padding:14px;color:#9B9292;font-size:12px;font-weight:700;text-align:center;' +
          'background:#F7F4F4">' + escapeHtml(type || "Файл") + "</div>";
    var dropKey = textIndex == null
      ? adIndex + ":" + index
      : adIndex + ":" + textIndex + ":" + index;
    return '<div style="position:relative;min-width:0;overflow:hidden;border:1px solid #EBE6E6;' +
      'border-radius:12px;background:#fff">' + media +
      '<div title="' + escapeHtml(name) + '" style="padding:9px 34px 9px 10px;overflow:hidden;' +
      'text-overflow:ellipsis;white-space:nowrap;color:#4D4343;font-size:11.5px;font-weight:700">' +
      escapeHtml(name) + "</div>" +
      '<button type="button" data-up-file-drop="' + dropKey +
      '" aria-label="Убрать ' + escapeHtml(name) + '" style="position:absolute;top:7px;right:7px;' +
      'width:24px;height:24px;padding:0;border:1px solid #E5DFDF;border-radius:50%;' +
      'background:rgba(255,255,255,.94);color:#B91414;font-size:17px;line-height:20px;cursor:pointer">×</button>' +
      "</div>";
  }

  function languageLabel(code) {
    var custom = state.upload && state.upload.languageNames[code];
    if (custom) return custom;
    var found = LANGUAGE_PRESETS.filter(function (row) { return row.code === code; })[0];
    return found ? found.label : code;
  }

  /* Короткая подпись языка для полей («Заголовок EN»). Для кодов вида xx_YY
     берём язык, для прочих (числовые ID локалей из поиска Meta) — имя. */
  function languageShort(code) {
    return String(code || "").indexOf("_") >= 0
      ? String(code).split("_")[0].toUpperCase()
      : languageLabel(code);
  }

  function adTextFields(adIndex, textIndex, text) {
    var suffix = state.upload.languages && text.language
      ? " " + languageShort(text.language)
      : "";
    var field = function (name, label, attrs) {
      return '<label class="meta-field" style="flex:1;min-width:150px">' +
        "<span>" + escapeHtml(label + suffix) + "</span>" +
        '<input class="meta-control" style="width:100%;padding:0 11px" ' + (attrs || "") +
        ' data-up-text="' + name + '" data-up-ad="' + adIndex + '" data-up-text-index="' +
        textIndex + '" value="' + escapeHtml(String(text[name] || "")) + '"></label>';
    };
    return '<div style="display:flex;gap:10px;flex-wrap:wrap;align-items:flex-end">' +
      field("headline", "Заголовок", 'maxlength="240"') +
      field("description", "Описание", 'maxlength="240"') +
      field("primary_text", "Текст") +
      // Ссылка этого языка: пустая — значит, общая ссылка кабинета со второго
      // шага; заполненная — Meta ведёт зрителя этого языка именно сюда.
      (state.upload.languages
        ? field("link_url", "Ссылка", 'placeholder="https://..." maxlength="2000"')
        : "") +
      '<label class="meta-field" style="width:170px"><span>Кнопка' + escapeHtml(suffix) +
      "</span>" +
      '<select class="meta-control meta-select" style="width:100%" data-up-text="call_to_action" ' +
      'data-up-ad="' + adIndex + '" data-up-text-index="' + textIndex + '">' +
      ctaOptions().map(function (option) {
        return '<option value="' + escapeHtml(option.value) + '"' +
          (option.value === text.call_to_action ? " selected" : "") + ">" +
          escapeHtml(option.label) + "</option>";
      }).join("") + "</select></label>" +
      "</div>";
  }

  function adFilesHtml(adIndex, files, textIndex) {
    var key = textIndex == null ? String(adIndex) : adIndex + ":" + textIndex;
    var hint = textIndex == null
      ? "jpg, png, gif, mp4 — файл уйдёт в каждый выбранный кабинет"
      : "Креатив этого языка: Meta покажет его зрителю именно этого языка";
    return '<div style="border:1px dashed #E2DADA;border-radius:14px;padding:16px;' +
      'margin-top:12px">' +
      '<button class="meta-action" type="button" data-up-files="' + key + '">' +
      "+ Добавить креативы</button>" +
      '<span style="margin-left:10px;font-size:11.5px;color:#9B9292;font-weight:600">' +
      hint + "</span>" +
      (files.length
        ? '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));' +
          'gap:10px;margin-top:14px">' + files.map(function (file, index) {
            return creativePreviewHtml(adIndex, file, index, textIndex);
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
      // Языки — табы: чипс выбранного языка, под ним его строка текстов
      // и его креативы («на каждый язык свой»).
      var activeText = state.upload.textTabs[index] || 0;
      if (!ad.texts[activeText]) activeText = 0;
      var textRow;
      var filesHtml;
      if (state.upload.languages) {
        var text = ad.texts[activeText];
        textRow = adTextFields(index, activeText, text);
        filesHtml = adFilesHtml(index, text.files || [], activeText);
      } else {
        textRow = ad.texts.map(function (entry, textIndex) {
          return adTextFields(index, textIndex, entry);
        }).join('<div style="height:10px"></div>');
        filesHtml = adFilesHtml(index, ad.files, null);
      }
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
        (state.upload.languages ? adLanguagesHtml(index, ad) : "") +
        '<div style="margin-bottom:12px">' + textRow + "</div>" +
        filesHtml + "</div>";
    }).join("");

    return panel + ads +
      '<button class="meta-action" type="button" data-up-ad-add>+ Добавить объявление</button>' +
      '<div class="meta-note" style="margin-top:14px">Кабинетов выбрано: ' + accounts.length +
      ". Каждый файл загрузится в каждый из них — один и тот же файл в другом кабинете " +
      "имеет другой хэш, и чужой Meta не примет.</div>" +
      (state.upload.langPicker ? langPickerHtml() : "");
  }

  /* Языки объявления: основной задаётся селектом, каждый язык — чипс-таб.
     Клик по чипсу показывает его строку текстов; × убирает язык. */
  function adLanguagesHtml(adIndex, ad) {
    var primary = ad.texts[0] || emptyText("");
    var active = state.upload.textTabs[adIndex] || 0;
    if (!ad.texts[active]) active = 0;
    var options = [{ code: "", label: "Основной язык" }].concat(
      LANGUAGE_PRESETS.map(function (row) { return row; })
    );
    // Нестандартные локали из поиска Meta тоже должны выбираться основным.
    ad.texts.forEach(function (text) {
      var code = text.language;
      if (code && !options.some(function (row) { return row.code === code; })) {
        options.push({ code: code, label: languageLabel(code) });
      }
    });
    var chips = ad.texts.map(function (text, textIndex) {
      var on = textIndex === active;
      var label = languageLabel(text.language);
      return '<span class="meta-tag" data-up-lang-tab="' + adIndex + ":" + textIndex +
        '" title="Показать тексты этого языка" style="cursor:pointer;' +
        (on ? "background:#B91414;color:#fff;font-family:Alumni Sans,Inter,sans-serif;text-transform:uppercase;letter-spacing:.02em;" : "") +
        '"' + (on ? ' data-up-lang-active="1"' : "") + ">" +
        escapeHtml(label) +
        (ad.texts.length > 1
          ? '<button type="button" data-up-lang-drop="' + adIndex + ":" + textIndex +
            '" aria-label="Убрать ' + escapeHtml(label) + '"' +
            ' style="' + (on ? "color:#fff;" : "") +
            'margin-left:5px">×</button>'
          : "") + "</span>";
    }).join("");
    return '<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;' +
      'margin-bottom:10px">' +
      '<label class="meta-field" style="width:190px"><span>Основной язык</span>' +
      '<select class="meta-control meta-select" style="width:100%" data-up-text="language" ' +
      'data-up-ad="' + adIndex + '" data-up-text-index="0">' +
      options.map(function (row) {
        return '<option value="' + escapeHtml(row.code) + '"' +
          (row.code === primary.language ? " selected" : "") + ">" +
          escapeHtml(row.label) + "</option>";
      }).join("") + "</select></label>" +
      chips +
      '<button class="meta-action" type="button" data-up-lang-add="' + adIndex +
      '">+ Добавить языки</button>' +
      "</div>";
  }

  function langPickerState() {
    return state.upload.langPicker || {};
  }

  function langPickerOptions() {
    /* Что показать в списке: пресеты, отфильтрованные по запросу, плюс то,
       что вернул поиск Meta. Запрос короче двух символов — только пресеты. */
    var picker = langPickerState();
    var query = (picker.query || "").trim().toLowerCase();
    var seen = {};
    var items = LANGUAGE_PRESETS.filter(function (row) {
      if (query && row.label.toLowerCase().indexOf(query) < 0 &&
        row.code.toLowerCase().indexOf(query) < 0) return false;
      seen[row.code] = true;
      return true;
    }).map(function (row) { return { code: row.code, name: row.label }; });
    (picker.remote || []).forEach(function (row) {
      if (seen[row.id]) return;
      if (query && row.name.toLowerCase().indexOf(query) < 0) return;
      items.push({ code: row.id, name: row.name });
    });
    return items;
  }

  function langPickerListHtml() {
    var picker = langPickerState();
    if (picker.loading) {
      return '<div class="meta-combo__empty">Ищем в Meta…</div>';
    }
    var items = langPickerOptions();
    if (!items.length) {
      return '<div class="meta-combo__empty">' +
        ((picker.query || "").trim().length < 2
          ? "Введите хотя бы два символа для поиска в Meta"
          : "Ничего не нашлось") + "</div>";
    }
    return items.map(function (row) {
      var checked = !!(picker.checked || {})[row.code];
      return '<label style="display:flex;align-items:center;gap:10px;padding:8px 10px;' +
        'border-radius:9px;cursor:pointer;font-size:12.5px;font-weight:600;color:#3A3030"' +
        ' onmouseover="this.style.background=\'#F7F4F4\'"' +
        ' onmouseout="this.style.background=\'transparent\'">' +
        '<input type="checkbox" data-up-lang-check="' + escapeHtml(row.code) + '"' +
        ' data-up-lang-name="' + escapeHtml(row.name) + '"' + (checked ? " checked" : "") +
        ' style="width:16px;height:16px;accent-color:#B91414;flex-shrink:0">' +
        "<span>" + escapeHtml(row.name) + '</span><span style="color:#9B9292;' +
        'font-size:11px;font-weight:600;margin-left:auto">' + escapeHtml(row.code) +
        "</span></label>";
    }).join("");
  }

  var langSearchTimer = null;

  function renderLangOptions() {
    var host = document.querySelector("[data-up-lang-options]");
    if (host) host.innerHTML = langPickerListHtml();
  }

  async function searchLocales(query) {
    var picker = state.upload.langPicker;
    if (!picker) return;
    picker.loading = true;
    renderLangOptions();
    try {
      var data = await api.get(
        "/meta/targeting?kind=locale&q=" + encodeURIComponent(query)
      );
      // Пока отвечала Meta, запрос могли сменить или модалку закрыть.
      if (!state.upload.langPicker || state.upload.langPicker.query !== query) return;
      state.upload.langPicker.remote = data.items || [];
      state.upload.langPicker.loading = false;
      renderLangOptions();
    } catch (error) {
      if (!state.upload.langPicker) return;
      state.upload.langPicker.loading = false;
      renderLangOptions();
    }
  }

  function langPickerHtml() {
    var picker = langPickerState();
    var picked = Object.keys(picker.checked || {}).length;
    return '<div style="position:fixed;inset:0;z-index:60;background:rgba(7,5,5,.42);' +
      'display:flex;align-items:center;justify-content:center;padding:24px">' +
      '<div role="dialog" aria-modal="true" aria-label="Добавить языки" ' +
      'style="width:100%;max-width:540px;background:#fff;border-radius:20px;padding:26px;' +
      'box-shadow:0 26px 60px rgba(30,20,20,.28);max-height:90vh;overflow:auto">' +
      '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px">' +
      '<div>' +
      '<div style="font-family:\'Alumni Sans\',\'Inter\',sans-serif;font-size:21px;' +
      'font-weight:700;letter-spacing:-.3px">Добавить языки</div>' +
      '<p style="font-size:12px;color:#6A6161;font-weight:500;margin-top:6px;line-height:1.55">' +
      "Отмеченные языки получат свою строку текстов и ссылку. Основной язык задаётся " +
      "селектом рядом с чипсами.</p></div>" +
      '<button type="button" data-up-lang-cancel aria-label="Закрыть" ' +
      'style="flex-shrink:0;width:34px;height:34px;border:1px solid #EBE6E6;background:#fff;' +
      'border-radius:10px;color:#857D7D;font-size:16px;font-weight:700;cursor:pointer">' +
      "×</button></div>" +
      '<div style="margin:16px 0 12px">' +
      '<input class="meta-control" style="width:100%;height:42px;padding:0 13px" ' +
      'type="search" placeholder="Поиск по справочнику Meta" data-up-lang-search value="' +
      escapeHtml(picker.query || "") + '"></div>' +
      '<div data-up-lang-options style="border:1px solid #EBE6E6;border-radius:12px;' +
      'max-height:280px;overflow:auto;padding:6px;min-height:120px">' +
      langPickerListHtml() + "</div>" +
      '<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;' +
      'margin-top:18px">' +
      '<span id="metaUpLangPicked" style="font-size:11.5px;color:#857D7D;font-weight:700">' +
      "Отмечено: " + picked + "</span>" +
      '<div style="display:flex;gap:10px">' +
      '<button type="button" data-up-lang-cancel style="height:38px;padding:0 16px;' +
      'border:1px solid #E8E2E2;border-radius:12px;background:#fff;color:#3A3030;' +
      'font-size:12.5px;font-weight:700;cursor:pointer">Отмена</button>' +
      '<button type="button" data-up-lang-apply style="height:38px;padding:0 18px;' +
      'border:0;border-radius:12px;background:#B91414;color:#fff;font-family:Alumni Sans,Inter,sans-serif;text-transform:uppercase;letter-spacing:.02em;font-size:14px;' +
      'font-weight:700;cursor:pointer">Добавить</button>' +
      "</div></div></div></div>";
  }

  function renderUpload() {
    var upload = state.upload;
    if (!upload) return;
    byId("metaUpSteps").innerHTML = uploadStepsHtml(upload.step);
    var body = byId("metaUpBody");
    if (upload.step === 1) body.innerHTML = renderUploadSettings();
    if (upload.step === 2) body.innerHTML = renderUploadAccounts();
    if (upload.step === 3) body.innerHTML = renderUploadCreatives();
    // Отдельной вкладки у мастера больше нет — в него заходят кнопкой «Залить»
    // в строке связки. Поэтому «Назад» с первого шага не прячется, а
    // возвращает к списку: иначе выйти было бы нечем.
    byId("metaUpBack").style.visibility = "";
    byId("metaUpBack").textContent = upload.step === 1 ? "К связкам" : "Назад";
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
    var noFiles = uploadAds().filter(function (ad) {
      if (state.upload.languages) {
        return ad.texts.some(function (text) { return !(text.files || []).length; });
      }
      return !ad.files.length;
    });
    if (noFiles.length) return uploadError(state.upload.languages
      ? "У каждого языка объявления должен быть свой креатив"
      : "У каждого объявления должен быть креатив");
    if (state.upload.languages) {
      var noLanguage = uploadAds().some(function (ad) {
        return ad.texts.some(function (text) { return !text.language; });
      });
      if (noLanguage) return uploadError("У каждого языкового варианта выберите язык");
      // Дубль языка в одном объявлении — два одинаковых правила показа в
      // asset_feed_spec: Meta такую креативку отбивает.
      var duplicated = uploadAds().some(function (ad) {
        var codes = ad.texts.map(function (text) {
          return text.language;
        }).filter(Boolean);
        return codes.length !== new Set(codes).size;
      });
      if (duplicated) return uploadError("Языки внутри объявления не должны повторяться");
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

    // Файлы пачки складываем по кабинетам: у «Разные крео» — по очереди.
    async function uploadForAccount(files, accountId, slot) {
      var picked = upload.split
        ? files.filter(function (_file, position) {
          return position % upload.accountIds.length === slot;
        })
        : files;
      var ids = [];
      for (var index = 0; index < picked.length; index += 1) {
        var form = new FormData();
        form.append("file", picked[index]);
        form.append("name", picked[index].name);
        var created = await api.upload(
          "/meta/creatives?account_id=" + encodeURIComponent(accountId) +
            (upload.unique ? "&unique=true" : ""),
          form
        );
        ids.push(created.id);
        byAccount[accountId].push(created.id);
      }
      return ids;
    }

    for (var adIndex = 0; adIndex < uploadAds().length; adIndex += 1) {
      var ad = uploadAds()[adIndex];
      for (var slot = 0; slot < upload.accountIds.length; slot += 1) {
        var accountId = upload.accountIds[slot];
        if (upload.languages) {
          // Свой креатив на каждый язык: тексты несут свои файлы и свои ids.
          var texts = [];
          for (var textIndex = 0; textIndex < ad.texts.length; textIndex += 1) {
            var text = ad.texts[textIndex];
            var ownIds = await uploadForAccount(text.files || [], accountId, slot);
            var clean = Object.assign({}, text);
            delete clean.files;
            clean.creative_ids = ownIds;
            texts.push(clean);
          }
          if (texts.length) {
            adsByAccount[accountId].push({ texts: texts, creative_ids: [] });
          }
        } else {
          var ids = await uploadForAccount(ad.files, accountId, slot);
          if (ids.length) {
            adsByAccount[accountId].push({
              // Ссылка языка уходит как есть: пустая — сервер подставит общую
              // ссылку кабинета, заполненная — Meta ведёт этот язык сюда.
              texts: ad.texts,
              creative_ids: ids
            });
          }
        }
      }
    }
    return { creatives: byAccount, ads: adsByAccount };
  }

  async function submitUpload() {
    var upload = state.upload;
    var values = upload.values;
    var chosenBundle = uploadBundle();
    var payload = {
      name: values.name.trim() || (chosenBundle && chosenBundle.name) || "Залив",
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
      // Расширенный режим: кампании, цель (4 селекта), бюджет и ставка.
      campaign_count: Number(values.campaign_count) || 1,
      objective: (values.objective || "").trim() || null,
      custom_event_type: (values.custom_event_type || "").trim() || null,
      attribution: (values.attribution || "").trim() || null,
      engaged_view: (values.engaged_view || "").trim() || null,
      budget_level: values.budget_level || null,
      budget_kind: values.budget_kind || null,
      budget_randomize: !!values.budget_randomize,
      budget_limit_min: values.budget_limit_on && values.budget_limit_min
        ? String(values.budget_limit_min) : null,
      budget_limit_max: values.budget_limit_on && values.budget_limit_max
        ? String(values.budget_limit_max) : null,
      budget_increases: values.budget_increase_on
        ? (values.budget_increases || []).filter(function (period) {
          return period && period.start_at && period.end_at;
        }).map(function (period) {
          return {
            start_at: localMoment(period.start_at),
            end_at: localMoment(period.end_at),
            kind: period.kind === "pct" ? "pct" : "sum",
            amount: Number(period.amount) || 0
          };
        }) : null,
      bid_strategy: values.bid_strategy || null,
      rule_ids: values.rules_on ? (values.rule_ids || []) : [],
      rule_group: values.rule_group_on ? (values.rule_group || "").trim() || null : null,
      tags: (values.tag_names || "").trim()
        ? {
          level: values.tag_level || "campaign",
          names: values.tag_names.split(",").map(function (name) {
            return name.trim();
          }).filter(Boolean),
          mode: values.tag_mode || "add"
        }
        : null,
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
          daily_budget: own.daily_budget || null,
          // Дополнительно: выключенный свитчер просто не оставил значения.
          campaign_name: own.campaign_name || null,
          url_tags: own.url_tags || null,
          display_link: own.display_link || null,
          beneficiary: own.beneficiary || null
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
      clearUploadPreviews();
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
    var langSearch = event.target.closest ? event.target.closest("[data-up-lang-search]") : null;
    if (langSearch) {
      var picker = state.upload.langPicker;
      if (picker) {
        picker.query = langSearch.value;
        // Перерисовываем только список: фокус в поиске должен остаться.
        renderLangOptions();
        if (langSearchTimer) clearTimeout(langSearchTimer);
        var query = langSearch.value.trim();
        if (query.length >= 2) {
          langSearchTimer = setTimeout(function () { searchLocales(query); }, 300);
        }
      }
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
      if (field.multiple) {
        state.upload.values[name] = Array.prototype.slice.call(field.selectedOptions || [])
          .map(function (option) { return option.value; })
          .filter(function (value) { return value; });
      } else {
        state.upload.values[name] = field.type === "checkbox" ? field.checked : field.value;
      }
      if (name === "template_id") {
        applyBundleToUpload(uploadBundle());
        return renderUpload();
      }
      // Переключатели, после которых карточка меняет состав полей.
      if (name === "budget_limit_on" || name === "budget_increase_on" ||
        name === "rules_on" || name === "rule_group_on") {
        return renderUpload();
      }
      return;
    }
    var period = target.closest ? target.closest("[data-up-period]") : null;
    if (period) {
      var periodIndex = Number(period.getAttribute("data-up-period"));
      state.upload.values.budget_increases[periodIndex][
        period.getAttribute("data-up-period-field")
      ] = period.value;
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
      var adIndex = Number(adText.getAttribute("data-up-ad"));
      var textIndex = Number(adText.getAttribute("data-up-text-index"));
      var textName = adText.getAttribute("data-up-text");
      uploadAds()[adIndex].texts[textIndex][textName] = adText.value;
      // «Основной язык» — селект: рядом с кодом запоминаем человеческое имя,
      // по нему публикация найдёт числовой ID локали в справочнике Meta.
      if (textName === "language" && adText.selectedIndex >= 0) {
        state.upload.languageNames[adText.value] =
          adText.options[adText.selectedIndex].text;
        uploadAds()[adIndex].texts[textIndex].language_name =
          state.upload.languageNames[adText.value] || "";
      }
      return;
    }
    var langCheck = target.closest ? target.closest("[data-up-lang-check]") : null;
    if (langCheck) {
      var picker = state.upload.langPicker;
      if (picker) {
        picker.checked = picker.checked || {};
        var code = langCheck.getAttribute("data-up-lang-check");
        if (langCheck.checked) {
          var name = langCheck.getAttribute("data-up-lang-name") || code;
          picker.checked[code] = name;
          // Имя запоминаем сразу: чипсы и «Основной язык» зовут язык по-человечески.
          state.upload.languageNames[code] = name;
        } else {
          delete picker.checked[code];
        }
        var counter = document.getElementById("metaUpLangPicked");
        if (counter) {
          counter.textContent = "Отмечено: " + Object.keys(picker.checked).length;
        }
      }
      return;
    }
    if (target.closest && target.closest("[data-up-only-picked]")) {
      state.upload.onlyPicked = target.checked;
      return renderUpload();
    }
    var extraSwitch = target.closest ? target.closest("[data-up-switch]") : null;
    if (extraSwitch) {
      state.upload.switches[extraSwitch.getAttribute("data-up-switch")] = target.checked;
      // Включили бенефициара — спрашиваем подсказки у уже отмеченных кабинетов.
      if (extraSwitch.getAttribute("data-up-switch") === "beneficiary" && target.checked) {
        state.upload.accountIds.forEach(function (id) { loadAccountBeneficiaries(id); });
      }
      return renderUpload();
    }
    if (target.closest && target.closest("[data-up-languages]")) {
      state.upload.languages = target.checked;
      // Включили языки — первому тексту нужен язык, иначе Meta не поймёт,
      // кому его показывать.
      uploadAds().forEach(function (ad) {
        var text = ad.texts[0] || emptyText("");
        if (target.checked && !text.language) {
          text.language = "en_US";
          text.language_name = "English (US)";
        }
        if (!target.checked) {
          // Файлы языка переносим в общий блок: без языков объявление
          // использует один креатив на все тексты.
          ad.files = ad.files.concat(text.files || []);
          text.files = [];
          ad.texts = [text];
        }
      });
      // После переключения панель языков всегда открыта на основном.
      state.upload.textTabs = {};
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
      if (target.checked && !state.rules.length) {
        api.get("/meta/rules?limit=200").then(function (payload) {
          state.rules = (payload && payload.items) || [];
          renderUpload();
        }).catch(function () {
          state.rules = [];
          renderUpload();
        });
      }
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
        if (state.upload.switches.beneficiary) loadAccountBeneficiaries(id);
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
      if (pick) {
        all.forEach(function (id) {
          loadAccountAssets(id);
          if (state.upload.switches.beneficiary) loadAccountBeneficiaries(id);
        });
      }
      return renderUpload();
    }
    if (closest("[data-up-link-all]")) return spreadLink();
    var periodAdd = closest("[data-up-period-add]");
    if (periodAdd) {
      if ((state.upload.values.budget_increases || []).length >= 50) {
        return showFailure(new Error("Периодов можно добавить не больше пятидесяти"));
      }
      state.upload.values.budget_increases = state.upload.values.budget_increases || [];
      state.upload.values.budget_increases.push(
        { start_at: "", end_at: "", kind: "sum", amount: "" }
      );
      return renderUpload();
    }
    var periodDrop = closest("[data-up-period-drop]");
    if (periodDrop) {
      state.upload.values.budget_increases.splice(
        Number(periodDrop.getAttribute("data-up-period-drop")), 1
      );
      return renderUpload();
    }

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
    var langAdd = closest("[data-up-lang-add]");
    if (langAdd) {
      state.upload.langPicker = {
        adIndex: Number(langAdd.getAttribute("data-up-lang-add")),
        query: "", remote: [], checked: {}, loading: false
      };
      return renderUpload();
    }
    if (closest("[data-up-lang-cancel]")) {
      state.upload.langPicker = null;
      return renderUpload();
    }
    var langApply = closest("[data-up-lang-apply]");
    if (langApply) {
      var picker = state.upload.langPicker;
      if (picker) {
        var owner = uploadAds()[picker.adIndex];
        Object.keys(picker.checked).forEach(function (code) {
          var exists = owner.texts.some(function (text) {
            return text.language === code;
          });
          if (!exists) {
            owner.texts.push(
              emptyText(code, picker.checked[code] || languageLabel(code))
            );
          }
        });
        // Показать строку последнего добавленного языка: его заполнять дальше.
        state.upload.textTabs[picker.adIndex] = owner.texts.length - 1;
        state.upload.langPicker = null;
      }
      return renderUpload();
    }
    var langTab = closest("[data-up-lang-tab]");
    if (langTab) {
      // Точка входа для чипса-таба, но не для его кнопки «×».
      if (event.target.closest("[data-up-lang-drop]")) {
        // удаление обрабатывается ниже
      } else {
        var tabWhere = langTab.getAttribute("data-up-lang-tab").split(":");
        state.upload.textTabs[Number(tabWhere[0])] = Number(tabWhere[1]);
        return renderUpload();
      }
    }
    var langDrop = closest("[data-up-lang-drop]");
    if (langDrop) {
      var where = langDrop.getAttribute("data-up-lang-drop").split(":");
      var owner = uploadAds()[Number(where[0])];
      var removed = Number(where[1]);
      if (owner.texts.length > 1) {
        owner.texts = owner.texts.filter(function (_text, position) {
          return position !== removed;
        });
        // Активный таб мог ссылаться на удалённый язык.
        var current = state.upload.textTabs[Number(where[0])] || 0;
        if (current === removed) state.upload.textTabs[Number(where[0])] = 0;
        if (current > removed) state.upload.textTabs[Number(where[0])] = current - 1;
      }
      return renderUpload();
    }
    var files = closest("[data-up-files]");
    if (files) return pickCreativeFiles(files.getAttribute("data-up-files"));
    var fileDrop = closest("[data-up-file-drop]");
    if (fileDrop) {
      var where = fileDrop.getAttribute("data-up-file-drop").split(":");
      var host = uploadAds()[Number(where[0])];
      if (where.length === 3 && state.upload.languages) {
        var text = host.texts[Number(where[1])];
        text.files = (text.files || []).filter(function (_file, position) {
          return position !== Number(where[2]);
        });
      } else {
        host.files = host.files.filter(function (_file, position) {
          return position !== Number(where[1]);
        });
      }
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

  function pickCreativeFiles(key) {
    var input = byId("metaUploadFiles");
    input.value = "";
    input.onchange = function () {
      var chosen = Array.prototype.slice.call(input.files || []);
      if (chosen.length) {
        var parts = String(key).split(":");
        var ad = uploadAds()[Number(parts[0])];
        if (parts.length > 1 && state.upload.languages) {
          // Свои креативы языка: складываем в его тексты.
          (ad.texts[Number(parts[1])].files = ad.texts[Number(parts[1])].files || [])
            .push.apply(ad.texts[Number(parts[1])].files, chosen);
        } else {
          ad.files = ad.files.concat(chosen);
        }
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
    scheduleLaunchPoll();
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
        return { value: creative.id, label: creativeLabel(creative) };
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

  /* --- вкладка правил: состояние --- */
  state.rulesTab = "fb";
  state.ruleSearch = "";
  state.rulePicked = {};
  state.groupSearch = "";
  state.groupPicked = {};
  state.groups = [];
  state.campaigns = [];
  state.ruleForm = null;
  state.groupForm = null;
  /* idle | loading | ready | error — вкладка рисует себя в любом из них, и
     пустая белая карточка перестала быть состоянием «ещё грузится». */
  state.rulesStatus = "idle";
  state.rulesError = "";

  var rulesRequest = null;

  async function loadRules(options) {
    var quiet = !!(options && options.quiet);
    if (rulesRequest) return rulesRequest;
    if (!quiet && state.rulesStatus !== "ready") {
      state.rulesStatus = "loading";
      renderRulesRoot();
    }
    rulesRequest = fetchRules();
    try {
      await rulesRequest;
    } finally {
      rulesRequest = null;
    }
  }

  async function fetchRules() {
    try {
      await loadReference();
      var page = await api.get("/meta/rules");
      state.rules = page.items || [];
      // Группы и журнал срабатываний — вторичные списки. Их отказ не должен
      // оставлять вкладку пустой: сами правила уже загружены и показываются.
      var rest = await Promise.all([
        api.get("/meta/rule-groups").catch(function () { return null; }),
        api.get("/meta/rule-events?limit=50").catch(function () { return null; })
      ]);
      state.groups = rest[0] ? (rest[0].items || []) : [];
      state.events = rest[1] ? (rest[1].items || []) : [];
      state.rulesStatus = "ready";
      state.rulesError = "";
    } catch (error) {
      // Не пробрасываем наверх: ошибка одного блока не повод накрывать весь
      // экран заглушкой «не удалось загрузить данные» — её видно в карточке,
      // и оттуда же можно повторить.
      state.rulesStatus = "error";
      state.rulesError = error && error.message
        ? error.message : "Не удалось загрузить автоправила";
    }
    renderRulesRoot();
    renderEvents();
  }

  /* Кампании нужны, только когда правило привязано к одной кампании: список
     тяжёлый, поэтому грузим его по требованию и один раз. */
  function ensureCampaigns() {
    if (state.campaigns.length || state.campaignsLoading) return;
    state.campaignsLoading = true;
    api.get("/meta/entities/campaigns").then(function (items) {
      state.campaigns = items || [];
    }).catch(function () {
      state.campaigns = [];
    }).then(function () {
      state.campaignsLoading = false;
      renderRuleForm();
    });
  }

  /* «Данные» одной строкой: с чем работает правило, где и за какой период. */
  function ruleScope(rule) {
    var reference = state.reference || {};
    var parts = [
      (reference.rule_levels || {})[rule.level] || rule.level,
      (reference.rule_statuses || {})[rule.entity_status] || rule.entity_status,
      (reference.rule_windows || {})[rule.window] || rule.window
    ];
    var where = rule.scope_kind === "campaign"
      ? "Кампания " + (campaignName(rule.campaign_external_id) || rule.campaign_external_id || "—")
      : (rule.account_name || "Все кабинеты");
    return '<div style="font-size:11px;color:#9B9292;font-weight:600;margin-top:4px;' +
      'line-height:1.5">' + escapeHtml(parts.join(" · ")) + "<br>" +
      escapeHtml(where) + " · мин. расход " + money(rule.min_spend) + "</div>";
  }

  function campaignName(externalId) {
    if (!externalId) return "";
    var row = (state.campaigns || []).filter(function (item) {
      return item.external_id === externalId;
    })[0];
    return row ? row.name : "";
  }

  function ruleConditions(rule) {
    var list = rule.conditions || [];
    if (!list.length) {
      return '<span class="meta-chip" style="background:#FFF9E9;color:#6A5A28">' +
        "Без условий — сработает на всех</span>";
    }
    return list.map(function (item) {
      return '<span class="meta-chip" style="background:#F4F0F0;color:#3A3030;' +
        'margin:2px 4px 2px 0">' + escapeHtml(conditionText(item)) + "</span>";
    }).join("");
  }

  /* Свои подписи операторов важнее справочника: в списке правил условие должно
     читаться теми же словами, что и в форме, где его набирали. */
  function conditionText(item) {
    var reference = state.reference || {};
    var sign = RULE_OPERATOR_LABELS[item.operator] ||
      (reference.rule_operators || {})[item.operator] || item.operator;
    return referenceMetricLabel(item.metric) + " " + sign + " " + (item.value === "" ||
      item.value === null || item.value === undefined ? "—" : item.value);
  }

  /* Справочник метрик: группы — для читаемости списка, коды — те же, что
     считает движок (`meta_metrics.METRIC_LABELS`). Дублировать один код под
     разными подписями нельзя: в `<select>` выбранным подсветится первый из
     них, и человек увидит не то, что выбрал. */
  var RULE_METRIC_GROUPS = [
    ["Наиболее распространенные", [
      ["spend", "Расход"], ["spend_total", "Потрачено за все время"],
      ["results", "Результаты"], ["cpa", "Цена за результат"],
      ["roi", "ROAS для покупок на веб-сайте"], ["roi", "Окупаемость затрат"],
      ["spend_day_pct", "% расходов за день"], ["spend_total_pct", "% расходов за все время"],
      ["reach_pct", "Охваченная аудитория, %"]
    ]],
    ["Настройки", [
      ["campaign_name", "Название кампании"], ["entity_name", "Название адсета"],
      ["objective", "Цель"], ["buying_type", "Закупочный тип"],
      ["spend_cap", "Предел затрат"], ["bid_amount", "Сумма ставки"],
      ["daily_budget", "Дневной бюджет"], ["lifetime_budget", "Бюджет на весь срок"]
    ]],
    ["Конверсии на веб-сайте (пиксель Fb)", [
      ["results", "Все конверсии на сайте"],
      ["pixel_purchases", "Покупки (пиксель Fb)"], ["pixel_leads", "Лиды (пиксель Fb)"],
      ["results", "Добавления платежной информации (пиксель Fb)"],
      ["results", "Добавления в корзину (пиксель Fb)"],
      ["results", "Завершенные регистрации (пиксель Fb)"],
      ["results", "Начатое оформление заказов (пиксель Fb)"]
    ]],
    ["Цена за конверсию на сайте (пиксель Facebook)", [
      ["cpa", "Цена за результат (пиксель Fb)"], ["cpc", "Цена за лид (пиксель Fb)"],
      ["cpl", "Цена за завершенную регистрацию (пиксель Fb)"]
    ]],
    ["Другое", [
      ["impressions", "Показы"], ["reach", "Охват"], ["clicks", "Клики"],
      ["link_clicks", "Клики по ссылке"], ["impressions", "Показы за весь срок действия"],
      ["leads", "Лиды"], ["actions_total", "Действия"], ["cpc", "CPC"],
      ["cpa", "CPA"], ["cpm", "CPM"], ["ctr", "CTR"], ["link_ctr", "CTR (ссылка)"],
      ["spend", "Потрачено сегодня"], ["yesterday_spend", "Вчерашние расходы"],
      ["pixel_leads", "Лиды (пиксель)"], ["pixel_purchases", "Покупки (пиксель)"],
      ["actions_total", "Другие действия"], ["cpa", "Цена за результат"],
      ["sales", "Продажи (Keitaro)"], ["profit", "Прибыль"], ["revenue", "Доход"],
      ["cpl", "Цена лида"]
    ]]
  ];

  /* Настройки объекта — строки: сравнивать их можно только на совпадение и
     вхождение, и порог у них тоже текстовый. Тот же список на бэкенде. */
  var RULE_TEXT_METRICS = {
    entity_name: true, campaign_name: true, objective: true, buying_type: true
  };

  var RULE_OPERATOR_LABELS = {
    lt: "<", lte: "≤", gt: ">", gte: "≥", eq: "=", ne: "!=", in: "∈",
    nin: "∉"
  };
  var RULE_NUMBER_OPERATORS = ["lt", "lte", "gt", "gte", "eq", "ne"];
  var RULE_TEXT_OPERATORS = ["eq", "ne", "in", "nin"];

  function isTextMetric(code) {
    return !!RULE_TEXT_METRICS[code];
  }

  function ruleOperators(metric) {
    return isTextMetric(metric) ? RULE_TEXT_OPERATORS : RULE_NUMBER_OPERATORS;
  }

  function referenceMetricLabel(code) {
    var labels = ((state.reference || {}).metrics || {});
    if (labels[code]) return labels[code];
    for (var g = 0; g < RULE_METRIC_GROUPS.length; g += 1) {
      var group = RULE_METRIC_GROUPS[g];
      for (var i = 0; i < group[1].length; i += 1) {
        if (group[1][i][0] === code) return group[1][i][1];
      }
    }
    return code;
  }

  function metricOptionsHtml(selected) {
    return RULE_METRIC_GROUPS.map(function (group) {
      return '<optgroup label="' + escapeHtml(group[0]) + '">' +
        group[1].map(function (item) {
          return '<option value="' + escapeHtml(item[0]) + '"' +
            (item[0] === selected ? " selected" : "") + ">" +
            escapeHtml(item[1]) + "</option>";
        }).join("") + "</optgroup>";
    }).join("");
  }

  var RULE_FREQUENCIES = [
    ["always", "Постоянно — при каждом прогоне"],
    ["daily_midnight", "Каждую полночь"],
    ["custom", "Свои дни и часы"]
  ];

  /* Область: с чем работает правило и на каком уровне. Пара «где + уровень»
     живёт в одном значении — раньше в списке было шесть пунктов с тремя
     повторяющимися value, и выбрать «Кабинет: адсет» было физически нельзя. */
  var RULE_SCOPES = [
    ["cabinet:ad", "Весь кабинет · объявления"],
    ["cabinet:adset", "Весь кабинет · адсеты"],
    ["cabinet:campaign", "Весь кабинет · кампании"],
    ["campaign:campaign", "Одна кампания · сама кампания"],
    ["campaign:adset", "Одна кампания · её адсеты"],
    ["campaign:ad", "Одна кампания · её объявления"]
  ];

  var RULE_ACTION_OPTIONS = [
    ["pause", "Остановить"],
    ["resume", "Запустить"],
    ["change_budget:adset", "Изменить бюджет адсета"],
    ["change_budget:campaign", "Изменить бюджет кампании"],
    ["change_bid", "Изменить ставку"]
  ];

  function ruleActionLabel(rule) {
    var reference = state.reference || {};
    var label = (reference.rule_actions || {})[rule.action] || rule.action;
    if (rule.action === "change_budget") {
      label = rule.level === "campaign" ? "Изменить бюджет кампании" : "Изменить бюджет адсета";
    }
    if (rule.action !== "change_budget" && rule.action !== "change_bid") return label;
    var sign = rule.action_sign === "minus" ? "−" : "+";
    var unit = rule.action_mode === "pct" ? " %" : " " + (rule.currency || "USD");
    if (!Number(rule.action_value)) return label + " " + sign + "…";
    return label + " " + sign + rule.action_value + unit;
  }

  function ruleScheduleLabel(rule) {
    if (rule.schedule_kind === "daily_midnight") return "Каждую полночь";
    if (rule.schedule_kind === "custom") {
      var payload = rule.schedule || {};
      var days = (payload.days || []).slice().sort(function (a, b) { return a - b; });
      var intervals = payload.intervals || [];
      var names = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
      var dayText = days.length === 7 ? "Ежедневно" : days.length
        ? days.map(function (day) { return names[day - 1] || day; }).join(", ")
        : "Любой день";
      var timeText = intervals.length
        ? intervals.map(function (item) {
          return (item.begin || "00:00") + "–" + (item.end || "00:00");
        }).join(", ")
        : "круглосуточно";
      return dayText + ", " + timeText;
    }
    return "Постоянно";
  }

  /* --- отрисовка вкладки --- */

  function renderRulesRoot() {
    var root = byId("metaRulesRoot");
    if (!root) return;
    syncRulesHeader();
    var focused = document.activeElement;
    var searchKind = focused && focused.getAttribute
      ? focused.getAttribute("data-rules-search") : null;
    if (state.rulesStatus === "loading" || state.rulesStatus === "idle") {
      root.innerHTML = '<div class="rule-skeleton" aria-hidden="true">' +
        "<span></span><span></span><span></span><span></span></div>";
      return renderRuleForm();
    }
    if (state.rulesStatus === "error") {
      root.innerHTML = '<div class="rule-state rule-state--error"><b>Автоправила не загрузились</b>' +
        escapeHtml(state.rulesError) + '<div style="margin-top:14px">' +
        '<button type="button" class="meta-action" data-rules-retry>Повторить</button></div></div>';
      return renderRuleForm();
    }
    root.innerHTML = rulesSubtabs() +
      (state.rulesTab === "fb" ? renderRulesList() : renderGroupsList());
    if (searchKind) {
      var field = root.querySelector('[data-rules-search="' + searchKind + '"]');
      if (field) {
        field.focus();
        try {
          field.setSelectionRange(field.value.length, field.value.length);
        } catch (error) {
          // type="search" не везде поддерживает каретку — фокуса достаточно.
        }
      }
    }
    renderRuleForm();
  }

  /* Кнопки живут в шапке вкладки, как на «Заливах». Раньше их прятали из JS и
     рисовали вторые такие же внутри карточки — одно и то же действие в двух
     местах, причём кнопка в шапке ни к чему не была привязана. */
  function syncRulesHeader() {
    var run = byId("metaRulesRun");
    var create = byId("metaRuleCreate");
    var ready = state.rulesStatus === "ready";
    if (run) run.style.display = state.canLaunch && ready ? "" : "none";
    if (!create) return;
    create.style.display = state.canLaunch && ready ? "" : "none";
    create.textContent = state.rulesTab === "groups" ? "Новая группа" : "Новое правило";
  }

  function createFromHeader() {
    if (state.rulesTab === "groups") {
      state.groupForm = { id: null, name: "", rule_ids: [] };
      state.ruleFormError = "";
      state.ruleFormFresh = true;
      return renderRuleForm();
    }
    openRuleEditor(null);
  }

  function rulesSubtabs() {
    return '<div class="rule-subtabs">' + [
      { key: "fb", label: "Правила", count: (state.rules || []).length },
      { key: "groups", label: "Группы правил", count: (state.groups || []).length }
    ].map(function (item) {
      return '<button type="button" class="rule-subtab' +
        (state.rulesTab === item.key ? " rule-subtab--on" : "") +
        '" data-rules-tab="' + item.key + '">' + item.label +
        '<span class="rule-subtab__count">' + item.count + "</span></button>";
    }).join("") + "</div>";
  }

  function rulesToolbar(kind) {
    var picked = kind === "fb" ? state.rulePicked : state.groupPicked;
    var count = Object.keys(picked).length;
    var search = kind === "fb" ? state.ruleSearch : state.groupSearch;
    return '<div class="rule-toolbar">' +
      '<input class="meta-control rule-toolbar__search" type="search" ' +
      'placeholder="Поиск по названию" aria-label="Поиск по названию" value="' +
      escapeHtml(search) + '" data-rules-search="' + kind + '">' +
      (count
        ? '<span style="font-size:12px;color:#857D7D;font-weight:700">Выбрано: ' + count +
          "</span>" +
          '<button type="button" class="meta-action" data-rules-clear>Снять выбор</button>' +
          '<button type="button" class="meta-action meta-action--danger" ' +
          'data-rules-delete-picked>Удалить выбранные</button>'
        : "") +
      "</div>";
  }

  function renderRulesList() {
    var query = (state.ruleSearch || "").trim().toLowerCase();
    var rows = (state.rules || []).filter(function (rule) {
      if (!query) return true;
      return String(rule.name || "").toLowerCase().indexOf(query) >= 0;
    });
    var body = rows.map(function (rule) {
      var picked = !!state.rulePicked[rule.id];
      return '<tr class="meta-row" style="border-bottom:1px solid #F7F4F4">' +
        '<td class="meta-cell meta-cell--left" style="padding-left:20px;width:36px">' +
        '<input type="checkbox" aria-label="Выбрать правило" data-rule-pick="' +
        escapeHtml(rule.id) + '"' + (picked ? " checked" : "") +
        ' style="width:16px;height:16px;accent-color:#B91414"></td>' +
        '<td class="meta-cell meta-cell--left" style="min-width:190px;white-space:normal">' +
        '<div style="display:flex;align-items:center;gap:9px;flex-wrap:wrap">' +
        '<span style="font-weight:700">' + escapeHtml(rule.name) + "</span>" +
        ruleToggleHtml(rule) + "</div>" + ruleScope(rule) + "</td>" +
        '<td class="meta-cell meta-cell--left" style="min-width:170px;white-space:normal">' +
        ruleConditions(rule) + "</td>" +
        '<td class="meta-cell meta-cell--left">' + escapeHtml(ruleActionLabel(rule)) + "</td>" +
        '<td class="meta-cell meta-cell--left" style="white-space:normal;min-width:130px">' +
        escapeHtml(ruleScheduleLabel(rule)) + "</td>" +
        '<td class="meta-cell meta-cell--left" style="padding-right:20px">' +
        '<div style="display:flex;gap:6px">' +
        '<button type="button" class="meta-action" data-rules-preview="' +
        escapeHtml(rule.id) + '" title="Показать, на кого правило сработает прямо сейчас">' +
        "Что сработает</button>" +
        (state.canLaunch
          ? '<button type="button" class="meta-action" data-rules-edit="' +
            escapeHtml(rule.id) + '">Изменить</button>' +
            '<button type="button" class="meta-action meta-action--danger" ' +
            'data-rules-delete="' + escapeHtml(rule.id) + '">Удалить</button>'
          : "") +
        "</div></td></tr>";
    }).join("");
    return rulesToolbar("fb") +
      '<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;' +
      'min-width:1000px"><thead><tr style="border-bottom:1px solid #F0EBEB">' +
      '<th class="meta-th meta-th--left" style="padding-left:20px;width:36px"></th>' +
      '<th class="meta-th meta-th--left">Правило</th>' +
      '<th class="meta-th meta-th--left">Условия</th>' +
      '<th class="meta-th meta-th--left">Действие</th>' +
      '<th class="meta-th meta-th--left">Когда проверять</th>' +
      '<th class="meta-th meta-th--left" style="padding-right:20px">Действия</th>' +
      "</tr></thead><tbody>" +
      (body || '<tr><td colspan="6"><div class="rule-state">' +
        (query
          ? "<b>Ничего не нашлось</b>По запросу «" + escapeHtml(state.ruleSearch) +
            "» правил нет."
          : "<b>Правил пока нет</b>Автоправило само остановит объявление или изменит " +
            "бюджет, когда цифры выйдут за рамки." +
            (state.canLaunch
              ? '<div style="margin-top:14px"><button type="button" ' +
                'class="meta-action meta-action--primary" data-rules-create>' +
                "Создать первое правило</button></div>"
              : "")) +
        "</div></td></tr>") +
      "</tbody></table></div>" +
      '<div class="rule-foot"><span>Правил: ' + rows.length +
      (rows.length === (state.rules || []).length ? "" : " из " + (state.rules || []).length) +
      "</span>" +
      '<span>Считаются по нашим числам: расход из Meta, доход из Keitaro</span></div>';
  }

  /* Выключить правило, не удаляя его, — самый частый способ «поставить на
     паузу» на время теста. Раньше состояние было только подписью. */
  function ruleToggleHtml(rule) {
    if (!state.canLaunch) {
      return rule.is_enabled
        ? '<span class="meta-chip" style="color:#0F9D58;background:#E4F7F0">включено</span>'
        : '<span class="meta-chip" style="color:#9B9292;background:#F7F4F4">выключено</span>';
    }
    return '<button type="button" class="meta-chip" data-rules-toggle="' +
      escapeHtml(rule.id) + '" style="border:0;cursor:pointer;' +
      (rule.is_enabled ? "color:#0F9D58;background:#E4F7F0" : "color:#9B9292;background:#F7F4F4") +
      '" title="' + (rule.is_enabled ? "Выключить правило" : "Включить правило") + '">' +
      (rule.is_enabled ? "включено" : "выключено") + "</button>";
  }

  function renderGroupsList() {
    var query = (state.groupSearch || "").trim().toLowerCase();
    var rows = (state.groups || []).filter(function (group) {
      if (!query) return true;
      return String(group.name || "").toLowerCase().indexOf(query) >= 0;
    });
    var body = rows.map(function (group) {
      var picked = !!state.groupPicked[group.id];
      var rules = (group.rules || []).map(function (rule) {
        return '<span class="meta-chip" style="background:#F4F0F0;color:#3A3030;' +
          'margin:2px 4px 2px 0">' + escapeHtml(rule.name) + "</span>";
      }).join("");
      return '<tr class="meta-row" style="border-bottom:1px solid #F7F4F4">' +
        '<td class="meta-cell meta-cell--left" style="padding-left:20px;width:36px">' +
        '<input type="checkbox" aria-label="Выбрать группу" data-group-pick="' +
        escapeHtml(group.id) + '"' + (picked ? " checked" : "") +
        ' style="width:16px;height:16px;accent-color:#B91414"></td>' +
        '<td class="meta-cell meta-cell--left" style="min-width:240px;font-weight:700">' +
        escapeHtml(group.name) + "</td>" +
        '<td class="meta-cell meta-cell--left" style="min-width:300px;white-space:normal">' +
        (rules || '<span style="color:#C6BDBD;font-size:11.5px;font-weight:600">' +
          "Правил в группе нет</span>") + "</td>" +
        '<td class="meta-cell meta-cell--left" style="padding-right:20px">' +
        (state.canLaunch
          ? '<div style="display:flex;gap:6px">' +
            '<button type="button" class="meta-action" data-groups-edit="' +
            escapeHtml(group.id) + '">Изменить</button>' +
            '<button type="button" class="meta-action meta-action--danger" ' +
            'data-groups-delete="' + escapeHtml(group.id) + '">Удалить</button></div>'
          : "") + "</td></tr>";
    }).join("");
    return rulesToolbar("groups") +
      '<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;' +
      'min-width:760px"><thead><tr style="border-bottom:1px solid #F0EBEB">' +
      '<th class="meta-th meta-th--left" style="padding-left:20px;width:36px"></th>' +
      '<th class="meta-th meta-th--left">Название</th>' +
      '<th class="meta-th meta-th--left">Правила</th>' +
      '<th class="meta-th meta-th--left" style="padding-right:20px">Действия</th>' +
      "</tr></thead><tbody>" +
      (body || '<tr><td colspan="4"><div class="rule-state">' +
        (query
          ? "<b>Ничего не нашлось</b>По запросу «" + escapeHtml(state.groupSearch) +
            "» групп нет."
          : "<b>Групп пока нет</b>Группа собирает правила вместе — удобно, когда их " +
            "много и они делятся по офферам или баерам." +
            (state.canLaunch
              ? '<div style="margin-top:14px"><button type="button" ' +
                'class="meta-action meta-action--primary" data-groups-create>' +
                "Создать первую группу</button></div>"
              : "")) +
        "</div></td></tr>") +
      "</tbody></table></div>" +
      '<div class="rule-foot"><span>Групп: ' + rows.length + "</span></div>";
  }

  /* --- обработчики автоправил --- */
  function emptyRuleForm() {
    // Эталонный набор: без кабинета, минимального расхода и выключателя —
    // пауза и включённость задаются серверными значениями по умолчанию.
    return {
      id: null, name: "", convert_currency: false, currency: "USD",
      schedule_kind: "always", schedule_days: [], schedule_intervals: [],
      scope_kind: "cabinet", level: "ad", campaign_external_id: "",
      entity_status: "active", window: "today",
      action: "pause", action_sign: "plus", action_mode: "pct",
      budget_kind: "daily", action_value: "", action_max: "",
      conditions: [{ metric: "spend", operator: "lt", value: "" }]
    };
  }

  function ruleFormFrom(rule) {
    var schedule = rule.schedule || {};
    var intervals = (schedule.intervals || []).map(function (item) {
      var begin = String(item.begin || "00:00").split(":");
      var end = String(item.end || "00:00").split(":");
      return {
        begin_h: begin[0] || "00", begin_m: begin[1] || "00",
        end_h: end[0] || "00", end_m: end[1] || "00"
      };
    });
    return {
      id: rule.id || null, name: rule.name || "",
      convert_currency: !!rule.convert_currency,
      currency: rule.currency || "USD", schedule_kind: rule.schedule_kind || "always",
      schedule_days: (schedule.days || []).slice(), schedule_intervals: intervals,
      scope_kind: rule.scope_kind || "cabinet", level: rule.level || "ad",
      campaign_external_id: rule.campaign_external_id || "",
      entity_status: rule.entity_status || "active", window: rule.window || "today",
      action: rule.action || "pause", action_sign: rule.action_sign || "plus",
      action_mode: rule.action_mode || "pct", budget_kind: rule.budget_kind || "daily",
      action_value: rule.action_value == null ? "" : String(rule.action_value),
      action_max: rule.action_max == null ? "" : String(rule.action_max),
      conditions: (rule.conditions || []).map(function (item) {
        return { metric: item.metric, operator: item.operator, value: item.value };
      })
    };
  }

  /* Что мешает сохранить. Пусто — значит форма готова: тем же списком
     подсвечивается кнопка, поэтому проверка живёт в одном месте. */
  function ruleFormProblem(f) {
    if (!String(f.name || "").trim()) return "Укажите название правила";
    if (f.scope_kind === "campaign" && !f.campaign_external_id) {
      return "Выберите кампанию, с которой работает правило";
    }
    if (f.action === "change_budget" && f.level === "ad") {
      return "У объявления нет своего бюджета — выберите уровень «адсеты» или «кампании»";
    }
    if (f.action === "change_bid" && f.level !== "adset") {
      return "Ставка задаётся на адсете — выберите область с адсетами";
    }
    if ((f.action === "change_budget" || f.action === "change_bid") &&
      !Number(f.action_value)) {
      return "Укажите, на сколько менять";
    }
    for (var i = 0; i < f.conditions.length; i += 1) {
      var item = f.conditions[i];
      var value = String(item.value == null ? "" : item.value).trim();
      if (!value) return "Заполните значение в условии №" + (i + 1);
      if (!isTextMetric(item.metric) && isNaN(Number(value))) {
        return "Условие №" + (i + 1) + ": «" + referenceMetricLabel(item.metric) +
          "» сравнивается с числом";
      }
    }
    return "";
  }

  async function saveRuleForm() {
    var f = state.ruleForm;
    if (!f) return;
    var problem = ruleFormProblem(f);
    if (problem) {
      state.ruleFormError = problem;
      return renderRuleForm();
    }
    // Правило без условий осмысленно («остановить все активные объявления»),
    // но подтвердить его стоит осознанно, а не проскочить по невнимательности.
    if (!f.conditions.length && f.action !== "notify") {
      if (!(await askConfirm({
        title: "Правило без условий",
        message: "Оно сработает на всех объектах области и выполнит «" +
          ruleActionLabel(f) + "». Это точно то, что нужно?",
        confirmLabel: "Да, сохранить",
        danger: true
      }))) return;
    }
    var payload = {
      name: f.name.trim(),
      convert_currency: !!f.convert_currency,
      currency: f.convert_currency ? (f.currency || "USD") : null,
      schedule_kind: f.schedule_kind,
      schedule: f.schedule_kind === "custom"
        ? {
          days: f.schedule_days,
          intervals: (f.schedule_intervals || []).map(function (item) {
            return {
              begin: item.begin_h + ":" + item.begin_m,
              end: item.end_h + ":" + item.end_m
            };
          })
        }
        : null,
      scope_kind: f.scope_kind,
      level: f.level,
      campaign_external_id: f.scope_kind === "campaign" ? (f.campaign_external_id || null) : null,
      entity_status: f.entity_status,
      window: f.window,
      action: f.action,
      action_sign: f.action_sign,
      action_mode: f.action_mode,
      budget_kind: f.budget_kind || "daily",
      action_value: f.action_value === "" ? null : String(f.action_value),
      action_max: f.action_max === "" ? null : String(f.action_max),
      conditions: f.conditions,
      min_spend: "0",
      is_enabled: true
    };
    state.ruleFormSaving = true;
    state.ruleFormError = "";
    renderRuleForm();
    try {
      if (f.id) await api.patch("/meta/rules/" + f.id, payload);
      else await api.post("/meta/rules", payload);
      state.ruleForm = null;
      state.ruleFormSaving = false;
      await loadRules({ quiet: true });
    } catch (error) {
      state.ruleFormSaving = false;
      state.ruleFormError = error && error.message
        ? error.message : "Не удалось сохранить правило";
      renderRuleForm();
    }
  }

  async function saveGroupForm() {
    var g = state.groupForm;
    if (!g.name.trim()) {
      state.ruleFormError = "Укажите название группы";
      return renderRuleForm();
    }
    var payload = { name: g.name.trim(), rule_ids: g.rule_ids || [] };
    state.ruleFormSaving = true;
    state.ruleFormError = "";
    renderRuleForm();
    try {
      if (g.id) await api.patch("/meta/rule-groups/" + g.id, payload);
      else await api.post("/meta/rule-groups", payload);
      state.groupForm = null;
      state.ruleFormSaving = false;
      await loadRules({ quiet: true });
    } catch (error) {
      state.ruleFormSaving = false;
      state.ruleFormError = error && error.message
        ? error.message : "Не удалось сохранить группу";
      renderRuleForm();
    }
  }

  function openRuleEditor(rule) {
    state.ruleForm = rule ? ruleFormFrom(rule) : emptyRuleForm();
    state.ruleFormError = "";
    state.ruleFormSaving = false;
    state.ruleFormFresh = true;
    if (state.ruleForm.scope_kind === "campaign") ensureCampaigns();
    renderRuleForm();
  }

  function closeRuleEditor() {
    state.ruleForm = null;
    state.groupForm = null;
    state.ruleFormError = "";
    state.ruleFormSaving = false;
    renderRuleForm();
  }

  document.addEventListener("click", function (event) {
    var target = event.target.closest ? event.target : null;
    if (!target) return;
    function closest(selector) { return target.closest ? target.closest(selector) : null; }

    if (closest("[data-rules-retry]")) return loadRules();
    var rulesTab = closest("[data-rules-tab]");
    if (rulesTab) {
      state.rulesTab = rulesTab.getAttribute("data-rules-tab");
      return renderRulesRoot();
    }
    if (closest("[data-rule-pick]")) {
      var id = closest("[data-rule-pick]").getAttribute("data-rule-pick");
      if (event.target.checked) state.rulePicked[id] = true;
      else delete state.rulePicked[id];
      return renderRulesRoot();
    }
    if (closest("[data-group-pick]")) {
      var id2 = closest("[data-group-pick]").getAttribute("data-group-pick");
      if (event.target.checked) state.groupPicked[id2] = true;
      else delete state.groupPicked[id2];
      return renderRulesRoot();
    }
    if (closest("[data-rules-clear]")) {
      state.rulePicked = {}; state.groupPicked = {};
      return renderRulesRoot();
    }
    if (closest("[data-rules-delete-picked]")) {
      return state.rulesTab === "fb"
        ? deletePickedRules(Object.keys(state.rulePicked))
        : deletePickedGroups(Object.keys(state.groupPicked));
    }
    if (closest("[data-rules-create]")) return openRuleEditor(null);
    var edit = closest("[data-rules-edit]");
    if (edit) {
      var rule = (state.rules || []).filter(function (row) {
        return row.id === edit.getAttribute("data-rules-edit");
      })[0];
      if (rule) return openRuleEditor(rule);
      return;
    }
    var preview = closest("[data-rules-preview]");
    if (preview) return previewRule(preview.getAttribute("data-rules-preview"));
    var toggle = closest("[data-rules-toggle]");
    if (toggle) return toggleRule(toggle.getAttribute("data-rules-toggle"));
    var del = closest("[data-rules-delete]");
    if (del) return deleteRule(del.getAttribute("data-rules-delete"));
    if (closest("[data-rules-run]")) return runRules();
    if (closest("[data-rules-save]")) return saveRuleForm();
    if (closest("[data-rules-close]")) return closeRuleEditor();
    if (closest("[data-rule-cond-add]")) {
      state.ruleForm.conditions.push({ metric: "spend", operator: "gt", value: "" });
      return renderRuleForm();
    }
    if (closest("[data-rule-cond-empty]")) {
      // «Мне нужно правило без условий»: по ТЗ это не пустой список, а готовое
      // условие-заглушка, которое пользователь потом правит под себя.
      state.ruleForm.conditions = [{ metric: "spend", operator: "lt", value: "999999" }];
      return renderRuleForm();
    }
    var condDrop = closest("[data-rule-cond-drop]");
    if (condDrop) {
      state.ruleForm.conditions.splice(
        Number(condDrop.getAttribute("data-rule-cond-drop")), 1);
      return renderRuleForm();
    }
    var day = closest("[data-rule-day]");
    if (day) {
      var dayNum = Number(day.getAttribute("data-rule-day"));
      var days = state.ruleForm.schedule_days;
      var at = days.indexOf(dayNum);
      if (at >= 0) days.splice(at, 1); else days.push(dayNum);
      return renderRuleForm();
    }
    if (closest("[data-rule-days-all]")) {
      var all = state.ruleForm.schedule_days.length === 7;
      state.ruleForm.schedule_days = all ? [] : [1, 2, 3, 4, 5, 6, 7];
      return renderRuleForm();
    }
    if (closest("[data-rule-int-add]")) {
      state.ruleForm.schedule_intervals.push(
        { begin_h: "00", begin_m: "00", end_h: "23", end_m: "59" });
      return renderRuleForm();
    }
    var intDrop = closest("[data-rule-int-drop]");
    if (intDrop) {
      state.ruleForm.schedule_intervals.splice(
        Number(intDrop.getAttribute("data-rule-int-drop")), 1);
      return renderRuleForm();
    }
    var budgetSeg = closest("[data-rule-form-budget]");
    if (budgetSeg) return ruleFormSet("budget_kind", budgetSeg.getAttribute("data-rule-form-budget"));
    var signSeg = closest("[data-rule-form-sign]");
    if (signSeg) return ruleFormSet("action_sign", signSeg.getAttribute("data-rule-form-sign"));

    /* группы */
    if (closest("[data-groups-create]")) {
      state.groupForm = { id: null, name: "", rule_ids: [] };
      state.ruleFormError = "";
      state.ruleFormFresh = true;
      return renderRuleForm();
    }
    var gEdit = closest("[data-groups-edit]");
    if (gEdit) {
      var group = (state.groups || []).filter(function (row) {
        return row.id === gEdit.getAttribute("data-groups-edit");
      })[0];
      if (group) {
        state.groupForm = {
          id: group.id, name: group.name || "",
          rule_ids: (group.rules || []).map(function (row) { return row.id; })
        };
        state.ruleFormError = "";
        state.ruleFormFresh = true;
        return renderRuleForm();
      }
      return;
    }
    var gDel = closest("[data-groups-delete]");
    if (gDel) return deleteGroup(gDel.getAttribute("data-groups-delete"));
    if (closest("[data-groups-save]")) return saveGroupForm();
    // Клик по затемнению закрывает форму — как в остальных модалках экрана.
    if (target.classList && target.classList.contains("rule-modal")) {
      return closeRuleEditor();
    }
  });

  function ruledName(id) {
    var rule = (state.rules || []).filter(function (row) { return row.id === id; })[0];
    return rule ? rule.name : id;
  }

  function groupedName(id) {
    var group = (state.groups || []).filter(function (row) { return row.id === id; })[0];
    return group ? group.name : id;
  }

  async function deleteRule(id) {
    if (!(await askConfirm({
      title: "Удалить правило?",
      message: "«" + ruledName(id) + "» перестанет срабатывать.",
      confirmLabel: "Удалить",
      danger: true
    }))) return;
    try {
      await api.delete("/meta/rules/" + id);
      delete state.rulePicked[id];
      await loadRules({ quiet: true });
    } catch (error) {
      showFailure(error);
    }
  }

  async function deleteGroup(id) {
    if (!(await askConfirm({
      title: "Удалить группу?",
      message: "«" + groupedName(id) + "» исчезнет, правила из неё останутся.",
      confirmLabel: "Удалить",
      danger: true
    }))) return;
    try {
      await api.delete("/meta/rule-groups/" + id);
      delete state.groupPicked[id];
      await loadRules({ quiet: true });
    } catch (error) {
      showFailure(error);
    }
  }

  async function deletePickedRules(ids) {
    if (!ids.length) return;
    if (!(await askConfirm({
      title: "Удалить выбранные правила?",
      message: "Правил к удалению: " + ids.length + ". Они перестанут срабатывать.",
      confirmLabel: "Удалить",
      danger: true
    }))) return;
    try {
      await Promise.all(ids.map(function (id) { return api.delete("/meta/rules/" + id); }));
      state.rulePicked = {};
      await loadRules({ quiet: true });
    } catch (error) {
      showFailure(error);
    }
  }

  async function deletePickedGroups(ids) {
    if (!ids.length) return;
    if (!(await askConfirm({
      title: "Удалить выбранные группы?",
      message: "Групп к удалению: " + ids.length + ". Правила внутри останутся.",
      confirmLabel: "Удалить",
      danger: true
    }))) return;
    try {
      await Promise.all(ids.map(function (id) {
        return api.delete("/meta/rule-groups/" + id);
      }));
      state.groupPicked = {};
      await loadRules({ quiet: true });
    } catch (error) {
      showFailure(error);
    }
  }

  async function toggleRule(id) {
    var rule = (state.rules || []).filter(function (row) { return row.id === id; })[0];
    if (!rule) return;
    try {
      await api.patch("/meta/rules/" + id, { is_enabled: !rule.is_enabled });
      await loadRules({ quiet: true });
    } catch (error) {
      showFailure(error);
    }
  }

  function ruleFormSet(field, value) {
    if (!state.ruleForm) return;
    state.ruleForm[field] = value;
    return renderRuleForm();
  }

  document.addEventListener("change", function (event) {
    var target = event.target;
    if (!target || !target.closest) return;
    if (target.closest("[data-rules-search]")) {
      var kind = target.getAttribute("data-rules-search");
      if (kind === "groups") state.groupSearch = target.value;
      else state.ruleSearch = target.value;
      return renderRulesRoot();
    }
    var form = target.closest("[data-rule-form]");
    if (form && state.ruleForm) {
      var name = form.getAttribute("data-rule-form");
      if (name === "scope") {
        // Область и уровень — одно значение вида «где:уровень».
        var pair = String(form.value).split(":");
        state.ruleForm.scope_kind = pair[0];
        state.ruleForm.level = pair[1];
        if (pair[0] === "campaign") ensureCampaigns();
        else state.ruleForm.campaign_external_id = "";
        return renderRuleForm();
      }
      if (name === "action") {
        // «Изменить бюджет адсета/кампании» — два пункта одного действия.
        var actionValue = String(form.value);
        if (actionValue.indexOf("change_budget:") === 0) {
          state.ruleForm.action = "change_budget";
          state.ruleForm.level = actionValue.split(":")[1];
        } else {
          state.ruleForm.action = actionValue;
          if (actionValue === "change_bid") state.ruleForm.level = "adset";
        }
        return renderRuleForm();
      }
      state.ruleForm[name] = form.type === "checkbox" ? form.checked : form.value;
      if (form.tagName !== "SELECT" && form.type !== "checkbox") {
        return refreshRuleSummary();
      }
      return renderRuleForm();
    }
    var condMetric = target.closest("[data-rule-cond-metric]");
    if (condMetric && state.ruleForm) {
      var metricRow = state.ruleForm.conditions[
        Number(condMetric.getAttribute("data-rule-cond-metric"))];
      metricRow.metric = condMetric.value;
      // Оператор из числового набора не годится тексту и наоборот — иначе
      // сервер вернёт 422 уже на сохранении.
      if (ruleOperators(metricRow.metric).indexOf(metricRow.operator) < 0) {
        metricRow.operator = isTextMetric(metricRow.metric) ? "in" : "gt";
      }
      return renderRuleForm();
    }
    var condOp = target.closest("[data-rule-cond-operator]");
    if (condOp && state.ruleForm) {
      state.ruleForm.conditions[Number(condOp.getAttribute("data-rule-cond-operator"))]
        .operator = condOp.value;
      return renderRuleForm();
    }
    var interval = target.closest("[data-rule-int]");
    if (interval && state.ruleForm) {
      var index = Number(interval.getAttribute("data-rule-int"));
      var part = interval.getAttribute("data-rule-int-part");
      state.ruleForm.schedule_intervals[index][part] = interval.value;
      return renderRuleForm();
    }
    var gf = target.closest ? target.closest("[data-group-form]") : null;
    if (gf && state.groupForm) {
      state.groupForm[gf.getAttribute("data-group-form")] = gf.value;
      return;
    }
    var gpick = target.closest ? target.closest("[data-group-rule-pick]") : null;
    if (gpick && state.groupForm) {
      state.groupForm.rule_ids = state.groupForm.rule_ids || [];
      var ruleId = gpick.getAttribute("data-group-rule-pick");
      var at = state.groupForm.rule_ids.indexOf(ruleId);
      if (gpick.checked && at < 0) state.groupForm.rule_ids.push(ruleId);
      if (!gpick.checked && at >= 0) state.groupForm.rule_ids.splice(at, 1);
      return renderRuleForm();
    }
  });

  /* Текстовые поля обновляют состояние без перерисовки: иначе каретка прыгала
     бы в конец на каждом введённом символе. Сводку внизу формы обновляем
     точечно — она единственное, что от них зависит. */
  document.addEventListener("input", function (event) {
    var target = event.target;
    if (!target || !target.closest) return;
    var valueField = target.closest("[data-rule-cond-value]");
    if (valueField && state.ruleForm) {
      var index = Number(valueField.getAttribute("data-rule-cond-value"));
      if (state.ruleForm.conditions[index]) {
        state.ruleForm.conditions[index].value = valueField.value;
      }
      return refreshRuleSummary();
    }
    var form = target.closest("[data-rule-form]");
    if (form && state.ruleForm && form.type !== "checkbox" && form.tagName !== "SELECT") {
      state.ruleForm[form.getAttribute("data-rule-form")] = form.value;
      return refreshRuleSummary();
    }
    var gf = target.closest("[data-group-form]");
    if (gf && state.groupForm && !gf.multiple) {
      state.groupForm[gf.getAttribute("data-group-form")] = gf.value;
    }
  });

  /* Esc закрывает форму правила — тот же жест, что и в остальных модалках. */
  document.addEventListener("keydown", function (event) {
    if (event.key !== "Escape") return;
    if (!state.ruleForm && !state.groupForm) return;
    closeRuleEditor();
  });

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

  /* ---------- модалка правила ----------
   *
   * Форма живёт в отдельном узле, а не внутри карточки со списком: перерисовка
   * таблицы больше не роняет открытую форму, а прокрутка её середины
   * переживает любое изменение полей. Шапка и подвал закреплены — «Сохранить»
   * не уезжает под нижний край, сколько бы условий ни добавили.
   */

  function renderRuleForm() {
    var host = byId("metaRuleFormRoot");
    if (!host) return;
    // Прокрутку страницы трогаем только на самом открытии и закрытии: функция
    // вызывается при каждой перерисовке списка, и безусловный сброс снимал бы
    // замок, поставленный чужой модалкой.
    if (!state.ruleForm && !state.groupForm) {
      if (host.innerHTML) {
        host.innerHTML = "";
        document.body.style.overflow = "";
      }
      return;
    }
    var body = host.querySelector(".rule-modal__body");
    var offset = body ? body.scrollTop : 0;
    if (!host.innerHTML) document.body.style.overflow = "hidden";
    host.innerHTML = state.ruleForm ? ruleFormModal() : groupFormModal();
    var next = host.querySelector(".rule-modal__body");
    if (next && offset) next.scrollTop = offset;
    if (state.ruleFormFresh) {
      state.ruleFormFresh = false;
      var focus = host.querySelector("[data-rule-autofocus]");
      if (focus) focus.focus();
    }
  }

  /* Сводка обновляется без перерисовки всей формы: её пересчитывают на каждый
     введённый символ, и переклеивать ради этого DOM было бы расточительно. */
  function refreshRuleSummary() {
    var host = byId("metaRuleFormRoot");
    if (!host || !state.ruleForm) return;
    var summary = host.querySelector("[data-rule-summary]");
    if (summary) summary.innerHTML = ruleSummaryHtml(state.ruleForm);
    var save = host.querySelector("[data-rules-save]");
    if (save) save.disabled = !!state.ruleFormSaving;
  }

  var RULE_LEVEL_PLURAL = {
    ad: "объявления", adset: "адсеты", campaign: "кампании"
  };

  /* Правило предложением: по семи выпадающим спискам не видно, что именно
     произойдёт, а здесь это одна фраза, которая меняется на глазах. */
  function ruleSummaryHtml(f) {
    var reference = state.reference || {};
    var where = f.scope_kind === "campaign"
      ? "в кампании «" + (campaignName(f.campaign_external_id) || "не выбрана") + "»"
      : (f.account_id
        ? "в кабинете «" + (accountName(f.account_id) || "—") + "»"
        : "во всех кабинетах");
    var conditions = f.conditions.length
      ? f.conditions.map(function (item) {
        return escapeHtml(conditionText(item));
      }).join(" <b>и</b> ")
      : "<b>условий нет — на всех подряд</b>";
    return "Проверяем " + escapeHtml(RULE_LEVEL_PLURAL[f.level] || f.level) +
      " " + escapeHtml(where) + " со статусом «" +
      escapeHtml((reference.rule_statuses || {})[f.entity_status] || f.entity_status) +
      "» за период «" +
      escapeHtml((reference.rule_windows || {})[f.window] || f.window) + "». " +
      "Если " + conditions + " — <b>" + escapeHtml(ruleActionLabel(f)) + "</b>. " +
      "Объекты с расходом ниже " + money(f.min_spend || 0) + " не трогаем.";
  }

  function accountName(id) {
    var row = referenceAccounts().filter(function (item) { return item.id === id; })[0];
    return row ? row.name : "";
  }

  var RULE_CURRENCIES = ("USD,EUR,GBP,PLN,UAH,KZT,TRY,BRL,INR,AED,SAR,NGN,BDT,PHP,PKR," +
    "MXN,CLP,COP,PEN,NZD,AUD,CAD,CHF,SEK,NOK,DKK,CZK,HUF,RON,HKD,SGD,KRW,JPY,CNY").split(",");

  function ruleFormModal() {
    var f = state.ruleForm;
    var title = f.id ? "Правило «" + escapeHtml(f.name || "без названия") + "»" : "Новое правило";
    return '<div class="rule-modal">' +
      '<div class="rule-modal__box" role="dialog" aria-modal="true" ' +
      'aria-label="Настройка автоправила">' +
      '<div class="rule-modal__head"><div style="min-width:0">' +
      '<h2 style="font-family:\'Alumni Sans\',Inter,sans-serif;font-size:20px;font-weight:700;' +
      'letter-spacing:-.3px">' + title + "</h2>" +
      '<div style="font-size:12px;color:#9B9292;font-weight:600;margin-top:4px">' +
      "Правило смотрит на наши числа: расход из Meta, доход из Keitaro</div></div>" +
      '<button type="button" data-rules-close aria-label="Закрыть" style="width:34px;' +
      'height:34px;flex-shrink:0;border:0;border-radius:10px;background:#F7F4F4;' +
      'color:#6A6161;font-size:17px">×</button></div>' +
      '<div class="rule-modal__body">' +
      ruleCardBasics(f) +
      ruleCardScope(f) +
      ruleCardSchedule(f) +
      ruleCardConditions(f) +
      ruleCardAction(f) +
      "</div>" +
      '<div class="rule-modal__foot">' +
      (state.ruleFormError
        ? '<div class="rule-error" style="flex:1;min-width:220px">' +
          escapeHtml(state.ruleFormError) + "</div>"
        : "") +
      '<div style="display:flex;gap:10px;margin-left:auto">' +
      '<button type="button" class="meta-action" style="height:42px;padding:0 18px" ' +
      "data-rules-close>Отмена</button>" +
      '<button type="button" class="meta-action meta-action--primary" ' +
      'style="height:42px;padding:0 20px" data-rules-save' +
      (state.ruleFormSaving ? " disabled" : "") + ">" +
      (state.ruleFormSaving ? "Сохраняем…" : f.id ? "Сохранить" : "Создать правило") +
      "</button></div></div></div></div>";
  }

  function ruleCardBasics(f) {
    return '<div class="meta-card"><div class="meta-card__title">Настройки</div>' +
      '<div class="rule-grid">' +
      '<label class="meta-field rule-grid--wide"><span>Название правила</span>' +
      '<input class="meta-control" data-rule-autofocus placeholder="Например: стоп при ROI ниже нуля" ' +
      'data-rule-form="name" value="' + escapeHtml(f.name || "") + '"></label>' +
      '<div class="rule-grid--wide"><label class="meta-switch">' +
      '<input type="checkbox" data-rule-form="convert_currency"' +
      (f.convert_currency ? " checked" : "") + '><span class="meta-switch__box"></span>' +
      "<span>Считать пороги в одной валюте<span class=\"meta-switch__hint\">" +
      "Кабинеты бывают в разных валютах — суммы приводятся к выбранной" +
      "</span></span></label>" +
      (f.convert_currency
        ? '<select class="meta-control meta-select" style="width:180px;margin-top:10px" ' +
          'data-rule-form="currency">' + RULE_CURRENCIES.map(function (code) {
            return '<option value="' + code + '"' + (f.currency === code ? " selected" : "") +
              ">" + code + "</option>";
          }).join("") + "</select>"
        : "") + "</div></div></div>";
  }

  function ruleCardScope(f) {
    var campaigns = "";
    if (f.scope_kind === "campaign") {
      campaigns = '<label class="meta-field rule-grid--wide"><span>Кампания</span>' +
        '<select class="meta-control meta-select" data-rule-form="campaign_external_id">' +
        '<option value="">' +
        (state.campaignsLoading ? "Загружаем кампании…" : "Выберите кампанию") + "</option>" +
        (state.campaigns || []).map(function (item) {
          return '<option value="' + escapeHtml(item.external_id) + '"' +
            (item.external_id === f.campaign_external_id ? " selected" : "") + ">" +
            escapeHtml(item.name + (item.account_name ? " · " + item.account_name : "")) +
            "</option>";
        }).join("") + "</select></label>";
    }
    return '<div class="meta-card"><div class="meta-card__title">Что проверяем</div>' +
      '<div class="rule-grid">' +
      '<label class="meta-field"><span>Область</span>' +
      '<select class="meta-control meta-select" data-rule-form="scope">' +
      RULE_SCOPES.map(function (row) {
        return '<option value="' + row[0] + '"' +
          (row[0] === f.scope_kind + ":" + f.level ? " selected" : "") + ">" +
          escapeHtml(row[1]) + "</option>";
      }).join("") + "</select>" +
      '<span style="display:block;font-size:11px;color:#9B9292;font-weight:600;margin-top:5px">' +
      "Действие применится к объекту этого уровня</span></label>" +
      '<label class="meta-field"><span>Какие статусы брать</span>' +
      '<select class="meta-control meta-select" data-rule-form="entity_status">' +
      dictOptions((state.reference || {}).rule_statuses, f.entity_status)
        .map(function (option) {
          return '<option value="' + escapeHtml(option.value) + '"' +
            (option.selected ? " selected" : "") + ">" + escapeHtml(option.label) +
            "</option>";
        }).join("") + "</select></label>" +
      campaigns +
      '<label class="meta-field"><span>Период статистики</span>' +
      '<select class="meta-control meta-select" data-rule-form="window">' +
      dictOptions((state.reference || {}).rule_windows, f.window).map(function (option) {
        return '<option value="' + escapeHtml(option.value) + '"' +
          (option.selected ? " selected" : "") + ">" + escapeHtml(option.label) + "</option>";
      }).join("") + "</select></label>" +
      "</div></div>";
  }

  function ruleCardSchedule(f) {
    var block = "";
    if (f.schedule_kind === "custom") {
      var days = f.schedule_days || [];
      var dayNames = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"];
      block = '<div style="margin-top:14px">' +
        '<div class="meta-card__sub">Дни недели</div>' +
        '<div style="display:flex;gap:6px;flex-wrap:wrap">' +
        dayNames.map(function (name, index) {
          var on = days.indexOf(index + 1) >= 0;
          return '<button type="button" class="rule-day' + (on ? " rule-day--on" : "") +
            '" data-rule-day="' + (index + 1) + '">' + name + "</button>";
        }).join("") +
        '<button type="button" class="rule-link" style="margin-left:6px" data-rule-days-all>' +
        (days.length === 7 ? "Снять все" : "Выбрать все") + "</button></div>" +
        '<div class="meta-card__sub" style="margin-top:16px">Часы</div>' +
        (f.schedule_intervals.length
          ? f.schedule_intervals.map(ruleIntervalHtml).join("")
          : '<div style="font-size:11.5px;color:#9B9292;font-weight:600">' +
            "Интервалов нет — правило работает круглые сутки в выбранные дни</div>") +
        '<button type="button" class="meta-action" style="margin-top:12px" data-rule-int-add>' +
        "+ Интервал времени</button></div>";
    }
    return '<div class="meta-card"><div class="meta-card__title">Когда проверять</div>' +
      '<div class="rule-grid">' +
      '<label class="meta-field"><span>Тип правила</span>' +
      '<select class="meta-control meta-select" data-rule-form="kind_rule">' +
      '<option value="schedule">По расписанию</option></select></label>' +
      '<label class="meta-field"><span>Частота</span>' +
      '<select class="meta-control meta-select" data-rule-form="schedule_kind">' +
      RULE_FREQUENCIES.map(function (row) {
        return '<option value="' + row[0] + '"' +
          (row[0] === f.schedule_kind ? " selected" : "") + ">" + escapeHtml(row[1]) +
          "</option>";
      }).join("") + "</select></label></div>" + block + "</div>";
  }

  function ruleIntervalHtml(item, index) {
    return '<div class="rule-int"><span>с</span>' +
      '<select class="meta-control meta-select" style="width:82px" data-rule-int="' + index +
      '" data-rule-int-part="begin_h">' + ruleHourOptions(item.begin_h) + "</select>" +
      '<select class="meta-control meta-select" style="width:82px" data-rule-int="' + index +
      '" data-rule-int-part="begin_m">' + ruleMinuteOptions(item.begin_m) + "</select>" +
      "<span>до</span>" +
      '<select class="meta-control meta-select" style="width:82px" data-rule-int="' + index +
      '" data-rule-int-part="end_h">' + ruleHourOptions(item.end_h) + "</select>" +
      '<select class="meta-control meta-select" style="width:82px" data-rule-int="' + index +
      '" data-rule-int-part="end_m">' + ruleMinuteOptions(item.end_m) + "</select>" +
      '<button type="button" class="meta-action meta-action--danger" data-rule-int-drop="' +
      index + '" aria-label="Убрать интервал">Убрать</button></div>';
  }

  function ruleCardConditions(f) {
    var rows = f.conditions.map(function (item, index) {
      var text = isTextMetric(item.metric);
      return '<div class="rule-cond">' +
        '<select class="meta-control meta-select" aria-label="Метрика" ' +
        'data-rule-cond-metric="' + index + '">' + metricOptionsHtml(item.metric) +
        "</select>" +
        '<select class="meta-control meta-select" aria-label="Оператор" ' +
        'data-rule-cond-operator="' + index + '">' +
        ruleOperators(item.metric).map(function (code) {
          return '<option value="' + code + '"' +
            (code === item.operator ? " selected" : "") + ">" +
            escapeHtml(RULE_OPERATOR_LABELS[code] || code) + "</option>";
        }).join("") + "</select>" +
        '<input class="meta-control" style="padding:0 12px"' +
        (text ? "" : ' type="number" step="any"') + ' aria-label="Значение" placeholder="' +
        (text ? "текст" : "число") + '" value="' +
        escapeHtml(item.value == null ? "" : String(item.value)) +
        '" data-rule-cond-value="' + index + '">' +
        '<button type="button" class="meta-action meta-action--danger" ' +
        'data-rule-cond-drop="' + index + '" aria-label="Убрать условие">×</button></div>';
    }).join("");
    return '<div class="meta-card"><div class="meta-card__title">Условия</div>' +
      '<div style="font-size:11.5px;color:#9B9292;font-weight:600;line-height:1.6">' +
      "Соединяются «и»: правило сработает, когда выполнены все сразу.</div>" +
      rows +
      (f.conditions.length
        ? ""
        : '<div class="meta-note meta-note--warn" style="margin-top:12px">' +
          "Условий нет — правило сработает на всех объектах области.</div>") +
      '<div style="display:flex;align-items:center;gap:14px;margin-top:14px;flex-wrap:wrap">' +
      '<button type="button" class="meta-action" data-rule-cond-add>+ Условие</button>' +
      (f.conditions.length
        ? '<button type="button" class="rule-link" data-rule-cond-empty>' +
          "Правило без условий</button>"
        : "") + "</div></div>";
  }

  function ruleCardAction(f) {
    var extra = "";
    if (f.action === "change_budget" || f.action === "change_bid") {
      var unit = f.action_mode === "pct" ? "%" : (f.convert_currency ? f.currency : "валюта кабинета");
      extra = '<div style="display:flex;gap:12px;align-items:flex-end;flex-wrap:wrap;' +
        'margin-top:14px">' +
        (f.action === "change_budget"
          ? '<div class="meta-field"><span>Какой бюджет</span>' +
            segRule("rule-form-budget", [
              { v: "daily", l: "Дневной" }, { v: "lifetime", l: "На весь срок" }
            ], f.budget_kind || "daily") + "</div>"
          : "") +
        '<div class="meta-field"><span>Куда</span>' +
        segRule("rule-form-sign", [{ v: "plus", l: "+" }, { v: "minus", l: "−" }],
          f.action_sign) + "</div>" +
        '<label class="meta-field" style="width:130px"><span>Как считать</span>' +
        '<select class="meta-control meta-select" data-rule-form="action_mode">' +
        '<option value="pct"' + (f.action_mode === "pct" ? " selected" : "") + ">%</option>" +
        '<option value="sum"' + (f.action_mode === "sum" ? " selected" : "") +
        ">Сумма</option></select></label>" +
        '<label class="meta-field" style="width:150px"><span>На сколько, ' +
        escapeHtml(unit) + '</span><input class="meta-control" type="number" min="0" ' +
        'step="0.01" data-rule-form="action_value" value="' +
        escapeHtml(String(f.action_value || "")) + '"></label>' +
        '<label class="meta-field" style="width:190px"><span>' +
        (f.action === "change_bid" ? "Потолок ставки" : "Потолок бюджета") + "</span>" +
        '<input class="meta-control" type="number" min="0" step="0.01" placeholder="без ограничения" ' +
        'data-rule-form="action_max" value="' + escapeHtml(String(f.action_max || "")) +
        '"></label></div>';
    }
    var warn = "";
    if (f.action === "change_budget" && f.level === "ad") {
      warn = "У объявления нет своего бюджета — выберите область с адсетами или кампаниями.";
    }
    if (f.action === "change_bid" && f.level !== "adset") {
      warn = "Ставка задаётся на адсете — выберите область с адсетами.";
    }
    return '<div class="meta-card"><div class="meta-card__title">Что делать</div>' +
      '<label class="meta-field" style="max-width:360px"><span>Действие</span>' +
      '<select class="meta-control meta-select" data-rule-form="action">' +
      RULE_ACTION_OPTIONS.map(function (row) {
        return '<option value="' + row[0] + '"' + (f.action === row[0] ? " selected" : "") +
          ">" + escapeHtml(row[1]) + "</option>";
      }).join("") + "</select></label>" + extra +
      (warn
        ? '<div class="meta-note meta-note--warn" style="margin-top:14px">' +
          escapeHtml(warn) + "</div>"
        : "") + "</div>";
  }

  function segRule(dataKey, options, selected) {
    return '<div class="meta-seg">' + options.map(function (option) {
      return '<button type="button" data-' + dataKey + '="' + option.v + '" class="' +
        (selected === option.v ? "meta-seg--on" : "") + '">' + option.l + "</button>";
    }).join("") + "</div>";
  }

  /* Собственные имена: в файле уже есть `hourOptions(host, values, selected)`,
     который заполняет готовый `<select>` для окна фиксации расхода. Одинаковые
     имена в одной области видимости молча затирали друг друга. */
  function ruleHourOptions(selected) {
    var rows = [];
    for (var h = 0; h < 24; h += 1) {
      var value = String(h < 10 ? "0" + h : h);
      rows.push('<option value="' + value + '"' + (String(selected) === value ? " selected" : "") +
        ">" + value + "</option>");
    }
    return rows.join("");
  }

  function ruleMinuteOptions(selected) {
    var rows = [];
    for (var m = 0; m < 60; m += 5) {
      var value = String(m < 10 ? "0" + m : m);
      rows.push('<option value="' + value + '"' + (String(selected) === value ? " selected" : "") +
        ">" + value + "</option>");
    }
    if (String(selected).length && rows.join("").indexOf('value="' + selected + '"') < 0) {
      rows.unshift('<option value="' + escapeHtml(String(selected)) + '" selected>' +
        escapeHtml(String(selected)) + "</option>");
    }
    return rows.join("");
  }

  function groupFormModal() {
    var g = state.groupForm;
    return '<div class="rule-modal">' +
      '<div class="rule-modal__box" style="max-width:520px" role="dialog" aria-modal="true" ' +
      'aria-label="Группа правил">' +
      '<div class="rule-modal__head"><div style="min-width:0">' +
      '<h2 style="font-family:\'Alumni Sans\',Inter,sans-serif;font-size:20px;font-weight:700;' +
      'letter-spacing:-.3px">' + (g.id ? "Изменить группу" : "Новая группа правил") + "</h2>" +
      '<div style="font-size:12px;color:#9B9292;font-weight:600;margin-top:4px">' +
      "Группа собирает правила вместе — удобно, когда их много</div></div>" +
      '<button type="button" data-rules-close aria-label="Закрыть" style="width:34px;' +
      'height:34px;flex-shrink:0;border:0;border-radius:10px;background:#F7F4F4;' +
      'color:#6A6161;font-size:17px">×</button></div>' +
      '<div class="rule-modal__body">' +
      '<label class="meta-field"><span>Название группы</span>' +
      '<input class="meta-control" data-rule-autofocus data-group-form="name" value="' +
      escapeHtml(g.name || "") + '"></label>' +
      '<div class="meta-field"><span>Правила в группе · отмечено: ' +
      (g.rule_ids || []).length + '</span><div style="display:grid;gap:8px;margin-top:8px;' +
      'max-height:280px;overflow-y:auto;padding-right:4px">' +
      ((state.rules || []).length
        ? (state.rules || []).map(function (rule) {
          var on = (g.rule_ids || []).indexOf(rule.id) >= 0;
          return '<label style="display:flex;align-items:center;gap:10px;padding:8px 10px;' +
            'border:1px solid ' + (on ? "#B91414" : "#EBE6E6") + ';border-radius:10px;' +
            'background:' + (on ? "#FCF1F1" : "#fff") + ';cursor:pointer;font-size:12.5px;' +
            'font-weight:600;color:#3A3030">' +
            '<input type="checkbox" data-group-rule-pick="' + escapeHtml(rule.id) + '"' +
            (on ? " checked" : "") + ' style="width:16px;height:16px;accent-color:#B91414">' +
            '<span style="flex:1">' + escapeHtml(rule.name) + "</span>" +
            '<span style="font-size:11px;color:#9B9292;font-weight:700;white-space:nowrap">' +
            escapeHtml(ruleActionLabel(rule)) + "</span></label>";
        }).join("")
        : '<div class="meta-note">Правил пока нет — создайте их на вкладке «Правила FB».</div>') +
      "</div></div>" +
      (state.ruleFormError
        ? '<div class="rule-error">' + escapeHtml(state.ruleFormError) + "</div>"
        : "") + "</div>" +
      '<div class="rule-modal__foot"><div style="display:flex;gap:10px;margin-left:auto">' +
      '<button type="button" class="meta-action" style="height:42px;padding:0 18px" ' +
      "data-rules-close>Отмена</button>" +
      '<button type="button" class="meta-action meta-action--primary" ' +
      'style="height:42px;padding:0 20px" data-groups-save' +
      (state.ruleFormSaving ? " disabled" : "") + ">" +
      (state.ruleFormSaving ? "Сохраняем…" : "Сохранить") + "</button></div></div>" +
      "</div></div>";
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
      await loadRules({ quiet: true });
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
      await loadRules({ quiet: true });
    } catch (error) {
      notify({
        title: "Не удалось отметить события",
        message: error && error.message ? error.message : ""
      });
    }
  }


  /* ---------- комментарии: ручная чистка ----------
   *
   * Чистка ручная по замыслу: под рекламой висит и спам, и живые вопросы
   * клиентов, и отличать одно от другого автоматом — значит рано или поздно
   * стереть вопрос покупателя. Интерфейс поэтому оптимизирован не под «удалить
   * всё», а под быстрый просмотр: сообщение читается целиком, признаки (ссылка,
   * телефон, ответ в ветке) подсвечены, фильтры сужают список до подозрительного.
   *
   * И загрузка, и действия идут заданием в воркере — с паузой между вызовами:
   * пачка запросов к комментариям без пауз это самый быстрый способ получить
   * чекпоинт на аккаунте. Интерфейс показывает прогресс и умеет отменить.
   */

  state.comments = [];
  state.commentsTotal = 0;
  state.commentPosts = [];
  state.commentPicked = {};
  state.commentAccount = "";
  state.commentPost = "";
  state.commentFilters = { only_links: false, only_phones: false, only_replies: false };
  state.commentStatus = "visible";
  state.commentQuery = "";
  state.commentAuthor = "";
  state.commentAccess = null;
  state.commentAccessFor = "";
  state.commentJob = null;
  state.commentsStatus = "idle";
  state.commentsError = "";

  var COMMENTS_PAGE = 100;
  var commentsRequest = null;
  // Фильтры меняют запрос к серверу. Если во время загрузки поменяли ещё один,
  // просто отдать текущий промис нельзя: он уедет со старыми параметрами, и
  // второе изменение молча потеряется. Помечаем «надо перечитать» и повторяем
  // сразу после текущего запроса.
  var commentsDirty = false;
  var commentJobTimer = null;

  function commentLimits() {
    return ((state.reference || {}).comment_limits) || {};
  }

  async function loadComments(options) {
    var quiet = !!(options && options.quiet);
    if (commentsRequest) {
      commentsDirty = true;
      return commentsRequest;
    }
    if (!quiet && state.commentsStatus !== "ready") {
      state.commentsStatus = "loading";
      renderComments();
    }
    commentsDirty = false;
    commentsRequest = fetchComments();
    try {
      await commentsRequest;
    } finally {
      commentsRequest = null;
    }
    if (commentsDirty) return loadComments({ quiet: true });
  }

  async function fetchComments() {
    try {
      await loadReference();
      syncCommentAccount();
      if (!state.commentAccount) {
        state.commentsStatus = "ready";
        state.comments = [];
        state.commentPosts = [];
        state.commentPostsHint = "";
        renderComments();
        return;
      }
      var posts = await api.get(
        "/meta/comments/posts?account_id=" + encodeURIComponent(state.commentAccount)
      );
      state.commentPosts = (posts && posts.items) || [];
      state.commentPostsHint = (posts && posts.hint) || "";
      if (state.commentPost && !postById(state.commentPost)) state.commentPost = "";
      var page = await api.get("/meta/comments?" + commentQueryString());
      state.comments = (page && page.items) || [];
      state.commentsTotal = (page && page.total) || 0;
      // Выбор переживает перерисовку, но не должен указывать на строки, которых
      // в текущей выборке уже нет.
      var visible = {};
      state.comments.forEach(function (row) { visible[row.external_id] = true; });
      Object.keys(state.commentPicked).forEach(function (id) {
        if (!visible[id]) delete state.commentPicked[id];
      });
      await refreshCommentJob({ quiet: true });
      state.commentsStatus = "ready";
      state.commentsError = "";
    } catch (error) {
      state.commentsStatus = "error";
      state.comments = [];
      state.commentsTotal = 0;
      state.commentsError = error && error.message
        ? error.message : "Не удалось загрузить комментарии";
    }
    renderComments();
    // Проверка доступа — отдельным запросом и не блокирующая список: она ходит
    // в Meta и может думать несколько секунд. Делаем её один раз на кабинет:
    // это настоящий вызов Graph, и повторять его на каждую смену фильтра
    // означало бы утроить трафик там, где мы его как раз бережём.
    probeCommentAccess();
  }

  function syncCommentAccount() {
    var accounts = referenceAccounts();
    if (!accounts.length) {
      state.commentAccount = "";
      return;
    }
    var known = accounts.filter(function (row) { return row.id === state.commentAccount; });
    if (!known.length) state.commentAccount = accounts[0].id;
  }

  function commentQueryString() {
    var parts = [
      "account_id=" + encodeURIComponent(state.commentAccount),
      "status=" + encodeURIComponent(state.commentStatus),
      "limit=" + COMMENTS_PAGE
    ];
    if (state.commentPost) {
      parts.push("post_external_id=" + encodeURIComponent(state.commentPost));
    }
    if (state.commentQuery.trim()) {
      parts.push("query=" + encodeURIComponent(state.commentQuery.trim()));
    }
    if (state.commentAuthor.trim()) {
      parts.push("author=" + encodeURIComponent(state.commentAuthor.trim()));
    }
    Object.keys(state.commentFilters).forEach(function (key) {
      if (state.commentFilters[key]) parts.push(key + "=true");
    });
    return parts.join("&");
  }

  function postById(id) {
    return (state.commentPosts || []).filter(function (row) {
      return row.post_external_id === id;
    })[0];
  }

  async function probeCommentAccess(force) {
    if (!state.commentAccount) return;
    if (!force && state.commentAccessFor === state.commentAccount) return;
    state.commentAccessFor = state.commentAccount;
    try {
      state.commentAccess = await api.get(
        "/meta/comments/access?account_id=" + encodeURIComponent(state.commentAccount) +
        (state.commentPost ? "&post_external_id=" + encodeURIComponent(state.commentPost) : "")
      );
    } catch (error) {
      state.commentAccess = {
        ok: false,
        reason: error && error.message ? error.message : "Проверка доступа не удалась"
      };
      // Разовый сбой не должен запирать проверку до конца сессии.
      state.commentAccessFor = "";
    }
    renderCommentAccess();
  }

  /* --- отрисовка --- */

  function renderComments() {
    if (!byId("metaCommentsBody")) return;
    renderCommentControls();
    renderCommentAccess();
    renderCommentJob();
    renderCommentList();
  }

  function renderCommentControls() {
    var accounts = referenceAccounts();
    var accountSelect = byId("metaCommentsAccount");
    if (accountSelect) {
      accountSelect.innerHTML = accounts.length
        ? accounts.map(function (row) {
          return accountOptionHtml(row, state.commentAccount);
        }).join("")
        : '<option value="">Кабинетов нет</option>';
    }
    var postSelect = byId("metaCommentsPost");
    if (postSelect) {
      postSelect.innerHTML = '<option value="">Все посты кабинета</option>' +
        (state.commentPosts || []).map(function (row) {
          return '<option value="' + escapeHtml(row.post_external_id) + '"' +
            (row.post_external_id === state.commentPost ? " selected" : "") +
            ' data-hint="' + escapeHtml(row.post_external_id) + '">' +
            escapeHtml(row.title) +
            (row.source === "page_ad" ? " · рекламный пост страницы" : "") +
            " · " + row.comments + " комм." +
            (row.page_name ? " · " + escapeHtml(row.page_name) : "") +
            (row.active_ads ? "" : " (не крутится)") + "</option>";
        }).join("");
    }
    var query = byId("metaCommentsQuery");
    if (query && query.value !== state.commentQuery) query.value = state.commentQuery;
    var author = byId("metaCommentsAuthor");
    if (author && author.value !== state.commentAuthor) author.value = state.commentAuthor;
    var status = byId("metaCommentsStatus");
    if (status && status.value !== state.commentStatus) status.value = state.commentStatus;
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-cm-flag]"),
      function (button) {
        var key = button.getAttribute("data-cm-flag");
        button.classList.toggle("cm-chip--on", !!state.commentFilters[key]);
      }
    );
    var hint = byId("metaCommentsHint");
    if (hint) {
      var limits = commentLimits();
      hint.textContent = limits.pages_per_post
        ? "Загружаем до " + limits.pages_per_post * 100 + " последних комментариев на пост, " +
          "по " + (limits.delay_ms || 0) + " мс между запросами"
        : "";
    }
    var refresh = byId("metaCommentsRefresh");
    if (refresh) {
      var busy = !!(state.commentJob && isJobActive(state.commentJob));
      refresh.disabled = busy || !state.commentAccount;
      refresh.textContent = busy ? "Идёт загрузка…" : "Загрузить из Meta";
    }
  }

  function renderCommentAccess() {
    var host = byId("metaCommentsAccess");
    if (!host) return;
    var access = state.commentAccess;
    if (!access || access.ok) {
      host.style.display = "none";
      host.innerHTML = "";
      return;
    }
    host.style.display = "";
    host.innerHTML = '<div class="meta-note meta-note--warn">' +
      '<b style="display:block;margin-bottom:4px">Комментарии сейчас недоступны</b>' +
      escapeHtml(access.reason || "") +
      (access.auth_method_label
        ? '<div style="margin-top:6px;color:#857D7D">Способ подключения: ' +
          escapeHtml(access.auth_method_label) + "</div>"
        : "") + "</div>";
  }

  function isJobActive(job) {
    return job && (job.status === "queued" || job.status === "running");
  }

  function renderCommentJob() {
    var host = byId("metaCommentsJob");
    if (!host) return;
    var job = state.commentJob;
    if (!job) {
      host.style.display = "none";
      host.innerHTML = "";
      return;
    }
    var active = isJobActive(job);
    var percent = job.total ? Math.round((job.processed / job.total) * 100) : 0;
    var tone = job.status === "failed" ? "#C41616"
      : job.status === "cancelled" ? "#C9821F"
        : job.status === "done" ? "#0F9D58" : "#B91414";
    host.style.display = "";
    host.innerHTML = '<div class="cm-job"><div class="cm-job__head"><div style="min-width:0">' +
      '<div class="cm-job__title">' + escapeHtml(job.kind_label || job.kind) +
      ' · <span style="color:' + tone + '">' + escapeHtml(jobStatusLabel(job)) + "</span></div>" +
      '<div class="cm-job__meta">Обработано ' + job.processed + " из " + job.total +
      (job.succeeded ? " · успешно " + job.succeeded : "") +
      (job.failed ? " · с ошибкой " + job.failed : "") + "</div>" +
      (job.error
        ? '<div class="cm-job__meta" style="color:#C41616">' + escapeHtml(job.error) + "</div>"
        : "") + "</div>" +
      (active && state.canComments
        ? '<button type="button" class="meta-action meta-action--danger" data-cm-cancel="' +
          escapeHtml(job.id) + '"' + (job.cancel_requested ? " disabled" : "") + ">" +
          (job.cancel_requested ? "Останавливаем…" : "Остановить") + "</button>"
        : "") + "</div>" +
      (active
        ? '<div class="cm-job__bar"><span style="width:' + Math.max(percent, 4) + '%"></span></div>'
        : "") + "</div>";
  }

  function jobStatusLabel(job) {
    return {
      queued: "в очереди", running: "выполняется", done: "готово",
      failed: "ошибка", cancelled: "остановлено"
    }[job.status] || job.status;
  }

  function renderCommentList() {
    var host = byId("metaCommentsBody");
    if (!host) return;
    if (state.commentsStatus === "loading" || state.commentsStatus === "idle") {
      host.innerHTML = '<div class="rule-skeleton" aria-hidden="true">' +
        "<span></span><span></span><span></span><span></span></div>";
      renderCommentBulk();
      return renderCommentFoot();
    }
    if (state.commentsStatus === "error") {
      host.innerHTML = '<div class="rule-state rule-state--error">' +
        "<b>Комментарии не загрузились</b>" + escapeHtml(state.commentsError) +
        '<div style="margin-top:14px"><button type="button" class="meta-action" ' +
        "data-cm-retry>Повторить</button></div></div>";
      renderCommentBulk();
      return renderCommentFoot();
    }
    if (!state.comments.length) {
      host.innerHTML = '<div class="rule-state">' + emptyCommentsText() + "</div>";
      renderCommentBulk();
      return renderCommentFoot();
    }
    host.innerHTML = state.comments.map(commentRowHtml).join("");
    renderCommentBulk();
    renderCommentFoot();
  }

  function emptyCommentsText() {
    if (!state.commentAccount) {
      return "<b>Нет кабинетов</b>Подключите Meta Ads, чтобы читать комментарии.";
    }
    if (!(state.commentPosts || []).length) {
      if (state.commentPostsHint) {
        return "<b>Объявления динамические</b>" + escapeHtml(state.commentPostsHint);
      }
      return "<b>В кабинете нет постов</b>Комментарии живут под постом объявления. " +
        "Если объявления есть, запустите синхронизацию — id поста приезжает вместе с ними.";
    }
    if (hasCommentFilters()) {
      return "<b>Под фильтр ничего не попало</b>Снимите часть условий или " +
        "смените статус — возможно, всё уже вычищено.";
    }
    return "<b>Комментарии ещё не загружены</b>Нажмите «Загрузить из Meta» — " +
      "мы заберём свежие комментарии под постами этого кабинета.";
  }

  function hasCommentFilters() {
    return !!(state.commentQuery.trim() || state.commentAuthor.trim() ||
      state.commentFilters.only_links || state.commentFilters.only_phones ||
      state.commentFilters.only_replies || state.commentPost);
  }

  function commentRowHtml(row) {
    var picked = !!state.commentPicked[row.external_id];
    var gone = row.status === "deleted";
    var marks = [];
    if (row.has_link) marks.push('<span class="cm-mark">ссылка</span>');
    if (row.has_phone) marks.push('<span class="cm-mark">телефон</span>');
    if (row.parent_external_id) {
      marks.push('<span class="cm-mark cm-mark--reply">ответ в ветке</span>');
    }
    if (row.like_count) {
      marks.push('<span class="cm-mark cm-mark--muted">' + row.like_count + " ❤</span>");
    }
    if (row.reply_count) {
      marks.push('<span class="cm-mark cm-mark--muted">ответов: ' + row.reply_count + "</span>");
    }
    return '<div class="cm-row' + (picked ? " cm-row--picked" : "") +
      (gone ? " cm-row--gone" : "") + '">' +
      (gone || !state.canComments
        ? "<span></span>"
        : '<input type="checkbox" aria-label="Выбрать комментарий" data-cm-pick="' +
          escapeHtml(row.external_id) + '"' + (picked ? " checked" : "") + ">") +
      '<div style="min-width:0"><div class="cm-head">' +
      '<span class="cm-author">' + escapeHtml(row.author_name || "Без имени") + "</span>" +
      '<span class="cm-when">' + escapeHtml(formatMoment(row.created_time)) + "</span>" +
      commentStatusChip(row) + "</div>" +
      '<div class="cm-text">' + escapeHtml(row.message || "— без текста —") + "</div>" +
      (marks.length ? '<div class="cm-marks">' + marks.join("") + "</div>" : "") +
      "</div>" +
      '<div class="cm-actions">' +
      '<a class="meta-action" style="display:inline-flex;align-items:center" target="_blank" ' +
      'rel="noopener noreferrer" href="' + escapeHtml(row.permalink) + '">В Facebook</a>' +
      (state.canComments && !gone
        ? (row.status === "hidden"
          ? '<button type="button" class="meta-action" data-cm-one="unhide" data-cm-id="' +
            escapeHtml(row.external_id) + '">Вернуть</button>'
          : '<button type="button" class="meta-action" data-cm-one="hide" data-cm-id="' +
            escapeHtml(row.external_id) + '">Скрыть</button>') +
          '<button type="button" class="meta-action meta-action--danger" data-cm-one="delete" ' +
          'data-cm-id="' + escapeHtml(row.external_id) + '">Удалить</button>'
        : "") + "</div></div>";
  }

  function commentStatusChip(row) {
    if (row.status === "visible") return "";
    var tone = row.status === "deleted"
      ? "color:#B91414;background:#FCF1F1"
      : "color:#6A5A28;background:#FFF9E9";
    return '<span class="meta-chip" style="' + tone + '">' +
      escapeHtml(row.status_label || row.status) + "</span>";
  }

  function renderCommentBulk() {
    var host = byId("metaCommentsBulk");
    if (!host) return;
    var ids = Object.keys(state.commentPicked);
    if (!ids.length || !state.canComments) {
      host.style.display = "none";
      host.innerHTML = "";
      return;
    }
    host.style.display = "";
    host.innerHTML = '<span class="cm-bulk__count">Выбрано: ' + ids.length + "</span>" +
      '<button type="button" class="meta-action" data-cm-bulk="hide">Скрыть</button>' +
      '<button type="button" class="meta-action" data-cm-bulk="unhide">Вернуть</button>' +
      '<button type="button" class="meta-action meta-action--danger" data-cm-bulk="delete">' +
      "Удалить навсегда</button>" +
      '<button type="button" class="meta-action" data-cm-clear>Снять выбор</button>' +
      '<div style="flex:1"></div>' +
      '<span style="font-size:11.5px;color:#857D7D;font-weight:600">' +
      "Обработка идёт по одному, с паузой — так безопаснее для аккаунта</span>";
  }

  function renderCommentFoot() {
    var host = byId("metaCommentsFoot");
    if (!host) return;
    var shown = state.comments.length;
    host.innerHTML = "<span>Показано: " + shown +
      (state.commentsTotal > shown ? " из " + state.commentsTotal : "") + "</span>" +
      (state.commentsTotal > shown
        ? "<span>Сузьте фильтры, чтобы увидеть остальные</span>"
        : '<span>Удалённые остаются в CRM вместе с текстом — их видно в статусе «Удалённые»</span>');
  }

  /* --- действия --- */

  async function startCommentFetch() {
    if (!state.commentAccount) return;
    try {
      var job = await api.post("/meta/comments/fetch", {
        account_id: state.commentAccount,
        posts: state.commentPost ? [state.commentPost] : [],
        /* Берём все посты кабинета: у остановленных кампаний комменты
           продолжают приходить, а «только активные» их молча пропускал —
           кабинет выглядел пустым, хотя коммент под постом был. */
        active_only: false
      });
      state.commentJob = job;
      renderComments();
      watchCommentJob();
    } catch (error) {
      showFailure(error);
    }
  }

  async function runCommentAction(action, ids) {
    if (!ids.length) return;
    var limits = commentLimits();
    if (limits.max_per_job && ids.length > limits.max_per_job) {
      return notify({
        title: "Слишком много за раз",
        message: "За один заход обрабатываем не больше " + limits.max_per_job +
          " комментариев. Сузьте выборку."
      });
    }
    var labels = { hide: "Скрыть", unhide: "Вернуть", delete: "Удалить" };
    var confirmed = await askConfirm(
      action === "delete"
        ? {
          title: "Удалить " + ids.length + " комм. навсегда?",
          message: "Meta не возвращает удалённые комментарии. В CRM останется их " +
            "текст и автор, но в Facebook их больше не будет — и восстановить их " +
            "нельзя ни нам, ни поддержке Meta.",
          confirmLabel: "Удалить навсегда",
          danger: true
        }
        : {
          title: labels[action] + " " + ids.length + " комм.?",
          message: action === "hide"
            ? "Скрытый комментарий останется виден автору и его друзьям, но исчезнет " +
              "для остальных. Действие обратимо."
            : "Комментарии снова увидят все.",
          confirmLabel: labels[action]
        }
    );
    if (!confirmed) return;
    try {
      var job = await api.post("/meta/comments/action", {
        account_id: state.commentAccount,
        action: action,
        comments: ids
      });
      state.commentJob = job;
      state.commentPicked = {};
      renderComments();
      watchCommentJob();
    } catch (error) {
      showFailure(error);
    }
  }

  async function cancelCommentJob(id) {
    try {
      state.commentJob = await api.post("/meta/comments/jobs/" + id + "/cancel", {});
      renderCommentJob();
    } catch (error) {
      showFailure(error);
    }
  }

  async function refreshCommentJob(options) {
    if (!state.commentAccount) return;
    try {
      var page = await api.get(
        "/meta/comments/jobs?limit=1&account_id=" + encodeURIComponent(state.commentAccount)
      );
      state.commentJob = ((page && page.items) || [])[0] || null;
    } catch (error) {
      if (!(options && options.quiet)) state.commentJob = null;
    }
  }

  /* Опрос, пока задание живо. Тика в две секунды достаточно: между вызовами
     Meta всё равно стоит пауза, и чаще прогресс просто не меняется. */
  function watchCommentJob() {
    if (commentJobTimer) window.clearTimeout(commentJobTimer);
    if (!isJobActive(state.commentJob)) return;
    commentJobTimer = window.setTimeout(async function () {
      commentJobTimer = null;
      var id = state.commentJob && state.commentJob.id;
      if (!id) return;
      try {
        state.commentJob = await api.get("/meta/comments/jobs/" + id);
      } catch (error) {
        return;
      }
      renderCommentJob();
      renderCommentControls();
      if (isJobActive(state.commentJob)) return watchCommentJob();
      // Задание закончилось — список надо перечитать: статусы изменились.
      await loadComments({ quiet: true });
    }, 2000);
  }


  /* Поиск поста по ссылке. Объявление может не попасть в список по трём
     причинам: оно создано только что и ещё не синхронизировано, лежит в другом
     кабинете, или у его креатива нет собственного поста. Во всех трёх случаях
     ссылка на пост решает вопрос напрямую. */
  async function resolveCommentRef() {
    var field = byId("metaCommentsRef");
    var host = byId("metaCommentsRefResult");
    if (!field || !host || !state.commentAccount) return;
    var ref = field.value.trim();
    if (!ref) {
      host.style.display = "none";
      return;
    }
    host.style.display = "";
    host.className = "cm-lookup__result";
    host.textContent = "Спрашиваем Meta…";
    var found;
    try {
      found = await api.get(
        "/meta/comments/resolve?account_id=" + encodeURIComponent(state.commentAccount) +
        "&ref=" + encodeURIComponent(ref)
      );
    } catch (error) {
      found = { ok: false, reason: error && error.message ? error.message : "Не вышло" };
    }
    state.commentRef = found;
    if (!found.ok) {
      host.className = "cm-lookup__result cm-lookup__result--bad";
      host.innerHTML = escapeHtml(found.reason || "Пост не найден");
      return;
    }
    host.className = "cm-lookup__result cm-lookup__result--ok";
    host.innerHTML = "Пост " + escapeHtml(found.post_external_id) +
      " · комментариев на первой странице: " + found.comments +
      (found.via ? " · распознано как " + escapeHtml(found.via) : "") +
      (found.known
        ? ""
        : " · синхронизация о нём ещё не знает — загрузить всё равно можно") +
      ' <button type="button" class="meta-action" style="margin-left:8px" ' +
      'data-cm-fetch-ref>Загрузить комментарии этого поста</button>';
  }

  async function fetchResolvedPost() {
    var found = state.commentRef;
    if (!found || !found.ok) return;
    try {
      var job = await api.post("/meta/comments/fetch", {
        account_id: state.commentAccount,
        posts: [found.post_external_id],
        active_only: false
      });
      state.commentJob = job;
      state.commentPost = found.post_external_id;
      renderComments();
      watchCommentJob();
    } catch (error) {
      showFailure(error);
    }
  }

  function bindComments() {
    var account = byId("metaCommentsAccount");
    if (!account) return;
    account.addEventListener("change", function () {
      state.commentAccount = account.value;
      state.commentPost = "";
      state.commentPicked = {};
      state.commentAccess = null;
      state.commentAccessFor = "";
      state.commentsStatus = "loading";
      loadComments().catch(showFailure);
    });
    byId("metaCommentsPost").addEventListener("change", function (event) {
      state.commentPost = event.target.value;
      state.commentPicked = {};
      loadComments({ quiet: true }).catch(showFailure);
    });
    byId("metaCommentsStatus").addEventListener("change", function (event) {
      state.commentStatus = event.target.value;
      loadComments({ quiet: true }).catch(showFailure);
    });
    var searchTimer = null;
    function debouncedSearch() {
      if (searchTimer) window.clearTimeout(searchTimer);
      // Поиск уходит на сервер: ждём, пока человек допечатает.
      searchTimer = window.setTimeout(function () {
        loadComments({ quiet: true }).catch(showFailure);
      }, 300);
    }
    byId("metaCommentsQuery").addEventListener("input", function (event) {
      state.commentQuery = event.target.value;
      debouncedSearch();
    });
    byId("metaCommentsAuthor").addEventListener("input", function (event) {
      state.commentAuthor = event.target.value;
      debouncedSearch();
    });
    byId("metaCommentsRefresh").addEventListener("click", function () {
      startCommentFetch();
    });
    byId("metaCommentsResolve").addEventListener("click", resolveCommentRef);
    byId("metaCommentsRef").addEventListener("keydown", function (event) {
      if (event.key === "Enter") {
        event.preventDefault();
        resolveCommentRef();
      }
    });
    byId("metaTabComments").addEventListener("click", function (event) {
      var target = event.target.closest ? event.target : null;
      if (!target) return;
      var flag = target.closest("[data-cm-flag]");
      if (flag) {
        var key = flag.getAttribute("data-cm-flag");
        state.commentFilters[key] = !state.commentFilters[key];
        return loadComments({ quiet: true }).catch(showFailure);
      }
      var pick = target.closest("[data-cm-pick]");
      if (pick) {
        var id = pick.getAttribute("data-cm-pick");
        if (pick.checked) state.commentPicked[id] = true;
        else delete state.commentPicked[id];
        var row = pick.closest(".cm-row");
        if (row) row.classList.toggle("cm-row--picked", !!pick.checked);
        return renderCommentBulk();
      }
      if (target.closest("[data-cm-clear]")) {
        state.commentPicked = {};
        return renderCommentList();
      }
      var bulk = target.closest("[data-cm-bulk]");
      if (bulk) {
        return runCommentAction(
          bulk.getAttribute("data-cm-bulk"), Object.keys(state.commentPicked)
        );
      }
      var one = target.closest("[data-cm-one]");
      if (one) {
        return runCommentAction(
          one.getAttribute("data-cm-one"), [one.getAttribute("data-cm-id")]
        );
      }
      var cancel = target.closest("[data-cm-cancel]");
      if (cancel) return cancelCommentJob(cancel.getAttribute("data-cm-cancel"));
      if (target.closest("[data-cm-fetch-ref]")) return fetchResolvedPost();
      if (target.closest("[data-cm-retry]")) return loadComments().catch(showFailure);
    });
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

  var SPINNER = '<span class="meta-spin" aria-hidden="true"></span>';

  /* Строка ожидания с кружком: браузер Meta поднимается десятками секунд, и без
     этого экран выглядит замершим. */
  function sessionBusy(scope, text) {
    sessionStatus(scope, SPINNER + escapeHtml(text));
  }

  /* Ожидание токена живёт обещаниями на самой сессии: «Далее» ждёт тот же
     браузер, который мог запустить и человек кнопкой, а закрытие сессии или
     ошибка обрывают ожидание — иначе кнопка ждала бы токен, которого уже
     никто не принесёт. */
  function sessionWait() {
    return new Promise(function (resolve, reject) {
      var session = state.session;
      if (!session || !session.sessionId) {
        reject(new Error("Браузер не запущен"));
        return;
      }
      if (session.token) {
        resolve(session.token);
        return;
      }
      session.waiters = session.waiters || [];
      session.waiters.push({ resolve: resolve, reject: reject });
    });
  }

  function sessionSettle(session, error, token) {
    var waiters = (session && session.waiters) || [];
    if (session) session.waiters = [];
    waiters.forEach(function (waiter) {
      if (error) waiter.reject(error);
      else waiter.resolve(token);
    });
  }

  function sessionTokenPreview(token) {
    return escapeHtml(token.slice(0, 14)) + "…" +
      '<span style="color:#9B9292"> (' + token.length + " симв.)</span>";
  }

  function setSessionButtons(scope, running) {
    // В мастере отдельной кнопки запуска нет — браузер поднимает «Далее».
    var start = scope === "wizard" ? null : byId("metaModalSessionStart");
    var close = scope === "wizard" ? byId("metaWizSessionClose") : byId("metaModalSessionClose");
    var cookies = scope === "wizard" ? byId("metaWizCookies") : byId("metaModalCookies");
    if (start) start.style.display = running ? "none" : "";
    close.style.display = running ? "" : "none";
    cookies.disabled = !!running;
  }

  function sessionStop() {
    sessionSettle(state.session, new Error("Браузер закрыт"));
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
      byId("metaWizSessionActions").style.display = isSession ? "" : "none";
    } else {
      byId("metaModalTokenField").style.display = isSession ? "none" : "";
      byId("metaModalSessionBlock").style.display = isSession ? "" : "none";
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
    var selectedConnection = scope === "modal" ? currentConnection() : null;
    var connectionId = selectedConnection && selectedConnection.can_edit !== false
      ? selectedConnection.id : null;
    if (!proxy) {
      sessionError(scope, "Укажите прокси — без него браузер не запустится (защита от бана)");
      return;
    }
    sessionStop();
    if (scope === "wizard") state.wizard.sessionToken = null;
    if (scope === "modal") byId("metaModalSessionToken").value = "";
    state.session = { scope: scope, sessionId: null, token: null, timer: null, waiters: [] };
    sessionBusy(scope, "Запускаем браузер…");
    setSessionButtons(scope, true);
    var started = state.session;
    try {
      var payload = { cookies: cookies, proxy_url: proxy, user_agent: userAgent || null };
      if (connectionId) payload.connection_id = connectionId;
      var res = await api.post("/meta/session/start", payload);
      state.session.sessionId = res.session_id;
      if (res.status === "error") {
        sessionError(scope, res.error || "Ошибка запуска");
        setSessionButtons(scope, false);
        throw sessionSettleError(started, res.error || "Ошибка запуска");
      }
      sessionPoll(scope);
    } catch (error) {
      if (started.settled) throw error;
      sessionError(scope, error && error.message ? error.message : "Не удалось запустить браузер");
      setSessionButtons(scope, false);
      throw sessionSettleError(started, error && error.message
        ? error.message : "Не удалось запустить браузер");
    }
    return sessionWait();
  }

  function sessionSettleError(session, text) {
    var error = new Error(text);
    if (session) session.settled = true;
    sessionSettle(session, error);
    return error;
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
          sessionSettleError(current, st.error || "Ошибка сессии");
          return;
        }
        if (st.status === "waiting_login" || st.status === "restoring") {
          sessionBusy(scope, (SESSION_STATUS_LABELS[st.status] || st.status) +
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
        sessionBusy(scope, SESSION_STATUS_LABELS[st.status] || st.status);
      } catch (error) {
        if (!state.session || state.session.sessionId !== id) return;
        window.clearInterval(current.timer);
        current.timer = null;
        sessionError(scope, error && error.message ? error.message : "Опрос статуса не удался");
        setSessionButtons(scope, false);
        sessionSettleError(current, error && error.message
          ? error.message : "Опрос статуса не удался");
      }
    }, 3000);
  }

  function onSessionToken(scope, token) {
    if (!token) {
      sessionError(scope, "Токен не найден — попробуйте зайти в Ads Manager в окне браузера и повторить");
      sessionSettleError(state.session, "Токен не найден");
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
    if (state.session) state.session.token = token;
    sessionSettle(state.session, null, token);
  }

  /* ---------- мастер подключения ---------- */

  var WIZARD_STEPS = ["Инструкции", "Токен", "Проверка", "Готово"];

  /* Инструкция под выбранный способ. Держать одну на всех нельзя: у токена из
     панели приложения и у токена сессии шаги вообще не пересекаются, а общий
     текст «создайте приложение» для второго просто неверен. */
  var AUTH_GUIDES = {
    system_user: {
      needs: [
        "Аккаунт разработчика Facebook",
        "Права Admin или Employee в Business Manager",
        "Приложение, созданное на developers.facebook.com"
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
      ]
    },
    session: {
      needs: [
        "Доступ к своему аккаунту Facebook с правами в Business Manager",
        "Cookies из браузера, в котором вы вошли в Ads Manager",
        "Прокси этого аккаунта — обязателен, иначе Meta заблокирует сессию"
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
      ]
    },
    app_token: {
      needs: [
        "Аккаунт разработчика Facebook",
        "Приложение, созданное на developers.facebook.com"
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
      ]
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
      }).join("") + "</ol>";
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
    state.wizard = { step: 1, accounts: [], summary: null, payload: null, sessionToken: null };
    renderWizardMethods("system_user");
    byId("metaWizName").value = "";
    byId("metaWizToken").value = "";
    byId("metaWizCookies").value = "";
    byId("metaWizProxy").value = "";
    byId("metaWizUserAgent").value = "";
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

  /* Кнопка «Далее» на время ожидания гаснет, а рядом крутится кружок: браузер
     Meta поднимается десятками секунд, и молчащий экран читается как зависший. */
  function wizardWaiting(on, text) {
    byId("metaWizNext").disabled = !!on;
    byId("metaWizStatus").innerHTML = on ? SPINNER + escapeHtml(text || "") : "";
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
    var last = WIZARD_STEPS.length;
    byId("metaWizBack").style.visibility = step === 1 || step === last ? "hidden" : "";
    byId("metaWizNext").textContent = step === 3
      ? "Подключить"
      : step === last ? "Готово" : "Далее →";
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
        if (!wizard.sessionToken) {
          // Отдельного «сначала нажмите Запустить браузер» больше нет: кнопка
          // «Далее» поднимает браузер сама. Если человек уже нажал её сам, ждём
          // тот же браузер, а не поднимаем второй.
          var running = state.session && state.session.sessionId && !state.session.settled;
          wizardWaiting(true, running ? "Ждём токен из браузера…" : "Запускаем браузер…");
          try {
            wizard.sessionToken = running ? await sessionWait() : await sessionStart("wizard");
          } catch (error) {
            return wizardError(error && error.message
              ? error.message : "Не удалось получить токен сессии");
          } finally {
            wizardWaiting(false);
          }
        }
        token = wizard.sessionToken;
      } else if (token.length < 20) {
        return wizardError("Вставьте токен");
      }
      // Business ID, sub_id, интервал и глубина перечитывания в мастер больше не
      // входят: на создании у них есть разумные значения по умолчанию, а меняют
      // их потом в настройках самого подключения.
      wizard.payload = {
        name: name,
        access_token: token,
        auth_method: method,
        proxy_url: proxy || null,
        user_agent: byId("metaWizUserAgent").value.trim() || null
      };
      await wizardCheck();
      return;
    }
    if (wizard.step === 3) {
      await wizardConnect();
      return;
    }
    closeWizard();
  }

  async function wizardCheck() {
    var wizard = state.wizard;
    wizardWaiting(true, "Спрашиваем Meta…");
    try {
      var result = await api.post("/meta/connections/preview", {
        access_token: wizard.payload.access_token,
        business_id: wizard.payload.business_id,
        // Проверка идёт тем же маршрутом, что и работа: токен за прокси без
        // него кабинетов не покажет.
        proxy_url: wizard.payload.proxy_url,
        user_agent: wizard.payload.user_agent,
        // Для токена сессии запросы Meta принимает только из браузерного
        // контекста живой сессии — передаём её id.
        session_id: state.session && state.session.sessionId ? state.session.sessionId : null
      });
      wizard.accounts = result.accounts || [];
      wizard.summary = result.summary || null;
      byId("metaWizCheck").innerHTML =
        wizardCheckHtml(wizard.accounts, wizard.summary, wizard.payload);
      wizard.step = 3;
      renderWizard();
    } catch (error) {
      wizardError(error && error.message ? error.message : "Meta отклонила токен");
    } finally {
      wizardWaiting(false);
    }
  }

  /* Плитка сводки. Числа здесь — то, что реально видно этим токеном: если
     кабинетов ноль или БМов не видно, чинить это надо до подключения, а не
     после первой синхронизации. */
  function summaryTile(label, value, hint) {
    return '<div style="border:1px solid #EBE6E6;border-radius:14px;padding:13px 15px">' +
      '<div style="font-size:10.5px;font-weight:700;color:#9B9292;text-transform:uppercase;' +
      'letter-spacing:.5px">' + escapeHtml(label) + "</div>" +
      '<div style="font-size:21px;font-weight:700;letter-spacing:-.4px;margin-top:5px">' +
      escapeHtml(value) + "</div>" +
      (hint
        ? '<div style="font-size:11px;color:#9B9292;font-weight:600;margin-top:3px">' +
          escapeHtml(hint) + "</div>"
        : "") + "</div>";
  }

  function wizardCheckHtml(accounts, summary, payload) {
    var totals = summary || {};
    var token = (payload && payload.access_token) || "";
    // Права у токенов разные: без business_management БМы и страницы не
    // отдаются вовсе, и прочерк здесь честнее нуля.
    function count(value) {
      return value === null || value === undefined ? "—" : num(value);
    }
    var campaignHint = totals.campaigns_partial
      ? "по первым " + num(totals.campaigns_scanned) + " кабинетам"
      : (totals.active_campaigns ? "активных: " + num(totals.active_campaigns) : "");
    return notice("#E4F7F0", "#16B57F",
      payload && payload.auth_method === "session" ? "EAAB-токен получен" : "Токен принят",
      "Так выглядит аккаунт, который подключается.") +
      // Целиком токен не показываем: это ключ от аккаунта, а для «получилось»
      // хватает начала и длины.
      (token
        ? '<div style="margin-top:12px;font-size:11.5px;color:#6A6161;font-weight:600">' +
          "Токен: " + sessionTokenPreview(token) + "</div>"
        : "") +
      '<div style="display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;' +
      'margin-top:14px">' +
      summaryTile("Кабинеты", count(totals.ad_accounts),
        totals.active_ad_accounts ? "рабочих: " + num(totals.active_ad_accounts) : "") +
      summaryTile("БМы", count(totals.businesses), "") +
      summaryTile("Фан-пейджи", count(totals.pages), "") +
      summaryTile("Кампании", count(totals.campaigns), campaignHint) +
      "</div>" +
      ((totals.currencies || []).length > 1
        ? '<div class="meta-note meta-note--warn" style="margin-top:14px">Кабинеты в разных ' +
          "валютах (" + escapeHtml((totals.currencies || []).join(", ")) +
          "). Meta считает день по таймзоне кабинета, а Keitaro — по своей: за «вчера» " +
          "расход и доход могут не сойтись.</div>"
        : "");
  }

  async function wizardConnect() {
    var wizard = state.wizard;
    wizardWaiting(true, "Подключаем…");
    try {
      // Шага выбора кабинетов больше нет: подключаются все, что видно токеном.
      // Ненужные выключают потом в списке кабинетов, там же, где назначают
      // ответственных.
      var payload = Object.assign({}, wizard.payload);
      if (wizard.payload.auth_method === "session" && state.session && state.session.sessionId) {
        // Создание подключения проверяет токен через живую сессию браузера.
        payload.session_id = state.session.sessionId;
      }
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
        "Импортировано кабинетов: " + wizard.accounts.length +
        ". Запущена загрузка статистики за 90 дней — она идёт в фоне, страницу можно " +
        "закрыть.") +
        (wizard.payload.attribution_sub_id
          ? '<div class="meta-note" style="margin-top:14px">Не забудьте добавить в ссылку ' +
            "трекера <b>sub_id_" + wizard.payload.attribution_sub_id +
            "={{campaign.id}}</b> — без этого параметра дохода и ROI по кампаниям не будет.</div>"
          : '<div class="meta-note meta-note--warn" style="margin-top:14px">sub_id с ID ' +
            "кампании не указан, поэтому доход и ROI считаться не будут. Его можно " +
            "добавить позже в настройках подключения.</div>");
      wizard.step = WIZARD_STEPS.length;
      renderWizard();
      state.reference = null;
      await loadConnections();
      await load();
      schedulePoll();
    } catch (error) {
      wizardError(error && error.message ? error.message : "Не удалось подключить");
    } finally {
      wizardWaiting(false);
    }
  }

  function bind() {
    byId("metaPeriod").addEventListener("change", function (event) {
      state.period = event.target.value;
      load().catch(showFailure);
    });
    byId("metaAccountFilter").addEventListener("change", function (event) {
      state.accountId = event.target.value;
      resetLevelFilters();
      load().catch(showFailure);
    });
    byId("metaOwnerFilter").addEventListener("change", function (event) {
      state.ownerId = event.target.value;
      resetLevelFilters();
      load().catch(showFailure);
    });
    byId("metaSync").addEventListener("click", startSync);
    // Мастер создаёт новое подключение в том числе когда другие уже существуют.
    byId("metaConnect").addEventListener("click", function () {
      // Существующие подключения открываются из уровня «Аккаунты» — здесь
      // остаётся только заведение нового.
      openWizard();
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
      if (!state.upload || state.upload.step <= 1) return setLaunchView("bundles");
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
    // Нативный <select> (ФП/пиксель в таблице кабинетов): после закрытия его
    // выпадашки Chromium «вписывает» сфокусированный элемент в видимую
    // область и прокручивает страницу (в т.ч. само окно). Запоминаем позицию
    // на момент клика и возвращаем её после выбора — страница не дёргается.
    byId("metaUpBody").addEventListener("mousedown", function (event) {
      var select = event.target.closest
        ? event.target.closest("select[data-up-account-field]") : null;
      if (!select) return;
      select._pageScrollY = window.scrollY;
    });
    byId("metaUpBody").addEventListener("change", function (event) {
      var select = event.target.closest
        ? event.target.closest("select[data-up-account-field]") : null;
      if (!select || select._pageScrollY === undefined) return;
      var saved = select._pageScrollY;
      select._pageScrollY = undefined;
      if (window.scrollY !== saved) window.scrollTo(0, saved);
      // Chromium может довернуть скролл уже после change — возвращаем ещё раз.
      window.setTimeout(function () {
        if (window.scrollY !== saved) window.scrollTo(0, saved);
      }, 50);
    });
    byId("metaUpBody").addEventListener("focusout", function (event) {
      // Выпадашку закрыли без выбора (Esc/клик мимо) — позиция тоже не должна
      // «уплыть» вместе с фокусом.
      var select = event.target.closest
        ? event.target.closest("select[data-up-account-field]") : null;
      if (!select || select._pageScrollY === undefined) return;
      var saved = select._pageScrollY;
      select._pageScrollY = undefined;
      if (window.scrollY !== saved) window.scrollTo(0, saved);
    });
    byId("metaUpBody").addEventListener("click", onUploadClick);
    byId("metaTableBody").addEventListener("click", function (event) {
      // Селект ответственного живёт в той же строке — клик по нему окно не открывает.
      if (event.target.closest && event.target.closest("select,button,input,a")) return;
      var connectionRow = event.target.closest
        ? event.target.closest("[data-connection-row]") : null;
      if (connectionRow) {
        openModal(connectionRow.getAttribute("data-connection-row"));
        return;
      }
      var row = event.target.closest ? event.target.closest("[data-hour-row]") : null;
      if (row) openHourModal(state.level, row.getAttribute("data-hour-row"));
    });
    byId("metaTableBody").addEventListener("change", function (event) {
      var filterBox = event.target.closest
        ? event.target.closest("[data-level-filter-pick]") : null;
      if (filterBox) {
        var filterLevel = filterBox.getAttribute("data-level-filter-kind");
        toggleLevelFilterPick(filterBox, filterBox.checked);
        syncLevelFilterChecks(filterLevel);
        return;
      }
      var box = event.target.closest ? event.target.closest("[data-spend-pick]") : null;
      if (!box) return;
      toggleSpendPick(box, box.checked);
      syncTableChecks();
      renderSpendButton();
    });
    byId("metaLevelHead").addEventListener("change", function (event) {
      var filterAll = event.target.closest
        ? event.target.closest("[data-level-filter-all]") : null;
      if (filterAll) {
        var level = filterAll.getAttribute("data-level-filter-all");
        Array.prototype.forEach.call(
          document.querySelectorAll('[data-level-filter-kind="' + level + '"]'),
          function (box) { toggleLevelFilterPick(box, filterAll.checked); }
        );
        syncLevelFilterChecks(level);
        return;
      }
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
    byId("metaWizSessionClose").addEventListener("click", function () {
      sessionStop();
      sessionResetUi("wizard");
    });
    byId("metaModalSessionStart").addEventListener("click", function () {
      // Причина отказа уже видна в строке статуса — тоста здесь не нужно,
      // иначе закрытие браузера самим человеком читалось бы как ошибка.
      sessionStart("modal").catch(function () {});
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
        clearUploadPreviews();
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

    byId("metaRulesRun").addEventListener("click", runRules);
    // Кнопка в шапке вкладки раньше была декорацией: обработчика у неё не было,
    // а из JS её ещё и прятали.
    byId("metaRuleCreate").addEventListener("click", createFromHeader);
    bindComments();
    byId("metaEventsAck").addEventListener("click", ackEvents);
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
    // Чистка комментариев не тратит деньги, но необратима и видна снаружи —
    // поэтому право своё, и его дают тем, кому заливы не доверены.
    state.canComments = hasPermission(user, "meta.comments");
    // Фиксацию делает баер со своего аккаунта, а `meta.launch` — про заливы и
    // деньги в кабинете, и у баера его нет. Расход он и так заводит руками в
    // Медиаборде: это то же самое право, только считает сумму Meta.
    state.canFixSpend = state.canLaunch || hasPermission(user, "media.manage");
    // Подключение персональное: любой пользователь с доступом к Meta Ads может
    // создать несколько своих подключений, даже без административного права.
    byId("metaConnect").style.display = "";
    bind();
    if (state.canManage || state.canLaunch || state.canFixSpend) {
      try {
        state.buyers = await api.get("/users/options");
      } catch (error) {
        // Назначать ответственных не выйдет, но сама статистика важнее — не роняем экран.
        state.buyers = [];
      }
    }
    // Вкладку восстанавливаем до загрузки данных: её содержимое рисует себя
    // скелетом и наполняется по мере ответов, а не появляется целиком в конце.
    restoreTab();
    await loadConnections();
    await load();
    schedulePoll();
  }

  window.CelestialMeta = { init: init, reload: load };
})();
