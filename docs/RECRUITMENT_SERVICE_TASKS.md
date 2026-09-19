# ТЗ на правки Recruitment Service

Дата: **8 сентября 2026 года**. Документ описывает три изменения на стороне `/opt/RecruitmentService`, без которых соответствующие правки в CRM сделать нельзя: нужных данных просто нет в ответе API.

Устройство сервиса и текущее поведение — в [RECRUITMENT_SERVICE.md](RECRUITMENT_SERVICE.md). Здесь только то, что меняется, и как проверить результат.

## Общий принцип

CRM читает кандидатов через `GET /external-candidates` и берёт данные **только** из `parsed_profile`, `sources` и `scores`. Поля, которые сервис хранит в других таблицах (`telegram_applications`) или во внутреннем `raw_data`, в интерфейс попасть не могут.

Отсюда правило для всех трёх задач: **если поле должно быть видно в CRM, оно должно оказаться в `parsed_profile`**.

Совместимость: все новые ключи необязательные. CRM переживает их отсутствие — карточка просто не покажет строку. Ломать существующие ключи нельзя.

---

## Задача 1. Позиция и файл у Telegram-отклика

### Зачем

Карточка отклика с Telegram сейчас показывает «Позиция не указана», хотя человек откликался на конкретную вакансию. Приложенный к отклику файл (резюме документом) в CRM не виден вовсе — а именно он часто и есть всё резюме кандидата.

### Что происходит сейчас

`POST /telegram/applications` принимает `telegram_user_id`, `telegram_full_name`, `telegram_username`, `vacancy_ref`, `candidate_text`, `resume_file_ref`.

`handle_telegram_application()` (`app/services/telegram_intake.py`) сохраняет `vacancy_ref`, `candidate_text` и `resume_file_ref` в строку `telegram_applications`, но в `parsed_profile` кандидата кладёт только имя и текст. Название вакансии и файл дальше не идут.

### Что сделать

**1.1. Принять название вакансии.** Добавить в тело `POST /telegram/applications` необязательное поле:

| Поле | Тип | Смысл |
|---|---|---|
| `vacancy_title` | `string`, ≤ 300 | Человеческое название позиции, на которую откликнулись |

**1.2. Заполнять `parsed_profile.position_title`** по такому правилу:

1. `vacancy_title` из запроса, если он пришёл;
2. иначе — `name` шаблона, найденного по `crm_vacancy_id == vacancy_ref` (тот же запрос, которым уже подбираются шаблоны для оценки);
3. иначе — оставить пустым, как сейчас.

Порядок важен: бот знает точное название кнопки, на которую нажал человек, а шаблон — только своё имя.

**1.3. Отдавать файл.** Добавить в `parsed_profile` два необязательных ключа:

| Ключ | Тип | Смысл |
|---|---|---|
| `resume_file_url` | `string` | HTTP(S)-ссылка, которую можно открыть из браузера |
| `resume_file_name` | `string` | Имя файла для подписи ссылки, например `resume.pdf` |

Если `resume_file_ref` — это уже URL, достаточно скопировать его в `resume_file_url`.

Если `resume_file_ref` — это `file_id` Telegram, ссылки на файл у CRM не появится: `file_id` без токена бота бесполезен, а токен бота CRM не знает и знать не должен. В этом случае нужен отдельный маршрут сервиса:

```http
GET /telegram/applications/{application_id}/file
Authorization: Bearer <INTERNAL_SERVICE_TOKEN>
→ 302 на временную ссылку Telegram  ИЛИ  200 + тело файла
```

и `resume_file_url` заполняется адресом этого маршрута. Маршрут внутренний, как остальные, — наружу его открывать не нужно, CRM ходит в сервис сама.

### Как проверить

```bash
curl -X POST http://recruitment-api:8000/telegram/applications \
  -H "Authorization: Bearer $INTERNAL_SERVICE_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"telegram_user_id": 1, "telegram_username": "leon_wp",
       "vacancy_ref": "<crm_vacancy_id существующего шаблона>",
       "vacancy_title": "Media Buyer",
       "candidate_text": "Добрый вечер, медиабайер с опытом…",
       "resume_file_ref": "https://example.com/cv.pdf"}'

curl -s "http://recruitment-api:8000/external-candidates?source=telegram&limit=1" \
  -H "Authorization: Bearer $INTERNAL_SERVICE_TOKEN"
```

Приёмка:

- `parsed_profile.position_title == "Media Buyer"`;
- `parsed_profile.resume_file_url` открывается в браузере;
- без `vacancy_title` позиция берётся из имени шаблона;
- существующие карточки Telegram не сломались: `text_blob` на месте.

---

## Задача 2. Дата рождения в профиле кандидата HH

### Зачем

Карточка кандидата с HH в CRM должна выглядеть так: **Имя · Дата рождения (и возраст) · Город**, ниже — ссылка на резюме. Имя, город и ссылка у CRM уже есть (`full_name`, `geo`, `sources[].external_url`), даты рождения нет.

### Что сделать

`normalize_resume()` (`app/providers/hh/normalize.py`) и структура `ParsedProfile` (`app/providers/base.py`) получают один новый ключ:

| Ключ | Тип | Формат |
|---|---|---|
| `birth_date` | `string \| null` | ISO `YYYY-MM-DD` |

Возраст CRM посчитает сама — хранить его отдельно не нужно, он устаревает.

**Нюанс источника данных.** Рабочий путь использует сокращённые объекты из выдачи `/resumes`, а не полное резюме. Если в сокращённом объекте даты рождения нет, есть два пути:

