"""Связка — заготовка кампании, адсета и объявления (ТЗ 3.5).

Связка описывает залив целиком: цель и бюджет кампании, таргет и плейсменты
адсета, тексты и кнопку объявления. Один раз собрал — заливаешь на любой кабинет.

Почему цель кампании отдельным справочником, а не полем `objective`. В Meta
целей шесть, а команда мыслит девятью: «Лиды» и «Продажи» это одна цель
`OUTCOME_SALES`/`OUTCOME_LEADS` с разной оптимизацией, а «Отметки Нравится» и
«Буст ФП» — одна цель `OUTCOME_ENGAGEMENT` с разной. Пресет хранит связку
«цель + оптимизация + событие оплаты», поэтому в интерфейсе видно то, что баер
называет целью, а в Graph API уходит то, что ждёт Meta.

Остальные параметры лежат в `settings` одним JSON тремя блоками — campaign,
adset, ad. Отдельными колонками их не разложить: это три десятка сквозных полей
Graph API, по которым мы никогда не ищем и не считаем. Проверяет их схема
`MetaBundleSettings`, а не база.
"""

import random
import re
from typing import Any

from app.services import geo

# Цель кампании: код → что уходит в Meta. Порядок — как в интерфейсе, по
# группам «Узнаваемость / Рассмотрение / Конверсия».
GOALS: dict[str, dict[str, str]] = {
    "reach": {
        "label": "Охват",
        "group": "awareness",
        "objective": "OUTCOME_AWARENESS",
        "optimization_goal": "REACH",
        "billing_event": "IMPRESSIONS",
    },
    "traffic": {
        "label": "Трафик",
        "group": "consideration",
        "objective": "OUTCOME_TRAFFIC",
        "optimization_goal": "LINK_CLICKS",
        "billing_event": "IMPRESSIONS",
    },
    "engagement": {
        "label": "Вовлеченность",
        "group": "consideration",
        "objective": "OUTCOME_ENGAGEMENT",
        "optimization_goal": "POST_ENGAGEMENT",
        "billing_event": "IMPRESSIONS",
    },
    "app_installs": {
        "label": "Установки приложения",
        "group": "consideration",
        "objective": "OUTCOME_APP_PROMOTION",
        "optimization_goal": "APP_INSTALLS",
        "billing_event": "IMPRESSIONS",
    },
    "post_likes": {
        "label": "Отметки «Нравится»",
        "group": "consideration",
        "objective": "OUTCOME_ENGAGEMENT",
        "optimization_goal": "POST_ENGAGEMENT",
        "billing_event": "IMPRESSIONS",
    },
    "page_likes": {
        "label": "Буст ФП (отметки «Нравится» страницы)",
        "group": "consideration",
        "objective": "OUTCOME_ENGAGEMENT",
        "optimization_goal": "PAGE_LIKES",
        "billing_event": "IMPRESSIONS",
    },
    "leads": {
        "label": "Лиды",
        "group": "conversion",
        "objective": "OUTCOME_LEADS",
        "optimization_goal": "OFFSITE_CONVERSIONS",
        "billing_event": "IMPRESSIONS",
    },
    "sales": {
        "label": "Продажи",
        "group": "conversion",
        "objective": "OUTCOME_SALES",
        "optimization_goal": "OFFSITE_CONVERSIONS",
        "billing_event": "IMPRESSIONS",
    },
    "messages": {
        "label": "Сообщения (Вовлеченность)",
        "group": "conversion",
        "objective": "OUTCOME_ENGAGEMENT",
        "optimization_goal": "CONVERSATIONS",
        "billing_event": "IMPRESSIONS",
    },
}
GROUP_LABELS = {
    "awareness": "Узнаваемость",
    "consideration": "Рассмотрение",
    "conversion": "Конверсия",
}
DEFAULT_GOAL = "leads"

# Цели, где Meta требует пиксель с событием: без него объявление не создастся.
PIXEL_GOALS = {"leads", "sales"}

