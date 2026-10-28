r"""Read the ``agentcore deploy --diff --json`` plan and judge it for the stage being deployed.

``agentcore deploy --diff --json`` prints the CDK text diff and then a JSON status object. The
installed CLI reads the diff itself with ``/^\[([+~-])\]\s+(\S+)\s+(\S+)/`` on ANSI-stripped
lines: ``[+] AWS::BedrockAgentCore::Gateway <path> <logical id>`` for a resource, and indented
``├─ [~] Property`` lines under it. This module parses that text. The format was read from the
CLI's source and has since been checked against two live plans (Release 2 Deploy A and Deploy B,
saved masked as ``tests/fixtures/real_plans/``). The gate fails closed in two ways: a plan with
no parsed resource line at all is a finding in every stage (with the masked tail of the output,
so the real format can be read), and every stage must show the change it exists to make.

In the live format a changed field is a ``[~] .Field:`` line (leading dot, trailing colon) and its
old and new values follow one level deeper as ``[-] old`` and ``[+] new`` lines. Those value lines
belong to the field above them; they are not properties.

The rules follow from what CloudFormation can do. It cannot change a Gateway's authorizer type, so
a ``[~]`` Gateway entry that touches ``AuthorizerType`` always means the render did not rename the
Gateway. Each stage must add its own resources:

``gateway``     exactly one Gateway (the mode's), and, when the other mode's Gateway exists,
                exactly one Gateway removed, which must be that Gateway
``targets``     a GatewayTarget and a Lambda function
``governance``  a PolicyEngine and a Policy
``complete``    a Runtime updated, or the catalog release: the MeridianCatalog target added or a
                Cedar rule updated, or the code-only Lambda hotfix. The retired
                SemanticTripSearchLambda target may be removed, but only in a plan that also
                updates the Concierge Runtime

The code-only Lambda hotfix (``CODE_ONLY_RULE``) is the one narrow allowance beyond that table. A
complete-stage plan passes it when every change line is a ``[~]`` ``AWS::Lambda::Function`` and
each function's property lines are only ``Code`` (with its ``S3Key`` and ``S3Bucket``) and
``Metadata``. A function with any other property line (``Environment``, ``Role``, ``Handler``,
``Timeout`` and so on), or with no property lines to read, is not code-only. Nothing else may ride
along: no ``[+]`` or ``[-]``, no Runtime, Gateway, Policy, engine, role, IAM policy, Lambda
permission, log group or other resource, and no GatewayTarget: the construct builds a target from
the function ARN and the inline tool definitions, so a target change is a schema or description
change, never a code change. Every other stage is unchanged.

The first stage may remove only what belongs to the replaced Gateway (every resource whose path
or logical id carries its construct id) and the Cedar engine with its rules, which the first stage
render leaves out. Every other removal is a finding, and the later stages refuse any removal
except the retired target at the ``complete`` stage.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from backend.agentcore.auth_mode import IAM, JWT
from scripts.identity_release import settings, stages

GATEWAY_TYPE = "AWS::BedrockAgentCore::Gateway"
TARGET_TYPE = "AWS::BedrockAgentCore::GatewayTarget"
FUNCTION_TYPE = "AWS::Lambda::Function"
ENGINE_TYPE = "AWS::BedrockAgentCore::PolicyEngine"
POLICY_TYPE = "AWS::BedrockAgentCore::Policy"
RUNTIME_TYPE = "AWS::BedrockAgentCore::Runtime"
ENGINE_TYPES = (ENGINE_TYPE, POLICY_TYPE)
EXPECTED = {
    stages.TARGETS: ("+", (TARGET_TYPE, FUNCTION_TYPE)),
    stages.GOVERNANCE: ("+", (ENGINE_TYPE, POLICY_TYPE)),
    stages.COMPLETE: ("~", (RUNTIME_TYPE,)),
}
CATALOG_TARGET = "MeridianCatalog"
CONCIERGE_RUNTIME = "AgentMeridianConcierge"
CODE_ONLY_RULE = "code-only Lambda hotfix"
# A target a release replaced. Its removal is the one removal a complete-stage plan may carry.
RETIRED_TARGETS = ("SemanticTripSearchLambda",)
CODE_PROPERTIES = ("Code", "Metadata")
CODE_FIELDS = ("S3Key", "S3Bucket")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
RESOURCE = re.compile(r"^\s*\[([+~-])\]\s+(AWS::\S+)\s*(.*)$")
CHILD = re.compile(r"^[\s│├└─|`+-]*\[([+~-])\]\s+(.+?)\s*$")
TAIL_LINES = 6
TAIL_CHARS = 400
MIN_KNOWN_LENGTH = 6
POOL_ID = re.compile(r"\b[a-z]{2}(?:-[a-z]+)+-\d+_[A-Za-z0-9]+\b")
HOSTED_HOST = re.compile(r"[A-Za-z0-9.-]+\.auth(?:-fips)?\.[a-z0-9-]+\.amazoncognito\.com")
DISCOVERY_URL = re.compile(r"https://cognito-idp\.[a-z0-9-]+\.amazonaws\.com\S*")
CLIENT_VALUES = re.compile(r"(?i)(allowed_?clients?|client_?ids?)([\s:=\"'\[]+)[^\n]*")
ACCOUNT_ID = re.compile(r"(?<!\d)\d{12}(?!\d)")
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
    tree: tuple[tuple[int, str, str], ...] = field(default=(), compare=False)
    """The same lines as ``children`` with their column and kind: ``(column, kind, text)``."""


def parse(output: str) -> list[Change]:
    """The resource changes in ``output``; ANSI colour, tree glyphs and the status line ignored."""
    changes: list[Change] = []
    children: list[str] = []
    tree: list[tuple[int, str, str]] = []

    def close() -> None:
        if changes:
            last = changes[-1]
            changes[-1] = Change(last.kind, last.type, last.rest, tuple(children), tuple(tree))

    for raw in ANSI.sub("", output).splitlines():
        top = RESOURCE.match(raw)
        if top and not raw.startswith((" ", "│")):
            close()
            children, tree = [], []
            changes.append(Change(top[1], top[2], top[3].strip()))
            continue
        child = CHILD.match(raw)
        if child and changes:
            children.append(child[2])
            tree.append((child.start(1) - 1, child[1], child[2]))
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


def mask_identifiers(text: str, known: Sequence[str] = ()) -> str:
    """Hide what a CLI's output must not repeat: pool and client ids, the hosted-UI host, the
    discovery URL and account ids. Matched by shape, so a value nobody listed is hidden too;
    ``known`` values (the settings in use) are hidden wherever they appear."""
    for value in known:
        if len(value) >= MIN_KNOWN_LENGTH:
            text = text.replace(value, "<masked>")
    text = DISCOVERY_URL.sub("<discovery-url>", text)
    text = HOSTED_HOST.sub("<hosted-host>", text)
    text = POOL_ID.sub("<pool>", text)
    text = CLIENT_VALUES.sub(lambda found: f"{found[1]}{found[2]}<client-ids>", text)
    return ACCOUNT_ID.sub("<acct>", text)


def tail(output: str, known: Sequence[str] = ()) -> str:
    """The last few non-empty lines of ``output`` on one line, with identifiers masked."""
    lines = [mask_identifiers(line.strip(), known)
             for line in ANSI.sub("", output).splitlines() if line.strip()]
    return " | ".join(lines[-TAIL_LINES:])[-TAIL_CHARS:]


def _other(mode: str) -> str:
    return IAM if mode == JWT else JWT


def _authorizer_findings(gateways: list[Change]) -> list[str]:
    touched = [c for c in gateways if c.kind == "~"
               and any(child.split()[0] == "AuthorizerType" for child in c.children if child)]
    if not touched:
        return []
    return [f"the deploy plan changes a Gateway's authorizer type in place; {REMEDY}"]


def _replacement_findings(gateways: list[Change], mode: str, replacing: bool,
                          shown: str) -> list[str]:
    added = [c for c in gateways if c.kind == "+"]
    removed = [c for c in gateways if c.kind == "-"]
    found = []
    mine = [c for c in added if names_gateway(c, mode)]
    if len(added) != 1 or len(mine) != 1:
        found.append(
            f"the first stage must add exactly one Gateway, {construct_id(mode)}, and the plan "
            f"adds {len(added)} ({len(mine)} of them that one); plan output ends: {shown}")
    replaced = _other(mode)
    if replacing and not (len(removed) == 1 and names_gateway(removed[0], replaced)):
        found.append(f"the first stage replaces the other mode's Gateway, so the plan must "
                     f"remove exactly one Gateway, {construct_id(replaced)}, and it removes "
                     f"{len(removed)} ({sum(names_gateway(c, replaced) for c in removed)} of "
                     "them that one)")
    return found


def _later_findings(gateways: list[Change]) -> list[str]:
    if any(c.kind == "+" for c in gateways):
        return ["this stage must not add a Gateway, but the plan does; the CLI state or the "
                "render is stale, run the render again"]
    return []


def _names_target(change: Change, name: str) -> bool:
    """Whether a GatewayTarget change is for exactly the target ``name`` (no longer name)."""
    return change.type == TARGET_TYPE and re.search(
        rf"/Target{re.escape(name)}(?![A-Za-z0-9])", change.rest) is not None


def _concierge_updated(changes: list[Change]) -> bool:
    """Whether the plan updates the Concierge Runtime, the one that calls the retired target."""
    return any(
        c.kind == "~" and c.type == RUNTIME_TYPE
        and re.search(rf"/{CONCIERGE_RUNTIME}/Runtime(?:\s|$)", c.rest) is not None
        for c in changes
    )


def _is_catalog_release(changes: list[Change]) -> bool:
    """A complete-stage plan that adds the catalog target or updates a Cedar rule."""
    return any(
        (c.kind == "+" and _names_target(c, CATALOG_TARGET))
        or (c.kind == "~" and c.type == POLICY_TYPE)
        for c in changes
    )


def _property_name(text: str) -> str:
    """The field a child line names: ``.S3Key:`` is ``S3Key``, ``Removed: .X`` is ``X``."""
    words = text.split()
    if words and words[0] in ("Added:", "Removed:"):
        words = words[1:]
    return (words[0] if words else text).lstrip(".").rstrip(":")


def _is_value(kind: str, text: str, parent: str | None) -> bool:
    """Whether a ``[-]``/``[+]`` line is the old or new value of the field above it."""
    marker = text.split()[0] in ("Added:", "Removed:") if text.split() else False
    return kind in "+-" and parent is not None and parent.rstrip().endswith(":") and not marker


def _function_strays(change: Change) -> list[str]:
    """The property lines of an updated function that are not a code change."""
    strays: list[str] = []
    open_fields: list[tuple[int, str, str]] = []
    top_level = False
    for column, kind, text in change.tree:
        while open_fields and open_fields[-1][0] >= column:
            open_fields.pop()
        if _is_value(kind, text, open_fields[-1][2] if open_fields else None):
            continue
        name = _property_name(text)
        path = [item[1] for item in open_fields] + [name]
        top_level = top_level or len(path) == 1
        if path[0] not in CODE_PROPERTIES or (
                path[0] == "Code" and len(path) > 1
                and (len(path) > 2 or name not in CODE_FIELDS)):
            strays.append(".".join(path))
        open_fields.append((column, name, text))
    return strays if top_level else ["no property lines shown, so the change cannot be read"]


def _code_only_refusal(changes: list[Change]) -> str | None:
    """Why the code-only Lambda hotfix does not cover ``changes``; None when it does."""
    stray = []
    for c in changes:
        if c.kind == "~" and c.type == FUNCTION_TYPE:
            stray += [f"[~] {c.type} {c.rest} changes {name}" for name in _function_strays(c)]
        elif c.type == TARGET_TYPE:
            stray.append(f"[{c.kind}] {c.type} {c.rest}: a target change carries a tool schema "
                         "or description change, not only code")
        else:
            stray.append(f"[{c.kind}] {c.type} {c.rest}")
    return "; ".join(stray) or None


def _updates_function(changes: list[Change]) -> bool:
    return any(c.kind == "~" and c.type == FUNCTION_TYPE for c in changes)


def _expected_findings(changes: list[Change], stage: str) -> list[str]:
    kind, wanted = EXPECTED[stage]
    present = {c.type for c in changes if c.kind == kind}
    missing = [resource for resource in wanted if resource not in present]
    if not missing or (stage == stages.COMPLETE and _is_catalog_release(changes)):
        return []
    complete_stage = stage == stages.COMPLETE
    refusal = _code_only_refusal(changes) if complete_stage else None
    if complete_stage and _updates_function(changes) and refusal is None:
        return []
    verb = {"+": "add", "~": "update"}[kind]
    alternatives = (f", or to add the {CATALOG_TARGET} target, or to update a Cedar rule "
                    f"({POLICY_TYPE}), or to be a {CODE_ONLY_RULE} (only [~] "
                    f"{FUNCTION_TYPE} changes whose properties are Code and Metadata)"
                    if complete_stage else "")
    reason = ""
    if complete_stage and refusal and (_updates_function(changes) or any(
            c.kind == "~" and c.type == TARGET_TYPE for c in changes)):
        reason = (f"; the {CODE_ONLY_RULE} rule did not apply because the plan also has: "
                  f"{refusal}")
    return [f"the {stage} stage expects the plan to {verb} {' and '.join(wanted)}"
            f"{alternatives}, and it does not {verb} {' or '.join(missing)}{reason}; the render "
            "or the deployed state is the wrong stage, run the render again"]


def _belongs_to_replaced(change: Change, replaced: str) -> bool:
    """Whether a removal is the replaced Gateway's own construct, or the Cedar engine."""
    if change.type in ENGINE_TYPES and stages.ENGINE_NAME in change.rest:
        return True
    return names_gateway(change, replaced)


