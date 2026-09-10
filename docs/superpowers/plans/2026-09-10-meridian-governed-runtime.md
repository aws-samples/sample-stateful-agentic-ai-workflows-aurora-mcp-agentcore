# Meridian Governed Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the AgentCore Runtime the agent that owns the Phase 4 tool loop, add a governed courtesy-hold tool on the gateway behind Cedar in ENFORCE mode, and feed the trace panel from what the runtime actually did.

**Architecture:** The FastAPI backend keeps the identity chain it already teaches (STS workload, traveler grant, RLS read, RLS write, audit) and becomes a thin caller of the runtime. The runtime is a Strands agent that discovers its tools from AgentCore Gateway over MCP with SigV4, keeps its conversation in AgentCore Memory through the Strands session manager, and streams activity spans back over SSE. A second Lambda target, `MeridianHolds`, exposes `get_package_details` and `create_courtesy_hold`; the hold Lambda authorizes its own execution role against `traveler_identity_bindings`, steps down to `meridian_app`, and calls the existing `create_courtesy_hold` SQL function. An AgentCore Policy engine with three Cedar policies decides every tool call before the Lambda runs. The runtime is deployed by the existing declarative `agentcore` CLI project, so every resource stays in `agentcore.json`.

**Tech Stack:** Python 3.13 backend (FastAPI, boto3), Python 3.14 runtime on AgentCore (Strands Agents ≥ 1.52, bedrock-agentcore ≥ 1.9.1, aws-opentelemetry-distro, httpx, mcp), Python 3.13 Lambda (boto3 only), `@aws/agentcore` CLI 0.27 with `@aws/agentcore-cdk` 0.1.0-alpha.47, Aurora PostgreSQL 18 over the RDS Data API, React + Vite frontend, pytest, vitest, jest.

Spec: the review delivered in this session (option B), plus `PRODUCT.md`, `DESIGN.md`, `docs/PRESENTER_GUIDE.md` claim boundaries.

## Global Constraints

- Work directly on `main` in `/Users/shayons/Desktop/Workshops/sample-stateful-agentic-ai-workflows-aurora-mcp-agentcore`. One commit per task. Push at the end of every task.
- All backend commands run from `meridian/`; Python is `venv/bin/python`, tests are `venv/bin/python -m pytest -m "not database"` (CI parity). Baseline: 322 passed, 6 skipped. Frontend baseline: 176 vitest tests pass.
- Do not touch the `meridian_session` memory resource in `agentcore.json`. Renaming or restrategising it replaces the resource and drops the seeded Tokyo history. Never rename `MeridianConcierge` or `meridian-aurora` either.
- Onward's database `onward` lives on the same cluster `meridian-demo`. No cluster-level operations of any kind.
- Never call `secretsmanager get-secret-value`. The Data API resolves the secret server side.
- No em dashes in AWS resource names or descriptions. Cedar policy names match `^[A-Za-z][A-Za-z0-9_]{0,47}$`.
- Cedar has no floating point type. Every amount that a policy compares is an integer number of cents.
- `context.input.<arg>` in Cedar is only valid for arguments that are `required` in the tool schema. Every argument a policy names is required.
- Coding standards: functions ≤ 100 lines, complexity ≤ 8, ≤ 5 positional params, 100-char lines, absolute imports (`from backend.x import y`), Google docstrings on non-trivial public APIs. `ruff check backend scripts tests` must be clean.
- Deploy in two `agentcore deploy` passes. Pass one: runtime code and env, the `MeridianHolds` target. Pass two: policy engine, policies, gateway association. Cedar validation needs the target's tool schema to exist, and the gateway association needs the gateway role's new policy-engine permissions to have propagated.
- The runtime is invoked with `accept: text/event-stream`. Every yielded item is one JSON object.

---

### Task 1: Delete the fabricated trace builder

**Files:**
- Delete: `meridian/frontend/src/utils/traceTelemetry.ts`

**Interfaces:**
- Consumes: nothing. `grep -rn "traceTelemetry'" meridian/frontend/src` returns no importers.
- Produces: nothing. This is dead code that hard-codes `execution_time_ms`, a `governance` field, and long-term facts.

- [ ] **Step 1: Confirm there are no importers**

Run: `cd meridian/frontend && grep -rn "traceTelemetry" src --include='*.ts' --include='*.tsx' | grep -v 'src/utils/traceTelemetry.ts'`
Expected: no output.

- [ ] **Step 2: Delete the file**

Run: `trash meridian/frontend/src/utils/traceTelemetry.ts`

- [ ] **Step 3: Lint, typecheck, test**

Run: `cd meridian/frontend && npm run lint && npx tsc --noEmit && npx vitest --run --reporter=dot 2>&1 | tail -4`
Expected: lint clean, 176 tests pass.

- [ ] **Step 4: Commit**

```bash
git add -A meridian/frontend/src/utils
git commit -m "Remove the unused fabricated trace preamble"
```

---

### Task 2: Runtime package skeleton and SigV4 gateway auth

**Files:**
- Modify: `meridian/meridian_agentcore/app/MeridianConcierge/pyproject.toml`
- Create: `meridian/meridian_agentcore/app/MeridianConcierge/gateway_auth.py`
- Test: `meridian/tests/test_runtime_gateway_auth.py`

**Interfaces:**
- Produces: `GatewaySigV4(session: boto3.Session, region: str)`, an `httpx.Auth` that signs MCP requests for service `bedrock-agentcore`.

- [ ] **Step 1: Write the failing test**

```python
"""The runtime signs gateway MCP requests with its execution role, not a bearer token."""

from __future__ import annotations

import sys
from pathlib import Path

import boto3
import httpx

RUNTIME = Path(__file__).resolve().parents[1] / "meridian_agentcore" / "app" / "MeridianConcierge"
sys.path.insert(0, str(RUNTIME))

from gateway_auth import GatewaySigV4  # noqa: E402


def test_gateway_requests_are_sigv4_signed_for_bedrock_agentcore():
    session = boto3.Session(
        aws_access_key_id="AKIAEXAMPLE",
        aws_secret_access_key="secret",
        aws_session_token="token",
        region_name="us-east-1",
    )
    auth = GatewaySigV4(session, "us-east-1")
    request = httpx.Request(
        "POST",
        "https://x.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp",
        content=b'{"jsonrpc":"2.0"}',
        headers={"content-type": "application/json", "accept": "application/json, text/event-stream"},
    )
    signed = next(auth.auth_flow(request))
    assert "AWS4-HMAC-SHA256 Credential=AKIAEXAMPLE/" in signed.headers["Authorization"]
    assert "/us-east-1/bedrock-agentcore/aws4_request" in signed.headers["Authorization"]
    assert signed.headers["X-Amz-Security-Token"] == "token"
    assert "X-Amz-Date" in signed.headers
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd meridian && venv/bin/python -m pytest tests/test_runtime_gateway_auth.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'gateway_auth'`.

- [ ] **Step 3: Write the auth module**

```python
"""SigV4 signing for the AgentCore Gateway MCP endpoint.

The gateway uses AWS_IAM inbound authorization, so the runtime signs every MCP
request with its own execution-role credentials. No bearer tokens, no identity
provider, no secrets in the code.
"""

from __future__ import annotations

import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

SIGNED_HEADERS = ("content-type", "accept", "mcp-session-id", "mcp-protocol-version")
COPIED_HEADERS = ("Authorization", "X-Amz-Date", "X-Amz-Security-Token", "X-Amz-Content-SHA256")


class GatewaySigV4(httpx.Auth):
    """An httpx auth hook that signs requests for the bedrock-agentcore service."""

    requires_request_body = True

    def __init__(self, session, region: str, service: str = "bedrock-agentcore") -> None:
        self._credentials = session.get_credentials()
        self._region = region
        self._service = service

    def auth_flow(self, request: httpx.Request):
        headers = {
            name: value
            for name, value in request.headers.items()
            if name.lower() in SIGNED_HEADERS
        }
        aws_request = AWSRequest(
            method=request.method,
            url=str(request.url),
            data=request.content or b"",
            headers=headers,
        )
        SigV4Auth(
            self._credentials.get_frozen_credentials(), self._service, self._region
        ).add_auth(aws_request)
        for name in COPIED_HEADERS:
            if name in aws_request.headers:
                request.headers[name] = aws_request.headers[name]
        yield request
```

- [ ] **Step 4: Update the runtime dependencies**

Replace the `dependencies` list in `pyproject.toml` with:

```toml
dependencies = [
    "aws-opentelemetry-distro>=0.18.0",
    "bedrock-agentcore>=1.9.1",
    "botocore[crt]>=1.35.0",
    "httpx>=0.28.0",
    "mcp>=1.26.0",
    "strands-agents>=1.52.0",
]
```

- [ ] **Step 5: Run the test**

Run: `cd meridian && venv/bin/python -m pytest tests/test_runtime_gateway_auth.py -q`
Expected: 1 passed.

- [ ] **Step 6: Commit**

```bash
git add meridian/meridian_agentcore/app/MeridianConcierge meridian/tests/test_runtime_gateway_auth.py
git commit -m "Sign runtime gateway requests with SigV4"
```

---

### Task 3: Runtime trace hooks

**Files:**
- Create: `meridian/meridian_agentcore/app/MeridianConcierge/trace.py`
- Test: `meridian/tests/test_runtime_trace_hooks.py`

**Interfaces:**
- Consumes: Strands `BeforeToolCallEvent`, `AfterToolCallEvent`, `HookProvider`.
- Produces: `TraceHooks(queue: asyncio.Queue, turn: TurnContext)`; `TurnContext(traveler_id, conversation_id, hold_confirmed, budget_ceiling_cents, gateway_id, policy_engine_id)`; `friendly_denial(text) -> str`; `short(tool_name) -> str`; `activity(...) -> dict`. Every emitted queue item is `("activity", dict)`, `("hold", dict)`, or `("packages", list)`. The activity dict has the backend's `ActivityEntry` keys: `id, timestamp, activity_type, title, details, agent_name, agent_file, execution_time_ms, telemetry`.

- [ ] **Step 1: Write the failing tests**

