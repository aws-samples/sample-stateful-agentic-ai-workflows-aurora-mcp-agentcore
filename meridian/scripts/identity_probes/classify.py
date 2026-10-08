"""Turn a raw answer from one layer into a verdict that names the layer that answered."""

from __future__ import annotations

import json
import re
from typing import Any, NamedTuple

from scripts.identity_probes.receipt import ALLOWED, ERROR, NOT_ATTRIBUTED, REFUSED, scrub

IDENTITY_CHECK = "not authorized for that traveler"
GRANT_CHECK = "is not authorized for traveler"
DIFFERENT_TRAVELER = "different traveler"
POLICY_WORDS = re.compile(r"cedar|polic", re.IGNORECASE)


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
        detail = body.get("detail", body.get("message", ""))
        return detail if isinstance(detail, str) else json.dumps(detail)
    return str(body)


def classify_backend(status: int, body: Any) -> Verdict:
    """Classify the backend's HTTP answer: a 2xx is allowed, a 403 names its check."""
    text = scrub(_detail_text(body))
    if 200 <= status < 300:
        return Verdict(ALLOWED, None, f"HTTP {status}")
    if status == 403 and IDENTITY_CHECK in text:
        return Verdict(REFUSED, "backend_identity_check", f"HTTP 403 {text}")
    if status == 403 and GRANT_CHECK in text:
        return Verdict(REFUSED, "workload_grant", f"HTTP 403 {text}")
    if status == 403:
        return Verdict(REFUSED, NOT_ATTRIBUTED, f"HTTP 403 {text}")
    return Verdict(ERROR, None, f"HTTP {status} {text}")


def classify_runtime(events: list[dict]) -> Verdict:
    """Classify a Runtime's event stream: a coded authorization error is a refusal."""
    errors = [event for event in events if event.get("type") == "error"]
    if errors:
        code = errors[0].get("code")
        message = scrub(errors[0].get("message", ""))
        if code == "authorization" and DIFFERENT_TRAVELER in message:
            return Verdict(REFUSED, "runtime_traveler_check", f"{code}: {message}")
        if code == "authorization":
            return Verdict(REFUSED, "runtime_token_check", f"{code}: {message}")
        return Verdict(ERROR, None, f"{code}: {message}")
    if any(event.get("type") in ("result", "activity") for event in events):
        return Verdict(ALLOWED, None, "no error event")
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


def _gateway_refused(raw: Any) -> bool:
    return gateway_shape(raw) != "success"


def classify_gateway(raw: Any, *, deny_rows: int, design: str) -> Verdict:
    """Classify a Gateway tool call and attribute a refusal to the layer that made it.

    Args:
        raw: What ``call_tool`` returned, or an ``{"error": ...}`` stand-in for an HTTP refusal.
        deny_rows: New ``traveler_access_audit`` deny rows for the caller's traveler since before
            the call. A new row means the Holds Lambda ran and refused.
        design: ``both``, ``cedar`` or ``interceptor``, the Gateway enforcement that shipped.
    """
    text = _gateway_text(raw)
    if not _gateway_refused(raw):
        return Verdict(ALLOWED, None, text or "tool call accepted")
    if deny_rows > 0:
        return Verdict(REFUSED, "gateway_workload_grant", f"{deny_rows} new deny row; {text}")
    if design == "cedar" or (design == "both" and POLICY_WORDS.search(text)):
        return Verdict(REFUSED, "gateway_cedar", text)
    if design == "interceptor":
        return Verdict(REFUSED, "gateway_interceptor", text)
    return Verdict(REFUSED, NOT_ATTRIBUTED, text)


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
