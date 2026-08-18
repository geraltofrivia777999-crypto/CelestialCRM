(function () {
  "use strict";

  var root = document.documentElement;
  var loader = document.getElementById("celestialInitialLoader");
  var application = loader ? loader.nextElementSibling : null;
  var startedAt = Date.now();
  var minimumVisibleMs = 180;
  var transitionMs = 180;
  var finished = false;
  var watchdog = null;

  /*
   * The curtain sits in the markup from the first painted frame, which is the only
   * thing that keeps the baked-in design mock-up off the screen: the pages are parsed
   * and painted progressively, so the browser can show the demo rows well before the
   * scripts at the end of <body> get to blank them. On a 66 KB page over a VPS link
   * that window is long enough to read.
   *
   * What differs between a cold start and an in-app navigation is *when* the curtain
   * may go. Cold: after the data lands, because there is no application frame to show
   * yet. Warm: as soon as the shell has blanked the mock-up and drawn itself from the
   * stored session — that costs no requests, so the page appears at once with "loading"
   * placeholders instead of holding a full-screen curtain for the whole round trip.
   */
  var warm = !!(window.CelestialSession && window.CelestialSession.read());

  root.classList.add("celestial-data-loading");
  if (application) {
    application.setAttribute("aria-hidden", "true");
    application.setAttribute("inert", "");
  }
  if (warm) {
    root.dataset.initialDataState = "warm";
    minimumVisibleMs = 0;
  }

  function removeLoader() {
    root.classList.remove("celestial-data-loading");
    if (root.dataset.initialDataState !== "warm") root.dataset.initialDataState = "ready";
    if (application) {
      application.removeAttribute("aria-hidden");
      application.removeAttribute("inert");
    }
    if (!loader) return;
    loader.classList.add("is-leaving");
    window.setTimeout(function () {
      if (loader && loader.parentNode) loader.parentNode.removeChild(loader);
    }, transitionMs);
  }

  function ready() {
    if (finished) return;
    finished = true;
    if (watchdog) window.clearTimeout(watchdog);
    var remaining = Math.max(0, minimumVisibleMs - (Date.now() - startedAt));
    window.setTimeout(removeLoader, remaining);
  }

  /* The shell has replaced every mock-up value with a placeholder and drawn itself.
   * On a warm navigation that is the moment the page is safe to show — waiting for the
   * data as well would put the curtain back on every click. */
  function shellReady() {
    if (warm) ready();
  }

  function showTimeout() {
    if (finished || !loader) return;
    root.dataset.initialDataState = "timeout";
    loader.classList.add("has-error");
    loader.setAttribute("aria-label", "Не удалось загрузить актуальные данные");
    var message = loader.querySelector(".celestial-loader-copy small");
    if (message) message.textContent = "Не удалось получить актуальные данные";
    var card = loader.querySelector(".celestial-loader-card");
    if (!card || card.querySelector(".celestial-loader-retry")) return;
    var retry = document.createElement("button");
    retry.type = "button";
    retry.className = "celestial-loader-retry";
    retry.textContent = "Повторить";
    retry.addEventListener("click", function () { window.location.reload(); });
    card.appendChild(retry);
  }

  // Never reveal the baked-in design mock-up on a timeout. Keep it covered and
  // offer a retry; a late successful response can still call ready() normally.
  if (loader) watchdog = window.setTimeout(showTimeout, 20000);

  window.CelestialLoader = {
    ready: ready,
    shellReady: shellReady
  };
})();
