from app.seed import _deleted_catalog_names


def test_deleted_catalog_names_supports_old_and_structured_audit_events() -> None:
    events = [
        ({}, "Deleted provider BRO"),
        ({"provider_name": "SPX"}, "Localized description"),
        (None, None),
    ]

    assert _deleted_catalog_names(
        events,
        data_key="provider_name",
        description_prefix="Deleted provider ",
    ) == {"bro", "spx"}
