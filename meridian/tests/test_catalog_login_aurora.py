"""The MeridianCatalog Lambda, run as the meridian_catalog login, reads what Aurora holds."""

import os
import uuid

import boto3
import pytest


from backend.db.rds_data_client import get_rds_data_client
from tests.lambda_targets_support import load_target
from tests.live_support import JORDAN, live_region, require_env

pytestmark = pytest.mark.database


@pytest.fixture
def catalog(monkeypatch):
    require_env("AURORA_CATALOG_SECRET_ARN")
    secret = os.environ["AURORA_CATALOG_SECRET_ARN"]
    module = load_target("meridian_catalog", "meridian_catalog_live")
    region = live_region()
    monkeypatch.setattr(module, "CONFIG", (
        os.environ["AURORA_CLUSTER_ARN"], secret, os.getenv("AURORA_DATABASE", "meridian")))
    monkeypatch.setattr(module, "RDS", boto3.client("rds-data", region_name=region))
    monkeypatch.setattr(module, "BEDROCK", boto3.client("bedrock-runtime", region_name=region))
    return module


async def _master(sql, params=()):
    return await get_rds_data_client().execute(sql, tuple(params))


async def test_search_trips_returns_the_rows_the_master_reads_for_the_same_filter(catalog):
    kinds = await _master("SELECT DISTINCT trip_type FROM trip_packages ORDER BY 1 LIMIT 1")
    trip_type = kinds[0]["trip_type"]

    result = catalog.search_trips({"trip_type": trip_type, "max_price_per_person": 100000,
                                   "limit": 3})

    expected = await _master(
        "SELECT package_id FROM trip_packages WHERE trip_type = %s AND price_per_person <= %s "
        "ORDER BY price_per_person ASC, package_id ASC LIMIT 3", (trip_type, 100000.0))
    assert [p["package_id"] for p in result["packages"]] == [r["package_id"] for r in expected]
    assert result["packages"] and all(p["trip_type"] == trip_type for p in result["packages"])


async def test_price_range_equals_the_aggregate_aurora_computes(catalog):
    rows = await _master(
        "SELECT MIN(price_per_person)::float AS lo, MAX(price_per_person)::float AS hi, "
        "COUNT(*) AS n FROM trip_packages WHERE destination ILIKE %s OR region ILIKE %s",
        ("%Tokyo%", "%Tokyo%"))
    result = catalog.price_range({"destination": "Tokyo"})
    assert result["sample_size"] == rows[0]["n"]
    assert (result["low"], result["high"]) == (round(rows[0]["lo"], 2), round(rows[0]["hi"], 2))


async def test_compare_packages_returns_the_packages_asked_for(catalog):
    ids = [r["package_id"] for r in await _master("SELECT package_id FROM trip_packages LIMIT 2")]
    found = catalog.compare_packages({"package_ids": ids})
    assert sorted(p["package_id"] for p in found) == sorted(ids)


async def test_region_inventory_counts_what_aurora_counts(catalog):
    region = (await _master(
        "SELECT region FROM trip_packages WHERE region IS NOT NULL ORDER BY region LIMIT 1")
              )[0]["region"]
    counted = await _master(
        "SELECT package_id, package_places_left(package_id) AS availability "
        "FROM trip_packages WHERE region ILIKE %s", (f"%{region}%",))
    slots = sum(sum(int(v or 0) for v in (row["availability"] or {}).values())
                for row in counted)

    result = catalog.region_inventory({"region": region})

    assert result["package_count"] == len(counted)
    assert result["total_departure_slots"] == slots


async def test_hybrid_search_keeps_to_the_ceiling_and_to_packages_with_places(catalog):
    result = catalog.hybrid_trip_search(
        {"query": "quiet boutique city stay in Tokyo under $3,000", "limit": 5})

    assert result["packages"], "the live catalog has Tokyo trips under $3,000"
    assert result["reranked"] is True
    assert result["candidates"]["semantic"] > 0 and result["candidates"]["merged"] > 0
    ids = [p["package_id"] for p in result["packages"]]
    placeholders = ",".join(["%s"] * len(ids))
    stored = {r["package_id"]: r for r in await _master(
        "SELECT package_id, price_per_person, package_places_left(package_id) AS availability "
        f"FROM trip_packages WHERE package_id IN ({placeholders})", ids)}
    assert set(stored) == set(ids)
    for package in result["packages"]:
        row = stored[package["package_id"]]
        assert float(row["price_per_person"]) <= 3000.0
        assert catalog.total_places(row["availability"]) > 0


async def _open_hold(booking_id):
    """Insert an unexpired held booking as the master (outside RLS); lines are added after."""
    await _master(
        "INSERT INTO bookings (booking_id, traveler_id, status, total_amount, hold_expires_at) "
        "VALUES (%s, %s, 'held', 1.00, CURRENT_TIMESTAMP + INTERVAL '15 minutes')",
        (booking_id, JORDAN))


async def _add_line(booking_id, package_id, duration, places):
    await _master(
        "INSERT INTO booking_lines (booking_id, package_id, duration, travelers_count, "
        "unit_price) VALUES (%s, %s, %s, %s, 1.00)", (booking_id, package_id, duration, places))


async def _release(booking_id):
    await _master("DELETE FROM booking_lines WHERE booking_id = %s", (booking_id,))
    await _master("DELETE FROM bookings WHERE booking_id = %s", (booking_id,))


async def test_a_sold_out_package_is_never_offered(catalog):
    rows = await _master(
        "SELECT package_id, name, package_places_left(package_id) AS availability "
        "FROM trip_packages ORDER BY package_id")
    open_rows = [r for r in rows if isinstance(r["availability"], dict)
                 and catalog.total_places(r["availability"]) > 0]
    if not open_rows:
        pytest.fail("no catalog package has places left to sell out: the seed is missing")
    target = open_rows[0]
    query = {"query": target["name"], "limit": 20}
    offered = [p["package_id"] for p in catalog.hybrid_trip_search(query)["packages"]]
    assert target["package_id"] in offered, "premise: a package with places is offered"

    booking_id = f"r2t19-{uuid.uuid4().hex[:10]}"
    try:
        await _open_hold(booking_id)
        for duration, places in target["availability"].items():
            if int(places or 0) > 0:
                await _add_line(booking_id, target["package_id"], duration, int(places))
        left = await _master("SELECT package_places_left(%s) AS availability",
                             (target["package_id"],))
        assert catalog.total_places(left[0]["availability"]) == 0

        result = catalog.hybrid_trip_search(query)
    finally:
        await _release(booking_id)

    assert result["candidates"]["merged"] > 0
    assert target["package_id"] not in [p["package_id"] for p in result["packages"]]


def test_the_login_cannot_write_through_the_lambdas_reader(catalog):
    with pytest.raises(Exception, match="permission denied"):
        catalog.execute("UPDATE trip_packages SET name = name WHERE false")
