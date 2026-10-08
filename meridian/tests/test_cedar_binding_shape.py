"""The traveler binding is a guarded ``unless`` rule that cannot error and so cannot fail open.

The first live run of the Gateway harness showed that the AgentCore Policy validator rejects
``!(principal.hasTag("traveler_id")) || ... || principal.getTag(...)`` because its flow typing does
not carry a negated guard through ``||``. The shape asserted here, a positive ``hasTag`` guard at
the start of a ``&&`` chain, is the idiom the AgentCore documentation shows for tag access.
"""

from __future__ import annotations

import json
import re

import cedarpy
import pytest

from scripts import render_agentcore_config as render_config
from tests.test_cedar_traveler_binding import BOOKING, GATEWAY, HOLD, policies

TEMPLATE = render_config.CONFIG_DIR / render_config.SPEC_TEMPLATE
TAG_ACCESS = re.compile(r'principal\.getTag\("([^"]+)"\)')
PERMIT_ALL = "permit(principal, action, resource);"
ALLOW, DENY = cedarpy.Decision.Allow, cedarpy.Decision.Deny


def template_statement() -> str:
    spec = json.loads(TEMPLATE.read_text())
    return next(
        p["statement"] for p in spec["policyEngines"][0]["policies"]
        if p["name"] == "meridian_traveler_binding"
    )


def condition_clauses(statement: str) -> list[str]:
    body = re.search(r"\b(?:unless|when)\s*\{(.*)\}\s*;\s*$", statement, re.DOTALL)
    assert body, "the rule has no unless or when clause"
    return [clause.strip() for clause in body.group(1).split("&&")]


def test_the_template_rule_has_no_negated_tag_guard() -> None:
    statement = template_statement()
    assert "!(principal.hasTag" not in statement
    assert not re.search(r"!\s*\(?\s*principal\s*\.\s*hasTag", statement)
    assert "||" not in statement


def test_every_get_tag_follows_its_has_tag_guard_in_one_conjunction() -> None:
    clauses = condition_clauses(template_statement())
    accessed = [(i, m.group(1)) for i, c in enumerate(clauses) for m in TAG_ACCESS.finditer(c)]
    assert accessed, "the rule never reads the tag"
    for index, tag in accessed:
        assert f'principal.hasTag("{tag}")' in clauses[:index], (tag, clauses)


def test_the_argument_is_read_only_after_its_has_guard() -> None:
    clauses = condition_clauses(template_statement())
    first_read = next(i for i, c in enumerate(clauses) if "context.input.travelerId" in c)
    assert "context.input has travelerId" in clauses[:first_read]


def test_the_rule_is_an_unless_over_the_same_scope_as_before() -> None:
    statement = template_statement()
    assert statement.startswith("forbid(principal is AgentCore::OAuthUser, action in [")
    assert f'AgentCore::Action::"{HOLD}"' in statement
    assert f'AgentCore::Action::"{BOOKING}"' in statement
    assert re.search(r"\)\s*unless\s*\{", statement)


def binding_only() -> str:
    return f"{PERMIT_ALL}\n{policies('jwt')['meridian_traveler_binding']}"


def evaluate(tags: object, arguments: dict) -> tuple[object, list]:
    entities = [
        {"uid": {"type": "AgentCore::OAuthUser", "id": "sub-1"}, "attrs": {}, "parents": [],
         "tags": tags},
        {"uid": {"type": "AgentCore::Gateway", "id": GATEWAY}, "attrs": {}, "parents": []},
        {"uid": {"type": "AgentCore::Action", "id": HOLD}, "attrs": {}, "parents": []},
    ]
    request = {
        "principal": 'AgentCore::OAuthUser::"sub-1"',
        "action": f'AgentCore::Action::"{HOLD}"',
        "resource": f'AgentCore::Gateway::"{GATEWAY}"',
        "context": {"input": arguments},
    }
    result = cedarpy.is_authorized(request, binding_only(), entities)
    return result.decision, list(result.diagnostics.errors)


TAG = {"traveler_id": "trv_a"}
CASES = [
    ("match", TAG, {"travelerId": "trv_a"}, ALLOW),
    ("match with extra context keys", TAG,
     {"travelerId": "trv_a", "extra": 1, "nested": {"x": [1, 2]}}, ALLOW),
    ("mismatch", TAG, {"travelerId": "trv_b"}, DENY),
    ("case differs", TAG, {"travelerId": "TRV_A"}, DENY),
    ("no tag at all", {}, {"travelerId": "trv_a"}, DENY),
    ("another tag only", {"role": "x"}, {"travelerId": "trv_a"}, DENY),
    ("no argument", TAG, {}, DENY),
    ("only extra keys", TAG, {"other": "trv_a"}, DENY),
    ("empty tag and empty argument", {"traveler_id": ""}, {"travelerId": ""}, DENY),
    ("empty tag, real argument", {"traveler_id": ""}, {"travelerId": "trv_a"}, DENY),
    ("real tag, empty argument", TAG, {"travelerId": ""}, DENY),
    ("integer argument", TAG, {"travelerId": 7}, DENY),
    ("boolean argument", TAG, {"travelerId": True}, DENY),
    ("list argument", TAG, {"travelerId": ["trv_a"]}, DENY),
    ("record argument", TAG, {"travelerId": {"id": "trv_a"}}, DENY),
    ("no tags, no argument", {}, {}, DENY),
]


@pytest.mark.parametrize(("label", "tags", "arguments", "expected"), CASES,
                         ids=[case[0] for case in CASES])
def test_the_rule_fails_closed_and_never_errors(label, tags, arguments, expected) -> None:
    decision, errors = evaluate(tags, arguments)
    assert errors == [], (label, errors)
    assert decision == expected, label


def test_an_empty_claim_is_denied_even_when_the_argument_is_empty() -> None:
    assert evaluate({"traveler_id": ""}, {"travelerId": ""})[0] == DENY
