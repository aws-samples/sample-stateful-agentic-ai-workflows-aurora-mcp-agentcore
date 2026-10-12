"""One switch, two modes, every hop: the default keeps IAM, jwt moves all of them together.

``MERIDIAN_AGENTCORE_AUTH`` unset or ``iam`` must leave today's release exactly as it is. ``jwt``
must change every hop at once, because a Runtime accepts IAM or JWT but never both. Each row below
runs one hop in both modes. The last test pins the set of files that branch on the mode, so a new
branch point cannot ship without a row here.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
from botocore.credentials import Credentials

from backend.agentcore import gateway as gateway_module
from backend.agentcore.caller_credential import caller_token_scope
from backend.agentcore.errors import CallerTokenExpired, CallerTokenMissing
from backend.agentcore.runtime import AgentCoreRuntimeAdapter
from backend.agentcore.runtime_https import RuntimeHttpClient
from backend.agentcore.workflow_runtime import WorkflowRuntimeClient
from backend.agents.phase_05_workflow import runtime_entry
from backend.agents.phase_05_workflow.runner import WorkflowCommand
from scripts import render_agentcore_config as render
from tests.jwt_support import access_token
from tests.test_cedar_traveler_binding import policies, rendered, runtime_env
from tests.test_concierge_identity import PAYLOAD, first_events, runtime  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/meridianv2_X-x"
JORDAN, DECOY = "trv_meridian_demo", "trv_demo_decoy"
MODES = ("iam", "jwt")
FRAME = b'data: "{\\"type\\": \\"result\\", \\"message\\": \\"ok\\", \\"state\\": {}}"\n\n'


@pytest.fixture(params=MODES)
def mode(request, monkeypatch):
    if request.param == "iam":
        monkeypatch.delenv("MERIDIAN_AGENTCORE_AUTH", raising=False)
    else:
        monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    return request.param


def https_transport(seen):
    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=iter([FRAME]))
    return httpx.MockTransport(handler)


def test_the_gateway_client_signs_in_iam_and_sends_the_token_in_jwt(mode):
    adapter = gateway_module.AgentCoreGatewayAdapter(
        gateway_url="https://gw.example/mcp", region="us-east-1")
    token = access_token()
    stop = RuntimeError("stop")
    with caller_token_scope(token), patch.object(
        gateway_module.urllib.request, "urlopen", side_effect=stop
    ) as urlopen, patch.object(
        gateway_module.NO_REDIRECT_OPENER, "open", side_effect=stop
    ) as opened:
        with pytest.raises(RuntimeError, match="stop"):
            adapter.call_tool("t", {})
    sent = opened if mode == "jwt" else urlopen
    headers = {k.lower(): v for k, v in sent.call_args.args[0].header_items()}
    if mode == "iam":
        assert headers["authorization"].startswith("AWS4-HMAC-SHA256")
    else:
        assert headers["authorization"] == f"Bearer {token}"


def test_the_concierge_client_invokes_through_boto_in_iam_and_https_in_jwt(mode):
    boto, seen = MagicMock(), []
    body = MagicMock()
    body.read.side_effect = [FRAME, b""]
    boto.invoke_agent_runtime.return_value = {"response": body}
    adapter = AgentCoreRuntimeAdapter(
        runtime_arn=ARN, qualifier="DEFAULT", region="us-east-1",
        http=RuntimeHttpClient(transport=https_transport(seen)))
    adapter._client = boto
    with caller_token_scope(access_token()):
        adapter.invoke_turn("c", JORDAN, "hi", "", budget_ceiling_cents=1, travelers_count=1)
    expected = (0, 1) if mode == "iam" else (1, 0)
    assert (len(seen), boto.invoke_agent_runtime.call_count) == expected


async def test_the_workflow_client_invokes_through_boto_in_iam_and_https_in_jwt(mode):
    boto, seen = MagicMock(), []
    body = MagicMock()
    body.read.side_effect = [FRAME, b""]
    boto.invoke_agent_runtime.return_value = {"response": body}
    client = WorkflowRuntimeClient(
        ARN, region="us-east-1", client=boto,
        http=RuntimeHttpClient(transport=https_transport(seen)))
    command = WorkflowCommand(query="q", traveler_id=JORDAN, thread_id="phase5-0123456789ab")
    with caller_token_scope(access_token()):
        await client.run(command)
    expected = (0, 1) if mode == "iam" else (1, 0)
    assert (len(seen), boto.invoke_agent_runtime.call_count) == expected


async def test_a_missing_token_is_refused_before_any_request_in_jwt_only(mode):
    boto, seen = MagicMock(), []
    body = MagicMock()
    body.read.side_effect = [FRAME, b""]
    boto.invoke_agent_runtime.return_value = {"response": body}
    client = WorkflowRuntimeClient(
        ARN, region="us-east-1", client=boto,
        http=RuntimeHttpClient(transport=https_transport(seen)))
    command = WorkflowCommand(query="q", traveler_id=JORDAN, thread_id="phase5-0123456789ab")
    if mode == "jwt":
        with pytest.raises(CallerTokenMissing):
            await client.run(command)
        assert (len(seen), boto.invoke_agent_runtime.call_count) == (0, 0)
    else:
        await client.run(command)
        assert boto.invoke_agent_runtime.call_count == 1


def test_an_expired_token_is_refused_by_the_gateway_client_in_jwt_only(mode):
    adapter = gateway_module.AgentCoreGatewayAdapter(
        gateway_url="https://gw.example/mcp", region="us-east-1")
    session = MagicMock()
    session.get_credentials.return_value.get_frozen_credentials.return_value = (
        Credentials("AKIAFAKE", "fake-secret"))
    expired = access_token(expires_in=-60)
    with caller_token_scope(expired), patch.object(
        gateway_module.boto3, "Session", return_value=session
    ):
        if mode == "jwt":
            with pytest.raises(CallerTokenExpired):
                adapter._build_headers(b"{}")
        else:
            headers = adapter._build_headers(b"{}")
            assert headers["Authorization"].startswith("AWS4-HMAC-SHA256")


def test_the_workflow_runtime_trusts_the_payload_in_iam_and_the_claim_in_jwt(mode):
    payload = {"traveler_id": DECOY}
    headers = {"Authorization": f"Bearer {access_token(JORDAN)}"}
    if mode == "iam":
        assert runtime_entry.resolve_traveler(payload, None, headers).traveler_id == DECOY
    else:
        with pytest.raises(Exception, match="different traveler"):
            runtime_entry.resolve_traveler(payload, None, headers)
        assert runtime_entry.resolve_traveler({}, None, headers).traveler_id == JORDAN


def test_the_concierge_runtime_signs_in_iam_and_forwards_the_token_in_jwt(mode, runtime):  # noqa: F811
    headers = {"Authorization": f"Bearer {access_token(JORDAN)}"}
    asyncio.run(first_events(runtime.run(PAYLOAD, headers), 1))
    expected = {"url", "auth_provider"} if mode == "iam" else {"url", "headers"}
    assert set(runtime.created[0]) == expected


def test_the_rendered_config_carries_the_mode_and_the_binding_rule_only_in_jwt(mode):
    env = None if mode == "iam" else "jwt"
    spec = rendered(env)
    for name in ("MeridianConcierge", "MeridianWorkflow"):
        assert runtime_env(spec, name)["MERIDIAN_AGENTCORE_AUTH"] == mode
    assert ("meridian_traveler_binding" in policies(env)) is (mode == "jwt")
    assert render.JWT_ONLY_POLICIES == {"meridian_traveler_binding"}


def test_stopping_a_session_follows_the_mode(mode):
    boto = MagicMock()
    seen = []
    http = RuntimeHttpClient(transport=httpx.MockTransport(
        lambda request: seen.append(request) or httpx.Response(200, json={})))
    client = WorkflowRuntimeClient(ARN, region="us-east-1", client=boto, http=http)
    with caller_token_scope(access_token()):
        stop = asyncio.run(client.stop_session(JORDAN, "phase5-0123456789ab"))
    assert stop.outcome == "stopped"
    expected = (1, 0) if mode == "jwt" else (0, 1)
    assert (len(seen), boto.stop_runtime_session.call_count) == expected


BRANCHING_FILES = {
    "backend/http_auth.py",
    "backend/agentcore/gateway.py",
    "backend/agentcore/runtime.py",
    "backend/agentcore/workflow_runtime.py",
    "backend/agents/phase_05_workflow/runtime_entry.py",
    "meridian_agentcore/app/MeridianConcierge/main.py",
    "scripts/agentcore_caller.py",
    "scripts/render_agentcore_config.py",
}


def test_only_the_files_with_a_row_above_branch_on_the_mode():
    pattern = re.compile(r"\b(jwt_mode|agentcore_auth_mode|auth_mode|identity_mode)\(")
    defining = {"backend/agentcore/auth_mode.py",
                "meridian_agentcore/app/MeridianConcierge/caller_identity.py"}
    found = set()
    for folder in ("backend", "scripts", "meridian_agentcore/app"):
        for path in (ROOT / folder).rglob("*.py"):
            relative = path.relative_to(ROOT).as_posix()
            if "/MeridianWorkflow/backend/" in relative or relative in defining:
                continue
            if pattern.search(path.read_text()):
                found.add(relative)
    assert found == BRANCHING_FILES, json.dumps(sorted(found ^ BRANCHING_FILES))
