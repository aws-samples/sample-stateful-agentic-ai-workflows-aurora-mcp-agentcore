"""Turn a raw answer from one layer into a verdict that names the layer that answered."""

from __future__ import annotations

import json
import re
from typing import Any, NamedTuple

from scripts.identity_probes.receipt import ALLOWED, ERROR, REFUSED, scrub

IDENTITY_CHECK = "not authorized for that traveler"
GRANT_CHECKS = ("is not authorized for traveler", "not authorized for the current workload")
DIFFERENT_TRAVELER = "different traveler"
INTERCEPTOR_PREFIX = "Identity Check Failed: "
CEDAR_PREFIX = re.compile(r"^(?:AuthorizeActionException - )?Tool Execution Denied")
CEDAR_CODE = -32002
DESIGN_REFUSERS = {
    "both": {"gateway_cedar", "gateway_interceptor", "gateway_workload_grant"},
    "cedar": {"gateway_cedar", "gateway_workload_grant"},
    "interceptor": {"gateway_interceptor", "gateway_workload_grant"},
}


class Verdict(NamedTuple):
    """The result of one probe, before timing and evidence are added.

    Attributes:
        result: ``allowed``, ``refused`` or ``error``.
        refused_by: A key of ``REFUSER_LABELS`` when refused, else None.
        detail: A short description of what came back.
    """

    result: str
    refused_by: str | None
    detail: str


def _detail_text(body: Any) -> str:
    if isinstance(body, dict):
        detail = body.get("detail", body.get("error", body.get("message", "")))
        return detail if isinstance(detail, str) else json.dumps(detail)
    return str(body)


def classify_backend(status: int, body: Any) -> Verdict:
    """Classify the backend's HTTP answer: a 2xx is allowed, a 403 must name its check.

    A 403 whose text names neither the token traveler check nor the workload grant is an error:
    it is not evidence of which layer refused.
    """
    text = scrub(_detail_text(body))
    if 200 <= status < 300:
        return Verdict(ALLOWED, None, f"HTTP {status}")
    if status == 403 and IDENTITY_CHECK in text:
        return Verdict(REFUSED, "backend_identity_check", f"HTTP 403 {text}")
    if status == 403 and any(marker in text for marker in GRANT_CHECKS):
        return Verdict(REFUSED, "workload_grant", f"HTTP 403 {text}")
    if status == 403:
        return Verdict(ERROR, None, f"HTTP 403 from an unrecognised check: {text}")
    return Verdict(ERROR, None, f"HTTP {status} {text}")


def classify_runtime(events: list[dict]) -> Verdict:
    """Classify a Runtime's event stream.

    Only the claim check's own message (the request names a different traveler than the token)
    is a refusal. Any other error, including every other ``authorization`` message (a malformed
    token, a missing traveler claim, no bearer), is an error: it says the token is bad, not that
    the traveler was refused. A stream is allowed only when it ends in a ``result``.
    """
    errors = [event for event in events if event.get("type") == "error"]
    if errors:
        code = errors[0].get("code")
        message = scrub(errors[0].get("message", ""))
        if code == "authorization" and DIFFERENT_TRAVELER in message:
            return Verdict(REFUSED, "runtime_traveler_check", f"{code}: {message}")
        return Verdict(ERROR, None, f"{code}: {message}")
    if any(event.get("type") == "result" for event in events):
        return Verdict(ALLOWED, None, "result event, no error event")
    return Verdict(ERROR, None, "the Runtime sent no result and no error")


def tool_payload(raw: Any) -> dict:
    """The JSON object inside a tool result's text content, or ``{}``."""
    if not isinstance(raw, dict):
        return {}
    content = (raw.get("result") or {}).get("content") or []
    text = "".join(block.get("text", "") for block in content if isinstance(block, dict))
    try:
        parsed = json.loads(text)
    except ValueError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _gateway_text(raw: Any) -> str:
    if not isinstance(raw, dict):
        return scrub(raw)
    parts = []
    error = raw.get("error")
    if error:
        parts.append(error if isinstance(error, str) else json.dumps(error))
    for block in (raw.get("result") or {}).get("content") or []:
        if isinstance(block, dict):
            parts.append(str(block.get("text", "")))
    return scrub(" ".join(parts))


