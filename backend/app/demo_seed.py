"""Наполнение базы демо-данными для нагрузочной проверки интерфейса.

Запуск (внутри контейнера api):

    python -m app.demo_seed

Объёмы настраиваются переменными окружения DEMO_*, см. константы ниже.
Скрипт идемпотентен по ключам уникальности: повторный запуск дополняет
данные, а не дублирует их. Чтобы удалить всё созданное, запустите с
DEMO_RESET=1 — будут удалены только записи с демо-префиксами.
"""

import asyncio
import os
import random
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.core.security import encrypt_secret, hash_password
from app.models import (
    AuditEvent,
    FinanceRecord,
    FinanceServiceValue,
    FinanceSpendValue,
    IntegrationConnection,
    MediaRecord,
    MediaServiceValue,
    MediaSpendValue,
    Offer,
    OfferBuyer,
    Partner,
    Role,
    Service,
    SpendProvider,
    Status,
    User,
    UserParent,
    Workspace,
)
from app.services.formulas import (
    amount_with_commission,
    finance_import_key,
    service_cost,
)

DEMO_PREFIX = "demo"

BUYERS = int(os.getenv("DEMO_BUYERS", "24"))
LEADS = int(os.getenv("DEMO_LEADS", "4"))
PARTNERS = int(os.getenv("DEMO_PARTNERS", "12"))
OFFERS = int(os.getenv("DEMO_OFFERS", "90"))
DAYS = int(os.getenv("DEMO_DAYS", "180"))
OFFERS_PER_BUYER = int(os.getenv("DEMO_OFFERS_PER_BUYER", "3"))
AUDIT_EVENTS = int(os.getenv("DEMO_AUDIT_EVENTS", "4000"))
BATCH = int(os.getenv("DEMO_BATCH", "2000"))
RESET = os.getenv("DEMO_RESET") == "1"

GEOS = [
    "BR", "MX", "IN", "ID", "PH", "VN", "TR", "EG", "NG", "ZA",
    "PL", "DE", "IT", "ES", "KZ", "UZ", "AZ", "PE", "CO", "CL",
]
VERTICALS = ["Casino", "Betting", "Crypto", "Dating", "Nutra", "Gambling", "Sweeps"]
SOURCES = ["Facebook", "Google", "TikTok", "UAC", "PWA", "Push"]
FIRST = [
    "Ivan", "Petr", "Anna", "Marat", "Dina", "Oleg", "Sergey", "Alina",
    "Timur", "Kirill", "Nurlan", "Aigerim", "Roman", "Daniil", "Elena",
    "Artem", "Vlad", "Yerlan", "Karina", "Maxim", "Damir", "Sofia",
    "Ruslan", "Alexey", "Madina", "Nikita", "Aidos", "Polina",
]
LAST = [
    "Orlov", "Sidorov", "Kim", "Nurpeisov", "Volkov", "Grishin", "Belov",
    "Zaitsev", "Ismailov", "Tokayev", "Popov", "Egorov", "Serik", "Bekov",
]


def money(value: float) -> Decimal:
    return Decimal(str(round(value, 4)))


async def flush_batch(db, rows: list, force: bool = False) -> None:
    if rows and (force or len(rows) >= BATCH):
        db.add_all(rows)
        await db.flush()
        rows.clear()


async def reset_demo(db, workspace_id) -> None:
    """Удаляет только демо-данные (по префиксу логина/имени)."""
    demo_users = (
        await db.execute(
            select(User.id).where(
                User.workspace_id == workspace_id,
                User.login.like(f"{DEMO_PREFIX}%"),
            )
        )
    ).scalars().all()
    demo_offers = (
        await db.execute(
            select(Offer.id).where(
                Offer.workspace_id == workspace_id,
                Offer.external_id.like(f"{DEMO_PREFIX}-%"),
            )
        )
    ).scalars().all()
    if demo_users or demo_offers:
        media_ids = (
            await db.execute(
                select(MediaRecord.id).where(
                    MediaRecord.workspace_id == workspace_id,
                    MediaRecord.buyer_id.in_(demo_users or [None]),
                )
            )
        ).scalars().all()
        finance_ids = (
            await db.execute(
                select(FinanceRecord.id).where(
                    FinanceRecord.workspace_id == workspace_id,
                    FinanceRecord.buyer_id.in_(demo_users or [None]),
                )
            )
        ).scalars().all()
        for model, ids in (
            (MediaServiceValue, media_ids),
            (MediaSpendValue, media_ids),
        ):
            if ids:
                await db.execute(delete(model).where(model.media_record_id.in_(ids)))
        for model, ids in (
            (FinanceServiceValue, finance_ids),
            (FinanceSpendValue, finance_ids),
        ):
            if ids:
                await db.execute(delete(model).where(model.finance_record_id.in_(ids)))
        if media_ids:
            await db.execute(delete(MediaRecord).where(MediaRecord.id.in_(media_ids)))
        if finance_ids:
            await db.execute(
                delete(FinanceRecord).where(FinanceRecord.id.in_(finance_ids))
            )
        if demo_offers:
            await db.execute(delete(OfferBuyer).where(OfferBuyer.offer_id.in_(demo_offers)))
            await db.execute(delete(Offer).where(Offer.id.in_(demo_offers)))
        await db.execute(
            delete(Partner).where(
                Partner.workspace_id == workspace_id,
                Partner.external_id.like(f"{DEMO_PREFIX}-%"),
            )
        )
        if demo_users:
            await db.execute(delete(UserParent).where(UserParent.user_id.in_(demo_users)))
            await db.execute(delete(UserParent).where(UserParent.parent_id.in_(demo_users)))
            await db.execute(
                delete(AuditEvent).where(AuditEvent.user_id.in_(demo_users))
            )
            await db.execute(delete(User).where(User.id.in_(demo_users)))
    await db.commit()
    print("Демо-данные удалены.")