- **Дешёвый.** Отдавать то, что есть: если HH возвращает только возраст — добавить ключ `age` (целое) вместо `birth_date`. CRM покажет «31 год» без даты.
- **Точный.** Вызывать `get_resume()` для кандидатов, которые действительно сохраняются (новые и обновлённые), и брать дату оттуда. Это плюс один HTTP-запрос на кандидата — при большом поиске заметно по лимитам HH.

Решение за вами; CRM поддержит любой из двух ключей. Если оба пустые, строка с датой в карточке не рисуется.

### Как проверить

```sql
SELECT parsed_profile->>'full_name'  AS name,
       parsed_profile->>'birth_date' AS birth_date,
       parsed_profile->>'age'        AS age,
       parsed_profile->>'geo'        AS city
FROM external_candidates
ORDER BY last_seen_at DESC
LIMIT 10;
```

Приёмка: у кандидатов, найденных после обновления, заполнено `birth_date` (или `age`); старые записи не изменились до следующей встречи кандидата.

---

## Задача 3. Город как настоящий фильтр поиска

### Зачем

В форме шаблона поиска «Гео» заменяется на «Город» — город проживания кандидата. Сейчас этот критерий фильтром не работает.

### Что происходит сейчас

CRM отправляет критерий с ключом **`geo`**. `build_search_params()` (`app/providers/hh/search.py`) распознаёт **`geo_area_id`** и кладёт его в параметр HH `area`. Ключ `geo` в этот список не входит, поэтому значение уходит в полнотекстовый запрос: «USA» ищется как слово в тексте резюме, а не как регион.

То же касается ключа `position` — он тоже не структурный и попадает в текст. Если так и задумано, отдельной правки не нужно; если «Должность» должна становиться `professional_role`, скажите — это отдельная задача со справочником ролей.

### Что сделать

**3.1. Справочник областей.** Добавить маршрут:

```http
GET /providers/hh/areas?query=Каз&limit=20
Authorization: Bearer <INTERNAL_SERVICE_TOKEN>
```

Ответ:

```json
{"items": [
  {"id": "88",   "name": "Казань",          "parent": "Республика Татарстан"},
  {"id": "1261", "name": "Казанская",        "parent": "Тверская область"}
]}
```

- `id` — то, что HH ждёт в параметре `area`, строкой;
- `parent` — родительский регион, чтобы человек не перепутал два одноимённых города;
- пустой `query` может отдавать страны верхнего уровня;
- источник — HH `/areas` (можно кэшировать в памяти процесса: справочник меняется раз в год) или `/suggests/areas`.

Справочник нужен именно в сервисе: это он ходит в HH со своим токеном, и он же превращает критерий в параметр запроса. CRM возить копию списка городов не должна — она разъедется с HH.

**3.2. Подтвердить обработку `geo_area_id`.** CRM после этой правки будет слать критерий:

```json
{"key": "geo_area_id", "value": "88", "mode": "required", "weight": 0}
```

Ожидаемое поведение: `area=88` в запросе к HH. Значение с `|` (несколько городов) разбирается существующим `split_alternatives()`.

**3.3. Проверить, что `preferred` не портит выдачу.** Сейчас, по коду `build_search_params()`, все `preferred` попадают в `preferred_terms` и через AND приклеиваются к текстовому запросу — включая структурные ключи. Из-за этого «желательный» город или опыт сужает выдачу HH так же жёстко, как обязательный, хотя по смыслу он должен только добавлять баллы. Нужно исключить структурные ключи из текстовой части: в текст идут только ключевые слова и нераспознанные критерии.

### Как проверить

```bash
curl -s "http://recruitment-api:8000/providers/hh/areas?query=Казан" \
  -H "Authorization: Bearer $INTERNAL_SERVICE_TOKEN"
```

Приёмка:

- маршрут отвечает списком с `id`, `name`, `parent`;
- шаблон с критерием `geo_area_id=88` даёт в логе запроса к HH `area=88` (событие `SEARCH_STARTED` или отладочный лог параметров);
- шаблон, где город указан как `preferred`, находит кандидатов и из других городов — только с меньшим баллом.

---

## Что нужно от вас отдельно, без правки кода

По жалобе «поиск не ищет по критериям» на стенде в шаблоне лежит ошибка **«Шаблон приостановлен»**. Это состояние базы сервиса, а не интерфейса: `process_one_run()` останавливает задание, когда у шаблона `is_active = false`. Чтобы понять, кто и когда его выключил, нужны две выборки:

```sql
SELECT id, name, template_type, is_active, auto_search_enabled,
       last_run_at, last_success_at, last_error
FROM search_templates
ORDER BY created_at DESC;

SELECT key, value, mode, weight
FROM search_template_criteria
WHERE search_template_id = '<id шаблона Media Buyer>';
```

Первая покажет, действительно ли шаблон выключен; вторая — какие критерии до сервиса доехали и в каком режиме.

---

## Сводка

| Задача | Файлы сервиса | Что появляется в API |
|---|---|---|
| 1. Позиция и файл Telegram | `app/services/telegram_intake.py`, схема и маршрут `POST /telegram/applications` | `vacancy_title` в запросе; `position_title`, `resume_file_url`, `resume_file_name` в `parsed_profile` |
| 2. Дата рождения HH | `app/providers/hh/normalize.py`, `app/providers/base.py` | `birth_date` (или `age`) в `parsed_profile` |
| 3. Город как фильтр | `app/api/providers_hh.py`, `app/providers/hh/search.py` и клиент HH | `GET /providers/hh/areas`; `geo_area_id` → `area`; `preferred` не сужает выдачу |

Пути файлов — по таблице навигации из [RECRUITMENT_SERVICE.md](RECRUITMENT_SERVICE.md); имя модуля с маршрутом Telegram там не зафиксировано, ищите по строке `/telegram/applications`.

Все три правки независимы и могут выезжать по отдельности. CRM к каждой готова заранее: недостающие поля она просто не показывает.
