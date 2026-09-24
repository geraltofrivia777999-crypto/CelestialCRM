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

  function askPrompt(options) {
    if (window.CelestialShell && window.CelestialShell.prompt) {
      return window.CelestialShell.prompt(options);
    }
    return Promise.resolve(window.prompt(options.message || options.title, options.value || ""));
  }


  var api = window.CelestialAPI;
  if (!api) return;
  var currentSessionUser = null;
  var teamState = {
    users: [],
    roles: [],
    permissions: [],
    campaignGroups: [],
    offerGroups: [],
    canManage: false
  };
  /* Два одинаковых списка людей в карточке пользователя: кому он подчинён и
     кто подчинён ему. Отличаются они только направлением связи, поэтому код
     один, а расходятся они префиксом элементов и набором отметок. */
  var PICKERS = {
    parent: { prefix: "parentPicker", empty: "Не выбраны — верхний уровень" },
    child: { prefix: "childPicker", empty: "Не выбраны — подчинённых нет" }
  };
  var pickerState = {
    parent: { selectedIds: new Set(), search: "" },
    child: { selectedIds: new Set(), search: "" },
    editingUserId: null
  };

  function byId(id) {
    return document.getElementById(id);
  }

  function text(id, value) {
    var element = byId(id);
    if (element) element.textContent = value;
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  function initials(value) {
    return String(value || "?")
      .trim()
      .split(/\s+/)
      .slice(0, 2)
      .map(function (part) { return part.charAt(0).toUpperCase(); })
      .join("") || "?";
  }

  function number(value) {
    return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 })
      .format(Number(value || 0));
  }

  function decimal(value, digits) {
    if (value == null) return "—";
    return new Intl.NumberFormat("ru-RU", {
      minimumFractionDigits: digits || 0,
      maximumFractionDigits: digits == null ? 2 : digits
    }).format(Number(value));
  }

  function money(value) {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 2
    }).format(Number(value || 0));
  }

  function percent(value) {
    return value == null ? "—" : decimal(value, 1) + "%";
  }

  function statusLabel(status) {
    if (status === "active") return "Активен";
    if (status === "blocked") return "Заблокирован";
    return "Неактивен";
  }

  function statusBadge(status) {
    var active = status === "active";
    var color = active ? "#16B57F" : "#857D7D";
    var background = active ? "#E4F7F0" : "#F0F1F5";
    return '<span style="display:inline-flex;align-items:center;gap:6px;color:' + color +
      ";background:" + background +
      ';border-radius:8px;padding:6px 10px;font-size:11.5px;font-weight:700">' +
      '<span style="width:6px;height:6px;border-radius:50%;background:' + color + '"></span>' +
      escapeHtml(statusLabel(status)) + "</span>";
  }

  function cell(value, extra) {
    return '<td style="padding:14px 12px;text-align:right;font-family:Inter;font-size:12px;' +
      (extra || "") + '">' + escapeHtml(value) + "</td>";
  }

  function emptyRow(columns, message) {
    return '<tr><td colspan="' + columns +
      '" style="padding:44px 24px;text-align:center;color:#9B9292;font-size:13px">' +
      escapeHtml(message || "Данных пока нет") + "</td></tr>";
  }

  function toast(message, kind) {
    var current = byId("celestialLiveToast");
    if (current) current.remove();
    var element = document.createElement("div");
    element.id = "celestialLiveToast";
    element.textContent = message;
    element.style.cssText =
      "position:fixed;right:24px;bottom:24px;z-index:99999;max-width:420px;" +
      "padding:13px 17px;border-radius:11px;color:#fff;font:700 12px Inter,sans-serif;" +
      "box-shadow:0 14px 38px rgba(23,17,17,.22);background:" +
      (kind === "error" ? "#FF0000" : kind === "info" ? "#B91414" : "#16B57F");
    document.body.appendChild(element);
    window.setTimeout(function () { element.remove(); }, 5000);
  }

  /* Меню строки лежит внутри карточки с `overflow:hidden` и горизонтальной
   * прокруткой таблицы, поэтому в обычном потоке его срезает по краю карточки —
   * у нижних строк от меню оставалась одна полоска. При открытии оно
   * переносится в координаты окна и, если снизу не хватает места, раскрывается
   * вверх. Позиция фиксированная, так что при прокрутке меню просто закрывается. */
  function placeRowMenu(details) {
    var menu = details.querySelector(".row-menu");
    var summary = details.querySelector("summary");
    if (!menu || !summary) return;
    var anchor = summary.getBoundingClientRect();
    menu.style.position = "fixed";
    menu.style.zIndex = "40";
    menu.style.right = "auto";
    menu.style.left = "0px";
    menu.style.top = "0px";
    var width = menu.offsetWidth;
    var height = menu.offsetHeight;
    var left = Math.min(anchor.right - width, window.innerWidth - width - 10);
    var fitsBelow = window.innerHeight - anchor.bottom > height + 12;
    menu.style.left = Math.max(10, left) + "px";
    menu.style.top = (fitsBelow ? anchor.bottom + 5 : anchor.top - height - 5) + "px";
  }

  function closeRowMenus(except) {
    document.querySelectorAll("details.row-actions[open]").forEach(function (item) {
      if (item !== except) item.removeAttribute("open");
    });
  }

  function bindRowMenus() {
    if (document.documentElement.dataset.rowMenusBound) return;
    document.documentElement.dataset.rowMenusBound = "true";
    // `toggle` не всплывает — слушаем на фазе перехвата.
    document.addEventListener("toggle", function (event) {
      var details = event.target;
      if (!details.classList || !details.classList.contains("row-actions")) return;
      if (!details.open) return;
      closeRowMenus(details);
      placeRowMenu(details);
    }, true);
    document.addEventListener("click", function (event) {
      if (!event.target.closest("details.row-actions")) closeRowMenus(null);
    });
    window.addEventListener("scroll", function () { closeRowMenus(null); }, true);
    window.addEventListener("resize", function () { closeRowMenus(null); });
  }

  function fail(error) {
    if (error && error.status === 401) {
      window.location.replace("/login.html?next=" + encodeURIComponent(
        window.location.pathname + window.location.search
      ));
      return true;
    }
    toast(error && error.message ? error.message : "Не удалось загрузить данные", "error");
    return false;
  }

  async function loadDashboard() {
    var data = await api.get("/dashboard");
    text("dashboardRevenue", money(data.revenue));
    text("dashboardSpend", money(data.spend));
    text("dashboardProfit", money(data.profit));
    text("dashboardRoi", percent(data.roi));
    text("dashboardLeads", number(data.leads));
    text("dashboardSales", number(data.sales));
    text("dashboardEpl", data.epl == null ? "—" : money(data.epl));
  }

  /*
   * The offers table lives in catalog-ui.js (star column, workflow statuses, the
   * Keitaro OFFERS group scope). There is no second copy here: a stale duplicate
   * silently rendered the old layout whenever the real module was missing.
   */
  function showCatalogMissing() {
    if (window.CelestialShell && window.CelestialShell.showLoadFailure) {
      window.CelestialShell.showLoadFailure("Модуль офферов не загрузился — обновите страницу");
    }
  }

  function showFinanceMissing() {
    if (window.CelestialShell && window.CelestialShell.showLoadFailure) {
      window.CelestialShell.showLoadFailure("Модуль финансов не загрузился — обновите страницу");
    }
  }

  function structure(item) {
    return '<td style="position:sticky;left:0;background:#fff;padding:12px 16px;border-right:1px solid #E8E2E2">' +
      '<div style="font-size:12.5px;font-weight:700">' + escapeHtml(item.offer || "—") + "</div>" +
      '<div style="font-size:10.5px;color:#9B9292;margin-top:4px">' +
      escapeHtml([item.record_date, item.buyer, item.geo, item.partner].filter(Boolean).join(" · ")) +
      "</div></td>";
  }

  async function loadMedia() {
    var page = await api.get("/media-records?limit=200");
    var body = byId("mediaTableBody");
    if (!body) return;
    body.innerHTML = (page.items || []).length ? page.items.map(function (item) {
      return '<tr style="border-bottom:1px solid #EFEAEA">' + structure(item) +
        [0, 0, 0, 0, 0, 0, 0].map(function () { return cell("—", "color:#9B9292;"); }).join("") +
        cell(number(item.installs)) + cell(number(item.registrations)) + cell(number(item.ftd)) +
        cell(money(item.rent)) + cell(money(item.spend)) +
        cell(money(item.revenue), "font-weight:700;") +
        cell(money(item.profit), "font-weight:700;color:" + (Number(item.profit) >= 0 ? "#16B57F;" : "#FF0000;")) +
        cell(percent(item.roi)) + cell(item.cpd == null ? "—" : money(item.cpd)) + "</tr>";
    }).join("") : emptyRow(17, "Данные появятся после первой синхронизации Keitaro");
  }

  async function loadTeam() {
    var canManage = currentSessionUser && currentSessionUser.role &&
      (currentSessionUser.role.permissions || []).some(function (permission) {
        return permission.code === "*" || permission.code === "team.manage";
      });
    var requests = [
      api.getAll("/users"),
      api.get("/roles"),
      canManage
        ? api.get("/integrations/keitaro/groups").catch(function () {
          return { campaign_groups: [], offer_groups: [] };
        })
        : Promise.resolve({ campaign_groups: [], offer_groups: [] }),
      canManage
        ? api.get("/permissions").catch(function () { return []; })
        : Promise.resolve([])
    ];
    var results = await Promise.all(requests);
    var users = results[0].items || [];
    var roles = results[1] || [];
    teamState = {
      users: users,
      roles: roles,
      permissions: results[3] || [],
      // Группа может быть новой и пока не содержать ни одной кампании или
      // оффера. Поэтому берём сам справочник Keitaro, а не выводим группы из
      // активных сущностей — именно из-за этого раньше пропадала XEI.
      campaignGroups: uniqueValues(results[2].campaign_groups || []),
      offerGroups: uniqueValues(results[2].offer_groups || []),
      canManage: Boolean(canManage)
    };
    text("teamTotal", number(results[0].total));
    text("teamActive", number(users.filter(function (user) {
      return user.status === "active";
    }).length));
    text("teamRoles", number(roles.length));
    text("usersResultCount", "Показано " + users.length + " из " + results[0].total);
    text("teamUsersTabCount", number(results[0].total));
    text("teamRolesTabCount", number(roles.length));
    text("teamSidebarTotal", userCountLabel(results[0].total));
    text("teamCurrentAvatar", initials(currentSessionUser && currentSessionUser.name));
    text("teamCurrentName", currentSessionUser && currentSessionUser.name);
    text("teamCurrentRole", currentSessionUser && currentSessionUser.role.name);
    if (byId("teamPagination")) {
      byId("teamPagination").style.display = results[0].total > 100 ? "flex" : "none";
    }
    if (byId("openUserModal")) {
      byId("openUserModal").style.display = canManage ? "flex" : "none";
    }
    if (byId("openRoleModal")) {
      byId("openRoleModal").style.display = canManage ? "" : "none";
    }
    populateTeamFilters();
    populateUserFormOptions(null);
    var body = byId("teamTableBody");
    if (!body) return;
    body.innerHTML = users.length ? users.map(function (user) {
      var parents = user.parents || [];
      // Подчинённых у пользователя не хранят списком: это те, у кого он записан
      // родителем. Список команды уже загружен, поэтому считаем прямо здесь.
      var children = users.filter(function (candidate) {
        return (candidate.parents || []).some(function (parent) {
          return parent.id === user.id;
        });
      });
      var parentHtml = peopleChips(parents);
      var childHtml = peopleChips(children);
      var actions = teamState.canManage
        ? '<details class="row-actions" style="position:relative"><summary style="width:34px;height:34px;' +
          'border-radius:9px;display:flex;align-items:center;justify-content:center;cursor:pointer">•••</summary>' +
          '<div class="row-menu"><button type="button" data-team-action="edit" data-user-id="' +
          escapeHtml(user.id) + '">Редактировать</button>' +
          '<button type="button" data-team-action="reset-password" data-user-id="' +
          escapeHtml(user.id) + '">Сбросить пароль</button>' +
          (currentSessionUser && currentSessionUser.id === user.id ? "" :
            '<button type="button" data-team-action="toggle-status" data-user-id="' +
            escapeHtml(user.id) + '">' +
            (user.status === "active" ? "Заблокировать" : "Разблокировать") + "</button>" +
            '<button type="button" data-team-action="delete" data-user-id="' +
            escapeHtml(user.id) + '" style="color:#C41616">Удалить</button>') +
          "</div></details>"
        : "";
      return '<tr class="user-row" data-user-row data-role="' + escapeHtml(user.role.name) +
        '" data-status="' + escapeHtml(user.status) + '" data-user-id="' +
        escapeHtml(user.id) + '" style="border-bottom:1px solid #F7F4F4">' +
        '<td style="padding:14px 24px"><div style="display:flex;align-items:center;gap:12px">' +
        '<span class="avatar" style="background:linear-gradient(135deg,#B91414,#D06060)">' +
        escapeHtml(initials(user.name)) + '</span><div><div style="font-size:13.5px;font-weight:700">' +
        escapeHtml(user.name) + '</div><div style="font-size:11px;color:#9B9292;margin-top:3px">@' +
        escapeHtml(user.login) + "</div></div></div></td>" +
        '<td style="padding:14px 18px;font-size:12.5px;font-weight:700">' +
        escapeHtml(user.role.name) + "</td>" +
        '<td style="padding:14px 18px">' + parentHtml + "</td>" +
        '<td style="padding:14px 18px">' + childHtml + "</td>" +
        '<td style="padding:14px 18px">' + statusBadge(user.status) + "</td>" +
        '<td style="padding:14px 18px;color:#9B9292">' + actions + "</td></tr>";
    }).join("") : emptyRow(6, "Пользователей пока нет");
    renderRoles();
    renderHierarchy();
    bindTeamActions();
  }

  /* Плашки людей в ячейке: родители и подчинённые выглядят одинаково.
     Больше трёх в строку не влезает, поэтому хвост сворачивается в счётчик. */
  function peopleChips(people) {
    if (!people || !people.length) {
      return '<span style="color:#9B9292;font-size:12.5px">—</span>';
    }
    var shown = people.slice(0, 3).map(function (person) {
      return '<span style="display:inline-flex;align-items:center;gap:6px;background:#F7F4F4;' +
        'border-radius:8px;padding:5px 8px;font-size:10.5px;font-weight:700">' +
        '<span class="mini-avatar" style="margin-left:0;background:#B91414">' +
        escapeHtml(initials(person.name)) + "</span>" + escapeHtml(person.name) + "</span>";
    }).join("");
    var rest = people.length > 3
      ? '<span title="' + escapeHtml(people.slice(3).map(function (person) {
        return person.name;
      }).join(", ")) + '" style="font-size:11px;color:#6A6161;font-weight:700">+' +
        (people.length - 3) + "</span>"
      : "";
    return '<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">' +
      shown + rest + "</div>";
  }

  function uniqueValues(values) {
    return Array.from(new Set(values.filter(Boolean))).sort(function (left, right) {
      return left.localeCompare(right, "ru");
    });
  }

  function userCountLabel(value) {
    var count = Number(value || 0);
    var mod10 = count % 10;
    var mod100 = count % 100;
    var word = "пользователей";
    if (mod10 === 1 && mod100 !== 11) word = "пользователь";
    else if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) {
      word = "пользователя";
    }
    return number(count) + " " + word;
  }

  function populateTeamFilters() {
    var filter = byId("teamRoleFilter");
    if (!filter) return;
    var selected = filter.value;
    filter.innerHTML = '<option value="">Все роли</option>' + teamState.roles.map(
      function (role) {
        return '<option value="' + escapeHtml(role.name) + '">' +
          escapeHtml(role.name) + "</option>";
      }
    ).join("");
    filter.value = selected;
  }

  function selectOptions(select, items, emptyLabel) {
    if (!select) return;
    select.innerHTML = (emptyLabel
      ? '<option value="">' + escapeHtml(emptyLabel) + "</option>"
      : "") + items.map(function (item) {
      var value = typeof item === "string" ? item : item.id;
      var label = typeof item === "string" ? item : item.name + " (@" + item.login + ")";
      return '<option value="' + escapeHtml(value) + '">' + escapeHtml(label) + "</option>";
    }).join("");
  }

  function populateUserFormOptions(editingUser) {
    var form = byId("createUserForm");
    if (!form) return;
    var roleItems = teamState.roles.map(function (role) {
      return { id: role.id, name: role.name, login: "" };
    });
    var roleSelect = form.elements.role;
    roleSelect.innerHTML = '<option value="">Выберите роль</option>' +
      roleItems.map(function (role) {
        return '<option value="' + escapeHtml(role.id) + '">' +
          escapeHtml(role.name) + "</option>";
      }).join("");
    pickerState.editingUserId = editingUser ? editingUser.id : null;
    Object.keys(PICKERS).forEach(renderPicker);
    selectOptions(form.elements.companyGroup, teamState.campaignGroups, "Без привязки");
    selectOptions(form.elements.offerGroup, teamState.offerGroups, "Без привязки");
  }

  /* Кого нельзя выбрать, чтобы иерархия не свернулась в кольцо: себя, а для
     родителей — всех своих подчинённых, для подчинённых — всех своих
     начальников. Направление обхода — единственная разница. */
  function availablePickerUsers(kind) {
    var excludedIds = new Set();
    var rootId = pickerState.editingUserId;
    if (rootId) {
      excludedIds.add(rootId);
      var queue = [rootId];
      while (queue.length) {
        var currentId = queue.shift();
        teamState.users.forEach(function (candidate) {
          var linked = kind === "parent"
            ? (candidate.parents || []).some(function (parent) {
              return parent.id === currentId;
            })
            : candidate.id === currentId
              ? false
              : (currentUserParents(currentId) || []).some(function (parent) {
                return parent.id === candidate.id;
              });
          if (linked && !excludedIds.has(candidate.id)) {
            excludedIds.add(candidate.id);
            queue.push(candidate.id);
          }
        });
      }
    }
    return teamState.users.filter(function (user) {
      return !excludedIds.has(user.id);
    });
  }

  function currentUserParents(userId) {
    var found = teamState.users.find(function (user) { return user.id === userId; });
    return found ? found.parents || [] : [];
  }

  function parentSelectionLabel(count) {
    if (!count) return "Не выбрано";
    var mod10 = count % 10;
    var mod100 = count % 100;
    var word = "пользователей";
    if (mod10 === 1 && mod100 !== 11) word = "пользователь";
    else if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) {
      word = "пользователя";
    }
    return "Выбрано: " + count + " " + word;
  }

  function setPickerSelection(kind, ids) {
    var allowedIds = new Set(availablePickerUsers(kind).map(function (user) {
      return user.id;
    }));
    pickerState[kind].selectedIds = new Set((ids || []).filter(function (id) {
      return allowedIds.has(id);
    }));
    pickerState[kind].search = "";
    var search = byId(PICKERS[kind].prefix + "Search");
    if (search) search.value = "";
    renderPicker(kind);
  }

  function renderPicker(kind) {
    var picker = PICKERS[kind];
    var summary = byId(picker.prefix + "Summary");
    var options = byId(picker.prefix + "Options");
    var count = byId(picker.prefix + "Count");
    if (!summary || !options) return;
    var users = availablePickerUsers(kind);
    var selectedUsers = users.filter(function (user) {
      return pickerState[kind].selectedIds.has(user.id);
    });
    summary.classList.toggle("is-empty", !selectedUsers.length);
    if (!selectedUsers.length) {
      summary.innerHTML = '<span class="people-picker-summary-text">' +
        escapeHtml(picker.empty) + "</span>";
    } else {
      var avatarHtml = '<span class="people-picker-avatars">' + selectedUsers.slice(0, 3).map(
        function (user) {
          return '<span class="people-picker-avatar">' +
            escapeHtml(initials(user.name)) + "</span>";
        }
      ).join("") + "</span>";
      var selectedText = selectedUsers.length === 1
        ? selectedUsers[0].name
        : selectedUsers[0].name + " и ещё " + (selectedUsers.length - 1);
      summary.innerHTML = avatarHtml + '<span class="people-picker-summary-text">' +
        escapeHtml(selectedText) + "</span>";
    }
    var query = pickerState[kind].search.trim().toLocaleLowerCase("ru");
    var visibleUsers = users.filter(function (user) {
      return !query || (user.name + " " + user.login + " " + user.role.name)
        .toLocaleLowerCase("ru").includes(query);
    });
    options.innerHTML = visibleUsers.length ? visibleUsers.map(function (user) {
      var selected = pickerState[kind].selectedIds.has(user.id);
      return '<label class="people-picker-option" role="option" aria-selected="' +
        (selected ? "true" : "false") + '"><input type="checkbox" data-picker-kind="' +
        kind + '" data-picker-id="' +
        escapeHtml(user.id) + '"' + (selected ? " checked" : "") + ">" +
        '<span class="people-picker-option-avatar">' + escapeHtml(initials(user.name)) +
        '</span><span class="people-picker-option-copy"><span class="people-picker-option-name">' +
        escapeHtml(user.name) + '</span><span class="people-picker-option-login">@' +
        escapeHtml(user.login) + " · " + escapeHtml(user.role.name) +
        "</span></span></label>";
    }).join("") : '<div class="people-picker-empty">' +
      (users.length ? "По вашему запросу никого не найдено" :
        "Других пользователей пока нет") + "</div>";
    if (count) count.textContent = parentSelectionLabel(selectedUsers.length);
    var clear = byId(picker.prefix + "Clear");
    if (clear) clear.style.visibility = selectedUsers.length ? "visible" : "hidden";
  }

  function setPickerOpen(kind, open) {
    var prefix = PICKERS[kind].prefix;
    var host = byId(prefix);
    var menu = byId(prefix + "Menu");
    var trigger = byId(prefix + "Trigger");
    if (!host || !menu || !trigger) return;
    host.classList.toggle("open", open);
    menu.hidden = !open;
    menu.classList.remove("open-up");
    trigger.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      // Открытым может быть только один список: два раскрытых меню наезжают
      // друг на друга внутри одной карточки.
      Object.keys(PICKERS).forEach(function (other) {
        if (other !== kind) setPickerOpen(other, false);
      });
      renderPicker(kind);
      window.setTimeout(function () {
        var modalCard = menu.closest(".modal-card");
        if (modalCard) {
          var menuBounds = menu.getBoundingClientRect();
          var modalBounds = modalCard.getBoundingClientRect();
          menu.classList.toggle("open-up", menuBounds.bottom > modalBounds.bottom - 10);
        }
        var search = byId(prefix + "Search");
        if (search) search.focus();
      }, 20);
    }
  }

  function closeAllPickers() {
    Object.keys(PICKERS).forEach(function (kind) { setPickerOpen(kind, false); });
  }

  /* ---------- теги пользователя ----------

     Первый тег — группа офферов Keitaro: он ставится сам и крестика не имеет.
     У нового пользователя группа стоит первой; у существующего новая группа
     встаёт под прежней, а не заменяет её — по старому тегу в Финансах уже
     могли ввести депозиты. Сервер держит то же правило. */
  var userTags = { tags: [], isNew: true };

  function sameTag(left, right) {
    return String(left).toLowerCase() === String(right).toLowerCase();
  }

  function currentOfferGroup() {
    var form = byId("createUserForm");
    return form ? String(form.elements.offerGroup.value || "").trim() : "";
  }

  function userTagList() {
    var group = currentOfferGroup();
    var list = userTags.tags.slice();
    var present = list.some(function (tag) { return sameTag(tag, group); });
    if (group && !present) {
      if (userTags.isNew) list.unshift(group);
      else list.push(group);
    }
    return list;
  }

  function renderUserTags() {
    var box = byId("userTags");
    var input = byId("userTagInput");
    if (!box || !input) return;
    var group = currentOfferGroup();
    box.querySelectorAll(".team-tag").forEach(function (node) { node.remove(); });
    userTagList().forEach(function (tag) {
      var chip = document.createElement("span");
      var auto = group && sameTag(tag, group);
      chip.className = "team-tag" + (auto ? " is-auto" : "");
      chip.textContent = tag;
      if (auto) {
        chip.title = "Группа офферов Keitaro — ставится автоматически";
      } else {
        var drop = document.createElement("button");
        drop.type = "button";
        drop.textContent = "×";
        drop.dataset.tag = tag;
        drop.setAttribute("aria-label", "Убрать тег " + tag);
        chip.appendChild(drop);
      }
      box.insertBefore(chip, input);
    });
  }

  // Набранное, но не подтверждённое Enter, тоже считается тегом — как в
  // ключевых словах Рекрутинга.
  function addUserTag(value) {
    var tag = String(value || "").trim().slice(0, 120);
    if (!tag) return false;
    if (userTagList().some(function (item) { return sameTag(item, tag); })) return true;
    userTags.tags.push(tag);
    renderUserTags();
    return true;
  }

  function bindUserTags() {
    var box = byId("userTags");
    var input = byId("userTagInput");
    if (!box || !input) return;
    input.addEventListener("keydown", function (event) {
      if (event.key !== "Enter") return;
      // Иначе Enter отправит всю форму пользователя.
      event.preventDefault();
      if (addUserTag(input.value)) input.value = "";
    });
    box.addEventListener("click", function (event) {
      var drop = event.target.closest("button[data-tag]");
      if (drop) {
        userTags.tags = userTags.tags.filter(function (tag) {
          return !sameTag(tag, drop.dataset.tag);
        });
        renderUserTags();
        return;
      }
      if (event.target === box) input.focus();
    });
    var add = byId("addUserTag");
    if (add) add.addEventListener("click", function () {
      if (input.value.trim() && addUserTag(input.value)) input.value = "";
      input.focus();
    });
    var form = byId("createUserForm");
    if (form) form.elements.offerGroup.addEventListener("change", renderUserTags);
  }

  function openUserEditor(user) {
    var form = byId("createUserForm");
    if (!form) return;
    form.reset();
    form.dataset.userId = user ? user.id : "";
    pickerState.editingUserId = user ? user.id : null;
    pickerState.parent.selectedIds = new Set((user && user.parents || []).map(
      function (parent) { return parent.id; }
    ));
    // Подчинённых у пользователя не хранят списком — это те, у кого он записан
    // родителем. Собираем их из уже загруженной команды.
    pickerState.child.selectedIds = new Set(
      user
        ? teamState.users.filter(function (candidate) {
          return (candidate.parents || []).some(function (parent) {
            return parent.id === user.id;
          });
        }).map(function (candidate) { return candidate.id; })
        : []
    );
    pickerState.parent.search = "";
    pickerState.child.search = "";
    populateUserFormOptions(user);
    text("userModalTitle", user ? "Редактирование пользователя" : "Новый пользователь");
    text(
      "userModalSubtitle",
      user ? "Обновите роль, иерархию и привязки Keitaro" :
        "Создайте учётную запись и назначьте доступы"
    );
    text("saveUserButton", user ? "Сохранить изменения" : "Создать пользователя");
    var passwordField = byId("userPasswordField");
    var passwordInput = form.elements.password;
    if (passwordField) passwordField.style.display = user ? "none" : "";
    passwordInput.required = !user;
    if (user) {
      form.elements.login.value = user.login;
      form.elements.role.value = user.role.id;
      form.elements.status.value = user.status;
      form.elements.companyGroup.value = user.keitaro_company_group || "";
      form.elements.offerGroup.value = user.keitaro_offer_group || "";
    }
    setPickerSelection("parent", Array.from(pickerState.parent.selectedIds));
    setPickerSelection("child", Array.from(pickerState.child.selectedIds));
    userTags = { tags: user ? (user.finance_tags || []).slice() : [], isNew: !user };
    if (byId("userTagInput")) byId("userTagInput").value = "";
    renderUserTags();
    closeAllPickers();
    keitaroGroupsStatus("");
    byId("userModal").classList.add("open");
    document.body.style.overflow = "hidden";
  }

  function closeTeamModal(id) {
    var modal = byId(id);
    if (modal) modal.classList.remove("open");
    if (id === "userModal") closeAllPickers();
    document.body.style.overflow = "";
  }

  /* Названия разделов — те же, что в меню: право «meta.launch» человек ищет
     как «Meta Ads», а не как «meta». */
  var PERMISSION_MODULES = {
    dashboard: "Dashboard",
    media: "Медиаборд",
    meta: "Meta Ads",
    finance: "Финансы",
    salary: "Зарплаты",
    offers: "Оффера",
    team: "Команда",
    recruitment: "Рекрутинг",
    workspace: "Задачи",
    knowledge: "База знаний",
    utilities: "Утилиты",
    settings: "Настройки"
  };

  /* Всё, что не «просмотр» и не «изменение», раньше подписывалось «просмотром»:
     в списке стояли три одинаковых «meta · просмотр», хотя это заливы,
     комментарии и собственно просмотр. */
  var PERMISSION_ACTIONS = {
    view: "просмотр",
    view_all: "весь справочник",
    channels: "каналы",
    events: "журнал",
    assign: "раздача баерам",
    manage: "изменение",
    export: "экспорт",
    launch: "заливы",
    comments: "комментарии",
    details: "приоритет, сроки, исполнители"
  };

  function permissionOrder(code) {
    var modules = Object.keys(PERMISSION_MODULES);
    var index = modules.indexOf(String(code).split(".")[0]);
    return index < 0 ? modules.length : index;
  }

  function permissionLabel(code) {
    var parts = String(code).split(".");
    return (PERMISSION_MODULES[parts[0]] || parts[0]) + " · " +
      (PERMISSION_ACTIONS[parts[1]] || parts[1] || "");
  }

  /* Иконка роли — по её собственному имени, а не по позиции в списке: иначе
     карточка меняла бы вид при каждом добавлении роли выше по алфавиту. */
  var ROLE_LOOKS = [
    {
      match: /админ|admin/i, background: "#FCF1F1", color: "#B91414",
      path: '<path d="M12 3 4 7v5c0 4.8 3.3 7.7 8 9 4.7-1.3 8-4.2 8-9V7l-8-4Z"/>'
    },
    {
      match: /лид|lead|тимлид/i, background: "#E4F7F0", color: "#16B57F",
      path: '<circle cx="8" cy="8" r="3"/><circle cx="17" cy="8" r="3"/>' +
        '<path d="M2 20a6 6 0 0 1 12 0M11 20a6 6 0 0 1 12 0"/>'
    },
    {
      match: /баер|buyer/i, background: "#FFF2E0", color: "#E8912B",
      path: '<path d="M4 17 15 6l3 3L7 20H4v-3ZM13 8l3 3"/>'
    },
    {
      match: /финанс|finance/i, background: "#EFF5FE", color: "#2C6BB5",
      path: '<rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="12" cy="12" r="3"/>'
    },
    {
      match: /cmo|маркет/i, background: "#F3EEFC", color: "#6B46C1",
      path: '<path d="M4 14V9l12-4v14L4 15H3a1 1 0 0 1-1-1Z"/><path d="M7 16v4"/>'
    }
  ];
  var ROLE_LOOK_DEFAULT = {
    background: "#F2EDED", color: "#6A6161",
    path: '<circle cx="12" cy="8" r="3.5"/><path d="M5 20a7 7 0 0 1 14 0"/>'
  };

  function roleLook(role) {
    var found = ROLE_LOOKS.filter(function (item) {
      return item.match.test(role.name || "");
    })[0] || ROLE_LOOK_DEFAULT;
    return {
      background: found.background,
      icon: '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="' +
        found.color + '" stroke-width="2" stroke-linejoin="round">' + found.path + "</svg>"
    };
  }

  async function deleteRole(roleId) {
    var role = teamState.roles.find(function (row) { return row.id === roleId; });
    if (!role) return;
    var assigned = teamState.users.filter(function (user) {
      return user.role.id === role.id;
    });
    var query = "";
    var message = "Роль пропадёт из списка. Пользователей с ней нет — ни у кого " +
      "доступ не изменится.";
    if (assigned.length) {
      // Человек без роли не войдёт в CRM, поэтому сначала спрашиваем, куда
      // перенести людей, и только вторым нажатием — удаляем.
      var move = byId("roleDeleteMove");
      var select = byId("roleDeleteReplacement");
      var others = teamState.roles.filter(function (row) { return row.id !== role.id; });
      if (move.hidden) {
        byId("roleDeleteMoveText").textContent = "Роль назначена: " + assigned.length + " " +
          (assigned.length === 1 ? "пользователь" : "пользователей") +
          ". Выберите роль, на которую их перенести, и нажмите «Перенести и удалить».";
        select.innerHTML = others.map(function (row) {
          return '<option value="' + escapeHtml(row.id) + '">' + escapeHtml(row.name) +
            "</option>";
        }).join("");
        move.hidden = false;
        byId("deleteRoleButton").textContent = "Перенести и удалить";
        move.scrollIntoView({ block: "nearest", behavior: "smooth" });
        return;
      }
      var target = others.find(function (row) { return row.id === select.value; });
      if (!target) return;
      query = "?replacement_role_id=" + encodeURIComponent(target.id);
      message = assigned.length + " " +
        (assigned.length === 1 ? "пользователь получит" : "пользователей получат") +
        " роль «" + target.name + "» и её права.";
    }
    if (!(await askConfirm({
      title: "Удалить роль «" + role.name + "»?",
      message: message,
      confirmLabel: assigned.length ? "Перенести и удалить" : "Удалить",
      danger: true
    }))) return;
    await api.delete("/roles/" + roleId + query);
    closeTeamModal("roleModal");
    toast("Роль удалена");
    await loadTeam();
  }

  function renderRoles() {
    var grid = byId("rolesGrid");
    if (!grid) return;
    grid.innerHTML = teamState.roles.length ? teamState.roles.map(function (role) {
      var usersCount = teamState.users.filter(function (user) {
        return user.role.id === role.id;
      }).length;
      var permissions = role.permissions || [];
      var look = roleLook(role);
      return '<article class="role-card"><div style="display:flex;align-items:center;' +
        'justify-content:space-between"><div style="width:42px;height:42px;border-radius:12px;' +
        "background:" + look.background + ";display:flex;align-items:center;" +
        'justify-content:center">' + look.icon + "</div>" +
        '<span style="font-size:11px;color:#9B9292;font-weight:700">' +
        number(usersCount) + " " + (usersCount === 1 ? "пользователь" : "пользователей") +
        '</span></div><h4 class="team-heading" style="font-size:16px;margin-top:15px">' +
        escapeHtml(role.name) + '</h4><p style="font-size:11.5px;color:#857D7D;line-height:1.55;' +
        'margin-top:5px;min-height:36px">' + escapeHtml(role.description || "Без описания") +
        '</p><div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:14px">' +
        permissions.slice(0, 3).map(function (permission) {
          return '<span class="permission-pill">' +
            escapeHtml(permissionLabel(permission.code)) + "</span>";
        }).join("") +
        (permissions.length > 3 ? '<span class="permission-pill">+' +
          (permissions.length - 3) + "</span>" : "") + "</div>" +
        (teamState.canManage
          ? '<button type="button" data-team-action="edit-role" data-role-id="' +
            escapeHtml(role.id) + '" style="width:100%;height:36px;margin-top:18px;border:1px solid ' +
            '#EBE6E6;background:#fff;border-radius:9px;color:#B91414;font-size:11.5px;' +
            'font-weight:700">Редактировать</button>'
          : "") + "</article>";
    }).join("") : '<div style="grid-column:1/-1;color:#9B9292">Ролей пока нет</div>';
  }

  function renderHierarchy() {
    var container = byId("hierarchyContent");
    if (!container) return;
    if (!teamState.users.length) {
      text("hierarchyLevelCount", "0 уровней");
      container.innerHTML = '<div class="hierarchy-canvas"><div class="people-picker-empty">' +
        "Добавьте первого пользователя, чтобы построить структуру команды</div></div>";
      return;
    }
    var usersById = new Map(teamState.users.map(function (user) {
      return [user.id, user];
    }));
    var levelsById = new Map();
    var hasCycle = false;

    function getLevel(userId, path) {
      if (levelsById.has(userId)) return levelsById.get(userId);
      if (path.has(userId)) {
        hasCycle = true;
        return 0;
      }
      var user = usersById.get(userId);
      if (!user) return 0;
      var nextPath = new Set(path);
      nextPath.add(userId);
      var parentIds = (user.parents || []).map(function (parent) {
        return parent.id;
      }).filter(function (parentId) {
        return usersById.has(parentId) && parentId !== userId;
      });
      var level = parentIds.length ? Math.max.apply(null, parentIds.map(function (parentId) {
        return getLevel(parentId, nextPath);
      })) + 1 : 0;
      levelsById.set(userId, level);
      return level;
    }

    teamState.users.forEach(function (user) {
      getLevel(user.id, new Set());
    });
    var maxLevel = Math.max.apply(null, Array.from(levelsById.values()));
    var levels = Array.from({ length: maxLevel + 1 }, function () {
      return [];
    });
    teamState.users.forEach(function (user) {
      levels[levelsById.get(user.id) || 0].push(user);
    });
    levels.forEach(function (users) {
      users.sort(function (left, right) {
        return left.name.localeCompare(right.name, "ru");
      });
    });
    var rootCount = levels[0].length;
    var linkCount = teamState.users.reduce(function (total, user) {
      return total + (user.parents || []).length;
    }, 0);
    var multiParentCount = teamState.users.filter(function (user) {
      return (user.parents || []).length > 1;
    }).length;
    text(
      "hierarchyLevelCount",
      levels.length + " " + (levels.length === 1 ? "уровень" :
        levels.length >= 2 && levels.length <= 4 ? "уровня" : "уровней")
    );
    var overview = '<div class="hierarchy-overview">' +
      '<span class="hierarchy-overview-item"><strong>' + rootCount +
      "</strong> на верхнем уровне</span>" +
      '<span class="hierarchy-overview-item"><strong>' + linkCount +
      "</strong> связей</span>" +
      '<span class="hierarchy-overview-item"><strong>' + multiParentCount +
      "</strong> с несколькими руководителями</span></div>";
    var warning = hasCycle
      ? '<div class="hierarchy-warning" style="margin-top:14px">' +
        "В структуре найдена циклическая связь. Проверьте назначенных руководителей.</div>"
      : "";

    // Иерархия — граф, а не обычное дерево: один человек может подчиняться
    // нескольким руководителям. Поэтому строим ветку для каждого руководителя,
    // а не назначаем сотруднику одного «основного» родителя по алфавиту.
    var allChildrenByParent = new Map();
    var roots = [];
    teamState.users.forEach(function (user) {
      var parentIds = (user.parents || []).map(function (parent) {
        return parent.id;
      }).filter(function (parentId) {
        return usersById.has(parentId) && parentId !== user.id;
      });
      if (!parentIds.length) roots.push(user);
      parentIds.forEach(function (parentId) {
        if (!allChildrenByParent.has(parentId)) allChildrenByParent.set(parentId, []);
        allChildrenByParent.get(parentId).push(user);
      });
    });
    var byName = function (left, right) {
      return left.name.localeCompare(right.name, "ru");
    };
    roots.sort(byName);
    allChildrenByParent.forEach(function (children) { children.sort(byName); });

    // Верхний руководитель часто добавлен родителем и тимлиду, и всем людям
    // тимлида. Такая транзитивная связь нужна для прав доступа, но в дереве
    // она дублировала бы человека рядом с его же веткой. Скрываем только эти
    // повторные рёбра; сама связь остаётся в базе и видна в счётчиках/бейджах.
    function reaches(fromId, targetId, visited) {
      if (fromId === targetId) return true;
      if (visited.has(fromId)) return false;
      visited.add(fromId);
      return (allChildrenByParent.get(fromId) || []).some(function (child) {
        return reaches(child.id, targetId, visited);
      });
    }

    var childrenByParent = new Map();
    allChildrenByParent.forEach(function (children, parentId) {
      var structural = children.filter(function (child) {
        return !children.some(function (other) {
          return other.id !== child.id && reaches(other.id, child.id, new Set([parentId]));
        });
      });
      childrenByParent.set(parentId, structural);
    });

    // Ветки с большим числом подчинённых сворачиваем по умолчанию,
    // чтобы дерево не растягивалось на несколько экранов по горизонтали.
    var AUTO_COLLAPSE_FROM = 7;

    function renderBranch(user, path) {
      if (path.has(user.id)) return "";
      var nextPath = new Set(path);
      nextPath.add(user.id);
      var children = childrenByParent.get(user.id) || [];
      var directChildCount = (allChildrenByParent.get(user.id) || []).length;
      var childHtml = children.map(function (child) {
        return renderBranch(child, nextPath);
      }).join("");
      if (!childHtml) return "<li>" + hierarchyNode(user, directChildCount) + "</li>";
      var collapsed = children.length >= AUTO_COLLAPSE_FROM;
      var toggle = '<button type="button" class="org-toggle" data-org-toggle ' +
        'data-count="' + children.length + '" title="Свернуть или развернуть ветку">' +
        (collapsed ? "+" + children.length : "–") + "</button>";
      return '<li class="' + (collapsed ? "org-collapsed" : "") + '">' +
        hierarchyNode(user, directChildCount) + toggle +
        "<ul>" + childHtml + "</ul></li>";
    }

    var tree = '<div class="hierarchy-canvas"><div class="org-tree"><ul class="org-root">' +
      roots.map(function (root) { return renderBranch(root, new Set()); }).join("") +
      "</ul></div></div>";
    container.innerHTML = overview + warning + tree;
    container.querySelectorAll("[data-org-toggle]").forEach(function (toggleButton) {
      toggleButton.addEventListener("click", function (event) {
        event.stopPropagation();
        var branch = toggleButton.closest("li");
        var collapsedNow = branch.classList.toggle("org-collapsed");
        toggleButton.textContent = collapsedNow
          ? "+" + toggleButton.getAttribute("data-count")
          : "–";
      });
    });
  }

  function hierarchyNode(user, childCount) {
    var parents = user.parents || [];
    var extraParents = Math.max(0, parents.length - 1);
    var reportLabel = childCount + " " +
      (childCount === 1 ? "подчинённый" :
        childCount >= 2 && childCount <= 4 ? "подчинённых" : "подчинённых");
    var extraHtml = extraParents
      ? '<span class="org-extra" title="' +
        escapeHtml(parents.map(function (parent) { return parent.name; }).join(", ")) +
        '">+' + extraParents + " рук.</span>"
      : "";
    var tagName = "article";
    var action = "";
    var teamNameHtml = childCount
      ? '<span class="org-team-name"><span>Команда</span><b>' +
        escapeHtml(user.team_name || "Название не задано") + "</b>" +
        (teamState.canManage
          ? '<button type="button" data-team-action="team-name" data-user-id="' +
            escapeHtml(user.id) + '">Изменить</button>'
          : "") + "</span>"
      : "";
    var editHtml = teamState.canManage
      ? '<button type="button" class="org-edit" data-team-action="edit" data-user-id="' +
        escapeHtml(user.id) + '">Профиль</button>'
      : "";
    return "<" + tagName + ' class="org-node' + (childCount ? " is-lead" : "") + '"' + action + ">" +
      '<span class="org-node-top"><span class="org-avatar">' +
      escapeHtml(initials(user.name)) + '</span><span class="org-copy">' +
      '<span class="org-name">' + escapeHtml(user.name) + "</span>" +
      '<span class="org-login">@' + escapeHtml(user.login) +
      '</span></span><span class="hierarchy-status ' +
      (user.status === "active" ? "" : "blocked") + '" title="' +
      (user.status === "active" ? "Активен" : "Заблокирован") + '"></span></span>' +
      teamNameHtml +
      '<span class="org-foot"><span>' + reportLabel + "</span>" + extraHtml +
      editHtml + "</span></" + tagName + ">";
  }

  function openRoleEditor(role) {
    var form = byId("roleForm");
    if (!form) return;
    form.reset();
    form.dataset.roleId = role ? role.id : "";
    text("roleModalTitle", role ? "Редактирование роли" : "Новая роль");
    form.elements.roleName.value = role ? role.name : "";
    form.elements.roleName.disabled = Boolean(role && role.name === "Administrator");
    form.elements.roleDescription.value = role ? role.description || "" : "";
    // Область доступа: чьи строки увидит человек с этой ролью.
    var scope = (role && role.data_scope) || "team";
    Array.prototype.forEach.call(form.elements.dataScope, function (input) {
      input.checked = input.value === scope;
    });
    var summaries = !role || role.show_finance_summaries !== false;
    // «Удалить роль» — внутри окна: на карточке кнопка стояла рядом с
    // «Редактировать», и промахнуться было слишком легко. Удалить можно любую
    // роль, в том числе стандартную; людей с ней переносят на другую роль.
    var deleteButton = byId("deleteRoleButton");
    if (deleteButton) {
      var own = Boolean(role && currentSessionUser && currentSessionUser.role &&
        currentSessionUser.role.id === role.id);
      deleteButton.hidden = !role;
      deleteButton.disabled = own;
      deleteButton.style.opacity = own ? ".5" : "";
      deleteButton.textContent = "Удалить роль";
      deleteButton.title = own ? "Это ваша роль — её удаление отрежет вам доступ" : "";
    }
    var move = byId("roleDeleteMove");
    if (move) move.hidden = true;
    var selected = new Set((role && role.permissions || []).map(function (permission) {
      return permission.code;
    }));
    // Порядок — как в меню, а не по коду: право ищут глазами, от раздела к
    // разделу. По алфавиту «Meta Ads» уезжал бы в конец списка вслед за
    // латиницей, хотя в меню он третий.
    var codes = teamState.permissions.slice().sort(function (left, right) {
      var byModule = permissionOrder(left) - permissionOrder(right);
      return byModule || permissionLabel(left).localeCompare(permissionLabel(right), "ru");
    });
    var cell = function (name, value, label, checked, title) {
      return '<label style="display:flex;align-items:center;gap:9px;border:1px solid #E8E2E2;' +
        'border-radius:10px;padding:10px 11px;font-size:11.5px;font-weight:700;color:#5A5050"' +
        (title ? ' title="' + escapeHtml(title) + '"' : "") + ">" +
        '<input type="checkbox" name="' + name + '" value="' + escapeHtml(value) + '"' +
        (checked ? " checked" : "") + "> " + escapeHtml(label) + "</label>";
    };
    // Сводки — не отдельное право в базе, а флаг роли, но выбирают его там же,
    // где и остальной доступ к Финансам: сразу за «Финансы · просмотр».
    var summariesCell = cell(
      "showFinanceSummaries", "1", "Финансы · сводки верхнего уровня", summaries,
      "«Общая», «Tier1» и «Tier2/3». Своя книга и сводки по командам — по доступу к данным"
    );
    var placed = false;
    byId("rolePermissions").innerHTML = codes.map(function (code) {
      var html = cell("permission", code, permissionLabel(code), selected.has(code));
      if (code === "finance.view") {
        placed = true;
        return html + summariesCell;
      }
      return html;
    }).join("") + (placed ? "" : summariesCell);
    byId("roleModal").classList.add("open");
    document.body.style.overflow = "hidden";
  }

  function showTemporaryPassword(password) {
    var existing = byId("temporaryPasswordDialog");
    if (existing) existing.remove();
    var dialog = document.createElement("div");
    dialog.id = "temporaryPasswordDialog";
    dialog.className = "modal-backdrop open";
    dialog.innerHTML = '<div class="modal-card" style="width:min(480px,100%);padding:24px">' +
      '<h2 class="team-heading" style="font-size:19px">Временный пароль</h2>' +
      '<p style="font-size:12px;color:#857D7D;margin-top:7px">Покажите его пользователю один раз.</p>' +
      '<input id="temporaryPasswordValue" class="form-input" readonly value="' +
      escapeHtml(password) + '" style="margin-top:18px;font-family:Inter">' +
      '<div style="display:flex;justify-content:flex-end;gap:9px;margin-top:17px">' +
      '<button type="button" data-team-action="close-secret" style="height:40px;border:1px solid #E5DFDF;' +
      'background:#fff;border-radius:9px;padding:0 14px">Закрыть</button>' +
      '<button type="button" data-team-action="copy-secret" style="height:40px;padding:0 16px;border:0;border-radius:10px;background:#B91414;color:#fff;font-family:Inter,-apple-system,Helvetica Neue,sans-serif;font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.02em;cursor:pointer;box-shadow:0 8px 18px rgba(185,20,20,.24)">Копировать</button>' +
      "</div></div>";
    document.body.appendChild(dialog);
  }

  function bindTeamActions() {
    if (document.documentElement.dataset.teamActionsBound) return;
    document.documentElement.dataset.teamActionsBound = "true";
    document.addEventListener("click", async function (event) {
      var openUser = event.target.closest("#openUserModal");
      var openRole = event.target.closest("#openRoleModal");
      var action = event.target.closest("[data-team-action]");
      var pickerHit = null;
      Object.keys(PICKERS).forEach(function (kind) {
        var prefix = PICKERS[kind].prefix;
        if (event.target.closest("#" + prefix + "Trigger")) {
          pickerHit = { kind: kind, action: "toggle" };
        } else if (event.target.closest("#" + prefix + "Clear")) {
          pickerHit = { kind: kind, action: "clear" };
        } else if (!pickerHit && event.target.closest("#" + prefix)) {
          pickerHit = { kind: kind, action: "inside" };
        }
      });
      if (pickerHit && pickerHit.action === "toggle") {
        event.preventDefault();
        setPickerOpen(
          pickerHit.kind,
          !byId(PICKERS[pickerHit.kind].prefix).classList.contains("open")
        );
        return;
      }
      if (pickerHit && pickerHit.action === "clear") {
        event.preventDefault();
        setPickerSelection(pickerHit.kind, []);
        return;
      }
      if (!pickerHit) closeAllPickers();
      if (openUser) {
        event.preventDefault();
        event.stopImmediatePropagation();
        openUserEditor(null);
        return;
      }
      if (openRole) {
        event.preventDefault();
        event.stopImmediatePropagation();
        openRoleEditor(null);
        return;
      }
      var deleteRoleButton = event.target.closest("#deleteRoleButton");
      if (deleteRoleButton) {
        event.preventDefault();
        if (!deleteRoleButton.disabled) deleteRole(byId("roleForm").dataset.roleId).catch(fail);
        return;
      }
      if (event.target.closest("#closeRoleModal") || event.target.closest("#cancelRoleModal")) {
        closeTeamModal("roleModal");
        return;
      }
      if (!action) return;
      event.preventDefault();
      var name = action.dataset.teamAction;
      if (name === "edit") {
        openUserEditor(teamState.users.find(function (user) {
          return user.id === action.dataset.userId;
        }));
      } else if (name === "team-name") {
        var lead = teamState.users.find(function (user) {
          return user.id === action.dataset.userId;
        });
        if (!lead) return;
        var teamName = await askPrompt({
          title: "Название команды",
          message: "Оно будет показано в селекторе и заголовке финансовой сводки.",
          value: lead.team_name || "",
          confirmLabel: "Сохранить"
        });
        if (teamName === null) return;
        try {
          await api.patch("/users/" + lead.id, {
            team_name: teamName.trim() || null
          });
          toast("Название команды сохранено");
          await loadTeam();
        } catch (error) {
          fail(error);
        }
      } else if (name === "delete-role") {
        if (!action.disabled) deleteRole(action.dataset.roleId).catch(fail);
      } else if (name === "edit-role") {
        openRoleEditor(teamState.roles.find(function (role) {
          return role.id === action.dataset.roleId;
        }));
      } else if (name === "toggle-status") {
        var user = teamState.users.find(function (item) {
          return item.id === action.dataset.userId;
        });
        if (!user) return;
        if (!(await askConfirm({
          title: user.status === "active"
            ? "Заблокировать пользователя?"
            : "Разблокировать пользователя?",
          message: user.name + " (@" + user.login + ")",
          confirmLabel: user.status === "active" ? "Заблокировать" : "Разблокировать",
          danger: user.status === "active"
        }))) return;
        try {
          await api.patch(
            "/users/" + user.id + "/status?new_status=" +
            (user.status === "active" ? "blocked" : "active"),
            {}
          );
          toast(user.status === "active" ? "Пользователь заблокирован" : "Пользователь активирован");
          await loadTeam();
        } catch (error) {
          fail(error);
        }
      } else if (name === "delete") {
        var doomed = teamState.users.find(function (item) {
          return item.id === action.dataset.userId;
        });
        if (!doomed) return;
        if (!(await askConfirm({
          title: "Удалить пользователя вместе с данными?",
          message: "«" + doomed.name + "» (@" + doomed.login + "). Будут удалены " +
            "записи Медиаборда, Финансов и финансовые книги. Подчинённые аккаунты " +
            "останутся, но будут отвязаны. Действие необратимо.",
          confirmLabel: "Удалить",
          danger: true
        }))) return;
        try {
          await api.delete("/users/" + doomed.id + "?purge=true");
          toast("Пользователь и его данные удалены");
          await loadTeam();
        } catch (error) {
          fail(error);
        }
      } else if (name === "reset-password") {
        if (!(await askConfirm({
          title: "Сбросить пароль?",
          message: "Текущий пароль перестанет работать сразу после сброса.",
          confirmLabel: "Сбросить",
          danger: true
        }))) return;
        try {
          var result = await api.post("/users/" + action.dataset.userId + "/reset-password", {});
          showTemporaryPassword(result.temporary_password);
        } catch (error) {
          fail(error);
        }
      } else if (name === "copy-secret") {
        var value = byId("temporaryPasswordValue");
        if (value) {
          value.select();
          if (navigator.clipboard && navigator.clipboard.writeText) {
            await navigator.clipboard.writeText(value.value);
          } else {
            document.execCommand("copy");
          }
          toast("Пароль скопирован");
        }
      } else if (name === "close-secret") {
        var secretDialog = byId("temporaryPasswordDialog");
        if (secretDialog) secretDialog.remove();
      }
    }, true);
    document.addEventListener("input", function (event) {
      var kind = Object.keys(PICKERS).find(function (name) {
        return event.target.id === PICKERS[name].prefix + "Search";
      });
      if (!kind) return;
      pickerState[kind].search = event.target.value;
      renderPicker(kind);
    });
    document.addEventListener("change", function (event) {
      var box = event.target.closest("[data-picker-id]");
      if (!box) return;
      var kind = box.dataset.pickerKind;
      if (box.checked) pickerState[kind].selectedIds.add(box.dataset.pickerId);
      else pickerState[kind].selectedIds.delete(box.dataset.pickerId);
      renderPicker(kind);
    });
    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape") return;
      Object.keys(PICKERS).forEach(function (kind) {
        var host = byId(PICKERS[kind].prefix);
        if (host && host.classList.contains("open")) setPickerOpen(kind, false);
      });
    });
    var userForm = byId("createUserForm");
    if (userForm) {
      userForm.addEventListener("submit", saveUser, true);
    }
    bindUserTags();
    var groupsButton = byId("refreshKeitaroGroups");
    if (groupsButton) {
      groupsButton.addEventListener("click", function () {
        refreshKeitaroGroups().catch(fail);
      });
    }
    var roleForm = byId("roleForm");
    if (roleForm) {
      roleForm.addEventListener("submit", saveRole, true);
    }
    // «Выбрать всё» и «Снять всё»: у роли вроде владельца прав три десятка,
    // и отмечать их по одному — отдельное занятие.
    [["rolePermissionsAll", true], ["rolePermissionsNone", false]].forEach(function (pair) {
      var button = byId(pair[0]);
      if (!button) return;
      button.addEventListener("click", function () {
        document.querySelectorAll('#rolePermissions input[type="checkbox"]').forEach(
          function (input) { input.checked = pair[1]; }
        );
      });
    });
  }

  function keitaroGroupsStatus(message, kind) {
    var host = byId("keitaroGroupsStatus");
    if (!host) return;
    host.textContent = message || "";
    host.hidden = !message;
    host.classList.toggle("is-error", kind === "error");
    host.classList.toggle("is-muted", kind === "muted");
  }

  /* Нового баера заводят так: в Keitaro открывают CRM доступ к его группе, а
     здесь привязывают к ней пользователя. Ждать синхронизации по расписанию,
     чтобы группа появилась в списке, незачем — кнопка забирает справочники
     Keitaro сразу. Уже выбранные в форме значения при этом не сбрасываются. */
  async function refreshKeitaroGroups() {
    var form = byId("createUserForm");
    var button = byId("refreshKeitaroGroups");
    if (!form || !button || button.disabled) return;
    var picked = {
      company: form.elements.companyGroup.value,
      offer: form.elements.offerGroup.value
    };
    button.disabled = true;
    button.classList.add("is-busy");
    keitaroGroupsStatus("Загружаем группы из Keitaro…", "muted");
    try {
      var result = await api.post("/integrations/keitaro/groups/refresh", {});
      teamState.campaignGroups = uniqueValues(
        teamState.campaignGroups.concat(result.campaign_groups || [])
      );
      teamState.offerGroups = uniqueValues(
        teamState.offerGroups.concat(result.offer_groups || [])
      );
      selectOptions(form.elements.companyGroup, teamState.campaignGroups, "Без привязки");
      selectOptions(form.elements.offerGroup, teamState.offerGroups, "Без привязки");
      form.elements.companyGroup.value = picked.company;
      form.elements.offerGroup.value = picked.offer;
      var fresh = (result.new_campaign_groups || []).concat(result.new_offer_groups || []);
      if (fresh.length) keitaroGroupsStatus("Новые группы: " + fresh.join(", "));
      else keitaroGroupsStatus("Новых групп нет — списки актуальны", "muted");
    } catch (error) {
      if (error && error.status === 401) return fail(error);
      keitaroGroupsStatus(error && error.message ? error.message
        : "Не удалось загрузить группы из Keitaro", "error");
    } finally {
      button.disabled = false;
      button.classList.remove("is-busy");
    }
  }

  async function saveUser(event) {
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
    var form = event.currentTarget;
    var userId = form.dataset.userId;
    var submit = byId("saveUserButton");
    // Логин — он же имя пользователя: два поля значили одно и то же, и в
    // списке команды человек искал себя то по имени, то по логину.
    var login = form.elements.login.value.trim().replace(/^@/, "");
    var payload = {
      name: login,
      login: login,
      role_id: form.elements.role.value,
      status: form.elements.status.value,
      parent_ids: Array.from(pickerState.parent.selectedIds),
      child_ids: Array.from(pickerState.child.selectedIds),
      keitaro_company_group: form.elements.companyGroup.value || null,
      keitaro_offer_group: form.elements.offerGroup.value || null,
      finance_tags: userTagList()
    };
    var pendingTag = byId("userTagInput");
    if (pendingTag && addUserTag(pendingTag.value)) {
      pendingTag.value = "";
      payload.finance_tags = userTagList();
    }
    if (!userId) payload.password = form.elements.password.value;
    submit.disabled = true;
    submit.textContent = "Сохраняю…";
    try {
      if (userId) await api.patch("/users/" + userId, payload);
      else await api.post("/users", payload);
      closeTeamModal("userModal");
      toast(userId ? "Пользователь обновлён" : "Пользователь создан");
      await loadTeam();
    } catch (error) {
      fail(error);
    } finally {
      submit.disabled = false;
      submit.textContent = userId ? "Сохранить изменения" : "Создать пользователя";
    }
  }

  async function saveRole(event) {
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
    var form = event.currentTarget;
    var roleId = form.dataset.roleId;
    var submit = byId("saveRoleButton");
    var scopeInput = form.querySelector('input[name="dataScope"]:checked');
    var payload = {
      name: form.elements.roleName.value.trim(),
      description: form.elements.roleDescription.value.trim(),
      data_scope: scopeInput ? scopeInput.value : "team",
      show_finance_summaries: Boolean(
        form.querySelector('input[name="showFinanceSummaries"]:checked')
      ),
      permission_codes: Array.from(
        form.querySelectorAll('input[name="permission"]:checked')
      ).map(function (input) { return input.value; })
    };
    submit.disabled = true;
    submit.textContent = "Сохраняю…";
    try {
      if (roleId) await api.patch("/roles/" + roleId, payload);
      else await api.post("/roles", payload);
      closeTeamModal("roleModal");
      toast(roleId ? "Роль обновлена" : "Роль создана");
      await loadTeam();
    } catch (error) {
      fail(error);
    } finally {
      submit.disabled = false;
      submit.textContent = "Сохранить роль";
    }
  }

  function settingRow(item, kind) {
    var typeCells = '<td style="padding:14px 18px"><span class="data-pill">' +
      (item.provider_type === "agent" ? "Агент" : "Платёжка") + "</span></td>" +
      '<td style="padding:14px 18px;font-size:13px;font-weight:700">' +
      escapeHtml(decimal(item.commission_pct, 1)) + "%</td>";
    var actions = settingsCanManage()
      ? settingActionMenu(item, kind)
      : '<span style="color:#C9BFBF">•••</span>';
    return '<tr class="settings-row" data-settings-row data-kind="agents" data-status="' +
      escapeHtml(item.status) + '" data-type="' + escapeHtml(item.provider_type) + '"' +
      ' style="border-bottom:1px solid #F7F4F4">' +
      '<td style="padding:14px 24px"><div style="display:flex;align-items:center;gap:12px">' +
      '<span class="entity-icon" style="background:#FCF1F1;color:#B91414;font-size:11px;font-weight:800">' +
      escapeHtml(initials(item.name)) + "</span>" +
      '<div style="font-size:13.5px;font-weight:700">' + escapeHtml(item.name) + "</div></div></td>" +
      typeCells +
      '<td style="padding:14px 18px">' + statusBadge(item.status) + "</td>" +
      '<td style="padding:14px 18px">' + actions + "</td></tr>";
  }

  function settingActionMenu(item, kind) {
    var toggleLabel = item.status === "active" ? "Деактивировать" : "Активировать";
    function button(op, label) {
      return '<button type="button" data-setting-action="' + op + '" data-kind="' + kind +
        '" data-id="' + escapeHtml(item.id) + '">' + label + "</button>";
    }
    return '<details class="row-actions" style="position:relative">' +
      '<summary style="width:34px;height:34px;border-radius:9px;display:flex;align-items:center;' +
      'justify-content:center;cursor:pointer"><svg width="18" height="18" viewBox="0 0 24 24">' +
      '<circle cx="5" cy="12" r="1.6" fill="#9B9292"/><circle cx="12" cy="12" r="1.6" fill="#9B9292"/>' +
      '<circle cx="19" cy="12" r="1.6" fill="#9B9292"/></svg></summary><div class="row-menu">' +
      button("edit", "Редактировать") + button("toggle", toggleLabel) + button("delete", "Удалить") +
      "</div></details>";
  }

  function formatDate(value) {
    if (!value) return "Ещё не запускалась";
    return new Intl.DateTimeFormat("ru-RU", {
      dateStyle: "short",
      timeStyle: "short",
      timeZone: "Europe/Moscow"
    }).format(new Date(value));
  }

  function renderIntegrations(overview) {
    var panel = byId("integrationsPanel");
    if (!panel) return;
    var grid = panel.querySelector(".integration-grid");
    if (!grid) return;
    var connections = overview.connections || [];
    // The cards are the only place a connection can be edited, so keep the raw
    // objects around: the edit form has to prefill from them.
    settingsState.connections = connections;
    var canManage = settingsCanManage();
    grid.innerHTML = connections.length ? connections.map(function (connection) {
      var run = connection.last_run;
      var failed = run && run.status === "failed";
      var statusText = failed ? "Ошибка" : connection.status === "active" ? "Подключено" : "Отключено";
      var statusColor = failed ? "#C41616" : connection.status === "active" ? "#16B57F" : "#9B9292";
      return '<article class="integration-card integration-row" data-connection-id="' +
        escapeHtml(connection.id) + '">' +
        '<div class="integration-row__main">' +
        '<div class="integration-row__mark">K</div>' +
        '<div class="integration-row__title">' +
        '<div class="settings-heading" style="font-size:15px;font-weight:700">' +
        escapeHtml(connection.name) + '</div>' +
        '<div class="integration-row__url">' + escapeHtml(connection.base_url) +
        "</div></div>" +
        '<div class="integration-row__facts">' +
        '<span><b>Синхронизация</b>' + escapeHtml(formatDate(connection.last_sync_at)) +
        "</span>" +
        '<span><b>Расписание</b>каждые ' +
        escapeHtml(connection.sync_interval_minutes) + " мин.</span>" +
        "</div>" +
        '<span class="row-status ' + (failed || connection.status !== "active"
          ? "row-status--off" : "row-status--on") +
        '" style="color:' + statusColor + '">' + statusText + "</span>" +
        '<div class="row-side">' +
        '<button class="live-sync-connection row-btn" type="button" data-connection-id="' +
        escapeHtml(connection.id) + '">Синхронизировать</button>' +
        (canManage ? connectionActionButton(connection.id, "edit", "Изменить") +
          connectionActionButton(connection.id, "delete", "Удалить") : "") +
        "</div></div>" +
        connectionErrorBlock(run) +
        "</article>";
    }).join("") : '<div style="grid-column:1/-1;padding:36px;border:1px dashed #DDD5D5;border-radius:14px;text-align:center;color:#857D7D">' +
      'Подключений пока нет. Нажмите «Добавить подключение» и укажите URL вашего Keitaro-трекера.</div>';
  }

  /* The card used to say "Ошибка" and nothing else, which is exactly the case where
   * the reason matters most — a wrong URL and a wrong key look identical otherwise. */
  function connectionErrorBlock(run) {
    if (!run || run.status !== "failed" || !run.error) return "";
    // A crash inside the sync arrives here as a whole SQL traceback. The first
    // sentence is the part a human reads; the rest stays in the tooltip.
    var full = String(run.error).replace(/\s+/g, " ").trim();
    var short = full.length > 180 ? full.slice(0, 180) + "…" : full;
    return '<div title="' + escapeHtml(full) + '" style="margin-top:12px;padding:11px 12px;' +
      'border-radius:11px;background:#FCF1F1;border:1px solid #F3DADA;font-size:11px;' +
      'line-height:1.5;color:#8E2020">' + escapeHtml(short) + "</div>";
  }

  var CONNECTION_ICONS = {
    edit: '<path d="M4 20h4L19 9l-4-4L4 16v4Z" fill="none" stroke="currentColor" stroke-width="2" ' +
      'stroke-linejoin="round"/>',
    delete: '<path d="M5 7h14M10 7V5h4v2M7 7l1 13h8l1-13" fill="none" stroke="currentColor" ' +
      'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>'
  };

  function connectionActionButton(id, action, title) {
    return '<button type="button" data-connection-action="' + action + '" data-connection-id="' +
      escapeHtml(id) + '" title="' + escapeHtml(title) + '" aria-label="' + escapeHtml(title) +
      '" style="width:39px;height:39px;flex:0 0 auto;border:1px solid #E5DFDF;border-radius:10px;' +
      'background:#fff;color:' + (action === "delete" ? "#C41616" : "#6A6161") +
      ';display:flex;align-items:center;justify-content:center">' +
      '<svg width="17" height="17" viewBox="0 0 24 24">' + CONNECTION_ICONS[action] + "</svg></button>";
  }

  var settingsState = {
    activeTab: "agents", providers: [], connections: [], canManage: false
  };

  function allows(code) {
    return Boolean(currentSessionUser && currentSessionUser.role &&
      (currentSessionUser.role.permissions || []).some(function (permission) {
        return permission.code === "*" || permission.code === code;
      }));
  }

  function settingsCanManage() {
    return currentSessionUser && currentSessionUser.role &&
      (currentSessionUser.role.permissions || []).some(function (permission) {
        return permission.code === "*" || permission.code === "settings.manage";
      });
  }

  function setSettingsModal(id, open) {
    var modal = byId(id);
    if (modal) modal.classList.toggle("open", open);
    document.body.style.overflow = open ? "hidden" : "";
  }

  function closeEntityModal() {
    setSettingsModal("entityModal", false);
  }

  function updateSettingsHeaderAction() {
    var button = byId("openEntityModal");
    if (!button) return;
    // Кнопка заводит агента и принадлежит одной вкладке. Перечислять остальные
    // в запрете нельзя: список разделов растёт, и «Тиры стран» уже оказались
    // не учтены — кнопка висела над справочником стран.
    if (settingsState.activeTab !== "agents" || !settingsCanManage()) {
      button.style.display = "none";
      return;
    }
    button.style.display = "flex";
    var label = byId("entityButtonLabel");
    if (label) label.textContent = "Добавить агента";
  }

  function switchSettingsTab(tab) {
    settingsState.activeTab = tab;
    document.querySelectorAll("[data-settings-tab]").forEach(function (item) {
      item.classList.toggle("active", item.dataset.settingsTab === tab);
    });
    document.querySelectorAll("[data-settings-panel]").forEach(function (panel) {
      panel.classList.toggle("active", panel.dataset.settingsPanel === tab);
    });
    var search = byId("settingsSearch");
    if (search) search.value = "";
    updateSettingsHeaderAction();
    applySettingsFilters();
  }

  function applySettingsFilters() {
    var search = byId("settingsSearch");
    var query = (search ? search.value : "").trim().toLowerCase();
    document.querySelectorAll("[data-settings-row]").forEach(function (row) {
      var current = row.dataset.kind === settingsState.activeTab;
      var matches = current &&
        (!query || row.textContent.toLowerCase().indexOf(query) >= 0);
      row.style.display = matches ? "" : "none";
    });
  }

  function findSetting(id) {
    return settingsState.providers.find(function (item) {
      return String(item.id) === String(id);
    });
  }

  function openEntityEditor(item) {
    var form = byId("entityForm");
    if (!form) return;
    form.dataset.editId = item ? item.id : "";
    text("entityModalTitle", item ? "Редактирование записи" : "Новый агент или платёжка");
    text("entityModalSubtitle", "Комиссия агента или платёжной системы для SPEND");
    form.elements.entityName.value = item ? item.name : "";
    form.elements.commission.value = item ? Number(item.commission_pct) : 0;
    form.elements.entityType.value = item && item.provider_type === "payment"
      ? "Платёжка" : "Агент";
    form.elements.entityStatus.value = item && item.status !== "active" ? "Неактивен" : "Активен";
    setSettingsModal("entityModal", true);
    window.setTimeout(function () {
      var input = form.querySelector("input");
      if (input) input.focus();
    }, 50);
  }

  async function submitEntity() {
    var form = byId("entityForm");
    var id = form.dataset.editId;
    var name = form.elements.entityName.value.trim();
    if (!name) return;
    var status = form.elements.entityStatus.value === "Неактивен" ? "inactive" : "active";
    var commission = Number(form.elements.commission.value || 0);
    var submit = form.querySelector('[type="submit"]');
    submit.disabled = true;
    submit.textContent = "Сохраняю…";
    try {
      var payload = {
        name: name,
        provider_type: form.elements.entityType.value === "Платёжка" ? "payment" : "agent",
        commission_pct: commission,
        status: status
      };
      if (id) await api.put("/spend-providers/" + id, payload);
      else await api.post("/spend-providers", payload);
      toast(id ? "Изменения сохранены" : "Запись добавлена");
      closeEntityModal();
      await loadSettings();
    } finally {
      submit.disabled = false;
      submit.textContent = "Сохранить";
    }
  }

  async function toggleSetting(id) {
    var item = findSetting(id);
    if (!item) return;
    var status = item.status === "active" ? "inactive" : "active";
    await api.put("/spend-providers/" + id, {
      name: item.name,
      provider_type: item.provider_type,
      commission_pct: Number(item.commission_pct),
      status: status
    });
    toast(status === "active" ? "Активировано" : "Деактивировано");
    await loadSettings();
  }

  async function deleteSetting(id) {
    var item = findSetting(id);
    if (!item) return;
    if (!(await askConfirm({
      title: "Удалить «" + item.name + "»?",
      message: "Действие необратимо.",
      confirmLabel: "Удалить",
      danger: true
    }))) return;
    await api.delete("/spend-providers/" + id);
    toast("Удалено");
    await loadSettings();
  }


  /* ---------- тиры стран ----------
   *
   * Хранится только Tier1: всё остальное — Tier2/3, и держать вторую сотню
   * строк ради одного и того же ответа незачем. Страна переезжает между
   * колонками одним кликом, сохранение идёт целым списком.
   */

  var tierState = { tier1: [], tier23: [], dirty: false };

  function renderTiers() {
    var query = (byId("tierSearch") ? byId("tierSearch").value : "").trim().toLowerCase();
    var matches = function (row) {
      return !query ||
        row.code.toLowerCase().indexOf(query) >= 0 ||
        row.name.toLowerCase().indexOf(query) >= 0;
    };
    var draw = function (host, rows, action, symbol, title) {
      var visible = rows.filter(matches);
      if (!visible.length) {
        byId(host).innerHTML = '<div class="tier-empty">Ничего не найдено</div>';
        return;
      }
      byId(host).innerHTML = '<table class="tier-table"><thead><tr>' +
        '<th class="tier-code">Код</th><th>Страна</th><th></th></tr></thead><tbody>' +
        visible.map(function (row) {
          return '<tr><td class="tier-code">' + escapeHtml(row.code) + "</td><td>" +
            escapeHtml(row.name) + "</td><td>" +
            (settingsState.canManage
              ? '<button type="button" data-tier-move="' + action + '" data-code="' +
                escapeHtml(row.code) + '" aria-label="' + title + '" title="' + title +
                '">' + symbol + "</button>"
              : "") + "</td></tr>";
        }).join("") + "</tbody></table>";
    };
    draw("tierOneList", tierState.tier1, "down", "→", "Перенести в Tier2/3");
    draw("tierTwoList", tierState.tier23, "up", "←", "Перенести в Tier1");
    text("tierOneCount", number(tierState.tier1.length));
    text("tierTwoCount", number(tierState.tier23.length));
    text("settingsTiersTabCount", number(tierState.tier1.length));
  }

  function moveTier(code, direction) {
    var from = direction === "down" ? "tier1" : "tier23";
    var to = direction === "down" ? "tier23" : "tier1";
    var index = tierState[from].findIndex(function (row) { return row.code === code; });
    if (index < 0) return;
    var row = tierState[from].splice(index, 1)[0];
    tierState[to].push(row);
    tierState[to].sort(function (a, b) { return a.name.localeCompare(b.name, "ru"); });
    renderTiers();
    saveTiers();
  }

  async function saveTiers() {
    try {
      var saved = await api.put("/country-tiers", {
        tier1: tierState.tier1.map(function (row) { return row.code; })
      });
      tierState.tier1 = saved.tier1 || [];
      tierState.tier23 = saved.tier23 || [];
      renderTiers();
      toast("Тиры сохранены");
    } catch (error) {
      toast(error && error.message ? error.message : "Не удалось сохранить");
      await loadTiers();
    }
  }

  async function loadTiers() {
    if (!byId("tierOneList")) return;
    try {
      var payload = await api.get("/country-tiers");
      tierState.tier1 = payload.tier1 || [];
      tierState.tier23 = payload.tier23 || [];
      renderTiers();
    } catch (error) {
      byId("tierOneList").innerHTML =
        '<div class="tier-empty">' +
        escapeHtml(error && error.message ? error.message : "Справочник недоступен") +
        "</div>";
    }
  }

  function bindSettingsPage() {
    if (document.documentElement.dataset.settingsBound) return;
    document.documentElement.dataset.settingsBound = "true";
    document.querySelectorAll("[data-settings-tab]").forEach(function (tab) {
      tab.addEventListener("click", function () {
        switchSettingsTab(tab.dataset.settingsTab);
      });
    });
    var tierSearch = byId("tierSearch");
    if (tierSearch) tierSearch.addEventListener("input", renderTiers);
    ["tierOneList", "tierTwoList"].forEach(function (id) {
      var host = byId(id);
      if (!host) return;
      host.addEventListener("click", function (event) {
        var button = event.target.closest ? event.target.closest("[data-tier-move]") : null;
        if (button) moveTier(button.dataset.code, button.dataset.tierMove);
      });
    });
    var addButton = byId("openEntityModal");
    if (addButton) addButton.addEventListener("click", function () {
      openEntityEditor(null);
    });
    ["closeEntityModal", "cancelEntityModal"].forEach(function (id) {
      var element = byId(id);
      if (element) element.addEventListener("click", closeEntityModal);
    });
    var entityModal = byId("entityModal");
    if (entityModal) entityModal.addEventListener("click", function (event) {
      if (event.target === entityModal) closeEntityModal();
    });
    var addConnection = byId("addConnection");
    if (addConnection) addConnection.addEventListener("click", function () {
      openConnectionEditor(null);
    });
    ["closeConnectionModal", "cancelConnectionModal"].forEach(function (id) {
      var element = byId(id);
      if (element) element.addEventListener("click", function () {
        setSettingsModal("connectionModal", false);
      });
    });
    var connectionModal = byId("connectionModal");
    if (connectionModal) connectionModal.addEventListener("click", function (event) {
      if (event.target === connectionModal) setSettingsModal("connectionModal", false);
    });
    var form = byId("entityForm");
    if (form) form.addEventListener("submit", function (event) {
      event.preventDefault();
      submitEntity().catch(fail);
    });
    var search = byId("settingsSearch");
    if (search) search.addEventListener("input", applySettingsFilters);
    document.addEventListener("click", function (event) {
      var action = event.target.closest("[data-setting-action]");
      if (!action) return;
      var menu = action.closest("details");
      if (menu) menu.removeAttribute("open");
      var id = action.getAttribute("data-id");
      var op = action.getAttribute("data-setting-action");
      if (op === "edit") openEntityEditor(findSetting(id));
      else if (op === "toggle") toggleSetting(id).catch(fail);
      else if (op === "delete") deleteSetting(id).catch(fail);
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        closeEntityModal();
        setSettingsModal("connectionModal", false);
      }
    });
    updateSettingsHeaderAction();
  }

  async function loadSettings() {
    settingsState.canManage = allows("settings.manage");
    loadTiers().catch(function () {});  // ошибку рисует сам загрузчик
    var results = await Promise.all([
      api.getAll("/spend-providers"),
      api.get("/integrations/keitaro/overview")
    ]);
    var providers = results[0];
    var overview = results[1];
    settingsState.providers = providers.items || [];
    text("settingsProvidersTotal", number((providers.items || []).length));
    text("settingsConnectionsTotal", number((overview.connections || []).length));
    text("settingsProvidersTabCount", number(providers.total));
    text("settingsConnectionsTabCount", number((overview.connections || []).length));
    text("settingsProvidersResultCount", "Показано " + (providers.items || []).length +
      " из " + providers.total + " записей");
    text("settingsSyncSummary", overview.configured
      ? "Подключений: " + (overview.connections || []).length +
        " · кампаний: " + number(overview.counts.campaigns) +
        " · строк статистики: " + number(overview.counts.stat_rows)
      : "Синхронизации ещё не запускались");
    var providersBody = byId("providersTableBody");
    if (providersBody) {
      providersBody.innerHTML = (providers.items || []).length
        ? providers.items.map(function (item) { return settingRow(item, "provider"); }).join("")
        : emptyRow(5, "Агенты и платёжки пока не добавлены");
    }
    renderIntegrations(overview);
    bindSettingsActions();
    updateSettingsHeaderAction();
    applySettingsFilters();
  }

  async function currentConnection() {
    var page = await api.get("/integrations/keitaro?limit=1");
    return page.items && page.items[0];
  }

  async function runSync(connectionId, mode, button) {
    if (!connectionId) {
      toast("Сначала добавьте подключение на странице «Настройки»", "info");
      window.setTimeout(function () { window.location.href = "/Settings.dc.html"; }, 900);
      return;
    }
    var original = button ? button.textContent : "";
    if (button) {
      button.disabled = true;
      button.textContent = "Запускаю…";
    }
    try {
      var result = await api.post(
        "/integrations/keitaro/" + connectionId + "/sync?mode=" + (mode || "incremental"),
        {},
        "ui-sync-" + connectionId + "-" + Date.now()
      );
      toast("Синхронизация Keitaro поставлена в очередь", "info");
      await pollSync(connectionId, result.run_id, button);
    } catch (error) {
      fail(error);
    } finally {
      if (button) {
        button.disabled = false;
        button.textContent = original || "Синхронизировать";
      }
    }
  }

  async function pollSync(connectionId, runId, button) {
    for (var attempt = 0; attempt < 60; attempt += 1) {
      await new Promise(function (resolve) { window.setTimeout(resolve, 2000); });
      var page = await api.get("/integrations/keitaro/" + connectionId + "/runs?limit=10");
      var run = (page.items || []).find(function (item) { return item.id === runId; });
      if (!run) continue;
      if (button) button.textContent = "Синхронизация " + number(run.progress_pct) + "%";
      if (run.status === "success") {
        toast("Keitaro синхронизирован: обработано " + number(run.rows_processed) + " строк");
        await reloadCurrentPage();
        return;
      }
      if (run.status === "failed") {
        throw new Error(run.error || "Синхронизация Keitaro завершилась с ошибкой");
      }
    }
    toast("Синхронизация продолжается в фоне", "info");
  }

  function bindSettingsActions() {
    document.querySelectorAll(".live-sync-connection").forEach(function (button) {
      if (button.dataset.bound) return;
      button.dataset.bound = "true";
      button.addEventListener("click", function () {
        runSync(button.dataset.connectionId, "incremental", button);
      });
    });
    document.querySelectorAll("[data-connection-action]").forEach(function (button) {
      if (button.dataset.bound) return;
      button.dataset.bound = "true";
      button.addEventListener("click", function () {
        var id = button.dataset.connectionId;
        if (button.dataset.connectionAction === "edit") openConnectionEditor(findConnection(id));
        else deleteConnection(id).catch(fail);
      });
    });
  }

  var INTERVAL_LABELS = [
    { label: "Каждые 15 минут", minutes: 15 },
    { label: "Каждый час", minutes: 60 },
    { label: "Раз в сутки", minutes: 1440 },
    { label: "Только вручную", minutes: 1440 }
  ];

  function intervalMinutes(label) {
    for (var index = 0; index < INTERVAL_LABELS.length; index += 1) {
      if (INTERVAL_LABELS[index].label === label) return INTERVAL_LABELS[index].minutes;
    }
    return 15;
  }

  function intervalLabel(minutes) {
    for (var index = 0; index < INTERVAL_LABELS.length; index += 1) {
      if (INTERVAL_LABELS[index].minutes === minutes) return INTERVAL_LABELS[index].label;
    }
    return INTERVAL_LABELS[0].label;
  }

  function findConnection(id) {
    return (settingsState.connections || []).filter(function (item) {
      return item.id === id;
    })[0] || null;
  }

  /* One modal for both cases. `connection` null means "add"; otherwise the form is
   * prefilled and the API key stays blank — the server never hands it back, so an
   * empty field has to mean "keep the current one". */
  function openConnectionEditor(connection) {
    var form = byId("connectionForm");
    if (!form) return;
    form.reset();
    byId("connectionId").value = connection ? connection.id : "";
    byId("connectionName").value = connection ? connection.name : "";
    byId("connectionUrl").value = connection ? connection.base_url : "";
    var key = byId("connectionKey");
    key.value = "";
    key.required = !connection;
    key.placeholder = connection ? "Не менять" : "Введите ключ API";
    byId("connectionKeyHint").style.display = connection ? "block" : "none";
    byId("connectionInterval").value = intervalLabel(
      connection ? connection.sync_interval_minutes : 15
    );
    // «Интеграция включена» и пересинхронизация — только у существующего
    // подключения: новому нечего выключать и нечего загружать заново.
    byId("connectionStatusField").style.display = connection ? "flex" : "none";
    byId("connectionEnabled").checked = connection ? connection.status === "active" : true;
    resetResync(connection);
    text("connectionModalTitle", connection ? "Изменение подключения" : "Подключение Keitaro");
    text(
      "connectionModalSubtitle",
      connection ? "Проверим доступ и сохраним новые настройки" : "Добавьте новый аккаунт трекера"
    );
    text("connectionSubmit", connection ? "Проверить и сохранить" : "Проверить и подключить");
    setSettingsModal("connectionModal", true);
  }

  async function deleteConnection(id) {
    var connection = findConnection(id);
    if (!connection) return;
    var name = connection.name;
    if (!(await askConfirm({
      title: "Удалить подключение «" + name + "»?",
      message: "Вместе с ним из CRM пропадут офферы, партнёрки, кампании и " +
        "статистика, загруженные из этого трекера.",
      confirmLabel: "Удалить",
      danger: true
    }))) return;
    try {
      await api.delete("/integrations/keitaro/" + id);
    } catch (error) {
      // 409 means manual Медиаборд/Финансы rows hang off these offers. That is data
      // nobody synced, so it takes a second, explicit yes before it goes.
      if (error.status !== 409) throw error;
      if (!(await askConfirm({
        title: "Удалить вместе с записями?",
        message: error.message,
        confirmLabel: "Удалить всё",
        danger: true
      }))) return;
      await api.delete("/integrations/keitaro/" + id + "?purge=true");
    }
    toast("Подключение «" + name + "» удалено");
    await loadSettings();
    if (window.CelestialShell) await window.CelestialShell.refreshSync();
  }

  /* Пересинхронизация из «Дополнительно» в окне подключения: заново загрузить
     из Keitaro выбранный период. Обычная синхронизация только дописывает дни,
     а эта заменяет их — то, чего в трекере уже нет, из CRM тоже уходит. */
  var RESYNC_PERIODS = { 30: "30 дней", 90: "90 дней", 180: "180 дней", 365: "год" };
  var resyncState = { connectionId: null, days: 30, runId: null };

  function resyncStatus(message, isError) {
    var host = byId("connectionResyncStatus");
    if (!host) return;
    host.textContent = message || "";
    host.hidden = !message;
    host.classList.toggle("is-error", !!isError);
  }

  function paintResyncPeriods() {
    document.querySelectorAll("[data-resync-days]").forEach(function (button) {
      var on = Number(button.getAttribute("data-resync-days")) === resyncState.days;
      button.classList.toggle("is-on", on);
      button.setAttribute("aria-checked", on ? "true" : "false");
    });
  }

  function setResyncBusy(busy, label) {
    var run = byId("connectionResync");
    if (run) {
      run.disabled = busy;
      run.textContent = label || "Пересинхронизировать";
    }
    document.querySelectorAll("[data-resync-days]").forEach(function (button) {
      button.disabled = busy;
    });
  }

  function toggleAdvanced(open) {
    var toggle = byId("connectionAdvancedToggle");
    var body = byId("connectionAdvancedBody");
    if (!toggle || !body) return;
    toggle.setAttribute("aria-expanded", open ? "true" : "false");
    body.hidden = !open;
  }

  function resetResync(connection) {
    var advanced = byId("connectionAdvanced");
    if (!advanced) return;
    advanced.style.display = connection ? "block" : "none";
    // Окно того же подключения открыли, пока идёт пересинхронизация, —
    // прогресс остаётся на месте, а не сбрасывается в «ничего не запущено».
    if (connection && resyncState.connectionId === connection.id && resyncState.runId) {
      toggleAdvanced(true);
      return;
    }
    toggleAdvanced(false);
    resyncState.connectionId = connection ? connection.id : null;
    resyncState.days = 30;
    resyncState.runId = null;
    paintResyncPeriods();
    setResyncBusy(false);
    resyncStatus("");
  }

  async function startResync() {
    var connectionId = resyncState.connectionId;
    if (!connectionId || resyncState.runId) return;
    var days = resyncState.days;
    var period = RESYNC_PERIODS[days];
    if (!(await askConfirm({
      title: "Пересинхронизировать данные за " + period + "?",
      message: "Статистика и показатели Медиаборда за этот период загрузятся из Keitaro " +
        "заново — то, чего в трекере уже нет, из CRM уйдёт. Ручные правки и расходы " +
        "агентов останутся. Загрузка идёт в фоне, окно можно закрыть.",
      confirmLabel: "Пересинхронизировать"
    }))) return;
    setResyncBusy(true, "Запускаю…");
    resyncStatus("");
    try {
      var result = await api.post(
        "/integrations/keitaro/" + connectionId + "/sync?mode=resync&days=" + days,
        {},
        "ui-resync-" + connectionId + "-" + Date.now()
      );
      if (result.already_running && result.mode !== "resync") {
        setResyncBusy(false);
        resyncStatus("Сейчас идёт обычная синхронизация. Пересинхронизацию можно " +
          "запустить, когда она закончится.", true);
        return;
      }
      resyncState.runId = result.run_id;
      await followResync(connectionId, result.run_id, period);
    } catch (error) {
      if (error && error.status === 401) return fail(error);
      if (resyncState.connectionId === connectionId) {
        resyncState.runId = null;
        setResyncBusy(false);
        resyncStatus(error && error.message ? error.message
          : "Не удалось запустить пересинхронизацию", true);
      }
    }
  }

  /* Год по дню — это минуты, а не секунды: окно показывает, сколько дней уже
     загружено, и не бросает опрос через пару минут, как короткая синхронизация.
     Окно закрыли или открыли другое подключение — опрос идёт дальше молча и
     сообщает только итог. */
  async function followResync(connectionId, runId, period) {
    var deadline = Date.now() + 130 * 60 * 1000;
    while (Date.now() < deadline) {
      await new Promise(function (resolve) { window.setTimeout(resolve, 3000); });
      var page = await api.get("/integrations/keitaro/" + connectionId + "/runs?limit=10");
      var run = (page.items || []).find(function (item) { return item.id === runId; });
      var visible = resyncState.runId === runId;
      if (!run) continue;
      if (run.status === "success") {
        if (visible) {
          resyncState.runId = null;
          setResyncBusy(false);
          resyncStatus("Готово: данные за " + period + " загружены заново, строк обработано — " +
            number(run.rows_processed) + ".");
        }
        toast("Пересинхронизация Keitaro завершена");
        await reloadCurrentPage();
        return;
      }
      if (run.status === "failed") {
        if (visible) {
          resyncState.runId = null;
          setResyncBusy(false);
          resyncStatus(run.error || "Пересинхронизация завершилась с ошибкой", true);
        } else {
          toast(run.error || "Пересинхронизация Keitaro завершилась с ошибкой", "error");
        }
        return;
      }
      if (visible) {
        var details = run.details || {};
        setResyncBusy(true, "Пересинхронизация " + number(run.progress_pct) + "%");
        resyncStatus(details.days_total
          ? "Загружено дней: " + number(details.days_completed) + " из " +
            number(details.days_total)
          : "Загружаем справочники Keitaro…");
      }
    }
    if (resyncState.runId === runId) {
      resyncState.runId = null;
      setResyncBusy(false);
      resyncStatus("Пересинхронизация продолжается в фоне");
    }
  }

  function bindConnectionForm() {
    var form = byId("connectionForm");
    if (!form || form.dataset.liveBound) return;
    form.dataset.liveBound = "true";
    var advancedToggle = byId("connectionAdvancedToggle");
    if (advancedToggle) advancedToggle.addEventListener("click", function () {
      toggleAdvanced(advancedToggle.getAttribute("aria-expanded") !== "true");
    });
    document.querySelectorAll("[data-resync-days]").forEach(function (button) {
      button.addEventListener("click", function () {
        resyncState.days = Number(button.getAttribute("data-resync-days"));
        paintResyncPeriods();
      });
    });
    var resyncButton = byId("connectionResync");
    if (resyncButton) resyncButton.addEventListener("click", function () {
      startResync().catch(fail);
    });
    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      var editingId = byId("connectionId").value;
      var submit = byId("connectionSubmit");
      var label = submit.textContent;
      submit.disabled = true;
      submit.textContent = "Проверяю…";
      try {
        if (editingId) {
          var changes = {
            name: byId("connectionName").value.trim(),
            base_url: byId("connectionUrl").value.trim(),
            status: byId("connectionEnabled").checked ? "active" : "inactive",
            sync_interval_minutes: intervalMinutes(byId("connectionInterval").value)
          };
          var newKey = byId("connectionKey").value;
          if (newKey) changes.api_key = newKey;
          await api.patch("/integrations/keitaro/" + editingId, changes);
          setSettingsModal("connectionModal", false);
          form.reset();
          toast("Подключение обновлено");
          await loadSettings();
          if (window.CelestialShell) await window.CelestialShell.refreshSync();
          return;
        }
        var connection = await api.post("/integrations/keitaro", {
          name: byId("connectionName").value.trim(),
          base_url: byId("connectionUrl").value.trim(),
          api_key: byId("connectionKey").value,
          sync_interval_minutes: intervalMinutes(byId("connectionInterval").value),
          timezone: "Europe/Moscow",
          buyer_sub_id: 1,
          lookback_days: 2
        });
        setSettingsModal("connectionModal", false);
        form.reset();
        toast("Подключение проверено. Запускаю загрузку последних 90 дней.", "info");
        await loadSettings();
        var syncButton = document.querySelector(
          '.live-sync-connection[data-connection-id="' + connection.id + '"]'
        );
        runSync(connection.id, "backfill", syncButton);
      } catch (error) {
        fail(error);
      } finally {
        submit.disabled = false;
        submit.textContent = label;
      }
    }, true);
  }

  async function reloadCurrentPage() {
    var path = window.location.pathname.toLowerCase();
    if (path.indexOf("offer") >= 0) {
      if (window.CelestialCatalog) return window.CelestialCatalog.reloadOffers();
      return showCatalogMissing();
    }
    if (path.indexOf("mediaboard") >= 0) {
      if (window.CelestialBoard) return window.CelestialBoard.initMedia(currentSessionUser);
      return loadMedia();
    }
    if (path.indexOf("finance") >= 0) {
      if (window.CelestialFinance) return window.CelestialFinance.reload();
      return showFinanceMissing();
    }
    if (path.indexOf("settings") >= 0) return loadSettings();
    return loadDashboard();
  }

  /*
   * `/auth/me` used to gate everything, which cost a full round trip before the module
   * could even ask for its data — three sequential waves per navigation. A tab that has
   * already signed in reuses the stored user, so the shell and the module start at once
   * and the identity is re-checked in the background instead of in front of the user.
   */
  async function resolveSessionUser() {
    var cached = window.CelestialSession && window.CelestialSession.read();
    if (!cached) {
      var user = await api.get("/auth/me");
      if (window.CelestialSession) window.CelestialSession.write(user);
      return user;
    }
    api.get("/auth/me").then(function (fresh) {
      if (window.CelestialSession) window.CelestialSession.write(fresh);
      // Permissions can change mid-session; the shell re-applies them without a reload.
      if (window.CelestialShell && JSON.stringify(fresh) !== JSON.stringify(cached)) {
        window.CelestialShell.refreshUser(fresh);
      }
    }).catch(function (error) {
      // 401 means the cached session is gone — `fail` sends the user to the login page.
      if (error && error.status === 401) fail(error);
    });
    return cached;
  }

  async function start() {
    var redirecting = false;
    var shell = null;
    bindRowMenus();
    try {
      try {
        currentSessionUser = await resolveSessionUser();
        if (window.CelestialShell) {
          shell = await window.CelestialShell.init(currentSessionUser);
          // The shell already rendered an access-denied screen; loading would only 403.
          if (shell && shell.allowed === false) return;
          // Mock-up values are gone and the frame is drawn — nothing left to hide.
          if (window.CelestialLoader) window.CelestialLoader.shellReady();
        }
      } catch (error) {
        redirecting = fail(error) === true;
        return;
      }
      var path = window.location.pathname.toLowerCase();
      try {
        if (path.indexOf("mediaboard") >= 0) {
          if (window.CelestialBoard) await window.CelestialBoard.initMedia(currentSessionUser);
          else await loadMedia();
        }
        else if (path.indexOf("finance") >= 0) {
          if (window.CelestialFinance) await window.CelestialFinance.init(currentSessionUser);
          else showFinanceMissing();
        }
        else if (path.indexOf("tasks") >= 0 || path.indexOf("knowledge") >= 0) {
          if (window.CelestialWorkspace) await window.CelestialWorkspace.init(currentSessionUser);
        }
        // MetaAds v2 (Structure.dc.html) — те же вкладки и окна, что в Meta Ads,
        // только «Обзор» заменён деревом из meta-tree.js.
        else if (path.indexOf("metaads") >= 0 || path.indexOf("structure") >= 0) {
          if (window.CelestialMeta) await window.CelestialMeta.init(currentSessionUser);
          if (path.indexOf("structure") >= 0 && window.CelestialMetaTree) {
            await window.CelestialMetaTree.init(currentSessionUser);
          }
        }
        else if (path.indexOf("offer") >= 0) {
          if (window.CelestialCatalog) await window.CelestialCatalog.initOffers(currentSessionUser);
          else showCatalogMissing();
        }
        else if (path.indexOf("team") >= 0) await loadTeam();
        else if (path.indexOf("utilities") >= 0) {
          if (window.CelestialUtilities) await window.CelestialUtilities.init(currentSessionUser);
        }
        else if (path.indexOf("settings") >= 0) {
          bindConnectionForm();
          bindSettingsPage();
          await loadSettings();
          // Раздел «Расчет ЗП» живёт в своём модуле и своих правах: экран
          // настроек может быть открыт человеку, которому чужие зарплаты
          // видеть незачем.
          if (window.CelestialSalary) await window.CelestialSalary.init(currentSessionUser);
        } else if (window.CelestialDashboard) {
          await window.CelestialDashboard.init(currentSessionUser);
        } else await loadDashboard();
      } catch (error) {
        redirecting = fail(error) === true;
        // Without the cold-start curtain there is no retry button on screen, so the
        // failure has to be shown where the data belongs.
        if (!redirecting && window.CelestialShell && window.CelestialShell.showLoadFailure) {
          window.CelestialShell.showLoadFailure(error && error.message);
        }
      }
      if (shell && shell.ready) await shell.ready;
    } finally {
      if (!redirecting && window.CelestialLoader) window.CelestialLoader.ready();
    }
  }

  // Back/forward can restore a page from the bfcache with the DOM exactly as it was
  // left — numbers included, and no script re-runs to refresh them. For a CRM that is
  // stale data on screen, so the restored page is reloaded instead.
  window.addEventListener("pageshow", function (event) {
    if (event.persisted) window.location.reload();
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
