"""Единые календарные часы CRM.

Timestamp в базе и очередях остаются UTC. Даты, которые пользователь понимает
как «сегодня», «этот месяц» или «с полуночи», считаются по Москве.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.config import settings

MOSCOW_TIMEZONE = "Europe/Moscow"


def business_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(settings.default_timezone or MOSCOW_TIMEZONE)
    except ZoneInfoNotFoundError:
        return ZoneInfo(MOSCOW_TIMEZONE)


def business_now() -> datetime:
    return datetime.now(business_timezone())


def business_today() -> date:
    return business_now().date()


# Шаги, которые делят сутки без остатка: прогон по такому шагу всегда попадает
# в 00:00 и в начало часа, а не «через 30 минут от запуска сервера».
ALIGNED_STEPS = (15, 20, 30, 60, 120, 180, 240, 360, 480, 720, 1440)


def aligned_step_minutes(value: int | None, minimum: int = 15) -> int:
    """Ближайший снизу шаг из ALIGNED_STEPS, но не меньше `minimum`."""
    wanted = max(int(value or 0), minimum)
    fitting = [step for step in ALIGNED_STEPS if minimum <= step <= wanted]
    return fitting[-1] if fitting else minimum


def meta_rules_step_minutes() -> int:
    """Как часто сервер прогоняет автоправила Meta."""
    return aligned_step_minutes(settings.meta_rules_interval_minutes)
