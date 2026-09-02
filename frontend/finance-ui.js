/*
 * Финансы: книга одного баера за месяц — показатели по строкам, дни по столбцам.
 *
 * Всё заполняется руками; из Keitaro сюда ничего не приходит. Формулой считаются
 * только доход по офферу (депозиты по тегам × ставка), доход общий, профит, ROI
 * и зарплата.
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
    // Какая из таблиц баера открыта: обзор только читается, T1 и T23 заполняют.
    bookTab: "overview",
    buyerId: null,
    buyerName: "",
    year: 0,
    month: 0,
    days: 31,
    prevMinus: 0,
    eurRate: 1,
    manual: { spendBuyer: {}, spendAgent: {}, costs: {} },
    offers: [],
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
  // Затраты принадлежат дню целиком: спенд приходит из кабинета одной суммой,
  // и разносить его по офферам руками дороже получаемой точности. По тирам его
  // распределяет сводка — по доле дохода дня.
  function spend(day) { return num(state.manual.spendBuyer[day]); }
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
    var now = new Date();
    return {
      weekday: WEEK[weekday],
      off: weekday === 0 || weekday === 6,
      today: now.getFullYear() === state.year &&
        now.getMonth() + 1 === state.month && now.getDate() === day
    };
  }

  function renderHead() {
    var head = byId("finGridHead");
    head.innerHTML = "";
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
    head.appendChild(tr);
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

  function addOffer() {
    // Тег создаётся пустым: как назвать строку, решает пользователь, а не мы.
    state.offers.push({ name: "Новый оффер", partner: "", geo: null, sourceOfferId: null, rate: 0,
      rateCurrency: "USD", tags: [{ name: "", values: {} }] });
    renderBody();
    recalc();
    scheduleSave();
    var names = byId("finGridBody").querySelectorAll(".fin-o-name");
    var last = names[names.length - 1];
    if (last) { last.focus(); last.select(); }
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
      input.disabled = !state.canManage;
      noAutofill(input);
      input.setAttribute("aria-label", label + ", день " + d);
      input.addEventListener("input", function (event) {
        var day = Number(event.target.dataset.d);
        var raw = event.target.value;
        if (raw === "") delete store[day];
        else store[day] = num(raw);
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

  function offerHeadRow(offer, index) {
    var tr = el("tr", "fin-offer-head");
    var td = el("td", "fin-c-name");
    var wrap = el("div", "fin-o-wrap");
    wrap.appendChild(el("i", "fin-o-dot"));

    var name = document.createElement("input");
    name.className = "fin-o-name";
    name.value = offer.name;
    name.disabled = !state.canManage;
    name.setAttribute("aria-label", "Название оффера");
    noAutofill(name);
    name.addEventListener("input", function () {
      offer.name = name.value;
      scheduleSave();
    });
    wrap.appendChild(name);

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
    body.appendChild(section(inputRow(
      "spendBuyer", "Спенд", "то, что открутил баер",
      state.manual.spendBuyer, "0.01"
    ), "cost"));
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
    calcCells.spendBuyer.sum.textContent = money(spendSum);
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
    byId("finCardSpend").textContent = withSign(money(spendSum));
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

  function status(kind, text) {
    var box = byId("finSaved");
    box.classList.toggle("is-busy", kind === "busy");
    box.classList.toggle("is-error", kind === "error");
    box.querySelector("span").textContent = text;
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
      var spendBuyer = num(state.manual.spendBuyer[d]);
      var spendAgent = num(state.manual.spendAgent[d]);
      var dayCosts = num(state.manual.costs[d]);
      if (!spendBuyer && !spendAgent && !dayCosts) continue;
      days[d] = { spend_buyer: spendBuyer, spend_agent: spendAgent, costs: dayCosts };
    }
    return {
      buyer_id: state.buyerId,
      year: state.year,
      month: state.month,
      tier: state.bookTab,
      eur_usd_rate: num(state.eurRate) || 1,
      days: days,
      offers: state.offers.map(function (offer) {
        return {
          name: (offer.name || "").trim() || "Без названия",
          partner: (offer.partner || "").trim() || null,
          geo: offer.geo || null,
          source_offer_id: offer.sourceOfferId || null,
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
    dirty = false;
    activeSave = (async function () {
      try {
        var savedBook = await api.put("/finance/book", requestPayload);
        // Перенос принадлежит серверу. Если параллельно не появилась новая
        // локальная правка, сразу принимаем пересчитанный остаток из ответа.
        if (!dirty && savedBook && savedBook.buyer &&
            savedBook.buyer.id === requestPayload.buyer_id &&
            savedBook.year === requestPayload.year &&
            savedBook.month === requestPayload.month &&
            state.buyerId === requestPayload.buyer_id &&
            state.year === requestPayload.year && state.month === requestPayload.month) {
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
    state.manual = { spendBuyer: {}, spendAgent: {}, costs: {} };
    Object.keys(book.days || {}).forEach(function (day) {
      var entry = book.days[day];
      if (num(entry.spend_buyer)) state.manual.spendBuyer[Number(day)] = num(entry.spend_buyer);
      if (num(entry.spend_agent)) state.manual.spendAgent[Number(day)] = num(entry.spend_agent);
      if (num(entry.costs)) state.manual.costs[Number(day)] = num(entry.costs);
    });
    state.offers = (book.offers || []).map(function (offer) {
      return {
        name: offer.name,
        partner: offer.partner || "",
        geo: offer.geo || null,
        sourceOfferId: offer.source_offer_id || null,
        rate: num(offer.rate),
        rateCurrency: offer.rate_currency === "EUR" ? "EUR" : "USD",
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
    spend: "cost", costs: "cost", salary: "cost", payout: "cost",
    income: "result", profit: "result", roi: "result"
  };

  function bandClass(key, previousKey) {
    var band = SUMMARY_BANDS[key];
    if (!band) return "";
    var opens = SUMMARY_BANDS[previousKey] !== band;
    return " fin-col--" + band + (opens ? " is-band-start" : "");
  }

  function summaryCell(key, value, previousKey) {
    var formatted = key === "roi" ? summaryPercent(value) : summaryMoney(value);
    var tone = key === "profit" || key === "roi"
      ? (num(value) < 0 ? " fin-neg" : num(value) > 0 ? " fin-pos" : "")
      : "";
    return '<td class="' + (tone + bandClass(key, previousKey)).trim() + '">' +
      escapeHtml(formatted) + "</td>";
  }

  function summaryTable(columns, rows, total) {
    var head = columns.map(function (column, index) {
      var band = index === 0 || column.text
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
          (columns[index - 1] || {}).key);
      }).join("") + "</tr>";
    }).join("");
    if (total) {
      body += '<tr class="fin-summary-total"><td>ИТОГО</td>' +
        columns.slice(1).map(function (column, index) {
          if (column.text) return "<td></td>";
          return summaryCell(column.key, total[column.key],
            (columns[index] || {}).key);
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
    byId("finSummaryView").hidden = !summaryMode;
    byId("finBookSheet").hidden = summaryMode;
    byId("finBookSalary").hidden = summaryMode;
    byId("finSaved").hidden = summaryMode;
    byId("finCardSalary").closest(".fin-card").hidden = summaryMode;
    var buyerSheet = state.sheet.indexOf("buyer:") === 0;
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

  function renderFinanceSummary(data) {
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
    byId("finSummaryTiers").innerHTML = summaryTable(tierColumns, data.tiers, data.cards);
    byId("finSummaryBuyersBlock").hidden = !data.buyers.length;
    byId("finSummaryBuyersTitle").textContent = teamSummary
      ? "Тимлид и команда" : "По баерам";
    byId("finSummaryBuyers").innerHTML = summaryTable(
      buyerColumns, data.buyers, data.cards
    );
    // Показываем по данным, а не по имени листа: в общей сводке баера лист
    // называется «buyer:…», и проверка на «all» прятала блок вместе с числами.
    byId("finSummaryDailyBlock").hidden = !(data.daily || []).length;
    byId("finSummaryDaily").innerHTML = dailyGrid(data.daily, data.cards);

    var salary = data.salary;
    byId("finSummarySalaryBlock").hidden = !salary;
    if (salary) {
      byId("finSalaryFundLabel").textContent = salary.label
        || (teamSummary ? "Фонд ЗП команды" : "Фонд ЗП компании");
      byId("finSalaryFund").textContent = summaryMoney(salary.total);
      // Пустые части блока не рисуем: у баера нет ни групп, ни списка людей —
      // подпись «нет начислений» над его же зарплатой читалась бы как ошибка.
      var groups = salary.groups || [];
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
    menu.appendChild(menuTitle("Сводки"));
    (state.scopes.summaries || []).forEach(function (summary) {
      var item = el("li", null, summary.name);
      item.setAttribute("role", "option");
      item.dataset.sheet = summary.scope;
      item.setAttribute("aria-selected", String(summary.scope === state.sheet));
      menu.appendChild(item);
    });
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
      item.setAttribute("role", "option");
      item.dataset.sheet = "buyer:" + buyer.id;
      item.setAttribute("aria-selected", String(item.dataset.sheet === state.sheet));
      menu.appendChild(item);
    });
  }

  function sheetName(sheet) {
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
    if (!sheetName(state.sheet)) state.sheet = "all";
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
      if (result.pending) {
        text += ", без строки в таблице: " + result.pending;
      }
      if (result.skipped) text += ", пропущено: " + result.skipped;
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

  function notify(text, result) {
    var details = [];
    (result && result.errors ? result.errors : []).forEach(function (row) {
      details.push(row);
    });
    if (result && result.pending) {
      details.push(
        "Депозиты без строки: заведите тег под нужным оффером в книге — " +
        "следующий синк за тот же период разложит их сам."
      );
    }
    (result && result.reasons ? result.reasons : []).slice(0, 5).forEach(function (row) {
      details.push(row);
    });
    if (window.CelestialShell && window.CelestialShell.notify) {
      window.CelestialShell.notify({
        title: text,
        message: details.join("\n")
      });
      return;
    }
    window.alert(text + (details.length ? "\n\n" + details.join("\n") : ""));
  }

  function bind() {
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
    var now = new Date();
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
    state.sheet = preferred && sheetName(preferred) ? preferred : "all";
    state.buyerName = sheetName(state.sheet) || "Общая";
    byId("finBuyerName").textContent = state.buyerName;
    renderBuyerMenu();
    await loadSelection();
  }

  window.CelestialFinance = { init: init, reload: loadSelection };
})();
