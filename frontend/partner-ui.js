/* Интеграции с ПП (Partner Integration Service) — раздел «Настройки».

   Депозиты из партнёрок раскладываются в финансы баеров по тегам: сервис
   хранит факты «дата — оффер — тег — депозиты», а CRM раскладывает их в
   книги. Здесь только управление: интеграции, привязки офферов, синк. */
(function () {
  "use strict";

  var api = window.CelestialAPI;
  var state = {
    items: [], loading: false, form: null, runs: null, error: "",
    // Реестр «тег → баер»: без него депозит некуда адресовать, и раскладка
    // раньше писала его в книгу каждому баеру оффера.
    tags: null, pending: [],
    // Глубина ручного синка в днях, по интеграции. Семь дней покрывают
    // обычную задержку партнёрки, но после простоя или правки тегов нужно
    // перетянуть больше — поэтому период выбирается, а не зашит.
    syncDays: {},
    // Платформы, под которые у сервиса есть шаблон коннектора. Список приходит
    // с бэкенда: соответствие «платформа → шаблон» живёт в его настройках.
    platforms: []
  };

  /* Подтверждение в стиле CRM, а не окном браузера: оно рисуется у верхней
     кромки и подписано адресом сервера — на фоне интерфейса это выглядит как
     сообщение постороннего сайта. Контракт тот же, ответ приходит промисом. */
  function askConfirm(options) {
    if (window.CelestialShell && window.CelestialShell.confirm) {
      return window.CelestialShell.confirm(options);
    }
    return Promise.resolve(window.confirm(options.message || options.title));
  }

  function byId(id) { return document.getElementById(id); }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
  }

  var CLOSE_ICON = '<svg width="17" height="17" viewBox="0 0 24 24">' +
    '<path d="m6 6 12 12M18 6 6 18" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round"/></svg>';

  var SYNC_ICON = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none">' +
    '<path d="M4 12a8 8 0 0 1 14-5M20 12a8 8 0 0 1-14 5M18 3v4h-4M6 21v-4h4" ' +
    'stroke="currentColor" stroke-width="2"/></svg>';

  /* Счётчик на вкладке: подключений у ПП обычно одно-два, и по цифре сразу
     видно, заведено ли вообще что-нибудь. */
  function renderTabCount() {
    var total = String((state.items || []).length);
    ["settingsPartnersTabCount", "settingsPartnersTotal"].forEach(function (id) {
      var node = byId(id);
      if (node) node.textContent = total;
    });
  }

  function render() {
    var root = byId("partnerIntegrationsRoot");
    if (!root) return;
    renderTabCount();
    if (state.loading) {
      root.innerHTML = '<div class="settings-skeleton" aria-hidden="true">' +
        "<i></i><i></i></div>";
      return;
    }
    if (state.error) {
      root.innerHTML = '<div class="integration-card"><div class="settings-empty" ' +
        'style="border-color:#F1D9D9"><b style="color:#B91414">Интеграции не загрузились</b>' +
        "<span>" + escapeHtml(state.error) + "</span>" +
        '<button type="button" class="settings-btn" style="margin-top:14px" data-pi-retry>' +
        "Повторить</button></div></div>";
      return;
    }
    renderTagsButton();
    if (!(state.items || []).length) {
      root.innerHTML = '<div class="integration-card"><div class="settings-empty">' +
        "<b>Интеграций с ПП пока нет</b><span>Создайте первую — и депозиты будут сами " +
        "прилетать в финансы байеров по тегам.</span>" +
        '<button type="button" class="settings-btn settings-btn--primary" ' +
        'style="margin-top:14px" data-pi-create>+ Новая интеграция</button></div></div>';
    } else {
      root.innerHTML = state.items.map(cardHtml).join("");
    }
    // Модалки живут вне сетки карточек: в гриде с gap они были лишним рядом,
    // а перерисовка списка их роняла.
    var host = modalHost();
    host.innerHTML = state.form ? formModal()
        : state.runs != null ? runsModal()
          : state.tags ? tagsModal() : "";
  }

  /* Отдельный узел под модалки — создаётся один раз рядом с панелью. */
  function modalHost() {
    var host = byId("partnerIntegrationModals");
    if (host) return host;
    host = document.createElement("div");
    host.id = "partnerIntegrationModals";
    (byId("partnerIntegrationsPanel") || document.body).appendChild(host);
    return host;
  }

  function statusChip(item) {
    if (item.last_sync_status === "success") {
      return '<span class="settings-chip settings-chip--ok"><i></i>Синк прошёл</span>';
    }
    if (item.last_sync_status === "failed") {
      return '<span class="settings-chip settings-chip--bad"><i></i>Ошибка синка</span>';
    }
    return '<span class="settings-chip settings-chip--idle"><i></i>Ещё не синкалась</span>';
  }

  function cardHtml(item) {
    // Привязка живёт в самом оффере: здесь только видно, что она есть.
    var linked = (item.offers || []);
    var chips = linked.length
      ? linked.slice(0, 12).map(function (offer) {
        return '<span class="data-pill" title="' + escapeHtml(offer.name) + '">' +
          escapeHtml(offer.external_offer_id) + "</span>";
      }).join("") + (linked.length > 12
        ? '<span class="data-pill">+' + (linked.length - 12) + "</span>" : "")
      : '<span style="font-size:11px;color:#C6BDBD;font-weight:600">' +
        "Ни у одного оффера не заполнен ID у ПП — данные не попадут в финансы. " +
        "Заполните его в разделе «Оффера».</span>";
    var initials = escapeHtml((item.partner_name || "PP").slice(0, 2).toUpperCase());
    return '<article class="integration-card">' +
      '<div class="pi-head"><div class="pi-id">' +
      '<div class="pi-mark">' + initials + "</div>" +
      '<div style="min-width:0"><div class="settings-heading pi-name">' +
      escapeHtml(item.partner_name) + "</div>" +
      '<div class="pi-sub">' + escapeHtml(item.base_url) + " · " +
      escapeHtml(item.name) + "</div></div></div>" +
      '<div style="display:flex;align-items:center;gap:8px">' + statusChip(item) +
      '<details class="row-actions" style="position:relative"><summary style="width:34px;' +
      'height:34px;border-radius:9px;display:flex;align-items:center;justify-content:center;' +
      'cursor:pointer"><svg width="18" height="18"><circle cx="5" cy="9" r="1.6" fill="#9B9292"/>' +
      '<circle cx="12" cy="9" r="1.6" fill="#9B9292"/><circle cx="19" cy="9" r="1.6" ' +
      'fill="#9B9292"/></svg></summary><div class="row-menu">' +
      '<button type="button" data-pi-edit="' + escapeHtml(item.id) + '">Редактировать</button>' +
      '<button type="button" data-pi-delete="' + escapeHtml(item.id) + '">Удалить</button>' +
      "</div></details></div></div>" +
      '<div class="settings-tiles" style="margin-top:18px">' +
      '<div class="settings-tile"><b>Последний синк</b><span>' +
      escapeHtml(formatMoment(item.last_sync_at)) + "</span></div>" +
      '<div class="settings-tile"><b>Офферов с ID</b><span>' + linked.length + "</span></div>" +
      "</div>" +
      '<div style="margin-top:16px"><div class="pi-section-title">ID офферов у партнёрки</div>' +
      '<div class="pi-pills">' + chips + "</div></div>" +
      '<div class="pi-actions">' +
      '<button class="settings-btn settings-btn--primary" type="button" data-pi-sync="' +
      escapeHtml(item.id) + '">' + SYNC_ICON + "<span>Синхронизировать за " +
      syncDays(item.id) + " " + dayWord(syncDays(item.id)) + "</span></button>" +
      '<select class="settings-btn pi-period" data-pi-days="' + escapeHtml(item.id) +
      '" aria-label="Период синхронизации">' +
      SYNC_PERIODS.map(function (days) {
        return '<option value="' + days + '"' +
          (days === syncDays(item.id) ? " selected" : "") + ">" +
          days + " " + dayWord(days) + "</option>";
      }).join("") + "</select>" +
      '<button type="button" class="settings-btn" data-pi-runs="' + escapeHtml(item.id) +
      '">История синков</button></div></article>';
  }

  var SYNC_PERIODS = [7, 14, 30];

  function syncDays(id) {
    var picked = Number(state.syncDays[id]);
    return SYNC_PERIODS.indexOf(picked) >= 0 ? picked : 7;
  }

  function dayWord(days) {
    var tail = days % 100;
    if (tail < 11 || tail > 14) {
      tail = days % 10;
      if (tail === 1) return "день";
      if (tail >= 2 && tail <= 4) return "дня";
    }
    return "дней";
  }

  function formatMoment(value) {
    if (!value) return "—";
    var parsed = new Date(value);
    return isNaN(parsed.getTime()) ? String(value)
      : window.CelestialTime.format(parsed, { day: "2-digit", month: "2-digit",
        hour: "2-digit", minute: "2-digit" });
  }

  /* Шапка и подвал модалки — те же, что у «Подключения Keitaro» на этом же
     экране: иначе окно партнёрки выглядит пришедшим из другого продукта. */
  function modalHead(title, subtitle) {
    return '<div style="padding:22px 24px 18px;border-bottom:1px solid #EBE6E6;display:flex;' +
      'justify-content:space-between;gap:18px"><div><h2 class="settings-heading" ' +
      'style="font-size:20px">' + escapeHtml(title) + "</h2>" +
      (subtitle
        ? '<div style="font-size:11.5px;color:#9B9292;margin-top:5px">' +
          escapeHtml(subtitle) + "</div>"
        : "") +
      '</div><button type="button" data-pi-close aria-label="Закрыть" style="width:34px;' +
      'height:34px;flex-shrink:0;border:0;border-radius:9px;background:#F7F4F4;color:#857D7D">' +
      CLOSE_ICON + "</button></div>";
  }

  function modalFoot(buttons) {
    return '<div style="padding:16px 24px 22px;border-top:1px solid #EBE6E6;display:flex;' +
      'justify-content:flex-end;gap:10px;flex-wrap:wrap">' + buttons + "</div>";
  }

  function field(label, control, hint) {
    return "<label><span class=\"field-label\">" + escapeHtml(label) + "</span>" + control +
      (hint
        ? '<span style="display:block;font-size:10.5px;color:#9B9292;margin-top:5px;' +
          'line-height:1.5">' + escapeHtml(hint) + "</span>"
        : "") + "</label>";
  }

  function selectInput(name, value, options) {
    return '<select class="form-input" data-pi-form="' + name + '">' +
      '<option value="">Выберите платформу</option>' +
      options.map(function (option) {
        return '<option value="' + escapeHtml(option.value) + '"' +
          (option.value === value ? " selected" : "") + ">" +
          escapeHtml(option.label) + "</option>";
      }).join("") + "</select>";
  }

  function textInput(name, value, placeholder, type) {
    return '<input class="form-input" data-pi-form="' + name + '" type="' + (type || "text") +
      '" placeholder="' + escapeHtml(placeholder || "") + '" value="' + escapeHtml(value || "") +
      '"' + (type === "password" ? ' autocomplete="off"' : "") + ">";
  }

  /* ---------- модалка создания/редактирования ---------- */

  function formModal() {
    var f = state.form;
    return '<div class="modal-backdrop open">' +
      '<div class="modal-card" role="dialog" aria-modal="true">' +
      modalHead(f.id ? "Изменить интеграцию" : "Новая интеграция с ПП") +
      '<div style="padding:22px 24px;display:grid;gap:17px">' +
      field("Партнёрка", textInput("partner_name", f.partner_name, "Jugabet CO")) +
      field("Платформа", selectInput("platform", f.platform, state.platforms)) +
      field("API адрес", textInput("base_url", f.base_url, "https://api.jugabet.com")) +
      field(
        "API ключ" + (f.id ? " (пусто — не менять)" : ""),
        textInput("api_key", "", "", "password")
      ) +
      "</div>" +
      modalFoot(
        '<button type="button" class="settings-btn settings-btn--tall" data-pi-close>' +
        "Отмена</button>" +
        '<button type="button" class="settings-btn settings-btn--tall settings-btn--primary" ' +
        "data-pi-save>Сохранить</button>"
      ) + "</div></div>";
  }

  function runsModal() {
    var integration = integrationById(state.runs);
    var runs = (integration && integration.runs) || [];
    var rows = runs.map(function (run) {
      var chip = run.status === "success"
        ? '<span class="settings-chip settings-chip--ok"><i></i>успех</span>'
        : run.status === "failed"
          ? '<span class="settings-chip settings-chip--bad"><i></i>ошибка</span>'
          : '<span class="settings-chip settings-chip--warn"><i></i>идёт</span>';
      return "<tr><td>" + escapeHtml(formatMoment(run.created_at)) + "</td>" +
        '<td style="white-space:nowrap">' + escapeHtml(run.date_from) + " — " +
        escapeHtml(run.date_to) + "</td>" +
        "<td>" + chip +
        (run.error
          ? '<div style="font-size:11px;color:#B91414;font-weight:600;margin-top:5px;' +
            'line-height:1.45;white-space:normal;min-width:240px">' +
            escapeHtml(run.error) + "</div>"
          : "") + "</td>" +
        "<td>" + run.records_upserted + "</td>" +
        "<td>" + (run.trigger === "scheduled" ? "по расписанию" : "вручную") + "</td></tr>";
    }).join("");
    return '<div class="modal-backdrop open">' +
      '<div class="modal-card" role="dialog" aria-modal="true" style="width:min(720px,100%)">' +
      modalHead("История синков", "Последние запуски этой интеграции") +
      '<div style="padding:8px 24px 22px"><div style="overflow-x:auto">' +
      '<table class="pi-runs"><thead><tr><th>Дата</th><th>Период</th><th>Статус</th>' +
      "<th>Записей</th><th>Триггер</th></tr></thead><tbody>" +
      (rows ||
        '<tr><td colspan="5" style="padding:26px 12px;text-align:center;color:#9B9292;' +
        'font-weight:600">Синков ещё не было</td></tr>') +
      "</tbody></table></div></div>" +
      modalFoot(
        '<button type="button" class="settings-btn settings-btn--tall" data-pi-close>' +
        "Закрыть</button>"
      ) + "</div></div>";
  }


  /* ---------- реестр «тег → баер» ----------
   *
   * Сервис партнёрок отдаёт тег сырой строкой из sub1/sub2 и про наших людей
   * ничего не знает. Кому принадлежит трафик — решает CRM, и это единственное,
   * что не даёт депозиту разойтись по книгам всех баеров оффера.
   */

  function renderTagsButton() {
    var host = byId("partnerIntegrationsActions");
    var header = byId("partnerIntegrationCreate");
    if (!host || !header) return;
    var existing = byId("partnerTagsOpen");
    var waiting = (state.pending || []).length;
    // Кнопки нет, пока всё раскладывается: это не настройка, а сигнал о
    // зависших деньгах.
    if (!waiting) {
      if (existing && existing.parentNode) existing.parentNode.removeChild(existing);
      return;
    }
    var label = "Теги без строки <span class=\"pi-badge\">" + waiting + "</span>";
    if (existing) {
      existing.innerHTML = label;
      return;
    }
    var button = document.createElement("button");
    button.type = "button";
    button.id = "partnerTagsOpen";
    button.className = "settings-btn";
    button.style.height = "40px";
    button.innerHTML = label;
    host.insertBefore(button, header);
  }

  async function loadTags() {
    try {
      state.pending = (await api.get("/partner-integrations/tags/pending")) || [];
    } catch (error) {
      state.pending = [];
    }
  }

  function tagsModal() {
    var pending = (state.pending || []).map(function (row) {
      var why = row.reason === "ambiguous"
        ? "заведён сразу у нескольких баеров"
        : "такой строки в финансах нет";
      return '<div class="pi-pending">' +
        '<div style="min-width:0"><div class="pi-pending__tag">' +
        escapeHtml(row.tag) + "</div>" +
        '<div class="pi-pending__meta">' + why +
        (row.offer_name ? " · оффер «" + escapeHtml(row.offer_name) + "»" : "") +
        " · фактов " + row.facts_count + " · депозитов " + (row.deposits_total || 0) +
        "</div></div></div>";
    }).join("");
    return '<div class="modal-backdrop open">' +
      '<div class="modal-card" role="dialog" aria-modal="true" style="width:min(640px,100%)">' +
      modalHead(
        "Теги без строки в финансах",
        "Депозиты пришли, но класть их некуда"
      ) +
      '<div class="modal-scroll" style="padding:22px 24px">' +
      '<div class="rec-note" style="font-size:12px;color:#6A6161;font-weight:600;' +
      'line-height:1.6;margin-bottom:14px">Ничего настраивать здесь не нужно. ' +
      "Заведите тег в книге баера под нужным оффером — и следующий синк за тот " +
      "же период разложит эти депозиты сам. Оффер находится по своему ID у ПП, " +
      "который стоит в разделе «Оффера».</div>" +
      (pending ||
        '<div style="font-size:12px;color:#9B9292;font-weight:600;padding:12px 0">' +
        "Всё разложено — зависших тегов нет.</div>") +
      "</div>" +
      modalFoot(
        '<button type="button" class="settings-btn settings-btn--tall" data-pi-close>' +
        "Понятно</button>"
      ) + "</div></div>";
  }

  function integrationById(id) {
    return (state.items || []).filter(function (row) { return row.id === id; })[0] || null;
  }

  async function load() {
    state.loading = true;
    state.error = "";
    render();
    try {
      state.items = await api.get("/partner-integrations");
      if (!state.platforms.length) {
        // Справочник запрашиваем один раз: он не меняется между открытиями.
        state.platforms = await api.get("/partner-integrations/platforms");
      }
      state.loading = false;
    } catch (error) {
      state.loading = false;
      state.error = error && error.message ? error.message : "Не удалось загрузить";
    }
    render();
  }

  document.addEventListener("click", function (event) {
    var target = event.target.closest ? event.target : null;
    if (!target) return;
    function closest(selector) { return target.closest(selector); }
    if (!closest("[data-pi-edit]") && !closest("[data-pi-delete]") &&
      !closest("[data-pi-sync]") &&
      !closest("[data-pi-runs]") && !closest("[data-pi-close]") &&
      !closest("[data-pi-save]") && !closest("#partnerIntegrationCreate") &&
      !closest("[data-pi-create]") && !closest("[data-pi-retry]") &&
      !closest("#partnerTagsOpen")) return;
    if (closest("[data-pi-retry]")) return load();
    if (closest("#partnerTagsOpen")) {
      return loadTags().then(function () {
        state.tags = true;
        render();
      });
    }
    var edit = closest("[data-pi-edit]");
    if (edit) {
      var item = integrationById(edit.getAttribute("data-pi-edit"));
      if (item) { state.form = Object.assign({}, item, { api_key: "" }); render(); }
      return;
    }
    var del = closest("[data-pi-delete]");
    if (del) {
      var id = del.getAttribute("data-pi-delete");
      askConfirm({
        title: "Удалить интеграцию?",
        message: "Привязки офферов к этой партнёрке удалятся вместе с ней.",
        confirmLabel: "Удалить",
        danger: true
      }).then(function (confirmed) {
        if (!confirmed) return;
        return api.delete("/partner-integrations/" + id).then(load);
      }).catch(function (error) {
        notify(error && error.message || "Не удалось удалить");
      });
      return;
    }
    var sync = closest("[data-pi-sync]");
    if (sync) {
      var syncId = sync.getAttribute("data-pi-sync");
      sync.disabled = true;
      var today = window.CelestialTime.today();
      var iso = window.CelestialTime.dateISO;
      // Период включает сегодняшний день, поэтому шагов назад на один меньше.
      var from = new Date(today.getTime() - (syncDays(syncId) - 1) * 86400000);
      api.post("/partner-integrations/" + syncId + "/sync", {
        date_from: iso(from), date_to: iso(today)
      }).then(function (result) {
        var text = "Синк прошёл: записей " + result.records_upserted;
        if (result.records_pending) {
          text += ", без баера " + result.records_pending +
            " — привяжите теги в «Тегах баеров»";
        }
        if (result.records_skipped) text += ", пропущено " + result.records_skipped;
        notify(text);
        return loadTags().then(load);
      }).catch(function (error) {
        notify(error && error.message || "Синк не удался");
        sync.disabled = false;
      });
      return;
    }
    var runsBtn = closest("[data-pi-runs]");
    if (runsBtn) {
      var rId = runsBtn.getAttribute("data-pi-runs");
      var integration = integrationById(rId);
      state.runs = rId;
      if (integration && !integration.runs) {
        api.get("/partner-integrations/" + rId + "/runs").then(function (runs) {
          integration.runs = runs || [];
          render();
        }).catch(function () { integration.runs = []; render(); });
      }
      return render();
    }
    if (closest("#partnerIntegrationCreate") || closest("[data-pi-create]")) {
      // Ни названия, ни ID интеграции на сервисе форма не спрашивает: первое —
      // подпись партнёрки, второе выдаёт сам сервис, когда бэкенд заводит там
      // интеграцию по шаблону платформы. Адреса сервиса в форме тоже нет — он
      // и ключ к нему живут в .env бэкенда.
      state.form = {
        partner_name: "", platform: "", base_url: "", api_key: "", is_enabled: true
      };
      return render();
    }
    if (closest("[data-pi-close]")) {
      state.form = null; state.runs = null; state.tags = null;
      return render();
    }
    if (closest("[data-pi-save]")) return saveForm();
  });

  document.addEventListener("change", function (event) {
    var field = event.target.closest ? event.target.closest("[data-pi-form]") : null;
    if (field && state.form) state.form[field.getAttribute("data-pi-form")] = field.value;
    var period = event.target.closest ? event.target.closest("[data-pi-days]") : null;
    if (period) {
      state.syncDays[period.getAttribute("data-pi-days")] = Number(period.value);
      render();
    }
  });

  function formValue(name) {
    var el = document.querySelector('[data-pi-form="' + name + '"]');
    return el ? el.value : "";
  }

  async function saveForm() {
    var f = state.form;
    if (!f) return;
    var payload = {
      partner_name: formValue("partner_name"),
      platform: formValue("platform"),
      base_url: formValue("base_url"),
      api_key: formValue("api_key")
    };
    try {
      if (f.id) {
        await api.patch("/partner-integrations/" + f.id, payload);
      } else {
        await api.post("/partner-integrations", payload);
      }
      state.form = null;
      await load();
    } catch (error) {
      window.alert(error && error.message || "Не удалось сохранить");
    }
  }

  function notify(message) {
    if (window.CelestialShell && window.CelestialShell.notify) {
      window.CelestialShell.notify({ title: message });
      return;
    }
    window.alert(message);
  }

  async function boot() {
    if (!byId("partnerIntegrationsRoot")) return;
    await load();
    // Неразобранные теги — это зависшие деньги: счётчик должен быть виден
    // сразу, не дожидаясь, пока кто-то откроет реестр.
    await loadTags();
    render();
  }

  boot();
})();
