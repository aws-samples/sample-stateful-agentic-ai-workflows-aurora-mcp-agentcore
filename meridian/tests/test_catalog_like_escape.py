"""price_range and region_inventory make LIKE wildcards in their argument literal."""

import pytest

from backend.tools import catalog_tools
from tests.test_catalog_lambda import call, catalog


class Capture:
    def __init__(self):
        self.params = None

    async def execute_one(self, _sql, params):
        self.params = params
        return None

    async def execute(self, _sql, params):
        self.params = params
        return []


@pytest.fixture
def module_db(monkeypatch):
    capture = Capture()
    monkeypatch.setattr(catalog_tools, "get_rds_data_client", lambda: capture)
    return capture


@pytest.fixture
def lambda_params(monkeypatch):
    seen = {}

    def fake_execute(_sql, params=()):
        seen["params"] = params
        return []

    monkeypatch.setattr(catalog, "CONFIG", ("arn:cluster", "arn:gateway-secret", "meridian"))
    monkeypatch.setattr(catalog, "execute", fake_execute)
    return seen


@pytest.mark.parametrize(
    ("argument", "pattern"),
    [("%", "%\\%%"), ("a_b", "%a\\_b%"), ("100%", "%100\\%%"), ("c:\\x", "%c:\\\\x%")],
)
async def test_price_range_binds_an_escaped_pattern(module_db, argument, pattern):
    await catalog_tools.price_range(argument)

    assert module_db.params == (pattern, pattern)


@pytest.mark.parametrize(
    ("argument", "pattern"), [("%", "%\\%%"), ("a_b", "%a\\_b%")]
)
async def test_region_inventory_binds_an_escaped_pattern(module_db, argument, pattern):
    await catalog_tools.region_inventory(argument)

    assert module_db.params == (pattern,)


@pytest.mark.parametrize(("argument", "pattern"), [("%", "%\\%%"), ("a_b", "%a\\_b%")])
def test_the_lambda_price_range_binds_an_escaped_pattern(lambda_params, argument, pattern):
    call("price_range", {"destination": argument})

    assert lambda_params["params"] == (pattern, pattern)


@pytest.mark.parametrize(("argument", "pattern"), [("%", "%\\%%"), ("a_b", "%a\\_b%")])
def test_the_lambda_region_inventory_binds_an_escaped_pattern(
    lambda_params, argument, pattern
):
    call("region_inventory", {"region": argument})

    assert lambda_params["params"] == (pattern,)
