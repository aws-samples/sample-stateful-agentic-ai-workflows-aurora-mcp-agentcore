"""Trace spans and the packages a Phase 3 turn observed.

An MCP tool's error reaches the model only as ``catalog_tool_failed`` and the tool name, the same
answer a failed local tool gives and the MeridianCatalog Lambda gives through the Gateway. A local
tool's own argument error is the exception: Strands words it from the model's own input and never
from server or credential text, so it reaches the model unchanged, where it helps the model
correct its call.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, List, NamedTuple

from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent, HookProvider

from backend.activity import ActivityEntry, TraceTelemetry, create_activity
from backend.agents.concierge_definition import package_rows, short_name, tool_payload
from backend.agents.phase_03_retrieval.mcp_session import MCP_TOOLS
from backend.agents.phase_03_retrieval.tools import DETAIL_CHARS, LOCAL_TOOLS, TOOL_FAILED
from backend.agents.strands_hooks import is_setup_error_text
from backend.security.redaction import mask_log_text, masked_traceback

logger = logging.getLogger(__name__)

AGENT_NAME = "RetrievalAgent"
AGENT_FILE = "agents/phase_03_retrieval/agent.py"
MAX_DETAIL = 240
# FastMCP words every failure on the server, argument validation included, this way.
SERVER_ERROR_PREFIX = "Error executing tool "
# Strands words a failure of the client side, such as a closed session or a dead server process.
CLIENT_ERROR_PREFIXES = ("Tool execution failed: ", "Tool execution cancelled locally")


def surface(name: str) -> str:
    """Where a tool runs, in the words the trace shows."""
    return "in-process" if name in LOCAL_TOOLS else "meridian-concierge MCP server over stdio"


def _clip(text: str) -> str:
    return text if len(text) <= MAX_DETAIL else text[: MAX_DETAIL - 3] + "..."


class _Call(NamedTuple):
    """One tool call as its spans show it."""

    name: str
    arguments: str
    elapsed: int | None = None


class McpFailure(NamedTuple):
    """One failed MCP tool call: where its error came from, and its masked detail.

    ``origin`` is ``server`` (the server reported it, so its session is alive), ``client``
    (Strands built it: the session or the server process is gone) or ``unknown``. ``setup`` is
    true when the text names a credential, permission or configuration failure (see
    ``is_setup_error_text``), which ends the turn.
    """

    tool: str
    origin: str
    detail: str
    setup: bool = False


def _error_text(result: dict) -> str:
    blocks = [b for b in result.get("content") or [] if isinstance(b, dict)]
    return "".join(b["text"] for b in blocks if isinstance(b.get("text"), str))


def error_origin(result: dict) -> str:
    """Whether an error result was reported by the server, built by the client, or neither."""
    text = _error_text(result)
    if text.startswith(SERVER_ERROR_PREFIX):
        return "server"
    if text.startswith(CLIENT_ERROR_PREFIXES):
        return "client"
    return "unknown"


def _failed(result: dict, payload: Any) -> bool:
    """An error status, or a local tool's ``catalog_tool_failed`` answer."""
    if result.get("status") != "success":
        return True
    return isinstance(payload, dict) and payload.get("error") == TOOL_FAILED


def _summary(rows: List[dict], payload: Any) -> str:
    if rows:
        return f"{len(rows)} packages"
    if isinstance(payload, dict):
        return "returned " + ", ".join(sorted(payload)[:6])
    return "result returned"


