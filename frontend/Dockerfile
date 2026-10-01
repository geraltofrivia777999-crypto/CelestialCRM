FROM nginx:1.27-alpine

COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY frontend/favicon.svg frontend/favicon-180.png frontend/api.js frontend/loading-ui.js frontend/navigation.js frontend/board-ui.js frontend/dashboard-ui.js frontend/catalog-ui.js frontend/finance-ui.js frontend/meta-ui.js frontend/meta-tree.js frontend/meta-geo-rules.js frontend/workspace-ui.js frontend/salary-ui.js frontend/utilities-ui.js frontend/cap-ui.js frontend/select-ui.js frontend/date-ui.js frontend/date-range-ui.js frontend/keitaro-ui.js frontend/recruitment-ui.js frontend/partner-ui.js frontend/responsive.css frontend/login.html /usr/share/nginx/html/
COPY frontend/assets/ /usr/share/nginx/html/assets/
COPY *.dc.html /tmp/celestial-pages/

# Макеты хранятся в корне как обычные HTML-файлы. На этапе сборки добавляем
# только общий API-клиент и навигацию — никакой компиляции и Node.js.
RUN set -eu; \
    for source in /tmp/celestial-pages/*.dc.html; do \
      page="$(basename "$source")"; \
      sed \
        -e 's#</head>#<link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="apple-touch-icon" href="/favicon-180.png"><link rel="stylesheet" href="/responsive.css?v=20260915-filter-panel"></head>#' \
        -e 's#<body>#<body><div id="celestialInitialLoader" class="celestial-initial-loader" role="status" aria-live="polite" aria-label="Загрузка актуальных данных"><div class="celestial-loader-card"><span class="celestial-loader-mark" aria-hidden="true"><span></span></span><span class="celestial-loader-copy"><strong>Celestial</strong><small>Загружаем актуальные данные…</small></span></div><span class="celestial-loader-progress" aria-hidden="true"><span></span></span></div>#' \
        -e 's#</body>#<script src="/api.js"></script><script src="/loading-ui.js"></script><script src="/navigation.js?v=20260924-metaads-v2-admin-fix"></script><script src="/board-ui.js?v=20261001-geo-spend"></script><script src="/dashboard-ui.js?v=20260918-offer-total"></script><script src="/catalog-ui.js?v=20260930-lead-caps"></script><script src="/finance-ui.js?v=20260930-blocked-users"></script><script src="/meta-ui.js?v=20261001-sync-state"></script><script src="/meta-tree.js?v=20261001-kt-names"></script><script src="/meta-geo-rules.js?v=20260930-rule-sets"></script><script src="/workspace-ui.js?v=20260919-no-date-hints"></script><script src="/salary-ui.js?v=20260909-trim-zeros2"></script><script src="/utilities-ui.js?v=20260924-cap-channels"></script><script src="/cap-ui.js?v=20260924-cap-no-thread"></script><script src="/keitaro-ui.js?v=20260927-geo-rules"></script><script src="/recruitment-ui.js?v=20260911-tg-file"></script><script src="/partner-ui.js?v=20260910-confirm"></script><script src="/select-ui.js?v=20260908-search"></script><script src="/date-ui.js"></script><script src="/date-range-ui.js?v=20260917-meta-period-fix"></script></body>#' \
        "$source" > "/usr/share/nginx/html/$page"; \
    done; \
    cp /usr/share/nginx/html/Dashboard.dc.html /usr/share/nginx/html/index.html; \
    rm -rf /tmp/celestial-pages

EXPOSE 80
