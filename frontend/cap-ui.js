/*
 * Форма CapAlert — общая для «Утилит» и «Офферов».
 *
 * Раньше их было две: полная в Утилитах и урезанная в Офферах, которая умела
 * только создать капу на один оффер. После того как капа научилась нескольким
 * офферам, вторая форма ещё и сломалась — она слала поле `offer_id`, которого
 * в API больше нет. Поэтому форма здесь одна, а страницы её только открывают.
 *
 * Модалка строится своим DOM и своими стилями, не полагаясь на разметку
 * страницы: у Офферов и Утилит она разная, а форма должна выглядеть одинаково.
 */
(function () {
  "use strict";

  var api = window.CelestialAPI;

  // Пороги, которые ставят чаще всего. Ввод числом это не заменяет, а
  // избавляет от него в девяти случаях из десяти.
  var THRESHOLDS = [25, 50, 70, 75, 80, 90, 100];
  var MODAL_ID = "celestialCapModal";

  var cache = { reference: null, channels: null };
  var form = null;

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

  function askConfirm(options) {
    if (window.CelestialShell && window.CelestialShell.confirm) {
      return window.CelestialShell.confirm(options);
    }
    return Promise.resolve(window.confirm(options.message || options.title));
  }

  function reference() {
    if (!cache.reference) cache.reference = api.get("/utilities/reference");
    return cache.reference;
  }

  function channels() {
    if (!cache.channels) {
      cache.channels = api.get("/utilities/channels").then(function (page) {
        return (page.items || []).filter(function (row) {
          return row.status === "active";
        });
      });
    }
    return cache.channels;
  }

  // Капа — свободный текст, но «300 FTD / день» чаще всего и есть лимит.
  function capNumber(value) {
    var match = String(value || "").match(/\d+/);
    return match ? match[0] : "";
  }

  function browserTimezone() {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    } catch (error) {
      return "UTC";
    }
  }

  /* Список зон берём у самого браузера — он знает актуальный набор IANA и не
     устареет вместе с нашим кодом. Там, где `supportedValuesOf` нет, остаётся
     короткий список тех зон, по которым реально живут команды. */
  var TIMEZONE_FALLBACK = [
    "UTC", "Europe/Kyiv", "Europe/Moscow", "Europe/Warsaw", "Europe/Berlin",
    "Europe/London", "Europe/Lisbon", "Asia/Almaty", "Asia/Tbilisi", "Asia/Dubai",
    "Asia/Bangkok", "Asia/Manila", "America/New_York", "America/Sao_Paulo"
  ];

  function timezoneList() {
    var zones = [];
    try {
      if (Intl.supportedValuesOf) zones = Intl.supportedValuesOf("timeZone") || [];
    } catch (error) {
      zones = [];
    }
    if (!zones.length) zones = TIMEZONE_FALLBACK.slice();
    if (zones.indexOf("UTC") < 0) zones.unshift("UTC");
    return zones;
  }

  function timezoneOptions(selected) {
    var current = selected || browserTimezone();
    var zones = timezoneList();
    // Зона, которой в списке нет (сохранили когда-то, а браузер её не знает),
    // всё равно должна остаться выбранной — иначе сохранение молча её сменит.
    if (zones.indexOf(current) < 0) zones = [current].concat(zones);
    return zones.map(function (zone) {
      return '<option value="' + escapeHtml(zone) + '"' +
        (zone === current ? " selected" : "") + ">" + escapeHtml(zone) + "</option>";
    }).join("");
  }

  function thresholdChoices(current) {
    /* Готовые проценты плюс те, что у капы уже стоят. Без второй половины
       порог, которого нет в наборе, просто исчезал бы из формы — и терялся
       при первом же сохранении. */
    var values = THRESHOLDS.slice();
    (current || []).forEach(function (percent) {
      if (values.indexOf(percent) < 0) values.push(percent);
    });
    return values.sort(function (a, b) { return a - b; });
  }

  function optionsHtml(items, selected) {
    return items.map(function (item) {
      var value = item.code !== undefined ? item.code : item.id;
      return '<option value="' + escapeHtml(value) + '"' +
        (String(value) === String(selected) ? " selected" : "") + ">" +
        escapeHtml(item.label || item.name) + "</option>";
    }).join("");
  }

  function blank(offer) {
    return {
      name: offer ? "CAP · " + offer.name : "",
      status: "active",
      channel_id: null,
      thread_id: "",
      offer_ids: offer ? [String(offer.id)] : [],
      user_id: null,
      metric: "sales",
      limit_value: offer ? capNumber(offer.cap) : "",
      period: "day",
      timezone: browserTimezone(),
      notify_at: [100]
    };
  }

  function modal() {
    var existing = document.getElementById(MODAL_ID);
    if (existing) return existing;
    var overlay = document.createElement("div");
    overlay.id = MODAL_ID;
    overlay.className = "capf-back";
    overlay.setAttribute("role", "dialog");
    overlay.setAttribute("aria-modal", "true");
    overlay.innerHTML =
      '<div class="capf-card">' +
      '<div class="capf-head"><div><h2 class="capf-title" id="capfTitle">CapAlert</h2>' +
      '<div class="capf-sub" id="capfSubtitle"></div></div>' +
      '<button type="button" class="capf-x" data-capf="close" aria-label="Закрыть">' +
      '<svg width="17" height="17" viewBox="0 0 24 24"><path d="m6 6 12 12M18 6 6 18" ' +
      'stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg></button></div>' +
      '<div class="capf-body" id="capfBody"></div>' +
      '<div class="capf-error" id="capfError" role="alert"></div>' +
      '<div class="capf-foot">' +
      '<button type="button" class="capf-del" data-capf="delete">Удалить</button>' +
      '<button type="button" class="capf-cancel" data-capf="close">Отмена</button>' +
      '<button type="button" class="capf-save" data-capf="save">Сохранить</button>' +
      "</div></div>";
    document.body.appendChild(overlay);

    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) return close();
      var action = event.target.closest
        ? event.target.closest("[data-capf]")
        : null;
      if (!action) return;
      var kind = action.getAttribute("data-capf");
      if (kind === "close") return close();
      if (kind === "save") return submit();
      if (kind === "delete") return remove();
    });
    overlay.addEventListener("change", function (event) {
      var chip = event.target.closest
        ? event.target.closest("[data-capf-threshold]")
        : null;
      if (chip) chip.parentElement.classList.toggle("capf-chip--on", chip.checked);
      if (event.target.matches && event.target.matches('[data-capf-field="period"]')) {
        applyPeriod(event.target.value);
      }
    });
    overlay.addEventListener("input", function (event) {
      if (!event.target.matches || !event.target.matches("[data-capf-search]")) return;
      filterOffers(event.target.value);
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && overlay.classList.contains("capf-open")) close();
    });
    return overlay;
  }

  // Офферов у команды сотни, и список без поиска пролистывать бесполезно.
  function filterOffers(query) {
    var clean = String(query || "").trim().toLowerCase();
    var host = document.querySelector("[data-capf-offers]");
    if (!host) return;
    var shown = 0;
    Array.prototype.forEach.call(host.querySelectorAll("label"), function (row) {
      var hit = !clean || row.textContent.toLowerCase().indexOf(clean) >= 0;
      // Уже отмеченные не прячем: иначе поиск выглядел бы как снятие галочек.
      var checked = row.querySelector("input").checked;
      row.style.display = hit || checked ? "" : "none";
      if (hit) shown += 1;
    });
    var empty = document.querySelector("[data-capf-nooffers]");
    if (empty) empty.style.display = shown ? "none" : "";
  }

  function fieldsHtml(rule, data) {
    var half = "capf-field capf-field--half";
    return '<label class="capf-field"><span class="capf-label">Название</span>' +
      '<input class="capf-input" data-capf-field="name" value="' +
      escapeHtml(rule.name) + '"></label>' +

      // Порядок пар: сначала «чей это лимит», потом «куда писать». Раньше
      // пользователь стоял под каналом, и связка «канал + тема» разрывалась.
      '<label class="' + half + '"><span class="capf-label">Пользователь</span>' +
      '<select class="capf-input capf-select" data-capf-field="user_id">' +
      optionsHtml(
        [{ code: "", label: "Не выбран" }].concat(data.reference.users.map(function (item) {
          return { code: item.id, label: item.name };
        })),
        rule.user_id || ""
      ) + "</select></label>" +

      '<label class="' + half + '"><span class="capf-label">Статус</span>' +
      '<select class="capf-input capf-select" data-capf-field="status">' +
      optionsHtml(
        [{ code: "active", label: "Активен" }, { code: "inactive", label: "Выключен" }],
        rule.status
      ) + "</select></label>" +

      '<label class="' + half + '"><span class="capf-label">Канал</span>' +
      '<select class="capf-input capf-select" data-capf-field="channel_id">' +
      optionsHtml(data.channels, rule.channel_id) + "</select></label>" +

      '<label class="' + half + '"><span class="capf-label">Thread ID</span>' +
      '<input class="capf-input" data-capf-field="thread_id" value="' +
      escapeHtml(rule.thread_id || "") + '">' +
      '<span class="capf-hint">Только для супергрупп с темами</span></label>' +

      '<div class="capf-field"><span class="capf-label">Офферы</span>' +
      '<input class="capf-input capf-search" data-capf-search placeholder="Поиск по названию">' +
      '<div class="capf-list" data-capf-offers>' +
      data.reference.offers.map(function (offer) {
        var on = (rule.offer_ids || []).indexOf(String(offer.id)) >= 0;
        return '<label class="capf-check"><input type="checkbox" data-capf-offer="' +
          escapeHtml(offer.id) + '"' + (on ? " checked" : "") + ">" +
          escapeHtml(offer.name) + "</label>";
      }).join("") +
      '<div class="capf-none" data-capf-nooffers style="display:none">Ничего не найдено</div>' +
      "</div>" +
      '<span class="capf-hint">Можно выбрать несколько — их показатели ' +
      "складываются в один лимит</span></div>" +

      '<label class="' + half + '"><span class="capf-label">Что ограничиваем</span>' +
      '<select class="capf-input capf-select" data-capf-field="metric">' +
      optionsHtml(data.reference.cap_metrics, rule.metric) + "</select></label>" +

      '<label class="' + half + '"><span class="capf-label">Лимит</span>' +
      '<input class="capf-input" type="number" step="any" min="1" ' +
      'data-capf-field="limit_value" value="' + escapeHtml(rule.limit_value) +
      '"></label>' +

      '<label class="' + half + '"><span class="capf-label">Период сброса</span>' +
      '<select class="capf-input capf-select" data-capf-field="period">' +
      optionsHtml(data.reference.cap_periods, rule.period) + "</select></label>" +

      '<label class="' + half + ' capf-tz"><span class="capf-label">Таймзона</span>' +
      '<select class="capf-input capf-select" data-capf-field="timezone">' +
      timezoneOptions(rule.timezone) + "</select>" +
      '<span class="capf-hint">В её полночь счётчик и обнуляется</span></label>' +

      '<div class="capf-field"><span class="capf-label">Пороги уведомлений</span>' +
      '<div class="capf-chips">' + thresholdChoices(rule.notify_at).map(function (percent) {
        var on = (rule.notify_at || []).indexOf(percent) >= 0;
        return '<label class="capf-chip' + (on ? " capf-chip--on" : "") + '">' +
          '<input type="checkbox" data-capf-threshold="' + percent + '"' +
          (on ? " checked" : "") + ">" + percent + " %</label>";
      }).join("") + "</div>" +
      '<span class="capf-hint">Сообщение уйдёт, когда прогресс дойдёт до ' +
      "выбранного порога</span></div>" +

      '<div class="capf-note">Нужно выбрать офферы или пользователя — иначе ' +
      "непонятно, чей это лимит. Пороги считаются заново в каждом периоде — " +
      "кроме общего лимита, у которого периода нет вовсе.</div>";
  }

  function siblingsHtml(siblings) {
    if (!siblings.length) return "";
    return '<div class="capf-field capf-siblings"><span class="capf-label">' +
      "Уже настроено на этот оффер</span>" +
      siblings.map(function (rule) {
        return '<button type="button" class="capf-sibling" data-capf-open="' +
          escapeHtml(rule.id) + '"><span>' + escapeHtml(rule.name) + "</span>" +
          '<span class="capf-sibling-note">' +
          escapeHtml((rule.metric_label || rule.metric) + " · до " + rule.limit_value) +
          "</span></button>";
      }).join("") + "</div>";
  }

  /* У общего лимита периода нет, а значит нет и полуночи, в которую что-то
     обнуляется. Подсказка про таймзону там врала бы, поэтому меняется вместе
     с периодом: зона всё ещё нужна — по ней считается «сегодня» у верхней
     границы, — но обнуления не будет. */
  function applyPeriod(period) {
    var hint = document.querySelector(".capf-tz .capf-hint");
    if (!hint) return;
    hint.textContent = period === "total"
      ? "Счётчик не обнуляется — зона задаёт только границу «сегодня»"
      : "В её полночь счётчик и обнуляется";
  }

  function readForm() {
    var overlay = modal();
    var values = {};
    Array.prototype.forEach.call(
      overlay.querySelectorAll("[data-capf-field]"),
      function (input) { values[input.getAttribute("data-capf-field")] = input.value; }
    );
    values.offer_ids = Array.prototype.map.call(
      overlay.querySelectorAll("[data-capf-offer]:checked"),
      function (input) { return input.getAttribute("data-capf-offer"); }
    );
    values.notify_at = Array.prototype.map.call(
      overlay.querySelectorAll("[data-capf-threshold]:checked"),
      function (input) { return Number(input.getAttribute("data-capf-threshold")); }
    );
    return values;
  }

  function payload(values) {
    return {
      name: String(values.name || "").trim(),
      status: values.status,
      channel_id: values.channel_id,
      thread_id: String(values.thread_id || "").trim() || null,
      offer_ids: values.offer_ids || [],
      user_id: values.user_id || null,
      metric: values.metric,
      limit_value: Number(values.limit_value),
      period: values.period,
      timezone: String(values.timezone || "").trim() || "UTC",
      notify_at: (values.notify_at || []).length ? values.notify_at : [100]
    };
  }

  function showError(message) {
    var host = document.getElementById("capfError");
    if (!host) return;
    host.textContent = message || "";
    host.style.display = message ? "" : "none";
  }

  function close() {
    var overlay = document.getElementById(MODAL_ID);
    if (overlay) overlay.classList.remove("capf-open");
    form = null;
  }

  async function submit() {
    if (!form) return;
    var button = document.querySelector(".capf-save");
    showError("");
    button.disabled = true;
    try {
      var body = payload(readForm());
      if (!body.name) throw new Error("Укажите название");
      if (!(body.limit_value > 0)) throw new Error("Укажите лимит больше нуля");
      if (!body.offer_ids.length && !body.user_id) {
        throw new Error("Выберите офферы или пользователя — иначе непонятно, чей это лимит");
      }
      if (form.ruleId) await api.patch("/utilities/caps/" + form.ruleId, body);
      else await api.post("/utilities/caps", body);
      var done = form.onSaved;
      close();
      if (done) await done();
    } catch (error) {
      showError(error && error.message ? error.message : "Не удалось сохранить");
    } finally {
      button.disabled = false;
    }
  }

  async function remove() {
    if (!form || !form.ruleId) return;
    var ok = await askConfirm({
      title: "Удалить CapAlert?",
      message: "«" + form.name + "» перестанет контролироваться.",
      confirmLabel: "Удалить",
      danger: true
    });
    if (!ok) return;
    try {
      await api.delete("/utilities/caps/" + form.ruleId);
      var done = form.onSaved;
      close();
      if (done) await done();
    } catch (error) {
      showError(error && error.message ? error.message : "Не удалось удалить");
    }
  }

  /**
   * Открыть форму.
   *
   * options.rule — существующая капа (режим правки);
   * options.offer — оффер, из карточки которого пришли (режим создания);
   * options.onSaved — что перезагрузить после сохранения.
   */
  async function open(options) {
    var settings = options || {};
    var overlay = modal();
    try {
      var loaded = await Promise.all([reference(), channels()]);
      var data = { reference: loaded[0], channels: loaded[1] };
      if (!data.channels.length) {
        return notify({
          title: "Сначала нужен канал",
          message: "CapAlert пишет в чат Telegram. Добавьте канал в разделе «Утилиты»."
        });
      }
      var rule = settings.rule
        ? JSON.parse(JSON.stringify(settings.rule))
        : blank(settings.offer);
      if (!rule.channel_id) rule.channel_id = data.channels[0].id;
      rule.offer_ids = (rule.offer_ids || []).map(String);

      // Капы, которые уже висят на этом оффере. Без них кнопка «CapAlert» в
      // Офферах умела бы только плодить дубли.
      var siblings = [];
      if (settings.offer && !settings.rule) {
        var page = await api.get("/utilities/caps");
        siblings = (page.items || []).filter(function (row) {
          return (row.offer_ids || []).map(String).indexOf(String(settings.offer.id)) >= 0;
        });
      }

      form = {
        ruleId: settings.rule ? settings.rule.id : null,
        name: rule.name,
        onSaved: settings.onSaved,
        offer: settings.offer
      };
      document.getElementById("capfTitle").textContent =
        settings.rule ? "CapAlert" : "Новый CapAlert";
      document.getElementById("capfSubtitle").textContent = settings.offer
        ? settings.offer.name
        : "Лимит на связку офферов или на баера за период";
      document.getElementById("capfBody").innerHTML =
        siblingsHtml(siblings) + fieldsHtml(rule, data);
      document.querySelector(".capf-del").style.display =
        settings.rule ? "" : "none";
      showError("");
      applyPeriod(rule.period);
      overlay.classList.add("capf-open");

      overlay.querySelectorAll("[data-capf-open]").forEach(function (button) {
        button.addEventListener("click", function () {
          var found = siblings.filter(function (row) {
            return row.id === button.getAttribute("data-capf-open");
          })[0];
          if (found) open({ rule: found, offer: settings.offer, onSaved: settings.onSaved });
        });
      });
      var first = overlay.querySelector("[data-capf-field]");
      if (first) first.focus();
    } catch (error) {
      notify({
        title: "Не удалось открыть CapAlert",
        message: error && error.message ? error.message : ""
      });
    }
  }

  window.CelestialCap = {
    open: open,
    // Каналы и справочник кэшируются на страницу: после правки в Утилитах
    // список каналов мог измениться.
    reset: function () { cache.reference = null; cache.channels = null; }
  };
})();
