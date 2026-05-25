"""
Bedrock AgentCore Identity adapter for Phase 4.

Identity is what lets us tell the audit trail "this turn ran as workload
identity X under IAM principal Y."  In a fully provisioned setup the
adapter exchanges the workload identity token for a scoped resource
credential before each turn; in workshop mode it falls back to
`sts:GetCallerIdentity` so we still surface the IAM principal honestly.

Configuration (.env):

    AGENTCORE_WORKLOAD_IDENTITY=arn:aws:bedrock-agentcore:...:workload-identity/meridian
    AGENTCORE_RESOURCE_PROVIDER=meridian-aurora-rds-data
    AGENTCORE_REGION=us-east-1

API references:
- create_workload_identity:    https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore-control/client/create_workload_identity.html
- get_resource_oauth2_token:   https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore/client/get_resource_oauth2_token.html
- get_resource_api_key:        https://docs.aws.amazon.com/boto3/latest/reference/services/bedrock-agentcore/client/get_resource_api_key.html
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

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


class AgentCoreIdentityAdapter:
    def __init__(
        self,
        workload_identity: Optional[str] = None,
        resource_provider: Optional[str] = None,
        region: Optional[str] = None,
    ) -> None:
        self.workload_identity = workload_identity or os.getenv("AGENTCORE_WORKLOAD_IDENTITY")
        self.resource_provider = resource_provider or os.getenv("AGENTCORE_RESOURCE_PROVIDER")
        self.region = region or os.getenv(
            "AGENTCORE_REGION", os.getenv("AWS_DEFAULT_REGION", "us-east-1")
        )
        self._sts = None
        self._runtime = None
        self._iam_identity_cache: Optional[str] = None

    # ----------------------------------------------------------- IAM identity

    def iam_identity(self) -> str:
        """sts:GetCallerIdentity Arn (cached for the process lifetime)."""
        if self._iam_identity_cache is not None:
            return self._iam_identity_cache
        try:
            if self._sts is None:
                self._sts = boto3.client("sts")
            self._iam_identity_cache = self._sts.get_caller_identity().get("Arn", "unknown")
        except Exception as exc:  # pragma: no cover
            logger.warning("sts:GetCallerIdentity failed: %s", exc)
            self._iam_identity_cache = "unresolved"
        return self._iam_identity_cache

    # ---------------------------------------------------------- per-turn scope

    def scope_for_turn(self) -> IdentityScope:
        """Return the identity envelope to attach to one Phase 4 turn.

        When AGENTCORE_WORKLOAD_IDENTITY + AGENTCORE_RESOURCE_PROVIDER are
        configured we fetch a fresh resource API key per turn (M2M flow);
        otherwise we surface only the IAM principal so the trace and audit
        log still reflect reality.
        """
        iam = self.iam_identity()

        if not (self.workload_identity and self.resource_provider):
            return IdentityScope(
                iam_identity=iam,
                workload_identity=None,
                resource_provider=None,
                token_status="unconfigured",
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

        return IdentityScope(
            iam_identity=iam,
            workload_identity=self.workload_identity,
            resource_provider=self.resource_provider,
            token_status=status,
        )


_adapter: Optional[AgentCoreIdentityAdapter] = None


def get_agentcore_identity() -> AgentCoreIdentityAdapter:
    global _adapter
    if _adapter is None:
        _adapter = AgentCoreIdentityAdapter()
    return _adapter
