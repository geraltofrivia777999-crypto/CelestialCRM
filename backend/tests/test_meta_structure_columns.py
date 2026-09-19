from decimal import Decimal
from types import SimpleNamespace

from app.services.meta_levels import Bucket
from app.services.meta_metrics import (
    REGISTRATION_ACTION_TYPES,
    action_count,
    metrics,
    totals,
)


def _stat(**values):
    base = {
        "spend": Decimal("0"), "impressions": 0, "clicks": 0, "link_clicks": 0,
        "pixel_leads": 0, "pixel_purchases": 0, "actions": {}, "campaign_external_id": "c1",
    }
    base.update(values)
    return SimpleNamespace(**base)


def test_one_conversion_under_several_names_is_counted_once() -> None:
    actions = {
        "omni_complete_registration": 7,
        "complete_registration": 7,
        "offsite_conversion.fb_pixel_complete_registration": 6,
    }
    assert action_count(actions, REGISTRATION_ACTION_TYPES) == 7
    assert action_count({}, REGISTRATION_ACTION_TYPES) == 0
    assert action_count(None, REGISTRATION_ACTION_TYPES) == 0


def test_structure_rows_carry_ads_manager_costs() -> None:
    bucket = Bucket(key="c1", name="Campaign")
    bucket.add(_stat(
        spend=Decimal("40"), impressions=4000, clicks=50, link_clicks=20, pixel_leads=8,
        pixel_purchases=2,
        actions={"complete_registration": 5, "landing_page_view": 16, "mobile_app_install": 4},
    ))
    bucket.add(_stat(spend=Decimal("10"), impressions=1000, clicks=10, link_clicks=5,
                     actions={"omni_complete_registration": 5}))
    assert (bucket.registrations, bucket.landing_views, bucket.installs) == (10, 16, 4)

    row = metrics(
        bucket.spend, bucket.impressions, bucket.clicks, None, 0, 4,
        link_clicks=bucket.link_clicks, results=bucket.results,
        pixel_leads=bucket.pixel_leads, pixel_purchases=bucket.pixel_purchases,
        registrations=bucket.registrations, landing_views=bucket.landing_views,
        installs=bucket.installs,
    )
    assert row["cpc_link"] == 2.0
    assert row["cpc"] == round(50 / 60, 2)
    assert row["cost_per_pixel_lead"] == 6.25
    assert row["cost_per_registration"] == 5.0
    assert row["cost_per_purchase"] == 25.0
    assert row["cost_per_landing_view"] == 3.12  # 3.125, округление q2
    assert row["cost_per_install"] == 12.5
    assert row["cost_per_sale"] == 12.5
    assert row["ctr"] == 1.2 and row["link_ctr"] == 0.5


def test_empty_counts_give_no_cost_instead_of_zero() -> None:
    row = metrics(Decimal("5"), 100, 0, None, 0, 0)
    for key in ("cpc_link", "cost_per_pixel_lead", "cost_per_registration", "cost_per_sale"):
        assert row[key] is None
    summary = totals([_stat(spend=Decimal("9"), actions={"complete_registration": 3})], {})
    assert summary["registrations"] == 3 and summary["cost_per_registration"] == 3.0
