"""
Bedrock AgentCore Identity adapter for Phase 4.

Identity authenticates the workload that is asking to act for a traveler.
When AgentCore credential exchange succeeds, that workload identity becomes
the subject checked against Aurora ``traveler_identity_bindings``. Otherwise,
the stable AWS IAM principal id from STS is the authorization subject.

Configuration (preferred — @aws/agentcore CLI):

    cd meridian/meridian_agentcore/agentcore
    agentcore add identity --name meridian-workload   # when available in your CLI version
    agentcore deploy -y

Or override manually after deploy:

    AGENTCORE_WORKLOAD_IDENTITY=arn:aws:bedrock-agentcore:...:workload-identity/meridian
    AGENTCORE_RESOURCE_PROVIDER=meridian-aurora-rds-data
    AGENTCORE_WORKLOAD_TOKEN=  # minted by Runtime — not set by hand in production

AWS docs:
  - AgentCore Identity:
    https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/identity.html
  - IAM GetCallerIdentity (fallback principal lookup):
    https://docs.aws.amazon.com/STS/latest/APIReference/API_GetCallerIdentity.html

API references (boto3):
- create_workload_identity:
  https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore-control/client/create_workload_identity.html
- get_resource_oauth2_token:
  https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore/client/get_resource_oauth2_token.html
- get_resource_api_key:
  https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore/client/get_resource_api_key.html
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

from backend.agentcore.cli_config import resolve_agentcore_config
from backend.authorization import AuthorizationContext

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


@dataclass
class IdentityScope:
    """The identity used for one Phase 4 turn."""

    iam_identity: str          # caller principal ARN from sts:GetCallerIdentity
    workload_identity: Optional[str]  # AgentCore workload identity ARN, if any
    resource_provider: Optional[str]  # AgentCore resource credential provider name
    token_status: str          # 'live', 'unconfigured', or short error message
    authorization: AuthorizationContext  # stable AWS subject used for traveler grants


class AgentCoreIdentityAdapter:
    """Resolves the identity envelope for each Phase 4 turn.

    Produces the ``IdentityScope`` used by the Security trace, traveler
    authorization lookup, and per-turn audit row. A live AgentCore workload
    identity is preferred; authenticated IAM is the fail-closed fallback.
    """

    def __init__(
        self,
        workload_identity: Optional[str] = None,
        resource_provider: Optional[str] = None,
        region: Optional[str] = None,
    ) -> None:
        cli = resolve_agentcore_config()
        self.workload_identity = workload_identity or cli.workload_identity
        self.resource_provider = resource_provider or cli.resource_provider
        self.region = region or cli.region
        self.cli_sources = cli.sources
        self._sts = None
        self._runtime = None
        self._iam_identity_cache: Optional[str] = None
        self._iam_subject_cache: Optional[str] = None

    # ----------------------------------------------------------- IAM identity

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
            logger.warning("sts:GetCallerIdentity failed: %s", exc)
            self._iam_identity_cache = "unresolved"
            self._iam_subject_cache = "unresolved"
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

    # ---------------------------------------------------------- per-turn scope

    def scope_for_turn(self) -> IdentityScope:
        """Return the identity envelope to attach to one Phase 4 turn.

        When AGENTCORE_WORKLOAD_IDENTITY + AGENTCORE_RESOURCE_PROVIDER are
        configured we fetch a fresh resource API key per turn (M2M flow);
        otherwise we surface only the IAM principal so the trace and audit
        log still reflect reality.
        """
        iam_authorization = self.authorization_context()
        iam = iam_authorization.principal

        if not (self.workload_identity and self.resource_provider):
            return IdentityScope(
                iam_identity=iam,
                workload_identity=None,
                resource_provider=None,
                token_status="unconfigured",
                authorization=iam_authorization,
            )

        try:
            if self._runtime is None:
                self._runtime = boto3.client("bedrock-agentcore", region_name=self.region)
            # The workload-identity JWT is normally minted by AgentCore Runtime
            # before invoking the agent.  In our FastAPI process we don't have
            # one, so we treat configured-but-no-token as "scoped"; the audit
            # entry still records both identities.
            response = self._runtime.get_resource_api_key(
                workloadIdentityToken=os.getenv("AGENTCORE_WORKLOAD_TOKEN", ""),
                resourceCredentialProviderName=self.resource_provider,
            )
            _ = response.get("apiKey")  # not stored; the call is what matters
            status = "live"
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "Unknown")
            status = f"error:{code}"
            logger.warning("AgentCore Identity get_resource_api_key failed: %s", code)
        except Exception as exc:  # pragma: no cover
            status = f"error:{type(exc).__name__}"
            logger.warning("AgentCore Identity unavailable: %s", exc)

        authorization = (
            AuthorizationContext(
                provider="agentcore_workload",
                subject_id=self.workload_identity,
                principal=iam,
            )
            if status == "live"
            else iam_authorization
        )

        return IdentityScope(
            iam_identity=iam,
            workload_identity=self.workload_identity,
            resource_provider=self.resource_provider,
            token_status=status,
            authorization=authorization,
        )


_adapter: Optional[AgentCoreIdentityAdapter] = None


def get_agentcore_identity() -> AgentCoreIdentityAdapter:
    global _adapter
    if _adapter is None:
        _adapter = AgentCoreIdentityAdapter()
    return _adapter
