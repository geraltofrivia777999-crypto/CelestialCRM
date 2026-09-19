from decimal import Decimal

from app.api.routers.analytics import (
    MEDIA_EXPORT_HEADERS,
    _media_export_rows,
    _media_export_total,
)


def _group(buyer: str, offer: str, spend: str, providers: dict) -> dict:
    return {
        "buyer": buyer, "tier": "T1", "geo": "DE", "partner": "P", "offer": offer,
        "records": 1, "installs": 10, "registrations": 5, "ftd": 2,
        "spend": spend, "revenue": "100", "providers": providers,
    }


def test_export_adds_a_spend_column_per_agent() -> None:
    """В выгрузке видно, сколько спенда внесено через каждого агента."""
    agents = [("a1", "Agent A"), ("a2", "Agent B")]
    rows = _media_export_rows(
        [
            _group("Buyer", "Offer 1", "30", {"a1": {"amount": "20"}, "a2": {"amount": "10"}}),
            _group("Buyer", "Offer 2", "15", {"a2": {"amount": "15"}}),
        ],
        agents,
    )
    width = len(MEDIA_EXPORT_HEADERS)
    assert [row[width:] for row in rows] == [
        [Decimal("20"), Decimal("10")],
        [Decimal("0"), Decimal("15")],
    ]
    total = _media_export_total(rows, len(agents))
    assert total[width:] == [Decimal("20"), Decimal("25")]
    # Без агентов строка прежней ширины — старые выгрузки не меняются.
    assert len(_media_export_rows([_group("Buyer", "Offer", "5", {})])[0]) == width
