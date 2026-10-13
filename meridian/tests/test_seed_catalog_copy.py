"""Guard: the seeded traveler text the app shows carries no middle dot, em dash or en dash."""

from scripts import travel_catalog

BANNED = {"·": "middle dot", "—": "em dash", "–": "en dash"}


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _strings(item)


def test_seed_catalog_values_have_no_middle_dot_or_dash():
    catalog = {
        name: value
        for name, value in vars(travel_catalog).items()
        if name.isupper() and isinstance(value, (list, dict))
    }
    assert "TRAVELER_PROFILES" in catalog and "DEMO_CONVERSATION_MESSAGES" in catalog
    offences = [
        f"{name} ({label}): {text[:60]}"
        for name, value in catalog.items()
        for text in _strings(value)
        for mark, label in BANNED.items()
        if mark in text
    ]
    assert offences == []
