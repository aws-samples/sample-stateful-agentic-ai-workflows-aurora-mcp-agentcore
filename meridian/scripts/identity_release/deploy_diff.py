r"""Read the ``agentcore deploy --diff --json`` plan and judge it for the stage being deployed.

``agentcore deploy --diff --json`` prints the CDK text diff and then a JSON status object. The
installed CLI reads the diff itself with ``/^\[([+~-])\]\s+(\S+)\s+(\S+)/`` on ANSI-stripped
lines: ``[+] AWS::BedrockAgentCore::Gateway <path> <logical id>`` for a resource, and indented
``├─ [~] Property`` lines under it. This module parses that text. The format is read from the
CLI's source, not yet from a live run, so the gate fails closed: a stage that must add a Gateway
and finds no change lines is a finding, with the tail of the output so the real format can be read.

The rules follow from what CloudFormation can do. It cannot change a Gateway's authorizer type, so
a ``[~]`` Gateway entry that touches ``AuthorizerType`` always means the render did not rename the
Gateway. The first stage of a mode switch is the replacement: one new Gateway, and the old one
removed. Every later stage touches no Gateway and deletes none of the resources a stale state file
would delete (the holds Lambda, the targets, the engine and its rules).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from scripts.identity_release import settings, stages

GATEWAY_TYPE = "AWS::BedrockAgentCore::Gateway"
NEVER_DELETED = ("AWS::BedrockAgentCore::Runtime", "AWS::BedrockAgentCore::Memory")
BUILT_BY_STAGES = (
    "AWS::Lambda::Function", "AWS::BedrockAgentCore::GatewayTarget",
    "AWS::BedrockAgentCore::PolicyEngine", "AWS::BedrockAgentCore::Policy",
)
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
RESOURCE = re.compile(r"^\s*\[([+~-])\]\s+(AWS::\S+)\s*(.*)$")
CHILD = re.compile(r"^[\s│├└─|`+-]*\[([+~-])\]\s+(.+?)\s*$")
TAIL_LINES = 6
TAIL_CHARS = 400
REMEDY = (
    "the render did not rename the Gateway; run python scripts/render_agentcore_config.py in the "
    "release mode (the jwt Gateway is a new Gateway with its own name) and never change the "
    "authorizer through CloudFormation"
)


@dataclass(frozen=True)
class Change:
    """One resource line of the diff and the property lines indented under it."""

    kind: str
    type: str
    rest: str
    children: tuple[str, ...] = ()


def parse(output: str) -> list[Change]:
    """The resource changes in ``output``; ANSI colour, tree glyphs and the status line ignored."""
    changes: list[Change] = []
    children: list[str] = []

    def close() -> None:
        if changes:
            last = changes[-1]
            changes[-1] = Change(last.kind, last.type, last.rest, tuple(children))

    for raw in ANSI.sub("", output).splitlines():
        top = RESOURCE.match(raw)
        if top and not raw.startswith((" ", "│")):
            close()
            children = []
            changes.append(Change(top[1], top[2], top[3].strip()))
            continue
        child = CHILD.match(raw)
        if child and changes:
            children.append(child[2])
    close()
    return changes


def construct_id(mode: str) -> str:
    """The CDK construct id of the mode's Gateway, as it appears in the diff's path."""
    parts = settings.gateway_logical_name(mode).split("-")
    return "Gateway" + "".join(part.capitalize() for part in parts)


def names_gateway(change: Change, mode: str) -> bool:
    """Whether a Gateway change is for the mode's Gateway (the iam id is a prefix of the jwt id)."""
    wanted = construct_id(mode)
    longer = [construct_id(other)[len(wanted):] for other in settings.GATEWAY_LOGICAL_NAMES
              if other != mode and construct_id(other).startswith(wanted)]
    guard = f"(?!{'|'.join(map(re.escape, longer))})" if longer else ""
    return re.search(wanted + guard, change.rest) is not None


def _tail(output: str) -> str:
    lines = [line.strip() for line in ANSI.sub("", output).splitlines() if line.strip()]
    return " | ".join(lines[-TAIL_LINES:])[-TAIL_CHARS:]


def _authorizer_findings(gateways: list[Change]) -> list[str]:
    touched = [c for c in gateways if c.kind == "~"
               and any(child.split()[0] == "AuthorizerType" for child in c.children if child)]
    if not touched:
        return []
    return [f"the deploy plan changes a Gateway's authorizer type in place; {REMEDY}"]


def _replacement_findings(gateways: list[Change], mode: str, replacing: bool,
                          output: str) -> list[str]:
    added = [c for c in gateways if c.kind == "+"]
    removed = [c for c in gateways if c.kind == "-"]
    found = []
    mine = [c for c in added if names_gateway(c, mode)]
    if len(added) != 1 or len(mine) != 1:
        found.append(
            f"the first stage must add exactly one Gateway, {construct_id(mode)}, and the plan "
            f"adds {len(added)} ({len(mine)} of them that one); plan output ends: "
            f"{_tail(output)}")
    if replacing and len(removed) != 1:
        found.append(f"the first stage replaces the other mode's Gateway, so the plan must "
                     f"remove exactly one Gateway, and it removes {len(removed)}")
    if not replacing and removed:
        found.append("the plan removes a Gateway, but there is no other Gateway to replace")
    return found


def _later_findings(gateways: list[Change]) -> list[str]:
    if any(c.kind in "+-" for c in gateways):
        return ["this stage must not add or remove a Gateway, but the plan does; the CLI state "
                "or the render is stale, run the render again"]
    return []


def _deletion_findings(changes: list[Change], stage: str) -> list[str]:
    protected = NEVER_DELETED if stage == stages.GATEWAY else (*NEVER_DELETED, *BUILT_BY_STAGES)
    if stage != stages.GATEWAY:
        protected = (*protected, GATEWAY_TYPE)
    return [f"the plan deletes {c.type} ({c.rest}); a stale state file or render would do that, "
            "run the render again" for c in changes if c.kind == "-" and c.type in protected]


def plan_findings(code: int, output: str, argv: Sequence[str], *, stage: str, mode: str,
                  replacing: bool) -> list[str]:
    """Findings for a finished diff run at ``stage``; a diff that failed cannot clear the deploy.

    ``replacing`` says the other mode's Gateway exists live, so the first stage must remove it.
    """
    if code != 0:
        return [f"{' '.join(argv)} exited {code}, so the plan cannot be checked: {_tail(output)}"]
    changes = parse(output)
    gateways = [c for c in changes if c.type == GATEWAY_TYPE]
    found = _authorizer_findings(gateways)
    if stage == stages.GATEWAY:
        found += _replacement_findings(gateways, mode, replacing, output)
    else:
        found += _later_findings(gateways)
    return found + _deletion_findings(changes, stage)
