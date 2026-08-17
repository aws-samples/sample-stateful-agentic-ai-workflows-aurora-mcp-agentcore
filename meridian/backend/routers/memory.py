"""Memory API — long-term preferences from Aurora.

AWS docs:
  - RDS Data API:
    https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.html
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.agentcore.identity import get_agentcore_identity
from backend.authorization import TravelerAuthorizationError
from backend.db.rds_data_client import get_rds_data_client
from backend.http_auth import (
    HttpPrincipal,
    authorize_traveler,
    require_http_principal,
)
from backend.memory.store import DEMO_TRAVELER_ID, get_memory_store

router = APIRouter(prefix="/api/memory", tags=["memory"])


class MemoryFactResponse(BaseModel):
    key: str
    value: str
    source: Optional[str] = None
    confidence: Optional[float] = None


class MemoryProfileResponse(BaseModel):
    traveler_id: str
    facts: List[MemoryFactResponse]
    profile: Optional[dict] = None


class MemoryFactUpdate(BaseModel):
    value: str = Field(min_length=1, max_length=1000)


@router.get("/{traveler_id}", response_model=MemoryProfileResponse)
async def get_memory_profile(
    traveler_id: str = DEMO_TRAVELER_ID,
    principal: HttpPrincipal = Depends(require_http_principal),
) -> MemoryProfileResponse:
    traveler_id = authorize_traveler(principal, traveler_id)
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
            facts = await store.recall_preferences(traveler_id, transaction_id=tx)
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
            for f in facts
        ],
        profile=profile,
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
    traveler_id = authorize_traveler(principal, traveler_id)
    preference_key = preference_key.strip()
    value = update.value.strip()
    if not preference_key:
        raise HTTPException(status_code=422, detail="Preference key cannot be empty")
    if len(preference_key) > 100:
        raise HTTPException(status_code=422, detail="Preference is too long")

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
    traveler_id = authorize_traveler(principal, traveler_id)
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
