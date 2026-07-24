(function () {
  "use strict";

  var api = window.CelestialAPI;
  if (!api) return;

  /* ---------- helpers ---------- */

  function byId(id) { return document.getElementById(id); }

  function setText(id, value) {
    var element = byId(id);
    if (element) element.textContent = value;
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;").replace(/'/g, "&#039;");
  }

  function number(value) {
    return new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 0 }).format(Number(value || 0));
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

  function toast(message, kind) {
    var current = byId("celestialLiveToast");
    if (current) current.remove();
    var element = document.createElement("div");
    element.id = "celestialLiveToast";
    element.textContent = message;
    element.style.cssText =
      "position:fixed;right:24px;bottom:24px;z-index:99999;max-width:420px;" +
      "padding:13px 17px;border-radius:11px;color:#fff;font:700 12px Manrope,sans-serif;" +
      "box-shadow:0 14px 38px rgba(31,34,49,.22);background:" +
      (kind === "error" ? "#D94B61" : kind === "info" ? "#5A5FE0" : "#16B57F");
    document.body.appendChild(element);
    window.setTimeout(function () { element.remove(); }, 5000);
  }

  function fail(error) {
    if (error && error.status === 401) {
      window.location.replace("/login.html?next=" + encodeURIComponent(
        window.location.pathname + window.location.search));
      return;
    }
    toast(error && error.message ? error.message : "Ошибка запроса", "error");
  }

  function statusPill(status) {
    var active = status === "active";
    var color = active ? "#16B57F" : "#F1556C";
    var background = active ? "#E4F7F0" : "#FDECEF";
    return '<span style="display:inline-flex;align-items:center;gap:6px;font-size:12.5px;font-weight:700;color:' +
      color + ";background:" + background + ';padding:6px 11px;border-radius:8px">' +
      '<span style="width:6px;height:6px;border-radius:50%;background:' + color + '"></span>' +
      (active ? "Активно" : "Неактивно") + "</span>";
  }

  function statusControl(entity, item, canManage) {
    if (!canManage) return statusPill(item.status);
    var nextLabel = item.status === "active" ? "Неактивно" : "Активно";
    return '<button type="button" data-status-toggle data-status-entity="' + entity +
      '" data-status-id="' + escapeHtml(item.id) + '" data-status-current="' + escapeHtml(item.status) +
      '" title="Изменить на «' + nextLabel + '»" aria-label="Изменить статус на ' + nextLabel +
      '" style="border:0;background:transparent;padding:0;cursor:pointer;font-family:inherit">' +
      statusPill(item.status) + "</button>";
  }

  function bindStatusControls() {
    if (document.documentElement.dataset.catalogStatusControlsBound) return;
    document.documentElement.dataset.catalogStatusControlsBound = "true";
    document.addEventListener("click", function (event) {
      var button = event.target.closest("[data-status-toggle]");
      if (!button || button.disabled) return;
      var entity = button.getAttribute("data-status-entity");
      var id = button.getAttribute("data-status-id");
      var current = button.getAttribute("data-status-current");
      var next = current === "active" ? "inactive" : "active";
      var collection = entity === "offer" ? offersState.offers : partnersState.partners;
      var item = collection.find(function (entry) { return String(entry.id) === String(id); });
      if (!item) return;
      button.disabled = true;
      button.style.opacity = ".55";
      api.patch("/" + entity + "s/" + id + "/status", { status: next })
        .then(function (updated) {
          item.status = updated.status;
          item.status_overridden = updated.status_overridden;
          toast((entity === "offer" ? "Статус оффера" : "Статус партнёрки") + " сохранён");
          if (entity === "offer") {
            renderOfferStats();
            renderOfferRows();
          } else {
            renderPartnerStats();
            renderPartnerRows();
          }
        })
        .catch(function (error) {
          button.disabled = false;
          button.style.opacity = "";
          fail(error);
        });
    });
  }

  var AVATAR_COLORS = ["#5A5FE0", "#16B57F", "#E8912B", "#F1556C", "#6D5FF5", "#3D8DE8"];

  function avatarColor(index) { return AVATAR_COLORS[index % AVATAR_COLORS.length]; }

  var PAGE_SIZE = 6;

  function pageList(total, current) {
    if (total <= 7) {
      var all = [];
      for (var i = 1; i <= total; i += 1) all.push(i);
      return all;
    }
    var pages = [1];
    var start = Math.max(2, current - 1);
    var end = Math.min(total - 1, current + 1);
    if (start > 2) pages.push("…");
    for (var page = start; page <= end; page += 1) pages.push(page);
    if (end < total - 1) pages.push("…");
    pages.push(total);
    return pages;
  }

  function pagerCell(label, page, active, disabled) {
    var base = "width:34px;height:34px;border-radius:9px;display:flex;align-items:center;justify-content:center;" +
      "font-weight:700;font-size:13px;font-family:Space Grotesk;";
    if (page == null) {
      return '<span style="' + base + 'color:#A2A7B5">' + label + "</span>";
    }
    if (active) {
      return '<span style="' + base + 'background:#5A5FE0;color:#fff">' + label + "</span>";
    }
    var style = base + "background:#fff;border:1px solid #ECEEF3;color:" +
      (disabled ? "#D6D9E4;cursor:not-allowed" : "#6B7180;cursor:pointer");
    return '<button type="button"' + (disabled ? " disabled" : ' data-page="' + page + '"') +
      ' style="' + style + '">' + label + "</button>";
  }

  function renderPager(container, totalPages, currentPage, goTo) {
    if (!container) return;
    if (totalPages <= 1) { container.innerHTML = ""; return; }
    var parts = [pagerCell("‹", currentPage - 1, false, currentPage <= 1)];
    pageList(totalPages, currentPage).forEach(function (page) {
      if (page === "…") parts.push(pagerCell("…", null, false, false));
      else parts.push(pagerCell(String(page), page, page === currentPage, false));
    });
    parts.push(pagerCell("›", currentPage + 1, false, currentPage >= totalPages));
    container.innerHTML = parts.join("");
    container.querySelectorAll("[data-page]").forEach(function (button) {
      button.addEventListener("click", function () {
        goTo(Number(button.getAttribute("data-page")));
      });
    });
  }

  /* ==========================================================
     OFFERS
     ========================================================== */

  var offersState = { user: null, offers: [], buyers: [], canManage: false, page: 1 };

  function fillSelect(id, values, includeAll) {
    var element = byId(id);
    if (!element) return;
    var current = element.value;
    var options = includeAll ? ['<option value="">' + includeAll + "</option>"] : [];
    values.forEach(function (entry) {
      options.push('<option value="' + escapeHtml(entry.value) + '">' + escapeHtml(entry.label) + "</option>");
    });
    element.innerHTML = options.join("");
    element.value = current;
  }

  function offerMatchesFilters(offer) {
    var search = (byId("offerSearch") ? byId("offerSearch").value : "").trim().toLowerCase();
    var geo = byId("filterGeo") ? byId("filterGeo").value : "";
    var partner = byId("filterPartner") ? byId("filterPartner").value : "";
    var buyer = byId("filterBuyer") ? byId("filterBuyer").value : "";
    var status = byId("filterStatus") ? byId("filterStatus").value : "";
    if (search) {
      var haystack = (offer.name + " " + offer.external_id).toLowerCase();
      if (haystack.indexOf(search) < 0) return false;
    }
    if (geo && offer.geo !== geo) return false;
    if (partner && offer.partner !== partner) return false;
    if (status && offer.status !== status) return false;
    if (buyer && !(offer.buyers || []).some(function (b) { return String(b.id) === buyer; })) return false;
    return true;
  }

  function renderOfferRows() {
    var body = byId("offersTableBody");
    if (!body) return;
    var visible = offersState.offers.filter(offerMatchesFilters);
    var totalPages = Math.max(1, Math.ceil(visible.length / PAGE_SIZE));
    if (offersState.page > totalPages) offersState.page = totalPages;
    var pageItems = visible.slice((offersState.page - 1) * PAGE_SIZE, offersState.page * PAGE_SIZE);
    if (!visible.length) {
      var message = offersState.offers.length
        ? "Офферы не найдены — измените фильтры"
        : "Офферы появятся после синхронизации Keitaro";
      body.innerHTML = '<tr><td colspan="6" style="padding:44px 24px;text-align:center;color:#A2A7B5;font-size:13px">' +
        escapeHtml(message) + "</td></tr>";
    } else {
      body.innerHTML = pageItems.map(function (offer, index) {
        var buyers = offer.buyers || [];
        var avatars = buyers.slice(0, 3).map(function (buyer, i) {
          return '<span title="' + escapeHtml(buyer.name) + '" style="display:inline-flex;align-items:center;justify-content:center;' +
            "width:28px;height:28px;border-radius:50%;margin-left:" + (i ? "-8px" : "0") +
            ";border:2px solid #fff;color:#fff;font-size:10px;font-weight:700;font-family:Space Grotesk;background:" +
            avatarColor(index + i) + '">' + escapeHtml(initials(buyer.name)) + "</span>";
        }).join("");
        var buyerLabel = buyers.length
          ? '<span style="font-size:11.5px;color:#6B7180;font-weight:600;margin-left:9px">' +
            buyers.length + " " + pluralBuyers(buyers.length) + "</span>"
          : '<span style="font-size:12px;color:#A2A7B5">Не назначены</span>';
        var action = offersState.canManage
          ? '<button type="button" data-assign="' + escapeHtml(offer.id) + '" title="Назначить баеров" ' +
            'style="border:1px solid #ECEEF3;background:#fff;border-radius:9px;height:34px;padding:0 12px;' +
            'font:700 12px Manrope,sans-serif;color:#5A5FE0;cursor:pointer">Баеры</button>'
          : '<span style="color:#C7CAD6">•••</span>';
        return '<tr style="border-bottom:1px solid #F5F6FA">' +
          '<td style="padding:15px 24px"><div style="display:flex;align-items:center;gap:13px">' +
          '<div style="width:40px;height:40px;border-radius:12px;background:#EEF0FF;color:#5A5FE0;flex-shrink:0;' +
          'display:flex;align-items:center;justify-content:center;font-weight:800;font-size:12px;font-family:Space Grotesk">' +
          escapeHtml(initials(offer.name)) + '</div><div style="min-width:0"><div style="font-weight:700;font-size:14px">' +
          escapeHtml(offer.name) + '</div><div style="font-size:11px;color:#A2A7B5;margin-top:3px">ID ' +
          escapeHtml(offer.external_id) + "</div></div></div></td>" +
          '<td style="padding:15px 18px;font-weight:700">' + escapeHtml(offer.geo || "—") + "</td>" +
          '<td style="padding:15px 18px">' + escapeHtml(offer.partner || "—") + "</td>" +
          '<td style="padding:15px 18px"><div style="display:flex;align-items:center">' + avatars + buyerLabel + "</div></td>" +
          '<td style="padding:15px 18px">' + statusControl("offer", offer, offersState.canManage) + "</td>" +
          '<td style="padding:15px 18px;text-align:right">' + action + "</td></tr>";
      }).join("");
    }
    setText("offersResultCount", "Показано " + pageItems.length + " из " + visible.length +
      (visible.length !== offersState.offers.length ? " (всего " + offersState.offers.length + ")" : "") +
      " · синхронизировано из Keitaro");
    renderPager(byId("offersPagination"), totalPages, offersState.page, function (page) {
      offersState.page = page;
      renderOfferRows();
    });
  }

  function pluralBuyers(count) {
    var mod10 = count % 10, mod100 = count % 100;
    if (mod10 === 1 && mod100 !== 11) return "баер";
    if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return "баера";
    return "баеров";
  }

  function renderOfferStats() {
    var offers = offersState.offers;
    setText("offersTotal", number(offers.length));
    setText("offersActive", number(offers.filter(function (o) { return o.status === "active"; }).length));
    var geos = {};
    offers.forEach(function (o) { if (o.status === "active" && o.geo) geos[o.geo] = true; });
    setText("offersGeos", number(Object.keys(geos).length));
  }

  function populateOfferFilters() {
    var geos = {}, partners = {};
    offersState.offers.forEach(function (offer) {
      if (offer.geo) geos[offer.geo] = true;
      if (offer.partner) partners[offer.partner] = true;
    });
    fillSelect("filterGeo", Object.keys(geos).sort().map(function (g) {
      return { value: g, label: g };
    }), "Все GEO");
    fillSelect("filterPartner", Object.keys(partners).sort().map(function (p) {
      return { value: p, label: p };
    }), "Все партнёрки");
    fillSelect("filterBuyer", offersState.buyers.map(function (b) {
      return { value: String(b.id), label: b.name };
    }), "Все баеры");
    fillSelect("filterStatus", [
      { value: "active", label: "Активно" },
      { value: "inactive", label: "Неактивно" }
    ], "Все статусы");
  }

  function applyOfferFilters() {
    offersState.page = 1;
    renderOfferRows();
  }

  function bindOfferControls() {
    if (document.documentElement.dataset.offerControlsBound) return;
    document.documentElement.dataset.offerControlsBound = "true";
    bindStatusControls();
    var search = byId("offerSearch");
    if (search) search.addEventListener("input", applyOfferFilters);
    ["filterGeo", "filterPartner", "filterBuyer", "filterStatus"].forEach(function (id) {
      var element = byId(id);
      if (element) element.addEventListener("change", applyOfferFilters);
    });
    var reset = byId("resetOfferFilters");
    if (reset) reset.addEventListener("click", function () {
      ["offerSearch", "filterGeo", "filterPartner", "filterBuyer", "filterStatus"].forEach(function (id) {
        var element = byId(id);
        if (element) element.value = "";
      });
      applyOfferFilters();
    });
    document.addEventListener("click", function (event) {
      var assign = event.target.closest("[data-assign]");
      if (assign) openAssignModal(assign.getAttribute("data-assign"));
    });
  }

  /* ----- assign buyers modal ----- */

  function openAssignModal(offerId) {
    var offer = offersState.offers.find(function (o) { return String(o.id) === String(offerId); });
    if (!offer) return;
    var existing = byId("assignBuyersModal");
    if (existing) existing.remove();
    var selected = {};
    (offer.buyers || []).forEach(function (b) { selected[String(b.id)] = true; });
    var overlay = document.createElement("div");
    overlay.id = "assignBuyersModal";
    overlay.style.cssText =
      "position:fixed;inset:0;z-index:9999;background:rgba(23,26,38,.45);display:flex;" +
      "align-items:center;justify-content:center;padding:24px;font-family:'Manrope',sans-serif";
    var rows = offersState.buyers.map(function (buyer) {
      var id = String(buyer.id);
      return '<label style="display:flex;align-items:center;gap:11px;padding:10px 12px;border-radius:10px;cursor:pointer;' +
        'border:1px solid #ECEEF3;margin-bottom:8px">' +
        '<input type="checkbox" data-buyer="' + escapeHtml(id) + '"' + (selected[id] ? " checked" : "") +
        ' style="width:17px;height:17px;accent-color:#5A5FE0;cursor:pointer">' +
        '<span style="display:inline-flex;align-items:center;justify-content:center;width:30px;height:30px;border-radius:50%;' +
        'color:#fff;font-size:11px;font-weight:700;font-family:Space Grotesk;background:' + avatarColor(buyer._index || 0) + '">' +
        escapeHtml(initials(buyer.name)) + "</span>" +
        '<div><div style="font-weight:700;font-size:13px">' + escapeHtml(buyer.name) + "</div>" +
        '<div style="font-size:11px;color:#A2A7B5">@' + escapeHtml(buyer.login) + "</div></div></label>";
    }).join("");
    overlay.innerHTML =
      '<div style="background:#fff;border-radius:18px;max-width:460px;width:100%;max-height:calc(100vh - 48px);' +
      'display:flex;flex-direction:column;box-shadow:0 24px 70px rgba(23,26,38,.3)">' +
      '<div style="padding:22px 24px 16px;border-bottom:1px solid #ECEEF3">' +
      '<h2 style="font-family:Space Grotesk;font-size:19px;font-weight:700">Назначить баеров</h2>' +
      '<div style="font-size:12px;color:#A2A7B5;margin-top:4px">' + escapeHtml(offer.name) + "</div></div>" +
      '<div style="padding:16px 24px;overflow-y:auto">' +
      (rows || '<div style="color:#A2A7B5;font-size:13px;text-align:center;padding:20px">Нет доступных баеров</div>') +
      "</div>" +
      '<div style="display:flex;justify-content:flex-end;gap:10px;padding:15px 24px 18px;border-top:1px solid #ECEEF3">' +
      '<button id="assignCancel" style="border:1px solid #ECEEF3;background:#fff;border-radius:10px;padding:10px 18px;' +
      'font:700 13px Manrope,sans-serif;color:#6B7180;cursor:pointer">Отмена</button>' +
      '<button id="assignSave" style="border:none;background:#5A5FE0;color:#fff;border-radius:10px;padding:10px 22px;' +
      'font:700 13px Manrope,sans-serif;cursor:pointer;box-shadow:0 8px 18px rgba(90,95,224,.28)">Сохранить</button>' +
      "</div></div>";
    document.body.appendChild(overlay);
    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) overlay.remove();
    });
    byId("assignCancel").addEventListener("click", function () { overlay.remove(); });
    byId("assignSave").addEventListener("click", function () {
      var ids = Array.prototype.slice.call(overlay.querySelectorAll("[data-buyer]:checked"))
        .map(function (input) { return input.getAttribute("data-buyer"); });
      var button = byId("assignSave");
      button.disabled = true;
      button.textContent = "Сохраняю…";
      api.put("/offers/" + offerId + "/buyers", { buyer_ids: ids })
        .then(function () {
          toast("Баеры обновлены");
          overlay.remove();
          return loadOffers();
        })
        .catch(function (error) {
          button.disabled = false;
          button.textContent = "Сохранить";
          fail(error);
        });
    });
  }

  async function loadOffers() {
    var results = await Promise.all([
      api.getAll("/offers"),
      api.get("/users/options")
    ]);
    offersState.offers = results[0].items || [];
    offersState.buyers = (results[1] || []).map(function (buyer, index) {
      buyer._index = index;
      return buyer;
    });
    populateOfferFilters();
    renderOfferStats();
    renderOfferRows();
  }

  async function initOffers(user) {
    offersState.user = user;
    offersState.canManage = hasPermission(user, "offers.manage");
    bindOfferControls();
    await loadOffers();
  }

  /* ==========================================================
     PARTNERS
     ========================================================== */

  var partnersState = { user: null, partners: [], canManage: false, page: 1 };

  function partnerMatchesFilters(partner) {
    var search = (byId("partnerSearch") ? byId("partnerSearch").value : "").trim().toLowerCase();
    var status = byId("partnerStatusFilter") ? byId("partnerStatusFilter").value : "";
    if (search && partner.name.toLowerCase().indexOf(search) < 0) return false;
    if (status && partner.status !== status) return false;
    return true;
  }

  function renderPartnerRows() {
    var body = byId("partnersTableBody");
    if (!body) return;
    var visible = partnersState.partners.filter(partnerMatchesFilters);
    var totalPages = Math.max(1, Math.ceil(visible.length / PAGE_SIZE));
    if (partnersState.page > totalPages) partnersState.page = totalPages;
    var pageItems = visible.slice((partnersState.page - 1) * PAGE_SIZE, partnersState.page * PAGE_SIZE);
    if (!visible.length) {
      body.innerHTML = '<tr><td colspan="3" style="padding:44px 24px;text-align:center;color:#A2A7B5;font-size:13px">' +
        "Партнёрки не найдены</td></tr>";
    } else {
      body.innerHTML = pageItems.map(function (partner, index) {
        return '<tr style="border-bottom:1px solid #F5F6FA">' +
          '<td style="padding:15px 24px"><div style="display:flex;align-items:center;gap:13px">' +
          '<div style="width:38px;height:38px;border-radius:11px;color:#fff;flex-shrink:0;' +
          'display:flex;align-items:center;justify-content:center;font-weight:700;font-size:13px;font-family:Space Grotesk;background:' +
          avatarColor(index) + '">' + escapeHtml(initials(partner.name)) + "</div>" +
          '<div><div style="font-weight:700;font-size:14px">' + escapeHtml(partner.name) + "</div>" +
          '<div style="font-size:10.5px;color:#A2A7B5;margin-top:3px">ID ' + escapeHtml(partner.external_id) + "</div></div></div></td>" +
          '<td style="padding:15px 24px"><span style="font-family:Space Grotesk;font-weight:700;font-size:14px">' +
          number(partner.offers_count) + '</span><span style="font-size:12.5px;color:#A2A7B5;margin-left:6px">офферов</span></td>' +
          '<td style="padding:15px 24px">' + statusControl("partner", partner, partnersState.canManage) + "</td></tr>";
      }).join("");
    }
    setText("partnersResultCount", "Показано " + pageItems.length + " из " + visible.length +
      (visible.length !== partnersState.partners.length ? " (всего " + partnersState.partners.length + ")" : "") +
      " · синхронизировано из Keitaro");
    renderPager(byId("partnersPagination"), totalPages, partnersState.page, function (page) {
      partnersState.page = page;
      renderPartnerRows();
    });
  }

  function renderPartnerStats() {
    var partners = partnersState.partners;
    setText("partnersTotal", number(partners.length));
    setText("partnersActive", number(partners.filter(function (p) { return p.status === "active"; }).length));
    setText("partnersOffers", number(partners.reduce(function (total, p) {
      return total + Number(p.offers_count || 0);
    }, 0)));
  }

  function bindPartnerControls() {
    if (document.documentElement.dataset.partnerControlsBound) return;
    document.documentElement.dataset.partnerControlsBound = "true";
    bindStatusControls();
    var search = byId("partnerSearch");
    if (search) search.addEventListener("input", function () {
      partnersState.page = 1;
      renderPartnerRows();
    });
    var status = byId("partnerStatusFilter");
    if (status) status.addEventListener("change", function () {
      partnersState.page = 1;
      renderPartnerRows();
    });
  }

  async function loadPartners() {
    var page = await api.getAll("/partners");
    partnersState.partners = page.items || [];
    renderPartnerStats();
    renderPartnerRows();
  }

  async function initPartners(user) {
    partnersState.user = user;
    partnersState.canManage = hasPermission(user, "offers.manage");
    bindPartnerControls();
    await loadPartners();
  }

  window.CelestialCatalog = {
    initOffers: initOffers,
    initPartners: initPartners,
    reloadOffers: loadOffers,
    reloadPartners: loadPartners
  };
})();
