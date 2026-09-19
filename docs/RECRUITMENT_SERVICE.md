# Рекрутинг: устройство и текущая работа сервиса

Дата сверки: **8 сентября 2026 года**. Документ описывает существующий код, а не план развития. Проверены исходники на сервере `203.161.60.118`, конфигурация запуска и фактическая схема PostgreSQL Recruitment Service. Текущая ревизия этой базы — **`0005`**.

Значения паролей, внутренних ключей и OAuth-токенов в документ не включены. Названия переменных приведены для навигации по конфигурации.

## 1. Где что находится

Рекрутинг состоит из двух приложений с отдельными базами:

| Компонент | Ответственность | Расположение на сервере |
|---|---|---|
| CRM, интерфейс | Вкладки, формы шаблонов, список откликов, оценка, действия HR, доска найма | `/opt/celestial-crm/frontend/recruitment-ui.js`, `/opt/celestial-crm/Recruitment.dc.html` |
| CRM, API | Проверка прав пользователя, проксирование в сервис, аудит действий, воронка и записи интервью | `/opt/celestial-crm/backend/app/api/routers/recruitment.py` |
| Recruitment API | CRUD шаблонов, запуски, кандидаты, HH OAuth, список вакансий, приём Telegram | `/opt/RecruitmentService/app/api/` |
| Recruitment worker | Планирование и выполнение поиска резюме и проверки откликов | `/opt/RecruitmentService/app/worker.py` |
| PostgreSQL сервиса | Шаблоны, критерии, запуски, найденные кандидаты, оценки, подключения HH | Контейнер `recruitmentservice-recruitment-postgres-1` |
| PostgreSQL CRM | Рабочие пространства, пользователи, карточки воронки, вложения, аудит | Контейнер `celestial-crm-postgres-1` |

Схема обмена:

```text
Браузер HR
  → CRM /api/v1/recruitment/*
    → Recruitment API /search-templates, /external-candidates, ...
      → PostgreSQL Recruitment Service

Recruitment worker → HH API → PostgreSQL Recruitment Service

Действие «Добавить в кандидаты»
  → review_status=added в сервисе
  → recruitment_candidates в PostgreSQL CRM
```

Сервис не отправляет кандидатов в CRM вебхуком. CRM сама читает его API. Браузер не получает `INTERNAL_SERVICE_TOKEN` и не обращается к базе сервиса напрямую.

Внутри контейнеров пути отличаются: Recruitment Service работает из **`/srv`**, CRM backend — из **`/app`**. Поэтому `app/worker.py` сервиса находится в контейнере по `/srv/app/worker.py`.

## 2. Навигация по логике

В таблице пути `app/...` относятся к `/opt/RecruitmentService`, а `backend/...` и `frontend/...` — к `/opt/celestial-crm`.

| Файл | Главные функции / объекты | За что отвечает |
|---|---|---|
| `app/main.py` | FastAPI `app` | Подключение маршрутов, жизненный цикл приложения |
| `app/config.py` | `Settings`, `settings` | Переменные окружения сервиса |
| `app/db/base.py` | `engine`, `SessionLocal`, `get_db`, `UUIDPKMixin`, `TimestampMixin` | Соединение с БД, сессии, общие поля моделей |
| `app/api/search_templates.py` | `create_template`, `update_template`, `run_template` | API шаблонов; ручной запуск создаёт запись очереди |
| `app/repositories/search_templates.py` | `_validate_template`, `_check_duplicate`, `list_search_templates` | Совместимость типа и настроек; сохранение; вычисление счётчика кандидатов |
| `app/schemas/search_template.py` | `SearchTemplateCreate/Update/Out`, `CriterionIn` | Формат запросов и ответов, допустимые интервалы и веса |
| `app/worker.py` | `schedule_due_templates`, `check_negotiations_for_due_templates`, `_poll_loop`, `process_one_run` | Планировщики и исполнитель общей очереди |
| `app/repositories/search_runs.py` | `create_search_run`, `claim_next_queued`, `mark_completed`, `mark_failed`, `reset_to_queued` | Состояния запуска и захват задания |
| `app/services/search_execution.py` | `execute_search_run`, `_process_hh_resume` | Выбор одного источника, нормализация, дедупликация, оценка, статистика |
| `app/providers/hh/search.py` | `build_search_params`, `_map_single_value`, `_build_text_query` | Преобразование критериев в параметры поиска HH |
| `app/providers/hh/resumes.py` | `iter_all_resumes`, `search_resumes`, `get_resume` | Пагинация резюме; отдельный метод полного резюме |
| `app/providers/hh/negotiations.py` | `iter_negotiation_resumes` | Получение новых откликов по ID вакансии |
| `app/providers/hh/normalize.py` | `normalize_resume` | Преобразование ответа HH в единый профиль |
| `app/providers/base.py` | `ParsedProfile` | Общая структура профиля для оценки |
| `app/providers/hh/client.py` | `HHClient`, `_retry_delay` | HTTP, авторизация, обработка ошибок HH и повторы |
| `app/providers/hh/auth.py` | `build_authorize_url`, `exchange_code_for_token`, `refresh_access_token` | OAuth-функции; наличие функции обновления не означает автоматическое обновление токена |
| `app/api/providers_hh.py` | `hh_status`, `hh_connect`, `hh_callback`, `hh_vacancies` | Подключение HH и список доступных вакансий |
| `app/repositories/candidates.py` | `get_or_create_candidate`, `upsert_candidate_score`, `list_candidates`, `set_review_status` | Общая карточка кандидата, история каналов, оценка и разбор HR |
| `app/scoring/engine.py` | `score_candidate`, `_match`, `_normalize_score`, `_tier_for_score` | Расчёт оценки и соответствия критериям |
| `app/services/telegram_intake.py` | `handle_telegram_application` | Приём Telegram-заявки и оценка по связанным шаблонам |
| `app/logging_config.py` | `log_event`, `JsonFormatter`, `_redact` | Структурированные события в stdout |
| `backend/app/services/recruitment.py` | `RecruitmentClient`, `_request` | Внутренний HTTP-клиент CRM → сервис |
| `backend/app/api/routers/recruitment.py` | `review_candidate`, `pipeline_board`, маршруты `pipeline/*` | Права, аудит, связь разбора с воронкой, файлы интервью |
| `backend/app/services/recruitment_pipeline.py` | `snapshot`, `upsert_from_external`, `sync_added`, `board` | Локальная карточка найма и её снимок профиля |
| `frontend/recruitment-ui.js` | `state`, `renderSearch`, `renderResponses`, `editorPayload`, `loadIncoming`, `candidateCard` | Разделение экранов, формы и отображение данных |

