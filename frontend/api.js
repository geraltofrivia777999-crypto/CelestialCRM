(function () {
  "use strict";

  var API_ROOT = "/api/v1";

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
      var fields = payload && payload.error && payload.error.details
        ? payload.error.details.fields
        : null;
      var message = fields && fields.length && fields[0].message
        ? fields[0].message.replace(/^Value error, /, "")
        : payload && payload.error && payload.error.message
          ? payload.error.message
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
    request: request
  };

  fetch("/api/health", { credentials: "include" })
    .then(function (response) { return response.ok ? response.json() : Promise.reject(); })
    .then(function () { document.documentElement.dataset.apiStatus = "online"; })
    .catch(function () { document.documentElement.dataset.apiStatus = "offline"; });
})();
