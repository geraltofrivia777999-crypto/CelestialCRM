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
      "padding:13px 17px;border-radius:11px;color:#fff;font:700 12px Inter,sans-serif;" +
      "box-shadow:0 14px 38px rgba(23,17,17,.22);background:" +
      (kind === "error" ? "#FF0000" : kind === "info" ? "#B91414" : "#16B57F");
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

  // v2 keeps avatars inside the brand: red, green and amber, no blues.
  var AVATAR_COLORS = ["#B91414", "#16B57F", "#F5A524", "#C41616", "#E8912B", "#8A0F0F"];

  function avatarColor(index) { return AVATAR_COLORS[index % AVATAR_COLORS.length]; }

  /* ==========================================================
     OFFERS

     Оффера заводятся вручную: раздел ведёт свой справочник, а из Keitaro
     остаются только списки GEO и партнёрок, чтобы значения совпадали с
     Медиабордом и Финансами.
     ========================================================== */

  var offersState = {
    user: null,
    offers: [],
    people: [],
    geos: [],
    partners: [],
    canManage: false,
    canManageCaps: false,
    // Какие оффера раскрыты: KPI и комментарий показываются под строкой.
    opened: {},
    starredFirst: false,
    statusAnchor: null,
    rowAnchor: null
  };

  // Ручной справочник: синхронизированные строки принадлежат трекеру.
  var OFFERS_QUERY = "/offers?manual=true&scope_offers=true";

  /* ----- workflow status ----- */

  var OFFER_STATUSES = [
    { value: "active", label: "Активен", color: "#3D5573", background: "#EFF4FB" },
    { value: "working", label: "В работе", color: "#16B57F", background: "#E4F7F0" },
    { value: "hold", label: "Холд", color: "#6A6161", background: "#F2EDED" },
    { value: "stop", label: "Стоп", color: "#C41616", background: "#FCF1F1" },
    { value: "free", label: "Не занят", color: "#9B9292", background: "#F7F4F4" }
  ];

  function offerStatus(value) {
    for (var i = 0; i < OFFER_STATUSES.length; i += 1) {
      if (OFFER_STATUSES[i].value === value) return OFFER_STATUSES[i];
    }
    return { value: value, label: value || "—", color: "#9B9292", background: "#F7F4F4" };
  }

  function offerStatusPill(value) {
    var status = offerStatus(value);
    return '<span style="display:inline-flex;align-items:center;gap:6px;font-size:12.5px;font-weight:700;color:' +
      status.color + ";background:" + status.background + ';padding:6px 11px;border-radius:8px;white-space:nowrap">' +
      '<span style="width:6px;height:6px;border-radius:50%;background:' + status.color + '"></span>' +
      escapeHtml(status.label) + "</span>";
  }

  function offerStatusControl(offer, canManage) {
    if (!canManage) return offerStatusPill(offer.status);
    return '<button type="button" data-offer-status="' + escapeHtml(offer.id) +
      '" title="Изменить статус" aria-label="Изменить статус оффера" ' +
      'style="display:inline-flex;align-items:center;gap:5px;border:0;background:transparent;padding:0;' +
      'cursor:pointer;font-family:inherit">' + offerStatusPill(offer.status) +
      '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" aria-hidden="true">' +
      '<path d="m7 10 5 5 5-5" stroke="#9B9292" stroke-width="2" stroke-linecap="round" ' +
      'stroke-linejoin="round"/></svg></button>';
  }

  function closeStatusMenu() {
    var menu = byId("offerStatusMenu");
    if (menu) menu.remove();
  }

  function findOffer(offerId) {
    return offersState.offers.find(function (entry) {
      return String(entry.id) === String(offerId);
    });
  }

  // Меню у кнопки, а если снизу не помещается — над ней.
  function anchorMenu(menu, button, height) {
    var box = button.getBoundingClientRect();
    var below = box.bottom + 6;
    var top = below + height > window.innerHeight - 8 ? box.top - height - 6 : below;
    menu.style.top = Math.max(8, top) + "px";
    menu.style.left = Math.max(8, Math.min(box.left, window.innerWidth - menu.offsetWidth - 8)) + "px";
  }

  function openStatusMenu(button, offerId) {
    var offer = findOffer(offerId);
    if (!offer) return;
    closeStatusMenu();
    var menu = document.createElement("div");
    menu.id = "offerStatusMenu";
    menu.style.cssText =
      "position:fixed;z-index:9998;width:196px;padding:6px;background:#fff;border:1px solid #EBE6E6;" +
      "border-radius:12px;box-shadow:0 16px 38px rgba(30,20,20,.18);font-family:Inter,sans-serif";
    menu.innerHTML = OFFER_STATUSES.map(function (status) {
      var active = status.value === offer.status;
      return '<button type="button" data-status-value="' + status.value + '" ' +
        'style="width:100%;display:flex;align-items:center;gap:9px;border:0;padding:9px 10px;border-radius:9px;' +
        "background:" + (active ? "#F7F4F4" : "transparent") + ";cursor:pointer;text-align:left;" +
        'font:700 12.5px Inter,sans-serif;color:' + status.color + '">' +
        '<span style="width:7px;height:7px;border-radius:50%;flex-shrink:0;background:' + status.color +
        '"></span>' + escapeHtml(status.label) +
        (active ? '<span style="margin-left:auto;color:#9B9292;font-weight:600">•</span>' : "") +
        "</button>";
    }).join("");
    document.body.appendChild(menu);
    anchorMenu(menu, button, menu.offsetHeight);
    menu.addEventListener("click", function (event) {
      var option = event.target.closest("[data-status-value]");
      if (!option) return;
      var next = option.getAttribute("data-status-value");
      closeStatusMenu();
      if (next === offer.status) return;
      saveOfferStatus(offer, next);
    });
  }

  function saveOfferStatus(offer, next) {
    var previous = offer.status;
    offer.status = next;
    renderOfferStats();
    renderOfferRows();
    api.patch("/offers/" + offer.id + "/status", { status: next })
      .then(function (updated) {
        offer.status = updated.status;
        toast("Статус оффера сохранён");
        renderOfferStats();
        renderOfferRows();
      })
      .catch(function (error) {
        offer.status = previous;
        renderOfferStats();
        renderOfferRows();
        fail(error);
      });
  }

  /* ----- row menu ----- */

  function closeRowMenu() {
    var menu = byId("offerRowMenu");
    if (menu) menu.remove();
    offersState.rowAnchor = null;
  }

  function openRowMenu(button, offerId) {
    var offer = findOffer(offerId);
    if (!offer) return;
    closeRowMenu();
    var menu = document.createElement("div");
    menu.id = "offerRowMenu";
    menu.style.cssText =
      "position:fixed;z-index:9998;width:186px;padding:6px;background:#fff;border:1px solid #EBE6E6;" +
      "border-radius:12px;box-shadow:0 16px 38px rgba(30,20,20,.18);font-family:Inter,sans-serif";
    var items = [{ action: "edit", label: "Редактировать" }];
    if (offersState.canManageCaps) items.push({ action: "cap", label: "Создать CapAlert" });
    items.push({ action: "delete", label: "Удалить", danger: true });
    menu.innerHTML = items.map(function (item) {
      return '<button type="button" data-row-action="' + item.action + '" ' +
        'style="width:100%;border:0;background:transparent;padding:9px 10px;border-radius:9px;text-align:left;' +
        'cursor:pointer;font:600 12.5px Inter,sans-serif;color:' +
        (item.danger ? "#C41616" : "#453A3A") + '">' + escapeHtml(item.label) + "</button>";
    }).join("");
    document.body.appendChild(menu);
    anchorMenu(menu, button, menu.offsetHeight);
    button.setAttribute("aria-expanded", "true");
    offersState.rowAnchor = button;
    menu.addEventListener("click", function (event) {
      var option = event.target.closest("[data-row-action]");
      if (!option) return;
      var action = option.getAttribute("data-row-action");
      closeRowMenu();
      if (action === "edit") openOfferForm(offer);
      if (action === "cap") openCapModal(offer);
      if (action === "delete") removeOffer(offer);
    });
  }

  function removeOffer(offer) {
    if (!window.confirm("Удалить оффер «" + offer.name + "»?")) return;
    api.delete("/offers/" + offer.id)
      .then(function () {
        toast("Оффер удалён");
        return loadOffers();
      })
      .catch(fail);
  }

  /* ----- star ----- */

  function starIcon(starred) {
    var path = "m12 3.4 2.7 5.6 6.1.9-4.4 4.3 1 6.1-5.4-2.9-5.4 2.9 1-6.1L3.2 9.9l6.1-.9L12 3.4Z";
    if (starred) {
      return '<svg width="16" height="16" viewBox="0 0 24 24" fill="#F5A524"><path d="' + path +
        '"/></svg>';
    }
    return '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="' + path +
      '" stroke="#CFC5C5" stroke-width="1.7" stroke-linejoin="round"/></svg>';
  }

  function starCell(offer, canManage) {
    var starred = !!offer.is_starred;
    var icon = starIcon(starred);
    if (!canManage) {
      return '<td style="padding:15px 0 15px 20px"><span style="display:flex;align-items:center;' +
        'justify-content:center;width:30px;height:30px">' + icon + "</span></td>";
    }
    var title = starred ? "Убрать из избранного" : "Добавить в избранное";
    return '<td style="padding:15px 0 15px 20px"><button type="button" data-star="' +
      escapeHtml(offer.id) + '" title="' + title + '" aria-label="' + title +
      '" aria-pressed="' + starred + '" style="display:flex;align-items:center;justify-content:center;' +
      'width:30px;height:30px;border:0;background:transparent;padding:0;cursor:pointer">' +
      icon + "</button></td>";
  }

  function toggleStar(offerId) {
    var offer = findOffer(offerId);
    if (!offer) return;
    var next = !offer.is_starred;
    offer.is_starred = next;
    renderOfferRows();
    api.patch("/offers/" + offer.id + "/star", { is_starred: next })
      .then(function (updated) { offer.is_starred = updated.is_starred; })
      .catch(function (error) {
        offer.is_starred = !next;
        renderOfferRows();
        fail(error);
      });
  }

  function renderStarSortState() {
    var button = byId("offersStarSort");
    if (!button) return;
    var active = offersState.starredFirst;
    button.setAttribute("aria-pressed", active ? "true" : "false");
    button.title = active ? "Обычный порядок" : "Сначала избранные";
    var icon = byId("offersStarSortIcon");
    if (icon) {
      icon.outerHTML = starIcon(active).replace("<svg ", '<svg id="offersStarSortIcon" ');
    }
    var arrow = byId("offersStarSortArrow");
    if (arrow) {
      var path = arrow.querySelector("path");
      if (path) path.setAttribute("stroke", active ? "#B91414" : "#C9BFBF");
    }
  }

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
    // Выбранное значение могло исчезнуть из справочника — тогда фильтр
    // молча остался бы включённым и прятал все строки.
    if (element.value !== current) element.value = "";
  }

  function offerMatchesFilters(offer) {
    var search = (byId("offerSearch") ? byId("offerSearch").value : "").trim().toLowerCase();
    var geo = byId("filterGeo") ? byId("filterGeo").value : "";
    var partner = byId("filterPartner") ? byId("filterPartner").value : "";
    var lead = byId("filterLead") ? byId("filterLead").value : "";
    var buyer = byId("filterBuyer") ? byId("filterBuyer").value : "";
    var status = byId("filterStatus") ? byId("filterStatus").value : "";
    if (search) {
      var haystack = (offer.name + " " + (offer.cap || "")).toLowerCase();
      if (haystack.indexOf(search) < 0) return false;
    }
    if (geo && offer.geo !== geo) return false;
    if (partner && offer.partner !== partner) return false;
    if (status && offer.status !== status) return false;
    if (lead && !hasPerson(offer.leads, lead)) return false;
    if (buyer && !hasPerson(offer.buyers, buyer)) return false;
    return true;
  }

  function hasPerson(list, id) {
    return (list || []).some(function (person) { return String(person.id) === id; });
  }

  function sortOffers(offers) {
    return offers.slice().sort(function (left, right) {
      if (offersState.starredFirst && !!left.is_starred !== !!right.is_starred) {
        return left.is_starred ? -1 : 1;
      }
      return String(left.name).localeCompare(String(right.name), "ru");
    });
  }

  function peopleCell(offer, kind, index) {
    var people = (kind === "leads" ? offer.leads : offer.buyers) || [];
    var avatars = people.slice(0, 3).map(function (person, i) {
      return '<span title="' + escapeHtml(person.name) + '" style="display:inline-flex;align-items:center;' +
        "justify-content:center;width:28px;height:28px;border-radius:50%;flex-shrink:0;margin-left:" +
        (i ? "-8px" : "0") + ";border:2px solid #fff;color:#fff;font-size:10px;font-weight:700;" +
        "font-family:Inter;background:" + avatarColor(index + i) + '">' +
        escapeHtml(initials(person.name)) + "</span>";
    }).join("");
    var extra = people.length > 3
      ? '<span style="font-size:11.5px;color:#6A6161;font-weight:600;margin-left:9px;white-space:nowrap">+' +
        (people.length - 3) + "</span>"
      : "";
    var empty = '<span style="font-size:12px;color:#9B9292;white-space:nowrap">Не назначены</span>';
    var button = offersState.canManage
      ? '<button type="button" data-assign="' + escapeHtml(offer.id) + '" data-assign-kind="' + kind +
        '" title="Назначить" aria-label="' + (kind === "leads" ? "Назначить тимлидов" : "Назначить баеров") +
        " для " + escapeHtml(offer.name) +
        '" style="flex-shrink:0;margin-left:10px;border:1px solid #EBE6E6;background:#fff;border-radius:9px;' +
        'height:30px;padding:0 10px;font:700 11.5px Inter,sans-serif;color:#B91414;cursor:pointer">' +
        (people.length ? "Изменить" : kind === "leads" ? "+ ТЛ" : "+ Баеры") + "</button>"
      : "";
    return '<td style="padding:15px 14px"><div style="display:flex;align-items:center">' +
      (people.length ? avatars + extra : empty) + button + "</div></td>";
  }

  function capCell(offer) {
    var count = Number(offer.caps_count || 0);
    var badge = count
      ? '<span title="Уже настроено правил CAP: ' + count + '" style="display:inline-flex;align-items:center;' +
        'gap:5px;font-size:11.5px;font-weight:700;color:#0E7350;background:#E4F7F0;padding:5px 9px;' +
        'border-radius:8px;white-space:nowrap">' + count + " CAP</span>"
      : "";
    if (!offersState.canManageCaps) {
      return '<td style="padding:15px 14px">' +
        (badge || '<span style="font-size:12px;color:#9B9292">—</span>') + "</td>";
    }
    return '<td style="padding:15px 14px"><div style="display:flex;align-items:center;gap:8px">' + badge +
      '<button type="button" data-cap="' + escapeHtml(offer.id) +
      '" aria-label="Создать CapAlert для ' + escapeHtml(offer.name) +
      '" style="flex-shrink:0;border:1px solid #EBE6E6;background:#fff;border-radius:9px;height:30px;' +
      'padding:0 11px;font:700 11.5px Inter,sans-serif;color:#B91414;cursor:pointer;white-space:nowrap">' +
      "+ CapAlert</button></div></td>";
  }

  function renderOfferRows() {
    var body = byId("offersTableBody");
    if (!body) return;
    var visible = sortOffers(offersState.offers.filter(offerMatchesFilters));
    if (!visible.length) {
      var message = offersState.offers.length
        ? "Офферы не найдены — измените фильтры"
        : "Офферов пока нет — заведите первый кнопкой «Новый оффер»";
      body.innerHTML = '<tr><td colspan="11" style="padding:44px 24px;text-align:center;color:#9B9292;font-size:13px">' +
        escapeHtml(message) + "</td></tr>";
    } else {
      body.innerHTML = visible.map(function (offer, index) {
        return '<tr class="offer-row" style="border-bottom:1px solid #F7F4F4">' +
          starCell(offer, offersState.canManage) +
          '<td style="padding:15px 24px"><button type="button" class="offer-open" ' +
          'data-offer-open="' + escapeHtml(offer.id) + '" aria-expanded="' +
          (offersState.opened[offer.id] ? "true" : "false") +
          '" aria-label="Показать KPI и комментарий оффера ' + escapeHtml(offer.name) + '">' +
          '<div style="width:40px;height:40px;border-radius:12px;background:#FCF1F1;color:#B91414;flex-shrink:0;' +
          'display:flex;align-items:center;justify-content:center;font-weight:800;font-size:12px;font-family:Inter">' +
          escapeHtml(initials(offer.name)) + '</div><div style="min-width:0;text-align:left">' +
          '<div style="font-weight:700;font-size:14px">' + escapeHtml(offer.name) + "</div></div>" +
          '<svg class="offer-open__arrow" width="14" height="14" viewBox="0 0 24 24" fill="none" ' +
          'aria-hidden="true"><path d="m7 10 5 5 5-5" stroke="#9B9292" stroke-width="2" ' +
          'stroke-linecap="round" stroke-linejoin="round"/></svg></button></td>' +
          '<td style="padding:15px 14px">' + escapeHtml(offer.partner || "—") + "</td>" +
          '<td style="padding:15px 14px;font-weight:700">' + escapeHtml(offer.geo || "—") + "</td>" +
          '<td style="padding:15px 14px;font-weight:700;white-space:nowrap">' +
          escapeHtml(cpaLabel(offer)) + "</td>" +
          '<td style="padding:15px 14px;font-weight:700' +
          (offer.cap ? "" : ";color:#9B9292") + '">' + escapeHtml(offer.cap || "—") + "</td>" +
          capCell(offer) +
          peopleCell(offer, "leads", index) +
          peopleCell(offer, "buyers", index + 2) +
          '<td style="padding:15px 14px">' + offerStatusControl(offer, offersState.canManage) + "</td>" +
          '<td class="offers-actions" style="padding:15px 14px">' + rowMenuCell(offer) + "</td></tr>" +
          detailsRow(offer);
      }).join("");
    }
    setText("offersResultCount", "Показано " + visible.length + " из " + offersState.offers.length);
  }

  /* KPI и комментарий раскрываются под строкой, а не занимают по столбцу.
     Это длинный текст: в таблице он либо обрезается до бессмысленного
     огрызка, либо растягивает строку на пол-экрана — а читают его редко и
     по одному офферу за раз. */
  /* Ставка в валюте партнёрки: с ней оффер и уезжает в книгу баера. */
  function cpaLabel(offer) {
    var value = Number(offer.cpa || 0);
    if (!value) return "—";
    var sign = offer.cpa_currency === "EUR" ? "€" : "$";
    return sign + " " + value.toLocaleString("ru-RU", {
      minimumFractionDigits: 0,
      maximumFractionDigits: 2
    });
  }

  function detailsRow(offer) {
    if (!offersState.opened[offer.id]) return "";
    var blocks = [
      { label: "KPI", value: offer.kpi },
      { label: "Комментарий", value: offer.comment }
    ].filter(function (block) { return String(block.value || "").trim(); });
    return '<tr class="offer-details"><td colspan="11" style="padding:0 24px 16px">' +
      '<div class="offer-details__card">' +
      (blocks.length
        ? blocks.map(function (block) {
          return '<div class="offer-details__block"><div class="offer-details__label">' +
            escapeHtml(block.label) + "</div><div class=\"offer-details__text\">" +
            escapeHtml(block.value) + "</div></div>";
        }).join("")
        : '<div class="offer-details__empty">KPI и комментарий не заполнены' +
          (offersState.canManage ? " — их можно добавить в карточке оффера." : ".") +
          "</div>") +
      "</div></td></tr>";
  }

  function toggleOfferDetails(offerId) {
    if (offersState.opened[offerId]) delete offersState.opened[offerId];
    else offersState.opened[offerId] = true;
    renderOfferRows();
  }

  function rowMenuCell(offer) {
    if (!offersState.canManage) return "";
    return '<button type="button" class="offer-more" data-row-menu="' + escapeHtml(offer.id) +
      '" aria-haspopup="true" aria-expanded="false" aria-label="Действия с оффером ' +
      escapeHtml(offer.name) + '"><svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" ' +
      'aria-hidden="true"><circle cx="5" cy="12" r="1.7"/><circle cx="12" cy="12" r="1.7"/>' +
      '<circle cx="19" cy="12" r="1.7"/></svg></button>';
  }

  function renderOfferStats() {
    var offers = offersState.offers;
    setText("offersTotal", number(offers.length));
    ["free", "active", "working"].forEach(function (value, index) {
      var id = ["offersFree", "offersAtLeads", "offersActive"][index];
      setText(id, number(offers.filter(function (o) { return o.status === value; }).length));
    });
  }

  function populateOfferFilters() {
    var partners = {};
    offersState.offers.forEach(function (offer) {
      if (offer.partner) partners[offer.partner] = true;
    });
    fillSelect("filterGeo", offersState.geos.map(function (g) {
      return { value: g, label: g };
    }), "Все GEO");
    fillSelect("filterPartner", Object.keys(partners).sort().map(function (p) {
      return { value: p, label: p };
    }), "Все партнёрки");
    var people = offersState.people.map(function (person) {
      return { value: String(person.id), label: person.name };
    });
    fillSelect("filterLead", people, "Все ТЛы");
    fillSelect("filterBuyer", people, "Все баеры");
    fillSelect("filterStatus", OFFER_STATUSES.map(function (status) {
      return { value: status.value, label: status.label };
    }), "Все статусы");
  }

  function applyOfferFilters() {
    renderOfferRows();
  }

  function bindOfferControls() {
    if (document.documentElement.dataset.offerControlsBound) return;
    document.documentElement.dataset.offerControlsBound = "true";
    var starSort = byId("offersStarSort");
    if (starSort) starSort.addEventListener("click", function () {
      offersState.starredFirst = !offersState.starredFirst;
      renderStarSortState();
      renderOfferRows();
    });
    var search = byId("offerSearch");
    if (search) search.addEventListener("input", applyOfferFilters);
    ["filterGeo", "filterPartner", "filterLead", "filterBuyer", "filterStatus"].forEach(function (id) {
      var element = byId(id);
      if (element) element.addEventListener("change", applyOfferFilters);
    });
    var reset = byId("resetOfferFilters");
    if (reset) reset.addEventListener("click", function () {
      ["offerSearch", "filterGeo", "filterPartner", "filterLead", "filterBuyer", "filterStatus"]
        .forEach(function (id) {
          var element = byId(id);
          if (element) element.value = "";
        });
      applyOfferFilters();
    });
    var create = byId("offerCreate");
    if (create) create.addEventListener("click", function () { openOfferForm(null); });
    document.addEventListener("click", function (event) {
      var assign = event.target.closest("[data-assign]");
      if (assign) {
        openAssignModal(assign.getAttribute("data-assign"), assign.getAttribute("data-assign-kind"));
        return;
      }
      var open = event.target.closest("[data-offer-open]");
      if (open) {
        toggleOfferDetails(open.getAttribute("data-offer-open"));
        return;
      }
      var cap = event.target.closest("[data-cap]");
      if (cap) {
        openCapModal(findOffer(cap.getAttribute("data-cap")));
        return;
      }
      var star = event.target.closest("[data-star]");
      if (star) {
        toggleStar(star.getAttribute("data-star"));
        return;
      }
      var rowMenu = event.target.closest("[data-row-menu]");
      if (rowMenu) {
        var openRow = byId("offerRowMenu");
        var sameRow = offersState.rowAnchor === rowMenu;
        closeRowMenu();
        if (!openRow || !sameRow) openRowMenu(rowMenu, rowMenu.getAttribute("data-row-menu"));
        return;
      }
      var statusButton = event.target.closest("[data-offer-status]");
      if (statusButton) {
        var open = byId("offerStatusMenu");
        var same = statusButton === offersState.statusAnchor;
        closeStatusMenu();
        // Clicking the same pill twice closes the menu instead of reopening it.
        if (!open || !same) {
          offersState.statusAnchor = statusButton;
          openStatusMenu(statusButton, statusButton.getAttribute("data-offer-status"));
        } else {
          offersState.statusAnchor = null;
        }
        return;
      }
      if (!event.target.closest("#offerStatusMenu")) closeStatusMenu();
      if (!event.target.closest("#offerRowMenu")) closeRowMenu();
    });
    document.addEventListener("keydown", function (event) {
      if (event.key !== "Escape") return;
      closeStatusMenu();
      closeRowMenu();
    });
    window.addEventListener("resize", function () { closeStatusMenu(); closeRowMenu(); });
    window.addEventListener("scroll", function () { closeStatusMenu(); closeRowMenu(); }, true);
  }

  /* ----- shared modal shell ----- */

  function openModal(id, title, subtitle, bodyHtml, saveLabel) {
    var existing = byId(id);
    if (existing) existing.remove();
    var overlay = document.createElement("div");
    overlay.id = id;
    overlay.style.cssText =
      "position:fixed;inset:0;z-index:9999;background:rgba(18,12,12,.45);display:flex;" +
      "align-items:center;justify-content:center;padding:24px;font-family:'Inter',sans-serif";
    overlay.innerHTML =
      '<div role="dialog" aria-modal="true" aria-label="' + escapeHtml(title) +
      '" style="background:#fff;border-radius:18px;max-width:520px;width:100%;max-height:calc(100vh - 48px);' +
      'display:flex;flex-direction:column;box-shadow:0 24px 70px rgba(18,12,12,.3)">' +
      '<div style="padding:22px 24px 16px;border-bottom:1px solid #EBE6E6">' +
      '<h2 style="font-family:Inter;font-size:19px;font-weight:700">' + escapeHtml(title) + "</h2>" +
      (subtitle
        ? '<div style="font-size:12px;color:#9B9292;margin-top:4px">' + escapeHtml(subtitle) + "</div>"
        : "") +
      "</div>" +
      '<div style="padding:16px 24px;overflow-y:auto">' + bodyHtml + "</div>" +
      '<div style="display:flex;justify-content:flex-end;gap:10px;padding:15px 24px 18px;border-top:1px solid #EBE6E6">' +
      '<button data-modal-cancel style="border:1px solid #EBE6E6;background:#fff;border-radius:10px;padding:10px 18px;' +
      'font:700 13px Inter,sans-serif;color:#6A6161;cursor:pointer">Отмена</button>' +
      '<button data-modal-save style="border:none;background:#B91414;color:#fff;border-radius:10px;padding:10px 22px;' +
      'font:700 13px Inter,sans-serif;cursor:pointer;box-shadow:0 8px 18px rgba(185,20,20,.28)">' +
      escapeHtml(saveLabel || "Сохранить") + "</button></div></div>";
    document.body.appendChild(overlay);
    overlay.addEventListener("click", function (event) {
      if (event.target === overlay) overlay.remove();
    });
    overlay.querySelector("[data-modal-cancel]").addEventListener("click", function () {
      overlay.remove();
    });
    overlay.addEventListener("keydown", function (event) {
      if (event.key === "Escape") overlay.remove();
    });
    var first = overlay.querySelector("input,select,textarea");
    if (first) first.focus();
    return overlay;
  }

  function onSave(overlay, handler) {
    var button = overlay.querySelector("[data-modal-save]");
    button.addEventListener("click", function () {
      var label = button.textContent;
      button.disabled = true;
      button.textContent = "Сохраняю…";
      Promise.resolve()
        .then(handler)
        .then(function () { overlay.remove(); })
        .catch(function (error) {
          button.disabled = false;
          button.textContent = label;
          fail(error);
        });
    });
  }

  function peopleChecklist(name, selectedIds) {
    var selected = {};
    (selectedIds || []).forEach(function (id) { selected[String(id)] = true; });
    if (!offersState.people.length) {
      return '<div style="color:#9B9292;font-size:13px;text-align:center;padding:20px">Нет доступных людей</div>';
    }
    return offersState.people.map(function (person, index) {
      var id = String(person.id);
      return '<label class="offer-pick">' +
        '<input type="checkbox" data-' + name + '="' + escapeHtml(id) + '"' +
        (selected[id] ? " checked" : "") +
        ' style="width:17px;height:17px;accent-color:#B91414;cursor:pointer">' +
        '<span style="display:inline-flex;align-items:center;justify-content:center;width:30px;height:30px;' +
        'border-radius:50%;color:#fff;font-size:11px;font-weight:700;font-family:Inter;background:' +
        avatarColor(index) + '">' + escapeHtml(initials(person.name)) + "</span>" +
        '<span><span style="display:block;font-weight:700;font-size:13px">' + escapeHtml(person.name) +
        '</span><span style="display:block;font-size:11px;color:#9B9292">@' + escapeHtml(person.login) +
        "</span></span></label>";
    }).join("");
  }

  function checkedValues(overlay, name) {
    return Array.prototype.slice.call(overlay.querySelectorAll("[data-" + name + "]:checked"))
      .map(function (input) { return input.getAttribute("data-" + name); });
  }

  /* ----- assign leads / buyers ----- */

  function openAssignModal(offerId, kind) {
    var offer = findOffer(offerId);
    if (!offer) return;
    var leads = kind === "leads";
    var current = (leads ? offer.leads : offer.buyers) || [];
    var overlay = openModal(
      "assignPeopleModal",
      leads ? "Назначить тимлидов" : "Назначить баеров",
      offer.name + (leads ? " · оффер станет «Активен»" : " · оффер уйдёт «В работу»"),
      peopleChecklist("person", current.map(function (person) { return person.id; }))
    );
    onSave(overlay, function () {
      var ids = checkedValues(overlay, "person");
      var path = "/offers/" + offer.id + (leads ? "/leads" : "/buyers");
      var payload = leads ? { lead_ids: ids } : { buyer_ids: ids };
      return api.put(path, payload).then(function () {
        toast(leads ? "Тимлиды обновлены" : "Баеры обновлены");
        return loadOffers();
      });
    });
  }

  /* ----- create / edit offer ----- */

  function activeCurrency() {
    var pressed = byId("offerFormCpaCurrency")
      .querySelector('[aria-pressed="true"]');
    return pressed && pressed.getAttribute("data-currency") === "EUR" ? "EUR" : "USD";
  }

  function selectOptions(values, current, placeholder) {
    var options = ['<option value="">' + escapeHtml(placeholder) + "</option>"];
    values.forEach(function (entry) {
      options.push('<option value="' + escapeHtml(entry.value) + '"' +
        (String(entry.value) === String(current || "") ? " selected" : "") + ">" +
        escapeHtml(entry.label) + "</option>");
    });
    return options.join("");
  }

  /* Ставку набирают руками, поэтому в поле — то, что человек и написал бы:
     «10», а не «10,0000». Хвост нулей от Decimal сюда не доезжает. */
  function moneyInput(value) {
    var amount = Number(value || 0);
    if (!amount) return "";
    return String(Math.round(amount * 10000) / 10000);
  }

  function currencyToggle(current) {
    return [{ code: "USD", sign: "$" }, { code: "EUR", sign: "€" }].map(function (item) {
      return '<button type="button" data-currency="' + item.code + '" aria-pressed="' +
        (String(current || "USD") === item.code ? "true" : "false") + '" title="' +
        item.code + '">' + item.sign + "</button>";
    }).join("");
  }

  function openOfferForm(offer) {
    var editing = !!offer;
    var body =
      '<label class="offer-label" for="offerFormName">Название</label>' +
      '<input id="offerFormName" class="offer-field" maxlength="240" value="' +
      escapeHtml(editing ? offer.name : "") + '" placeholder="Например, Nervio Forte">' +
      '<div class="offer-grid">' +
      '<div><label class="offer-label" for="offerFormCpa">CPA</label>' +
      '<div class="offer-money">' +
      '<input id="offerFormCpa" class="offer-field" inputmode="decimal" ' +
      'autocomplete="off" value="' +
      escapeHtml(editing ? moneyInput(offer.cpa) : "") + '" placeholder="0">' +
      '<span class="offer-cur" id="offerFormCpaCurrency" role="group" ' +
      'aria-label="Валюта ставки">' +
      currencyToggle(editing ? offer.cpa_currency : "USD") + "</span></div></div>" +
      '<div><label class="offer-label" for="offerFormCap">Капа</label>' +
      '<input id="offerFormCap" class="offer-field" maxlength="160" value="' +
      escapeHtml(editing ? (offer.cap || "") : "") + '" placeholder="300 FTD / день"></div>' +
      '</div>' +
      '<div class="offer-grid">' +
      '<div><label class="offer-label" for="offerFormGeo">GEO</label>' +
      '<select id="offerFormGeo" class="offer-field">' +
      selectOptions(offersState.geos.map(function (g) { return { value: g, label: g }; }),
        editing ? offer.geo : "", "Не выбрано") + "</select></div>" +
      '<div><label class="offer-label" for="offerFormPartner">Партнёрка</label>' +
      '<select id="offerFormPartner" class="offer-field">' +
      selectOptions(offersState.partners.map(function (p) {
        return { value: p.id, label: p.name };
      }), editing ? offer.partner_id : "", "Не выбрана") + "</select></div></div>" +
      '<div style="margin-top:14px"><label class="offer-label" for="offerFormKpi">KPI</label>' +
      '<textarea id="offerFormKpi" class="offer-field offer-area" maxlength="4000" ' +
      'placeholder="Например: FTD от 25$, апрув от 40%">' +
      escapeHtml(editing ? (offer.kpi || "") : "") + "</textarea></div>" +
      '<div style="margin-top:14px"><label class="offer-label" for="offerFormComment">Комментарий</label>' +
      '<textarea id="offerFormComment" class="offer-field offer-area" maxlength="4000" ' +
      'placeholder="Что важно знать по этому офферу">' +
      escapeHtml(editing ? (offer.comment || "") : "") + "</textarea></div>" +
      '<div style="margin-top:18px"><span class="offer-label">Тимлиды</span>' +
      peopleChecklist("lead", editing ? (offer.leads || []).map(function (p) { return p.id; }) : []) +
      "</div>" +
      '<div style="margin-top:14px"><span class="offer-label">Баеры</span>' +
      peopleChecklist("buyer", editing ? (offer.buyers || []).map(function (p) { return p.id; }) : []) +
      "</div>";
    var overlay = openModal(
      "offerFormModal",
      editing ? "Оффер" : "Новый оффер",
      editing ? offer.name : "Статус проставится сам: ТЛ — «Активен», баеры — «В работе»",
      body,
      editing ? "Сохранить" : "Создать"
    );
    // Валюта — переключатель из двух кнопок, а не select: вариантов ровно два,
    // и список ради них разворачивать незачем.
    byId("offerFormCpaCurrency").addEventListener("click", function (event) {
      var button = event.target.closest ? event.target.closest("[data-currency]") : null;
      if (!button) return;
      Array.prototype.forEach.call(
        byId("offerFormCpaCurrency").querySelectorAll("[data-currency]"),
        function (item) {
          item.setAttribute("aria-pressed", String(item === button));
        }
      );
    });
    onSave(overlay, function () {
      var name = byId("offerFormName").value.trim();
      if (!name) {
        byId("offerFormName").focus();
        throw new Error("Название оффера обязательно");
      }
      var payload = {
        name: name,
        cap: byId("offerFormCap").value.trim() || null,
        cpa: Number(String(byId("offerFormCpa").value).replace(",", ".")) || 0,
        cpa_currency: activeCurrency(),
        kpi: byId("offerFormKpi").value.trim() || null,
        comment: byId("offerFormComment").value.trim() || null,
        geo: byId("offerFormGeo").value || null,
        partner_id: byId("offerFormPartner").value || null,
        lead_ids: checkedValues(overlay, "lead"),
        buyer_ids: checkedValues(overlay, "buyer")
      };
      var request = editing
        ? api.put("/offers/" + offer.id, payload)
        : api.post("/offers", payload);
      return request.then(function () {
        toast(editing ? "Оффер сохранён" : "Оффер создан");
        return loadOffers();
      });
    });
  }

  /* ----- CapAlert ----- */

  /* ----- CapAlert ----- */

  // Форма общая с «Утилитами»: она умеет несколько офферов, таймзону, пороги
  // и правку уже существующих кап. Своя урезанная копия здесь однажды уже
  // разошлась с API и молча перестала работать.
  function openCapModal(offer) {
    if (!offer || !window.CelestialCap) return;
    window.CelestialCap.open({
      offer: offer,
      onSaved: function () {
        toast("CapAlert сохранён");
        return loadOffers();
      }
    });
  }

  async function loadOffers() {
    var results = await Promise.all([
      api.getAll(OFFERS_QUERY),
      api.get("/users/options"),
      api.get("/offers/reference")
    ]);
    offersState.offers = results[0].items || [];
    offersState.people = results[1] || [];
    offersState.geos = (results[2] || {}).geos || [];
    offersState.partners = (results[2] || {}).partners || [];
    populateOfferFilters();
    renderOfferStats();
    renderOfferRows();
  }

  async function initOffers(user) {
    offersState.user = user;
    offersState.canManage = hasPermission(user, "offers.manage");
    offersState.canManageCaps = hasPermission(user, "utilities.manage");
    var create = byId("offerCreate");
    if (create) create.style.display = offersState.canManage ? "inline-flex" : "none";
    bindOfferControls();
    renderStarSortState();
    await loadOffers();
  }

  window.CelestialCatalog = {
    initOffers: initOffers,
    reloadOffers: loadOffers
  };
})();
