import uuid
from decimal import Decimal

import pytest
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.models import (
    FinanceBook,
    FinanceBookDay,
    FinanceBookOffer,
    FinanceOfferTag,
    FinanceTagDay,
    User,
)
from app.services.formulas import salary_for_profit, salary_percent
from tests.test_media_finance import _admin_client

# Настоящий июль из таблицы «Александр | CG Партнерка» — числа сверены с файлом.
JULY_DAYS = {
    "18": {"spend_buyer": 52.94},
    "19": {"spend_buyer": 103.68, "costs": 2.23},
    "20": {"spend_buyer": 135.97},
    "21": {"spend_buyer": 87.24},
    "22": {"spend_buyer": 2.1},
    "23": {"spend_buyer": 84.53},
    "24": {"spend_buyer": 72.76},
    "25": {"spend_buyer": 145.43},
    "27": {"spend_buyer": 18.19},
    "28": {"spend_buyer": 80.13},
    "29": {"spend_buyer": 88.89},
}
JULY_OFFERS = [
    {"name": "LuckyStar", "partner": "Fame LATAM", "rate": 13,
     "tags": [{"name": "SOK", "values": {}}]},
    {"name": "JugaBet", "partner": "GROWE", "rate": 12,
     "tags": [{"name": "SOK", "values": {"18": 1, "19": 1, "21": 2, "22": 2}}]},
    {"name": "Fortunazo", "partner": "GROWE", "rate": 13,
     "tags": [{"name": "SOK", "values": {"23": 7, "24": 2, "25": 2, "26": 1,
                                         "27": 1, "28": 5}}]},
]


@pytest.fixture
async def buyer_id(database):
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.login == "admin"))
        ident = str(admin.id)
        workspace_id = admin.workspace_id

    yield ident

    # SQLite в тестах не применяет ON DELETE CASCADE, поэтому дети убираются сами —
    # иначе сироты от одного теста всплывают в проверках следующего.
    async with SessionLocal() as db:
        books = select(FinanceBook.id).where(FinanceBook.workspace_id == workspace_id)
        offers = select(FinanceBookOffer.id).where(FinanceBookOffer.book_id.in_(books))
        tags = select(FinanceOfferTag.id).where(FinanceOfferTag.offer_id.in_(offers))
        await db.execute(delete(FinanceTagDay).where(FinanceTagDay.tag_id.in_(tags)))
        await db.execute(delete(FinanceOfferTag).where(FinanceOfferTag.offer_id.in_(offers)))
        await db.execute(delete(FinanceBookOffer).where(FinanceBookOffer.book_id.in_(books)))
        await db.execute(delete(FinanceBookDay).where(FinanceBookDay.book_id.in_(books)))
        await db.execute(delete(FinanceBook).where(FinanceBook.workspace_id == workspace_id))
        await db.commit()


def _payload(buyer, **overrides) -> dict:
    body = {
        "buyer_id": buyer,
        "year": 2026,
        "month": 7,
        "prev_minus": 0,
        "days": JULY_DAYS,
        "offers": JULY_OFFERS,
    }
    body.update(overrides)
    return body


def _payload_with_profit(
    buyer: str,
    profit: int,
    *,
    year: int = 2026,
    month: int = 7,
    prev_minus: int = 0,
) -> dict:
    """Минимальная книга с заданным целым профитом для проверки переноса."""
    if profit < 0:
        days = {"1": {"spend_buyer": abs(profit)}}
        offers = []
    else:
        days = {}
        offers = [] if profit == 0 else [{
            "name": "Тестовый оффер",
            "rate": profit,
            "tags": [{"name": "SOK", "values": {"1": 1}}],
        }]
    return _payload(
        buyer,
        year=year,
        month=month,
        prev_minus=prev_minus,
        days=days,
        offers=offers,
    )


