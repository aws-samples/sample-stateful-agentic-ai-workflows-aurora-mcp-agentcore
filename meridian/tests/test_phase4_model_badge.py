"""Phase 4 names the model the AgentCore Runtime reported, or no model at all.

The Runtime reuses a Bedrock client for its configured model, with no automatic
fallback chain, and reports that id in the ``model`` field of its runtime
span. The backend reads that row; it never substitutes the configured model.
"""

import asyncio

from backend.agents.phase_04_production.concierge import runtime_model_id
from backend.http_auth import HttpPrincipal
from backend.routers.chat import ActivityEntry, ChatRequest, TraceTelemetry, chat

PRINCIPAL = HttpPrincipal(
    subject_id="test-client",
    traveler_id="trv_meridian_demo",
    authentication="test",
)


def _runtime_started(fields: list[dict]) -> ActivityEntry:
    return ActivityEntry(
        id="rt-start",
        timestamp="2026-09-28T18:00:00Z",
        activity_type="runtime",
        title="AgentCore Runtime: turn started",
        telemetry=TraceTelemetry(
            category="runtime",
            component="Bedrock AgentCore Runtime, MeridianConcierge",
            status="ok",
            fields=fields,
        ),
    )


MODEL_ROW = {"label": "model", "value": "global.anthropic.claude-haiku-4-5-20251001-v1:0"}


def test_runtime_model_id_reads_the_model_row_from_the_runtime_span():
    span = _runtime_started([{"label": "trace_id", "value": "abc"}, MODEL_ROW])
    assert runtime_model_id([span]) == MODEL_ROW["value"]


def test_runtime_model_id_reads_the_dict_spans_the_concierge_collects():
    span = {
        "title": "AgentCore Runtime: turn started",
        "telemetry": {"category": "runtime", "fields": [MODEL_ROW]},
    }
    assert runtime_model_id([span]) == MODEL_ROW["value"]


def test_runtime_model_id_is_none_without_a_model_row():
    assert runtime_model_id([_runtime_started([{"label": "trace_id", "value": "abc"}])]) is None
    assert runtime_model_id([]) is None


def test_runtime_model_id_ignores_a_model_field_outside_the_runtime_span():
    gateway = ActivityEntry(
        id="gw",
        timestamp="2026-09-28T18:00:00Z",
        activity_type="tool_call",
        title="AgentCore Gateway: tools/list",
        telemetry=TraceTelemetry(category="gateway", fields=[MODEL_ROW]),
    )
    assert runtime_model_id([gateway]) is None


def _run_phase4(monkeypatch, activities: list, message: str) -> object:
    async def fake_production_search(*_args, **_kwargs):
        return [], activities, "Runtime reply.", "conv-model", []

    monkeypatch.setattr("backend.routers.chat.production_search", fake_production_search)
    return asyncio.run(chat(
        ChatRequest(phase=4, customer_id="trv_meridian_demo", message=message),
        PRINCIPAL,
    ))


def test_phase4_reply_badge_names_the_runtime_model(monkeypatch):
    response = _run_phase4(
        monkeypatch, [_runtime_started([MODEL_ROW])], "Find Tokyo trips for me."
    )
    assert response.message == "Runtime reply."
    # The configured primary is Sonnet 5; the runtime reported Haiku, and the
    # badge must name the model that ran.
    assert response.model_label == "Claude Haiku 4.5"


def test_phase4_reply_has_no_badge_when_the_runtime_reports_no_model(monkeypatch):
    response = _run_phase4(
        monkeypatch, [_runtime_started([{"label": "trace_id", "value": "abc"}])],
        "Find Tokyo trips for me.",
    )
    assert response.model_label is None


def test_phase4_reply_badge_names_the_reported_gpt_model(monkeypatch):
    response = _run_phase4(
        monkeypatch,
        [_runtime_started([{"label": "model", "value": "us.openai.gpt-6-luna"}])],
        "Find Tokyo trips for me.",
    )
    assert response.model_label == "GPT-6 Luna"


def test_phase4_workflow_handoff_has_no_badge(monkeypatch):
    # The handoff reply is fixed text, not the runtime model's reply.
    response = _run_phase4(
        monkeypatch, [_runtime_started([MODEL_ROW])],
        "My JFK-to-Tokyo flight was cancelled. Rework the trip, then check "
        "duration availability for the best three options.",
    )
    assert response.model_label is None
