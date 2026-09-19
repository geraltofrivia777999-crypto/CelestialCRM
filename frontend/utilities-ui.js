/*
 * Утилиты: Alert и CAP — ТЗ 9.
 *
 * Раздел пишет в чат команды от её имени, поэтому у него две особенности.
 *
 * Первая — токен бота сюда не возвращается никогда: с сервера приходит только
 * имя бота и когда его проверяли. Показывать токен в поле «на всякий случай»
 * значит показывать его каждому, кто заглянет через плечо.
 *
 * Вторая — прежде чем правило начнёт писать в чат, его можно проверить: у
 * уведомления есть «Что сейчас», у канала — пробное сообщение. Алерт, который
 * впервые срабатывает в бою, обычно срабатывает не так, как ждали.
 */
(function () {
  "use strict";

  var api = window.CelestialAPI;

  var state = {
    user: null,
    canManage: false,
    tab: "alerts",
    bot: null,
    reference: null,
    channels: [],
    alerts: [],
    caps: [],
    capSummary: null,
    capStatus: "",
    capMetric: "",
    // Журнал: "" — все записи, "true" — ушедшие, "false" — застрявшие.
    eventFilter: "",
    form: null,
    // Черновик правила и текущий шаг мастера: форма перерисовывается целиком
    // на каждом шаге, поэтому значения держатся здесь, а не в DOM.
    alert: null,
    alertStep: 0,
    // Куда вернуться после того, как канал наконец создан.
    afterChannel: null
  };

  function byId(id) { return document.getElementById(id); }

  /* Числа приходят с бэкенда как Decimal и сериализуются с хвостом нулей —
     «50.0000» вместо «50». Читать такое в таблице тяжело, а точность до
     четвёртого знака в лимитах и порогах не нужна. */
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

  function hasPermission(user, code) {
    if (!user || !user.role) return false;
    return (user.role.permissions || []).some(function (permission) {
      return permission.code === "*" || permission.code === code;
    });
  }

  function moment(value) {
    if (!value) return "—";
    var date = new Date(value);
    if (isNaN(date.getTime())) return "—";
    return window.CelestialTime.format(date, {
      day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit"
    });
  }

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

  /* ---------- общая форма ---------- */

  function optionsHtml(items, selected) {
    return items.map(function (item) {
      var value = item.code !== undefined ? item.code : item.id;
      return '<option value="' + escapeHtml(value) + '"' +
        (String(value) === String(selected) ? " selected" : "") + ">" +
        escapeHtml(item.label || item.name) + "</option>";
    }).join("");
  }

  function fieldHtml(field) {
    var span = field.half ? "" : "grid-column:1/-1;";
    var hint = field.hint
      ? '<span style="display:block;font-size:11px;color:#9B9292;font-weight:600;' +
        'margin-top:6px;line-height:1.5">' + escapeHtml(field.hint) + "</span>"
      : "";
    if (field.type === "select") {
      return '<label style="' + span + '" data-field-wrap="' + escapeHtml(field.name) +
        '"><span class="field-label">' + escapeHtml(field.label) + "</span>" +
        '<select class="form-input settings-select" data-field="' + escapeHtml(field.name) +
        '">' + optionsHtml(field.options || [], field.value) + "</select>" + hint + "</label>";
    }
    if (field.type === "checkbox") {
      return '<label style="' + span + 'display:flex;align-items:center;gap:9px;' +
        'font-size:12.5px;font-weight:600" data-field-wrap="' + escapeHtml(field.name) + '">' +
        '<input type="checkbox" data-field="' + escapeHtml(field.name) + '"' +
        (field.value ? " checked" : "") + ">" + escapeHtml(field.label) + "</label>";
    }
    if (field.type === "textarea") {
      return '<label style="' + span + '" data-field-wrap="' + escapeHtml(field.name) +
        '"><span class="field-label">' + escapeHtml(field.label) + "</span>" +
        '<textarea class="form-input" data-field="' + escapeHtml(field.name) +
        '" style="height:88px;padding:11px 13px;line-height:1.5">' +
        escapeHtml(field.value || "") + "</textarea>" + hint + "</label>";
    }
    return '<label style="' + span + '" data-field-wrap="' + escapeHtml(field.name) +
      '"><span class="field-label">' + escapeHtml(field.label) + "</span>" +
      '<input class="form-input" type="' + (field.type || "text") + '" data-field="' +
      escapeHtml(field.name) + '" value="' + escapeHtml(
        field.value === null || field.value === undefined ? "" : field.value
      ) + '"' +
      (field.placeholder ? ' placeholder="' + escapeHtml(field.placeholder) + '"' : "") +
      (field.step ? ' step="' + field.step + '"' : "") +
      (field.min !== undefined ? ' min="' + field.min + '"' : "") + ">" + hint + "</label>";
  }

  function readForm() {
    var values = {};
    Array.prototype.forEach.call(
      byId("utilModalBody").querySelectorAll("[data-field]"),
      function (input) {
        values[input.getAttribute("data-field")] =
          input.type === "checkbox" ? input.checked : input.value;
      }
    );
    return values;
  }

  function openForm(config) {
    state.form = config;
    byId("utilModalTitle").textContent = config.title;
    byId("utilModalSubtitle").textContent = config.subtitle || "";
    byId("utilModalBody").innerHTML = (config.fields || []).map(fieldHtml).join("") +
      (config.html || "") +
      (config.note
        ? '<div style="grid-column:1/-1;background:#F7F4F4;border-radius:12px;padding:12px 14px;' +
          'font-size:11.5px;color:#6A6161;font-weight:600;line-height:1.6">' +
          escapeHtml(config.note) + "</div>"
        : "");
    byId("utilModalDelete").style.display = config.onDelete ? "" : "none";
    formError("");
    byId("utilModal").classList.add("open");
    if (config.after) config.after(byId("utilModalBody"));
    var first = byId("utilModalBody").querySelector("[data-field]");
    if (first) first.focus();
  }

  function closeForm() {
    byId("utilModal").classList.remove("open");
    state.form = null;
  }

  function formError(message) {
    var host = byId("utilModalError");
    host.textContent = message || "";
    host.style.display = message ? "" : "none";
  }

  async function submitForm() {
    if (!state.form) return;
    formError("");
    byId("utilModalSave").disabled = true;
    try {
      await state.form.onSave(readForm());
      closeForm();
    } catch (error) {
      // Переход к следующему шагу мастера — не ошибка: форма остаётся открытой
      // и молчит, иначе под кнопкой появлялся бы красный текст на каждом «Далее».
      if (!(error && error.step)) {
        formError(error && error.message ? error.message : "Не удалось сохранить");
      }
    } finally {
      byId("utilModalSave").disabled = false;
    }
  }

  /* ---------- бот ---------- */

  function renderBot() {
    var host = byId("utilBotState");
    var button = byId("utilBotSetup");
    if (state.bot && state.bot.connected) {
      host.innerHTML = '<span style="width:7px;height:7px;border-radius:50%;' +
        'background:#16B57F"></span>@' + escapeHtml(state.bot.username || "бот") +
        ' <span style="color:#B4ABAB;font-weight:600">· проверен ' +
        escapeHtml(moment(state.bot.checked_at)) + "</span>";
      button.textContent = "Заменить токен";
    } else {
      host.innerHTML = '<span style="width:7px;height:7px;border-radius:50%;' +
        'background:#C9821F"></span>Бот не подключён';
      button.textContent = "Подключить Telegram";
    }
    button.style.display = state.canManage ? "" : "none";
  }

  function openBotForm() {
    openForm({
      title: state.bot && state.bot.connected ? "Токен бота" : "Подключение Telegram",
      subtitle: "Токен выдаёт @BotFather после команды /newbot",
      fields: [
        { name: "token", label: "Токен бота", placeholder: "123456:AA…" }
      ],
      note: "Токен сохранится, только если Telegram его примет. Дальше он хранится " +
        "зашифрованным и обратно не показывается — по нему можно писать в любой чат, " +
        "куда бота добавили.",
      onSave: async function (values) {
        if (!String(values.token || "").trim()) throw new Error("Укажите токен");
        await api.request("/utilities/bot", {
          method: "PUT",
          body: JSON.stringify({ token: values.token.trim() })
        });
        await loadBot();
      },
      onDelete: state.bot && state.bot.connected ? async function () {
        if (!(await askConfirm({
          title: "Отключить бота?",
          message: "Уведомления перестанут уходить, пока не подключите нового.",
          confirmLabel: "Отключить",
          danger: true
        }))) return;
        await api.delete("/utilities/bot");
        closeForm();
        await loadBot();
      } : null
    });
  }

  /* ---------- каналы ---------- */

  function renderChannels() {
    byId("utilChannelsCount").textContent = state.channels.length;
    var host = byId("utilChannels");
    if (!state.channels.length) {
      host.innerHTML = emptyHtml(
        "Каналов нет.",
        "Канал — это чат Telegram, куда придут уведомления. Добавьте бота в чат и " +
        "укажите его chat_id."
      );
      return;
    }
    host.innerHTML = state.channels.map(function (channel) {
      var off = channel.status !== "active";
      return card(off,
        escapeHtml(channel.name),
        "chat_id " + escapeHtml(channel.chat_id) +
          (channel.thread_id ? " · тема " + escapeHtml(channel.thread_id) : ""),
        off ? "Выключен" : "Активен", off,
        state.canManage
          ? '<button type="button" data-channel-test="' + escapeHtml(channel.id) +
            '" class="row-btn">Проверить</button>' +
            '<button type="button" data-channel-edit="' + escapeHtml(channel.id) +
            '" class="row-btn">Изменить</button>'
          : "");
    }).join("");
  }

  function openChannelForm(channelId) {
    var channel = channelId
      ? state.channels.filter(function (row) { return row.id === channelId; })[0]
      : { name: "", chat_id: "", thread_id: "", status: "active" };
    if (!channel) return;
    openForm({
      title: channelId ? "Канал" : "Новый канал",
      subtitle: "Чат Telegram, куда уходят уведомления",
      fields: [
        { name: "name", label: "Название", value: channel.name, half: true },
        { name: "status", label: "Статус", type: "select", half: true,
          value: channel.status,
          options: [{ code: "active", label: "Активен" }, { code: "inactive", label: "Выключен" }] },
        { name: "chat_id", label: "chat_id", value: channel.chat_id, half: true,
          hint: "У группы он отрицательный, например -1001234567890" },
        { name: "thread_id", label: "Тема супергруппы (необязательно)", half: true,
          value: channel.thread_id || "" }
      ],
      html: '<div style="grid-column:1/-1;border:1px solid #EBE6E6;border-radius:12px;' +
        'padding:13px 15px;display:flex;align-items:center;gap:12px;flex-wrap:wrap">' +
        '<div style="flex:1;min-width:200px;font-size:11.5px;color:#6A6161;font-weight:600;' +
        'line-height:1.5">Уже добавили бота в чат? Найдём chat_id сами.</div>' +
        '<button type="button" id="utilFindChats" style="height:36px;border:1px solid #E5DFDF;' +
        'border-radius:9px;background:#fff;padding:0 13px;font-size:11.5px;font-weight:700">' +
        "Найти чаты</button></div>" +
        '<div id="utilChatList" style="grid-column:1/-1;display:none"></div>',
      note: "Бот должен быть добавлен в чат и иметь право писать. Проверить связь " +
        "можно кнопкой «Проверить» — она отправит туда пробное сообщение.",
      onSave: async function (values) {
        var payload = {
          name: String(values.name || "").trim(),
          chat_id: String(values.chat_id || "").trim(),
          thread_id: String(values.thread_id || "").trim() || null,
          status: values.status
        };
        if (!payload.name) throw new Error("Укажите название канала");
        if (!payload.chat_id) throw new Error("Укажите chat_id");
        if (channelId) await api.patch("/utilities/channels/" + channelId, payload);
        else await api.post("/utilities/channels", payload);
        await loadChannels();
        var retry = state.afterChannel;
        state.afterChannel = null;
        if (retry) window.setTimeout(retry, 0);
      },
      onDelete: channelId ? async function () {
        if (!(await askConfirm({
          title: "Удалить канал?",
          message: "«" + channel.name + "». Уведомления, которые в него писали, " +
            "тоже будут удалены.",
          confirmLabel: "Удалить",
          danger: true
        }))) return;
        await api.delete("/utilities/channels/" + channelId);
        closeForm();
        await Promise.all([loadChannels(), loadAlerts(), loadCaps()]);
      } : null
    });
  }

  async function offerChannel(reason, retry) {
    if (!state.canManage) {
      notify({
        title: "Каналов нет",
        message: reason + " Добавить его может администратор или тимлид."
      });
      return;
    }
    var ready = await askConfirm({
      title: "Сначала добавим канал",
      message: reason + " Канал — это чат Telegram, куда придут уведомления. " +
        "Создадим его сейчас?",
      confirmLabel: "Добавить канал"
    });
    if (!ready) return;
    state.afterChannel = retry;
    openChannelForm(null);
  }

  // Bot API не отдаёт список чатов бота, только последние события. Поэтому
  // «Найти чаты» показывает те чаты, где бота видели за сутки: обычно это
  // ровно тот, куда его только что добавили.
  async function findChats() {
    var host = byId("utilChatList");
    var button = byId("utilFindChats");
    if (!host || !button) return;
    button.disabled = true;
    button.textContent = "Ищем…";
    host.style.display = "";
    host.innerHTML = '<div style="font-size:11.5px;color:#9B9292;font-weight:600">' +
      "Спрашиваем Telegram…</div>";
    try {
      var result = await api.get("/utilities/bot/chats");
      var chats = result.chats || [];
      if (!chats.length) {
        host.innerHTML = '<div style="border:1px dashed #DDD5D5;border-radius:11px;' +
          'padding:13px 15px;font-size:11.5px;color:#6A6161;font-weight:600;line-height:1.6">' +
          "Telegram не показал ни одного чата. Он помнит события только сутки и " +
          "только пока у бота не настроен вебхук.<br>Напишите в нужную группу любое " +
          "сообщение и нажмите «Найти чаты» ещё раз — либо введите chat_id вручную.</div>";
        return;
      }
      host.innerHTML = '<div style="display:grid;gap:8px">' + chats.map(function (chat) {
        return '<button type="button" data-chat-pick="' + escapeHtml(chat.chat_id) +
          '" data-chat-title="' + escapeHtml(chat.title) +
          '" style="display:flex;align-items:center;gap:10px;border:1px solid #E5DFDF;' +
          'border-radius:11px;background:#fff;padding:11px 13px;text-align:left">' +
          '<span style="flex:1;min-width:0;font-size:12.5px;font-weight:700">' +
          escapeHtml(chat.title) + "</span>" +
          '<span style="font-size:11px;color:#9B9292;font-weight:600">' +
          escapeHtml(chat.chat_id) + "</span>" +
          (chat.known
            ? '<span style="font-size:10.5px;color:#857D7D;font-weight:700;background:#F2EDED;' +
              'border-radius:7px;padding:3px 7px">уже добавлен</span>'
            : "") + "</button>";
      }).join("") + "</div>";
    } catch (error) {
      host.innerHTML = '<div style="font-size:11.5px;color:#B91414;font-weight:700">' +
        escapeHtml(error && error.message ? error.message : "Не удалось получить чаты") +
        "</div>";
    } finally {
      button.disabled = false;
      button.textContent = "Найти чаты";
    }
  }

  async function testChannel(channelId) {
    try {
      await api.post("/utilities/channels/" + channelId + "/test", {});
      notify({ title: "Сообщение отправлено", message: "Проверьте чат в Telegram." });
    } catch (error) {
      notify({
        title: "Telegram не принял сообщение",
        message: error && error.message ? error.message : ""
      });
    }
  }

  /* ---------- Alert: мастер из трёх шагов (ТЗ 9.1) ---------- */

  /*
   * Два вида уведомления и ничего между ними.
   *
   * «Уведомление по депозитам» идёт от событий: пришла продажа в журнале
   * конверсий Keitaro — ушло сообщение. Выбирать тип события не из чего, он
   * один, поэтому шага «какой триггер» здесь нет.
   *
   * «Отчёт» идёт от времени: наступил слот расписания — ушла сводка за период.
   * Область у него всегда вся команда, поэтому выбора области тоже нет.
   *
   * Шага «Лимиты» и «Проверка» нет намеренно: пробную отправку делает кнопка
   * «Тест» прямо на шаге действий, где написан текст, — там она и нужна.
   */

  var ALERT_STEPS = [
    { code: "trigger", label: "Триггер" },
    { code: "filters", label: "Условия" },
    { code: "actions", label: "Действия" }
  ];
  var ALERT_KINDS = [
    {
      code: "deposit",
      title: "Уведомление по депозитам",
      hint: "Сообщение на каждую продажу из Keitaro"
    },
    {
      code: "report",
      title: "Отчёт",
      hint: "Сводка за период по расписанию"
    }
  ];

  function flatConditions(tree) {
    var items = [];
    function collect(node) {
      (node && node.items || []).forEach(function (item) {
        if (item.items) collect(item);
        else items.push(JSON.parse(JSON.stringify(item)));
      });
    }
    collect(tree);
    return { op: tree && tree.op === "or" ? "or" : "and", items: items };
  }

  function customScheduleTime(schedule) {
    var match = /^daily_((?:[01]\d|2[0-3]))([0-5]\d)$/.exec(schedule || "");
    return match ? match[1] + ":" + match[2] : "";
  }

  function alertDraft(rule) {
    var schedule = rule ? rule.schedule : "daily_09";
    var scheduleTime = customScheduleTime(schedule);
    // Старые составные пресеты продолжают работать до редактирования, но в
    // новой форме превращаются в одно понятное ежедневное время.
    if (schedule === "twice") scheduleTime = "09:00";
    if (schedule === "workdays_10") scheduleTime = "10:00";
    return {
      id: rule ? rule.id : null,
      name: rule ? rule.name : "",
      status: rule ? rule.status : "active",
      kind: rule ? rule.kind : "deposit",
      channel_id: rule ? rule.channel_id : (state.channels[0] || {}).id,
      thread_id: rule ? (rule.thread_id || "") : "",
      conditions: rule && rule.conditions && rule.conditions.items
        ? flatConditions(rule.conditions)
        : { op: "and", items: [] },
      window: rule ? rule.window : "today",
      schedule_mode: scheduleTime ? "custom" : schedule,
      schedule_time: scheduleTime || "09:00",
      // Часовой пояс команды. +3 по умолчанию — по нему живёт большинство,
      // а «по серверу» означало бы UTC и отчёт в шесть утра.
      timezone: rule ? rule.timezone : "Europe/Moscow",
      message_template: rule ? (rule.message_template || "") : ""
    };
  }

  function alertMacros() {
    return (state.reference.macros || {})[state.alert.kind] || [];
  }

  function stepsHtml() {
    return '<div class="wiz-steps">' + ALERT_STEPS.map(function (step, index) {
      var done = index < state.alertStep;
      var active = index === state.alertStep;
      return '<button type="button" class="wiz-step' +
        (active ? " wiz-step--on" : "") + (done ? " wiz-step--done" : "") +
        '" data-alert-step="' + index + '"><span class="wiz-step__num">' +
        (done ? "✓" : index + 1) + "</span>" + escapeHtml(step.label) + "</button>";
    }).join('<span class="wiz-line"></span>') + "</div>";
  }

  function kindsHtml() {
    return '<div class="wiz-cards">' + ALERT_KINDS.map(function (kind) {
      var on = state.alert.kind === kind.code;
      return '<button type="button" class="wiz-card' + (on ? " wiz-card--on" : "") +
        '" data-alert-kind="' + kind.code + '"><span class="wiz-card__title">' +
        escapeHtml(kind.title) + "</span>" +
        '<span class="wiz-card__hint">' + escapeHtml(kind.hint) + "</span>" +
        (on ? '<span class="wiz-card__tick">✓</span>' : "") + "</button>";
    }).join("") + "</div>";
  }

  /* Условия депозитного уведомления — строками, как в конструкторе правил.
     Полей ровно два, но одного списка галочек не хватало: «всё, кроме этой
     группы» ими не выразить, а список офферов растёт каждую неделю. */

  function conditionFields() { return state.reference.condition_fields || []; }
  function conditionOperators() { return state.reference.condition_operators || []; }

  function operatorTakesValues(code) {
    var found = conditionOperators().filter(function (item) {
      return item.code === code;
    })[0];
    return !found || found.values;
  }

  function sourceOptions(field) {
    var meta = conditionFields().filter(function (item) {
      return item.code === field;
    })[0];
    return (meta && state.reference[meta.source]) || [];
  }

  function newCondition() {
    var field = (conditionFields()[0] || {}).code || "campaign_group";
    return { field: field, operator: "in", values: [], text: "" };
  }

  function condNodeAt(path) {
    if (!path) return state.alert.conditions;
    return path.split(".").reduce(function (node, index) {
      return node.items[Number(index)];
    }, state.alert.conditions);
  }

  function condRemoveAt(path) {
    var parts = path.split(".");
    var last = Number(parts.pop());
    condNodeAt(parts.join(".")).items.splice(last, 1);
  }

  function valuesControlHtml(item, path) {
    if (!operatorTakesValues(item.operator)) {
      return '<input class="form-input cnd-value" data-cnd="text" data-path="' + path +
        '" value="' + escapeHtml(item.text || "") + '" placeholder="часть названия">';
    }
    var options = sourceOptions(item.field);
    if (!options.length) {
      return '<span class="cnd-empty">Список пуст — появится после ' +
        "синхронизации Keitaro</span>";
    }
    // Значение одно: фильтр читают как «поле — условие — значение», и набор
    // галочек в третьей клетке этой фразы не соответствовал. Сам список рисует
    // общий компонент выпадающих списков — с поиском и панелью во весь экран.
    var picked = String((item.values || [])[0] || "");
    return '<select class="form-input settings-select cnd-value" data-cnd="value" ' +
      'data-path="' + path + '"><option value="">Выберите значение</option>' +
      options.map(function (option) {
        return '<option value="' + escapeHtml(option.code) + '"' +
          (String(option.code) === picked ? " selected" : "") + ">" +
          escapeHtml(option.label) + "</option>";
      }).join("") + "</select>";
  }

  function conditionRowHtml(item, path) {
    return '<div class="cnd-row" data-path="' + path + '">' +
      '<div class="cnd-labels"><span>Поле</span><span>Условие</span>' +
      '<span>Значение</span><span></span></div><div class="cnd-controls">' +
      '<select class="form-input settings-select cnd-field" data-cnd="field" ' +
      'data-path="' + path + '">' +
      conditionFields().map(function (meta) {
        return '<option value="' + escapeHtml(meta.code) + '"' +
          (meta.code === item.field ? " selected" : "") + ">" +
          escapeHtml(meta.label) + "</option>";
      }).join("") + "</select>" +
      '<select class="form-input settings-select cnd-op" data-cnd="operator" ' +
      'data-path="' + path + '">' +
      conditionOperators().map(function (meta) {
        return '<option value="' + escapeHtml(meta.code) + '"' +
          (meta.code === item.operator ? " selected" : "") + ">" +
          escapeHtml(meta.label) + "</option>";
      }).join("") + "</select><div class=\"cnd-value-wrap\">" +
      valuesControlHtml(item, path) + "</div>" +
      '<button type="button" class="cnd-drop" data-cnd-act="remove" data-path="' +
      path + '" aria-label="Удалить фильтр" title="Удалить фильтр">' +
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3' +
      'M7 7l1 13h8l1-13M10 11v5M14 11v5"></path></svg></button></div></div>';
  }

  function renderConditions() {
    var host = byId("utilConditions");
    if (!host) return;
    var tree = state.alert.conditions;
    host.innerHTML = '<div class="cnd-intro"><div><div class="cnd-title">' +
      'Фильтры события</div><div class="cnd-subtitle">Дополнительные условия для ' +
      'депозитов — необязательно</div></div><div class="cnd-join">' +
      '<span>Объединение условий:</span>' +
      '<button type="button" data-cnd-act="set-op" data-cnd-op="and" aria-pressed="' +
      (tree.op === "and") + '" class="' + (tree.op === "and" ? "is-on" : "") +
      '">AND (все)</button>' +
      '<button type="button" data-cnd-act="set-op" data-cnd-op="or" aria-pressed="' +
      (tree.op === "or") + '" class="' + (tree.op === "or" ? "is-on" : "") +
      '">OR (любое)</button></div></div>' +
      '<div class="cnd-list">' + (tree.items.length
        ? tree.items.map(function (item, index) {
          return conditionRowHtml(item, String(index));
        }).join("")
        : '<div class="cnd-none">Фильтров нет — придут любые депозиты</div>') +
      "</div>" +
      '<div class="cnd-actions">' +
      '<button type="button" class="cnd-add" data-cnd-act="add-cond" data-path="">' +
      "+ Добавить фильтр</button></div>";
  }

  function handleConditionClick(event) {
    var button = event.target.closest ? event.target.closest("[data-cnd-act]") : null;
    if (!button) return false;
    event.preventDefault();
    var action = button.getAttribute("data-cnd-act");
    var path = button.getAttribute("data-path");
    if (action === "set-op") state.alert.conditions.op = button.getAttribute("data-cnd-op");
    else if (action === "add-cond") condNodeAt(path).items.push(newCondition());
    else if (action === "remove") condRemoveAt(path);
    renderConditions();
    return true;
  }

  function handleConditionChange(event) {
    var input = event.target.closest ? event.target.closest("[data-cnd]") : null;
    if (!input) return false;
    var node = condNodeAt(input.getAttribute("data-path"));
    var key = input.getAttribute("data-cnd");
    if (key === "value") {
      // На сервер значения по-прежнему едут списком: оператор «в списке» умеет
      // несколько, просто выбирают из них по одному.
      node.values = input.value ? [input.value] : [];
      return true;
    }
    node[key] = input.value;
    // Поле сменилось — прежние значения относились к другому справочнику.
    if (key === "field") node.values = [];
    if (key === "field" || key === "operator") renderConditions();
    return true;
  }

  function conditionsPayload(node) {
    return {
      op: node.op || "and",
      items: (node.items || []).map(function (item) {
        if (item.items) return conditionsPayload(item);
        var takes = operatorTakesValues(item.operator);
        return {
          field: item.field,
          operator: item.operator,
          values: takes ? (item.values || []) : [],
          text: takes ? "" : String(item.text || "").trim()
        };
      })
    };
  }

  function conditionsProblem(node) {
    var bad = null;
    (node.items || []).forEach(function (item) {
      if (bad) return;
      if (item.items) { bad = conditionsProblem(item); return; }
      if (operatorTakesValues(item.operator)) {
        if (!(item.values || []).length) bad = "Выберите значения в условии";
      } else if (!String(item.text || "").trim()) {
        bad = "Укажите, что должно содержаться";
      }
    });
    return bad;
  }

  function stepTriggerHtml() {
    var report = state.alert.kind === "report";
    var schedules = (state.reference.schedules || []).concat([
      { code: "custom", label: "Ежедневно в своё время" }
    ]);
    return '<label class="wiz-field"><span>Название</span>' +
      '<input class="form-input" data-alert-field="name" value="' +
      escapeHtml(state.alert.name) + '" placeholder="Например: депозиты команды"></label>' +
      '<div class="wiz-field"><span>Тип уведомления</span>' + kindsHtml() + "</div>" +
      (report
        ? '<div class="wiz-pair">' +
          '<label class="wiz-field"><span>Расписание</span>' +
          '<select class="form-input settings-select" data-alert-field="schedule_mode">' +
          optionsHtml(schedules, state.alert.schedule_mode) +
          "</select>" + (state.alert.schedule_mode === "custom"
            ? '<span class="wiz-schedule-time"><span>Время отправки</span>' +
              '<input class="form-input" type="time" data-alert-field="schedule_time" ' +
              'value="' + escapeHtml(state.alert.schedule_time) + '"></span>'
            : "") + "</label>" +
          '<label class="wiz-field"><span>Период отчёта</span>' +
          '<select class="form-input settings-select" data-alert-field="window">' +
          optionsHtml(state.reference.windows || [], state.alert.window) +
          "</select></label>" +
          '<label class="wiz-field"><span>Часовой пояс</span>' +
          '<select class="form-input settings-select" data-alert-field="timezone">' +
          timezoneOptionsHtml(state.alert.timezone) + "</select></label>" +
          '<label class="wiz-field"><span>Статус</span>' +
          '<select class="form-input settings-select" data-alert-field="status">' +
          optionsHtml(
            [{ code: "active", label: "Активно" }, { code: "inactive", label: "Выключено" }],
            state.alert.status
          ) + "</select></label></div>"
        : '<div class="wiz-pair">' +
          '<label class="wiz-field"><span>Часовой пояс</span>' +
          '<select class="form-input settings-select" data-alert-field="timezone">' +
          timezoneOptionsHtml(state.alert.timezone) + "</select></label>" +
          '<label class="wiz-field"><span>Статус</span>' +
          '<select class="form-input settings-select" data-alert-field="status">' +
          optionsHtml(
            [{ code: "active", label: "Активно" }, { code: "inactive", label: "Выключено" }],
            state.alert.status
          ) + "</select></label></div>");
  }

  function stepFiltersHtml() {
    if (state.alert.kind === "report") {
      return '<div class="wiz-note">У отчёта фильтров нет — он считает всю команду.</div>';
    }
    return '<div class="wiz-field"><div id="utilConditions" class="cnd-tree"></div></div>';
  }

  /* Макросы живут рядом с полем, но открываются только по запросу: постоянная
     панель отнимала у самого сообщения почти половину шага. */

  var SUB_ID = /^sub_id_\d+$/;

  function macroChip(macro) {
    return '<button type="button" class="mcr" data-alert-macro="' +
      escapeHtml(macro.code) + '" data-macro-description="' +
      escapeHtml(macro.label) + '" data-macro-search="' +
      escapeHtml((macro.code + " " + macro.label).toLowerCase()) +
      '" title="' + escapeHtml(macro.label) + '">' +
      '<span class="mcr__code">{' + escapeHtml(macro.code) + "}</span></button>";
  }

  function macroSectionHtml(title, macros) {
    if (!macros.length) return "";
    return '<div class="mcr-section"><div class="mcr-title">' + escapeHtml(title) +
      '</div><div class="mcr-grid">' + macros.map(macroChip).join("") + "</div></div>";
  }

  function macrosHtml() {
    var all = alertMacros();
    var main = all.filter(function (macro) { return !SUB_ID.test(macro.code); });
    var subs = all.filter(function (macro) { return SUB_ID.test(macro.code); });
    var reportPeriod = state.alert.kind === "report"
      ? main.filter(function (macro) { return macro.code === "period"; }) : [];
    var reportMetrics = state.alert.kind === "report"
      ? main.filter(function (macro) { return macro.code !== "period"; }) : [];
    var sections = state.alert.kind === "report"
      ? macroSectionHtml("Период / время", reportPeriod) +
        macroSectionHtml("Метрики", reportMetrics)
      : macroSectionHtml("Данные конверсии", main) +
        macroSectionHtml("Метки sub_id", subs);
    return '<div class="mcr-picker"><button type="button" class="mcr-toggle" ' +
      'data-alert-macro-toggle aria-expanded="false" aria-controls="utilMacroPopover">' +
      "Макросы</button>" +
      '<div class="mcr-popover" id="utilMacroPopover" hidden>' +
      '<input class="form-input mcr-search" data-alert-macro-search ' +
      'placeholder="Поиск макроса…">' +
      '<div class="mcr-scroll">' + sections + "</div>" +
      '<div class="mcr-description" id="utilMacroDescription" hidden></div></div></div>';
  }

  function stepActionsHtml() {
    return '<div class="wiz-pair">' +
      '<label class="wiz-field"><span>Канал</span>' +
      '<select class="form-input settings-select" data-alert-field="channel_id">' +
      optionsHtml(state.channels.map(function (channel) {
        return { code: channel.id, label: channel.name };
      }), state.alert.channel_id) + "</select></label>" +
      '<label class="wiz-field"><span>Тема супергруппы</span>' +
      '<input class="form-input" data-alert-field="thread_id" value="' +
      escapeHtml(state.alert.thread_id) + '" placeholder="для супергрупп с темами">' +
      "</label></div>" +
      '<div class="wiz-field"><div class="wiz-message-head"><span>Текст сообщения</span>' +
      macrosHtml() + "</div>" +
      '<textarea class="form-input wiz-area" data-alert-field="message_template" ' +
      'placeholder="Напишите сообщение и добавьте нужные макросы">' +
      escapeHtml(state.alert.message_template) + "</textarea></div>" +
      '<div class="wiz-test"><button type="button" class="row-btn" id="utilAlertTest">' +
      "Отправить тест в чат</button>" +
      '<span class="wiz-hint" id="utilAlertTestState"></span></div>';
  }

  function timezoneOptionsHtml(selected) {
    var zones = ["Europe/Moscow", "Europe/Kyiv", "Europe/Warsaw", "Europe/London", "UTC"];
    try {
      if (Intl.supportedValuesOf) {
        var all = Intl.supportedValuesOf("timeZone") || [];
        if (all.length) zones = all;
      }
    } catch (error) { /* старый браузер — остаётся короткий список */ }
    var current = selected || "Europe/Moscow";
    if (zones.indexOf(current) < 0) zones = [current].concat(zones);
    return zones.map(function (zone) {
      return '<option value="' + escapeHtml(zone) + '"' +
        (zone === current ? " selected" : "") + ">" + escapeHtml(zone) + "</option>";
    }).join("");
  }

  function renderAlertStep() {
    var body = byId("utilModalBody");
    var step = ALERT_STEPS[state.alertStep].code;
    body.innerHTML = stepsHtml() +
      (step === "trigger" ? stepTriggerHtml()
        : step === "filters" ? stepFiltersHtml() : stepActionsHtml());
    if (step === "filters" && state.alert.kind !== "report") renderConditions();
    byId("utilModalSave").textContent =
      state.alertStep === ALERT_STEPS.length - 1 ? "Сохранить" : "Далее →";
    byId("utilModalCancel").textContent = state.alertStep ? "← Назад" : "Отмена";
  }

  function readAlertStep() {
    var body = byId("utilModalBody");
    Array.prototype.forEach.call(
      body.querySelectorAll("[data-alert-field]"),
      function (input) { state.alert[input.getAttribute("data-alert-field")] = input.value; }
    );
    Array.prototype.forEach.call(
      body.querySelectorAll("[data-alert-list]"),
      function (host) {
        state.alert[host.getAttribute("data-alert-list")] = Array.prototype.map.call(
          host.querySelectorAll("[data-alert-pick]:checked"),
          function (input) { return input.getAttribute("data-alert-pick"); }
        );
      }
    );
  }

  function scheduleValue() {
    if (state.alert.schedule_mode !== "custom") return state.alert.schedule_mode;
    return /^([01]\d|2[0-3]):[0-5]\d$/.test(state.alert.schedule_time || "")
      ? "daily_" + state.alert.schedule_time.replace(":", "") : "";
  }

  function alertPayload() {
    return {
      name: String(state.alert.name || "").trim(),
      status: state.alert.status,
      kind: state.alert.kind,
      channel_id: state.alert.channel_id,
      thread_id: String(state.alert.thread_id || "").trim() || null,
      conditions: state.alert.kind === "report"
        ? { op: "and", items: [] }
        : conditionsPayload(state.alert.conditions),
      window: state.alert.window,
      schedule: scheduleValue(),
      timezone: state.alert.timezone,
      message_template: String(state.alert.message_template || "").trim() || null
    };
  }

  function openAlertForm(ruleId) {
    var rule = ruleId
      ? state.alerts.filter(function (row) { return row.id === ruleId; })[0]
      : null;
    if (ruleId && !rule) return;
    if (!state.channels.length) {
      // Не тупик: из предупреждения сразу открываем форму канала.
      return offerChannel("Уведомлению нужен чат, куда писать.", function () {
        openAlertForm(ruleId);
      });
    }
    state.alert = alertDraft(rule);
    state.alertStep = 0;
    openForm({
      title: ruleId ? "Уведомление" : "Новое уведомление",
      subtitle: "Триггер, условия и сообщение",
      fields: [],
      after: renderAlertStep,
      onSave: async function () {
        readAlertStep();
        if (state.alertStep < ALERT_STEPS.length - 1) {
          if (!state.alert.name.trim()) throw new Error("Укажите название");
          if (state.alertStep === 0 && state.alert.kind === "report" &&
            state.alert.schedule_mode === "custom" && !scheduleValue()) {
            throw new Error("Укажите время отправки");
          }
          if (state.alertStep === 1 && state.alert.kind !== "report") {
            var problem = conditionsProblem(state.alert.conditions);
            if (problem) throw new Error(problem);
          }
          state.alertStep += 1;
          renderAlertStep();
          // Форма остаётся открытой: это переход к следующему шагу,
          // а не сохранение.
          throw new StepChange();
        }
        var payload = alertPayload();
        if (!payload.name) throw new Error("Укажите название");
        if (ruleId) await api.patch("/utilities/alerts/" + ruleId, payload);
        else await api.post("/utilities/alerts", payload);
        await loadAlerts();
      },
      onDelete: ruleId ? async function () {
        if (!(await askConfirm({
          title: "Удалить уведомление?",
          message: "«" + rule.name + "» перестанет срабатывать.",
          confirmLabel: "Удалить",
          danger: true
        }))) return;
        await api.delete("/utilities/alerts/" + ruleId);
        closeForm();
        await loadAlerts();
      } : null
    });
  }

  // Переход между шагами — не ошибка, но и не сохранение. Отдельный тип, чтобы
  // общая форма не показала его текст как сообщение об ошибке.
  function StepChange() { this.step = true; }

  function alertBack() {
    if (!state.alert || !state.alertStep) return closeForm();
    readAlertStep();
    state.alertStep -= 1;
    renderAlertStep();
  }

  async function sendAlertTest() {
    readAlertStep();
    var button = byId("utilAlertTest");
    var hint = byId("utilAlertTestState");
    var payload = alertPayload();
    if (!payload.name) payload.name = "Тест";
    button.disabled = true;
    hint.textContent = "Отправляем…";
    try {
      var result = await api.post("/utilities/alerts/test", payload);
      hint.textContent = "Ушло в чат: " + (result.message || "").split("\n")[0];
    } catch (error) {
      hint.textContent = error && error.message ? error.message : "Не удалось отправить";
    } finally {
      button.disabled = false;
    }
  }

  function insertMacro(code) {
    var area = byId("utilModalBody").querySelector('[data-alert-field="message_template"]');
    if (!area) return;
    var at = area.selectionStart || area.value.length;
    area.value = area.value.slice(0, at) + "{" + code + "}" + area.value.slice(at);
    area.focus();
    area.selectionStart = area.selectionEnd = at + code.length + 2;
    state.alert.message_template = area.value;
  }

  function toggleMacroPopover(button) {
    var popover = byId("utilMacroPopover");
    if (!popover) return;
    var open = popover.hidden;
    popover.hidden = !open;
    button.setAttribute("aria-expanded", String(open));
    if (open) {
      var search = popover.querySelector("[data-alert-macro-search]");
      if (search) search.focus();
    }
  }

  function closeMacroPopover() {
    var popover = byId("utilMacroPopover");
    var button = byId("utilModalBody").querySelector("[data-alert-macro-toggle]");
    if (popover) popover.hidden = true;
    if (button) button.setAttribute("aria-expanded", "false");
  }

  function filterMacros(input) {
    var query = String(input.value || "").trim().toLowerCase();
    var popover = input.closest(".mcr-popover");
    popover.querySelectorAll("[data-macro-search]").forEach(function (button) {
      button.hidden = query && button.getAttribute("data-macro-search").indexOf(query) < 0;
    });
    popover.querySelectorAll(".mcr-section").forEach(function (section) {
      section.hidden = !section.querySelector("[data-macro-search]:not([hidden])");
    });
  }

  function showMacroDescription(button) {
    var description = byId("utilMacroDescription");
    if (!description) return;
    description.textContent = button.getAttribute("data-macro-description") || "";
    description.hidden = false;
  }

  function hideMacroDescription() {
    var description = byId("utilMacroDescription");
    if (description) description.hidden = true;
  }

  function alertSummary(rule) {
    if (rule.kind === "report") {
      return rule.schedule_label + " · " + rule.window_label + " · " + rule.timezone;
    }
    return "Продажи Keitaro · " + (rule.conditions_text || "любые депозиты");
  }

  function renderAlerts() {
    byId("utilAlertsCount").textContent = state.alerts.length;
    var host = byId("utilAlerts");
    if (!state.alerts.length) {
      host.innerHTML = emptyHtml(
        "Уведомлений нет.",
        "«Уведомление по депозитам» пишет в чат на каждую продажу из Keitaro, " +
        "«Отчёт» присылает сводку за период по расписанию."
      );
      return;
    }
    host.innerHTML = state.alerts.map(function (rule) {
      var off = rule.status !== "active";
      return card(off,
        escapeHtml(rule.name) +
          ' <span style="font-size:11px;font-weight:700;color:#857D7D;background:#F2EDED;' +
          'border-radius:7px;padding:3px 7px;margin-left:6px">' +
          escapeHtml(rule.kind_label) + "</span>",
        escapeHtml(alertSummary(rule)),
        off ? "Выключено" : "Активно", off,
        state.canManage
          ? '<button type="button" data-alert-edit="' + escapeHtml(rule.id) +
            '" class="row-btn">Изменить</button>'
          : "");
    }).join("");
  }

  /* ---------- CAP (ТЗ 9.2) ---------- */

  function renderCaps() {
    var host = byId("utilCaps");
    var summary = state.capSummary || { total: 0, active: 0, reached: 0 };
    byId("utilCapsCount").textContent = summary.total;
    byId("utilCapSummary").innerHTML = [
      { label: "Всего", value: summary.total, color: "#070505" },
      { label: "Активных", value: summary.active, color: "#0E7350" },
      { label: "Достигнуто", value: summary.reached, color: "#B91414" }
    ].map(function (card) {
      return '<div class="cap-stat"><div style="font-size:10.5px;color:#9B9292;' +
        'font-weight:700;text-transform:uppercase;letter-spacing:.5px">' +
        escapeHtml(card.label) + "</div>" +
        '<div style="font-size:21px;font-weight:700;margin-top:4px;color:' + card.color +
        '">' + card.value + "</div></div>";
    }).join("");

    if (!state.caps.length) {
      host.innerHTML = '<tr><td colspan="9" style="padding:40px 20px;text-align:center;' +
        'color:#9B9292;font-size:12.5px">' +
        (state.capStatus || state.capMetric
          ? "Под фильтр ничего не подошло"
          : "CapAlert не настроены. Это лимит на связку офферов или на баера за период — " +
            "когда набранное доходит до порога, в чат уходит предупреждение.") +
        "</td></tr>";
      return;
    }
    host.innerHTML = state.caps.map(function (rule) {
      var off = rule.status !== "active";
      var progress = rule.progress || { value: 0, limit: 0, percent: 0 };
      var period = labelFrom(state.reference.cap_periods, rule.period);
      // Полоска не уезжает за край, но подпись показывает настоящий процент.
      var width = Math.min(progress.percent, 100);
      var tone = progress.percent >= 100 ? " cap-bar--over"
        : progress.percent >= 80 ? " cap-bar--warn" : "";
      return '<tr style="border-bottom:1px solid #F7F4F4">' +
        '<td class="cap-cell" style="padding-left:20px">' +
        '<div style="font-weight:700;font-size:13px">' + escapeHtml(rule.name) + "</div></td>" +
        '<td class="cap-cell">' + escapeHtml(rule.user_name || "—") + "</td>" +
        '<td class="cap-cell">' + escapeHtml(rule.channel_name || "—") + "</td>" +
        '<td class="cap-cell">' + escapeHtml(rule.metric_label || rule.metric) + "</td>" +
        '<td class="cap-cell">' + escapeHtml(period) +
        '<div style="font-size:10.5px;color:#9B9292;margin-top:2px">' +
        escapeHtml(rule.timezone || "Europe/Moscow") + "</div></td>" +
        '<td class="cap-cell"><div style="display:flex;align-items:baseline;' +
        'justify-content:space-between;gap:10px;font-size:12.5px;font-weight:700">' +
        "<span>" + escapeHtml(trimNumber(progress.value)) + " / " +
        escapeHtml(trimNumber(progress.limit)) + "</span>" +
        '<span style="color:' + (progress.percent >= 100 ? "#B91414"
          : progress.percent >= 80 ? "#C9821F" : "#857D7D") +
        '">' + progress.percent + " %</span></div>" +
        '<div class="cap-bar' + tone + '"><span style="width:' + width + '%"></span></div></td>' +
        '<td class="cap-cell">' + thresholdChips(rule) + "</td>" +
        '<td class="cap-cell">' + (off
          ? '<span class="settings-chip" style="color:#9B9292;background:#F7F4F4">Выключен</span>'
          : '<span class="settings-chip" style="color:#0E7350;background:#E4F7F0">Активен</span>') +
        "</td>" +
        '<td class="cap-cell" style="padding-right:20px;text-align:right">' +
        (state.canManage
          ? '<button type="button" data-cap-edit="' + escapeHtml(rule.id) +
            '" style="height:32px;border:1px solid #E5DFDF;border-radius:9px;background:#fff;' +
            'padding:0 12px;font-size:11.5px;font-weight:700">Изменить</button>'
          : "") + "</td></tr>";
    }).join("");
  }

  /* Пороги — чипами, а пройденные с галочкой. Строка «100 % · отправлен 100 %»
     заставляла сверять два числа глазами, а нужен ответ на один вопрос: по
     какому порогу уже ушло сообщение, а какой ещё впереди. */
  function thresholdChips(rule) {
    var sent = Number(rule.notified_percent || 0);
    var values = (rule.notify_at || []).slice().sort(function (a, b) { return a - b; });
    if (!values.length) return '<span style="color:#9B9292">—</span>';
    return '<div class="cap-thresholds">' + values.map(function (value) {
      var passed = sent >= value;
      return '<span class="cap-threshold' + (passed ? " is-passed" : "") + '"' +
        (passed ? ' title="Уведомление отправлено"' : "") + ">" + value + " %" +
        (passed ? " ✓" : "") + "</span>";
    }).join("") + "</div>";
  }

  function capFailure(error) {
    notify({
      title: "Не удалось загрузить капы",
      message: error && error.message ? error.message : ""
    });
  }

  function labelFrom(list, code) {
    var found = (list || []).filter(function (item) { return item.code === code; })[0];
    return found ? found.label : code;
  }

  // Сама форма живёт в cap-ui.js: её же открывает кнопка «CapAlert» в
  // Офферах. Две копии одной формы однажды уже разошлись с API.
  function openCapForm(ruleId) {
    var rule = ruleId
      ? state.caps.filter(function (row) { return row.id === ruleId; })[0]
      : null;
    if (ruleId && !rule) return;
    if (!state.channels.length) {
      return offerChannel("CapAlert нужен чат, куда писать.", function () {
        openCapForm(ruleId);
      });
    }
    window.CelestialCap.open({ rule: rule, onSaved: loadCaps });
  }

  /* ---------- журнал ---------- */

  function emptyEventsText() {
    if (state.eventFilter === "true") return "Отправленных уведомлений пока нет.";
    if (state.eventFilter === "false") {
      return "Все уведомления ушли — застрявших нет.";
    }
    return "Уведомления ещё не отправлялись.";
  }

  async function loadEvents() {
    var host = byId("utilEvents");
    try {
      var page = await api.get("/utilities/events?limit=50" +
        (state.eventFilter ? "&delivered=" + state.eventFilter : ""));
      var items = page.items || [];
      if (!items.length) {
        host.innerHTML = '<div style="padding:24px;text-align:center;font-size:12.5px;' +
          'color:#857D7D;font-weight:600">' + escapeHtml(emptyEventsText()) + "</div>";
        return;
      }
      host.innerHTML = items.map(function (event) {
        return '<div style="padding:14px 20px;border-bottom:1px solid #F7F4F4">' +
          '<div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">' +
          '<span style="font-size:12.5px;font-weight:700">' + escapeHtml(event.rule_name) +
          "</span>" +
          '<span style="font-size:11px;font-weight:700;border-radius:7px;padding:3px 8px;' +
          (event.delivered
            ? "color:#16B57F;background:#E4F7F0\">отправлено"
            : "color:#B91414;background:#FCF1F1\">не ушло") + "</span>" +
          '<span style="font-size:11px;color:#9B9292;font-weight:600;margin-left:auto">' +
          escapeHtml(moment(event.created_at)) + "</span></div>" +
          '<div style="font-size:11.5px;color:#6A6161;font-weight:600;margin-top:6px;' +
          'white-space:pre-line">' + escapeHtml(event.message) + "</div>" +
          (event.error
            ? '<div style="font-size:11px;color:#B91414;font-weight:700;margin-top:5px">' +
              escapeHtml(event.error) + "</div>"
            : "") + "</div>";
      }).join("");
    } catch (error) {
      host.innerHTML = '<div style="padding:24px;text-align:center;font-size:12.5px;' +
        'color:#B91414;font-weight:600">' +
        escapeHtml(error && error.message ? error.message : "Журнал недоступен") + "</div>";
    }
  }

  /* ---------- общее ---------- */

  function card(off, title, subtitle, badge, danger, actions) {
    return '<article style="background:#fff;border:1px solid #EBE6E6;border-radius:16px;' +
      'padding:16px 20px' + (off ? ";opacity:.62" : "") + '">' +
      '<div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">' +
      '<div style="flex:1;min-width:220px">' +
      '<div style="font-size:14px;font-weight:700">' + title + "</div>" +
      '<div style="font-size:11.5px;color:#9B9292;font-weight:600;margin-top:5px;' +
      'line-height:1.6">' + subtitle + "</div></div>" +
      '<div class="row-side"><span class="row-status ' +
      (danger ? "row-status--off" : "row-status--on") + '">' + badge + "</span>" +
      actions + "</div></div></article>";
  }

  function emptyHtml(title, hint) {
    return '<div style="background:#fff;border:1px dashed #DDD5D5;border-radius:16px;' +
      'padding:28px;text-align:center;color:#857D7D;font-size:12.5px;font-weight:600;' +
      'line-height:1.6"><b>' + escapeHtml(title) + "</b><br>" + escapeHtml(hint) + "</div>";
  }

  function switchTab(tab) {
    if ((tab === "channels" && state.canChannels === false) ||
      (tab === "events" && state.canEvents === false)) {
      tab = "alerts";
    }
    state.tab = tab;
    document.querySelectorAll("[data-util-tab]").forEach(function (button) {
      button.classList.toggle("active", button.getAttribute("data-util-tab") === tab);
    });
    document.querySelectorAll("[data-util-panel]").forEach(function (panel) {
      panel.classList.toggle("active", panel.getAttribute("data-util-panel") === tab);
    });
    if (tab === "events") loadEvents();
  }

  async function loadBot() {
    state.bot = await api.get("/utilities/bot");
    renderBot();
  }

  async function loadChannels() {
    var page = await api.get("/utilities/channels");
    state.channels = page.items || [];
    // Форма CapAlert держит свой кэш каналов: канал могли только что добавить
    // или выключить прямо на этой странице.
    if (window.CelestialCap) window.CelestialCap.reset();
    renderChannels();
  }

  async function loadAlerts() {
    var page = await api.get("/utilities/alerts");
    state.alerts = page.items || [];
    renderAlerts();
  }

  async function loadCaps() {
    var query = [];
    if (state.capStatus) query.push("status=" + encodeURIComponent(state.capStatus));
    if (state.capMetric) query.push("metric=" + encodeURIComponent(state.capMetric));
    var page = await api.get("/utilities/caps" + (query.length ? "?" + query.join("&") : ""));
    state.caps = page.items || [];
    // Счётчики шапки считает сервер: по отфильтрованному списку они врали бы.
    state.capSummary = page.summary || null;
    renderCaps();
  }

  function bind() {
    byId("utilCapStatus").addEventListener("change", function (event) {
      state.capStatus = event.target.value;
      loadCaps().catch(capFailure);
    });
    byId("utilCapMetric").addEventListener("change", function (event) {
      state.capMetric = event.target.value;
      loadCaps().catch(capFailure);
    });
    document.querySelectorAll("[data-util-events]").forEach(function (button) {
      button.addEventListener("click", function () {
        state.eventFilter = button.getAttribute("data-util-events");
        document.querySelectorAll("[data-util-events]").forEach(function (item) {
          item.classList.toggle("active", item === button);
        });
        loadEvents();
      });
    });
    document.querySelectorAll("[data-util-tab]").forEach(function (button) {
      button.addEventListener("click", function () {
        switchTab(button.getAttribute("data-util-tab"));
      });
    });
    byId("utilBotSetup").addEventListener("click", openBotForm);
    byId("utilChannelCreate").addEventListener("click", function () { openChannelForm(null); });
    byId("utilAlertCreate").addEventListener("click", function () { openAlertForm(null); });
    byId("utilCapCreate").addEventListener("click", function () { openCapForm(null); });

    byId("utilModalClose").addEventListener("click", closeForm);
    byId("utilModalCancel").addEventListener("click", function () {
      if (state.alert && state.alertStep) return alertBack();
      closeForm();
    });
    byId("utilModal").addEventListener("click", function (event) {
      if (event.target === byId("utilModal")) closeForm();
    });
    byId("utilModalSave").addEventListener("click", function () {
      submitForm().catch(function () {});
    });
    byId("utilModalDelete").addEventListener("click", function () {
      if (state.form && state.form.onDelete) {
        state.form.onDelete().catch(function (error) {
          formError(error && error.message ? error.message : "Не удалось удалить");
        });
      }
    });
    byId("utilModalBody").addEventListener("input", function (event) {
      var macroSearch = event.target.closest
        ? event.target.closest("[data-alert-macro-search]") : null;
      if (macroSearch) return filterMacros(macroSearch);
      var text = event.target.closest ? event.target.closest('[data-cnd="text"]') : null;
      if (text) condNodeAt(text.getAttribute("data-path")).text = text.value;
      if (event.target.matches &&
        event.target.matches('[data-alert-field="message_template"]')) {
        state.alert.message_template = event.target.value;
      }
    });
    byId("utilModalBody").addEventListener("change", function (event) {
      if (event.target.matches &&
        event.target.matches('[data-alert-field="schedule_mode"]')) {
        readAlertStep();
        return renderAlertStep();
      }
      handleConditionChange(event);
    });

    byId("utilModalBody").addEventListener("click", function (event) {
      var target = event.target;
      if (!target.closest) return;
      if (!target.closest(".mcr-picker")) closeMacroPopover();
      if (handleConditionClick(event)) return;
      var kind = target.closest("[data-alert-kind]");
      if (kind) {
        event.preventDefault();
        readAlertStep();
        state.alert.kind = kind.getAttribute("data-alert-kind");
        return renderAlertStep();
      }
      var step = target.closest("[data-alert-step]");
      if (step) {
        event.preventDefault();
        readAlertStep();
        // Назад — свободно, вперёд — только по «Далее»: иначе можно
        // проскочить шаг, не заполнив его.
        var index = Number(step.getAttribute("data-alert-step"));
        if (index < state.alertStep) {
          state.alertStep = index;
          renderAlertStep();
        }
        return;
      }
      var macro = target.closest("[data-alert-macro]");
      if (macro) {
        event.preventDefault();
        return insertMacro(macro.getAttribute("data-alert-macro"));
      }
      var macroToggle = target.closest("[data-alert-macro-toggle]");
      if (macroToggle) {
        event.preventDefault();
        return toggleMacroPopover(macroToggle);
      }
      if (target.closest("#utilAlertTest")) {
        event.preventDefault();
        return sendAlertTest();
      }
      if (target.closest("[data-tree-act]")) return handleTreeClick(event);
      if (target.closest("#utilTreeCheck")) {
        event.preventDefault();
        return checkDraft();
      }
      if (target.closest("#utilFindChats")) {
        event.preventDefault();
        return findChats();
      }
      var pick = target.closest("[data-chat-pick]");
      if (!pick) return;
      event.preventDefault();
      var body = byId("utilModalBody");
      body.querySelector('[data-field="chat_id"]').value =
        pick.getAttribute("data-chat-pick");
      var name = body.querySelector('[data-field="name"]');
      if (!name.value.trim()) name.value = pick.getAttribute("data-chat-title");
    });
    byId("utilModalBody").addEventListener("mouseover", function (event) {
      var macro = event.target.closest ? event.target.closest("[data-alert-macro]") : null;
      if (macro) showMacroDescription(macro);
    });
    byId("utilModalBody").addEventListener("mouseout", function (event) {
      var macro = event.target.closest ? event.target.closest("[data-alert-macro]") : null;
      if (macro && !(event.relatedTarget && macro.contains(event.relatedTarget))) {
        hideMacroDescription();
      }
    });
    byId("utilModalBody").addEventListener("focusin", function (event) {
      var macro = event.target.closest ? event.target.closest("[data-alert-macro]") : null;
      if (macro) showMacroDescription(macro);
    });
    byId("utilModalBody").addEventListener("focusout", function (event) {
      if (event.target.closest && event.target.closest("[data-alert-macro]")) {
        hideMacroDescription();
      }
    });
    byId("utilChannels").addEventListener("click", function (event) {
      var target = event.target;
      if (!target.closest) return;
      var edit = target.closest("[data-channel-edit]");
      if (edit) return openChannelForm(edit.getAttribute("data-channel-edit"));
      var test = target.closest("[data-channel-test]");
      if (test) return testChannel(test.getAttribute("data-channel-test"));
    });
    byId("utilAlerts").addEventListener("click", function (event) {
      var target = event.target;
      if (!target.closest) return;
      var edit = target.closest("[data-alert-edit]");
      if (edit) return openAlertForm(edit.getAttribute("data-alert-edit"));
    });
    byId("utilCaps").addEventListener("click", function (event) {
      var edit = event.target.closest ? event.target.closest("[data-cap-edit]") : null;
      if (edit) openCapForm(edit.getAttribute("data-cap-edit"));
    });
  }

  async function init(user) {
    if (!byId("utilAlerts")) return;
    state.user = user;
    state.canManage = hasPermission(user, "utilities.manage");
    // «Каналы» и «Журнал» — по отдельным правам роли: чаты и журнал отправок
    // видеть нужно не всем, кто настраивает свои уведомления.
    state.canChannels = hasPermission(user, "utilities.channels");
    state.canEvents = hasPermission(user, "utilities.events");
    [["channels", state.canChannels], ["events", state.canEvents]].forEach(function (pair) {
      var tab = document.querySelector('[data-util-tab="' + pair[0] + '"]');
      if (tab) tab.style.display = pair[1] ? "" : "none";
    });
    ["utilAlertCreate", "utilCapCreate", "utilChannelCreate"].forEach(function (id) {
      byId(id).style.display = state.canManage ? "" : "none";
    });
    if (!state.canChannels) byId("utilChannelCreate").style.display = "none";
    if ((state.tab === "channels" && !state.canChannels) ||
      (state.tab === "events" && !state.canEvents)) {
      switchTab("alerts");
    }
    bind();
    state.reference = await api.get("/utilities/reference");
    byId("utilCapMetric").innerHTML = '<option value="">Все метрики</option>' +
      (state.reference.cap_metrics || []).map(function (item) {
        return '<option value="' + escapeHtml(item.code) + '">' +
          escapeHtml(item.label) + "</option>";
      }).join("");
    await Promise.all([loadBot(), loadChannels(), loadAlerts(), loadCaps()]);
  }

  window.CelestialUtilities = { init: init };
})();