Локальные файлы CRM: [клиент сервиса](/Users/adilkhan/PycharmProjects/CelestialCRM/backend/app/services/recruitment.py), [роутер](/Users/adilkhan/PycharmProjects/CelestialCRM/backend/app/api/routers/recruitment.py), [воронка](/Users/adilkhan/PycharmProjects/CelestialCRM/backend/app/services/recruitment_pipeline.py), [интерфейс](/Users/adilkhan/PycharmProjects/CelestialCRM/frontend/recruitment-ui.js), [модели](/Users/adilkhan/PycharmProjects/CelestialCRM/backend/app/models.py).

## 3. Шаблоны поиска и откликов

Физически оба типа хранятся в одной таблице **`search_templates`**. Разделение задаёт поле **`template_type`**. Название API `/search-templates` сохранено для обоих типов.

| Поле | `search` — поиск резюме | `responses` — отклики на вакансию |
|---|---|---|
| `template_type` | `search` | `responses` |
| `hh_vacancy_id` | Не задаётся; API отклоняет непустое значение | Обязательный числовой ID вакансии HH |
| `criteria` | Используются для запроса резюме и последующей оценки | Только для оценки уже полученных откликов |
| `auto_search_enabled` | Управляет автопоиском | Должен быть `false`; не управляет сбором откликов |
| `interval_minutes` | Интервал автопоиска | Не используется планировщиком откликов |
| `is_active` | Разрешает выполнение шаблона | Включает/приостанавливает сбор |
| Результат | Резюме из `/resumes` | Встроенные резюме из `/negotiations/response` |

Правила реализованы в `_validate_template()` и `update_search_template()`:

- Сменить тип уже созданного шаблона через API нельзя.
- Для одной вакансии проверяется наличие другого шаблона `responses`, в том числе приостановленного. Эта проверка находится в коде; уникального ограничения БД на `hh_vacancy_id` нет.
- Частичное обновление объединяется с существующими значениями перед проверкой. `is_active=false` не очищает привязку к HH.
- `criteria=[]` очищает критерии. При обновлении `criteria=None` оставляет их без изменений.
- `crm_vacancy_id` — отдельная непрозрачная ссылка/ID для CRM и Telegram-сопоставления. Это **не** `hh_vacancy_id` и не внешний ключ на таблицу вакансий.
- Интерфейс требует хотя бы один критерий для нового поиска. Сам API допускает пустой поисковый шаблон; исполнитель пропустит поиск при пустом `criteria`.

Выбор вакансии проходит через `hh_vacancies()`: `/me` → `employer.id` → `/employers/{id}/vacancies/active` с `per_page=20` и обходом страниц. В браузер возвращаются только `id` и `name`. Уже подключённые вакансии недоступны для повторного выбора в форме.

### Что изменилось миграцией `0005`

- Добавлено `search_templates.template_type`, по умолчанию `search`.
- Старые шаблоны с непустым `hh_vacancy_id` переведены в `responses`, их `auto_search_enabled` выключен.
- Добавлены `candidate_sources.seen_search_at` и `seen_response_at`.
- История заполнена по старому `via`; старые HH-источники без `via` отмечены как найденные поиском.

Миграция не создаёт шаблоны для вакансий сама: подключения вакансий создаются отдельно через прикладную логику.

## 4. Планировщики и очередь

Все три цикла живут в одном процессе `python -m app.worker`, запускаются через `asyncio.gather()`.

| Переменная в `app/worker.py` | Значение | Назначение |
|---|---:|---|
| `POLL_INTERVAL_SECONDS` | `5.0` | Пауза проверки очереди, если заданий нет |
| `SCHEDULER_INTERVAL_SECONDS` | `30.0` | Частота проверки расписания поиска |
| `NEGOTIATIONS_POLL_INTERVAL_SECONDS` | `60.0` | Частота постановки проверок откликов |
| `DEFAULT_INTERVAL_MINUTES` | `60` | Запасной интервал автопоиска |

Это константы Python, а не параметры `.env`. Разрешённые значения `ALLOWED_INTERVAL_MINUTES` в схеме: `15, 30, 60, 120, 360, 720, 1440`.

### Поиск по расписанию

`schedule_due_templates()` выбирает активные `search` с `auto_search_enabled=true`. Если `next_run_at` наступил или ещё не задан и `_has_pending_run()` не нашёл `queued/running`, создаётся запуск с `trigger=scheduled`. Затем `next_run_at` устанавливается в `now + interval_minutes`.

