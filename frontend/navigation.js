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
    "Meta Ads": "/MetaAds.dc.html",
    "Задачи": "/Tasks.dc.html",
    "База знаний": "/Knowledge.dc.html",
    "Финансы": "/Finance.dc.html",
    "Оффера": "/Offers.dc.html",
    "Команда": "/Team.dc.html",
    "Рекрутинг": "/Recruitment.dc.html",
    "Утилиты": "/Utilities.dc.html",
    "Настройки": "/Settings.dc.html"
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
      values: []
    },
    {
      id: "meta",
      match: "metaads",
      permission: "meta.view",
      title: "Meta Ads",
      containers: ["metaLevelHead", "metaTableBody", "metaAttribution"],
      values: ["metaSpend", "metaClicks", "metaLeads", "metaProfit", "metaResultCount"]
    },
    {
      id: "tasks",
      match: "tasks",
      permission: "workspace.view",
      title: "Задачи",
      containers: ["wsBoard"],
      values: ["wsTaskSummary"]
    },
    {
      id: "knowledge",
      match: "knowledge",
      permission: "knowledge.view",
      title: "База знаний",
      containers: ["wsKbTree", "wsDoc"],
      values: []
    },
    {
      id: "finance",
      match: "finance",
      permission: "finance.view",
      title: "Финансы",
      containers: ["finGridHead", "finGridBody", "finSummaryTiers",
        "finSummaryBuyers", "finSummaryDaily", "finSalaryRoles", "finSalaryTiers", "finSummaryPeople"],
      values: ["finOfferCount", "finCardIncome", "finCardSpend",
        "finCardCosts", "finCardProfit", "finCardRoi", "finCardSalary", "finSalaryFund"]
    },
    {
      id: "offers",
      match: "offer",
      permission: "offers.view",
      title: "Оффера",
      containers: ["offersTableBody"],
      values: ["offersTotal", "offersFree", "offersAtLeads", "offersActive",
        "offersResultCount"]
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
      id: "recruitment",
      match: "recruitment",
      permission: "recruitment.view",
      title: "Рекрутинг",
      containers: ["recBody", "recError", "recHeaderNote"],
      values: []
    },
    {
      id: "utilities",
      match: "utilities",
      permission: "utilities.view",
      title: "Утилиты",
      containers: ["utilAlerts", "utilCaps", "utilChannels", "utilEvents"],
      values: ["utilAlertsCount", "utilCapsCount", "utilChannelsCount"]
    },
    {
      id: "settings",
      match: "settings",
      permission: "settings.view",
      title: "Настройки",
      containers: ["providersTableBody", "integrationsGrid",
        "tierOneList", "tierTwoList"],
      values: ["settingsProvidersTotal", "settingsConnectionsTotal",
        "settingsProvidersTabCount", "settingsConnectionsTabCount",
        "settingsPartnersTabCount", "settingsPartnersTotal",
        "settingsProvidersResultCount", "settingsSyncSummary",
        "settingsTiersTabCount", "tierOneCount", "tierTwoCount"]
    },
    {
      id: "dashboard",
      match: "",
      permission: "dashboard.view",
      title: "Dashboard",
      containers: ["dashboardWorkingOffers", "dashboardChartSvg", "dashboardChartLabels"],
      values: ["dashboardRevenue", "dashboardSpend", "dashboardProfit", "dashboardRoi",
        "dashboardLeads", "dashboardSales", "dashboardEpl", "dashboardOffersCount"]
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
          '" style="padding:44px 24px;text-align:center;color:#9B9292;font-size:13px">' +
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

  /*
   * On a warm navigation there is no full-screen loader to carry the retry button, so a
   * failed module load has to say so where the data would have been. Placeholders are
   * never left spinning and mock values never come back.
   */
  function showLoadFailure(message) {
    var rule = currentRule();
    rule.containers.forEach(function (id) {
      var element = document.getElementById(id);
      if (!element || element.tagName !== "TBODY") return;
      element.innerHTML = '<tr><td colspan="' + columnCount(element) +
        '" style="padding:38px 24px;text-align:center;color:#857D7D;font-size:13px">' +
        escapeHtml(message || "Не удалось загрузить данные") +
        '<br><button type="button" data-shell-action="reload" style="margin-top:12px;border:1px solid #E5DFDF;' +
        "background:#F8F5F5;border-radius:9px;padding:7px 15px;font:700 12px Inter,sans-serif;" +
        'color:#B91414;cursor:pointer">Повторить</button></td></tr>';
      var retry = element.querySelector('[data-shell-action="reload"]');
      if (retry) retry.addEventListener("click", function () { window.location.reload(); });
    });
  }

  function renderAccessDenied(rule) {
    var host = document.querySelector("main") || document.body;
    host.innerHTML =
      '<section role="alert" style="max-width:520px;margin:96px auto;padding:32px;text-align:center;' +
      "background:#fff;border:1px solid #EBE6E6;border-radius:18px;font-family:'Inter',sans-serif\">" +
      '<div style="width:52px;height:52px;margin:0 auto 18px;border-radius:15px;background:#FDECEF;' +
      'display:flex;align-items:center;justify-content:center">' +
      '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
      '<rect x="4" y="10" width="16" height="10" rx="2.5" stroke="#FF0000" stroke-width="2"/>' +
      '<path d="M8 10V7a4 4 0 0 1 8 0v3" stroke="#FF0000" stroke-width="2" stroke-linecap="round"/></svg></div>' +
      '<h1 style="font:700 20px \'Alumni Sans\',Inter,sans-serif;color:#070505">Раздел недоступен</h1>' +
      '<p style="margin-top:10px;font-size:13px;color:#6A6161;line-height:1.55">У вашей роли нет прав на раздел «' +
      escapeHtml(rule.title) + '». Обратитесь к администратору, если доступ нужен для работы.</p>' +
      '<a href="/" style="display:inline-block;margin-top:20px;height:40px;line-height:40px;padding:0 20px;' +
      "background:#B91414;color:#fff;font-family:Alumni Sans,Inter,sans-serif;text-transform:uppercase;letter-spacing:.02em;border-radius:10px;font:600 14px Alumni Sans,Inter,sans-serif;text-decoration:none\">" +
      "На дашборд</a></section>";
  }

  function applyNavPermissions(user) {
    document.querySelectorAll("nav a, nav > div").forEach(function (element) {
      var name = pageName(element);
      if (!name) return;
      var rule = pageRules.find(function (item) { return item.title === name; });
      if (!rule) return;
      // Toggled both ways, since a background re-check can grant access as well as
      // remove it — and the markup lays these out with an inline `display:flex`, so the
      // original value has to come back rather than being cleared.
      if (element.dataset.shellDisplay === undefined) {
        element.dataset.shellDisplay = element.style.display || "";
      }
      element.style.display = hasPermission(user, rule.permission)
        ? element.dataset.shellDisplay
        : "none";
    });
  }

  /* Applied when the background `/auth/me` disagrees with the user this tab started with. */
  function refreshUser(user) {
    if (!user) return;
    shellState.user = user;
    var footer = document.getElementById("celestialAccountButton");
    if (footer) {
      var avatar = footer.children[0];
      var identity = footer.children[1];
      if (avatar) avatar.textContent = initials(user.name || user.login);
      if (identity && identity.children[0]) {
        identity.children[0].textContent = user.name || user.login;
      }
      if (identity && identity.children[1]) {
        identity.children[1].textContent = user.role ? user.role.name : "";
      }
    }
    applyNavPermissions(user);
    var rule = currentRule();
    if (!hasPermission(user, rule.permission)) {
      shellState.allowed = false;
      renderAccessDenied(rule);
    }
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
      "#celestialAccountButton:hover,#celestialAccountButton:focus-visible{background:#FAF8F8}" +
      "#celestialAccountButton[aria-expanded=true]{background:#F7F4F4}" +
      "#celestialAccountMenu{position:absolute;left:14px;right:14px;bottom:calc(100% + 9px);z-index:10020;" +
      "background:#fff;border:1px solid #EBE6E6;border-radius:13px;padding:6px;box-shadow:0 16px 42px rgba(23,17,17,.18)}" +
      "#celestialAccountMenu[hidden]{display:none}" +
      ".celestial-account-action{display:block;width:100%;border:0;background:transparent;border-radius:9px;" +
      "padding:10px 11px;text-align:left;font:600 12.5px Inter,sans-serif;color:#3A3030;cursor:pointer}" +
      ".celestial-account-action:hover,.celestial-account-action:focus-visible{background:#F7F4F4;outline:none}" +
      ".celestial-account-action[data-shell-action=logout]{color:#FF0000}" +
      "#celestialProfileOverlay{position:fixed;inset:0;z-index:10030;background:rgba(18,12,12,.42);" +
      "display:flex;align-items:center;justify-content:center;padding:24px}" +
      "#celestialProfileDialog{width:min(430px,100%);background:#fff;border:1px solid #EBE6E6;border-radius:18px;" +
      "box-shadow:0 24px 70px rgba(18,12,12,.25);padding:24px}" +
      ".celestial-profile-row{display:flex;justify-content:space-between;gap:24px;padding:11px 0;border-bottom:1px solid #F0EBEB}" +
      ".celestial-profile-label{font-size:12px;color:#857D7D;font-weight:600}" +
      ".celestial-profile-value{font-size:12.5px;color:#2A2020;font-weight:700;text-align:right;overflow-wrap:anywhere}" +
      "#celestialSyncCard{transition:transform .16s ease,box-shadow .16s ease;outline:none}" +
      "#celestialSyncCard[data-actionable=true]{cursor:pointer}" +
      "#celestialSyncCard[data-actionable=true]:hover,#celestialSyncCard[data-actionable=true]:focus-visible{" +
      "transform:translateY(-1px);box-shadow:0 10px 24px rgba(185,20,20,.25)}" +
      ".celestial-dialog-scrim{position:fixed;inset:0;z-index:10060;background:rgba(7,5,5,.46);" +
      "display:flex;align-items:center;justify-content:center;padding:24px;" +
      "animation:celestial-dialog-in .14s ease}" +
      ".celestial-dialog{width:min(430px,100%);background:#fff;border-radius:20px;padding:24px;" +
      "box-shadow:0 26px 60px rgba(30,20,20,.3)}" +
      ".celestial-dialog h2{font:700 19px/1.25 'Alumni Sans',Inter,sans-serif;letter-spacing:-.3px;" +
      "color:#070505;margin:0}" +
      ".celestial-dialog p{font:500 13px/1.6 Inter,sans-serif;color:#5A5151;margin:9px 0 0;" +
      "white-space:pre-line;overflow-wrap:anywhere}" +
      ".celestial-dialog p:empty{display:none}" +
      ".celestial-dialog-input{width:100%;height:44px;margin-top:16px;padding:0 13px;" +
      "border:1px solid #E8E2E2;border-radius:11px;font:600 13px Inter,sans-serif;color:#3A3030;" +
      "outline:none}" +
      ".celestial-dialog-input:focus{border-color:#D06060;box-shadow:0 0 0 3px rgba(185,20,20,.1)}" +
      ".celestial-dialog-actions{display:flex;gap:10px;justify-content:flex-end;margin-top:22px;" +
      "flex-wrap:wrap}" +
      ".celestial-dialog-button{min-height:42px;padding:0 18px;border:1px solid #E8E2E2;" +
      "border-radius:12px;background:#fff;color:#3A3030;font:700 13px Inter,sans-serif;cursor:pointer}" +
      ".celestial-dialog-button:hover{background:#F7F4F4}" +
      ".celestial-dialog-button--primary{border:0;background:#070505;color:#fff}" +
      ".celestial-dialog-button--primary:hover{background:#231A1A}" +
      ".celestial-dialog-button--danger{background:#B91414;box-shadow:0 8px 18px rgba(185,20,20,.24)}" +
      ".celestial-dialog-button--danger:hover{background:#A21212}" +
      "@keyframes celestial-dialog-in{from{opacity:0}to{opacity:1}}" +
      "@media(prefers-reduced-motion:reduce){.celestial-dialog-scrim{animation:none}}" +
      "@media(max-width:520px){.celestial-dialog-scrim{align-items:flex-end;padding:0}" +
      ".celestial-dialog{border-radius:20px 20px 0 0;" +
      "padding:22px 18px calc(20px + env(safe-area-inset-bottom))}" +
      ".celestial-dialog-actions{flex-direction:column-reverse}" +
      ".celestial-dialog-button{width:100%}}" +
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

  function setupNavigation() {
    var candidates = document.querySelectorAll("nav a, nav > div");

    candidates.forEach(function (element) {
      var name = pageName(element);
      if (!name) return;

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

  function setupResponsiveShell() {
    var aside = document.querySelector("aside");
    var main = document.querySelector("main");
    if (!aside || !main || document.getElementById("celestialMobileMenu")) return;

    aside.classList.add("celestial-sidebar");
    main.classList.add("celestial-main");

    var menuButton = document.createElement("button");
    menuButton.id = "celestialMobileMenu";
    menuButton.className = "celestial-mobile-menu";
    menuButton.type = "button";
    menuButton.setAttribute("aria-label", "Открыть навигацию");
    menuButton.setAttribute("aria-controls", "celestialSidebar");
    menuButton.setAttribute("aria-expanded", "false");
    menuButton.innerHTML =
      '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
      '<path d="M4 7h16M4 12h16M4 17h10" stroke="currentColor" stroke-width="2" ' +
      'stroke-linecap="round"/></svg>';

    var scrim = document.createElement("button");
    scrim.className = "celestial-sidebar-scrim";
    scrim.type = "button";
    scrim.setAttribute("aria-label", "Закрыть навигацию");

    aside.id = "celestialSidebar";
    document.body.appendChild(menuButton);
    document.body.appendChild(scrim);

    function setSidebarOpen(open) {
      document.body.classList.toggle("celestial-sidebar-open", open);
      menuButton.setAttribute("aria-expanded", String(open));
      menuButton.setAttribute(
        "aria-label",
        open ? "Закрыть навигацию" : "Открыть навигацию"
      );
    }

    menuButton.addEventListener("click", function () {
      setSidebarOpen(!document.body.classList.contains("celestial-sidebar-open"));
    });
    scrim.addEventListener("click", function () { setSidebarOpen(false); });
    aside.addEventListener("click", function (event) {
      if (event.target.closest("a")) setSidebarOpen(false);
    });
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") setSidebarOpen(false);
    });
    window.addEventListener("resize", function () {
      if (window.innerWidth > 1024) setSidebarOpen(false);
    });
  }

  /* ---------- диалоги подтверждения ----------
   *
   * Штатные window.confirm/prompt/alert браузер рисует своим окном у верхней
   * кромки — оно выпадает из интерфейса CRM и перекрывает шапку, а на карточке
   * задачи вообще выглядит как сообщение сайта, а не действия. Здесь тот же
   * контракт (Promise вместо возвращаемого значения) и та же вёрстка, что у
   * остальных модалок: карточка по центру, затемнение, красная кнопка действия.
   */

  var dialogState = { resolve: null, previousFocus: null, previousOverflow: "" };

  function closeDialog(result) {
    var node = document.getElementById("celestialDialog");
    if (node) node.remove();
    document.body.style.overflow = dialogState.previousOverflow;
    var resolve = dialogState.resolve;
    var focus = dialogState.previousFocus;
    dialogState.resolve = null;
    dialogState.previousFocus = null;
    if (focus && document.contains(focus)) focus.focus();
    if (resolve) resolve(result);
  }

  function shellDialog(options) {
    var config = options || {};
    var kind = config.kind || "confirm";
    // Открытый диалог закрываем как отменённый: два подтверждения одновременно
    // означали бы, что пользователь отвечает не на тот вопрос, который видит.
    if (dialogState.resolve) closeDialog(kind === "prompt" ? null : false);

    return new Promise(function (resolve) {
      // Стили обычно уже вставлены оболочкой, но диалог может понадобиться
      // раньше — например, когда страница ещё не прошла инициализацию. Без
      // этого вопрос показался бы голым текстом поверх интерфейса.
      injectShellStyles();
      dialogState.resolve = resolve;
      dialogState.previousFocus = document.activeElement;
      dialogState.previousOverflow = document.body.style.overflow;

      var scrim = document.createElement("div");
      scrim.id = "celestialDialog";
      scrim.className = "celestial-dialog-scrim";
      scrim.innerHTML =
        '<div class="celestial-dialog" role="alertdialog" aria-modal="true" ' +
        'aria-labelledby="celestialDialogTitle" aria-describedby="celestialDialogText">' +
        '<h2 id="celestialDialogTitle">' + escapeHtml(config.title || "Подтвердите действие") +
        "</h2>" +
        '<p id="celestialDialogText">' + escapeHtml(config.message || "") + "</p>" +
        (kind === "prompt"
          ? '<input id="celestialDialogInput" class="celestial-dialog-input" type="text" ' +
            'value="' + escapeHtml(config.value || "") + '"' +
            (config.placeholder ? ' placeholder="' + escapeHtml(config.placeholder) + '"' : "") +
            ">"
          : "") +
        '<div class="celestial-dialog-actions">' +
        (kind === "alert"
          ? ""
          : '<button type="button" class="celestial-dialog-button" data-dialog="cancel">' +
            escapeHtml(config.cancelLabel || "Отмена") + "</button>") +
        '<button type="button" class="celestial-dialog-button celestial-dialog-button--primary' +
        (config.danger ? " celestial-dialog-button--danger" : "") + '" data-dialog="ok">' +
        escapeHtml(config.confirmLabel || (kind === "alert" ? "Понятно" : "Подтвердить")) +
        "</button></div></div>";
      document.body.appendChild(scrim);
      document.body.style.overflow = "hidden";

      function answer(ok) {
        var input = document.getElementById("celestialDialogInput");
        if (kind === "prompt") return closeDialog(ok ? input.value : null);
        closeDialog(kind === "alert" ? true : ok);
      }

      scrim.addEventListener("click", function (event) {
        var button = event.target.closest ? event.target.closest("[data-dialog]") : null;
        if (button) return answer(button.getAttribute("data-dialog") === "ok");
        // Клик мимо карточки — это отказ, а не подтверждение.
        if (event.target === scrim) answer(false);
      });
      scrim.addEventListener("keydown", function (event) {
        if (event.key === "Escape") {
          event.preventDefault();
          return answer(false);
        }
        if (event.key === "Enter" && event.target.tagName !== "BUTTON") {
          event.preventDefault();
          return answer(true);
        }
        if (event.key !== "Tab") return;
        var focusable = scrim.querySelectorAll("button,input");
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

      var input = document.getElementById("celestialDialogInput");
      if (input) {
        input.focus();
        input.select();
      } else {
        scrim.querySelector('[data-dialog="ok"]').focus();
      }
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
      "border-radius:11px;color:#fff;font:700 12px Inter,sans-serif;box-shadow:0 14px 38px rgba(23,17,17,.22);" +
      "background:" + (kind === "error" ? "#FF0000" : kind === "info" ? "#B91414" : "#16B57F");
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
      '<div style="width:44px;height:44px;border-radius:12px;background:linear-gradient(135deg,#070505,#B91414);' +
      'display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:15px">' +
      escapeHtml(initials(user.name || user.login)) + "</div>" +
      '<div><h2 id="celestialProfileTitle" style="font:700 19px Alumni Sans,Inter,sans-serif;color:#070505">' +
      escapeHtml(user.name || user.login) + '</h2><div style="font-size:12px;color:#857D7D;margin-top:3px">Профиль пользователя</div></div></div>' +
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
        ? '<button type="button" data-profile-settings style="height:38px;border:1px solid #EBE6E6;background:#fff;' +
          'border-radius:9px;padding:0 14px;font:700 12px Inter,sans-serif;color:#B91414;cursor:pointer">Настройки</button>'
        : "") +
      '<button type="button" data-profile-close style="height:38px;border:0;background:#B91414;color:#fff;font-family:Alumni Sans,Inter,sans-serif;text-transform:uppercase;letter-spacing:.02em;' +
      'border-radius:9px;padding:0 17px;font:700 12px Inter,sans-serif;cursor:pointer">Закрыть</button></div></section>';
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
      if (window.CelestialSession) window.CelestialSession.clear();
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
      color = "#D8D0D0";
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
      color = "#D8D0D0";
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

  function renderSyncLoading() {
    var card = shellState.syncCard;
    if (!card || card.children.length < 3) return;
    var detail = card.children[1];
    var row = card.children[2];
    var dot = row.children[0];
    var label = row.children[1];
    card.dataset.actionable = "false";
    card.setAttribute("aria-busy", "true");
    card.removeAttribute("role");
    card.removeAttribute("tabindex");
    if (detail) detail.textContent = "Проверяем подключение…";
    if (dot) {
      dot.style.background = "#D8D0D0";
      dot.style.boxShadow = "0 0 0 3px rgba(214,217,228,.3)";
    }
    if (label) label.textContent = "Проверка API";
    card.title = "Проверяем состояние Keitaro";
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
    renderSyncLoading();
    var initialRefresh = refreshSyncStatus();
    if (shellState.syncTimer) window.clearInterval(shellState.syncTimer);
    shellState.syncTimer = window.setInterval(refreshSyncStatus, 30000);
    return initialRefresh;
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

    return { allowed: true, ready: setupSyncCard() };
  }

  document.addEventListener("DOMContentLoaded", function () {
    setupResponsiveShell();
    setupNavigation();
    // Runs before authentication so the demo rows never flash on screen.
    clearMockData(currentRule());
  });

  window.CelestialShell = {
    init: initShell,
    refreshSync: refreshSyncStatus,
    currentRule: currentRule,
    refreshUser: refreshUser,
    showLoadFailure: showLoadFailure,
    // Замена window.confirm/prompt/alert: тот же смысл, но Promise и стиль CRM.
    confirm: function (options) {
      return shellDialog(Object.assign({ kind: "confirm" }, options));
    },
    prompt: function (options) {
      return shellDialog(Object.assign({ kind: "prompt" }, options));
    },
    notify: function (options) {
      return shellDialog(Object.assign({ kind: "alert" }, options));
    },
    toast: toast
  };
})();
