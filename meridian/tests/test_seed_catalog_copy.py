"""Guard: the seeded traveler text the app shows carries no middle dot."""

from scripts import travel_catalog

MIDDLE_DOT = "·"


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def test_seed_catalog_values_have_no_middle_dot():
    catalog = {
        name: value
        for name, value in vars(travel_catalog).items()
        if name.isupper() and isinstance(value, (list, dict))
    }
    assert "TRAVELER_PROFILES" in catalog and "TRAVELER_PREFERENCES" in catalog
    offences = [
        f"{name}: {text[:60]}"
        for name, value in catalog.items()
        for text in _strings(value)
        if MIDDLE_DOT in text
    ]
    assert offences == []
