from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

from app.api.routers.analytics import _structured_export
from app.api.routers.utilities import _newest_first


def _group(buyer: str, day: str, spend: str, providers: dict, geo: str = "DE") -> dict:
    return {
        "buyer": buyer, "date": day, "tier": "T1", "geo": geo, "partner": "P",
        "offer": f"Offer {geo}", "records": 1, "installs": 10, "registrations": 5, "ftd": 2,
        "spend": spend, "revenue": "100", "providers": providers,
    }


GROUPS = [
    _group("KOD", "2026-09-14", "91.29", {"mt": {"amount": "58.26"}, "cg": {"amount": "33.03"}}),
    _group("BURZ", "2026-09-14", "200", {"wf": {"amount": "200"}}, geo="CO"),
    _group("BURZ", "2026-09-14", "47.94", {"wf": {"amount": "47.94"}}, geo="AR"),
]
AGENTS = [("cg", "CrossGif"), ("mt", "MT"), ("wf", "WEFUN AG")]


def test_export_follows_the_board_structure() -> None:
    headers, rows = _structured_export(GROUPS, ["buyer", "date", "agent"], AGENTS)
    assert headers[:4] == ["Баер", "Дата", "Агент", "Записей"]
    # Агент — уровнем, поэтому отдельных столбцов SPEND · агент нет.
    assert not any(title.startswith("SPEND ·") for title in headers)
    lines = [(row[0], row[1], row[2], row[7]) for row in rows[:-1]]
    assert lines == [
        # «Без агента» у BURZ держит воронку и доход, спенд весь у WEFUN AG.
        ("BURZ", "14.09.2026", "WEFUN AG", Decimal("247.94")),
        ("BURZ", "14.09.2026", "Без агента", Decimal("0")),
        ("KOD", "14.09.2026", "CrossGif", Decimal("33.03")),
        ("KOD", "14.09.2026", "MT", Decimal("58.26")),
        ("KOD", "14.09.2026", "Без агента", Decimal("0.00")),
    ]
    burz_none = rows[1]
    assert burz_none[3:7] == [2, 20, 10, 4]
    total = rows[-1]
    assert total[0] == "Общая" and total[7] == Decimal("339.23") and total[8] == Decimal("300")


def test_export_without_agent_level_keeps_agent_columns() -> None:
    headers, rows = _structured_export(GROUPS, ["buyer", "geo"], AGENTS)
    assert headers[:2] == ["Баер", "GEO"]
    assert headers[-3:] == ["SPEND · CrossGif", "SPEND · MT", "SPEND · WEFUN AG"]
    assert [row[:2] for row in rows] == [["BURZ", "AR"], ["BURZ", "CO"], ["KOD", "DE"], ["Общая", ""]]
    assert rows[-1][-3:] == [Decimal("33.03"), Decimal("58.26"), Decimal("247.94")]


def test_cap_offers_newest_first() -> None:
    stamp = datetime(2026, 9, 1)
    offers = [
        SimpleNamespace(external_id="12", created_at=stamp, name="old"),
        SimpleNamespace(external_id="907", created_at=stamp, name="new"),
        SimpleNamespace(external_id="88", created_at=stamp, name="mid"),
    ]
    assert [offer.name for offer in _newest_first(offers)] == ["new", "mid", "old"]
