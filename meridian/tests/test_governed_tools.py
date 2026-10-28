"""The workflow reaches Gateway tools with the signed-in caller's token and fails closed."""

import json
from urllib.error import URLError

import pytest

from backend.agentcore import gateway as gateway_module
from backend.agentcore.caller_credential import caller_token_scope, current_caller_token
from backend.agentcore.errors import CallerTokenMissing
from backend.agents.phase_05_workflow import governed_tools as tools
from backend.agents.phase_05_workflow.service import build_workflow_runner
from backend.agents.phase_05_workflow.run_calls import call_of_response
from backend.audit_vocabulary import ToolCall
from backend.trace_names import WORKFLOW_CODE

URL = "https://gw-1.gateway.bedrock-agentcore.us-east-1.amazonaws.com/mcp"
ROWS = [{
    "package_id": "CTY-002", "name": "Tokyo", "operator": "Meridian", "trip_type": "city",
    "price_per_person": 2500.0, "description": "Seven nights", "image_url": "",
    "destination": "Tokyo", "region": "Asia", "durations": ["7 nights"],
    "availability": {"7 nights": 4}, "highlights": ["Sushi"], "similarity": 0.9,
    "pre_rerank_position": 2, "pre_rerank_similarity": 0.8, "rank_delta": 1,
}]
CANDIDATES = {"semantic": 3, "lexical": 2, "merged": 4}
CEDAR_DENIAL = "AuthorizeActionException - Tool Execution Denied: denied by default"


def reply(payload=None, *, text=None, is_error=False):
    body = text if text is not None else json.dumps(payload)
    return {"result": {"content": [{"type": "text", "text": body}], "isError": is_error}}


class FakeGateway:
    def __init__(self, response=None):
        self.seen, self._response = [], response or reply({"ok": True})

    def call_tool(self, tool, arguments):
        self.seen.append((tool, arguments, current_caller_token()))
        return self._response


async def test_a_call_forwards_the_callers_token_and_leaves_the_outer_scope_alone(monkeypatch):
    fake = FakeGateway()
    monkeypatch.setattr(gateway_module, "get_agentcore_gateway", lambda: fake)
    response = await tools.call_gateway_tool("T___x", {"a": 1}, caller_token="tok-1")
    assert response == reply({"ok": True})
    assert fake.seen == [("T___x", {"a": 1}, "tok-1")]
    assert current_caller_token() is None


async def test_a_call_without_a_token_fails_closed_in_jwt_mode(monkeypatch):
    monkeypatch.setenv("MERIDIAN_AGENTCORE_AUTH", "jwt")
    monkeypatch.delenv("AGENTCORE_GATEWAY_ACCESS_TOKEN", raising=False)
    adapter = gateway_module.AgentCoreGatewayAdapter(gateway_url=URL, region="us-east-1")
    monkeypatch.setattr(gateway_module, "get_agentcore_gateway", lambda: adapter)
    with pytest.raises(CallerTokenMissing):
        await tools.call_gateway_tool("T___x", {}, caller_token="")


async def test_calls_are_logged_in_order_with_bare_names_inside_a_scope_and_nowhere_else(
    monkeypatch,
):
    monkeypatch.setattr(gateway_module, "get_agentcore_gateway", lambda: FakeGateway())
    await tools.call_gateway_tool("T___outside", {}, caller_token="t")
    with tools.tool_log_scope() as calls:
        await tools.call_gateway_tool("T___a", {}, caller_token="t")
        await tools.call_gateway_tool("T___a", {}, caller_token="t")
        await tools.call_gateway_tool("T___b", {}, caller_token="t")
    assert calls == [ToolCall("a", "allowed"), ToolCall("a", "allowed"), ToolCall("b", "allowed")]


async def test_a_transport_failure_is_logged_as_an_error_call_and_raises_gateway_error(monkeypatch):
    class Down:
        def call_tool(self, tool, arguments):
            raise ConnectionError("gateway down")

    monkeypatch.setattr(gateway_module, "get_agentcore_gateway", lambda: Down())
    with tools.tool_log_scope() as calls:
        with pytest.raises(tools.GatewayToolError):
            await tools.call_gateway_tool("T___a", {}, caller_token="t")
    assert calls == [ToolCall("a", "error")]


def test_a_recorded_transport_logs_the_call_as_it_ended_and_returns_the_reply():
    inner = lambda name, arguments: reply({"hold": {"bookingId": "b"}})  # noqa: E731
    with tools.tool_log_scope() as calls:
        out = tools.recording(inner)("T___hold", {})
    assert out == reply({"hold": {"bookingId": "b"}}) and calls == [ToolCall("hold", "allowed")]


