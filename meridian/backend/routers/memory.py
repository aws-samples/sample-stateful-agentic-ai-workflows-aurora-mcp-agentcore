"""Memory API — long-term preferences from Aurora."""

from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

from backend.db.rds_data_client import get_rds_data_client
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


@router.get("/{traveler_id}", response_model=MemoryProfileResponse)
async def get_memory_profile(traveler_id: str = DEMO_TRAVELER_ID) -> MemoryProfileResponse:
    store = get_memory_store()
    db = get_rds_data_client()
    # Pin RLS for the read so the API endpoint exercises the same isolation
    # path the concierge agent uses.
    async with db.scoped_session(traveler_id=traveler_id, agent_type="memory_agent") as tx:
        facts = await store.recall_preferences(traveler_id, transaction_id=tx)
        profile = await store.recall_profile(traveler_id, transaction_id=tx)
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
