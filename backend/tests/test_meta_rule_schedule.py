"""Когда автоправилу разрешено срабатывать и когда сервер его прогоняет."""

from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app.core import clock
from app.core.clock import aligned_step_minutes
from app.services.meta_rules import schedule_allows
from app.workers.celery_app import clock_aligned

MSK = ZoneInfo("Europe/Moscow")


@pytest.fixture(autouse=True)
def thirty_minute_runs(monkeypatch):
    monkeypatch.setattr(clock.settings, "meta_rules_interval_minutes", 30)
    monkeypatch.setattr(clock.settings, "default_timezone", "Europe/Moscow")


def _rule(kind: str, schedule: dict | None = None):
    return SimpleNamespace(schedule_kind=kind, schedule=schedule)


def _msk(hour: int, minute: int, day: int = 17) -> datetime:
    return datetime(2026, 9, day, hour, minute, 3, tzinfo=MSK).astimezone(UTC)


def test_steps_snap_to_the_clock() -> None:
    assert aligned_step_minutes(30) == 30
    assert aligned_step_minutes(5) == 15
    assert aligned_step_minutes(45) == 30
    assert aligned_step_minutes(90) == 60
    assert aligned_step_minutes(5000) == 1440
    half_hour = clock_aligned(30)
    assert half_hour.minute == {0, 30}
    three_hours = clock_aligned(180)
    assert three_hours.minute == {0} and three_hours.hour == {0, 3, 6, 9, 12, 15, 18, 21}


def test_midnight_is_moscow_and_fires_once() -> None:
    rule = _rule("daily_midnight")
    # 00:00 по Москве — это 21:00 UTC: раньше правило ждало полночь UTC.
    assert schedule_allows(rule, _msk(0, 0), _msk(23, 30, day=16))
    assert not schedule_allows(rule, _msk(3, 0), _msk(2, 30))
    # Следующий прогон той же ночи полночь уже не засчитывает.
    assert not schedule_allows(rule, _msk(0, 30), _msk(0, 0))
    # Прогон опоздал на несколько секунд после полуночи — всё равно срабатывает.
    assert schedule_allows(rule, _msk(0, 1), _msk(23, 31, day=16))
    # Без прошлой отметки (первая проверка) — смотрим на один шаг назад.
    assert schedule_allows(rule, _msk(0, 0))
    # Сервер лежал всю ночь — утром «полуночное» правило не догоняем.
    assert not schedule_allows(rule, _msk(9, 0), _msk(20, 0, day=16))


def test_custom_hours_are_moscow_and_short_windows_are_not_lost() -> None:
    rule = _rule("custom", {"days": [], "intervals": [{"begin": "10:00", "end": "12:00"}]})
    assert schedule_allows(rule, _msk(10, 30), _msk(10, 0))
    assert not schedule_allows(rule, _msk(12, 30), _msk(12, 0))

    short = _rule("custom", {"days": [], "intervals": [{"begin": "10:05", "end": "10:10"}]})
    # Прогоны в 10:00 и 10:30 окно не задевают — засчитываем его прогону 10:30.
    assert not schedule_allows(short, _msk(10, 0), _msk(9, 30))
    assert schedule_allows(short, _msk(10, 30), _msk(10, 0))
    assert not schedule_allows(short, _msk(11, 0), _msk(10, 30))

    # 17.09.2026 — четверг (4). Окно только по пятницам.
    friday = _rule("custom", {"days": [5], "intervals": [{"begin": "10:00", "end": "12:00"}]})
    assert not schedule_allows(friday, _msk(10, 30), _msk(10, 0))
    assert schedule_allows(friday, _msk(10, 30, day=18), _msk(10, 0, day=18))

    late = _rule("custom", {"days": [], "intervals": [{"begin": "23:50", "end": "23:55"}]})
    assert schedule_allows(late, _msk(0, 0, day=18), _msk(23, 30))


def test_always_is_every_run() -> None:
    assert schedule_allows(_rule("always"), _msk(4, 30), _msk(4, 0))
