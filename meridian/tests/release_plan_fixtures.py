"""Deploy plans shaped like the stack the CDK app synthesizes, for the plan gate's tests.

The resource types, construct paths and logical ids below are the ones in the template the
AgentCore CDK app synthesizes for the ``iam`` Gateway (``Mcp/GatewayMeridianAurora`` with the
semantic target, the holds Lambda and its role, and the ``Application`` constructs for the Cedar
engine, its three rules and the Runtimes). The ``jwt`` ids are the same shape with the ``Jwt``
construct id; their hashes are invented. No live ``agentcore deploy --diff`` has been seen: the
line layout (``[+] type path logical-id`` and indented ``[~]`` property lines) is the CDK text
diff the CLI is documented to print, so these fixtures do not prove the real format.
"""

from __future__ import annotations

import re

STATUS = '{"success":true,"targetName":"default","stackName":"AgentCore-meridianv2-default"}'
HEADER = "Stack AgentCore-meridianv2-default\nResources"
GATEWAY = "AWS::BedrockAgentCore::Gateway"
IAM_GATEWAY = ("Mcp/GatewayMeridianAurora", "McpGatewayMeridianAurora")
JWT_GATEWAY = ("Mcp/GatewayMeridianAuroraJwt", "McpGatewayMeridianAuroraJwt")
ENGINE = ("Application/PolicyEngineMeridianGovernance", "ApplicationPolicyEngineMeridianGovernance")
RULES = ("meridian_read_tools", "meridian_hold_governance", "meridian_booking_governance")

SEMANTIC_PARTS = [("AWS::BedrockAgentCore::GatewayTarget", "TargetSemanticTripSearchLambda")]
HOLDS_PARTS = [
    ("AWS::IAM::Role", "LambdaMeridianHolds/Role"),
    ("AWS::IAM::Policy", "LambdaMeridianHolds/Policy"),
    ("AWS::Lambda::Function", "LambdaMeridianHolds/Function"),
    ("AWS::Logs::LogGroup", "LambdaMeridianHolds/Function/LogGroup"),
    ("AWS::BedrockAgentCore::GatewayTarget", "TargetMeridianHolds"),
]
GATEWAY_PARTS = [
    ("AWS::IAM::Role", "Role"),
    ("AWS::IAM::Policy", "Role/DefaultPolicy"),
    (GATEWAY, ""),
]

CONCIERGE = (
    "[~] AWS::BedrockAgentCore::Runtime Application/AgentMeridianConcierge/Runtime "
    "ApplicationAgentMeridianConciergeRuntimeC59BB6EB\n"
    " ├─ [~] AuthorizerConfiguration\n"
    " │   └─ [+] Added: .CustomJWTAuthorizer\n"
    " └─ [~] EnvironmentVariables\n"
    "     └─ [~] .MERIDIAN_AGENTCORE_AUTH\n")


def line(kind: str, resource_type: str, construct: tuple[str, str], part: str = "") -> str:
    """One resource line: ``[kind] type path logical-id`` with a suffix hash."""
    path, logical = construct
    joined = f"{path}/{part}" if part else path
    suffix = re.sub(r"[^A-Za-z0-9]", "", part)
    return f"[{kind}] {resource_type} {joined} {logical}{suffix}9F3A1C2B"


def construct_lines(kind: str, construct: tuple[str, str], *parts: tuple[str, str]) -> list[str]:
    """The lines of a construct's resources, in synthesis order."""
    return [line(kind, resource_type, construct, part) for resource_type, part in parts]


def engine_lines(kind: str) -> list[str]:
    """The Cedar engine and its three rules."""
    path, logical = ENGINE
    lines = [line(kind, "AWS::BedrockAgentCore::PolicyEngine", ENGINE)]
    lines += [line(kind, "AWS::BedrockAgentCore::Policy", ENGINE, f"Policy{rule}")
              for rule in RULES]
    return lines


def plan(*blocks: str) -> str:
    """The CLI's output: the stack header, the changes, then the JSON status object."""
    return HEADER + "\n" + "\n".join(blocks) + "\n" + STATUS


def first_stage(new: tuple[str, str] = JWT_GATEWAY, old: tuple[str, str] | None = IAM_GATEWAY,
                ) -> str:
    """The plan of the first stage: the new Gateway, and the old one's constructs removed."""
    blocks = construct_lines("+", new, *GATEWAY_PARTS, *SEMANTIC_PARTS) + [CONCIERGE]
    if old is not None:
        blocks += construct_lines("-", old, *GATEWAY_PARTS, *SEMANTIC_PARTS, *HOLDS_PARTS)
        blocks += engine_lines("-")
    return plan(*blocks)


def targets_stage(gateway: tuple[str, str] = JWT_GATEWAY) -> str:
    """The plan of the second stage: the holds Lambda, its role and the holds target."""
    return plan(*construct_lines("+", gateway, *HOLDS_PARTS), CONCIERGE)


def governance_stage(gateway: tuple[str, str] = JWT_GATEWAY) -> str:
    """The plan of the third stage: the engine, the rules and the Gateway's association."""
    update = (f"[~] {GATEWAY} {gateway[0]} {gateway[1]}9F3A1C2B\n"
              " └─ [~] PolicyEngineConfiguration")
    return plan(*engine_lines("+"), update)


def complete_stage() -> str:
    """The plan of the last stage: both Runtimes learn the engine id."""
    return plan(CONCIERGE)