async def test_book_round_trip_matches_the_source_spreadsheet(buyer_id) -> None:
    with _admin_client() as client:
        saved = client.put("/api/v1/finance/book", json=_payload(buyer_id))
        assert saved.status_code == 200
        read = client.get(
            f"/api/v1/finance/book?buyer_id={buyer_id}&year=2026&month=7"
        )
        assert read.status_code == 200

    for payload in (saved.json(), read.json()):
        total = payload["totals"]["total"]
        assert payload["month"] == 7, "месячные итоги не должны затирать номер месяца"
        assert payload["days_in_month"] == 31
        assert Decimal(total["income"]) == Decimal("306")
        assert Decimal(total["spend_buyer"]) == Decimal("871.86")
        assert Decimal(total["costs"]) == Decimal("2.23")
        assert Decimal(total["profit"]) == Decimal("-568.09")
        assert Decimal(total["salary"]) == 0
        assert len(payload["offers"]) == 3


async def test_daily_profit_adds_up_to_the_month(buyer_id) -> None:
    """В исходной таблице сумма дней не сходилась с итогом ровно на Costs."""
    with _admin_client() as client:
        payload = client.put("/api/v1/finance/book", json=_payload(buyer_id)).json()

    daily = sum(Decimal(row["profit"]) for row in payload["totals"]["daily"])
    assert daily == Decimal(payload["totals"]["total"]["profit"])


async def test_empty_cells_are_not_stored(buyer_id) -> None:
    with _admin_client() as client:
        client.put(
            "/api/v1/finance/book",
            json=_payload(
                buyer_id,
                days={"1": {"spend_buyer": 0, "spend_agent": 0, "costs": 0}, **JULY_DAYS},
                offers=[{"name": "Пустой", "rate": 5,
                         "tags": [{"name": "SOK", "values": {"3": 0}}]}],
            ),
        )

    async with SessionLocal() as db:
        book = await db.scalar(select(FinanceBook))
        offers = list(
            (
                await db.execute(
                    select(FinanceBookOffer).where(FinanceBookOffer.book_id == book.id)
                )
            ).scalars()
        )
        assert len(offers) == 1
        tag = await db.scalar(
            select(FinanceOfferTag).where(FinanceOfferTag.offer_id == offers[0].id)
        )
        stored = await db.scalar(
            select(FinanceTagDay).where(FinanceTagDay.tag_id == tag.id)
        )
        # Нулевой день — это отсутствие данных, а не значение.
        assert stored is None


async def test_removing_an_offer_takes_its_deposits_with_it(buyer_id) -> None:
    with _admin_client() as client:
        client.put("/api/v1/finance/book", json=_payload(buyer_id))
        trimmed = client.put(
            "/api/v1/finance/book", json=_payload(buyer_id, offers=JULY_OFFERS[:1])
        ).json()

    assert len(trimmed["offers"]) == 1
    assert Decimal(trimmed["totals"]["total"]["income"]) == 0
    async with SessionLocal() as db:
        book = await db.scalar(select(FinanceBook.id))
        offers = select(FinanceBookOffer.id).where(FinanceBookOffer.book_id == book)
        tags = select(FinanceOfferTag.id).where(FinanceOfferTag.offer_id.in_(offers))
        orphan = await db.scalar(
            select(FinanceTagDay).where(FinanceTagDay.tag_id.notin_(tags))
        )
        assert orphan is None, "депозиты удалённого оффера остались в базе"


async def test_offer_count_is_not_capped(buyer_id) -> None:
    """Ограничение в шесть офферов из таблицы не должно переехать в CRM."""
    offers = [
        {"name": f"Оффер {index}", "rate": 1,
         "tags": [{"name": "SOK", "values": {"4": 1}}]}
        for index in range(12)
    ]
    with _admin_client() as client:
        payload = client.put(
            "/api/v1/finance/book", json=_payload(buyer_id, offers=offers)
        ).json()

    assert len(payload["offers"]) == 12
    assert Decimal(payload["totals"]["total"]["income"]) == Decimal("12")


async def test_february_has_28_days_and_ignores_a_29th(buyer_id) -> None:
    with _admin_client() as client:
        payload = client.put(
            "/api/v1/finance/book",
            json=_payload(
                buyer_id,
                month=2,
                days={"29": {"spend_buyer": 100}},
                offers=[],
            ),
        ).json()

    assert payload["days_in_month"] == 28
    assert len(payload["totals"]["daily"]) == 28
    # День, которого в месяце нет, не должен попасть в итог.
    assert Decimal(payload["totals"]["total"]["spend_buyer"]) == 0


