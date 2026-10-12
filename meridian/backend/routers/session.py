"""Who the API says you are.

``GET /api/me`` returns the traveler the verified credential is bound to. The browser reads it
to show the signed-in traveler; the answer comes from the principal ``require_http_principal``
built, never from anything the caller sent.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.http_auth import HttpPrincipal, require_http_principal

router = APIRouter(prefix="/api", tags=["session"])


class SessionResponse(BaseModel):
    """The authenticated caller, as the API sees it."""

    traveler_id: str
    authentication: str


@router.get("/me", response_model=SessionResponse)
async def read_session(
    principal: HttpPrincipal = Depends(require_http_principal),
) -> SessionResponse:
    """Return the traveler this request is authenticated as."""
    return SessionResponse(
        traveler_id=principal.traveler_id, authentication=principal.authentication
    )
