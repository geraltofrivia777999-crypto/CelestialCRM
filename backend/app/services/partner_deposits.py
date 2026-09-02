"""Раскладка депозитов ПП по финансовой таблице.

Сервис партнёрок отдаёт факты «дата — оффер CRM — тег — депозиты». Куда именно
их положить, определяет **сама финансовая таблица**: строка тега живёт под
конкретным оффером в книге конкретного баера, и пара «оффер + тег» однозначно
указывает на ячейку. Ничего заводить отдельно не нужно — тег, созданный в
книге, и есть привязка.

**Почему пара, а не один тег.** У оффера бывает несколько баеров: определять
получателя по офферу — значит положить один депозит в книгу каждому и задвоить
доход. А один только тег не всегда однозначен — зато под конкретным оффером он
уже указывает на единственную строку. Одинаковый тег под разными офферами —
норма, а не повод гадать.

Порядок поиска:

1. строка тега под этим оффером в книге за месяц факта — пишем прямо в неё;
2. строки за месяц нет, но в прошлых месяцах она была — берём того же баера и
   заводим строку в нужном месяце (книги и так переносятся с месяца на месяц);
3. нигде нет — факт откладывается в `PartnerPendingTag`, а не выбрасывается.
   Это реальные деньги, и «синк прошёл, записей 0» вместо них — худший из
   возможных ответов. Заведите тег под оффером в книге — следующий синк за тот
   же период разложит всё задним числом.

Повторный синк не задваивает: значение за день перезаписывается. Ручные правки
финансиста живут до следующего синка — данные ПП приоритетны, в этом и смысл
интеграции.
"""

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    FinanceBook,
    FinanceBookOffer,
    FinanceOfferTag,
    FinanceTagDay,
    Offer,
    PartnerPendingTag,
)
from app.services.country_tiers import tier_for, tier_map

# Причины, по которым факт не лёг в книгу.
PENDING_NO_BUYER = "no_buyer"
PENDING_AMBIGUOUS = "ambiguous"


def normalize_tag(value: object) -> str:
    """Ключ тега: без пробелов по краям, регистр не важен.

    ПП присылает тег как есть, и `LUKA`, `luka` и `Luka ` — один и тот же баер.
    Сравнивать их посимвольно значило бы завести три строки в книге.
    """
    return " ".join(str(value or "").split()).casefold()