async def test_book_of_an_unknown_buyer_is_not_found(buyer_id) -> None:
    with _admin_client() as client:
        response = client.get(
            f"/api/v1/finance/book?buyer_id={uuid.uuid4()}&year=2026&month=7"
        )
        assert response.status_code == 404


@pytest.mark.parametrize(
    "profit,percent,salary",
    [
        ("-1", "0", "0"),
        ("0", "10", "0"),
        ("10000", "10", "1000"),
        # Ступень меняется целиком: один доллар сверху стоит 500.
        ("10001", "15", "1500.15"),
        ("15000", "15", "2250"),
        ("20000", "20", "4000"),
        ("40000", "25", "10000"),
        ("40001", "30", "12000.30"),
    ],
)
def test_salary_ladder(profit: str, percent: str, salary: str) -> None:
    value = Decimal(profit)
    assert salary_percent(value) == Decimal(percent)
    assert salary_for_profit(value) == Decimal(salary)


async def test_offer_income_sums_every_tag(buyer_id) -> None:
    """Депозиты вводятся по нескольким тегам, а доход берёт их все."""
    offer = {
        "name": "LuckyStar",
        "rate": 10,
        "tags": [
            {"name": "SOK", "values": {"5": 2}},
            {"name": "Долёты", "values": {"5": 3}},
            {"name": "RAF", "values": {"6": 1}},
        ],
    }
    with _admin_client() as client:
        payload = client.put(
            "/api/v1/finance/book",
            json=_payload(buyer_id, days={}, offers=[offer]),
        ).json()

    assert [tag["name"] for tag in payload["offers"][0]["tags"]] == ["SOK", "Долёты", "RAF"]
    daily = {row["day"]: Decimal(row["income"]) for row in payload["totals"]["daily"]}
    assert daily[5] == Decimal("50")
    assert daily[6] == Decimal("10")
    assert Decimal(payload["totals"]["total"]["income"]) == Decimal("60")


async def test_tag_can_be_renamed_and_removed(buyer_id) -> None:
    with _admin_client() as client:
        client.put(
            "/api/v1/finance/book",
            json=_payload(buyer_id, days={}, offers=[{
                "name": "LuckyStar", "rate": 10,
                "tags": [{"name": "SOK", "values": {"5": 2}},
                         {"name": "Долёты", "values": {"5": 3}}],
            }]),
        )
        renamed = client.put(
            "/api/v1/finance/book",
            json=_payload(buyer_id, days={}, offers=[{
                "name": "LuckyStar", "rate": 10,
                "tags": [{"name": "Депозиты", "values": {"5": 2}}],
            }]),
        ).json()

    tags = renamed["offers"][0]["tags"]
    assert [tag["name"] for tag in tags] == ["Депозиты"]
    assert Decimal(renamed["totals"]["total"]["income"]) == Decimal("20")


async def test_tag_may_stay_unnamed_until_the_user_names_it(buyer_id) -> None:
    """Новый тег создаётся пустым, и это состояние должно доезжать до базы как есть."""
    with _admin_client() as client:
        payload = client.put(
            "/api/v1/finance/book",
            json=_payload(buyer_id, days={}, offers=[{
                "name": "LuckyStar", "rate": 10,
                "tags": [{"name": "", "values": {"5": 2}}],
            }]),
        ).json()

    tag = payload["offers"][0]["tags"][0]
    # Ни «SOK», ни «Без названия» — имя за пользователя не придумывается.
    assert tag["name"] == ""
    assert Decimal(payload["totals"]["total"]["income"]) == Decimal("20")


async def test_negative_month_is_automatically_carried_forward(buyer_id) -> None:
    with _admin_client() as client:
        july = client.put(
            "/api/v1/finance/book",
            json=_payload_with_profit(buyer_id, -500),
        ).json()
        august = client.get(
            f"/api/v1/finance/book?buyer_id={buyer_id}&year=2026&month=8"
        ).json()

    assert Decimal(july["totals"]["total"]["next_minus"]) == Decimal("500")
    assert Decimal(july["totals"]["total"]["payout"]) == 0
    assert Decimal(august["prev_minus"]) == Decimal("500")
    assert Decimal(august["totals"]["total"]["next_minus"]) == Decimal("500")


