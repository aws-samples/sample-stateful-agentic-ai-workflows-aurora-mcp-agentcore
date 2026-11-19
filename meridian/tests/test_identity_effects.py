"""The real ports send the right requests, keep tokens in memory and always roll back."""

import json
from types import SimpleNamespace

import httpx
import pytest

from backend.agentcore.caller_credential import current_caller_token
from scripts import governed_release
from scripts.identity_probes import effects
from scripts.identity_probes.effects import (
    UNSCOPED_TABLES,
    AuroraCleanup,
    AuroraPort,
    GatewayPort,
    HttpPort,
    RuntimePort,
    TokenCache,
)

BASE = "https://site.example.net"
GATEWAY = "https://gateway.example.net/mcp"
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
    port = GatewayPort(gateway, tokens()[0], url=GATEWAY)

    assert port("decoy", "Tool___x", {}) == {"result": {"content": []}}
    assert gateway.token_seen == "token-for-decoy" and current_caller_token() is None


def test_gateway_port_turns_a_4xx_into_an_error_answer_and_reraises_a_5xx():
    denied = FakeGateway(RuntimeError("Gateway HTTP 403: AccessDenied"))
    refused = GatewayPort(denied, tokens()[0], url=GATEWAY)
    broken = GatewayPort(FakeGateway(RuntimeError("Gateway HTTP 502: bad")), tokens()[0],
                         url=GATEWAY)

    assert refused("decoy", "Tool___x", {}) == {
        "error": {"http_status": 403, "message": "AccessDenied"}}
    with pytest.raises(RuntimeError, match="502"):
        broken("decoy", "Tool___x", {})


class FakeRds:
    """A Data API client that enforces the booking policy: both settings must be pinned."""

    def __init__(self, *, role="meridian_app", active=True, count=0, bookings=None):
        self.role, self.active, self.count = role, active, count
        self.bookings = dict(bookings if bookings is not None else
                             {"HLD-1": "trv_meridian_demo", "HLD-2": "trv_meridian_demo"})
        self.sql, self.rolled_back, self.committed = [], [], []
        self.pinned: dict[str, str] = {}
        self.deleted: list[tuple[str, str]] = []
        self.held = set(self.bookings)
        self.threads: dict[str, str] = {}

    def begin_transaction(self):
        self.pinned = {}
        return "tx-1"

    def rollback_transaction(self, transaction_id):
        self.rolled_back.append(transaction_id)
        self.pinned = {}

    def commit_transaction(self, transaction_id):
        self.committed.append(transaction_id)
        self.pinned = {}

    def _visible(self, traveler=None):
        allowed = (self.pinned.get("traveler") is not None
                   and self.pinned.get("agent") == "booking_agent")
        return [b for b, owner in self.bookings.items()
                if allowed and owner == self.pinned["traveler"]
                and (traveler is None or owner == traveler)]

    async def execute(self, query, params=None, transaction_id=None):
        self.sql.append((query, params, transaction_id))
        if "set_config" in query:
            self.pinned["traveler"] = params[0]
            if "app.agent_type" in query:
                self.pinned["agent"] = params[1]
            return []
        if "current_user" in query and "relname" not in query:
            return [{"role": self.role, "active": self.active}]
        if query.startswith("DELETE FROM"):
            table = query.split()[2]
            self.deleted.append((table, params[0]))
            if table == "bookings":
                self.bookings.pop(params[0], None)
            return []
        if "COUNT(*)" in query and query != governed_release.THREADS_OF_JOURNEY_SQL:
            return [{"n": self.count}]
        return self._rows(query, params)

    def _rows(self, query, params):
        if query == governed_release.ACTIVE_SQL:
            return [{"active": params[0] in self.held} for b in self._visible() if b == params[0]]
        if query == governed_release.BOOKING_HOLD_SQL:
            return ([{"booking_id": params[0], "hold_request_id": f"hrq-{params[0]}",
                      "journey_id": "jrn-1"}] if params[0] in self.bookings else [])
        if query == governed_release.RUN_HOLDS_SQL:
            return [{"booking_id": b, "hold_request_id": f"hrq-{b}", "journey_id": "jrn-1"}
                    for b in sorted(self.bookings) if self.threads.get(params[0]) == "jrn-1"]
        if query == governed_release.THREADS_OF_JOURNEY_SQL:
            return [{"n": 1}]
        if "FROM bookings WHERE booking_id = %s" in query:
            return [{"booking_id": b} for b in self._visible() if b == params[0]]
        if "FROM bookings WHERE traveler_id = %s" in query:
            return [{"booking_id": b} for b in self._visible(params[0])]
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


