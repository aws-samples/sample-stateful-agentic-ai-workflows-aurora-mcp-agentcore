"""The real ports send the right requests, keep tokens in memory and always roll back."""

import json

import httpx
import pytest

from backend.agentcore.caller_credential import current_caller_token
from scripts.identity_probes.effects import (
    AuroraCleanup,
    AuroraPort,
    GatewayPort,
    HttpPort,
    RuntimePort,
    TokenCache,
)

BASE = "https://site.example.net"
ARN = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/MeridianWorkflow-abc123"


def tokens():
    minted = []

    def mint(user):
        minted.append(user)
        return f"token-for-{user}"

    return TokenCache(mint), minted


def test_the_token_cache_mints_each_user_once_and_warm_mints_both():
    cache, minted = tokens()

    cache.warm()
    assert cache("jordan") == "token-for-jordan" and cache("decoy") == "token-for-decoy"
    cache("jordan")

    assert sorted(minted) == ["decoy", "jordan"]


def test_http_port_sends_the_users_bearer_token_and_the_body():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url), request.headers["authorization"],
                     request.content))
        return httpx.Response(403, json={"detail": "no"})

    cache, minted = tokens()
    port = HttpPort(BASE, cache, transport=httpx.MockTransport(handler))

    status, body = port("decoy", "POST", "/api/order", {"traveler_id": "trv_x"})
    port("decoy", "GET", "/api/me", None)

    assert (status, body) == (403, {"detail": "no"})
    assert seen[0][:3] == ("POST", BASE + "/api/order", "Bearer token-for-decoy")
    assert json.loads(seen[0][3]) == {"traveler_id": "trv_x"} and minted == ["decoy"]


def test_http_port_returns_text_for_an_answer_that_is_not_json():
    port = HttpPort(BASE, tokens()[0], transport=httpx.MockTransport(
        lambda request: httpx.Response(502, text="Bad gateway")))

    assert port("jordan", "GET", "/api/me", None) == (502, "Bad gateway")


class FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data, self.closed = data, False

    def read(self, size: int) -> bytes:
        chunk, self._data = self._data[:size], self._data[size:]
        return chunk

    def close(self) -> None:
        self.closed = True


class FakeRuntimeClient:
    def __init__(self, stream: bytes) -> None:
        self.stream, self.calls, self.body = stream, [], None

    def invoke(self, *, url, token, session_id, payload):
        self.calls.append((url, token, session_id, payload))
        self.body = FakeBody(self.stream)
        return {"response": self.body}


SSE = (b'data: {"type": "heartbeat"}\n\ndata: {"type": "result", "state": {}}\n\n'
       b'data: {"type": "heartbeat"}\n\n')


def test_runtime_port_reads_events_until_the_result_and_closes_the_stream():
    client = FakeRuntimeClient(SSE)
    port = RuntimePort("us-east-1", {"workflow": ARN}, tokens()[0], client=client)

    events = port("jordan", "workflow", {"event": "workflow_turn", "mode": "ping"}, 60)

    assert [e["type"] for e in events] == ["heartbeat", "result"] and client.body.closed
    url, token, session, payload = client.calls[0]
    assert url.startswith("https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/")
    assert token == "token-for-jordan" and len(session) >= 33
    assert b"token-for" not in payload and json.loads(payload)["mode"] == "ping"


def test_runtime_port_stops_after_the_event_limit():
    client = FakeRuntimeClient(SSE)
    port = RuntimePort("us-east-1", {"concierge": ARN}, tokens()[0], client=client)

    assert len(port("jordan", "concierge", {"event": "concierge_turn"}, 1)) == 1


class FakeGateway:
    def __init__(self, outcome):
        self.outcome, self.token_seen = outcome, None

    def call_tool(self, tool, arguments):
        self.token_seen = current_caller_token()
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def test_gateway_port_binds_the_users_token_for_the_call_only():
    gateway = FakeGateway({"result": {"content": []}})
    port = GatewayPort(gateway, tokens()[0])

    assert port("decoy", "Tool___x", {}) == {"result": {"content": []}}
    assert gateway.token_seen == "token-for-decoy" and current_caller_token() is None