### Отклики

`check_negotiations_for_due_templates()` сначала проверяет наличие токена подключённого HH-аккаунта. Затем выбирает активные `responses` с ID вакансии. Если для шаблона нет `queued/running`, создаёт `search_runs` со статусом `queued`.

Функция **не скачивает отклики сама**, несмотря на старое описание внутри её docstring. Скачивание выполняется общим исполнителем очереди.

### Выполнение

1. `claim_next_queued()` берёт старейшее задание через `SELECT ... FOR UPDATE SKIP LOCKED`.
2. Выставляет `status=running`, `started_at=now` и фиксирует транзакцию.
3. `process_one_run()` загружает шаблон с критериями, проверяет `is_active` и подключение HH.
4. `execute_search_run()` выбирает строго один путь по `template_type`.
5. Успех записывает `completed`, `finished_at`, `stats`, обновляет `last_run_at` и `last_success_at`, очищает `last_error`.
6. Ошибка записывает `failed`, `error_message`, а в основном обработчике выполнения — ещё и `template.last_error`.

`search_runs.trigger` различает **ручной/плановый запуск**, а не поиск/отклики. Чтобы определить источник, нужно присоединить `search_templates.template_type`.

Один worker выполняет задания последовательно. «Каждую минуту» — период постановки проверки, а не гарантия доставки за 60 секунд: длительный поиск, очередь и HTTP-повторы могут увеличить задержку.

При штатном SIGTERM/SIGINT текущая задача отменяется и возвращается в `queued`. После аварийного завершения процесса автоматического освобождения оставшихся `running` по таймауту нет. Такой запуск блокирует следующие проверки своего шаблона через `_has_pending_run()`.

Ручной endpoint `/search-templates/{id}/run` сразу добавляет задание; отдельной проверки существующего запуска в этом endpoint нет. Блокировка при захвате защищает одно задание от двух исполнителей, но не делает два разных ручных задания одним.

## 5. Как загружаются данные HH

### Поиск резюме

`build_search_params()` преобразует критерии в параметры HH. Структурированные обязательные критерии могут стать `experience`, `employment_form`, `work_format`, `job_search_status`, `area`, `professional_role`, `salary_from`, `salary_to`, `language`.

Примеры соответствий в коде:

| Ключ критерия | Пример значения | Параметр HH |
|---|---|---|
| `experience_level` | `1_3_years` | `experience=between1And3` |
| `employment_type` | `full` | `employment_form=FULL` |
| `work_format` | `remote` | `work_format=REMOTE` |
| `geo_area_id` | Числовая строка | `area` |
| `professional_role_id` | Числовая строка | `professional_role` |
| `language` | `eng:b2` | `language=eng.b2` |

Нераспознанные обязательные критерии попадают в полнотекстовый запрос. `split_alternatives()` разбирает значения с `|`; альтернативы объединяются через OR.

**Нюанс реализации:** все `preferred` сейчас добавляются в `preferred_terms`, даже если поле структурированное. `_build_text_query()` соединяет обязательную часть и OR-группу предпочтений через AND. Поэтому предпочтения способны повлиять на то, какие резюме HH вообще вернёт. Комментарий о том, что структурированные предпочтения полностью исключены из поискового запроса, не соответствует телу `build_search_params()`.

`iter_all_resumes()` использует `MAX_PER_PAGE=100`, `MAX_RESULT_DEPTH=2000`. Это ограничения, зафиксированные в клиенте; документ не утверждает актуальные внешние лимиты HH сверх проверенного кода.

Рабочий путь использует сокращённые объекты из выдачи `/resumes`. Функция `get_resume()` существует, но исполнитель не вызывает её для каждого кандидата.

### Отклики на вакансии

`iter_negotiation_resumes()` читает только **`RESPONSE_COLLECTION="response"`**:

```http
GET /negotiations/response?vacancy_id=<hh_vacancy_id>&page=0&per_page=50
```

`MAX_PER_PAGE=50`. Обход заканчивается по `pages` или пустому `items`. Встроенное `item.resume` дополняется `_negotiation` с полями `id`, `state`, `created_at`.

Отдельной таблицы событий откликов нет. `_negotiation` находится в текущем `external_candidates.raw_data`. При очередной загрузке снимок может обновиться.

Коллекции приглашений, интервью, отказов и другие этапы HH не загружаются. Поэтому сумма счётчиков HH по вакансии может быть больше количества кандидатов, полученных этим сервисом. Отклик, который переместили из `response` до первой проверки, этим путём не будет обнаружен.

Загрузка выполняет GET-запросы; она не отправляет кандидатам сообщения и не переводит их между этапами HH.

## 6. Нормализация, дедупликация и разбор

`normalize_resume()` возвращает `ParsedProfile`: `full_name`, `position_title`, `total_experience_months`, `geo`, `employment_type`, `work_format`, `salary_expectation`, `salary_currency`, `job_search_status`, `languages`, `skills`, `last_experience_ended_months_ago`, `text_blob`.

Отсутствующие поля сокращённого резюме остаются пустыми. `text_blob` используется для проверки ключевых слов.

### Идентичность кандидата

`get_or_create_candidate()` ищет источник по **`(source, external_id)`**. Это же сочетание защищено уникальным ограничением `uq_candidate_sources_source_external_id`.

