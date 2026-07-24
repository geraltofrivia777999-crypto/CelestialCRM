FROM nginx:1.27-alpine

COPY frontend/nginx.conf /etc/nginx/conf.d/default.conf
COPY frontend/api.js frontend/navigation.js frontend/board-ui.js frontend/dashboard-ui.js frontend/catalog-ui.js frontend/keitaro-ui.js frontend/responsive.css frontend/login.html /usr/share/nginx/html/
COPY *.dc.html /tmp/celestial-pages/

# Макеты хранятся в корне как обычные HTML-файлы. На этапе сборки добавляем
# только общий API-клиент и навигацию — никакой компиляции и Node.js.
RUN set -eu; \
    for source in /tmp/celestial-pages/*.dc.html; do \
      page="$(basename "$source")"; \
      sed \
        -e 's#</head>#<link rel="stylesheet" href="/responsive.css"></head>#' \
        -e 's#</body>#<script src="/api.js"></script><script src="/navigation.js"></script><script src="/board-ui.js"></script><script src="/dashboard-ui.js"></script><script src="/catalog-ui.js"></script><script src="/keitaro-ui.js"></script></body>#' \
        "$source" > "/usr/share/nginx/html/$page"; \
    done; \
    cp /usr/share/nginx/html/Dashboard.dc.html /usr/share/nginx/html/index.html; \
    rm -rf /tmp/celestial-pages

EXPOSE 80