def test_the_preference_count_is_pinned_to_the_traveler_and_rolled_back():
    rds = FakeRds(count=9)

    assert AuroraPort(rds).baseline_count("trv_meridian_demo") == 9

    assert [p for q, p, _ in rds.sql if "set_config" in q] == [("trv_meridian_demo",)]
    assert rds.rolled_back == ["tx-1"] and rds.committed == []


def test_bookings_are_read_with_the_traveler_and_the_booking_agent_pinned():
    rds = FakeRds()
    port = AuroraPort(rds)

    assert port.booking_ids("trv_meridian_demo") == {"HLD-1", "HLD-2"}
    assert port.booking_ids("trv_demo_decoy") == set()

    pins = [(q, p) for q, p, _ in rds.sql if "set_config" in q]
    assert all("app.agent_type" in q and p[1] == "booking_agent" for q, p in pins)
    assert [p[0] for _, p in pins] == ["trv_meridian_demo", "trv_demo_decoy"]
    assert rds.rolled_back == ["tx-1", "tx-1"] and rds.committed == []
    assert all(tx == "tx-1" for _, _, tx in rds.sql)


def test_hold_bookings_are_found_through_the_journey_thread_without_a_scope():
    class Holds(FakeRds):
        def _rows(self, query, params):
            if "JOIN journey_threads" in query and params == ("phase5-proof-idpabcj",):
                return [{"booking_id": "HLD-9"}]
            return []

    rds = Holds()

    assert AuroraPort(rds).hold_bookings("phase5-proof-idpabcj") == {"HLD-9"}
    assert all(tx is None for _, _, tx in rds.sql)


def test_aurora_port_reads_the_audit_table_without_a_scope():
    rds = FakeRds(count=4)

    assert AuroraPort(rds).deny_audit_count("trv_demo_decoy") == 4
    assert all(tx is None for _, _, tx in rds.sql)


def visibility_rows(**overrides):
    row = {"secured": True, "forced": False, "exempt": True}
    return [{"table_name": name, **{**row, **overrides.get(name, {})}} for name in UNSCOPED_TABLES]


class Visibility(FakeRds):
    def __init__(self, rows):
        super().__init__()
        self.rows = rows

    def _rows(self, query, params):
        return self.rows if "pg_class" in query else []


def test_preflight_accepts_tables_the_role_owns_or_bypasses_or_that_have_no_policy():
    rows = visibility_rows(traveler_access_audit={"secured": False, "exempt": False})

    AuroraPort(Visibility(rows)).preflight()


@pytest.mark.parametrize("override", [
    {"forced": True}, {"exempt": False}, {"forced": True, "exempt": True},
])
def test_preflight_refuses_when_an_unscoped_read_would_silently_return_nothing(override):
    rows = visibility_rows(journey_threads=override)

    with pytest.raises(RuntimeError, match="journey_threads"):
        AuroraPort(Visibility(rows)).preflight()


def test_preflight_refuses_when_a_table_the_proof_reads_is_missing():
    with pytest.raises(RuntimeError, match="hold_requests"):
        AuroraPort(Visibility(
            [r for r in visibility_rows() if r["table_name"] != "hold_requests"])).preflight()


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


class ReleaseGateway:
    """The governed release tool over the fake Aurora: a release makes the booking inactive."""

    def __init__(self, rds, *, works=True):
        self.rds, self.works, self.calls = rds, works, []

    def call_tool(self, name, args):
        booking = args["holdRequestId"].removeprefix("hrq-")
        self.calls.append((name, booking, args["travelerId"]))
        if not self.works:
            body = {"error": "hold_not_found"}
        else:
            self.rds.held.discard(booking)
            body = {"release": {"status": "released", "replayed": False}}
        return {"result": {"content": [{"type": "text", "text": json.dumps(body)}]}}


