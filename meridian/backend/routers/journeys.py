"""Journey read API — the evidence behind Presenter proof.

`GET /api/journeys/{journey_id}` assembles a typed document from the tables
that already hold the facts. It has no workflow side effects.

Authorization runs on every read: the HTTP principal is resolved, the traveler
claim is authorized against it, and the journey's persisted owner is checked
inside the scoped session. A journey id grants nothing on its own.
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from backend.agentcore.identity import get_agentcore_identity
from backend.db.journey_document import assemble_journey_document
from backend.db.rds_data_client import get_rds_data_client
from backend.http_auth import HttpPrincipal, authorize_traveler, require_http_principal

router = APIRouter(prefix="/api/journeys", tags=["journeys"])

LATEST_JOURNEYS_SQL = """
SELECT j.journey_id, j.status, j.checkpoint_backend, j.active_thread_id,
       j.created_at, j.updated_at,
       (SELECT count(*) FROM journey_executions e WHERE e.journey_id = j.journey_id)
           AS execution_count
  FROM journeys j
 WHERE j.traveler_id = %s
 ORDER BY j.created_at DESC
 LIMIT %s
"""


@router.get("")
async def list_journeys(
    principal: HttpPrincipal = Depends(require_http_principal),
    traveler_id: Optional[str] = Query(default=None, max_length=50),
    limit: int = Query(default=10, ge=1, le=50),
) -> Dict[str, Any]:
    """List the caller's journeys, newest first.

    The shell needs a journey id before it can read a document, and a demo
    machine should not have to be told one by hand.
    """
    owner = authorize_traveler(principal, traveler_id)
    client = get_rds_data_client()
    try:
        async with client.scoped_session(
            traveler_id=owner,
            agent_type="booking_agent",
            authorization=get_agentcore_identity().authorization_context(),
        ) as tx:
            rows = await client.execute(
                LATEST_JOURNEYS_SQL, (owner, limit), transaction_id=tx
            )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return {
        "traveler_id": owner,
        "journeys": [
            {
                "journey_id": r["journey_id"],
                "status": r["status"],
                "checkpoint_backend": r["checkpoint_backend"],
                "active_thread_id": r["active_thread_id"],
                "execution_count": int(r["execution_count"]),
                "created_at": str(r["created_at"]),
                "updated_at": str(r["updated_at"]),
            }
            for r in rows
        ],
    }


@router.get("/{journey_id}")
async def read_journey(
    journey_id: str,
    principal: HttpPrincipal = Depends(require_http_principal),
    traveler_id: Optional[str] = Query(default=None, max_length=50),
) -> Dict[str, Any]:
    """Assemble the evidence document for one journey.

    Raises:
        HTTPException: 404 when no such journey is visible to this traveler,
            403 when the journey is owned by someone else.
    """
    owner = authorize_traveler(principal, traveler_id)
    try:
        return await assemble_journey_document(
            get_rds_data_client(), journey_id, owner
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except PermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
