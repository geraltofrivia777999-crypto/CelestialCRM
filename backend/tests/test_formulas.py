from datetime import date
from decimal import Decimal
from uuid import uuid4

from app.services.formulas import (
    amount_with_commission,
    finance_import_key,
    finance_metrics,
    media_metrics,
    rent_cost,
    service_cost,
)


def test_media_metrics_use_override_and_handle_zero() -> None:
    result = media_metrics(
        Decimal("100"), Decimal("40"), Decimal("50"), ftd=0
    )
    assert result == {
        "spend": Decimal("50.0000"),
        "profit": Decimal("50.0000"),
        "roi": Decimal("100.00"),
        "cpd": None,
    }


def test_finance_metrics_include_rent_and_spend() -> None:
    result = finance_metrics(Decimal("200"), Decimal("20"), Decimal("80"))
    assert result["costs"] == Decimal("100.0000")
    assert result["profit"] == Decimal("100.0000")
    assert result["roi"] == Decimal("100.00")


def test_cost_calculations() -> None:
    assert rent_cost(Decimal("1000"), Decimal("0.03")) == Decimal("30.0000")
    assert amount_with_commission(Decimal("100"), Decimal("8.5")) == Decimal("108.5000")


def test_service_cost_applies_the_configured_commission() -> None:
    # ТЗ 2.4.1: both the install price and the service commission are taken into account.
    assert service_cost(
        Decimal("1000"), Decimal("0.03"), Decimal("0")
    ) == Decimal("30.0000")
    assert service_cost(
        Decimal("1000"), Decimal("0.03"), Decimal("10")
    ) == Decimal("33.0000")


def test_finance_import_key_is_normalized_and_stable() -> None:
    buyer, offer = uuid4(), uuid4()
    first = finance_import_key(date(2026, 7, 23), buyer, offer, " HTTPS://X.test/a ")
    second = finance_import_key(date(2026, 7, 23), buyer, offer, "https://x.test/a")
    assert first == second
    assert len(first) == 64

