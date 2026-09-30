/*
 * MetaAds v2 · «Автоправила» по GEO: пороги, при которых объект встаёт на паузу.
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
  var INTERVAL_WORDS = { 15: "15 мин", 30: "30 мин", 60: "1 час", 120: "2 часа", 240: "4 часа" };

  var state = {
    settings: null,
    rules: [],
    countries: [],
    drafts: {},
    saving: {},
    view: "rules",
    events: [],
    eventsTotal: 0,
    running: false,
    bound: false
  };

  /* ---------- числа ---------- */

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
    var date = new Date(iso);
    return date.toLocaleString("ru-RU", {
      day: "numeric", month: "short", hour: "2-digit", minute: "2-digit"
    });
  }

  /* ---------- настройки ---------- */

  function renderSettings() {
    var config = state.settings;
    if (!config) return;
    byId("grLevel").value = config.level;
    byId("grInterval").innerHTML = (config.intervals || [15, 30, 60, 120, 240]).map(function (minutes) {
      return '<option value="' + minutes + '"' + (minutes === config.interval_minutes ? " selected" : "") +
        ">" + (INTERVAL_WORDS[minutes] || minutes + " мин") + "</option>";
    }).join("");
    var auto = byId("grAuto");
    auto.checked = !!config.auto_enabled;
    auto.disabled = !config.server_enabled;
    byId("grAutoWrap").classList.toggle("meta-switch--off", !config.server_enabled);
    byId("grAutoLabel").textContent = config.server_enabled
      ? "Автопрогон" : "Автопрогон выключен на сервере";
    var last = config.last_run_result || {};
    byId("grLast").textContent = config.last_run_at
      ? "Последний прогон: " + stamp(config.last_run_at) + " · сработало " + (last.triggered || 0)
      : "";
    var run = byId("grRun");
    run.disabled = state.running;
    run.lastChild.textContent = state.running ? "Проверяем…" : "Прогнать сейчас";
  }

  async function saveSettings(patch) {
    try {
      state.settings = Object.assign(state.settings || {},
        await api.put("/meta/geo-rules/settings", patch));
    } catch (error) {
      notify({ title: "Настройка не сохранилась", message: error.message || String(error) });
    }
    renderSettings();
    renderTable();
  }

  /* ---------- таблица правил ---------- */

  function renderHead() {
    var campaignLevel = !state.settings || state.settings.level === "campaign";
    byId("grHead").innerHTML = "<tr><th>GEO</th><th>Вкл/Выкл</th>" + COLUMNS.map(function (column) {
      var muted = column.deps && !campaignLevel;
      return '<th title="' + escapeHtml(muted ? DEPS_ONLY_CAMPAIGN : column.title) + '"' +
        (muted ? ' class="is-muted"' : "") + ">" + escapeHtml(column.label) + "</th>";
    }).join("") + "<th>Действия</th></tr>";
  }

  function draftOf(rule) {
    return state.drafts[rule.country_code] || null;
  }

  function isDirty(rule) {
    var draft = draftOf(rule);
    if (!draft) return false;
    return COLUMNS.some(function (column) {
      var value = parsed(draft[column.key]);
      return !value.ok || value.value !== rule[column.key];
    });
  }

  function renderTable() {
    renderHead();
    var campaignLevel = !state.settings || state.settings.level === "campaign";
    byId("grBody").innerHTML = state.rules.length ? state.rules.map(function (rule) {
      var code = rule.country_code;
      var draft = draftOf(rule) || {};
      var dirty = isDirty(rule);
      var busy = !!state.saving[code];
      return '<tr class="gr-row' + (rule.is_enabled ? "" : " is-off") + (dirty ? " is-dirty" : "") +
        '" data-gr-row="' + code + '">' +
        '<td><div class="gr-geo"><b>' + escapeHtml(code) + "</b>" +
        escapeHtml(countryName(code)) + "</div></td>" +
        '<td><label class="meta-switch" style="justify-content:center"><input type="checkbox" ' +
        'data-gr-toggle="' + code + '"' + (rule.is_enabled ? " checked" : "") +
        (busy ? " disabled" : "") + ' aria-label="Правило ' + code + '">' +
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
        '<button type="button" data-gr-drop="' + code + '" title="Удалить" aria-label="Удалить"' +
        (busy ? " disabled" : "") + '><svg width="14" height="14" viewBox="0 0 24 24" fill="none" ' +
        'stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M4 7h16M10 11v6M14 11v6' +
        'M6 7l1 13h10l1-13M9 7V4h6v3"/></svg></button></span></td></tr>';
    }).join("") : '<tr><td colspan="10" style="padding:36px;color:#9B9292">' +
      "Добавьте GEO, чтобы настроить правила</td></tr>";
    renderGeoPicker();
  }

  function renderGeoPicker() {
    var used = {};
    state.rules.forEach(function (rule) { used[rule.country_code] = true; });
    var free = state.countries.filter(function (row) { return !used[row.code]; });
    byId("grGeo").innerHTML = '<option value="">Выберите GEO</option>' + free.map(function (row) {
      return '<option value="' + escapeHtml(row.code) + '">' + escapeHtml(row.code) + " · " +
        escapeHtml(row.ru || row.name) + "</option>";
    }).join("");
    byId("grAdd").disabled = !free.length;
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
    var rule = state.rules.find(function (row) { return row.country_code === code; });
    var draft = state.drafts[code];
    var prepared = payloadOf(Object.assign({}, rule || { country_code: code, is_enabled: true },
      patch || {}), draft);
    if (prepared.bad.length) {
      notify({ title: "Проверьте значения", message: "Нужны числа больше нуля: " + prepared.bad.join(", ") });
      return;
    }
    state.saving[code] = true;
    renderTable();
    try {
      var saved = await api.put("/meta/geo-rules/" + encodeURIComponent(code), prepared.body);
      var index = state.rules.findIndex(function (row) { return row.country_code === code; });
      if (index >= 0) state.rules[index] = saved;
      else state.rules.push(saved);
      state.rules.sort(function (a, b) { return a.country_code.localeCompare(b.country_code); });
      delete state.drafts[code];
    } catch (error) {
      notify({ title: "Правило не сохранилось", message: error.message || String(error) });
    } finally {
      delete state.saving[code];
      renderTable();
    }
  }

  async function dropRule(code) {
    if (!(await askConfirm({
      title: "Удалить правило " + code + "?",
      message: "Объекты этого GEO перестанут проверяться.",
      confirmLabel: "Удалить", danger: true
    }))) return;
    try {
      await api["delete"]("/meta/geo-rules/" + encodeURIComponent(code));
      state.rules = state.rules.filter(function (row) { return row.country_code !== code; });
      delete state.drafts[code];
      renderTable();
    } catch (error) {
      notify({ title: "Не удалось удалить", message: error.message || String(error) });
    }
  }

  /* ---------- прогон ---------- */

  async function runNow() {
    if (state.running) return;
    var level = { campaign: "кампании", adset: "адсеты", ad: "объявления" }[
      state.settings ? state.settings.level : "campaign"];
    if (!(await askConfirm({
      title: "Прогнать правила сейчас?",
      message: "Активные " + level + ", которые подходят под правила, встанут на паузу в FB.",
      confirmLabel: "Прогнать"
    }))) return;
    state.running = true;
    renderSettings();
    try {
      var result = await api.post("/meta/geo-rules/run", {});
      notify({
        title: result.triggered ? "Правила сработали" : "Ничего не сработало",
        message: "Проверено: " + result.checked + " · сработало: " + result.triggered +
          " · на паузе: " + result.paused + (result.failed ? " · ошибок: " + result.failed : "")
      });
      if (result.triggered && window.CelestialMetaTree) window.CelestialMetaTree.reload();
    } catch (error) {
      notify({ title: "Прогон не выполнен", message: error.message || String(error) });
    } finally {
      state.running = false;
    }
    await loadRules();
    if (state.view === "history") await loadEvents(true);
  }

  /* ---------- история ---------- */

  function renderEvents() {
    byId("grEvents").innerHTML = state.events.length ? state.events.map(function (row) {
      var failed = row.status === "failed";
      return "<tr><td>" + escapeHtml(stamp(row.created_at)) + "</td>" +
        '<td><div class="gr-obj"><b>' + escapeHtml(row.name) + "</b><i>" +
        escapeHtml((LEVEL_WORDS[row.level] || row.level) + " · " + row.external_id) + "</i></div></td>" +
        '<td><div class="gr-obj"><b>' + escapeHtml(row.account_name || "—") + "</b><i>" +
        escapeHtml(row.account_external_id || "") + "</i></div></td>" +
        '<td><span class="gr-geo"><b>' + escapeHtml(row.country_code) + "</b></span></td>" +
        "<td>" + escapeHtml(row.reason) + "</td>" +
        '<td><span class="gr-badge gr-badge--' + (failed ? "failed" : "paused") + '"' +
        (failed && row.error ? ' title="' + escapeHtml(row.error) + '"' : "") + ">" +
        (failed ? "Ошибка" : "Пауза") + "</span></td>" +
        "<td>" + (row.trigger === "manual" ? "Вручную" : "Авто") + "</td></tr>";
    }).join("") : '<tr><td colspan="7" style="padding:36px;text-align:center;color:#9B9292">' +
      "Срабатываний пока не было</td></tr>";
    byId("grEventsCount").textContent = state.eventsTotal
      ? "Показано " + state.events.length + " из " + state.eventsTotal : "";
    byId("grMore").hidden = state.events.length >= state.eventsTotal;
  }

  async function loadEvents(reset) {
    var offset = reset ? 0 : state.events.length;
    try {
      var page = await api.get("/meta/geo-rules/events?limit=50&offset=" + offset);
      state.events = reset ? page.items : state.events.concat(page.items);
      state.eventsTotal = page.total;
    } catch (error) {
      notify({ title: "История не загрузилась", message: error.message || String(error) });
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
    byId("grLevel").addEventListener("change", function (event) {
      saveSettings({ level: event.target.value });
    });
    byId("grInterval").addEventListener("change", function (event) {
      saveSettings({ interval_minutes: Number(event.target.value) });
    });
    byId("grAuto").addEventListener("change", function (event) {
      saveSettings({ auto_enabled: event.target.checked });
    });
    byId("grRun").addEventListener("click", runNow);
    byId("grAdd").addEventListener("click", function () {
      var code = byId("grGeo").value;
      if (!code) return byId("grGeo").focus();
      saveRule(code, { is_enabled: true });
    });
    var body = byId("grBody");
    body.addEventListener("input", function (event) {
      var field = event.target;
      if (!field.hasAttribute("data-gr-key")) return;
      var code = field.getAttribute("data-gr-geo");
      state.drafts[code] = state.drafts[code] || {};
      state.drafts[code][field.getAttribute("data-gr-key")] = field.value;
      field.classList.toggle("is-invalid", !parsed(field.value).ok);
      var rule = state.rules.find(function (row) { return row.country_code === code; });
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
    byId("grMore").addEventListener("click", function () { loadEvents(false); });
  }

  async function loadRules() {
    try {
      var payload = await api.get("/meta/geo-rules");
      state.settings = payload.settings;
      state.rules = payload.rules || [];
      state.countries = payload.countries || [];
    } catch (error) {
      byId("grBody").innerHTML = '<tr><td colspan="10" style="padding:36px;color:#B91414">' +
        escapeHtml(error.message || "Не удалось загрузить правила") + "</td></tr>";
      return;
    }
    renderSettings();
    renderTable();
  }

  async function init() {
    if (!byId("grBody")) return;
    if (!state.bound) {
      state.bound = true;
      bind();
    }
    await loadRules();
  }

  window.CelestialMetaGeoRules = { init: init, reload: loadRules };
})();
