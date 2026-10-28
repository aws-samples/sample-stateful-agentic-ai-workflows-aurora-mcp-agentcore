"""The workflow's Gateway tool calls, made with the signed-in caller's access token.

Phase 5 reaches its catalog search and its holds through AgentCore Gateway, so the request
interceptor, Cedar and the target's own checks see what the workflow does:
``MeridianCatalog___hybrid_trip_search`` for discovery, ``MeridianHolds___create_courtesy_hold``
and ``MeridianHolds___release_hold`` for holds. Its availability, memory and read-back reads stay
scoped Aurora reads under the workflow login. In ``jwt`` mode a Gateway call carries the caller's
token; in ``iam`` mode it is signed with the Runtime role. Nothing here falls back to an
in-process path: a refusal or an outage is an error the run reports.

Every Gateway call a run makes is logged for its audit row (see ``run_audit``): one ``ToolCall``
per call, in the order the calls finished, under the bare tool name, with how the call ended
(``run_calls.call_of_response``). A call cut off by a cancellation has no result to record; the
row's ``turn_error`` says the run was cancelled.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import re
from contextlib import contextmanager
from typing import Any, Awaitable, Callable, Dict, Iterator, List, Optional, Tuple

from backend.activity import Product
from backend.agentcore.caller_claims import CallerClaimsError
from backend.agentcore.caller_credential import caller_token_scope, current_caller_token
from backend.agentcore.errors import AgentCoreNotConfiguredError, CallerCredentialError
from backend.agents.phase_05_workflow.governed_hold import (
    ENVELOPE_PHRASES,
    envelope_failure,
    log_detail,
    response_text,
)
from backend.agents.phase_05_workflow.run_calls import call_of_response
from backend.agents.phase_05_workflow.state import activity, utc_now
from backend.audit_vocabulary import CODE_CLASSES, ERROR, ToolCall
from backend.catalog_compat import row_to_api_product
from backend.tools.tool_names import bare_tool_name
from backend.trace_names import WORKFLOW_CODE

HTTP_STATUS = re.compile(r"^Gateway HTTP (\d{3})")
# One code per thing a caller does differently: sign in again (401, 403), retry soon (408, 429),
# fix the request (other 4xx), wait for an outage (5xx).
HTTP_CODES = {"401": "unauthorized", "403": "unauthorized", "408": "timeout", "429": "throttled"}
TRANSPORT_FAILURES = {
    "unauthorized": "the Gateway rejected the caller's credentials",
    "throttled": "the Gateway is limiting requests, try again shortly",
    "rejected": "the Gateway rejected the request",
    "server_error": "the Gateway answered with a server error",
    "timeout": "the Gateway call timed out",
    "unreachable": "the Gateway could not be reached",
    "unreadable_reply": "the Gateway reply could not be read",
    "transport": "the Gateway call failed",
}
SEARCH_TOOL = "MeridianCatalog___hybrid_trip_search"
SyncCall = Callable[[str, Dict[str, Any]], Dict[str, Any]]
AsyncCall = Callable[..., Awaitable[Dict[str, Any]]]

_CALLED: contextvars.ContextVar[Optional[List[ToolCall]]] = contextvars.ContextVar(
    "meridian_workflow_tool_calls", default=None
)


class GatewayToolError(RuntimeError):
    """The Gateway refused a tool call or answered with something unusable.

    The message is a fixed phrase chosen where the error is raised, never text the Gateway or
    the target sent: the exception is saved in a snapshot and logged, and a target's text can
    hold account ids or tokens. Operators read the masked detail in the log record written
    beside the raise (:func:`log_detail`).

    Attributes:
        tool: The Gateway tool name.
        refused_by: ``"policy"`` or ``"identity_check"`` when a layer before the target refused.
        code: A short word for the failure, such as ``"unauthorized"`` or ``"timeout"``.
    """

    def __init__(
        self, tool: str, message: str, *, refused_by: Optional[str] = None, code: str = "failed"
    ) -> None:
        super().__init__(f"Gateway tool {tool} failed: {message}")
        self.tool = tool
        self.refused_by = refused_by
        self.code = code


def _transport_code(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, ValueError):
        return "unreadable_reply"
    status = HTTP_STATUS.match(str(exc))
    if status:
        return HTTP_CODES.get(status.group(1)) or (
            "server_error" if status.group(1).startswith("5") else "rejected"
        )
    return "unreachable" if isinstance(exc, OSError) else "transport"


@contextmanager
def tool_log_scope() -> Iterator[List[ToolCall]]:
    """Log the calls made inside the block, one ``ToolCall`` each, in the order they finished."""
    log: List[ToolCall] = []
    reset = _CALLED.set(log)
    try:
        yield log
    finally:
        _CALLED.reset(reset)


def record_call(call: ToolCall) -> None:
    """Add ``call`` to the current run's log; outside a scope this does nothing."""
    log = _CALLED.get()
    if log is not None:
        log.append(call)