async def main() -> None:
    rnd = random.Random(20260725)
    async with SessionLocal() as db:
        workspace = await db.scalar(select(Workspace).limit(1))
        if workspace is None:
            raise SystemExit("Сначала запустите базовый seed: python -m app.seed")

        if RESET:
            await reset_demo(db, workspace.id)
            return

        roles = {
            role.name: role
            for role in (
                await db.execute(select(Role).where(Role.workspace_id == workspace.id))
            ).scalars()
        }
        buyer_role = roles.get("Buyer") or roles["Administrator"]
        lead_role = roles.get("Team Lead") or buyer_role

        services = (
            await db.execute(
                select(Service).where(Service.workspace_id == workspace.id)
            )
        ).scalars().all()
        providers = (
            await db.execute(
                select(SpendProvider).where(SpendProvider.workspace_id == workspace.id)
            )
        ).scalars().all()
        if not services or not providers:
            raise SystemExit("Нет services/spend_providers: запустите python -m app.seed")

        # --- подключение (обязательно для offers/partners) -------------------
        connection = await db.scalar(
            select(IntegrationConnection).where(
                IntegrationConnection.workspace_id == workspace.id
            )
        )
        if connection is None:
            connection = IntegrationConnection(
                workspace_id=workspace.id,
                name="Demo Keitaro",
                base_url="https://demo-tracker.example.com",
                api_key_encrypted=encrypt_secret("demo-api-key"),
                status=Status.inactive,
            )
            db.add(connection)
            await db.flush()

        # --- пользователи ----------------------------------------------------
        existing_logins = set(
            (
                await db.execute(
                    select(User.login).where(User.workspace_id == workspace.id)
                )
            ).scalars()
        )
        password_hash = hash_password("demo12345")

        leads: list[User] = []
        for i in range(LEADS):
            login = f"{DEMO_PREFIX}lead{i + 1:02d}"
            if login in existing_logins:
                leads.append(
                    await db.scalar(
                        select(User).where(
                            User.workspace_id == workspace.id, User.login == login
                        )
                    )
                )
                continue
            user = User(
                workspace_id=workspace.id,
                role_id=lead_role.id,
                name=f"{rnd.choice(FIRST)} {rnd.choice(LAST)}",
                login=login,
                password_hash=password_hash,
                status=Status.active,
                keitaro_company_group=f"Team {i + 1}",
            )
            db.add(user)
            leads.append(user)
        await db.flush()

        buyers: list[User] = []
        for i in range(BUYERS):
            login = f"{DEMO_PREFIX}buyer{i + 1:03d}"
            if login in existing_logins:
                buyers.append(
                    await db.scalar(
                        select(User).where(
                            User.workspace_id == workspace.id, User.login == login
                        )
                    )
                )
                continue
            status = Status.active if i % 9 else Status.inactive
            user = User(
                workspace_id=workspace.id,
                role_id=buyer_role.id,
                name=f"{rnd.choice(FIRST)} {rnd.choice(LAST)}",
                login=login,
                password_hash=password_hash,
                status=status,
                keitaro_company_group=f"Team {i % max(LEADS, 1) + 1}",
                keitaro_offer_group=rnd.choice(VERTICALS),
            )
            db.add(user)
            buyers.append(user)
        await db.flush()

        existing_parents = set(
            (
                await db.execute(
                    select(UserParent.user_id, UserParent.parent_id)
                )
            ).all()
        )
        for i, buyer in enumerate(buyers):
            if not leads:
                break
            parent = leads[i % len(leads)]
            if (buyer.id, parent.id) not in existing_parents:
                db.add(UserParent(user_id=buyer.id, parent_id=parent.id))
        await db.flush()
        print(f"Пользователи: {len(leads)} тимлидов, {len(buyers)} байеров")

        # --- партнёры --------------------------------------------------------
        existing_partner_ext = set(
            (
                await db.execute(
                    select(Partner.external_id).where(
                        Partner.connection_id == connection.id
                    )
                )
            ).scalars()
        )
        partners: list[Partner] = []
        for i in range(PARTNERS):
            ext = f"{DEMO_PREFIX}-p{i + 1:03d}"
            if ext in existing_partner_ext:
                partners.append(
                    await db.scalar(
                        select(Partner).where(
                            Partner.connection_id == connection.id,
                            Partner.external_id == ext,
                        )
                    )
                )
                continue
            partner = Partner(
                workspace_id=workspace.id,
                connection_id=connection.id,
                external_id=ext,
                name=f"{rnd.choice(VERTICALS)} Affiliates {i + 1:02d}",
                status=Status.active if i % 7 else Status.inactive,
            )
            db.add(partner)
            partners.append(partner)
        await db.flush()

        # --- офферы ----------------------------------------------------------
        existing_offer_ext = set(
            (
                await db.execute(
                    select(Offer.external_id).where(Offer.connection_id == connection.id)
                )
            ).scalars()
        )
        offers: list[Offer] = []
        for i in range(OFFERS):
            ext = f"{DEMO_PREFIX}-o{i + 1:04d}"
            if ext in existing_offer_ext:
                offers.append(
                    await db.scalar(
                        select(Offer).where(
                            Offer.connection_id == connection.id,
                            Offer.external_id == ext,
                        )
                    )
                )
                continue
            geo = GEOS[i % len(GEOS)]
            vertical = VERTICALS[i % len(VERTICALS)]
            offer = Offer(
                workspace_id=workspace.id,
                connection_id=connection.id,
                partner_id=partners[i % len(partners)].id,
                external_id=ext,
                name=f"{vertical} {geo} {rnd.choice(SOURCES)} #{i + 1:03d}",
                geo=geo,
                group_name=vertical,
                status=Status.active if i % 11 else Status.inactive,
            )
            db.add(offer)
            offers.append(offer)
        await db.flush()
        print(f"Партнёры: {len(partners)}, офферы: {len(offers)}")

        # --- связка оффер ↔ байер -------------------------------------------
        existing_links = set(
            (await db.execute(select(OfferBuyer.offer_id, OfferBuyer.user_id))).all()
        )
        buyer_offers: dict[User, list[Offer]] = {}
        for index, buyer in enumerate(buyers):
            picked = [
                offers[(index * OFFERS_PER_BUYER + shift) % len(offers)]
                for shift in range(OFFERS_PER_BUYER)
            ]
            buyer_offers[buyer] = picked
            for offer in picked:
                if (offer.id, buyer.id) not in existing_links:
                    db.add(OfferBuyer(offer_id=offer.id, user_id=buyer.id))
                    existing_links.add((offer.id, buyer.id))
        await db.flush()

        # --- медиаборд и финансы --------------------------------------------
        today = date.today()
        start = today - timedelta(days=DAYS - 1)
        existing_media = set(
            (
                await db.execute(
                    select(
                        MediaRecord.record_date,
                        MediaRecord.buyer_id,
                        MediaRecord.offer_id,
                    ).where(MediaRecord.workspace_id == workspace.id)
                )
            ).all()
        )
        existing_finance = set(
            (
                await db.execute(
                    select(FinanceRecord.import_key).where(
                        FinanceRecord.workspace_id == workspace.id
                    )
                )
            ).scalars()
        )

        media_children: list = []
        finance_children: list = []
        media_count = 0
        finance_count = 0

        for day_offset in range(DAYS):
            record_date = start + timedelta(days=day_offset)
            season = 1.0 + 0.25 * ((day_offset % 30) / 30.0)
            weekend = 0.75 if record_date.weekday() >= 5 else 1.0
            day_records: list[MediaRecord] = []
            day_finance: list[FinanceRecord] = []

            for buyer in buyers:
                if buyer.status is not Status.active and rnd.random() < 0.8:
                    continue
                for offer in buyer_offers[buyer]:
                    if rnd.random() < 0.15:
                        continue

                    installs = int(rnd.gauss(1400, 500) * season * weekend)
                    installs = max(installs, 60)
                    registrations = int(installs * rnd.uniform(0.06, 0.22))
                    ftd = int(registrations * rnd.uniform(0.05, 0.3))
                    revenue = money(ftd * rnd.uniform(18, 140))

                    service = services[(day_offset + installs) % len(services)]
                    rent_qty = money(installs)
                    rent = service_cost(
                        rent_qty, service.install_cost, service.commission_pct
                    )

                    provider = providers[(day_offset + ftd) % len(providers)]
                    base_spend = money(installs * rnd.uniform(0.06, 0.35))
                    spend = amount_with_commission(base_spend, provider.commission_pct)

                    if (record_date, buyer.id, offer.id) not in existing_media:
                        record = MediaRecord(
                            workspace_id=workspace.id,
                            record_date=record_date,
                            buyer_id=buyer.id,
                            offer_id=offer.id,
                            installs=installs,
                            registrations=registrations,
                            ftd=ftd,
                            revenue=revenue,
                            spend_calculated=spend,
                            spend_override=(
                                money(float(spend) * rnd.uniform(0.9, 1.15))
                                if rnd.random() < 0.05
                                else None
                            ),
                            source="demo",
                            manual_fields=["installs"] if rnd.random() < 0.08 else [],
                            external_payload={"cost": float(base_spend)},
                        )
                        day_records.append(record)

                    import_key = finance_import_key(
                        record_date, buyer.id, offer.id, f"https://trk.example.com/{offer.external_id}"
                    )
                    if import_key not in existing_finance:
                        existing_finance.add(import_key)
                        day_finance.append(
                            FinanceRecord(
                                workspace_id=workspace.id,
                                record_date=record_date,
                                buyer_id=buyer.id,
                                offer_id=offer.id,
                                link=f"https://trk.example.com/{offer.external_id}",
                                import_key=import_key,
                                rent=rent,
                                spend=spend,
                                qual=money(float(revenue) * rnd.uniform(0.4, 0.9)),
                                revenue=revenue,
                                salary=money(float(revenue) * rnd.uniform(0.02, 0.08)),
                                source="demo",
                            )
                        )

            if day_records:
                db.add_all(day_records)
                await db.flush()
                media_count += len(day_records)
                for record in day_records:
                    service = services[record.installs % len(services)]
                    provider = providers[record.installs % len(providers)]
                    media_children.append(
                        MediaServiceValue(
                            media_record_id=record.id,
                            service_id=service.id,
                            quantity=money(record.installs),
                        )
                    )
                    media_children.append(
                        MediaSpendValue(
                            media_record_id=record.id,
                            provider_id=provider.id,
                            base_amount=money(float(record.spend_calculated) * 0.95),
                        )
                    )
                await flush_batch(db, media_children)

            if day_finance:
                db.add_all(day_finance)
                await db.flush()
                finance_count += len(day_finance)
                for record in day_finance:
                    service = services[int(record.rent) % len(services)]
                    provider = providers[int(record.spend) % len(providers)]
                    finance_children.append(
                        FinanceServiceValue(
                            finance_record_id=record.id,
                            service_id=service.id,
                            quantity=money(float(record.rent) / max(float(service.install_cost), 0.0001)),
                        )
                    )
                    finance_children.append(
                        FinanceSpendValue(
                            finance_record_id=record.id,
                            provider_id=provider.id,
                            base_amount=money(float(record.spend) * 0.95),
                        )
                    )
                await flush_batch(db, finance_children)

            if day_offset % 20 == 0:
                await db.commit()
                print(
                    f"  {record_date}: медиа {media_count}, финансы {finance_count}",
                    flush=True,
                )

        await flush_batch(db, media_children, force=True)
        await flush_batch(db, finance_children, force=True)
        await db.commit()
        print(f"Медиазаписи: +{media_count}, финансовые записи: +{finance_count}")

        # --- аудит -----------------------------------------------------------
        event_types = [
            ("auth.login", "Пользователь вошёл в систему"),
            ("media.values_changed", "Изменены сервисы и расходы"),
            ("finance.import", "Импорт финансовых данных"),
            ("offers.updated", "Обновлён оффер"),
            ("users.updated", "Обновлён пользователь"),
            ("settings.changed", "Изменены настройки"),
        ]
        audit_rows: list[AuditEvent] = []
        actors = buyers + leads
        for _ in range(AUDIT_EVENTS):
            event_type, description = rnd.choice(event_types)
            actor = rnd.choice(actors)
            audit_rows.append(
                AuditEvent(
                    workspace_id=workspace.id,
                    user_id=actor.id,
                    event_type=event_type,
                    entity_type="demo",
                    entity_id=str(rnd.randint(1, 9999)),
                    description=description,
                    ip_address=f"10.{rnd.randint(0, 255)}.{rnd.randint(0, 255)}.{rnd.randint(1, 254)}",
                    data={"demo": True},
                )
            )
            await flush_batch(db, audit_rows)
        await flush_batch(db, audit_rows, force=True)
        await db.commit()
        print(f"События аудита: +{AUDIT_EVENTS}")
        print("Готово. Пароль всех демо-пользователей: demo12345")


if __name__ == "__main__":
    asyncio.run(main())