# Окно атрибуции — как его называет Meta в `attribution_spec`.
ATTRIBUTION_WINDOWS: dict[str, dict] = {
    "1d_click": {
        "label": "1 день после клика",
        "spec": [{"event_type": "CLICK_THROUGH", "window_days": 1}],
    },
    "7d_click": {
        "label": "7 дней после клика",
        "spec": [{"event_type": "CLICK_THROUGH", "window_days": 7}],
    },
    "1d_click_1d_view": {
        "label": "1 день после клика или 1 день после просмотра",
        "spec": [
            {"event_type": "CLICK_THROUGH", "window_days": 1},
            {"event_type": "VIEW_THROUGH", "window_days": 1},
        ],
    },
    "7d_click_1d_view": {
        "label": "7 дней после клика или 1 день после просмотра",
        "spec": [
            {"event_type": "CLICK_THROUGH", "window_days": 7},
            {"event_type": "VIEW_THROUGH", "window_days": 1},
        ],
    },
}
DEFAULT_ATTRIBUTION = "7d_click_1d_view"

# Тип локации в `geo_locations.location_types`. «Проживающие» — вариант по
# умолчанию: для товарки турист в стране бесполезен.
LOCATION_TYPES: dict[str, dict] = {
    "home": {"label": "Проживающие", "types": ["home"]},
    "recent": {"label": "Недавно посещавшие", "types": ["recent"]},
    "travel_in": {"label": "Путешествующие", "types": ["travel_in"]},
    "any": {"label": "Все в этой локации", "types": ["home", "recent"]},
}
DEFAULT_LOCATION_TYPE = "home"

# Особых категорий у Meta три. Политику и азартные игры сюда не добавляем:
# в `special_ad_categories` рекламных кабинетов их нет, они живут в отдельном
# согласовании, и выбор, который Meta не примет, — это обещание, а не настройка.
SPECIAL_AD_CATEGORIES: dict[str, str] = {
    "CREDIT": "Кредиты",
    "HOUSING": "Жилье",
    "EMPLOYMENT": "Работа",
}
SPECIAL_AD_CATEGORY_HINTS: dict[str, str] = {
    "CREDIT": (
        "Реклама кредитных карт, кредитов на покупку транспортных средств, "
        "долгосрочного финансирования или других подобных возможностей."
    ),
    "HOUSING": (
        "Реклама, связанная с объявлениями о продаже или аренде недвижимости, "
        "страхованием домовладельцев, ипотечными кредитами или другими подобными "
        "возможностями."
    ),
    "EMPLOYMENT": (
        "Реклама вакансий, программ стажировки и профессиональной сертификации, "
        "а также других подобных возможностей."
    ),
}

BID_STRATEGIES: dict[str, str] = {
    "LOWEST_COST_WITHOUT_CAP": "Максимальное количество",
    "LOWEST_COST_WITH_BID_CAP": "Предельная ставка",
    "COST_CAP": "Цель по цене за результат",
}
# Названия стратегий сами по себе ничего не объясняют: «предельная ставка» и
# «цель по цене» отличаются тем, что первая ограничивает аукцион, а вторая —
# среднюю цену результата. Разница видна только из описания.
BID_STRATEGY_HINTS: dict[str, str] = {
    "LOWEST_COST_WITHOUT_CAP": (
        "Помогает достичь максимального количества результатов с учётом вашего бюджета"
    ),
    "LOWEST_COST_WITH_BID_CAP": (
        "Указывает системе максимальную ставку, которую можно потратить на аукционе"
    ),
    "COST_CAP": (
        "Позволяет независимо от рыночных условий удерживать цены на среднем уровне"
    ),
}
# Как называется сумма рядом со стратегией: у одной это потолок аукциона, у
# другой — целевая цена результата, и «Ставка» на обе не годится.
BID_AMOUNT_LABELS: dict[str, str] = {
    "LOWEST_COST_WITH_BID_CAP": "Предельная ставка",
    "COST_CAP": "Цель по цене за результат",
}

