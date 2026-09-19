"""Спенд Медиаборда → книги баера, разложенный по тирам.

Спенд вводили в книгу руками, хотя он уже есть на доске: там он собирается из
агентов и платёжек с их комиссиями. Двойной ввод расходился — в книге одна
цифра, в Медиаборде другая, и спор «где правда» решался не в пользу CRM.

Разложить его по тирам стало можно после того, как доска научилась считать тир:
тир — это страна оффера по справочнику «Настройки → Тиры стран», а у баера под
каждый тир своя таблица. Поэтому расход одного дня разъезжается по двум книгам
ровно так же, как разъезжаются сами офферы.

Оффер без гео (тир «не определён») в книгу не попадает: угадывать за человека,
Tier1 это или нет, нельзя, а молча приписать к одному из тиров — значит испортить
обе таблицы. Такой расход возвращается отдельным числом, чтобы его было видно.
"""

import calendar
import uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    FinanceBook,
    FinanceBookDay,
    FinanceBookOffer,
    FinanceOfferTag,
    MediaRecord,
    MediaSpendValue,
    Offer,
    SpendProvider,
    Workspace,
)
from app.services.country_tiers import UNASSIGNED, tier_for, tier_map

ZERO = Decimal("0")
BOOK_TIERS = ("T1", "T23")


def _provider_amount():
    """Та же формула, что на доске: ручная правка перебивает комиссию."""
    return func.round(
        func.coalesce(
            MediaSpendValue.manual_amount_override,
            func.round(
                MediaSpendValue.base_amount * (SpendProvider.commission_pct / 100 + 1), 4
            ),
        ),
        4,
    )


async def spend_by_tier_and_day(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    buyer_id: uuid.UUID,
    year: int,
    month: int,
) -> tuple[dict[str, dict[int, Decimal]], Decimal]:
    """Расход баера за месяц: {тир: {день: сумма}} и остаток без тира.

    Источник тот же, что и у колонки SPEND на доске: ручная правка записи
    перебивает разбивку по платёжкам, а она — сохранённое значение.
    """
    days_in_month = calendar.monthrange(year, month)[1]
    first = date(year, month, 1)
    last = date(year, month, days_in_month)

    providers = (
        select(
            MediaSpendValue.media_record_id.label("record_id"),
            func.sum(_provider_amount()).label("amount"),
        )
        .join(SpendProvider, SpendProvider.id == MediaSpendValue.provider_id)
        .group_by(MediaSpendValue.media_record_id)
        .subquery("record_providers")
    )
    effective = func.coalesce(
        MediaRecord.spend_override,
        providers.c.amount,
        MediaRecord.spend_calculated,
    )
    rows = (
        await db.execute(
            select(
                MediaRecord.record_date,
                Offer.geo,
                func.sum(effective).label("spend"),
            )
            .join(Offer, Offer.id == MediaRecord.offer_id)
            .outerjoin(providers, providers.c.record_id == MediaRecord.id)
            .where(
                MediaRecord.workspace_id == workspace_id,
                MediaRecord.buyer_id == buyer_id,
                MediaRecord.record_date >= first,
                MediaRecord.record_date <= last,
            )
            .group_by(MediaRecord.record_date, Offer.geo)
        )
    ).all()

    tiers = await tier_map(db, workspace_id)
    by_tier: dict[str, dict[int, Decimal]] = {tier: {} for tier in BOOK_TIERS}
    unassigned = ZERO
    for row in rows:
        amount = Decimal(str(row.spend or 0))
        tier = tier_for(row.geo, tiers)
        if tier == UNASSIGNED:
            unassigned += amount
            continue
        day = row.record_date.day
        by_tier[tier][day] = by_tier[tier].get(day, ZERO) + amount
    return by_tier, unassigned


async def refresh_for_record(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    buyer_id: uuid.UUID,
    day: date,
) -> None:
    """Обновление в той же транзакции: ошибка не должна оставить разные суммы."""
    await pull_spend_to_books(db, workspace_id, buyer_id, day.year, day.month)


async def create_book_from_previous(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    buyer_id: uuid.UUID,
    year: int,
    month: int,
    tier: str,
) -> FinanceBook:
    """Create an automatic book with the same template as an unopened month.

    The caller owns the workspace lock. Only offer/tag structure and the currency
    rate carry forward; deposits, manual spend and other day values do not.
    """
    from app.services.finance_books import before_period, recalculate_carry_chain

    matching = select(FinanceBook).where(
        FinanceBook.workspace_id == workspace_id,
        FinanceBook.buyer_id == buyer_id,
        FinanceBook.tier == tier,
    )
    previous = await db.scalar(matching.where(before_period(year, month)).order_by(
        FinanceBook.year.desc(), FinanceBook.month.desc(),
    ).limit(1))
    opening = ZERO
    if previous is None:
        first = await db.scalar(matching.order_by(
            FinanceBook.year, FinanceBook.month,
        ).limit(1))
        if first is not None:
            # A backfill can insert a new first month. Preserve the legacy opening
            # balance as the beginning of that longer chain, rather than replacing
            # it with the default zero of the new book. Recalculation then assigns
            # the resulting carry to the former first month exactly once.
            opening = max(first.prev_minus or ZERO, ZERO)

    book = FinanceBook(
        workspace_id=workspace_id,
        buyer_id=buyer_id,
        year=year,
        month=month,
        tier=tier,
        prev_minus=opening,
        eur_usd_rate=(previous.eur_usd_rate or Decimal("1")) if previous else Decimal("1"),
    )
    db.add(book)
    await db.flush()
    if previous is None:
        await recalculate_carry_chain(db, workspace_id, buyer_id, tier)
        return book

    offers = (await db.scalars(select(FinanceBookOffer).where(
        FinanceBookOffer.book_id == previous.id,
    ).order_by(FinanceBookOffer.position, FinanceBookOffer.name))).all()
    if not offers:
        await recalculate_carry_chain(db, workspace_id, buyer_id, tier)
        return book
    tags = (await db.scalars(select(FinanceOfferTag).where(
        FinanceOfferTag.offer_id.in_([offer.id for offer in offers]),
    ).order_by(FinanceOfferTag.position, FinanceOfferTag.name))).all()
    copies = {}
    for offer in offers:
        copied = FinanceBookOffer(
            book_id=book.id,
            source_offer_id=offer.source_offer_id,
            position=offer.position,
            name=offer.name,
            partner=offer.partner,
            geo=offer.geo,
            rate=offer.rate,
            rate_currency=offer.rate_currency,
            locked_fields=list(offer.locked_fields or []),
        )
        db.add(copied)
        copies[offer.id] = copied
    await db.flush()
    db.add_all([
        FinanceOfferTag(offer_id=copies[tag.offer_id].id, position=tag.position, name=tag.name)
        for tag in tags
    ])
    await db.flush()
    await recalculate_carry_chain(db, workspace_id, buyer_id, tier)
    return book


