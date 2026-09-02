/*
 * Workspace: канбан-доска команды и база знаний (ТЗ 8).
 *
 * Две вещи, которые определили устройство этого файла.
 *
 * Первая — порядок карточек. Он приходит с сервера отдельным полем, а не
 * выводится из даты создания: доску переставляют мышью, и карточки обязаны
 * оставаться там, куда их положили, а не перескакивать после обновления.
 *
 * Вторая — статья хранится блоками, как в Notion, а не готовым HTML. Из блоков
 * можно перерисовать статью в любом виде и найти по ней текст; из HTML обратно
 * блоки уже не собрать. Поэтому редактор работает со списком объектов, а
 * contenteditable используется только внутри одного блока.
 */
(function () {
  "use strict";

  var api = window.CelestialAPI;

  var state = {
    user: null,
    canManageBoard: false,
    canViewKnowledge: false,
    canManageKnowledge: false,
    tab: "tasks",
    people: [],
    board: null,
    // Разделы доски: по отделу на вкладку. Активный запоминается между заходами
    // — человек работает в своём отделе, а не выбирает его каждое утро.
    sections: [],
    sectionId: null,
    canManageSections: false,
    fields: [],
    statuses: [],
    templates: [],
    // Метаданные загруженных файлов по ID: в значениях полей лежат только ID.
    attachments: {},
    filters: { search: "", assignee: "", status: "", priority: "" },
    // Сортировка у каждой колонки своя — код лежит по её id.
    columnSort: {},
    sortMenu: null,
    drag: null,
    // Свёрнутые колонки доски и открытый композер быстрого добавления —
    // переживают перерисовку доски, поэтому лежат в state, а не в DOM.
    folded: {},
    quick: null,
    cardMenu: null,
    form: null,
    settingsTab: "statuses",
    tree: null,
    collapsed: {},
    article: null,
    blocks: [],
    editing: false,
    dirty: false,
    searchTimer: null,
    boardRequest: 0,
    treeRequest: 0,
    searchRequest: 0,
    focusStack: [],
    blockMenuAnchor: null
  };

  var PRIORITIES = {
    critical: { label: "Критический", color: "#C41616", background: "#FCF1F1" },
    high: { label: "Высокий", color: "#C9821F", background: "#FFF6E9" },
    medium: { label: "Средний", color: "#2C4E77", background: "#EFF5FE" },
    low: { label: "Низкий", color: "#6A6161", background: "#F2EDED" }
  };
  // Типы пользовательских полей. Порядок здесь — порядок в выпадающем списке
  // при создании поля, от самых частых к редким.
  var FIELD_KINDS = {
    text: "Текст",
    textarea: "Многострочный текст",
    select: "Выпадающий список",
    labels: "Метки (несколько значений)",
    date: "Дата",
    number: "Число",
    money: "Сумма",
    checkbox: "Чекбокс",
    user: "Пользователь",
    url: "Ссылка",
    file: "Файлы"
  };
  var FIELD_ICONS = {
    text: "T", textarea: "¶", select: "▾", labels: "◍", date: "▦", number: "#",
    money: "$", checkbox: "☑", user: "@", url: "↗", file: "⎘"
  };
  // Типы со списком вариантов — у них редактируется «Варианты списка».
  var OPTION_KINDS = ["select", "labels"];
  var CURRENCIES = ["USD", "EUR", "RUB", "KZT", "UAH", "GBP", "TRY", "BRL"];
  var ARTICLE_STATUS = {
    draft: { label: "Черновик", color: "#C9821F", background: "#FFF6E9" },
    published: { label: "Опубликована", color: "#16B57F", background: "#E4F7F0" },
    archived: { label: "В архиве", color: "#6A6161", background: "#F2EDED" }
  };
  var BLOCK_MENU = [
    { type: "paragraph", label: "Текст" },
    { type: "heading_1", label: "Заголовок 1" },
    { type: "heading_2", label: "Заголовок 2" },
    { type: "heading_3", label: "Заголовок 3" },
    { type: "bulleted_list", label: "Маркированный список" },
    { type: "numbered_list", label: "Нумерованный список" },
    { type: "checklist", label: "Чек-лист" },
    { type: "table", label: "Таблица" },
    { type: "quote", label: "Цитата" },
    { type: "code", label: "Блок с кодом" },
    { type: "divider", label: "Разделитель" },
    { type: "image", label: "Изображение" },
    { type: "video", label: "Видео" },
    { type: "file", label: "Файл" },
    { type: "page_link", label: "Вложенная страница" }
  ];

  function byId(id) {
    return document.getElementById(id);
  }

  function escapeHtml(value) {
    return String(value === null || value === undefined ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function hasPermission(user, code) {
    var codes = (user && user.role && user.role.permissions) || [];
    return codes.some(function (item) {
      return item.code === code || item.code === "*";
    });
  }

  function initials(name) {
    var parts = String(name || "?").trim().split(/\s+/);
    return ((parts[0] || "?")[0] + (parts[1] ? parts[1][0] : "")).toUpperCase();
  }

  function today() {
    var now = new Date();
    return new Date(now.getFullYear(), now.getMonth(), now.getDate());
  }

  function formatDay(value) {
    if (!value) return "";
    var date = new Date(value + "T00:00:00");
    if (isNaN(date.getTime())) return value;
    return date.toLocaleDateString("ru-RU", { day: "2-digit", month: "short" });
  }

  function formatMoment(value) {
    if (!value) return "—";
    var moment = new Date(value);
    if (isNaN(moment.getTime())) return "—";
    return moment.toLocaleString("ru-RU", {
      day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit"
    });
  }

  /* ---------- подтверждения ----------
   *
   * Окно браузера рисуется у верхней кромки и подписано адресом сервера — на
   * фоне карточки задачи это выглядит как сообщение постороннего сайта.
   * Спрашиваем модалкой CRM; контракт тот же, только ответ приходит промисом.
   */

  function askConfirm(options) {
    if (window.CelestialShell && window.CelestialShell.confirm) {
      return window.CelestialShell.confirm(options);
    }
    return Promise.resolve(window.confirm(options.message || options.title));
  }

  function askPrompt(options) {
    if (window.CelestialShell && window.CelestialShell.prompt) {
      return window.CelestialShell.prompt(options);
    }
    return Promise.resolve(
      window.prompt(options.message || options.title, options.value || "")
    );
  }

  function notify(options) {
    if (window.CelestialShell && window.CelestialShell.notify) {
      return window.CelestialShell.notify(options);
    }
    window.alert(options.message || options.title);
    return Promise.resolve(true);
  }

  function fail(error, fallback) {
    notify({
      title: fallback || "Не получилось",
      message: error && error.message ? error.message : ""
    });
  }

  function loadingHtml(label) {
    return '<div class="ws-state" role="status"><span class="ws-state__spinner" ' +
      'aria-hidden="true"></span><strong>' + escapeHtml(label || "Загружаем…") +
      '</strong><span>Это займёт несколько секунд.</span></div>';
  }

  function errorHtml(message, retry) {
    return '<div class="ws-state" role="alert"><strong>Не удалось загрузить данные</strong>' +
      '<span>' + escapeHtml(message || "Попробуйте ещё раз") + '</span>' +
      (retry ? '<button class="ws-btn" type="button" ' + retry +
        ' style="margin-top:14px">Повторить</button>' : "") + '</div>';
  }

  function showModal(id) {
    var modal = byId(id);
    if (!modal) return;
    state.focusStack.push({ modal: modal, focus: document.activeElement });
    modal.style.display = "flex";
    modal.setAttribute("aria-hidden", "false");
    document.body.style.overflow = "hidden";
    window.setTimeout(function () {
      var target = modal.querySelector("[autofocus]") ||
        modal.querySelector("input:not([disabled]),select:not([disabled]),textarea:not([disabled])") ||
        modal.querySelector("button:not([disabled])");
      if (target) target.focus();
    }, 0);
  }

  function hideModal(id) {
    var modal = byId(id);
    if (!modal) return;
    modal.style.display = "none";
    modal.setAttribute("aria-hidden", "true");
    var index = -1;
    state.focusStack.forEach(function (entry, entryIndex) {
      if (entry.modal === modal) index = entryIndex;
    });
    var entry = index >= 0 ? state.focusStack.splice(index, 1)[0] : null;
    if (!state.focusStack.length) document.body.style.overflow = "";
    if (entry && entry.focus && document.contains(entry.focus)) entry.focus.focus();
  }

  function topModal() {
    return state.focusStack.length
      ? state.focusStack[state.focusStack.length - 1].modal
      : null;
  }

  /* ---------- общая форма ---------- */

  function hintHtml(field) {
    return field.hint ? '<span class="ws-hint">' + escapeHtml(field.hint) + "</span>" : "";
  }

  function labelHtml(field) {
    /* Обязательность помечаем звёздочкой, а не подписью «обязательное поле»
       под каждым полем: подпись повторялась столько раз, сколько полей в
       брифе, и всё равно ничего не сообщала — о незаполненном поле говорит
       сообщение при сохранении. */
    return "<span>" + escapeHtml(field.label) +
      (field.required ? '<i class="ws-req" title="Обязательное поле">*</i>' : "") +
      "</span>";
  }

  function fieldClass(field) {
    return "ws-field" + (field.row ? " ws-field--row" : "");
  }

  function fieldHtml(field) {
    var span = field.half ? "" : "grid-column:1/-1;";
    var body;
    if (field.type === "heading") {
      return '<div style="grid-column:1/-1;margin-top:8px;padding-top:14px;' +
        'border-top:1px solid #F0EBEB;font-size:11px;font-weight:700;letter-spacing:.08em;' +
        'text-transform:uppercase;color:#AFA6A6">' + escapeHtml(field.label) + "</div>";
    }
    if (field.type === "select") {
      body = '<select class="ws-control ws-select" data-field="' + escapeHtml(field.name) + '">' +
        (field.options || []).map(function (option) {
          return '<option value="' + escapeHtml(option.value) + '"' +
            (option.selected ? " selected" : "") + ">" + escapeHtml(option.label) + "</option>";
        }).join("") + "</select>";
    } else if (field.type === "textarea") {
      body = '<textarea class="ws-control" data-field="' + escapeHtml(field.name) + '">' +
        escapeHtml(field.value || "") + "</textarea>";
    } else if (field.type === "checkbox") {
      return '<label class="ws-pick" style="' + span + '"' + whenAttr(field) + ">" +
        '<input type="checkbox" data-field="' + escapeHtml(field.name) + '"' +
        (field.value ? " checked" : "") + ">" +
        "<span>" + escapeHtml(field.label) +
        (field.hint ? '<span style="display:block;font-size:11px;color:#9B9292;font-weight:500;' +
          'margin-top:3px">' + escapeHtml(field.hint) + "</span>" : "") + "</span></label>";
    } else if (field.type === "checklist") {
      return '<div class="' + fieldClass(field) + '" style="' + span + '"' + whenAttr(field) + '>' +
        labelHtml(field) + '<div class="ws-checklist" data-checklist="' +
        escapeHtml(field.name) + '">' + checklistHtml(field) + "</div>" +
        hintHtml(field) + "</div>";
    } else if (field.type === "chips") {
      return '<div class="' + fieldClass(field) + '" style="' + span + '"' + whenAttr(field) + '>' +
        labelHtml(field) + chipsHtml(field) +
        hintHtml(field) + "</div>";
    } else if (field.type === "files") {
      return '<div class="' + fieldClass(field) + '" style="' + span + '"' + whenAttr(field) + '>' +
        labelHtml(field) + filesControlHtml(field) +
        hintHtml(field) + "</div>";
    } else if (field.type === "fieldpicker") {
      // Список полей выше обычного чек-листа: перетаскивать в окошко на три
      // строки неудобно, а полей в брифе обычно семь-десять.
      return '<div class="ws-field" style="' + span + '"><span>' +
        escapeHtml(field.label) + '</span><div class="ws-checklist" ' +
        'style="max-height:330px" data-fieldpicker="' +
        escapeHtml(field.name) + '">' + fieldPickerHtml(field) + "</div>" +
        (field.footer || "") +
        hintHtml(field) + "</div>";
    } else {
      body = '<input class="ws-control" type="' + (field.type || "text") + '" data-field="' +
        escapeHtml(field.name) + '" value="' + escapeHtml(
          field.value === null || field.value === undefined ? "" : field.value
        ) + '"' +
        (field.placeholder ? ' placeholder="' + escapeHtml(field.placeholder) + '"' : "") +
        (field.min !== undefined ? ' min="' + field.min + '"' : "") +
        (field.max !== undefined ? ' max="' + field.max + '"' : "") +
        (field.step ? ' step="' + field.step + '"' : "") + ">";
    }
    return '<label class="' + fieldClass(field) + '" style="' + span + '"' + whenAttr(field) + ">" +
      labelHtml(field) + body +
      hintHtml(field) + "</label>";
  }

  // Поле, которое имеет смысл только для части типов: «Варианты списка» нужны
  // списку и меткам, валюта — сумме. Показываем их по выбранному типу, а не
  // вываливаем все настройки сразу.
  function whenAttr(field) {
    return field.when ? ' data-when="' + escapeHtml(field.when.join(",")) + '"' : "";
  }

  function applyKindVisibility(container, kind) {
    Array.prototype.forEach.call(container.querySelectorAll("[data-when]"), function (node) {
      var kinds = node.getAttribute("data-when").split(",");
      node.style.display = kinds.indexOf(kind) >= 0 ? "" : "none";
    });
  }

  function filesControlHtml(field) {
    var ids = field.value || [];
    return '<div data-files="' + escapeHtml(field.name) + '" data-ids="' +
      escapeHtml(ids.join(",")) + '" style="display:grid;gap:7px">' +
      fileRowsHtml(ids) +
      '<button class="ws-action" type="button" data-file-add="' + escapeHtml(field.name) +
      '" style="justify-self:start">Прикрепить файл</button></div>';
  }

  function fileRowsHtml(ids) {
    // Пустой список ничего не сообщает: под полем и так стоит «Прикрепить файл».
    if (!ids.length) return "";
    return ids.map(function (id) {
      var meta = state.attachments[id] || { file_name: "Файл", byte_size: 0, url: "" };
      return '<div class="ws-pick" data-file-row="' + escapeHtml(id) + '">' +
        '<a href="' + escapeHtml(meta.url || "#") + '" target="_blank" rel="noopener" ' +
        'style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;' +
        'white-space:nowrap">' + escapeHtml(meta.file_name) + "</a>" +
        '<span style="font-size:11px;color:#9B9292;font-weight:600">' +
        fileSize(meta.byte_size) + "</span>" +
        '<button class="ws-action ws-action--danger" type="button" data-file-remove="' +
        escapeHtml(id) + '">Убрать</button></div>';
    }).join("");
  }

  // Файл грузится сразу, а к задаче привязывается при сохранении карточки:
  // повторно отдать выбранный файл браузер не даст, а форму часто закрывают и
  // открывают заново. Непривязанные файлы убирает фоновая уборка.
  function bindChips(container) {
    container.addEventListener("click", function (event) {
      var chip = event.target.closest ? event.target.closest("[data-chip]") : null;
      if (!chip) return;
      event.preventDefault();
      toggleChip(chip);
    });
    container.addEventListener("input", function (event) {
      if (event.target && event.target.hasAttribute &&
        event.target.hasAttribute("data-chip-filter")) filterChips(event.target);
    });
  }

  function bindFileControls(container) {
    container.addEventListener("click", function (event) {
      if (!event.target.closest) return;
      var add = event.target.closest("[data-file-add]");
      if (add) {
        event.preventDefault();
        return pickTaskFile(add.closest("[data-files]"));
      }
      var remove = event.target.closest("[data-file-remove]");
      if (remove) {
        event.preventDefault();
        var host = remove.closest("[data-files]");
        var id = remove.getAttribute("data-file-remove");
        setFileIds(host, fileIds(host).filter(function (item) { return item !== id; }));
      }
    });
  }

  function pickTaskFile(host) {
    if (!host) return;
    var picker = byId("wsFilePicker");
    picker.value = "";
    picker.onchange = async function () {
      var file = picker.files && picker.files[0];
      picker.onchange = null;
      if (!file) return;
      var button = host.querySelector("[data-file-add]");
      button.disabled = true;
      button.textContent = "Загружаем…";
      try {
        var form = new FormData();
        form.append("file", file);
        var uploaded = await api.upload("/workspace/attachments", form);
        state.attachments[uploaded.id] = uploaded;
        setFileIds(host, fileIds(host).concat([uploaded.id]));
      } catch (error) {
        taskError(error && error.message ? error.message : "Не удалось загрузить файл");
      } finally {
        button.disabled = false;
        button.textContent = "Прикрепить файл";
      }
    };
    picker.click();
  }

  function fileSize(bytes) {
    var value = Number(bytes || 0);
    if (value < 1024) return value + " Б";
    if (value < 1024 * 1024) return Math.round(value / 1024) + " КБ";
    return (value / (1024 * 1024)).toFixed(1) + " МБ";
  }

  /*
   * В списке только поля самого шаблона.
   *
   * Раньше здесь лежали все поля воркспейса с галочками, и новый шаблон
   * открывался готовым списком чужих полей — от предыдущего брифа. Чтобы
   * собрать свой, приходилось сначала разбираться, что из этого не твоё.
   * Поля общие и переиспользовать их по-прежнему можно, но теперь это
   * отдельное осознанное действие «добавить существующее».
   */
  function fieldPickerHtml(field) {
    var picked = (field.value || []).slice();
    // Пустой список ничего не сообщает: кнопка «Создать поле» стоит прямо под ним.
    if (!picked.length) return "";
    return picked.map(function (id) {
      var row = state.fields.filter(function (item) { return item.id === id; })[0];
      return row ? pickRowHtml(field.name, row) : "";
    }).join("");
  }

  function pickRowHtml(name, row) {
    /* Галочка спрятана, но осталась: по ней читаются состав и порядок полей,
       а лишний переключатель в списке, где всё и так выбрано, только сбивает. */
    return '<div class="ws-pick" draggable="true" data-pick-row="' + escapeHtml(row.id) + '">' +
      '<span class="ws-grip" aria-hidden="true">⠿</span>' +
      '<input type="checkbox" hidden data-pick="' + escapeHtml(name) + '" value="' +
      escapeHtml(row.id) + '" checked>' +
      '<span style="flex:1;min-width:0">' + escapeHtml(row.name) + "</span>" +
      '<span class="ws-chip" style="color:#6A6161;background:#F2EDED">' +
      escapeHtml(FIELD_ICONS[row.kind] || "") + " " +
      escapeHtml(FIELD_KINDS[row.kind] || row.kind) + "</span>" +
      (row.is_required
        ? '<span class="ws-chip" style="color:#C9821F;background:#FFF6E9">обяз.</span>'
        : "") +
      '<button class="ws-action" type="button" data-field-edit="' + escapeHtml(row.id) +
      '" aria-label="Изменить поле">✎</button>' +
      '<button class="ws-action ws-action--danger" type="button" data-field-delete="' +
      escapeHtml(row.id) + '" aria-label="Удалить поле">✕</button></div>';
  }

  /* Порядок полей меняют перетаскиванием: стрелками переставить бриф из семи
     полей — это два десятка кликов. */
  function bindPickerDrag(host) {
    var dragged = null;
    host.addEventListener("dragstart", function (event) {
      var row = event.target.closest ? event.target.closest("[data-pick-row]") : null;
      if (!row) return;
      dragged = row;
      row.classList.add("ws-pick--dragging");
      event.dataTransfer.effectAllowed = "move";
      // Safari не начинает перетаскивание без данных в буфере.
      event.dataTransfer.setData("text/plain", row.getAttribute("data-pick-row"));
    });
    host.addEventListener("dragend", function () {
      if (dragged) dragged.classList.remove("ws-pick--dragging");
      Array.prototype.forEach.call(host.querySelectorAll(".ws-pick--over"), function (row) {
        row.classList.remove("ws-pick--over");
      });
      dragged = null;
    });
    host.addEventListener("dragover", function (event) {
      if (!dragged) return;
      event.preventDefault();
      var row = event.target.closest ? event.target.closest("[data-pick-row]") : null;
      Array.prototype.forEach.call(host.querySelectorAll(".ws-pick--over"), function (item) {
        if (item !== row) item.classList.remove("ws-pick--over");
      });
      if (!row || row === dragged) return;
      row.classList.add("ws-pick--over");
      // Вставляем до или после соседа по его середине — так строка встаёт
      // туда, куда целится курсор, а не всегда перед ним.
      var box = row.getBoundingClientRect();
      var after = event.clientY > box.top + box.height / 2;
      host.insertBefore(dragged, after ? row.nextSibling : row);
    });
    host.addEventListener("drop", function (event) {
      if (dragged) event.preventDefault();
    });
  }

  // Поле заводится прямо здесь: отдельная вкладка «Поля» заставляла выйти из
  // шаблона, создать поле и вернуться, чтобы его отметить.
  function inlineFieldHtml(field, id) {
    var currency = (field && field.config ? field.config.currency : "") || "USD";
    return '<div id="' + id + '" data-inline-field style="display:none;' +
      'border:1px solid #EBE6E6;' +
      'border-radius:12px;padding:14px 15px;margin-top:10px;background:#FCFBFB">' +
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:12px">' +
      '<label class="ws-field"><span>Название поля</span>' +
      '<input class="ws-control" data-field="new_field_name" style="padding:0 13px" value="' +
      escapeHtml(field ? field.name : "") + '"></label>' +
      (field
        ? '<label class="ws-field"><span>Тип</span><input class="ws-control" ' +
          'style="padding:0 13px" value="' +
          escapeHtml(FIELD_KINDS[field.kind] || field.kind) + '" disabled></label>'
        : '<label class="ws-field"><span>Тип</span>' +
          '<select class="ws-control ws-select" data-field="new_field_kind">' +
          Object.keys(FIELD_KINDS).map(function (key) {
            return '<option value="' + key + '">' + escapeHtml(FIELD_KINDS[key]) + "</option>";
          }).join("") + "</select></label>") +
      '<label class="ws-field" style="grid-column:1/-1" data-when="' +
      OPTION_KINDS.join(",") + '"><span>Варианты списка</span>' +
      '<textarea class="ws-control" data-field="new_field_options" ' +
      'style="min-height:74px;padding:11px 13px">' +
      escapeHtml(field ? (field.options || []).join("\n") : "") + "</textarea>" +
      '<span style="display:block;font-size:11px;color:#9B9292;font-weight:600;' +
      'margin-top:5px">по одному в строке</span></label>' +
      '<label class="ws-field" data-when="money"><span>Валюта</span>' +
      '<select class="ws-control ws-select" data-field="new_field_currency">' +
      CURRENCIES.map(function (code) {
        return '<option value="' + code + '"' + (code === currency ? " selected" : "") +
          ">" + code + "</option>";
      }).join("") + "</select></label>" +
      '<label class="ws-pick" style="grid-column:1/-1">' +
      '<input type="checkbox" data-field="new_field_required"' +
      (field && field.is_required ? " checked" : "") + "><span>Обязательное поле</span></label>" +
      "</div>" +
      '<div style="display:flex;gap:9px;margin-top:12px">' +
      '<button class="ws-btn ws-btn--primary" type="button" data-inline-save ' +
      'style="height:38px">' + (field ? "Сохранить поле" : "Добавить поле") + "</button>" +
      '<button class="ws-btn" type="button" data-inline-cancel style="height:38px">' +
      "Отмена</button>" +
      '<span data-inline-error style="align-self:center;font-size:11.5px;' +
      'color:#B91414;font-weight:700"></span></div></div>';
  }

  // Выбранные метки помечаются в самом чипе, а не отдельной галочкой: так
  // видно набор целиком, а не построчный список с полупустыми строками.
  function chipsHtml(field) {
    var options = field.options || [];
    var selected = options.filter(function (option) { return option.selected; })
      .map(function (option) { return option.value; });
    if (!options.length) {
      return '<div class="ws-chips"><span class="ws-chip-empty">' +
        escapeHtml(field.empty || "Вариантов нет") + "</span></div>";
    }
    // Поиск появляется только когда вариантов много: для трёх он лишний.
    var filter = options.length > 12
      ? '<input class="ws-chip-filter" type="search" data-chip-filter="' +
        escapeHtml(field.name) + '" placeholder="Найти значение">'
      : "";
    return filter + '<div class="ws-chips" data-chips="' + escapeHtml(field.name) +
      '" data-values="' + escapeHtml(selected.join("\u0001")) + '">' +
      options.map(function (option) {
        return '<button type="button" class="ws-chip-option' +
          (option.selected ? " ws-chip-option--on" : "") + '" data-chip="' +
          escapeHtml(option.value) + '" aria-pressed="' +
          (option.selected ? "true" : "false") + '">' +
          escapeHtml(option.label) + "</button>";
      }).join("") + "</div>";
  }

  function chipValues(host) {
    var raw = host.getAttribute("data-values") || "";
    return raw ? raw.split("\u0001").filter(Boolean) : [];
  }

  function toggleChip(button) {
    var host = button.closest("[data-chips]");
    var value = button.getAttribute("data-chip");
    var values = chipValues(host);
    var index = values.indexOf(value);
    if (index >= 0) values.splice(index, 1);
    else values.push(value);
    host.setAttribute("data-values", values.join("\u0001"));
    var on = index < 0;
    button.classList.toggle("ws-chip-option--on", on);
    button.setAttribute("aria-pressed", on ? "true" : "false");
  }

  function filterChips(input) {
    var host = input.parentElement.querySelector("[data-chips]");
    if (!host) return;
    var query = input.value.trim().toLowerCase();
    Array.prototype.forEach.call(host.querySelectorAll("[data-chip]"), function (chip) {
      var visible = !query ||
        chip.getAttribute("data-chip").toLowerCase().indexOf(query) >= 0;
      chip.style.display = visible ? "" : "none";
    });
  }

  function checklistHtml(field) {
    if (!(field.options || []).length) {
      return '<div style="font-size:11.5px;color:#9B9292;font-weight:600">' +
        escapeHtml(field.empty || "Нечего выбрать") + "</div>";
    }
    return field.options.map(function (option) {
      return '<label class="ws-pick"><input type="checkbox" data-check="' +
        escapeHtml(field.name) + '" value="' + escapeHtml(option.value) + '"' +
        (option.selected ? " checked" : "") + "><span>" + escapeHtml(option.label) +
        "</span></label>";
    }).join("");
  }

  function readFields(container) {
    var values = {};
    Array.prototype.forEach.call(container.querySelectorAll("[data-field]"), function (input) {
      values[input.getAttribute("data-field")] =
        input.type === "checkbox" ? input.checked : input.value;
    });
    Array.prototype.forEach.call(container.querySelectorAll("[data-checklist]"), function (host) {
      var name = host.getAttribute("data-checklist");
      values[name] = Array.prototype.map.call(
        host.querySelectorAll("input[data-check]:checked"),
        function (input) { return input.value; }
      );
    });
    Array.prototype.forEach.call(container.querySelectorAll("[data-chips]"), function (host) {
      values[host.getAttribute("data-chips")] = chipValues(host);
    });
    Array.prototype.forEach.call(container.querySelectorAll("[data-files]"), function (host) {
      values[host.getAttribute("data-files")] = fileIds(host);
    });
    Array.prototype.forEach.call(container.querySelectorAll("[data-fieldpicker]"), function (host) {
      // Порядок берём из DOM, а не из порядка полей доски: в шаблоне важно, в
      // каком порядке поля читают, и переставляют их именно здесь.
      values[host.getAttribute("data-fieldpicker")] = Array.prototype.map.call(
        host.querySelectorAll("input[data-pick]:checked"),
        function (input) { return input.value; }
      );
    });
    return values;
  }

  function fileIds(host) {
    var raw = host.getAttribute("data-ids") || "";
    return raw ? raw.split(",").filter(Boolean) : [];
  }

  function setFileIds(host, ids) {
    host.setAttribute("data-ids", ids.join(","));
    var button = host.querySelector("[data-file-add]");
    var markup = fileRowsHtml(ids);
    Array.prototype.forEach.call(
      host.querySelectorAll("[data-file-row]"),
      function (node) { node.remove(); }
    );
    button.insertAdjacentHTML("beforebegin", markup);
  }

  function openForm(config) {
    state.form = config;
    byId("wsFormTitle").textContent = config.title;
    byId("wsFormSubtitle").textContent = config.subtitle || "";
    var body = byId("wsFormBody");
    body.innerHTML = (config.html || "") + (config.fields || []).map(fieldHtml).join("") +
      (config.footerHtml || "") +
      (config.note ? '<div class="ws-note" style="grid-column:1/-1">' + config.note + "</div>" : "");
    byId("wsFormDelete").style.display = config.onDelete ? "" : "none";
    if (config.after) config.after(body);
    formError("");
    byId("wsFormStatus").textContent = "";
    showModal("wsFormModal");
    var first = body.querySelector("[data-field]");
    if (first) first.focus();
  }

  function closeForm() {
    hideModal("wsFormModal");
    state.form = null;
  }

  function formError(message) {
    var host = byId("wsFormError");
    host.textContent = message || "";
    host.style.display = message ? "" : "none";
  }

  async function submitForm() {
    if (!state.form) return;
    formError("");
    byId("wsFormSave").disabled = true;
    byId("wsFormStatus").textContent = "Сохраняем…";
    try {
      await state.form.onSave(readFields(byId("wsFormBody")));
      closeForm();
    } catch (error) {
      formError(error && error.message ? error.message : "Не удалось сохранить");
    } finally {
      byId("wsFormSave").disabled = false;
      byId("wsFormStatus").textContent = "";
    }
  }

  /* ---------- доска задач (ТЗ 8.1) ---------- */

  /* Вид доски запоминается между заходами: фильтр и сортировку выставляют под
     свою работу один раз, а не каждое утро заново.

     Не храним две вещи. Текст поиска — он всегда про «сейчас», и вчерашний
     запрос в пустой доске пугает. Фильтр по колонке — он повторяет то, что и
     так видно на доске, а удалённая колонка оставила бы фильтр, о котором
     нельзя догадаться. */
  var VIEW_KEY = "celestial.tasks.view";
  var FOLD_KEY = "celestial.tasks.folded";
  var SORT_KEY = "celestial.tasks.columnsort";

  /* Сортировка у каждой колонки своя.
   *
   * Общая на всю доску не годилась: колонки живут по-разному. В «Открыта»
   * смотрят, что горит по сроку, в «Готово» — что закрыли последним, а в
   * «В работе» порядок расставлен руками, и любая сортировка его прячет.
   * Одним переключателем на всю доску одно из трёх всегда было неправильным.
   *
   * Считается в браузере: доска уже пришла целиком, и гонять запрос ради
   * перестановки двадцати карточек незачем. */
  var SORTS = [
    { code: "position", label: "Мой порядок" },
    { code: "due_date", label: "По сроку" },
    { code: "priority", label: "По приоритету" },
    { code: "created", label: "Сначала новые" },
    { code: "created_asc", label: "Сначала старые" },
    { code: "updated", label: "Сначала изменённые" },
    { code: "updated_asc", label: "Давно не менялись" }
  ];
  var PRIORITY_ORDER = { critical: 0, high: 1, medium: 2, low: 3 };

  // Через `|| 9` этот ранг брать нельзя: у критического приоритета он равен
  // нулю, и «пустое» значение отправило бы самое срочное в конец списка.
  function priorityRank(value) {
    var rank = PRIORITY_ORDER[value];
    return rank === undefined ? 9 : rank;
  }

  function sortFor(columnId) {
    return state.columnSort[columnId] || "position";
  }

  function sortLabel(code) {
    var found = SORTS.filter(function (item) { return item.code === code; })[0];
    return found ? found.label : code;
  }

  function sortTasks(tasks, code) {
    var rows = tasks.slice();
    if (code === "due_date") {
      // Задачи без срока — в конец: пустой срок иначе «самый ранний» и
      // заслоняет то, что горит.
      rows.sort(function (a, b) {
        if (!a.due_date !== !b.due_date) return a.due_date ? -1 : 1;
        return (a.due_date || "") < (b.due_date || "") ? -1
          : (a.due_date || "") > (b.due_date || "") ? 1 : a.position - b.position;
      });
    } else if (code === "priority") {
      rows.sort(function (a, b) {
        var diff = priorityRank(a.priority) - priorityRank(b.priority);
        return diff || a.position - b.position;
      });
    } else if (code === "created" || code === "created_asc" ||
      code === "updated" || code === "updated_asc") {
      var key = code.indexOf("created") === 0 ? "created_at" : "updated_at";
      var back = code.indexOf("_asc") < 0;
      rows.sort(function (a, b) {
        var left = String(a[key] || "");
        var right = String(b[key] || "");
        // Позиция вторым ключом: две задачи, заведённые в одну секунду, иначе
        // меняются местами от перерисовки к перерисовке.
        var diff = left < right ? -1 : left > right ? 1 : a.position - b.position;
        return back ? -diff : diff;
      });
    } else {
      rows.sort(function (a, b) { return a.position - b.position; });
    }
    return rows;
  }

  function readStore(key, fallback) {
    try {
      var raw = window.localStorage.getItem(key);
      return raw ? JSON.parse(raw) : fallback;
    } catch (error) {
      return fallback;
    }
  }

  function writeStore(key, value) {
    try {
      window.localStorage.setItem(key, JSON.stringify(value));
    } catch (error) {
      // Приватный режим Safari запрещает запись — вид просто не запомнится.
    }
  }

  // Вызывается, когда список людей уже загружен: сохранённый исполнитель мог
  // уволиться, и фильтр по несуществующему человеку показал бы пустую доску
  // без понятной причины.
  function restoreView() {
    state.folded = readStore(FOLD_KEY, {}) || {};
    state.columnSort = readStore(SORT_KEY, {}) || {};
    var saved = readStore(VIEW_KEY, null);
    if (!saved || typeof saved !== "object") return;
    var known = function (select, value) {
      return !!value && Array.prototype.some.call(select.options, function (option) {
        return option.value === value;
      });
    };
    if (state.people.some(function (person) { return person.id === saved.assignee; })) {
      state.filters.assignee = saved.assignee;
    }
    if (known(byId("wsPriorityFilter"), saved.priority)) {
      state.filters.priority = saved.priority;
      byId("wsPriorityFilter").value = saved.priority;
    }
    // Раздел здесь не проверить — их список приходит вместе с доской. Если
    // раздел удалили или закрыли, сервер ответит отказом, и доска откроет
    // первый доступный.
    if (typeof saved.section === "string") state.sectionId = saved.section;
  }

  function saveView() {
    writeStore(VIEW_KEY, {
      assignee: state.filters.assignee,
      priority: state.filters.priority,
      section: state.sectionId || ""
    });
  }

  async function loadBoard() {
    var requestId = ++state.boardRequest;
    var host = byId("wsBoard");
    host.setAttribute("aria-busy", "true");
    if (!state.board) {
      host.innerHTML = loadingHtml("Загружаем задачи");
      byId("wsTaskSummary").textContent = "Загружаем доску…";
    }
    var query = [];
    if (state.sectionId) query.push("section_id=" + encodeURIComponent(state.sectionId));
    if (state.filters.search) query.push("search=" + encodeURIComponent(state.filters.search));
    if (state.filters.assignee) query.push("assignee_id=" + state.filters.assignee);
    if (state.filters.status) query.push("status_id=" + state.filters.status);
    if (state.filters.priority) query.push("priority=" + state.filters.priority);
    // С сервера доска приходит в ручном порядке, а колонки раскладывает уже
    // браузер — у каждой своя сортировка.
    query.push("sort=position");
    try {
      var payload = await api.get("/workspace/board?" + query.join("&"));
      if (requestId !== state.boardRequest) return false;
      state.board = payload;
      state.fields = payload.fields || [];
      state.sections = payload.sections || [];
      state.canManageSections = !!payload.can_manage_sections;
      state.sectionId = payload.section_id || null;
      renderSections();
      // Дописываем, а не заменяем: только что загруженный файл ещё не привязан
      // к задаче и в ответе доски его нет, а показать его имя нужно сразу.
      Object.keys(payload.attachments || {}).forEach(function (id) {
        state.attachments[id] = payload.attachments[id];
      });
      state.statuses = (payload.columns || []).map(function (column) {
        return {
          id: column.id, name: column.name, color: column.color,
          is_system: column.is_system, is_terminal: column.is_terminal, tasks: column.count
        };
      });
      renderBoard();
      renderFilters();
      return true;
    } catch (error) {
      if (requestId !== state.boardRequest) return false;
      // Запомненный раздел мог закрыться или исчезнуть — тогда открываем
      // первый доступный вместо ошибки. Повтор без раздела по этой причине уже
      // не упадёт, так что зацикливания нет.
      if (state.sectionId) {
        state.sectionId = null;
        saveView();
        return loadBoard();
      }
      host.innerHTML = errorHtml(
        error && error.message ? error.message : "Сервер не ответил",
        'data-ws-retry="board"'
      );
      byId("wsTaskSummary").textContent = "Доска временно недоступна";
      return false;
    } finally {
      if (requestId === state.boardRequest) host.setAttribute("aria-busy", "false");
    }
  }

  function currentSection() {
    return (state.sections || []).filter(function (row) {
      return row.id === state.sectionId;
    })[0] || null;
  }

  function sectionRights(section) {
    return (section && section.rights) || {};
  }

  /* Полоса разделов. Шестерёнка и «+» стоят в конце полосы и никуда не
     переезжают: кнопка у активной вкладки прыгала бы по строке при каждом
     переключении раздела, и попасть в неё мышью было бы нельзя не глядя.
     Шестерёнка настраивает именно открытый раздел. */
  function renderSections() {
    var host = byId("wsSections");
    if (!host) return;
    var rows = state.sections || [];
    var manage = state.canManageSections;
    if (!rows.length && !manage) {
      host.style.display = "none";
      host.innerHTML = "";
      return;
    }
    host.style.display = "";
    host.innerHTML = rows.map(function (section) {
      var active = section.id === state.sectionId;
      return '<button class="ws-section' + (active ? " ws-section--active" : "") +
        '" type="button" role="tab" aria-selected="' + (active ? "true" : "false") +
        '" data-ws-section="' + escapeHtml(section.id) + '">' +
        "<span>" + escapeHtml(section.title) + "</span>" +
        '<span class="ws-section__count">' + (section.tasks || 0) + "</span></button>";
    }).join("") + (manage && state.sectionId
      ? '<button class="ws-section-tool" type="button" data-ws-section-edit="' +
        escapeHtml(state.sectionId) + '" title="Раздел и доступ" ' +
        'aria-label="Настроить открытый раздел">⚙</button>'
      : "") + (manage
      ? '<button class="ws-section-tool" type="button" data-ws-section-add ' +
        'title="Новый раздел" aria-label="Новый раздел">+</button>'
      : "");
  }

  // Права в разделе доски: свои карточки и так правит автор с исполнителем,
  // поэтому «правка» и «удаление» здесь именно про чужие.
  var SECTION_RIGHTS = [
    { key: "view", label: "просмотр" },
    { key: "create", label: "создание" },
    { key: "edit", label: "правка чужих" },
    { key: "delete", label: "удаление чужих" }
  ];

  async function openBoardSectionForm(sectionId) {
    var section = (state.sections || []).filter(function (row) {
      return row.id === sectionId;
    })[0];
    var access = sectionId ? await api.get("/workspace/sections/" + sectionId + "/access") : null;
    openForm({
      title: sectionId ? "Раздел доски" : "Новый раздел",
      subtitle: sectionId
        ? (access.open ? "Сейчас раздел открыт всей команде" : "Доступ к разделу ограничен")
        : "Отдел со своей доской — колонки, поля и шаблоны у всех разделов общие",
      // Название идёт в html, а не в fields: форма рисует html первым, а
      // название раздела должно стоять выше списка прав.
      html: fieldHtml({
        name: "title", label: "Название", value: section ? section.title : "",
        placeholder: "Например, Дизайнеры"
      }) + (access
        ? accessGroupHtml(
            "Роли",
            "Права получают все, у кого эта роль.",
            access.roles.map(function (role) {
              return accessRowHtml("role", role.role_id, role.role_name, role, SECTION_RIGHTS);
            }).join("")
          ) + accessGroupHtml(
            "Отдельные люди",
            "Именное правило сильнее правила его роли.",
            access.users.map(function (person) {
              return accessRowHtml("user", person.user_id, person.user_name, person,
                SECTION_RIGHTS);
            }).join("")
          )
        : ""),
      note: sectionId
        ? "Пока не отмечен никто, раздел виден всей команде и задачи в нём заводит " +
          "любой. Достаточно отметить одного адресата, чтобы раздел стал доступен " +
          "только отмеченным."
        : "Доступ настраивается после создания — раздел откроется на доске, и там " +
          "будет шестерёнка.",
      onDelete: sectionId && (state.sections || []).length > 1
        ? function () { return removeBoardSection(section); }
        : null,
      onSave: async function (values) {
        var title = String(values.title || "").trim();
        if (!title) throw new Error("Укажите название раздела");
        if (!sectionId) {
          var created = await api.post("/workspace/sections", { title: title });
          state.sectionId = created.id;
          saveView();
        } else {
          if (title !== section.title) {
            await api.patch("/workspace/sections/" + sectionId, { title: title });
          }
          await api.request("/workspace/sections/" + sectionId + "/access", {
            method: "PUT",
            body: JSON.stringify({ rules: readAccessRules(byId("wsFormBody"), SECTION_RIGHTS) })
          });
        }
        await loadBoard();
      }
    });
  }

  async function removeBoardSection(section) {
    var others = (state.sections || []).filter(function (row) { return row.id !== section.id; });
    if (!others.length) return;
    var target = others[0];
    var confirmed = await askConfirm({
      title: "Удалить раздел?",
      message: section.tasks
        ? "В разделе «" + section.title + "» " + section.tasks +
          " задач — они переедут в раздел «" + target.title + "»."
        : "Раздел «" + section.title + "» будет удалён.",
      confirmLabel: "Удалить",
      danger: true
    });
    if (!confirmed) return;
    await api.delete(
      "/workspace/sections/" + section.id + (section.tasks ? "?move_to=" + target.id : "")
    );
    state.sectionId = target.id;
    saveView();
    await loadBoard();
    closeForm();
  }

  function renderBoard() {
    var host = byId("wsBoard");
    // Меню висит на body и пережило бы перерисовку, указывая на карточку,
    // которой в этом месте доски уже нет.
    closeCardMenu();
    var columns = (state.board && state.board.columns) || [];
    var canCreate = !!sectionRights(currentSection()).can_create;
    byId("wsTaskCreate").disabled = !columns.length || !canCreate;
    byId("wsBoardSettings").disabled = !state.canManageBoard;
    if (state.board && !state.sections.length) {
      host.innerHTML = '<div class="ws-state"><strong>Нет доступных разделов</strong>' +
        "<span>" + (state.canManageSections
          ? "Создайте раздел кнопкой «+» рядом с заголовком."
          : "Попросите руководителя открыть вам раздел доски.") + "</span></div>";
      byId("wsTaskSummary").textContent = "Разделы закрыты";
      return;
    }
    if (!columns.length) {
      host.innerHTML = '<div class="ws-state"><strong>На доске нет колонок</strong>' +
        '<span>' + (state.canManageBoard
          ? "Откройте настройки доски и создайте первую колонку."
          : "Попросите руководителя настроить доску.") + '</span></div>';
      byId("wsTaskSummary").textContent = "Колонки не настроены";
      return;
    }
    host.innerHTML = columns.map(columnHtml).join("");

    var total = (state.board && state.board.total) || 0;
    byId("wsTaskSummary").textContent = total
      ? "Задач на доске: " + total
      : (hasActiveFilters() ? "Ничего не найдено" : "Задач пока нет");
    var overdue = (state.board && state.board.overdue) || 0;
    var badge = byId("wsOverdue");
    badge.style.display = overdue ? "" : "none";
    badge.textContent = "Просрочено: " + overdue;
    byId("wsResetFilters").style.display = hasActiveFilters() ? "" : "none";
    var mine = byId("wsMineFilter");
    mine.disabled = !state.user;
    mine.classList.toggle("ws-mine--on", isMine());
    mine.setAttribute("aria-pressed", isMine() ? "true" : "false");
    focusQuick();
  }

  function columnHtml(column) {
    var folded = !!state.folded[column.id];
    var sort = sortFor(column.id);
    var head = '<div class="ws-column__head">' +
      '<span class="ws-dot" style="background:' + escapeHtml(column.color) + '"></span>' +
      '<span class="ws-column__name" style="font-size:13px;font-weight:700;flex:1;min-width:0;' +
      'overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' +
      escapeHtml(column.name) + "</span>" +
      '<span style="font-size:11.5px;color:#9B9292;font-weight:700">' + column.count + "</span>" +
      '<button class="ws-column__sort' + (sort === "position" ? "" : " ws-column__sort--on") +
      '" type="button" data-sort-menu="' + escapeHtml(column.id) +
      '" title="Сортировка колонки: ' + escapeHtml(sortLabel(sort)) +
      '" aria-label="Сортировка колонки ' + escapeHtml(column.name) + '">' +
      '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
      '<path d="M7 4v16m0 0-3-3m3 3 3-3M17 20V4m0 0-3 3m3-3 3 3" stroke="currentColor" ' +
      'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></button>' +
      '<button class="ws-column__fold" type="button" data-fold="' + escapeHtml(column.id) +
      '" title="Свернуть колонку" aria-label="Свернуть колонку ' + escapeHtml(column.name) +
      '">‹</button></div>';
    if (folded) {
      return '<div class="ws-column ws-column--collapsed" data-column="' +
        escapeHtml(column.id) + '" data-fold="' + escapeHtml(column.id) +
        '" title="Развернуть колонку" role="button" tabindex="0">' + head +
        '<div class="ws-column__body" data-drop="' + escapeHtml(column.id) + '"></div></div>';
    }
    return '<div class="ws-column" data-column="' + escapeHtml(column.id) + '">' + head +
      '<div class="ws-column__body" data-drop="' + escapeHtml(column.id) + '">' +
      (column.tasks.length
        ? sortTasks(column.tasks, sort).map(cardHtml).join("")
        : '<div style="padding:18px 6px;text-align:center;color:#B4ABAB;font-size:11.5px;' +
          'font-weight:600">Пусто</div>') +
      "</div>" +
      '<div class="ws-column__foot">' + quickHtml(column) + "</div></div>";
  }

  /* Быстрое добавление: название набирают прямо в колонке, как в Notion.
     Карточка со всеми полями остаётся для задач, которым это нужно. */
  function quickHtml(column) {
    // В разделе, куда пускают только смотреть, композер не нужен: кнопка вела
    // бы к отказу сервера.
    if (!sectionRights(currentSection()).can_create) return "";
    if (state.quick !== column.id) {
      return '<button class="ws-column__add" type="button" data-quick-open="' +
        escapeHtml(column.id) + '">+ Задача</button>';
    }
    return '<div class="ws-quick" data-quick="' + escapeHtml(column.id) + '">' +
      '<textarea data-quick-input rows="1" placeholder="Название задачи" ' +
      'aria-label="Название новой задачи в колонке ' + escapeHtml(column.name) +
      '"></textarea>' +
      '<div class="ws-quick__hint">Enter — создать · Esc — закрыть</div></div>';
  }

  function focusQuick() {
    if (!state.quick) return;
    var input = byId("wsBoard").querySelector(
      '[data-quick="' + state.quick + '"] [data-quick-input]'
    );
    if (input) input.focus();
    else state.quick = null;
  }

  function isMine() {
    return !!(state.user && state.filters.assignee === state.user.id);
  }

  function hasActiveFilters() {
    return !!(state.filters.search || state.filters.assignee || state.filters.status ||
      state.filters.priority);
  }

  function cardHtml(task) {
    var priority = PRIORITIES[task.priority] || PRIORITIES.medium;
    var canEdit = task.can_edit !== false;
    // Перетаскивать можно всегда: колонку задаче меняют и в отсортированной
    // колонке. Порядок внутри неё считает сортировка, поэтому туда карточка
    // просто добавляется в конец — но переносу это не мешает.
    var canDrag = canEdit;
    var overdue = task.due_date && !task.is_done &&
      new Date(task.due_date + "T00:00:00") < today();
    return '<div class="ws-card' + (overdue ? " ws-card--overdue" : "") +
      (canEdit ? "" : " ws-card--readonly") + '" draggable="' + (canDrag ? "true" : "false") +
      '" role="button" tabindex="0" aria-label="Открыть задачу ' + escapeHtml(task.title) +
      '" data-can-drag="' + (canDrag ? "true" : "false") + '" data-task="' +
      escapeHtml(task.id) + '">' +
      '<button class="ws-card__more" type="button" data-card-menu="' + escapeHtml(task.id) +
      '" aria-haspopup="menu" aria-expanded="false" aria-label="Действия с задачей">⋯</button>' +
      '<div style="font-size:13px;font-weight:600;line-height:1.45;color:#241C1C;' +
      'padding-right:22px">' + escapeHtml(task.title) + "</div>" +
      (task.description
        ? '<div style="font-size:11.5px;color:#9B9292;margin-top:5px;overflow:hidden;' +
          'display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical">' +
          escapeHtml(task.description) + "</div>"
        : "") +
      // Значения полей брифа на карточку не выносим: доска нужна, чтобы окинуть
      // взглядом колонку, а не прочитать каждую задачу целиком.
      '<div style="display:flex;align-items:center;gap:7px;margin-top:11px;flex-wrap:wrap">' +
      '<span class="ws-chip" style="color:' + priority.color + ";background:" +
      priority.background + '">' + escapeHtml(priority.label) + "</span>" +
      (task.due_date
        ? '<span class="ws-chip" style="color:' + (overdue ? "#C41616" : "#6A6161") +
          ";background:" + (overdue ? "#FCF1F1" : "#F2EDED") + '">' +
          (overdue ? "просрочено " : "") + escapeHtml(formatDay(task.due_date)) + "</span>"
        : "") +
      '<span style="flex:1"></span>' +
      (!canEdit ? '<span class="ws-card__lock" title="Только просмотр">Только просмотр</span>' : "") +
      task.assignees.map(function (person) {
        return '<span class="ws-avatar" title="' + escapeHtml(person.name) + '">' +
          escapeHtml(initials(person.name)) + "</span>";
      }).join("") +
      "</div></div>";
  }


  function shorten(value, limit) {
    var text = String(value);
    return text.length > limit ? text.slice(0, limit - 1) + "…" : text;
  }

  function renderFilters() {
    var assignee = byId("wsAssigneeFilter");
    if (assignee.options.length <= 1) {
      assignee.innerHTML = '<option value="">Все исполнители</option>' +
        state.people.map(function (person) {
          return '<option value="' + escapeHtml(person.id) + '">' +
            escapeHtml(person.name) + "</option>";
        }).join("");
    }
    var status = byId("wsStatusFilter");
    status.innerHTML = '<option value="">Все статусы</option>' +
      state.statuses.map(function (row) {
        return '<option value="' + escapeHtml(row.id) + '">' + escapeHtml(row.name) + "</option>";
      }).join("");
    status.value = state.filters.status;
    assignee.value = state.filters.assignee;
    ["wsSearch", "wsAssigneeFilter", "wsStatusFilter", "wsPriorityFilter"]
      .forEach(function (id) { byId(id).disabled = false; });
  }

  /* ---------- быстрое добавление ---------- */

  function openQuick(statusId) {
    state.quick = statusId;
    renderBoard();
  }

  function closeQuick() {
    if (!state.quick) return;
    state.quick = null;
    renderBoard();
  }

  // Обязательные поля доски одним названием не заполнить. Сервер ответил бы
  // «Заполните поле», поэтому вместо ошибки открываем карточку с уже вписанным
  // названием: набранное не теряется, а дозаполнить можно сразу.
  function blockingFields() {
    return state.fields.filter(function (field) {
      return field.is_required && field.show_always;
    });
  }

  async function quickCreate(statusId, title) {
    var text = (title || "").trim();
    if (!text) return closeQuick();
    if (blockingFields().length) {
      state.quick = null;
      renderBoard();
      openTask(null, { title: text, status_id: statusId });
      return;
    }
    var input = byId("wsBoard").querySelector(
      '[data-quick="' + statusId + '"] [data-quick-input]'
    );
    if (input) { input.value = ""; input.disabled = true; }
    try {
      await api.post("/workspace/tasks", {
        title: text, status_id: statusId, section_id: state.sectionId || null
      });
      // Композер остаётся открытым: задачи заводят пачками, и закрывать его
      // после каждой значило бы требовать лишний клик на каждую следующую.
      await loadBoard();
    } catch (error) {
      if (input) { input.disabled = false; input.value = text; input.focus(); }
      fail(error, "Не удалось создать задачу");
    }
  }

  /* ---------- меню карточки ---------- */

  function closeCardMenu() {
    if (!state.cardMenu) return;
    var host = byId("wsCardMenu");
    if (host) host.remove();
    Array.prototype.forEach.call(
      document.querySelectorAll(".ws-card--menu"),
      function (card) { card.classList.remove("ws-card--menu"); }
    );
    var button = document.querySelector('[data-card-menu][aria-expanded="true"]');
    if (button) button.setAttribute("aria-expanded", "false");
    state.cardMenu = null;
  }

  function menuLabel(text) {
    return '<div style="font-size:10px;font-weight:700;letter-spacing:.07em;' +
      'text-transform:uppercase;color:#B4ABAB;padding:8px 10px 4px">' +
      escapeHtml(text) + "</div>";
  }

  function menuItem(action, value, label, extra) {
    return '<button type="button" data-menu-action="' + action + '" data-menu-value="' +
      escapeHtml(value || "") + '">' + label + (extra || "") + "</button>";
  }

  function cardMenuHtml(task) {
    var canEdit = task.can_edit !== false;
    var canDelete = task.can_delete !== false;
    var html = menuItem("open", task.id, "Открыть");
    if (!canEdit) return html;
    html += menuLabel("Приоритет");
    html += Object.keys(PRIORITIES).map(function (key) {
      var meta = PRIORITIES[key];
      return menuItem("priority", key,
        '<span class="ws-dot" style="background:' + meta.color + '"></span>' +
        escapeHtml(meta.label),
        task.priority === key ? '<span style="margin-left:auto;color:#B91414">✓</span>' : "");
    }).join("");
    var others = state.statuses.filter(function (row) { return row.id !== task.status_id; });
    if (others.length) {
      html += menuLabel("Перенести в");
      html += others.map(function (row) {
        return menuItem("move", row.id,
          '<span class="ws-dot" style="background:' + escapeHtml(row.color) + '"></span>' +
          escapeHtml(row.name));
      }).join("");
    }
    html += '<div style="height:1px;background:#F0EBEB;margin:6px 4px"></div>';
    html += menuItem("duplicate", task.id, "Дублировать");
    if (canDelete) {
      html += '<button type="button" data-menu-action="delete" data-menu-value="' +
        escapeHtml(task.id) + '" style="color:#B91414">Удалить</button>';
    }
    return html;
  }

  function openCardMenu(button) {
    var taskId = button.getAttribute("data-card-menu");
    if (state.cardMenu === taskId) return closeCardMenu();
    closeCardMenu();
    var task = findTask(taskId);
    if (!task) return;
    var menu = document.createElement("div");
    menu.id = "wsCardMenu";
    menu.className = "ws-menu";
    menu.setAttribute("role", "menu");
    menu.style.position = "fixed";
    menu.innerHTML = cardMenuHtml(task);
    document.body.appendChild(menu);
    // Меню держится у кнопки, но не вылезает за экран: в правой колонке доски
    // иначе половина пунктов оказалась бы за краем окна.
    var box = button.getBoundingClientRect();
    // Кнопка невидима вне ховера, и в редких случаях (тач-эмуляция, вызов из
    // кода) её прямоугольник нулевой. Тогда цепляемся за саму карточку.
    if (!box.width && !box.height) box = button.closest(".ws-card").getBoundingClientRect();
    var width = menu.offsetWidth;
    var height = menu.offsetHeight;
    var left = Math.min(box.right - width, window.innerWidth - width - 10);
    var top = box.bottom + 6;
    if (top + height > window.innerHeight - 10) top = Math.max(10, box.top - height - 6);
    menu.style.left = Math.max(10, left) + "px";
    menu.style.top = top + "px";
    button.setAttribute("aria-expanded", "true");
    var card = button.closest(".ws-card");
    if (card) card.classList.add("ws-card--menu");
    state.cardMenu = taskId;
  }

  function closeSortMenu() {
    var menu = byId("wsSortMenu");
    if (menu) menu.remove();
    state.sortMenu = null;
  }

  function openSortMenu(button) {
    var columnId = button.getAttribute("data-sort-menu");
    if (state.sortMenu === columnId) return closeSortMenu();
    closeSortMenu();
    var current = sortFor(columnId);
    var menu = document.createElement("div");
    menu.id = "wsSortMenu";
    menu.className = "ws-menu";
    menu.setAttribute("role", "menu");
    menu.style.position = "fixed";
    menu.innerHTML = SORTS.map(function (item) {
      return '<button type="button" role="menuitem" data-sort-pick="' +
        escapeHtml(item.code) + '"' + (item.code === current ? ' aria-current="true"' : "") +
        ">" + escapeHtml(item.label) +
        (item.code === current ? '<span style="margin-left:auto">✓</span>' : "") +
        "</button>";
    }).join("");
    document.body.appendChild(menu);
    var box = button.getBoundingClientRect();
    var left = Math.min(box.right - menu.offsetWidth, window.innerWidth - menu.offsetWidth - 10);
    var top = box.bottom + 6;
    if (top + menu.offsetHeight > window.innerHeight - 10) {
      top = Math.max(10, box.top - menu.offsetHeight - 6);
    }
    menu.style.left = Math.max(10, left) + "px";
    menu.style.top = top + "px";
    state.sortMenu = columnId;
  }

  function bindSortMenu() {
    document.addEventListener("click", function (event) {
      var pick = event.target.closest ? event.target.closest("[data-sort-pick]") : null;
      if (pick && state.sortMenu) {
        var columnId = state.sortMenu;
        var code = pick.getAttribute("data-sort-pick");
        if (code === "position") delete state.columnSort[columnId];
        else state.columnSort[columnId] = code;
        writeStore(SORT_KEY, state.columnSort);
        closeSortMenu();
        // Перерисовываем доску, а не грузим заново: карточки уже здесь.
        renderBoard();
        return;
      }
      var button = event.target.closest ? event.target.closest("[data-sort-menu]") : null;
      if (button) {
        event.stopPropagation();
        return openSortMenu(button);
      }
      if (state.sortMenu) closeSortMenu();
    });
    window.addEventListener("resize", closeSortMenu);
  }

  function bindCardMenu() {
    document.addEventListener("click", function (event) {
      if (!state.cardMenu) return;
      var item = event.target.closest ? event.target.closest("[data-menu-action]") : null;
      if (item && item.closest("#wsCardMenu")) {
        event.stopPropagation();
        runCardAction(item.getAttribute("data-menu-action"), item.getAttribute("data-menu-value"));
        return;
      }
      if (event.target.closest && event.target.closest("[data-card-menu]")) return;
      closeCardMenu();
    });
    // Доска прокручивается по горизонтали и внутри колонок: меню, оставшееся
    // на прежнем месте, указывало бы не на ту карточку.
    window.addEventListener("resize", closeCardMenu);
    document.addEventListener("scroll", closeCardMenu, true);
  }

  async function runCardAction(action, value) {
    var taskId = state.cardMenu;
    var task = findTask(taskId);
    closeCardMenu();
    if (!task) return;
    if (action === "open") return openTask(task.id);
    if (action === "priority") return patchTask(task, { priority: value });
    if (action === "move") return moveToColumn(task, value);
    if (action === "duplicate") return duplicateTask(task);
    if (action === "delete") return removeTask(task);
  }

  async function patchTask(task, changes) {
    try {
      await api.patch("/workspace/tasks/" + task.id, changes);
      await loadBoard();
    } catch (error) {
      fail(error, "Не удалось изменить задачу");
    }
  }

  async function moveToColumn(task, statusId) {
    var column = ((state.board && state.board.columns) || []).filter(function (row) {
      return row.id === statusId;
    })[0];
    try {
      await api.post("/workspace/tasks/" + task.id + "/move", {
        status_id: statusId, position: column ? column.count : 0
      });
      await loadBoard();
    } catch (error) {
      fail(error, "Не удалось перенести задачу");
    }
  }

  async function duplicateTask(task) {
    try {
      await api.post("/workspace/tasks", {
        title: task.title + " (копия)",
        description: task.description || null,
        section_id: task.section_id || state.sectionId || null,
        status_id: task.status_id,
        priority: task.priority,
        start_date: task.start_date || null,
        due_date: task.due_date || null,
        assignee_ids: (task.assignee_ids || []).slice(),
        custom_values: Object.assign({}, task.custom_values || {}),
        template_id: task.template_id || null
      });
      await loadBoard();
    } catch (error) {
      fail(error, "Не удалось продублировать задачу");
    }
  }

  async function removeTask(task) {
    var confirmed = await askConfirm({
      title: "Удалить задачу?",
      message: "«" + task.title + "» и её вложения будут удалены безвозвратно.",
      confirmLabel: "Удалить",
      danger: true
    });
    if (!confirmed) return;
    try {
      await api.delete("/workspace/tasks/" + task.id);
      await loadBoard();
    } catch (error) {
      fail(error, "Не удалось удалить задачу");
    }
  }

  /* Перетаскивание карточек — ТЗ 8.1. */

  function bindDragAndDrop() {
    var board = byId("wsBoard");

    board.addEventListener("dragstart", function (event) {
      var card = event.target.closest ? event.target.closest(".ws-card") : null;
      if (!card || card.getAttribute("data-can-drag") !== "true") {
        event.preventDefault();
        return;
      }
      state.drag = card.getAttribute("data-task");
      card.classList.add("ws-card--dragging");
      event.dataTransfer.effectAllowed = "move";
      // Safari не начинает перетаскивание, если ничего не положить в dataTransfer.
      event.dataTransfer.setData("text/plain", state.drag);
    });

    board.addEventListener("dragend", clearDrag);

    board.addEventListener("dragover", function (event) {
      var zone = event.target.closest ? event.target.closest("[data-drop]") : null;
      if (!zone || !state.drag) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = "move";
      var column = zone.closest(".ws-column");
      Array.prototype.forEach.call(board.querySelectorAll(".ws-column--over"), function (item) {
        if (item !== column) item.classList.remove("ws-column--over");
      });
      column.classList.add("ws-column--over");
      showDropLine(zone, event.clientY);
    });

    board.addEventListener("drop", function (event) {
      var zone = event.target.closest ? event.target.closest("[data-drop]") : null;
      if (!zone || !state.drag) return;
      event.preventDefault();
      var taskId = state.drag;
      var index = dropIndex(zone, event.clientY);
      clearDrag();
      moveTask(taskId, zone.getAttribute("data-drop"), index);
    });
  }

  function clearDrag() {
    var board = byId("wsBoard");
    Array.prototype.forEach.call(board.querySelectorAll(".ws-card--dragging"), function (card) {
      card.classList.remove("ws-card--dragging");
    });
    Array.prototype.forEach.call(board.querySelectorAll(".ws-column--over"), function (column) {
      column.classList.remove("ws-column--over");
    });
    var line = byId("wsDropLine");
    if (line) line.remove();
    state.drag = null;
  }

  /* Линия вставки идёт туда же, куда посчитает dropIndex: если показать её в
     другом месте, карточка «прыгнет» после отпускания. */
  function columnSize(statusId) {
    var column = ((state.board && state.board.columns) || []).filter(function (item) {
      return item.id === statusId;
    })[0];
    return column ? column.tasks.length : 0;
  }

  function showDropLine(zone, clientY) {
    // В отсортированной колонке место броска ни на что не влияет — линия там
    // обещала бы порядок, которого не будет. Хватает подсветки самой колонки.
    if (sortFor(zone.getAttribute("data-drop")) !== "position") {
      var stale = byId("wsDropLine");
      if (stale) stale.remove();
      return;
    }
    var line = byId("wsDropLine");
    if (!line) {
      line = document.createElement("div");
      line.id = "wsDropLine";
      line.className = "ws-drop-line";
    }
    var cards = Array.prototype.slice.call(
      zone.querySelectorAll(".ws-card:not(.ws-card--dragging)")
    );
    var index = dropIndex(zone, clientY);
    if (index >= cards.length) zone.appendChild(line);
    else zone.insertBefore(line, cards[index]);
  }

  /* Куда именно бросили: перед первой карточкой, чья середина ниже курсора. */
  function dropIndex(zone, clientY) {
    var cards = Array.prototype.slice.call(
      zone.querySelectorAll(".ws-card:not(.ws-card--dragging)")
    );
    for (var index = 0; index < cards.length; index += 1) {
      var box = cards[index].getBoundingClientRect();
      if (clientY < box.top + box.height / 2) return index;
    }
    return cards.length;
  }

  async function moveTask(taskId, statusId, position) {
    var task = findTask(taskId);
    if (!task || task.can_edit === false) return;
    var sorted = sortFor(statusId) !== "position";
    // Перестановка внутри отсортированной колонки бессмысленна: сортировка
    // вернёт свой порядок на первой же перерисовке, и карточка прыгнет назад.
    if (sorted && statusId === task.status_id) return;
    try {
      await api.post("/workspace/tasks/" + taskId + "/move", {
        status_id: statusId, position: sorted ? columnSize(statusId) : position
      });
      await loadBoard();
    } catch (error) {
      fail(error, "Не удалось перенести задачу");
      await loadBoard();
    }
  }

  /* ---------- карточка задачи ---------- */

  function taskFields(task) {
    var statuses = state.statuses;
    // Раздел показывается, только когда их несколько: на доске из одного
    // раздела это поле без выбора.
    var movable = (state.sections || []).filter(function (row) {
      return sectionRights(row).can_create || row.id === task.section_id;
    });
    var sectionField = movable.length > 1
      ? [{ name: "section_id", label: "Раздел", type: "select", half: true,
          options: movable.map(function (row) {
            return { value: row.id, label: row.title,
              selected: row.id === (task.section_id || state.sectionId) };
          }) }]
      : [];
    return sectionField.concat([
      { name: "title", label: "Название", value: task.title || "",
        placeholder: "Что нужно сделать" },
      { name: "status_id", label: "Статус", type: "select", half: true,
        options: statuses.map(function (row) {
          return { value: row.id, label: row.name, selected: row.id === task.status_id };
        }) },
      { name: "priority", label: "Приоритет", type: "select", half: true,
        options: Object.keys(PRIORITIES).map(function (key) {
          return { value: key, label: PRIORITIES[key].label,
            selected: key === (task.priority || "medium") };
        }) },
      { name: "start_date", label: "Дата начала", type: "date", half: true,
        value: task.start_date || "" },
      { name: "due_date", label: "Срок выполнения", type: "date", half: true,
        value: task.due_date || "" },
      { name: "assignee_ids", label: "Исполнители", type: "checklist",
        empty: "В команде пока некому поручить",
        options: state.people.map(function (person) {
          return { value: person.id, label: person.name,
            selected: (task.assignee_ids || []).indexOf(person.id) >= 0 };
        }) }
    ]);
  }

  // Блок пользовательских полей карточки — отдельным контейнером, потому что
  // при смене шаблона перерисовывается только он, а введённое в стандартные
  // поля остаётся на месте.
  function customBlockHtml(fields, values) {
    // Кнопка нужна даже когда полей ещё нет: поле часто заводят ровно тогда,
    // когда впервые понадобилось в конкретной задаче.
    var creator = state.canManageBoard
      ? '<div style="grid-column:1/-1">' +
        '<button class="ws-action" type="button" id="wsTaskFieldNew">+ Создать поле</button>' +
        inlineFieldHtml(null, "wsTaskFieldInline") + "</div>"
      : "";
    if (!fields.length && !creator) {
      return '<div id="wsTaskCustom" style="grid-column:1/-1"></div>';
    }
    // Поля брифа идут в один столбец с подписью слева: их читают сверху вниз
    // как анкету, а в двух колонках названия и значения перемешиваются.
    return '<div id="wsTaskCustom" class="ws-fieldrows" style="grid-column:1/-1;' +
      'margin-top:6px;padding-top:16px;border-top:1px solid #F0EBEB">' +
      '<div style="grid-column:1/-1;font-size:11px;font-weight:700;letter-spacing:.08em;' +
      'text-transform:uppercase;color:#AFA6A6">Поля</div>' +
      customFieldInputs(fields, values).map(fieldHtml).join("") + creator + "</div>";
  }

  function redrawCustomBlock(templateId) {
    var host = byId("wsTaskCustom");
    if (!host) return;
    // Введённое пользователем переносим в новый набор полей: смена шаблона не
    // должна стирать то, что уже набрали в общих полях доски.
    var previous = collectCustomValues(readFields(host), state.fields);
    var fields = visibleFields(templateId, previous);
    host.outerHTML = customBlockHtml(fields, previous);
  }

  /* ---------- пользовательские поля (ТЗ 8.1) ---------- */

  // Какие поля показывает карточка: сначала поля её шаблона в порядке шаблона,
  // затем общие поля доски. Поле с уже заполненным значением остаётся, даже
  // если его убрали из шаблона, — иначе значение нельзя было бы ни увидеть, ни
  // стереть. Та же логика на сервере, здесь она нужна для новой задачи, у
  // которой шаблон меняют прямо в форме.
  function visibleFields(templateId, values) {
    var template = state.templates.filter(function (row) {
      return row.id === templateId;
    })[0];
    var byId = {};
    state.fields.forEach(function (field) { byId[field.id] = field; });
    var ordered = [];
    var seen = {};
    ((template && template.field_ids) || []).forEach(function (id) {
      if (byId[id] && !seen[id]) { seen[id] = true; ordered.push(byId[id]); }
    });
    state.fields.forEach(function (field) {
      if (seen[field.id]) return;
      if (field.show_always || (values || {})[field.id] !== undefined) {
        seen[field.id] = true;
        ordered.push(field);
      }
    });
    return ordered;
  }

  function customFieldInputs(fields, values) {
    return fields.map(function (field) {
      var value = (values || {})[field.id];
      // Подпись слева от поля, обязательность — звёздочкой у названия.
      // «Обязательное поле» под каждым полем повторялось столько раз, сколько
      // полей в брифе, и о незаполненном всё равно сообщает сохранение.
      var base = { name: "cf_" + field.id, label: field.name, half: false,
        row: true, required: !!field.is_required };
      if (field.kind === "select") {
        return Object.assign(base, { type: "select", options: [{ value: "", label: "—" }].concat(
          (field.options || []).map(function (option) {
            return { value: option, label: option, selected: option === value };
          })) });
      }
      if (field.kind === "labels") {
        return Object.assign(base, { type: "chips", half: false,
          empty: "У поля нет вариантов",
          options: (field.options || []).map(function (option) {
            return { value: option, label: option,
              selected: (value || []).indexOf(option) >= 0 };
          }) });
      }
      if (field.kind === "user") {
        return Object.assign(base, { type: "select", options: [{ value: "", label: "—" }].concat(
          state.people.map(function (person) {
            return { value: person.id, label: person.name, selected: person.id === value };
          })) });
      }
      if (field.kind === "checkbox") {
        return Object.assign(base, { type: "checkbox", value: !!value, half: false });
      }
      if (field.kind === "textarea") {
        return Object.assign(base, { type: "textarea", value: value || "", half: false });
      }
      if (field.kind === "number") {
        return Object.assign(base, { type: "number", step: "any", value: value === undefined ? "" : value });
      }
      if (field.kind === "money") {
        return Object.assign(base, { type: "number", step: "0.01",
          label: field.name + ", " + ((field.config || {}).currency || "USD"),
          value: value === undefined ? "" : value });
      }
      if (field.kind === "date") {
        return Object.assign(base, { type: "date", value: value || "" });
      }
      if (field.kind === "url") {
        return Object.assign(base, { type: "url", placeholder: "https://",
          value: value || "" });
      }
      if (field.kind === "file") {
        return Object.assign(base, { type: "files", half: false, value: (value || []).slice() });
      }
      return Object.assign(base, { value: value || "" });
    });
  }

  function collectCustomValues(values, fields) {
    var custom = {};
    (fields || state.fields).forEach(function (field) {
      var raw = values["cf_" + field.id];
      if (raw === undefined) return;
      if (field.kind === "checkbox") {
        if (raw) custom[field.id] = true;
        return;
      }
      if (field.kind === "labels" || field.kind === "file") {
        if ((raw || []).length) custom[field.id] = raw;
        return;
      }
      if (raw === "" || raw === null) return;
      // Сумма уходит строкой: на сервере она станет Decimal, и превращать её по
      // дороге в double, который не умеет представить 0.1, незачем.
      custom[field.id] = field.kind === "number" ? Number(raw) : raw;
    });
    return custom;
  }

  // `prefill` приходит из быстрого добавления: название уже набрали в колонке,
  // и заставлять набирать его второй раз нельзя.
  function openTask(taskId, prefill) {
    var task = taskId ? findTask(taskId) : (prefill || {});
    if (!task) return;
    var editing = !!taskId;
    var canEdit = !editing || task.can_edit !== false;
    var canDelete = editing && task.can_delete !== false;

    byId("wsTaskTitle").textContent = editing ? "Задача" : "Новая задача";
    byId("wsTaskSubtitle").textContent = editing
      ? "Создана " + formatMoment(task.created_at)
      : "";
    var body = byId("wsTaskBody");
    // У существующей задачи набор полей приходит с сервера, у новой — считается
    // по выбранному шаблону здесь же и пересобирается при его смене.
    var fields = editing && task.field_ids
      ? task.field_ids.map(function (id) {
          return state.fields.filter(function (row) { return row.id === id; })[0];
        }).filter(Boolean)
      : visibleFields(defaultTemplateId(), task.custom_values);
    body.innerHTML = (!canEdit
      ? '<div class="ws-note" style="grid-column:1/-1">Вы можете посмотреть эту задачу, ' +
        'но изменить или перенести её может только автор, исполнитель или руководитель.</div>'
      : "") + (!editing && state.templates.length ? templatePickerHtml() : "") +
      taskFields(task).map(fieldHtml).join("") +
      customBlockHtml(fields, task.custom_values);
    if (!canEdit) {
      Array.prototype.forEach.call(body.querySelectorAll("input,select,textarea"), function (input) {
        input.disabled = true;
      });
    }
    byId("wsTaskSave").style.display = canEdit ? "" : "none";
    byId("wsTaskDelete").style.display = canDelete ? "" : "none";
    taskError("");
    byId("wsTaskStatus").textContent = "";
    byId("wsTaskModal").setAttribute("data-task-id", taskId || "");
    byId("wsTaskModal").setAttribute("data-can-edit", canEdit ? "true" : "false");
    byId("wsTaskModal").setAttribute(
      "data-template-id", editing ? (task.template_id || "") : (defaultTemplateId() || "")
    );
    showModal("wsTaskModal");
    var first = body.querySelector("[data-field]");
    if (first) first.focus();
  }

  function applyTemplateToTaskForm(templateId) {
    // Шаблон меняет только набор полей и их значения по умолчанию. Название,
    // приоритет, колонку, срок и исполнителей он не трогает: их вводят под
    // конкретную задачу, и подставленное значение приходилось бы стирать.
    redrawCustomBlock(templateId);
    var template = state.templates.filter(function (item) { return item.id === templateId; })[0];
    var host = byId("wsTaskCustom");
    if (!template || !host) return;
    state.fields.forEach(function (field) {
      var value = (template.custom_values || {})[field.id];
      if (value === undefined || value === null) return;
      var input = host.querySelector('[data-field="cf_' + field.id + '"]');
      if (input) {
        if (input.type === "checkbox") input.checked = !!value;
        else input.value = value;
        return;
      }
      var chips = host.querySelector('[data-chips="cf_' + field.id + '"]');
      if (chips) {
        chips.setAttribute("data-values", (value || []).join("\u0001"));
        Array.prototype.forEach.call(chips.querySelectorAll("[data-chip]"), function (chip) {
          var on = (value || []).indexOf(chip.getAttribute("data-chip")) >= 0;
          chip.classList.toggle("ws-chip-option--on", on);
          chip.setAttribute("aria-pressed", on ? "true" : "false");
        });
        return;
      }
      var list = host.querySelector('[data-checklist="cf_' + field.id + '"]');
      if (list) {
        Array.prototype.forEach.call(list.querySelectorAll("input[data-check]"), function (box) {
          box.checked = (value || []).indexOf(box.value) >= 0;
        });
      }
    });
  }

  // Шаблон по умолчанию подставляется сам: команда работает по одному брифу,
  // и выбирать его вручную в самом частом действии раздела — лишний шаг.
  function defaultTemplateId() {
    var found = state.templates.filter(function (row) { return row.is_default; })[0];
    return found ? found.id : null;
  }

  function templatePickerHtml() {
    var current = defaultTemplateId();
    return '<label class="ws-field" style="grid-column:1/-1"><span>Шаблон задачи</span>' +
      '<select class="ws-control ws-select" data-field="template_id">' +
      '<option value=""' + (current ? "" : " selected") + ">Без шаблона</option>" +
      state.templates.map(function (template) {
        return '<option value="' + escapeHtml(template.id) + '"' +
          (template.id === current ? " selected" : "") + ">" +
          escapeHtml(template.name) +
          (template.is_default ? " — по умолчанию" : "") + "</option>";
      }).join("") + "</select></label>";
  }

  function findTask(taskId) {
    var found = null;
    ((state.board && state.board.columns) || []).forEach(function (column) {
      column.tasks.forEach(function (task) {
        if (task.id === taskId) found = task;
      });
    });
    return found;
  }

  function taskError(message) {
    var host = byId("wsTaskError");
    host.textContent = message || "";
    host.style.display = message ? "" : "none";
  }

  async function saveTask() {
    var taskId = byId("wsTaskModal").getAttribute("data-task-id");
    if (taskId && byId("wsTaskModal").getAttribute("data-can-edit") !== "true") {
      taskError("У вас нет права изменять эту задачу");
      return;
    }
    var values = readFields(byId("wsTaskBody"));
    if (!String(values.title || "").trim()) {
      taskError("Укажите название задачи");
      return;
    }
    // Описание из карточки убрали, и в payload его нет намеренно: PATCH меняет
    // только присланные поля, поэтому текст у старых задач остаётся на месте.
    var payload = {
      title: values.title.trim(),
      // Без явного раздела задача ушла бы в первый доступный, а не в тот,
      // который сейчас открыт на доске.
      section_id: values.section_id || state.sectionId || null,
      status_id: values.status_id || null,
      priority: values.priority,
      start_date: values.start_date || null,
      due_date: values.due_date || null,
      assignee_ids: values.assignee_ids || [],
      custom_values: collectCustomValues(values)
    };
    if (!taskId && values.template_id) payload.template_id = values.template_id;

    taskError("");
    byId("wsTaskSave").disabled = true;
    byId("wsTaskStatus").textContent = "Сохраняем…";
    try {
      if (taskId) {
        await api.patch("/workspace/tasks/" + taskId, payload);
      } else {
        await api.post("/workspace/tasks", payload);
      }
      hideModal("wsTaskModal");
      await loadBoard();
    } catch (error) {
      taskError(error && error.message ? error.message : "Не удалось сохранить задачу");
    } finally {
      byId("wsTaskSave").disabled = false;
      byId("wsTaskStatus").textContent = "";
    }
  }

  async function deleteTask() {
    var taskId = byId("wsTaskModal").getAttribute("data-task-id");
    var task = findTask(taskId);
    if (!task || task.can_delete === false) {
      taskError("У вас нет права удалять эту задачу");
      return;
    }
    var confirmed = await askConfirm({
      title: "Удалить задачу?",
      message: "«" + task.title + "» и её вложения будут удалены безвозвратно.",
      confirmLabel: "Удалить",
      danger: true
    });
    if (!confirmed) return;
    try {
      await api.delete("/workspace/tasks/" + taskId);
      hideModal("wsTaskModal");
      await loadBoard();
    } catch (error) {
      taskError(error && error.message ? error.message : "Не удалось удалить задачу");
    }
  }

  /* ---------- настройки доски ---------- */

  async function openSettings() {
    if (!state.canManageBoard) return;
    await loadTemplates();
    showModal("wsSettingsModal");
    renderSettings();
  }

  function renderSettings() {
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-ws-settings]"),
      function (button) {
        button.classList.toggle(
          "ws-tab--active", button.getAttribute("data-ws-settings") === state.settingsTab
        );
      }
    );
    var host = byId("wsSettingsBody");
    if (state.settingsTab === "statuses") return renderStatusSettings(host);
    renderTemplateSettings(host);
  }

  function renderStatusSettings(host) {
    host.innerHTML = '<div style="display:grid;gap:9px">' +
      state.statuses.map(function (row) {
        return '<div class="ws-settings-row">' +
          '<span class="ws-dot" style="background:' + escapeHtml(row.color) + '"></span>' +
          '<span style="font-size:13px;font-weight:700;flex:1">' + escapeHtml(row.name) + "</span>" +
          (row.is_terminal
            ? '<span class="ws-chip" style="color:#16B57F;background:#E4F7F0">завершает</span>'
            : "") +
          '<span style="font-size:11.5px;color:#9B9292;font-weight:600">' +
          row.tasks + " задач</span>" +
          '<button class="ws-action" data-status-edit="' + escapeHtml(row.id) +
          '">Изменить</button>' +
          (row.is_system
            ? '<span style="font-size:11px;color:#B4ABAB;font-weight:600">базовая</span>'
            : '<button class="ws-action ws-action--danger" data-status-delete="' +
              escapeHtml(row.id) + '">Удалить</button>') +
          "</div>";
      }).join("") + "</div>" +
      '<button class="ws-btn ws-btn--primary" id="wsStatusCreate" type="button" ' +
      'style="margin-top:14px">Добавить колонку</button>';
  }

  function renderTemplateSettings(host) {
    host.innerHTML = (state.templates.length
      ? '<div style="display:grid;gap:9px">' + state.templates.map(function (template) {
        return '<div class="ws-settings-row">' +
          '<span style="font-size:13px;font-weight:700;flex:1">' + escapeHtml(template.name) +
          (template.is_default
            ? '<span class="ws-chip" style="color:#0E7350;background:#E4F7F0;' +
              'margin-left:7px">по умолчанию</span>'
            : "") + "</span>" +
          '<span style="font-size:11.5px;color:#9B9292;font-weight:600">' +
          ((template.field_ids || []).length
            ? (template.field_ids || []).map(function (id) {
                var field = state.fields.filter(function (row) { return row.id === id; })[0];
                return field ? field.name : null;
              }).filter(Boolean).join(" · ")
            : "полей нет") + "</span>" +
          '<button class="ws-action" data-template-edit="' + escapeHtml(template.id) +
          '">Изменить</button>' +
          '<button class="ws-action ws-action--danger" data-template-delete="' +
          escapeHtml(template.id) + '">Удалить</button></div>';
      }).join("") + "</div>"
      : '<div class="ws-note">Шаблонов нет. Шаблон задаёт, какие поля появятся ' +
        "в карточке и в каком порядке — например, бриф на креатив: вид, ГЕО, " +
        "формат, исходник, референс, ТЗ. Поля создаются прямо в шаблоне.</div>") +
      '<button class="ws-btn ws-btn--primary" id="wsTemplateCreate" type="button" ' +
      'style="margin-top:14px">Добавить шаблон</button>';
  }

  async function loadTemplates() {
    var page = await api.get("/workspace/task-templates");
    state.templates = page.items || [];
  }

  function openStatusForm(statusId) {
    var status = statusId
      ? state.statuses.filter(function (row) { return row.id === statusId; })[0]
      : { name: "", color: "#6A6161", is_terminal: false };
    if (!status) return;
    openForm({
      title: statusId ? "Колонка доски" : "Новая колонка",
      subtitle: "Колонки — это статусы задач из ТЗ 8.1",
      fields: [
        { name: "name", label: "Название", value: status.name, half: true },
        { name: "color", label: "Цвет", type: "color", value: status.color, half: true },
        { name: "is_terminal", label: "Задача в этой колонке считается выполненной",
          type: "checkbox", value: !!status.is_terminal }
      ],
      onSave: async function (values) {
        var payload = { name: values.name.trim(), color: values.color,
          is_terminal: values.is_terminal };
        if (!payload.name) throw new Error("Укажите название колонки");
        if (statusId) {
          await api.patch("/workspace/statuses/" + statusId, payload);
        } else {
          await api.post("/workspace/statuses", payload);
        }
        await loadBoard();
        renderSettings();
      },
      onDelete: statusId && !status.is_system ? async function () {
        await deleteStatus(status);
      } : null
    });
  }

  async function deleteStatus(status) {
    var target = null;
    if (status.tasks) {
      var others = state.statuses.filter(function (row) { return row.id !== status.id; });
      var names = others.map(function (row, index) {
        return (index + 1) + ". " + row.name;
      }).join("\n");
      var answer = await askPrompt({
        title: "Куда перенести задачи?",
        message: "В колонке «" + status.name + "» " + status.tasks +
          " задач. Введите номер колонки:\n\n" + names,
        confirmLabel: "Перенести и удалить"
      });
      if (answer === null) return;
      target = others[Number(answer) - 1];
      if (!target) throw new Error("Такой колонки в списке нет");
    } else if (!(await askConfirm({
      title: "Удалить колонку?",
      message: "«" + status.name + "» исчезнет с доски.",
      confirmLabel: "Удалить",
      danger: true
    }))) {
      return;
    }
    await api.delete(
      "/workspace/statuses/" + status.id + (target ? "?move_to=" + target.id : "")
    );
    await loadBoard();
    closeForm();
    renderSettings();
  }

  function openTemplateForm(templateId) {
    var template = templateId
      ? state.templates.filter(function (row) { return row.id === templateId; })[0]
      : { name: "", custom_values: {}, field_ids: [], is_default: false };
    if (!template) return;
    openForm({
      title: templateId ? "Шаблон задачи" : "Новый шаблон",
      subtitle: "Какие поля появятся в карточке и в каком порядке",
      fields: [
        { name: "name", label: "Название шаблона", value: template.name || "" },
        { name: "field_ids", label: "Поля шаблона", type: "fieldpicker",
          value: (template.field_ids || []).slice(),
          footer: '<div class="ws-pick-tools">' +
            '<button class="ws-action" type="button" id="wsFieldNew">+ Создать поле</button>' +
            "</div>" + inlineFieldHtml(null, "wsFieldInline") },
        { name: "is_default", label: "Ставить по умолчанию", type: "checkbox",
          value: !!template.is_default,
          hint: "Новая задача сразу открывается с этим шаблоном. По умолчанию " +
            "может быть только один — отметка снимется с прежнего." }
      ],
      after: function (body) {
        var picker = body.querySelector("[data-fieldpicker]");
        if (picker) bindPickerDrag(picker);
      },
      onSave: async function (values) {
        var payload = {
          name: String(values.name || "").trim(),
          field_ids: values.field_ids || [],
          is_default: !!values.is_default,
          // Значения по умолчанию задавались прямо здесь и путались с полями
          // самого шаблона: список «Вид крео / GEO / NTRCN» шёл дважды подряд,
          // сверху как состав, снизу как значения. Состав шаблона — это одно,
          // содержимое конкретной задачи — другое.
          custom_values: template.custom_values || {}
        };
        if (!payload.name) throw new Error("Укажите название шаблона");
        if (templateId) {
          await api.patch("/workspace/task-templates/" + templateId, payload);
        } else {
          await api.post("/workspace/task-templates", payload);
        }
        await loadTemplates();
        renderSettings();
      },
      onDelete: templateId ? async function () {
        if (!(await askConfirm({
          title: "Удалить шаблон?",
          message: "«" + template.name + "». Уже созданные по нему задачи останутся.",
          confirmLabel: "Удалить",
          danger: true
        }))) return;
        await api.delete("/workspace/task-templates/" + templateId);
        await loadTemplates();
        closeForm();
        renderSettings();
      } : null
    });
  }

  /* ---------- создание поля прямо в шаблоне ---------- */

  function bindInlineField(container, hostId, inTemplate) {
    container.addEventListener("click", function (event) {
      var target = event.target;
      if (!target.closest) return;
      if (target.closest("#wsFieldNew") || target.closest("#wsTaskFieldNew")) {
        event.preventDefault();
        return openInlineField(byId(hostId), null);
      }
      var edit = target.closest("[data-field-edit]");
      if (edit && !target.closest("#wsSettingsBody")) {
        event.preventDefault();
        return openInlineField(
          byId(hostId),
          state.fields.filter(function (row) {
            return row.id === edit.getAttribute("data-field-edit");
          })[0]
        );
      }
      var remove = target.closest("[data-field-delete]");
      if (remove && !target.closest("#wsSettingsBody")) {
        event.preventDefault();
        return deleteInlineField(remove.getAttribute("data-field-delete"), inTemplate);
      }
      var save = target.closest("[data-inline-save]");
      if (save) {
        event.preventDefault();
        return saveInlineField(inlineHost(save));
      }
      var cancel = target.closest("[data-inline-cancel]");
      if (cancel) {
        event.preventDefault();
        return closeInlineField(inlineHost(cancel));
      }
    });
  }

  function inlineHost(node) {
    return node.closest ? node.closest("[data-inline-field]") : null;
  }

  function openInlineField(host, field) {
    if (!host) return;
    var id = host.id;
    // Перерисовываем целиком: у редактирования и создания разные поля, и
    // подменять их по одному пришлось бы в четырёх местах.
    host.outerHTML = inlineFieldHtml(field || null, id);
    host = byId(id);
    host.style.display = "";
    host.setAttribute("data-editing", field ? field.id : "");
    applyKindVisibility(host, field ? field.kind : "text");
    var name = host.querySelector('[data-field="new_field_name"]');
    if (name) name.focus();
  }

  function closeInlineField(host) {
    if (host) host.style.display = "none";
  }

  async function saveInlineField(host) {
    var error = host.querySelector("[data-inline-error]");
    var fieldId = host.getAttribute("data-editing") || "";
    var existing = state.fields.filter(function (row) { return row.id === fieldId; })[0];
    var kind = existing
      ? existing.kind
      : host.querySelector('[data-field="new_field_kind"]').value;
    var name = host.querySelector('[data-field="new_field_name"]').value.trim();
    var options = String(host.querySelector('[data-field="new_field_options"]').value || "")
      .split("\n").map(function (line) { return line.trim(); }).filter(Boolean);
    error.textContent = "";
    if (!name) {
      error.textContent = "Укажите название поля";
      return;
    }
    // Поле, заведённое в шаблоне, принадлежит этому шаблону. Заведённое прямо
    // в карточке — общее для доски: его только что попросили в конкретной
    // задаче, но привязывать его не к чему.
    var inTemplate = !!byId("wsFormBody").querySelector("[data-fieldpicker]") &&
      host.closest("#wsFormBody");
    var payload = {
      name: name,
      is_required: host.querySelector('[data-field="new_field_required"]').checked,
      show_always: !inTemplate
    };
    if (OPTION_KINDS.indexOf(kind) >= 0) {
      if (!options.length) {
        error.textContent = "Добавьте хотя бы один вариант списка";
        return;
      }
      payload.options = options;
    }
    if (kind === "money") {
      payload.config = { currency: host.querySelector('[data-field="new_field_currency"]').value };
    }
    var button = host.querySelector("[data-inline-save]");
    button.disabled = true;
    try {
      var saved;
      if (existing) {
        saved = await api.patch("/workspace/fields/" + existing.id, payload);
      } else {
        payload.kind = kind;
        saved = await api.post("/workspace/fields", payload);
      }
      await loadBoard();
      if (inTemplate) {
        // Новое поле сразу отмечено в шаблоне: его для того и заводили.
        redrawPicker(existing ? null : saved.id);
        closeInlineField(byId(host.id));
      } else {
        redrawTaskFields();
      }
    } catch (requestError) {
      error.textContent = requestError && requestError.message
        ? requestError.message : "Не удалось сохранить поле";
    } finally {
      if (byId(host.id)) byId(host.id).querySelector("[data-inline-save]").disabled = false;
    }
  }

  async function deleteInlineField(fieldId, inTemplate) {
    var field = state.fields.filter(function (row) { return row.id === fieldId; })[0];
    if (!field) return;
    if (!(await askConfirm({
      title: "Удалить поле?",
      message: "Значения поля «" + field.name + "» пропадут из всех задач, " +
        "а его файлы будут удалены.",
      confirmLabel: "Удалить",
      danger: true
    }))) return;
    try {
      await api.delete("/workspace/fields/" + fieldId);
      await Promise.all([loadBoard(), loadTemplates()]);
      if (inTemplate) redrawPicker(null);
      else redrawTaskFields();
    } catch (error) {
      fail(error, "Не удалось удалить поле");
    }
  }

  // Блок полей карточки пересобирается на месте: введённое в остальную форму
  // остаётся, а только что созданное поле появляется сразу.
  function redrawTaskFields() {
    var host = byId("wsTaskCustom");
    if (!host) return;
    var previous = collectCustomValues(readFields(host), state.fields);
    var picker = byId("wsTaskModal").getAttribute("data-template-id") || null;
    var fields = visibleFields(picker, previous);
    host.outerHTML = customBlockHtml(fields, previous);
  }

  // Список полей перерисовывается по месту, чтобы не потерять то, что уже
  // набрали в остальной форме.
  function redrawPicker(checkId) {
    var host = byId("wsFormBody").querySelector("[data-fieldpicker]");
    if (!host) return;
    var picked = Array.prototype.map.call(
      host.querySelectorAll("input[data-pick]:checked"),
      function (input) { return input.value; }
    );
    if (checkId && picked.indexOf(checkId) < 0) picked.push(checkId);
    host.innerHTML = fieldPickerHtml({ name: "field_ids", value: picked });
    bindPickerDrag(host);
  }

  /* ---------- база знаний (ТЗ 8.2) ---------- */

  async function loadTree() {
    if (!state.canViewKnowledge) return false;
    var requestId = ++state.treeRequest;
    var treeHost = byId("wsKbTree");
    treeHost.setAttribute("aria-busy", "true");
    treeHost.innerHTML = loadingHtml("Загружаем базу знаний");
    if (!state.article) byId("wsDoc").innerHTML = loadingHtml("Загружаем статьи");
    try {
      var tree = await api.get("/knowledge/tree");
      if (requestId !== state.treeRequest) return false;
      state.tree = tree;
      renderTree();
      var canCreateArticle = state.canManageKnowledge || treeHasRight(tree.sections, "can_create");
      byId("wsSectionCreate").style.display = state.canManageKnowledge ? "" : "none";
      byId("wsArticleCreate").style.display = canCreateArticle ? "" : "none";
      byId("wsKbTreeActions").style.display =
        (state.canManageKnowledge || canCreateArticle) ? "flex" : "none";
      if (!state.article) renderDoc();
      return true;
    } catch (error) {
      if (requestId !== state.treeRequest) return false;
      state.tree = null;
      treeHost.innerHTML = errorHtml(
        error && error.message ? error.message : "Сервер не ответил",
        'data-ws-retry="tree"'
      );
      if (!state.article) {
        byId("wsDoc").innerHTML = '<div class="ws-state"><strong>Статьи недоступны</strong>' +
          '<span>Повторите загрузку в панели слева.</span></div>';
      }
      return false;
    } finally {
      if (requestId === state.treeRequest) treeHost.setAttribute("aria-busy", "false");
    }
  }

  function treeHasRight(sections, right) {
    return (sections || []).some(function (section) {
      return !!(section.rights && section.rights[right]) || treeHasRight(section.children, right);
    });
  }

  function renderTree() {
    var host = byId("wsKbTree");
    var tree = state.tree || { sections: [], loose_articles: [] };
    var html = tree.sections.map(function (section) {
      return sectionHtml(section, 0);
    }).join("");
    if (tree.loose_articles.length) {
      html += '<div style="font-size:10.5px;font-weight:700;color:#B4ABAB;text-transform:uppercase;' +
        'letter-spacing:.7px;padding:12px 9px 6px">Без раздела</div>' +
        tree.loose_articles.map(function (article) {
          return articleHtml(article, 0);
        }).join("");
    }
    host.innerHTML = html ||
      '<div style="padding:24px 8px;text-align:center;color:#9B9292;font-size:12px;' +
      'font-weight:600;line-height:1.6">База знаний пуста.' +
      (tree.can_manage ? "<br>Создайте первый раздел." : "") + "</div>";
  }

  function sectionHtml(section, depth) {
    var inner = section.articles.map(function (article) {
      return articleHtml(article, depth + 1);
    }).join("") + (section.children || []).map(function (child) {
      return sectionHtml(child, depth + 1);
    }).join("");
    var empty = !inner;
    var open = !state.collapsed[section.id];
    var bodyId = "wsKbBody-" + section.id;
    return "<div>" +
      '<div class="ws-tree-section" data-section="' + escapeHtml(section.id) + '" ' +
      (empty ? "" : 'role="button" tabindex="0" aria-expanded="' + (open ? "true" : "false") +
        '" aria-controls="' + escapeHtml(bodyId) + '" ') +
      'aria-label="Раздел ' + escapeHtml(section.title) + '" ' +
      'style="padding-left:' + (9 + depth * 12) + "px" +
      (empty ? ";cursor:default" : "") + '">' +
      // Треугольник поворачивается вместо смены символа: так строка не дёргается
      // по ширине при сворачивании.
      '<span aria-hidden="true" style="width:11px;flex-shrink:0;font-size:9px;color:#B4ABAB;' +
      "transition:transform .15s;display:inline-block;transform:rotate(" +
      (open ? "90" : "0") + 'deg)">' + (empty ? "" : "▶") + "</span>" +
      '<span style="font-size:13px">' + (section.icon ? escapeHtml(section.icon) : "📁") + "</span>" +
      '<span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' +
      escapeHtml(section.title) + "</span>" +
      (!open && !empty
        ? '<span style="font-size:10.5px;color:#B4ABAB;font-weight:700">' +
          countInside(section) + "</span>"
        : "") +
      (state.canManageKnowledge && section.rights && section.rights.can_manage
        ? '<button class="ws-action" type="button" aria-label="Настроить раздел ' +
          escapeHtml(section.title) + '" data-section-edit="' + escapeHtml(section.id) +
          '" style="height:26px;padding:0 7px;font-size:13px;line-height:20px">⋯</button>'
        : "") +
      "</div>" +
      (empty ? "" : '<div id="' + escapeHtml(bodyId) + '"' +
        (open ? "" : ' hidden style="display:none"') + ">" + inner + "</div>") +
      "</div>";
  }

  // Сколько всего внутри — показываем у свёрнутого раздела, иначе непонятно,
  // пустой он или просто закрыт.
  function countInside(section) {
    return (section.articles || []).length +
      (section.children || []).reduce(function (total, child) {
        return total + 1 + countInside(child);
      }, 0);
  }

  function expandTo(sectionId) {
    if (!sectionId || !state.tree) return;
    var parents = {};
    (function walk(sections) {
      (sections || []).forEach(function (section) {
        parents[section.id] = section.parent_id;
        walk(section.children);
      });
    })(state.tree.sections);
    var changed = false;
    var current = sectionId;
    var guard = 0;
    while (current && guard++ < 20) {
      if (state.collapsed[current]) {
        delete state.collapsed[current];
        changed = true;
      }
      current = parents[current];
    }
    if (changed) saveCollapsed();
  }

  function toggleSection(sectionId) {
    if (state.collapsed[sectionId]) delete state.collapsed[sectionId];
    else state.collapsed[sectionId] = true;
    saveCollapsed();
    renderTree();
  }

  // Свёрнутые разделы переживают перезагрузку: дерево — это навигация, и
  // раскрывать его заново после каждого F5 никто не станет.
  var COLLAPSED_KEY = "celestial.knowledge.collapsed";

  function loadCollapsed() {
    try {
      var raw = window.localStorage.getItem(COLLAPSED_KEY);
      return raw ? JSON.parse(raw) || {} : {};
    } catch (error) {
      return {};
    }
  }

  function saveCollapsed() {
    try {
      window.localStorage.setItem(COLLAPSED_KEY, JSON.stringify(state.collapsed));
    } catch (error) {
      // Приватный режим или переполненное хранилище — сворачивание всё равно
      // работает, просто не запоминается.
    }
  }

  function articleHtml(article, depth) {
    var active = state.article && state.article.id === article.id;
    var tone = ARTICLE_STATUS[article.status] || ARTICLE_STATUS.draft;
    return '<div class="ws-tree-item' + (active ? " ws-tree-item--active" : "") + '" ' +
      'role="button" tabindex="0" aria-current="' + (active ? "page" : "false") + '" ' +
      'data-article="' + escapeHtml(article.id) + '" style="padding-left:' +
      (9 + depth * 12) + 'px">' +
      '<span style="font-size:12px;opacity:.7">📄</span>' +
      '<span style="flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' +
      escapeHtml(article.title) + "</span>" +
      (article.status !== "published"
        ? '<span class="ws-dot" title="' + escapeHtml(tone.label) + '" style="background:' +
          tone.color + '"></span>'
        : "") + "</div>";
  }

  async function openArticle(articleId) {
    if (state.editing && state.dirty && !(await askConfirm({
      title: "Уйти без сохранения?",
      message: "Изменения в статье не сохранены и будут потеряны.",
      confirmLabel: "Уйти",
      danger: true
    }))) return;
    var host = byId("wsDoc");
    host.setAttribute("aria-busy", "true");
    host.innerHTML = loadingHtml("Открываем статью");
    try {
      state.article = await api.get("/knowledge/articles/" + articleId);
      state.blocks = (state.article.blocks || []).slice();
      state.editing = false;
      state.dirty = false;
      // Открытую статью в дереве должно быть видно — например, когда её нашли
      // поиском, а её раздел свёрнут.
      expandTo(state.article.section_id);
      renderTree();
      renderDoc();
      return true;
    } catch (error) {
      host.innerHTML = errorHtml(
        error && error.message ? error.message : "Не удалось открыть статью",
        'data-ws-retry-article="' + escapeHtml(articleId) + '"'
      );
      return false;
    } finally {
      host.setAttribute("aria-busy", "false");
    }
  }

  function renderDoc() {
    var host = byId("wsDoc");
    if (!state.article) {
      host.innerHTML = '<div style="padding:60px 20px;text-align:center;color:#9B9292">' +
        '<div style="font-size:15px;font-weight:700;color:#6A6161">Выберите статью слева</div>' +
        '<div style="font-size:12.5px;font-weight:500;margin-top:8px;line-height:1.6">' +
        "Здесь живут инструкции, регламенты и обучающие материалы команды.</div></div>";
      return;
    }
    var article = state.article;
    var rights = article.rights || {};
    var tone = ARTICLE_STATUS[article.status] || ARTICLE_STATUS.draft;

    host.innerHTML =
      '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:16px;' +
      'margin-bottom:18px;flex-wrap:wrap">' +
      '<div style="flex:1;min-width:220px">' +
      (state.editing
        ? '<input id="wsArticleTitle" class="ws-control" value="' + escapeHtml(article.title) +
          '" style="width:100%;height:46px;font-size:20px;font-weight:700;padding:0 13px">'
        : '<h2 style="font-family:\'Alumni Sans\',\'Inter\',sans-serif;font-size:26px;' +
          'font-weight:700;letter-spacing:-.4px">' + escapeHtml(article.title) + "</h2>") +
      '<div style="display:flex;align-items:center;gap:9px;margin-top:8px;flex-wrap:wrap">' +
      '<span class="ws-chip" style="color:' + tone.color + ";background:" + tone.background +
      '">' + escapeHtml(tone.label) + "</span>" +
      '<span style="font-size:11.5px;color:#9B9292;font-weight:600">изменено ' +
      escapeHtml(formatMoment(article.updated_at)) +
      (article.updated_by ? " · " + escapeHtml(article.updated_by) : "") + "</span></div></div>" +
      '<div style="display:flex;gap:8px;flex-wrap:wrap">' + docActions(rights) + "</div></div>" +
      (state.editing ? editorToolbarHtml() : "") +
      '<div id="wsBlocks">' + renderBlocks() + "</div>" +
      (state.editing
        ? '<button id="wsAddBlock" class="ws-action" type="button" style="margin-top:14px">' +
          "+ Добавить блок</button>"
        : "") +
      childrenHtml(article);
  }

  function docActions(rights) {
    if (state.editing) {
      return '<button id="wsArticleSave" class="ws-btn ws-btn--primary" type="button">' +
        "Сохранить</button>" +
        '<button id="wsArticleCancel" class="ws-btn" type="button">Отмена</button>';
    }
    var buttons = [];
    if (rights.can_edit) {
      buttons.push('<button id="wsArticleEdit" class="ws-btn ws-btn--primary" type="button">' +
        "Редактировать</button>");
      if (state.article.status !== "published") {
        buttons.push('<button id="wsArticlePublish" class="ws-btn" type="button">' +
          "Опубликовать</button>");
      }
      if (state.article.status !== "archived") {
        buttons.push('<button id="wsArticleArchive" class="ws-btn" type="button">' +
          "В архив</button>");
      } else {
        buttons.push('<button id="wsArticleRestore" class="ws-btn" type="button">' +
          "Вернуть из архива</button>");
      }
    }
    if (rights.can_delete) {
      buttons.push('<button id="wsArticleDelete" class="ws-btn" type="button" ' +
        'style="border-color:#F1D9D9;color:#B91414">Удалить</button>');
    }
    return buttons.join("");
  }

  function childrenHtml(article) {
    if (!article.children || !article.children.length) return "";
    return '<div style="margin-top:28px;padding-top:18px;border-top:1px solid #F0EBEB">' +
      '<div style="font-size:11px;font-weight:700;color:#9B9292;text-transform:uppercase;' +
      'letter-spacing:.7px;margin-bottom:10px">Вложенные страницы</div>' +
      article.children.map(function (child) {
        return '<div class="ws-tree-item" role="button" tabindex="0" data-article="' +
          escapeHtml(child.id) + '">' +
          '<span style="font-size:12px;opacity:.7">📄</span><span>' +
          escapeHtml(child.title) + "</span></div>";
      }).join("") + "</div>";
  }

  /* --- блочный редактор --- */

  function editorToolbarHtml() {
    return '<div class="ws-toolbar">' +
      '<button type="button" data-exec="bold" title="Жирный"><b>Ж</b></button>' +
      '<button type="button" data-exec="italic" title="Курсив"><i>К</i></button>' +
      '<button type="button" data-exec="underline" title="Подчёркнутый"><u>П</u></button>' +
      '<button type="button" data-exec="strikeThrough" title="Зачёркнутый"><s>З</s></button>' +
      '<button type="button" data-link="1" title="Ссылка">Ссылка</button>' +
      '<button type="button" data-unlink="1" title="Убрать ссылку">Без ссылки</button>' +
      '<span style="flex:1"></span>' +
      '<span style="font-size:11px;color:#9B9292;font-weight:600;align-self:center;' +
      'padding:0 6px">Форматирование применяется к выделенному тексту</span></div>';
  }

  function renderBlocks() {
    if (!state.blocks.length) {
      return state.editing
        ? '<div style="color:#B4ABAB;font-size:13px;font-weight:600;padding:10px 0">' +
          "Пусто. Нажмите «Добавить блок».</div>"
        : '<div style="color:#9B9292;font-size:13px;font-weight:600;padding:10px 0">' +
          "В статье пока ничего нет.</div>";
    }
    return state.blocks.map(function (block, index) {
      return '<div class="ws-block' + (state.editing ? " ws-block--editing" : "") +
        '" data-block="' + index + '">' +
        (state.editing ? blockToolsHtml(index) : "") +
        blockBodyHtml(block, index) + "</div>";
    }).join("");
  }

  function blockToolsHtml(index) {
    return '<div class="ws-block__tools">' +
      '<button class="ws-block__tool" type="button" data-block-menu="' + index +
      '" title="Тип блока" aria-label="Изменить тип блока">⋮</button></div>' +
      '<div style="position:absolute;right:4px;top:3px;display:flex;gap:2px">' +
      '<button class="ws-block__tool" type="button" data-block-up="' + index +
      '" title="Выше" aria-label="Переместить блок выше">↑</button>' +
      '<button class="ws-block__tool" type="button" data-block-down="' + index +
      '" title="Ниже" aria-label="Переместить блок ниже">↓</button>' +
      '<button class="ws-block__tool" type="button" data-block-remove="' + index +
      '" title="Удалить" aria-label="Удалить блок">×</button></div>';
  }

  function blockBodyHtml(block, index) {
    var editable = state.editing;
    var attrs = editable
      ? ' contenteditable="true" role="textbox" aria-multiline="true" data-edit="' + index +
        '" data-placeholder="Текст…"'
      : "";
    switch (block.type) {
      case "heading_1":
        return '<div class="ws-text ws-h1"' + attrs + ">" + (block.text || "") + "</div>";
      case "heading_2":
        return '<div class="ws-text ws-h2"' + attrs + ">" + (block.text || "") + "</div>";
      case "heading_3":
        return '<div class="ws-text ws-h3"' + attrs + ">" + (block.text || "") + "</div>";
      case "quote":
        return '<div class="ws-text ws-quote"' + attrs + ">" + (block.text || "") + "</div>";
      case "code":
        return '<pre class="ws-code"' + (editable
          ? ' contenteditable="true" data-edit="' + index + '"'
          : "") + ">" + escapeHtml(block.text || "") + "</pre>";
      case "divider":
        return '<div class="ws-divider"></div>';
      case "bulleted_list":
      case "numbered_list":
      case "checklist":
        return listHtml(block, index);
      case "table":
        return tableHtml(block, index);
      case "image":
        return mediaHtml(block, index, "image");
      case "video":
        return mediaHtml(block, index, "video");
      case "file":
        return mediaHtml(block, index, "file");
      case "page_link":
        return '<div class="ws-tree-item" data-article="' + escapeHtml(block.article_id) +
          '" style="border:1px solid #EBE6E6"><span style="font-size:12px;opacity:.7">📄</span>' +
          "<span>" + escapeHtml(block.title || "Страница") + "</span></div>";
      default:
        return '<div class="ws-text"' + attrs + ">" + (block.text || "") + "</div>";
    }
  }

  function listHtml(block, index) {
    var items = block.items || [];
    var body = items.map(function (item, itemIndex) {
      var marker = block.type === "numbered_list"
        ? (itemIndex + 1) + "."
        : block.type === "checklist"
          ? '<input type="checkbox" data-check-item="' + index + ":" + itemIndex + '"' +
            (item.checked ? " checked" : "") + (state.editing ? "" : " disabled") + ">"
          : "•";
      return '<div class="ws-li">' +
        '<span class="ws-li__marker">' + marker + "</span>" +
        '<div class="ws-text" style="flex:1"' +
        (state.editing
          ? ' contenteditable="true" data-edit-item="' + index + ":" + itemIndex + '"'
          : "") + ">" + (item.text || "") + "</div>" +
        (state.editing
          ? '<button class="ws-block__tool" type="button" data-item-remove="' + index + ":" +
            itemIndex + '">×</button>'
          : "") + "</div>";
    }).join("");
    return body + (state.editing
      ? '<button class="ws-action" type="button" data-item-add="' + index +
        '" style="margin-top:6px">+ пункт</button>'
      : "");
  }

  function tableHtml(block, index) {
    var rows = block.rows || [];
    return '<div style="overflow-x:auto"><table class="ws-table"><tbody>' +
      rows.map(function (row, rowIndex) {
        return "<tr>" + row.map(function (cell, cellIndex) {
          return "<td" + (state.editing
            ? ' contenteditable="true" data-cell="' + index + ":" + rowIndex + ":" + cellIndex + '"'
            : "") + ">" + (cell || "") + "</td>";
        }).join("") + "</tr>";
      }).join("") + "</tbody></table></div>" +
      (state.editing
        ? '<div style="display:flex;gap:6px;margin-top:8px">' +
          '<button class="ws-action" type="button" data-row-add="' + index + '">+ строка</button>' +
          '<button class="ws-action" type="button" data-col-add="' + index + '">+ колонка</button>' +
          "</div>"
        : "");
  }

  function mediaHtml(block, index, kind) {
    var url = block.attachment_id
      ? "/api/v1/knowledge/attachments/" + block.attachment_id
      : block.url;
    var body;
    if (!url) {
      body = '<div style="color:#B4ABAB;font-size:12.5px;font-weight:600">Файл не выбран</div>';
    } else if (kind === "image") {
      body = '<img src="' + escapeHtml(url) + '" alt="' + escapeHtml(block.caption || "") +
        '" style="max-width:100%;border-radius:12px;display:block">';
    } else if (kind === "video") {
      body = '<video src="' + escapeHtml(url) + '" controls ' +
        'style="max-width:100%;border-radius:12px;display:block"></video>';
    } else {
      body = '<a href="' + escapeHtml(url) + '" target="_blank" rel="noopener noreferrer" ' +
        'style="display:flex;align-items:center;gap:10px;border:1px solid #EBE6E6;' +
        'border-radius:12px;padding:12px 14px;font-size:12.5px;font-weight:700">' +
        "<span>📎</span><span>" + escapeHtml(block.name || "Файл") + "</span></a>";
    }
    return body +
      (state.editing
        ? '<input class="ws-control" data-caption="' + index + '" value="' +
          escapeHtml(block.caption || "") + '" placeholder="Подпись" ' +
          'style="width:100%;height:34px;margin-top:8px;padding:0 11px;font-size:12px">' +
          '<button class="ws-action" type="button" data-media-pick="' + index + ":" + kind +
          '" style="margin-top:6px">' + (url ? "Заменить файл" : "Выбрать файл") + "</button>"
        : block.caption
          ? '<div style="font-size:11.5px;color:#9B9292;font-weight:600;margin-top:6px">' +
            escapeHtml(block.caption) + "</div>"
          : "");
  }

  function syncBlocksFromDom() {
    var host = byId("wsBlocks");
    if (!host) return;
    Array.prototype.forEach.call(host.querySelectorAll("[data-edit]"), function (node) {
      var index = Number(node.getAttribute("data-edit"));
      if (!state.blocks[index]) return;
      state.blocks[index].text = state.blocks[index].type === "code"
        ? node.textContent
        : node.innerHTML;
    });
    Array.prototype.forEach.call(host.querySelectorAll("[data-edit-item]"), function (node) {
      var parts = node.getAttribute("data-edit-item").split(":");
      var block = state.blocks[Number(parts[0])];
      if (block && block.items && block.items[Number(parts[1])]) {
        block.items[Number(parts[1])].text = node.innerHTML;
      }
    });
    Array.prototype.forEach.call(host.querySelectorAll("[data-cell]"), function (node) {
      var parts = node.getAttribute("data-cell").split(":");
      var block = state.blocks[Number(parts[0])];
      if (block && block.rows && block.rows[Number(parts[1])]) {
        block.rows[Number(parts[1])][Number(parts[2])] = node.innerHTML;
      }
    });
    Array.prototype.forEach.call(host.querySelectorAll("[data-caption]"), function (node) {
      var block = state.blocks[Number(node.getAttribute("data-caption"))];
      if (block) block.caption = node.value;
    });
  }

  function redrawBlocks() {
    syncBlocksFromDom();
    var host = byId("wsBlocks");
    if (host) host.innerHTML = renderBlocks();
    state.dirty = true;
  }

  function newBlock(type) {
    var block = { id: String(Date.now()) + Math.random().toString(16).slice(2, 6), type: type };
    if (type === "table") {
      block.rows = [["", ""], ["", ""]];
    } else if (type === "bulleted_list" || type === "numbered_list" || type === "checklist") {
      block.items = [{ text: "", checked: false }];
    } else if (type === "image" || type === "video" || type === "file") {
      block.caption = "";
    } else if (type !== "divider" && type !== "page_link") {
      block.text = "";
    }
    return block;
  }

  function showBlockMenu(anchor, onPick) {
    closeBlockMenu();
    state.blockMenuAnchor = anchor;
    var menu = document.createElement("div");
    menu.className = "ws-menu";
    menu.id = "wsBlockMenu";
    menu.setAttribute("role", "menu");
    menu.innerHTML = BLOCK_MENU.map(function (item) {
      return '<button type="button" role="menuitem" data-pick="' + item.type + '">' +
        escapeHtml(item.label) + "</button>";
    }).join("");
    document.body.appendChild(menu);
    var box = anchor.getBoundingClientRect();
    menu.style.left = Math.min(box.left, window.innerWidth - 250) + "px";
    menu.style.top = (box.bottom + window.scrollY + 6) + "px";
    menu.addEventListener("click", function (event) {
      var button = event.target.closest ? event.target.closest("[data-pick]") : null;
      if (!button) return;
      closeBlockMenu();
      onPick(button.getAttribute("data-pick"));
    });
    var first = menu.querySelector("button");
    if (first) first.focus();
  }

  function closeBlockMenu() {
    var menu = byId("wsBlockMenu");
    if (menu) menu.remove();
  }

  function pickFile(accept, onFile) {
    var picker = byId("wsFilePicker");
    picker.value = "";
    picker.accept = accept;
    picker.onchange = function () {
      var file = picker.files && picker.files[0];
      picker.value = "";
      if (file) onFile(file);
    };
    picker.click();
  }

  async function uploadFile(file) {
    var form = new FormData();
    form.append("file", file);
    // Новый API проверяет право на раздел ещё до сохранения файла. Старые версии
    // FastAPI спокойно игнорируют дополнительное multipart-поле, поэтому клиент
    // остаётся совместимым во время поэтапного деплоя.
    if (state.article && state.article.section_id) {
      form.append("section_id", state.article.section_id);
    }
    return api.upload("/knowledge/attachments", form);
  }

  async function saveArticle() {
    syncBlocksFromDom();
    var titleInput = byId("wsArticleTitle");
    var title = titleInput ? titleInput.value.trim() : state.article.title;
    if (!title) {
      notify({ title: "У статьи должно быть название" });
      return;
    }
    try {
      await api.patch("/knowledge/articles/" + state.article.id, {
        title: title, blocks: state.blocks
      });
      state.dirty = false;
      state.editing = false;
      await openArticle(state.article.id);
      await loadTree();
    } catch (error) {
      fail(error, "Не удалось сохранить статью");
    }
  }

  async function setArticleStatus(status) {
    try {
      await api.patch("/knowledge/articles/" + state.article.id, { status: status });
      await openArticle(state.article.id);
      await loadTree();
    } catch (error) {
      fail(error, "Не удалось изменить статус статьи");
    }
  }

  async function deleteArticle() {
    if (!(await askConfirm({
      title: "Удалить статью?",
      message: "«" + state.article.title + "» и её вложения будут удалены безвозвратно.",
      confirmLabel: "Удалить",
      danger: true
    }))) return;
    try {
      await api.delete("/knowledge/articles/" + state.article.id);
      state.article = null;
      state.blocks = [];
      await loadTree();
      renderDoc();
    } catch (error) {
      fail(error, "Не удалось удалить статью");
    }
  }

  function openSectionForm(sectionId) {
    var section = sectionId ? findSection(state.tree.sections, sectionId) : null;
    if (sectionId && !section) return;
    var options = [{ value: "", label: "Верхний уровень" }];
    flattenSections(state.tree ? state.tree.sections : [], 0, function (item, depth) {
      if (sectionId && item.id === sectionId) return;
      options.push({
        value: item.id,
        label: new Array(depth + 1).join("— ") + item.title,
        selected: section && section.parent_id === item.id
      });
    });
    openForm({
      title: sectionId ? "Раздел базы знаний" : "Новый раздел",
      subtitle: "Разделы образуют дерево, права наследуются сверху вниз",
      fields: [
        { name: "title", label: "Название", value: section ? section.title : "", half: true },
        { name: "icon", label: "Значок", value: section && section.icon ? section.icon : "",
          half: true, placeholder: "📁", hint: "любой эмодзи" },
        { name: "parent_id", label: "Внутри раздела", type: "select", options: options }
      ],
      note: sectionId
        ? '<button class="ws-action" type="button" id="wsAccessOpen">Настроить доступ</button>'
        : null,
      onSave: async function (values) {
        var payload = {
          title: values.title.trim(),
          icon: values.icon || null,
          parent_id: values.parent_id || null
        };
        if (!payload.title) throw new Error("Укажите название раздела");
        if (sectionId) {
          await api.patch("/knowledge/sections/" + sectionId, payload);
        } else {
          await api.post("/knowledge/sections", payload);
        }
        await loadTree();
      },
      onDelete: sectionId ? async function () {
        if (!(await askConfirm({
          title: "Удалить раздел?",
          message: "«" + section.title + "» вместе с настройками доступа.",
          confirmLabel: "Удалить",
          danger: true
        }))) return;
        await api.delete("/knowledge/sections/" + sectionId);
        await loadTree();
        closeForm();
      } : null
    });
  }

  function findSection(sections, sectionId) {
    for (var index = 0; index < (sections || []).length; index += 1) {
      if (sections[index].id === sectionId) return sections[index];
      var found = findSection(sections[index].children, sectionId);
      if (found) return found;
    }
    return null;
  }

  function flattenSections(sections, depth, visit) {
    (sections || []).forEach(function (section) {
      visit(section, depth);
      flattenSections(section.children, depth + 1, visit);
    });
  }

  var ACCESS_RIGHTS = [
    { key: "view", label: "просмотр" },
    { key: "create", label: "создание" },
    { key: "edit", label: "правка" },
    { key: "delete", label: "удаление" },
    { key: "manage", label: "доступ" }
  ];

  function accessRowHtml(scope, id, name, entry, rights) {
    var key = scope + ":" + id;
    return '<div style="border:1px solid #EBE6E6;border-radius:12px;padding:11px 14px">' +
      '<label style="display:flex;align-items:center;gap:8px;font-size:13px;font-weight:700">' +
      '<input type="checkbox" data-access="' + escapeHtml(key) + '"' +
      (entry.configured ? " checked" : "") + ">" + escapeHtml(name) + "</label>" +
      '<div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:9px;padding-left:26px">' +
      (rights || ACCESS_RIGHTS).map(function (right) {
        return '<label style="display:flex;align-items:center;gap:6px;font-size:11.5px;' +
          'font-weight:600;color:#6A6161"><input type="checkbox" data-right="' +
          escapeHtml(key) + ":" + right.key + '"' +
          (entry["can_" + right.key] ? " checked" : "") + ">" + right.label + "</label>";
      }).join("") + "</div></div>";
  }

  /* Правила из формы доступа — одинаково для базы знаний и разделов доски.
     Неотмеченный адресат правила не получает: пустой список означает «раздел
     открыт», а не «закрыт всем». */
  function readAccessRules(body, rights) {
    var list = rights || ACCESS_RIGHTS;
    var rules = [];
    Array.prototype.forEach.call(body.querySelectorAll("[data-access]"), function (input) {
      if (!input.checked) return;
      var key = input.getAttribute("data-access");
      var parts = key.split(":");
      var rule = parts[0] === "role" ? { role_id: parts[1] } : { user_id: parts[1] };
      list.forEach(function (right) {
        var box = body.querySelector('[data-right="' + key + ":" + right.key + '"]');
        rule["can_" + right.key] = !!(box && box.checked);
      });
      rules.push(rule);
    });
    return rules;
  }

  function accessGroupHtml(title, hint, rows) {
    return '<div style="grid-column:1/-1;margin-top:6px">' +
      '<div style="font-size:11px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;' +
      'color:#AFA6A6;margin-bottom:8px">' + escapeHtml(title) + "</div>" +
      '<div style="font-size:11.5px;color:#9B9292;font-weight:600;line-height:1.5;' +
      'margin-bottom:9px">' + escapeHtml(hint) + "</div>" +
      '<div style="display:grid;gap:9px">' + rows + "</div></div>";
  }

  async function openAccessForm(sectionId) {
    var access = await api.get("/knowledge/sections/" + sectionId + "/access");
    openForm({
      title: "Доступ к разделу",
      subtitle: "«" + access.title + "»" + (access.inherited
        ? " — сейчас права наследуются от родителя"
        : ""),
      html: accessGroupHtml(
        "Роли",
        "Права получают все, у кого эта роль.",
        access.roles.map(function (role) {
          return accessRowHtml("role", role.role_id, role.role_name, role);
        }).join("")
      ) + accessGroupHtml(
        "Отдельные люди",
        "Именное правило сильнее правила его роли. Достаточно отметить одного " +
          "человека, чтобы раздел стал виден только ему.",
        access.users.map(function (person) {
          return accessRowHtml("user", person.user_id, person.user_name, person);
        }).join("")
      ),
      note: "Отмеченный адресат получает права явно. Неотмеченные наследуют права от " +
        "родительского раздела; если явных правил нет ни у кого, раздел виден всем.",
      onSave: async function () {
        await api.request("/knowledge/sections/" + sectionId + "/access", {
          method: "PUT",
          body: JSON.stringify({ rules: readAccessRules(byId("wsFormBody")) })
        });
        await loadTree();
      }
    });
  }

  function openArticleForm() {
    var options = state.canManageKnowledge ? [{ value: "", label: "Без раздела" }] : [];
    flattenSections(state.tree ? state.tree.sections : [], 0, function (item, depth) {
      if (item.rights && !item.rights.can_create) return;
      options.push({ value: item.id, label: new Array(depth + 1).join("— ") + item.title });
    });
    if (!options.length) {
      fail(null, "Нет раздела, в котором вам разрешено создавать статьи");
      return;
    }
    openForm({
      title: "Новая статья",
      subtitle: "Статья создаётся черновиком — команда её пока не увидит",
      fields: [
        { name: "title", label: "Название", value: "" },
        { name: "section_id", label: "Раздел", type: "select", options: options }
      ],
      onSave: async function (values) {
        if (!values.title.trim()) throw new Error("Укажите название статьи");
        var article = await api.post("/knowledge/articles", {
          title: values.title.trim(),
          section_id: values.section_id || null,
          blocks: [],
          status: "draft"
        });
        await loadTree();
        await openArticle(article.id);
        state.editing = true;
        renderDoc();
      }
    });
  }

  async function runSearch(query) {
    var host = byId("wsKbResults");
    var requestId = ++state.searchRequest;
    if (!query || query.length < 2) {
      host.style.display = "none";
      host.innerHTML = "";
      return;
    }
    host.style.display = "";
    host.innerHTML = '<div style="padding:10px 9px;font-size:11.5px;color:#9B9292;' +
      'font-weight:600" role="status">Ищем…</div>';
    try {
      var page = await api.get("/knowledge/articles/search?q=" + encodeURIComponent(query));
      if (requestId !== state.searchRequest) return;
      host.innerHTML = (page.items || []).length
      ? '<div style="font-size:10.5px;font-weight:700;color:#B4ABAB;text-transform:uppercase;' +
        'letter-spacing:.7px;padding:4px 9px 6px">Найдено: ' + page.items.length + "</div>" +
        page.items.map(function (article) {
          return '<div class="ws-tree-item" role="button" tabindex="0" data-article="' +
            escapeHtml(article.id) + '" ' +
            'style="align-items:flex-start;flex-direction:column;gap:3px">' +
            "<span>" + escapeHtml(article.title) + "</span>" +
            '<span style="font-size:10.5px;color:#9B9292;font-weight:500;line-height:1.5">' +
            escapeHtml(article.excerpt || "") + "</span></div>";
        }).join("")
      : '<div style="padding:10px 9px;font-size:11.5px;color:#9B9292;font-weight:600">' +
        "Ничего не найдено</div>";
    } catch (error) {
      if (requestId !== state.searchRequest) return;
      host.innerHTML = '<div style="padding:10px 9px;font-size:11.5px;color:#B91414;' +
        'font-weight:600">' + escapeHtml(error && error.message
          ? error.message : "Поиск временно недоступен") + '</div>';
    }
  }

  /* ---------- события ---------- */

  function bindTasks() {
    byId("wsSearch").addEventListener("input", function (event) {
      var value = event.target.value;
      window.clearTimeout(state.searchTimer);
      state.searchTimer = window.setTimeout(function () {
        state.filters.search = value.trim();
        loadBoard().catch(showFailure);
      }, 300);
    });
    ["Assignee", "Status", "Priority"].forEach(function (name) {
      byId("ws" + name + "Filter").addEventListener("change", function (event) {
        state.filters[name.toLowerCase()] = event.target.value;
        saveView();
        loadBoard().catch(showFailure);
      });
    });
    byId("wsMineFilter").addEventListener("click", function () {
      if (!state.user) return;
      state.filters.assignee = isMine() ? "" : state.user.id;
      byId("wsAssigneeFilter").value = state.filters.assignee;
      saveView();
      loadBoard().catch(showFailure);
    });
    byId("wsResetFilters").addEventListener("click", function () {
      state.filters = { search: "", assignee: "", status: "", priority: "" };
      byId("wsSearch").value = "";
      byId("wsAssigneeFilter").value = "";
      byId("wsStatusFilter").value = "";
      byId("wsPriorityFilter").value = "";
      saveView();
      loadBoard().catch(showFailure);
    });

    var sectionHost = byId("wsSections");
    if (sectionHost) {
      sectionHost.addEventListener("click", function (event) {
        var target = event.target;
        var closest = function (selector) {
          return target.closest ? target.closest(selector) : null;
        };
        if (closest("[data-ws-section-add]")) {
          return openBoardSectionForm(null).catch(showFailure);
        }
        var gear = closest("[data-ws-section-edit]");
        if (gear) {
          return openBoardSectionForm(gear.getAttribute("data-ws-section-edit"))
            .catch(showFailure);
        }
        var tab = closest("[data-ws-section]");
        if (!tab) return;
        var id = tab.getAttribute("data-ws-section");
        if (id === state.sectionId) return;
        state.sectionId = id;
        saveView();
        loadBoard().catch(showFailure);
      });
    }

    byId("wsTaskCreate").addEventListener("click", function () { openTask(null); });
    byId("wsBoardSettings").addEventListener("click", function () {
      openSettings().catch(showFailure);
    });
    byId("wsBoard").addEventListener("click", function (event) {
      var target = event.target;
      var closest = function (selector) {
        return target.closest ? target.closest(selector) : null;
      };
      if (closest('[data-ws-retry="board"]')) return loadBoard();
      var more = closest("[data-card-menu]");
      if (more) {
        event.stopPropagation();
        return openCardMenu(more);
      }
      var quickOpen = closest("[data-quick-open]");
      if (quickOpen) return openQuick(quickOpen.getAttribute("data-quick-open"));
      if (closest("[data-quick]")) return;
      var fold = closest("[data-fold]");
      if (fold) {
        var columnId = fold.getAttribute("data-fold");
        if (state.folded[columnId]) delete state.folded[columnId];
        else state.folded[columnId] = true;
        writeStore(FOLD_KEY, state.folded);
        return renderBoard();
      }
      var card = closest(".ws-card");
      if (card) openTask(card.getAttribute("data-task"));
    });
    // Правая кнопка на карточке — то же меню: так к нему привыкли в Notion.
    byId("wsBoard").addEventListener("contextmenu", function (event) {
      var card = event.target.closest ? event.target.closest(".ws-card") : null;
      if (!card) return;
      var button = card.querySelector("[data-card-menu]");
      if (!button) return;
      event.preventDefault();
      openCardMenu(button);
    });
    byId("wsBoard").addEventListener("keydown", function (event) {
      var quick = event.target.closest
        ? event.target.closest("[data-quick] [data-quick-input]") : null;
      if (quick) {
        if (event.key === "Enter" && !event.shiftKey) {
          event.preventDefault();
          quickCreate(quick.closest("[data-quick]").getAttribute("data-quick"), quick.value);
        } else if (event.key === "Escape") {
          event.preventDefault();
          event.stopPropagation();
          closeQuick();
        }
        return;
      }
      if (event.key !== "Enter" && event.key !== " ") return;
      // На «⋯» клавиша уже сработает как нажатие кнопки — открывать заодно и
      // саму задачу нельзя.
      if (event.target.closest && event.target.closest("[data-card-menu]")) return;
      var fold = event.target.closest ? event.target.closest(".ws-column--collapsed") : null;
      if (fold) {
        event.preventDefault();
        delete state.folded[fold.getAttribute("data-fold")];
        writeStore(FOLD_KEY, state.folded);
        return renderBoard();
      }
      var card = event.target.closest ? event.target.closest(".ws-card") : null;
      if (!card) return;
      event.preventDefault();
      openTask(card.getAttribute("data-task"));
    });
    // Композер закрывается, когда из него ушли: оставленный на другой колонке
    // он путал бы, куда попадёт следующая задача.
    byId("wsBoard").addEventListener("focusout", function (event) {
      if (!state.quick || !event.target.hasAttribute) return;
      if (!event.target.hasAttribute("data-quick-input")) return;
      var text = event.target.value;
      window.setTimeout(function () {
        var active = document.activeElement;
        if (active && active.hasAttribute && active.hasAttribute("data-quick-input")) return;
        if (text.trim()) return;
        closeQuick();
      }, 0);
    });
    bindDragAndDrop();
    bindCardMenu();
    bindSortMenu();

    byId("wsTaskClose").addEventListener("click", function () {
      hideModal("wsTaskModal");
    });
    byId("wsTaskModal").addEventListener("click", function (event) {
      if (event.target === event.currentTarget) hideModal("wsTaskModal");
    });
    byId("wsTaskBody").addEventListener("change", function (event) {
      if (event.target && event.target.getAttribute("data-field") === "template_id") {
        byId("wsTaskModal").setAttribute("data-template-id", event.target.value || "");
        applyTemplateToTaskForm(event.target.value);
      }
    });
    bindFileControls(byId("wsTaskBody"));
    bindChips(byId("wsTaskBody"));
    bindInlineField(byId("wsTaskBody"), "wsTaskFieldInline", false);
    byId("wsTaskModal").addEventListener("keydown", function (event) {
      // Cmd/Ctrl+Enter сохраняет откуда угодно, в том числе из описания: в
      // многострочном поле обычный Enter — это перенос строки.
      if (event.key === "Enter" && (event.metaKey || event.ctrlKey) &&
        byId("wsTaskSave").style.display !== "none") {
        event.preventDefault();
        saveTask();
        return;
      }
      if (event.key === "Enter" && event.target.tagName !== "TEXTAREA" &&
        event.target.tagName !== "SELECT" &&
        event.target.tagName !== "BUTTON" && !event.target.isContentEditable &&
        byId("wsTaskSave").style.display !== "none") {
        event.preventDefault();
        saveTask();
      }
    });
    byId("wsTaskSave").addEventListener("click", function () { saveTask(); });
    byId("wsTaskDelete").addEventListener("click", function () { deleteTask(); });

    byId("wsSettingsClose").addEventListener("click", function () {
      hideModal("wsSettingsModal");
    });
    byId("wsSettingsModal").addEventListener("click", function (event) {
      if (event.target === event.currentTarget) hideModal("wsSettingsModal");
    });
    Array.prototype.forEach.call(
      document.querySelectorAll("[data-ws-settings]"),
      function (button) {
        button.addEventListener("click", function () {
          state.settingsTab = button.getAttribute("data-ws-settings");
          renderSettings();
        });
      }
    );
    byId("wsSettingsBody").addEventListener("click", async function (event) {
      var target = event.target;
      if (!target.closest) return;
      if (target.closest("#wsStatusCreate")) return openStatusForm(null);
      if (target.closest("#wsTemplateCreate")) return openTemplateForm(null);
      var edit = target.closest("[data-status-edit]");
      if (edit) return openStatusForm(edit.getAttribute("data-status-edit"));
      var remove = target.closest("[data-status-delete]");
      if (remove) {
        var status = state.statuses.filter(function (row) {
          return row.id === remove.getAttribute("data-status-delete");
        })[0];
        return deleteStatus(status).catch(function (error) {
          fail(error, "Не удалось удалить колонку");
        });
      }
      var templateEdit = target.closest("[data-template-edit]");
      if (templateEdit) return openTemplateForm(templateEdit.getAttribute("data-template-edit"));
      var templateDelete = target.closest("[data-template-delete]");
      if (templateDelete) {
        var template = state.templates.filter(function (row) {
          return row.id === templateDelete.getAttribute("data-template-delete");
        })[0];
        if (!template || !(await askConfirm({
          title: "Удалить шаблон?",
          message: "«" + template.name + "». Уже созданные по нему задачи останутся.",
          confirmLabel: "Удалить",
          danger: true
        }))) return;
        return api.delete("/workspace/task-templates/" + template.id)
          .then(loadTemplates).then(renderSettings)
          .catch(function (error) { fail(error, "Не удалось удалить шаблон"); });
      }
    });

  }

  // Общая форма живёт на обеих страницах: колонки и поля доски на «Задачах»,
  // разделы и права — в «Базе знаний».
  function bindShared() {
    byId("wsFormClose").addEventListener("click", closeForm);
    byId("wsFormModal").addEventListener("click", function (event) {
      if (event.target === event.currentTarget) closeForm();
    });
    byId("wsFormSave").addEventListener("click", function () { submitForm().catch(function () {}); });
    byId("wsFormModal").addEventListener("keydown", function (event) {
      if (event.key === "Enter" && event.target.tagName !== "TEXTAREA" &&
        event.target.tagName !== "SELECT" &&
        event.target.tagName !== "BUTTON" && !event.target.isContentEditable) {
        event.preventDefault();
        submitForm().catch(function () {});
      }
    });
    byId("wsFormDelete").addEventListener("click", function () {
      if (state.form && state.form.onDelete) {
        state.form.onDelete().catch(function (error) {
          formError(error && error.message ? error.message : "Не удалось удалить");
        });
      }
    });
    byId("wsFormBody").addEventListener("click", function (event) {
      var target = event.target;
      if (!target.closest) return;
      var button = target.closest("#wsAccessOpen");
      if (!button || !state.form || !state.form.sectionId) return;
      openAccessForm(state.form.sectionId).catch(showFailure);
    });
    byId("wsFormBody").addEventListener("change", function (event) {
      if (event.target && event.target.getAttribute("data-field") === "kind") {
        applyKindVisibility(byId("wsFormBody"), event.target.value);
      }
      if (event.target && event.target.getAttribute("data-field") === "new_field_kind") {
        applyKindVisibility(byId("wsFieldInline"), event.target.value);
      }
    });
    bindChips(byId("wsFormBody"));
    bindInlineField(byId("wsFormBody"), "wsFieldInline", true);

  }

  function bindKnowledge() {
    byId("wsSectionCreate").addEventListener("click", function () { openSectionForm(null); });
    byId("wsArticleCreate").addEventListener("click", openArticleForm);
    byId("wsKbSearch").addEventListener("input", function (event) {
      var value = event.target.value.trim();
      window.clearTimeout(state.searchTimer);
      state.searchTimer = window.setTimeout(function () {
        runSearch(value).catch(showFailure);
      }, 300);
    });
    byId("wsKbTree").addEventListener("click", onTreeClick);
    byId("wsKbResults").addEventListener("click", onTreeClick);
    byId("wsDoc").addEventListener("click", onDocClick);
    [byId("wsKbTree"), byId("wsKbResults"), byId("wsDoc")].forEach(function (host) {
      host.addEventListener("keydown", function (event) {
        if (event.key !== "Enter" && event.key !== " ") return;
        var control = event.target.closest ? event.target.closest('[role="button"]') : null;
        if (!control) return;
        event.preventDefault();
        control.click();
      });
    });
    byId("wsDoc").addEventListener("input", function () { state.dirty = true; });
    // Вставка всегда как обычный текст: иначе в блок попадает чужая разметка со
    // стилями и скриптами, и сервер вырежет её уже после того, как всё съехало.
    byId("wsDoc").addEventListener("paste", function (event) {
      if (!state.editing) return;
      var editable = event.target.closest
        ? event.target.closest("[contenteditable=\"true\"]") : null;
      if (!editable) return;
      event.preventDefault();
      var text = (event.clipboardData || window.clipboardData).getData("text/plain");
      document.execCommand("insertText", false, text);
    });

  }

  // Escape, Tab-ловушка модалок и предупреждение о несохранённой статье —
  // одинаковы на обеих страницах.
  /* Горячие клавиши доски. Работают только когда не набирают текст: иначе «n»
     в названии задачи открывала бы новую карточку. */
  function boardShortcut(event) {
    if (state.tab !== "tasks") return false;
    if (event.metaKey || event.ctrlKey || event.altKey) return false;
    var target = event.target;
    var typing = target && (target.isContentEditable ||
      ["INPUT", "TEXTAREA", "SELECT"].indexOf(target.tagName) >= 0);
    if (typing) return false;
    if (event.key === "/") {
      event.preventDefault();
      byId("wsSearch").focus();
      byId("wsSearch").select();
      return true;
    }
    if (event.key === "n" || event.key === "N" || event.key === "т" || event.key === "Т") {
      if (byId("wsTaskCreate").disabled) return false;
      event.preventDefault();
      openTask(null);
      return true;
    }
    return false;
  }

  function bindGlobal() {
    document.addEventListener("click", function (event) {
      if (!event.target.closest) return closeBlockMenu();
      if (event.target.closest("#wsBlockMenu") || event.target.closest("[data-block-menu]") ||
        event.target.closest("#wsAddBlock")) return;
      closeBlockMenu();
    });
    document.addEventListener("keydown", function (event) {
      var blockMenu = byId("wsBlockMenu");
      if (blockMenu && event.key === "Escape") {
        event.preventDefault();
        closeBlockMenu();
        if (state.blockMenuAnchor && document.contains(state.blockMenuAnchor)) {
          state.blockMenuAnchor.focus();
        }
        state.blockMenuAnchor = null;
        return;
      }
      if (blockMenu && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
        var menuItems = Array.prototype.slice.call(blockMenu.querySelectorAll("button"));
        var menuIndex = menuItems.indexOf(document.activeElement);
        if (menuIndex >= 0) {
          event.preventDefault();
          var nextMenuIndex = (menuIndex + (event.key === "ArrowDown" ? 1 : -1) +
            menuItems.length) % menuItems.length;
          menuItems[nextMenuIndex].focus();
        }
        return;
      }
      var modal = topModal();
      if (event.key === "Escape" && state.cardMenu && !modal) {
        event.preventDefault();
        closeCardMenu();
        return;
      }
      if (event.key === "Escape" && modal) {
        if (modal.id === "wsFormModal") closeForm();
        else hideModal(modal.id);
        return;
      }
      if (!modal && boardShortcut(event)) return;
      if (event.key !== "Tab" || !modal) return;
      var focusable = Array.prototype.filter.call(
        modal.querySelectorAll("button:not([disabled]),input:not([disabled]),select:not([disabled])," +
          "textarea:not([disabled]),[tabindex]:not([tabindex=\"-1\"])") ,
        function (item) { return item.offsetParent !== null; }
      );
      if (!focusable.length) return;
      var first = focusable[0];
      var last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    });
    window.addEventListener("beforeunload", function (event) {
      if (state.editing && state.dirty) {
        event.preventDefault();
        event.returnValue = "";
      }
    });
  }

  function onTreeClick(event) {
    var target = event.target;
    if (!target.closest) return;
    if (target.closest('[data-ws-retry="tree"]')) return loadTree();
    var edit = target.closest("[data-section-edit]");
    if (edit) {
      event.stopPropagation();
      var sectionId = edit.getAttribute("data-section-edit");
      openSectionForm(sectionId);
      if (state.form) state.form.sectionId = sectionId;
      return;
    }
    var article = target.closest("[data-article]");
    if (article) return openArticle(article.getAttribute("data-article")).catch(showFailure);
    var section = target.closest('[data-section][role="button"]');
    if (section) return toggleSection(section.getAttribute("data-section"));
  }

  async function onDocClick(event) {
    var target = event.target;
    if (!target.closest) return;

    var retry = target.closest("[data-ws-retry-article]");
    if (retry) return openArticle(retry.getAttribute("data-ws-retry-article"));

    var article = target.closest("[data-article]");
    if (article && !state.editing) {
      return openArticle(article.getAttribute("data-article")).catch(showFailure);
    }
    if (target.closest("#wsArticleEdit")) {
      state.editing = true;
      state.dirty = false;
      return renderDoc();
    }
    if (target.closest("#wsArticleCancel")) {
      if (state.dirty && !(await askConfirm({
        title: "Отменить изменения?",
        message: "Всё, что вы набрали после последнего сохранения, будет потеряно.",
        confirmLabel: "Отменить изменения",
        danger: true
      }))) return;
      state.blocks = (state.article.blocks || []).slice();
      state.editing = false;
      state.dirty = false;
      return renderDoc();
    }
    if (target.closest("#wsArticleSave")) return saveArticle();
    if (target.closest("#wsArticlePublish")) return setArticleStatus("published");
    if (target.closest("#wsArticleArchive")) return setArticleStatus("archived");
    if (target.closest("#wsArticleRestore")) return setArticleStatus("draft");
    if (target.closest("#wsArticleDelete")) return deleteArticle();

    var exec = target.closest("[data-exec]");
    if (exec) {
      document.execCommand(exec.getAttribute("data-exec"), false, null);
      state.dirty = true;
      return;
    }
    if (target.closest("[data-link]")) {
      // Диалог забирает фокус, а вместе с ним пропадает выделение, к которому
      // и должна примениться ссылка. Запоминаем его до вопроса и возвращаем
      // после, иначе createLink срабатывает вхолостую.
      var editable = document.activeElement && document.activeElement.isContentEditable
        ? document.activeElement
        : null;
      var selection = window.getSelection();
      var range = selection && selection.rangeCount
        ? selection.getRangeAt(0).cloneRange()
        : null;
      var href = await askPrompt({
        title: "Ссылка",
        message: "Адрес ссылки",
        value: "https://",
        confirmLabel: "Вставить"
      });
      if (editable) editable.focus();
      if (range && selection) {
        selection.removeAllRanges();
        selection.addRange(range);
      }
      if (href && /^https?:\/\//i.test(href)) {
        document.execCommand("createLink", false, href);
        state.dirty = true;
      }
      return;
    }
    if (target.closest("[data-unlink]")) {
      document.execCommand("unlink", false, null);
      state.dirty = true;
      return;
    }
    if (target.closest("#wsAddBlock")) {
      return showBlockMenu(target.closest("#wsAddBlock"), function (type) {
        addBlock(type, state.blocks.length);
      });
    }

    var menu = target.closest("[data-block-menu]");
    if (menu) {
      var menuIndex = Number(menu.getAttribute("data-block-menu"));
      return showBlockMenu(menu, function (type) {
        syncBlocksFromDom();
        var replacement = newBlock(type);
        var previous = state.blocks[menuIndex];
        if (previous && previous.text !== undefined && replacement.text !== undefined) {
          replacement.text = previous.text;
        }
        state.blocks[menuIndex] = replacement;
        if (type === "image" || type === "video" || type === "file") {
          return chooseMedia(menuIndex, type);
        }
        redrawBlocks();
      });
    }

    var up = target.closest("[data-block-up]");
    if (up) return swapBlocks(Number(up.getAttribute("data-block-up")), -1);
    var down = target.closest("[data-block-down]");
    if (down) return swapBlocks(Number(down.getAttribute("data-block-down")), 1);
    var remove = target.closest("[data-block-remove]");
    if (remove) {
      syncBlocksFromDom();
      state.blocks.splice(Number(remove.getAttribute("data-block-remove")), 1);
      return redrawBlocks();
    }

    var itemAdd = target.closest("[data-item-add]");
    if (itemAdd) {
      syncBlocksFromDom();
      var listBlock = state.blocks[Number(itemAdd.getAttribute("data-item-add"))];
      listBlock.items = (listBlock.items || []).concat([{ text: "", checked: false }]);
      return redrawBlocks();
    }
    var itemRemove = target.closest("[data-item-remove]");
    if (itemRemove) {
      syncBlocksFromDom();
      var itemParts = itemRemove.getAttribute("data-item-remove").split(":");
      state.blocks[Number(itemParts[0])].items.splice(Number(itemParts[1]), 1);
      return redrawBlocks();
    }
    var check = target.closest("[data-check-item]");
    if (check) {
      var checkParts = check.getAttribute("data-check-item").split(":");
      state.blocks[Number(checkParts[0])].items[Number(checkParts[1])].checked = check.checked;
      state.dirty = true;
      return;
    }

    var rowAdd = target.closest("[data-row-add]");
    if (rowAdd) {
      syncBlocksFromDom();
      var tableBlock = state.blocks[Number(rowAdd.getAttribute("data-row-add"))];
      var width = (tableBlock.rows[0] || [""]).length;
      tableBlock.rows.push(new Array(width).fill(""));
      return redrawBlocks();
    }
    var colAdd = target.closest("[data-col-add]");
    if (colAdd) {
      syncBlocksFromDom();
      var wideBlock = state.blocks[Number(colAdd.getAttribute("data-col-add"))];
      wideBlock.rows.forEach(function (row) { row.push(""); });
      return redrawBlocks();
    }

    var mediaPick = target.closest("[data-media-pick]");
    if (mediaPick) {
      var mediaParts = mediaPick.getAttribute("data-media-pick").split(":");
      return chooseMedia(Number(mediaParts[0]), mediaParts[1]);
    }
  }

  function swapBlocks(index, delta) {
    syncBlocksFromDom();
    var target = index + delta;
    if (target < 0 || target >= state.blocks.length) return;
    var moved = state.blocks.splice(index, 1)[0];
    state.blocks.splice(target, 0, moved);
    redrawBlocks();
  }

  function addBlock(type, index) {
    syncBlocksFromDom();
    var block = newBlock(type);
    state.blocks.splice(index, 0, block);
    if (type === "page_link") {
      return pickArticleFor(index);
    }
    if (type === "image" || type === "video" || type === "file") {
      return chooseMedia(index, type);
    }
    redrawBlocks();
  }

  function chooseMedia(index, kind) {
    if (state.article && state.article.section_id &&
      !(state.article.rights && state.article.rights.can_create) && !state.canManageKnowledge) {
      notify({
        title: "Нет права загружать файлы",
        message: "В этом разделе загрузка новых файлов вам недоступна."
      });
      return;
    }
    var accept = kind === "image"
      ? "image/jpeg,image/png,image/gif,image/webp"
      : kind === "video"
        ? "video/mp4,video/webm,video/quicktime"
        : ".pdf,.zip,.doc,.docx,.xls,.xlsx,.txt,.csv";
    pickFile(accept, async function (file) {
      try {
        var uploaded = await uploadFile(file);
        var block = state.blocks[index];
        block.attachment_id = uploaded.id;
        block.name = uploaded.file_name;
        redrawBlocks();
      } catch (error) {
        fail(error, "Не удалось загрузить файл");
        redrawBlocks();
      }
    });
  }

  async function pickArticleFor(index) {
    var articles = [];
    flattenSections(state.tree ? state.tree.sections : [], 0, function (section) {
      section.articles.forEach(function (article) { articles.push(article); });
    });
    (state.tree ? state.tree.loose_articles : []).forEach(function (article) {
      articles.push(article);
    });
    articles = articles.filter(function (article) {
      return !state.article || article.id !== state.article.id;
    });
    if (!articles.length) {
      notify({ title: "Вложить нечего", message: "Других статей пока нет." });
      state.blocks.splice(index, 1);
      return redrawBlocks();
    }
    var list = articles.map(function (article, number) {
      return (number + 1) + ". " + article.title;
    }).join("\n");
    var answer = await askPrompt({
      title: "Вложенная страница",
      message: "Введите номер статьи:\n\n" + list,
      confirmLabel: "Вставить"
    });
    var picked = articles[Number(answer) - 1];
    if (!picked) {
      state.blocks.splice(index, 1);
    } else {
      state.blocks[index].article_id = picked.id;
      state.blocks[index].title = picked.title;
    }
    redrawBlocks();
  }

  function showFailure(error) {
    var message = error && error.message ? error.message : "Не удалось загрузить данные";
    if (window.CelestialShell && window.CelestialShell.showLoadFailure) {
      window.CelestialShell.showLoadFailure(message);
      return;
    }
    notify({ title: "Не удалось загрузить данные", message: message });
  }

  // «Задачи» и «База знаний» — две отдельные страницы с общим модулем: какая из
  // них открыта, определяется по разметке, а не по URL.
  async function init(user) {
    state.user = user;
    if (byId("wsBoard")) return initTasks(user);
    if (byId("wsKbTree")) return initKnowledge(user);
  }

  async function initTasks(user) {
    state.canManageBoard = hasPermission(user, "workspace.manage");
    byId("wsBoardSettings").style.display = state.canManageBoard ? "" : "none";
    bindTasks();
    bindShared();
    bindGlobal();
    try {
      state.people = await api.get("/users/options");
    } catch (error) {
      // Без списка людей задачи всё равно читаются — не роняем экран.
      state.people = [];
    }
    restoreView();
    await Promise.all([
      loadTemplates().catch(function () { state.templates = []; }),
      loadBoard()
    ]);
  }

  async function initKnowledge(user) {
    state.canViewKnowledge = hasPermission(user, "knowledge.view");
    state.canManageKnowledge = hasPermission(user, "knowledge.manage");
    state.collapsed = loadCollapsed();
    bindShared();
    bindKnowledge();
    bindGlobal();
    await loadTree();
  }

  function reload() {
    if (byId("wsBoard")) return loadBoard();
    if (byId("wsKbTree")) return loadTree();
    return Promise.resolve();
  }

  window.CelestialWorkspace = { init: init, reload: reload };
})();
