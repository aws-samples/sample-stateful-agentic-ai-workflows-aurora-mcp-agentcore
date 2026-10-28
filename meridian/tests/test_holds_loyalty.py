"""loyalty_balance runs under the workload grant and RLS, and refuses an unbound traveler."""

import json

import pytest

from backend.mcp import concierge_server
from tests.lambda_targets_support import TARGETS
# The same module object the `config` fixture patches; the fixture is used by name below.
from tests.test_holds_lambda import FakeDataApi, _context, config, holds  # noqa: F401

pytestmark = pytest.mark.usefixtures("config")
TARGET = TARGETS / "meridian_holds"
PROGRAMS = {"marriott_bonvoy": {"program": "Marriott Bonvoy", "member_id": "MB xxxx4821",
                                "tier": "Platinum Elite", "points_balance": 86240}}
TOOL = "MeridianHolds___loyalty_balance"


def _args(**overrides):
    return {"travelerId": "trv_meridian_demo", "program": "Marriott Bonvoy", **overrides}


def _api(**extra):
    return FakeDataApi({
        "FROM traveler_identity_bindings": [{"binding_id": "bind_1"}],
        "FROM traveler_profiles": [{"loyalty_programs": json.dumps(PROGRAMS)}],
        **extra,
    })


def test_a_bound_traveler_reads_the_saved_balance_inside_one_scoped_transaction(monkeypatch):
    api = _api()
    monkeypatch.setattr(holds, "RDS", api)

    result = holds.lambda_handler(_args(), _context(TOOL))

    assert result["points_balance"] == 86240 and result["tier"] == "Platinum Elite"
    assert result["member_id"] == "MB xxxx4821"
    assert result["governance"]["decision"] == "allow"
    assert "Platinum Elite" in result["summary"]
    joined = "\n".join(api.statements)
    assert joined.index("traveler_access_audit") < joined.index("SET LOCAL ROLE")
    assert joined.index("SET LOCAL ROLE") < joined.index("FROM traveler_profiles")
    assert "set_config('app.current_traveler_id'" in joined
    assert api.tx == ["begin", "commit"]
    read = api.parameters[api.statements.index(holds.LOYALTY_SQL)]
    assert read == {"traveler": "trv_meridian_demo"}


def test_a_traveler_the_workload_is_not_bound_to_is_refused_and_recorded(monkeypatch):
    api = FakeDataApi({"FROM traveler_identity_bindings": []})
    monkeypatch.setattr(holds, "RDS", api)

    result = holds.lambda_handler(_args(travelerId="trv_demo_decoy"), _context(TOOL))

    assert result["error"] == "traveler_not_authorized"
    assert result["governance"]["decision"] == "deny"
    assert not any("FROM traveler_profiles" in s for s in api.statements)
    assert "SET LOCAL ROLE" not in "\n".join(api.statements)
    audits = [p for s, p in zip(api.statements, api.parameters, strict=True)
              if "traveler_access_audit" in s]
    assert audits[-1]["decision"] == "deny" and audits[-1]["traveler"] == "trv_demo_decoy"
    # The answer names the deny row the Lambda kept, so the audit can name the grant's refusal.
    assert result["governance"]["access_audit_id"] == audits[-1]["audit_id"]
    assert api.tx == ["begin", "rollback"]


def test_a_program_the_traveler_does_not_have_is_unavailable_and_invents_no_points(
    monkeypatch,
):
    monkeypatch.setattr(holds, "RDS", _api())
    result = holds.lambda_handler(_args(program="United MileagePlus"), _context(TOOL))
    assert result["error"] == "loyalty_balance_unavailable"
    assert "points_balance" not in result and result["governance"]["decision"] == "allow"


def test_a_database_error_comes_back_as_a_named_error_and_rolls_back(monkeypatch):
    class Failing(FakeDataApi):
        def execute_statement(self, **kwargs):
            if "FROM traveler_profiles" in kwargs["sql"]:
                raise RuntimeError("traveler_scope_mismatch")
            return super().execute_statement(**kwargs)

    api = Failing({"FROM traveler_identity_bindings": [{"binding_id": "bind_1"}]})
    monkeypatch.setattr(holds, "RDS", api)
    result = holds.lambda_handler(_args(), _context(TOOL))
    assert result == {"error": "traveler_scope_mismatch"}
    assert api.tx == ["begin", "rollback"]


@pytest.mark.parametrize("program", ["Marriott Bonvoy", "bonvoy", "marriott bonvoy rewards",
                                     "United MileagePlus", "", "  "])
@pytest.mark.parametrize("programs", [PROGRAMS, {}, None, {"x": "not a dict"},
                                      {"y": {"program": "P", "points_balance": None}}])
def test_the_port_matches_the_servers_matcher(program, programs):
    assert holds.loyalty_from_programs("trv_x", program, programs) == (
        concierge_server.loyalty_from_programs("trv_x", program, programs))


def test_the_tool_schema_requires_the_pinned_traveler_and_the_program():
    tools = {t["name"]: t for t in json.loads((TARGET / "tool-schema.json").read_text())}
    schema = tools["loyalty_balance"]["inputSchema"]
    assert schema["required"] == ["travelerId", "program"]
    assert list(schema["properties"]) == ["travelerId", "program"]
    assert schema["additionalProperties"] is False


def test_the_tool_is_registered_with_the_lambda_handler():
    assert "loyalty_balance" in holds.TOOLS