async def test_the_debt_only_touches_the_salary_not_the_profit(
    buyer_id,
) -> None:
    """Долг прошлого месяца виден только в зарплате.

    Профит месяца — это его собственная работа: доход минус спенд и costs.
    Перенос вычитается один раз и только там, где считают начисление.
    """
    with _admin_client() as client:
        client.put(
            "/api/v1/finance/book",
            json=_payload_with_profit(buyer_id, -500),
        )
        august = client.put(
            "/api/v1/finance/book",
            json=_payload_with_profit(buyer_id, 3000, month=8),
        ).json()
        september = client.get(
            f"/api/v1/finance/book?buyer_id={buyer_id}&year=2026&month=9"
        ).json()

    total = august["totals"]["total"]
    assert Decimal(total["prev_minus"]) == Decimal("500")
    # Профит августа — его собственные 3000; долг виден только в зарплате,
    # которая считается от 2500.
    assert Decimal(total["profit"]) == Decimal("3000")
    assert Decimal(total["profit_after_debt"]) == Decimal("2500")
    assert Decimal(total["salary"]) == Decimal("250")
    assert Decimal(total["payout"]) == Decimal("250")
    assert Decimal(total["next_minus"]) == 0
    assert Decimal(september["prev_minus"]) == 0


async def test_historical_edit_recalculates_following_months(buyer_id) -> None:
    with _admin_client() as client:
        client.put(
            "/api/v1/finance/book",
            json=_payload_with_profit(buyer_id, -500),
        )
        client.put(
            "/api/v1/finance/book",
            json=_payload_with_profit(buyer_id, 3000, month=8),
        )

        # Уменьшили июльский убыток — в августе меньше вычитается из профита,
        # значит и зарплата за август становится больше.
        client.put(
            "/api/v1/finance/book",
            json=_payload_with_profit(buyer_id, -100),
        )
        august = client.get(
            f"/api/v1/finance/book?buyer_id={buyer_id}&year=2026&month=8"
        ).json()
        september = client.get(
            f"/api/v1/finance/book?buyer_id={buyer_id}&year=2026&month=9"
        ).json()

    total = august["totals"]["total"]
    assert Decimal(august["prev_minus"]) == Decimal("100")
    assert Decimal(total["profit"]) == Decimal("3000")
    assert Decimal(total["profit_after_debt"]) == Decimal("2900")
    assert Decimal(total["payout"]) == Decimal("290")
    assert Decimal(total["next_minus"]) == 0
    assert Decimal(september["prev_minus"]) == 0


async def test_repeated_save_does_not_duplicate_the_carry(buyer_id) -> None:
    body = _payload_with_profit(buyer_id, -275)
    with _admin_client() as client:
        client.put("/api/v1/finance/book", json=body)
        client.put("/api/v1/finance/book", json=body)
        august = client.get(
            f"/api/v1/finance/book?buyer_id={buyer_id}&year=2026&month=8"
        ).json()

    assert Decimal(august["prev_minus"]) == Decimal("275")


async def test_carry_crosses_missing_months_and_year_boundary(buyer_id) -> None:
    with _admin_client() as client:
        client.put(
            "/api/v1/finance/book",
            json=_payload_with_profit(buyer_id, -250, year=2026, month=12),
        )
        march = client.get(
            f"/api/v1/finance/book?buyer_id={buyer_id}&year=2027&month=3"
        ).json()

    assert Decimal(march["prev_minus"]) == Decimal("250")


async def test_client_cannot_override_automatic_carry(buyer_id) -> None:
    with _admin_client() as client:
        first = client.put(
            "/api/v1/finance/book",
            json=_payload_with_profit(buyer_id, 0, prev_minus=999),
        ).json()
        client.put(
            "/api/v1/finance/book",
            json=_payload_with_profit(buyer_id, -120),
        )
        august = client.put(
            "/api/v1/finance/book",
            json=_payload_with_profit(buyer_id, 0, month=8, prev_minus=999),
        ).json()

    assert Decimal(first["prev_minus"]) == 0
    assert Decimal(august["prev_minus"]) == Decimal("120")


