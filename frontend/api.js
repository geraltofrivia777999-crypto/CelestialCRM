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
    var payload = contentType.indexOf("application/json") >= 0
      ? await response.json()
      : await response.text();

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

  window.CelestialAPI = {
    get: function (path) {
      return request(path);
    },
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
