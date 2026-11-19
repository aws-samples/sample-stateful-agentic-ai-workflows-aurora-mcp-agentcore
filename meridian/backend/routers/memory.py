"""Memory API — long-term preferences from Aurora.

AWS docs:
  - RDS Data API:
    https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.html
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.agentcore.identity import get_agentcore_identity
from backend.agents.budget import (
    BUDGET_KEYS,
    budget_ceiling_per_traveler_cents,
    budget_is_unclear,
    parse_usd_amounts,
)
from backend.authorization import TravelerAuthorizationError
from backend.db.rds_data_client import get_rds_data_client
from backend.http_auth import (
    HttpPrincipal,
    authorize_traveler,
    require_http_principal,
)
from backend.memory.store import DEMO_TRAVELER_ID, get_memory_store

router = APIRouter(prefix="/api/memory", tags=["memory"])

# The gateway policy derives its ceiling from the traveler's whole preference
# set, so the ceiling this endpoint reports has to be read at the same breadth.
# The drawer keeps showing the highest-confidence handful it always showed;
# reading wider only changes which facts the ceiling is computed from.
GOVERNANCE_FACT_LIMIT = 50
DISPLAY_FACT_LIMIT = 8
BUDGET_AMOUNT_PROMPT = (
    "Enter the budget as one dollar amount or one range, for example $75 per traveler "
    "or $2k-3.5k. More than one amount, or another currency, cannot be applied."
)


class MemoryFactResponse(BaseModel):
    key: str
    value: str
    source: Optional[str] = None
    confidence: Optional[float] = None


class MemoryProfileResponse(BaseModel):
    traveler_id: str
    facts: List[MemoryFactResponse]
    profile: Optional[dict] = None
    # The saved per-traveler cap the gateway policy multiplies by the party. The
    # UI shows this rather than the profile's budget column, so what a traveler
    # reads on screen is the basis Cedar actually judged. None when unsaved.
    budget_ceiling_per_traveler_cents: Optional[int] = None
    # True when a budget is saved but is not one dollar amount or one range, so the cap above
    # is None and the gateway authorizes nothing. Lets the UI ask for an amount rather than
    # show "Not set". Older clients ignore it.
    budget_unclear: bool = False


class MemoryFactUpdate(BaseModel):
    value: str = Field(min_length=1, max_length=1000)


@router.get("/{traveler_id}", response_model=MemoryProfileResponse)
async def get_memory_profile(
    traveler_id: str = DEMO_TRAVELER_ID,
    principal: HttpPrincipal = Depends(require_http_principal),
) -> MemoryProfileResponse:
    traveler_id = await authorize_traveler(principal, traveler_id)
    store = get_memory_store()
    db = get_rds_data_client()
    # Pin RLS for the read so the API endpoint exercises the same isolation
    # path the concierge agent uses.
    try:
        async with db.scoped_session(
            traveler_id=traveler_id,
            agent_type="memory_agent",
            authorization=get_agentcore_identity().authorization_context(),
        ) as tx:
            facts = await store.recall_preferences(
                traveler_id, limit=GOVERNANCE_FACT_LIMIT, transaction_id=tx
            )
            profile = await store.recall_profile(traveler_id, transaction_id=tx)
    except TravelerAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return MemoryProfileResponse(
        traveler_id=traveler_id,
        facts=[
            MemoryFactResponse(
                key=f["key"],
                value=f["value"],
                source=f.get("source"),
                confidence=f.get("confidence"),
            )
            for f in facts[:DISPLAY_FACT_LIMIT]
        ],
        profile=profile,
        budget_ceiling_per_traveler_cents=budget_ceiling_per_traveler_cents(facts),
        budget_unclear=budget_is_unclear(facts),
    )


@router.patch(
    "/{traveler_id}/facts/{preference_key}",
    response_model=MemoryFactResponse,
)
async def update_memory_fact(
    traveler_id: str,
    preference_key: str,
    update: MemoryFactUpdate,
    principal: HttpPrincipal = Depends(require_http_principal),
) -> MemoryFactResponse:
    traveler_id = await authorize_traveler(principal, traveler_id)
    preference_key = preference_key.strip()
    value = update.value.strip()
    if not preference_key:
        raise HTTPException(status_code=422, detail="Preference key cannot be empty")
    if not value:
        raise HTTPException(status_code=422, detail="Preference value cannot be empty")
    if len(preference_key) > 100:
        raise HTTPException(status_code=422, detail="Preference is too long")
    if preference_key.lower() in BUDGET_KEYS and parse_usd_amounts(value) is None:
        raise HTTPException(status_code=422, detail=BUDGET_AMOUNT_PROMPT)

    store = get_memory_store()
    db = get_rds_data_client()
    try:
        async with db.scoped_session(
            traveler_id=traveler_id,
            agent_type="memory_agent",
            authorization=get_agentcore_identity().authorization_context(),
        ) as tx:
            fact = await store.update_preference(
                traveler_id,
                preference_key,
                value,
                transaction_id=tx,
            )
        return MemoryFactResponse(**fact)
    except HTTPException:
        raise
    except TravelerAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Traveler memory is unavailable") from exc


@router.delete("/{traveler_id}/facts/{preference_key}")
async def delete_memory_fact(
    traveler_id: str,
    preference_key: str,
    principal: HttpPrincipal = Depends(require_http_principal),
) -> dict:
    traveler_id = await authorize_traveler(principal, traveler_id)
    preference_key = preference_key.strip()
    if not preference_key or len(preference_key) > 100:
        raise HTTPException(status_code=422, detail="Preference key is invalid")
    store = get_memory_store()
    db = get_rds_data_client()
    try:
        async with db.scoped_session(
            traveler_id=traveler_id,
            agent_type="memory_agent",
            authorization=get_agentcore_identity().authorization_context(),
        ) as tx:
            await store.delete_preference(
                traveler_id,
                preference_key,
                transaction_id=tx,
            )
        return {"deleted": True, "key": preference_key}
    except TravelerAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Traveler memory is unavailable") from exc