- Для HH `external_id` — ID резюме.
- Для Telegram — строковое представление `telegram_user_id`.
- `external_candidates.id` — внутренний UUID карточки сервиса.
- `recruitment_candidates.external_id` в CRM — **этот UUID сервиса**, а не ID резюме HH.
- Автоматического объединения HH и Telegram по имени, телефону или почте нет.

При повторной встрече обновляются `raw_data`, `parsed_profile`, `last_seen_at` и URL источника. Снимки всех прежних версий резюме не архивируются.

### История каналов

| Поле `candidate_sources` | Смысл |
|---|---|
| `source` | Провайдер: `hh` или `telegram` |
| `via` | Последний зарегистрированный канал: `search` / `negotiation` |
| `seen_search_at` | Первая зафиксированная встреча через поиск |
| `seen_response_at` | Первая зафиксированная встреча через отклик |

Фильтр `via` в `list_candidates()` проверяет соответствующее поле истории **или** старое значение `via`. Поэтому новый поиск не удаляет кандидата из списка откликов. При этом это история наличия двух каналов, а не полный журнал всех откликов на каждую вакансию.

Связь с конкретным шаблоном отражается через `candidate_scores.search_template_id`. Один кандидат может иметь несколько оценок для разных вакансий.

### Решение HR

`review_status` хранится на общем кандидате:

```text
pending → added
pending → skipped
added/skipped → pending — явная отмена решения
```

Первый отклик кандидата, ранее пропущенного в поиске, переводит `skipped` обратно в `pending`. Повторная проверка уже известного отклика не отменяет решение HR. `added` автоматически не сбрасывается.

Решение общее для кандидата, а не отдельное по каждому шаблону. Поэтому в текущей модели нет независимого `review_status` для двух разных вакансий одного человека.

## 7. Оценка кандидатов

`score_candidate()` применяется после сохранения кандидата. Оценка хранится отдельно для пары **`(external_candidate_id, search_template_id)`**; повторный расчёт обновляет существующую запись.

| Режим `criterion.mode` | Поведение |
|---|---|
| `ignore` | Критерий пропускается |
| `required` | Меняет `hard_filters_passed`; напрямую не добавляет баллы |
| `preferred` | Добавляет `weight × match_fraction` к сумме |

```text
score = round(clamp(100 × Σ(weight × match_fraction) / Σ(weight), 0, 100))
```

Если сумма весов предпочтений равна нулю, оценка — **0**, даже если обязательные критерии выполнены. При отсутствии критериев это нормальный результат, а не ошибка импорта.

Пороговые значения `DEFAULT_SCORE_THRESHOLDS`: `low=0`, `medium=55`, `high=75`, `hot=90`. Поле `search_templates.score_thresholds` может их переопределить. Подробности проверки сохраняются в `candidate_scores.breakdown`.

`required` с несовпадением выставляет `hard_filters_passed=false`, но `_process_hh_resume()` уже сохранил карточку и оценку. API списка не отбрасывает такие записи автоматически. **Низкая оценка и невыполненный критерий не скрывают входящий отклик.**

`search_runs.stats` содержит:

| Ключ | Значение |
|---|---|
| `found` | Число обработанных объектов с непустым ID в этом запуске |
| `new` | Создано новых общих карточек кандидатов |
| `known` | Обновлено уже известных карточек |
| `passed_hard_filters` | Прошли все обязательные критерии |
| `above_threshold` | Прошли обязательные критерии и достигли порога `medium` |

При повторной проверке те же отклики снова учитываются в `found/known`. Эти числа нельзя суммировать по запускам как количество уникальных откликов.

`candidate_count` в ответе списка шаблонов — вычисляемое количество строк `candidate_scores` для шаблона. В `search_templates` такой колонки нет. Это число оценённых кандидатов за историю шаблона, а не число новых или неразобранных откликов. Детальный GET одного шаблона отдельно этот счётчик не вычисляет и может вернуть значение схемы по умолчанию `0`.

## 8. Telegram

`POST /telegram/applications` принимает `telegram_user_id`, необязательные `telegram_full_name`, `telegram_username`, `vacancy_ref`, `candidate_text`, `resume_file_ref`.

`handle_telegram_application()` нормализует профиль, создаёт/обновляет общего кандидата, добавляет строку `telegram_applications` и оценивает его по шаблонам, у которых `crm_vacancy_id == vacancy_ref`.

Текущий запрос сопоставления Telegram не фильтрует шаблоны по `template_type` или `is_active`. `resume_file_ref` — ссылка/идентификатор, а не бинарная загрузка.

Реализация создаёт `telegram_applications.sync_status=pending` и далее не переводит его в `synced`. Нельзя по этому полю делать вывод, что кандидат не сохранён: общая карточка создаётся раньше.

В коде сервиса нет реализации Telegram-бота. Наличие endpoint и таблицы не доказывает, что внешний бот подключён; это нужно отдельно проверять на стороне отправителя. Старые комментарии «никто не вызывает endpoint» также не являются доказательством текущего состояния внешнего бота.

## 9. CRM: экраны, воронка, файлы

В `frontend/recruitment-ui.js` объект `state` разделяет `templates`, `pending`, `incoming`, `board`, `editor`, `vacancies`, `responseTemplate`.

- `renderSearch()` показывает только шаблоны, у которых тип не `responses`.
- `loadPending()` запрашивает `source=hh&via=search&review_status=pending`, максимум 200 строк за запрос.
- `renderResponses()` показывает подключения вакансий и входящие заявки.
- `allIncoming()` читает страницы по 200 строк до короткой страницы; для HH используется `via=negotiation`, Telegram загружается отдельно.
- `responseTemplate` фильтрует отклики выбранной вакансии по `scores.search_template_id`.
- `candidateCard()` выбирает оценку, относящуюся к текущему виду шаблонов; на откликах учитывает выбранную вакансию.
- Обновление открытой вкладки откликов — `setInterval(..., 60000)`, только при видимом документе, закрытом редакторе и отсутствии текущей загрузки.
- Ручной запуск отслеживается `pollRun()` с паузой 3000 мс.

