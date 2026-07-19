"""Shared authorization types for traveler-scoped data access."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class AuthorizationContext:
    """Authenticated workload subject requesting access to a traveler."""

    provider: str
    subject_id: str
    principal: str


@dataclass(frozen=True)
class AuthorizationDecision:
    """Result of the application authorization lookup in Aurora."""

    allowed: bool
    decision: str
    traveler_id: str
    provider: str
    subject_id: str
    principal: str
    binding_id: Optional[str] = None
    audit_id: Optional[str] = None
    reason: Optional[str] = None


class TravelerAuthorizationError(PermissionError):
    """Raised before RLS scope is set when a traveler claim is not authorized."""

    def __init__(self, decision: AuthorizationDecision):
        self.decision = decision
        super().__init__(
            f"{decision.provider} subject is not authorized for traveler "
            f"{decision.traveler_id}"
        )
