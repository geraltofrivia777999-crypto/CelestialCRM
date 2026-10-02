/*
 * Финансы: книга одного баера за месяц — показатели по строкам, дни по столбцам.
 *
 * Спенд приходит из медиаборда; явная ручная правка заменяет его до очистки.
 * Доход по офферу, профит, ROI и зарплата рассчитываются из актуальных данных.
 * Книга уходит на сервер целиком с задержкой после последнего нажатия — так
 * набор длинного ряда цифр не превращается в очередь из тридцати запросов.
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


  var api = window.CelestialAPI;
  var WEEK = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];
  var MONTHS = [
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь"
  ];
  var MONTHS_GENITIVE = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря"
  ];
  var SAVE_DELAY = 900;
  // Книга всегда в долларах. Евро живёт только на ставке оффера и переводится
  // по курсу месяца — курс хранится в книге, поэтому закрытый месяц не
  // пересчитывается задним числом вслед за рынком.
  var CURRENCIES = { USD: "$", EUR: "€" };

  /* Прочерк остаётся прочерком: «$ –» — это не сумма. */
  function withSign(text) {
    return text === "–" ? text : "$ " + text;
  }

  // Шкала по умолчанию — правило заказчика: процент берётся от месячного
  // профита целиком. Действует, пока в «Настройки → Расчет ЗП» не завели
  // правило для этого баера; тогда сервер присылает свои ступени.
  var LADDER = [
    { pct: 0,  range: "профит ≤ 0",      ceiling: 0 },
    { pct: 10, range: "до 10 000",       ceiling: 10000 },
    { pct: 15, range: "10 001 – 15 000", ceiling: 15000 },
    { pct: 20, range: "15 001 – 20 000", ceiling: 20000 },
    { pct: 25, range: "20 001 – 40 000", ceiling: 40000 },
    { pct: 30, range: "выше 40 000",     ceiling: Infinity }
  ];

  function ladder() {
    return state.ladder && state.ladder.length ? state.ladder : LADDER;
  }

  function salaryStep(profit) {
    var steps = ladder();
    if (profit < 0) return 0;
    for (var i = 1; i < steps.length; i += 1) {
      if (profit <= steps[i].ceiling) return i;
    }
    return steps.length - 1;
  }

  // Правило приходит разобранным: части от профита книги считаются здесь на
  // каждое нажатие клавиши, остальное сервер уже сложил в одну сумму — за этот
  // месяц оно от правки книги не изменится.
  function planSalary(plan, profit) {
    var total = 0;
    var percent = 0;
    (plan.parts || []).forEach(function (part) {
      if (part.kind === "flat") { total += num(part.amount); return; }
      if (profit <= 0) return;
      var rate = part.kind === "grid"
        ? gridPercent(part.tiers || [], profit)
        : num(part.percent);
      percent += rate;
      total += profit * rate / 100;
    });
    return { salary: Math.max(q4(total), 0), percent: percent };
  }

  function gridPercent(tiers, amount) {
    for (var i = 0; i < tiers.length; i += 1) {
      if (tiers[i].up_to === null || tiers[i].up_to === "") return num(tiers[i].percent);
      if (amount <= num(tiers[i].up_to)) return num(tiers[i].percent);
    }
    return 0;
  }

  function stepsFromPlan(plan) {
    if (!plan || !plan.steps || !plan.steps.length) return null;
    return plan.steps.map(function (step) {
      return {
        pct: num(step.percent),
        range: step.label,
        ceiling: step.up_to === null || step.up_to === undefined || step.up_to === ""
          ? Infinity
          : num(step.up_to)
      };
    });
  }

  var state = {
    user: null,
    canManage: false,
    buyers: [],
    scopes: { summaries: [], teams: [], buyers: [] },
    // Страны и партнёрки для полей оффера — те же справочники, что в Офферах.
    countries: [],
    partners: [],
    sheet: "all",
    // Сводка «Партнёрки»: общий лист для заполнения чужих книг. Пока рисуется
    // только разметка — числа и сохранение появятся следующим шагом.
    partnersView: { buyers: [], loaded: false, users: null, partners: null, saving: false },
    // Какая из таблиц баера открыта: обзор только читается, T1 и T23 заполняют.
    bookTab: "overview",
    buyerId: null,
    buyerName: "",
    year: 0,
    month: 0,
    days: 31,
    prevMinus: 0,
    eurRate: 1,
    mediaSpend: {},
    manualDirty: {},
    // Теги баера из «Команды»: строки под оффером, добавленным в книге.
    buyerTags: [],
    manualRevision: 0,
    manual: { spendBuyer: {}, spendAgent: {}, costs: {} },
    offers: [],
    // Справочник офферов для выбора новой строки книги. Грузится по первому
    // нажатию «+ оффер»: на обзоре и в закрытых месяцах он не нужен.
    catalog: { items: [], loading: false, loaded: false, error: "" },
    // Правило зарплаты этого баера за этот месяц и его ступени; null — правила
    // нет, действует шкала по умолчанию.
    plan: null,
    ladder: null,
    // Пока книга не приехала с сервера, state пустой, и любое сохранение из него
    // затёрло бы месяц. Флаг снимается только успешной загрузкой.
    loaded: false
  };

  var calcCells = {};
  var navRows = [];
  var saveTimer = null;
  var activeSave = null;
  var loadSequence = 0;
  var dirty = false;
  var OFFER_LOCK_FIELDS = ["name", "partner", "geo", "rate", "rate_currency"];

  function offerLocked(offer) {
    return (offer.lockedFields || []).length > 0;
  }

  function lockOfferFields(offer) {
    offer.lockedFields = OFFER_LOCK_FIELDS.slice();
  }

  /* ---------- helpers ---------- */

  function byId(id) { return document.getElementById(id); }
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }
  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }
  /* Автозаполнение и менеджеры паролей молча правят поля и шлют input-события,
   * а автосохранение отправляет результат на сервер как правку человека. */
  function noAutofill(input) {
    input.setAttribute("autocomplete", "off");
    input.setAttribute("autocorrect", "off");
    input.setAttribute("spellcheck", "false");
    input.setAttribute("data-lpignore", "true");
    input.setAttribute("data-1p-ignore", "true");
    return input;
  }

  function num(value) {
    var parsed = Number(value);
    return isFinite(parsed) ? parsed : 0;
  }
  function q4(value) {
    return Math.round((num(value) + Number.EPSILON) * 10000) / 10000;
  }
  function money(v) {
    if (!v) return "–";
    return v.toLocaleString("ru-RU", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function whole(v) {
    if (!v) return "–";
    return v.toLocaleString("ru-RU", { maximumFractionDigits: 0 });
  }
  function percent(v) {
    if (v === null) return "–";
    return v.toLocaleString("ru-RU", { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + "%";
  }
  function summaryMoney(value) {
    if (value === null || value === undefined) return "–";
    return "$ " + num(value).toLocaleString("ru-RU", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    });
  }
  function summaryPercent(value) {
    if (value === null || value === undefined) return "–";
    return num(value).toLocaleString("ru-RU", {
      minimumFractionDigits: 1,
      maximumFractionDigits: 1
    }) + "%";
  }
  function hasPermission(user, code) {
    if (!user || !user.role) return false;
    return (user.role.permissions || []).some(function (item) {
      return item.code === "*" || item.code === code;
    });
  }

  /* ---------- расчёт ---------- */

  function offerDeposits(offer, day) {
    return offer.tags.reduce(function (sum, tag) {
      return sum + num(tag.values[day]);
    }, 0);
  }
  /* Ставка в долларах: евровая переводится по курсу месяца. */
  function offerRate(offer) {
    return offer.rateCurrency === "EUR"
      ? num(offer.rate) * (num(state.eurRate) || 1)
      : num(offer.rate);
  }
  function offerIncome(offer, day) { return offerDeposits(offer, day) * offerRate(offer); }
  function income(day) {
    return state.offers.reduce(function (sum, offer) {
      return sum + offerIncome(offer, day);
    }, 0);
  }
  // Пустая ручная ячейка использует медиаборд; явный 0 заменяет его нулём.
  /* Спенд дня — целые доллары: столбец, его сумма и карточка наверху должны
     сходиться, а копейки кабинета в книге ничего не решают. Сервер округляет
     то же значение при переносе из медиаборда. */
  function spend(day) {
    return Math.round(state.manual.spendBuyer[day] != null
      ? num(state.manual.spendBuyer[day]) : num(state.mediaSpend[day]));
  }
  function costs(day) { return num(state.manual.costs[day]); }
  function profit(day) { return income(day) - spend(day) - costs(day); }
  function roi(day) {
    var base = spend(day) + costs(day);
    return base > 0 ? profit(day) / base * 100 : null;
  }
  function totalOf(getter) {
    var sum = 0;
    for (var d = 1; d <= state.days; d += 1) sum += getter(d) || 0;
    return sum;
  }
  function seriesOf(getter) {
    var out = [];
    for (var d = 1; d <= state.days; d += 1) out.push(getter(d) || 0);
    return out;
  }

  /* ---------- шапка сетки ---------- */

  function dayFlags(day) {
    var date = new Date(state.year, state.month - 1, day);
    var weekday = date.getDay();
    var now = window.CelestialTime.today();
    return {
      weekday: WEEK[weekday],
      off: weekday === 0 || weekday === 6,
      today: now.getFullYear() === state.year &&
        now.getMonth() + 1 === state.month && now.getDate() === day
    };
  }

  function renderHead() {
    byId("finGridHead").innerHTML = "";
    byId("finGridHead").appendChild(dayHeadRow());
  }

  /* Шапка таблицы по дням: «Показатель», «За месяц» и день за днём. Одна и та
     же у книги баера и у сводки «Партнёрки». */
  function dayHeadRow() {
    var tr = el("tr");
    var name = el("th", "fin-c-name");
    name.appendChild(el("span", "fin-h-lab", "Показатель"));
    tr.appendChild(name);
    var sum = el("th", "fin-c-sum");
    sum.appendChild(el("span", "fin-h-lab", "За месяц"));
    tr.appendChild(sum);
    for (var d = 1; d <= state.days; d += 1) {
      var flags = dayFlags(d);
      var th = el("th", "fin-day" + (flags.off ? " is-off" : "") + (flags.today ? " is-today" : ""));
      th.style.position = "sticky";
      th.dataset.d = d;
      th.appendChild(el("span", "fin-day-n", String(d)));
      th.appendChild(el("span", "fin-day-w", flags.weekday));
      tr.appendChild(th);
    }
    return tr;
  }

  /* ---------- строки ---------- */

  function bandRow(kind, label, action) {
    var tr = el("tr", "fin-band fin-band--" + kind);
    var fixed = el("td", "fin-band-label");
    fixed.colSpan = 2;
    var inner = el("div", "fin-band-inner");
    inner.appendChild(el("span", "fin-band-text", label));
    if (action) inner.appendChild(action);
    fixed.appendChild(inner);
    tr.appendChild(fixed);
    var days = el("td", "fin-band-fill");
    days.colSpan = state.days;
    tr.appendChild(days);
    return tr;
  }

  /* Цвет области живёт на строке, а не на отдельных ячейках: так его наследуют
   * и залипшие колонки слева, и дневные ячейки. */
  function section(tr, kind) {
    tr.classList.add("fin-sec--" + kind);
    return tr;
  }

  function addOfferButton() {
    var button = el("button", "fin-band-add", "+ оффер");
    button.type = "button";
    button.id = "finAddOffer";
    button.disabled = !state.canManage;
    button.addEventListener("click", addOffer);
    return button;
  }

  /* Новая строка книги начинается с выбора оффера из справочника. Ручной
     ввод убрали: набранный руками оффер приходил без ID у партнёрки, а по
     нему депозиты из ПП и находят строку — без ID она молча оставалась
     пустой. Теперь оффер сначала заводят в разделе «Оффера». */
  function addOffer() {
    // Тег создаётся пустым: как назвать строку, решает пользователь, а не мы.
    state.offers.push({ picking: true, name: "", partner: "", geo: null,
      sourceOfferId: null, rate: 0, rateCurrency: "USD",
      lockedFields: [],
      tags: [{ name: "", values: {} }] });
    renderBody();
    recalc();
    loadOfferCatalog().then(function () {
      // Список приезжает после отрисовки — перерисовываем, чтобы селект
      // наполнился, не сбрасывая уже введённое в другие строки.
      if (state.offers.some(function (offer) { return offer.picking; })) renderBody();
    });
    var picker = byId("finGridBody").querySelector(".fin-o-pick");
    if (picker) picker.focus();
  }

  /* Перенос из справочника — те же поля, что подставляет назначение оффера
     баеру: имя, партнёрка, гео и ставка с её валютой. */
  function applyCatalogOffer(offer, item) {
    offer.picking = false;
    offer.name = item.name || "Новый оффер";
    offer.partner = item.partner || "";
    offer.geo = item.geo || null;
    offer.rate = num(item.cpa);
    offer.rateCurrency = item.cpa_currency === "EUR" ? "EUR" : "USD";
    offer.sourceOfferId = item.id;
    offer.externalId = item.external_id || "";
    offer.lockedFields = [];
    // Как при назначении оффера в «Офферах»: по строке на каждый тег баера.
    // Только пока строки пустые — набранное руками не переписываем.
    var blank = offer.tags.every(function (tag) {
      return !String(tag.name || "").trim() && !Object.keys(tag.values || {}).length;
    });
    if (blank && state.buyerTags.length) {
      offer.tags = state.buyerTags.map(function (name) { return { name: name, values: {} }; });
    }
    renderBody();
    recalc();
    scheduleSave();
  }

  async function loadOfferCatalog() {
    var catalog = state.catalog;
    if (catalog.loaded || catalog.loading) return;
    catalog.loading = true;
    catalog.error = "";
    // В финансах работают с офферами, заведёнными вручную в разделе
    // «Оффера» (manual=true = connection_id IS NULL): трекерные из Keitaro
    // сюда не попадают — они не про деньги баера по сделке.
    var query = "?manual=true&limit=200";
    try {
      // Сначала офферы этого баера — книга ведётся по ним. Если ему ещё
      // ничего не назначили, показываем весь видимый справочник: иначе
      // список пуст и выбирать не из чего.
      var mine = await api.get("/offers" + query +
        "&for_buyer_id=" + encodeURIComponent(state.buyerId));
      var items = (mine && mine.items) || [];
      if (!items.length) {
        var all = await api.get("/offers" + query);
        items = (all && all.items) || [];
      }
      catalog.items = items;
      catalog.loaded = true;
    } catch (error) {
      catalog.error = error && error.message ? error.message : "Справочник недоступен";
    } finally {
      catalog.loading = false;
    }
  }

  function labelCell(label, hint) {
    var td = el("td", "fin-c-name");
    var box = el("div", "fin-r-lab", label);
    if (hint) {
      var note = el("span", "fin-r-hint", hint);
      box.appendChild(note);
      td.hintNode = note;
    }
    td.appendChild(box);
    return td;
  }

  function inputRow(key, label, hint, store, step, labelNode) {
    var tr = el("tr");
    tr.appendChild(labelNode || labelCell(label, hint));
    var sum = el("td", "fin-c-sum");
    tr.appendChild(sum);
    var line = [];
    for (var d = 1; d <= state.days; d += 1) {
      var flags = dayFlags(d);
      var td = el("td", "fin-num fin-in" + (flags.off ? " is-off" : ""));
      td.dataset.d = d;
      var input = document.createElement("input");
      input.type = "number";
      input.step = step;
      input.inputMode = "decimal";
      input.value = store[d] != null ? store[d] : "";
      input.dataset.d = d;
      if (key === "spendManual") input.dataset.spendManual = "true";
      input.disabled = !state.canManage;
      noAutofill(input);
      input.setAttribute("aria-label", label + ", день " + d);
      input.addEventListener("input", function (event) {
        var day = Number(event.target.dataset.d);
        var raw = event.target.value;
        if (raw === "") delete store[day];
        else store[day] = num(raw);
        if (key === "spendManual") state.manualDirty[day] = ++state.manualRevision;
        scheduleSave();
      });
      // Колесо над сфокусированным полем молча меняет цифру — при прокрутке
      // длинной таблицы это тихая порча данных.
      input.addEventListener("wheel", function (event) {
        if (document.activeElement === event.target) event.target.blur();
      }, { passive: true });
      td.appendChild(input);
      tr.appendChild(td);
      line.push(input);
    }
    navRows.push(line);
    calcCells[key] = { sum: sum, days: null };
    return tr;
  }

  /* Спенд — одна строка вместо трёх («Из медиаборда», «Ручной спенд», «Спенд
     итого»). В ячейке лежит то, что доска посчитала по GEO и тиру, пока её не
     тронули руками; правка запирает ячейку замком, и медиаборд её больше не
     переписывает. Пустая ячейка снимает замок и возвращает автоподсчёт —
     то же делает клик по самому замку.

     Замок закрыт = на сервере лежит manual_spend этого дня; открыт = null и
     значение приезжает из media_spend. */
  var LOCK_CLOSED = '<svg viewBox="0 0 24 24" width="11" height="11" aria-hidden="true">' +
    '<path d="M7 10V7a5 5 0 0 1 10 0v3" fill="none" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round"/><rect x="4.5" y="10" width="15" height="10" rx="2.5" ' +
    'fill="currentColor"/></svg>';
  var LOCK_OPEN = '<svg viewBox="0 0 24 24" width="11" height="11" aria-hidden="true">' +
    '<path d="M7 10V7a5 5 0 0 1 9.5-2.2" fill="none" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round"/><rect x="4.5" y="10" width="15" height="10" rx="2.5" ' +
    'fill="none" stroke="currentColor" stroke-width="2"/></svg>';

  function spendLocked(day) {
    return state.manual.spendBuyer[day] != null;
  }

  function paintSpendCell(cell) {
    var day = Number(cell.dataset.d);
    var locked = spendLocked(day);
    cell.td.classList.toggle("is-locked", locked);
    cell.lock.innerHTML = locked ? LOCK_CLOSED : LOCK_OPEN;
    cell.lock.title = locked
      ? "Значение внесено вручную. Медиаборд его не меняет — нажмите, чтобы вернуть автоподсчёт"
      : "Значение из медиаборда. Впишите своё, чтобы закрепить";
    cell.lock.setAttribute("aria-label", cell.lock.title);
    cell.lock.setAttribute("aria-pressed", locked ? "true" : "false");
    paintSpendMaster();
    if (cell.input !== document.activeElement) {
      // Спенд ведём в целых долларах: «1259.9876» в ячейку не помещается и
      // обрезается, а копейки кабинетов на решения финансиста не влияют.
      var value = locked ? state.manual.spendBuyer[day] : state.mediaSpend[day];
      cell.input.value = value == null ? "" : Math.round(num(value));
    }
  }

  function repaintSpend() {
    spendCells.forEach(paintSpendCell);
  }

  var spendCells = [];
  var spendMaster = null;

  /* Общий замок у подписи «Спенд» — весь месяц разом. Своего состояния у него
     нет: закрыт, только когда закрыт каждый день, поэтому после перезагрузки
     показывает то, что лежит на сервере. */
  function spendAllLocked() {
    for (var d = 1; d <= state.days; d += 1) {
      if (!spendLocked(d)) return false;
    }
    return state.days > 0;
  }

  function paintSpendMaster() {
    if (!spendMaster) return;
    var locked = spendAllLocked();
    spendMaster.innerHTML = locked ? LOCK_CLOSED : LOCK_OPEN;
    spendMaster.title = locked
      ? "Весь месяц закреплён. Нажмите, чтобы вернуть автоподсчёт из медиаборда во все дни"
      : "Нажмите, чтобы закрепить весь месяц: медиаборд перестанет менять эти дни";
    spendMaster.setAttribute("aria-label", spendMaster.title);
    spendMaster.setAttribute("aria-pressed", locked ? "true" : "false");
  }

  // Закрыть: каждый открытый день фиксируется тем, что в нём сейчас (пустой —
  // нулём). Открыть: все дни возвращаются к медиаборду, закрытые поодиночке тоже.
  function toggleSpendMonth() {
    var unlock = spendAllLocked();
    for (var d = 1; d <= state.days; d += 1) {
      if (unlock) delete state.manual.spendBuyer[d];
      else if (spendLocked(d)) continue;
      else state.manual.spendBuyer[d] = Math.round(num(state.mediaSpend[d]));
      state.manualDirty[d] = ++state.manualRevision;
    }
    repaintSpend();
    recalc();
    scheduleSave();
  }

  function spendRow() {
    var tr = el("tr");
    spendMaster = null;
    var label = labelCell("Спенд", "медиаборд, пока не закрыт замок");
    var head = el("div", "fin-o-wrap fin-spend-head");
    head.appendChild(label.firstChild);
    // Как у оффера: замок только у тех, кто правит книгу.
    if (state.canManage) {
      spendMaster = el("button", "fin-spend-lock");
      spendMaster.type = "button";
      spendMaster.addEventListener("click", toggleSpendMonth);
      head.appendChild(spendMaster);
      // Место корзины оффера — замки строк совпадают по вертикали.
      var gap = el("span", "fin-o-del fin-spend-gap");
      gap.setAttribute("aria-hidden", "true");
      gap.innerHTML = '<svg viewBox="0 0 24 24" width="15" height="15"></svg>';
      head.appendChild(gap);
    }
    label.appendChild(head);
    tr.appendChild(label);
    var sum = el("td", "fin-c-sum");
    tr.appendChild(sum);
    var line = [];
    spendCells = [];
    for (var d = 1; d <= state.days; d += 1) {
      var flags = dayFlags(d);
      var td = el("td", "fin-num fin-in fin-in--lock" + (flags.off ? " is-off" : ""));
      td.dataset.d = d;
      var input = document.createElement("input");
      input.type = "number";
      input.step = "1";
      input.inputMode = "numeric";
      input.dataset.d = d;
      input.dataset.spendManual = "true";
      input.disabled = !state.canManage;
      noAutofill(input);
      input.setAttribute("aria-label", "Спенд, день " + d);
      if (state.canSpend) {
        // Правка расхода идёт не в книгу, а в Медиаборд: там у суммы есть
        // агенты и своя комиссия, и книга берёт готовое число оттуда.
        input.readOnly = true;
        input.addEventListener("focus", function (event) {
          openSpendDay(Number(event.target.dataset.d));
        });
        input.addEventListener("click", function (event) {
          openSpendDay(Number(event.target.dataset.d));
        });
      }
      input.addEventListener("input", function (event) {
        var day = Number(event.target.dataset.d);
        var raw = event.target.value;
        // Пустая ячейка — это снятый замок, а не ноль: ноль вписывают явно.
        if (raw === "") delete state.manual.spendBuyer[day];
        // Введённое округляем на месте: строка целиком в целых долларах, и
        // одна ячейка с копейками ломала бы и вид, и сумму столбца.
        else state.manual.spendBuyer[day] = Math.round(num(raw));
        state.manualDirty[day] = ++state.manualRevision;
        paintSpendCell(spendCells[day - 1]);
        scheduleSave();
        recalc();
      });
      input.addEventListener("blur", function (event) {
        // Ячейку очистили и ушли — показываем то, что подставил медиаборд.
        paintSpendCell(spendCells[Number(event.target.dataset.d) - 1]);
      });
      // Колесо над сфокусированным полем молча меняет цифру — при прокрутке
      // длинной таблицы это тихая порча данных.
      input.addEventListener("wheel", function (event) {
        if (document.activeElement === event.target) event.target.blur();
      }, { passive: true });
      var lock = document.createElement("button");
      lock.type = "button";
      lock.className = "fin-lock";
      lock.dataset.d = d;
      lock.disabled = !state.canManage;
      lock.addEventListener("click", function (event) {
        var day = Number(event.currentTarget.dataset.d);
        if (!spendLocked(day)) return event.currentTarget.parentElement
          .querySelector("input").focus();
        delete state.manual.spendBuyer[day];
        state.manualDirty[day] = ++state.manualRevision;
        paintSpendCell(spendCells[day - 1]);
        scheduleSave();
        recalc();
      });
      td.appendChild(input);
      td.appendChild(lock);
      tr.appendChild(td);
      line.push(input);
      spendCells.push({ dataset: { d: d }, td: td, input: input, lock: lock });
    }
    navRows.push(line);
    calcCells.spendBuyer = { sum: sum, days: null };
    repaintSpend();
    return tr;
  }

  function calcRow(key, label, hint, cls) {
    var tr = el("tr", cls || "fin-calc");
    var name = labelCell(label, hint);
    tr.appendChild(name);
    var sum = el("td", "fin-c-sum");
    tr.appendChild(sum);
    var days = {};
    for (var d = 1; d <= state.days; d += 1) {
      var flags = dayFlags(d);
      var td = el("td", "fin-num" + (flags.off ? " is-off" : ""));
      td.dataset.d = d;
      var value = el("span", "fin-v", "–");
      td.appendChild(value);
      tr.appendChild(td);
      days[d] = value;
    }
    calcCells[key] = { sum: sum, days: days, hint: name.hintNode };
    return tr;
  }

  /* Название тега правится прямо в таблице: «SOK» — такое же имя, как любое
   * другое, и команда переименовывает его под свои строки. */
  function tagLabelCell(offer, tag, tagIndex) {
    var td = el("td", "fin-c-name");
    var wrap = el("div", "fin-tag-wrap");
    var name = document.createElement("input");
    name.className = "fin-tag-name" + (tag.name ? "" : " is-empty");
    name.value = tag.name;
    name.placeholder = "Назовите тег";
    name.disabled = !state.canManage;
    name.dataset.tag = tagIndex;
    name.dataset.offer = state.offers.indexOf(offer);
    name.setAttribute("aria-label", "Название тега");
    noAutofill(name);
    name.addEventListener("input", function () {
      tag.name = name.value;
      name.classList.toggle("is-empty", !name.value.trim());
      scheduleSave();
    });
    wrap.appendChild(name);

    if (state.canManage) {
      var del = el("button", "fin-tag-del");
      del.type = "button";
      del.title = "Удалить тег";
      del.setAttribute("aria-label", "Удалить тег");
      del.innerHTML = '<svg viewBox="0 0 24 24" width="13" height="13">' +
        '<path d="M6 12h12" fill="none" stroke="currentColor" stroke-width="2.4" ' +
        'stroke-linecap="round"/></svg>';
      del.addEventListener("click", async function () {
        if (!(await askConfirm({
          title: "Удалить тег?",
          message: "«" + (tag.name || "без названия") +
            "» будет удалён вместе с депозитами за месяц.",
          confirmLabel: "Удалить",
          danger: true
        }))) return;
        offer.tags.splice(tagIndex, 1);
        renderBody();
        recalc();
        scheduleSave();
      });
      wrap.appendChild(del);
    }
    td.appendChild(wrap);
    return td;
  }

  /* Строки под оффером называет финансист, поэтому вопрос перечисляет их
   * настоящие имена. «SOK» здесь было бы враньём: такого тега может и не быть. */
  function deleteOfferQuestion(offer) {
    var named = offer.tags.map(function (tag) { return (tag.name || "").trim(); })
      .filter(Boolean);
    var tail;
    if (!named.length) {
      tail = " вместе с депозитами за месяц?";
    } else {
      var list = named.map(function (name) { return "«" + name + "»"; }).join(", ");
      tail = named.length === 1
        ? " вместе с тегом " + list + " и его депозитами за месяц?"
        : " вместе с тегами " + list + " и их депозитами за месяц?";
    }
    return "Удалить оффер «" + (offer.name || "без названия") + "»" + tail;
  }

  function renderRateNote(offer, note) {
    note.textContent = offer.rateCurrency === "EUR" && num(offer.rate)
      ? "≈ $" + offerRate(offer).toLocaleString("ru-RU", { maximumFractionDigits: 2 })
      : "";
  }

  /* Выбор оффера у новой строки. Список — обычный <select>: его подхватывает
     select-ui и даёт поиск с подписью, а ID из подписи ищется наравне с
     названием — по нему строку и сверяют с кабинетом партнёрки. */
  function offerPicker(offer) {
    var picker = document.createElement("select");
    picker.className = "fin-o-name fin-o-pick meta-select";
    picker.setAttribute("aria-label", "Выберите оффер из списка");
    var catalog = state.catalog;
    var first = document.createElement("option");
    first.value = "";
    first.textContent = catalog.loading
      ? "Загружаем офферы…"
      : catalog.error
        ? "Справочник недоступен"
        : "Выберите оффер…";
    picker.appendChild(first);
    catalog.items.forEach(function (item) {
      var option = document.createElement("option");
      option.value = item.id;
      option.textContent = item.name;
      var hint = [
        item.external_id ? "ID " + item.external_id : "без ID",
        item.geo || null,
        item.partner || null
      ].filter(Boolean).join(" · ");
      option.setAttribute("data-hint", hint);
      picker.appendChild(option);
    });
    picker.disabled = !state.canManage || catalog.loading;
    picker.addEventListener("change", function () {
      var chosen = catalog.items.filter(function (item) {
        return item.id === picker.value;
      })[0];
      if (chosen) applyCatalogOffer(offer, chosen);
    });
    return picker;
  }

  function offerHeadRow(offer, index) {
    var tr = el("tr", "fin-offer-head");
    var td = el("td", "fin-c-name");
    var wrap = el("div", "fin-o-wrap");
    wrap.appendChild(el("i", "fin-o-dot"));

    if (offer.picking) {
      wrap.appendChild(offerPicker(offer));
    } else {
      var name = document.createElement("input");
      name.className = "fin-o-name";
      name.value = offer.name;
      name.disabled = !state.canManage;
      name.setAttribute("aria-label", "Название оффера");
      noAutofill(name);
      name.addEventListener("input", function () {
        offer.name = name.value;
        lockOfferFields(offer);
        scheduleSave();
      });
      wrap.appendChild(name);
    }

    if (state.canManage && offer.sourceOfferId) {
      var offerLock = el("button", "fin-spend-lock");
      offerLock.type = "button";
      offerLock.innerHTML = offerLocked(offer) ? LOCK_CLOSED : LOCK_OPEN;
      offerLock.title = offerLocked(offer)
        ? "Ручные данные защищены. Нажмите, чтобы разрешить обновление из «Офферов»"
        : "Данные обновляются из «Офферов». Нажмите, чтобы защитить ручные значения";
      offerLock.setAttribute("aria-label", offerLock.title);
      offerLock.addEventListener("click", function () {
        var wasLocked = offerLocked(offer);
        offer.lockedFields = wasLocked ? [] : OFFER_LOCK_FIELDS.slice();
        offer.syncFromCatalog = wasLocked;
        renderBody();
        scheduleSave();
      });
      wrap.appendChild(offerLock);
    }

    /* ID оффера у партнёрки: по нему строку сверяют с кабинетом ПП и понимают,
       почему депозиты приехали или не приехали. Здесь он только показан —
       меняют его в разделе «Оффера», где он и живёт. */
    if (offer.externalId) {
      var external = el("span", "fin-o-id", "ID " + offer.externalId);
      external.title = "ID оффера у партнёрки — из раздела «Оффера»";
      wrap.appendChild(external);
    }

    if (state.canManage) {
      var del = el("button", "fin-o-del");
      del.type = "button";
      del.title = "Удалить оффер";
      del.setAttribute("aria-label", "Удалить оффер");
      del.innerHTML = '<svg viewBox="0 0 24 24" width="15" height="15">' +
        '<path d="M5 7h14M10 7V5h4v2M7 7l1 13h8l1-13" fill="none" stroke="currentColor" ' +
        'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
      del.addEventListener("click", async function () {
        if (!(await askConfirm({
          title: "Удалить оффер из книги?",
          message: deleteOfferQuestion(offer),
          confirmLabel: "Удалить",
          danger: true
        }))) return;
        state.offers.splice(index, 1);
        renderBody();
        recalc();
        scheduleSave();
      });
      wrap.appendChild(del);
    }
    td.appendChild(wrap);
    tr.appendChild(td);

    var meta = el("td", "fin-c-sum fin-o-meta-cell");
    var box = el("div", "fin-o-meta");

    // Партнёрки — тот же справочник, что в Офферах: он приходит из Keitaro, и
    // свободный ввод разводил одну партнёрку по написаниям.
    var partner = document.createElement("select");
    partner.className = "fin-o-partner";
    partner.disabled = !state.canManage;
    partner.setAttribute("aria-label", "Партнёрка");
    var names = [""].concat(state.partners);
    if (offer.partner && state.partners.indexOf(offer.partner) < 0) {
      // Название, сохранённое до справочника, не теряем.
      names.push(offer.partner);
    }
    names.forEach(function (name) {
      var option = document.createElement("option");
      option.value = name;
      option.textContent = name || "Партнёрка —";
      partner.appendChild(option);
    });
    partner.value = offer.partner || "";
    partner.addEventListener("change", function () {
      offer.partner = partner.value || null;
      lockOfferFields(offer);
      scheduleSave();
    });
    box.appendChild(partner);

    // Тир руками не ставят: он приходит из «Настройки → Тиры стран» по гео.
    // Иначе два оффера на одну страну разъехались бы по тирам, и сводка
    // перестала бы сходиться сама с собой.
    var geo = document.createElement("select");
    geo.className = "fin-o-geo";
    geo.disabled = !state.canManage;
    geo.setAttribute("aria-label", "Гео оффера");
    var known = [{ code: "", name: "Гео —" }].concat(state.countries);
    if (offer.geo && !state.countries.some(function (item) {
      return item.code === offer.geo;
    })) {
      // Код, которого нет в справочнике, уже сохранён у оффера — не теряем его.
      known.push({ code: offer.geo, name: offer.geo });
    }
    known.forEach(function (item) {
      var option = document.createElement("option");
      option.value = item.code;
      // Только код: в строке оффера рядом стоят партнёрка и ставка, и полное
      // название страны вытесняло их за край.
      option.textContent = item.code || item.name;
      geo.appendChild(option);
    });
    geo.value = offer.geo || "";

    geo.addEventListener("change", function () {
      offer.geo = geo.value || null;
      lockOfferFields(offer);
      scheduleSave();
    });
    box.appendChild(geo);

    var rate = el("div", "fin-o-rate");
    rate.appendChild(el("span", null, "Ставка"));

    // Валюта у каждого оффера своя: часть партнёрок платит в евро.
    var currency = el("button", "fin-o-cur", CURRENCIES[offer.rateCurrency] || "$");
    currency.type = "button";
    currency.disabled = !state.canManage;
    currency.title = "Валюта ставки: доллар или евро";
    currency.setAttribute("aria-label", "Валюта ставки");
    currency.classList.toggle("is-eur", offer.rateCurrency === "EUR");
    currency.addEventListener("click", function () {
      offer.rateCurrency = offer.rateCurrency === "EUR" ? "USD" : "EUR";
      lockOfferFields(offer);
      currency.textContent = CURRENCIES[offer.rateCurrency];
      currency.classList.toggle("is-eur", offer.rateCurrency === "EUR");
      renderRateNote(offer, note);
      recalc();
      scheduleSave();
    });
    rate.appendChild(currency);

    var rateInput = document.createElement("input");
    rateInput.type = "number";
    rateInput.step = "0.01";
    rateInput.inputMode = "decimal";
    rateInput.value = offer.rate;
    rateInput.disabled = !state.canManage;
    rateInput.setAttribute("aria-label", "Ставка за конверсию");
    noAutofill(rateInput);
    rateInput.addEventListener("input", function () {
      offer.rate = num(rateInput.value);
      lockOfferFields(offer);
      renderRateNote(offer, note);
      recalc();
      scheduleSave();
    });
    rate.appendChild(rateInput);
    box.appendChild(rate);

    // Рядом с евровой ставкой — во что она превращается в книге.
    var note = el("span", "fin-o-usd");
    renderRateNote(offer, note);
    box.appendChild(note);

    if (state.canManage) {
      var addTag = el("button", "fin-tag-add", "+ тег");
      addTag.type = "button";
      addTag.title = "Добавить тег";
      addTag.addEventListener("click", function () {
        offer.tags.push({ name: "", values: {} });
        renderBody();
        recalc();
        scheduleSave();
        focusTag(offer, offer.tags.length - 1);
      });
      box.appendChild(addTag);
    }

    meta.appendChild(box);
    tr.appendChild(meta);

    var days = el("td", "fin-o-days");
    days.colSpan = state.days;
    tr.appendChild(days);
    return tr;
  }

  function previousMonthLabel() {
    var previous = new Date(state.year, state.month - 2, 1);
    return MONTHS_GENITIVE[previous.getMonth()] + " " + previous.getFullYear();
  }

  function renderBody() {
    var body = byId("finGridBody");
    body.innerHTML = "";
    calcCells = {};
    navRows = [];

    body.appendChild(bandRow("cost", "Затраты"));
    body.appendChild(section(spendRow(), "cost"));
    body.appendChild(section(inputRow(
      "costs", "Costs", "комиссии, сервисы, прочее", state.manual.costs, "0.01"
    ), "cost"));

    body.appendChild(bandRow("offer", "Офферы", addOfferButton()));
    state.offers.forEach(function (offer, index) {
      body.appendChild(section(offerHeadRow(offer, index), "offer"));
      offer.tags.forEach(function (tag, tagIndex) {
        body.appendChild(section(inputRow(
          "tag" + index + "_" + tagIndex, tag.name, null, tag.values, "1",
          tagLabelCell(offer, tag, tagIndex)
        ), "offer"));
      });
      body.appendChild(section(
        calcRow("inc" + index, "Доход", "депозиты × ставка", "fin-calc fin-offer-income"),
        "offer"
      ));
    });

    body.appendChild(bandRow("result", "Результат"));
    body.appendChild(section(
      calcRow("income", "Общий доход", "сумма по офферам", "fin-calc fin-total"), "result"));
    body.appendChild(section(
      calcRow("profit", "Профит", "доход − спенд − costs", "fin-calc fin-total"), "result"));
    body.appendChild(section(
      calcRow("roi", "ROI", "профит месяца ÷ (спенд + costs)"), "result"));

    byId("finOfferCount").textContent = state.offers.length
      ? state.offers.length + " " + plural(state.offers.length, "оффер", "оффера", "офферов")
      : "Офферов пока нет";
    buildNav();
  }

  /* Новый тег ждёт названия, поэтому курсор сразу в нём. */
  function focusTag(offer, tagIndex) {
    var index = state.offers.indexOf(offer);
    var row = byId("finGridBody").querySelector(
      '.fin-tag-name[data-offer="' + index + '"][data-tag="' + tagIndex + '"]'
    );
    if (row) { row.focus(); row.select(); }
  }

  function plural(count, one, few, many) {
    var mod10 = count % 10, mod100 = count % 100;
    if (mod10 === 1 && mod100 !== 11) return one;
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few;
    return many;
  }

  /* ---------- пересчёт ---------- */

  function writeRow(key, format, getter, tone) {
    var slot = calcCells[key];
    if (!slot || !slot.days) return 0;
    var sum = 0;
    for (var d = 1; d <= state.days; d += 1) {
      var value = getter(d);
      if (value != null) sum += value;
      var node = slot.days[d];
      node.textContent = format(value);
      node.classList.toggle("fin-neg", tone === true && value < 0);
      node.classList.toggle("fin-pos", tone === true && value > 0);
      node.classList.toggle("is-zero", !value);
    }
    return sum;
  }

  function recalc() {
    state.offers.forEach(function (offer, index) {
      var incomeSum = writeRow("inc" + index, whole, function (day) {
        return offerIncome(offer, day);
      });
      calcCells["inc" + index].sum.textContent = whole(incomeSum);
      offer.tags.forEach(function (tag, tagIndex) {
        var tagSum = 0;
        for (var d = 1; d <= state.days; d += 1) tagSum += num(tag.values[d]);
        calcCells["tag" + index + "_" + tagIndex].sum.textContent = whole(tagSum);
      });
    });

    var spendSum = totalOf(spend);
    calcCells.spendBuyer.sum.textContent = whole(spendSum);
    var costsSum = totalOf(costs);
    calcCells.costs.sum.textContent = money(costsSum);

    var incomeSum = writeRow("income", whole, income);
    calcCells.income.sum.textContent = whole(incomeSum);

    // Дневные ячейки — чистый день; у переноса нет своей даты, поэтому он
    // входит только в месячный итог.
    var monthProfit = writeRow("profit", money, profit, true);
    var carried = Math.max(num(state.prevMinus), 0);
    var profitSum = q4(monthProfit - carried);
    var profitCell = calcCells.profit.sum;
    profitCell.textContent = money(profitSum);
    profitCell.classList.toggle("fin-neg", profitSum < 0);
    profitCell.classList.toggle("fin-pos", profitSum > 0);
    if (calcCells.profit.hint) {
      calcCells.profit.hint.textContent = carried
        ? "доход − спенд − costs − перенос " + money(carried) + " из " + previousMonthLabel()
        : "доход − спенд − costs";
    }

    writeRow("roi", percent, roi, true);
    var base = spendSum + costsSum;
    var roiSum = base > 0 ? monthProfit / base * 100 : null;
    var roiCell = calcCells.roi.sum;
    roiCell.textContent = percent(roiSum);
    roiCell.classList.toggle("fin-neg", roiSum !== null && roiSum < 0);
    roiCell.classList.toggle("fin-pos", roiSum !== null && roiSum > 0);

    renderSummary(incomeSum, spendSum, costsSum, profitSum, roiSum);
  }

  function renderSummary(incomeSum, spendSum, costsSum, profitSum, roiSum) {
    byId("finCardIncome").textContent = withSign(whole(incomeSum));
    byId("finCardSpend").textContent = withSign(whole(spendSum));
    byId("finCardCosts").textContent = withSign(money(costsSum));

    var profitCard = byId("finCardProfit");
    profitCard.textContent = withSign(money(profitSum));
    profitCard.className = "fin-card-value " +
      (profitSum < 0 ? "is-loss" : profitSum > 0 ? "is-gain" : "");

    var roiCard = byId("finCardRoi");
    roiCard.textContent = percent(roiSum);
    roiCard.className = "fin-card-value " +
      (roiSum !== null && roiSum < 0 ? "is-loss" : roiSum > 0 ? "is-gain" : "");

    // Профит уже с переносом, поэтому долг из зарплаты второй раз не вычитается.
    var computed = state.plan
      ? planSalary(state.plan, profitSum)
      : (function () {
          var step = salaryStep(profitSum);
          var pct = LADDER[step].pct;
          return { salary: profitSum < 0 ? 0 : q4(profitSum * pct / 100), percent: pct };
        })();
    renderSettlement(profitSum, computed.salary, computed.salary);

    spark(byId("finSparkIncome"), seriesOf(income), "#7E7070");
    spark(byId("finSparkSpend"), seriesOf(spend), "#7E7070");
    spark(byId("finSparkProfit"), seriesOf(profit), profitSum < 0 ? "#BE2317" : "#0E7350");
  }

  function renderSettlement(profitValue, salary, payout) {
    var step = salaryStep(profitValue);
    byId("finCardSalary").textContent = withSign(money(salary));
    byId("finPaySalary").textContent = money(salary);
    byId("finPayTotal").textContent = money(payout);

    [].forEach.call(byId("finLadderSteps").children, function (item, index) {
      item.classList.toggle("is-on", index === step);
    });
  }

  function renderAuthoritativeSettlement(total) {
    if (!total) return;
    renderSettlement(
      num(total.profit),
      num(total.salary),
      Math.max(num(total.payout), 0)
    );
  }

  function spark(svg, data, color) {
    if (!svg) return;
    svg.innerHTML = "";
    if (!data.some(function (value) { return value !== 0; })) return;
    var width = 120, height = 30, pad = 3;
    var max = Math.max.apply(null, data.concat([0]));
    var min = Math.min.apply(null, data.concat([0]));
    var span = (max - min) || 1;
    var y = function (value) { return height - pad - (value - min) / span * (height - pad * 2); };
    var x = function (index) {
      return data.length > 1 ? index / (data.length - 1) * width : 0;
    };
    var points = data.map(function (value, index) { return x(index) + "," + y(value); }).join(" ");
    var zero = y(0);
    var ns = "http://www.w3.org/2000/svg";

    var area = document.createElementNS(ns, "polygon");
    area.setAttribute("points", "0," + zero + " " + points + " " + width + "," + zero);
    area.style.fill = color;
    area.setAttribute("opacity", ".12");
    svg.appendChild(area);

    var base = document.createElementNS(ns, "line");
    base.setAttribute("x1", 0); base.setAttribute("x2", width);
    base.setAttribute("y1", zero); base.setAttribute("y2", zero);
    base.style.stroke = "currentColor";
    base.setAttribute("opacity", ".18");
    base.setAttribute("stroke-dasharray", "2 3");
    svg.appendChild(base);

    var line = document.createElementNS(ns, "polyline");
    line.setAttribute("points", points);
    line.setAttribute("fill", "none");
    line.style.stroke = color;
    line.setAttribute("stroke-width", "1.6");
    line.setAttribute("stroke-linejoin", "round");
    line.setAttribute("stroke-linecap", "round");
    line.setAttribute("vector-effect", "non-scaling-stroke");
    svg.appendChild(line);

    var last = data.length - 1;
    while (last > 0 && data[last] === 0) last -= 1;
    var dot = document.createElementNS(ns, "circle");
    dot.setAttribute("cx", x(last));
    dot.setAttribute("cy", y(data[last]));
    dot.setAttribute("r", "2.4");
    dot.style.fill = color;
    svg.appendChild(dot);
  }

  /* ---------- клавиатура ---------- */

  function buildNav() {
    navRows.forEach(function (line, row) {
      line.forEach(function (input, column) {
        input.dataset.r = row;
        input.dataset.c = column;
      });
    });
  }

  function focusCell(row, column) {
    var line = navRows[row];
    if (!line) return;
    var input = line[column];
    if (!input) return;
    input.focus();
    input.select();
  }

  function onKeydown(event) {
    var input = event.target;
    if (!input.dataset || input.dataset.r === undefined) return;
    var row = Number(input.dataset.r), column = Number(input.dataset.c);
    var key = event.key;
    if (key === "Enter") {
      event.preventDefault();
      focusCell(event.shiftKey ? row - 1 : row + 1, column);
    } else if (key === "ArrowUp") {
      event.preventDefault();
      focusCell(row - 1, column);
    } else if (key === "ArrowDown") {
      event.preventDefault();
      focusCell(row + 1, column);
    } else if (key === "ArrowLeft" && input.selectionStart === 0) {
      event.preventDefault();
      focusCell(row, column - 1);
    } else if (key === "ArrowRight" && input.selectionStart === input.value.length) {
      event.preventDefault();
      focusCell(row, column + 1);
    } else if (key === "Escape") {
      input.blur();
    }
  }

  /* Вставка из Excel: строки буфера ложатся на строки таблицы, табы — на дни. */
  function onPaste(event) {
    var input = event.target;
    if (!input.dataset || input.dataset.r === undefined) return;
    var text = (event.clipboardData || window.clipboardData).getData("text");
    if (!/[\t\n]/.test(text)) return;
    event.preventDefault();
    var row = Number(input.dataset.r), column = Number(input.dataset.c);
    text.replace(/\r/g, "").split("\n").forEach(function (line, rowOffset) {
      if (line === "") return;
      line.split("\t").forEach(function (raw, columnOffset) {
        var target = navRows[row + rowOffset] && navRows[row + rowOffset][column + columnOffset];
        if (!target || target.disabled) return;
        var value = raw.trim().replace(/\s/g, "").replace(",", ".");
        target.value = value === "" || isNaN(Number(value)) ? "" : value;
        target.dispatchEvent(new Event("input", { bubbles: true }));
      });
    });
    recalc();
  }

  var hotDay = null;
  function onHover(event) {
    var cell = event.target.closest ? event.target.closest("[data-d]") : null;
    var day = cell ? cell.dataset.d : null;
    if (day === hotDay) return;
    var grid = byId("finGrid");
    if (hotDay !== null) {
      [].forEach.call(grid.querySelectorAll('[data-d="' + hotDay + '"]'), function (node) {
        node.classList.remove("is-hot");
      });
    }
    hotDay = day;
    if (hotDay !== null) {
      [].forEach.call(grid.querySelectorAll('[data-d="' + hotDay + '"]'), function (node) {
        node.classList.add("is-hot");
      });
    }
  }

  /* ---------- сохранение ---------- */

  /* Индикатор молчит, пока всё в порядке: постоянное «Сохранено» в шапке ничего
     не сообщало, а «Сохраняю…» и так видно по кнопке синхронизации. Остаются
     только ошибки — без них неудачное сохранение прошло бы незаметно. */
  function status(kind, text) {
    var box = byId("finSaved");
    if (!box) return;
    var bad = kind === "error";
    box.hidden = !bad;
    box.classList.toggle("is-error", bad);
    box.querySelector("span").textContent = bad ? text : "";
  }

  function scheduleSave() {
    if (!state.canManage || !state.buyerId || !state.loaded) return;
    dirty = true;
    status("busy", "Сохраняю…");
    clearTimeout(saveTimer);
    saveTimer = setTimeout(function () { save().catch(function () {}); }, SAVE_DELAY);
  }

  function payload() {
    var days = {};
    for (var d = 1; d <= state.days; d += 1) {
      var manualSpend = state.manual.spendBuyer[d] != null ? num(state.manual.spendBuyer[d]) : null;
      var spendAgent = num(state.manual.spendAgent[d]);
      var dayCosts = num(state.manual.costs[d]);
      days[d] = { spend_agent: spendAgent, costs: dayCosts };
      // Only an edited cell may replace or clear a manual override.
      if (state.manualDirty[d]) days[d].manual_spend = manualSpend;
    }
    return {
      buyer_id: state.buyerId,
      year: state.year,
      month: state.month,
      tier: state.bookTab,
      eur_usd_rate: num(state.eurRate) || 1,
      days: days,
      // Строка, в которой оффер ещё не выбран, не сохраняется: у неё нет ни
      // названия, ни связи со справочником — сохранять нечего.
      offers: state.offers.filter(function (offer) {
        return !offer.picking;
      }).map(function (offer) {
        return {
          name: (offer.name || "").trim() || "Без названия",
          partner: (offer.partner || "").trim() || null,
          geo: offer.geo || null,
          source_offer_id: offer.sourceOfferId || null,
          locked_fields: offer.lockedFields || [],
          sync_from_catalog: !!offer.syncFromCatalog,
          rate: num(offer.rate),
          rate_currency: offer.rateCurrency === "EUR" ? "EUR" : "USD",
          tags: offer.tags.map(function (tag) {
            var values = {};
            Object.keys(tag.values).forEach(function (day) {
              if (num(tag.values[day])) values[day] = num(tag.values[day]);
            });
              // Имя не подставляем: безымянный тег так и хранится безымянным.
            return { name: (tag.name || "").trim(), values: values };
          })
        };
      })
    };
  }

  /* Переключение месяца или баера перерисовывает state с нуля, поэтому
   * недописанное сохранение сначала доводится до конца — иначе последняя
   * набранная цифра пропадает, а с ней и доверие к автосохранению. */
  async function flushSave() {
    clearTimeout(saveTimer);
    // Переход не должен обгонять уже отправленный PUT: следующий месяц берёт
    // перенос именно из его результата. Ошибку не проглатываем — при сбое
    // остаёмся в текущем месяце с dirty=true и даём пользователю повторить.
    if (activeSave) await activeSave;
    while (state.loaded && dirty) {
      await save();
    }
  }

  function save() {
    if (!state.loaded || !state.buyerId) return;
    if (activeSave) {
      // Сохранение уже в пути: новые правки уедут следующим заходом.
      clearTimeout(saveTimer);
      return activeSave;
    }
    var requestPayload = payload();
    var sentManual = Object.assign({}, state.manualDirty);
    dirty = false;
    activeSave = (async function () {
      try {
        var savedBook = await api.put("/finance/book", requestPayload);
        Object.keys(sentManual).forEach(function (day) {
          if (state.manualDirty[day] === sentManual[day]) delete state.manualDirty[day];
        });
        // Перенос принадлежит серверу. Если параллельно не появилась новая
        // локальная правка, сразу принимаем пересчитанный остаток из ответа.
        if (!dirty && savedBook && savedBook.buyer &&
            savedBook.buyer.id === requestPayload.buyer_id &&
            savedBook.year === requestPayload.year &&
            savedBook.month === requestPayload.month &&
            state.buyerId === requestPayload.buyer_id &&
            state.year === requestPayload.year && state.month === requestPayload.month) {
          state.mediaSpend = {};
          Object.keys(state.manual.spendBuyer).forEach(function (day) { delete state.manual.spendBuyer[day]; });
          Object.keys(savedBook.days || {}).forEach(function (day) {
            var entry = savedBook.days[day];
            state.mediaSpend[Number(day)] = num(entry.media_spend);
            if (entry.manual_spend != null) state.manual.spendBuyer[Number(day)] = num(entry.manual_spend);
          });
          // Ответ принёс и пересчитанный медиаборд, и оставшиеся замки.
          repaintSpend();
          state.prevMinus = num(savedBook.prev_minus);
          // Правило могли поменять в Настройках, пока книга была открыта:
          // ответ на сохранение приносит актуальную шкалу.
          state.plan = savedBook.salary_plan || null;
          state.ladder = stepsFromPlan(state.plan);
          renderLadder();
          recalc();
          renderAuthoritativeSettlement(savedBook.totals && savedBook.totals.total);
        }
        status("ok", dirty ? "Сохраняю…" : "Сохранено");
      } catch (error) {
        dirty = true;
        status("error", error && error.message ? error.message : "Не сохранилось");
        throw error;
      } finally {
        activeSave = null;
        if (dirty) {
          clearTimeout(saveTimer);
          saveTimer = setTimeout(function () { save().catch(function () {}); }, SAVE_DELAY);
        }
      }
    })();
    return activeSave;
  }

  /* ---------- загрузка ---------- */

  function applyBook(book) {
    state.days = book.days_in_month;
    state.plan = book.salary_plan || null;
    state.ladder = stepsFromPlan(state.plan);
    state.prevMinus = num(book.prev_minus);
    state.eurRate = num(book.eur_usd_rate) || 1;
    state.mediaSpend = {};
    state.manualDirty = {};
    state.manual = { spendBuyer: {}, spendAgent: {}, costs: {} };
    state.buyerTags = (book.buyer && book.buyer.tags) || [];
    Object.keys(book.days || {}).forEach(function (day) {
      var entry = book.days[day];
      state.mediaSpend[Number(day)] = num(entry.media_spend);
      if (entry.manual_spend != null) state.manual.spendBuyer[Number(day)] = num(entry.manual_spend);
      if (num(entry.spend_agent)) state.manual.spendAgent[Number(day)] = num(entry.spend_agent);
      if (num(entry.costs)) state.manual.costs[Number(day)] = num(entry.costs);
    });
    state.offers = (book.offers || []).map(function (offer) {
      return {
        name: offer.name,
        partner: offer.partner || "",
        geo: offer.geo || null,
        sourceOfferId: offer.source_offer_id || null,
        // ID у партнёрки — только для чтения: правят его в разделе «Оффера».
        externalId: offer.external_id || "",
        rate: num(offer.rate),
        rateCurrency: offer.rate_currency === "EUR" ? "EUR" : "USD",
        lockedFields: offer.locked_fields || [],
        syncFromCatalog: false,
        tags: (offer.tags || []).map(function (tag) {
          var values = {};
          Object.keys(tag.values || {}).forEach(function (day) {
            values[Number(day)] = num(tag.values[day]);
          });
          return { name: tag.name, values: values };
        })
      };
    });
  }

  async function loadBook() {
    if (!state.buyerId) return;
    var request = {
      buyerId: state.buyerId,
      year: state.year,
      month: state.month,
      sequence: ++loadSequence
    };
    clearTimeout(saveTimer);
    state.loaded = false;
    dirty = false;
    status("busy", "Загружаю…");
    var book;
    try {
      book = await api.get("/finance/book?buyer_id=" + encodeURIComponent(request.buyerId) +
        "&year=" + request.year + "&month=" + request.month +
        "&tier=" + state.bookTab);
    } catch (error) {
      if (request.sequence !== loadSequence) return;
      throw error;
    }
    // Быстрый второй клик уже мог запустить другой GET. Запоздавший ответ
    // нельзя применять к новому state — иначе строки одного месяца окажутся
    // под другим заголовком и могут уехать обратно на сервер через PUT.
    if (request.sequence !== loadSequence ||
        request.buyerId !== state.buyerId ||
        request.year !== state.year || request.month !== state.month) return;
    applyBook(book);
    renderSpendWarning(book.unassigned_spend);
    renderPeriod();
    renderLadder();
    renderCurrency();
    renderHead();
    renderBody();
    recalc();
    renderAuthoritativeSettlement(book.totals && book.totals.total);
    state.loaded = true;
    status("ok", "Сохранено");
  }

  function sheetTitle() {
    if (state.sheet.indexOf("buyer:") !== 0 || state.bookTab === "overview") {
      return "по дням";
    }
    return "по дням · " + (state.bookTab === "T1" ? "Tier1" : "Tier2/3");
  }

  function renderPeriod() {
    var month = MONTHS[state.month - 1];
    var label = month.charAt(0).toUpperCase() + month.slice(1) + " " + state.year;
    byId("finPeriod").textContent = label;
    byId("finSheetTitle").textContent = label + " · " + sheetTitle();
  }

  function renderCurrency() {
    var input = byId("finEurRate");
    // Поле не перезаписывается, пока в нём набирают: иначе «1.0» превратится
    // в «1» под пальцами.
    if (document.activeElement !== input) input.value = state.eurRate;
    input.disabled = !state.canManage;
  }

  function renderLadder() {
    var list = byId("finLadderSteps");
    list.innerHTML = "";
    var title = byId("finLadderRule");
    if (title) {
      title.textContent = state.plan && state.plan.rule ? state.plan.rule : "";
      title.style.display = state.plan && state.plan.rule ? "" : "none";
    }
    ladder().forEach(function (step) {
      var item = el("li");
      item.appendChild(el("span", "fin-st-pct", step.pct + "%"));
      item.appendChild(el("span", "fin-st-range", step.range));
      list.appendChild(item);
    });
  }

  /* Полоса колонки — та же группировка, что в книге и в Медиаборде: что
     потрачено, что заработано. Числа в семь столбцов без этого читаются как
     одна сплошная простыня. */
  var SUMMARY_BANDS = {
    spend: "cost", costs: "cost", salary: "cost",
    income: "offer", profit: "result", roi: "result"
  };

  function bandClass(key, previousKey) {
    var band = SUMMARY_BANDS[key];
    if (!band) return "";
    var opens = SUMMARY_BANDS[previousKey] !== band;
    return " fin-col--" + band + (opens ? " is-band-start" : "");
  }

  function summaryCell(key, value, previousKey, plain) {
    var formatted = key === "roi" ? summaryPercent(value) : summaryMoney(value);
    var tone = key === "profit" || key === "roi"
      ? (num(value) < 0 ? " fin-neg" : num(value) > 0 ? " fin-pos" : "")
      : "";
    var band = plain ? "" : bandClass(key, previousKey);
    return '<td class="' + (tone + band).trim() + '">' +
      escapeHtml(formatted) + "</td>";
  }

  /* plain — таблица без цветных блоков: у «По тирам» колонки различает сама
     подпись, а полосы делали из трёх строк пёструю сетку. */
  function summaryTable(columns, rows, total, plain) {
    var head = columns.map(function (column, index) {
      var band = index === 0 || column.text || plain
        ? ""
        : bandClass(column.key, index ? (columns[index - 1] || {}).key : null);
      return '<th class="' + band.trim() + '">' + escapeHtml(column.label) + "</th>";
    }).join("");
    var body = rows.map(function (row) {
      return "<tr>" + columns.map(function (column, index) {
        if (index === 0 || column.text) {
          return "<td>" + escapeHtml(row[column.key]) + "</td>";
        }
        return summaryCell(column.key, row[column.key],
          (columns[index - 1] || {}).key, plain);
      }).join("") + "</tr>";
    }).join("");
    if (total) {
      body += '<tr class="fin-summary-total"><td>ИТОГО</td>' +
        columns.slice(1).map(function (column, index) {
          if (column.text) return "<td></td>";
          return summaryCell(column.key, total[column.key],
            (columns[index] || {}).key, plain);
        }).join("") + "</tr>";
    }
    // Шапку из пустых ячеек не рисуем: в таблице «имя — сумма» подписи не
    // нужны, а серая полоса поверх строк читается как обрезанная строка.
    var titled = columns.some(function (column) { return column.label; });
    return '<table class="fin-grid fin-summary-grid">' +
      (titled ? "<thead><tr>" + head + "</tr></thead>" : "") +
      "<tbody>" + body + "</tbody></table>";
  }

  /* Дни всегда рисуются сеткой книги: показатели строками, дни столбцами,
     слева «За месяц». Это тот же вид, в котором данные и заполняют, — читать
     и сверять их глазами можно только так. Список из тридцати строк для этого
     не годится. */
  function dailyGrid(rows, total) {
    var cell = function (value) { return money(q4(value)); };
    var lines = [
      { key: "spend", label: "Спенд", format: cell },
      { key: "costs", label: "Costs", format: cell },
      { key: "income", label: "Доход", format: cell },
      { key: "profit", label: "Профит", format: cell, tone: true },
      {
        key: "roi",
        label: "ROI",
        tone: true,
        format: function (value) {
          return value === null || value === undefined ? "–" : percent(num(value));
        }
      }
    ];
    var head = '<th class="fin-c-name"><span class="fin-h-lab">Показатель</span></th>' +
      '<th class="fin-c-sum"><span class="fin-h-lab">За месяц</span></th>' +
      rows.map(function (row) {
        var flags = dayFlags(row.day);
        return '<th class="fin-day' + (flags.off ? " is-off" : "") +
          (flags.today ? " is-today" : "") + '"><span class="fin-day-n">' + row.day +
          '</span><span class="fin-day-w">' + flags.weekday + "</span></th>";
      }).join("");
    var body = lines.map(function (line) {
      var band = SUMMARY_BANDS[line.key];
      return '<tr class="fin-calc' + (band ? " fin-row--" + band : "") +
        '"><td class="fin-c-name"><span class="fin-r-lab">' +
        escapeHtml(line.label) + '</span></td><td class="fin-c-sum">' +
        escapeHtml(line.format(total[line.key])) + "</td>" +
        rows.map(function (row) {
          var value = row[line.key];
          var tone = line.tone && num(value) < 0
            ? " fin-neg"
            : line.tone && num(value) > 0 ? " fin-pos" : "";
          return '<td class="fin-cell' + tone + (num(value) ? "" : " is-zero") + '">' +
            escapeHtml(line.format(value)) + "</td>";
        }).join("") + "</tr>";
    }).join("");
    return '<table class="fin-grid fin-summary-days"><thead><tr>' + head +
      "</tr></thead><tbody>" + body + "</tbody></table>";
  }

  function renderSummaryCards(cards) {
    byId("finCardIncome").textContent = summaryMoney(cards.income);
    byId("finCardSpend").textContent = summaryMoney(cards.spend);
    byId("finCardCosts").textContent = summaryMoney(cards.costs);
    var profitNode = byId("finCardProfit");
    profitNode.textContent = summaryMoney(cards.profit);
    profitNode.className = "fin-card-value " +
      (num(cards.profit) < 0 ? "is-loss" : num(cards.profit) > 0 ? "is-gain" : "");
    var roiNode = byId("finCardRoi");
    roiNode.textContent = summaryPercent(cards.roi);
    roiNode.className = "fin-card-value " +
      (num(cards.roi) < 0 ? "is-loss" : num(cards.roi) > 0 ? "is-gain" : "");
    ["finSparkIncome", "finSparkSpend", "finSparkProfit"].forEach(function (id) {
      byId(id).innerHTML = "";
    });
  }

  function setView(summaryMode) {
    var partnersMode = state.sheet === "partners";
    byId("finSummaryView").hidden = !summaryMode || partnersMode;
    byId("finBookSheet").hidden = summaryMode;
    byId("finBookSalary").hidden = summaryMode;
    byId("finCardSalary").closest(".fin-card").hidden = summaryMode;
    // Сводка «Партнёрки» — три отдельных блока: карточки, фильтры и таблица.
    Array.prototype.forEach.call(document.querySelectorAll(".fin-partners-part"),
      function (part) { part.hidden = !partnersMode; });
    // Карточки месяца считаются по книге или сводке — в общем листе их нет.
    var cards = byId("finCards");
    if (cards) cards.hidden = partnersMode;
  }

  /* Фонд по ролям — той же таблицей, что команда вела в своей: колонка на
     роль, строка на тир. У СМО тира нет: он смотрит за всеми командами сразу,
     поэтому его сумма стоит одной ячейкой на всю колонку. */
  var SALARY_ROLES = [
    { category: "buyers", label: "ЗП Баеров" },
    { category: "team_leads", label: "ЗП ТЛов" },
    { category: "cmo", label: "ЗП СМО", flat: true },
    { category: "other", label: "Другие роли", flat: true, optional: true }
  ];
  var SALARY_TIER_ROWS = [
    { tier: "T1", label: "Tier1" },
    { tier: "T23", label: "Tier2/3" },
    { tier: "mixed", label: "Оба тира", optional: true },
    { tier: "unassigned", label: "Без тира", optional: true }
  ];

  function salaryRoles(groups) {
    var cells = {};
    groups.forEach(function (group) {
      cells[group.category + "|" + group.tier] = group.amount;
    });
    var has = function (test) {
      return groups.some(function (group) {
        return test(group) && num(group.amount);
      });
    };
    var columns = SALARY_ROLES.filter(function (role) {
      if (role.flat) return false;
      return !role.optional || has(function (group) {
        return group.category === role.category;
      });
    });
    var rows = SALARY_TIER_ROWS.filter(function (row) {
      return !row.optional || has(function (group) {
        return group.tier === row.tier && !columnIsFlat(group.category);
      });
    });
    var head = "<th></th>" + columns.map(function (role) {
      return "<th>" + escapeHtml(role.label) + "</th>";
    }).join("");
    var body = rows.map(function (row) {
      return '<tr><td class="fin-role-side">' + escapeHtml(row.label) + "</td>" +
        columns.map(function (role) {
          return "<td>" +
            escapeHtml(summaryMoney(cells[role.category + "|" + row.tier] || 0)) +
            "</td>";
        }).join("") + "</tr>";
    }).join("");
    return '<table class="fin-grid fin-summary-grid fin-roles__grid">' +
      "<thead><tr>" + head + "</tr></thead><tbody>" + body + "</tbody></table>";
  }

  /* Роли без тира — колонкой рядом с таблицей: заголовок на уровне «ЗП по
     тирам», сумма под ним. СМО стоит над командами, и в разбивке по тирам ему
     места нет. */
  function salaryApart(groups) {
    var cells = {};
    groups.forEach(function (group) {
      cells[group.category + "|" + group.tier] = group.amount;
    });
    return SALARY_ROLES.filter(function (role) {
      return role.flat && (!role.optional || num(flatAmount(cells, role.category)));
    }).map(function (role) {
      return '<div class="fin-role-card"><span>' + escapeHtml(role.label) +
        "</span><b>" +
        escapeHtml(summaryMoney(flatAmount(cells, role.category))) + "</b></div>";
    }).join("");
  }

  function columnIsFlat(category) {
    return SALARY_ROLES.some(function (role) {
      return role.category === category && role.flat;
    });
  }

  function flatAmount(cells, category) {
    var total = 0;
    Object.keys(cells).forEach(function (key) {
      if (key.indexOf(category + "|") === 0) total += num(cells[key]);
    });
    return total;
  }

  function renderSpendWarning(amount) {
    var warning = byId("finSpendWarning");
    if (!warning) {
      warning = el("div");
      warning.id = "finSpendWarning";
      warning.setAttribute("role", "status");
      warning.style.cssText = "padding:12px 16px;margin:12px 0;border:1px solid #E6C59A;border-radius:10px;background:#FFF8EE;color:#775021;font-size:12px";
      byId("finBookTabs").parentNode.insertBefore(warning, byId("finBookTabs"));
    }
    warning.hidden = !num(amount);
    warning.textContent = "Спенд без GEO: " + money(num(amount)) +
      ". Он не включён в таблицы по тирам. Укажите страну у оффера в медиаборде.";
  }

  function renderFinanceSummary(data) {
    renderSpendWarning(data.unassigned_spend);
    setView(true);
    renderSummaryCards(data.cards);
    var teamSummary = state.sheet.indexOf("team:") === 0;
    var moneyColumns = [
      { key: "spend", label: "Спенд" },
      { key: "income", label: "Доход" },
      { key: "costs", label: "Costs" },
      { key: "profit", label: "Профит" },
      { key: "roi", label: "ROI" }
    ];
    var tierColumns = [{ key: "name", label: "Тир" }].concat(moneyColumns);
    var buyerColumns = [{ key: "buyer", label: "Баер" }];
    if (teamSummary) {
      buyerColumns.push({ key: "role", label: "Роль", text: true });
    }
    buyerColumns = buyerColumns.concat(moneyColumns);
    byId("finSummaryTiersBlock").hidden = !data.tiers.length;
    byId("finSummaryTiers").innerHTML = summaryTable(tierColumns, data.tiers, data.cards, true);
    byId("finSummaryBuyersBlock").hidden = !data.buyers.length;
    byId("finSummaryBuyersTitle").textContent = teamSummary
      ? "Тимлид и команда" : "По баерам";
    byId("finSummaryBuyers").innerHTML = summaryTable(
      buyerColumns, data.buyers, data.cards, true
    );
    // Показываем по данным, а не по имени листа: в общей сводке баера лист
    // называется «buyer:…», и проверка на «all» прятала блок вместе с числами.
    byId("finSummaryDailyBlock").hidden = !(data.daily || []).length;
    byId("finSummaryDaily").innerHTML = dailyGrid(data.daily, data.cards);

    var salary = data.salary;
    // Разбивка по ролям — только когда есть что разбивать: у баера групп нет,
    // и его начисление целиком видно строкой фонда и в «Общей ЗП по тирам».
    var groups = salary ? salary.groups || [] : [];
    byId("finSalaryFundRow").hidden = !salary;
    byId("finSummarySalaryBlock").hidden = !groups.length;
    if (salary) {
      byId("finSalaryFundLabel").textContent = salary.label
        || (teamSummary ? "Фонд ЗП команды" : "Фонд ЗП компании");
      byId("finSalaryFund").textContent = summaryMoney(salary.total);
      byId("finSalaryRoles").hidden = !groups.length;
      byId("finSalaryRoles").innerHTML = salaryRoles(groups);
      var apart = groups.length ? salaryApart(groups) : "";
      byId("finSalaryApart").hidden = !apart;
      byId("finSalaryApart").innerHTML = apart;
      byId("finSalaryTiersBlock").hidden = !(salary.tiers || []).length;
      byId("finSalaryTiers").innerHTML = summaryTable(
        [{ key: "name", label: "Тир" }, { key: "amount", label: "Начислено" }],
        salary.tiers || [],
        null
      );
      var people = salary.people || [];
      byId("finSummaryPeopleBlock").hidden = !people.length;
      byId("finSummaryPeople").innerHTML = summaryTable(
        [{ key: "user_name", label: "Сотрудник" }, { key: "payout", label: "К выплате" }],
        people,
        null
      );
    } else {
      byId("finSalaryTiersBlock").hidden = true;
      byId("finSummaryPeopleBlock").hidden = true;
    }
  }

  function menuTitle(text) {
    var item = el("li", "fin-menu-title", text);
    item.setAttribute("role", "presentation");
    return item;
  }

  function renderBuyerMenu() {
    var menu = byId("finBuyerMenu");
    menu.innerHTML = "";
    // Роли «Только свои данные» сводки не приходят: заголовок без списка
    // выглядел бы поломкой.
    if ((state.scopes.summaries || []).length) menu.appendChild(menuTitle("Сводки"));
    (state.scopes.summaries || []).forEach(function (summary) {
      var item = el("li", null, summary.name);
      item.setAttribute("role", "option");
      item.dataset.sheet = summary.scope;
      item.setAttribute("aria-selected", String(summary.scope === state.sheet));
      menu.appendChild(item);
    });
    if ((state.scopes.summaries || []).length) {
      var partners = el("li", null, "Партнёрки");
      partners.setAttribute("role", "option");
      partners.dataset.sheet = "partners";
      partners.setAttribute("aria-selected", String(state.sheet === "partners"));
      menu.appendChild(partners);
    }
    if ((state.scopes.teams || []).length) {
      menu.appendChild(menuTitle("Команды"));
      state.scopes.teams.forEach(function (team) {
        var item = el("li", null, 'Сводка по "' + team.name + '"');
        item.setAttribute("role", "option");
        item.dataset.sheet = "team:" + team.id;
        item.setAttribute("aria-selected", String(item.dataset.sheet === state.sheet));
        menu.appendChild(item);
      });
    }
    menu.appendChild(menuTitle("Баеры"));
    (state.scopes.buyers || []).forEach(function (buyer) {
      var item = el("li", null, buyer.name);
      // Заблокированный не теряет книг: они на месте, просто вход закрыт.
      if (buyer.blocked) item.appendChild(el("span", "fin-menu-muted", " · заблокирован"));
      item.setAttribute("role", "option");
      item.dataset.sheet = "buyer:" + buyer.id;
      item.setAttribute("aria-selected", String(item.dataset.sheet === state.sheet));
      menu.appendChild(item);
    });
  }

  /* Что открыть первым. Обычно это «Общая», но роли с доступом только к своим
     данным сводок не видят — ей открывается собственная книга. */
  function defaultSheet() {
    var summaries = state.scopes.summaries || [];
    if (summaries.length) return summaries[0].scope;
    var buyers = state.scopes.buyers || [];
    return buyers.length ? "buyer:" + buyers[0].id : "all";
  }

  function sheetName(sheet) {
    if (sheet === "partners") {
      return (state.scopes.summaries || []).length ? "Партнёрки" : null;
    }
    var summary = (state.scopes.summaries || []).find(function (item) {
      return item.scope === sheet;
    });
    if (summary) return summary.name;
    if (sheet.indexOf("team:") === 0) {
      var teamId = sheet.slice(5);
      var team = (state.scopes.teams || []).find(function (item) {
        return item.id === teamId;
      });
      return team ? 'Сводка по "' + team.name + '"' : null;
    }
    if (sheet.indexOf("buyer:") === 0) {
      var buyerId = sheet.slice(6);
      var buyer = (state.scopes.buyers || []).find(function (item) {
        return item.id === buyerId;
      });
      return buyer ? buyer.name : null;
    }
    return null;
  }

  async function loadScopes() {
    state.scopes = await api.get(
      "/finance/scopes?year=" + state.year + "&month=" + state.month
    );
    state.buyers = state.scopes.buyers || [];
    state.countries = state.scopes.countries || [];
    state.partners = state.scopes.partners || [];
  }

  function renderBookTabs() {
    var host = byId("finBookTabs");
    if (!host) return;
    Array.prototype.forEach.call(host.querySelectorAll("[data-book-tab]"),
      function (tab) {
        var active = tab.getAttribute("data-book-tab") === state.bookTab;
        tab.setAttribute("aria-selected", String(active));
      });
  }

  /* Общая сводка баера: обе его таблицы за месяц, только чтение. Заполняют
     Tier1 и Tier2/3 по отдельности — здесь отвечают на вопрос «сколько
     всего», не смешивая числа. */
  async function loadBuyerOverview() {
    var request = {
      buyerId: state.buyerId,
      year: state.year,
      month: state.month,
      sequence: ++loadSequence
    };
    state.loaded = false;
    var data = await api.get(
      "/finance/buyer-overview?buyer_id=" + encodeURIComponent(request.buyerId) +
      "&year=" + request.year + "&month=" + request.month
    );
    if (request.sequence !== loadSequence) return;
    renderPeriod();
    renderFinanceSummary({
      title: "",
      unassigned_spend: data.unassigned_spend,
      cards: data.cards,
      tiers: data.tiers,
      daily: data.daily,
      buyers: [],
      salary: data.salary
    });
  }

  async function loadSummary() {
    var request = {
      sheet: state.sheet,
      year: state.year,
      month: state.month,
      sequence: ++loadSequence
    };
    state.loaded = false;
    var data = await api.get(
      "/finance/summary?year=" + request.year + "&month=" + request.month +
      "&scope=" + encodeURIComponent(request.sheet)
    );
    if (request.sequence !== loadSequence || request.sheet !== state.sheet ||
        request.year !== state.year || request.month !== state.month) return;
    renderPeriod();
    renderFinanceSummary(data);
  }

  async function loadSelection() {
    var isBuyer = state.sheet.indexOf("buyer:") === 0;
    if (state.sheet === "partners") {
      byId("finBookTabs").hidden = true;
      state.buyerId = null;
      setView(true);
      renderPeriod();
      await loadPartnersView();
      return;
    }
    byId("finBookTabs").hidden = !isBuyer;
    renderBookTabs();
    if (isBuyer) {
      state.buyerId = state.sheet.slice(6);
      if (state.bookTab === "overview") {
        setView(true);
        await loadBuyerOverview();
      } else {
        setView(false);
        await loadBook();
      }
    } else {
      state.buyerId = null;
      await loadSummary();
    }
  }

  async function selectBuyer(sheet, remember) {
    if (!sheetName(sheet) || sheet === state.sheet) return;
    if (state.sheet.indexOf("buyer:") === 0) await flushSave();
    state.sheet = sheet;
    // Новый лист открывается общей сводкой: сначала смотрят, потом заполняют.
    state.bookTab = "overview";
    state.buyerName = sheetName(sheet);
    byId("finBuyerName").textContent = state.buyerName;
    renderBuyerMenu();
    if (remember !== false) {
      api.put("/me/preferences/finance.sheet", { value: { sheet: sheet } })
        .catch(function () {});
    }
    await loadSelection();
  }

  async function switchMonth(delta) {
    if (state.sheet.indexOf("buyer:") === 0) await flushSave();
    shiftMonth(delta);
    renderPeriod();
    await loadScopes();
    if (!sheetName(state.sheet)) state.sheet = defaultSheet();
    state.buyerName = sheetName(state.sheet);
    byId("finBuyerName").textContent = state.buyerName;
    renderBuyerMenu();
    await loadSelection();
  }

  function shiftMonth(delta) {
    var month = state.month + delta;
    var year = state.year;
    if (month < 1) { month = 12; year -= 1; }
    if (month > 12) { month = 1; year += 1; }
    state.month = month;
    state.year = year;
  }

  /* ---------- сборка ---------- */


  /* ---------- депозиты из партнёрок ----------
   *
   * Сервис партнёрок ничего не собирает сам: он идёт в ПП только когда его
   * попросят. Поэтому кнопка живёт здесь, рядом с таблицей, — период берётся
   * из открытого месяца, офферы и теги из неё же. Ничего указывать не нужно:
   * ID оффера у партнёрки стоит в разделе «Оффера», тег уже заведён в книге.
   */

  async function syncPartners() {
    if (!state.buyerId) return;
    var button = byId("finPartnerSync");
    if (!button || button.disabled) return;
    button.disabled = true;
    var label = button.querySelector("span");
    var before = label ? label.textContent : "";
    if (label) label.textContent = "Тянем из ПП…";
    status("busy", "Тянем депозиты из партнёрок…");
    try {
      var result = await api.post(
        "/finance/book/sync-partners?buyer_id=" + encodeURIComponent(state.buyerId) +
        "&year=" + state.year + "&month=" + state.month + "&tier=" + state.bookTab,
        {}
      );
      var text = "Депозитов записано: " + result.upserted;
      if (result.pending) text += " · без строки: " + result.pending;
      notify(text, result);
      // Таблица могла заполниться — перечитываем её.
      await loadBook();
      status("ok", "Сохранено");
    } catch (error) {
      status("error", error && error.message ? error.message : "Синк не удался");
    } finally {
      button.disabled = false;
      if (label) label.textContent = before;
    }
  }

  /* Итог синка коротко: сколько записано и сколько не легло, а из подробностей
     — только то, что требует действия. Разбор по каждому тегу («такой строки в
     финансах нет») занимал пол-экрана и повторял одну и ту же мысль: заведите
     тег в книге. Ошибки интеграций показываем как есть — это не шум. */
  function notify(text, result) {
    var details = [];
    (result && result.errors ? result.errors : []).forEach(function (row) {
      details.push(row);
    });
    if (result && result.pending) {
      details.push(
        "Заведите тег под нужным оффером в книге — следующий синк разложит их сам."
      );
    }
    if (window.CelestialShell && window.CelestialShell.notify) {
      window.CelestialShell.notify({
        title: text,
        message: details.join("\n")
      });
      return;
    }
    window.alert(text + (details.length ? "\n\n" + details.join("\n") : ""));
  }

  /* ---------- сводка «Партнёрки» ----------
   *
   * Общий лист, из которого удобно заполнять сразу несколько книг: строки
   * сгруппированы баерами, внутри баера — его офферы, внутри оффера — теги.
   * Пока это только разметка: ячейки не редактируются и ничего не сохраняют.
   */

  // Разметка строк листа: где чьи суммы и какие баеры сейчас на экране.
  var partnersCells = {};
  var partnersVisible = [];
  // Что ещё не доехало на сервер, таймер отправки и счётчик новых тегов.
  var partnersFilters = { users: null, partners: null };
  var partnersPending = {};
  var partnersTimer = null;
  var partnersUid = 0;

  async function loadPartnersView() {
    var view = state.partnersView;
    byId("finPartnersCount").textContent = "Загружаю…";
    try {
      var payload = await api.get(
        "/finance/partners?year=" + state.year + "&month=" + state.month
      );
      // Числа приходят строками с четырьмя знаками (Numeric в базе) —
      // в ячейке должно стоять «4», а не «4,0000».
      view.buyers = ((payload && payload.buyers) || []).map(function (buyer) {
        return {
          id: buyer.id,
          name: buyer.name,
          eur_usd_rate: num(buyer.eur_usd_rate) || 1,
          offers: (buyer.offers || []).map(function (offer) {
            return Object.assign({}, offer, {
              rate: num(offer.rate),
              tags: (offer.tags || []).map(function (tag) {
                var values = {};
                Object.keys(tag.values || {}).forEach(function (day) {
                  values[Number(day)] = num(tag.values[day]);
                });
                return { id: tag.id, name: tag.name || "", values: values };
              })
            });
          })
        };
      });
      view.names = (payload && payload.partners) || [];
      view.loaded = true;
      if (payload && payload.days_in_month) state.days = payload.days_in_month;
    } catch (error) {
      view.buyers = [];
      byId("finPartnersCount").textContent = error && error.message
        ? error.message : "Не удалось загрузить сводку";
      return;
    }
    renderPartnersFilters();
    renderPartners();
  }

  /* Кто попадёт в лист: баеры с раздаными офферами. Отмеченные в фильтре —
     они и остаются, пустой фильтр означает «все». */
  function partnersRows() {
    var view = state.partnersView;
    var people = pickedKeys(view.users);
    var partners = pickedKeys(view.partners);
    return view.buyers.filter(function (buyer) {
      return !people.length || people.indexOf(buyer.id) >= 0;
    }).map(function (buyer) {
      return {
        id: buyer.id,
        name: buyer.name,
        eurRate: num(buyer.eur_usd_rate) || 1,
        offers: (buyer.offers || []).filter(function (offer) {
          return !partners.length || partners.indexOf(offer.partner || "") >= 0;
        })
      };
    }).filter(function (buyer) { return buyer.offers.length; });
  }

  function partnersPeople() {
    return state.partnersView.buyers.map(function (buyer) {
      return { id: buyer.id, name: buyer.name };
    });
  }

  function partnersNames() {
    return (state.partnersView.names || []).slice();
  }

  /* Фильтры — тот же компонент, что над Медиабордом: поиск, выбранное чипами
     в самом поле, в списке только невыбранное. */
  function renderPartnersFilters() {
    if (partnersFilters.users) {
      partnersFilters.users.setItems(partnersPeople().map(function (person) {
        return { value: person.id, label: person.name };
      }));
    }
    if (partnersFilters.partners) {
      partnersFilters.partners.setItems(partnersNames().map(function (name) {
        return { value: name, label: name };
      }));
    }
  }

  function pickedKeys(filter) {
    return filter && filter.values ? filter.values() : [];
  }

  /* Строка оффера — того же вида, что в книге баера: партнёрка, гео и ставка
     стоят справа от названия, под тегами считается доход. */

  function partnersRate(offer, eurRate) {
    return offer.rate_currency === "EUR"
      ? num(offer.rate) * (num(eurRate) || 1)
      : num(offer.rate);
  }

  /* Теги строки — те же, что в книге баера: один оффер льют с нескольких
     связок, и депозиты по ним считают отдельно. Пустая строка нужна всегда:
     иначе в оффер без тегов нечего вводить. */
  function partnersTags(offer) {
    if (!offer.tags) offer.tags = [];
    if (!offer.tags.length) offer.tags.push({ name: "", values: {} });
    return offer.tags;
  }

  function partnersDeposits(offer, day) {
    return partnersTags(offer).reduce(function (sum, tag) {
      return sum + num((tag.values || {})[day]);
    }, 0);
  }

  /* Сохранение идёт теми же строками, что и правились: сервер кладёт их в
     книгу нужного баера и возвращает id заведённых строк. */
  function partnersMarkDirty(buyer, offer, tag) {
    if (!state.canManage) return;
    tag.dirty = true;
    tag.buyerId = buyer.id;
    tag.offer = offer;
    partnersPending[buyer.id + "|" + (offer.book_offer_id || offer.source_offer_id) +
      "|" + (tag.id || tag.uid || (tag.uid = "new" + (partnersUid += 1)))] = {
      buyer: buyer, offer: offer, tag: tag
    };
    status("busy", "Сохраняю…");
    clearTimeout(partnersTimer);
    partnersTimer = setTimeout(function () {
      savePartners().catch(function () {});
    }, SAVE_DELAY);
  }

  function partnersEntry(item, drop) {
    var tag = item.tag;
    var values = {};
    for (var d = 1; d <= state.days; d += 1) {
      // Пустую ячейку отправляем нулём: так сервер стирает прежнее число.
      values[d] = tag.values && tag.values[d] != null ? tag.values[d] : 0;
    }
    return {
      buyer_id: item.buyer.id,
      book_offer_id: item.offer.book_offer_id || null,
      source_offer_id: item.offer.source_offer_id || null,
      tag_id: tag.id || null,
      name: tag.name || "",
      values: drop ? {} : values,
      drop: !!drop
    };
  }

  async function savePartners() {
    var keys = Object.keys(partnersPending);
    if (!keys.length || state.partnersView.saving) return;
    var batch = keys.map(function (key) { return partnersPending[key]; });
    partnersPending = {};
    state.partnersView.saving = true;
    try {
      var answer = await api.put("/finance/partners", {
        year: state.year,
        month: state.month,
        tags: batch.map(function (item) { return partnersEntry(item, item.drop); })
      });
      (answer.saved || []).forEach(function (row, index) {
        var item = batch[index];
        if (!item) return;
        item.offer.book_offer_id = row.book_offer_id;
        item.offer.tier = row.tier;
        item.tag.id = row.tag_id;
        item.tag.dirty = false;
      });
      status("ok", "");
    } catch (error) {
      // Не сохранилось — возвращаем строки в очередь, чтобы следующий ввод
      // или повтор отправил их снова, а не потерял.
      batch.forEach(function (item) {
        partnersPending[item.buyer.id + "|" + (item.offer.book_offer_id ||
          item.offer.source_offer_id) + "|" + (item.tag.id || item.tag.uid)] = item;
      });
      status("error", error && error.message ? error.message : "Не удалось сохранить");
    } finally {
      state.partnersView.saving = false;
    }
    if (Object.keys(partnersPending).length) {
      clearTimeout(partnersTimer);
      partnersTimer = setTimeout(function () { savePartners().catch(function () {}); }, SAVE_DELAY);
    }
  }

  function partnersOfferRow(buyer, offer) {
    var tr = el("tr", "fin-offer-head");
    var td = el("td", "fin-c-name");
    var wrap = el("div", "fin-o-wrap");
    wrap.appendChild(el("i", "fin-o-dot"));
    var name = document.createElement("input");
    name.className = "fin-o-name";
    name.value = offer.name || "";
    name.disabled = true;
    name.setAttribute("aria-label", "Название оффера");
    wrap.appendChild(name);
    td.appendChild(wrap);
    tr.appendChild(td);

    var meta = el("td", "fin-c-sum fin-o-meta-cell");
    var box = el("div", "fin-o-meta");
    var partner = document.createElement("select");
    partner.className = "fin-o-partner";
    partner.disabled = true;
    partner.setAttribute("aria-label", "Партнёрка");
    var partnerOption = document.createElement("option");
    partnerOption.textContent = offer.partner || "Партнёрка —";
    partner.appendChild(partnerOption);
    box.appendChild(partner);

    var geo = document.createElement("select");
    geo.className = "fin-o-geo";
    geo.disabled = true;
    geo.setAttribute("aria-label", "Гео оффера");
    var geoOption = document.createElement("option");
    geoOption.textContent = offer.geo || "Гео —";
    geo.appendChild(geoOption);
    box.appendChild(geo);

    var rate = el("div", "fin-o-rate");
    rate.appendChild(el("span", null, "Ставка"));
    var currency = el("button", "fin-o-cur", CURRENCIES[offer.rate_currency] || "$");
    currency.type = "button";
    currency.disabled = true;
    currency.classList.toggle("is-eur", offer.rate_currency === "EUR");
    rate.appendChild(currency);
    var rateInput = document.createElement("input");
    rateInput.type = "number";
    rateInput.value = num(offer.rate);
    rateInput.disabled = true;
    rateInput.setAttribute("aria-label", "Ставка за конверсию");
    rate.appendChild(rateInput);
    box.appendChild(rate);

    var addTag = el("button", "fin-tag-add", "+ тег");
    addTag.type = "button";
    addTag.title = "Добавить тег";
    addTag.disabled = !state.canManage;
    addTag.addEventListener("click", function () {
      partnersTags(offer).push({ name: "", values: {} });
      renderPartners();
      focusPartnersTag(buyer.id, offer);
    });
    box.appendChild(addTag);

    meta.appendChild(box);
    tr.appendChild(meta);

    var days = el("td", "fin-o-days");
    days.colSpan = state.days;
    tr.appendChild(days);
    return tr;
  }

  /* Курсор сразу в новом теге: его первым делом называют. */
  function focusPartnersTag(buyerId, offer) {
    var key = buyerId + "|" + (offer.book_offer_id || offer.source_offer_id);
    var last = document.querySelectorAll(
      '#finPartnersBody .fin-tag-name[data-row="' + key + '"]'
    );
    var input = last[last.length - 1];
    if (input) { input.focus(); input.select(); }
  }

  function partnersTagRow(buyer, offer, tag, tagIndex) {
    if (!tag.values) tag.values = {};
    var store = tag.values;
    var tr = el("tr");
    var td = el("td", "fin-c-name");
    var box = el("div", "fin-tag-wrap");
    var name = document.createElement("input");
    name.className = "fin-tag-name" + (tag.name ? "" : " is-empty");
    name.placeholder = "Назовите тег";
    name.value = tag.name || "";
    name.dataset.row = buyer.id + "|" + (offer.book_offer_id || offer.source_offer_id);
    name.disabled = !state.canManage;
    name.setAttribute("aria-label", "Название тега");
    noAutofill(name);
    name.addEventListener("input", function () {
      tag.name = name.value;
      name.classList.toggle("is-empty", !name.value.trim());
      partnersMarkDirty(buyer, offer, tag);
    });
    box.appendChild(name);

    var tags = partnersTags(offer);
    if (tags.length > 1 && state.canManage) {
      var del = el("button", "fin-tag-del");
      del.type = "button";
      del.title = "Удалить тег";
      del.setAttribute("aria-label", "Удалить тег");
      del.innerHTML = '<svg viewBox="0 0 24 24" width="13" height="13">' +
        '<path d="M6 12h12" fill="none" stroke="currentColor" stroke-width="2.4" ' +
        'stroke-linecap="round"/></svg>';
      del.addEventListener("click", async function () {
        if (!(await askConfirm({
          title: "Удалить тег?",
          message: "«" + (tag.name || "без названия") +
            "» будет удалён вместе с депозитами за месяц.",
          confirmLabel: "Удалить",
          danger: true
        }))) return;
        tags.splice(tagIndex, 1);
        if (tag.id) {
          partnersPending["drop|" + tag.id] = { buyer: buyer, offer: offer, tag: tag, drop: true };
          savePartners().catch(function () {});
        }
        renderPartners();
      })
      box.appendChild(del);
    }
    td.appendChild(box);
    tr.appendChild(td);
    var sum = el("td", "fin-c-sum");
    tr.appendChild(sum);
    for (var d = 1; d <= state.days; d += 1) {
      var cell = el("td", "fin-num fin-in" + (dayFlags(d).off ? " is-off" : ""));
      var input = document.createElement("input");
      input.type = "number";
      input.inputMode = "decimal";
      input.dataset.d = d;
      input.value = store[d] != null ? store[d] : "";
      input.disabled = !state.canManage;
      noAutofill(input);
      input.addEventListener("input", function (event) {
        var day = Number(event.target.dataset.d);
        if (event.target.value === "") delete store[day];
        else store[day] = num(event.target.value);
        recalcPartners();
        partnersMarkDirty(buyer, offer, tag);
      });
      input.addEventListener("wheel", function (event) {
        if (document.activeElement === event.target) event.target.blur();
      }, { passive: true });
      cell.appendChild(input);
      tr.appendChild(cell);
    }
    tag.sumCell = sum;
    return tr;
  }

  function partnersIncomeRow(buyer, offer) {
    var tr = el("tr", "fin-calc fin-offer-income");
    tr.appendChild(labelCell("Доход"));
    var sum = el("td", "fin-c-sum");
    tr.appendChild(sum);
    var days = {};
    for (var d = 1; d <= state.days; d += 1) {
      var cell = el("td", "fin-num" + (dayFlags(d).off ? " is-off" : ""));
      var value = el("span", "fin-v", "–");
      cell.appendChild(value);
      tr.appendChild(cell);
      days[d] = value;
    }
    partnersCells["inc|" + buyer.id + "|" + (offer.book_offer_id || offer.source_offer_id)] =
      { sum: sum, days: days };
    return tr;
  }

  /* Доход считается на лету по ставке оффера — как в книге баера. */
  function recalcPartners() {
    var totals = { deposits: 0, income: 0 };
    partnersVisible.forEach(function (row) {
      var bandDeposits = 0;
      row.offers.forEach(function (offer) {
        var rate = partnersRate(offer, row.eurRate);
        var deposits = 0;
        var incomeSum = 0;
        var slot = partnersCells["inc|" + row.id + "|" +
          (offer.book_offer_id || offer.source_offer_id)];
        for (var d = 1; d <= state.days; d += 1) {
          var count = partnersDeposits(offer, d);
          deposits += count;
          incomeSum += count * rate;
          if (slot) {
            slot.days[d].textContent = count ? whole(count * rate) : "–";
            slot.days[d].classList.toggle("is-zero", !count);
          }
        }
        if (slot) slot.sum.textContent = incomeSum ? whole(incomeSum) : "–";
        partnersTags(offer).forEach(function (tag) {
          if (!tag.sumCell) return;
          var tagSum = 0;
          for (var day = 1; day <= state.days; day += 1) tagSum += num((tag.values || {})[day]);
          tag.sumCell.textContent = tagSum ? whole(tagSum) : "–";
        });
        bandDeposits += deposits;
        totals.deposits += deposits;
        totals.income += incomeSum;
      });
      if (row.bandValue) {
        row.bandValue.textContent = bandDeposits ? whole(bandDeposits) + " деп." : "— деп.";
      }
    });
    var cards = document.querySelectorAll("#finPartnersCards .fin-pcard b");
    if (cards[0]) cards[0].textContent = totals.deposits ? whole(totals.deposits) : "—";
    if (cards[1]) {
      cards[1].textContent = totals.income ? "$ " + money(totals.income) : "—";
      cards[1].className = totals.income ? "is-gain" : "";
    }
  }

  /* Полоса баера: аватар, имя, сколько у него строк и сколько депозитов —
     чтобы в длинном листе было видно, где кончается один человек и начинается
     другой. */
  function partnersBandRow(row) {
    var tr = el("tr", "fin-band fin-band--offer fin-pband");
    var fixed = el("td", "fin-band-label");
    fixed.colSpan = 2;
    var inner = el("div", "fin-pband-inner");
    inner.appendChild(el("span", "fin-pavatar", initials(row.name)));
    inner.appendChild(el("span", "fin-pname", row.name));
    inner.appendChild(el("span", "fin-pcount",
      row.offers.length + " " + plural(row.offers.length, "оффер", "оффера", "офферов")));
    var deposits = el("span", "fin-pdep", "— деп.");
    row.bandValue = deposits;
    inner.appendChild(deposits);
    fixed.appendChild(inner);
    tr.appendChild(fixed);
    var days = el("td", "fin-band-fill");
    days.colSpan = state.days;
    tr.appendChild(days);
    return tr;
  }

  function initials(name) {
    var parts = String(name || "").trim().split(/\s+/);
    var first = (parts[0] || "").slice(0, 2);
    return (parts.length > 1 ? parts[0][0] + parts[1][0] : first).toUpperCase();
  }

  function renderPartnersCards(rows) {
    var host = byId("finPartnersCards");
    if (!host) return;
    var offers = rows.reduce(function (total, row) { return total + row.offers.length; }, 0);
    var partners = {};
    rows.forEach(function (row) {
      row.offers.forEach(function (offer) {
        if (offer.partner) partners[offer.partner] = true;
      });
    });
    var partnerCount = Object.keys(partners).length;
    var cards = [
      { label: "Депозиты", value: "—" },
      { label: "Доход", value: "—" },
      { label: "Строк офферов", value: String(offers) },
      { label: "Партнёрок", value: String(partnerCount) },
      { label: "Баеров", value: String(rows.length) }
    ];
    host.innerHTML = "";
    cards.forEach(function (card) {
      var box = el("div", "fin-pcard");
      box.appendChild(el("span", null, card.label));
      box.appendChild(el("b", null, card.value));
      host.appendChild(box);
    });
  }

  function renderPartners() {
    var head = byId("finPartnersHead");
    head.innerHTML = "";
    head.appendChild(dayHeadRow());
    var body = byId("finPartnersBody");
    body.innerHTML = "";
    var rows = partnersRows();
    renderPartnersCards(rows);
    partnersCells = {};
    partnersVisible = rows;
    rows.forEach(function (row) {
      body.appendChild(partnersBandRow(row));
      row.offers.forEach(function (offer) {
        body.appendChild(section(partnersOfferRow(row, offer), "offer"));
        partnersTags(offer).forEach(function (tag, tagIndex) {
          body.appendChild(section(partnersTagRow(row, offer, tag, tagIndex), "offer"));
        });
        body.appendChild(section(partnersIncomeRow(row, offer), "offer"));
      });
    });
    recalcPartners();
    var offers = rows.reduce(function (total, row) { return total + row.offers.length; }, 0);
    byId("finPartnersCount").textContent = rows.length
      ? rows.length + " " + plural(rows.length, "баер", "баера", "баеров") + " · " +
        offers + " " + plural(offers, "оффер", "оффера", "офферов")
      : "Под фильтр ничего не подошло";
  }

  /* ---------- окно расхода дня ----------
   *
   * Книга показывает расход Медиаборда, поэтому и правится он там же: окно
   * раскладывает сумму по агентам и делит её между офферами этого тира за
   * день — ровно как окно «Изменить данные» в Медиаборде.
   */

  var spendDay = null;
  // Номер последнего открытия окна: ячейка открывает его и на focus, и на
  // click, и строки агентов должен добавить только последний вызов — иначе
  // каждый агент дня появлялся в окне дважды.
  var spendOpenSeq = 0;

  function spendTier() {
    return state.bookTab === "T23" ? "T23" : "T1";
  }

  function spendScopeLabel() {
    return "Все офферы · " + (spendTier() === "T23" ? "Tier2/3" : "Tier1");
  }

  async function spendProviders() {
    if (state.spendProviders) return state.spendProviders;
    try {
      var page = await api.get("/spend-providers?status=active&limit=200");
      state.spendProviders = (page && page.items) || [];
    } catch (error) {
      state.spendProviders = [];
    }
    return state.spendProviders;
  }

  function agentName(provider) {
    var percent = Number(provider.commission_pct || 0);
    if (!isFinite(percent) || percent <= 0) return provider.name;
    return provider.name + " (" + String(Number(percent.toFixed(2))) + "%)";
  }

  /* Что уже разложено за этот день: суммы агентов по записям нужного тира. */
  async function spendDayAgents(day) {
    var date = state.year + "-" + pad2(state.month) + "-" + pad2(day);
    var totals = {};
    var records = 0;
    try {
      var page = await api.get("/media-records?limit=1000&date_from=" + date +
        "&date_to=" + date + "&buyer_id=" + encodeURIComponent(state.buyerId));
      (page.items || []).forEach(function (item) {
        if (item.tier !== spendTier()) return;
        records += 1;
        Object.keys(item.providers || {}).forEach(function (id) {
          var base = Number((item.providers[id] || {}).base_amount || 0);
          if (!isFinite(base) || base <= 0) return;
          totals[id] = (totals[id] || 0) + base;
        });
      });
    } catch (error) {
      return { totals: {}, records: null };
    }
    return { totals: totals, records: records };
  }

  function pad2(value) {
    return (value < 10 ? "0" : "") + value;
  }

  function spendModalError(text) {
    var box = byId("finSpendError");
    box.textContent = text || "";
    box.hidden = !text;
  }

  var SPEND_DROP_SVG =
    '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
    '<path d="m6 6 12 12M18 6 6 18" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round"/></svg>';

  // Поле с подписью над ним — как в окне правки Медиаборда.
  function spendField(label, control) {
    var field = el("label", "board-edit-field", label);
    field.appendChild(control);
    return field;
  }

  function spendAgentRow(providerId, amount) {
    var row = el("div", "board-edit-agent");
    row.setAttribute("data-agent-row", "1");
    var pick = document.createElement("select");
    pick.className = "board-edit-select";
    (state.spendProviders || []).forEach(function (provider) {
      var option = document.createElement("option");
      option.value = provider.id;
      option.textContent = agentName(provider);
      option.selected = provider.id === providerId;
      pick.appendChild(option);
    });
    var sum = document.createElement("input");
    sum.className = "board-edit-input";
    sum.type = "number";
    sum.step = "0.01";
    sum.min = "0";
    sum.inputMode = "decimal";
    sum.value = amount != null ? amount : "";
    noAutofill(sum);
    var drop = el("button", "board-edit-agent-drop");
    drop.type = "button";
    drop.title = "Убрать агента";
    drop.setAttribute("aria-label", "Убрать агента");
    drop.innerHTML = SPEND_DROP_SVG;
    var hint = el("span", "board-edit-agent-hint");
    function paint() {
      var provider = (state.spendProviders || []).filter(function (item) {
        return item.id === pick.value;
      })[0];
      var percent = Number((provider || {}).commission_pct || 0);
      var value = Number(sum.value);
      hint.textContent = isFinite(value) && value > 0
        ? "в SPEND: $" + (value * (percent / 100 + 1)).toFixed(2)
        : "";
    }
    pick.addEventListener("change", function () {
      paint();
      spendRefreshAgents();
    });
    sum.addEventListener("input", paint);
    drop.addEventListener("click", function () {
      row.remove();
      spendRefreshAgents();
    });
    row.appendChild(spendField("Агент", pick));
    row.appendChild(spendField("Сумма до комиссии, USD", sum));
    row.appendChild(drop);
    row.appendChild(hint);
    paint();
    return { row: row, hint: hint, pick: pick, sum: sum };
  }

  /* Как в Медиаборде: агент в дне один раз — занятых гасим в списках, а
     «Добавить агента» выключаем, когда свободных не осталось. */
  function spendRefreshAgents() {
    var host = byId("finSpendAgents");
    var rows = host.querySelectorAll("[data-agent-row]");
    var picks = host.querySelectorAll("[data-agent-row] select");
    Array.prototype.forEach.call(picks, function (pick) {
      var taken = Array.prototype.filter.call(picks, function (other) {
        return other !== pick;
      }).map(function (other) { return other.value; });
      Array.prototype.forEach.call(pick.options, function (option) {
        option.disabled = taken.indexOf(option.value) >= 0;
      });
    });
    byId("finSpendAddAgent").disabled = rows.length >= (state.spendProviders || []).length;
    byId("finSpendEmpty").hidden = rows.length > 0 || !!(spendDay && spendDay.manual);
  }

  function spendAddAgent(providerId, amount) {
    var parts = spendAgentRow(providerId, amount);
    byId("finSpendAgents").appendChild(parts.row);
    spendRefreshAgents();
    return parts;
  }

  async function openSpendDay(day) {
    if (!state.canSpend || !state.buyerId) return;
    // Окно этого дня уже открыто (второй вызов того же клика) — не трогаем.
    if (spendDay && spendDay.day === day && !byId("finSpendModal").hidden) return;
    var seq = ++spendOpenSeq;
    await spendProviders();
    if (seq !== spendOpenSeq || !state.spendProviders.length) return;
    spendDay = { day: day, tier: spendTier() };
    byId("finSpendDate").textContent = pad2(day) + "." + pad2(state.month) + "." + state.year;
    byId("finSpendBuyer").textContent = state.buyerName || "";
    byId("finSpendScope").textContent = spendScopeLabel();
    byId("finSpendAgents").innerHTML = "";
    byId("finSpendAddAgent").hidden = false;
    spendModalError("");
    spendRefreshAgents();
    if (window.CelestialBoard && window.CelestialBoard.modalStyles) {
      window.CelestialBoard.modalStyles();
    }
    byId("finSpendModal").hidden = false;
    document.body.style.overflow = "hidden";
    byId("finSpendModal").querySelector(".board-edit-card").focus();
    var loaded = await spendDayAgents(day);
    if (seq !== spendOpenSeq || !spendDay || spendDay.day !== day) return;
    // Пустой день открывается без агентов: строку добавляют кнопкой.
    Object.keys(loaded.totals).forEach(function (id) {
      spendAddAgent(id, roundMoney(loaded.totals[id]));
    });
    if (loaded.records === 0) {
      // Делить не по чему: в этот день у баера нет ни одной записи нужного
      // тира. Тогда окно пишет сумму прямо в книгу — как прежняя ручная ячейка.
      spendDay.manual = true;
      byId("finSpendAgents").innerHTML = "";
      byId("finSpendAddAgent").hidden = true;
      var field = document.createElement("input");
      field.className = "board-edit-input";
      field.type = "number";
      field.step = "1";
      field.min = "0";
      field.inputMode = "numeric";
      field.id = "finSpendManual";
      field.value = state.manual.spendBuyer[day] != null ? state.manual.spendBuyer[day] : "";
      field.setAttribute("aria-label", "Расход за день, USD");
      noAutofill(field);
      byId("finSpendAgents").appendChild(spendField("Расход за день, $", field));
      spendRefreshAgents();
      spendModalError(
        "За этот день у баера нет офферов " + spendScopeLabel().toLowerCase() +
        " — сумма ляжет прямо в книгу."
      );
    }
  }

  function roundMoney(value) {
    var amount = Number(value);
    if (!isFinite(amount)) return "";
    return Math.round((amount + Number.EPSILON) * 100) / 100;
  }

  function closeSpendDay() {
    spendDay = null;
    byId("finSpendModal").hidden = true;
    document.body.style.overflow = "";
  }

  async function saveSpendDay() {
    if (!spendDay) return;
    if (spendDay.manual) {
      var field = byId("finSpendManual");
      var day = spendDay.day;
      if (!field.value.trim()) delete state.manual.spendBuyer[day];
      else state.manual.spendBuyer[day] = Math.round(num(field.value));
      state.manualDirty[day] = ++state.manualRevision;
      paintSpendCell(spendCells[day - 1]);
      recalc();
      scheduleSave();
      closeSpendDay();
      return;
    }
    var providers = [];
    var seen = {};
    var rows = byId("finSpendAgents").querySelectorAll("[data-agent-row]");
    for (var index = 0; index < rows.length; index += 1) {
      var pick = rows[index].querySelector("select");
      var sum = rows[index].querySelector("input");
      var value = num(sum.value);
      if (!sum.value.trim()) continue;
      if (seen[pick.value]) {
        spendModalError("Один агент дважды в одном дне — уберите лишнюю строку");
        return;
      }
      seen[pick.value] = true;
      providers.push({ provider_id: pick.value, base_amount: value });
    }
    var button = byId("finSpendSave");
    button.disabled = true;
    spendModalError("");
    var savedDay = spendDay.day;
    try {
      // loadBook ниже сбрасывает отложенное сохранение книги — сначала
      // доводим его, чтобы не потерять только что набранные цифры.
      await flushSave();
      await api.post("/media-records/day-spend", {
        record_date: state.year + "-" + pad2(state.month) + "-" + pad2(savedDay),
        buyer_id: state.buyerId,
        tier: spendDay.tier,
        providers: providers
      });
      closeSpendDay();
      await loadBook();
      lockSpendFromMedia(savedDay, providers.length > 0);
    } catch (error) {
      spendModalError(error && error.message ? error.message : "Не удалось сохранить");
    } finally {
      button.disabled = false;
    }
  }

  /* Сумма, внесённая в Финансах, запирает день замком — как ручной ввод в
     ячейку: дальнейшие правки в Медиаборде книгу этого дня уже не меняют.
     Внесённое в самом Медиаборде замок не трогает. Фиксируем итог дня с
     комиссией, который Медиаборд только что пересчитал; окно без агентов
     замок снимает. */
  function lockSpendFromMedia(day, lock) {
    if (!state.loaded || !spendCells[day - 1]) return;
    if (lock) state.manual.spendBuyer[day] = Math.round(num(state.mediaSpend[day]));
    else delete state.manual.spendBuyer[day];
    state.manualDirty[day] = ++state.manualRevision;
    paintSpendCell(spendCells[day - 1]);
    recalc();
    scheduleSave();
  }

  function bindSpendDay() {
    if (!byId("finSpendModal")) return;
    byId("finSpendClose").addEventListener("click", closeSpendDay);
    byId("finSpendCancel").addEventListener("click", closeSpendDay);
    byId("finSpendSave").addEventListener("click", function () {
      saveSpendDay().catch(function () {});
    });
    byId("finSpendAddAgent").addEventListener("click", function () {
      var used = Array.prototype.map.call(
        byId("finSpendAgents").querySelectorAll("[data-agent-row] select"),
        function (node) { return node.value; }
      );
      var free = (state.spendProviders || []).filter(function (provider) {
        return used.indexOf(provider.id) < 0;
      })[0];
      spendAddAgent(free ? free.id : (state.spendProviders[0] || {}).id, "");
    });
    byId("finSpendModal").addEventListener("click", function (event) {
      if (event.target === event.currentTarget) closeSpendDay();
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && !byId("finSpendModal").hidden) closeSpendDay();
    });
  }

  function bindPartners() {
    var factory = window.CelestialBoard && window.CelestialBoard.multiFilter;
    if (!byId("finPartnersUsers") || !factory) return;
    partnersFilters.users = factory(byId("finPartnersUsers"), renderPartners);
    partnersFilters.partners = factory(byId("finPartnersPartners"), renderPartners);
    state.partnersView.users = partnersFilters.users;
    state.partnersView.partners = partnersFilters.partners;
    byId("finPartnersReset").addEventListener("click", function () {
      var changed = partnersFilters.users.clear();
      changed = partnersFilters.partners.clear() || changed;
      if (changed) renderPartners();
    });
  }

  function bind() {
    bindPartners();
    bindSpendDay();
    var grid = byId("finGrid");
    grid.addEventListener("keydown", onKeydown);
    grid.addEventListener("paste", onPaste);
    grid.addEventListener("input", function () { recalc(); });
    grid.addEventListener("mouseover", onHover);
    grid.addEventListener("mouseleave", function () { onHover({ target: document.body }); });

    var eurRate = byId("finEurRate");
    noAutofill(eurRate);
    eurRate.addEventListener("input", function () {
      // Пустое поле по дороге к «1,08» не должно обнулять доход всей книги.
      state.eurRate = num(eurRate.value) || 1;
      renderBody();
      recalc();
      scheduleSave();
    });
    eurRate.addEventListener("wheel", function (event) {
      if (document.activeElement === event.target) event.target.blur();
    }, { passive: true });

    var partnerSync = byId("finPartnerSync");
    if (partnerSync) {
      partnerSync.style.display = state.canManage ? "" : "none";
      partnerSync.addEventListener("click", function () {
        syncPartners();
      });
    }
    byId("finPrevMonth").addEventListener("click", function () {
      switchMonth(-1).catch(fail);
    });
    byId("finNextMonth").addEventListener("click", function () {
      switchMonth(1).catch(fail);
    });

    byId("finBookTabs").addEventListener("click", function (event) {
      var tab = event.target.closest ? event.target.closest("[data-book-tab]") : null;
      if (!tab) return;
      var next = tab.getAttribute("data-book-tab");
      if (next === state.bookTab) return;
      // Недописанное сохранение доводится до конца: переключение вкладки
      // перерисовывает state с нуля, и последняя цифра иначе пропала бы.
      flushSave().then(function () {
        state.bookTab = next;
        renderBookTabs();
        return loadSelection();
      }).catch(fail);
    });
    var buyerBtn = byId("finBuyerBtn"), buyerMenu = byId("finBuyerMenu");
    buyerBtn.addEventListener("click", function () {
      var open = buyerBtn.getAttribute("aria-expanded") === "true";
      buyerBtn.setAttribute("aria-expanded", String(!open));
      buyerMenu.hidden = open;
    });
    buyerMenu.addEventListener("click", function (event) {
      var item = event.target.closest("li[data-sheet]");
      if (!item) return;
      buyerMenu.hidden = true;
      buyerBtn.setAttribute("aria-expanded", "false");
      selectBuyer(item.dataset.sheet).catch(fail);
    });
    document.addEventListener("click", function (event) {
      if (!event.target.closest(".fin-name")) {
        buyerMenu.hidden = true;
        buyerBtn.setAttribute("aria-expanded", "false");
      }
    });

    // Уход со страницы с несохранённой правкой — самая обидная потеря данных здесь.
    window.addEventListener("beforeunload", function (event) {
      if (!dirty && !activeSave) return;
      event.preventDefault();
      event.returnValue = "";
    });
  }

  function fail(error) {
    status("error", error && error.message ? error.message : "Ошибка запроса");
  }

  async function init(user) {
    if (!byId("finGrid")) return;
    state.user = user;
    state.canManage = hasPermission(user, "finance.manage");
    // Расход книги живёт в Медиаборде: окно дня правит именно его записи.
    state.canSpend = hasPermission(user, "media.manage");
    var now = window.CelestialTime.today();
    state.year = now.getFullYear();
    state.month = now.getMonth() + 1;

    renderLadder();
    renderPeriod();
    renderCurrency();
    bind();

    var preference = await api.get("/me/preferences/finance.sheet")
      .catch(function () { return { value: {} }; });
    await loadScopes();
    var preferred = preference && preference.value && preference.value.sheet;
    state.sheet = preferred && sheetName(preferred) ? preferred : defaultSheet();
    state.buyerName = sheetName(state.sheet) || "Общая";
    byId("finBuyerName").textContent = state.buyerName;
    renderBuyerMenu();
    await loadSelection();
  }

  window.CelestialFinance = { init: init, reload: loadSelection };
})();
