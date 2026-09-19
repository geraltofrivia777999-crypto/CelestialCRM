"""Расход Meta за отрезок времени и его привязка к офферу — ТЗ 2.4.4.

Баер льёт один оффер с 12:00 до 16:00, потом переключается на другой. Дневная
статистика Meta про это не знает — она отдаёт сумму за сутки, поэтому окно
берётся из почасовой разбивки и записывается отдельно.

Окно задаётся от часа одного дня до часа другого: «с 10 августа 22:00 по
12 августа 16:00». Медиаборд при этом живёт записями «день + баер + оффер»,
поэтому длинное окно раскладывается на посуточные части — каждая часть ложится
в свою запись и снимается отдельно.

Четыре вещи, без которых фиксация была бы опасной.

* **Окно — полуинтервал `[from, to)`.** «С 12:00 по 16:00» — это часы 12, 13,
  14 и 15. Иначе шестнадцатый час попадал бы и в это окно, и в следующее.
* **Пересечения запрещены.** Один и тот же час одной кампании нельзя отнести
  дважды: второй клик по кнопке молча удваивал бы расход в Медиаборде.
* **Хранится сумма до процента агента.** Процент у агента меняется, и
  пересчитать запись без исходной суммы было бы не из чего.
* **Длинное окно не длиннее месяца.** Почасовая разбивка каждого дня — это
  поход в Meta; окно на полгода впереди себя десятки таких походов ради
  фиксации, которой столько не нужно.

Часы считает сама Meta и только в таймзоне рекламного кабинета — других
вариантов Graph API не даёт. Но команда живёт по Москве, и «с 12 до 16» для неё
значит московские часы, а не эфиопские. Поэтому почасовая разбивка кабинета
пересобирается в UTC+3: час окна берётся из того часа кабинета, который в это
время шёл на самом деле, — при необходимости из соседних суток.
"""

import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MetaSpendCommit
from app.services.formulas import q

HOURS = 24
# Смысловой потолок длины окна. Совпадает с потолком почасового отчёта:
# дольше окна всё равно не смотрят, а Meta отдаёт такой период частями.
MAX_WINDOW_DAYS = 30


def compose(day: date, hour: int) -> datetime:
    """Точка окна: час дня, где «24:00» — это полночь следующего дня."""
    if hour == HOURS:
        return datetime.combine(day + timedelta(days=1), time(0))
    return datetime.combine(day, time(hour))


def window_bounds(
    date_from: date, hour_from: int, date_to: date, hour_to: int
) -> tuple[datetime, datetime]:
    """Границы окна полуинтервалом `[от, до)`."""
    return compose(date_from, hour_from), compose(date_to, hour_to)


def validate_window(
    date_from: date, hour_from: int, date_to: date, hour_to: int
) -> None:
    """Окно осмысленно: конец позже начала и длина в разумных пределах."""
    if not 0 <= hour_from < HOURS or not 1 <= hour_to <= HOURS:
        raise ValueError("Окно задаётся часами от 0 до 24")
    start, end = window_bounds(date_from, hour_from, date_to, hour_to)
    if end <= start:
        raise ValueError("Конец окна должен быть позже начала")
    if (date_to - date_from).days > MAX_WINDOW_DAYS:
        raise ValueError(f"Окно фиксации не длиннее {MAX_WINDOW_DAYS} дней")


def split_into_days(
    date_from: date, hour_from: int, date_to: date, hour_to: int
) -> list[tuple[date, int, int]]:
    """Разложить окно на посуточные части `(день, час_от, час_до)`.

    Первая часть идёт до конца суток, средние накрывают сутки целиком,
    последняя заканчивается в час конца окна. Однодневное окно остаётся одним
    отрезком — прежнее поведение без переизобретения.
    """
    if date_from == date_to:
        return [(date_from, hour_from, hour_to)]
    parts = [(date_from, hour_from, HOURS)]
    middle = date_from + timedelta(days=1)
    while middle < date_to:
        parts.append((middle, 0, HOURS))
        middle += timedelta(days=1)
    parts.append((date_to, 0, hour_to))
    return parts


# Часы окна команда задаёт по Москве: и Медиаборд, и книги баера живут в этой
# зоне, а кабинет может быть в любой.
REPORT_TZ = ZoneInfo("Europe/Moscow")