### Воронка

`review_candidate()` сначала сохраняет решение в Recruitment Service. При `added` вызывает `upsert_from_external()` в CRM; при других решениях убирает локальную карточку через `drop_external()`.

Этапы задаются в `recruitment_pipeline.py`:

```text
STAGES = screening, interview, offer, hired, rejected
CLOSED_STAGES = hired, rejected
```

Новая карточка получает `screening`. Повторное добавление обновляет снимок профиля, но сохраняет уже выбранный этап. `snapshot()` использует лучшую оценку из полученных оценок кандидата.

`GET /pipeline` читает из сервиса до 200 кандидатов с `review_status=added` и через `sync_added()` добавляет отсутствующие карточки. В этом пути нет обхода следующих страниц; уже существующие карточки `sync_added()` пропускает. При недоступности сервиса доска продолжает работать на данных CRM, выставляя `service_available=false`.

Обновление решения в сервисе и сохранение карточки CRM — две отдельные транзакции, общей транзакции между базами нет. Это важно при разборе частично выполненного действия.

### Таблица CRM `recruitment_candidates`

Модель — `backend/app/models.py::RecruitmentCandidate`.

| Поля | Назначение |
|---|---|
| `id`, `created_at`, `updated_at` | Идентификатор и служебные даты |
| `workspace_id` | FK на `workspaces`; ограничивает видимость карточки |
| `external_id` (`varchar(64)`) | UUID кандидата Recruitment Service в строковом виде; межбазового FK нет |
| `stage`, `stage_changed_at` | Этап найма и дата его изменения |
| `position_title`, `geo`, `source`, `tier`, `score` | Снимок профиля и оценки |
| `salary_expectation` (`numeric(18,2)`), `experience_months` | Ожидания и стаж |
| `external_url` | Ссылка на внешнее резюме/профиль |
| `owner_id`, `added_by_id` | FK на `users`: ответственный и добавивший |
| `note` | Заметка HR |
| `target_position` | На какую позицию рассматривают; отличается от должности в резюме |
| `telegram_contact` | Контакт, введённый командой |
| `interview_record` | URL или ссылка на загруженную запись интервью |

Уникальность: `UNIQUE(workspace_id, external_id)`. Индекс: `(workspace_id, stage)`. Этап — строка, допустимые значения проверяются в CRM API.

Записи интервью загружает `POST /pipeline/{row_id}/record`: используются общие `KnowledgeAttachment`, `attachments` и `storage`, с привязкой вложения к `candidate_id`. Выдача через `/records/{attachment_id}` проверяет workspace и наличие связи с кандидатом. Это механизм CRM, не Recruitment Service.

## 10. Таблицы Recruitment Service

Ниже — логическое назначение. Точная выгрузка типов, NULL, DEFAULT, enum, индексов и внешних ключей приложена в [schema-2026-09-08.sql](/Users/adilkhan/PycharmProjects/CelestialCRM/docs/recruitment/schema-2026-09-08.sql). Это **только схема**, без данных кандидатов и токенов.

| Таблица | Назначение и ключевые связи |
|---|---|
| `provider_accounts` | Подключения HH: `provider`, `label`, `status`, `connected_at`, `meta` |
| `provider_tokens` | Токены аккаунта; FK `provider_account_id → provider_accounts.id`, CASCADE |
| `search_templates` | Настройки обоих типов, расписание, последнее состояние |
| `search_template_criteria` | `key/value/mode/weight`; FK на шаблон, CASCADE |
| `search_runs` | Очередь и история выполнений; FK на шаблон, CASCADE |
| `external_candidates` | Общий профиль, сырые данные, решение HR |
| `candidate_sources` | Внешняя идентичность и история каналов; FK на кандидата, CASCADE |
| `candidate_scores` | Оценка кандидата по шаблону; оба FK с CASCADE |
| `telegram_applications` | Записи поступивших Telegram-заявок; FK на кандидата с SET NULL |
| `integration_logs` | Таблица для событий существует, но текущий `log_event()` в неё не пишет |
| `alembic_version` | Текущая ревизия структуры базы |

Справочники PostgreSQL:

| Enum | Значения |
|---|---|
| `provider_type` | `hh` |
| `provider_account_status` | `connected`, `disconnected`, `error` |
| `source_type` | `hh`, `telegram` |
| `discovery_channel` | `search`, `negotiation` |
| `review_status` | `pending`, `added`, `skipped` |
| `criterion_mode` | `required`, `preferred`, `ignore` |
| `score_tier` | `low`, `medium`, `high`, `hot` |
| `search_run_status` | `queued`, `running`, `completed`, `failed` |
| `search_run_trigger` | `manual`, `scheduled` |
| `telegram_sync_status` | `pending`, `synced`, `failed` |

`template_type` — `varchar(20)`, **не PostgreSQL enum**. Ограничение `search/responses` задано в Pydantic. UUID `id` создаёт приложение через `uuid.uuid4`; отсутствие DEFAULT для UUID в DDL нормально.

В фактической базе JSON-поля имеют тип **JSONB**, хотя ORM-модели используют общий SQLAlchemy `JSON`. Для ручного SQL следует ориентироваться на приложенный DDL.