def parse_offer_id(raw: object) -> uuid.UUID | None:
    """`offer_id` из ответа сервиса.

    В инструкции сервиса пример показан числом (`123`), у нас офферы — UUID.
    Раньше несоответствие роняло весь синк с необработанным `ValueError`:
    ловим здесь и возвращаем None, чтобы пропустить один факт, а не пачку.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return uuid.UUID(text)
    except (ValueError, AttributeError, TypeError):
        return None


def parse_amount(raw: object) -> Decimal | None:
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None


async def _book_for(
    db: AsyncSession, offer: Offer, buyer_id: uuid.UUID, day: date, tiers: dict[str, str]
) -> FinanceBook:
    """Книга баера за месяц даты: есть — берём, нет — создаём переносом
    офферов с прошлого месяца (как это делает пустой GET /finance/book)."""
    year, month = day.year, day.month
    tier = tier_for(offer.geo, tiers)
    book = await db.scalar(
        select(FinanceBook).where(
            FinanceBook.workspace_id == offer.workspace_id,
            FinanceBook.buyer_id == buyer_id,
            FinanceBook.year == year,
            FinanceBook.month == month,
            FinanceBook.tier == tier,
        )
    )
    if book:
        return book
    book = FinanceBook(
        workspace_id=offer.workspace_id,
        buyer_id=buyer_id,
        year=year,
        month=month,
        tier=tier,
    )
    db.add(book)
    await db.flush()
    previous = date(year - 1, 12, 1) if month == 1 else date(year, month - 1, 1)
    previous_rows = list(
        (
            await db.execute(
                select(FinanceBookOffer)
                .join(FinanceBook, FinanceBook.id == FinanceBookOffer.book_id)
                .where(
                    FinanceBook.workspace_id == offer.workspace_id,
                    FinanceBook.buyer_id == buyer_id,
                    FinanceBook.tier == tier,
                    and_(FinanceBook.year == previous.year, FinanceBook.month == previous.month),
                )
            )
        ).scalars()
    )
    for row in previous_rows:
        copy = FinanceBookOffer(
            book_id=book.id,
            position=row.position,
            name=row.name,
            partner=row.partner,
            geo=row.geo,
            rate=row.rate,
            rate_currency=row.rate_currency,
            source_offer_id=row.source_offer_id,
        )
        db.add(copy)
        await db.flush()
        for tag in (
            await db.execute(
                select(FinanceOfferTag).where(FinanceOfferTag.offer_id == row.id)
            )
        ).scalars():
            db.add(FinanceOfferTag(offer_id=copy.id, position=tag.position, name=tag.name))
    return book


async def _offer_row(db: AsyncSession, book: FinanceBook, offer: Offer) -> FinanceBookOffer:
    """Строка оффера в книге: по связи со справочником, иначе по имени."""
    row = await db.scalar(
        select(FinanceBookOffer).where(
            FinanceBookOffer.book_id == book.id,
            FinanceBookOffer.source_offer_id == offer.id,
        )
    )
    if row:
        return row
    row = await db.scalar(
        select(FinanceBookOffer).where(
            FinanceBookOffer.book_id == book.id, FinanceBookOffer.name == offer.name
        )
    )
    if row:
        # Нашлась по имени — привяжем к справочнику, дальше найдётся напрямую.
        row.source_offer_id = offer.id
        await db.flush()
        return row
    # Новая строка встаёт в конец книги, а не нулевой позицией поверх остальных.
    last = await db.scalar(
        select(func.max(FinanceBookOffer.position)).where(FinanceBookOffer.book_id == book.id)
    )
    row = FinanceBookOffer(
        book_id=book.id,
        position=int(last or 0) + 1,
        name=offer.name,
        geo=offer.geo,
        rate=Decimal("0"),
        rate_currency="USD",
        source_offer_id=offer.id,
    )
    db.add(row)
    await db.flush()
    return row


async def _tag_row(
    db: AsyncSession, offer_row: FinanceBookOffer, tag_name: str
) -> FinanceOfferTag:
    """Строка тега под оффером. Сравнение по нормализованному имени, чтобы
    `LUKA` и `luka` не превратились в две строки."""
    key = normalize_tag(tag_name)
    rows = list(
        (
            await db.execute(
                select(FinanceOfferTag).where(FinanceOfferTag.offer_id == offer_row.id)
            )
        ).scalars()
    )
    for row in rows:
        if normalize_tag(row.name) == key:
            return row
    tag = FinanceOfferTag(
        offer_id=offer_row.id, position=len(rows), name=tag_name
    )
    db.add(tag)
    await db.flush()
    return tag


async def tag_rows_for(
    db: AsyncSession, workspace_id: uuid.UUID, offer: Offer, tag_key: str
) -> list[tuple[FinanceBook, FinanceOfferTag]]:
    """Строки тега, заведённые под этим оффером в книгах.

    Ключ — пара «оффер + тег», а не тег сам по себе. Так и работает финансовая
    таблица: строка тега живёт под конкретным оффером в книге конкретного
    баера, и эта пара однозначно указывает на ячейку. Одинаковый тег под
    разными офферами — норма, а не повод гадать, чей он.
    """
    rows = await db.execute(
        select(FinanceBook, FinanceBookOffer, FinanceOfferTag)
        .join(FinanceBookOffer, FinanceBookOffer.book_id == FinanceBook.id)
        .join(FinanceOfferTag, FinanceOfferTag.offer_id == FinanceBookOffer.id)
        .where(FinanceBook.workspace_id == workspace_id)
    )
    found: list[tuple[FinanceBook, FinanceOfferTag]] = []
    for book, offer_row, tag_row in rows:
        same_offer = (
            offer_row.source_offer_id == offer.id
            or (offer_row.name or "").strip().casefold()
            == (offer.name or "").strip().casefold()
        )
        if same_offer and normalize_tag(tag_row.name) == tag_key:
            found.append((book, tag_row))
    # Свежие книги первыми: по ним и определяется баер, если за месяц факта
    # строки ещё нет.
    found.sort(key=lambda pair: (pair[0].year, pair[0].month), reverse=True)
    return found


async def resolve_cell(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    offer: Offer,
    tag: str,
    day: date,
    tiers: dict[str, str],
    cache: dict,
) -> tuple[FinanceOfferTag | None, str]:
    """Ячейка финансовой таблицы под этот факт.

    Ничего заводить в настройках не нужно: тег, созданный в книге под оффером,
    и есть привязка. Если за месяц факта строки ещё нет, а в прошлых месяцах
    она была — берём того же баера и заводим строку в нужном месяце сами
    (книги и так переносятся с месяца на месяц).
    """
    key = normalize_tag(tag)
    cache_key = (offer.id, key)
    if cache_key in cache:
        buyer_id = cache[cache_key]
        if buyer_id is None:
            return None, PENDING_NO_BUYER
    else:
        rows = await tag_rows_for(db, workspace_id, offer, key)
        if not rows:
            cache[cache_key] = None
            return None, PENDING_NO_BUYER
        exact = [
            pair for pair in rows
            if pair[0].year == day.year and pair[0].month == day.month
        ]
        if exact:
            owners = {pair[0].buyer_id for pair in exact}
            if len(owners) > 1:
                return None, PENDING_AMBIGUOUS
            cache[cache_key] = exact[0][0].buyer_id
            return exact[0][1], ""
        owners = {pair[0].buyer_id for pair in rows}
        if len(owners) > 1:
            return None, PENDING_AMBIGUOUS
        buyer_id = rows[0][0].buyer_id
        cache[cache_key] = buyer_id
    # Строки за месяц факта нет — заводим её у того же баера.
    book = await _book_for(db, offer, buyer_id, day, tiers)
    offer_row = await _offer_row(db, book, offer)
    return await _tag_row(db, offer_row, tag), ""


async def _remember_pending(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    tag: str,
    reason: str,
    deposits: Decimal,
    offer_ref: str | None,
) -> None:
    key = normalize_tag(tag)
    # sample_offer_id — UUID-колонка: ссылка на оффер CRM. Если факт пришёл
    # по внешнему ID из ПП (оффер ещё не найден), сохранить её нельзя —
    # это не ошибка, просто сэмпл не указываем.
    try:
        offer_uuid = uuid.UUID(str(offer_ref)) if offer_ref else None
    except ValueError:
        offer_uuid = None
    now = datetime.now(UTC)
    row = await db.scalar(
        select(PartnerPendingTag).where(
            PartnerPendingTag.workspace_id == workspace_id,
            PartnerPendingTag.tag_key == key,
        )
    )
    if not row:
        row = PartnerPendingTag(
            workspace_id=workspace_id,
            tag_key=key,
            tag=" ".join(str(tag).split()) or "—",
            reason=reason,
            facts_count=0,
            deposits_total=Decimal("0"),
            sample_offer_id=offer_uuid,
            first_seen_at=now,
        )
        db.add(row)
    row.reason = reason
    row.facts_count += 1
    row.deposits_total = (row.deposits_total or Decimal("0")) + deposits
    row.last_seen_at = now
    if offer_uuid and not row.sample_offer_id:
        row.sample_offer_id = offer_uuid
    await db.flush()


async def clear_pending(db: AsyncSession, workspace_id: uuid.UUID, tag_key: str) -> None:
    row = await db.scalar(
        select(PartnerPendingTag).where(
            PartnerPendingTag.workspace_id == workspace_id,
            PartnerPendingTag.tag_key == tag_key,
        )
    )
    if row:
        await db.delete(row)


async def distribute_deposits(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    facts: list[dict],
) -> dict:
    """Разложить факты ПП по книгам баеров.

    `facts` — ответ /stats сервиса: [{date, offer_id, tag, deposits}].
    `offer_id` — ID оффера из партнёрского сервиса. Оффер CRM находится по
    `external_id` (поле «ID» на карточке оффера): привязка строится сама, без
    ручного маппинга.
    """
    upserted = 0
    pending = 0
    skipped = 0
    reasons: list[str] = []
    offers: dict[str, Offer | None] = {}
    buyers: dict[str, tuple[uuid.UUID | None, str]] = {}
    tiers = await tier_map(db, workspace_id)
    by_external: dict[str, Offer | None] = {}

    # Причина у пачки фактов почти всегда одна и та же: один неразобранный тег
    # даёт по строке на каждый день. Копим со счётчиком, иначе человек читает
    # одно и то же предложение подряд и не видит за ним остальных причин.
    counted: dict[str, int] = {}

    def note(text: str) -> None:
        counted[text] = counted.get(text, 0) + 1

    async def offer_by_external_id(external: str) -> Offer | None:
        if external in by_external:
            return by_external[external]
        # Новый путь: оффер заведён руками и несёт «ID» из партнёрки в
        # `external_id`. Старый — факт шлёт UUID самого оффера: тоже принимаем.
        found = await db.scalar(
            select(Offer).where(
                Offer.workspace_id == workspace_id,
                Offer.external_id == external,
            )
        )
        if found is None:
            try:
                found = await db.get(Offer, uuid.UUID(external))
            except ValueError:
                found = None
        by_external[external] = found
        return found

    for fact in facts:
        try:
            day = date.fromisoformat(str(fact.get("date"))[:10])
        except (ValueError, TypeError):
            skipped += 1
            note(f"битая дата: {fact.get('date')!r}")
            continue
        # ID оффера из ПП — строка: оффер ищется по своему полю «ID».
        external = str(fact.get("offer_id") or "").strip()
        if not external:
            skipped += 1
            note("факт без offer_id")
            continue
        if external not in offers:
            offers[external] = await offer_by_external_id(external)
        offer = offers[external]
        # Воркспейс проверяем здесь, а не только в вызывающем коде: функция
        # раскладывает деньги и не должна полагаться на чужую фильтрацию.
        if not offer or offer.workspace_id != workspace_id:
            skipped += 1
            note(f"оффер с ID {external} не найден в этом воркспейсе")
            continue
        deposits = parse_amount(fact.get("deposits"))
        if deposits is None:
            skipped += 1
            note(f"нечисловые депозиты: {fact.get('deposits')!r}")
            continue
        tag_raw = " ".join(str(fact.get("tag") or "").split())
        if not tag_raw:
            # Спека сервиса: tag может быть null — трафик без атрибуции.
            pending += 1
            await _remember_pending(
                db, workspace_id, "(без тега)", PENDING_NO_BUYER, deposits, external
            )
            note(f"{day}: трафик без тега по офферу «{offer.name}»")
            continue
        tag_row, reason = await resolve_cell(
            db, workspace_id, offer, tag_raw, day, tiers, buyers
        )
        if not tag_row:
            pending += 1
            await _remember_pending(db, workspace_id, tag_raw, reason, deposits, external)
            note(
                f"тег «{tag_raw}» под оффером «{offer.name}»: "
                + (
                    "заведён сразу у нескольких баеров"
                    if reason == PENDING_AMBIGUOUS
                    else "такой строки в финансах нет — заведите тег под этим "
                    "оффером в книге баера, и следующий синк её заполнит"
                )
            )
            continue
        value = await db.scalar(
            select(FinanceTagDay).where(
                FinanceTagDay.tag_id == tag_row.id, FinanceTagDay.day == day.day
            )
        )
        if value:
            value.deposits = deposits
        else:
            db.add(FinanceTagDay(tag_id=tag_row.id, day=day.day, deposits=deposits))
        await db.flush()
        # Тег разобран — из очереди неразобранных его убираем.
        await clear_pending(db, workspace_id, normalize_tag(tag_raw))
        upserted += 1
    reasons = [
        text if count == 1 else f"{text} (фактов: {count})"
        for text, count in sorted(
            counted.items(), key=lambda pair: -pair[1]
        )[:20]
    ]
    return {
        "upserted": upserted,
        "pending": pending,
        "skipped": skipped,
        "reasons": reasons,
    }