async def test_earliest_legacy_manual_minus_is_preserved_as_opening_debt(
    buyer_id,
) -> None:
    with _admin_client() as client:
        client.put(
            "/api/v1/finance/book",
            json=_payload_with_profit(buyer_id, 0),
        )

    # Так выглядит значение, которое уже было введено финансистом до включения
    # автоматического переноса на рабочей базе.
    async with SessionLocal() as db:
        book = await db.scalar(select(FinanceBook))
        book.prev_minus = Decimal("375")
        await db.commit()

    with _admin_client() as client:
        saved = client.put(
            "/api/v1/finance/book",
            json=_payload_with_profit(buyer_id, 0),
        ).json()
        august = client.get(
            f"/api/v1/finance/book?buyer_id={buyer_id}&year=2026&month=8"
        ).json()

    assert Decimal(saved["prev_minus"]) == Decimal("375")
    assert Decimal(saved["totals"]["total"]["next_minus"]) == Decimal("375")
    assert Decimal(august["prev_minus"]) == Decimal("375")


async def test_offers_carry_into_a_month_that_has_no_book_yet(buyer_id) -> None:
    """Список офферов переезжает на следующий месяц, цифры — нет."""
    with _admin_client() as client:
        client.put("/api/v1/finance/book", json=_payload(buyer_id))
        august = client.get(
            f"/api/v1/finance/book?buyer_id={buyer_id}&year=2026&month=8"
        ).json()

    assert [offer["name"] for offer in august["offers"]] == [
        "LuckyStar",
        "JugaBet",
        "Fortunazo",
    ]
    for offer in august["offers"]:
        assert [tag["name"] for tag in offer["tags"]] == ["SOK"]
        # Депозиты остаются в июле: переезжает список работ, а не его цифры.
        assert all(tag["values"] == {} for tag in offer["tags"])
    assert august["totals"]["total"]["income"] == "0.0000"
    assert august["offers"][1]["rate"] == "12.0000"


async def test_a_saved_month_keeps_its_own_offers(buyer_id) -> None:
    """Заготовка не возвращается, если месяц уже сохранён своим составом."""
    with _admin_client() as client:
        client.put("/api/v1/finance/book", json=_payload(buyer_id))
        client.put(
            "/api/v1/finance/book",
            json=_payload(
                buyer_id,
                month=8,
                days={},
                offers=[{"name": "Только этот", "rate": 5, "tags": []}],
            ),
        )
        august = client.get(
            f"/api/v1/finance/book?buyer_id={buyer_id}&year=2026&month=8"
        ).json()

    assert [offer["name"] for offer in august["offers"]] == ["Только этот"]


async def test_eur_rate_converts_the_offer_rate_into_dollars(buyer_id) -> None:
    """Книга считается в долларах: евровая ставка переводится по курсу месяца."""
    with _admin_client() as client:
        saved = client.put(
            "/api/v1/finance/book",
            json=_payload(
                buyer_id,
                days={},
                eur_usd_rate=1.1,
                offers=[
                    {"name": "В евро", "rate": 10, "rate_currency": "EUR",
                     "tags": [{"name": "SOK", "values": {"3": 5}}]},
                    {"name": "В долларах", "rate": 10,
                     "tags": [{"name": "SOK", "values": {"3": 5}}]},
                ],
            ),
        ).json()

    # 5 × 10 € × 1,1 = 55 $, плюс 5 × 10 $ = 50 $.
    assert Decimal(saved["totals"]["total"]["income"]) == Decimal("105")
    assert saved["offers"][0]["rate_currency"] == "EUR"
    assert saved["offers"][1]["rate_currency"] == "USD"
    assert Decimal(saved["eur_usd_rate"]) == Decimal("1.1")


async def test_eur_rate_is_carried_into_a_new_month(buyer_id) -> None:
    with _admin_client() as client:
        client.put(
            "/api/v1/finance/book",
            json=_payload(buyer_id, eur_usd_rate=1.085),
        )
        august = client.get(
            f"/api/v1/finance/book?buyer_id={buyer_id}&year=2026&month=8"
        ).json()

    assert Decimal(august["eur_usd_rate"]) == Decimal("1.085")
    # Валюта ставки переезжает вместе с оффером.
    assert all(offer["rate_currency"] == "USD" for offer in august["offers"])