def recording(call: SyncCall) -> SyncCall:
    """Wrap a blocking transport so every call is logged as it ended."""

    def recorded(tool: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        try:
            response = call(tool, arguments)
        except Exception:
            record_call(ToolCall(bare_tool_name(tool), ERROR))
            raise
        record_call(call_of_response(tool, response))
        return response

    return recorded


def invoke_gateway_tool(
    tool: str, arguments: Dict[str, Any], *, caller_token: str
) -> Dict[str, Any]:
    """Make one blocking ``tools/call`` as the caller.

    Raises:
        CallerTokenMissing: ``jwt`` mode and ``caller_token`` is empty.
        CallerTokenExpired: The token expired or is about to.
        GatewayToolError: The transport failed (HTTP 401, 403, 5xx, timeout, unreachable, an
            unreadable body). Its message is fixed; the detail goes to the log, masked.
    """
    from backend.agentcore.gateway import get_agentcore_gateway

    with caller_token_scope(caller_token or None):
        try:
            return get_agentcore_gateway().call_tool(tool, arguments)
        except (CallerCredentialError, CallerClaimsError, AgentCoreNotConfiguredError):
            raise
        except (RuntimeError, OSError, ValueError) as exc:
            code = _transport_code(exc)
            log_detail(tool, f"{type(exc).__name__}: {exc}")
            raise GatewayToolError(tool, TRANSPORT_FAILURES[code], code=code) from None


async def call_gateway_tool(
    tool: str, arguments: Dict[str, Any], *, caller_token: str
) -> Dict[str, Any]:
    """Call a Gateway tool as the caller, log how it ended, and return the response.

    Args:
        tool: The Gateway tool name, target prefix included.
        arguments: The tool arguments.
        caller_token: The signed-in caller's access token (ignored in ``iam`` mode).
    """
    try:
        response = await asyncio.to_thread(
            invoke_gateway_tool, tool, arguments, caller_token=caller_token
        )
    except Exception:
        record_call(ToolCall(bare_tool_name(tool), ERROR))
        raise
    record_call(call_of_response(tool, response))
    return response


def tool_payload(tool: str, response: Dict[str, Any]) -> Dict[str, Any]:
    """The JSON object a tool returned.

    Raises:
        GatewayToolError: An error envelope, a named tool error, or an unreadable result.
    """
    text = response_text(response)
    failure = envelope_failure(response, text)
    if failure is not None:
        message, layer = failure
        log_detail(tool, message)
        raise GatewayToolError(
            tool, ENVELOPE_PHRASES[layer], refused_by=layer, code=layer or "tool_error"
        )
    try:
        payload = json.loads(text) if text else {}
    except ValueError:
        log_detail(tool, text)
        raise GatewayToolError(tool, "the result is not readable JSON", code="unreadable") from None
    if not isinstance(payload, dict):
        raise GatewayToolError(tool, "the result is not a JSON object", code="unreadable")
    error = payload.get("error")
    if error is not None:
        log_detail(tool, str(error))
        named = f" ({error})" if isinstance(error, str) and error in CODE_CLASSES else ""
        raise GatewayToolError(tool, f"the tool returned an error{named}", code="tool_error")
    return payload


def _products(rows: Any, limit: int) -> List[Product]:
    if not isinstance(rows, list):
        raise GatewayToolError(SEARCH_TOOL, "the result has no packages list", code="unreadable")
    try:
        return [
            Product(
                **row_to_api_product(row),
                pre_rerank_position=row.get("pre_rerank_position"),
                pre_rerank_similarity=row.get("pre_rerank_similarity"),
                rank_delta=row.get("rank_delta"),
            )
            for row in rows[:limit]
        ]
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        log_detail(SEARCH_TOOL, f"{type(exc).__name__}: {exc}")
        raise GatewayToolError(
            SEARCH_TOOL,
            "a package row is unusable; rows need package_id, name and price_per_person",
            code="unusable_row",
        ) from None


def _search_span(payload: Dict[str, Any], count: int, elapsed_ms: int) -> Dict[str, Any]:
    candidates = payload.get("candidates")
    candidates = candidates if isinstance(candidates, dict) else {}
    return activity(
        "tool_call",
        f"Gateway tool: {SEARCH_TOOL}",
        details=f"{count} ranked packages from the catalog target",
        agent_name=WORKFLOW_CODE,
        execution_time_ms=elapsed_ms,
        sql_query=(
            f"-- AgentCore Gateway tools/call {SEARCH_TOOL}\n"
            "-- MeridianCatalog Lambda: embedding, pgvector and full-text arms, price and stock\n"
            "--   gate, then rerank"
        ),
        telemetry={
            "category": "gateway",
            "component": "AgentCore Gateway, MeridianCatalog Lambda, Aurora",
            "status": "ok",
            "fields": [
                {"label": "gateway_tool", "value": SEARCH_TOOL, "mono": True},
                {"label": "candidates_semantic", "value": str(candidates.get("semantic", ""))},
                {"label": "candidates_lexical", "value": str(candidates.get("lexical", ""))},
                {"label": "candidates_merged", "value": str(candidates.get("merged", ""))},
                {"label": "reranked", "value": "yes" if payload.get("reranked") else "no"},
            ],
        },
    )


async def gateway_search(
    query: str, limit: int = 5, *, call: Optional[AsyncCall] = None
) -> Tuple[List[Product], List[Dict[str, Any]]]:
    """Hybrid trip search through the Gateway, as the signed-in caller.

    Args:
        query: The traveler's words.
        limit: How many ranked packages to return.
        call: ``async (tool, arguments, *, caller_token) -> response``; tests inject one.

    Returns:
        The ranked products, with the rerank fields the Recovery desk animates, and one span
        describing the call. The single Gateway span replaces the embedding, pgvector, lexical
        and rerank spans the in-process search reported, because those steps now run inside the
        catalog Lambda.

    Raises:
        GatewayToolError: The Gateway or the target refused the call or answered with an
            unusable result. The workflow does not search in-process instead.
    """
    caller = call or call_gateway_tool
    started = utc_now()
    response = await caller(
        SEARCH_TOOL, {"query": query, "limit": limit}, caller_token=current_caller_token() or ""
    )
    payload = tool_payload(SEARCH_TOOL, response)
    products = _products(payload.get("packages"), limit)
    elapsed = int((utc_now() - started).total_seconds() * 1000)
    return products, [_search_span(payload, len(products), elapsed)]
