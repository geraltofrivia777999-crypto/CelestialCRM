# Celestial CRM

Рабочий MVP CRM для арбитражной команды. Исходные `*.dc.html` в корне — это
одновременно визуальные макеты и production-страницы. Docker собирает из них
обычный статический HTML без frontend-фреймворка и без Node.js.

## Стек

- HTML + CSS + обычный JavaScript
- FastAPI + SQLAlchemy 2 + PostgreSQL
- Celery + Redis для Keitaro и долгих операций
- Alembic для миграций
- Docker Compose + Nginx

## Локальный запуск

1. Скопируйте `.env.example` в `.env`.
2. Задайте надёжные `SECRET_KEY`, `FIELD_ENCRYPTION_KEY`, `ADMIN_PASSWORD`.
3. Запустите:

```bash
docker compose up --build
```

CRM будет доступна на `http://localhost:8080`, OpenAPI — на
`http://localhost:8080/api/docs`.

Перед включением Keitaro создайте подключение в «Настройки → Интеграции», выполните
ручной backfill и сверьте дневные клики, конверсии и Revenue. Затем установите
`KEITARO_SYNC_ENABLED=true`.

### Подключение Keitaro

В форме нужен адрес установленного трекера, например
`https://tracker.example.com`. Адрес документации `admin-api.docs.keitaro.io`
не является адресом трекера и отклоняется при сохранении.

После проверки соединения CRM автоматически запускает backfill за 90 дней.
Дальнейшая синхронизация использует перекрывающее окно, идемпотентный upsert и
checkpoint. В Медиаборд попадают:

- `campaign_unique_clicks` → INST;
- `leads` → REG;
- `sales` → FTD;
- `revenue` → Revenue;
- `cost` → рассчитанный SPEND.

Баер определяется по логину в `Sub ID`, номер которого настраивается у
подключения; резервный способ — совпадение группы кампаний с группой пользователя.
API-ключ хранится в базе только в зашифрованном виде и не возвращается через API.

## Финансовый импорт

CSV/XLSX должен содержать колонки:

`date,buyer_login,offer_external_id,link,rent,spend,qual,revenue,salary`

Повторный импорт с тем же `date + buyer + offer + link` обновляет запись.

## Проверки

```bash
cd backend
pip install -e ".[dev]"
ruff check app tests
pytest

docker compose build frontend
```

## Production

- Используйте внешний TLS reverse proxy или добавьте сертификаты в Nginx.
- Установите `COOKIE_SECURE=true`.
- Делайте ежедневный backup PostgreSQL и регулярно проверяйте восстановление.
- Не храните `.env` и API-ключи в Git.
- Перед миграциями создавайте backup и используйте версионированные Docker-образы.

Backup и проверка восстановления:

```bash
./infra/backup.sh
CONFIRM_RESTORE=celestial ./infra/restore.sh backups/celestial-YYYYMMDDTHHMMSSZ.sql.gz
```

Staging использует те же образы и сервисы:

```bash
docker compose --env-file .env.staging -f docker-compose.yml -f docker-compose.staging.yml up --build
```