async def test_a_broken_eur_rate_is_rejected(buyer_id) -> None:
    with _admin_client() as client:
        for bad in (0, -1, 1085):
            response = client.put(
                "/api/v1/finance/book", json=_payload(buyer_id, eur_usd_rate=bad)
            )
            assert response.status_code == 422, bad
        wrong_currency = client.put(
            "/api/v1/finance/book",
            json=_payload(buyer_id, offers=[
                {"name": "X", "rate": 1, "rate_currency": "RUB", "tags": []}
            ]),
        )
        assert wrong_currency.status_code == 422


async def test_each_tier_table_keeps_its_own_numbers(buyer_id) -> None:
    """Таблицы независимы: расход Tier1 не появляется в Tier2/3."""
    with _admin_client() as client:
        first = client.put(
            "/api/v1/finance/book",
            json=_payload(
                buyer_id,
                days={"1": {"spend_buyer": 25, "costs": 6}},
                offers=[
                    {
                        "name": "Tier1",
                        "geo": "US",
                        "rate": 10,
                        "tags": [{"name": "SOK", "values": {"1": 4}}],
                    }
                ],
                tier="T1",
            ),
        ).json()
        second = client.put(
            "/api/v1/finance/book",
            json=_payload(
                buyer_id,
                days={"1": {"spend_buyer": 10, "costs": 2}},
                offers=[
                    {
                        "name": "Tier2/3",
                        "geo": "BR",
                        "rate": 20,
                        "tags": [{"name": "SOK", "values": {"1": 1}}],
                    }
                ],
                tier="T23",
            ),
        ).json()
        summary = client.get(
            "/api/v1/finance/summary?year=2026&month=7&scope=all"
        ).json()

    assert Decimal(first["totals"]["total"]["spend_buyer"]) == Decimal("25")
    assert Decimal(second["totals"]["total"]["spend_buyer"]) == Decimal("10")

    rows = {row["tier"]: row for row in summary["tiers"]}
    # Числа фактические: каждая таблица отдала ровно то, что в неё ввели.
    assert Decimal(rows["T1"]["spend"]) == Decimal("25")
    assert Decimal(rows["T23"]["spend"]) == Decimal("10")
    assert Decimal(rows["T1"]["income"]) == Decimal("40")
    assert Decimal(rows["T23"]["income"]) == Decimal("20")
    assert Decimal(summary["cards"]["spend"]) == Decimal("35")


async def test_the_debt_chain_is_separate_for_each_tier(buyer_id) -> None:
    """Минус Tier2/3 не гасится прибылью Tier1: зарплата тоже считается врозь."""
    with _admin_client() as client:
        client.put(
            "/api/v1/finance/book",
            json=_payload(
                buyer_id,
                days={"1": {"spend_buyer": 500}},
                offers=[],
                tier="T23",
                year=2026,
                month=6,
            ),
        )
        client.put(
            "/api/v1/finance/book",
            json=_payload(
                buyer_id,
                days={"1": {"spend_buyer": 10}},
                offers=[
                    {
                        "name": "Плюс",
                        "rate": 100,
                        "tags": [{"name": "SOK", "values": {"1": 10}}],
                    }
                ],
                tier="T1",
                year=2026,
                month=6,
            ),
        )
        tier_one = client.get(
            f"/api/v1/finance/book?buyer_id={buyer_id}&year=2026&month=7&tier=T1"
        ).json()
        tier_two = client.get(
            f"/api/v1/finance/book?buyer_id={buyer_id}&year=2026&month=7&tier=T23"
        ).json()

    assert Decimal(tier_one["prev_minus"]) == Decimal("0")
    assert Decimal(tier_two["prev_minus"]) == Decimal("500")


async def test_offer_geo_decides_its_tier(buyer_id) -> None:
    offer = {"name": "Оффер", "geo": "BR", "rate": 1, "tags": []}
    with _admin_client() as client:
        saved = client.put(
            "/api/v1/finance/book",
            json=_payload(buyer_id, days={}, offers=[offer]),
        ).json()
        read = client.get(
            f"/api/v1/finance/book?buyer_id={buyer_id}&year=2026&month=7"
        ).json()

    for book in (saved, read):
        assert book["offers"][0]["geo"] == "BR"
        # Тир страны — подсказка рядом с гео; сам тир задаёт таблица.
        assert book["offers"][0]["tier"] == "T23"
        assert book["tier"] == "T1"
        # Затрат у оффера нет: они живут на дне книги.
        assert "days" not in book["offers"][0]
