"""search_trips against the live catalog: the rows it returns are the rows the filters allow.

Expected values come from the catalog at run time, so a reseeded catalog does not break them.
"""

import pytest

from backend.db.rds_data_client import get_rds_data_client
from backend.tools import catalog_tools

pytestmark = pytest.mark.database

TEXT_COLUMNS = catalog_tools.TEXT_COLUMNS


async def _a_trip_type_with_a_ceiling() -> tuple[str, float]:
    """A trip type with at least three packages, and a ceiling that keeps some but not all."""
    rows = await get_rds_data_client().execute(
        "SELECT trip_type, price_per_person FROM trip_packages ORDER BY trip_type, price_per_person"
    )
    by_type: dict[str, list[float]] = {}
    for row in rows:
        by_type.setdefault(row["trip_type"], []).append(float(row["price_per_person"]))
    for trip_type, prices in by_type.items():
        if len(prices) >= 3 and prices[0] < prices[-1]:
            return trip_type, prices[len(prices) // 2]
    pytest.skip("no trip type in the catalog has three packages at differing prices")


async def _prices_for(trip_type: str, ceiling: float) -> list[float]:
    rows = await get_rds_data_client().execute(
        "SELECT price_per_person FROM trip_packages WHERE trip_type = %s "
        "AND price_per_person <= %s ORDER BY price_per_person",
        (trip_type, ceiling),
    )
    return [float(row["price_per_person"]) for row in rows]


async def test_trip_type_and_ceiling_return_only_matching_rows_cheapest_first():
    trip_type, ceiling = await _a_trip_type_with_a_ceiling()
    expected = await _prices_for(trip_type, ceiling)
    assert expected

    found = await catalog_tools.search_trips(
        trip_type=trip_type, max_price_per_person=ceiling, limit=20
    )

    packages = found["packages"]
    assert {p["trip_type"] for p in packages} == {trip_type}
    assert all(p["price_per_person"] <= ceiling for p in packages)
    assert [p["price_per_person"] for p in packages] == expected[:20]


async def test_the_limit_caps_the_rows_returned():
    trip_type, ceiling = await _a_trip_type_with_a_ceiling()
    available = len(await _prices_for(trip_type, ceiling))
    assert available >= 2

    found = await catalog_tools.search_trips(
        trip_type=trip_type, max_price_per_person=ceiling, limit=1
    )

    assert len(found["packages"]) == 1
    assert found["packages"][0]["price_per_person"] == (await _prices_for(trip_type, ceiling))[0]


async def test_free_text_returns_rows_that_contain_the_words():
    rows = await get_rds_data_client().execute(
        "SELECT destination FROM trip_packages WHERE destination IS NOT NULL "
        "AND destination <> '' ORDER BY package_id LIMIT 1"
    )
    if not rows:
        pytest.skip("no package in the catalog has a destination")
    word = rows[0]["destination"].split()[0]

    found = await catalog_tools.search_trips(text=word, limit=20)

    assert found["packages"]
    for package in found["packages"]:
        haystack = " ".join(str(package[column] or "") for column in TEXT_COLUMNS).lower()
        assert word.lower() in haystack


async def test_a_hostile_trip_type_returns_no_rows():
    found = await catalog_tools.search_trips(trip_type="City Breaks' OR '1'='1")

    assert found["packages"] == []


async def test_a_percent_sign_in_text_is_not_a_wildcard():
    found = await catalog_tools.search_trips(text="%", limit=20)

    for package in found["packages"]:
        haystack = " ".join(str(package[column] or "") for column in TEXT_COLUMNS)
        assert "%" in haystack
