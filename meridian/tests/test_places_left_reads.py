"""Every read that tells a traveler how many places a trip has reports places left.

``trip_packages.availability`` is capacity. A hold counts confirmed bookings and
unexpired holds against it without changing the column, so a read of the raw column
advertises places that are already reserved. The fake catalog below answers with the
raw capacity unless the query asks ``package_places_left``, so a read path that falls
back to the column reports the wrong number and fails here.
"""

import ast
import json
import re
import sys
from pathlib import Path

import pytest

from backend.retrieval import availability
from backend.routers import products

CAPACITY = {"3 nights": 2, "7 nights": 4}
PLACES_LEFT = {"3 nights": 0, "7 nights": 1}
ROW = {
    "package_id": "TKY-003", "name": "Tokyo Culture Week", "operator": "Meridian",
    "price_per_person": 1949.0, "description": "", "image_url": "", "trip_type": "City Breaks",
    "destination": "Tokyo", "region": "Asia", "durations": ["3 nights", "7 nights"],
    "highlights": [],
}


def _row_for(sql: str) -> dict:
    places = PLACES_LEFT if "package_places_left(package_id) AS availability" in sql else CAPACITY
    return {**ROW, "availability": places}


class CatalogDb:
    async def execute(self, sql, params=None, transaction_id=None):
        return [_row_for(sql)]

    async def execute_one(self, sql, params=None, transaction_id=None):
        return _row_for(sql)


async def test_the_availability_step_reports_places_left(monkeypatch):
    monkeypatch.setattr(availability, "get_rds_data_client", CatalogDb)
    found, _spans, message = await availability.retrieval_availability_search(
        "Is it available?", package_id="TKY-003"
    )
    assert found[0].availability == PLACES_LEFT
    assert "1 places left" in message


async def test_the_package_detail_api_reports_places_left(monkeypatch):
    monkeypatch.setattr(products, "get_rds_data_client", CatalogDb)
    package = await products._get_package("TKY-003")
    assert package.availability == PLACES_LEFT


class LambdaDataApi:
    def execute_statement(self, **kwargs):
        return {"formattedRecords": json.dumps([
            {**_row_for(kwargs["sql"]), "availability": json.dumps(_row_for(kwargs["sql"])
                                                                   ["availability"])}
        ])}


@pytest.fixture
def holds(monkeypatch):
    target = (Path(__file__).resolve().parents[1] / "meridian_agentcore" / "agentcore"
              / "gateway_targets" / "meridian_holds")
    monkeypatch.syspath_prepend(str(target))
    sys.modules.pop("lambda_function", None)
    import lambda_function

    monkeypatch.setattr(lambda_function, "CONFIG", lambda_function.AuroraConfig("a", "s", "d"))
    monkeypatch.setattr(lambda_function, "RDS", LambdaDataApi())
    yield lambda_function
    sys.modules.pop("lambda_function", None)


def test_the_gateway_package_details_report_places_left(holds):
    result = holds.get_package_details({"packageId": "TKY-003"})
    assert result["package"]["availability"] == PLACES_LEFT
    assert "7 nights: 1 places left" in result["summary"]


ROOT = Path(__file__).resolve().parents[1]
SOURCES = [
    path
    for folder in ("backend", "meridian_agentcore/agentcore/gateway_targets", "scripts")
    for path in (ROOT / folder).rglob("*.py")
    if "MeridianWorkflow/backend" not in path.as_posix() and "/.cache/" not in path.as_posix()
]
# A column list kept apart from its query, as products.py and search_utils.py build them.
# A model prompt that lists the schema is prose, not a select list.
MAX_COLUMN_LIST_WORDS = 40
COLUMN_LIST = re.compile(r"\bdurations\s*,\s*availability\b(?!\s*\()")
RAW_AVAILABILITY = re.compile(r"(?<!\bAS )(?<![\w.>])\bavailability\b(?!\s*\()", re.I)


def _sql_strings(path: Path):
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value
        elif isinstance(node, ast.JoinedStr):
            text = "".join(
                part.value for part in node.values
                if isinstance(part, ast.Constant) and isinstance(part.value, str)
            )
            yield node.lineno, text


def _selects_raw_availability(lower: str) -> bool:
    """A catalog SELECT, or a catalog column list built for one, naming the raw column."""
    if "from trip_packages" in lower:
        columns = lower.split("from trip_packages")[0]
        if "select" not in columns:
            return False
    elif len(lower.split()) <= MAX_COLUMN_LIST_WORDS and COLUMN_LIST.search(lower):
        columns = lower
    else:
        return False
    columns = columns.replace("package_places_left(package_id) as availability", "")
    return bool(RAW_AVAILABILITY.search(columns))


def test_no_catalog_read_selects_raw_capacity_as_availability():
    """A SELECT on trip_packages that returns availability must return places left."""
    offenders = []
    for path in SOURCES:
        for line, text in _sql_strings(path):
            if _selects_raw_availability(text.lower()):
                offenders.append(f"{path.relative_to(ROOT)}:{line}")
    assert offenders == [], f"raw capacity read as availability: {offenders}"
