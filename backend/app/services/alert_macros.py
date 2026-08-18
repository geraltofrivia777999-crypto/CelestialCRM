"""Макросы сообщений Alert — ТЗ 9.1.

Каталог один на все стороны: по нему форма показывает подсказку, движок
подставляет значения, а кнопка «Тест» собирает пример. Разойтись им негде.

Два набора, потому что два вида уведомлений говорят о разном:

* депозит — про конкретную конверсию: кампания, оффер, время клика, sub_id;
* отчёт — про период целиком: лиды, продажи, доход, расход, профит, ROI.

Незаполненный макрос подставляется прочерком, а не пустой строкой: строка
«Оффер: » выглядит как поломка вёрстки, а «Оффер: —» читается как факт.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from html import escape
from zoneinfo import ZoneInfo

DASH = "—"


@dataclass(frozen=True)
class Macro:
    code: str
    label: str
    example: str


DEPOSIT_MACROS = (
    Macro("campaign", "Название кампании Keitaro", "FB | RU | Vulkan"),
    Macro("offer", "Название оффера", "Vulkan DE 250$"),
    Macro("click_at", "Дата и время клика", "08.08.2026 14:23"),
    Macro("conversion_at", "Дата и время конверсии", "08.08.2026 15:02"),
    Macro("revenue", "Доход по конверсии", "250.00"),
    Macro("country", "Страна", "DE"),
    *[
        Macro(f"sub_id_{index}", f"sub_id_{index}", f"значение sub_id_{index}")
        for index in range(1, 11)
    ],
)

REPORT_MACROS = (
    Macro("period", "Период", "08.08.2026 — 08.08.2026"),
    Macro("leads", "Лиды", "128"),
    Macro("sales", "Продажи", "17"),
    Macro("revenue", "Доход", "4 250.00"),
    Macro("spend", "Расход", "3 100.00"),
    Macro("profit", "Профит", "1 150.00"),
    Macro("roi", "ROI, %", "37.10"),
)

DEFAULT_DEPOSIT_TEMPLATE = (
    "💰 Новый депозит\n"
    "Оффер: {offer}\n"
    "Кампания: {campaign}\n"
    "Доход: {revenue}\n"
    "Клик: {click_at}"
)
DEFAULT_REPORT_TEMPLATE = (
    "📊 Отчёт за {period}\n"
    "Лиды: {leads}\n"
    "Продажи: {sales}\n"
    "Доход: {revenue}\n"
    "Расход: {spend}\n"
    "Профит: {profit}\n"
    "ROI: {roi} %"
)


def macros_for(kind: str) -> tuple[Macro, ...]:
    return REPORT_MACROS if kind == "report" else DEPOSIT_MACROS


def default_template(kind: str) -> str:
    return DEFAULT_REPORT_TEMPLATE if kind == "report" else DEFAULT_DEPOSIT_TEMPLATE


def render(template: str, values: dict, *, escape_values: bool = False) -> str:
    """Подставить макросы. Неизвестные оставляем как есть.

    Молча вычищать `{что-то}` нельзя: человек написал это осмысленно, и лучше
    он увидит фигурные скобки в сообщении, чем пустоту на их месте.

    `escape_values` нужен для отправки в Telegram с HTML-разметкой: теги в
    шаблоне пишет человек и они должны сработать, а вот угловая скобка в
    названии оффера — это текст, и без экранирования она уронила бы всё
    сообщение с ошибкой разбора.
    """
    text = template or ""
    for code, value in values.items():
        replacement = DASH if value in (None, "") else str(value)
        text = text.replace(
            "{" + code + "}", escape(replacement) if escape_values else replacement
        )
    return text


def sample(kind: str) -> dict:
    return {macro.code: macro.example for macro in macros_for(kind)}


def moment(value: datetime | None, timezone_name: str) -> str:
    """Время в таймзоне правила.

    В базе всё лежит в UTC, а «клик в 14:23» человек читает по своим часам —
    без пересчёта сообщение спорит с тем, что видно в трекере.
    """
    if not value:
        return DASH
    try:
        zone = ZoneInfo(timezone_name or "UTC")
    except Exception:
        zone = ZoneInfo("UTC")
    return value.astimezone(zone).strftime("%d.%m.%Y %H:%M")


def money(value: Decimal | float | None) -> str:
    if value is None:
        return DASH
    return f"{Decimal(str(value)).quantize(Decimal('0.01'))}"