def _retired_target(change: Change) -> bool:
    return any(_names_target(change, name) for name in RETIRED_TARGETS)


def _removal_findings(changes: list[Change], stage: str, mode: str,
                      replacing: bool) -> list[str]:
    replaced = _other(mode)
    allowed = stage == stages.GATEWAY and replacing
    concierge_updated = _concierge_updated(changes)
    found = []
    for c in changes:
        if c.kind != "-":
            continue
        if stage == stages.COMPLETE and _retired_target(c):
            if not concierge_updated:
                found.append(
                    f"the plan removes the retired target ({c.rest}) without updating the "
                    "Concierge Runtime; the deployed Concierge Runtime still calls it, so the "
                    f"same plan must update it ({RUNTIME_TYPE} {CONCIERGE_RUNTIME}): deploy the "
                    "new Concierge Runtime with it")
            continue
        if allowed and _belongs_to_replaced(c, replaced):
            continue
        found.append(
            f"the plan deletes {c.type} ({c.rest}); only the replaced {replaced} Gateway's own "
            "constructs and the Cedar engine may go in the first stage, a target a release "
            "retired may go at the complete stage, and nothing else may go, so a stale state "
            "file or render would do this: run the render again")
    return found


def plan_findings(code: int, output: str, argv: Sequence[str], *, stage: str, mode: str,
                  replacing: bool, known: Sequence[str] = ()) -> list[str]:
    """Findings for a finished diff run at ``stage``; a diff that failed cannot clear the deploy.

    ``replacing`` says the other mode's Gateway exists live, so the first stage must remove it.
    A plan with no parsed resource line is a finding in every stage. ``known`` are setting
    values (pool and client ids) to hide from the output shown in a finding.
    """
    shown = tail(output, known)
    if code != 0:
        return [f"{' '.join(argv)} exited {code}, so the plan cannot be checked: {shown}"]
    changes = parse(output)
    if not changes:
        return [f"the {stage} stage cannot be judged: the plan output has no resource change "
                f"lines (the format may differ from the one read from the CLI); read the plan "
                f"by eye. Output ends: {shown}"]
    gateways = [c for c in changes if c.type == GATEWAY_TYPE]
    found = _authorizer_findings(gateways)
    if stage == stages.GATEWAY:
        found += _replacement_findings(gateways, mode, replacing, shown)
    else:
        found += _later_findings(gateways) + _expected_findings(changes, stage)
    return found + _removal_findings(changes, stage, mode, replacing)
