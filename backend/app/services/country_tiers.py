"""Тир страны — «Настройки → Тиры стран».

Тир принадлежит гео, а не человеку и не офферу: один баер в одном месяце ведёт
и Tier1, и Tier2/3. Поэтому в книге выбирается гео, а тир к нему подставляет
этот справочник — руками его не ставят, иначе два оффера на одну страну могли
бы разойтись по тирам, и сводка перестала бы сходиться сама с собой.

В таблице хранится только то, что отличается от умолчания: страна без строки —
Tier2/3.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CountryTier
from app.services.geo import normalize_geo

TIER_1 = "T1"
TIER_23 = "T23"
UNASSIGNED = "unassigned"

# Стартовый список Tier1. Это не константа поведения, а заготовка: команда
# правит её в настройках под договорённости с партнёрками.
DEFAULT_TIER_1 = (
    "US", "CA", "GB", "DE", "FR", "AT", "CH", "NO", "SE", "DK",
    "FI", "IS", "NL", "BE", "LU", "IE", "IT", "ES", "CZ", "PL",
    "PT", "SI", "AU", "NZ", "JP", "SG", "AE", "KR", "IL", "SA",
)


async def tier_map(db: AsyncSession, workspace_id: uuid.UUID) -> dict[str, str]:
    """Код страны → тир. Чего нет в ответе, то Tier2/3."""
    rows = await db.execute(
        select(CountryTier.code, CountryTier.tier).where(
            CountryTier.workspace_id == workspace_id
        )
    )
    return {code: tier for code, tier in rows}


def tier_for(geo: str | None, tiers: dict[str, str]) -> str:
    """Тир одного гео.

    Пустое гео — это не Tier2/3, а «неизвестно»: оффер без страны попадает в
    отдельную строку сводки, чтобы догадка не выглядела как измеренный факт.
    """
    code = normalize_geo(geo)
    if not code:
        return UNASSIGNED
    return tiers.get(code, TIER_23)