## 11. HTTP-контракт

Пути первой колонки имеют префикс CRM **`/api/v1/recruitment`**. Вторая колонка — пути внутреннего сервиса без этого префикса.

| CRM | Recruitment Service | Назначение |
|---|---|---|
| `GET/POST /search-templates` | `GET/POST /search-templates` | Список/создание шаблонов обоих типов |
| `PUT/DELETE /search-templates/{id}` | Те же пути | Изменение/удаление |
| `POST /search-templates/{id}/run` | Тот же путь | Поставить ручное задание; `202` и `search_run_id` |
| `GET /search-runs`, `GET /search-runs/{id}` | Те же пути | История и состояние |
| `GET /candidates` | `GET /external-candidates` | Список кандидатов |
| `PATCH /candidates/{id}/review` | `PATCH /external-candidates/{id}/review` | Решение `pending/added/skipped` |
| `GET /hh/status` | `GET /providers/hh/status` | Сохранённое состояние подключения |
| `GET /hh/vacancies` | `GET /providers/hh/vacancies` | Активные вакансии доступного работодателя |
| `POST /hh/connect` | `POST /providers/hh/connect` | Получить OAuth-ссылку |
| `GET/PATCH/DELETE /pipeline...` | Не проксируется | Доска и карточки в CRM |
| `POST /pipeline/{id}/record`, `GET /records/{id}` | Не проксируется | Записи интервью |

`GET /external-candidates`: фильтры `source`, `via`, `search_template_id`, `min_score`, `review_status`, `limit` (1–200), `offset`. `via=negotiation` — канал отклика; это не значение `template_type`.

Напрямую в сервисе также есть `GET /search-templates/{id}`, `GET /external-candidates/{id}`, `POST /telegram/applications`. OAuth callback доступен через отдельный публичный путь **`/providers/hh/callback`**, который nginx проксирует в Recruitment API.

В CRM маршруты проверяют `require_permission("recruitment.view")`, включая изменения. Внутренние маршруты защищены `require_service_token`; callback намеренно не требует внутренний Bearer-токен.

`RecruitmentClient._request()` имеет таймаут 20 секунд. Ошибки сервиса `401/403` превращаются в сообщение об отклонённом внутреннем токене. Другие HTTP-ошибки используют `detail` сервиса. `_call()` возвращает браузеру `502` с `error.message`.

## 12. Конфигурация, HH OAuth и HTTP-ошибки

| Переменная | Где | Роль |
|---|---|---|
| `INTERNAL_SERVICE_TOKEN` | Recruitment `.env`, `app/config.py` | Bearer-секрет внутреннего API |
| `DATABASE_URL` | Recruitment `.env` | Подключение сервиса к своей PostgreSQL |
| `HH_CLIENT_ID`, `HH_CLIENT_SECRET`, `HH_REDIRECT_URI` | Recruitment `.env` | Параметры приложения HH и callback |
| `ENV`, `LOG_LEVEL` | Recruitment `.env` | Режим и уровень логов; default `development` / `INFO` |
| `RECRUITMENT_API_BASE` → `settings.recruitment_api_base` | CRM `.env`, `backend/app/core/config.py` | По умолчанию `http://recruitment-api:8000` |
| `RECRUITMENT_SERVICE_TOKEN` → `settings.recruitment_service_token` | CRM `.env` | Должен соответствовать `INTERNAL_SERVICE_TOKEN` сервиса |
| `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB` | Compose Recruitment | Параметры контейнера PostgreSQL |
| `CELESTIAL_NETWORK_NAME` | Compose Recruitment | Имя общей внешней Docker-сети CRM |

`hh_connect()` формирует ссылку авторизации. `hh_callback()` обменивает `code` на токены, ставит аккаунту `connected`, создаёт строку `provider_tokens` и рассчитывает `expires_at` по полученному `expires_in`.

`get_connected_hh_access_token()` выбирает последнюю по `created_at` запись токена среди подключённых HH-аккаунтов. Шаблон не имеет собственного FK на HH-аккаунт: выбор подключения общий. `hh_status()` проверяет сохранённый статус в БД, а не работоспособность токена запросом к HH.

`refresh_access_token()` определена, но из рабочего пути её никто не вызывает. `expires_at` при выборе токена не проверяется. При проблеме авторизации нельзя полагаться только на зелёный статус подключения.

Токены сейчас хранятся в `provider_tokens` как текст; автоматического шифрования этих полей в коде нет. В OAuth-ссылке генерируется `state`, но callback его не проверяет. Это существующие ограничения, а не реализованные меры защиты.

Параметры `HHClient`:

- `HH_API_BASE_URL="https://api.hh.ru"`.
- Заголовок `Authorization: Bearer ...`; `User-Agent="CelestialGroup-RecruitmentService/1.0"`.
- Таймаут одного HTTP-запроса — 30 секунд.
- `max_retries=3`: до четырёх попыток при `429` и `5xx`.
- `_MAX_BACKOFF_SECONDS=30.0` ограничивает собственную экспоненциальную задержку; числовой `Retry-After` используется отдельно.
- `401` → `HHAuthError`; `403` → `HHAccessDeniedError`. Эти ошибки автоматически не повторяются клиентом.
- Текст ошибки `403` предполагает проблему тарифа, но сам по себе не доказывает её: код не разбирает точную причину отказа HH.

## 13. Развёртывание и диагностика

Стек Recruitment описан в `/opt/RecruitmentService/docker-compose.yml`:

