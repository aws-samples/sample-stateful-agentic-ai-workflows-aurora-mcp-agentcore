"""Phase 3 reads package details the way the MeridianHolds Gateway tool does."""

import asyncio
import re
import sys
from pathlib import Path

from backend.tools import package_tools

HOLDS = (Path(__file__).resolve().parents[1] / "meridian_agentcore" / "agentcore"
         / "gateway_targets" / "meridian_holds")
sys.path.insert(0, str(HOLDS))

import lambda_function as holds  # noqa: E402


class _Db:
    def __init__(self, row):
        self.row, self.reads = row, []

    async def execute_one(self, sql, params=None, transaction_id=None):
        self.reads.append((" ".join(sql.split()), tuple(params or ())))
        return self.row


ROW = {"package_id": "CTY-002", "name": "Tokyo", "operator": "Nippon", "destination": "Tokyo",
       "region": "Asia", "price_per_person": 2500.0, "durations": ["7 nights"],
       "availability": {"7 nights": 4}, "highlights": ["Sushi"]}


def test_the_sql_is_the_holds_lambdas_with_positional_placeholders():
    expected = re.sub(r":package_id\b", "%s", holds.PACKAGE_SQL)
    assert " ".join(package_tools.PACKAGE_SQL.split()) == " ".join(expected.split())


def test_the_result_has_the_gateway_tools_shape_and_summary(monkeypatch):
    db = _Db(dict(ROW))
    monkeypatch.setattr(package_tools, "get_rds_data_client", lambda: db)

    result = asyncio.run(package_tools.get_package_details("CTY-002"))

    assert result["package"]["availability"] == {"7 nights": 4}
    assert result["summary"] == "Tokyo, 7 nights: 4 places left"
    assert db.reads[0][1] == ("CTY-002",)
    gateway_summary = f"{ROW['name']}, " + ", ".join(
        f"{k}: {v} places left" for k, v in ROW["availability"].items())
    assert result["summary"] == gateway_summary


def test_an_unknown_package_is_answered_with_its_code_not_an_empty_package(monkeypatch):
    monkeypatch.setattr(package_tools, "get_rds_data_client", lambda: _Db(None))
    assert package_tools.UNKNOWN_PACKAGE == "unknown_package"
    assert asyncio.run(package_tools.get_package_details("NOPE")) == {
        "error": "unknown_package", "package_id": "NOPE"}


def test_the_gateway_tool_answers_an_unknown_package_the_same_way(monkeypatch):
    monkeypatch.setattr(package_tools, "get_rds_data_client", lambda: _Db(None))
    monkeypatch.setattr(holds, "query", lambda *_args, **_kwargs: [])

    in_process = asyncio.run(package_tools.get_package_details(" NOPE "))

    assert holds.get_package_details({"packageId": " NOPE "}) == in_process


def test_a_blank_id_reads_nothing(monkeypatch):
    db = _Db(dict(ROW))
    monkeypatch.setattr(package_tools, "get_rds_data_client", lambda: db)
    assert asyncio.run(package_tools.get_package_details("  ")) == {
        "error": "unknown_package", "package_id": ""}
    assert db.reads == []
