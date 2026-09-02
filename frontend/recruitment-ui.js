/* Раздел «Рекрутинг»: шаблоны поиска HH, отклики, кандидаты, настройки HH.

   Все данные живут в Recruitment Service — CRM только показывает их через
   свой бэкенд-прокси (/api/v1/recruitment/*). Ничего не кешируем агрессивно:
   кандидат разобран HR — он должен исчезнуть из «Откликов» сразу. */
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
    // Доска найма: этапы ведёт CRM, сервис рекрутинга их не хранит.
    board: null,
    boardLoading: false,
    boardSearch: "",
    boardStage: "",
    boardServiceDown: false,
    users: [],
    hh: null,
    editor: null,
    busy: false
  };

  /* Источники, которые считаются входящими отклики. Результаты активного
     поиска по резюме (`hh`) сюда не входят: это находки, а не отклики, и
     смешивать их — ровно та путаница, из-за которой «Отклики» показывали всё
     найденное. Когда подключат HH negotiations, их источник добавится сюда. */
  var INCOMING_SOURCES = ["telegram"];

  var INTERVALS = [
    [15, "15 минут"], [30, "30 минут"], [60, "1 час"], [120, "2 часа"],
    [360, "6 часов"], [720, "12 часов"], [1440, "24 часа"]
  ];

  /* Структурные критерии: HR выбирает значение по-человечески, а UI сам
     переводит выбор в key/value, которые понимает Recruitment Service. */
  var CRITERIA = [
    { key: "experience_level", label: "Опыт", type: "select", hint: "общий стаж",
      options: [
        ["no_experience", "Без опыта"], ["1_3_years", "1–3 года"],
        ["3_6_years", "3–6 лет"], ["6_plus_years", "6+ лет"]
      ] },
    { key: "employment_type", label: "Занятость", type: "select",
      options: [
        ["full", "Полная"], ["part_time", "Частичная"],
        ["internship", "Стажировка"], ["volunteer", "Волонтёрство"]
      ] },
    { key: "work_format", label: "Формат работы", type: "select",
      options: [
        ["on_site", "Офис"], ["remote", "Удалённо"], ["hybrid", "Гибрид"],
        ["field_work", "Разъезды"], ["fly_in_fly_out", "Вахта"]
      ] },
    { key: "job_search_status", label: "Статус поиска", type: "select",
      options: [
        ["active_search", "Активно ищет"], ["looking_for_offers", "Рассматривает предложения"],
        ["not_looking_for_job", "Не ищет работу"], ["has_job_offer", "Есть оффер"],
        ["accepted_job_offer", "Принял оффер"]
      ] },
    { key: "geo", label: "Гео", type: "text", hint: "город или страна — участвует в баллах" },
    { key: "language", label: "Язык", type: "language", hint: "уровень делает критерий фильтром HH" },
    { key: "salary_from", label: "Зарплата от", type: "number", hint: "валюта резюме" },
    { key: "salary_to", label: "Зарплата до", type: "number", hint: "валюта резюме" }
  ];

  var KEYWORD_CATEGORIES = [
    ["vertical", "Вертикаль"], ["traffic_source", "Источник трафика"],
    ["technology", "Технология"], ["keyword", "Другое"]
  ];

  var MODES = [["required", "Обязательный"], ["preferred", "Желательный"], ["ignore", "Не учитывать"]];

  var TIERS = {
    hot: { label: "HOT", bg: "#B91414", color: "#fff" },
    high: { label: "HIGH", bg: "#C9821F", color: "#fff" },
    medium: { label: "MEDIUM", bg: "#2C4E77", color: "#fff" },
    low: { label: "LOW", bg: "#E8E2E2", color: "#6A6161" }
  };

  var SOURCES = { hh: "HH", telegram: "Telegram" };

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
      : parsed.toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
  }

  /* ---------- загрузка ---------- */

  async function loadTemplates() {
    state.templates = await api.get("/recruitment/search-templates");
  }

  async function loadRuns(templateId) {
    var runs = await api.get("/recruitment/search-runs?search_template_id=" +
      encodeURIComponent(templateId));
    state.runs[templateId] = runs || [];
    return state.runs[templateId];
  }

  async function loadPending() {
    state.pendingLoading = true;
    render();
    var params = ["review_status=pending", "limit=200"];
    if (state.pendingTemplate) params.push("search_template_id=" + state.pendingTemplate);
    try {
      state.pending = await api.get("/recruitment/candidates?" + params.join("&"));
      state.pendingLoading = false;
    } catch (error) {
      state.pendingLoading = false;
      state.pending = [];
      showError(error.message);
    }
    render();
  }

  /* Входящие отклики запрашиваются пофамильно по источникам, а не «всё
     неразобранное»: иначе в них попадают резюме, найденные поиском. */
  async function loadIncoming() {
    state.incomingLoading = true;
    render();
    try {
      var batches = await Promise.all(INCOMING_SOURCES.map(function (source) {
        return api.get("/recruitment/candidates?review_status=pending&limit=200&source=" +
          encodeURIComponent(source));
      }));
      var rows = [];
      batches.forEach(function (batch) {
        (batch || []).forEach(function (row) { rows.push(row); });
      });
      state.incoming = rows;
      state.incomingLoading = false;
    } catch (error) {
      state.incomingLoading = false;
      state.incoming = [];
      showError(error.message);
    }
    render();
  }

  async function loadBoard() {
    state.boardLoading = true;
    render();
    try {
      var payload = await api.get("/recruitment/pipeline");
      state.board = payload;
      state.boardServiceDown = payload.service_available === false;
      state.boardLoading = false;
    } catch (error) {
      state.boardLoading = false;
      state.board = null;
      showError(error.message);
    }
    if (!state.users.length) {
      try {
        state.users = await api.get("/users/options");
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
    } catch (error) {
      showError(error.message);
    }
  }

  async function removeBoardRow(rowId) {
    if (!window.confirm("Убрать кандидата с доски найма?")) return;
    try {
      await api.delete("/recruitment/pipeline/" + rowId);
      await loadBoard();
    } catch (error) {
      showError(error.message);
    }
  }

  async function loadHh() {
    try {
      state.hh = await api.get("/recruitment/hh/status");
    } catch (error) {
      state.hh = { connected: false, error: error.message };
    }
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
          loadPending();
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
    (template.criteria || []).forEach(function (row) {
      criteria[row.key] = { mode: row.mode, value: row.value, weight: row.weight || 0 };
    });
    var keywords = (template.criteria || []).filter(function (row) {
      return CRITERIA.every(function (item) { return item.key !== row.key; });
    }).map(function (row) {
      return { key: row.key, value: row.value, mode: row.mode, weight: row.weight || 0 };
    });
    return {
      id: template.id || null,
      name: template.name || "",
      vacancy: template.crm_vacancy_id || "",
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
      name: String(editor.name || "").trim(),
      crm_vacancy_id: String(editor.vacancy || "").trim() || null,
      is_active: editor.active,
      auto_search_enabled: editor.auto,
      interval_minutes: editor.auto ? Number(editor.interval) : null,
      criteria: criteria
    };
  }

  async function saveEditor() {
    var payload = editorPayload();
    if (!payload.name) return showError("Укажите название шаблона");
    if (!payload.criteria.length) return showError("Добавьте хотя бы один критерий");
    if (payload.auto_search_enabled && !INTERVALS.some(function (row) {
      return row[0] === payload.interval_minutes;
    })) return showError("Интервал автопоиска: 15м/30м/1ч/2ч/6ч/12ч/24ч");
    try {
      if (state.editor.id) {
        await api.put("/recruitment/search-templates/" + state.editor.id, payload);
      } else {
        await api.post("/recruitment/search-templates", payload);
      }
      state.editor = null;
      showError("");
      await loadTemplates();
      render();
    } catch (error) {
      showError(error.message);
    }
  }

  async function deleteTemplate(templateId) {
    if (!window.confirm("Удалить шаблон? История запусков останется в сервисе.")) return;
    try {
      await api.delete("/recruitment/search-templates/" + templateId);
      if (state.selectedTemplateId === templateId) state.selectedTemplateId = "";
      await loadTemplates();
      render();
    } catch (error) {
      showError(error.message);
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
      if (decision === "added") {
        notify("Кандидат добавлен — он на этапе «Скрининг» во вкладке «Кандидаты»");
        // Доска устарела: перечитаем её при следующем открытии вкладки.
        state.board = null;
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
    document.querySelectorAll("[data-rec-tab]").forEach(function (button) {
      button.classList.toggle("rec-tab--active", button.getAttribute("data-rec-tab") === state.tab);
    });
    if (state.tab === "search") body.innerHTML = renderSearch();
    if (state.tab === "responses") body.innerHTML = renderResponses();
    if (state.tab === "candidates") body.innerHTML = renderCandidates();
    if (state.tab === "settings") body.innerHTML = renderSettings();
    var note = byId("recHeaderNote");
    note.textContent = state.tab === "search"
      ? "Шаблоны ищут резюме на HH по вашим критериям и присылают оценённых кандидатов."
      : state.tab === "responses"
        ? "Только те, кто откликнулся сам. Найденные поиском резюме — на вкладке «Поиск кандидатов»."
        : state.tab === "candidates"
          ? "Воронка найма: скрининг, интервью, оффер, найм или отказ. Этапы ведёт CRM."
          : "Статус подключения к HH. Поиск резюме работает только с подключённым аккаунтом.";
  }

  function renderSearch() {
    var cards = state.templates.map(function (template) {
      return templateCard(template);
    }).join("");
    var selected = state.templates.filter(function (row) {
      return row.id === state.selectedTemplateId;
    })[0];
    var detail = selected ? templateDetail(selected) : "";
    return '<div style="display:flex;align-items:center;justify-content:space-between;' +
      'gap:12px;flex-wrap:wrap;margin-bottom:14px">' +
      '<div style="font-size:12.5px;color:#857D7D;font-weight:700">Шаблоны поиска: ' +
      state.templates.length + "</div>" +
      '<button class="meta-action meta-action--primary" type="button" data-rec-new-template>' +
      "+ Новый шаблон</button></div>" +
      (state.templates.length
        ? '<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));' +
          'gap:12px">' + cards + "</div>" : emptyBlock("Шаблонов ещё нет — создайте первый, " +
          "чтобы поиск начал находить резюме на HH.")) +
      detail + (state.editor ? editorModal() : "");
  }

  function emptyBlock(text) {
    return '<div class="rec-card"><div class="rec-empty">' + escapeHtml(text) + "</div></div>";
  }

  function templateCard(template) {
    var active = template.id === state.selectedTemplateId;
    var lastRun = (state.runs[template.id] || [])[0];
    var auto = template.auto_search_enabled
      ? '<span class="rec-chip" style="background:#E4F7F0;color:#0E7350">Автопоиск · ' +
        intervalLabel(template.interval_minutes) + "</span>"
      : '<span class="rec-chip">Вручную</span>';
    return '<div class="rec-card" style="' + (active ? "border-color:#B91414;" : "") +
      'cursor:pointer" data-rec-open-template="' + escapeHtml(template.id) + '">' +
      '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:10px">' +
      '<div style="font-size:14px;font-weight:700">' + escapeHtml(template.name) + "</div>" + auto +
      "</div>" +
      '<div style="font-size:11.5px;color:#9B9292;font-weight:600;margin-top:6px">Вакансия: ' +
      escapeHtml(template.crm_vacancy_id || "—") + "</div>" +
      '<div style="font-size:11.5px;color:#9B9292;font-weight:600;margin-top:3px">Критериев: ' +
      (template.criteria || []).length + "</div>" +
      (lastRun ? '<div style="font-size:11.5px;color:#857D7D;font-weight:600;margin-top:8px">' +
        "Последний запуск: " + runStatusLabel(lastRun.status) + " · " +
        escapeHtml(dateLabel(lastRun.created_at)) + "</div>" : "") +
      '<div style="display:flex;gap:8px;margin-top:14px;flex-wrap:wrap">' +
      '<button class="meta-action meta-action--primary" type="button" data-rec-run="' +
      escapeHtml(template.id) + '">Запустить</button>' +
      '<button class="meta-action" type="button" data-rec-edit="' + escapeHtml(template.id) +
      '">Редактировать</button>' +
      '<button class="meta-action meta-action--danger" type="button" data-rec-delete="' +
      escapeHtml(template.id) + '">Удалить</button></div></div>';
  }

  function templateDetail(template) {
    var runs = state.runs[template.id] || [];
    var pending = state.pending.filter(function (row) {
      return !state.pendingTemplate || row.scores.some(function (score) {
        return score.search_template_id === state.selectedTemplateId;
      });
    });
    return '<div class="rec-card" style="margin-top:16px">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;gap:12px;' +
      'flex-wrap:wrap"><div style="font-size:15px;font-weight:700">' +
      escapeHtml(template.name) + ": неразобранные находки</div>" +
      '<button class="meta-action" type="button" data-rec-refresh-pending>Обновить</button></div>' +
      '<div style="margin-top:12px">' +
      (pending.length
        ? pending.map(candidateCard).join('<div style="height:12px"></div>')
        : '<div class="rec-empty">Пока никого. Запустите поиск — находки появятся здесь ' +
          "после завершения прогона.</div>") + "</div>" +
      '<div style="margin-top:18px"><div style="font-size:12.5px;color:#857D7D;font-weight:700;' +
      'margin-bottom:10px">История запусков</div>' + runsTable(runs) + "</div></div>";
  }

  function runsTable(runs) {
    if (!runs.length) return '<div class="rec-empty">Запусков ещё не было.</div>';
    return '<div style="overflow-x:auto"><table style="border-collapse:collapse;width:100%;' +
      'min-width:640px"><thead><tr style="border-bottom:1px solid #F0EBEB">' +
      ["Статус", "Триггер", "Начат", "Завершён", "Найдено", "Новых", "Прошли фильтры", "Выше порога"]
        .map(function (label) {
          return '<th class="meta-th meta-th--left">' + label + "</th>";
        }).join("") + "</tr></thead><tbody>" +
      runs.map(function (run) {
        var stats = run.stats || {};
        return "<tr style=\"border-bottom:1px solid #F7F4F4\">" +
          '<td class="meta-cell meta-cell--left">' + runStatusLabel(run.status) + "</td>" +
          '<td class="meta-cell meta-cell--left">' +
          (run.trigger === "scheduled" ? "Автопоиск" : "Вручную") + "</td>" +
          '<td class="meta-cell meta-cell--left">' + escapeHtml(dateLabel(run.started_at)) + "</td>" +
          '<td class="meta-cell meta-cell--left">' + escapeHtml(dateLabel(run.finished_at)) + "</td>" +
          "<td class=\"meta-cell\">" + (stats.found ?? "—") + "</td>" +
          "<td class=\"meta-cell\">" + (stats.new ?? "—") + "</td>" +
          "<td class=\"meta-cell\">" + (stats.passed_hard_filters ?? "—") + "</td>" +
          "<td class=\"meta-cell\">" + (stats.above_threshold ?? "—") + "</td></tr>";
      }).join("") + "</tbody></table></div>";
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
  function candidateCard(candidate) {
    var profile = candidate.parsed_profile || {};
    var score = (candidate.scores || [])[0];
    var tier = TIERS[(score || {}).tier] || TIERS.low;
    var sourceRows = candidate.sources || [];
    var sourceLabels = sourceRows.map(function (row) {
      return SOURCES[row.source] || row.source;
    }).join(" · ");
    var skills = (profile.skills || []).slice(0, 8).map(function (skill) {
      return '<span class="rec-chip">' + escapeHtml(skill) + "</span>";
    }).join("");
    return '<div class="rec-card" style="padding:16px">' +
      '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:12px;' +
      'flex-wrap:wrap">' +
      '<div style="min-width:0"><div style="font-size:14px;font-weight:700">' +
      escapeHtml(profile.position_title || "Позиция не указана") + "</div>" +
      '<div style="font-size:11.5px;color:#9B9292;font-weight:600;margin-top:4px">' +
      escapeHtml(sourceLabels || "—") + " · " + escapeHtml(dateLabel(candidate.first_seen_at)) +
      "</div></div>" +
      '<div style="display:flex;align-items:center;gap:8px">' +
      '<span class="rec-chip" style="background:' + tier.bg + ";color:" + tier.color + '">' +
      tier.label + " · " + ((score || {}).score ?? 0) + "</span></div></div>" +
      '<div style="display:flex;gap:14px;flex-wrap:wrap;margin-top:10px;font-size:12px;' +
      'color:#3A3030;font-weight:600">' +
      "<span>Гео: " + escapeHtml(profile.geo || "—") + "</span>" +
      "<span>Опыт: " + escapeHtml(experienceLabel(profile.total_experience_months)) + "</span>" +
      "<span>Ожидание: " + moneyLabel(profile.salary_expectation) + "</span></div>" +
      (skills ? '<div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:10px">' +
        skills + "</div>" : "") +
      (score && score.hard_filters_passed === false
        ? '<div style="margin-top:10px;background:#FCF1F1;border-radius:10px;padding:8px 12px;' +
          'font-size:11.5px;color:#B91414;font-weight:600">Не прошёл обязательные критерии — ' +
          "можно добавить вручную, если кандидат всё равно интересен.</div>" : "") +
      breakdownHtml(score) +
      cardFooter(candidate) + "</div>";
  }

  function breakdownHtml(score) {
    if (!score || !score.breakdown) return "";
    var rows = Object.entries(score.breakdown).map(function (pair) {
      var value = pair[1];
      var text = typeof value === "object" && value !== null
        ? JSON.stringify(value) : String(value);
      return "<div><b>" + escapeHtml(pair[0]) + "</b>: " + escapeHtml(text) + "</div>";
    }).join("");
    return '<details style="margin-top:10px"><summary style="cursor:pointer;font-size:11.5px;' +
      'color:#857D7D;font-weight:700">Разбор оценки</summary>' +
      '<div style="margin-top:8px;font-size:11.5px;color:#6A6161;font-weight:600;' +
      'line-height:1.7">' + rows + "</div></details>";
  }

  function cardFooter(candidate) {
    if (candidate.review_status === "added") {
      return '<div style="margin-top:12px;padding-top:10px;border-top:1px solid #F4F0F0;' +
        'font-size:11.5px;color:#857D7D;font-weight:600">Добавил: ' +
        escapeHtml(candidate.reviewed_by || "—") + " · " +
        escapeHtml(dateLabel(candidate.reviewed_at)) + "</div>";
    }
    return '<div style="display:flex;gap:8px;margin-top:12px;padding-top:12px;' +
      'border-top:1px solid #F4F0F0">' +
      '<button class="meta-action meta-action--primary" type="button" data-rec-add="' +
      escapeHtml(candidate.id) + '">Добавить в кандидаты</button>' +
      '<button class="meta-action" type="button" data-rec-skip="' + escapeHtml(candidate.id) +
      '">Пропустить</button></div>';
  }

  /* ---------- отклики ---------- */

  function renderResponses() {
    var templateOptions = [["", "Все шаблоны"]].concat(
      state.templates.map(function (row) { return [row.id, row.name]; })
    );
    var templateFilter = '<select class="meta-control meta-select" style="height:34px;' +
      'padding:0 11px" data-rec-pending-template>' +
      templateOptions.map(function (row) {
        return '<option value="' + escapeHtml(row[0]) + '"' +
          (row[0] === state.pendingTemplate ? " selected" : "") + ">" +
          escapeHtml(row[1]) + "</option>";
      }).join("") + "</select>";
    var rows = state.incoming;
    if (state.pendingTemplate) {
      rows = rows.filter(function (row) {
        return (row.scores || []).some(function (score) {
          return score.search_template_id === state.pendingTemplate;
        });
      });
    }
    return '<div class="rec-card"><div style="display:flex;align-items:center;gap:10px;' +
      'flex-wrap:wrap;margin-bottom:14px">' +
      '<span style="font-size:12.5px;font-weight:700">Входящих: ' + rows.length + "</span>" +
      '<div style="flex:1"></div>' + templateFilter +
      '<button class="meta-action" type="button" data-rec-refresh-incoming>Обновить</button>' +
      "</div>" +
      '<div class="rec-note" style="margin-bottom:14px">Здесь только те, кто откликнулся ' +
      "сам. Резюме, найденные шаблонами поиска, живут на вкладке «Поиск кандидатов» — " +
      "в откликах их быть не должно.</div>" +
      '<div id="recIncomingList">' +
      (state.incomingLoading
        ? '<div class="rec-empty">Загружаем…</div>'
        : rows.length
          ? rows.map(candidateCard).join('<div style="height:12px"></div>')
          : '<div class="rec-empty">Входящих откликов нет.<br>Telegram-отклики появятся ' +
            "после подключения бота к сервису рекрутинга, отклики с HH — после " +
            "реализации negotiations на его стороне.</div>") +
      "</div></div>";
  }

  /* ---------- кандидаты ---------- */

  var STAGE_TONES = {
    screening: { bg: "#F4F0F0", color: "#6A6161" },
    interview: { bg: "#EFF5FE", color: "#2C4E77" },
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
      return '<div class="rec-col"><div class="rec-col__head">' +
        '<span>' + escapeHtml(stage.label) + "</span>" +
        '<span class="rec-col__count">' + cards.length + "</span></div>" +
        '<div class="rec-col__body">' +
        (cards.length
          ? cards.map(boardCard).join("")
          : '<div class="rec-col__empty">Пусто</div>') +
        "</div></div>";
    }).join("");
    return '<div class="rec-card"><div style="display:flex;align-items:center;gap:10px;' +
      'flex-wrap:wrap;margin-bottom:14px">' +
      '<input class="meta-control" style="width:260px;height:34px;padding:0 11px" ' +
      'type="search" placeholder="Поиск по позиции, гео, ответственному" value="' +
      escapeHtml(state.boardSearch) + '" data-rec-board-search>' +
      (state.boardStage
        ? '<button class="meta-action" type="button" data-rec-stage-filter="">Все этапы</button>'
        : "") +
      '<div style="flex:1"></div>' +
      '<button class="meta-action" type="button" data-rec-refresh-board>Обновить</button>' +
      "</div>" +
      '<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:14px">' + stageFilter +
      "</div>" +
      (state.boardServiceDown
        ? '<div class="rec-note" style="border-color:#F1D9D9;color:#B91414;margin-bottom:14px">' +
          "Сервис рекрутинга не отвечает — доска показана по данным CRM. Новые " +
          "отобранные кандидаты появятся, когда связь восстановится.</div>"
        : "") +
      (board.items.length
        ? '<div class="rec-board">' + columns + "</div>"
        : '<div class="rec-empty">Пока никого. Отбирайте людей на вкладках «Поиск ' +
          "кандидатов» и «Отклики» кнопкой «Добавить в кандидаты» — они появятся " +
          "здесь на этапе «Скрининг».</div>") +
      "</div>";
  }

  function boardCard(row) {
    var tone = STAGE_TONES[row.stage] || STAGE_TONES.screening;
    var tier = TIERS[row.tier] || null;
    var stageOptions = (state.board.stages || []).map(function (stage) {
      return '<option value="' + stage.key + '"' +
        (stage.key === row.stage ? " selected" : "") + ">" + escapeHtml(stage.label) +
        "</option>";
    }).join("");
    var ownerOptions = '<option value="">Без ответственного</option>' +
      state.users.map(function (user) {
        return '<option value="' + escapeHtml(user.id) + '"' +
          (user.id === row.owner_id ? " selected" : "") + ">" +
          escapeHtml(user.name) + "</option>";
      }).join("");
    return '<div class="rec-cand" style="border-left:3px solid ' + tone.color + '">' +
      '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px">' +
      '<div style="min-width:0"><div class="rec-cand__title">' +
      escapeHtml(row.position_title || "Позиция не указана") + "</div>" +
      '<div class="rec-cand__meta">' +
      escapeHtml(SOURCES[row.source] || row.source || "—") +
      (row.geo ? " · " + escapeHtml(row.geo) : "") +
      (row.experience_months ? " · " + escapeHtml(experienceLabel(row.experience_months)) : "") +
      "</div></div>" +
      (tier
        ? '<span class="rec-chip" style="flex-shrink:0;background:' + tier.bg + ";color:" +
          tier.color + '">' + tier.label + "</span>"
        : "") + "</div>" +
      (row.salary_expectation
        ? '<div class="rec-cand__meta" style="margin-top:6px">Ожидание: ' +
          moneyLabel(row.salary_expectation) + "</div>"
        : "") +
      '<div style="display:grid;gap:6px;margin-top:10px">' +
      '<select class="meta-control meta-select" style="height:32px;padding:0 10px;font-size:12px" ' +
      'data-rec-stage="' + escapeHtml(row.id) + '">' + stageOptions + "</select>" +
      '<select class="meta-control meta-select" style="height:32px;padding:0 10px;font-size:12px" ' +
      'data-rec-owner="' + escapeHtml(row.id) + '">' + ownerOptions + "</select>" +
      "</div>" +
      '<textarea class="meta-control rec-cand__note" placeholder="Заметка" ' +
      'data-rec-note="' + escapeHtml(row.id) + '">' + escapeHtml(row.note || "") +
      "</textarea>" +
      '<div class="rec-cand__foot">' +
      '<span>' + escapeHtml(dateLabel(row.stage_changed_at)) + "</span>" +
      (row.external_url
        ? '<a href="' + escapeHtml(row.external_url) + '" target="_blank" ' +
          'rel="noopener noreferrer">Резюме</a>'
        : "<span></span>") +
      '<button class="rec-cand__drop" type="button" data-rec-board-remove="' +
      escapeHtml(row.id) + '" aria-label="Убрать с доски">×</button></div>' +
      "</div>";
  }

  /* ---------- настройки HH ---------- */

  function renderSettings() {
    var hh = state.hh || {};
    var connected = hh.connected === true;
    return '<div class="rec-card" style="max-width:520px">' +
      '<div style="display:flex;align-items:center;gap:14px">' +
      '<div style="width:44px;height:44px;border-radius:13px;display:flex;align-items:center;' +
      'justify-content:center;background:' + (connected ? "#E4F7F0" : "#FCF1F1") + '">' +
      '<svg width="22" height="22" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="9" stroke="' +
      (connected ? "#0E7350" : "#B91414") + '" stroke-width="2"/><path d="M8.5 12.2l2.3 2.3 4.7-4.8" stroke="' +
      (connected ? "#0E7350" : "#B91414") + '" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></div>' +
      "<div><div style=\"font-size:15px;font-weight:700\">HH-аккаунт " +
      (connected ? "подключён" : (hh.error ? "недоступен" : "не подключён")) + "</div>" +
      '<div style="font-size:12px;color:#857D7D;font-weight:600;margin-top:4px">' +
      (connected
        ? escapeHtml(hh.label || hh.account_id || "") +
          (hh.connected_at ? " · с " + escapeHtml(dateLabel(hh.connected_at)) : "")
        : escapeHtml(hh.error || "Поиск резюме работает только с подключённым аккаунтом.")) +
      "</div></div></div>" +
      (connected
        ? '<button class="meta-action" type="button" data-rec-hh-refresh style="margin-top:16px">' +
          "Проверить статус</button>"
        : '<button class="meta-action meta-action--primary" type="button" data-rec-hh-connect ' +
          'style="margin-top:16px">Подключить HH</button>') +
      '<div class="rec-note" style="margin-top:14px">Кнопка открывает страницу авторизации ' +
      "HH в новой вкладке. После подтверждения доступа вернитесь сюда — статус обновится " +
      "по кнопке «Проверить статус».</div></div>";
  }

  /* ---------- модалка шаблона ---------- */

  function editorModal() {
    var editor = state.editor;
    var structural = CRITERIA.map(function (item) {
      var mode = editorMode(item.key);
      var value = editorValue(item.key);
      var weightShown = mode === "preferred";
      var valueField;
      if (item.type === "select") {
        valueField = '<select class="meta-control meta-select" style="width:100%" ' +
          'data-rec-criterion="' + item.key + '" data-rec-field="value">' +
          '<option value="">—</option>' +
          item.options.map(function (option) {
            return '<option value="' + option[0] + '"' +
              (value === option[0] ? " selected" : "") + ">" + option[1] + "</option>";
          }).join("") + "</select>";
      } else if (item.type === "language") {
        var parts = String(value || "").split(":");
        valueField = '<div style="display:flex;gap:6px">' +
          '<select class="meta-control meta-select" style="width:60%" data-rec-criterion="' +
          item.key + '" data-rec-field="value">' +
          '<option value="">—</option><option value="eng"' +
          (parts[0] === "eng" ? " selected" : "") + ">English</option></select>" +
          '<select class="meta-control meta-select" style="width:40%" data-rec-criterion="' +
          item.key + '" data-rec-field="level">' +
          '<option value="">любой</option>' +
          ["a2", "b1", "b2", "c1", "c2"].map(function (level) {
            return '<option value="' + level + '"' +
              (parts[1] === level ? " selected" : "") + ">" + level.toUpperCase() + "</option>";
          }).join("") + "</select></div>";
      } else {
        valueField = '<input class="meta-control" style="width:100%;padding:0 11px" type="' +
          (item.type === "number" ? "number" : "text") + '" data-rec-criterion="' + item.key +
          '" data-rec-field="value" value="' + escapeHtml(value == null ? "" : value) + '">';
      }
      return '<div style="display:grid;grid-template-columns:150px 1fr 130px 92px;gap:8px;' +
        'align-items:center;padding:7px 0;border-bottom:1px solid #F7F4F4">' +
        '<span style="font-size:12px;font-weight:700;color:#3A3030" title="' +
        escapeHtml(item.hint || "") + '">' + item.label + "</span>" + valueField +
        '<select class="meta-control meta-select" style="height:34px;padding:0 9px;font-size:11.5px" ' +
        'data-rec-criterion="' + item.key + '" data-rec-field="mode">' +
        MODES.map(function (modeRow) {
          return '<option value="' + modeRow[0] + '"' + (mode === modeRow[0] ? " selected" : "") +
            ">" + modeRow[1] + "</option>";
        }).join("") + "</select>" +
        '<input class="meta-control" style="height:34px;padding:0 9px;font-size:11.5px" type="number" ' +
        'min="0" max="100" title="Вес в баллах (0–100)" placeholder="вес" value="' +
        (weightShown ? editorWeight(item.key) : "") + '"' +
        (weightShown ? "" : " disabled") +
        ' data-rec-criterion="' + item.key + '" data-rec-field="weight"></div>';
    }).join("");

    var keywords = editor.keywords.map(function (row, index) {
      return '<div style="display:grid;grid-template-columns:150px 1fr 130px 92px 34px;gap:8px;' +
        'align-items:center;padding:7px 0;border-bottom:1px solid #F7F4F4">' +
        '<select class="meta-control meta-select" style="height:34px;padding:0 9px;font-size:11.5px" ' +
        'data-rec-keyword="' + index + '" data-rec-field="key">' +
        KEYWORD_CATEGORIES.map(function (category) {
          return '<option value="' + category[0] + '"' + (row.key === category[0] ? " selected" : "") +
            ">" + category[1] + "</option>";
        }).join("") + "</select>" +
        '<input class="meta-control" style="height:34px;padding:0 11px" type="text" ' +
        'placeholder="Например: iGaming, Facebook, Keitaro" value="' + escapeHtml(row.value || "") +
        '" data-rec-keyword="' + index + '" data-rec-field="value">' +
        '<select class="meta-control meta-select" style="height:34px;padding:0 9px;font-size:11.5px" ' +
        'data-rec-keyword="' + index + '" data-rec-field="mode">' +
        MODES.map(function (modeRow) {
          return '<option value="' + modeRow[0] + '"' + (row.mode === modeRow[0] ? " selected" : "") +
            ">" + modeRow[1] + "</option>";
        }).join("") + "</select>" +
        '<input class="meta-control" style="height:34px;padding:0 9px;font-size:11.5px" type="number" ' +
        'min="0" max="100" placeholder="вес" value="' +
        (row.mode === "preferred" ? row.weight || 40 : "") + '"' +
        (row.mode === "preferred" ? "" : " disabled") +
        ' data-rec-keyword="' + index + '" data-rec-field="weight">' +
        '<button class="meta-action meta-action--danger" type="button" data-rec-keyword-drop="' +
        index + '" title="Убрать">×</button></div>';
    }).join("");

    return '<div style="position:fixed;inset:0;z-index:60;background:rgba(7,5,5,.42);' +
      'display:flex;align-items:center;justify-content:center;padding:24px">' +
      '<div role="dialog" aria-modal="true" style="width:100%;max-width:760px;background:#fff;' +
      'border-radius:20px;padding:26px;box-shadow:0 26px 60px rgba(30,20,20,.28);' +
      'max-height:90vh;overflow:auto">' +
      '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px">' +
      "<div><div style=\"font-family:'Alumni Sans',Inter,sans-serif;font-size:21px;" +
      'font-weight:700;letter-spacing:-.3px">' +
      (editor.id ? "Редактировать шаблон" : "Новый шаблон поиска") + "</div>" +
      '<p style="font-size:12px;color:#6A6161;font-weight:500;margin-top:6px;line-height:1.55">' +
      "«Обязательный» отсекает неподходящих резюме, «Желательный» только добавляет баллы. " +
      "Вес решает, насколько критерий важен в итоговой оценке.</p></div>" +
      '<button type="button" data-rec-editor-close aria-label="Закрыть" style="flex-shrink:0;' +
      'width:34px;height:34px;border:1px solid #EBE6E6;background:#fff;border-radius:10px;' +
      'color:#857D7D;font-size:16px;font-weight:700;cursor:pointer">×</button></div>' +
      '<div style="display:grid;grid-template-columns:1fr 220px;gap:12px;margin-top:16px">' +
      '<label class="meta-field"><span>Название шаблона</span>' +
      '<input class="meta-control" style="width:100%;padding:0 13px" type="text" ' +
      'placeholder="Media Buyer (Facebook, iGaming)" value="' + escapeHtml(editor.name) +
      '" data-rec-editor-field="name"></label>' +
      '<label class="meta-field"><span>Вакансия (ссылка/ID)</span>' +
      '<input class="meta-control" style="width:100%;padding:0 13px" type="text" ' +
      'placeholder="vac-123" value="' + escapeHtml(editor.vacancy) + '" ' +
      'data-rec-editor-field="vacancy"></label></div>' +
      '<div style="margin-top:16px;font-size:12.5px;font-weight:700;color:#3A3030">Критерии</div>' +
      '<div style="margin-top:6px">' + structural + "</div>" +
      '<div style="display:flex;align-items:center;justify-content:space-between;margin-top:14px">' +
      '<div style="font-size:12.5px;font-weight:700;color:#3A3030">Ключевые слова</div>' +
      '<button class="meta-action" type="button" data-rec-keyword-add>+ Добавить</button></div>' +
      '<div style="margin-top:6px">' +
      (keywords || '<div class="rec-empty" style="padding:14px">Ключевых слов нет — ' +
        "добавьте то, что должно встретиться в резюме.</div>") + "</div>" +
      '<div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-top:16px;' +
      'padding-top:14px;border-top:1px solid #F0EBEB">' +
      '<label class="meta-switch"><input type="checkbox" data-rec-editor-field="auto"' +
      (editor.auto ? " checked" : "") +
      '><span class="meta-switch__box"></span><span>Автопоиск</span></label>' +
      '<select class="meta-control meta-select" style="width:170px;height:36px;padding:0 11px" ' +
      'data-rec-editor-field="interval"' + (editor.auto ? "" : " disabled") + ">" +
      INTERVALS.map(function (row) {
        return '<option value="' + row[0] + '"' + (Number(editor.interval) === row[0] ? " selected" : "") +
          ">" + row[1] + "</option>";
      }).join("") + "</select></div>" +
      '<div style="display:flex;justify-content:flex-end;gap:10px;margin-top:20px">' +
      '<button class="meta-action" type="button" data-rec-editor-close>Отмена</button>' +
      '<button class="meta-action meta-action--primary" type="button" data-rec-editor-save>Сохранить</button>' +
      "</div></div></div>";
  }

  /* ---------- события ---------- */

  function onTabClick(button) {
    state.tab = button.getAttribute("data-rec-tab");
    showError("");
    if (state.tab === "search" && !state.pending.length && !state.pendingLoading) loadPending();
    if (state.tab === "responses" && !state.incoming.length && !state.incomingLoading) {
      loadIncoming();
    }
    if (state.tab === "candidates" && !state.board && !state.boardLoading) loadBoard();
    if (state.tab === "settings" && !state.hh) loadHh();
    render();
  }

  document.addEventListener("click", function (event) {
    var target = event.target;
    function closest(selector) {
      return target.closest ? target.closest(selector) : null;
    }
    var tab = closest("[data-rec-tab]");
    if (tab) return onTabClick(tab);

    if (closest("[data-rec-new-template]")) {
      state.editor = emptyEditor(null);
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
      loadRuns(state.selectedTemplateId).then(render).catch(function () {});
      loadPending();
      return render();
    }
    if (closest("[data-rec-refresh-pending]")) return loadPending();

    var add = closest("[data-rec-add]");
    if (add) return reviewCandidate(add.getAttribute("data-rec-add"), "added");
    var skip = closest("[data-rec-skip]");
    if (skip) return reviewCandidate(skip.getAttribute("data-rec-skip"), "skipped");

    if (closest("[data-rec-refresh-incoming]")) return loadIncoming();
    if (closest("[data-rec-refresh-board]")) return loadBoard();
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

    if (closest("[data-rec-keyword-add]")) {
      state.editor.keywords.push(
        { key: "vertical", value: "", mode: "preferred", weight: 40 });
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
      return render();
    }
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
      if (name === "auto") {
        state.editor.auto = editorField.checked;
        return render();
      }
      if (name === "interval") {
        state.editor.interval = Number(editorField.value);
        return;
      }
      state.editor[name] = editorField.value;
      return;
    }
    var pendingTemplate = closest("[data-rec-pending-template]");
    if (pendingTemplate) {
      state.pendingTemplate = pendingTemplate.value;
      return loadPending();
    }
  });

  document.addEventListener("input", function (event) {
    var target = event.target;
    var search = target.closest ? target.closest("[data-rec-board-search]") : null;
    if (search) {
      state.boardSearch = search.value;
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
    try {
      await loadTemplates();
    } catch (error) {
      showError(error.message);
    }
    render();
  }

  boot();
})();
