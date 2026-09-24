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

  /* Лимит приходит как Decimal — «50.0000». В поле ввода и в подписи это
     мешает: человек вводил целое число и ждёт увидеть его же. */
  function trimNumber(value) {
    var text = String(value == null ? "" : value);
    if (!/^-?\d+\.\d+$/.test(text)) return text;
    return text.replace(/0+$/, "").replace(/\.$/, "");
  }

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
    return "Europe/Moscow";
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
      channel_ids: [],
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
      var inPicker = event.target.closest ? event.target.closest("[data-capf-ms]") : null;
      if (inPicker) return pickerClick(event, inPicker);
      // Клик в любом другом месте формы закрывает открытые списки.
      closeLists(null);
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
    });
    overlay.addEventListener("input", function (event) {
      if (!event.target.matches || !event.target.matches("[data-capf-search]")) return;
      var typing = event.target.closest("[data-capf-ms]");
      openList(typing, true);
      filterItems(typing, event.target.value);
    });
    overlay.addEventListener("keydown", function (event) {
      if (!event.target.matches || !event.target.matches("[data-capf-search]")) return;
      pickerKeydown(event, event.target.closest("[data-capf-ms]"));
    });
    // Клик по варианту не должен уводить фокус из поля ввода.
    overlay.addEventListener("mousedown", function (event) {
      if (event.target.closest && event.target.closest("[data-capf-list]")) {
        event.preventDefault();
      }
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && overlay.classList.contains("capf-open")) close();
    });
    return overlay;
  }

  /* Поле с выбранными чипами и выпадающим списком. Раньше это был список из
     сотен строк с галочками: отмеченный оффер приходилось искать прокруткой, а
     понять, что вообще выбрано, можно было только пролистав всё. Теперь
     выбранное видно в самом поле, ввод там же ищет по названию, а уже
     выбранное из списка пропадает.

     Полей таких два — офферы и каналы, — поэтому функции получают само поле
     (`ms`), а не ищут единственное на странице. */
  var MS_TEXTS = {
    offers: {
      placeholder: "Выберите офферы",
      search: "Поиск офферов",
      clear: "Снять все офферы",
      list: "Список офферов",
      missing: "Оффер недоступен"
    },
    channels: {
      placeholder: "Выберите каналы",
      search: "Поиск каналов",
      clear: "Снять все каналы",
      list: "Список каналов",
      missing: "Канал недоступен"
    }
  };

  function crossSvg(size) {
    return '<svg width="' + size + '" height="' + size + '" viewBox="0 0 24 24" fill="none" ' +
      'aria-hidden="true"><path d="m6 6 12 12M18 6 6 18" stroke="currentColor" ' +
      'stroke-width="2.2" stroke-linecap="round"/></svg>';
  }

  function chipHtml(id, name) {
    return '<span class="capf-ms__chip" data-capf-chip="' + escapeHtml(id) + '" title="' +
      escapeHtml(name) + '"><span class="capf-ms__chip-text">' + escapeHtml(name) + "</span>" +
      '<button type="button" class="capf-ms__chip-x" data-capf-chip-drop="' + escapeHtml(id) +
      '" aria-label="Убрать ' + escapeHtml(name) + '">' + crossSvg(12) + "</button></span>";
  }

  function pickerHtml(kind, items, selected) {
    var texts = MS_TEXTS[kind];
    var names = {};
    items.forEach(function (item) { names[String(item.id)] = item.name; });
    var taken = {};
    // Оффера или канала может уже не быть в справочнике — чип всё равно
    // остаётся: иначе первое же сохранение молча убрало бы его из капы.
    var chips = selected.map(function (id) {
      taken[String(id)] = true;
      return chipHtml(id, names[String(id)] || texts.missing);
    }).join("");
    return '<div class="capf-ms" data-capf-ms="' + kind + '">' +
      '<div class="capf-ms__box" data-capf-ms-box>' + chips +
      '<input class="capf-ms__input" data-capf-search autocomplete="off" ' +
      'aria-label="' + texts.search + '" placeholder="' +
      (selected.length ? "" : texts.placeholder) + '">' +
      '<span class="capf-ms__tools">' +
      '<button type="button" class="capf-ms__clear" data-capf-ms-clear ' +
      'aria-label="' + texts.clear + '"' + (selected.length ? "" : " hidden") + ">" +
      crossSvg(14) + "</button>" +
      '<span class="capf-ms__sep" aria-hidden="true"></span>' +
      '<button type="button" class="capf-ms__caret" data-capf-ms-toggle aria-label="' +
      texts.list + '">' +
      '<svg width="15" height="15" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
      '<path d="m6 9 6 6 6-6" stroke="currentColor" stroke-width="2.2" ' +
      'stroke-linecap="round" stroke-linejoin="round"/></svg></button></span></div>' +
      '<div class="capf-ms__list" data-capf-list hidden>' +
      items.map(function (item) {
        return '<button type="button" class="capf-ms__option" data-capf-option="' +
          escapeHtml(item.id) + '"' + (taken[String(item.id)] ? " hidden" : "") + ">" +
          escapeHtml(item.name) + "</button>";
      }).join("") +
      '<div class="capf-none" data-capf-noitems hidden>Ничего не найдено</div>' +
      "</div></div>";
  }

  function pickedIds(kind) {
    var overlay = document.getElementById(MODAL_ID);
    var ms = overlay ? overlay.querySelector('[data-capf-ms="' + kind + '"]') : null;
    if (!ms) return [];
    return Array.prototype.map.call(
      ms.querySelectorAll("[data-capf-chip]"),
      function (chip) { return chip.getAttribute("data-capf-chip"); }
    );
  }

  // Офферов у команды сотни: список сужается по мере ввода.
  function filterItems(ms, query) {
    if (!ms) return;
    var clean = String(query || "").trim().toLowerCase();
    var taken = {};
    Array.prototype.forEach.call(ms.querySelectorAll("[data-capf-chip]"), function (chip) {
      taken[chip.getAttribute("data-capf-chip")] = true;
    });
    var first = null;
    Array.prototype.forEach.call(ms.querySelectorAll("[data-capf-option]"), function (node) {
      var hit = !taken[node.getAttribute("data-capf-option")] &&
        (!clean || node.textContent.toLowerCase().indexOf(clean) >= 0);
      node.hidden = !hit;
      if (!hit) node.classList.remove("is-active");
      if (hit && !first) first = node;
    });
    ms.querySelector("[data-capf-noitems]").hidden = !!first;
    // Ищут, чтобы выбрать: первое совпадение сразу под Enter.
    if (clean && first && !ms.querySelector(".capf-ms__option.is-active")) {
      first.classList.add("is-active");
    }
  }

  function openList(ms, show) {
    if (!ms) return;
    var list = ms.querySelector("[data-capf-list]");
    if (list.hidden === !show) return;
    list.hidden = !show;
    ms.classList.toggle("is-open", show);
    if (!show) {
      var active = list.querySelector(".is-active");
      if (active) active.classList.remove("is-active");
      return;
    }
    filterItems(ms, ms.querySelector("[data-capf-search]").value);
    // Поле бывает у нижнего края формы — список не должен уходить за него.
    if (list.scrollIntoView) list.scrollIntoView({ block: "nearest" });
  }

  function closeLists(except) {
    var overlay = document.getElementById(MODAL_ID);
    if (!overlay) return;
    Array.prototype.forEach.call(overlay.querySelectorAll("[data-capf-ms]"), function (ms) {
      if (ms !== except) openList(ms, false);
    });
  }

  function picksChanged(ms) {
    var input = ms.querySelector("[data-capf-search]");
    var count = ms.querySelectorAll("[data-capf-chip]").length;
    input.placeholder = count ? "" : MS_TEXTS[ms.getAttribute("data-capf-ms")].placeholder;
    ms.querySelector("[data-capf-ms-clear]").hidden = !count;
    filterItems(ms, input.value);
    input.focus();
  }

  function pickOption(ms, option) {
    var input = ms.querySelector("[data-capf-search]");
    var holder = document.createElement("span");
    holder.innerHTML = chipHtml(option.getAttribute("data-capf-option"), option.textContent);
    ms.querySelector("[data-capf-ms-box]").insertBefore(holder.firstChild, input);
    option.classList.remove("is-active");
    input.value = "";
    picksChanged(ms);
  }

  function dropChip(ms, id) {
    Array.prototype.forEach.call(ms.querySelectorAll("[data-capf-chip]"), function (chip) {
      if (chip.getAttribute("data-capf-chip") === id) chip.remove();
    });
    picksChanged(ms);
  }

  function moveActive(ms, step) {
    var options = Array.prototype.filter.call(
      ms.querySelectorAll("[data-capf-option]"),
      function (node) { return !node.hidden; }
    );
    if (!options.length) return;
    var current = ms.querySelector(".capf-ms__option.is-active");
    var index = options.indexOf(current);
    var next = index < 0
      ? options[step > 0 ? 0 : options.length - 1]
      : options[(index + step + options.length) % options.length];
    if (current) current.classList.remove("is-active");
    next.classList.add("is-active");
    if (next.scrollIntoView) next.scrollIntoView({ block: "nearest" });
  }

  function pickerClick(event, ms) {
    var target = event.target;
    // Список второго поля закрываем: открытые разом, они перекрывали бы
    // друг друга.
    closeLists(ms);
    var drop = target.closest("[data-capf-chip-drop]");
    if (drop) return dropChip(ms, drop.getAttribute("data-capf-chip-drop"));
    if (target.closest("[data-capf-ms-clear]")) {
      Array.prototype.forEach.call(ms.querySelectorAll("[data-capf-chip]"), function (chip) {
        chip.remove();
      });
      return picksChanged(ms);
    }
    var option = target.closest("[data-capf-option]");
    if (option) return pickOption(ms, option);
    var input = ms.querySelector("[data-capf-search]");
    if (target.closest("[data-capf-ms-toggle]")) {
      openList(ms, !ms.classList.contains("is-open"));
      return input.focus();
    }
    if (target.closest("[data-capf-ms-box]")) {
      openList(ms, true);
      input.focus();
    }
  }

  function pickerKeydown(event, ms) {
    var input = event.target;
    var listOpen = ms.classList.contains("is-open");
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      openList(ms, true);
      return moveActive(ms, event.key === "ArrowDown" ? 1 : -1);
    }
    if (event.key === "Enter") {
      var active = ms.querySelector(".capf-ms__option.is-active");
      if (listOpen && active && !active.hidden) {
        event.preventDefault();
        pickOption(ms, active);
      }
      return;
    }
    if (event.key === "Escape" && listOpen) {
      // Esc сначала закрывает список, а не всю форму с несохранёнными правками.
      event.preventDefault();
      event.stopPropagation();
      return openList(ms, false);
    }
    if (event.key === "Backspace" && !input.value) {
      var chips = ms.querySelectorAll("[data-capf-chip]");
      if (chips.length) dropChip(ms, chips[chips.length - 1].getAttribute("data-capf-chip"));
    }
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

      // Каналов может быть несколько: один лимит ждут и в чате команды, и у
      // тимлида. Отмеченные горят красным, как пороги уведомлений ниже.
      '<div class="capf-field"><span class="capf-label">Каналы</span>' +
      pickerHtml("channels", data.channels, rule.channel_ids || []) + "</div>" +

      '<label class="' + half + '"><span class="capf-label">Thread ID</span>' +
      '<input class="capf-input" data-capf-field="thread_id" value="' +
      escapeHtml(rule.thread_id || "") + '"></label>' +

      '<div class="capf-field"><span class="capf-label">Офферы</span>' +
      pickerHtml("offers", data.reference.offers, rule.offer_ids || []) +
      '<span class="capf-hint">Можно выбрать несколько — их показатели ' +
      "складываются в один лимит</span></div>" +

      '<label class="' + half + '"><span class="capf-label">Что ограничиваем</span>' +
      '<select class="capf-input capf-select" data-capf-field="metric">' +
      optionsHtml(data.reference.cap_metrics, rule.metric) + "</select></label>" +

      '<label class="' + half + '"><span class="capf-label">Лимит</span>' +
      '<input class="capf-input" type="number" step="any" min="1" ' +
      'data-capf-field="limit_value" value="' + escapeHtml(trimNumber(rule.limit_value)) +
      '"></label>' +

      '<label class="' + half + '"><span class="capf-label">Период сброса</span>' +
      '<select class="capf-input capf-select" data-capf-field="period">' +
      optionsHtml(data.reference.cap_periods, rule.period) + "</select></label>" +

      '<label class="' + half + '"><span class="capf-label">Таймзона</span>' +
      '<select class="capf-input capf-select" data-capf-field="timezone">' +
      timezoneOptions(rule.timezone) + "</select></label>" +

      '<div class="capf-field"><span class="capf-label">Пороги уведомлений</span>' +
      '<div class="capf-chips">' + thresholdChoices(rule.notify_at).map(function (percent) {
        var on = (rule.notify_at || []).indexOf(percent) >= 0;
        return '<label class="capf-chip' + (on ? " capf-chip--on" : "") + '">' +
          '<input type="checkbox" data-capf-threshold="' + percent + '"' +
          (on ? " checked" : "") + ">" + percent + " %</label>";
      }).join("") + "</div>" +
      '<span class="capf-hint">Сообщение уйдёт, когда прогресс дойдёт до ' +
      "выбранного порога</span></div>";
  }

  function siblingsHtml(siblings) {
    if (!siblings.length) return "";
    return '<div class="capf-field capf-siblings"><span class="capf-label">' +
      "Уже настроено на этот оффер</span>" +
      siblings.map(function (rule) {
        return '<button type="button" class="capf-sibling" data-capf-open="' +
          escapeHtml(rule.id) + '"><span>' + escapeHtml(rule.name) + "</span>" +
          '<span class="capf-sibling-note">' +
          escapeHtml((rule.metric_label || rule.metric) + " · до " +
            trimNumber(rule.limit_value)) +
          "</span></button>";
      }).join("") + "</div>";
  }

  function readForm() {
    var overlay = modal();
    var values = {};
    Array.prototype.forEach.call(
      overlay.querySelectorAll("[data-capf-field]"),
      function (input) { values[input.getAttribute("data-capf-field")] = input.value; }
    );
    values.offer_ids = pickedIds("offers");
    values.channel_ids = pickedIds("channels");
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
      channel_ids: values.channel_ids || [],
      thread_id: String(values.thread_id || "").trim() || null,
      offer_ids: values.offer_ids || [],
      user_id: values.user_id || null,
      metric: values.metric,
      limit_value: Number(values.limit_value),
      period: values.period,
      timezone: String(values.timezone || "").trim() || "Europe/Moscow",
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
      if (!body.channel_ids.length) throw new Error("Выберите хотя бы один канал");
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
      rule.channel_ids = (rule.channel_ids && rule.channel_ids.length
        ? rule.channel_ids
        : (rule.channel_id ? [rule.channel_id] : [data.channels[0].id])).map(String);
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