- `recruitment-api`: `uvicorn app.main:app --host 0.0.0.0 --port 8000`.
- `recruitment-worker`: `python -m app.worker`.
- `recruitment-postgres`: PostgreSQL 16, постоянный том.
- API подключён к внутренней сети и общей сети CRM; worker — к внутренней.
- Команда запуска API сама не выполняет Alembic. При обновлении структуры нужно отдельно применять миграции до запуска кода, который требует новые поля.

Миграции: `0001` — базовые таблицы, `0002` — разбор кандидатов, `0003` — ID вакансии HH, `0004` — канал `via`, `0005` — типы шаблонов и история обоих каналов.

Логи смотреть в Docker stdout. `log_event()` выводит JSON с `event_type` и `context`; полезные события: `SEARCH_STARTED`, `SEARCH_COMPLETED`, `SEARCH_FAILED`, `CANDIDATE_FOUND`, `CANDIDATE_DUPLICATE`, `HH_REQUEST_RETRY`, `HH_REQUEST_FAILED`, `SCHEDULER_TICK_FAILED`, `NEGOTIATIONS_LOOP_TICK_FAILED`.

Таблица `integration_logs` не заполняется этой функцией, хотя комментарий модели утверждает обратное. Редактирование секретов в `_redact()` применяется к ключам верхнего уровня `context`; не стоит считать его очисткой произвольного вложенного JSON или текста ошибки.

### Безопасные проверки без изменения данных

На сервере:

```bash
docker ps --filter name=recruitment
docker logs --since 30m recruitmentservice-recruitment-worker-1
docker logs --since 30m recruitmentservice-recruitment-api-1
docker exec recruitmentservice-recruitment-api-1 alembic current
```

В PostgreSQL сервиса:

```sql
-- Что настроено и когда успешно проверялось.
SELECT id, name, template_type, hh_vacancy_id, is_active,
       auto_search_enabled, interval_minutes, last_success_at, last_error
FROM search_templates
ORDER BY created_at DESC;

-- Последние запуски с расшифровкой типа шаблона.
SELECT r.id, t.name, t.template_type, r.status, r.trigger,
       r.started_at, r.finished_at, r.stats, r.error_message
FROM search_runs r
JOIN search_templates t ON t.id = r.search_template_id
ORDER BY r.created_at DESC
LIMIT 30;

-- Незавершённые задания, которые могут задерживать следующие проверки.
SELECT id, search_template_id, status, created_at, started_at
FROM search_runs
WHERE status IN ('queued', 'running')
ORDER BY created_at;

-- Наличие истории каналов: один человек может учитываться в обеих колонках.
SELECT count(*) FILTER (WHERE seen_search_at IS NOT NULL) AS seen_in_search,
       count(*) FILTER (WHERE seen_response_at IS NOT NULL) AS seen_in_responses
FROM candidate_sources
WHERE source = 'hh';

-- Состояние подключения без чтения самих токенов.
SELECT a.status, a.connected_at, t.created_at AS token_created_at, t.expires_at
FROM provider_accounts a
LEFT JOIN provider_tokens t ON t.provider_account_id = a.id
ORDER BY t.created_at DESC;
```

### Если отклики не приходят

1. Проверить `template_type=responses`, непустой `hh_vacancy_id`, `is_active=true`.
2. Проверить подключение HH; сохранённое `connected` не исключает истёкший/отозванный токен.
3. Посмотреть `queued/running`: зависший запуск не даст планировщику создать следующий.
4. Посмотреть `last_success_at`, `last_error` и `search_runs.error_message`.
5. Если выполнение успешно, проверить, есть ли у HH кандидаты именно в коллекции `response`.
6. Если кандидаты есть в базе, проверить `review_status`, историю `seen_response_at`, фильтр вакансии и фильтр источника в интерфейсе.
7. Если API выдаёт `UndefinedColumnError`, сверить фактическую схему с `alembic current`, а не повторять загрузку HH.

## 14. Что важно не перепутать

- `search_templates` — таблица двух типов шаблонов, несмотря на название.
- `template_type=responses` и `via=negotiation` — разные поля разных сущностей.
- `crm_vacancy_id`, `hh_vacancy_id`, HH ID резюме и UUID кандидата сервиса — четыре разных идентификатора.
- `review_status` — решение по общей находке; `stage` — этап найма в CRM.
- Ноль баллов при пустых критериях не означает, что кандидат отклонён.
- Счётчик кандидатов шаблона, `stats.found` запуска и число неразобранных на экране измеряют разные вещи.
- Наличие функции, enum или таблицы в коде не означает, что она подключена к рабочему потоку: примеры — refresh токена, `integration_logs`, `telegram_applications.sync_status`.

## Приложение A. Фактические колонки таблиц сервиса

Следующие определения извлечены из PostgreSQL при подготовке документа. Внешние ключи, уникальные ограничения и индексы полностью приведены в приложенном SQL-файле.

### `alembic_version`

```sql
CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);
```

### `candidate_scores`

```sql
CREATE TABLE public.candidate_scores (
    id uuid NOT NULL,
    external_candidate_id uuid NOT NULL,
    search_template_id uuid NOT NULL,
    score integer NOT NULL,
    tier public.score_tier NOT NULL,
    breakdown jsonb,
    hard_filters_passed boolean NOT NULL,
    computed_at timestamp with time zone DEFAULT now() NOT NULL
);
```

### `candidate_sources`

