"""The stages in which a replaced Gateway is built, read from the AgentCore CLI's deployed state.

CloudFormation cannot change an existing Gateway's authorizer type, so the ``jwt`` release creates
a new Gateway under a new name and the stack deletes the ``iam`` one. The stack cannot hold both:
the Lambda behind the ``MeridianHolds`` target has a fixed physical name, and every target
adds outputs under one construct id, so two Gateways with the same target names fail to synthesize.
The new Gateway is therefore built in the order the first deployment used:

``gateway``     the Gateway and the targets that own no fixed name; no holds target, no Cedar engine
``targets``     the holds target (its Lambda is created now that the old one is gone)
``governance``  the Cedar engine and rules, which name the Gateway and its tools
``complete``    the Runtimes learn the engine's id; nothing is left out

Each stage is the first one whose resource the deployed state does not list. A state file that
is missing or stale reads as an earlier stage, which would delete resources, so ``deploy``
compares the stage with the live Gateway before it runs anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from scripts.identity_release import settings

GATEWAY, TARGETS, GOVERNANCE, COMPLETE = "gateway", "targets", "governance", "complete"
STAGES = (GATEWAY, TARGETS, GOVERNANCE, COMPLETE)
ENGINE_NAME = "MeridianGovernance"


@dataclass(frozen=True)
class DeployedIds:
    """What the CLI's state lists for one Gateway and the policy engine."""

    gateway_id: str | None
    holds_target_id: str | None
    policy_engine_id: str | None


def gateway_keys(gateway: str) -> tuple[str, ...]:
    """Keys from a target's resources to the Gateway's entry."""
    return ("mcp", "gateways", gateway)


def holds_keys(gateway: str) -> tuple[str, ...]:
    """Keys from a target's resources to the holds target's id."""
    return (*gateway_keys(gateway), "targets", settings.HOLDS_TARGET, "targetId")


ENGINE_KEYS = ("policyEngines", ENGINE_NAME, "policyEngineId")


def stage_for(ids: DeployedIds) -> str:
    """The stage the next deploy is: the first resource the state does not list."""
    if not ids.gateway_id:
        return GATEWAY
    if not ids.holds_target_id:
        return TARGETS
    if not ids.policy_engine_id:
        return GOVERNANCE
    return COMPLETE


def next_stage(stage: str) -> str | None:
    """The stage after ``stage``, or ``None`` once the configuration is complete."""
    position = STAGES.index(stage) + 1
    return STAGES[position] if position < len(STAGES) else None


def renders_holds(stage: str) -> bool:
    """Whether the holds target (and its Lambda) are part of this stage's render."""
    return stage != GATEWAY


def renders_gateway_id(stage: str) -> bool:
    """Whether the Gateway's id is known, so the Runtimes get it and the rules can name it."""
    return stage != GATEWAY


def renders_engine(stage: str) -> bool:
    """Whether the Cedar engine, its rules and the Gateway's association are rendered."""
    return stage in (GOVERNANCE, COMPLETE)


def renders_engine_id(stage: str) -> bool:
    """Whether the engine's id is known, so the Runtimes get it."""
    return stage == COMPLETE


def _node(node: Any, keys: tuple[str, ...]) -> Any:
    """The value at ``keys`` below ``node``, or ``None`` when a level is missing or odd."""
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node


def _text(node: Any, keys: tuple[str, ...]) -> str | None:
    found = _node(node, keys)
    return found if isinstance(found, str) and found else None


def deployed_ids(state: Any, target: str, gateway: str) -> DeployedIds:
    """The ids the CLI's state lists for ``gateway`` in ``target``; absent ones are ``None``."""
    resources = _node(state, ("targets", target, "resources"))
    return DeployedIds(
        _text(resources, (*gateway_keys(gateway), "gatewayId")),
        _text(resources, holds_keys(gateway)),
        _text(resources, ENGINE_KEYS))