def test_a_recorded_transport_that_raises_logs_an_error_call_and_reraises():
    def inner(name, arguments):
        raise TimeoutError("read timed out")

    with tools.tool_log_scope() as calls, pytest.raises(TimeoutError):
        tools.recording(inner)("T___hold", {})
    assert calls == [ToolCall("hold", "error")]


async def test_search_calls_the_catalog_tool_as_the_caller_and_returns_products():
    seen = []

    async def call(tool, arguments, *, caller_token):
        seen.append((tool, arguments, caller_token))
        return reply({"packages": ROWS, "candidates": CANDIDATES, "reranked": True})

    with caller_token_scope("tok-1"):
        products, spans = await tools.gateway_search("quiet tokyo", limit=3, call=call)
    assert seen == [("MeridianCatalog___hybrid_trip_search",
                     {"query": "quiet tokyo", "limit": 3}, "tok-1")]
    assert (products[0].product_id, products[0].price) == ("CTY-002", 2500.0)
    assert (products[0].pre_rerank_position, products[0].rank_delta) == (2, 1)
    assert products[0].pre_rerank_similarity == 0.8
    assert spans[0]["agent_name"] == WORKFLOW_CODE
    fields = {f["label"]: f["value"] for f in spans[0]["telemetry"]["fields"]}
    assert fields["gateway_tool"] == tools.SEARCH_TOOL
    assert (fields["reranked"], fields["candidates_merged"]) == ("yes", "4")


@pytest.mark.parametrize(("response", "refused_by"), [
    (reply(text=CEDAR_DENIAL, is_error=True), "policy"),
    (reply(text="Identity Check Failed: no bearer token.", is_error=True), "identity_check"),
    (reply({"error": "boom"}), None),
    (reply(text="not json"), None),
    (reply({"packages": "nope"}), None),
    (reply({"packages": [{"name": "no id"}]}), None),
])
async def test_a_refused_or_unusable_answer_is_an_error_never_an_in_process_search(
    monkeypatch, response, refused_by
):
    def forbidden(*_args, **_kwargs):
        pytest.fail("the workflow must not search in-process when the Gateway fails")

    monkeypatch.setattr("backend.retrieval.hybrid.retrieval_search", forbidden)

    async def call(tool, arguments, *, caller_token):
        return response

    with pytest.raises(tools.GatewayToolError) as raised:
        await tools.gateway_search("q", call=call)
    assert raised.value.refused_by == refused_by
    assert tools.SEARCH_TOOL in str(raised.value)


def test_the_production_runner_searches_through_the_gateway_by_default():
    runner = build_workflow_runner(client=object())
    assert runner._nodes.search_fn is tools.gateway_search


LEAK = "arn:aws:iam::123456789012:role/x Bearer abc.def.ghi"


def _http_error(code):
    return RuntimeError(f"Gateway HTTP {code}: denied for {LEAK}")


@pytest.mark.parametrize(("failure", "code"), [
    (_http_error(401), "unauthorized"),
    (_http_error(403), "unauthorized"),
    (_http_error(502), "server_error"),
    (TimeoutError(f"timed out for {LEAK}"), "timeout"),
    (URLError(f"refused for {LEAK}"), "unreachable"),
    (ValueError(f"Expecting value near {LEAK}"), "unreadable_reply"),
])
async def test_a_transport_failure_carries_a_code_and_none_of_the_servers_text(
    monkeypatch, failure, code
):
    class Down:
        def call_tool(self, tool, arguments):
            raise failure

    monkeypatch.setattr(gateway_module, "get_agentcore_gateway", lambda: Down())
    with pytest.raises(tools.GatewayToolError) as raised:
        await tools.call_gateway_tool("T___a", {}, caller_token="t")
    assert raised.value.code == code
    assert "123456789012" not in str(raised.value) and "abc.def" not in str(raised.value)
    assert raised.value.__cause__ is None and raised.value.__suppress_context__


def test_the_detail_kept_for_operators_is_masked_and_bounded(monkeypatch, caplog):
    class Down:
        def call_tool(self, tool, arguments):
            raise RuntimeError(f"Gateway HTTP 403: {LEAK} " + "z" * 900)

    monkeypatch.setattr(gateway_module, "get_agentcore_gateway", lambda: Down())
    with caplog.at_level("WARNING"), pytest.raises(tools.GatewayToolError):
        tools.invoke_gateway_tool("T___a", {}, caller_token="t")
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "<acct>" in logged and "Bearer <token>" in logged
    assert "123456789012" not in logged and "abc.def" not in logged and len(logged) < 600


