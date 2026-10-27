"""
The backend's workload identity for Phase 4 and Phase 5.

The backend acts as its AWS IAM principal: the stable principal id from
``sts:GetCallerIdentity`` is the subject checked against Aurora
``traveler_identity_bindings``. The signed-in person is authenticated separately, by
``backend/http_auth.py`` and, in ``jwt`` mode, by the inbound JWT authorizers of AgentCore
Identity on both Runtimes and the Gateway. Meridian does not use AgentCore Identity's outbound
side (the token vault, OAuth credential providers and on-behalf-of token exchange).

AWS docs:
  - IAM GetCallerIdentity:
    https://docs.aws.amazon.com/STS/latest/APIReference/API_GetCallerIdentity.html
  - AgentCore inbound JWT authorizer:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/inbound-jwt-authorizer.html
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import boto3

from backend.authorization import AuthorizationContext

logger = logging.getLogger(__name__)


@dataclass
class IdentityScope:
    """The identity used for one Phase 4 turn."""

    iam_identity: str  # caller principal ARN from sts:GetCallerIdentity
    authorization: AuthorizationContext  # stable AWS subject used for traveler grants


class AgentCoreIdentityAdapter:
    """Resolves the backend's IAM identity for each Phase 4 turn.

    Produces the ``IdentityScope`` used by the Security trace, the traveler
    authorization lookup and the per-turn audit row.
    """

    def __init__(self) -> None:
        self._sts = None
        self._iam_identity_cache: Optional[str] = None
        self._iam_subject_cache: Optional[str] = None

    def _resolve_iam_caller(self) -> tuple[str, str]:
        """Return the caller ARN and stable IAM principal id."""
        if self._iam_identity_cache is not None and self._iam_subject_cache is not None:
            return self._iam_identity_cache, self._iam_subject_cache
        try:
            if self._sts is None:
                self._sts = boto3.client("sts")
            caller = self._sts.get_caller_identity()
            self._iam_identity_cache = caller.get("Arn", "unknown")
            user_id = caller.get("UserId", "")
            # Assumed-role UserIds are "stable-role-id:ephemeral-session".
            self._iam_subject_cache = user_id.split(":", 1)[0] or "unresolved"
        except Exception as exc:  # pragma: no cover
            # Not cached: one transient failure must not fail every grant check
            # until the process restarts.
            logger.warning("sts:GetCallerIdentity failed: %s", exc)
            return "unresolved", "unresolved"
        return self._iam_identity_cache, self._iam_subject_cache

    def iam_identity(self) -> str:
        """sts:GetCallerIdentity Arn (cached for the process lifetime)."""
        return self._resolve_iam_caller()[0]

    def authorization_context(self) -> AuthorizationContext:
        """Return the authenticated AWS workload subject used for grants."""
        arn, subject_id = self._resolve_iam_caller()
        return AuthorizationContext(
            provider="aws_iam",
            subject_id=subject_id,
            principal=arn,
        )

    def scope_for_turn(self) -> IdentityScope:
        """Return the identity to attach to one Phase 4 turn: the backend's IAM principal."""
        authorization = self.authorization_context()
        return IdentityScope(iam_identity=authorization.principal, authorization=authorization)


_adapter: Optional[AgentCoreIdentityAdapter] = None


def get_agentcore_identity() -> AgentCoreIdentityAdapter:
    global _adapter
    if _adapter is None:
        _adapter = AgentCoreIdentityAdapter()
    return _adapter