class RetrievalTraceHooks(HookProvider):
    """One span before and one after every tool call; remembers the latest packages.

    ``mcp_failures`` records each tool served over MCP that failed. ``client_suspect`` is true
    when one of them was not reported by the server, or could not be set up (expired credentials
    need a fresh server process), so the pool should replace the client. The model never reads an
    MCP error's text: it gets ``catalog_tool_failed`` and the tool name, as a successful result.
    """

    def __init__(self, spans: List[ActivityEntry], clock: Callable[[], float] = time.monotonic):
        self.spans = spans
        self.clock = clock
        self.started: dict[str, float] = {}
        self.tools_called: List[str] = []
        self.packages: List[dict] = []
        self.mcp_failures: List[McpFailure] = []

    @property
    def client_suspect(self) -> bool:
        """True when an MCP tool failed in a way the server did not report, or on setup."""
        return any(failure.origin != "server" or failure.setup for failure in self.mcp_failures)

    def register_hooks(self, registry, **kwargs: Any) -> None:
        registry.add_callback(BeforeToolCallEvent, self.before)
        registry.add_callback(AfterToolCallEvent, self.after)

    def _span(self, kind: str, title: str, details: str, call: _Call,
              status: str) -> ActivityEntry:
        entry = create_activity(
            kind, title=title, details=details, execution_time_ms=call.elapsed,
            agent_name=AGENT_NAME, agent_file=AGENT_FILE)
        entry.telemetry = TraceTelemetry(
            category="tool", component=surface(call.name), status=status,
            fields=[
                {"label": "tool", "value": call.name, "mono": True},
                {"label": "arguments", "value": call.arguments, "mono": True},
                {"label": "surface", "value": surface(call.name)},
            ])
        return entry

    def before(self, event: BeforeToolCallEvent) -> None:
        name = short_name(event.tool_use["name"])
        arguments = json.dumps(event.tool_use.get("input") or {}, ensure_ascii=False, default=str)
        self.started[event.tool_use["toolUseId"]] = self.clock()
        self.tools_called.append(name)
        self.spans.append(self._span(
            "tool_call", f"Tool call: {name}", _clip(arguments), _Call(name, arguments), "ok"))

    def _note_failure(self, event: AfterToolCallEvent, name: str, result: dict) -> None:
        """Log a failed call once, with masked text. An MCP error also hides from the model.

        The replacement has status ``success``, like a failed local tool's answer and the
        Gateway's, so the model sees one shape on every surface; ``after`` has already marked
        the span failed from the original result.

        A local tool answers for itself, and an unregistered name carries Strands' own text and
        no server text, so only a name in ``MCP_TOOLS`` is recorded or replaced.
        """
        origin = error_origin(result) if result.get("status") != "success" else "server"
        text = _error_text(result)
        detail = mask_log_text(text)[:DETAIL_CHARS]
        trace = f"\n{masked_traceback(event.exception)}" if event.exception else ""
        logger.warning("Phase 3 tool %s returned an error (%s): %s%s", name, origin, detail, trace)
        if name not in MCP_TOOLS:
            return
        failed_in_error = result.get("status") != "success"
        self.mcp_failures.append(McpFailure(
            name, origin, detail, setup=failed_in_error and is_setup_error_text(text)))
        if failed_in_error:
            event.result = {
                "toolUseId": event.tool_use["toolUseId"], "status": "success",
                "content": [{"text": json.dumps({"error": TOOL_FAILED, "tool": name})}]}

    def after(self, event: AfterToolCallEvent) -> None:
        name = short_name(event.tool_use["name"])
        arguments = json.dumps(event.tool_use.get("input") or {}, ensure_ascii=False, default=str)
        began = self.started.pop(event.tool_use["toolUseId"], None)
        elapsed = round((self.clock() - began) * 1000) if began is not None else None
        result = event.result or {}
        payload = tool_payload(result)
        if _failed(result, payload):
            self._note_failure(event, name, result)
            self.spans.append(self._span(
                "error", f"Tool call failed: {name}",
                "The tool returned an error; the backend log has the details.",
                _Call(name, arguments, elapsed), "error"))
            return
        rows = package_rows(name, payload)
        if rows:
            self.packages = rows
        self.spans.append(self._span(
            "tool_call", f"Tool result: {name}", _summary(rows, payload),
            _Call(name, arguments, elapsed), "ok"))