@pytest.fixture
def gateway(monkeypatch):
    def install(rds, **kwargs):
        made = ReleaseGateway(rds, **kwargs)
        monkeypatch.setattr("backend.agentcore.gateway.get_agentcore_gateway", lambda: made)
        return made
    return install


def _written(rds):
    return [q for q, _p, _t in rds.sql
            if q.lstrip().upper().startswith(("DELETE", "UPDATE", "INSERT"))]


def test_release_gives_each_booking_back_through_the_gateway_and_deletes_nothing(gateway):
    rds = FakeRds()
    made = gateway(rds)

    released = AuroraCleanup(rds).release_bookings([("trv_meridian_demo", "HLD-1")])

    assert released == 1 and rds.held == {"HLD-2"}
    assert [c[1:] for c in made.calls] == [("HLD-1", "trv_meridian_demo")]
    assert rds.bookings == {"HLD-1": "trv_meridian_demo", "HLD-2": "trv_meridian_demo"}
    assert rds.deleted == [] and _written(rds) == []
    pins = [p for q, p, _ in rds.sql if "set_config" in q]
    assert pins and all(pin == ("trv_meridian_demo", "booking_agent") for pin in pins)


def test_release_still_gives_back_the_rest_when_one_booking_cannot_be_seen(gateway):
    rds = FakeRds(bookings={"HLD-2": "trv_meridian_demo", "HLD-3": "trv_demo_decoy"})
    made = gateway(rds)

    with pytest.raises(RuntimeError, match="HLD-3") as caught:
        AuroraCleanup(rds).release_bookings([("trv_meridian_demo", "HLD-3"),
                                             ("trv_meridian_demo", "HLD-2")])

    assert "HLD-2" not in str(caught.value) and "HLD-2" not in rds.held
    assert "not visible" in str(caught.value) and rds.deleted == []
    assert [c[1] for c in made.calls] == ["HLD-3", "HLD-2"]


def test_release_gives_a_decoy_booking_back_as_the_decoy(gateway):
    rds = FakeRds(bookings={"HLD-D": "trv_demo_decoy"})
    made = gateway(rds)

    assert AuroraCleanup(rds).release_bookings([("trv_demo_decoy", "HLD-D")]) == 1
    assert made.calls == [(governed_release_tool(), "HLD-D", "trv_demo_decoy")]
    assert rds.held == set()


def governed_release_tool():
    from backend.agents.phase_05_workflow.governed_hold import RELEASE_TOOL
    return RELEASE_TOOL


def test_a_release_the_gateway_refuses_is_reported_after_two_calls_and_nothing_is_deleted(
        gateway):
    rds = FakeRds(bookings={"HLD-1": "trv_meridian_demo"})
    made = gateway(rds, works=False)
    cleanup = AuroraCleanup(rds)

    with pytest.raises(RuntimeError, match="HLD-1"):
        cleanup.release_bookings([("trv_meridian_demo", "HLD-1")])
    with pytest.raises(RuntimeError, match="HLD-1"):
        cleanup.release_bookings([("trv_meridian_demo", "HLD-1")])

    assert len(made.calls) == 2 and rds.held == {"HLD-1"} and _written(rds) == []


def test_a_thread_of_this_proof_has_its_holds_given_back_and_a_foreign_thread_is_refused(
        gateway):
    rds = FakeRds(bookings={"HLD-1": "trv_meridian_demo"})
    rds.threads = {"phase5-proof-idpabc12345wj": "jrn-1"}
    made = gateway(rds)
    cleanup = AuroraCleanup(rds)

    cleanup.release_thread("phase5-proof-idpabc12345wj")
    assert rds.held == set() and len(made.calls) == 1 and _written(rds) == []

    with pytest.raises(RuntimeError, match="not named by this proof"):
        cleanup.release_thread("jordan-real-thread")


