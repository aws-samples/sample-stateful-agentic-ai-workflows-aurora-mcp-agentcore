"""Deterministic loyalty tool coverage."""

import asyncio

from backend.mcp import concierge_server


class _ProfileDb:
    async def execute_one(self, _sql, _params):
        return {
            "loyalty_programs": {
                "marriott_bonvoy": {
                    "program": "Marriott Bonvoy",
                    "member_id": "MB-xx4821",
                    "tier": "Platinum Elite",
                    "points_balance": 86240,
                }
            }
        }


def test_loyalty_balance_reads_seeded_aurora_profile(monkeypatch) -> None:
    monkeypatch.setattr(concierge_server, "_db", lambda: _ProfileDb())

    result = asyncio.run(
        concierge_server.loyalty_balance(
            "trv_meridian_demo",
            "Marriott Bonvoy",
        )
    )

    assert result["tier"] == "Platinum Elite"
    assert result["points_balance"] == 86240
    assert result["source"] == "traveler_profiles.loyalty_programs"
