(function () {
  "use strict";

  var api = window.CelestialAPI;
  var shellState = {
    user: null,
    allowed: true,
    syncCard: null,
    syncStatus: null,
    syncTimer: null,
    syncBusy: false
  };

  var pages = {
    "Dashboard": "/",
    "Медиаборд": "/Mediaboard.dc.html",
    "Финансы": "/Finance.dc.html",
    "Партнёрки": "/Partners.dc.html",
    "Оффера": "/Offers.dc.html",
    "Команда": "/Team.dc.html",
    "Настройки": "/Settings.dc.html"
  };

  // Present in the sidebar markup but not implemented yet. Marked as such instead of
  // silently swallowing the click.
  var upcoming = {
    "Meta Ads": "Модуль Meta Ads ещё не реализован",
    "Workspace": "Модуль Workspace ещё не реализован",
    "Утилиты": "Модуль «Утилиты» ещё не реализован",
    "Логи": "Модуль «Логи» ещё не реализован"
  };

  // The .dc.html files double as design mock-ups, so they ship with demo rows and demo
  // numbers baked into the markup. Every container below is owned by JS: it is blanked
  // before the first request so a failed or forbidden load can never leave fake people,
  // services or KPIs on screen.
  var pageRules = [
    {
      id: "mediaboard",
      match: "mediaboard",
      permission: "media.view",
      title: "Медиаборд",
      containers: ["mediaTableHead", "mediaTableBody"],
      values: ["mediaResultCount"]
    },
    {
      id: "finance",
      match: "finance",
      permission: "finance.view",
      title: "Финансы",
      containers: ["financeTableHead", "financeTableBody"],
      values: ["financeResultCount", "financeKpiRevenue", "financeKpiCosts",
        "financeKpiProfit", "financeKpiRoi"]
    },
    {
      id: "partners",
      match: "partner",
      permission: "partners.view",
      title: "Партнёрки",
      containers: ["partnersTableBody", "partnersPagination"],
      values: ["partnersTotal", "partnersActive", "partnersOffers", "partnersResultCount"]
    },
    {
      id: "offers",
      match: "offer",
      permission: "offers.view",
      title: "Оффера",
      containers: ["offersTableBody", "offersPagination"],
      values: ["offersTotal", "offersActive", "offersGeos", "offersResultCount"]
    },
    {
      id: "team",
      match: "team",
      permission: "team.view",
      title: "Команда",
      containers: ["teamTableBody", "rolesGrid", "hierarchyContent", "teamPagination"],
      values: ["teamTotal", "teamActive", "teamRoles", "teamSidebarTotal",
        "teamUsersTabCount", "teamRolesTabCount", "usersResultCount", "hierarchyLevelCount"]
    },
    {
      id: "settings",
      match: "settings",
      permission: "settings.view",
      title: "Настройки",
      containers: ["servicesTableBody", "providersTableBody", "integrationsPanel"],
      values: ["settingsServicesTotal", "settingsProvidersTotal", "settingsConnectionsTotal",
        "settingsServicesTabCount", "settingsProvidersTabCount", "settingsConnectionsTabCount",
        "settingsServicesResultCount", "settingsProvidersResultCount", "settingsSyncSummary"]
    },
    {
      id: "dashboard",
      match: "",
      permission: "dashboard.view",
      title: "Dashboard",
      containers: ["dashboardWorkingOffers", "dashboardChartSvg", "dashboardChartLabels"],
      values: ["dashboardRevenue", "dashboardSpend", "dashboardProfit", "dashboardRoi",
        "dashboardLeads", "dashboardSales", "dashboardEpl", "dashboardOffersCount",
        "dashboardRevenueDelta", "dashboardSpendDelta", "dashboardProfitDelta",
        "dashboardRoiDelta", "dashboardLeadsDelta", "dashboardSalesDelta", "dashboardEplDelta"]
    }
  ];

  function currentRule() {
    var path = window.location.pathname.toLowerCase();
    return pageRules.find(function (rule) {
      return rule.match && path.indexOf(rule.match) >= 0;
    }) || pageRules[pageRules.length - 1];
  }

  function columnCount(element) {
    var table = element.closest ? element.closest("table") : null;
    var header = table ? table.querySelector("thead tr") : null;
    return header ? Math.max(header.children.length, 1) : 1;
  }

  function clearMockData(rule) {
    rule.containers.forEach(function (id) {
      var element = document.getElementById(id);
      if (!element) return;
      if (element.tagName === "TBODY") {
        element.innerHTML = '<tr><td colspan="' + columnCount(element) +
          '" style="padding:44px 24px;text-align:center;color:#A2A7B5;font-size:13px">' +
          "Загрузка данных…</td></tr>";
      } else {
        element.innerHTML = "";
      }
    });
    rule.values.forEach(function (id) {
      var element = document.getElementById(id);
      if (element) element.textContent = "—";
    });
  }

  function renderAccessDenied(rule) {
    var host = document.querySelector("main") || document.body;
    host.innerHTML =
      '<section role="alert" style="max-width:520px;margin:96px auto;padding:32px;text-align:center;' +
      "background:#fff;border:1px solid #E5E7EF;border-radius:18px;font-family:'Manrope',sans-serif\">" +
      '<div style="width:52px;height:52px;margin:0 auto 18px;border-radius:15px;background:#FDECEF;' +
      'display:flex;align-items:center;justify-content:center">' +
      '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
      '<rect x="4" y="10" width="16" height="10" rx="2.5" stroke="#D94B61" stroke-width="2"/>' +
      '<path d="M8 10V7a4 4 0 0 1 8 0v3" stroke="#D94B61" stroke-width="2" stroke-linecap="round"/></svg></div>' +
      '<h1 style="font:700 20px \'Space Grotesk\',Manrope,sans-serif;color:#171A26">Раздел недоступен</h1>' +
      '<p style="margin-top:10px;font-size:13px;color:#6B7180;line-height:1.55">У вашей роли нет прав на раздел «' +
      escapeHtml(rule.title) + '». Обратитесь к администратору, если доступ нужен для работы.</p>' +
      '<a href="/" style="display:inline-block;margin-top:20px;height:40px;line-height:40px;padding:0 20px;' +
      "background:#5A5FE0;color:#fff;border-radius:10px;font:700 12.5px Manrope,sans-serif;text-decoration:none\">" +
      "На дашборд</a></section>";
  }

  function applyNavPermissions(user) {
    document.querySelectorAll("nav a, nav > div").forEach(function (element) {
      var name = pageName(element);
      if (!name) return;
      var rule = pageRules.find(function (item) { return item.title === name; });
      if (!rule || hasPermission(user, rule.permission)) return;
      element.style.display = "none";
    });
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
    return String(value || "?").trim().split(/\s+/).slice(0, 2)
      .map(function (part) { return part.charAt(0).toUpperCase(); }).join("") || "?";
  }

  function hasPermission(user, code) {
    if (!user || !user.role) return false;
    return (user.role.permissions || []).some(function (permission) {
      return permission.code === "*" || permission.code === code;
    });
  }

  function injectShellStyles() {
    if (document.getElementById("celestialShellStyles")) return;
    var style = document.createElement("style");
    style.id = "celestialShellStyles";
    style.textContent =
      "#celestialAccountButton{position:relative;cursor:pointer;user-select:none;outline:none;transition:background .16s ease}" +
      "#celestialAccountButton:hover,#celestialAccountButton:focus-visible{background:#F7F8FC}" +
      "#celestialAccountButton[aria-expanded=true]{background:#F4F5FA}" +
      "#celestialAccountMenu{position:absolute;left:14px;right:14px;bottom:calc(100% + 9px);z-index:10020;" +
      "background:#fff;border:1px solid #E5E7EF;border-radius:13px;padding:6px;box-shadow:0 16px 42px rgba(31,34,49,.18)}" +
      "#celestialAccountMenu[hidden]{display:none}" +
      ".celestial-account-action{display:block;width:100%;border:0;background:transparent;border-radius:9px;" +
      "padding:10px 11px;text-align:left;font:600 12.5px Manrope,sans-serif;color:#383D4D;cursor:pointer}" +
      ".celestial-account-action:hover,.celestial-account-action:focus-visible{background:#F4F5FA;outline:none}" +
      ".celestial-account-action[data-shell-action=logout]{color:#D94B61}" +
      "#celestialProfileOverlay{position:fixed;inset:0;z-index:10030;background:rgba(23,26,38,.42);" +
      "display:flex;align-items:center;justify-content:center;padding:24px}" +
      "#celestialProfileDialog{width:min(430px,100%);background:#fff;border:1px solid #E5E7EF;border-radius:18px;" +
      "box-shadow:0 24px 70px rgba(23,26,38,.25);padding:24px}" +
      ".celestial-profile-row{display:flex;justify-content:space-between;gap:24px;padding:11px 0;border-bottom:1px solid #F0F1F6}" +
      ".celestial-profile-label{font-size:12px;color:#8A8FA3;font-weight:600}" +
      ".celestial-profile-value{font-size:12.5px;color:#282C3A;font-weight:700;text-align:right;overflow-wrap:anywhere}" +
      "#celestialSyncCard{transition:transform .16s ease,box-shadow .16s ease;outline:none}" +
      "#celestialSyncCard[data-actionable=true]{cursor:pointer}" +
      "#celestialSyncCard[data-actionable=true]:hover,#celestialSyncCard[data-actionable=true]:focus-visible{" +
      "transform:translateY(-1px);box-shadow:0 10px 24px rgba(90,95,224,.25)}" +
      ".celestial-upcoming-module{cursor:not-allowed!important;color:#9095A6!important}" +
      ".celestial-upcoming-badge{display:inline-flex;align-items:center;justify-content:center;margin-left:auto;" +
      "border:1px solid #DDE0FF;border-radius:999px;background:#F0F1FF;color:#666BE5;padding:2px 6px;" +
      "font:800 8px/1.25 Manrope,sans-serif;letter-spacing:.045em;white-space:nowrap}" +
      "@media(max-width:760px){#celestialAccountMenu{position:fixed;left:14px;right:14px;bottom:76px}}";
    document.head.appendChild(style);
  }

  function matchName(element, names) {
    var text = (element.textContent || "").replace(/\s+/g, " ").trim();
    return names.find(function (name) {
      return text === name || text.indexOf(name + " ") === 0;
    });
  }

  function pageName(element) {
    return matchName(element, Object.keys(pages));
  }

  function markUpcoming(element, name) {
    if (element.classList.contains("celestial-upcoming-module")) return;
    element.classList.add("celestial-upcoming-module");
    element.setAttribute("aria-disabled", "true");
    element.title = upcoming[name] + " — раздел появится позже";
    var badge = document.createElement("span");
    badge.className = "celestial-upcoming-badge";
    badge.setAttribute("aria-hidden", "true");
    badge.textContent = "СКОРО";
    element.appendChild(badge);
    element.addEventListener("click", function (event) {
      event.preventDefault();
      event.stopPropagation();
      toast(upcoming[name], "info");
    });
  }

  function setupNavigation() {
    var candidates = document.querySelectorAll("nav a, nav > div");

    candidates.forEach(function (element) {
      var name = pageName(element);
      if (!name) {
        var pending = matchName(element, Object.keys(upcoming));
        if (pending) markUpcoming(element, pending);
        return;
      }

      var target = pages[name];
      if (element.tagName === "A") {
        element.setAttribute("href", target);
        return;
      }

      element.setAttribute("role", "link");
      element.setAttribute("tabindex", "0");
      element.style.cursor = "pointer";
      element.addEventListener("click", function () {
        window.location.href = target;
      });
      element.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          window.location.href = target;
        }
      });
    });
  }

  function toast(message, kind) {
    var current = document.getElementById("celestialShellToast");
    if (current) current.remove();
    var element = document.createElement("div");
    element.id = "celestialShellToast";
    element.textContent = message;
    element.style.cssText =
      "position:fixed;right:24px;bottom:24px;z-index:10050;max-width:390px;padding:13px 17px;" +
      "border-radius:11px;color:#fff;font:700 12px Manrope,sans-serif;box-shadow:0 14px 38px rgba(31,34,49,.22);" +
      "background:" + (kind === "error" ? "#D94B61" : kind === "info" ? "#5A5FE0" : "#16B57F");
    document.body.appendChild(element);
    window.setTimeout(function () { element.remove(); }, 5000);
  }

  function setupAccount(user) {
    var aside = document.querySelector("aside");
    if (!aside || !aside.lastElementChild) return;
    var footer = aside.lastElementChild;
    if (footer.id === "celestialAccountButton") return;
    footer.id = "celestialAccountButton";
    footer.setAttribute("role", "button");
    footer.setAttribute("tabindex", "0");
    footer.setAttribute("aria-haspopup", "menu");
    footer.setAttribute("aria-expanded", "false");

    var children = footer.children;
    var avatar = children[0];
    var identity = children[1];
    var chevron = children[children.length - 1];
    if (avatar) avatar.textContent = initials(user.name || user.login);
    if (identity && identity.children[0]) identity.children[0].textContent = user.name || user.login;
    if (identity && identity.children[1]) {
      identity.children[1].textContent = user.role ? user.role.name : "";
    }
    if (chevron) chevron.style.transition = "transform .16s ease";

    var menu = document.createElement("div");
    menu.id = "celestialAccountMenu";
    menu.setAttribute("role", "menu");
    menu.hidden = true;
    menu.innerHTML =
      '<button class="celestial-account-action" type="button" role="menuitem" data-shell-action="profile">Мой профиль</button>' +
      (hasPermission(user, "settings.view")
        ? '<button class="celestial-account-action" type="button" role="menuitem" data-shell-action="settings">Настройки</button>'
        : "") +
      '<button class="celestial-account-action" type="button" role="menuitem" data-shell-action="logout">Выйти</button>';
    footer.appendChild(menu);

    function setOpen(open) {
      menu.hidden = !open;
      footer.setAttribute("aria-expanded", String(open));
      if (chevron) chevron.style.transform = open ? "rotate(180deg)" : "";
      if (open) {
        var first = menu.querySelector("button");
        if (first) first.focus();
      }
    }

    function toggleAccount(event) {
      if (event.target.closest("[data-shell-action]")) return;
      setOpen(menu.hidden);
    }
    footer.addEventListener("click", toggleAccount);
    footer.addEventListener("keydown", function (event) {
      if ((event.key === "Enter" || event.key === " ") && event.target === footer) {
        event.preventDefault();
        setOpen(menu.hidden);
      }
      if (event.key === "Escape") {
        setOpen(false);
        footer.focus();
      }
    });
    document.addEventListener("click", function (event) {
      if (!footer.contains(event.target)) setOpen(false);
    });
    menu.addEventListener("click", function (event) {
      var action = event.target.closest("[data-shell-action]");
      if (!action) return;
      event.stopPropagation();
      setOpen(false);
      if (action.dataset.shellAction === "profile") openProfile(user);
      if (action.dataset.shellAction === "settings") window.location.href = "/Settings.dc.html";
      if (action.dataset.shellAction === "logout") logout();
    });
  }

  function openProfile(user) {
    var existing = document.getElementById("celestialProfileOverlay");
    if (existing) existing.remove();
    var overlay = document.createElement("div");
    overlay.id = "celestialProfileOverlay";
    overlay.innerHTML =
      '<section id="celestialProfileDialog" role="dialog" aria-modal="true" aria-labelledby="celestialProfileTitle">' +
      '<div style="display:flex;align-items:center;gap:13px;margin-bottom:18px">' +
      '<div style="width:44px;height:44px;border-radius:12px;background:linear-gradient(135deg,#5A5FE0,#8E8BF0);' +
      'display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:15px">' +
      escapeHtml(initials(user.name || user.login)) + "</div>" +
      '<div><h2 id="celestialProfileTitle" style="font:700 19px Space Grotesk,Manrope,sans-serif;color:#171A26">' +
      escapeHtml(user.name || user.login) + '</h2><div style="font-size:12px;color:#8A8FA3;margin-top:3px">Профиль пользователя</div></div></div>' +
      '<div class="celestial-profile-row"><span class="celestial-profile-label">Логин</span>' +
      '<span class="celestial-profile-value">@' + escapeHtml(user.login) + "</span></div>" +
      '<div class="celestial-profile-row"><span class="celestial-profile-label">Роль</span>' +
      '<span class="celestial-profile-value">' + escapeHtml(user.role ? user.role.name : "—") + "</span></div>" +
      '<div class="celestial-profile-row"><span class="celestial-profile-label">Статус</span>' +
      '<span class="celestial-profile-value" style="color:#16B57F">Активен</span></div>' +
      (user.keitaro_company_group
        ? '<div class="celestial-profile-row"><span class="celestial-profile-label">Группа Keitaro</span>' +
          '<span class="celestial-profile-value">' + escapeHtml(user.keitaro_company_group) + "</span></div>"
        : "") +
      '<div style="display:flex;justify-content:flex-end;gap:9px;margin-top:20px">' +
      (hasPermission(user, "settings.view")
        ? '<button type="button" data-profile-settings style="height:38px;border:1px solid #E5E7EF;background:#fff;' +
          'border-radius:9px;padding:0 14px;font:700 12px Manrope,sans-serif;color:#5A5FE0;cursor:pointer">Настройки</button>'
        : "") +
      '<button type="button" data-profile-close style="height:38px;border:0;background:#5A5FE0;color:#fff;' +
      'border-radius:9px;padding:0 17px;font:700 12px Manrope,sans-serif;cursor:pointer">Закрыть</button></div></section>';
    document.body.appendChild(overlay);
    var close = overlay.querySelector("[data-profile-close]");
    if (close) close.focus();
    overlay.addEventListener("click", function (event) {
      if (event.target === overlay || event.target.closest("[data-profile-close]")) overlay.remove();
      if (event.target.closest("[data-profile-settings]")) window.location.href = "/Settings.dc.html";
    });
    overlay.addEventListener("keydown", function (event) {
      if (event.key === "Escape") overlay.remove();
    });
  }

  async function logout() {
    try {
      await api.post("/auth/logout", {});
      window.location.replace("/login.html");
    } catch (error) {
      toast(error.message || "Не удалось выйти", "error");
    }
  }

  function findSyncCard() {
    var aside = document.querySelector("aside");
    if (!aside) return null;
    return Array.prototype.slice.call(aside.children).find(function (child) {
      return (child.textContent || "").indexOf("Синхронизация Keitaro") >= 0;
    }) || null;
  }

  function relativeTime(value) {
    if (!value) return "Ожидает первой синхронизации";
    var seconds = Math.max(0, Math.floor((Date.now() - new Date(value).getTime()) / 1000));
    if (seconds < 45) return "Данные обновлены только что";
    if (seconds < 3600) return "Данные обновлены " + Math.floor(seconds / 60) + " мин назад";
    if (seconds < 86400) return "Данные обновлены " + Math.floor(seconds / 3600) + " ч назад";
    return "Данные обновлены " + Math.floor(seconds / 86400) + " дн назад";
  }

  function renderSyncStatus(status) {
    var card = shellState.syncCard;
    if (!card || card.children.length < 3) return;
    var detail = card.children[1];
    var row = card.children[2];
    var dot = row.children[0];
    var label = row.children[1];
    var canSync = hasPermission(shellState.user, "settings.manage");
    var canConfigure = hasPermission(shellState.user, "settings.view");
    var actionable = status.state !== "syncing" &&
      ((status.configured && canSync) || (!status.configured && canConfigure));
    card.dataset.actionable = String(actionable);
    card.setAttribute("aria-busy", String(status.state === "syncing"));
    if (actionable) {
      card.setAttribute("role", "button");
      card.setAttribute("tabindex", "0");
    } else {
      card.removeAttribute("role");
      card.removeAttribute("tabindex");
    }

    var color = "#7BEBB8";
    var text = "API активен";
    if (!status.configured) {
      detail.textContent = "Подключение ещё не настроено";
      text = "Требует настройки";
      color = "#D6D9E4";
    } else if (status.state === "syncing") {
      detail.textContent = "Синхронизация: " + Number(status.progress_pct || 0) + "%";
      text = "Обновление данных";
      color = "#FFD166";
    } else if (status.state === "error") {
      detail.textContent = "Последняя синхронизация завершилась с ошибкой";
      text = "Ошибка API";
      color = "#FF9BAA";
    } else if (status.state === "inactive") {
      detail.textContent = relativeTime(status.last_sync_at);
      text = "Подключение выключено";
      color = "#D6D9E4";
    } else {
      detail.textContent = relativeTime(status.last_sync_at);
    }
    if (dot) {
      dot.style.background = color;
      dot.style.boxShadow = "0 0 0 3px " + color + "4D";
    }
    if (label) label.textContent = text;
    card.title = actionable
      ? (status.configured ? "Нажмите, чтобы синхронизировать" : "Открыть настройки Keitaro")
      : text;
  }

  async function refreshSyncStatus() {
    if (!shellState.syncCard) return null;
    try {
      var status = await api.get("/integrations/keitaro/sidebar-status");
      shellState.syncStatus = status;
      renderSyncStatus(status);
      return status;
    } catch (error) {
      renderSyncStatus({
        configured: true,
        state: "error",
        progress_pct: 0,
        last_sync_at: null
      });
      return null;
    }
  }

  async function runSidebarSync() {
    var status = shellState.syncStatus;
    if (!status || shellState.syncBusy) return;
    if (!status.configured) {
      if (hasPermission(shellState.user, "settings.view")) {
        window.location.href = "/Settings.dc.html";
      }
      return;
    }
    if (!hasPermission(shellState.user, "settings.manage") || status.state === "syncing") return;
    shellState.syncBusy = true;
    renderSyncStatus(Object.assign({}, status, { state: "syncing", progress_pct: 0 }));
    try {
      await api.post(
        "/integrations/keitaro/" + status.connection_id + "/sync?mode=incremental",
        {},
        "sidebar-sync-" + status.connection_id + "-" + Date.now()
      );
      toast("Синхронизация Keitaro запущена", "info");
      for (var attempt = 0; attempt < 90; attempt += 1) {
        await new Promise(function (resolve) { window.setTimeout(resolve, 2000); });
        var current = await refreshSyncStatus();
        if (current && current.state !== "syncing") {
          if (current.state === "error") {
            toast(current.error || "Синхронизация завершилась с ошибкой", "error");
          } else {
            toast("Данные Keitaro обновлены");
            window.setTimeout(function () { window.location.reload(); }, 700);
          }
          return;
        }
      }
      toast("Синхронизация продолжается в фоне", "info");
    } catch (error) {
      toast(error.message || "Не удалось запустить синхронизацию", "error");
      await refreshSyncStatus();
    } finally {
      shellState.syncBusy = false;
    }
  }

  function setupSyncCard() {
    shellState.syncCard = findSyncCard();
    if (!shellState.syncCard) return;
    shellState.syncCard.id = "celestialSyncCard";
    shellState.syncCard.addEventListener("click", runSidebarSync);
    shellState.syncCard.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        runSidebarSync();
      }
    });
    refreshSyncStatus();
    if (shellState.syncTimer) window.clearInterval(shellState.syncTimer);
    shellState.syncTimer = window.setInterval(refreshSyncStatus, 30000);
  }

  async function initShell(user) {
    if (!user) return { allowed: false };
    if (document.documentElement.dataset.shellReady) {
      return { allowed: shellState.allowed };
    }
    document.documentElement.dataset.shellReady = "true";
    shellState.user = user;
    injectShellStyles();
    setupAccount(user);
    applyNavPermissions(user);

    var rule = currentRule();
    shellState.allowed = hasPermission(user, rule.permission);
    if (!shellState.allowed) {
      // Never fall through to the page loader: it would 403 and leave the mock-up
      // rows from the markup visible as if they were real data.
      renderAccessDenied(rule);
      return { allowed: false };
    }

    setupSyncCard();
    return { allowed: true };
  }

  document.addEventListener("DOMContentLoaded", function () {
    setupNavigation();
    // Runs before authentication so the demo rows never flash on screen.
    clearMockData(currentRule());
  });

  window.CelestialShell = {
    init: initShell,
    refreshSync: refreshSyncStatus,
    currentRule: currentRule
  };
})();
