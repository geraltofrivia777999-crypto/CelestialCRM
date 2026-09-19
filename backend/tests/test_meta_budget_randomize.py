from decimal import Decimal
from types import SimpleNamespace

from app.schemas import MetaBundleCampaign
from app.services.meta_launch import _randomized, _split_budget


def _launch(**values):
    base = {
        "id": "launch-seed", "daily_budget": Decimal("100"), "budget_kind": None,
        "budget_randomize": False, "budget_randomize_pct": None,
    }
    base.update(values)
    return SimpleNamespace(**base)


def test_spread_follows_the_percent() -> None:
    seeds = [f"seed-{index}" for index in range(200)]
    narrow = [_randomized(Decimal("100"), seed, 5) for seed in seeds]
    wide = [_randomized(Decimal("100"), seed, 40) for seed in seeds]
    assert all(Decimal("95") <= value <= Decimal("105") for value in narrow)
    assert all(Decimal("60") <= value <= Decimal("140") for value in wide)
    # Шире разброс — значения действительно выходят за ±5 %.
    assert any(value < Decimal("90") or value > Decimal("110") for value in wide)
    # Тот же залив — та же сумма: повтор после сбоя не меняет бюджет.
    assert _randomized(Decimal("100"), "same", 20) == _randomized(Decimal("100"), "same", 20)
    # Пусто или мусор — прежние ±10 %.
    assert Decimal("90") <= _randomized(Decimal("100"), "x", None) <= Decimal("110")


def test_launch_percent_wins_over_bundle() -> None:
    campaign = {"budget_randomize": True, "budget_randomize_pct": 50}
    from_bundle = _split_budget(_launch(), campaign)["daily_budget"]
    assert from_bundle == _randomized(Decimal("100"), "launch-seed", 50)
    own = _split_budget(_launch(budget_randomize=True, budget_randomize_pct=3), campaign)
    assert own["daily_budget"] == _randomized(Decimal("100"), "launch-seed", 3)
    assert _split_budget(_launch(), {})["daily_budget"] == Decimal("100")


def test_bundle_keeps_the_percent() -> None:
    block = MetaBundleCampaign(budget_randomize=True, budget_randomize_pct="15")
    assert block.budget_randomize_pct == Decimal("15")
    assert MetaBundleCampaign().budget_randomize_pct == Decimal("10")