async def pull_spend_to_books(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    buyer_id: uuid.UUID,
    year: int,
    month: int,
) -> dict:
    """Сверить автоматическую часть, сохранив явное ручное переопределение."""
    from app.services.finance_books import recalculate_carry_chain

    await lock_workspace(db, workspace_id)
    by_tier, unassigned = await spend_by_tier_and_day(db, workspace_id, buyer_id, year, month)
    books = {book.tier: book for book in (await db.scalars(select(FinanceBook).where(
        FinanceBook.workspace_id == workspace_id, FinanceBook.buyer_id == buyer_id,
        FinanceBook.year == year, FinanceBook.month == month,
    ))).all()}
    written = 0
    changed = ZERO
    for tier, days in by_tier.items():
        book = books.get(tier)
        if book is None:
            if not any(days.values()):
                continue
            book = await create_book_from_previous(
                db, workspace_id, buyer_id, year, month, tier,
            )
        existing = {row.day: row for row in (await db.scalars(select(FinanceBookDay).where(
            FinanceBookDay.book_id == book.id
        ))).all()}
        # Previously imported days must be reset even if their source was removed
        # or moved to the other tier. Untouched manual-only days remain manual.
        affected_days = set(days) | {d for d, row in existing.items() if row.media_spend is not None}
        for day in sorted(affected_days):
            # Спенд книги — целые доллары: в ячейку шириной в три цифры
            # «1259.9876» не помещается и обрезается на глазах, а копейки
            # рекламных кабинетов на решения финансиста не влияют. Округляем
            # по обычным правилам, до половины вверх.
            value = days.get(day, ZERO).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            row = existing.get(day)
            if row is None:
                row = FinanceBookDay(book_id=book.id, day=day, spend_buyer=ZERO)
                db.add(row)
            elif row.media_spend is None and row.manual_spend is None and row.spend_buyer:
                # Compatibility with pre-migration/imported legacy rows.
                row.manual_spend = row.spend_buyer
            effective = row.manual_spend if row.manual_spend is not None else value
            if row.media_spend != value or row.spend_buyer != effective:
                changed += effective - (row.spend_buyer or ZERO)
                row.media_spend = value
                row.spend_buyer = effective
                written += 1
        await db.flush()
    if written:
        for tier in BOOK_TIERS:
            await recalculate_carry_chain(db, workspace_id, buyer_id, tier)
    return {"written": written, "changed": changed, "unassigned": unassigned, "missing_books": []}


async def refresh_period(db: AsyncSession, workspace_id: uuid.UUID,
                         buyer_ids: set[uuid.UUID], year: int, month: int) -> Decimal:
    """Same reconciliation for personal books, team summaries and payroll."""
    if not buyer_ids:
        return ZERO
    await lock_workspace(db, workspace_id)
    unassigned = ZERO
    for buyer_id in sorted(buyer_ids, key=str):
        result = await pull_spend_to_books(db, workspace_id, buyer_id, year, month)
        unassigned += result["unassigned"]
    return unassigned


async def refresh_workspace(db: AsyncSession, workspace_id: uuid.UUID) -> None:
    """Country/offer/commission changes affect every month shown on the media board."""
    await lock_workspace(db, workspace_id)
    records = (await db.execute(select(MediaRecord.buyer_id, MediaRecord.record_date).where(
        MediaRecord.workspace_id == workspace_id
    ).distinct())).all()
    periods = {(buyer, day.year, day.month) for buyer, day in records}
    # Include removed/reclassified sources whose previous automatic amount remains.
    periods.update((await db.execute(select(FinanceBook.buyer_id, FinanceBook.year, FinanceBook.month)
        .where(FinanceBook.workspace_id == workspace_id))).all())
    for buyer, year, month in sorted(periods, key=lambda r: (str(r[0]), r[1], r[2])):
        await pull_spend_to_books(db, workspace_id, buyer, year, month)


async def lock_workspace(db: AsyncSession, workspace_id: uuid.UUID) -> None:
    """One lock order across source edits, individual books and all-team payroll.

    NO KEY UPDATE allows FK inserts referencing the workspace and avoids lock
    upgrade deadlocks. Acquire before writes or per-buyer locks, never afterward.
    """
    with db.no_autoflush:
        await db.execute(select(Workspace.id).where(Workspace.id == workspace_id)
                         .with_for_update(key_share=True))