def account_offset(timezone_name: str | None, day: date) -> int:
    """На сколько часов отчётная зона опережает зону кабинета в этот день.

    Считается на конкретную дату, а не раз и навсегда: и Москва, и кабинет
    могут жить с переходом на летнее время, и летом разница другая.
    """
    if not timezone_name:
        return 0
    try:
        account_zone = ZoneInfo(str(timezone_name))
    except Exception:  # noqa: BLE001 — незнакомая зона не должна ронять фиксацию
        return 0
    moment = datetime.combine(day, time(12, 0))
    account_shift = account_zone.utcoffset(moment) or timedelta(0)
    report_shift = REPORT_TZ.utcoffset(moment) or timedelta(0)
    return int((report_shift - account_shift).total_seconds() // 3600)


def shift_hours(
    previous: list[dict], current_day: list[dict], following: list[dict], offset: int
) -> list[dict]:
    """Разбивка кабинета, пересобранная в отчётную зону.

    `offset` — насколько отчётная зона опережает зону кабинета. Час X по Москве
    это час `X - offset` у кабинета: при положительном сдвиге начало суток
    берётся из вчерашнего дня кабинета, при отрицательном конец — из завтрашнего.
    Соседние сутки приходят пустыми, если их не запрашивали, — тогда края окна
    просто останутся нулевыми, а не приедут чужими числами.
    """
    if not offset:
        return current_day
    source = {
        -1: {int(row.get("hour", -1)): row for row in previous or []},
        0: {int(row.get("hour", -1)): row for row in current_day or []},
        1: {int(row.get("hour", -1)): row for row in following or []},
    }
    result = []
    for hour in range(HOURS):
        raw = hour - offset
        shift = 0
        if raw < 0:
            raw += HOURS
            shift = -1
        elif raw >= HOURS:
            raw -= HOURS
            shift = 1
        bucket = source[shift].get(raw)
        result.append({
            "hour": hour,
            "spend": (bucket or {}).get("spend", 0.0),
            "impressions": (bucket or {}).get("impressions", 0),
            "clicks": (bucket or {}).get("clicks", 0),
            "link_clicks": (bucket or {}).get("link_clicks", 0),
        })
    return result


def window_spend(hours: list[dict], hour_from: int, hour_to: int) -> Decimal:
    """Сумма расхода по часам окна `[hour_from, hour_to)`."""
    total = Decimal("0")
    for bucket in hours:
        hour = int(bucket.get("hour", -1))
        if hour_from <= hour < hour_to:
            total += Decimal(str(bucket.get("spend") or 0))
    return q(total)


def describe_window(hour_from: int, hour_to: int) -> str:
    return f"{hour_from:02d}:00–{hour_to:02d}:00"


def describe_span(
    date_from: date, hour_from: int, date_to: date, hour_to: int
) -> str:
    """Окно словами: внутри суток коротко, через полночь — с датами."""
    if date_from == date_to:
        return describe_window(hour_from, hour_to)
    return (
        f"{date_from.isoformat()} {describe_window(hour_from, HOURS)} – "
        f"{date_to.isoformat()} {describe_window(0, hour_to)}"
    )


async def commits_for_range(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    date_from: date,
    date_to: date | None = None,
) -> list[MetaSpendCommit]:
    last = date_to or date_from
    return list(
        (
            await db.execute(
                select(MetaSpendCommit)
                .where(
                    MetaSpendCommit.workspace_id == workspace_id,
                    MetaSpendCommit.record_date >= date_from,
                    MetaSpendCommit.record_date <= last,
                )
                .order_by(
                    MetaSpendCommit.record_date,
                    MetaSpendCommit.hour_from,
                    MetaSpendCommit.created_at,
                )
            )
        ).scalars()
    )


def commit_bounds(commit: MetaSpendCommit) -> tuple[datetime, datetime]:
    """Границы сохранённой части окна одной точкой отсчёта."""
    return compose(commit.record_date, commit.hour_from), compose(
        commit.record_date, commit.hour_to
    )


def taken_hours(commits: list[MetaSpendCommit], campaign_id: str) -> list[int]:
    """Часы кампании за один день, уже отнесённые на какой-то оффер.

    Нужны на экране: иначе единственный способ узнать, что окно занято, — это
    получить отказ при сохранении.
    """
    hours: set[int] = set()
    for commit in commits:
        if commit.campaign_external_id != campaign_id:
            continue
        hours.update(range(commit.hour_from, min(commit.hour_to, HOURS)))
    return sorted(hours)


def taken_windows(
    commits: list[MetaSpendCommit], campaign_id: str
) -> list[dict]:
    """Все занятые отрезки кампании в запрошенном диапазоне дней."""
    windows = []
    for commit in commits:
        if commit.campaign_external_id != campaign_id:
            continue
        windows.append(
            {
                "date": commit.record_date.isoformat(),
                "from": commit.hour_from,
                "to": commit.hour_to,
            }
        )
    return windows


def conflict_span(
    commits: list[MetaSpendCommit],
    campaign_id: str,
    date_from: date,
    hour_from: int,
    date_to: date,
    hour_to: int,
) -> MetaSpendCommit | None:
    """Сохранённая часть, пересекающаяся с новым окном, если такая есть.

    Обе стороны сравниваются точками во времени: часть прошлого окна лежит в
    своём дне, а новое окно может переходить через полночь.
    """
    start, end = window_bounds(date_from, hour_from, date_to, hour_to)
    for commit in commits:
        if commit.campaign_external_id != campaign_id:
            continue
        other_start, other_end = commit_bounds(commit)
        if other_start < end and start < other_end:
            return commit
    return None
