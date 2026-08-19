FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# Xvfb + x11vnc: браузерные сессии Meta Ads (токен EAAB) запускаются headful,
# а при капче/чекпойнте оператор входит вручную через VNC (порт 5900).
RUN apt-get update && apt-get install -y --no-install-recommends \
    xvfb x11vnc xauth \
    wget curl ca-certificates \
    fonts-liberation fonts-noto-color-emoji \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Нативный Chromium под архитектуру хоста (amd64 или arm64) + системные
# зависимости браузера. Канал "chrome" из настроек откатывается на этот
# Chromium автоматически, если реального Chrome в образе нет.
RUN playwright install --with-deps chromium

COPY alembic.ini .
COPY alembic ./alembic
COPY app ./app

# DISPLAY :99 — виртуальный экран Xvfb для headful-браузера.
ENV DISPLAY=:99

EXPOSE 8000 5900
