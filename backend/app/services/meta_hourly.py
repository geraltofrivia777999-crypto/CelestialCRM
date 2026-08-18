"""Расход по часам для одного объекта Meta.

Данные не хранятся: их спрашивают точечно — открыли карточку объявления,
посмотрели, закрыли. Держать почасовую копию всей статистики значило бы
раздуть базу в двадцать четыре раза ради редкого взгляда.

Часы считает сама Meta и только в таймзоне рекламного кабинета — других
вариантов у Graph API нет. Поэтому таймзона уезжает в ответ рядом с числами:
без неё «пик в 19:00» ничего не значит, если кабинет живёт в другом поясе.
"""

import json
from datetime import date
from decimal import Decimal

from app.services.meta import MetaClient, decimal_value, hour_of, integer_value

# Столько живёт закешированный ответ. Меньше минуты смысла нет — Meta всё равно
# обновляет инсайты с задержкой; больше десяти — человек нажмёт «обновить»
# и не поймёт, почему ничего не изменилось.
CACHE_TTL_SECONDS = 300
HOURS = 24


def empty_hours() -> list[dict]:
    return [
        {"hour": hour, "spend": 0.0, "impressions": 0, "clicks": 0, "link_clicks": 0}
        for hour in range(HOURS)
    ]


def fold(rows: list[dict]) -> list[dict]:
    """Свести ответ Meta в 24 корзины, сложив дни периода.

    Строка без распознанного часа отбрасывается, а не приписывается к нулевому:
    лишний расход в полночь выглядел бы как реальный ночной пик.
    """
    buckets = empty_hours()
    for row in rows:
        hour = hour_of(row)
        if hour is None:
            continue
        bucket = buckets[hour]
        bucket["spend"] += float(decimal_value(row.get("spend")))
        bucket["impressions"] += integer_value(row.get("impressions"))
        bucket["clicks"] += integer_value(row.get("clicks"))
        bucket["link_clicks"] += integer_value(row.get("inline_link_clicks"))
    for bucket in buckets:
        bucket["spend"] = round(bucket["spend"], 2)
    return buckets


def summarize(buckets: list[dict]) -> dict:
    """Итог и самый дорогой час — то, ради чего окно и открывают."""
    total = round(sum(bucket["spend"] for bucket in buckets), 2)
    peak = max(buckets, key=lambda bucket: bucket["spend"])
    return {
        "spend": total,
        "impressions": sum(bucket["impressions"] for bucket in buckets),
        "clicks": sum(bucket["clicks"] for bucket in buckets),
        "link_clicks": sum(bucket["link_clicks"] for bucket in buckets),
        # Пик показываем только когда деньги вообще были: «пик в 00:00 на нуле»
        # читается как факт, хотя это просто первая корзина.
        "peak_hour": peak["hour"] if total > 0 else None,
        "peak_spend": peak["spend"] if total > 0 else None,
    }


async def load(
    client: MetaClient, external_id: str, start: date, end: date
) -> list[dict]:
    return fold(await client.hourly_insights(external_id, start, end))


def cache_key(workspace_id: object, external_id: str, start: date, end: date) -> str:
    return f"meta:hourly:{workspace_id}:{external_id}:{start.isoformat()}:{end.isoformat()}"


def encode(payload: dict) -> str:
    return json.dumps(payload, default=_plain)


def _plain(value: object) -> object:
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"Not JSON serializable: {type(value)!r}")
