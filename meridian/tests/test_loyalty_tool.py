"""Loyalty tool: reads the seeded profile under the traveler grant, refuses the rest."""

import asyncio
from contextlib import asynccontextmanager

import pytest

from backend.authorization import AuthorizationDecision, TravelerAuthorizationError
from backend.mcp import concierge_server


class _ProfileDb:
    def __init__(self, decision: AuthorizationDecision | None = None) -> None:
        self.decision = decision
        self.reads: list[tuple[str, tuple, str | None]] = []
        self.scopes: list[dict] = []

    @asynccontextmanager
    async def scoped_session(self, **kwargs):
        self.scopes.append(kwargs)
        if self.decision is not None:
            raise TravelerAuthorizationError(self.decision)
        yield "txn-loyalty"

    async def execute_one(self, sql, params, transaction_id=None):
        self.reads.append((sql, params, transaction_id))
        return {
            "loyalty_programs": {
                "marriott_bonvoy": {
                    "program": "Marriott Bonvoy",
                    "member_id": "MB xxxx4821",
                    "tier": "Platinum Elite",
                    "points_balance": 86240,
                }
            }
        }


def test_loyalty_balance_reads_seeded_aurora_profile_inside_scoped_session(monkeypatch) -> None:
    db = _ProfileDb()
    monkeypatch.setattr(concierge_server, "_db", lambda: db)
    monkeypatch.setattr(concierge_server, "_authorization", lambda: "authz")

    result = asyncio.run(
        concierge_server.loyalty_balance("trv_meridian_demo", "Marriott Bonvoy")
    )

    assert result["tier"] == "Platinum Elite"
    assert result["member_id"] == "MB xxxx4821"
    assert result["points_balance"] == 86240
    assert result["source"] == "traveler_profiles.loyalty_programs"
    assert db.scopes == [
        {"traveler_id": "trv_meridian_demo", "agent_type": "concierge_agent",
         "authorization": "authz"}
    ]
    (_sql, params, transaction_id), = db.reads
    assert params == ("trv_meridian_demo",)
    assert transaction_id == "txn-loyalty"


def test_loyalty_balance_refuses_an_unbound_traveler_without_reading(monkeypatch) -> None:
    refused = AuthorizationDecision(
        allowed=False, decision="deny", traveler_id="trv_demo_decoy",
        provider="aws_iam", subject_id="AROA-test", principal="arn:aws:sts::000:assumed-role/x",
        reason="no active identity binding",
    )
    db = _ProfileDb(decision=refused)
    monkeypatch.setattr(concierge_server, "_db", lambda: db)
    monkeypatch.setattr(concierge_server, "_authorization", lambda: "authz")

    result = asyncio.run(
        concierge_server.loyalty_balance("trv_demo_decoy", "Marriott Bonvoy")
    )

    assert result["error"] == "traveler_not_authorized"
    assert result["traveler_id"] == "trv_demo_decoy"
    assert "points_balance" not in result
    assert db.reads == []


@pytest.mark.parametrize("program", ["Unlisted program", "", "   "])
def test_missing_program_never_returns_generated_points(monkeypatch, program):
    from backend.routers.chat import _format_domain_reply

    db = _ProfileDb()
    monkeypatch.setattr(concierge_server, "_db", lambda: db)
    monkeypatch.setattr(concierge_server, "_authorization", lambda: "authz")
    result = asyncio.run(concierge_server.loyalty_balance("trv_meridian_demo", program))
    assert result["error"] == "loyalty_balance_unavailable"
    assert "points_balance" not in result
    assert len(db.reads) == 1
    reply = _format_domain_reply("loyalty_balance", result)
    assert "no recorded balance" in reply
    assert "holds no grant" not in reply


def test_missing_balance_is_unavailable_instead_of_zero(monkeypatch):
    class NoBalance(_ProfileDb):
        async def execute_one(self, *args, **kwargs):
            return {"loyalty_programs": {"marriott": {"program": "Marriott Bonvoy"}}}

    monkeypatch.setattr(concierge_server, "_db", NoBalance)
    monkeypatch.setattr(concierge_server, "_authorization", lambda: "authz")
    result = asyncio.run(concierge_server.loyalty_balance("trv_meridian_demo", "Marriott Bonvoy"))
    assert result["error"] == "loyalty_balance_unavailable"
    assert "points_balance" not in result