def test_cleanup_lists_the_holds_still_held_for_threads_and_new_bookings(gateway):
    class Rds(FakeRds):
        def _rows(self, query, params):
            if "journey_threads" in query and "LIKE" in query:
                return [{"thread": "phase5-proof-idpabc12345wj"}]
            return super()._rows(query, params)

    rds = Rds(bookings={"HLD-OLD": "trv_meridian_demo", "HLD-NEW": "trv_meridian_demo"})
    rds.threads = {"phase5-proof-idpabc12345wj": "jrn-1"}
    baseline = {"trv_meridian_demo": frozenset({"HLD-OLD"}), "trv_demo_decoy": frozenset()}

    items = AuroraCleanup(rds).leftovers("phase5-proof-idpabc12345", baseline)

    assert items.count("booking HLD-NEW of trv_meridian_demo still held or of unknown state") == 1


def test_a_kept_booking_that_reads_inactive_is_not_a_leftover():
    rds = FakeRds(bookings={"HLD-OLD": "trv_meridian_demo", "HLD-NEW": "trv_meridian_demo"})
    rds.held = {"HLD-OLD"}
    baseline = {"trv_meridian_demo": frozenset({"HLD-OLD"}), "trv_demo_decoy": frozenset()}

    assert AuroraCleanup(rds).leftovers("phase5-proof-idpabc12345", baseline) == []


def test_a_booking_the_pinned_scope_cannot_see_is_a_leftover_never_released():
    baseline = {"trv_meridian_demo": frozenset(), "trv_demo_decoy": frozenset()}

    class Hidden(FakeRds):
        def _rows(self, query, params):
            if query == governed_release.ACTIVE_SQL:
                return []
            return super()._rows(query, params)

    items = AuroraCleanup(Hidden(bookings={"HLD-X": "trv_meridian_demo"})).leftovers(
        "phase5-proof-idpabc12345", baseline)

    assert items == ["booking HLD-X of trv_meridian_demo still held or of unknown state"]


def test_the_gateway_port_refuses_a_url_that_may_not_receive_a_token():
    with pytest.raises(SystemExit, match="Refusing to send a credential"):
        GatewayPort(FakeGateway({}), tokens()[0], url="http://gateway.example.net/mcp")
    GatewayPort(FakeGateway({}), tokens()[0], url="https://gateway.example.net/mcp")


class Config:
    workflow_runtime_arn = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/W-abc"
    runtime_arn = "arn:aws:bedrock-agentcore:us-east-1:123456789012:runtime/C-abc"


def rig_world(monkeypatch, gateway_url):
    gateway = SimpleNamespace(gateway_url=gateway_url)
    monkeypatch.setattr(effects, "resolve_agentcore_config", Config)
    monkeypatch.setattr(effects, "get_agentcore_gateway", lambda: gateway)
    monkeypatch.setattr(effects, "RDSDataClient", lambda **kwargs: object())


@pytest.mark.parametrize(("base", "gateway", "message"), [
    ("http://site.example.net", GATEWAY, "Refusing to send a credential"),
    (BASE, "http://gateway.example.net/mcp", "https"),
])
def test_build_rig_turns_a_refused_url_into_an_error_main_can_mask(
        monkeypatch, base, gateway, message):
    rig_world(monkeypatch, gateway)

    with pytest.raises(RuntimeError, match=message):
        effects.build_rig({}, "us-east-1", base, tokens=tokens()[0])


def test_build_rig_refuses_plain_http_on_loopback_for_the_gateway(monkeypatch):
    rig_world(monkeypatch, "http://localhost:8080/mcp")

    with pytest.raises(RuntimeError, match="https"):
        effects.build_rig({}, "us-east-1", BASE, tokens=tokens()[0])


def test_build_rig_still_accepts_a_loopback_http_backend(monkeypatch):
    rig_world(monkeypatch, GATEWAY)

    ports, _ = effects.build_rig({}, "us-east-1", "http://localhost:8000", tokens=tokens()[0])

    assert ports.http is not None


def test_build_rig_accepts_https_urls(monkeypatch):
    rig_world(monkeypatch, GATEWAY)

    ports, _ = effects.build_rig({}, "us-east-1", BASE, tokens=tokens()[0])

    assert ports.gateway is not None
