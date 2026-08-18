"""Расход Meta за отрезок дня и его привязка к офферу — ТЗ 2.4.4.

Баер льёт один оффер с 12:00 до 16:00, потом переключается на другой. Дневная
статистика Meta про это не знает — она отдаёт сумму за сутки, поэтому окно
берётся из почасовой разбивки и записывается отдельно.

Три вещи, без которых фиксация была бы опасной.

* **Окно — полуинтервал `[from, to)`.** «С 12:00 по 16:00» — это часы 12, 13,
  14 и 15. Иначе шестнадцатый час попадал бы и в это окно, и в следующее.
* **Пересечения запрещены.** Один и тот же час одной кампании нельзя отнести
  дважды: второй клик по кнопке молча удваивал бы расход в Медиаборде.
* **Хранится сумма до процента агента.** Процент у агента меняется, и
  пересчитать запись без исходной суммы было бы не из чего.

Часы считает сама Meta и только в таймзоне рекламного кабинета — других
вариантов Graph API не даёт. Поэтому таймзона едет рядом с числами: «с 12 до
16» ничего не значит, если кабинет живёт в другом поясе.
"""

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import MetaSpendCommit
from app.services.formulas import q

HOURS = 24


def window_spend(hours: list[dict], hour_from: int, hour_to: int) -> Decimal:
    """Сумма расхода по часам окна `[hour_from, hour_to)`."""
    total = Decimal("0")
    for bucket in hours:
        hour = int(bucket.get("hour", -1))
        if hour_from <= hour < hour_to:
            total += Decimal(str(bucket.get("spend") or 0))
    return q(total)


def overlaps(first: tuple[int, int], second: tuple[int, int]) -> bool:
    """Пересекаются ли два полуинтервала часов."""
    return first[0] < second[1] and second[0] < first[1]


def describe_window(hour_from: int, hour_to: int) -> str:
    return f"{hour_from:02d}:00–{hour_to:02d}:00"


async def commits_for_day(
    db: AsyncSession, workspace_id: uuid.UUID, record_date: date
) -> list[MetaSpendCommit]:
    return list(
        (
            await db.execute(
                select(MetaSpendCommit)
                .where(
                    MetaSpendCommit.workspace_id == workspace_id,
                    MetaSpendCommit.record_date == record_date,
                )
                .order_by(MetaSpendCommit.hour_from, MetaSpendCommit.created_at)
            )
        ).scalars()
    )


def taken_hours(commits: list[MetaSpendCommit], campaign_id: str) -> list[int]:
    """Часы кампании, уже отнесённые на какой-то оффер.

    Нужны на экране: иначе единственный способ узнать, что окно занято, — это
    получить отказ при сохранении.
    """
    hours: set[int] = set()
    for commit in commits:
        if commit.campaign_external_id != campaign_id:
            continue
        hours.update(range(commit.hour_from, min(commit.hour_to, HOURS)))
    return sorted(hours)


def conflict(
    commits: list[MetaSpendCommit], campaign_id: str, window: tuple[int, int]
) -> MetaSpendCommit | None:
    for commit in commits:
        if commit.campaign_external_id != campaign_id:
            continue
        if overlaps((commit.hour_from, commit.hour_to), window):
            return commit
    return None
