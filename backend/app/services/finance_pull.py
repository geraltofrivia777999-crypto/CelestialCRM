"""Оффер справочника → книга баера.

Назначили баера на оффер — строка появляется у него в Финансах сразу, с
названием, партнёркой, гео и ставкой. Раньше её заводили руками, и книга
расходилась со справочником в мелочах: то название сокращено, то ставка
устарела, то оффер вовсе забыли завести и месяц считался без него.

Тир таблицы берётся из гео по справочнику «Настройки → Тиры стран»: у баера
две книги, и выбор между ними — это и есть тир страны.
"""

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import business_today
from app.models import FinanceBook, FinanceBookOffer, FinanceOfferTag, Offer, Partner
from app.services.country_tiers import UNASSIGNED, tier_for, tier_map


async def pull_offer_to_books(
    db: AsyncSession,
    offer: Offer,
    buyer_ids: list[uuid.UUID],
    *,
    today: date | None = None,
) -> list[uuid.UUID]:
    """Завести оффер в книгах перечисленных баеров за текущий месяц.

    Возвращает id баеров, которым строка добавилась. Уже заведённый оффер
    обновляется, а не дублируется: у строки есть ссылка на справочник.

    Оффер без гео не переносится: тир — это страна, и угадывать её нельзя.
    Прошлые месяцы не трогаются — закрытый месяц не должен меняться из-за
    сегодняшнего назначения.
    """
    from app.services.finance_spend import create_book_from_previous, lock_workspace

    await lock_workspace(db, offer.workspace_id)
    if not buyer_ids:
        return []
    tiers = await tier_map(db, offer.workspace_id)
    tier = tier_for(offer.geo, tiers)
    if tier == UNASSIGNED:
        return []

    moment = today or business_today()
    partner = await _partner_name(db, offer)
    touched: list[uuid.UUID] = []
    for buyer_id in buyer_ids:
        book = await db.scalar(
            select(FinanceBook).where(
                FinanceBook.workspace_id == offer.workspace_id,
                FinanceBook.buyer_id == buyer_id,
                FinanceBook.year == moment.year,
                FinanceBook.month == moment.month,
                FinanceBook.tier == tier,
            )
        )
        if book is None:
            book = await create_book_from_previous(
                db, offer.workspace_id, buyer_id, moment.year, moment.month, tier
            )

        row = await db.scalar(
            select(FinanceBookOffer).where(
                FinanceBookOffer.book_id == book.id,
                FinanceBookOffer.source_offer_id == offer.id,
            )
        )
        if row is not None:
            # Ручная правка защищена замком. Остальные поля актуализируются при
            # каждом сохранении оффера, включая уже расшаренные книги.
            locked = set(row.locked_fields or [])
            if "name" not in locked:
                row.name = offer.name
            if "partner" not in locked:
                row.partner = partner
            if "geo" not in locked:
                row.geo = offer.geo
            if "rate" not in locked:
                row.rate = offer.cpa
            if "rate_currency" not in locked:
                row.rate_currency = offer.cpa_currency or "USD"
            continue

        position = await db.scalar(
            select(FinanceBookOffer.position)
            .where(FinanceBookOffer.book_id == book.id)
            .order_by(FinanceBookOffer.position.desc())
            .limit(1)
        )
        row = FinanceBookOffer(
            book_id=book.id,
            position=(position or 0) + 1 if position is not None else 0,
            name=offer.name,
            partner=partner,
            geo=offer.geo,
            rate=offer.cpa,
            rate_currency=offer.cpa_currency or "USD",
            source_offer_id=offer.id,
        )
        db.add(row)
        await db.flush()
        # Пустая строка под оффером: имя ей даёт сам баер — «SOK», «долёты»,
        # что угодно. Без неё вводить депозиты некуда.
        db.add(FinanceOfferTag(offer_id=row.id, position=0, name=""))
        touched.append(buyer_id)
    return touched


async def push_book_changes_to_offers(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    rows: list[dict],
) -> list[str]:
    """Вернуть гео и ставку из книги обратно в справочник офферов.

    Связь двусторонняя намеренно: правку чаще замечает тот, кто ведёт книгу —
    партнёрка подняла ставку, гео связки уточнилось. Если бы справочник об этом
    не узнавал, следующее назначение того же оффера привезло бы другому баеру
    старые числа, и две таблицы разошлись бы молча.

    Название не синхронизируется: в книге его правят под свою связку, и
    переписывать им справочник значило бы терять исходное имя оффера.

    Строка между таблицами не переезжает, даже если новое гео другого тира:
    депозиты и затраты уже введены в эту таблицу. Несовпадение видно по значку
    тира рядом с гео.
    """
    wanted = {row["source_offer_id"]: row for row in rows if row.get("source_offer_id")}
    if not wanted:
        return []
    offers = list(
        (
            await db.execute(
                select(Offer).where(
                    Offer.workspace_id == workspace_id,
                    Offer.id.in_(list(wanted)),
                )
            )
        ).scalars()
    )
    changed: list[str] = []
    for offer in offers:
        row = wanted[offer.id]
        locked = set(row.get("locked_fields") or [])
        geo = offer.geo if "geo" in locked else row.get("geo")
        rate = offer.cpa if "rate" in locked else row.get("rate")
        currency = (
            offer.cpa_currency
            if "rate_currency" in locked
            else (row.get("rate_currency") or "USD")
        )
        if offer.geo == geo and offer.cpa == rate and offer.cpa_currency == currency:
            continue
        offer.geo = geo
        offer.cpa = rate
        offer.cpa_currency = currency
        changed.append(offer.name)
    return changed


async def _partner_name(db: AsyncSession, offer: Offer) -> str | None:
    """Название партнёрки отдельным запросом, а не через связь оффера.

    У только что созданного оффера связь `partner` ещё не загружена, и
    обращение к ней в асинхронной сессии падает `MissingGreenlet`: создать
    оффер сразу с партнёркой и баером было нельзя. Из базы оффер приходит уже
    со связью, поэтому назначение вторым шагом работало и ошибку прятало.
    """
    if not offer.partner_id:
        return None
    return await db.scalar(select(Partner.name).where(Partner.id == offer.partner_id))