def test_gateway_port_turns_a_4xx_into_an_error_answer_and_reraises_a_5xx():
    refused = GatewayPort(FakeGateway(RuntimeError("Gateway HTTP 403: AccessDenied")), tokens()[0])
    broken = GatewayPort(FakeGateway(RuntimeError("Gateway HTTP 502: bad")), tokens()[0])

    assert refused("decoy", "Tool___x", {}) == {
        "error": {"http_status": 403, "message": "AccessDenied"}}
    with pytest.raises(RuntimeError, match="502"):
        broken("decoy", "Tool___x", {})


class FakeRds:
    def __init__(self, *, role="meridian_app", active=True, count=0):
        self.role, self.active, self.count = role, active, count
        self.sql, self.rolled_back, self.committed = [], [], []

    def begin_transaction(self):
        return "tx-1"

    def rollback_transaction(self, transaction_id):
        self.rolled_back.append(transaction_id)

    def commit_transaction(self, transaction_id):
        self.committed.append(transaction_id)

    async def execute(self, query, params=None, transaction_id=None):
        self.sql.append((query, params, transaction_id))
        if "current_user" in query:
            return [{"role": self.role, "active": self.active}]
        if "COUNT(*)" in query:
            return [{"n": self.count}]
        if "FROM bookings" in query:
            return [{"booking_id": "HLD-1"}, {"booking_id": "HLD-2"}]
        return []


def test_aurora_port_pins_the_context_steps_down_and_rolls_back():
    rds = FakeRds(count=0)

    visible = AuroraPort(rds).scoped_count("trv_demo_decoy", "trv_meridian_demo")

    queries = [q for q, _, _ in rds.sql]
    assert visible == 0 and rds.rolled_back == ["tx-1"] and rds.committed == []
    assert "set_config('app.current_traveler_id', %s, true)" in queries[0]
    assert rds.sql[0][1] == ("trv_demo_decoy",)
    assert queries[1] == "SET LOCAL ROLE meridian_app"
    assert rds.sql[-1][1] == ("trv_meridian_demo",)


@pytest.mark.parametrize("fake", [FakeRds(role="meridian_admin"), FakeRds(active=False)])
def test_aurora_port_refuses_to_report_a_count_it_cannot_trust(fake):
    with pytest.raises(RuntimeError, match="zero count would prove nothing"):
        AuroraPort(fake).scoped_count("trv_demo_decoy", "trv_meridian_demo")

    assert fake.rolled_back == ["tx-1"]


def test_aurora_port_reads_force_rls_tables_pinned_and_rolled_back():
    rds = FakeRds(count=9)
    port = AuroraPort(rds)

    assert port.baseline_count("trv_meridian_demo") == 9
    assert port.booking_ids("trv_meridian_demo") == {"HLD-1", "HLD-2"}

    pins = [(q, p) for q, p, _ in rds.sql if "set_config" in q]
    assert [p for _, p in pins] == [("trv_meridian_demo",)] * 2
    assert rds.rolled_back == ["tx-1", "tx-1"] and rds.committed == []
    assert all(tx == "tx-1" for _, _, tx in rds.sql)


def test_aurora_port_reads_the_audit_table_without_a_scope():
    rds = FakeRds(count=4)

    assert AuroraPort(rds).deny_audit_count("trv_demo_decoy") == 4
    assert all(tx is None for _, _, tx in rds.sql)


def test_http_port_refuses_a_host_that_may_not_receive_a_token():
    with pytest.raises(SystemExit, match="Refusing to send a credential"):
        HttpPort("http://site.example.net", tokens()[0])
    HttpPort("http://127.0.0.1:8000", tokens()[0])


def test_http_port_does_not_follow_a_redirect_with_the_token():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://evil.example.org/"})

    port = HttpPort(BASE, tokens()[0], transport=httpx.MockTransport(handler))

    assert port("jordan", "GET", "/api/me", None)[0] == 302 and seen == [BASE + "/api/me"]


def test_cleanup_counts_threads_and_bookings_that_remain():
    class Rds(FakeRds):
        async def execute(self, query, params=None, transaction_id=None):
            if "journey_threads" in query:
                return [{"thread": "phase5-proof-idpabc12345j"}]
            if "booking_id = %s" in query:
                return [{"booking_id": params[0]}]
            return []

    assert AuroraCleanup(Rds()).leftovers("phase5-proof-idpabc12345", ["HLD-1"]) == 2
