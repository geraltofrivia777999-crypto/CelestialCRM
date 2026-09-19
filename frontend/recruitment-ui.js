/* Раздел «Рекрутинг»: шаблоны поиска HH, отклики, кандидаты, настройки HH.

   Все данные живут в Recruitment Service — CRM только показывает их через
   свой бэкенд-прокси (/api/v1/recruitment/*). Последний снимок держим в кэше
   вкладки для быстрого старта, а актуальность догоняем частым фоновым опросом. */
(function () {
  "use strict";

  var api = window.CelestialAPI;

  var state = {
    tab: "search",
    templates: [],
    selectedTemplateId: "",
    runs: {},
    runTimer: null,
    // Находки поиска: то, что нашли шаблоны и что HR ещё не разобрала.
    pending: [],
    pendingTemplate: "",
    pendingLoading: false,
    // Входящие отклики: человек написал сам. Отдельный пул, а не тот же
    // список с другим фильтром, — иначе результаты поиска попадают в «Отклики».
    incoming: [],
    incomingLoading: false,
    // Источник отклика: telegram | hh. Пусто — показываем все.
    incomingSource: "",
    // Доска найма: этапы ведёт CRM, сервис рекрутинга их не хранит.
    board: null,
    // Раскрытая карточка кандидата. Держим саму строку, а не id: после
    // сохранения доска перезагружается, и по id пришлось бы искать заново.
    boardCard: null,
    // Раскрытое «Резюме» в карточке телеграмного кандидата — id строки.
    boardResume: null,
    // Что не так с формой шаблона — показывается в самом окне: страницу под
    // ним закрывает подложка, и там ошибку никто не увидит.
    editorError: "",
    // Что не так с формой «Завести кандидата» — показывается в самом окне.
    newCandidateError: "",
    // Форма «Завести кандидата»: null — окно закрыто.
    newCandidate: null,
    // Файл записи интервью, выбранный в той же форме. Кандидата ещё нет, и
    // загружать файл некуда: он уходит на сервер сразу после заведения.
    newCandidateRecord: null,
    boardLoading: false,
    boardSearch: "",
    boardStage: "",
    boardServiceDown: false,
    users: [],
    hh: null,
    vacancies: [],
    // В критерий уходит ID области HH, а подпись нужна только человеку.
    // Запоминаем её локально, чтобы после повторного открытия формы не
    // показывать вместо «Казань» техническое значение «88».
    areaLabels: loadAreaLabels(),
    areaQuery: "",
    areaSuggestions: [],
    areaLoading: false,
    areaTimer: null,
    areaRequest: 0,
    responseTemplate: "",
    // Отклики с раскрытым сообщением целиком — id кандидатов.
    openTexts: [],
    // Раскрытые разборы оценки. Автообновление заменяет карточки целиком,
    // поэтому открытое состояние нельзя оставлять только в DOM.
    openBreakdowns: {},
    editor: null,
    busy: false,
    /* Когда какой список в последний раз пришёл с сервера. По этим отметкам
       вкладка решает, показывать данные сразу или сначала их запросить. */
    stamps: {},
    // Сколько фоновых обновлений сейчас идёт: список при них остаётся на месте.
    refreshing: 0,
    templatesLoading: false
  };

  /* Кэш нужен только для мгновенной отрисовки, а не как источник истины.
     Динамические списки протухают быстрее шаблонов: новый отклик или движение
     карточки должны появиться почти сразу. */
  var FRESH_MS = {
    templates: 60000,
    pending: 10000,
    incoming: 10000,
    board: 10000
  };
  var CACHE_VERSION = 1;
  var CACHE_MAX_AGE = 30 * 60 * 1000;
  var CACHE_ITEM_LIMIT = 1500000;
  var LIVE_REFRESH_MS = 10000;
  var lastLiveRefresh = 0;

  function isFresh(key) {
    return Date.now() - (state.stamps[key] || 0) < (FRESH_MS[key] || 10000);
  }

  function cachePrefix() {
    var user = window.CelestialSession && window.CelestialSession.read();
    return user && user.id
      ? "celestial.recruitment." + CACHE_VERSION + "." + user.id + "."
      : "";
  }

  function cacheName(name) {
    if (name === "pending") return "pending." + (state.pendingTemplate || "all");
    return name;
  }

  function readCache(name) {
    var prefix = cachePrefix();
    if (!prefix) return null;
    try {
      var raw = window.sessionStorage.getItem(prefix + cacheName(name));
      if (!raw) return null;
      var saved = JSON.parse(raw);
      if (!saved || Date.now() - Number(saved.savedAt || 0) > CACHE_MAX_AGE) return null;
      return saved;
    } catch (_) {
      return null;
    }
  }

  function writeCache(name, value) {
    var prefix = cachePrefix();
    if (!prefix) return;
    try {
      var raw = JSON.stringify({ savedAt: Date.now(), value: value });
      // Большое резюме не должно вытеснить из sessionStorage сессию и весь UI.
      if (raw.length <= CACHE_ITEM_LIMIT) {
        window.sessionStorage.setItem(prefix + cacheName(name), raw);
      }
    } catch (_) { /* private mode или закончилась квота */ }
  }

  function dropCache(name) {
    var prefix = cachePrefix();
    if (!prefix) return;
    try { window.sessionStorage.removeItem(prefix + cacheName(name)); } catch (_) {}
  }

  /* Что именно человек смотрел: вкладка, выбранный шаблон, фильтры списков.
     Данные раздела уже переживают перезаход, а вид — нет: после обновления
     страницы или перехода в другой раздел и обратно всё возвращалось к
     «Поиску резюме» со сброшенными фильтрами, и место в работе приходилось
     искать заново. */
  var VIEW_FIELDS = [
    "tab", "selectedTemplateId", "pendingTemplate", "incomingSource",
    "responseTemplate", "boardSearch", "boardStage"
  ];
  var lastView = "";

  function persistView() {
    var value = {};
    VIEW_FIELDS.forEach(function (name) { value[name] = state[name]; });
    var raw = JSON.stringify(value);
    // Пишем только когда вид действительно изменился: render зовут на каждый
    // символ в поиске, а sessionStorage — синхронный.
    if (raw === lastView) return;
    lastView = raw;
    writeCache("view", value);
  }

  function restoreView() {
    var saved = readCache("view");
    var value = saved && saved.value;
    if (!value) return;
    VIEW_FIELDS.forEach(function (name) {
      if (typeof value[name] === "string") state[name] = value[name];
    });
    if (["search", "responses", "candidates"].indexOf(state.tab) < 0) state.tab = "search";
    lastView = JSON.stringify(value);
  }

  function restoreCache() {
    // Статус HH тоже лежит в снимке: он меняется раз в полгода, а без него
    // шапка на первом кадре каждый раз уверяла, что HH не подключён.
    var hh = readCache("hh");
    if (hh && hh.value) state.hh = hh.value;
    ["templates", "pending", "incoming", "board", "users"].forEach(function (name) {
      var saved = readCache(name);
      if (!saved) return;
      state[name] = saved.value;
      if (name === "board" && saved.value) {
        state.boardServiceDown = saved.value.service_available === false;
      }
      if (name !== "users") state.stamps[name] = Number(saved.savedAt || 0);
    });
  }

  /* Источники, которые считаются входящими отклики. Результаты активного
     поиска по резюме (`hh`) сюда не входят: это находки, а не отклики, и
     смешивать их — ровно та путаница, из-за которой «Отклики» показывали всё
     найденное. Когда подключат HH negotiations, их источник добавится сюда. */

  var INTERVALS = [
    [15, "15 минут"], [30, "30 минут"], [60, "1 час"], [120, "2 часа"],
    [360, "6 часов"], [720, "12 часов"], [1440, "24 часа"]
  ];

  /* Структурные критерии: HR выбирает значение по-человечески, а UI сам
     переводит выбор в key/value, которые понимает Recruitment Service. */
  /* Сервис понимает несколько вариантов в одном критерии через «|»: совпадает
     любой из перечисленных. Поэтому там, где у HR обычно не один ответ —
     должность и её синонимы, статусы поиска, список стран — поле принимает
     несколько значений, а склейка происходит уже при отправке. */
  var CRITERIA = [
    { key: "position", label: "Должность", type: "tags",
      hint: "синонимы названия — совпадёт любой",
      placeholder: "Media Buyer" },
    { key: "experience_level", label: "Опыт", type: "multi", hint: "общий стаж",
      options: [
        ["no_experience", "Без опыта"], ["1_3_years", "1–3 года"],
        ["3_6_years", "3–6 лет"], ["6_plus_years", "6+ лет"]
      ] },
    { key: "employment_type", label: "Занятость", type: "multi",
      options: [
        ["full", "Полная"], ["part_time", "Частичная"],
        ["internship", "Стажировка"], ["volunteer", "Волонтёрство"]
      ] },
    { key: "work_format", label: "Формат работы", type: "multi",
      options: [
        ["on_site", "Офис"], ["remote", "Удалённо"], ["hybrid", "Гибрид"],
        ["field_work", "Разъезды"], ["fly_in_fly_out", "Вахта"]
      ] },
    { key: "job_search_status", label: "Статус поиска", type: "multi",
      options: [
        ["active_search", "Активно ищет"], ["looking_for_offers", "Рассматривает предложения"],
        ["not_looking_for_job", "Не ищет работу"], ["has_job_offer", "Есть оффер"],
        ["accepted_job_offer", "Принял оффер"]
      ] },
    /* Город — структурный фильтр HH. Пользователь видит название, но сервис
       получает ID области; несколько выбранных ID соединяются через «|». */
    { key: "geo_area_id", label: "Город", type: "areas",
      hint: "город проживания кандидата; можно выбрать несколько",
      placeholder: "Начните вводить город" }
  ];

  var KEYWORD_CATEGORIES = [
    ["vertical", "Вертикаль"], ["traffic_source", "Источник трафика"],
    ["technology", "Технология"], ["keyword", "Другое"]
  ];

  /* Третий элемент — короткая подпись для переключателя в карточке критерия:
     «Не учитывать» в кнопку шириной с палец не помещается. Порядок — от
     безобидного к строгому, как читают слева направо. */
  var MODES = [
    ["ignore", "Не учитывать", "Нет"],
    ["preferred", "Желательный", "Желат."],
    ["required", "Обязательный", "Обязат."]
  ];

  var TIERS = {
    hot: { label: "HOT", bg: "#B91414", color: "#fff" },
    high: { label: "HIGH", bg: "#C9821F", color: "#fff" },
    medium: { label: "MEDIUM", bg: "#2C4E77", color: "#fff" },
    low: { label: "LOW", bg: "#E8E2E2", color: "#6A6161" }
  };

  var SOURCES = { hh: "HH", telegram: "Telegram" };

  function criterionByKey(key) {
    return CRITERIA.filter(function (item) { return item.key === key; })[0];
  }

  function isMulti(item) {
    return item.type === "multi" || item.type === "tags" || item.type === "areas";
  }

  function loadAreaLabels() {
    try {
      return JSON.parse(localStorage.getItem("celestial.recruitment.hhAreas") || "{}") || {};
    } catch (_) {
      return {};
    }
  }

  function rememberArea(area) {
    var label = area.name + (area.parent ? " (" + area.parent + ")" : "");
    state.areaLabels[String(area.id)] = label;
    try {
      localStorage.setItem(
        "celestial.recruitment.hhAreas", JSON.stringify(state.areaLabels)
      );
    } catch (_) {}
    saveAreaLabels();
    return label;
  }

  /* Названия городов нужны не только тому браузеру, где город выбирали: в
     критерии шаблона лежит ID области HH, и без названия соответствие города
     не с чем сравнивать. Поэтому справочник едет в настройки пользователя. */
  var areaSaveTimer = null;

  function saveAreaLabels() {
    if (areaSaveTimer) window.clearTimeout(areaSaveTimer);
    areaSaveTimer = window.setTimeout(function () {
      api.put("/me/preferences/recruitment.hh_areas", { value: state.areaLabels })
        .catch(function () { /* подпись останется локальной — не критично */ });
    }, 400);
  }

  async function loadStoredAreaLabels() {
    try {
      var saved = await api.get("/me/preferences/recruitment.hh_areas");
      var value = (saved && saved.value) || {};
      Object.keys(value).forEach(function (id) {
        if (!state.areaLabels[id]) state.areaLabels[id] = value[id];
      });
      if (Object.keys(value).length) render();
    } catch (_) { /* без справочника критерий покажет «нет данных» */ }
  }

  function areaLabel(id) {
    return state.areaLabels[String(id)] || "Область HH · " + id;
  }

  function resumeFileUrl(value) {
    var text = String(value || "").trim();
    var match = /\/telegram\/applications\/([0-9a-f-]{36})\/file(?:$|[?#])/i.exec(text);
    return match
      ? "/api/v1/recruitment/telegram/applications/" + match[1] + "/file"
      : text;
  }

  /* «a|b|c» → ["a","b","c"]. Пустые куски выбрасываем: строка «a||b» приходит
     от руки и означает те же два значения. */
  function splitValues(value) {
    return String(value == null ? "" : value).split("|").map(function (part) {
      return part.trim();
    }).filter(Boolean);
  }

  function valueList(key) {
    var value = (state.editor.criteria[key] || {}).value;
    return Array.isArray(value) ? value : splitValues(value);
  }

  function byId(id) { return document.getElementById(id); }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
  }

  function showError(message) {
    var host = byId("recError");
    host.textContent = message || "";
    host.style.display = message ? "" : "none";
  }

  /* Подтверждение в стиле CRM, а не окном браузера: оно рисуется у верхней
     кромки, подписано адресом сервера и выглядит как сообщение постороннего
     сайта. Контракт тот же, только ответ приходит промисом. */
  function askConfirm(options) {
    if (window.CelestialShell && window.CelestialShell.confirm) {
      return window.CelestialShell.confirm(options);
    }
    return Promise.resolve(window.confirm(options.message || options.title));
  }

  function notify(message) {
    if (window.CelestialLoading && window.CelestialLoading.notify) {
      window.CelestialLoading.notify({ title: message });
    }
  }

  function experienceLabel(months) {
    var value = Number(months || 0);
    if (!value) return "—";
    if (value < 12) return value + " мес.";
    var years = Math.floor(value / 12);
    var rest = value % 12;
    return years + " г." + (rest ? " " + rest + " мес." : "");
  }

  function moneyLabel(value) {
    var amount = Number(value || 0);
    return amount ? amount.toLocaleString("ru-RU") : "—";
  }

  function dateLabel(value) {
    if (!value) return "—";
    var parsed = new Date(value);
    return isNaN(parsed.getTime()) ? String(value)
      : window.CelestialTime.format(parsed, { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  }

  /* ---------- загрузка ---------- */

  async function loadTemplates() {
    if (state.templatesLoading) return;
    state.templatesLoading = true;
    try {
      state.templates = await api.get("/recruitment/search-templates");
      state.stamps.templates = Date.now();
      writeCache("templates", state.templates);
    } finally {
      state.templatesLoading = false;
    }
  }

  /* Обновление, при котором на экране ничего не пропадает: список остаётся
     прежним, пока не придёт новый. Полоса «Загружаем…» уместна, только когда
     показывать пока нечего, — а при переходе между вкладками она стирала уже
     прочитанное и раздражала больше, чем помогала. */
  function quietly(work) {
    // Никакой метки «обновляем…»: ни фоновое обновление, ни кнопка «Обновить»
    // её не показывают — она мигала и только мешала. Экран перерисовывается,
    // когда пришли новые данные.
    state.refreshing += 1;
    return work().catch(function () { /* тихое обновление молчит и об ошибке */ })
      .then(function () {
        state.refreshing -= 1;
        render();
      });
  }

  async function loadRuns(templateId) {
    var runs = await api.get("/recruitment/search-runs?search_template_id=" +
      encodeURIComponent(templateId));
    state.runs[templateId] = runs || [];
    return state.runs[templateId];
  }

  async function loadPending(quiet) {
    if (!quiet) {
      state.pendingLoading = true;
      render();
    }
    var params = ["review_status=pending", "limit=200", "source=hh", "via=search"];
    if (state.pendingTemplate) params.push("search_template_id=" + state.pendingTemplate);
    try {
      state.pending = await api.get("/recruitment/candidates?" + params.join("&"));
      state.stamps.pending = Date.now();
      writeCache("pending", state.pending);
      state.pendingLoading = false;
    } catch (error) {
      state.pendingLoading = false;
      if (!quiet && !state.stamps.pending) state.pending = [];
      if (!quiet) showError(error.message);
    }
    render();
  }

  /* Входящие отклики запрашиваются пофамильно по источникам, а не «всё
     неразобранное»: иначе в них попадают резюме, найденные поиском. */
  async function allIncoming(source) {
    var rows = [], batch;
    do {
      batch = await api.get("/recruitment/candidates?review_status=pending&limit=200&source=" + source + "&offset=" + rows.length);
      rows = rows.concat(batch || []);
    } while (batch && batch.length === 200);
    return rows;
  }

  async function loadVacancies() {
    try {
      var result = await api.get("/recruitment/hh/vacancies");
      state.vacancies = result.items || [];
      render();
    } catch (error) { showError(error.message); }
  }

  function areaSuggestionsHtml() {
    if (state.areaLoading) {
      return '<div class="rec-area__state">Ищем города…</div>';
    }
    if (!state.areaQuery.trim()) return "";
    if (state.areaQuery.trim().length < 2) {
      return '<div class="rec-area__state">Введите хотя бы 2 буквы</div>';
    }
    if (!state.areaSuggestions.length) {
      return '<div class="rec-area__state">Ничего не найдено</div>';
    }
    return state.areaSuggestions.map(function (area) {
      var label = area.name + (area.parent ? " (" + area.parent + ")" : "");
      return '<button type="button" class="rec-area__option" data-rec-area-pick ' +
        'data-rec-area-id="' + escapeHtml(area.id) + '" data-rec-area-name="' +
        escapeHtml(area.name) + '" data-rec-area-parent="' +
        escapeHtml(area.parent || "") + '">' + escapeHtml(label) + "</button>";
    }).join("");
  }

  function paintAreaSuggestions() {
    var menu = document.querySelector("[data-rec-area-menu]");
    if (!menu) return;
    menu.innerHTML = areaSuggestionsHtml();
    menu.classList.toggle(
      "is-open", !!(state.areaQuery.trim() || state.areaLoading)
    );
    var input = document.querySelector("[data-rec-area-input]");
    if (input) {
      input.setAttribute("aria-expanded", menu.classList.contains("is-open"));
    }
  }

  async function loadAreaSuggestions(query) {
    var request = ++state.areaRequest;
    state.areaLoading = true;
    paintAreaSuggestions();
    try {
      var result = await api.get(
        "/recruitment/hh/areas?query=" + encodeURIComponent(query) + "&limit=20"
      );
      if (request !== state.areaRequest) return;
      state.areaSuggestions = (result && result.items) || [];
      state.areaLoading = false;
      paintAreaSuggestions();
    } catch (error) {
      if (request !== state.areaRequest) return;
      state.areaSuggestions = [];
      state.areaLoading = false;
      paintAreaSuggestions();
      showError(error.message);
    }
  }

  async function loadIncoming(quiet) {
    if (state.incomingLoading) return;
    state.incomingLoading = true;
    if (!quiet) {
      render();
    }
    try {
      /* У HH две ветки, и обе приходят как source=hh: активный поиск по базе
         резюме и входящие отклики на размещённую вакансию. В «Отклики» берём
         только вторую — иначе экран дублировал бы находки поиска. */
      var batches = await Promise.all([
        allIncoming("telegram"),
        allIncoming("hh&via=negotiation")
      ]);
      var rows = [];
      batches.forEach(function (batch) {
        (batch || []).forEach(function (row) { rows.push(row); });
      });
      state.incoming = rows;
      state.stamps.incoming = Date.now();
      writeCache("incoming", state.incoming);
      state.incomingLoading = false;
    } catch (error) {
      state.incomingLoading = false;
      if (!quiet && !state.stamps.incoming) state.incoming = [];
      if (!quiet) showError(error.message);
    }
    render();
  }

  async function loadBoard(quiet) {
    if (state.boardLoading) return;
    state.boardLoading = true;
    if (!quiet) {
      render();
    }
    try {
      var payload = await api.get("/recruitment/pipeline");
      state.board = payload;
      state.boardServiceDown = payload.service_available === false;
      state.stamps.board = Date.now();
      writeCache("board", state.board);
      state.boardLoading = false;
    } catch (error) {
      state.boardLoading = false;
      if (!quiet && !state.stamps.board) state.board = null;
      if (!quiet) showError(error.message);
    }
    if (!state.users.length) {
      try {
        state.users = await api.get("/users/options");
        writeCache("users", state.users);
      } catch (error) {
        state.users = [];
      }
    }
    render();
  }

  async function patchBoardRow(rowId, patch) {
    try {
      await api.patch("/recruitment/pipeline/" + rowId, patch);
      await loadBoard();
      syncOpenCard(rowId);
    } catch (error) {
      showError(error.message);
    }
  }

  /* Раскрытая карточка держит копию строки: после перезагрузки доски её нужно
     подменить свежей, иначе форма покажет значения до сохранения. */
  function syncOpenCard(rowId) {
    if (!state.boardCard || state.boardCard.id !== rowId) return;
    var fresh = ((state.board || {}).items || []).filter(function (item) {
      return item.id === rowId;
    })[0];
    state.boardCard = fresh || null;
    render();
  }

  async function postRecord(rowId, file) {
    var form = new FormData();
    form.append("file", file);
    // Через fetch, а не через api: тот шлёт JSON, а здесь multipart.
    var response = await fetch("/api/v1/recruitment/pipeline/" + rowId + "/record", {
      method: "POST", body: form, credentials: "same-origin"
    });
    if (!response.ok) {
      var detail = "";
      try { detail = (await response.json()).detail || ""; } catch (error) { detail = ""; }
      throw new Error(detail || "Не удалось загрузить запись");
    }
  }

  async function uploadRecord(rowId, file) {
    try {
      await postRecord(rowId, file);
      await loadBoard();
      syncOpenCard(rowId);
    } catch (error) {
      showError(error.message);
    }
  }

  async function removeBoardRow(rowId) {
    var confirmed = await askConfirm({
      title: "Убрать кандидата с доски?",
      message: "Карточка уйдёт с доски найма. Сам кандидат и его резюме останутся.",
      confirmLabel: "Убрать",
      danger: true
    });
    if (!confirmed) return;
    try {
      await api.delete("/recruitment/pipeline/" + rowId);
      state.boardCard = null;
      await loadBoard();
    } catch (error) {
      showError(error.message);
    }
  }

  async function loadHh() {
    // Флаг нужен: статус тянется на любой вкладке, и без него каждая смена
    // вкладки слала бы новый запрос, пока летит предыдущий.
    state.hhLoading = true;
    try {
      state.hh = await api.get("/recruitment/hh/status");
      writeCache("hh", state.hh);
    } catch (error) {
      state.hh = { connected: false, error: error.message };
    }
    state.hhLoading = false;
    render();
  }

  /* ---------- запуск поиска и поллинг ---------- */

  async function runSearch(templateId) {
    showError("");
    try {
      var started = await api.post("/recruitment/search-templates/" + templateId + "/run", {});
      var runId = started.search_run_id;
      state.runs[templateId] = state.runs[templateId] || [];
      state.runs[templateId].unshift({ id: runId, status: "queued", trigger: "manual" });
      render();
      pollRun(templateId, runId, 0);
    } catch (error) {
      showError(error.message);
    }
  }

  function pollRun(templateId, runId, attempt) {
    if (state.runTimer) clearTimeout(state.runTimer);
    if (attempt > 100) return;
    state.runTimer = setTimeout(async function () {
      try {
        var run = await api.get("/recruitment/search-runs/" + runId);
        var list = state.runs[templateId] || [];
        var index = list.findIndex(function (row) { return row.id === runId; });
        if (index >= 0) list[index] = run;
        render();
        if (run.status === "queued" || run.status === "running") {
          pollRun(templateId, runId, attempt + 1);
          return;
        }
        if (run.status === "completed") {
          notify("Поиск завершён: найдено " + ((run.stats || {}).found || 0) +
            ", новых " + ((run.stats || {}).new || 0));
          quietly(function () { return loadPending(true); });
        } else if (run.status === "failed") {
          showError("Поиск упал: " + (run.error_message || "причина неизвестна"));
        }
      } catch (error) {
        showError(error.message);
      }
    }, 3000);
  }

  /* ---------- шаблон: редактор ---------- */

  function emptyEditor(template) {
    template = template || {};
    var criteria = {};
    var legacyGeo = "";
    (template.criteria || []).forEach(function (row) {
      // Старый `geo` был обычным текстом. Его нельзя отправить как area ID:
      // оставляем название в поле поиска, чтобы HR выбрала точный город.
      var key = row.key === "geo" ? "geo_area_id" : row.key;
      var item = criterionByKey(key);
      if (row.key === "geo") legacyGeo = row.value || "";
      criteria[key] = {
        mode: row.mode,
        value: row.key === "geo" ? []
          : item && isMulti(item) ? splitValues(row.value) : row.value,
        weight: row.weight || 0
      };
    });
    var keywords = (template.criteria || []).filter(function (row) {
      return row.key !== "geo" && CRITERIA.every(function (item) {
        return item.key !== row.key;
      });
    }).map(function (row) {
      return { key: row.key, value: row.value, mode: row.mode, weight: row.weight || 0 };
    });
    state.areaQuery = legacyGeo;
    state.areaSuggestions = [];
    state.areaLoading = false;
    var type = template.template_type || (state.tab === "responses" ? "responses" : "search");
    return {
      templateType: type,
      /* Шаблон откликов бывает двух видов: к вакансии HH (сервис ходит за
         откликами сам) и к коду вакансии, который присылает Telegram-бот. Вид
         определяется тем, чем шаблон привязан, а отдельного типа у сервиса
         нет — по коду он сопоставляет отклики любого шаблона. */
      telegram: type === "responses" &&
        (template.telegram === true ||
          (!!template.id && !template.hh_vacancy_id)),
      id: template.id || null,
      name: template.name || "",
      vacancy: template.crm_vacancy_id || "",
      hhVacancy: template.hh_vacancy_id || "",
      active: template.is_active !== false,
      auto: !!template.auto_search_enabled,
      interval: template.interval_minutes || 60,
      criteria: criteria,
      keywords: keywords
    };
  }

  function editorValue(key) {
    return (state.editor.criteria[key] || {}).value;
  }

  function editorMode(key) {
    return (state.editor.criteria[key] || {}).mode || "ignore";
  }

  function editorWeight(key) {
    return (state.editor.criteria[key] || {}).weight || 40;
  }

  /* Значение критерия-тегов в его список. Тем же путём идут Enter, запятая и
     сохранение формы: правило одно — набранное не должно теряться.

     Первое значение включает критерий: набрать «Media Buyer» и оставить
     переключатель на «Не учитывать» — значит не искать вовсе, а человек
     набирал его не для этого. */
  function addTagValue(key, text) {
    var value = String(text || "").trim();
    if (!value) return false;
    var list = valueList(key);
    var added = false;
    splitValues(value.replace(/,/g, "|")).forEach(function (part) {
      if (list.indexOf(part) < 0) {
        list.push(part);
        added = true;
      }
    });
    setCriterion(key, { value: list });
    if (list.length && editorMode(key) === "ignore") {
      setCriterion(key, { mode: "required" });
    }
    return added;
  }

  /* Набранное, но не подтверждённое Enter'ом — тоже ответ формы. Перед
     сохранением дособираем такие поля: человек написал «Media Buyer», нажал
     «Сохранить», и шаблон уходил без единственного критерия. */
  function flushPendingValues() {
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-rec-tag-add]"),
      function (input) {
        if (addTagValue(input.getAttribute("data-rec-tag-add"), input.value)) {
          input.value = "";
        }
      }
    );
    var keyword = document.querySelector("[data-rec-keyword-input]");
    if (keyword && addKeyword(keyword.value)) keyword.value = "";
  }

  function setCriterion(key, patch) {
    var row = state.editor.criteria[key] || { mode: "ignore", value: "", weight: 40 };
    state.editor.criteria[key] = Object.assign(row, patch);
  }

  function editorPayload() {
    var editor = state.editor;
    var criteria = [];
    CRITERIA.forEach(function (item) {
      var row = editor.criteria[item.key];
      if (!row || row.mode === "ignore") return;
      var value = row.value;
      if (item.type === "language") {
        value = row.value ? row.value + (row.level ? ":" + row.level : "") : "";
      }
      if (isMulti(item)) {
        // Сервису уходит одна строка: «Media Buyer|Traffic Manager».
        value = (Array.isArray(value) ? value : splitValues(value)).join("|");
      }
      if (value === "" || value == null) return;
      criteria.push({
        key: item.key,
        value: item.type === "number" ? Number(value) : String(value),
        mode: row.mode,
        weight: row.mode === "preferred" ? Number(row.weight) || 0 : 0
      });
    });
    editor.keywords.forEach(function (row) {
      if (row.mode === "ignore" || !String(row.value || "").trim()) return;
      criteria.push({
        key: row.key || "keyword",
        value: String(row.value).trim(),
        mode: row.mode,
        weight: row.mode === "preferred" ? Number(row.weight) || 0 : 0
      });
    });
    return {
      template_type: editor.templateType,
      name: String(editor.name || "").trim(),
      crm_vacancy_id: String(editor.vacancy || "").trim() || null,
      hh_vacancy_id: editor.templateType === "responses" && !editor.telegram
        ? String(editor.hhVacancy || "").trim() || null
        : null,
      is_active: editor.active,
      auto_search_enabled: editor.templateType === "search" && editor.auto,
      interval_minutes: editor.templateType === "search" && editor.auto ? Number(editor.interval) : null,
      criteria: criteria
    };
  }

  /* Что не так с формой — в самом окне, а не на странице под ним. */
  function editorProblem(message) {
    state.editorError = message;
    render();
  }

  async function saveEditor() {
    flushPendingValues();
    var payload = editorPayload();
    if (!payload.name) return editorProblem("Укажите название шаблона");
    if (payload.template_type === "responses" && state.editor.telegram) {
      if (!payload.crm_vacancy_id) {
        return editorProblem("Укажите код вакансии — по нему бот привяжет отклик к шаблону");
      }
      if (!payload.criteria.length) {
        return editorProblem("Добавьте хотя бы один критерий — иначе оценивать нечем");
      }
    } else if (payload.template_type === "responses" && !payload.hh_vacancy_id) {
      return editorProblem("Выберите вакансию HH");
    }
    if (payload.template_type === "search" && !payload.criteria.length) {
      return editorProblem("Добавьте хотя бы один критерий поиска");
    }
    if (payload.auto_search_enabled && !INTERVALS.some(function (row) {
      return row[0] === payload.interval_minutes;
    })) return editorProblem("Интервал автопоиска: 15м/30м/1ч/2ч/6ч/12ч/24ч");
    try {
      if (state.editor.id) {
        await api.put("/recruitment/search-templates/" + state.editor.id, payload);
      } else {
        await api.post("/recruitment/search-templates", payload);
      }
      state.editor = null;
      state.editorError = "";
      showError("");
      await loadTemplates();
      render();
    } catch (error) {
      editorProblem(error.message);
    }
  }

  async function deleteTemplate(templateId) {
    var confirmed = await askConfirm({
      title: "Удалить шаблон?",
      message: "История запусков останется в сервисе рекрутинга.",
      confirmLabel: "Удалить",
      danger: true
    });
    if (!confirmed) return;
    try {
      await api.delete("/recruitment/search-templates/" + templateId);
      if (state.selectedTemplateId === templateId) state.selectedTemplateId = "";
      await loadTemplates();
      render();
    } catch (error) {
      showError(error.message);
    }
  }

  /* Заведение кандидата руками. Ставим его в начало воронки и открываем его
     же карточку: почти всегда следом дописывают детали. */
  async function saveNewCandidate() {
    var form = state.newCandidate || {};
    // Ошибка показывается в самом окне: на странице под ним её закрывает
    // подложка, и человек видит только то, что «Завести» ничего не делает.
    if (
      !String(form.position_title || "").trim() &&
      !String(form.telegram_contact || "").trim()
    ) {
      state.newCandidateError = "Укажите позицию или телеграм";
      return render();
    }
    state.newCandidateError = "";
    state.busy = true;
    render();
    var file = state.newCandidateRecord;
    var payload = Object.assign({}, form);
    // Выбран файл — ссылка на запись будет на него, текстовое поле не шлём.
    if (file) delete payload.interview_record;
    try {
      var row = await api.post("/recruitment/pipeline", payload);
      state.newCandidate = null;
      state.newCandidateRecord = null;
      var recordError = "";
      if (file) {
        // Кандидат уже заведён: если файл не дошёл, окно не держим открытым —
        // повторное «Завести» создало бы дубль. Запись догружают в карточке.
        try {
          await postRecord(row.id, file);
        } catch (error) {
          recordError = error.message;
        }
      }
      state.busy = false;
      await loadBoard(true);
      state.boardCard = (state.board && state.board.items || []).filter(function (item) {
        return item.id === row.id;
      })[0] || null;
      if (recordError) {
        showError("Кандидат заведён, но запись не загрузилась: " + recordError);
      } else {
        notify("Кандидат заведён — он на этапе «Скрининг»");
      }
      render();
    } catch (error) {
      state.busy = false;
      state.newCandidateError = error.message;
      render();
    }
  }

  /* ---------- триаж ---------- */

  async function reviewCandidate(candidateId, decision) {
    try {
      await api.patch("/recruitment/candidates/" + candidateId + "/review",
        { decision: decision });
      // Разобранная находка уходит из обоих пулов неразобранного.
      state.pending = state.pending.filter(function (row) { return row.id !== candidateId; });
      state.incoming = state.incoming.filter(function (row) { return row.id !== candidateId; });
      writeCache("pending", state.pending);
      writeCache("incoming", state.incoming);
      if (decision === "added") {
        notify("Кандидат добавлен — он на этапе «Скрининг» во вкладке «Кандидаты»");
        // Доска устарела: перечитаем её при следующем открытии вкладки.
        state.board = null;
        state.stamps.board = 0;
        dropCache("board");
        if (state.tab === "candidates") await loadBoard();
      }
      render();
    } catch (error) {
      showError(error.message);
    }
  }

  /* ---------- рендер ---------- */

  function render() {
    // Скрипт инжектится во все страницы CRM: на чужих делать нечего.
    var body = byId("recBody");
    if (!body) return;
    // Перерисовка заменяет DOM целиком, и модальный лист сбрасывается в
    // начало — пользователь, нажавший кнопку внизу длинной формы, оказывался
    // наверху. Позицию скролла листа запоминаем и возвращаем на место.
    var openSheet = body.querySelector(".rec-sheet");
    var openCard = openSheet ? openSheet.querySelector(".rec-sheet__card") : null;
    var sheetScroll = openSheet ? openSheet.scrollTop : 0;
    var cardScroll = openCard ? openCard.scrollTop : 0;
    // Снимаем фактическое состояние перед заменой DOM. Так раскрытый разбор
    // не схлопывается ни от фонового опроса, ни от ручного обновления.
    body.querySelectorAll(".rec-bd[data-rec-breakdown]").forEach(function (details) {
      var candidateId = details.getAttribute("data-rec-breakdown");
      if (details.open) state.openBreakdowns[candidateId] = true;
      else delete state.openBreakdowns[candidateId];
    });
    document.querySelectorAll("[data-rec-tab]").forEach(function (button) {
      button.classList.toggle("rec-tab--active", button.getAttribute("data-rec-tab") === state.tab);
    });
    persistView();
    if (state.tab === "search") body.innerHTML = renderSearch();
    if (state.tab === "responses") body.innerHTML = renderResponses();
    if (state.tab === "candidates") body.innerHTML = renderCandidates();
    var freshSheet = body.querySelector(".rec-sheet");
    if (freshSheet) {
      var freshCard = freshSheet.querySelector(".rec-sheet__card");
      if (sheetScroll) freshSheet.scrollTop = sheetScroll;
      if (freshCard && cardScroll) freshCard.scrollTop = cardScroll;
    }
    renderHhButton();
  }

  function renderSearch() {
    var searchTemplates = state.templates.filter(function (template) { return template.template_type !== "responses"; });
    var cards = searchTemplates.map(function (template) {
      return templateCard(template);
    }).join("");
    var selected = state.templates.filter(function (row) {
      return row.id === state.selectedTemplateId;
    })[0];
    var detail = selected ? templateDetail(selected) : "";
    /* Кнопка создания стоит плиткой в ряду шаблонов, а не в шапке: новый шаблон
       заводят там же, где смотрят существующие, и глазу не нужно прыгать в
       противоположный угол экрана. */
    var addTile = '<button class="rec-tpl-add" type="button" data-rec-new-template ' +
      'aria-label="Новый шаблон"><span class="rec-tpl-add__plus">+</span>' +
      "<span>Новый шаблон</span></button>";
    return '<div style="display:flex;align-items:center;justify-content:space-between;' +
      'gap:12px;flex-wrap:wrap;margin-bottom:14px">' +
      '<div style="font-size:12.5px;color:#857D7D;font-weight:700">Шаблоны поиска: ' +
      searchTemplates.length + "</div></div>" +
      '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));' +
      'gap:12px">' + cards + addTile + "</div>" +
      detail + (state.editor ? editorModal() : "");
  }

  var ICONS = {
    vacancy: '<path d="M4 8h16v11H4zM9 8V6a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2" stroke="currentColor" ' +
      'stroke-width="1.7" stroke-linejoin="round" fill="none"/>',
    criteria: '<path d="M4 7h16M7 12h10M10 17h4" stroke="currentColor" stroke-width="1.7" ' +
      'stroke-linecap="round" fill="none"/>',
    pencil: '<path d="m4 20 .8-3.4L15.6 5.8a1.7 1.7 0 0 1 2.4 0l1.2 1.2a1.7 1.7 0 0 1 0 2.4' +
      'L8.4 20.2 5 21z" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round" fill="none"/>',
    trash: '<path d="M5 7h14M10 7V5h4v2m-7 0 1 13h8l1-13" stroke="currentColor" ' +
      'stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" fill="none"/>',
    birth: '<path d="M4 20h16v-7H4zM8 13V9m4 4V8m4 5V9M8 6.5a1.2 1.2 0 1 0 0-.1M12 5.5a1.2 ' +
      '1.2 0 1 0 0-.1M16 6.5a1.2 1.2 0 1 0 0-.1" stroke="currentColor" stroke-width="1.6" ' +
      'stroke-linecap="round" stroke-linejoin="round" fill="none"/>',
    place: '<path d="M12 21s6-5.3 6-10a6 6 0 1 0-12 0c0 4.7 6 10 6 10z" stroke="currentColor" ' +
      'stroke-width="1.6" stroke-linejoin="round" fill="none"/><circle cx="12" cy="11" r="2.2" ' +
      'stroke="currentColor" stroke-width="1.6" fill="none"/>',
    doc: '<path d="M7 3h7l4 4v14H7zM14 3v4h4" stroke="currentColor" stroke-width="1.6" ' +
      'stroke-linejoin="round" fill="none"/>',
    send: '<path d="M21 4 3 11l7 3 3 7z" stroke="currentColor" stroke-width="1.6" ' +
      'stroke-linejoin="round" fill="none"/><path d="m10 14 4-4" stroke="currentColor" ' +
      'stroke-width="1.6" stroke-linecap="round" fill="none"/>',
    external: '<path d="M14 4h6v6M20 4l-8 8M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 ' +
      '1 1-1h5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" ' +
      'stroke-linejoin="round" fill="none"/>',
    clock: '<circle cx="12" cy="12" r="8" stroke="currentColor" stroke-width="1.6" ' +
      'fill="none"/><path d="M12 8v4.4l2.8 1.6" stroke="currentColor" stroke-width="1.6" ' +
      'stroke-linecap="round" fill="none"/>',
    money: '<path d="M12 5v14M9.2 8.4h4.4a2 2 0 0 1 0 4h-3.2a2 2 0 0 0 0 4h4.4" ' +
      'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" fill="none"/>'
  };

  function icon(name, size) {
    return '<svg width="' + (size || 15) + '" height="' + (size || 15) +
      '" viewBox="0 0 24 24" aria-hidden="true">' + ICONS[name] + "</svg>";
  }

  /* «1 критерий», «2 критерия», «5 критериев» — число всегда на виду, и
     несогласованное окончание в карточке заметно сразу. */
  function criteriaLabel(count) {
    var tail = count % 100;
    if (tail < 11 || tail > 14) {
      tail = count % 10;
      if (tail === 1) return count + " критерий";
      if (tail >= 2 && tail <= 4) return count + " критерия";
    }
    return count + " критериев";
  }

  function templateCard(template) {
    var active = template.id === state.selectedTemplateId;
    var lastRun = (state.runs[template.id] || [])[0];
    var auto = template.auto_search_enabled;
    return '<div class="rec-tpl' + (active ? " is-active" : "") +
      '" data-rec-open-template="' + escapeHtml(template.id) + '">' +
      '<div class="rec-tpl__head">' +
      '<span class="rec-tpl__name">' + escapeHtml(template.name) + "</span>" +
      '<span class="rec-tpl__mode' + (auto ? " is-auto" : "") + '">' +
      (auto ? "Автопоиск · " + intervalLabel(template.interval_minutes) : "Вручную") +
      "</span></div>" +
      '<div class="rec-tpl__row">' + icon("vacancy") + "<span>" +
      (template.crm_vacancy_id
        ? "Вакансия: " + escapeHtml(template.crm_vacancy_id)
        : "Вакансия не привязана") + "</span></div>" +
      '<div class="rec-tpl__row">' + icon("criteria") + "<span>" +
      criteriaLabel((template.criteria || []).length) +
      (template.hh_vacancy_id
        ? " · отклики с hh.ru"
        : "") + "</span></div>" +
      '<div class="rec-tpl__row">' +
      '<span class="rec-tpl__dot' + (lastRun ? " is-" + lastRun.status : "") + '"></span>' +
      "<span>" + (lastRun
        ? runStatusLabel(lastRun.status) + " · " + escapeHtml(dateLabel(lastRun.created_at))
        : "Ещё не запускался") + "</span></div>" +
      '<div class="rec-tpl__foot">' +
      '<button class="rec-tpl__run" type="button" data-rec-run="' + escapeHtml(template.id) +
      '"><svg width="13" height="13" viewBox="0 0 24 24" aria-hidden="true">' +
      '<path d="M7 5l12 7-12 7z" fill="currentColor"/></svg>Запустить</button>' +
      '<button class="rec-tpl__icon" type="button" title="Редактировать" ' +
      'aria-label="Редактировать" data-rec-edit="' + escapeHtml(template.id) + '">' +
      icon("pencil", 16) + "</button>" +
      '<button class="rec-tpl__icon rec-tpl__icon--danger" type="button" title="Удалить" ' +
      'aria-label="Удалить" data-rec-delete="' + escapeHtml(template.id) + '">' +
      icon("trash", 16) + "</button></div></div>";
  }

  function templateDetail(template) {
    var runs = state.runs[template.id] || [];
    var pending = state.pending.filter(function (row) {
      return !state.pendingTemplate || row.scores.some(function (score) {
        return score.search_template_id === state.selectedTemplateId;
      });
    }).sort(byMatch);
    return '<div class="rec-card" style="margin-top:16px">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;' +
      'flex-wrap:wrap"><div style="font-size:15px;font-weight:700">' +
      escapeHtml(template.name) + ": неразобранные находки</div>" +
      '<div style="flex:1"></div>' +
      '<button class="meta-action" type="button" data-rec-refresh-pending>Обновить</button>' +
      "</div>" +
      '<div style="margin-top:12px">' +
      (pending.length
        ? pending.map(candidateCard).join('<div style="height:12px"></div>')
        : '<div class="rec-empty">Пока никого. Запустите поиск — находки появятся здесь ' +
          "после завершения прогона.</div>") + "</div>" +
      '<div style="margin-top:18px"><div style="font-size:12.5px;color:#857D7D;font-weight:700;' +
      'margin-bottom:10px">История запусков</div>' + runsTable(runs) + "</div></div>";
  }

  /* Числовые колонки выравниваются по правому краю вместе с заголовками:
     заголовок слева, а число справа — и глазу не за что зацепиться, колонки
     выглядят разъехавшимися. */
  var RUN_COLUMNS = [
    ["Статус", false], ["Триггер", false], ["Начат", false], ["Завершён", false],
    ["Найдено", true], ["Новых", true], ["Прошли фильтры", true], ["Выше порога", true]
  ];

  function runsTable(runs) {
    if (!runs.length) return '<div class="rec-empty">Запусков ещё не было.</div>';
    var head = RUN_COLUMNS.map(function (column) {
      return '<th class="rec-runs__th' + (column[1] ? " rec-runs__th--num" : "") + '">' +
        column[0] + "</th>";
    }).join("");
    var rows = runs.map(function (run) {
      var stats = run.stats || {};
      var cells = [
        runStatusLabel(run.status),
        run.trigger === "scheduled" ? "Автопоиск" : "Вручную",
        escapeHtml(dateLabel(run.started_at)),
        escapeHtml(dateLabel(run.finished_at)),
        stats.found, stats["new"], stats.passed_hard_filters, stats.above_threshold
      ];
      return "<tr>" + cells.map(function (value, index) {
        var cls = "rec-runs__cell";
        if (index >= 4) cls += " rec-runs__cell--num";
        if (value == null) {
          value = "—";
          cls += " rec-runs__cell--empty";
        }
        return '<td class="' + cls + '">' + value + "</td>";
      }).join("") + "</tr>";
    }).join("");
    return '<div class="rec-runs"><table><thead><tr>' + head +
      "</tr></thead><tbody>" + rows + "</tbody></table></div>";
  }

  function runStatusLabel(status) {
    return {
      queued: '<span class="rec-chip">В очереди</span>',
      running: '<span class="rec-chip" style="background:#E8F1FB;color:#2C4E77">Идёт поиск</span>',
      completed: '<span class="rec-chip" style="background:#E4F7F0;color:#0E7350">Завершён</span>',
      failed: '<span class="rec-chip" style="background:#FCF1F1;color:#B91414">Ошибка</span>'
    }[status] || escapeHtml(status || "—");
  }

  function intervalLabel(minutes) {
    var found = INTERVALS.filter(function (row) { return row[0] === Number(minutes); })[0];
    return found ? found[1] : minutes + " мин";
  }

  /* Карточка кандидата — общая для находок, откликов и базы. */
  /* Телеграм отклика: имя из профиля и ссылка на диалог. Для отклика это
     главное — по нему пишут, а не по резюме, которого у телеграмных нет. */
  function contactRow(candidate, withoutName) {
    var profile = candidate.parsed_profile || {};
    var telegram = (candidate.sources || []).filter(function (row) {
      return row.source === "telegram";
    })[0];
    var known = fullName(profile);
    if (!telegram) return withoutName || !known ? "" : '<div class="rec-contact">' +
      "<b>" + escapeHtml(known) + "</b></div>";
    var name = known && !withoutName ? escapeHtml(known) : "";
    var link = telegram && telegram.external_url
      ? '<a href="' + escapeHtml(telegram.external_url) + '" target="_blank" ' +
        'rel="noopener noreferrer">' +
        escapeHtml(telegramHandle(telegram.external_url)) + "</a>"
      : (telegram ? escapeHtml("id " + telegram.external_id) : "");
    return '<div class="rec-contact">' + (name ? "<b>" + name + "</b>" : "") +
      (name && link ? "<span>·</span>" : "") + link + "</div>";
  }

  function telegramHandle(url) {
    var match = /t\.me\/([A-Za-z0-9_]+)/.exec(String(url || ""));
    return match ? "@" + match[1] : url;
  }

  /* Текст отклика и приложенный файл. У телеграмного кандидата структурного
     резюме нет — только то, что он написал сам, и это единственное, по чему
     его вообще можно оценить глазами. */
  /* Длинное сообщение из телеграма — под кат. Отклик на пять экранов
     превращает список в ленту: сначала видно начало, целиком — по кнопке. */
  var TEXT_LIMIT = 260;

  /* Приложенный файл — карточкой над текстом: это первое, что открывают в
     отклике, и искать ссылку под простынёй сообщения незачем. Размер файла
     сервис не отдаёт, поэтому в подписи только тип и откуда он взялся. */
  function resumeFileCard(profile) {
    var file = resumeFileUrl(profile.resume_file_url || profile.resume_url);
    if (!file) return "";
    var name = String(profile.resume_file_name || "Файл резюме");
    var extension = name.indexOf(".") > 0 ? name.split(".").pop().toUpperCase() : "";
    return '<a class="rec-file" href="' + escapeHtml(file) + '" target="_blank" ' +
      'rel="noopener noreferrer">' +
      '<span class="rec-file__mark">' + icon("doc", 18) + "</span>" +
      '<span class="rec-file__body"><b>' + escapeHtml(name) + "</b><i>" +
      escapeHtml([extension, "из отклика"].filter(Boolean).join(" · ")) + "</i></span>" +
      '<span class="rec-file__go" aria-hidden="true">' +
      '<svg width="15" height="15" viewBox="0 0 24 24" fill="none">' +
      '<path d="M12 4v11m0 0 4-4m-4 4-4-4M5 19h14" stroke="currentColor" stroke-width="1.8" ' +
      'stroke-linecap="round" stroke-linejoin="round"/></svg></span></a>';
  }

  function applicationText(profile, candidateId) {
    var text = String(profile.text_blob || "").trim();
    var fileCard = resumeFileCard(profile);
    if (!text && !fileCard) return "";
    var folded = candidateId && text.length > TEXT_LIMIT &&
      state.openTexts.indexOf(candidateId) < 0;
    return '<div class="rec-apply">' + fileCard +
      (text
        ? '<div class="rec-apply__text' + (folded ? " is-folded" : "") + '">' +
          escapeHtml(text) + "</div>"
        : "") +
      (candidateId && text.length > TEXT_LIMIT
        ? '<button type="button" class="meta-action rec-apply__more" data-rec-text="' +
          escapeHtml(candidateId) + '">' +
          (folded ? "Показать сообщение полностью" : "Свернуть сообщение") + "</button>"
        : "") + "</div>";
  }



  /* Насколько кандидат отвечает тому, что задали в шаблоне.

     Сервис присылает по каждому критерию `{mode, passed, weight,
     match_fraction}`, и это единственное, по чему видно, подходит человек или
     нет. Правило простое и объяснимое: выполнены все критерии — HIGH,
     выполнена половина — MEDIUM, меньше — LOW. Частичное совпадение
     (`match_fraction`) считается дробной долей: «два навыка из четырёх» — это
     половина критерия, а не ноль.

     Обязательный критерий, который не выполнен, опускает кандидата в LOW
     независимо от остальных: он не подходит по условию, которое сами и
     назвали обязательным. */
  /* ---------- своя проверка критериев ----------

     Разбор, который присылает сервис, сравнивает значение критерия с профилем
     буквально: город уходит к нему идентификатором области HH («2»), а в
     профиле лежит название («Санкт-Петербург») — и обязательный критерий
     «Город» оказывался невыполненным у кандидата ровно из этого города. То же
     с опытом (ступень против числа месяцев) и с должностью (точное равенство
     против названия резюме).

     Поэтому соответствие CRM считает сама — по критериям шаблона и разобранному
     профилю кандидата. Там, где своих данных не хватает, остаётся вердикт
     сервиса; если и его нет, критерий помечается «нет данных» и в долю не
     входит — «не выполнен» без проверки хуже честного пробела. */

  // Ступени опыта HH в месяцах: [от, до).
  var EXPERIENCE_RANGES = {
    no_experience: [0, 12],
    "1_3_years": [12, 36],
    "3_6_years": [36, 72],
    "6_plus_years": [72, Infinity]
  };

  // Ключи, которые сравниваются с одноимённым полем профиля как есть.
  var PROFILE_FIELDS = {
    employment_type: "employment_type",
    work_format: "work_format",
    job_search_status: "job_search_status"
  };

  function normalizeText(value) {
    return String(value == null ? "" : value).toLowerCase().trim()
      .replace(/ё/g, "е").replace(/^г\.\s*/, "").replace(/\s+/g, " ");
  }

  /* Значение профиля списком: сервис отдаёт то строкой, то массивом — у
     занятости и формата работы в резюме бывает несколько вариантов. */
  function profileValues(profile, key) {
    var value = (profile || {})[key];
    if (value == null || value === "") return [];
    return (Array.isArray(value) ? value : [value])
      .map(normalizeText).filter(Boolean);
  }

  function textHaystack(profile) {
    return [
      profile.text_blob, profile.position_title,
      (profile.skills || []).join(" ")
    ].map(normalizeText).join(" ");
  }

  /* Совпал ли критерий: 1 — да, 0 — нет, null — сравнить не с чем. */
  function criterionMatch(criterion, profile) {
    var alternatives = splitValues(String(criterion.value || "")).map(normalizeText)
      .filter(Boolean);
    if (!alternatives.length) return null;
    var key = criterion.key;

    if (key === "experience_level") {
      var months = Number(profile.total_experience_months);
      if (!isFinite(months)) return null;
      return alternatives.some(function (level) {
        var range = EXPERIENCE_RANGES[level];
        return range && months >= range[0] && months < range[1];
      }) ? 1 : 0;
    }

    if (PROFILE_FIELDS[key]) {
      var values = profileValues(profile, PROFILE_FIELDS[key]);
      if (!values.length) return null;
      return alternatives.some(function (option) {
        return values.indexOf(option) >= 0;
      }) ? 1 : 0;
    }

    if (key === "geo_area_id" || key === "geo") {
      var city = normalizeText(profile.geo);
      if (!city) return null;
      // В критерии лежит ID области HH, а в профиле — её название. Название
      // берём из справочника, который заполняется при выборе города.
      var names = alternatives.map(function (value) {
        // Только настоящее название из справочника: подпись-заглушка
        // «Область HH · 2» с городом в резюме не сравнивается.
        return key === "geo" ? value : normalizeText(state.areaLabels[String(value)] || "");
      }).filter(Boolean);
      if (!names.length) return null;
      return names.some(function (label) {
        // В справочнике подпись с областью: «Казань (Республика Татарстан)».
        // Сравниваем и с ней, и с одним названием города.
        var name = label.replace(/\s*\(.*\)\s*$/, "");
        return city === name || city.indexOf(name) >= 0 || name.indexOf(city) >= 0 ||
          label.indexOf(city) >= 0;
      }) ? 1 : 0;
    }

    if (key === "position") {
      var title = normalizeText(profile.position_title);
      var text = textHaystack(profile);
      if (!title && !text) return null;
      return alternatives.some(function (option) {
        return (title && (title.indexOf(option) >= 0 || option.indexOf(title) >= 0)) ||
          (text && text.indexOf(option) >= 0);
      }) ? 1 : 0;
    }

    // Ключевые слова и всё незнакомое — поиск по тексту резюме.
    var haystack = textHaystack(profile);
    if (!haystack) return null;
    return alternatives.some(function (option) {
      return haystack.indexOf(option) >= 0;
    }) ? 1 : 0;
  }

  /* Критерии шаблона, по которому кандидата оценивали. */
  function templateCriteria(score) {
    var template = score ? templateOf(score) : null;
    return (template && template.criteria) || [];
  }

  /* Разбор соответствия: свои проверки поверх того, что прислал сервис. */
  function scoreBreakdown(candidate, score) {
    if (!score) return null;
    var service = score.breakdown || null;
    var criteria = templateCriteria(score).filter(function (row) {
      return row && row.key && row.mode !== "ignore";
    });
    if (!criteria.length) return service;
    var profile = (candidate || {}).parsed_profile || {};
    var breakdown = {};
    criteria.forEach(function (criterion) {
      var fraction = criterionMatch(criterion, profile);
      var fallback = service && service[criterion.key];
      if (fraction === null && fallback && typeof fallback === "object") {
        breakdown[criterion.key] = fallback;
        return;
      }
      breakdown[criterion.key] = {
        mode: criterion.mode,
        weight: Number(criterion.weight) || 0,
        passed: fraction === null ? null : fraction === 1,
        match_fraction: fraction
      };
    });
    return breakdown;
  }

  /* Всё о соответствии одного кандидата — считается один раз на карточку. */
  function scoreView(candidate) {
    var score = bestScore(candidate);
    if (!score) return null;
    var breakdown = scoreBreakdown(candidate, score);
    return {
      score: score,
      breakdown: breakdown,
      share: matchShare(breakdown),
      tier: matchTier(breakdown, score)
    };
  }

  /* Провален ли обязательный критерий — по тому же разбору, что и доля.
     Вердикт сервиса здесь не годится: он и ставил «не выполнен» городу,
     который на деле совпадает. */
  function failedRequired(breakdown) {
    if (!breakdown) return false;
    return Object.keys(breakdown).some(function (key) {
      var row = breakdown[key];
      return row && typeof row === "object" && row.mode === "required" &&
        (row.passed === false || row.match_fraction === 0);
    });
  }

  function matchShare(breakdown) {
    if (!breakdown) return null;
    var entries = Object.keys(breakdown).map(function (key) { return breakdown[key]; })
      .filter(function (value) {
        // «Нет данных» в долю не входит: критерий не проверен, и записывать его
        // в невыполненные значило бы занижать оценку за пробел в резюме.
        return value && typeof value === "object" && value.mode !== "ignore" &&
          !(value.passed == null && value.match_fraction == null);
      });
    if (!entries.length) return null;
    var failed = entries.some(function (value) {
      return value.mode === "required" &&
        (value.passed === false || value.match_fraction === 0);
    });
    if (failed) return 0;
    var sum = entries.reduce(function (total, value) {
      if (value.passed === true) return total + 1;
      if (value.passed === false) return total;
      return total + (typeof value.match_fraction === "number" ? value.match_fraction : 0);
    }, 0);
    return sum / entries.length;
  }

  function matchTier(breakdown, score) {
    var share = matchShare(breakdown);
    // Разбора нет — верим оценке сервиса: она хотя бы отражает его порог.
    if (share === null) {
      if (score && score.hard_filters_passed === false) return "low";
      return (score || {}).tier || "low";
    }
    if (share >= 0.999) return "high";
    if (share >= 0.5) return "medium";
    return "low";
  }

  /* Что писать рядом с отметкой. Доля выполненных критериев объясняет саму
     отметку, а балл сервиса — нет: «LOW · 0» у телеграмного отклика значит
     «его никто не искал», и число там ничего не добавляет. */
  function tierValue(view) {
    if (view && view.share !== null) return Math.round(view.share * 100) + " %";
    return String(((view || {}).score || {}).score ?? 0);
  }

  var NO_SCORE = { label: "БЕЗ ОЦЕНКИ", bg: "#F4F0F0", color: "#9B9292" };

  var TIER_RANK = { hot: 0, high: 1, medium: 2, low: 3 };

  /* Подходящие — выше: список читают сверху, и HIGH внизу страницы никто не
     найдёт. Внутри одной ступени порядок задаёт балл сервиса. */
  function byMatch(a, b) {
    var left = scoreView(a);
    var right = scoreView(b);
    var rank = TIER_RANK[(left || {}).tier || "low"] -
      TIER_RANK[(right || {}).tier || "low"];
    if (rank) return rank;
    return ((right && right.share) || 0) - ((left && left.share) || 0);
  }

  function templateOf(score) {
    return state.templates.find(function (row) {
      return row.id === score.search_template_id;
    });
  }

  function bestOf(scores) {
    return scores.slice().sort(function (a, b) { return b.score - a.score; })[0];
  }

  /* Оценка того шаблона, в списке которого кандидат сейчас показан.

     На вкладке откликов сначала ищем оценку шаблона откликов — это она и
     объясняет, почему человек здесь. Если её нет (в телеграм-отклике не указан
     код вакансии, или человека сначала нашли поиском), берём любую другую:
     оценка по шаблону поиска — тоже оценка, и показать её честнее, чем оставить
     карточку без разбора вовсе. */
  function bestScore(candidate) {
    var scores = candidate.scores || [];
    if (state.tab !== "responses") {
      return bestOf(scores.filter(function (score) {
        var template = templateOf(score);
        return !template || template.template_type !== "responses";
      }));
    }
    var own = scores.filter(function (score) {
      var template = templateOf(score);
      return template && template.template_type === "responses" &&
        (!state.responseTemplate || template.id === state.responseTemplate);
    });
    return bestOf(own.length ? own : scores);
  }

  /* Имя человека. Сервис нормализует резюме в `full_name`, но разные
     провайдеры кладут его по-разному, поэтому собираем из того, что пришло:
     готовое поле, части ФИО или общее `name`. */
    /* Название позиции у отклика. HH присылает её в разборе резюме; у
     телеграмного отклика сервис позиции не отдаёт вовсе — но отклик
     принадлежит вакансии выбранного шаблона, и её название честнее пустоты. */
  function candidateTitle(candidate, profile) {
    if (profile.position_title) return profile.position_title;
    // Сервис теперь прикладывает к кандидату его TG-заявку: вакансия, на
    // которую откликнулся человек, живёт в vacancy_ref.
    var application = candidate.telegram_application || {};
    if (application.vacancy_ref) return application.vacancy_ref;
    if (state.tab === "responses" && state.responseTemplate) {
      var template = state.templates.find(function (row) {
        return row.id === state.responseTemplate;
      });
      if (template) return template.name;
    }
    return "";
  }

  function fullName(profile) {
    var source = profile || {};
    var ready = String(source.full_name || source.name || "").trim();
    if (ready) return ready;
    return [source.last_name, source.first_name, source.middle_name]
      .map(function (part) { return String(part || "").trim(); })
      .filter(Boolean).join(" ");
  }

  /* Карточка находки: позиция и оценка сверху, под чертой — человек, ещё
     ниже — решение. Ссылка на резюме кнопкой рядом с именем: её открывают
     раньше, чем нажимают «Добавить». */
  function candidateCard(candidate) {
    var profile = candidate.parsed_profile || {};
    var view = scoreView(candidate);
    var score = view && view.score;
    // Без оценки «LOW · 0» врёт: кандидата никто не проверял, а отметка
    // выглядит как вердикт. Поэтому у такой карточки метка нейтральная.
    var tier = view ? (TIERS[view.tier] || TIERS.low) : NO_SCORE;
    var sourceRows = candidate.sources || [];
    /* «HH · отклик» против «HH · поиск»: обе ветки приходят одним источником, а
       разговор с человеком начинается по-разному — он написал сам или его
       нашли. */
    var sourceLabels = sourceRows.map(function (row) {
      var name = SOURCES[row.source] || row.source;
      if (row.seen_search_at && row.seen_response_at) return name + " · поиск и отклик";
      if (row.seen_response_at || row.via === "negotiation") return name + " · отклик";
      if (row.via === "search") return name + " · поиск";
      return name;
    }).join(" · ");
    var skills = (profile.skills || []).slice(0, 8).map(function (skill) {
      return '<span class="rec-chip">' + escapeHtml(skill) + "</span>";
    }).join("");
    var telegram = sourceRows.filter(function (row) { return row.source === "telegram"; })[0];
    /* Только возраст и город, и только у находки с HH: опыт и ожидания сервис
       считает по тексту резюме и врал чаще, чем помогал, а у телеграмного
       отклика этих полей нет вовсе. Всё остальное открывается на hh.ru. */
    var facts = telegram
      // Возраст, а не дата рождения: в строке читают «31 год», а точная дата
      // нужна разве что в подробной карточке.
      ? []
      : [ageLabel(profile), profile.geo].filter(Boolean);
    var name = fullName(profile);
    var handle = contactHandle(candidate);
    /* Телеграм — это ник рядом с именем, а не кнопка: «профиль» ведёт в тот же
       диалог, и отдельной строкой он только съедает место. Кнопка остаётся у
       HH: там за ней лежит резюме целиком. */
    var contact = handle
      ? (telegram && telegram.external_url
        ? '<a class="rec-scard__tg" href="' + escapeHtml(telegram.external_url) +
          '" target="_blank" rel="noopener noreferrer">' + escapeHtml(handle) + "</a>"
        : '<span class="rec-scard__tg">' + escapeHtml(handle) + "</span>")
      : "";
    var source = sourceRows.filter(function (row) {
      return row.external_url && row.source !== "telegram";
    })[0];
    var resume = source
      ? '<a class="rec-open" href="' + escapeHtml(source.external_url) + '" target="_blank" ' +
        'rel="noopener noreferrer">' + icon("doc", 15) + "Открыть резюме</a>"
      : "";
    return '<div class="rec-scard">' +
      '<div class="rec-scard__head">' +
      '<div style="min-width:0">' +
      '<div class="rec-scard__title">' +
      escapeHtml(candidateTitle(candidate, profile) || "Позиция не указана") + "</div>" +
      '<div class="rec-scard__sub">' +
      escapeHtml(sourceLabels || "—") + " · " + escapeHtml(dateLabel(candidate.first_seen_at)) +
      "</div></div>" +
      '<span class="rec-chip" style="flex-shrink:0;background:' + tier.bg + ";color:" +
      tier.color + '">' + tier.label + (view ? " · " + tierValue(view) : "") +
      "</span></div>" +
      '<div class="rec-scard__body">' +
      /* Кто это и ссылка на резюме — одной строкой: имя, возраст и город идут
         через точку одним текстом, кнопка резюме прижата к правому краю. Чипы
         с иконками на каждый факт делали из строки набор ярлыков, хотя читают
         её как одно предложение. */
      (name || facts.length || contact || resume
        ? '<div class="rec-scard__who">' +
          [
            name ? '<span class="rec-scard__name">' + escapeHtml(name) + "</span>" : "",
            contact
          ].concat(facts.map(function (fact) {
            return '<span class="rec-scard__facts">' + escapeHtml(fact) + "</span>";
          })).filter(Boolean).join('<i class="rec-scard__dot">·</i>') +
          resume + "</div>"
        : "") +
      // Текст показываем только у телеграмного отклика: там это всё резюме
      // целиком. У находки с HH в него попадает выжимка из резюме — тот же
      // список должностей, что и в заголовке, только на полкарточки.
      (telegram ? applicationText(profile, candidate.id) : "") +
      (skills ? '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:10px">' +
        skills + "</div>" : "") +
      (view && failedRequired(view.breakdown)
        ? '<div style="margin-top:10px;background:#FCF1F1;border-radius:10px;padding:8px 12px;' +
          'font-size:11.5px;color:#B91414;font-weight:600">Не прошёл обязательные критерии — ' +
          "можно добавить вручную, если кандидат всё равно интересен.</div>" : "") +
      (view
        ? breakdownHtml(view, candidate.id)
        : '<div class="rec-noscore">Оценка не считалась: отклик не привязан ' +
          "ни к одному шаблону — критерии сравнивать не с чем.</div>") + "</div>" +
      cardFooter(candidate) + "</div>";
  }

  /* Человеческое название критерия: у структурных оно есть в конструкторе, у
     ключевых слов ключ и есть название («igaming», «Keitaro»). */
  /* Ключи, которыми критерии назывались раньше. Разбор оценки приходит с теми
     ключами, с какими шаблон уходил в сервис, и у давних запусков там ещё
     старое имя — без этой таблицы в разборе стояло бы «geo» вместо «Город». */
  var LEGACY_CRITERIA = { geo: "Город", geo_area: "Город", city: "Город" };

  function criterionLabel(key) {
    var known = criterionByKey(key);
    if (known) return known.label;
    if (LEGACY_CRITERIA[key]) return LEGACY_CRITERIA[key];
    var category = KEYWORD_CATEGORIES.filter(function (row) { return row[0] === key; })[0];
    return category ? category[1] : key;
  }

  /* Разбор оценки, а не дамп JSON. Сервис отдаёт по критерию объект вида
     {mode, passed, weight, match_fraction} — читать его сырым нельзя, а
     показать нужно: по нему видно, за что кандидат недобрал баллы. */
  /* Строка критерия: слева состояние, дальше название, режим и вклад. Цвет
     несёт тот же смысл, что и подпись, — читать можно и по одному, и по
     другому: на цвет глаз попадает первым, но дальтоник его не различит. */
  function breakdownRow(key, value) {
    var name = escapeHtml(criterionLabel(key));
    if (value === null || typeof value !== "object") {
      return '<div class="rec-mrow"><span class="rec-mrow__dot"></span>' +
        '<span class="rec-mrow__name">' + name + "</span>" +
        '<span class="rec-mrow__tail">' + escapeHtml(String(value)) + "</span></div>";
    }
    var fraction = typeof value.match_fraction === "number" ? value.match_fraction : null;
    var done = value.passed === true || fraction === 1;
    var failed = value.passed === false || fraction === 0;
    // Не проверяли — так и пишем: в резюме нет поля, по которому сравнивать.
    var unknown = !done && !failed && value.passed == null && fraction === null;
    var state = done ? "is-ok" : failed ? "is-no" : "is-part";
    var mark = done ? "✓" : failed ? "✕" : unknown ? "?" : "~";
    var verdict = done ? "выполнен" : failed ? "не выполнен"
      : unknown ? "нет данных" : Math.round(fraction * 100) + " % совпадения";
    var mode = value.mode === "required" ? "обязательный"
      : value.mode === "preferred" ? "желательный" : "";
    return '<div class="rec-mrow ' + state + '">' +
      '<span class="rec-mrow__dot">' + mark + "</span>" +
      '<span class="rec-mrow__name">' + name + "</span>" +
      (mode ? '<span class="rec-mrow__mode">' + mode + "</span>" : "") +
      '<span class="rec-mrow__tail">' + escapeHtml(verdict) +
      (value.weight ? " · вес " + Number(value.weight) : "") + "</span></div>";
  }

  /* Разбор оценки: сначала полоса соответствия — по ней видно решение целиком,
     не читая критериев, — и уже под ней сами критерии, свёрнутые. Цвет полосы
     тот же, что у отметки: HIGH и «зелёная полоса» должны означать одно. */
  function breakdownHtml(view, candidateId) {
    if (!view) return "";
    // Балл без разбора: сервис оценил, но не прислал по каким критериям, а
    // шаблона под рукой нет. Полосу рисовать не по чему, а сам балл показать
    // надо — иначе непонятно, откуда взялась отметка.
    if (!view.breakdown) {
      return '<div class="rec-noscore">Балл ' +
        escapeHtml(String(view.score.score ?? 0)) +
        " · разбор по критериям сервис не прислал.</div>";
    }
    var entries = Object.keys(view.breakdown).map(function (key) {
      return [key, view.breakdown[key]];
    }).filter(function (pair) {
      return !pair[1] || typeof pair[1] !== "object" || pair[1].mode !== "ignore";
    });
    if (!entries.length) return "";
    var share = view.share;
    var tier = view.tier;
    var percent = share === null ? null : Math.round(share * 100);
    var counted = entries.filter(function (pair) {
      return pair[1] && typeof pair[1] === "object" &&
        !(pair[1].passed == null && pair[1].match_fraction == null);
    });
    var done = counted.filter(function (pair) {
      return pair[1].passed === true || pair[1].match_fraction === 1;
    }).length;
    var rows = entries.map(function (pair) {
      return breakdownRow(pair[0], pair[1]);
    }).join("");
    return '<div class="rec-match is-' + tier + '">' +
      '<div class="rec-match__head">' +
      "<span>Соответствие критериям</span>" +
      "<b>" + (percent === null ? "—" : percent + " %") + "</b></div>" +
      '<div class="rec-match__bar"><i style="width:' +
      (percent === null ? 0 : percent) + '%"></i></div>' +
      '<details class="rec-bd" data-rec-breakdown="' + escapeHtml(candidateId) + '"' +
      (state.openBreakdowns[candidateId] ? " open" : "") + '><summary>' +
      done + " из " + counted.length +
      " — разбор по критериям</summary>" +
      '<div class="rec-bd__rows">' + rows + "</div></details></div>";
  }

  /* Ссылка на резюме — до решения, а не после: HR открывает его, чтобы
     решить, добавлять человека или пропустить. */
  function resumeLink(candidate) {
    var source = (candidate.sources || []).filter(function (row) {
      return row.external_url;
    })[0];
    if (!source) return "";
    return '<a class="rec-resume" href="' + escapeHtml(source.external_url) +
      '" target="_blank" rel="noopener noreferrer">' +
      (source.source === "telegram" ? "Профиль" : "Резюме") + "</a>";
  }

  /* Решение по находке. Ссылки на резюме здесь нет: она стоит выше, рядом с
     именем — её открывают до решения, а не вместе с ним. */
  function cardFooter(candidate) {
    if (candidate.review_status === "added") {
      return '<div class="rec-scard__foot rec-scard__foot--done">Добавил: ' +
        escapeHtml(candidate.reviewed_by || "—") + " · " +
        escapeHtml(dateLabel(candidate.reviewed_at)) + "</div>";
    }
    return '<div class="rec-scard__foot">' +
      '<button class="meta-action meta-action--primary" type="button" data-rec-add="' +
      escapeHtml(candidate.id) + '">Добавить в кандидаты</button>' +
      '<button class="meta-action" type="button" data-rec-skip="' + escapeHtml(candidate.id) +
      '">Пропустить</button></div>';
  }

  /* ---------- отклики ---------- */

  /* Подключённые вакансии HH — настройка подраздела «HH», а не шапка всех
     откликов: над общим списком блок читался как заголовок раздела, хотя к
     телеграмным откликам отношения не имеет. */
  function responseTemplates() {
    var templates = state.templates.filter(function (row) {
      return row.template_type === "responses" && row.hh_vacancy_id;
    });
    /* Подключённые вакансии — отдельной карточкой над списком: это настройка
       сбора, а не отклики. Внутри общей карточки плитки вакансий читались как
       первые строки списка, и граница между «что собираем» и «что собрали»
       пропадала. */
    return '<div class="rec-card" style="margin-bottom:14px">' +
      '<div style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:14px">' +
      '<strong>Вакансии HH · ' + templates.length + '</strong>' +
      '<button class="meta-action meta-action--primary" data-rec-new-response>Подключить вакансию HH</button></div>' +
      '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px">' +
      templates.map(function (row) {
        return '<div class="rec-card"><strong>' + escapeHtml(row.name) + '</strong>' +
          '<div class="rec-note" style="margin-top:8px">' + (row.is_active ? 'Сбор включён · каждую минуту' : 'Сбор приостановлен') + '</div>' +
          '<div style="margin-top:8px;font-size:12px">Последняя успешная проверка: ' + escapeHtml(dateLabel(row.last_success_at)) +
          '<br>Кандидатов получено: ' + Number(row.candidate_count || 0) + '</div>' +
          (row.last_error ? '<div style="color:#C41616;margin-top:8px;font-size:12px">' + escapeHtml(row.last_error) + '</div>' : '') +
          '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px"><button class="meta-action" data-rec-edit="' + escapeHtml(row.id) + '">Настроить</button>' +
          '<button class="meta-action" data-rec-toggle-response="' + escapeHtml(row.id) + '">' + (row.is_active ? 'Приостановить' : 'Включить сбор') + '</button>' +
          '<button class="meta-action" data-rec-show-response="' + escapeHtml(row.id) + '">Отклики</button></div></div>';
      }).join('') + '</div>' +
      (templates.length
        ? ""
        : '<div class="rec-empty">Вакансия HH ещё не подключена.</div>') +
      "</div>";
  }

  /* Шаблоны телеграмных откликов. Сервис сопоставляет отклик из бота с
     шаблоном по коду вакансии: бот присылает код кнопки, на которую нажал
     человек, и оценка считается по критериям того шаблона, у которого код
     совпал. Без такого шаблона отклик приходит без оценки — сравнивать не с
     чем, и в карточке так и написано. */
  function telegramTemplates() {
    var templates = state.templates.filter(function (row) {
      return row.template_type === "responses" && !row.hh_vacancy_id;
    });
    return '<div class="rec-card" style="margin-bottom:14px">' +
      '<div style="display:flex;justify-content:space-between;gap:12px;flex-wrap:wrap;' +
      'margin-bottom:14px"><strong>Шаблоны Telegram · ' + templates.length + "</strong>" +
      '<button class="meta-action meta-action--primary" data-rec-new-telegram>' +
      "Новый шаблон Telegram</button></div>" +
      '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px">' +
      templates.map(function (row) {
        return '<div class="rec-card"><strong>' + escapeHtml(row.name) + "</strong>" +
          '<div class="rec-note" style="margin-top:8px">Код вакансии: ' +
          escapeHtml(row.crm_vacancy_id || "не указан") + "</div>" +
          '<div style="margin-top:8px;font-size:12px">Критериев: ' +
          Number((row.criteria || []).length) +
          "<br>Кандидатов оценено: " + Number(row.candidate_count || 0) + "</div>" +
          '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-top:12px">' +
          '<button class="meta-action" data-rec-edit="' + escapeHtml(row.id) +
          '">Настроить</button>' +
          '<button class="meta-action" data-rec-show-response="' + escapeHtml(row.id) +
          '">Отклики</button></div></div>';
      }).join("") + "</div>" +
      (templates.length
        ? ""
        : '<div class="rec-empty">Шаблона ещё нет. Заведите его с тем же кодом ' +
          "вакансии, который присылает бот, — и отклики начнут получать оценку.</div>") +
      "</div>";
  }

  /* Ник в телеграме: у отклика он приходит ссылкой на диалог, у кандидата с
     HH — разобранным полем профиля. */
  function contactHandle(candidate) {
    var profile = candidate.parsed_profile || {};
    var telegram = (candidate.sources || []).filter(function (row) {
      return row.source === "telegram";
    })[0];
    if (telegram && telegram.external_url) return telegramHandle(telegram.external_url);
    if (telegram && telegram.external_id) return "id " + telegram.external_id;
    return profile.telegram_username ? "@" + String(profile.telegram_username)
      .replace(/^@/, "") : "";
  }

  function renderResponses() {
    /* Фильтр по источнику, а не по шаблону: отклик приходит сам, шаблоном его
       никто не искал, и у телеграмных откликов оценок обычно нет вовсе. */
    var sourceFilter = [["", "Все источники"], ["telegram", "Telegram"], ["hh", "HH"]]
      .map(function (row) {
        return '<button class="rec-src' + (row[0] === state.incomingSource ? " is-on" : "") +
          '" type="button" data-rec-incoming-source="' + row[0] + '">' + row[1] + "</button>";
      }).join("");
    var rows = state.incoming;
    if (state.responseTemplate) rows = rows.filter(function (row) {
      return (row.scores || []).some(function (score) { return score.search_template_id === state.responseTemplate; });
    });
    if (state.incomingSource) {
      rows = rows.filter(function (row) {
        return (row.sources || []).some(function (item) {
          return item.source === state.incomingSource;
        });
      });
    }
    return (state.responseTemplate ? '<button class="meta-action" style="margin-bottom:12px" data-rec-show-response="">Все вакансии</button>' : "") + '<div class="rec-card" style="margin-bottom:14px">' +
      '<div style="display:flex;align-items:center;gap:10px;' +
      'flex-wrap:wrap;margin-bottom:14px">' +
      '<span style="font-size:12.5px;font-weight:700">Входящих: ' + rows.length + "</span>" +
      '<div class="rec-src-group">' + sourceFilter + "</div>" +
      '<div style="flex:1"></div>' +
      '<button class="meta-action" type="button" data-rec-refresh-incoming>Обновить</button>' +
      "</div>" +
      '<div class="rec-note">Здесь только те, кто откликнулся ' +
      "сам. Резюме, найденные шаблонами поиска, живут на вкладке «Поиск резюме» — " +
      "в откликах их быть не должно.</div></div>" +
      (state.incomingSource !== "telegram" ? responseTemplates() : "") +
      (state.incomingSource !== "hh" ? telegramTemplates() : "") +
      '<div class="rec-card" id="recIncomingList">' +
      /* «Загружаем…» уместно, только когда показывать нечего: при обновлении
         уже прочитанный список должен остаться на экране, иначе кнопка
         «Обновить» стирает то, что человек в этот момент читает. */
      (rows.length
        ? '<div class="rec-scard-list">' + rows.slice().sort(byMatch)
          .map(candidateCard).join("") + "</div>"
        : state.incomingLoading
          ? '<div class="rec-empty">Загружаем…</div>'
          : '<div class="rec-empty">Входящих откликов нет.<br>Telegram-отклики появятся ' +
            "после подключения бота к сервису рекрутинга, отклики с HH — после " +
            "подключения вакансии в подразделе «HH».</div>") +
      "</div>" + (state.editor ? editorModal() : "");
  }

  /* ---------- кандидаты ---------- */

  var STAGE_TONES = {
    screening: { bg: "#F4F0F0", color: "#6A6161" },
    interview: { bg: "#EFF5FE", color: "#2C4E77" },
    tech_interview: { bg: "#EEF7F3", color: "#0E7350" },
    offer: { bg: "#FFF2E0", color: "#8A5A12" },
    hired: { bg: "#E4F7F0", color: "#0E7350" },
    rejected: { bg: "#FCF1F1", color: "#B91414" }
  };

  /* Доска найма. Этапы ведёт CRM: сервис рекрутинга отвечает только за то,
     разобрана находка или нет, а скрининг/интервью/оффер — процесс компании. */
  function renderCandidates() {
    if (state.boardLoading && !state.board) {
      return '<div class="rec-card"><div class="rec-empty">Загружаем доску найма…</div></div>';
    }
    var board = state.board || { stages: [], items: [] };
    var query = state.boardSearch.trim().toLowerCase();
    var items = board.items.filter(function (row) {
      if (state.boardStage && row.stage !== state.boardStage) return false;
      if (!query) return true;
      return [row.position_title, row.geo, row.owner_name, row.note, row.source]
        .join(" ").toLowerCase().indexOf(query) >= 0;
    });
    var stageFilter = board.stages.map(function (stage) {
      var on = state.boardStage === stage.key;
      return '<button class="meta-action' + (on ? " meta-action--primary" : "") +
        '" type="button" data-rec-stage-filter="' + stage.key + '">' +
        escapeHtml(stage.label) + " · " + stage.count + "</button>";
    }).join("");
    var columns = board.stages.map(function (stage) {
      var cards = items.filter(function (row) { return row.stage === stage.key; });
      return '<div class="rec-col" data-rec-drop="' + escapeHtml(stage.key) +
        '"><div class="rec-col__head">' +
        '<span>' + escapeHtml(stage.label) + "</span>" +
        '<span class="rec-col__count">' + cards.length + "</span></div>" +
        '<div class="rec-col__body">' +
        (cards.length
          ? cards.map(boardCard).join("")
          : '<div class="rec-col__empty">Пусто</div>') +
        /* Кандидата заводят в начало воронки, поэтому кнопка стоит там же —
           под последней карточкой «Скрининга», а не только в шапке доски. */
        (stage.key === "screening"
          ? '<button type="button" class="rec-col__add" data-rec-new-candidate>' +
            "+ Кандидат</button>"
          : "") +
        "</div></div>";
    }).join("");
    return (state.responseTemplate ? '<button class="meta-action" style="margin-bottom:12px" data-rec-show-response="">Все вакансии</button>' : "") + '<div class="rec-card"><div style="display:flex;align-items:center;gap:10px;' +
      'flex-wrap:wrap;margin-bottom:14px">' +
      '<input class="meta-control" style="width:260px;height:34px;padding:0 11px" ' +
      'type="search" placeholder="Поиск по позиции, гео, ответственному" value="' +
      escapeHtml(state.boardSearch) + '" data-rec-board-search>' +
      (state.boardStage
        ? '<button class="meta-action" type="button" data-rec-stage-filter="">Все этапы</button>'
        : "") +
      '<div style="flex:1"></div>' +
      '<button class="meta-action meta-action--primary" type="button" data-rec-new-candidate>' +
      "Завести кандидата</button>" +
      '<button class="meta-action" type="button" data-rec-refresh-board>Обновить</button>' +
      "</div>" +
      '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px">' + stageFilter +
      "</div>" +
      (state.boardServiceDown
        ? '<div class="rec-note" style="border-color:#F1D9D9;color:#B91414;margin-bottom:14px">' +
          "Сервис рекрутинга не отвечает — доска показана по данным CRM. Новые " +
          "отобранные кандидаты появятся, когда связь восстановится.</div>"
        : "") +
      (state.boardCard ? candidateModal(state.boardCard) : "") +
      (board.items.length
        ? '<div class="rec-board">' + columns + "</div>"
        : '<div class="rec-empty">Пока никого. Отбирайте людей на вкладках «Поиск ' +
          "кандидатов» и «Отклики» кнопкой «Добавить в кандидаты» — они появятся " +
          "здесь на этапе «Скрининг». Пришедшего по рекомендации заводите " +
          "кнопкой «Завести кандидата».</div>") +
      (state.newCandidate ? newCandidateModal() : "") +
      "</div>";
  }

  /* Подключение HH — кнопка в шапке, а не вкладка: это разовая настройка, к
     которой возвращаются раз в полгода, и держать ради неё раздел в одном ряду
     с рабочими экранами значит показывать её каждый день без надобности. */
  function renderHhButton() {
    var host = byId("recHeaderHh");
    if (!host) return;
    var hh = state.hh || {};
    var connected = hh.connected === true;
    /* Пока статус не пришёл, шапка молчит о подключении: раньше она на первом
       кадре показывала «HH не подключён» с красной точкой и кнопкой, и
       подключённый аккаунт «появлялся» только после перехода на другую
       вкладку — к этому времени ответ уже приходил. */
    var unknown = !state.hh;
    var label = connected
      ? escapeHtml(hh.label || hh.account_id || "аккаунт")
      : unknown ? "Проверяем HH…" : (hh.error ? "HH недоступен" : "HH не подключён");
    host.innerHTML = '<div class="rec-hh">' +
      '<span class="rec-hh__dot" style="background:' +
      (connected ? "#0E7350" : unknown ? "#C9BEBE" : "#B91414") + '"></span>' +
      '<span class="rec-hh__label" title="' +
      (connected && hh.connected_at ? "подключён " + escapeHtml(dateLabel(hh.connected_at)) : "") +
      '">' + label + "</span>" +
      (unknown
        ? ""
        : connected
          ? '<button class="meta-action" type="button" data-rec-hh-refresh>Проверить</button>'
          : '<button class="meta-action meta-action--primary" type="button" ' +
            "data-rec-hh-connect>Подключить HH</button>") + "</div>";
  }

  /* «12.06.1994 · 31 год» — дата рождения и возраст из неё же. Возраст сервис
     не хранит: он устаревает молча, а дата — нет. */
  function birthLabel(profile) {
    var raw = String((profile || {}).birth_date || "").slice(0, 10);
    var parts = raw.split("-");
    if (parts.length !== 3) {
      var age = Number((profile || {}).age);
      return age ? age + " " + plural(age, "год", "года", "лет").split(" ")[1] : "";
    }
    var born = new Date(Number(parts[0]), Number(parts[1]) - 1, Number(parts[2]));
    var now = new Date();
    var years = now.getFullYear() - born.getFullYear();
    if (now.getMonth() < born.getMonth() ||
      (now.getMonth() === born.getMonth() && now.getDate() < born.getDate())) years -= 1;
    var text = parts[2] + "." + parts[1] + "." + parts[0];
    return years > 0 && years < 120
      ? text + " · " + plural(years, "год", "года", "лет")
      : text;
  }

  /* Только возраст, без даты: в колонке доски шириной в 240 пикселей
     «12.06.1994 · 31 год» занимает всю строку, а решает в ней возраст. */
  function ageLabel(profile) {
    var full = birthLabel(profile);
    var parts = full.split(" · ");
    return parts.length > 1 ? parts[1] : (/^\d{2}\.\d{2}\.\d{4}$/.test(full) ? "" : full);
  }

  /* Ник в телеграме: у отклика он приходит сам, у кандидата с HH его вписывают
     руками внутри карточки — и там же он потом нужен на виду. */
  function telegramNick(row) {
    var profile = row.profile || {};
    var handle = profile.telegram_username || row.telegram_contact ||
      (row.external_url && (row.source || "") === "telegram"
        ? telegramHandle(row.external_url)
        : "");
    handle = String(handle || "").trim();
    if (!handle) return "";
    return handle.indexOf("@") === 0 || handle.indexOf("id ") === 0 ? handle : "@" + handle;
  }

  /* Кто это. На доске карточку узнают по вакансии, источнику и отметке — имя
     и возраст занимали две строки в колонке шириной в палец и повторяли то,
     что видно внутри карточки. Остаётся ник телеграма: по нему пишут, не
     открывая карточку. В подробной карточке места больше — там и имя, и дата
     рождения, и город. */
  function identityLine(row, compact) {
    var profile = row.profile || {};
    var nick = telegramNick(row);
    if (compact || (row.source || "") === "telegram") {
      return nick ? '<span class="rec-cand__who">' + escapeHtml(nick) + "</span>" : "";
    }
    var parts = [fullName(profile), birthLabel(profile), row.geo].filter(Boolean);
    return parts.length
      ? '<span class="rec-cand__who">' + parts.map(escapeHtml).join(" · ") + "</span>"
      : "";
  }

  function boardCard(row) {
    var mark = sourceMark(row);
    var tier = TIERS[row.tier] || null;
    var title = row.target_position || row.position_title || "Позиция не указана";
    /* Карточка на доске — только то, по чему её узнают в лицо: кто, откуда и
       насколько подходит. Всё остальное открывается по клику: в колонке
       шириной в 240 пикселей форма из шести полей читалась хуже, чем список.

       Ссылка на резюме есть только у кандидата с HH: там она ведёт на готовую
       страницу и экономит открытие карточки. У телеграмного отклика резюме —
       текст и файл, они раскрываются внутри карточки. */
    /* Источник ушёл в подвал строкой с иконкой, а цветом его повторяет кромка
       слева: этап и так виден по колонке, а вот откуда человек — нет.
       Наверху карточки плашка занимала целую строку ради одного слова. */
    return '<div class="rec-cand" draggable="true" data-rec-card="' + escapeHtml(row.id) +
      '" style="border-left:3px solid ' + mark.color + '">' +
      '<div class="rec-cand__top">' +
      '<span class="rec-cand__title">' + escapeHtml(title) + "</span>" +
      (tier
        ? '<span class="rec-chip" style="flex-shrink:0;background:' + tier.bg + ";color:" +
          tier.color + '">' + tier.label + "</span>"
        : "") + "</div>" +
      identityLine(row, true) +
      (row.note
        ? '<div class="rec-cand__quote">' + escapeHtml(row.note) + "</div>"
        : "") +
      '<div class="rec-cand__foot">' +
      '<span class="rec-cand__from" style="color:' + mark.color + '">' +
      icon(mark.icon, 13) + escapeHtml(mark.label) + "</span>" +
      "<span>" + escapeHtml(dateLabel(row.stage_changed_at)) + "</span></div></div>";
  }

  /* Кандидат, заведённый руками: пришёл по рекомендации, написал в личку,
     встретили на конференции. Полей ровно столько, сколько нужно, чтобы
     карточка на доске читалась, — остальное дописывают внутри неё. */
  function newCandidateModal() {
    var form = state.newCandidate || {};
    /* Поля те же, что в карточке кандидата: подпись строкой, поле во всю
       ширину колонки, разделы подписаны сверху. Форма заведения и карточка —
       про одного и того же человека, и выглядеть они должны одинаково. */
    function field(label, name, placeholder, wide) {
      return "<label" + (wide ? ' style="grid-column:1/-1"' : "") + ">" +
        escapeHtml(label) +
        '<input class="meta-control" type="text" data-rec-new-field="' + name +
        '" placeholder="' + escapeHtml(placeholder || "") + '" value="' +
        escapeHtml(form[name] || "") + '"></label>';
    }
    var ownerOptions = '<option value="">Без ответственного</option>' +
      state.users.map(function (user) {
        return '<option value="' + escapeHtml(user.id) + '"' +
          (user.id === form.owner_id ? " selected" : "") + ">" +
          escapeHtml(user.name) + "</option>";
      }).join("");
    return '<div class="rec-sheet" data-rec-new-close>' +
      '<div class="rec-sheet__card" role="dialog" aria-modal="true">' +
      '<div class="rec-sheet__head"><div class="rec-sheet__title">Новый кандидат</div>' +
      '<button type="button" class="rec-editor__close" data-rec-new-close ' +
      'aria-label="Закрыть">×</button></div>' +
      '<div class="rec-sheet__body">' +
      '<div class="rec-sheet__legend">Классификация</div>' +
      '<div class="rec-sheet__grid">' +
      field("Позиция", "position_title", "Media Buyer") +
      '<label>Ответственный<select class="meta-control meta-select" ' +
      'data-rec-new-field="owner_id">' + ownerOptions + "</select></label>" +
      field("Телеграм", "telegram_contact", "@username", true) +
      "</div>" +
      '<div class="rec-sheet__legend">Интервью</div>' +
      '<div class="rec-sheet__record"><label class="rec-sheet__field">Ссылка на запись' +
      (state.newCandidateRecord
        ? '<span class="rec-sheet__record-done"><span class="rec-sheet__record-name" title="' +
          escapeHtml(state.newCandidateRecord.name) + '">' +
          escapeHtml(state.newCandidateRecord.name) + "</span>" +
          '<button type="button" data-rec-new-record-clear aria-label="Убрать файл">×</button></span>'
        : '<input class="meta-control" type="text" data-rec-new-field="interview_record" ' +
          'placeholder="https://…" value="' + escapeHtml(form.interview_record || "") + '">') +
      "</label>" +
      '<label class="rec-sheet__file" title="Прикрепить файл записи">' +
      '<input type="file" hidden data-rec-new-record>Файл</label></div>' +
      '<div class="rec-sheet__legend">Резюме</div>' +
      '<div class="rec-sheet__record"><label class="rec-sheet__field">Ссылка на резюме' +
      '<input class="meta-control" type="text" data-rec-new-field="external_url" ' +
      'placeholder="https://hh.ru/resume/…" value="' + escapeHtml(form.external_url || "") +
      '"></label></div>' +
      '<label class="rec-sheet__note">Заметка' +
      '<textarea class="meta-control rec-cand__note" data-rec-new-field="note" ' +
      'placeholder="Откуда пришёл, о чём договорились">' +
      escapeHtml(form.note || "") + "</textarea></label></div>" +
      '<div class="rec-editor__foot">' +
      (state.newCandidateError
        ? '<span class="rec-editor__error">' + escapeHtml(state.newCandidateError) + "</span>"
        : "<span>Кандидат встанет на этап «Скрининг»</span>") +
      '<div class="rec-editor__actions">' +
      '<button class="meta-action" type="button" data-rec-new-close>Отмена</button>' +
      '<button class="rec-editor__save" type="button" data-rec-new-save' +
      (state.busy ? " disabled" : "") + ">" +
      (state.busy ? "Сохраняю…" : "Завести") + "</button></div></div>" +
      "</div></div>";
  }

  /* Откуда кандидат. Заведённый руками — «Вручную»: hh.ru на нём означал бы,
     что за ним есть резюме на hh.ru, а его нет. */
  var SOURCE_MARKS = {
    hh: { label: "hh.ru", icon: "doc", color: "#2C6BD8" },
    telegram: { label: "Telegram", icon: "send", color: "#16B57F" },
    manual: { label: "Вручную", icon: "pencil", color: "#857D7D" }
  };

  function sourceMark(row) {
    return SOURCE_MARKS[row.source || ""] || SOURCE_MARKS.hh;
  }

  function sourceLabel(row) {
    return sourceMark(row).label;
  }

  /* Подробности кандидата — модалкой: на доске место дорого, а полей шесть. */
  function candidateModal(row) {
    var tier = TIERS[row.tier] || null;
    var telegram = (row.source || "") === "telegram";
    var hh = (row.source || "") === "hh";
    /* Телеграм подставляем сам: из снимка профиля, а у старых кандидатов,
       у которых его нет, — из ссылки t.me в источнике отклика. */
    var telegramAuto = String((row.profile || {}).telegram_username || "");
    if (!telegramAuto && row.external_url && String(row.external_url).indexOf("t.me/") >= 0) {
      telegramAuto = telegramHandle(row.external_url);
    }
    var ownerOptions = '<option value="">Без ответственного</option>' +
      state.users.map(function (user) {
        return '<option value="' + escapeHtml(user.id) + '"' +
          (user.id === row.owner_id ? " selected" : "") + ">" +
          escapeHtml(user.name) + "</option>";
      }).join("");
    return '<div class="rec-sheet" data-rec-card-close>' +
      '<div class="rec-sheet__card" role="dialog" aria-modal="true" data-rec-sheet>' +
      // Отступы шапке задаёт таблица стилей: инлайновые 20px не совпадали с
      // 16px у строк ниже, и название позиции стояло на четыре пикселя правее
      // всего остального в карточке.
      '<div class="rec-sheet__head">' +
      '<span class="rec-sheet__title">' +
      escapeHtml(row.target_position || row.position_title || "Позиция не указана") + "</span>" +
      (tier
        ? '<span class="rec-chip" style="background:' + tier.bg + ";color:" + tier.color +
          '">' + tier.label + "</span>"
        : "") + "</div>" +
      /* Строка «кто это» — только у кандидата с резюме на hh.ru: там она и
         правда рассказывает о человеке то, чего нет в полях ниже. У отклика из
         телеграма её место занимает ник в своём поле, а у заведённого руками —
         те же имя и город, которые вписали в форме; строка повторяла их и
         висела над разделителем сама по себе.

         Ни источника, ни опыта, ни ожиданий здесь тоже нет: источник виден по
         кромке карточки на доске, а опыт и ожидания сервис считает по тексту
         резюме и ошибается чаще, чем помогает. */
      (hh && identityLine(row)
        ? '<div class="rec-sheet__who">' + identityLine(row) + "</div>" : "") +
      resumeBlock(row) +
      '<div class="rec-sheet__body">' +
      '<div class="rec-sheet__legend">Классификация</div>' +
      '<div class="rec-sheet__grid">' +
      '<label>Позиция<input class="meta-control" type="text" placeholder="Позиция" ' +
      'data-rec-field="target_position" data-rec-row="' + escapeHtml(row.id) + '" value="' +
      escapeHtml(row.target_position || "") + '"></label>' +
      '<label>Ответственный<select class="meta-control meta-select" data-rec-owner="' +
      escapeHtml(row.id) + '">' + ownerOptions + "</select></label>" +
      '<label style="grid-column:1/-1">Телеграм' +
      '<input class="meta-control" type="text" placeholder="@username" ' +
      'data-rec-field="telegram_contact" data-rec-row="' + escapeHtml(row.id) + '" value="' +
      escapeHtml(row.telegram_contact || telegramAuto) +
      '"></label></div>' +
      '<div class="rec-sheet__legend">Интервью</div>' +
      '<div class="rec-sheet__record">' +
      '<label class="rec-sheet__field">Ссылка на запись' + recordControl(row) + "</label>" +
      '<label class="rec-sheet__file" title="Загрузить файл записи">' +
      '<input type="file" hidden data-rec-record="' + escapeHtml(row.id) + '">Файл</label>' +
      "</div>" +
      '<label class="rec-sheet__note">Заметка' +
      '<textarea class="meta-control rec-cand__note" placeholder="Комментарий команды…" ' +
      'data-rec-note="' + escapeHtml(row.id) + '">' + escapeHtml(row.note || "") +
      "</textarea></label></div>" +
      '<div class="rec-sheet__foot">' +
      "<span>Добавлен " + escapeHtml(dateLabel(row.created_at || row.stage_changed_at)) +
      "</span>" +
      '<button type="button" class="rec-sheet__drop" data-rec-board-remove="' +
      escapeHtml(row.id) + '">Убрать из пайплайна</button></div></div></div>';
  }

  /* «Резюме» в карточке. У кандидата с HH это ссылка на hh.ru — там всё
     резюме целиком. У телеграмного резюме как документа нет: есть текст, что
     он написал, и файл, если приложил, — они и раскрываются здесь же, не
     уводя человека со страницы. */
  function resumeBlock(row) {
    var telegram = (row.source || "") === "telegram";
    if (!telegram) {
      return row.external_url
        ? '<div class="rec-sheet__resume"><a class="rec-sheet__link" href="' +
          escapeHtml(row.external_url) + '" target="_blank" rel="noopener noreferrer">' +
          "Резюме на hh.ru</a></div>"
        : "";
    }
    var profile = row.profile || {};
    var text = String(profile.application_text || "").trim();
    // Файл — той же карточкой, что в откликах: иконка, имя, тип и «скачать».
    // Голая ссылка с именем файла выглядела случайной кнопкой под текстом.
    var file = resumeFileCard(profile);
    /* Кнопка «Резюме» есть всегда: по клику раскрывается то, что человек
       прислал в отклике. Если ни текста, ни файла — честно пишем «Резюме нет»,
       а не молча прячем кнопку. */
    var open = state.boardResume === row.id;
    return '<div class="rec-sheet__resume">' +
      '<button type="button" class="rec-sheet__link rec-sheet__toggle" ' +
      'data-rec-resume="' + escapeHtml(row.id) + '">Резюме' +
      '<span class="rec-sheet__caret' + (open ? " is-open" : "") + '">▾</span></button>' +
      (open
        ? text || file
          ? '<div class="rec-apply">' + file +
            (text ? '<div class="rec-apply__text">' + escapeHtml(text) + "</div>" : "") +
            "</div>"
          : '<div class="rec-cand__quote">Резюме нет</div>'
        : "") + "</div>";
  }

  function recordControl(row) {
    var uploaded = String(row.interview_record || "")
      .indexOf("/api/v1/recruitment/records/") === 0;
    if (uploaded) {
      return '<span class="rec-sheet__record-done">' +
        '<a href="' + escapeHtml(row.interview_record) + '" target="_blank" ' +
        'rel="noopener noreferrer">Запись загружена</a>' +
        '<button type="button" data-rec-record-clear="' + escapeHtml(row.id) +
        '" aria-label="Убрать запись">×</button></span>';
    }
    return '<input class="meta-control" type="text" placeholder="https://…" ' +
      'data-rec-field="interview_record" data-rec-row="' + escapeHtml(row.id) + '" value="' +
      escapeHtml(row.interview_record || "") + '">';
  }

  /* ---------- модалка шаблона ---------- */

  /* Значение критерия: чипсы у списков, теги у свободного текста. Вынесено из
     карточки, чтобы сама карточка читалась как «шапка + поле + вес». */
  function criterionValueHtml(item) {
    var value = editorValue(item.key);
    if (item.type === "multi") {
      // Варианты чипами, а не списком: их немного, выбирают обычно два-три,
      // и выпадающий список с галочками читался бы хуже, чем видимый набор.
      var picked = valueList(item.key);
      return '<div class="rec-chips">' + item.options.map(function (option) {
        var on = picked.indexOf(option[0]) >= 0;
        return '<button type="button" class="rec-chip' + (on ? " is-on" : "") +
          '" data-rec-pick="' + item.key + '" data-rec-value="' + option[0] + '">' +
          option[1] + "</button>";
      }).join("") + "</div>";
    }
    if (item.type === "tags") {
      var tags = valueList(item.key);
      return '<div class="rec-chips">' + tags.map(function (tag, index) {
        return '<span class="rec-tag">' + escapeHtml(tag) +
          '<button type="button" data-rec-tag-drop="' + item.key +
          '" data-rec-index="' + index + '" aria-label="Убрать">×</button></span>';
      }).join("") +
        '<input class="rec-tag-input" type="text" data-rec-tag-add="' + item.key +
        '" placeholder="' + escapeHtml(item.placeholder || "добавить") +
        '" aria-label="Добавить значение"></div>';
    }
    if (item.type === "areas") {
      var areas = valueList(item.key);
      return '<div class="rec-area">' +
        '<div class="rec-chips">' + areas.map(function (id, index) {
          return '<span class="rec-tag">' + escapeHtml(areaLabel(id)) +
            '<button type="button" data-rec-tag-drop="' + item.key +
            '" data-rec-index="' + index +
            '" aria-label="Убрать город">×</button></span>';
        }).join("") + '</div><div class="rec-area__search">' +
        '<input class="rec-tag-input rec-area__input" type="search" ' +
        'data-rec-area-input value="' + escapeHtml(state.areaQuery) + '" placeholder="' +
        escapeHtml(item.placeholder || "Начните вводить город") + '" autocomplete="off" ' +
        'role="combobox" aria-autocomplete="list" aria-expanded="false">' +
        '<div class="rec-area__menu" data-rec-area-menu role="listbox">' +
        areaSuggestionsHtml() + "</div></div></div>";
    }
    return '<input class="meta-control" style="width:100%;padding:0 11px" type="text" ' +
      'data-rec-criterion="' + item.key + '" data-rec-field="value" value="' +
      escapeHtml(value == null ? "" : value) + '">';
  }

  /* Карточка критерия: режим переключателем из трёх кнопок, вес — ползунком.

     Вес показывается только у «Желательного»: в сервисе баллы даёт именно он,
     а «Обязательный» решает, проходит ли резюме фильтр, и его вес в расчёт не
     идёт. Поле веса рядом с обязательным критерием обещало бы влияние на
     оценку, которого нет. */
  function criterionCard(item) {
    var mode = editorMode(item.key);
    var weight = editorWeight(item.key);
    return '<div class="rec-crit' + (mode === "ignore" ? "" : " is-on") + '">' +
      '<div class="rec-crit__head"><span class="rec-crit__name" title="' +
      escapeHtml(item.hint || "") + '">' + escapeHtml(item.label) + "</span>" +
      '<div class="rec-modes">' + MODES.map(function (row) {
        return '<button type="button" class="rec-mode' +
          (mode === row[0] ? " is-on is-" + row[0] : "") + '" data-rec-mode="' + item.key +
          '" data-rec-value="' + row[0] + '">' + row[2] + "</button>";
      }).join("") + "</div></div>" +
      '<div class="rec-crit__value">' + criterionValueHtml(item) + "</div>" +
      (mode === "preferred"
        ? '<label class="rec-weight"><span>Вес</span>' +
          '<input type="range" min="0" max="100" step="1" value="' + weight +
          '" data-rec-criterion="' + item.key + '" data-rec-field="weight">' +
          '<b>' + weight + "</b></label>"
        : "") + "</div>";
  }

  /* Ключевое слово в список. Возвращает, было ли что добавлять: пустую строку
     и повтор молча пропускаем — чип-дубль ничего не даёт, а ошибка на пустом
     поле выглядит придиркой.

     Слово всегда «желательное»: оно добавляет баллы, а не отсекает резюме.
     Обязательным его делать нельзя молча — так шаблон перестал бы находить
     тех, кто написал ту же мысль другими словами. */
  function addKeyword(word) {
    var value = String(word || "").trim().replace(/,$/, "");
    if (!value) return false;
    var known = state.editor.keywords.some(function (row) {
      return String(row.value || "").toLowerCase() === value.toLowerCase();
    });
    if (!known) {
      state.editor.keywords.push(
        { key: "keyword", value: value, mode: "preferred", weight: 40 });
    }
    return true;
  }

  function keywordRow(row, index) {
    return '<span class="rec-tag rec-tag--kw">' + escapeHtml(row.value || "") +
      '<button type="button" data-rec-keyword-drop="' + index +
      '" aria-label="Убрать">×</button></span>';
  }

  /* Итог формы одной строкой: сколько критериев режут выдачу и на сколько
     баллов набрано желательных. По этим двум числам и понятно, что получится
     из шаблона, — считать их в уме по карточкам никто не станет. */
  function editorSummary() {
    var required = 0;
    var weight = 0;
    Object.keys(state.editor.criteria).forEach(function (key) {
      var row = state.editor.criteria[key] || {};
      if (row.mode === "required") required += 1;
      if (row.mode === "preferred") weight += Number(row.weight) || 0;
    });
    state.editor.keywords.forEach(function (row) {
      if (row.mode === "required") required += 1;
      if (row.mode === "preferred") weight += Number(row.weight) || 0;
    });
    return plural(required, "обязательный", "обязательных", "обязательных") +
      " · суммарный вес " + weight;
  }

  function plural(count, one, few, many) {
    var mod10 = count % 10;
    var mod100 = count % 100;
    var word = mod10 === 1 && mod100 !== 11 ? one
      : mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20) ? few : many;
    return count + " " + word;
  }

  function editorModal() {
    var editor = state.editor;
    var responses = editor.templateType === "responses";
    // Шаблон телеграмных откликов привязан не к вакансии HH, а к коду, который
    // присылает бот, — поэтому и поле в шапке другое.
    var telegram = responses && editor.telegram;
    var criteria = CRITERIA.map(criterionCard).join("");
    var keywords = editor.keywords.map(keywordRow).join("");

    return '<div class="rec-sheet"><div class="rec-sheet__card rec-editor" role="dialog" ' +
      'aria-modal="true">' +
      '<div class="rec-sheet__head"><div>' +
      '<div class="rec-sheet__title">' +
      (telegram
        ? (editor.id ? "Шаблон Telegram-откликов" : "Новый шаблон Telegram-откликов")
        : responses ? "Настройка откликов на вакансию"
          : editor.id ? "Редактировать шаблон поиска" : "Новый шаблон поиска") + "</div>" +
      '<div class="rec-editor__legend">' +
      '<span><i class="rec-editor__dot rec-editor__dot--req"></i>Обязательный — фильтр</span>' +
      '<span><i class="rec-editor__dot rec-editor__dot--pref"></i>Желательный — баллы</span>' +
      "</div></div>" +
      '<button type="button" data-rec-editor-close aria-label="Закрыть" ' +
      'class="rec-editor__close">×</button></div>' +

      /* Шапка шаблона — такой же карточкой, как критерии ниже: два поля,
         висящие прямо на фоне окна, читались как случайный ввод, а не как
         часть формы. Подсказка под «Кодом вакансии» объясняет, зачем он: сам
         по себе «ссылка / ID» ничего не говорит. */
      '<div class="rec-editor__card">' +
      '<div class="rec-editor__grid">' +
      '<label class="meta-field"><span>Название шаблона</span>' +
      '<input class="meta-control" type="text" placeholder="Media Buyer (Facebook, iGaming)" ' +
      'value="' + escapeHtml(editor.name) + '" data-rec-editor-field="name"></label>' +
      (responses && !telegram
        ? '<label class="meta-field"><span>Вакансия HH</span>' +
          '<select class="meta-control meta-select" data-rec-editor-field="hhVacancy">' +
          '<option value="">Выберите вакансию</option>' +
          (editor.hhVacancy && !state.vacancies.some(function (row) {
            return row.id === editor.hhVacancy;
          })
            ? '<option selected value="' + escapeHtml(editor.hhVacancy) + '">' +
              escapeHtml(editor.name) + " (подключена)</option>"
            : "") +
          state.vacancies.map(function (row) {
            var used = state.templates.some(function (tpl) {
              return tpl.template_type === "responses" && tpl.hh_vacancy_id === row.id &&
                tpl.id !== editor.id;
            });
            return '<option value="' + escapeHtml(row.id) + '"' +
              (row.id === editor.hhVacancy ? " selected" : "") + (used ? " disabled" : "") +
              ">" + escapeHtml(row.name) + (used ? " — уже подключена" : "") + "</option>";
          }).join("") + "</select></label>"
        : '<label class="meta-field"><span>Код вакансии</span>' +
          '<input class="meta-control" type="text" placeholder="например, media-buyer-fb" ' +
          'value="' + escapeHtml(editor.vacancy) + '" data-rec-editor-field="vacancy"></label>') +
      "</div>" +
      (telegram
        ? '<div class="rec-editor__note">Тот же код бот присылает вместе с ' +
          "откликом — по нему отклик попадает в этот шаблон и получает оценку " +
          "по его критериям.</div>"
        : "") +
      "</div>" +

      '<div class="rec-editor__legend-row">Критерии' +
      (responses
        ? '<span class="rec-editor__hint">Все отклики попадут в список — критерии только ' +
          "расставляют приоритеты</span>"
        : "") + "</div>" +
      '<div class="rec-crits">' + criteria + "</div>" +

      /* Кнопки «Добавить» нет: слово добавляет Enter — тот же жест, что и в
         полях критериев, и второй способ рядом только сбивал. Набранное и не
         подтверждённое всё равно попадёт в шаблон при сохранении. */
      '<div class="rec-editor__legend-row">Ключевые слова</div>' +
      '<div class="rec-chips rec-editor__kw">' + keywords +
      '<input class="rec-tag-input" type="text" data-rec-keyword-input ' +
      'placeholder="Facebook Ads" aria-label="Добавить ключевое слово"></div>' +

      '<div class="rec-editor__auto">' +
      (telegram
        ? '<span class="rec-editor__hint">Оценка считается в момент отклика — ' +
          "запускать шаблон вручную не нужно</span>"
        : responses
        ? '<span class="rec-editor__hint">Отклики проверяются автоматически, ' +
          "пока сбор включён</span>"
        : '<label class="meta-switch"><input type="checkbox" data-rec-editor-field="auto"' +
          (editor.auto ? " checked" : "") +
          '><span class="meta-switch__box"></span><span>Автопоиск</span></label>' +
          '<select class="meta-control meta-select" style="width:150px;height:34px;padding:0 11px" ' +
          'data-rec-editor-field="interval"' + (editor.auto ? "" : " disabled") + ">" +
          INTERVALS.map(function (row) {
            return '<option value="' + row[0] + '"' +
              (Number(editor.interval) === row[0] ? " selected" : "") + ">" + row[1] + "</option>";
          }).join("") + "</select>") +
      // Тумблера «Включён» здесь нет: шаблон приостанавливают кнопкой на его
      // карточке в списке, и два разных места для одного состояния только
      // путали. Прежнее значение сохраняется как есть.
      "</div>" +

      '<div class="rec-editor__foot">' +
      (state.editorError
        ? '<span class="rec-editor__error">' + escapeHtml(state.editorError) + "</span>"
        : "<span>" + escapeHtml(editorSummary()) + "</span>") +
      '<div class="rec-editor__actions">' +
      '<button class="meta-action" type="button" data-rec-editor-close>Отмена</button>' +
      '<button class="rec-editor__save" type="button" data-rec-editor-save>Сохранить</button>' +
      "</div></div></div></div>";
  }

  /* ---------- события ---------- */

  /* Переключение вкладки не должно выглядеть как новый заход в раздел: то,
     что уже загружено, показываем сразу, а свежесть догоняем в фоне. Грузим
     с полосой только пустую вкладку — там показывать всё равно нечего. */
  function ensureTab(force) {
    /* Пустая вкладка и незагруженная — разные вещи: пустой список тоже ответ,
       и переспрашивать его на каждом переключении незачем. Поэтому смотрим на
       отметку времени, а не на длину списка. */
    if ((force || !isFresh("templates")) && !state.templatesLoading) {
      quietly(loadTemplates);
    }
    if (state.tab === "search") {
      // Шаблон мог остаться открытым с прошлого захода: истории запусков у
      // него ещё нет, а без неё карточка уверяет, что запусков не было.
      if (state.selectedTemplateId && !state.runs[state.selectedTemplateId]) {
        loadRuns(state.selectedTemplateId).then(render).catch(function () {});
      }
      // Список находок показывается только внутри выбранного шаблона.
      if (state.pendingTemplate && !state.stamps.pending && !state.pendingLoading) loadPending();
      else if (state.pendingTemplate && (force || !isFresh("pending")) &&
        !state.pendingLoading) quietly(function () { return loadPending(true); });
    }
    if (state.tab === "responses") {
      if (!state.stamps.incoming && !state.incomingLoading) loadIncoming();
      else if ((force || !isFresh("incoming")) && !state.incomingLoading) {
        quietly(function () { return loadIncoming(true); });
      }
    }
    if (state.tab === "candidates") {
      if (!state.stamps.board && !state.boardLoading) loadBoard();
      else if ((force || !isFresh("board")) && !state.boardLoading) {
        quietly(function () { return loadBoard(true); });
      }
    }
    if (!state.hh && !state.hhLoading) loadHh();
  }

  /* Вкладка получает свежие данные сразу после возвращения пользователя и
     затем раз в десять секунд. Кэш всё это время остаётся на экране. */
  function refreshLive() {
    if (document.hidden || state.editor || state.newCandidate || state.refreshing) return;
    if (Date.now() - lastLiveRefresh < 2500) return;
    lastLiveRefresh = Date.now();
    ensureTab(true);
  }

  function onTabClick(button) {
    state.tab = button.getAttribute("data-rec-tab");
    showError("");
    render();
    ensureTab();
  }

  document.addEventListener("click", function (event) {
    var target = event.target;
    function closest(selector) {
      return target.closest ? target.closest(selector) : null;
    }
    var tab = closest("[data-rec-tab]");
    if (tab) return onTabClick(tab);

    if (closest("[data-rec-new-response]")) {
      state.editor = emptyEditor({template_type: "responses"});
      state.editorError = "";
      loadVacancies();
      return render();
    }
    if (closest("[data-rec-new-candidate]")) {
      state.newCandidate = {};
      state.newCandidateRecord = null;
      state.newCandidateError = "";
      return render();
    }
    if (closest("[data-rec-new-record-clear]")) {
      state.newCandidateRecord = null;
      return render();
    }
    var newClose = closest("[data-rec-new-close]");
    if (newClose && !closest("[data-rec-new-save]")) {
      /* Окно закрывают крестик, «Отмена» и клик по подложке мимо карточки.
         Раньше здесь сравнивался ближайший `data-rec-new-close` с родителем
         карточки — но у клика по полю ближайшим оказывалась та же подложка,
         и окно закрывалось от нажатия на любое поле. */
      if (!closest(".rec-sheet__card") || newClose.tagName === "BUTTON") {
        state.newCandidate = null;
        state.newCandidateRecord = null;
        state.newCandidateError = "";
        return render();
      }
    }
    if (closest("[data-rec-new-save]")) return saveNewCandidate();
    if (closest("[data-rec-new-telegram]")) {
      state.editor = emptyEditor({ template_type: "responses", telegram: true });
      state.editorError = "";
      return render();
    }
    var toggleResponse = closest("[data-rec-toggle-response]");
    if (toggleResponse) {
      var response = state.templates.find(function (row) { return row.id === toggleResponse.getAttribute("data-rec-toggle-response"); });
      return api.put("/recruitment/search-templates/" + response.id, {is_active: !response.is_active}).then(loadTemplates).then(render).catch(function (error) { showError(error.message); });
    }
    var showResponse = closest("[data-rec-show-response]");
    if (showResponse) { state.responseTemplate = showResponse.getAttribute("data-rec-show-response"); return render(); }
    if (closest("[data-rec-new-template]")) {
      state.editor = emptyEditor(null);
      state.editorError = "";
      return render();
    }
    var run = closest("[data-rec-run]");
    if (run) {
      event.stopPropagation();
      return runSearch(run.getAttribute("data-rec-run"));
    }
    var edit = closest("[data-rec-edit]");
    if (edit) {
      event.stopPropagation();
      var template = state.templates.filter(function (row) {
        return row.id === edit.getAttribute("data-rec-edit");
      })[0];
      state.editor = emptyEditor(template);
      state.editorError = "";
      if (template.template_type === "responses") loadVacancies();
      return render();
    }
    var drop = closest("[data-rec-delete]");
    if (drop) {
      event.stopPropagation();
      return deleteTemplate(drop.getAttribute("data-rec-delete"));
    }
    var openTemplate = closest("[data-rec-open-template]");
    if (openTemplate) {
      state.selectedTemplateId = openTemplate.getAttribute("data-rec-open-template");
      state.pendingTemplate = state.selectedTemplateId;
      var pendingCache = readCache("pending");
      if (pendingCache) {
        state.pending = pendingCache.value;
        state.stamps.pending = Number(pendingCache.savedAt || 0);
      } else {
        state.pending = [];
        state.stamps.pending = 0;
      }
      loadRuns(state.selectedTemplateId).then(render).catch(function () {});
      if (state.stamps.pending) {
        quietly(function () { return loadPending(true); });
      } else {
        loadPending();
      }
      return render();
    }
    if (closest("[data-rec-refresh-pending]")) {
      return quietly(function () { return loadPending(true); });
    }

    var textToggle = closest("[data-rec-text]");
    if (textToggle) {
      var textId = textToggle.getAttribute("data-rec-text");
      state.openTexts = state.openTexts.indexOf(textId) < 0
        ? state.openTexts.concat(textId)
        : state.openTexts.filter(function (item) { return item !== textId; });
      return render();
    }

    var add = closest("[data-rec-add]");
    if (add) return reviewCandidate(add.getAttribute("data-rec-add"), "added");
    var skip = closest("[data-rec-skip]");
    if (skip) return reviewCandidate(skip.getAttribute("data-rec-skip"), "skipped");

    if (closest("[data-rec-refresh-incoming]")) {
      return quietly(function () {
        return Promise.all([loadTemplates(), loadIncoming(true)]);
      });
    }
    if (closest("[data-rec-refresh-board]")) {
      return quietly(function () { return loadBoard(true); });
    }
    var stageFilter = closest("[data-rec-stage-filter]");
    if (stageFilter) {
      var key = stageFilter.getAttribute("data-rec-stage-filter");
      state.boardStage = state.boardStage === key ? "" : key;
      return render();
    }
    var boardRemove = closest("[data-rec-board-remove]");
    if (boardRemove) return removeBoardRow(boardRemove.getAttribute("data-rec-board-remove"));
    if (closest("[data-rec-hh-connect]")) return connectHh();
    if (closest("[data-rec-hh-refresh]")) return loadHh();

    var pick = closest("[data-rec-pick]");
    if (pick) {
      var pickKey = pick.getAttribute("data-rec-pick");
      var chosen = valueList(pickKey);
      var one = pick.getAttribute("data-rec-value");
      var at = chosen.indexOf(one);
      if (at >= 0) chosen.splice(at, 1); else chosen.push(one);
      setCriterion(pickKey, { value: chosen });
      // Первый выбранный вариант включает критерий: оставлять его «не
      // учитывать» после явного выбора значит потерять клик.
      if (chosen.length && editorMode(pickKey) === "ignore") {
        setCriterion(pickKey, { mode: "required" });
      }
      return render();
    }
    var areaPick = closest("[data-rec-area-pick]");
    if (areaPick) {
      var areaId = areaPick.getAttribute("data-rec-area-id");
      var selectedAreas = valueList("geo_area_id");
      if (selectedAreas.indexOf(areaId) < 0) selectedAreas.push(areaId);
      rememberArea({
        id: areaId,
        name: areaPick.getAttribute("data-rec-area-name"),
        parent: areaPick.getAttribute("data-rec-area-parent")
      });
      setCriterion("geo_area_id", { value: selectedAreas });
      if (editorMode("geo_area_id") === "ignore") {
        setCriterion("geo_area_id", { mode: "required" });
      }
      state.areaQuery = "";
      state.areaSuggestions = [];
      state.areaLoading = false;
      return render();
    }
    var tagDrop = closest("[data-rec-tag-drop]");
    if (tagDrop) {
      var dropKey = tagDrop.getAttribute("data-rec-tag-drop");
      var list = valueList(dropKey);
      list.splice(Number(tagDrop.getAttribute("data-rec-index")), 1);
      setCriterion(dropKey, { value: list });
      return render();
    }
    if (closest("[data-rec-card-close]") && !closest("[data-rec-sheet]")) {
      state.boardCard = null;
      state.boardResume = null;
      return render();
    }
    var resumeToggle = closest("[data-rec-resume]");
    if (resumeToggle) {
      var resumeId = resumeToggle.getAttribute("data-rec-resume");
      state.boardResume = state.boardResume === resumeId ? null : resumeId;
      return render();
    }
    var openCard = closest("[data-rec-card]");
    // Ссылку и кнопку внутри карточки открытие не перехватывает.
    if (openCard && !closest("a") && !closest("button")) {
      var picked = (state.board.items || []).filter(function (item) {
        return item.id === openCard.getAttribute("data-rec-card");
      })[0];
      if (picked) {
        state.boardCard = picked;
        return render();
      }
    }
    var incomingSource = closest("[data-rec-incoming-source]");
    if (incomingSource) {
      state.incomingSource = incomingSource.getAttribute("data-rec-incoming-source");
      return render();
    }
    var recordClear = closest("[data-rec-record-clear]");
    if (recordClear) {
      return patchBoardRow(recordClear.getAttribute("data-rec-record-clear"),
        { interview_record: "" });
    }
    var modeButton = closest("[data-rec-mode]");
    if (modeButton) {
      setCriterion(modeButton.getAttribute("data-rec-mode"),
        { mode: modeButton.getAttribute("data-rec-value") });
      return render();
    }
    var keywordDrop = closest("[data-rec-keyword-drop]");
    if (keywordDrop) {
      state.editor.keywords.splice(Number(keywordDrop.getAttribute("data-rec-keyword-drop")), 1);
      return render();
    }
    if (closest("[data-rec-editor-save]")) return saveEditor();
    if (closest("[data-rec-editor-close]")) {
      state.editor = null;
      state.editorError = "";
      state.areaQuery = "";
      state.areaSuggestions = [];
      return render();
    }
    if (!closest(".rec-area")) {
      state.areaSuggestions = [];
      state.areaQuery = "";
      paintAreaSuggestions();
    }
  });

  /* Перетаскивание карточки между колонками. Этап меняется по отпусканию —
     до него ничего не сохраняем: человек может передумать на полпути. */
  var dragged = null;

  document.addEventListener("dragstart", function (event) {
    var card = event.target.closest ? event.target.closest("[data-rec-card]") : null;
    if (!card) return;
    dragged = card.getAttribute("data-rec-card");
    card.classList.add("is-dragging");
    // Без данных в буфере Firefox не начинает перетаскивание.
    if (event.dataTransfer) {
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", dragged);
    }
  });

  document.addEventListener("dragend", function (event) {
    var card = event.target.closest ? event.target.closest("[data-rec-card]") : null;
    if (card) card.classList.remove("is-dragging");
    dragged = null;
    [].forEach.call(document.querySelectorAll(".rec-col.is-over"), function (column) {
      column.classList.remove("is-over");
    });
  });

  document.addEventListener("dragover", function (event) {
    if (!dragged) return;
    var column = event.target.closest ? event.target.closest("[data-rec-drop]") : null;
    if (!column) return;
    // Без preventDefault браузер считает область запрещённой для сброса.
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "move";
    column.classList.add("is-over");
  });

  document.addEventListener("dragleave", function (event) {
    var column = event.target.closest ? event.target.closest("[data-rec-drop]") : null;
    if (column && !column.contains(event.relatedTarget)) column.classList.remove("is-over");
  });

  document.addEventListener("drop", function (event) {
    if (!dragged) return;
    var column = event.target.closest ? event.target.closest("[data-rec-drop]") : null;
    if (!column) return;
    event.preventDefault();
    column.classList.remove("is-over");
    var rowId = dragged;
    var stage = column.getAttribute("data-rec-drop");
    dragged = null;
    var row = (state.board.items || []).filter(function (item) {
      return item.id === rowId;
    })[0];
    // Сброс в ту же колонку — не перенос: лишний запрос и лишняя запись в
    // истории этапов.
    if (row && row.stage === stage) return;
    patchBoardRow(rowId, { stage: stage });
  });

  document.addEventListener("change", function (event) {
    var stageSelect = event.target.closest
      ? event.target.closest("[data-rec-stage]") : null;
    if (stageSelect) {
      return patchBoardRow(stageSelect.getAttribute("data-rec-stage"),
        { stage: stageSelect.value });
    }
    var ownerSelect = event.target.closest
      ? event.target.closest("[data-rec-owner]") : null;
    if (ownerSelect) {
      return patchBoardRow(ownerSelect.getAttribute("data-rec-owner"),
        { owner_id: ownerSelect.value });
    }
    // Заметка сохраняется по потере фокуса: писать её посимвольно в базу
    // значило бы слать запрос на каждую букву.
    var noteField = event.target.closest
      ? event.target.closest("[data-rec-note]") : null;
    if (noteField) {
      return patchBoardRow(noteField.getAttribute("data-rec-note"),
        { note: noteField.value });
    }
    var cardField = event.target.closest
      ? event.target.closest("[data-rec-field][data-rec-row]") : null;
    if (cardField) {
      var patch = {};
      patch[cardField.getAttribute("data-rec-field")] = cardField.value;
      return patchBoardRow(cardField.getAttribute("data-rec-row"), patch);
    }
    var newRecord = event.target.closest
      ? event.target.closest("[data-rec-new-record]") : null;
    if (newRecord && state.newCandidate) {
      if (newRecord.files && newRecord.files[0]) {
        state.newCandidateRecord = newRecord.files[0];
        render();
      }
      return;
    }
    var recordFile = event.target.closest
      ? event.target.closest("[data-rec-record]") : null;
    if (recordFile && recordFile.files && recordFile.files[0]) {
      return uploadRecord(recordFile.getAttribute("data-rec-record"), recordFile.files[0]);
    }
    var target = event.target;
    function closest(selector) {
      return target.closest ? target.closest(selector) : null;
    }
    var criterion = closest("[data-rec-criterion]");
    if (criterion) {
      setCriterion(criterion.getAttribute("data-rec-criterion"), (function () {
        var patch = {};
        patch[criterion.getAttribute("data-rec-field")] =
          criterion.type === "checkbox" ? criterion.checked : criterion.value;
        return patch;
      })());
      // Вес активен только у «Желательного»: перерисовываем модалку.
      if (criterion.getAttribute("data-rec-field") === "mode") return render();
      if (criterion.getAttribute("data-rec-field") === "weight") {
        // Число рядом с ползунком меняем на месте: перерисовка модалки на
        // каждое движение мыши обрывала бы само перетаскивание.
        var label = criterion.parentElement && criterion.parentElement.querySelector("b");
        if (label) label.textContent = criterion.value;
        var summary = document.querySelector(".rec-editor__foot span");
        if (summary) summary.textContent = editorSummary();
      }
      return;
    }
    var keyword = closest("[data-rec-keyword]");
    if (keyword) {
      var row = state.editor.keywords[Number(keyword.getAttribute("data-rec-keyword"))];
      if (row) {
        row[keyword.getAttribute("data-rec-field")] =
          keyword.getAttribute("data-rec-field") === "weight"
            ? Number(keyword.value) || 0 : keyword.value;
        if (keyword.getAttribute("data-rec-field") === "mode") return render();
      }
      return;
    }
    var editorField = closest("[data-rec-editor-field]");
    if (editorField) {
      var name = editorField.getAttribute("data-rec-editor-field");
      if (name === "auto" || name === "active") {
        state.editor[name] = editorField.checked;
        return render();
      }
      if (name === "interval") {
        state.editor.interval = Number(editorField.value);
        return;
      }
      state.editor[name] = editorField.value;
      if (name === "hhVacancy" && !state.editor.name) {
        var vacancy = state.vacancies.find(function (row) { return row.id === editorField.value; });
        if (vacancy) { state.editor.name = vacancy.name; render(); }
      }
      return;
    }
    var pendingTemplate = closest("[data-rec-pending-template]");
    if (pendingTemplate) {
      state.pendingTemplate = pendingTemplate.value;
      return loadPending();
    }
  });

  document.addEventListener("keydown", function (event) {
    // Esc закрывает раскрытую карточку: мышью для этого нужно попасть в фон.
    if (event.key === "Escape" && state.boardCard) {
      state.boardCard = null;
      state.boardResume = null;
      return render();
    }
    var areaInput = event.target.closest
      ? event.target.closest("[data-rec-area-input]") : null;
    if (areaInput) {
      if (event.key === "Escape") {
        state.areaSuggestions = [];
        state.areaQuery = "";
        areaInput.value = "";
        return paintAreaSuggestions();
      }
      if (event.key === "Enter" && state.areaSuggestions.length) {
        event.preventDefault();
        var firstArea = document.querySelector("[data-rec-area-pick]");
        if (firstArea) firstArea.click();
      }
      return;
    }
    var keywordInput = event.target.closest
      ? event.target.closest("[data-rec-keyword-input]") : null;
    if (keywordInput) {
      if (event.key !== "Enter" && event.key !== ",") return;
      event.preventDefault();
      if (!addKeyword(keywordInput.value)) return;
      render();
      var again = document.querySelector("[data-rec-keyword-input]");
      if (again) again.focus();
      return;
    }
    var field = event.target.closest ? event.target.closest("[data-rec-tag-add]") : null;
    if (!field) return;
    // Запятая — тот же разделитель, что и Enter: копируя список стран, её
    // набирают по привычке.
    if (event.key !== "Enter" && event.key !== ",") return;
    event.preventDefault();
    if (!field.value.trim()) return;
    var tagKey = field.getAttribute("data-rec-tag-add");
    addTagValue(tagKey, field.value);
    render();
    // Поле пересоздаётся перерисовкой — курсор возвращаем в новое.
    var next = document.querySelector('[data-rec-tag-add="' + tagKey + '"]');
    if (next) next.focus();
  });

  document.addEventListener("input", function (event) {
    var target = event.target;
    /* Вес критерия — на ходу, пока ползунок тянут: перерисовать форму на
       каждое движение нельзя (ползунок пересоздался бы и потерял мышь),
       поэтому меняем число и итог в подвале прямо на месте. */
    var weight = target.closest
      ? target.closest('[data-rec-field="weight"][data-rec-criterion]') : null;
    if (weight && state.editor) {
      var value = Number(weight.value) || 0;
      setCriterion(weight.getAttribute("data-rec-criterion"), { weight: value });
      var shown = weight.parentNode.querySelector("b");
      if (shown) shown.textContent = value;
      var foot = document.querySelector(".rec-editor__foot > span");
      if (foot && !state.editorError) foot.textContent = editorSummary();
      return;
    }
    var newField = target.closest ? target.closest("[data-rec-new-field]") : null;
    if (newField && state.newCandidate) {
      // Значение кладём в состояние без перерисовки: она сбросила бы каретку.
      state.newCandidate[newField.getAttribute("data-rec-new-field")] = newField.value;
      return;
    }
    var areaInput = target.closest ? target.closest("[data-rec-area-input]") : null;
    if (areaInput) {
      state.areaQuery = areaInput.value;
      state.areaSuggestions = [];
      window.clearTimeout(state.areaTimer);
      if (state.areaQuery.trim().length < 2) {
        state.areaLoading = false;
        return paintAreaSuggestions();
      }
      state.areaLoading = true;
      paintAreaSuggestions();
      state.areaTimer = window.setTimeout(function () {
        loadAreaSuggestions(state.areaQuery.trim());
      }, 250);
      return;
    }
    var search = target.closest ? target.closest("[data-rec-board-search]") : null;
    if (search) {
      state.boardSearch = search.value;
      // Здесь перерисовывается только тело вкладки, а не весь раздел, поэтому
      // вид запоминаем сами: иначе строка поиска не пережила бы перезаход.
      persistView();
      // Перерисовываем только тело вкладки и возвращаем каретку: иначе поиск
      // теряет фокус на каждом символе.
      var host = byId("recBody");
      var scroll = host.scrollTop;
      host.innerHTML = renderCandidates();
      host.scrollTop = scroll;
      var next = host.querySelector("[data-rec-board-search]");
      if (next) {
        next.focus();
        next.setSelectionRange(next.value.length, next.value.length);
      }
    }
  });

  async function connectHh() {
    try {
      var result = await api.post("/recruitment/hh/connect", {});
      if (result && result.authorize_url) {
        window.open(result.authorize_url, "_blank", "noopener");
        notify("Открыли страницу авторизации HH в новой вкладке");
      }
    } catch (error) {
      showError(error.message);
    }
  }

  async function boot() {
    // На остальных страницах раздела нет: ни запросов, ни рендера.
    if (!byId("recBody")) return;
    // Сначала вид, потом данные: ключ кэша находок зависит от выбранного
    // шаблона, и в обратном порядке они читались бы не из той ячейки.
    restoreView();
    restoreCache();
    loadStoredAreaLabels();
    // Сначала рисуем последний снимок, затем сверяем его с сервером. Поэтому
    // повторный вход не ждёт Recruitment Service даже при медленной сети.
    render();
    ensureTab(true);
  }

  /* Частый фоновый опрос безопасен для интерфейса: старый список не исчезает,
     а одинаковые запросы защищены loading-флагами. */
  window.setInterval(function () {
    if (byId("recBody")) refreshLive();
  }, LIVE_REFRESH_MS);
  window.addEventListener("focus", refreshLive);
  document.addEventListener("visibilitychange", function () {
    if (!document.hidden) refreshLive();
  });
  boot();
})();
