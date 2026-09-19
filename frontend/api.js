(function () {
  "use strict";

  var API_ROOT = "/api/v1";
  var CRM_TIMEZONE = "Europe/Moscow";

  function moscowParts(value) {
    var date = value instanceof Date ? value : new Date(value == null ? Date.now() : value);
    var parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: CRM_TIMEZONE,
      year: "numeric",
      month: "2-digit",
      day: "2-digit"
    }).formatToParts(date);
    var values = {};
    parts.forEach(function (part) { values[part.type] = part.value; });
    return {
      year: Number(values.year),
      month: Number(values.month),
      day: Number(values.day)
    };
  }

  function moscowToday() {
    var parts = moscowParts();
    // Календарные виджеты работают с Date через локальные getters. Объект
    // синтетический: важна московская дата, а не соответствующий ей timestamp.
    return new Date(parts.year, parts.month - 1, parts.day);
  }

  function dateISO(date) {
    return date.getFullYear() + "-" + String(date.getMonth() + 1).padStart(2, "0") +
      "-" + String(date.getDate()).padStart(2, "0");
  }

  window.CelestialTime = {
    timezone: CRM_TIMEZONE,
    today: moscowToday,
    todayISO: function () { return dateISO(moscowToday()); },
    dateISO: dateISO,
    format: function (value, options) {
      var date = value instanceof Date ? value : new Date(value);
      return new Intl.DateTimeFormat(
        "ru-RU",
        Object.assign({}, options || {}, { timeZone: CRM_TIMEZONE })
      ).format(date);
    },
    localInputISO: function (value) {
      if (!value) return null;
      var text = String(value);
      if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(text)) text += ":00";
      var parsed = new Date(text + "+03:00");
      return isNaN(parsed.getTime()) ? null : parsed.toISOString();
    },
    nextMidnightISO: function () {
      var parts = moscowParts();
      return new Date(Date.UTC(parts.year, parts.month - 1, parts.day + 1, -3)).toISOString();
    }
  };

  async function request(path, options) {
    var config = Object.assign(
      {
        credentials: "include",
        headers: { "Content-Type": "application/json" }
      },
      options || {}
    );

    if (config.body instanceof FormData) {
      delete config.headers["Content-Type"];
    }

    var response = await fetch(API_ROOT + path, config);
    var contentType = response.headers.get("content-type") || "";
    var payload = null;

    // 204/205 carry no body, and an empty body must never be fed to JSON.parse —
    // it throws before the response.ok check and turns a success into an error.
    if (response.status !== 204 && response.status !== 205) {
      var raw = await response.text();
      if (raw) {
        if (contentType.indexOf("application/json") >= 0) {
          try {
            payload = JSON.parse(raw);
          } catch (parseError) {
            payload = raw;
          }
        } else {
          payload = raw;
        }
      }
    }

    if (!response.ok) {
      // A dead session must not leave a cached user behind: the next page would treat
      // the tab as signed in and render the shell before the redirect to /login.
      if (response.status === 401 && window.CelestialSession) {
        window.CelestialSession.clear();
      }
      var fields = payload && payload.error && payload.error.details
        ? payload.error.details.fields
        : null;
      var message = fields && fields.length && fields[0].message
        ? fields[0].message.replace(/^Value error, /, "")
        : payload && payload.error && payload.error.message
          ? payload.error.message
          : payload && typeof payload.detail === "string"
            ? payload.detail
            : response.status === 413
              ? "Файл слишком большой. Максимальный размер — 90 МБ"
              : "Ошибка запроса";
      var error = new Error(message);
      error.status = response.status;
      error.payload = payload;
      throw error;
    }

    return payload;
  }

  // Follows a paginated endpoint to the end. The server caps `limit`, so asking for
  // more than the cap silently truncated the list before this existed.
  async function getAll(path, pageSize) {
    var size = pageSize || 200;
    var separator = path.indexOf("?") >= 0 ? "&" : "?";
    var items = [];
    var total = 0;
    var offset = 0;

    for (var guard = 0; guard < 100; guard += 1) {
      var page = await request(path + separator + "limit=" + size + "&offset=" + offset);
      var batch = page.items || [];
      items = items.concat(batch);
      total = page.total || 0;
      if (!batch.length || items.length >= total) break;
      offset += batch.length;
    }

    return { items: items, total: total, limit: items.length, offset: 0 };
  }

  /*
   * The pages are separate documents, so every in-app navigation re-runs the whole
   * start-up: `/auth/me`, then the shell, then the module. Keeping the signed-in user
   * for the tab lets a second page render its chrome and fire its data request straight
   * away, with `/auth/me` revalidating in the background. Only identity and permissions
   * live here — never numbers, and the server still authorises every request.
   */
  var SESSION_KEY = "celestial.session.user";

  var session = {
    read: function () {
      try {
        var raw = window.sessionStorage.getItem(SESSION_KEY);
        if (!raw) return null;
        var parsed = JSON.parse(raw);
        return parsed && parsed.role ? parsed : null;
      } catch (error) {
        return null;
      }
    },
    write: function (user) {
      try {
        if (user) window.sessionStorage.setItem(SESSION_KEY, JSON.stringify(user));
      } catch (error) { /* private mode, or storage is full */ }
    },
    clear: function () {
      try {
        window.sessionStorage.removeItem(SESSION_KEY);
      } catch (error) { /* nothing cached to drop */ }
    }
  };

  window.CelestialSession = session;

  window.CelestialAPI = {
    get: function (path) {
      return request(path);
    },
    getAll: getAll,
    post: function (path, data, idempotencyKey) {
      var headers = { "Content-Type": "application/json" };
      if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
      return request(path, {
        method: "POST",
        headers: headers,
        body: JSON.stringify(data)
      });
    },
    patch: function (path, data) {
      return request(path, {
        method: "PATCH",
        body: JSON.stringify(data)
      });
    },
    put: function (path, data) {
      return request(path, {
        method: "PUT",
        body: JSON.stringify(data)
      });
    },
    delete: function (path) {
      return request(path, { method: "DELETE" });
    },
    // Multipart: браузер сам проставит boundary, поэтому Content-Type задавать нельзя.
    upload: function (path, formData) {
      return request(path, { method: "POST", headers: {}, body: formData });
    },
    request: request
  };

  fetch("/api/health", { credentials: "include" })
    .then(function (response) { return response.ok ? response.json() : Promise.reject(); })
    .then(function () { document.documentElement.dataset.apiStatus = "online"; })
    .catch(function () { document.documentElement.dataset.apiStatus = "offline"; });
})();