# Версии ОС, которые понимает `user_os`. Список конечный и меняется раз в год —
# держим его у себя: спрашивать у Meta нечего, отдельной ручки под него нет.
OS_VERSIONS: dict[str, list[str]] = {
    "android": [
        "1.0", "1.1", "1.5", "1.6", "2.0", "2.1", "2.2", "2.3", "3.0", "3.1", "3.2",
        "4.0", "4.1", "4.2", "4.3", "4.4", "5.0", "5.1", "6.0", "7.0", "7.1", "8.0",
        "8.1", "9.0", "10.0", "11.0", "12.0", "13.0", "14.0", "15.0",
    ],
    "ios": [
        "2.0", "3.0", "4.0", "5.0", "6.0", "7.0", "8.0", "9.0", "10.0", "11.0",
        "12.0", "13.0", "14.0", "15.0", "16.0", "17.0", "18.0",
    ],
}

PIXEL_EVENTS: dict[str, str] = {
    "LEAD": "Лид",
    "PURCHASE": "Покупка",
    "COMPLETE_REGISTRATION": "Регистрация",
    "ADD_TO_CART": "Добавление в корзину",
    "INITIATED_CHECKOUT": "Начало оформления",
    "SUBSCRIBE": "Подписка",
    "CONTACT": "Контакт",
    "VIEW_CONTENT": "Просмотр контента",
}


def goal_for(code: str | None) -> dict[str, str]:
    return GOALS.get(str(code or ""), GOALS[DEFAULT_GOAL])


def reference() -> dict:
    """Справочники связки для интерфейса — одним куском, чтобы не плодить ручки."""
    return {
        "goals": [
            {"code": code, **{key: value for key, value in goal.items() if key != "spec"}}
            for code, goal in GOALS.items()
        ],
        "goal_groups": GROUP_LABELS,
        "attribution_windows": [
            {"code": code, "label": item["label"]}
            for code, item in ATTRIBUTION_WINDOWS.items()
        ],
        "location_types": [
            {"code": code, "label": item["label"]} for code, item in LOCATION_TYPES.items()
        ],
        "special_ad_categories": SPECIAL_AD_CATEGORIES,
        "bid_strategies": BID_STRATEGIES,
        "bid_strategy_hints": BID_STRATEGY_HINTS,
        "bid_amount_labels": BID_AMOUNT_LABELS,
        "pixel_events": PIXEL_EVENTS,
        "os_versions": OS_VERSIONS,
        "macros": MACROS,
        "special_ad_category_hints": SPECIAL_AD_CATEGORY_HINTS,
        # Страны отдаём справочником: коды стран не меняются, и гонять за ними
        # в Meta на каждый ввод символа незачем.
        "countries": geo.country_options(),
    }


# --- макросы в названиях -----------------------------------------------------

