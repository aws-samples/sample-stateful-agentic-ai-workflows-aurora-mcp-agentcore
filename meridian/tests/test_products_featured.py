"""The featured catalog view honours filters and pages, and keeps its default first page.

The fake database runs the router's real SQL in SQLite over the seeded catalog, so the
ranking, filter, ordering and paging are executed rather than recorded.
"""

import sqlite3

import pytest

import backend.routers.products as products
from backend.routers.products import list_packages, list_products
from scripts.travel_catalog import TRIP_PACKAGES

# The first page the featured view returned before filters and paging were added: the top two
# packages by price in each trip type, ordered by trip type then price, cut at ten rows.
DEFAULT_FIRST_PAGE = [
    "ADV-001", "ADV-004", "BCH-001", "BCH-005", "TKY-003", "BIZ-003", "TKY-005", "CTY-002",
    "FAM-003", "TKY-002",
]
FEATURED_TOTAL = 12


class SqliteCatalog:
    """Executes the router's SQL against the seeded trip packages."""

    def __init__(self, packages=TRIP_PACKAGES) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.create_function("package_places_left", 1, lambda _id: None)
        self.conn.execute(
            "CREATE TABLE trip_packages (package_id TEXT PRIMARY KEY, name TEXT, trip_type TEXT,"
            " destination TEXT, region TEXT, price_per_person REAL, operator TEXT,"
            " description TEXT, image_url TEXT, durations TEXT, highlights TEXT)"
        )
        for p in packages:
            self.conn.execute(
                "INSERT INTO trip_packages (package_id, name, trip_type, destination, region,"
                " price_per_person, operator, description, image_url) VALUES (?,?,?,?,?,?,?,?,?)",
                [p["package_id"], p["name"], p["trip_type"], p.get("destination"),
                 p.get("region"), p["price_per_person"], p.get("operator"),
                 p.get("description"), p.get("image_url")],
            )

    async def execute(self, sql, params=()):
        cursor = self.conn.execute(sql.replace("%s", "?"), tuple(params))
        return [dict(row) for row in cursor.fetchall()]

    async def execute_one(self, sql, params=()):
        rows = await self.execute(sql, params)
        return rows[0] if rows else None


@pytest.fixture
def catalog(monkeypatch):
    db = SqliteCatalog()
    monkeypatch.setattr(products, "get_rds_data_client", lambda: db)
    return db


async def featured(offset=0, limit=50, category=None):
    return await list_products(category=category, limit=limit, offset=offset, featured=True)


def ids(body):
    return [p["product_id"] for p in body["products"]]


async def test_default_first_page_keeps_its_packages_and_order(catalog):
    body = await featured()
    assert ids(body) == DEFAULT_FIRST_PAGE


async def test_the_total_counts_every_featured_package_not_the_page(catalog):
    body = await featured()
    assert body["total"] == FEATURED_TOTAL
    assert len(body["products"]) == 10


async def test_page_two_continues_after_page_one(catalog):
    first = await featured()
    second = await featured(offset=10)
    assert ids(second) == ["WEL-001", "WEL-002"]
    assert not set(ids(first)) & set(ids(second))
    assert second["total"] == FEATURED_TOTAL


async def test_a_trip_type_filter_applies_before_ranking(catalog):
    body = await featured(category="Wellness & Luxury")
    assert ids(body) == ["WEL-001", "WEL-002"]
    assert body["total"] == 2


async def test_an_unknown_trip_type_is_empty(catalog):
    body = await featured(category="Nope")
    assert body == {"products": [], "total": 0}


async def test_paging_inside_a_filter(catalog):
    body = await featured(category="Wellness & Luxury", limit=1, offset=1)
    assert ids(body) == ["WEL-002"]
    assert body["total"] == 2


async def test_the_package_alias_applies_the_same_filter_and_paging(catalog):
    page = await list_packages(trip_type="City Breaks", limit=1, offset=1, featured=True)
    assert [p.package_id for p in page.packages] == ["CTY-002"]
    assert page.total == 2
    unfiltered = await list_packages(trip_type=None, limit=50, offset=10, featured=True)
    assert len(unfiltered.packages) == 2
    assert unfiltered.total == FEATURED_TOTAL


async def test_equal_prices_order_by_package_id(monkeypatch):
    tied = [
        {**TRIP_PACKAGES[0], "package_id": f"TIE-00{n}", "trip_type": "Tied",
         "price_per_person": 1000.0} for n in (3, 1, 2)
    ]
    monkeypatch.setattr(products, "get_rds_data_client", lambda: SqliteCatalog(tied))
    body = await featured()
    assert ids(body) == ["TIE-001", "TIE-002"]