def gateway_shape(raw: Any) -> str:
    """Which form a Gateway answer takes, so a live refusal can be pinned in a fixture."""
    if not isinstance(raw, dict):
        return "not_a_dict"
    if raw.get("error"):
        return "error"
    if (raw.get("result") or {}).get("isError"):
        return "result.isError"
    if tool_payload(raw).get("error"):
        return "payload.error"
    return "success"


def _is_http_failure(raw: Any) -> bool:
    error = raw.get("error") if isinstance(raw, dict) else None
    return isinstance(error, dict) and "http_status" in error


def _evidence_refuser(raw: dict, shape: str, text: str) -> str | None:
    """The layer a refusal's own wording or code names, or None when it names none."""
    if shape == "result.isError" and text.startswith(INTERCEPTOR_PREFIX):
        return "gateway_interceptor"
    if shape == "result.isError" and CEDAR_PREFIX.match(text):
        return "gateway_cedar"
    error = raw.get("error")
    if shape == "error" and isinstance(error, dict) and error.get("code") == CEDAR_CODE:
        return "gateway_cedar"
    return None


def classify_gateway(raw: Any, *, deny_rows: int, design: str) -> Verdict:
    """Classify a Gateway tool call and attribute a refusal to the layer that made it.

    A refusal counts only with evidence of its layer: an HTTP 200 tool error that carries the
    interceptor's ``Identity Check Failed: `` text or Cedar's ``Tool Execution Denied`` text or
    JSON-RPC code -32002, or a new deny row in ``traveler_access_audit`` (the Holds Lambda's
    workload grant). An HTTP 4xx or 5xx, a timeout, a validation error or any other failure is an
    error, because it does not show that identity was the reason.

    Args:
        raw: What ``call_tool`` returned, or an ``{"error": {"http_status": ...}}`` stand-in.
        deny_rows: New ``traveler_access_audit`` deny rows for the caller's traveler since before
            the call. A new row means the Holds Lambda ran and refused.
        design: ``both``, ``cedar`` or ``interceptor``, the Gateway enforcement that shipped; a
            refusal from a layer the design does not have is an error.
    """
    shape = gateway_shape(raw)
    text = _gateway_text(raw)
    if shape == "success":
        return Verdict(ALLOWED, None, text or "tool call accepted")
    if shape == "not_a_dict" or _is_http_failure(raw):
        return Verdict(ERROR, None, f"not a tool refusal ({shape}): {text}")
    evidence = _evidence_refuser(raw, shape, text)
    if evidence and deny_rows > 0:
        return Verdict(ERROR, None, f"contradictory evidence: {evidence} text with "
                       f"{deny_rows} new deny row; {text}")
    refuser = evidence or ("gateway_workload_grant" if deny_rows > 0 else None)
    if refuser is None:
        return Verdict(ERROR, None, f"a failure with no refusal evidence ({shape}): {text}")
    if refuser not in DESIGN_REFUSERS.get(design, set()):
        return Verdict(ERROR, None, f"{refuser} refused but design {design!r} has no such "
                       f"layer; {text}")
    prefix = f"{deny_rows} new deny row; " if refuser == "gateway_workload_grant" else ""
    return Verdict(REFUSED, refuser, f"{prefix}{text}")


def classify_database_decoy(visible: int, baseline: int) -> Verdict:
    """The decoy's scoped count of Jordan's rows against the number that exist."""
    if baseline <= 0:
        return Verdict(ERROR, None, "no rows exist to hide, so this check proves nothing")
    if visible == 0:
        return Verdict(REFUSED, "database_rls", f"0 of {baseline} rows visible")
    return Verdict(ALLOWED, None, f"{visible} of {baseline} rows visible")


def classify_database_jordan(visible: int, baseline: int) -> Verdict:
    """Jordan's scoped count of his own rows against the number that exist."""
    if baseline <= 0:
        return Verdict(ERROR, None, "no rows exist, so this check proves nothing")
    if visible == 0:
        return Verdict(REFUSED, "database_rls", f"own rows hidden: 0 of {baseline}")
    if visible > baseline:
        return Verdict(ERROR, None, f"{visible} rows visible but only {baseline} exist")
    return Verdict(ALLOWED, None, f"{visible} of {baseline} rows visible")
