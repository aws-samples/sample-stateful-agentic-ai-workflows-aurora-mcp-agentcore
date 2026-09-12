"""The selected presentation phase cannot weaken authorization for business writes."""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from backend.http_auth import HttpPrincipal
from backend.routers import chat, journeys

PRINCIPAL = HttpPrincipal("test-subject", "trv_meridian_demo", "test")


@pytest.mark.parametrize("phase", [1, 2, 3, 4, 5])
async def test_every_phase_routes_clicked_holds_through_governance(monkeypatch, phase):
    governed = AsyncMock(return_value=chat.OrderResponse(message="Policy refused", activities=[]))
    monkeypatch.setattr(chat, "production_hold", governed)
    monkeypatch.setattr(chat, "get_rds_data_client", lambda: pytest.fail("Direct database fallback"))
    result = await chat.process_order(
        chat.OrderRequest(product_id="PKG-1", phase=phase, quantity=7), PRINCIPAL
    )
    assert result.order is None
    governed.assert_awaited_once()
    assert governed.call_args.args[0].traveler_id == PRINCIPAL.traveler_id
    assert governed.call_args.args[0].quantity == 7


async def test_a_policy_path_failure_never_falls_back_to_sql(monkeypatch):
    monkeypatch.setattr(chat, "production_hold", AsyncMock(side_effect=HTTPException(503, "Unavailable")))
    monkeypatch.setattr(chat, "get_rds_data_client", lambda: pytest.fail("Direct database fallback"))
    with pytest.raises(HTTPException) as failure:
        await chat.process_order(chat.OrderRequest(product_id="PKG-1", phase=1), PRINCIPAL)
    assert failure.value.status_code == 503


async def test_traveler_mismatch_never_reaches_the_writer(monkeypatch):
    governed = AsyncMock()
    monkeypatch.setattr(chat, "production_hold", governed)
    with pytest.raises(HTTPException) as failure:
        await chat.process_order(
            chat.OrderRequest(product_id="PKG-1", phase=4, traveler_id="other"), PRINCIPAL
        )
    assert failure.value.status_code == 403
    governed.assert_not_awaited()


@pytest.mark.parametrize("revoked", [False, True])
async def test_journey_listing_rechecks_the_workload_grant(monkeypatch, revoked):
    authorization = object()
    execute = AsyncMock(return_value=[])

    @asynccontextmanager
    async def scoped(**kwargs):
        assert kwargs == {
            "traveler_id": PRINCIPAL.traveler_id,
            "agent_type": "booking_agent",
            "authorization": authorization,
        }
        if revoked:
            raise PermissionError("grant revoked")
        yield "scoped-transaction"

    monkeypatch.setattr(journeys, "get_agentcore_identity", lambda: SimpleNamespace(
        authorization_context=lambda: authorization
    ))
    monkeypatch.setattr(journeys, "get_rds_data_client", lambda: SimpleNamespace(
        scoped_session=scoped, execute=execute
    ))
    if revoked:
        with pytest.raises(HTTPException) as failure:
            await journeys.list_journeys(PRINCIPAL, None, 10)
        assert failure.value.status_code == 403
        execute.assert_not_awaited()
    else:
        result = await journeys.list_journeys(PRINCIPAL, None, 10)
        assert result["journeys"] == []
        assert execute.call_args.kwargs["transaction_id"] == "scoped-transaction"
