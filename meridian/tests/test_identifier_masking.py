"""No 12-digit run appears in the three payloads that used to print the account id."""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

from backend.agents.phase_04_production import concierge as concierge_mod
from backend.authorization import AuthorizationContext, AuthorizationDecision
from backend.db.journey_document import _authorization
from backend.http_auth import HttpPrincipal
from backend.routers import diagnostics
from backend.security.redaction import ACCOUNT_ID_PATTERN, mask_payload
from tests.test_production_transaction_boundaries import build_agent, runtime_decision

ACCOUNT = "123456789012"
ARN = f"arn:aws:sts::{ACCOUNT}:assumed-role/Meridian/session-a"
UUID_ENDING_IN_DIGITS = "f29c6d37-e9b9-4e77-bba8-890123456789"


def assert_no_account_id(payload) -> None:
    text = json.dumps(payload, default=str)
    assert not ACCOUNT_ID_PATTERN.search(text), f"a 12-digit run is left in: {text[:300]}"


def test_an_activity_id_ending_in_twelve_digits_survives_masking():
    activity = {"id": UUID_ENDING_IN_DIGITS, "details": f"role arn:aws:iam::{ACCOUNT}:role/x"}

    masked = mask_payload(activity)

    assert masked["id"] == UUID_ENDING_IN_DIGITS
    assert masked["details"] == "role arn:aws:iam::<acct>:role/x"
    assert_no_account_id(masked)


async def test_the_journey_authorization_subject_is_masked():
    async def q(sql, params):
        return [{
            "audit_id": "authz_1", "identity_provider": "aws_iam", "subject_id": "AROATEST",
            "principal": ARN, "decision": "allow", "reason": "active identity binding",
            "decided_at": "2026-10-01 10:00:00",
        }]

    evidence = await _authorization(q, "trv_1", "2026-10-02T14:00:00+00:00")

    assert evidence["subject"] == "arn:aws:sts::<acct>:assumed-role/Meridian/session-a"
    assert_no_account_id(evidence)


def test_the_phase_4_trace_fields_are_masked(monkeypatch):
    forwarded_span = {
        "id": "rt-2", "timestamp": "t", "activity_type": "tool_call",
        "title": "AgentCore Gateway: tools/list",
        "details": f"role arn:aws:iam::{ACCOUNT}:role/x",
        "telemetry": {"category": "gateway", "status": "ok", "fields": [
            {"label": "endpoint",
             "value": f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:gateway/g"},
        ]},
    }
    decision = runtime_decision(
        runtime_arn=f"arn:aws:bedrock-agentcore:us-east-1:{ACCOUNT}:runtime/rt-1",
        activities=[forwarded_span],
    )
    agent = build_agent([], decision, [])
    agent.identity = SimpleNamespace(scope_for_turn=lambda: SimpleNamespace(
        iam_identity=ARN,
        authorization=SimpleNamespace(provider="aws_iam", subject_id="AROATEST", principal=ARN),
    ))
    monkeypatch.setattr(concierge_mod, "require_agentcore_platform", lambda **_kw: None)

    _packages, activities, *_ = asyncio.run(
        agent.process_turn("Plan Tokyo", "trv_meridian_demo", None, 5)
    )

    spans = [a.model_dump() for a in activities]
    assert_no_account_id(spans)
    identity = next(s for s in spans if s["title"] == "Workload identity: AWS STS")
    fields = {f["label"]: f["value"] for f in identity["telemetry"]["fields"]}
    assert fields["iam_identity"] == "arn:aws:sts::<acct>:assumed-role/Meridian/session-a"
    runtime = next(s for s in spans if s["title"] == "AgentCore Runtime: turn complete")
    labels = {f["label"]: f["value"] for f in runtime["telemetry"]["fields"]}
    assert labels["runtime_arn"] == "arn:aws:bedrock-agentcore:us-east-1:<acct>:runtime/rt-1"


class ProbeDb:
    @asynccontextmanager
    async def scoped_session(self, **_kwargs):
        yield "tx-1"

    async def check_traveler_authorization(self, traveler_id, authorization, write_audit=True):
        return AuthorizationDecision(
            allowed=True, decision="allow", traveler_id=traveler_id, provider="aws_iam",
            subject_id="AROATEST", principal=ARN, binding_id="b1", audit_id="a1",
            reason="active identity binding",
        )

    async def execute(self, sql, params=(), transaction_id=None):
        if "current_user" in sql:
            return [{"effective_role": "meridian_app", "rls_active": True,
                     "scope": "trv_meridian_demo", "auth_provider": "aws_iam",
                     "auth_subject": ARN}]
        return [{"n": 2}]


async def test_the_rls_probe_principal_is_masked(monkeypatch):
    async def count(*_args, **_kwargs):
        return 5

    async def no_policies(*_args, **_kwargs):
        return []

    monkeypatch.setattr(diagnostics, "get_rds_data_client", lambda: ProbeDb())
    monkeypatch.setattr(diagnostics, "get_agentcore_identity", lambda: SimpleNamespace(
        authorization_context=lambda: AuthorizationContext("aws_iam", "AROATEST", ARN)))
    monkeypatch.setattr(diagnostics, "admin_count", count)
    monkeypatch.setattr(diagnostics, "_load_policies", no_policies)
    principal = HttpPrincipal("sub-1", "trv_meridian_demo", "test")

    response = await diagnostics.rls_probe(diagnostics.RlsProbeRequest(), principal)

    payload = response.model_dump()
    assert payload["authorization"]["principal"] == (
        "arn:aws:sts::<acct>:assumed-role/Meridian/session-a")
    assert payload["debug"]["authorization_subject"] == (
        "arn:aws:sts::<acct>:assumed-role/Meridian/session-a")
    assert_no_account_id(payload)
