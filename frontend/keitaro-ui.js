(function () {
  "use strict";

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
  var parentPickerState = {
    selectedIds: new Set(),
    editingUserId: null,
    search: ""
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
    var color = active ? "#16B57F" : "#8A8FA3";
    var background = active ? "#E4F7F0" : "#F0F1F5";
    return '<span style="display:inline-flex;align-items:center;gap:6px;color:' + color +
      ";background:" + background +
      ';border-radius:8px;padding:6px 10px;font-size:11.5px;font-weight:700">' +
      '<span style="width:6px;height:6px;border-radius:50%;background:' + color + '"></span>' +
      escapeHtml(statusLabel(status)) + "</span>";
  }

  function cell(value, extra) {
    return '<td style="padding:14px 12px;text-align:right;font-family:Space Grotesk;font-size:12px;' +
      (extra || "") + '">' + escapeHtml(value) + "</td>";
  }

  function emptyRow(columns, message) {
    return '<tr><td colspan="' + columns +
      '" style="padding:44px 24px;text-align:center;color:#A2A7B5;font-size:13px">' +
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
      "box-shadow:0 14px 38px rgba(31,34,49,.22);background:" +
      (kind === "error" ? "#D94B61" : kind === "info" ? "#5A5FE0" : "#16B57F");
    document.body.appendChild(element);
    window.setTimeout(function () { element.remove(); }, 5000);
  }

  function fail(error) {
    if (error && error.status === 401) {
      window.location.replace("/login.html?next=" + encodeURIComponent(
        window.location.pathname + window.location.search
      ));
      return;
    }
    toast(error && error.message ? error.message : "Не удалось загрузить данные", "error");
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

  async function loadPartners() {
    var page = await api.get("/partners?limit=100");
    var items = page.items || [];
    text("partnersTotal", number(page.total));
    text("partnersActive", number(items.filter(function (item) {
      return item.status === "active";
    }).length));
    text("partnersOffers", number(items.reduce(function (total, item) {
      return total + Number(item.offers_count || 0);
    }, 0)));
    text("partnersResultCount", "Показано " + items.length + " из " + page.total +
      " · синхронизировано из Keitaro");
    var body = byId("partnersTableBody");
    if (!body) return;
    body.innerHTML = items.length ? items.map(function (item) {
      return '<tr style="border-bottom:1px solid #F5F6FA">' +
        '<td style="padding:15px 24px"><div style="display:flex;align-items:center;gap:13px">' +
        '<div style="width:38px;height:38px;border-radius:11px;background:#EEF0FF;color:#5A5FE0;' +
        'display:flex;align-items:center;justify-content:center;font-weight:700;font-size:13px">' +
        escapeHtml(initials(item.name)) + '</div><div><div style="font-weight:700;font-size:14px">' +
        escapeHtml(item.name) + '</div><div style="font-size:10.5px;color:#A2A7B5;margin-top:3px">ID ' +
        escapeHtml(item.external_id) + "</div></div></div></td>" +
        '<td style="padding:15px 24px;font-weight:700">' + number(item.offers_count) + "</td>" +
        '<td style="padding:15px 24px">' + statusBadge(item.status) + "</td>" +
        '<td style="padding:15px 24px;color:#A2A7B5">•••</td></tr>';
    }).join("") : emptyRow(4, "Партнёрки появятся после синхронизации Keitaro");
  }

  async function loadOffers() {
    var page = await api.get("/offers?limit=100");
    var items = page.items || [];
    text("offersTotal", number(page.total));
    text("offersActive", number(items.filter(function (item) {
      return item.status === "active";
    }).length));
    text("offersGeos", number(new Set(items.filter(function (item) {
      return item.status === "active" && item.geo;
    }).map(function (item) { return item.geo; })).size));
    text("offersResultCount", "Показано " + items.length + " из " + page.total +
      " · синхронизировано из Keitaro");
    var body = byId("offersTableBody");
    if (!body) return;
    body.innerHTML = items.length ? items.map(function (item) {
      var buyers = (item.buyers || []).map(function (buyer) { return buyer.name; });
      return '<tr class="offer-row" data-offer-row data-geo="' + escapeHtml(item.geo || "") +
        '" data-partner="' + escapeHtml(item.partner || "") + '" data-buyers="' +
        escapeHtml(buyers.join(" ")) + '" data-status="' + escapeHtml(item.status) +
        '" style="border-bottom:1px solid #F5F6FA">' +
        '<td style="padding:15px 24px"><div style="display:flex;align-items:center;gap:13px">' +
        '<div style="width:40px;height:40px;border-radius:12px;background:#EEF0FF;color:#5A5FE0;' +
        'display:flex;align-items:center;justify-content:center;font-weight:800;font-size:12px">' +
        escapeHtml(initials(item.name)) + '</div><div><div style="font-weight:700;font-size:14px">' +
        escapeHtml(item.name) + '</div><div style="font-size:11px;color:#A2A7B5;margin-top:3px">ID ' +
        escapeHtml(item.external_id) + "</div></div></div></td>" +
        '<td style="padding:15px 18px;font-weight:700">' + escapeHtml(item.geo || "—") + "</td>" +
        '<td style="padding:15px 18px">' + escapeHtml(item.partner || "—") + "</td>" +
        '<td style="padding:15px 18px">' + escapeHtml(buyers.join(", ") || "Не назначены") + "</td>" +
        '<td style="padding:15px 18px">' + statusBadge(item.status) + "</td>" +
        '<td style="padding:15px 18px;color:#A2A7B5">•••</td></tr>';
    }).join("") : emptyRow(6, "Офферы появятся после синхронизации Keitaro");
    document.dispatchEvent(new CustomEvent("celestial:offers-loaded"));
  }

  function structure(item) {
    return '<td style="position:sticky;left:0;background:#fff;padding:12px 16px;border-right:1px solid #E7E9F1">' +
      '<div style="font-size:12.5px;font-weight:700">' + escapeHtml(item.offer || "—") + "</div>" +
      '<div style="font-size:10.5px;color:#A2A7B5;margin-top:4px">' +
      escapeHtml([item.record_date, item.buyer, item.geo, item.partner].filter(Boolean).join(" · ")) +
      "</div></td>";
  }

  async function loadMedia() {
    var page = await api.get("/media-records?limit=200");
    var body = byId("mediaTableBody");
    if (!body) return;
    body.innerHTML = (page.items || []).length ? page.items.map(function (item) {
      return '<tr style="border-bottom:1px solid #EEF0F5">' + structure(item) +
        [0, 0, 0, 0, 0, 0, 0].map(function () { return cell("—", "color:#A2A7B5;"); }).join("") +
        cell(number(item.installs)) + cell(number(item.registrations)) + cell(number(item.ftd)) +
        cell(money(item.rent)) + cell(money(item.spend)) +
        cell(money(item.revenue), "font-weight:700;") +
        cell(money(item.profit), "font-weight:700;color:" + (Number(item.profit) >= 0 ? "#16B57F;" : "#D94B61;")) +
        cell(percent(item.roi)) + cell(item.cpd == null ? "—" : money(item.cpd)) + "</tr>";
    }).join("") : emptyRow(17, "Данные появятся после первой синхронизации Keitaro");
  }

  async function loadFinance() {
    var page = await api.get("/finance-records?limit=200");
    var body = byId("financeTableBody");
    if (!body) return;
    body.innerHTML = (page.items || []).length ? page.items.map(function (item) {
      return '<tr style="border-bottom:1px solid #EEF0F5">' + structure(item) +
        [0, 0, 0, 0, 0, 0, 0].map(function () { return cell("—", "color:#A2A7B5;"); }).join("") +
        cell("—", "color:#A2A7B5;") + cell(money(item.rent)) + cell(money(item.spend)) +
        cell(money(item.revenue), "font-weight:700;") +
        cell(money(item.profit), "font-weight:700;color:" + (Number(item.profit) >= 0 ? "#16B57F;" : "#D94B61;")) +
        cell(percent(item.roi)) + cell(money(item.salary)) + "</tr>";
    }).join("") : emptyRow(15, "Финансовых записей пока нет");
  }

  async function loadTeam() {
    var canManage = currentSessionUser && currentSessionUser.role &&
      (currentSessionUser.role.permissions || []).some(function (permission) {
        return permission.code === "*" || permission.code === "team.manage";
      });
    var requests = [
      api.get("/users?limit=100"),
      api.get("/roles"),
      api.get("/campaigns?limit=100").catch(function () { return { items: [] }; }),
      api.get("/offers?limit=100").catch(function () { return { items: [] }; }),
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
      permissions: results[4] || [],
      campaignGroups: uniqueValues((results[2].items || []).map(function (item) {
        return item.group_name;
      })),
      offerGroups: uniqueValues((results[3].items || []).map(function (item) {
        return item.group_name;
      })),
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
      var groups = [user.keitaro_company_group, user.keitaro_offer_group].filter(Boolean);
      var parents = user.parents || [];
      var parentHtml = parents.length
        ? '<div style="display:flex;align-items:center;gap:6px;flex-wrap:wrap">' +
          parents.map(function (parent) {
            return '<span style="display:inline-flex;align-items:center;gap:6px;background:#F4F5F9;' +
              'border-radius:8px;padding:5px 8px;font-size:10.5px;font-weight:700">' +
              '<span class="mini-avatar" style="margin-left:0;background:#5A5FE0">' +
              escapeHtml(initials(parent.name)) + "</span>" + escapeHtml(parent.name) + "</span>";
          }).join("") + "</div>"
        : '<span style="color:#A2A7B5;font-size:12.5px">—</span>';
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
            (user.status === "active" ? "Заблокировать" : "Разблокировать") + "</button>") +
          "</div></details>"
        : "";
      return '<tr class="user-row" data-user-row data-role="' + escapeHtml(user.role.name) +
        '" data-status="' + escapeHtml(user.status) + '" data-user-id="' +
        escapeHtml(user.id) + '" style="border-bottom:1px solid #F5F6FA">' +
        '<td style="padding:14px 24px"><div style="display:flex;align-items:center;gap:12px">' +
        '<span class="avatar" style="background:linear-gradient(135deg,#5A5FE0,#8A78EF)">' +
        escapeHtml(initials(user.name)) + '</span><div><div style="font-size:13.5px;font-weight:700">' +
        escapeHtml(user.name) + '</div><div style="font-size:11px;color:#A2A7B5;margin-top:3px">@' +
        escapeHtml(user.login) + "</div></div></div></td>" +
        '<td style="padding:14px 18px;font-size:12.5px;font-weight:700">' +
        escapeHtml(user.role.name) + "</td>" +
        '<td style="padding:14px 18px">' + parentHtml + "</td>" +
        '<td style="padding:14px 18px;font-size:12px">' +
        escapeHtml(groups.join(" · ") || "Не заданы") + "</td>" +
        '<td style="padding:14px 18px">' + statusBadge(user.status) + "</td>" +
        '<td style="padding:14px 18px;color:#A2A7B5">' + actions + "</td></tr>";
    }).join("") : emptyRow(6, "Пользователей пока нет");
    renderRoles();
    renderHierarchy();
    bindTeamActions();
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
    parentPickerState.editingUserId = editingUser ? editingUser.id : null;
    renderParentPicker();
    selectOptions(form.elements.companyGroup, teamState.campaignGroups, "Без привязки");
    selectOptions(form.elements.offerGroup, teamState.offerGroups, "Без привязки");
  }

  function availableParentUsers() {
    var excludedIds = new Set();
    if (parentPickerState.editingUserId) {
      excludedIds.add(parentPickerState.editingUserId);
      var queue = [parentPickerState.editingUserId];
      while (queue.length) {
        var currentId = queue.shift();
        teamState.users.forEach(function (candidate) {
          var isChild = (candidate.parents || []).some(function (parent) {
            return parent.id === currentId;
          });
          if (isChild && !excludedIds.has(candidate.id)) {
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

  function setParentPickerSelection(ids) {
    var allowedIds = new Set(availableParentUsers().map(function (user) {
      return user.id;
    }));
    parentPickerState.selectedIds = new Set((ids || []).filter(function (id) {
      return allowedIds.has(id);
    }));
    parentPickerState.search = "";
    var search = byId("parentPickerSearch");
    if (search) search.value = "";
    renderParentPicker();
  }

  function renderParentPicker() {
    var summary = byId("parentPickerSummary");
    var options = byId("parentPickerOptions");
    var count = byId("parentPickerCount");
    if (!summary || !options) return;
    var users = availableParentUsers();
    var selectedUsers = users.filter(function (user) {
      return parentPickerState.selectedIds.has(user.id);
    });
    summary.classList.toggle("is-empty", !selectedUsers.length);
    if (!selectedUsers.length) {
      summary.innerHTML = '<span class="parent-picker-summary-text">Не выбраны — верхний уровень</span>';
    } else {
      var avatarHtml = '<span class="parent-picker-avatars">' + selectedUsers.slice(0, 3).map(
        function (user) {
          return '<span class="parent-picker-avatar">' +
            escapeHtml(initials(user.name)) + "</span>";
        }
      ).join("") + "</span>";
      var selectedText = selectedUsers.length === 1
        ? selectedUsers[0].name
        : selectedUsers[0].name + " и ещё " + (selectedUsers.length - 1);
      summary.innerHTML = avatarHtml + '<span class="parent-picker-summary-text">' +
        escapeHtml(selectedText) + "</span>";
    }
    var query = parentPickerState.search.trim().toLocaleLowerCase("ru");
    var visibleUsers = users.filter(function (user) {
      return !query || (user.name + " " + user.login + " " + user.role.name)
        .toLocaleLowerCase("ru").includes(query);
    });
    options.innerHTML = visibleUsers.length ? visibleUsers.map(function (user) {
      var selected = parentPickerState.selectedIds.has(user.id);
      return '<label class="parent-picker-option" role="option" aria-selected="' +
        (selected ? "true" : "false") + '"><input type="checkbox" data-parent-id="' +
        escapeHtml(user.id) + '"' + (selected ? " checked" : "") + ">" +
        '<span class="parent-picker-option-avatar">' + escapeHtml(initials(user.name)) +
        '</span><span class="parent-picker-option-copy"><span class="parent-picker-option-name">' +
        escapeHtml(user.name) + '</span><span class="parent-picker-option-login">@' +
        escapeHtml(user.login) + " · " + escapeHtml(user.role.name) +
        "</span></span></label>";
    }).join("") : '<div class="parent-picker-empty">' +
      (users.length ? "По вашему запросу никого не найдено" :
        "Других пользователей пока нет") + "</div>";
    if (count) count.textContent = parentSelectionLabel(selectedUsers.length);
    var clear = byId("parentPickerClear");
    if (clear) clear.style.visibility = selectedUsers.length ? "visible" : "hidden";
  }

  function setParentPickerOpen(open) {
    var picker = byId("parentPicker");
    var menu = byId("parentPickerMenu");
    var trigger = byId("parentPickerTrigger");
    if (!picker || !menu || !trigger) return;
    picker.classList.toggle("open", open);
    menu.hidden = !open;
    menu.classList.remove("open-up");
    trigger.setAttribute("aria-expanded", open ? "true" : "false");
    if (open) {
      renderParentPicker();
      window.setTimeout(function () {
        var modalCard = menu.closest(".modal-card");
        if (modalCard) {
          var menuBounds = menu.getBoundingClientRect();
          var modalBounds = modalCard.getBoundingClientRect();
          menu.classList.toggle("open-up", menuBounds.bottom > modalBounds.bottom - 10);
        }
        var search = byId("parentPickerSearch");
        if (search) search.focus();
      }, 20);
    }
  }

  function openUserEditor(user) {
    var form = byId("createUserForm");
    if (!form) return;
    form.reset();
    form.dataset.userId = user ? user.id : "";
    parentPickerState.editingUserId = user ? user.id : null;
    parentPickerState.selectedIds = new Set((user && user.parents || []).map(function (parent) {
      return parent.id;
    }));
    parentPickerState.search = "";
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
      form.elements.name.value = user.name;
      form.elements.login.value = user.login;
      form.elements.role.value = user.role.id;
      form.elements.status.value = user.status;
      form.elements.companyGroup.value = user.keitaro_company_group || "";
      form.elements.offerGroup.value = user.keitaro_offer_group || "";
    }
    setParentPickerSelection(Array.from(parentPickerState.selectedIds));
    setParentPickerOpen(false);
    byId("userModal").classList.add("open");
    document.body.style.overflow = "hidden";
  }

  function closeTeamModal(id) {
    var modal = byId(id);
    if (modal) modal.classList.remove("open");
    if (id === "userModal") setParentPickerOpen(false);
    document.body.style.overflow = "";
  }

  function permissionLabel(code) {
    var labels = {
      dashboard: "Dashboard",
      media: "Медиаборд",
      finance: "Финансы",
      partners: "Партнёрки",
      offers: "Офферы",
      team: "Команда",
      settings: "Настройки"
    };
    var parts = String(code).split(".");
    return (labels[parts[0]] || parts[0]) + " · " +
      (parts[1] === "manage" ? "изменение" :
        parts[1] === "export" ? "экспорт" : "просмотр");
  }

  function renderRoles() {
    var grid = byId("rolesGrid");
    if (!grid) return;
    grid.innerHTML = teamState.roles.length ? teamState.roles.map(function (role) {
      var usersCount = teamState.users.filter(function (user) {
        return user.role.id === role.id;
      }).length;
      var permissions = role.permissions || [];
      return '<article class="role-card"><div style="display:flex;align-items:center;' +
        'justify-content:space-between"><div style="width:42px;height:42px;border-radius:12px;' +
        'background:#EEF0FF;color:#5A5FE0;display:flex;align-items:center;justify-content:center;' +
        'font-weight:800">R</div><span style="font-size:11px;color:#A2A7B5;font-weight:700">' +
        number(usersCount) + " " + (usersCount === 1 ? "пользователь" : "пользователей") +
        '</span></div><h4 class="team-heading" style="font-size:16px;margin-top:15px">' +
        escapeHtml(role.name) + '</h4><p style="font-size:11.5px;color:#8A8FA3;line-height:1.55;' +
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
            '#E5E7EF;background:#fff;border-radius:9px;color:#5A5FE0;font-size:11.5px;' +
            'font-weight:700">Редактировать</button>'
          : "") + "</article>";
    }).join("") : '<div style="grid-column:1/-1;color:#A2A7B5">Ролей пока нет</div>';
  }

  function renderHierarchy() {
    var container = byId("hierarchyContent");
    if (!container) return;
    if (!teamState.users.length) {
      text("hierarchyLevelCount", "0 уровней");
      container.innerHTML = '<div class="hierarchy-canvas"><div class="parent-picker-empty">' +
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

    // Дерево строим по первому руководителю; остальные показываем бейджем на карточке.
    var childrenByParent = new Map();
    var roots = [];
    teamState.users.forEach(function (user) {
      var primaryParent = (user.parents || []).map(function (parent) {
        return parent.id;
      }).filter(function (parentId) {
        return usersById.has(parentId) && parentId !== user.id;
      })[0];
      if (primaryParent) {
        if (!childrenByParent.has(primaryParent)) childrenByParent.set(primaryParent, []);
        childrenByParent.get(primaryParent).push(user);
      } else {
        roots.push(user);
      }
    });
    var byName = function (left, right) {
      return left.name.localeCompare(right.name, "ru");
    };
    roots.sort(byName);
    childrenByParent.forEach(function (children) { children.sort(byName); });

    // Ветки с большим числом подчинённых сворачиваем по умолчанию,
    // чтобы дерево не растягивалось на несколько экранов по горизонтали.
    var AUTO_COLLAPSE_FROM = 7;

    function renderBranch(user, path) {
      if (path.has(user.id)) return "";
      var nextPath = new Set(path);
      nextPath.add(user.id);
      var children = childrenByParent.get(user.id) || [];
      var childHtml = children.map(function (child) {
        return renderBranch(child, nextPath);
      }).join("");
      if (!childHtml) return "<li>" + hierarchyNode(user, children.length) + "</li>";
      var collapsed = children.length >= AUTO_COLLAPSE_FROM;
      var toggle = '<button type="button" class="org-toggle" data-org-toggle ' +
        'data-count="' + children.length + '" title="Свернуть или развернуть ветку">' +
        (collapsed ? "+" + children.length : "–") + "</button>";
      return '<li class="' + (collapsed ? "org-collapsed" : "") + '">' +
        hierarchyNode(user, children.length) + toggle +
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
    var groups = uniqueValues(
      [user.keitaro_company_group, user.keitaro_offer_group].filter(Boolean)
    );
    var tagHtml = '<span class="hierarchy-tag role">' + escapeHtml(user.role.name) + "</span>" +
      groups.map(function (group) {
        return '<span class="hierarchy-tag">' + escapeHtml(group) + "</span>";
      }).join("");
    var reportLabel = childCount + " " +
      (childCount === 1 ? "подчинённый" :
        childCount >= 2 && childCount <= 4 ? "подчинённых" : "подчинённых");
    var extraHtml = extraParents
      ? '<span class="org-extra" title="' +
        escapeHtml(parents.map(function (parent) { return parent.name; }).join(", ")) +
        '">+' + extraParents + " рук.</span>"
      : "";
    var tagName = teamState.canManage ? "button" : "article";
    var action = teamState.canManage
      ? ' type="button" data-team-action="edit" data-user-id="' + escapeHtml(user.id) + '"'
      : "";
    return "<" + tagName + ' class="org-node' + (childCount ? " is-lead" : "") + '"' + action + ">" +
      '<span class="org-node-top"><span class="org-avatar">' +
      escapeHtml(initials(user.name)) + '</span><span class="org-copy">' +
      '<span class="org-name">' + escapeHtml(user.name) + "</span>" +
      '<span class="org-login">@' + escapeHtml(user.login) +
      '</span></span><span class="hierarchy-status ' +
      (user.status === "active" ? "" : "blocked") + '" title="' +
      (user.status === "active" ? "Активен" : "Заблокирован") + '"></span></span>' +
      '<span class="org-tags">' + tagHtml + "</span>" +
      '<span class="org-foot"><span>' + reportLabel + "</span>" + extraHtml +
      "</span></" + tagName + ">";
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
    var selected = new Set((role && role.permissions || []).map(function (permission) {
      return permission.code;
    }));
    byId("rolePermissions").innerHTML = teamState.permissions.map(function (code) {
      return '<label style="display:flex;align-items:center;gap:9px;border:1px solid #E7E9F1;' +
        'border-radius:10px;padding:10px 11px;font-size:11.5px;font-weight:700;color:#555B6D">' +
        '<input type="checkbox" name="permission" value="' + escapeHtml(code) + '"' +
        (selected.has(code) ? " checked" : "") + "> " +
        escapeHtml(permissionLabel(code)) + "</label>";
    }).join("");
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
      '<p style="font-size:12px;color:#8A8FA3;margin-top:7px">Покажите его пользователю один раз.</p>' +
      '<input id="temporaryPasswordValue" class="form-input" readonly value="' +
      escapeHtml(password) + '" style="margin-top:18px;font-family:Space Grotesk">' +
      '<div style="display:flex;justify-content:flex-end;gap:9px;margin-top:17px">' +
      '<button type="button" data-team-action="close-secret" style="height:40px;border:1px solid #E1E4ED;' +
      'background:#fff;border-radius:9px;padding:0 14px">Закрыть</button>' +
      '<button type="button" data-team-action="copy-secret" style="height:40px;border:0;background:#5A5FE0;' +
      'color:#fff;border-radius:9px;padding:0 14px;font-weight:700">Копировать</button>' +
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
      var parentTrigger = event.target.closest("#parentPickerTrigger");
      var parentClear = event.target.closest("#parentPickerClear");
      if (parentTrigger) {
        event.preventDefault();
        setParentPickerOpen(!byId("parentPicker").classList.contains("open"));
        return;
      }
      if (parentClear) {
        event.preventDefault();
        setParentPickerSelection([]);
        return;
      }
      if (!event.target.closest("#parentPicker")) {
        setParentPickerOpen(false);
      }
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
      } else if (name === "edit-role") {
        openRoleEditor(teamState.roles.find(function (role) {
          return role.id === action.dataset.roleId;
        }));
      } else if (name === "toggle-status") {
        var user = teamState.users.find(function (item) {
          return item.id === action.dataset.userId;
        });
        if (!user || !window.confirm(
          user.status === "active" ? "Заблокировать пользователя?" : "Разблокировать пользователя?"
        )) return;
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
      } else if (name === "reset-password") {
        if (!window.confirm("Сбросить пароль пользователя? Текущий пароль перестанет работать.")) {
          return;
        }
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
      if (event.target.id !== "parentPickerSearch") return;
      parentPickerState.search = event.target.value;
      renderParentPicker();
    });
    document.addEventListener("change", function (event) {
      var parentCheckbox = event.target.closest("[data-parent-id]");
      if (!parentCheckbox) return;
      if (parentCheckbox.checked) {
        parentPickerState.selectedIds.add(parentCheckbox.dataset.parentId);
      } else {
        parentPickerState.selectedIds.delete(parentCheckbox.dataset.parentId);
      }
      renderParentPicker();
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && byId("parentPicker") &&
          byId("parentPicker").classList.contains("open")) {
        setParentPickerOpen(false);
      }
    });
    var userForm = byId("createUserForm");
    if (userForm) {
      userForm.addEventListener("submit", saveUser, true);
    }
    var roleForm = byId("roleForm");
    if (roleForm) {
      roleForm.addEventListener("submit", saveRole, true);
    }
  }

  async function saveUser(event) {
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
    var form = event.currentTarget;
    var userId = form.dataset.userId;
    var submit = byId("saveUserButton");
    var payload = {
      name: form.elements.name.value.trim(),
      login: form.elements.login.value.trim().replace(/^@/, ""),
      role_id: form.elements.role.value,
      status: form.elements.status.value,
      parent_ids: Array.from(parentPickerState.selectedIds),
      keitaro_company_group: form.elements.companyGroup.value || null,
      keitaro_offer_group: form.elements.offerGroup.value || null
    };
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
    var payload = {
      name: form.elements.roleName.value.trim(),
      description: form.elements.roleDescription.value.trim(),
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
    var isService = kind === "service";
    var typeCells = isService
      ? '<td style="padding:14px 18px;font-size:13px;font-weight:700">' +
        escapeHtml(money(item.install_cost)) + "</td>" +
        '<td style="padding:14px 18px;font-size:13px;font-weight:700">' +
        escapeHtml(decimal(item.commission_pct, 1)) + "%</td>"
      : '<td style="padding:14px 18px"><span class="data-pill">' +
        (item.provider_type === "agent" ? "Агент" : "Платёжка") + "</span></td>" +
        '<td style="padding:14px 18px;font-size:13px;font-weight:700">' +
        escapeHtml(decimal(item.commission_pct, 1)) + "%</td>";
    var actions = settingsCanManage()
      ? settingActionMenu(item, kind)
      : '<span style="color:#C7CAD6">•••</span>';
    return '<tr class="settings-row" data-settings-row data-kind="' +
      (isService ? "services" : "agents") + '" data-status="' + escapeHtml(item.status) + '"' +
      (isService ? "" : ' data-type="' + escapeHtml(item.provider_type) + '"') +
      ' style="border-bottom:1px solid #F5F6FA">' +
      '<td style="padding:14px 24px"><div style="display:flex;align-items:center;gap:12px">' +
      '<span class="entity-icon" style="background:#EEF0FF;color:#5A5FE0;font-size:11px;font-weight:800">' +
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
      '<circle cx="5" cy="12" r="1.6" fill="#A2A7B5"/><circle cx="12" cy="12" r="1.6" fill="#A2A7B5"/>' +
      '<circle cx="19" cy="12" r="1.6" fill="#A2A7B5"/></svg></summary><div class="row-menu">' +
      button("edit", "Редактировать") + button("toggle", toggleLabel) + button("delete", "Удалить") +
      "</div></details>";
  }

  function formatDate(value) {
    if (!value) return "Ещё не запускалась";
    return new Intl.DateTimeFormat("ru-RU", {
      dateStyle: "short",
      timeStyle: "short"
    }).format(new Date(value));
  }

  function renderIntegrations(overview) {
    var panel = byId("integrationsPanel");
    if (!panel) return;
    var grid = panel.querySelector(".integration-grid");
    if (!grid) return;
    var connections = overview.connections || [];
    grid.innerHTML = connections.length ? connections.map(function (connection) {
      var run = connection.last_run;
      var failed = run && run.status === "failed";
      var statusText = failed ? "Ошибка" : connection.status === "active" ? "Подключено" : "Отключено";
      var statusColor = failed ? "#D94B61" : "#16B57F";
      return '<article class="integration-card" data-connection-id="' + escapeHtml(connection.id) + '">' +
        '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:14px"><div style="display:flex;align-items:center;gap:12px">' +
        '<div style="width:48px;height:48px;border-radius:14px;background:linear-gradient(135deg,#5A5FE0,#7D67ED);' +
        'display:flex;align-items:center;justify-content:center;color:#fff;font-size:18px;font-weight:800">K</div>' +
        '<div><div class="settings-heading" style="font-size:16px;font-weight:700">' +
        escapeHtml(connection.name) + '</div><div style="font-size:10.5px;color:#A2A7B5;margin-top:3px">' +
        escapeHtml(connection.base_url) + '</div></div></div><span style="color:' + statusColor +
        ';font-size:10.5px;font-weight:700">' + statusText + "</span></div>" +
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:19px">' +
        '<div style="background:#F7F8FB;border-radius:11px;padding:11px"><div style="font-size:10px;color:#A2A7B5;font-weight:700">ПОСЛЕДНЯЯ СИНХРОНИЗАЦИЯ</div>' +
        '<div style="font-size:12px;font-weight:700;margin-top:5px">' +
        escapeHtml(formatDate(connection.last_sync_at)) + "</div></div>" +
        '<div style="background:#F7F8FB;border-radius:11px;padding:11px"><div style="font-size:10px;color:#A2A7B5;font-weight:700">РАСПИСАНИЕ</div>' +
        '<div style="font-size:12px;font-weight:700;margin-top:5px">Каждые ' +
        escapeHtml(connection.sync_interval_minutes) + " мин.</div></div></div>" +
        '<div style="margin-top:14px;font-size:11px;color:#8A8FA3">Офферы, партнёрки, кампании, клики, лиды, продажи, расходы и Revenue</div>' +
        '<button class="live-sync-connection" type="button" data-connection-id="' +
        escapeHtml(connection.id) +
        '" style="width:100%;height:39px;margin-top:16px;border:0;border-radius:10px;background:#5A5FE0;color:#fff;font-size:11.5px;font-weight:700">Синхронизировать</button></article>';
    }).join("") : '<div style="grid-column:1/-1;padding:36px;border:1px dashed #D9DCE8;border-radius:14px;text-align:center;color:#8A8FA3">' +
      'Подключений пока нет. Нажмите «Добавить подключение» и укажите URL вашего Keitaro-трекера.</div>';
  }

  var settingsState = { activeTab: "services", services: [], providers: [] };

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
    if (settingsState.activeTab === "integrations" || !settingsCanManage()) {
      button.style.display = "none";
      return;
    }
    button.style.display = "flex";
    var label = byId("entityButtonLabel");
    if (label) {
      label.textContent = settingsState.activeTab === "services" ? "Добавить сервис" : "Добавить агента";
    }
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
    var status = byId("serviceStatusFilter") ? byId("serviceStatusFilter").value : "";
    var type = byId("agentTypeFilter") ? byId("agentTypeFilter").value : "";
    document.querySelectorAll("[data-settings-row]").forEach(function (row) {
      var current = row.dataset.kind === settingsState.activeTab;
      var matches = current &&
        (!query || row.textContent.toLowerCase().indexOf(query) >= 0) &&
        (settingsState.activeTab !== "services" || !status || row.dataset.status === status) &&
        (settingsState.activeTab !== "agents" || !type || row.dataset.type === type);
      row.style.display = matches ? "" : "none";
    });
  }

  function findSetting(kind, id) {
    var list = kind === "service" ? settingsState.services : settingsState.providers;
    return list.find(function (item) { return String(item.id) === String(id); });
  }

  function openEntityEditor(item, kind) {
    var form = byId("entityForm");
    if (!form) return;
    var isService = kind === "service";
    form.dataset.editKind = kind;
    form.dataset.editId = item ? item.id : "";
    text("entityModalTitle", item
      ? (isService ? "Редактирование сервиса" : "Редактирование записи")
      : (isService ? "Новый сервис" : "Новый агент или платёжка"));
    text("entityModalSubtitle", isService
      ? "Стоимость инсталла и комиссия для расчётов Медиаборда"
      : "Комиссия агента или платёжной системы для SPEND");
    byId("entityTypeField").style.display = isService ? "none" : "";
    byId("installCostField").style.display = isService ? "" : "none";
    form.elements.entityName.value = item ? item.name : "";
    form.elements.commission.value = item ? Number(item.commission_pct) : 0;
    if (isService) {
      form.elements.installCost.value = item ? Number(item.install_cost) : 0.03;
    } else {
      form.elements.entityType.value = item && item.provider_type === "payment"
        ? "Платёжка" : "Агент";
    }
    form.elements.entityStatus.value = item && item.status !== "active" ? "Неактивен" : "Активен";
    setSettingsModal("entityModal", true);
    window.setTimeout(function () {
      var input = form.querySelector("input");
      if (input) input.focus();
    }, 50);
  }

  async function submitEntity() {
    var form = byId("entityForm");
    var kind = form.dataset.editKind ||
      (settingsState.activeTab === "agents" ? "provider" : "service");
    var id = form.dataset.editId;
    var name = form.elements.entityName.value.trim();
    if (!name) return;
    var status = form.elements.entityStatus.value === "Неактивен" ? "inactive" : "active";
    var commission = Number(form.elements.commission.value || 0);
    var submit = form.querySelector('[type="submit"]');
    submit.disabled = true;
    submit.textContent = "Сохраняю…";
    try {
      if (kind === "service") {
        var servicePayload = {
          name: name,
          install_cost: Number(form.elements.installCost.value || 0),
          commission_pct: commission,
          status: status
        };
        if (id) await api.put("/services/" + id, servicePayload);
        else await api.post("/services", servicePayload);
      } else {
        var providerPayload = {
          name: name,
          provider_type: form.elements.entityType.value === "Платёжка" ? "payment" : "agent",
          commission_pct: commission,
          status: status
        };
        if (id) await api.put("/spend-providers/" + id, providerPayload);
        else await api.post("/spend-providers", providerPayload);
      }
      toast(id ? "Изменения сохранены" : "Запись добавлена");
      closeEntityModal();
      await loadSettings();
    } finally {
      submit.disabled = false;
      submit.textContent = "Сохранить";
    }
  }

  async function toggleSetting(kind, id) {
    var item = findSetting(kind, id);
    if (!item) return;
    var status = item.status === "active" ? "inactive" : "active";
    if (kind === "service") {
      await api.put("/services/" + id, {
        name: item.name,
        install_cost: Number(item.install_cost),
        commission_pct: Number(item.commission_pct),
        status: status
      });
    } else {
      await api.put("/spend-providers/" + id, {
        name: item.name,
        provider_type: item.provider_type,
        commission_pct: Number(item.commission_pct),
        status: status
      });
    }
    toast(status === "active" ? "Активировано" : "Деактивировано");
    await loadSettings();
  }

  async function deleteSetting(kind, id) {
    var item = findSetting(kind, id);
    if (!item) return;
    if (!window.confirm("Удалить «" + item.name + "»? Действие необратимо.")) return;
    if (kind === "service") await api.delete("/services/" + id);
    else await api.delete("/spend-providers/" + id);
    toast("Удалено");
    await loadSettings();
  }

  function bindSettingsPage() {
    if (document.documentElement.dataset.settingsBound) return;
    document.documentElement.dataset.settingsBound = "true";
    document.querySelectorAll("[data-settings-tab]").forEach(function (tab) {
      tab.addEventListener("click", function () {
        switchSettingsTab(tab.dataset.settingsTab);
      });
    });
    var addButton = byId("openEntityModal");
    if (addButton) addButton.addEventListener("click", function () {
      openEntityEditor(null, settingsState.activeTab === "agents" ? "provider" : "service");
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
      setSettingsModal("connectionModal", true);
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
    ["serviceStatusFilter", "agentTypeFilter"].forEach(function (id) {
      var element = byId(id);
      if (element) element.addEventListener("change", applySettingsFilters);
    });
    document.addEventListener("click", function (event) {
      var action = event.target.closest("[data-setting-action]");
      if (!action) return;
      var menu = action.closest("details");
      if (menu) menu.removeAttribute("open");
      var kind = action.getAttribute("data-kind");
      var id = action.getAttribute("data-id");
      var op = action.getAttribute("data-setting-action");
      if (op === "edit") openEntityEditor(findSetting(kind, id), kind);
      else if (op === "toggle") toggleSetting(kind, id).catch(fail);
      else if (op === "delete") deleteSetting(kind, id).catch(fail);
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
    var results = await Promise.all([
      api.get("/services?limit=100"),
      api.get("/spend-providers?limit=100"),
      api.get("/integrations/keitaro/overview")
    ]);
    var services = results[0];
    var providers = results[1];
    var overview = results[2];
    settingsState.services = services.items || [];
    settingsState.providers = providers.items || [];
    text("settingsServicesTotal", number((services.items || []).filter(function (item) {
      return item.status === "active";
    }).length));
    text("settingsProvidersTotal", number((providers.items || []).length));
    text("settingsConnectionsTotal", number((overview.connections || []).length));
    text("settingsServicesTabCount", number(services.total));
    text("settingsProvidersTabCount", number(providers.total));
    text("settingsConnectionsTabCount", number((overview.connections || []).length));
    text("settingsServicesResultCount", "Показано " + (services.items || []).length +
      " из " + services.total + " сервисов");
    text("settingsProvidersResultCount", "Показано " + (providers.items || []).length +
      " из " + providers.total + " записей");
    text("settingsSyncSummary", overview.configured
      ? "Подключений: " + (overview.connections || []).length +
        " · кампаний: " + number(overview.counts.campaigns) +
        " · строк статистики: " + number(overview.counts.stat_rows)
      : "Синхронизации ещё не запускались");
    var servicesBody = byId("servicesTableBody");
    var providersBody = byId("providersTableBody");
    if (servicesBody) {
      servicesBody.innerHTML = (services.items || []).length
        ? services.items.map(function (item) { return settingRow(item, "service"); }).join("")
        : emptyRow(5, "Сервисы пока не добавлены");
    }
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

  async function bindSyncButton(id) {
    var button = byId(id);
    if (!button) return;
    button.addEventListener("click", async function (event) {
      event.preventDefault();
      event.stopImmediatePropagation();
      try {
        var connection = await currentConnection();
        await runSync(connection && connection.id, "incremental", button);
      } catch (error) {
        fail(error);
      }
    }, true);
  }

  function bindSettingsActions() {
    document.querySelectorAll(".live-sync-connection").forEach(function (button) {
      if (button.dataset.bound) return;
      button.dataset.bound = "true";
      button.addEventListener("click", function () {
        runSync(button.dataset.connectionId, "incremental", button);
      });
    });
  }

  function bindConnectionForm() {
    var form = byId("connectionForm");
    if (!form || form.dataset.liveBound) return;
    form.dataset.liveBound = "true";
    form.addEventListener("submit", async function (event) {
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      var fields = form.querySelectorAll("input, select");
      var intervalMap = {
        "Каждые 15 минут": 15,
        "Каждый час": 60,
        "Раз в сутки": 1440,
        "Только вручную": 1440
      };
      var submit = form.querySelector('[type="submit"]');
      submit.disabled = true;
      submit.textContent = "Проверяю…";
      try {
        var connection = await api.post("/integrations/keitaro", {
          name: fields[0].value.trim(),
          base_url: fields[1].value.trim(),
          api_key: fields[2].value,
          sync_interval_minutes: intervalMap[fields[3].value] || 15,
          timezone: "Asia/Qyzylorda",
          buyer_sub_id: 1,
          lookback_days: 2
        });
        var modal = byId("connectionModal");
        if (modal) modal.classList.remove("open");
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
        submit.textContent = "Проверить и подключить";
      }
    }, true);
  }

  async function reloadCurrentPage() {
    var path = window.location.pathname.toLowerCase();
    if (path.indexOf("partner") >= 0) {
      return window.CelestialCatalog ? window.CelestialCatalog.reloadPartners() : loadPartners();
    }
    if (path.indexOf("offer") >= 0) {
      return window.CelestialCatalog ? window.CelestialCatalog.reloadOffers() : loadOffers();
    }
    if (path.indexOf("mediaboard") >= 0) {
      if (window.CelestialBoard) return window.CelestialBoard.initMedia(currentSessionUser);
      return loadMedia();
    }
    if (path.indexOf("settings") >= 0) return loadSettings();
    return loadDashboard();
  }

  async function start() {
    try {
      currentSessionUser = await api.get("/auth/me");
      if (window.CelestialShell) await window.CelestialShell.init(currentSessionUser);
    } catch (error) {
      fail(error);
      return;
    }
    var path = window.location.pathname.toLowerCase();
    try {
      if (path.indexOf("mediaboard") >= 0) {
        if (window.CelestialBoard) await window.CelestialBoard.initMedia(currentSessionUser);
        else await loadMedia();
      }
      else if (path.indexOf("finance") >= 0) {
        if (window.CelestialBoard) await window.CelestialBoard.initFinance(currentSessionUser);
        else await loadFinance();
      }
      else if (path.indexOf("partner") >= 0) {
        if (window.CelestialCatalog) await window.CelestialCatalog.initPartners(currentSessionUser);
        else await loadPartners();
      }
      else if (path.indexOf("offer") >= 0) {
        if (window.CelestialCatalog) await window.CelestialCatalog.initOffers(currentSessionUser);
        else await loadOffers();
      }
      else if (path.indexOf("team") >= 0) await loadTeam();
      else if (path.indexOf("settings") >= 0) {
        bindConnectionForm();
        bindSettingsPage();
        await loadSettings();
      } else if (window.CelestialDashboard) {
        await window.CelestialDashboard.init(currentSessionUser);
      } else await loadDashboard();
    } catch (error) {
      fail(error);
    }
    bindSyncButton("syncPartners");
    bindSyncButton("syncOffers");
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
