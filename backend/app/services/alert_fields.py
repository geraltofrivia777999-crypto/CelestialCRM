"""Периоды и показатели Alert — ТЗ 9.1.

Здесь остались две вещи, нужные отчёту: пресеты периода и формула, по которой
из пяти сумм Медиаборда получаются доход, расход, профит и ROI.

Формула живёт в одном месте намеренно: капы, отчёты и экран обязаны показывать
одно и то же число, иначе спор «у меня в CRM другое» неразрешим.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from app.services.formulas import q


@dataclass(frozen=True)
class Field:
    """Поле, которое умеет печатать своё значение."""

    code: str
    label: str
    unit: str  # money | count | percent


FIELDS: dict[str, Field] = {
    item.code: item
    for item in (
        Field("spend", "Расход (SPEND)", "money"),
        Field("revenue", "Доход", "money"),
        Field("profit", "Профит (доход − расход)", "money"),
        Field("roi", "ROI, %", "percent"),
        Field("installs", "Инсталлы", "count"),
        Field("leads", "Лиды (регистрации)", "count"),
        Field("sales", "Депозиты (FTD)", "count"),
        Field("cpl", "Цена лида", "money"),
        Field("cpd", "Цена депозита", "money"),
        Field("cpi", "Цена инсталла", "money"),
        Field("cr", "Конверсия лид → депозит, %", "percent"),
    )
}

WINDOWS = {
    "today": "Сегодня",
    "yesterday": "Вчера",
    "last_3d": "Последние 3 дня",
    "last_7d": "Последние 7 дней",
    "last_14d": "Последние 14 дней",
    "last_30d": "Последние 30 дней",
    "this_week": "Текущая неделя",
    "last_week": "Прошлая неделя",
    "month": "Текущий месяц",
    "last_month": "Прошлый месяц",
}

def window_range(window: str, today: date) -> tuple[date, date]:
    if window == "yesterday":
        day = today - timedelta(days=1)
        return day, day
    if window in {"last_3d", "last_7d", "last_14d", "last_30d"}:
        days = int(window.split("_")[1].rstrip("d"))
        return today - timedelta(days=days - 1), today
    if window == "this_week":
        return today - timedelta(days=today.weekday()), today
    if window == "last_week":
        monday = today - timedelta(days=today.weekday() + 7)
        return monday, monday + timedelta(days=6)
    if window == "month":
        return today.replace(day=1), today
    if window == "last_month":
        first_of_this = today.replace(day=1)
        last = first_of_this - timedelta(days=1)
        return last.replace(day=1), last
    return today, today


def format_value(code: str, value) -> str:
    if value is None:
        return "—"
    unit = FIELDS.get(code, Field(code, code, "money")).unit
    number = Decimal(str(value))
    if unit == "count":
        return f"{int(number)}"
    if unit == "percent":
        return f"{number.quantize(Decimal('0.01'))} %"
    return f"{number.quantize(Decimal('0.01'))}"


def media_values(
    revenue: Decimal, spend: Decimal, installs: int, leads: int, sales: int
) -> dict[str, Decimal | None]:
    """Показатели Медиаборда из пяти сумм.

    Формула живёт здесь одна: капы, алерты и предпросмотр обязаны показывать
    одно и то же число, иначе спор «у меня в CRM другое» неразрешим.
    """
    return {
        "revenue": q(revenue),
        "spend": q(spend),
        "profit": q(revenue - spend),
        "roi": None if spend == 0 else q((revenue - spend) / spend * 100),
        "installs": Decimal(installs),
        "leads": Decimal(leads),
        "sales": Decimal(sales),
        "cpl": None if leads == 0 else q(spend / Decimal(leads)),
        "cpd": None if sales == 0 else q(spend / Decimal(sales)),
        "cpi": None if installs == 0 else q(spend / Decimal(installs)),
        "cr": None if leads == 0 else q(Decimal(sales) / Decimal(leads) * 100),
    }
