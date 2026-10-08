"""Read whether each Runtime's role may call the Gateway with AWS credentials.

In ``iam`` mode a Runtime signs its calls to the Gateway, so its role needs
``bedrock-agentcore:InvokeGateway`` on that Gateway. The ``jwt`` render removes the statement from
both Runtime roles when ``agentcore deploy -y`` runs it, and the ``UpdateAgentRuntime`` rollback
cannot put it back: only an IAM render deployed with ``agentcore deploy -y`` does. A finding here
is how ``check``, the snapshot and the rollback notice the difference.

A grant counts by the same rules as ``lambda_release``: an Allow statement whose action matches
(wildcards included) and whose resource matches the Gateway's ARN, in the role's inline or
attached policies. Conditions and Deny statements are not evaluated, so the answer errs toward
"can still call".
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from scripts.identity_release import lambda_release

ACTION = "bedrock-agentcore:invokegateway"
FIX = ("only an IAM render deployed with `agentcore deploy -y` restores it (the jwt deploy removes "
       "the statement)")


def gateway_arn(account: str, region: str, gateway_id: str) -> str:
    """The Gateway's ARN, built the way the control plane reports it."""
    return f"arn:aws:bedrock-agentcore:{region}:{account}:gateway/{gateway_id}"


def findings(iam: Any, runtimes: Mapping[str, Any], gateway: str) -> list[str]:
    """One line per Runtime whose role cannot call ``bedrock-agentcore:InvokeGateway``.

    Only reads (``iam:ListRolePolicies``, ``GetRolePolicy``, ``ListAttachedRolePolicies``,
    ``GetPolicy``, ``GetPolicyVersion``).
    """
    found = []
    for name in sorted(runtimes):
        subject = f"Runtime {name}"
        role = str((runtimes[name] or {}).get("roleArn") or "")
        if "/" not in role:
            found.append(f"{subject}: has no role to read, so InvokeGateway cannot be checked")
            continue
        grants = lambda_release.secret_grants(iam, role.rsplit("/", 1)[-1], ACTION)
        if not any(grant.covers(gateway) for grant in grants):
            found.append(f"{subject}: its role cannot call bedrock-agentcore:InvokeGateway on "
                         f"the Gateway, so it cannot reach its tools with AWS credentials; {FIX}")
    return found