```sql
CREATE TABLE public.candidate_sources (
    id uuid NOT NULL,
    external_candidate_id uuid NOT NULL,
    source public.source_type NOT NULL,
    external_id character varying(255) NOT NULL,
    external_url character varying(1000),
    first_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    via public.discovery_channel,
    seen_search_at timestamp with time zone,
    seen_response_at timestamp with time zone
);
```

### `external_candidates`

```sql
CREATE TABLE public.external_candidates (
    id uuid NOT NULL,
    first_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    raw_data jsonb,
    parsed_profile jsonb,
    crm_candidate_id character varying(255),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    review_status public.review_status DEFAULT 'pending'::public.review_status NOT NULL,
    reviewed_at timestamp with time zone,
    reviewed_by character varying(255)
);
```

### `integration_logs`

```sql
CREATE TABLE public.integration_logs (
    id uuid NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    event_type character varying(100) NOT NULL,
    level character varying(20) DEFAULT 'info'::character varying NOT NULL,
    source character varying(50),
    message text,
    context jsonb
);
```

### `provider_accounts`

```sql
CREATE TABLE public.provider_accounts (
    id uuid NOT NULL,
    provider public.provider_type DEFAULT 'hh'::public.provider_type NOT NULL,
    label character varying(255),
    status public.provider_account_status DEFAULT 'disconnected'::public.provider_account_status NOT NULL,
    connected_at timestamp with time zone,
    meta jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);
```

### `provider_tokens`

```sql
CREATE TABLE public.provider_tokens (
    id uuid NOT NULL,
    provider_account_id uuid NOT NULL,
    access_token text NOT NULL,
    refresh_token text,
    expires_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);
```

### `search_runs`

```sql
CREATE TABLE public.search_runs (
    id uuid NOT NULL,
    search_template_id uuid NOT NULL,
    trigger public.search_run_trigger NOT NULL,
    status public.search_run_status DEFAULT 'queued'::public.search_run_status NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    stats jsonb,
    error_message text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);
```

### `search_template_criteria`

```sql
CREATE TABLE public.search_template_criteria (
    id uuid NOT NULL,
    search_template_id uuid NOT NULL,
    key character varying(100) NOT NULL,
    value character varying(500) NOT NULL,
    mode public.criterion_mode NOT NULL,
    weight integer DEFAULT 0 NOT NULL
);
```

### `search_templates`

```sql
CREATE TABLE public.search_templates (
    id uuid NOT NULL,
    name character varying(255) NOT NULL,
    crm_vacancy_id character varying(255),
    is_active boolean DEFAULT true NOT NULL,
    auto_search_enabled boolean DEFAULT false NOT NULL,
    interval_minutes integer,
    score_thresholds jsonb,
    last_run_at timestamp with time zone,
    next_run_at timestamp with time zone,
    last_success_at timestamp with time zone,
    last_error text,
    created_by character varying(255),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    hh_vacancy_id character varying(255),
    template_type character varying(20) DEFAULT 'search'::character varying NOT NULL
);
```

### `telegram_applications`

```sql
CREATE TABLE public.telegram_applications (
    id uuid NOT NULL,
    telegram_user_id bigint NOT NULL,
    vacancy_ref character varying(255),
    candidate_text text,
    resume_file_ref character varying(500),
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    external_candidate_id uuid,
    sync_status public.telegram_sync_status DEFAULT 'pending'::public.telegram_sync_status NOT NULL
);
```

## Приложение B. Точки входа в серверном коде

Номера строк соответствуют исходникам, прочитанным с сервера 8 сентября 2026 года. После изменений они могут сдвинуться; имя функции остаётся ориентиром для поиска. Указанные пути находятся на сервере.

| Символ | Файл и строка |
|---|---|
| `POLL_INTERVAL_SECONDS` | `/opt/RecruitmentService/app/worker.py:28` |
| `get_connected_hh_access_token` | `/opt/RecruitmentService/app/worker.py:42` |
| `process_one_run` | `/opt/RecruitmentService/app/worker.py:57` |
| `schedule_due_templates` | `/opt/RecruitmentService/app/worker.py:110` |
| `check_negotiations_for_due_templates` | `/opt/RecruitmentService/app/worker.py:140` |
| `_poll_loop` | `/opt/RecruitmentService/app/worker.py:194` |
| `execute_search_run` | `/opt/RecruitmentService/app/services/search_execution.py:28` |
| `_process_hh_resume` | `/opt/RecruitmentService/app/services/search_execution.py:73` |
| `_validate_template` | `/opt/RecruitmentService/app/repositories/search_templates.py:93` |
| `_check_duplicate` | `/opt/RecruitmentService/app/repositories/search_templates.py:105` |
| `update_search_template` | `/opt/RecruitmentService/app/repositories/search_templates.py:55` |
| `get_or_create_candidate` | `/opt/RecruitmentService/app/repositories/candidates.py:18` |
| `list_candidates` | `/opt/RecruitmentService/app/repositories/candidates.py:157` |
| `set_review_status` | `/opt/RecruitmentService/app/repositories/candidates.py:136` |
| `build_search_params` | `/opt/RecruitmentService/app/providers/hh/search.py:78` |
| `iter_negotiation_resumes` | `/opt/RecruitmentService/app/providers/hh/negotiations.py:43` |
| `DEFAULT_SCORE_THRESHOLDS` | `/opt/RecruitmentService/app/scoring/engine.py:31` |
| `score_candidate` | `/opt/RecruitmentService/app/scoring/engine.py:58` |
| `handle_telegram_application` | `/opt/RecruitmentService/app/services/telegram_intake.py:30` |
| `Settings` | `/opt/RecruitmentService/app/config.py:6` |