```python
"""The runtime turns every gateway tool call into a trace span and enforces the hold contract."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

RUNTIME = Path(__file__).resolve().parents[1] / "meridian_agentcore" / "app" / "MeridianConcierge"
sys.path.insert(0, str(RUNTIME))

from trace import TraceHooks, TurnContext, friendly_denial, short  # noqa: E402


def _turn(**overrides):
    base = dict(
        traveler_id="trv_meridian_demo",
        conversation_id="conv-1",
        hold_confirmed=False,
        budget_ceiling_cents=400000,
        gateway_id="gw-1",
        policy_engine_id="pe-1",
    )
    base.update(overrides)
    return TurnContext(**base)


def _event(name, inputs, tool_use_id="t1", result=None):
    return SimpleNamespace(
        tool_use={"name": name, "toolUseId": tool_use_id, "input": inputs},
        cancel_tool=False,
        result=result,
    )


def _drain(queue):
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


def test_short_strips_the_target_prefix():
    assert short("MeridianHolds___create_courtesy_hold") == "create_courtesy_hold"
    assert short("semantic_trip_search") == "semantic_trip_search"


def test_search_call_emits_a_tool_span_with_arguments():
    queue = asyncio.Queue()
    hooks = TraceHooks(queue, _turn())
    hooks.before(_event("SemanticTripSearchLambda___semantic_trip_search", {"query": "Tokyo", "limit": 5}))
    kind, span = _drain(queue)[0]
    assert kind == "activity"
    assert span["activity_type"] == "search"
    assert span["telemetry"]["category"] == "gateway"
    assert span["telemetry"]["status"] == "ok"
    labels = {field["label"]: field["value"] for field in span["telemetry"]["fields"]}
    assert labels["tool"] == "SemanticTripSearchLambda___semantic_trip_search"
    assert labels["auth"] == "SigV4"


def test_hold_arguments_are_pinned_to_the_turn_not_the_model():
    hooks = TraceHooks(asyncio.Queue(), _turn(hold_confirmed=False, budget_ceiling_cents=350000))
    event = _event(
        "MeridianHolds___create_courtesy_hold",
        {
            "travelerId": "trv_someone_else",
            "packageId": "CTY-002",
            "duration": "7 nights",
            "travelers": 2,
            "unitPriceCents": 250000,
            "totalCents": 500000,
            "holdMinutes": 720,
            "travelerConfirmed": True,
            "budgetCeilingCents": 9999999,
            "journeyRef": "concierge:conv-1",
        },
    )
    hooks.before(event)
    assert event.cancel_tool is False
    assert event.tool_use["input"]["travelerId"] == "trv_meridian_demo"
    assert event.tool_use["input"]["travelerConfirmed"] is False
    assert event.tool_use["input"]["budgetCeilingCents"] == 350000
    assert event.tool_use["input"]["journeyRef"] == "concierge:conv-1"


def test_successful_hold_emits_hold_and_result_span():
    queue = asyncio.Queue()
    hooks = TraceHooks(queue, _turn(hold_confirmed=True))
    event = _event("MeridianHolds___create_courtesy_hold", {"packageId": "CTY-002"})
    hooks.before(event)
    payload = {
        "hold": {"bookingId": "HLD-1", "status": "held", "replayed": False,
                 "expiresAt": "2026-09-11T00:00:00+00:00", "seatsRemaining": 3},
        "summary": "Held CTY-002 for 2 travelers",
        "governance": {"subject": "AROA1", "decision": "allow"},
    }
    event.result = {"status": "success", "content": [{"text": json.dumps(payload)}]}
    hooks.after(event)
    kinds = [kind for kind, _ in _drain(queue)]
    assert kinds.count("hold") == 1
    assert hooks.hold["bookingId"] == "HLD-1"


def test_policy_denial_is_a_denied_security_span_and_a_refused_hold():
    queue = asyncio.Queue()
    hooks = TraceHooks(queue, _turn())
    event = _event("MeridianHolds___create_courtesy_hold", {"packageId": "CTY-002"})
    hooks.before(event)
    event.result = {
        "status": "error",
        "content": [{"text": "Tool Execution Denied: [Policy evaluation denied due to "
                             "meridian_hold_within_budget-abc12de_]"}],
    }
    hooks.after(event)
    items = _drain(queue)
    result = [span for kind, span in items if kind == "activity"][-1]
    assert result["telemetry"]["status"] == "denied"
    assert result["telemetry"]["category"] == "security"
    assert "meridian_hold_within_budget" in result["details"]
    refused = [span for kind, span in items if kind == "hold"][0]
    assert refused["policyDecision"] == "deny"
    assert hooks.hold is None


def test_friendly_denial_names_the_policy_or_the_default_deny():
    assert friendly_denial("x [Policy evaluation denied due to meridian_hold_requires_confirmation-k2j_]").startswith(
        "Refused by Cedar policy meridian_hold_requires_confirmation"
    )
    assert "denied by default" in friendly_denial("[No policy applies to the request (denied by default).]")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd meridian && venv/bin/python -m pytest tests/test_runtime_trace_hooks.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'trace'` (the stdlib `trace` module may shadow; if `ImportError: cannot import name 'TraceHooks'` appears instead, the stdlib module won, which Step 3's file name choice avoids: name the module `turn_trace.py` and import `from turn_trace import ...` in the test).

- [ ] **Step 3: Write the hooks module** (as `turn_trace.py`; update the test import accordingly)

```python
"""Trace hooks: every gateway tool call becomes a span the showcase can render.

The hooks also pin the hold contract to the turn. The model proposes a hold; the
traveler id, the confirmation flag, the budget ceiling and the journey reference
always come from the request the backend authorized, never from the model. The
gateway's Cedar policies then decide with those values.
"""

from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookProvider

AGENT_FILE = "meridian_agentcore/app/MeridianConcierge/main.py"
POLICY_REASONS = {
    "meridian_hold_requires_confirmation": (
        "A courtesy hold runs only after the traveler confirms it and only for 12 hours or less."
    ),
    "meridian_hold_within_budget": (
        "The hold total must stay within the traveler's saved budget ceiling."
    ),
}
SPANS = {
    "semantic_trip_search": ("search", "AgentCore Gateway · tools/call → semantic_trip_search",
                             "Aurora pgvector search through the managed MCP tool"),
    "get_package_details": ("inventory", "AgentCore Gateway · tools/call → get_package_details",
                            "Live durations, availability and highlights for one package"),
    "create_courtesy_hold": ("order", "AgentCore Gateway · tools/call → create_courtesy_hold",
                             "Cedar decides on the arguments, then one atomic Aurora write"),
}


@dataclass(frozen=True)
class TurnContext:
    """What the backend authorized for this turn."""

    traveler_id: str
    conversation_id: str
    hold_confirmed: bool
    budget_ceiling_cents: int
    gateway_id: str
    policy_engine_id: str


def short(tool_name: str) -> str:
    return tool_name.split("___")[-1]


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def activity(activity_type, title, details=None, telemetry=None, elapsed_ms=None):
    """One ActivityEntry in the shape the backend's chat router already returns."""
    return {
        "id": f"rt-{uuid.uuid4().hex[:10]}",
        "timestamp": now(),
        "activity_type": activity_type,
        "title": title,
        "details": details,
        "agent_name": "ProductionAgent",
        "agent_file": AGENT_FILE,
        "execution_time_ms": elapsed_ms,
        "telemetry": telemetry,
    }


def friendly_denial(text: str) -> str:
    """Turn the gateway's policy error into a sentence; the raw text stays in the span."""
    named = re.search(r"denied due to ([A-Za-z0-9_]+?)(?:-[a-z0-9]+_?)?\]", text)
    if named:
        policy = named.group(1)
        return f"Refused by Cedar policy {policy}. {POLICY_REASONS.get(policy, '')}".strip()
    if "No policy applies" in text or "denied by default" in text:
        return "No Cedar policy permits this call, so the gateway denied it by default."
    return text


def parse_result(result: dict) -> dict | None:
    """The gateway returns the Lambda's JSON as MCP text content."""
    text = "".join(
        block.get("text", "") for block in result.get("content", []) if isinstance(block, dict)
    )
    try:
        payload = json.loads(text)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


class TraceHooks(HookProvider):
    """Emit spans for every tool call and pin the hold arguments to the turn."""

    def __init__(self, queue, turn: TurnContext) -> None:
        self.queue = queue
        self.turn = turn
        self.started: dict[str, float] = {}
        self.hold: dict | None = None
        self.packages: list[dict] = []

    def register_hooks(self, registry, **kwargs) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before)
        registry.add_callback(AfterToolCallEvent, self.after)

    def emit(self, kind: str, payload) -> None:
        self.queue.put_nowait((kind, payload))

    def before(self, event) -> None:
        name = short(event.tool_use["name"])
        if name not in SPANS:
            return
        args = event.tool_use.get("input") or {}
        if name == "create_courtesy_hold":
            self._pin_hold_arguments(args)
        kind, title, summary = SPANS[name]
        self.emit("activity", activity(kind, title, summary, {
            "category": "gateway",
            "component": "Bedrock AgentCore Gateway",
            "status": "ok",
            "fields": [
                {"label": "tool", "value": event.tool_use["name"], "mono": True},
                {"label": "arguments", "value": json.dumps(args, ensure_ascii=False), "mono": True},
                {"label": "auth", "value": "SigV4"},
                {"label": "gateway", "value": self.turn.gateway_id, "mono": True},
                {"label": "policy_engine", "value": self.turn.policy_engine_id, "mono": True},
            ],
        }))
        self.started[event.tool_use["toolUseId"]] = time.monotonic()

    def _pin_hold_arguments(self, args: dict) -> None:
        args["travelerId"] = self.turn.traveler_id
        args["travelerConfirmed"] = self.turn.hold_confirmed
        args["budgetCeilingCents"] = self.turn.budget_ceiling_cents
        args["journeyRef"] = f"concierge:{self.turn.conversation_id}"

    def after(self, event) -> None:
        name = short(event.tool_use["name"])
        if name not in SPANS:
            return
        tool_id = event.tool_use["toolUseId"]
        if tool_id not in self.started:
            return
        elapsed = round((time.monotonic() - self.started.pop(tool_id)) * 1000)
        result = event.result or {}
        payload = parse_result(result) if result.get("status") == "success" else None
        if payload is not None and "error" not in payload:
            self._success(name, payload, elapsed)
            return
        text = " ".join(
            block.get("text", "") for block in result.get("content", []) if isinstance(block, dict)
        )[:600]
        if payload is not None and "error" in payload:
            text = str(payload["error"])
        denied = bool(re.search(r"policy|denied|not authori[sz]ed|forbid", text, re.I))
        self._failure(name, text, denied, elapsed, event.tool_use)

    def _success(self, name: str, payload: dict, elapsed: int) -> None:
        if name == "semantic_trip_search":
            self.packages = payload.get("packages") or []
            self.emit("packages", self.packages)
        if name == "create_courtesy_hold":
            self.hold = payload.get("hold")
            self.emit("hold", {"hold": self.hold, "policyDecision": "allow"})
        summary = payload.get("summary") or f"{name} returned"
        fields = [{"label": "result", "value": summary}]
        governance = payload.get("governance")
        if governance:
            fields.append({"label": "workload", "value": governance.get("subject", ""), "mono": True})
            fields.append({"label": "traveler_grant", "value": governance.get("decision", "")})
        self.emit("activity", activity(
            "result" if name != "semantic_trip_search" else "search",
            f"{short(name)} · result", summary,
            {"category": "gateway", "component": "Bedrock AgentCore Gateway", "status": "ok",
             "fields": fields}, elapsed))

    def _failure(self, name, text, denied, elapsed, tool_use) -> None:
        summary = friendly_denial(text) if denied else (text or "The gateway returned no result.")
        title = "Hold refused by Cedar policy" if denied and name == "create_courtesy_hold" \
            else ("Tool call denied by policy" if denied else "Tool call failed")
        self.emit("activity", activity("security" if denied else "error", title, summary, {
            "category": "security" if denied else "tool",
            "component": "Bedrock AgentCore Policy" if denied else "Bedrock AgentCore Gateway",
            "status": "denied" if denied else "error",
            "fields": [
                {"label": "tool", "value": tool_use["name"], "mono": True},
                {"label": "arguments", "value": json.dumps(tool_use.get("input") or {}), "mono": True},
                {"label": "gateway_error", "value": text, "mono": True},
            ],
        }, elapsed))
        if name == "create_courtesy_hold":
            self.hold = None
            self.emit("hold", {"hold": None, "refused": summary, "error": text,
                               "policyDecision": "deny" if denied else None})
```

- [ ] **Step 4: Run the tests**

Run: `cd meridian && venv/bin/python -m pytest tests/test_runtime_trace_hooks.py -q`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add meridian/meridian_agentcore/app/MeridianConcierge/turn_trace.py meridian/tests/test_runtime_trace_hooks.py
git commit -m "Add runtime trace hooks that pin the hold contract to the turn"
```

---

### Task 4: Runtime entrypoint with gateway tools, memory and SSE events

**Files:**
- Rewrite: `meridian/meridian_agentcore/app/MeridianConcierge/main.py`
- Create: `meridian/meridian_agentcore/app/MeridianConcierge/prompts.py`
- Test: `meridian/tests/test_runtime_prompts.py`

**Interfaces:**
- Consumes: `GatewaySigV4`, `TraceHooks`, `TurnContext`, `activity`. Env vars injected by the CDK: `AGENTCORE_GATEWAY_MERIDIAN_AURORA_URL`, `MEMORY_MERIDIAN_SESSION_ID`; env vars set in `agentcore.json`: `BEDROCK_MODEL_ID`, `MERIDIAN_GATEWAY_ID`, `MERIDIAN_POLICY_ENGINE_ID`, `AWS_REGION`.
- Consumes payload: `{"event": "concierge_turn", "traveler_id", "conversation_id", "prompt", "memory_context", "budget_ceiling_cents": int, "travelers_count": int, "hold_confirmed": bool, "hold_target": {"package_id", "duration", "travelers", "unit_price_cents"} | null}`.
- Produces SSE items, each a JSON object: `{"type":"activity", ...ActivityEntry}`, `{"type":"packages","packages":[...]}`, `{"type":"hold", "hold":{...}|null, "refused"?, "policyDecision"?}`, `{"type":"token","text"}`, `{"type":"result","message","recommended_package_ids","follow_ups","trace_id","usage"}`, `{"type":"error","message"}`.

- [ ] **Step 1: Write the failing prompt test**

```python
"""The system prompt states the tool order and the hold rule the hooks enforce."""

from __future__ import annotations

import sys
from pathlib import Path

RUNTIME = Path(__file__).resolve().parents[1] / "meridian_agentcore" / "app" / "MeridianConcierge"
sys.path.insert(0, str(RUNTIME))

from prompts import system_prompt, turn_prompt  # noqa: E402


def test_system_prompt_names_the_tools_and_the_confirmation_rule():
    text = system_prompt(hold_confirmed=False, hold_target=None)
    assert "semantic_trip_search" in text
    assert "get_package_details" in text
    assert "create_courtesy_hold" in text
    assert "has not confirmed" in text


def test_confirmed_turn_prompt_carries_the_exact_hold_terms():
    target = {"package_id": "CTY-002", "duration": "7 nights", "travelers": 2, "unit_price_cents": 250000}
    text = turn_prompt("Hold it", "prefers window seats", target, hold_confirmed=True)
    assert "CTY-002" in text and "7 nights" in text and "250000" in text
    assert "confirmed" in text
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd meridian && venv/bin/python -m pytest tests/test_runtime_prompts.py -q`
Expected: FAIL, `No module named 'prompts'`.

- [ ] **Step 3: Write `prompts.py`**

```python
"""Prompts for the Meridian concierge runtime."""

from __future__ import annotations

import json

BASE = """You are Meridian's travel concierge, running inside Amazon Bedrock AgentCore Runtime
for one authenticated traveler. Your tools are served by AgentCore Gateway and every call is
checked by Cedar policy before it runs.

Work in this order and call one tool at a time:
1. semantic_trip_search(query, limit) to find candidate packages for the traveler's request.
2. get_package_details(packageId) for the packages you intend to recommend when the traveler
   cares about dates, duration or availability.
3. create_courtesy_hold(...) only when the turn says the traveler has confirmed a hold. Pass
   the exact package, duration, travelers and prices from the tool results; the platform pins
   the traveler identity, the confirmation flag and the budget ceiling.

Ground every statement in tool results and the authorized traveler context. Never invent
seats, prices, flight times or confirmations. Write two to four sentences for the traveler,
no headings, no lists, and mention the strongest saved-preference match when it applies.
"""

UNCONFIRMED = ("The traveler has not confirmed a hold on this turn. If they ask you to hold or "
               "book, you may attempt create_courtesy_hold so the gateway policy decides, and "
               "you must then tell the traveler exactly why it was refused and that the Hold "
               "button on a trip confirms it.")
CONFIRMED = ("The traveler has confirmed a hold on this turn. Call create_courtesy_hold once "
             "with the given terms, then report the hold id, expiry and remaining places.")


def system_prompt(hold_confirmed: bool, hold_target: dict | None) -> str:
    return BASE + "\n" + (CONFIRMED if hold_confirmed and hold_target else UNCONFIRMED)


def turn_prompt(message: str, memory_context: str, hold_target: dict | None,
                hold_confirmed: bool) -> str:
    parts = [f"Traveler request:\n{message}"]
    if memory_context:
        parts.append(f"Authorized traveler context:\n{memory_context}")
    if hold_confirmed and hold_target:
        parts.append("Hold confirmed by the traveler for exactly these terms:\n"
                     + json.dumps(hold_target, ensure_ascii=False))
    return "\n\n".join(parts)
```

- [ ] **Step 4: Rewrite `main.py`**

```python
"""Meridian concierge runtime.

A Strands agent plans with tools served by AgentCore Gateway over MCP, keeps its
conversation in AgentCore Memory, and streams activity spans back to the backend.
Every tool call is signed with the runtime's execution role and checked by the
gateway's Cedar policies before any Lambda runs.
"""

from __future__ import annotations

import asyncio
import json
import os
import time

import boto3
from bedrock_agentcore.memory.integrations.strands.config import (
    AgentCoreMemoryConfig,
    RetrievalConfig,
)
from bedrock_agentcore.memory.integrations.strands.session_manager import (
    AgentCoreMemorySessionManager,
)
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from opentelemetry import trace
from strands import Agent
from strands.models.bedrock import BedrockModel
from strands.tools.mcp import MCPClient

from gateway_auth import GatewaySigV4
from prompts import system_prompt, turn_prompt
from turn_trace import TraceHooks, TurnContext, activity

app = BedrockAgentCoreApp()
REGION = os.getenv("AWS_REGION", "us-east-1")
SESSION = boto3.Session(region_name=REGION)
GATEWAY_URL = os.environ["AGENTCORE_GATEWAY_MERIDIAN_AURORA_URL"]
MEMORY_ID = os.environ["MEMORY_MERIDIAN_SESSION_ID"]
MODEL_ID = os.getenv("BEDROCK_MODEL_ID", "global.anthropic.claude-sonnet-5")
GATEWAY_ID = os.getenv("MERIDIAN_GATEWAY_ID", GATEWAY_URL.split("//")[-1].split(".")[0])
POLICY_ENGINE_ID = os.getenv("MERIDIAN_POLICY_ENGINE_ID", "")
SESSION_NAMESPACE = "/users/{actorId}/sessions/{sessionId}"
FOLLOW_UPS = ["Compare the top options", "Check duration availability", "Explain the preference match"]


def trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    return format(context.trace_id, "032x") if context.is_valid else None


async def drive(agent, prompt, queue):
    try:
        async for part in agent.stream_async(prompt):
            await queue.put(("part", part))
    except Exception as error:  # noqa: BLE001 - surfaced to the backend as an error event
        await queue.put(("error", error))
    finally:
        await queue.put(("end", None))


def memory_manager(traveler_id: str, conversation_id: str) -> AgentCoreMemorySessionManager:
    config = AgentCoreMemoryConfig(
        memory_id=MEMORY_ID,
        session_id=conversation_id,
        actor_id=traveler_id,
        retrieval_config={SESSION_NAMESPACE: RetrievalConfig(top_k=5, relevance_score=0.3)},
    )
    return AgentCoreMemorySessionManager(agentcore_memory_config=config, region_name=REGION)


def memory_span(traveler_id: str, conversation_id: str) -> dict:
    client = SESSION.client("bedrock-agentcore")
    events = client.list_events(memoryId=MEMORY_ID, actorId=traveler_id,
                                sessionId=conversation_id, maxResults=50)
    count = len(events.get("events", []))
    return activity("reasoning", "AgentCore Memory · session restored",
                    f"{count} prior events for this conversation loaded by the session manager", {
                        "category": "memory_short",
                        "component": "Bedrock AgentCore Memory",
                        "status": "ok",
                        "fields": [
                            {"label": "memory_id", "value": MEMORY_ID, "mono": True},
                            {"label": "actor_id", "value": traveler_id, "mono": True},
                            {"label": "session_id", "value": conversation_id, "mono": True},
                            {"label": "events", "value": str(count)},
                            {"label": "namespace", "value": SESSION_NAMESPACE, "mono": True},
                        ]})


async def run(payload: dict):
    started = time.monotonic()
    traveler_id = str(payload.get("traveler_id") or "trv_meridian_demo")
    conversation_id = str(payload.get("conversation_id") or "conv-unknown")
    hold_target = payload.get("hold_target") or None
    turn = TurnContext(
        traveler_id=traveler_id,
        conversation_id=conversation_id,
        hold_confirmed=bool(payload.get("hold_confirmed")) and hold_target is not None,
        budget_ceiling_cents=int(payload.get("budget_ceiling_cents") or 0),
        gateway_id=GATEWAY_ID,
        policy_engine_id=POLICY_ENGINE_ID,
    )
    queue: asyncio.Queue = asyncio.Queue()
    hooks = TraceHooks(queue, turn)
    gateway = MCPClient(url=GATEWAY_URL, auth_provider=GatewaySigV4(SESSION, REGION))
    yield {"type": "activity", **activity("runtime", "AgentCore Runtime · turn started",
           f"microVM session for {conversation_id}", {
               "category": "runtime", "component": "Bedrock AgentCore Runtime · MeridianConcierge",
               "status": "ok", "fields": [
                   {"label": "trace_id", "value": trace_id() or "pending", "mono": True},
                   {"label": "model", "value": MODEL_ID, "mono": True},
                   {"label": "hold_confirmed", "value": str(turn.hold_confirmed).lower()},
               ]})}
    with gateway:
        tools = gateway.list_tools_sync()
        yield {"type": "activity", **activity("tool_call", "AgentCore Gateway · tools/list",
               f"{len(tools)} MCP tools discovered with IAM-signed requests", {
                   "category": "gateway", "component": "Bedrock AgentCore Gateway", "status": "ok",
                   "fields": [
                       {"label": "endpoint", "value": GATEWAY_URL, "mono": True},
                       {"label": "tools", "value": ", ".join(t.tool_name for t in tools)},
                       {"label": "policy_engine", "value": POLICY_ENGINE_ID or "none", "mono": True},
                   ]})}
        yield {"type": "activity", **memory_span(traveler_id, conversation_id)}
        agent = Agent(
            model=BedrockModel(model_id=MODEL_ID, region_name=REGION, max_tokens=1500),
            system_prompt=system_prompt(turn.hold_confirmed, hold_target),
            tools=tools,
            hooks=[hooks],
            session_manager=memory_manager(traveler_id, conversation_id),
            callback_handler=None,
        )
        prompt = turn_prompt(str(payload.get("prompt", "")), str(payload.get("memory_context", "")),
                             hold_target, turn.hold_confirmed)
        task = asyncio.create_task(drive(agent, prompt, queue))
        answer, usage = "", {}
        while True:
            kind, item = await queue.get()
            if kind == "activity":
                yield {"type": "activity", **item}
            elif kind == "packages":
                yield {"type": "packages", "packages": item}
            elif kind == "hold":
                yield {"type": "hold", **item}
            elif kind == "part":
                data = item.get("data")
                if data:
                    answer += data
                    yield {"type": "token", "text": data}
                elif "result" in item:
                    usage = dict(item["result"].metrics.accumulated_usage)
            elif kind == "error":
                yield {"type": "error", "message": str(item)[:600]}
                await task
                return
            elif kind == "end":
                break
        await task
    if not answer.strip():
        answer = "I could not produce a grounded recommendation from the live options."
    yield {"type": "result", "message": answer.strip(),
           "recommended_package_ids": [p.get("package_id") for p in hooks.packages if p.get("package_id")],
           "follow_ups": FOLLOW_UPS, "hold": hooks.hold, "trace_id": trace_id(), "usage": usage,
           "elapsed_ms": round((time.monotonic() - started) * 1000)}


@app.entrypoint
async def invoke(payload, context=None):
    if payload.get("event") != "concierge_turn":
        yield json.dumps({"type": "error", "message": "Unsupported Meridian Runtime event."})
        return
    async for item in run(payload):
        yield json.dumps(item, ensure_ascii=False)


if __name__ == "__main__":
    app.run()
```

- [ ] **Step 5: Run the prompt tests and a syntax check of main.py**

Run: `cd meridian && venv/bin/python -m pytest tests/test_runtime_prompts.py -q && venv/bin/python -m py_compile meridian_agentcore/app/MeridianConcierge/main.py`
Expected: 2 passed, no compile error.

- [ ] **Step 6: Commit**

```bash
git add meridian/meridian_agentcore/app/MeridianConcierge meridian/tests/test_runtime_prompts.py
git commit -m "Give the runtime the tool loop, memory session and SSE spans"
```

---

### Task 5: The MeridianHolds gateway Lambda

**Files:**
- Create: `meridian/meridian_agentcore/agentcore/gateway_targets/meridian_holds/pyproject.toml`
- Create: `meridian/meridian_agentcore/agentcore/gateway_targets/meridian_holds/lambda_function.py`
- Create: `meridian/meridian_agentcore/agentcore/gateway_targets/meridian_holds/tool-schema.json` (documentation copy of the inline `toolDefinitions`)
- Test: `meridian/tests/test_holds_lambda.py`

**Interfaces:**
- Consumes: SSM parameters `/meridian/aurora/cluster_arn`, `/meridian/aurora/secret_arn`, `/meridian/aurora/database`; the `create_courtesy_hold` SQL function (migration 008); `traveler_identity_bindings`, `traveler_access_audit`, `journeys`, `journey_threads`.
- Produces tools `get_package_details(packageId)` → `{package, summary}` and `create_courtesy_hold(travelerId, packageId, duration, travelers, unitPriceCents, totalCents, holdMinutes, travelerConfirmed, budgetCeilingCents, journeyRef)` → `{hold, governance, summary}` or `{error}`.
- Handler: `lambda_handler(event, context)`; tool name from `context.client_context.custom["bedrockAgentCoreToolName"]`.

- [ ] **Step 1: Write the failing tests**

```python
"""The holds Lambda authorizes its own role, steps down to meridian_app, and holds atomically."""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

TARGET = (Path(__file__).resolve().parents[1] / "meridian_agentcore" / "agentcore"
          / "gateway_targets" / "meridian_holds")
sys.path.insert(0, str(TARGET))

import lambda_function as holds  # noqa: E402


class FakeDataApi:
    def __init__(self, rows_by_marker):
        self.rows_by_marker = rows_by_marker
        self.statements = []
        self.tx = []

    def begin_transaction(self, **kwargs):
        self.tx.append("begin")
        return {"transactionId": "tx-1"}

    def commit_transaction(self, **kwargs):
        self.tx.append("commit")
        return {"transactionStatus": "Transaction Committed"}

    def rollback_transaction(self, **kwargs):
        self.tx.append("rollback")
        return {}

    def execute_statement(self, **kwargs):
        self.statements.append(kwargs["sql"])
        for marker, rows in self.rows_by_marker.items():
            if marker in kwargs["sql"]:
                return {"formattedRecords": json.dumps(rows)}
        return {"formattedRecords": "[]"}


@pytest.fixture
def config(monkeypatch):
    monkeypatch.setattr(holds, "CONFIG", holds.AuroraConfig("arn:cluster", "arn:secret", "meridian"))
    monkeypatch.setattr(holds, "SUBJECT", ("aws_iam", "AROAEXAMPLE", "arn:aws:sts::1:assumed-role/x/y"))


def _context(tool):
    return SimpleNamespace(client_context=SimpleNamespace(custom={"bedrockAgentCoreToolName": tool}))


def test_get_package_details_reads_availability(config, monkeypatch):
    api = FakeDataApi({"FROM trip_packages": [{"package_id": "CTY-002", "name": "Tokyo",
                                                "durations": '["7 nights"]', "availability": '{"7 nights": 4}',
                                                "highlights": '["Sushi"]', "price_per_person": 2500.0}]})
    monkeypatch.setattr(holds, "RDS", api)
    result = holds.lambda_handler({"packageId": "CTY-002"}, _context("MeridianHolds___get_package_details"))
    assert result["package"]["availability"] == {"7 nights": 4}
    assert "Tokyo" in result["summary"]


def test_hold_refuses_when_the_workload_has_no_grant(config, monkeypatch):
    api = FakeDataApi({"FROM traveler_identity_bindings": []})
    monkeypatch.setattr(holds, "RDS", api)
    result = holds.lambda_handler(_hold_args(), _context("MeridianHolds___create_courtesy_hold"))
    assert result["error"] == "traveler_not_authorized"
    assert any("traveler_access_audit" in sql and "'deny'" in sql for sql in api.statements)
    assert api.tx[-1] == "rollback"


def test_hold_sets_scope_steps_down_and_returns_the_row(config, monkeypatch):
    api = FakeDataApi({
        "FROM traveler_identity_bindings": [{"binding_id": "bind_1"}],
        "FROM journey_threads": [{"journey_id": "jrn_1"}],
        "FROM create_courtesy_hold": [{"booking_id": "HLD-1", "status": "held", "replayed": False,
                                       "seats_available": 4, "seats_reserved": 2, "seats_remaining": 2}],
    })
    monkeypatch.setattr(holds, "RDS", api)
    result = holds.lambda_handler(_hold_args(), _context("MeridianHolds___create_courtesy_hold"))
    assert result["hold"]["bookingId"] == "HLD-1"
    assert result["hold"]["seatsRemaining"] == 2
    assert result["governance"]["decision"] == "allow"
    joined = "\n".join(api.statements)
    assert "set_config('app.current_traveler_id'" in joined
    assert "SET LOCAL ROLE meridian_app" in joined
    assert api.tx == ["begin", "commit"]


def test_hold_reports_inventory_errors_by_name(config, monkeypatch):
    class Failing(FakeDataApi):
        def execute_statement(self, **kwargs):
            if "FROM create_courtesy_hold" in kwargs["sql"]:
                raise RuntimeError("ERROR: insufficient_inventory")
            return super().execute_statement(**kwargs)

    api = Failing({"FROM traveler_identity_bindings": [{"binding_id": "b"}],
                   "FROM journey_threads": [{"journey_id": "jrn_1"}]})
    monkeypatch.setattr(holds, "RDS", api)
    result = holds.lambda_handler(_hold_args(), _context("MeridianHolds___create_courtesy_hold"))
    assert result["error"] == "insufficient_inventory"
    assert api.tx[-1] == "rollback"


def test_unknown_tool_is_refused(config):
    with pytest.raises(ValueError):
        holds.lambda_handler({}, _context("MeridianHolds___delete_everything"))


def test_fingerprint_matches_the_backend_canonical_form():
    terms = holds.normalize_terms("cty-002 ", " 7  Nights", 2, Decimal("2500.00"))
    assert terms == {"package_id": "cty-002", "duration": "7 nights", "quantity": 2,
                     "unit_price": "2500.00", "total_amount": "5000.00"}


def _hold_args():
    return {"travelerId": "trv_meridian_demo", "packageId": "CTY-002", "duration": "7 nights",
            "travelers": 2, "unitPriceCents": 250000, "totalCents": 500000, "holdMinutes": 720,
            "travelerConfirmed": True, "budgetCeilingCents": 700000, "journeyRef": "concierge:conv-1"}
```

- [ ] **Step 2: Run to verify failure**

Run: `cd meridian && venv/bin/python -m pytest tests/test_holds_lambda.py -q`
Expected: FAIL, `No module named 'lambda_function'`.

- [ ] **Step 3: Write `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "meridian-holds"
version = "0.1.0"
description = "AgentCore Gateway target for Meridian package details and courtesy holds"
requires-python = ">=3.13"
dependencies = []

[tool.hatch.build.targets.wheel]
packages = ["."]
```

- [ ] **Step 4: Write `lambda_function.py`**

```python
"""Gateway target: get_package_details and create_courtesy_hold.

The gateway passes tool arguments as the event and the tool name in the client
context. Before any traveler-scoped write, this function authorizes its own
execution role against traveler_identity_bindings, records the decision in
traveler_access_audit, pins the RLS scope and steps down to meridian_app, all
inside one Data API transaction. The hold itself is the SQL function
create_courtesy_hold from migration 008, so a retried tool call replays the
same booking instead of taking a second one.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import boto3

PARAMETERS = ("/meridian/aurora/cluster_arn", "/meridian/aurora/secret_arn", "/meridian/aurora/database")
APP_ROLE = "meridian_app"
AGENT_TYPE = "concierge_agent"
HOLD_BACKEND = "gateway"
PACKAGE_SQL = (
    "SELECT package_id, name, operator, destination, region, price_per_person, "
    "durations, availability, highlights FROM trip_packages WHERE package_id = :package_id"
)


@dataclass(frozen=True)
class AuroraConfig:
    cluster_arn: str
    secret_arn: str
    database: str


def _load_config() -> AuroraConfig:
    ssm = boto3.client("ssm")
    response = ssm.get_parameters(Names=list(PARAMETERS))
    values = {p["Name"]: p["Value"] for p in response.get("Parameters", [])}
    missing = [name for name in PARAMETERS if name not in values]
    if missing:
        raise RuntimeError(f"Missing SSM parameters {missing}; run scripts/publish_gateway_parameters.py")
    return AuroraConfig(values[PARAMETERS[0]], values[PARAMETERS[1]], values[PARAMETERS[2]])


def _load_subject() -> tuple[str, str, str]:
    caller = boto3.client("sts").get_caller_identity()
    return "aws_iam", caller.get("UserId", "").split(":", 1)[0], caller.get("Arn", "unknown")


CONFIG = _load_config()
SUBJECT = _load_subject()
RDS = boto3.client("rds-data")


def _parameters(values: dict) -> list[dict]:
    params = []
    for key, value in values.items():
        if isinstance(value, bool):
            field = {"booleanValue": value}
        elif isinstance(value, int):
            field = {"longValue": value}
        elif isinstance(value, Decimal):
            field = {"stringValue": str(value)}
        else:
            field = {"stringValue": str(value)}
        params.append({"name": key, "value": field})
    return params


def query(sql: str, values: dict | None = None, tx: str | None = None) -> list[dict]:
    kwargs = {"resourceArn": CONFIG.cluster_arn, "secretArn": CONFIG.secret_arn,
              "database": CONFIG.database, "sql": sql, "formatRecordsAs": "JSON",
              "parameters": _parameters(values or {})}
    if tx:
        kwargs["transactionId"] = tx
    response = RDS.execute_statement(**kwargs)
    rows = json.loads(response.get("formattedRecords") or "[]")
    for row in rows:
        for key in ("durations", "availability", "highlights"):
            if isinstance(row.get(key), str):
                row[key] = json.loads(row[key])
    return rows


def normalize_terms(package_id: str, duration: str, quantity: int, unit_price: Decimal) -> dict:
    """The same canonical form as backend/agents/orchestration_05/hold_intent.py."""
    price = Decimal(unit_price).quantize(Decimal("0.01"))
    return {
        "package_id": package_id.strip().lower(),
        "duration": " ".join(duration.split()).lower(),
        "quantity": int(quantity),
        "unit_price": str(price),
        "total_amount": str(price * int(quantity)),
    }


def fingerprint(terms: dict) -> str:
    canonical = json.dumps(terms, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def get_package_details(args: dict) -> dict:
    package_id = str(args.get("packageId", "")).strip()
    rows = query(PACKAGE_SQL, {"package_id": package_id})
    if not rows:
        return {"error": f"Unknown package {package_id}"}
    package = rows[0]
    availability = package.get("availability") or {}
    open_slots = ", ".join(f"{k}: {v} places" for k, v in availability.items()) or "no published durations"
    return {"package": package, "summary": f"{package.get('name')} · {open_slots}"}


def _authorize(traveler_id: str, tx: str) -> dict:
    provider, subject_id, principal = SUBJECT
    binding = query(
        "SELECT binding_id FROM traveler_identity_bindings WHERE identity_provider = :provider "
        "AND subject_id = :subject AND traveler_id = :traveler AND status = 'active' "
        "AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP) LIMIT 1",
        {"provider": provider, "subject": subject_id, "traveler": traveler_id}, tx)
    allowed = bool(binding)
    decision = "allow" if allowed else "deny"
    query(
        "INSERT INTO traveler_access_audit (audit_id, identity_provider, subject_id, principal, "
        "requested_traveler_id, decision, reason) VALUES (:audit_id, :provider, :subject, "
        f":principal, :traveler, '{decision}', :reason)",
        {"audit_id": f"authz_{uuid.uuid4().hex[:12]}", "provider": provider, "subject": subject_id,
         "principal": principal, "traveler": traveler_id,
         "reason": "active identity binding" if allowed else "no active identity binding"}, tx)
    return {"allowed": allowed, "decision": decision, "subject": subject_id, "principal": principal}


def _scope(traveler_id: str, tx: str) -> None:
    query("SELECT set_config('app.current_traveler_id', :traveler, true)", {"traveler": traveler_id}, tx)
    query("SELECT set_config('app.agent_type', :agent, true)", {"agent": AGENT_TYPE}, tx)
    query(f"SET LOCAL ROLE {APP_ROLE}", None, tx)


def _journey(traveler_id: str, journey_ref: str, tx: str) -> str:
    query("SELECT pg_advisory_xact_lock(hashtextextended(:thread, 0))", {"thread": journey_ref}, tx)
    rows = query("SELECT journey_id FROM journey_threads WHERE thread_id = :thread", {"thread": journey_ref}, tx)
    if rows:
        return str(rows[0]["journey_id"])
    journey_id = f"jrn_{uuid.uuid4().hex[:12]}"
    query("INSERT INTO journeys (journey_id, traveler_id, checkpoint_backend) VALUES (:j, :t, :b)",
          {"j": journey_id, "t": traveler_id, "b": HOLD_BACKEND}, tx)
    query("INSERT INTO journey_threads (thread_id, journey_id) VALUES (:thread, :j) "
          "ON CONFLICT (thread_id) DO NOTHING", {"thread": journey_ref, "j": journey_id}, tx)
    query("UPDATE journeys SET active_thread_id = :thread, updated_at = CURRENT_TIMESTAMP "
          "WHERE journey_id = :j", {"thread": journey_ref, "j": journey_id}, tx)
    return journey_id


def _hold_terms(args: dict) -> dict:
    unit_price = Decimal(int(args["unitPriceCents"])) / Decimal(100)
    terms = normalize_terms(str(args["packageId"]), str(args["duration"]), int(args["travelers"]), unit_price)
    digest = hashlib.sha256(
        f"{args['journeyRef']}|{terms['package_id']}|{terms['duration']}|{terms['quantity']}".encode()
    ).hexdigest()[:12]
    return {"terms": terms, "unit_price": unit_price, "request_id": f"hrq_{digest}",
            "fingerprint": fingerprint(terms), "booking_id": f"HLD-{uuid.uuid4().hex[:8].upper()}"}


def create_courtesy_hold(args: dict) -> dict:
    traveler_id = str(args["travelerId"])
    hold = _hold_terms(args)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=int(args["holdMinutes"]))
    tx = RDS.begin_transaction(resourceArn=CONFIG.cluster_arn, secretArn=CONFIG.secret_arn,
                               database=CONFIG.database)["transactionId"]
    try:
        governance = _authorize(traveler_id, tx)
        if not governance["allowed"]:
            RDS.commit_transaction(resourceArn=CONFIG.cluster_arn, secretArn=CONFIG.secret_arn, transactionId=tx)
            return {"error": "traveler_not_authorized", "governance": governance}
        _scope(traveler_id, tx)
        journey_id = _journey(traveler_id, str(args["journeyRef"]), tx)
        rows = query(
            "SELECT booking_id, status, replayed, seats_available, seats_reserved, seats_remaining "
            "FROM create_courtesy_hold(:booking_id::TEXT, :traveler::TEXT, :journey::TEXT, "
            ":request_id::TEXT, :fingerprint::TEXT, :package_id::TEXT, :duration::TEXT, "
            ":quantity::INTEGER, :unit_price::NUMERIC, :total::NUMERIC, :expires_at::TIMESTAMPTZ)",
            {"booking_id": hold["booking_id"], "traveler": traveler_id, "journey": journey_id,
             "request_id": hold["request_id"], "fingerprint": hold["fingerprint"],
             "package_id": str(args["packageId"]).strip(), "duration": " ".join(str(args["duration"]).split()),
             "quantity": int(args["travelers"]), "unit_price": hold["unit_price"],
             "total": Decimal(hold["terms"]["total_amount"]), "expires_at": expires_at.isoformat()}, tx)
        RDS.commit_transaction(resourceArn=CONFIG.cluster_arn, secretArn=CONFIG.secret_arn, transactionId=tx)
    except Exception as error:  # noqa: BLE001 - the SQL function raises named business errors
        RDS.rollback_transaction(resourceArn=CONFIG.cluster_arn, secretArn=CONFIG.secret_arn, transactionId=tx)
        return {"error": _named_error(error)}
    row = rows[0]
    result = {"bookingId": row["booking_id"], "status": row["status"], "replayed": bool(row["replayed"]),
              "journeyId": journey_id, "holdRequestId": hold["request_id"], "expiresAt": expires_at.isoformat(),
              "seatsAvailable": row.get("seats_available"), "seatsRemaining": row.get("seats_remaining"),
              "totalAmount": hold["terms"]["total_amount"], "packageId": str(args["packageId"]).strip(),
              "duration": " ".join(str(args["duration"]).split()), "travelers": int(args["travelers"])}
    verb = "Replayed" if result["replayed"] else "Held"
    return {"hold": result, "governance": governance,
            "summary": f"{verb} {result['packageId']} ({result['duration']}) for {result['travelers']} "
                       f"traveler(s) as {result['bookingId']}, expires {expires_at.strftime('%H:%M UTC')}"}


def _named_error(error: Exception) -> str:
    text = str(error)
    for name in ("insufficient_inventory", "invalid_package_inventory", "journey_not_owned",
                 "traveler_scope_mismatch", "booking_agent_not_authorized", "invalid_hold_quantity",
                 "hold_request_parameter_mismatch"):
        if name in text:
            return name
    return text[:300]


TOOLS = {"get_package_details": get_package_details, "create_courtesy_hold": create_courtesy_hold}


def lambda_handler(event, context):
    custom = getattr(getattr(context, "client_context", None), "custom", None) or {}
    tool = custom.get("bedrockAgentCoreToolName", "")
    name = tool.split("___")[-1]
    if name not in TOOLS:
        raise ValueError(f"Unknown Meridian tool: {tool or '(none)'}")
    result = TOOLS[name](event or {})
    print(json.dumps({"meridian_tool": name, "ok": "error" not in result,
                      "gatewayRequestId": custom.get("bedrockAgentCoreAwsRequestId")}), flush=True)
    return result
```

Note for the test: module import runs `_load_config()` and `_load_subject()`. Make both lazy behind a `_boot()` called from `lambda_handler` when `CONFIG is None`, and let the test fixture set `CONFIG`/`SUBJECT` directly. Adjust the module so `CONFIG: AuroraConfig | None = None`, `SUBJECT: tuple | None = None`, `RDS = None`, and:

```python
def _boot() -> None:
    global CONFIG, SUBJECT, RDS
    if CONFIG is None:
        CONFIG = _load_config()
    if SUBJECT is None:
        SUBJECT = _load_subject()
    if RDS is None:
        RDS = boto3.client("rds-data")
```

with `_boot()` as the first line of `lambda_handler`. The tests set all three, so `_boot()` is a no-op there.

- [ ] **Step 5: Write `tool-schema.json`** (kept beside the code; the same definitions go inline into `agentcore.json` in Task 7)

```json
[
  {
    "name": "get_package_details",
    "description": "Read one Meridian trip package with its live durations, availability and highlights.",
    "inputSchema": {
      "type": "object",
      "properties": {"packageId": {"type": "string", "description": "Catalog package id, for example CTY-002."}},
      "required": ["packageId"],
      "additionalProperties": false
    }
  },
  {
    "name": "create_courtesy_hold",
    "description": "Place a courtesy hold on one package duration for the authorized traveler. No payment. Cedar policy decides on the arguments before this runs.",
    "inputSchema": {
      "type": "object",
      "properties": {
        "travelerId": {"type": "string"},
        "packageId": {"type": "string"},
        "duration": {"type": "string", "description": "Duration label exactly as the catalog publishes it."},
        "travelers": {"type": "integer", "minimum": 1, "maximum": 12},
        "unitPriceCents": {"type": "integer", "description": "Price per traveler in cents from the catalog."},
        "totalCents": {"type": "integer", "description": "unitPriceCents multiplied by travelers."},
        "holdMinutes": {"type": "integer", "minimum": 1, "maximum": 720},
        "travelerConfirmed": {"type": "boolean", "description": "Set by the platform from the traveler's confirmation, never by the model."},
        "budgetCeilingCents": {"type": "integer", "description": "The traveler's saved budget ceiling in cents, set by the platform."},
        "journeyRef": {"type": "string", "description": "Stable journey reference for replay, set by the platform."}
      },
      "required": ["travelerId", "packageId", "duration", "travelers", "unitPriceCents", "totalCents", "holdMinutes", "travelerConfirmed", "budgetCeilingCents", "journeyRef"],
      "additionalProperties": false
    }
  }
]
```

- [ ] **Step 6: Run the tests**

Run: `cd meridian && venv/bin/python -m pytest tests/test_holds_lambda.py -q`
Expected: 6 passed.

- [ ] **Step 7: Commit**

```bash
git add meridian/meridian_agentcore/agentcore/gateway_targets/meridian_holds meridian/tests/test_holds_lambda.py
git commit -m "Add the MeridianHolds gateway Lambda with the identity chain"
```

---

### Task 6: Provisioning scripts for parameters and the Lambda workload grant

**Files:**
- Create: `meridian/scripts/publish_gateway_parameters.py`
- Create: `meridian/scripts/bind_gateway_workload.py`
- Modify: `meridian/scripts/verify_agentcore.py` (policy engine, tools, observability checks)
- Test: `meridian/tests/test_gateway_provisioning_scripts.py`

**Interfaces:**
- `publish_gateway_parameters.parameters_from_env(env: dict) -> dict[str, str]` maps `AURORA_CLUSTER_ARN`, `AURORA_SECRET_ARN`, `AURORA_DATABASE` to the three SSM names; `main()` calls `ssm.put_parameter(Overwrite=True)`.
- `bind_gateway_workload.role_subject(iam, role_arn) -> tuple[str, str]` returns `(RoleId, role_arn)`; `main()` reads the deployed Lambda's role through `lambda:GetFunctionConfiguration` on `meridianv2-MeridianHolds`, then reuses `bind_current_identity.bind(db, provider="aws_iam", subject_id=RoleId, principal=role_arn)`.
- `verify_agentcore.py` adds rows: Policy engine (GetPolicyEngine status + gateway `policyEngineConfiguration.mode`), Gateway tools (MCP `tools/list` count, expects 3), Runtime observability (`AGENT_OBSERVABILITY_ENABLED` in `environmentVariables`).

- [ ] **Step 1: Write the failing tests**

```python
"""Provisioning helpers publish parameters and bind the holds Lambda role to Alex."""

from __future__ import annotations

from unittest.mock import MagicMock

from scripts.bind_gateway_workload import role_subject
from scripts.publish_gateway_parameters import parameters_from_env


def test_parameters_map_the_env_to_ssm_names():
    params = parameters_from_env({"AURORA_CLUSTER_ARN": "arn:c", "AURORA_SECRET_ARN": "arn:s",
                                  "AURORA_DATABASE": "meridian"})
    assert params == {"/meridian/aurora/cluster_arn": "arn:c", "/meridian/aurora/secret_arn": "arn:s",
                      "/meridian/aurora/database": "meridian"}


def test_missing_env_is_an_error():
    try:
        parameters_from_env({"AURORA_CLUSTER_ARN": "arn:c"})
    except SystemExit as error:
        assert "AURORA_SECRET_ARN" in str(error)
    else:
        raise AssertionError("expected SystemExit")


def test_role_subject_is_the_stable_role_id():
    iam = MagicMock()
    iam.get_role.return_value = {"Role": {"RoleId": "AROAEXAMPLE", "Arn": "arn:aws:iam::1:role/holds"}}
    assert role_subject(iam, "arn:aws:iam::1:role/holds") == ("AROAEXAMPLE", "arn:aws:iam::1:role/holds")
    iam.get_role.assert_called_once_with(RoleName="holds")
```

- [ ] **Step 2: Run to verify failure**

Run: `cd meridian && venv/bin/python -m pytest tests/test_gateway_provisioning_scripts.py -q`
Expected: FAIL, import errors.

- [ ] **Step 3: Write `publish_gateway_parameters.py`**

```python
#!/usr/bin/env python3
"""Publish the Aurora connection settings the MeridianHolds Lambda reads from SSM.

The CDK-built gateway Lambda has no environment variables of its own, so the
cluster ARN, the secret ARN and the database name live in Parameter Store under
/meridian/aurora/. Values come from meridian/.env, the same file the backend
reads. Nothing secret is published: a secret ARN is a pointer, not a secret.

Usage:
    cd meridian
    python scripts/publish_gateway_parameters.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import boto3
from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PARAMETERS = {
    "/meridian/aurora/cluster_arn": "AURORA_CLUSTER_ARN",
    "/meridian/aurora/secret_arn": "AURORA_SECRET_ARN",
    "/meridian/aurora/database": "AURORA_DATABASE",
}


def parameters_from_env(env: dict) -> dict[str, str]:
    """Map the backend environment to SSM parameter names, failing on any gap."""
    missing = [key for key in PARAMETERS.values() if not (env.get(key) or "").strip()]
    if missing:
        raise SystemExit(f"Missing in meridian/.env: {', '.join(missing)}")
    return {name: env[key].strip() for name, key in PARAMETERS.items()}


def main() -> None:
    env = {**dotenv_values(Path(__file__).resolve().parents[1] / ".env"), **os.environ}
    region = env.get("AWS_DEFAULT_REGION", "us-east-1")
    ssm = boto3.client("ssm", region_name=region)
    for name, value in parameters_from_env(env).items():
        ssm.put_parameter(Name=name, Value=value, Type="String", Overwrite=True,
                          Description="Meridian gateway Lambda configuration")
        print(f"put {name}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Write `bind_gateway_workload.py`**

```python
#!/usr/bin/env python3
"""Grant the MeridianHolds Lambda's execution role access to Alex's traveler record.

The gateway Lambda is a workload like the FastAPI backend: before it sets a
traveler scope it must hold an active row in traveler_identity_bindings. The
subject is the role's stable RoleId, which is what sts:GetCallerIdentity returns
as the first part of UserId inside the function.

Usage:
    cd meridian
    python scripts/bind_gateway_workload.py [--function meridianv2-MeridianHolds]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import boto3
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.bind_current_identity import bind  # noqa: E402

load_dotenv()
DEFAULT_FUNCTION = "meridianv2-MeridianHolds"


def role_subject(iam, role_arn: str) -> tuple[str, str]:
    """Return (RoleId, role ARN) for the role behind a Lambda function."""
    role = iam.get_role(RoleName=role_arn.rsplit("/", 1)[-1])["Role"]
    return role["RoleId"], role["Arn"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--function", default=DEFAULT_FUNCTION)
    args = parser.parse_args()
    region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
    role_arn = boto3.client("lambda", region_name=region).get_function_configuration(
        FunctionName=args.function)["Role"]
    subject_id, principal = role_subject(boto3.client("iam"), role_arn)
    bind(boto3.client("rds-data", region_name=region), provider="aws_iam",
         subject_id=subject_id, principal=principal)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Extend `verify_agentcore.py`**

Read the file fully first. Add three checks after the existing Memory row, keeping its table style:

```python
def check_policy_engine(control, gateway_id: str) -> tuple[str, str]:
    """Return (status, detail) for the gateway's policy engine association."""
    gateway = control.get_gateway(gatewayIdentifier=gateway_id)
    config = gateway.get("policyEngineConfiguration") or {}
    engine_arn = config.get("policyEngineArn") or ""
    if not engine_arn:
        return "MISSING", "gateway has no policy engine"
    engine = control.get_policy_engine(policyEngineId=engine_arn.rsplit("/", 1)[-1])
    return engine.get("status", "UNKNOWN"), f"{config.get('mode')} · {engine_arn.rsplit('/', 1)[-1]}"


def check_gateway_tools(gateway) -> tuple[str, str]:
    tools, _ = gateway.list_tools()
    names = sorted(t["name"] for t in tools)
    return ("READY" if len(names) >= 3 else "PARTIAL"), ", ".join(names)


def check_observability(runtime: dict) -> tuple[str, str]:
    env = runtime.get("environmentVariables") or {}
    enabled = env.get("AGENT_OBSERVABILITY_ENABLED") == "true"
    return ("READY" if enabled else "OFF"), "ADOT spans to CloudWatch" if enabled else "AGENT_OBSERVABILITY_ENABLED unset"
```

Wire them into the table and the exit code (all READY/ACTIVE/ENFORCE required for exit 0).

- [ ] **Step 6: Run the tests and ruff**

Run: `cd meridian && venv/bin/python -m pytest tests/test_gateway_provisioning_scripts.py -q && venv/bin/python -m ruff check scripts tests`
Expected: 3 passed, ruff clean.

- [ ] **Step 7: Commit**

```bash
git add meridian/scripts/publish_gateway_parameters.py meridian/scripts/bind_gateway_workload.py meridian/scripts/verify_agentcore.py meridian/tests/test_gateway_provisioning_scripts.py
git commit -m "Add gateway parameter, workload grant and policy verification scripts"
```

---

### Task 7: Declare the target, runtime environment, policy engine and Cedar in `agentcore.json`

**Files:**
- Modify: `meridian/meridian_agentcore/agentcore/agentcore.json`
- Modify: `meridian/meridian_agentcore/agentcore/cdk/test/cdk.test.ts`

**Interfaces:**
- Produces gateway target `MeridianHolds` (tools `MeridianHolds___get_package_details`, `MeridianHolds___create_courtesy_hold`), policy engine `MeridianGovernance`, runtime env vars.

- [ ] **Step 1: Update the CDK test expectations**

```ts
  expect(spec.agentCoreGateways[0].targets.map((t: { name: string }) => t.name)).toEqual([
    'SemanticTripSearchLambda',
    'MeridianHolds',
  ]);
  expect(spec.policyEngines).toHaveLength(1);
  expect(spec.policyEngines[0].policies).toHaveLength(3);
  expect(spec.agentCoreGateways[0].policyEngineConfiguration).toEqual({
    policyEngineName: 'MeridianGovernance',
    mode: 'ENFORCE',
  });
```

Run: `cd meridian/meridian_agentcore/agentcore/cdk && npm run build && npm test -- --runInBand 2>&1 | tail -15`
Expected: FAIL on the target list.

- [ ] **Step 2: Edit `agentcore.json`**

Runtime: add

```json
"envVars": [
  {"name": "AGENT_OBSERVABILITY_ENABLED", "value": "true"},
  {"name": "OTEL_PYTHON_DISTRO", "value": "aws_distro"},
  {"name": "OTEL_PYTHON_CONFIGURATOR", "value": "aws_configurator"},
  {"name": "OTEL_RESOURCE_ATTRIBUTES", "value": "service.name=meridian-concierge"},
  {"name": "BEDROCK_MODEL_ID", "value": "global.anthropic.claude-sonnet-5"},
  {"name": "MERIDIAN_GATEWAY_ID", "value": "meridianv2-meridian-aurora-temzt21jg0"},
  {"name": "MERIDIAN_POLICY_ENGINE_ID", "value": "MeridianGovernance"}
]
```

Gateway: append the target and the association

```json
{
  "name": "MeridianHolds",
  "targetType": "lambda",
  "toolDefinitions": [ ...the two definitions from Task 5 Step 5... ],
  "compute": {
    "host": "Lambda",
    "implementation": {"language": "Python", "path": "./agentcore/gateway_targets/meridian_holds", "handler": "lambda_function.lambda_handler"},
    "pythonVersion": "PYTHON_3_13",
    "timeout": 30,
    "memorySize": 512,
    "iamPolicy": {
      "Version": "2012-10-17",
      "Statement": [
        {"Effect": "Allow", "Action": ["ssm:GetParameters"], "Resource": "arn:aws:ssm:us-east-1:619763002613:parameter/meridian/aurora/*"},
        {"Effect": "Allow", "Action": ["rds-data:ExecuteStatement", "rds-data:BeginTransaction", "rds-data:CommitTransaction", "rds-data:RollbackTransaction"], "Resource": "arn:aws:rds:us-east-1:619763002613:cluster:meridian-demo"},
        {"Effect": "Allow", "Action": ["secretsmanager:GetSecretValue"], "Resource": "arn:aws:secretsmanager:us-east-1:619763002613:secret:meridian-demo-credentials-W0pH9X"}
      ]
    }
  }
}
```

and on the gateway object, in pass two only: `"policyEngineConfiguration": {"policyEngineName": "MeridianGovernance", "mode": "ENFORCE"}`.

Policy engine (pass two):

```json
"policyEngines": [
  {
    "name": "MeridianGovernance",
    "description": "Cedar policies over the Meridian gateway tools. Default deny; a hold needs the traveler's confirmation and must stay within budget.",
    "policies": [
      {
        "name": "meridian_read_tools",
        "description": "Any authenticated caller may search packages and read package details.",
        "validationMode": "FAIL_ON_ANY_FINDINGS",
        "statement": "permit(principal, action in [AgentCore::Action::\"SemanticTripSearchLambda___semantic_trip_search\", AgentCore::Action::\"MeridianHolds___get_package_details\"], resource == AgentCore::Gateway::\"arn:aws:bedrock-agentcore:us-east-1:619763002613:gateway/meridianv2-meridian-aurora-temzt21jg0\");"
      },
      {
        "name": "meridian_hold_requires_confirmation",
        "description": "A courtesy hold runs only after the traveler confirmed it, for at most 12 hours and at most 6 travelers.",
        "validationMode": "FAIL_ON_ANY_FINDINGS",
        "statement": "permit(principal, action == AgentCore::Action::\"MeridianHolds___create_courtesy_hold\", resource == AgentCore::Gateway::\"arn:aws:bedrock-agentcore:us-east-1:619763002613:gateway/meridianv2-meridian-aurora-temzt21jg0\") when { context.input.travelerConfirmed == true && context.input.holdMinutes <= 720 && context.input.travelers <= 6 };"
      },
      {
        "name": "meridian_hold_within_budget",
        "description": "Forbid any hold whose total exceeds the traveler's saved budget ceiling.",
        "validationMode": "FAIL_ON_ANY_FINDINGS",
        "statement": "forbid(principal, action == AgentCore::Action::\"MeridianHolds___create_courtesy_hold\", resource == AgentCore::Gateway::\"arn:aws:bedrock-agentcore:us-east-1:619763002613:gateway/meridianv2-meridian-aurora-temzt21jg0\") when { context.input.totalCents > context.input.budgetCeilingCents };"
      }
    ]
  }
]
```

- [ ] **Step 3: Validate and synth**

Run: `cd meridian/meridian_agentcore && agentcore validate --json && cd agentcore/cdk && npm run build && npm test -- --runInBand 2>&1 | tail -15`
Expected: `{"success":true}`, jest passes. If bundling of the `lambda` target fails under jest, the fallback is a `lambdaFunctionArn` target deployed by a new `scripts/deploy_holds_lambda.py` following the existing `semantic_trip_search` pattern. Record which path was taken in `docs/AGENTCORE_LEARNINGS.md`.

- [ ] **Step 4: Commit**

```bash
git add meridian/meridian_agentcore/agentcore/agentcore.json meridian/meridian_agentcore/agentcore/cdk/test/cdk.test.ts
git commit -m "Declare the MeridianHolds target, Cedar governance and runtime observability"
```

---

### Task 8: Deploy pass one (runtime, target, parameters, grant) and prove the tools

- [ ] **Step 1: Publish parameters**

Run: `cd meridian && venv/bin/python scripts/publish_gateway_parameters.py`
Expected: three `put` lines.

- [ ] **Step 2: Diff, then deploy with the policy engine and association temporarily removed**

Keep `policyEngines: []` and no `policyEngineConfiguration` for this pass (stash them in the working tree, not in a commit).
Run: `cd meridian/meridian_agentcore && agentcore deploy --diff 2>&1 | tail -60`
Check: `AWS::BedrockAgentCore::Memory` shows no replacement; runtime updates in place; new Lambda, role, target appear.
Run: `agentcore deploy -y --verbose 2>&1 | tail -40`
Expected: stack UPDATE_COMPLETE; `.cli/deployed-state.json` now lists target `MeridianHolds`.

- [ ] **Step 3: Grant the Lambda role**

Run: `cd meridian && venv/bin/python scripts/bind_gateway_workload.py`
Expected: `Authorized aws_iam:AROA… for trv_meridian_demo`.

- [ ] **Step 4: Prove the tools over MCP with SigV4 from the laptop**

Write `meridian/scripts/smoke_gateway_tools.py` (committed): uses `backend.agentcore.gateway.AgentCoreGatewayAdapter.list_tools()` then `call_tool("MeridianHolds___get_package_details", {"packageId": "CTY-002"})` and prints the availability. Expected: three tools listed, package details returned. Do not call `create_courtesy_hold` from the laptop; the laptop identity is not the Lambda's and the point of the grant is that only the Lambda role holds.

- [ ] **Step 5: Commit the deployed state that is tracked** (only `agentcore.json` if changed; `.cli/` is gitignored) and the smoke script.

---

### Task 9: Deploy pass two (policy engine, policies, ENFORCE) and prove allow and deny

- [ ] **Step 1: Restore the policy engine and association in `agentcore.json`, validate, deploy**

Run: `cd meridian/meridian_agentcore && agentcore validate --json && agentcore deploy --diff 2>&1 | tail -40 && agentcore deploy -y --verbose 2>&1 | tail -40`
Expected: policy engine ACTIVE, three policies created, gateway updated with ENFORCE. If the gateway update fails with an authorization error on the policy engine, wait 30 s and rerun `agentcore deploy -y`; the L3 grants the permissions in the same stack and IAM propagation is the only reason it fails.

- [ ] **Step 2: Verify**

Run: `cd meridian && venv/bin/python scripts/verify_agentcore.py`
Expected: Runtime READY, Gateway READY, Memory ACTIVE, Policy engine ACTIVE · ENFORCE, Gateway tools READY (3), Observability READY.

- [ ] **Step 3: Prove allow and deny through the runtime**

Write `meridian/scripts/smoke_production_turn.py` (committed): invokes the runtime directly with `accept: text/event-stream` three times on one conversation id: (a) search turn; (b) "Hold the first option now" with `hold_confirmed: false` → expect a `hold` event with `policyDecision: deny` naming `meridian_hold_requires_confirmation` or the default deny; (c) `hold_confirmed: true` with a `hold_target` from (a) and `budget_ceiling_cents: 700000` → expect `hold.status == "held"`; (d) same as (c) with `budget_ceiling_cents: 1000` → expect deny naming `meridian_hold_within_budget`. Save the events to `meridian/.local/verification/<conversation>.json` (gitignored; add `.local/` to `.gitignore` if missing). Print one line per span.

- [ ] **Step 4: Confirm spans landed**

Query CloudWatch: log group `/aws/bedrock-agentcore/runtimes/meridianv2_MeridianConcierge-LpDBbFBjsc-DEFAULT`, stream `spans`, last 15 minutes, filter the trace id printed by the smoke script. Expected: at least one span with that trace id. Record the exact console URL pattern in `docs/OPERATIONS.md`.

- [ ] **Step 5: Commit**

```bash
git add meridian/scripts/smoke_production_turn.py meridian/.gitignore
git commit -m "Prove the governed hold path end to end"
```

---

### Task 10: Backend runtime adapter streams events

**Files:**
- Rewrite: `meridian/backend/agentcore/runtime.py`
- Modify: `meridian/tests/test_agentcore_adapters.py` (runtime tests)

**Interfaces:**
- Produces `RuntimeDecision` with new fields: `activities: list[dict]`, `packages: list[dict]`, `hold: dict | None`, `hold_refused: str | None`, `policy_decision: str | None`, `trace_id: str | None`, `usage: dict`, `elapsed_ms: int`.
- `invoke_turn(conversation_id, traveler_id, prompt, memory_context, *, budget_ceiling_cents, travelers_count, hold_confirmed=False, hold_target=None) -> RuntimeDecision`.
- `parse_sse(raw: bytes) -> list[dict]` (pure, tested).

- [ ] **Step 1: Write the failing tests** (replace `test_runtime_configured_invoke_live` and `test_runtime_unwraps_agentcore_sse_json_string`)

```python
def test_parse_sse_unwraps_the_double_encoded_json_lines():
    raw = (b'data: "{\\"type\\": \\"activity\\", \\"title\\": \\"x\\"}"\n\n'
           b'data: {"type": "result", "message": "Tokyo fits.", "recommended_package_ids": ["CTY-002"], '
           b'"follow_ups": [], "hold": null, "trace_id": "abc", "usage": {}, "elapsed_ms": 12}\n\n')
    events = parse_sse(raw)
    assert [event["type"] for event in events] == ["activity", "result"]


def test_runtime_invoke_collects_spans_packages_and_hold():
    adapter = AgentCoreRuntimeAdapter(runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123:runtime/x", region="us-east-1")
    body = MagicMock()
    body.read.side_effect = [
        b'data: {"type": "activity", "id": "a1", "timestamp": "t", "activity_type": "search", "title": "s"}\n\n'
        b'data: {"type": "packages", "packages": [{"package_id": "CTY-002", "name": "Tokyo"}]}\n\n'
        b'data: {"type": "hold", "hold": {"bookingId": "HLD-1", "status": "held"}, "policyDecision": "allow"}\n\n'
        b'data: {"type": "result", "message": "Held.", "recommended_package_ids": ["CTY-002"], "follow_ups": [], '
        b'"hold": {"bookingId": "HLD-1", "status": "held"}, "trace_id": "abc", "usage": {"inputTokens": 1}, "elapsed_ms": 5}\n\n',
        b"",
    ]
    mock_client = MagicMock()
    mock_client.invoke_agent_runtime.return_value = {"response": body, "ResponseMetadata": {"RequestId": "r"}}
    adapter._client = mock_client
    decision = adapter.invoke_turn("conv-1", "trv_demo", "hold it", "ctx", budget_ceiling_cents=700000,
                                   travelers_count=2, hold_confirmed=True,
                                   hold_target={"package_id": "CTY-002", "duration": "7 nights",
                                                "travelers": 2, "unit_price_cents": 250000})
    assert decision.message == "Held."
    assert decision.packages[0]["package_id"] == "CTY-002"
    assert decision.hold["bookingId"] == "HLD-1"
    assert decision.policy_decision == "allow"
    assert decision.trace_id == "abc"
    assert [a["title"] for a in decision.activities] == ["s"]
    payload = json.loads(mock_client.invoke_agent_runtime.call_args.kwargs["payload"])
    assert payload["hold_confirmed"] is True and payload["budget_ceiling_cents"] == 700000
    assert mock_client.invoke_agent_runtime.call_args.kwargs["accept"] == "text/event-stream"


def test_runtime_error_event_raises():
    adapter = AgentCoreRuntimeAdapter(runtime_arn="arn:aws:bedrock-agentcore:us-east-1:123:runtime/x", region="us-east-1")
    body = MagicMock()
    body.read.side_effect = [b'data: {"type": "error", "message": "boom"}\n\n', b""]
    adapter._client = MagicMock()
    adapter._client.invoke_agent_runtime.return_value = {"response": body, "ResponseMetadata": {}}
    with pytest.raises(RuntimeError, match="boom"):
        adapter.invoke_turn("conv-1", "trv_demo", "x", "", budget_ceiling_cents=0, travelers_count=1)
```

- [ ] **Step 2: Run to verify failure**

Run: `cd meridian && venv/bin/python -m pytest tests/test_agentcore_adapters.py -q -k runtime`
Expected: FAIL (`parse_sse` missing, signature mismatch).

- [ ] **Step 3: Rewrite `runtime.py`**

Keep `_build_runtime_session_id`, the client factory and `AgentCoreNotConfiguredError` handling. Replace the decision dataclass and the invoke path:

```python
@dataclass
class RuntimeDecision:
    """Everything the managed runtime did on one turn."""

    runtime_arn: str
    runtime_session_id: str
    qualifier: str
    message: str
    recommended_package_ids: list[str]
    follow_ups: list[str]
    activities: list[dict[str, Any]] = field(default_factory=list)
    packages: list[dict[str, Any]] = field(default_factory=list)
    hold: Optional[dict[str, Any]] = None
    hold_refused: Optional[str] = None
    policy_decision: Optional[str] = None
    trace_id: Optional[str] = None
    usage: dict[str, Any] = field(default_factory=dict)
    elapsed_ms: int = 0


def parse_sse(raw: bytes) -> list[dict[str, Any]]:
    """Decode the runtime's SSE body into the JSON objects it yielded."""
    events: list[dict[str, Any]] = []
    for line in raw.decode("utf-8", errors="replace").splitlines():
        if not line.startswith("data:"):
            continue
        value = json.loads(line[5:].strip())
        if isinstance(value, str):
            value = json.loads(value)
        if isinstance(value, dict):
            events.append(value)
    return events


def _read_stream(response: dict[str, Any]) -> bytes:
    body = response.get("response")
    chunks = bytearray()
    if hasattr(body, "read"):
        while True:
            chunk = body.read(4096)
            if not chunk:
                break
            chunks.extend(chunk)
        return bytes(chunks)
    for chunk in body or []:
        chunks.extend(chunk if isinstance(chunk, (bytes, bytearray)) else str(chunk).encode())
    return bytes(chunks)
```

and

```python
    def invoke_turn(self, conversation_id, traveler_id, prompt, memory_context, *,
                    budget_ceiling_cents, travelers_count, hold_confirmed=False, hold_target=None):
        arn = self._require_arn()
        session_id = self._build_runtime_session_id(conversation_id, traveler_id)
        payload = json.dumps({
            "event": "concierge_turn", "traveler_id": traveler_id, "conversation_id": conversation_id,
            "prompt": prompt, "memory_context": memory_context[:6000],
            "budget_ceiling_cents": int(budget_ceiling_cents), "travelers_count": int(travelers_count),
            "hold_confirmed": bool(hold_confirmed), "hold_target": hold_target,
            "timestamp": _utc_timestamp()}).encode()
        try:
            response = self._get_client().invoke_agent_runtime(
                agentRuntimeArn=arn, runtimeSessionId=session_id, payload=payload,
                qualifier=self.qualifier, contentType="application/json", accept="text/event-stream")
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "Unknown")
            raise RuntimeError(f"AgentCore Runtime invoke failed: {code}") from exc
        return self._decision(arn, session_id, parse_sse(_read_stream(response)))

    def _decision(self, arn, session_id, events):
        decision = RuntimeDecision(runtime_arn=arn, runtime_session_id=session_id,
                                   qualifier=self.qualifier, message="", recommended_package_ids=[], follow_ups=[])
        for event in events:
            kind = event.get("type")
            if kind == "activity":
                decision.activities.append({k: v for k, v in event.items() if k != "type"})
            elif kind == "packages":
                decision.packages = list(event.get("packages") or [])
            elif kind == "hold":
                decision.hold = event.get("hold")
                decision.hold_refused = event.get("refused")
                decision.policy_decision = event.get("policyDecision")
            elif kind == "error":
                raise RuntimeError(f"AgentCore Runtime error: {event.get('message')}")
            elif kind == "result":
                decision.message = str(event.get("message") or "").strip()
                decision.recommended_package_ids = [str(v) for v in event.get("recommended_package_ids") or [] if v]
                decision.follow_ups = [str(v) for v in event.get("follow_ups") or [] if v]
                decision.trace_id = event.get("trace_id")
                decision.usage = dict(event.get("usage") or {})
                decision.elapsed_ms = int(event.get("elapsed_ms") or 0)
        if not decision.message:
            raise RuntimeError("AgentCore Runtime returned no concierge message.")
        return decision
```

- [ ] **Step 4: Run the adapter tests**

Run: `cd meridian && venv/bin/python -m pytest tests/test_agentcore_adapters.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add meridian/backend/agentcore/runtime.py meridian/tests/test_agentcore_adapters.py
git commit -m "Stream runtime events into the backend decision"
```

---

### Task 11: The concierge orchestrates around the runtime; the hold goes through it

**Files:**
- Modify: `meridian/backend/agents/production_04/concierge.py`
- Delete: `meridian/backend/agentcore/memory.py` and its tests in `tests/test_agentcore_adapters.py`; remove `semantic_trip_search`, `call_tool` and `_extract_packages_from_mcp_result` from `backend/agentcore/gateway.py` (keep `list_tools` and `call_tool` only if `smoke_gateway_tools.py` needs `call_tool`; it does, so keep `call_tool`, delete `semantic_trip_search` and the extractor).
- Modify: `meridian/backend/agentcore/cli_config.py` (`require_agentcore_platform` keeps `require_memory` for the verify script only; `process_turn` calls it with `require_memory=False`).
- Modify: `meridian/backend/routers/chat.py` (`production_search`, `process_order` phase 4 branch)
- Modify: `meridian/tests/test_production_transaction_boundaries.py`, `meridian/tests/test_order_hold.py` (phase 4 case), new `meridian/tests/test_production_hold.py`

**Interfaces:**
- `ProductionAgent.process_turn(message, traveler_id, conversation_id, limit)` keeps its return tuple `(packages, activities, message, conv_id, memory_facts)`.
- New `ProductionAgent.process_hold(traveler_id, conversation_id, target: HoldTarget) -> HoldOutcome` where `HoldTarget(package_id, duration, travelers, unit_price_cents)` and `HoldOutcome(hold: dict | None, refused: str | None, policy_decision: str | None, activities: list, message: str, conv_id: str)`.
- `budget_ceiling_cents` comes from the traveler's Aurora preferences: the fact whose key is `budget` or `budget_ceiling`, parsed for the largest dollar figure, times 100 per traveler times travelers. If absent, default `MERIDIAN_DEFAULT_BUDGET_CEILING_CENTS` (env, default 400000). Implement `budget_ceiling_from_facts(facts, travelers) -> int` in `backend/agents/production_04/budget.py` with tests.

- [ ] **Step 1: Write the failing tests**

`tests/test_production_budget.py`:

```python
from backend.agents.production_04.budget import budget_ceiling_from_facts


def test_budget_fact_with_a_range_uses_the_upper_bound_per_traveler():
    facts = [{"key": "budget", "value": "Prefers $2k-3.5k per person"}]
    assert budget_ceiling_from_facts(facts, travelers=2) == 700000


def test_no_budget_fact_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("MERIDIAN_DEFAULT_BUDGET_CEILING_CENTS", "123400")
    assert budget_ceiling_from_facts([], travelers=1) == 123400
```

`tests/test_production_hold.py`: build a `ProductionAgent` with `__new__` as in `test_production_transaction_boundaries.py`, fake `agentcore_runtime.invoke_turn` returning a `RuntimeDecision` with a held hold, and assert: the read transaction committed before `invoke_turn` ran; `invoke_turn` was called with `hold_confirmed=True` and the target; the outcome message is the runtime message; a `"result"` activity with `"Courtesy hold persisted"` is appended; the audit write happened in the write transaction. A second test with `policy_decision="deny"` asserts `outcome.hold is None` and the message contains the refusal.

Update `test_production_transaction_boundaries.py`: remove `agentcore_memory` and `_search_packages` fakes; the runtime fake returns `RuntimeDecision(..., packages=[{"package_id": "pkg-1", "name": "Tokyo Replan"}])`; the ordering assertion covers `external:runtime` only, still after `tx-1:commit` and before `tx-2:begin`.

- [ ] **Step 2: Run to verify failure**

Run: `cd meridian && venv/bin/python -m pytest tests/test_production_budget.py tests/test_production_hold.py tests/test_production_transaction_boundaries.py -q`
Expected: FAIL.

- [ ] **Step 3: Write `budget.py`**

```python
"""Derive the hold budget ceiling the gateway policy compares against."""

from __future__ import annotations

import os
import re

DEFAULT_ENV = "MERIDIAN_DEFAULT_BUDGET_CEILING_CENTS"
AMOUNT = re.compile(r"\$?\s*(\d+(?:\.\d+)?)\s*(k)?", re.I)


def _dollars(text: str) -> list[float]:
    values = []
    for number, thousands in AMOUNT.findall(text):
        value = float(number) * (1000 if thousands else 1)
        if value >= 100:
            values.append(value)
    return values


def budget_ceiling_from_facts(facts: list[dict], travelers: int) -> int:
    """Return the per-trip ceiling in cents from the traveler's saved budget fact."""
    for fact in facts:
        if str(fact.get("key", "")).lower() in ("budget", "budget_ceiling", "budget_range"):
            amounts = _dollars(str(fact.get("value", "")))
            if amounts:
                return int(round(max(amounts) * 100)) * max(1, int(travelers))
    return int(os.getenv(DEFAULT_ENV, "400000"))
```

- [ ] **Step 4: Rework `process_turn` and add `process_hold`**

In `process_turn`: keep identity, the RLS read unit, `memory_context`; delete the AgentCore Memory list/retrieve/create_event blocks and the `_search_packages` gateway call; call

```python
        decision = await asyncio.to_thread(
            self.agentcore_runtime.invoke_turn, conv_id, traveler_id, message, runtime_context,
            budget_ceiling_cents=budget_ceiling_from_facts(memory_facts, travelers_count),
            travelers_count=travelers_count)
        for span in decision.activities:
            self._forward(span)
        packages = await self._hydrate(decision.packages)
```

where `_forward(span)` wraps the runtime dict into a `MemoryActivity` via the existing callback, and `_hydrate` is the current Aurora `trip_packages` join moved out of `_search_packages`. `travelers_count` is a new keyword parameter of `process_turn` defaulting to 1; `chat.py` passes `request.travelers_count`. Then the RLS write unit persists the turn and audits as today. The runtime span for the decision replaces the old "AgentCore Runtime · concierge decision" span and gains `trace_id`, `elapsed_ms` and a CloudWatch link field `trace_console` built as `https://us-east-1.console.aws.amazon.com/cloudwatch/home?region=us-east-1#logsV2:log-groups/log-group/$252Faws$252Fbedrock-agentcore$252Fruntimes$252F<runtime-id>-DEFAULT`.

`process_hold`: identity → RLS read (profile + prefs for the budget ceiling) → `invoke_turn(..., hold_confirmed=True, hold_target=asdict(target))` → write unit: `write_audit(operation="production_hold", ...)` → return `HoldOutcome`. When `decision.policy_decision == "deny"` the message is `decision.message` and `hold` is `None`.

- [ ] **Step 5: Route the Phase 4 hold in `chat.py`**

In `process_order`, before the generic path:

```python
    if request.phase == 4:
        return await production_hold(request, traveler_id)
```

with

```python
async def production_hold(request: OrderRequest, traveler_id: str) -> OrderResponse:
    """Phase 4 hold: the runtime asks the gateway, Cedar decides, the Lambda writes."""
    from backend.agents.production_04.concierge import HoldTarget, create_production_agent

    pkg = await _package_for_hold(request.product_id)          # existing product lookup + duration choice
    duration = _requested_duration(pkg, request.size)           # existing logic extracted
    target = HoldTarget(package_id=pkg["product_id"], duration=duration, travelers=request.quantity,
                        unit_price_cents=int(round(float(pkg["price"]) * 100)))
    outcome = await create_production_agent().process_hold(traveler_id, request.conversation_id, target)
    activities = [_memory_activity_to_entry(a) for a in outcome.activities]
    if not outcome.hold:
        return OrderResponse(message=outcome.message, order=None, activities=activities)
    order = _order_from_hold(pkg, request, outcome.hold)        # builds the existing Order model
    log_order(phase=4, order_id=order.order_id, product_id=request.product_id, total=order.total, status="held")
    return OrderResponse(message=outcome.message, order=order, activities=activities)
```

`OrderRequest` gains `conversation_id: Optional[str] = None`; the frontend passes the active conversation id (Task 12).

- [ ] **Step 6: Run the suite and ruff**

Run: `cd meridian && venv/bin/python -m ruff check backend scripts tests && venv/bin/python -m pytest -m "not database" -q 2>&1 | tail -3`
Expected: ruff clean; all pass (count rises by the new tests, minus the deleted memory adapter tests).

- [ ] **Step 7: Commit**

```bash
git add -A meridian/backend meridian/tests
git commit -m "Orchestrate Phase 4 around the runtime and route the hold through it"
```

---

### Task 12: Frontend: conversation id on holds, denied status, trace link

**Files:**
- Modify: `meridian/frontend/src/types/index.ts` (`OrderRequest.conversation_id?`, `TraceSpanStatus` adds `'denied'`)
- Modify: `meridian/frontend/src/showcase/hooks/useMeridianShowcase.ts` (`holdTrip` passes `conversation_id`; on `order` null with activities, show the message as the drawer body and the notice)
- Modify: `meridian/frontend/src/showcase/components/TracePanel.tsx` and `meridianShowcase.css` (status `denied` renders with the danger color and the label "Denied by policy"; a field whose label is `trace_console` renders as a link)
- Modify: `meridian/frontend/src/showcase/lib/showcaseAdapters.ts` (Phase 4 copy) and `CapabilityBrief.tsx` (Phase 4 brief)
- Tests: `TracePanel.test.tsx`, `showcaseAdapters.test.ts`

**Interfaces:**
- Consumes `OrderResponse` with `order: null` and `activities` carrying a `denied` span.

- [ ] **Step 1: Write the failing tests**

In `showcaseAdapters.test.ts`:

```ts
it('keeps a denied policy span as denied', () => {
  const span = activityToShowcaseTraceSpan({
    id: 'a', timestamp: 't', activity_type: 'security', title: 'Hold refused by Cedar policy',
    telemetry: { category: 'security', component: 'Bedrock AgentCore Policy', status: 'denied', fields: [] },
  }, 0, 'hold it');
  expect(span.status).toBe('denied');
  expect(span.category).toBe('security');
});
```

In `TracePanel.test.tsx`: render a state with one denied span and assert the text `Denied by policy` is visible, and that a field labelled `trace_console` renders an anchor with `href` equal to its value.

- [ ] **Step 2: Run to verify failure**

Run: `cd meridian/frontend && npx vitest --run src/showcase/lib/__tests__/showcaseAdapters.test.ts src/showcase/components/__tests__/TracePanel.test.tsx 2>&1 | tail -20`

- [ ] **Step 3: Implement**

Phase 4 copy in `SHOWCASE_PHASES`:

```ts
  {
    label: 'Production',
    phase: 4,
    description: 'Runtime-owned tools, Cedar policy, traveler memory, and RLS',
    capability: 'Trust',
    takeaway: 'Authenticate the workload, authorize Alex, let the agent call governed tools, and audit every turn.',
    proofPoint: 'Cedar decision + RLS',
    adds: 'The agent runs in AgentCore Runtime, calls Aurora tools through AgentCore Gateway, and every call is checked by Cedar policy before it runs.',
    tech: 'AgentCore Runtime · Gateway · Policy · Memory · Aurora RLS',
  },
```

`CapabilityBrief` phase 4:

```ts
  4: {
    title: 'Remember the traveler. Govern the action.',
    description: 'Use saved preferences only after checking who may access them, and let policy decide every tool call.',
    route: ['Workload identity', 'Runtime + Gateway tools', 'Cedar decision', 'RLS write + audit'],
    evidence: 'Traveler authorization, Cedar allow or deny, hold row, and audit event',
    pattern: 'Workload identity identifies the agent. A traveler grant allows access. The agent calls tools through the gateway, Cedar policy sees the arguments before code runs, RLS limits the rows, and an audit record captures each decision.',
  },
```

- [ ] **Step 4: Lint, typecheck, test, build**

Run: `cd meridian/frontend && npm run lint && npx tsc --noEmit && npx vitest --run --reporter=dot 2>&1 | tail -4 && npm run build 2>&1 | tail -3`
Expected: all clean.

- [ ] **Step 5: Commit**

```bash
git add -A meridian/frontend/src
git commit -m "Show Cedar decisions and runtime traces in the Production phase"
```

---

### Task 13: Live verification in the browser

- [ ] **Step 1: Restart the backend from a fresh shell** (the running process has expired credentials)

Run: `kill 98302; cd meridian && (nohup venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 > .local/backend.log 2>&1 &) && sleep 4 && curl -s localhost:8000/health | head -c 200`
Expected: `healthy`.

- [ ] **Step 2: Run the Phase 4 ladder prompts in Chrome** at `http://localhost:5173/showcase?view=ladder`, Phase 4: "Find Tokyo trips that fit my saved preferences." then the recall prompt. Expected spans: Identity, grant, RLS read, Runtime turn started, Gateway tools/list (3 tools), Memory session restored, semantic_trip_search call and result, persist_turn, audit. Screenshot.

- [ ] **Step 3: Hold** a recommended trip with the Hold button. Expected: `create_courtesy_hold` span with `policy_engine`, result span with workload and traveler_grant fields, hold receipt with the booking id and a 12-hour expiry. Screenshot.

- [ ] **Step 4: Deny** with the chat prompt "Hold the first option now." Expected: `Hold refused by Cedar policy` span with status Denied by policy and the reply naming the Hold button. Screenshot.

- [ ] **Step 5: Check spans in CloudWatch** for the trace id shown in the Runtime span.

- [ ] **Step 6: Record the evidence** in `docs/RELEASE_REVIEW.md` (date, prompts, booking id, trace id).

---

### Task 14: Documentation

**Files:**
- Modify: `README.md` (root: Phase 4 row, tech stack, surfaces), `meridian/README.md` (Architecture tree, Governance Boundary gains the Cedar layer, API `POST /api/chat/order` phase 4 note, Configuration for `MERIDIAN_DEFAULT_BUDGET_CEILING_CENTS`), `meridian/DEMO_SCRIPT.md` (Phase 4 gains Beat 2b "the governed hold": permit, then deny), `meridian/docs/PRESENTER_GUIDE.md` (section 5 control chain becomes seven steps; Claim Boundaries adds "Cedar governs tool arguments the platform pinned; it does not authenticate the human"), `meridian/docs/OPERATIONS.md` and `meridian/docs/AGENTCORE_DEPLOY_RUNBOOK.md` (what gets deployed: two targets, policy engine; the two-pass deploy; `publish_gateway_parameters.py`; `bind_gateway_workload.py`; verify output; CloudWatch spans), `meridian/docs/AGENTCORE_LEARNINGS.md` (Cedar has no floats; `context.input` needs required args; two-pass deploy; the CDK `lambda` target has no env vars so SSM carries config; the L3 grants InvokeGateway and policy-engine permissions), `meridian/docs/CODE_WALKTHROUGH.md` (Phase 4 files: runtime `main.py` and `turn_trace.py`, the holds Lambda, `concierge.py`), `meridian/STRUCTURE.md`, `meridian/backend/agents/README.md`, `meridian/backend/agent_catalog.py` (Phase 4 skills: `semantic_trip_search`, `get_package_details`, `create_courtesy_hold` served by the gateway; `cedar_decision`), `meridian/.env.example`.
- Delete: mentions of the backend calling `tools/call` itself, `AGENTCORE_GATEWAY_SEARCH_TOOL` stays for `smoke_gateway_tools.py`.

- [ ] **Step 1: Update every file listed**, keeping each doc's existing voice. No em dashes in new copy. Every claim must match Task 13's evidence.

- [ ] **Step 2: Run the contract tests that read docs**

Run: `cd meridian && venv/bin/python -m pytest tests/test_demo_prompt_contract.py tests/test_agent_catalog.py -q`
Expected: pass.

- [ ] **Step 3: Commit and push**

```bash
git add -A
git commit -m "Document the governed runtime path for Production"
git push origin main
```

---

### Task 15: Theme review

**Files:**
- Modify: `meridian/frontend/src/showcase/MeridianDeviceShowcase.tsx` (initial theme) and `meridian/frontend/src/showcase/meridianShowcase.css` (dark ground tokens)

- [ ] **Step 1: Decide the default.** The presenter guide already recommends the light theme for projectors, and the user prefers it. Make `light` the initial theme unless `?theme=dark` is in the URL or the user has toggled before (persist the toggle in `localStorage` under `meridian.theme`, wrapped in try/catch).

- [ ] **Step 2: Reduce the blue cast in dark.** Move `--mds-app-surface` from `#031023` to `#0b1424`, `--mds-experience-panel` from `#071a30` to `#111c2e`, `--mds-experience-panel-raised` from `#0a2038` to `#16233a`, and `--mds-recovery-rail` from `#071425` to `#0e1727`. Keep the navy sidebar. Run `venv/bin/python -m pytest tests/test_theme_token_contract.py -q` and the vitest suite.

- [ ] **Step 3: Screenshot both themes** on the ladder and concierge views and attach them to the final report so the user can choose.

- [ ] **Step 4: Commit and push**

```bash
git add -A meridian/frontend/src
git commit -m "Default the showcase to the light theme and calm the dark surfaces"
git push origin main
```

---

### Task 16: Publish Meridian behind CloudFront with basic auth (added 10 Sep 2026)

**Files:**
- Create: `meridian/Dockerfile`, `meridian/.dockerignore` (backend image for App Runner, built with Finch; `uv` from PyPI because the Finch VM cannot reach ghcr.io)
- Create: `meridian/infra/` CDK app (`package.json`, `tsconfig.json`, `cdk.json`, `bin/meridian-web.ts`, `lib/meridian-web-stack.ts`, `functions/viewer-request.js`)
- Create: `meridian/scripts/publish.py`
- Modify: `meridian/.gitignore` (`infra/node_modules`, `infra/dist`, `infra/cdk.out`, `.local/`)

**Architecture:** the Vite build lives in a private S3 bucket behind CloudFront (OAC). The FastAPI backend runs as a container on App Runner (1 vCPU, 2 GB, min 1 instance) with an instance role scoped to Bedrock invoke, the Aurora Data API on one cluster, one Aurora secret, and `InvokeAgentRuntime` on the Meridian runtime. One CloudFront distribution serves both: `/api/*` and `/health` go to App Runner with a 60 s origin timeout, everything else to S3. A CloudFront Function on viewer-request enforces basic auth, injects the backend bearer token on API paths, strips it elsewhere, and rewrites SPA routes to `/index.html`; both credentials live in a CloudFront KeyValueStore, never in code or templates. The backend refuses any caller without the bearer token, so the App Runner URL is not an open door.

**Lessons recorded:** the stack must be pinned to the region of Aurora, AgentCore and the token secret (`MERIDIAN_WEB_REGION`, default `us-east-1`); the shell's default profile region was `us-west-2` and the first deploy landed there. App Runner needs the complete secret ARN for `RuntimeEnvironmentSecrets`. The publish script writes the token to Secrets Manager (never reads it), builds and deploys, then writes the KeyValueStore through the AWS CLI because the KeyValueStore data plane needs SigV4A.

- [ ] Deploy succeeds in `us-east-1`; `.local/published.json` holds the URL, user, password and token (chmod 600, gitignored).
- [ ] `curl -u` against the URL returns the showcase HTML, `/health` returns healthy, and a Phase 1 chat turn returns products through CloudFront.
- [ ] Docs: root README and `meridian/README.md` get a "Publish behind CloudFront" section; `OPERATIONS.md` Part 1 gets the publish steps and teardown (`npx cdk destroy MeridianWeb`).

### Task 17: Route the Phase 5 workflow hold through the gateway (agreed 10 Sep 2026)

**Files:**
- Modify: `meridian/meridian_agentcore/agentcore/gateway_targets/meridian_holds/lambda_function.py` and the tool definition in `agentcore.json`: optional `holdRequestId` and `executionId` arguments; when `executionId` is present the Lambda verifies the running lease inside its transaction and sets `app.execution_id`, and `holdRequestId` replaces the derived request id so a resumed workflow replays the same booking.
- Modify: `meridian/backend/agents/orchestration_05/workflow.py`: the hold node calls the gateway tool (SigV4, `backend/agentcore/gateway.py`) with the checkpointed intent instead of `create_courtesy_hold` SQL; `travelerConfirmed` comes from the traveler's recovery-desk decision the workflow checkpointed.
- Tests: `tests/test_holds_lambda.py` (lease and replay), `tests/test_phase5_workflow.py` (gateway call with the checkpointed intent), and the live `tests/test_hold_request_identity_aurora.py` stays green.

- [x] One write path for holds; Cedar sees the Phase 5 hold; the restart proof still shows one booking id and the original expiry.