@pytest.mark.parametrize("response", [
    reply(text=f"denied for {LEAK}", is_error=True),
    reply(text=f"not json {LEAK}"),
    reply({"error": f"boom {LEAK}"}),
    reply({"packages": [{"name": f"no id {LEAK}"}]}),
])
async def test_no_failure_text_from_the_target_reaches_the_error(response):
    async def call(tool, arguments, *, caller_token):
        return response

    with pytest.raises(tools.GatewayToolError) as raised:
        await tools.gateway_search("q", call=call)
    assert "123456789012" not in str(raised.value) and "abc.def" not in str(raised.value)


@pytest.mark.parametrize("response", [
    {"result": "x"}, [1], {"result": {"content": "x"}}, {"result": {"content": [None, 3]}},
    reply({"packages": ["x"]}), reply({"packages": [None]}),
])
async def test_a_malformed_envelope_is_a_gateway_error_and_leaves_a_call_row(
    monkeypatch, response
):
    monkeypatch.setattr(
        gateway_module, "get_agentcore_gateway", lambda: FakeGateway(response=response)
    )
    with tools.tool_log_scope() as calls:
        with pytest.raises(tools.GatewayToolError):
            await tools.gateway_search("q")
    assert len(calls) == 1 and calls[0].result in ("error", "allowed")
    assert calls[0].tool == "hybrid_trip_search"


async def test_a_candidates_value_that_is_not_an_object_does_not_break_the_search():
    async def call(tool, arguments, *, caller_token):
        return reply({"packages": ROWS, "candidates": [1], "reranked": False})

    products, spans = await tools.gateway_search("q", call=call)
    assert len(products) == 1 and spans


async def test_a_null_error_is_not_an_error_in_the_run_or_in_the_audit():
    answer = reply({"error": None, "packages": ROWS, "candidates": CANDIDATES})

    async def call(tool, arguments, *, caller_token):
        return answer

    products, _ = await tools.gateway_search("q", call=call)
    assert [p.product_id for p in products] == ["CTY-002"]
    assert call_of_response(tools.SEARCH_TOOL, answer) == ToolCall("hybrid_trip_search", "allowed")


async def test_search_returns_at_most_limit_products():
    rows = [{**ROWS[0], "package_id": f"CTY-{n:03d}"} for n in range(6)]

    async def call(tool, arguments, *, caller_token):
        return reply({"packages": rows, "candidates": CANDIDATES, "reranked": False})

    products, spans = await tools.gateway_search("q", limit=5, call=call)
    assert [p.product_id for p in products] == [f"CTY-{n:03d}" for n in range(5)]
    assert "5 ranked" in spans[0]["details"]


@pytest.mark.parametrize(("status", "code"), [
    (400, "rejected"), (404, "rejected"), (422, "rejected"), (408, "timeout"),
    (429, "throttled"), (401, "unauthorized"), (403, "unauthorized"),
    (500, "server_error"), (503, "server_error"),
])
def test_each_http_status_class_has_its_own_code_and_phrase(monkeypatch, status, code):
    class Down:
        def call_tool(self, tool, arguments):
            raise _http_error(status)

    monkeypatch.setattr(gateway_module, "get_agentcore_gateway", lambda: Down())
    with pytest.raises(tools.GatewayToolError) as raised:
        tools.invoke_gateway_tool("T___a", {}, caller_token="t")
    assert raised.value.code == code
    assert tools.TRANSPORT_FAILURES[code] in str(raised.value)


def test_the_http_status_classes_read_differently():
    phrases = {tools.TRANSPORT_FAILURES[c] for c in (
        "rejected", "throttled", "unauthorized", "server_error", "timeout")}
    assert len(phrases) == 5


async def test_a_claims_failure_ends_as_a_claims_failure_not_an_unknown_hold_outcome(monkeypatch):
    from unittest.mock import AsyncMock

    from backend.agentcore.caller_claims import CallerClaimsError
    from backend.agents.phase_05_workflow.nodes import WorkflowNodes
    from tests.phase5_support import fake_availability, fake_search

    class Refusing:
        def call_tool(self, tool, arguments):
            raise CallerClaimsError("malformed")

    monkeypatch.setattr(gateway_module, "get_agentcore_gateway", lambda: Refusing())
    with pytest.raises(CallerClaimsError):
        tools.invoke_gateway_tool("T___a", {}, caller_token="t")
    nodes = WorkflowNodes(fake_search, fake_availability)
    nodes._prepare_governed_hold = AsyncMock()
    with caller_token_scope("tok"), pytest.raises(CallerClaimsError):
        await nodes._place_hold({"packageId": "x"})
