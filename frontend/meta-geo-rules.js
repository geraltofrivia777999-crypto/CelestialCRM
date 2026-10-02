/*
 * MetaAds v2 · «Автоправила»: сначала создаётся автоправило (название и
 * уровень), внутри него — интервал, автопрогон и пороги по каждому GEO.
 *
 * NoClicks / NoInsts / NoRegs / NoDeps — сколько долларов объект может
 * потратить за сегодня без кликов, инсталлов, регистраций или депозитов.
 * Max AvgInst / AvgReg / AvgDep — предельная цена результата. Пустая ячейка —
 * проверка выключена. Считает и ставит на паузу сервер (meta_geo_rules.py).
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

  function askConfirm(options) {
    if (window.CelestialShell && window.CelestialShell.confirm) {
      return window.CelestialShell.confirm(options);
    }
    return Promise.resolve(window.confirm(options.message || options.title));
  }

  var COLUMNS = [
    { key: "no_clicks", label: "NoClicks", title: "Пауза, если потрачено от N $ и ни одного клика" },
    { key: "no_insts", label: "NoInsts", title: "Пауза, если потрачено от N $ и ни одного инсталла" },
    { key: "no_regs", label: "NoRegs", title: "Пауза, если потрачено от N $ и ни одной регистрации" },
    { key: "no_deps", label: "NoDeps", title: "Пауза, если потрачено от N $ и ни одного депозита",
      deps: true },
    { key: "max_avg_inst", label: "Max AvgInst", title: "Пауза, если цена инсталла выше N $" },
    { key: "max_avg_reg", label: "Max AvgReg", title: "Пауза, если цена регистрации выше N $" },
    { key: "max_avg_dep", label: "Max AvgDep", title: "Пауза, если цена депозита выше N $",
      deps: true }
  ];
  var DEPS_ONLY_CAMPAIGN = "Депозиты Keitaro известны только у кампаний";
  var LEVEL_WORDS = { campaign: "Кампания", adset: "Адсет", ad: "Объявление" };
  var LEVEL_PLURAL = { campaign: "Кампании", adset: "Адсеты", ad: "Объявления" };
  var INTERVAL_WORDS = { 5: "5 мин", 10: "10 мин", 15: "15 мин", 30: "30 мин", 60: "1 час" };
  var ICON_PLAY = '<svg width="11" height="11" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">' +
    '<path d="M7 4.5v15a1 1 0 0 0 1.5.86l12.5-7.5a1 1 0 0 0 0-1.72L8.5 3.64A1 1 0 0 0 7 4.5z"/></svg>';
  var ICON_TRASH = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
    'stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M4 7h16M10 11v6M14 11v6' +
    'M6 7l1 13h10l1-13M9 7V4h6v3"/></svg>';

  var state = {
    sets: [],
    countries: [],
    intervals: [5, 10, 15, 30, 60],
    serverEnabled: true,
    current: null,      // открытое автоправило вместе с его строками GEO
    drafts: {},
    saving: {},
    view: "rules",
    events: [],
    eventsTotal: 0,
    historySet: "",
    running: {},
    geoAdding: false,
    bound: false
  };

  /* ---------- общее ---------- */

  function shown(value) {
    return value == null ? "" : String(value).replace(".", ",");
  }

  function parsed(text) {
    var clean = String(text == null ? "" : text).replace(",", ".").trim();
    if (!clean) return { ok: true, value: null };
    var value = Number(clean);
    return isFinite(value) && value > 0 ? { ok: true, value: value } : { ok: false };
  }

  function countryName(code) {
    var found = state.countries.find(function (row) { return row.code === code; });
    return found ? (found.ru || found.name) : "";
  }

  function stamp(iso) {
    if (!iso) return "—";
    return new Date(iso).toLocaleString("ru-RU", {
      day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"
    });
  }

  function failed(title, error) {
    notify({ title: title, message: (error && error.message) || String(error) });
  }

  /* ---------- список автоправил ---------- */

  function showScreen(name) {
    Array.prototype.forEach.call(document.querySelectorAll("[data-gr-screen]"), function (screen) {
      screen.hidden = screen.getAttribute("data-gr-screen") !== name;
    });
  }

  function renderSets() {
    byId("grSets").innerHTML = state.sets.length ? state.sets.map(function (set) {
      var geos = set.geos || [];
      var chips = geos.slice(0, 6).map(function (code) {
        return "<b>" + escapeHtml(code) + "</b>";
      }).join("") + (geos.length > 6 ? "<b>+" + (geos.length - 6) + "</b>" : "");
      var last = set.last_run_result || {};
      var busy = !!state.running[set.id];
      return '<tr data-gr-open="' + set.id + '">' +
        "<td>" + escapeHtml(set.name) + "</td>" +
        "<td>" + escapeHtml(LEVEL_PLURAL[set.level] || set.level) + "</td>" +
        "<td>" + escapeHtml(INTERVAL_WORDS[set.interval_minutes] || set.interval_minutes + " мин") +
        "</td>" +
        '<td><span class="gr-on gr-on--' + (set.auto_enabled ? "yes" : "no") + '">' +
        (set.auto_enabled ? "Вкл" : "Выкл") + "</span></td>" +
        '<td><span class="gr-chips">' + (chips || "—") + "</span></td>" +
        "<td>" + (set.last_run_at
          ? escapeHtml(stamp(set.last_run_at)) + " · сработало " + (last.triggered || 0) : "—") +
        "</td>" +
        '<td><span class="gr-actions">' +
        '<button type="button" data-gr-run-set="' + set.id + '" title="Прогнать сейчас" ' +
        'aria-label="Прогнать сейчас"' + (busy || !geos.length ? " disabled" : "") + ">" +
        ICON_PLAY + "</button>" +
        '<button type="button" data-gr-drop-set="' + set.id + '" title="Удалить" aria-label="Удалить">' +
        ICON_TRASH + "</button></span></td></tr>";
    }).join("") : '<tr><td colspan="7" style="padding:40px;text-align:center;color:#9B9292;' +
      'cursor:default;font-weight:600">Автоправил пока нет</td></tr>';
  }

  async function loadSets() {
    try {
      var payload = await api.get("/meta/geo-rules");
      state.sets = payload.items || [];
      state.countries = payload.countries || [];
      state.intervals = payload.intervals || state.intervals;
      state.serverEnabled = payload.server_enabled !== false;
    } catch (error) {
      byId("grSets").innerHTML = '<tr><td colspan="7" style="padding:36px;color:#B91414">' +
        escapeHtml(error.message || "Не удалось загрузить автоправила") + "</td></tr>";
      return;
    }
    renderSets();
    renderHistoryFilter();
  }

  /* Окно «Создать автоправило»: только название и область — остальное внутри. */
  function ensureCreateModal() {
    if (byId("grCreateModal")) return;
    var modal = document.createElement("div");
    modal.className = "mt-modal";
    modal.id = "grCreateModal";
    modal.setAttribute("role", "dialog");
    modal.setAttribute("aria-modal", "true");
    modal.setAttribute("aria-labelledby", "grCreateTitle");
    modal.innerHTML = '<div class="mt-modal__card mt-modal__card--narrow">' +
      '<div class="mt-modal__head"><h2 class="mt-modal__title" id="grCreateTitle">' +
      "Новое автоправило</h2>" +
      '<button class="mt-modal__x" type="button" data-gr-create-close aria-label="Закрыть">' +
      '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
      'stroke-width="2.4" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6 6 18"/>' +
      "</svg></button></div>" +
      '<div class="mt-modal__body">' +
      '<label class="mt-field"><span>Название</span>' +
      '<input class="meta-control" id="grCreateName" maxlength="160"></label>' +
      '<label class="mt-field"><span>Область действия</span>' +
      '<select class="meta-control" id="grCreateLevel">' +
      '<option value="campaign">Кампании</option><option value="adset">Адсеты</option>' +
      '<option value="ad">Объявления</option></select></label>' +
      '<div class="mt-form-error" id="grCreateError" role="alert" hidden></div></div>' +
      '<div class="mt-modal__foot">' +
      '<button class="mt-btn" type="button" data-gr-create-close>Отмена</button>' +
      '<button class="mt-primary" type="button" id="grCreateSave">Создать</button></div></div>';
    document.body.appendChild(modal);
    modal.addEventListener("click", function (event) {
      if (event.target === modal || event.target.closest("[data-gr-create-close]")) closeCreate();
    });
    byId("grCreateSave").addEventListener("click", createSet);
    byId("grCreateName").addEventListener("keydown", function (event) {
      if (event.key === "Enter") createSet();
    });
  }

  function openCreate() {
    ensureCreateModal();
    byId("grCreateName").value = "Автоправило " + (state.sets.length + 1);
    byId("grCreateLevel").value = "campaign";
    byId("grCreateError").hidden = true;
    byId("grCreateModal").classList.add("is-open");
    byId("grCreateName").select();
  }

  function closeCreate() {
    var modal = byId("grCreateModal");
    if (modal) modal.classList.remove("is-open");
  }

  async function createSet() {
    var name = byId("grCreateName").value.trim();
    var error = byId("grCreateError");
    if (!name) {
      error.textContent = "Укажите название";
      error.hidden = false;
      return;
    }
    var save = byId("grCreateSave");
    save.disabled = true;
    try {
      var created = await api.post("/meta/geo-rules", {
        name: name, level: byId("grCreateLevel").value
      });
      closeCreate();
      await loadSets();
      await openSet(created.id);
    } catch (problem) {
      error.textContent = problem.message || String(problem);
      error.hidden = false;
    } finally {
      save.disabled = false;
    }
  }

  async function dropSet(id) {
    var set = state.sets.find(function (row) { return row.id === id; }) || state.current;
    if (!(await askConfirm({
      title: "Удалить автоправило «" + (set ? set.name : "") + "»?",
      message: "Его GEO перестанут проверяться. История срабатываний останется.",
      confirmLabel: "Удалить", danger: true
    }))) return;
    try {
      await api["delete"]("/meta/geo-rules/" + id);
    } catch (error) {
      return failed("Не удалось удалить", error);
    }
    if (state.current && state.current.id === id) closeSet();
    await loadSets();
  }

  async function runSet(id) {
    var set = state.current && state.current.id === id ? state.current :
      state.sets.find(function (row) { return row.id === id; });
    if (!set || state.running[id]) return;
    if (!(await askConfirm({
      title: "Прогнать «" + set.name + "» сейчас?",
      message: "Активные " + (LEVEL_PLURAL[set.level] || "").toLowerCase() +
        ", которые подходят под правило, встанут на паузу в FB.",
      confirmLabel: "Прогнать"
    }))) return;
    state.running[id] = true;
    renderSets();
    renderSettings();
    try {
      var result = await api.post("/meta/geo-rules/" + id + "/run", {});
      notify({
        title: result.triggered ? "Правило сработало" : "Ничего не сработало",
        message: "Проверено: " + result.checked + " · сработало: " + result.triggered +
          " · на паузе: " + result.paused + (result.failed ? " · ошибок: " + result.failed : "")
      });
      if (result.triggered && window.CelestialMetaTree) window.CelestialMetaTree.reload();
    } catch (error) {
      failed("Прогон не выполнен", error);
    } finally {
      delete state.running[id];
    }
    await loadSets();
    if (state.current && state.current.id === id) await openSet(id);
    if (state.view === "history") await loadEvents(true);
  }

  /* ---------- одно автоправило ---------- */

  async function openSet(id) {
    try {
      state.current = await api.get("/meta/geo-rules/" + id);
    } catch (error) {
      return failed("Автоправило не открылось", error);
    }
    state.drafts = {};
    showScreen("editor");
    byId("grName").value = state.current.name;
    renderSettings();
    renderTable();
  }

  function closeSet() {
    state.current = null;
    state.drafts = {};
    showScreen("list");
  }

  function renderSettings() {
    var set = state.current;
    if (!set) return;
    byId("grLevel").value = set.level;
    byId("grInterval").innerHTML = state.intervals.map(function (minutes) {
      return '<option value="' + minutes + '"' + (minutes === set.interval_minutes ? " selected" : "") +
        ">" + (INTERVAL_WORDS[minutes] || minutes + " мин") + "</option>";
    }).join("");
    var auto = byId("grAuto");
    auto.checked = !!set.auto_enabled;
    auto.disabled = !state.serverEnabled;
    byId("grAutoWrap").classList.toggle("meta-switch--off", !state.serverEnabled);
    byId("grAutoLabel").textContent = state.serverEnabled
      ? "Автопрогон" : "Автопрогон выключен на сервере";
    var last = set.last_run_result || {};
    byId("grLast").textContent = set.last_run_at
      ? "Последний прогон: " + stamp(set.last_run_at) + " · сработало " + (last.triggered || 0)
      : "";
    var run = byId("grRun");
    var busy = !!state.running[set.id];
    run.disabled = busy || !(set.rules || []).length;
    run.lastChild.textContent = busy ? "Проверяем…" : "Прогнать сейчас";
  }

  async function saveSet(patch) {
    var set = state.current;
    if (!set) return;
    try {
      Object.assign(set, await api.patch("/meta/geo-rules/" + set.id, patch));
    } catch (error) {
      failed("Настройка не сохранилась", error);
      byId("grName").value = set.name;
    }
    renderSettings();
    renderTable();
    loadSets();
  }

  function renderHead() {
    var campaignLevel = !state.current || state.current.level === "campaign";
    byId("grHead").innerHTML = "<tr><th>GEO</th><th>Вкл/Выкл</th>" + COLUMNS.map(function (column) {
      var muted = column.deps && !campaignLevel;
      return '<th title="' + escapeHtml(muted ? DEPS_ONLY_CAMPAIGN : column.title) + '"' +
        (muted ? ' class="is-muted"' : "") + ">" + escapeHtml(column.label) + "</th>";
    }).join("") + "<th>Действия</th></tr>";
  }

  function isDirty(rule) {
    var draft = state.drafts[rule.country_code];
    if (!draft) return false;
    return COLUMNS.some(function (column) {
      if (!(column.key in draft)) return false;
      var value = parsed(draft[column.key]);
      return !value.ok || value.value !== rule[column.key];
    });
  }

  function renderTable() {
    renderHead();
    var rules = (state.current && state.current.rules) || [];
    var campaignLevel = !state.current || state.current.level === "campaign";
    byId("grBody").innerHTML = rules.length ? rules.map(function (rule) {
      var code = rule.country_code;
      var draft = state.drafts[code] || {};
      var dirty = isDirty(rule);
      var busy = !!state.saving[code];
      return '<tr class="gr-row' + (rule.is_enabled ? "" : " is-off") + (dirty ? " is-dirty" : "") +
        '" data-gr-row="' + code + '">' +
        '<td><div class="gr-geo"><b>' + escapeHtml(code) + "</b>" +
        escapeHtml(countryName(code)) + "</div></td>" +
        '<td><label class="meta-switch" style="justify-content:center"><input type="checkbox" ' +
        'data-gr-toggle="' + code + '"' + (rule.is_enabled ? " checked" : "") +
        (busy ? " disabled" : "") + ' aria-label="GEO ' + code + '">' +
        '<span class="meta-switch__box"></span></label></td>' +
        COLUMNS.map(function (column) {
          var text = column.key in draft ? draft[column.key] : shown(rule[column.key]);
          var muted = column.deps && !campaignLevel;
          return '<td><input class="gr-input' + (muted ? " is-muted" : "") +
            (parsed(text).ok ? "" : " is-invalid") + '" inputmode="decimal" placeholder="—" ' +
            'data-gr-geo="' + code + '" data-gr-key="' + column.key + '" value="' +
            escapeHtml(text) + '" aria-label="' + escapeHtml(column.label + " " + code) + '"' +
            (muted ? ' title="' + escapeHtml(DEPS_ONLY_CAMPAIGN) + '"' : "") +
            (busy ? " disabled" : "") + "></td>";
        }).join("") +
        '<td><span class="gr-actions">' +
        '<button type="button" data-gr-save="' + code + '" title="Сохранить" aria-label="Сохранить"' +
        (dirty && !busy ? ' class="is-ready"' : " disabled") + ">" +
        '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" ' +
        'stroke-width="2" stroke-linejoin="round"><path d="M5 3h11l3 3v15H5z"/><path d="M8 3v5h8V3M8 21v-7h8v7"/>' +
        "</svg></button>" +
        '<button type="button" data-gr-drop="' + code + '" title="Убрать GEO" aria-label="Убрать GEO"' +
        (busy ? " disabled" : "") + ">" + ICON_TRASH + "</button></span></td></tr>";
    }).join("") : '<tr><td colspan="10" style="padding:36px;color:#9B9292">' +
      "Добавьте GEO, чтобы задать пороги</td></tr>";
    renderGeoPicker();
    renderSettings();
  }

  function renderGeoPicker() {
    var used = {};
    ((state.current && state.current.rules) || []).forEach(function (rule) {
      used[rule.country_code] = true;
    });
    var free = state.countries.filter(function (row) { return !used[row.code]; });
    byId("grAdd").disabled = !free.length;
    var query = byId("grGeoSearch").value.trim().toLocaleLowerCase();
    var matches = free.filter(function (row) {
      return [row.code, row.ru, row.name].join(" ").toLocaleLowerCase().indexOf(query) >= 0;
    });
    byId("grGeoList").innerHTML = matches.map(function (row) {
      return '<button type="button" class="gr-geo-choice" data-gr-geo-add="' + escapeHtml(row.code) +
        '"' + (state.geoAdding ? " disabled" : "") + '><b>' + escapeHtml(row.code) +
        '</b><span>' + escapeHtml(row.ru || row.name) + '</span><span aria-hidden="true">+</span></button>';
    }).join("") || '<div class="gr-geo-empty">' +
      (free.length ? "Ничего не найдено. Попробуйте другое название или код." : "Все GEO уже добавлены") + "</div>";
    byId("grGeoList").setAttribute("aria-busy", String(state.geoAdding));
    byId("grGeoStatus").textContent = state.geoAdding ? "Добавляем GEO…" :
      "Уже добавленные страны не показываются в списке.";
  }

  function openGeoPicker() {
    if (!state.current) return;
    byId("grGeoSearch").value = "";
    renderGeoPicker();
    byId("grGeoModal").classList.add("is-open");
    byId("grGeoSearch").focus();
  }

  function closeGeoPicker() {
    byId("grGeoModal").classList.remove("is-open");
    (byId("grAdd").disabled ? byId("grName") : byId("grAdd")).focus();
  }

  function payloadOf(rule, draft) {
    var body = { is_enabled: rule.is_enabled };
    var bad = [];
    COLUMNS.forEach(function (column) {
      var value = parsed(draft && column.key in draft ? draft[column.key] : rule[column.key]);
      if (!value.ok) bad.push(column.label);
      body[column.key] = value.ok ? value.value : null;
    });
    return { body: body, bad: bad };
  }

  async function saveRule(code, patch) {
    var set = state.current;
    if (!set) return;
    var rule = set.rules.find(function (row) { return row.country_code === code; });
    var prepared = payloadOf(Object.assign({}, rule || { country_code: code, is_enabled: true },
      patch || {}), state.drafts[code]);
    if (prepared.bad.length) {
      notify({ title: "Проверьте значения", message: "Нужны числа больше нуля: " + prepared.bad.join(", ") });
      return;
    }
    state.saving[code] = true;
    renderTable();
    try {
      var saved = await api.put("/meta/geo-rules/" + set.id + "/geo/" + encodeURIComponent(code),
        prepared.body);
      var index = set.rules.findIndex(function (row) { return row.country_code === code; });
      if (index >= 0) set.rules[index] = saved;
      else set.rules.push(saved);
      set.rules.sort(function (a, b) { return a.country_code.localeCompare(b.country_code); });
      delete state.drafts[code];
      if (index < 0) loadSets();
      return true;
    } catch (error) {
      failed("GEO не сохранилось", error);
    } finally {
      delete state.saving[code];
      renderTable();
    }
  }

  async function dropRule(code) {
    var set = state.current;
    if (!set || !(await askConfirm({
      title: "Убрать " + code + " из автоправила?",
      message: "Объекты этого GEO перестанут проверяться этим правилом.",
      confirmLabel: "Убрать", danger: true
    }))) return;
    try {
      await api["delete"]("/meta/geo-rules/" + set.id + "/geo/" + encodeURIComponent(code));
      set.rules = set.rules.filter(function (row) { return row.country_code !== code; });
      delete state.drafts[code];
      renderTable();
      loadSets();
    } catch (error) {
      failed("Не удалось убрать GEO", error);
    }
  }

  /* ---------- история ---------- */

  function renderHistoryFilter() {
    var select = byId("grHistorySet");
    if (!select) return;
    select.innerHTML = '<option value="">Все автоправила</option>' + state.sets.map(function (set) {
      return '<option value="' + set.id + '"' + (set.id === state.historySet ? " selected" : "") +
        ">" + escapeHtml(set.name) + "</option>";
    }).join("");
  }

  function renderEvents() {
    byId("grEvents").innerHTML = state.events.length ? state.events.map(function (row) {
      var failedRow = row.status === "failed";
      return "<tr><td>" + escapeHtml(stamp(row.created_at)) + "</td>" +
        '<td><b class="gr-rule-name">' + escapeHtml(row.rule_name || "—") + "</b></td>" +
        '<td><div class="gr-obj"><b>' + escapeHtml(row.name) + "</b><i>" +
        escapeHtml((LEVEL_WORDS[row.level] || row.level) + " · " + row.external_id) + "</i></div></td>" +
        '<td><div class="gr-obj"><b>' + escapeHtml(row.account_name || "—") + "</b><i>" +
        escapeHtml(row.account_external_id || "") + "</i></div></td>" +
        '<td><span class="gr-geo"><b>' + escapeHtml(row.country_code) + "</b></span></td>" +
        "<td>" + escapeHtml(row.reason) + "</td>" +
        '<td><span class="gr-badge gr-badge--' + (failedRow ? "failed" : "paused") + '"' +
        (failedRow && row.error ? ' title="' + escapeHtml(row.error) + '"' : "") + ">" +
        (failedRow ? "Ошибка" : "Пауза") + "</span></td>" +
        "<td>" + (row.trigger === "manual" ? "Вручную" : "Авто") + "</td></tr>";
    }).join("") : '<tr><td colspan="8" style="padding:36px;text-align:center;color:#9B9292">' +
      "Срабатываний пока не было</td></tr>";
    byId("grEventsCount").textContent = state.eventsTotal
      ? "Показано " + state.events.length + " из " + state.eventsTotal : "";
    byId("grMore").hidden = state.events.length >= state.eventsTotal;
  }

  async function loadEvents(reset) {
    var offset = reset ? 0 : state.events.length;
    var query = "?limit=50&offset=" + offset +
      (state.historySet ? "&rule_set_id=" + encodeURIComponent(state.historySet) : "");
    try {
      var page = await api.get("/meta/geo-rules/events" + query);
      state.events = reset ? page.items : state.events.concat(page.items);
      state.eventsTotal = page.total;
    } catch (error) {
      failed("История не загрузилась", error);
    }
    renderEvents();
  }

  function setView(view) {
    state.view = view;
    Array.prototype.forEach.call(document.querySelectorAll("[data-gr-view]"), function (button) {
      button.classList.toggle("meta-level--active", button.getAttribute("data-gr-view") === view);
    });
    Array.prototype.forEach.call(document.querySelectorAll("[data-gr-panel]"), function (panel) {
      panel.hidden = panel.getAttribute("data-gr-panel") !== view;
    });
    if (view === "history") loadEvents(true);
  }

  /* ---------- события ---------- */

  function bind() {
    Array.prototype.forEach.call(document.querySelectorAll("[data-gr-view]"), function (button) {
      button.addEventListener("click", function () { setView(button.getAttribute("data-gr-view")); });
    });
    byId("grCreate").addEventListener("click", openCreate);
    byId("grSets").addEventListener("click", function (event) {
      var run = event.target.closest("[data-gr-run-set]");
      if (run) return runSet(run.getAttribute("data-gr-run-set"));
      var drop = event.target.closest("[data-gr-drop-set]");
      if (drop) return dropSet(drop.getAttribute("data-gr-drop-set"));
      var row = event.target.closest("[data-gr-open]");
      if (row) openSet(row.getAttribute("data-gr-open"));
    });
    byId("grBack").addEventListener("click", closeSet);
    byId("grDeleteSet").addEventListener("click", function () {
      if (state.current) dropSet(state.current.id);
    });
    var name = byId("grName");
    name.addEventListener("keydown", function (event) {
      if (event.key === "Enter") name.blur();
      if (event.key === "Escape") {
        name.value = state.current ? state.current.name : "";
        name.blur();
      }
    });
    name.addEventListener("blur", function () {
      var value = name.value.trim();
      if (!state.current) return;
      if (!value) {
        name.value = state.current.name;
        return;
      }
      if (value !== state.current.name) saveSet({ name: value });
    });
    byId("grLevel").addEventListener("change", function (event) {
      saveSet({ level: event.target.value });
    });
    byId("grInterval").addEventListener("change", function (event) {
      saveSet({ interval_minutes: Number(event.target.value) });
    });
    byId("grAuto").addEventListener("change", function (event) {
      saveSet({ auto_enabled: event.target.checked });
    });
    byId("grRun").addEventListener("click", function () {
      if (state.current) runSet(state.current.id);
    });
    byId("grAdd").addEventListener("click", openGeoPicker);
    byId("grGeoSearch").addEventListener("input", renderGeoPicker);
    byId("grGeoModal").addEventListener("click", function (event) {
      if (event.target === byId("grGeoModal") || event.target.closest("[data-gr-geo-close]")) closeGeoPicker();
    });
    byId("grGeoList").addEventListener("click", async function (event) {
      var choice = event.target.closest("[data-gr-geo-add]");
      if (!choice || state.geoAdding || !state.current) return;
      state.geoAdding = true;
      renderGeoPicker();
      try {
        if (await saveRule(choice.getAttribute("data-gr-geo-add"), { is_enabled: true })) closeGeoPicker();
      } finally {
        state.geoAdding = false;
        renderGeoPicker();
      }
    });
    var body = byId("grBody");
    body.addEventListener("input", function (event) {
      var field = event.target;
      if (!field.hasAttribute("data-gr-key")) return;
      var code = field.getAttribute("data-gr-geo");
      state.drafts[code] = state.drafts[code] || {};
      state.drafts[code][field.getAttribute("data-gr-key")] = field.value;
      field.classList.toggle("is-invalid", !parsed(field.value).ok);
      var rule = state.current.rules.find(function (row) { return row.country_code === code; });
      var row = field.closest("tr");
      var dirty = rule && isDirty(rule);
      row.classList.toggle("is-dirty", !!dirty);
      var save = row.querySelector("[data-gr-save]");
      save.disabled = !dirty;
      save.classList.toggle("is-ready", !!dirty);
    });
    body.addEventListener("keydown", function (event) {
      if (event.key === "Enter" && event.target.hasAttribute("data-gr-key")) {
        saveRule(event.target.getAttribute("data-gr-geo"));
      }
    });
    body.addEventListener("change", function (event) {
      var toggle = event.target.closest("[data-gr-toggle]");
      if (toggle) saveRule(toggle.getAttribute("data-gr-toggle"), { is_enabled: toggle.checked });
    });
    body.addEventListener("click", function (event) {
      var save = event.target.closest("[data-gr-save]");
      if (save) return saveRule(save.getAttribute("data-gr-save"));
      var drop = event.target.closest("[data-gr-drop]");
      if (drop) dropRule(drop.getAttribute("data-gr-drop"));
    });
    byId("grHistorySet").addEventListener("change", function (event) {
      state.historySet = event.target.value;
      loadEvents(true);
    });
    byId("grMore").addEventListener("click", function () { loadEvents(false); });
    document.addEventListener("keydown", function (event) {
      if (byId("grGeoModal").classList.contains("is-open")) {
        if (event.key === "Escape") { event.preventDefault(); closeGeoPicker(); return; }
        if (event.key === "Tab") {
          var fields = byId("grGeoModal").querySelectorAll("button:not(:disabled), input:not(:disabled)");
          var first = fields[0], last = fields[fields.length - 1];
          if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
          else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
        }
      } else if (event.key === "Escape") closeCreate();
    });
  }

  async function init() {
    if (!byId("grSets")) return;
    if (!state.bound) {
      state.bound = true;
      bind();
    }
    showScreen("list");
    await loadSets();
  }

  window.CelestialMetaGeoRules = { init: init, reload: loadSets };
})();
