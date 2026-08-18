FROM nginx:1.27-alpine

COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY frontend/api.js frontend/loading-ui.js frontend/navigation.js frontend/board-ui.js frontend/dashboard-ui.js frontend/catalog-ui.js frontend/finance-ui.js frontend/meta-ui.js frontend/workspace-ui.js frontend/salary-ui.js frontend/utilities-ui.js frontend/cap-ui.js frontend/select-ui.js frontend/keitaro-ui.js frontend/responsive.css frontend/login.html /usr/share/nginx/html/
COPY frontend/assets/ /usr/share/nginx/html/assets/
COPY *.dc.html /tmp/celestial-pages/

# Макеты хранятся в корне как обычные HTML-файлы. На этапе сборки добавляем
# только общий API-клиент и навигацию — никакой компиляции и Node.js.
RUN set -eu; \
    for source in /tmp/celestial-pages/*.dc.html; do \
      page="$(basename "$source")"; \
      sed \
        -e 's#</head>#<link rel="stylesheet" href="/responsive.css"></head>#' \
        -e 's#<body>#<body><div id="celestialInitialLoader" class="celestial-initial-loader" role="status" aria-live="polite" aria-label="Загрузка актуальных данных"><div class="celestial-loader-card"><span class="celestial-loader-mark" aria-hidden="true"><span></span></span><span class="celestial-loader-copy"><strong>Celestial</strong><small>Загружаем актуальные данные…</small></span></div><span class="celestial-loader-progress" aria-hidden="true"><span></span></span></div>#' \
        -e 's#</body>#<script src="/api.js"></script><script src="/loading-ui.js"></script><script src="/navigation.js"></script><script src="/board-ui.js"></script><script src="/dashboard-ui.js"></script><script src="/catalog-ui.js"></script><script src="/finance-ui.js"></script><script src="/meta-ui.js"></script><script src="/workspace-ui.js"></script><script src="/salary-ui.js"></script><script src="/utilities-ui.js"></script><script src="/cap-ui.js"></script><script src="/keitaro-ui.js"></script><script src="/select-ui.js"></script></body>#' \
        "$source" > "/usr/share/nginx/html/$page"; \
    done; \
    cp /usr/share/nginx/html/Dashboard.dc.html /usr/share/nginx/html/index.html; \
    rm -rf /tmp/celestial-pages

EXPOSE 80