# Макрос подставляется в названия кампании, адсета и объявления в момент
# публикации. Считаем их на сервере, а не в браузере: заливов из одной связки
# бывает двадцать, и номер адсета должен быть номером внутри своего залива.
#
# Списки разные по уровням, и это не придирка: ID кампании можно подставить в
# название адсета, потому что кампания к тому моменту уже создана, а в название
# самой кампании — нельзя, Meta присваивает ID после создания. Показывать
# макрос, который гарантированно останется пустым, — обещание, а не настройка.
_COMMON_MACROS: list[dict[str, str]] = [
    {"code": "{{bundle.name}}", "label": "название связки"},
    {"code": "{{launch.name}}", "label": "название залива"},
    {"code": "{{cab.name}}", "label": "название кабинета"},
    {"code": "{{cab.id}}", "label": "ID кабинета"},
    {"code": "{{cab.time}}", "label": "таймзона кабинета, на котором будет залита реклама"},
    {"code": "{{bm.name}}", "label": "название БМ"},
    {"code": "{{page.id}}", "label": "ID ФП"},
    {"code": "{{pixel.id}}", "label": "ID пикселя"},
    {"code": "{{geo}}", "label": "гео залива"},
    {"code": "{{offer}}", "label": "оффер"},
    {"code": "{{age}}", "label": "возраст"},
    {"code": "{{gender}}", "label": "пол"},
    {"code": "{{creative.name}}", "label": "название файла креатива"},
    {"code": "{{date}}", "label": "текущая дата в формате 2020-01-01"},
    {"code": "{{datetime}}", "label": "дата и время залива в формате 2020-01-01 00:00:00"},
    {"code": "{{time}}", "label": "время залива в формате 00:00"},
    {
        "code": "{{random.digits.6}}",
        "label": "рандомный набор цифр, в конце можно указать своё количество",
    },
    {
        "code": "{{random.letters.en.6}}",
        "label": "рандомный набор латинских букв, в конце можно указать своё количество",
    },
    {
        "code": "{{random.letters.ru.6}}",
        "label": "рандомный набор русских букв, в конце можно указать своё количество",
    },
]
MACROS: dict[str, list[dict[str, str]]] = {
    "campaign": _COMMON_MACROS
    + [{"code": "{{campaign.number}}", "label": "порядковый номер кампаний при создании"}],
    "adset": _COMMON_MACROS
    + [
        {"code": "{{campaign.id}}", "label": "ID кампании"},
        {"code": "{{adset.number}}", "label": "порядковый номер адсетов при создании"},
    ],
    "ad": _COMMON_MACROS
    + [
        {"code": "{{campaign.id}}", "label": "ID кампании"},
        {"code": "{{adset.id}}", "label": "ID адсета"},
        {"code": "{{ad.number}}", "label": "порядковый номер объявлений при создании"},
    ],
}

_MACRO_RE = re.compile(r"\{\{\s*([a-zA-Z_.0-9]+)\s*\}\}")
_LETTERS = {
    "en": "abcdefghijklmnopqrstuvwxyz",
    "ru": "абвгдеёжзийклмнопрстуфхцчшщыэюя",
}


def render_macros(
    pattern: str | None, context: dict[str, Any], rng: random.Random | None = None
) -> str:
    """Подставить значения макросов в название.

    Неизвестный макрос выбрасывается, а не остаётся в тексте: «ad #{{ad.nmber}}»
    в кабинете выглядит как опечатка баера, но чинить её пришлось бы уже в Meta.
    """
    if not pattern:
        return ""
    picker = rng or random

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name.startswith("random."):
            return _random_token(name, picker)
        value = context.get(name)
        return "" if value is None else str(value)

    return " ".join(_MACRO_RE.sub(replace, str(pattern)).split())


def _random_token(name: str, picker: random.Random) -> str:
    """`random.digits.6`, `random.letters.en.4` — длина последним числом."""
    parts = name.split(".")
    length = 6
    if parts[-1].isdigit():
        length = max(1, min(int(parts[-1]), 32))
        parts = parts[:-1]
    alphabet = "0123456789" if parts[-1] == "digits" else _LETTERS.get(parts[-1], "")
    if not alphabet:
        return ""
    return "".join(picker.choice(alphabet) for _ in range(length))


# --- spintax ------------------------------------------------------------------

_SPIN_RE = re.compile(r"\{([^{}]*)\}")


def spin(text: str | None, rng: random.Random | None = None) -> str:
    """Развернуть spintax `{вариант|вариант}`, начиная с самых вложенных.

    Разворачиваем при публикации, а не при сохранении связки: смысл spintax в
    том, что у каждого объявления свой вариант текста, а связка одна на все.
    """
    if not text:
        return ""
    picker = rng or random
    result = str(text)
    # Ограничение на число проходов — защита от текста, где скобки не сходятся:
    # без него строка вида «{a|{b» крутила бы цикл вечно.
    for _ in range(20):
        replaced = _SPIN_RE.sub(
            lambda match: picker.choice(match.group(1).split("|")), result
        )
        if replaced == result:
            break
        result = replaced
    return result
