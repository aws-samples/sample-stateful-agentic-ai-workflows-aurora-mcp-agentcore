"""Turn probe outcomes into the answers to the four open questions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

DENIAL = re.compile(r"^(?:AuthorizeActionException\s*-\s*)?Tool Execution Denied:", re.I)
REFUSAL = re.compile(r"^Identity Check Failed:")
JORDAN = "trv_meridian_demo"
DECOY = "trv_demo_decoy"
PASS, FAIL, INFO = "PASS", "FAIL", "INFO"


@dataclass(frozen=True)
class Outcome:
    """What one probe call returned.

    Attributes:
        kind: ``ok`` (the target ran), ``denied`` (Cedar), ``refused`` (the interceptor),
            ``error`` (any other tool or JSON-RPC error) or ``http_error``.
        status: The HTTP status code.
        message: The error text, or an empty string.
        echo: The echo target's reply when ``kind`` is ``ok``.
    """

    kind: str
    status: int
    message: str = ""
    echo: Optional[Dict[str, Any]] = None


def classify(status: int, body: Any) -> Outcome:
    """Classify one MCP ``tools/call`` HTTP reply."""
    if status != 200 or not isinstance(body, dict):
        return Outcome("http_error", status, str(body)[:300])
    error = body.get("error")
    if isinstance(error, dict):
        message = str(error.get("message") or "")
        kind = "denied" if error.get("code") == -32002 or DENIAL.match(message) else "error"
        return Outcome(kind, status, message[:300])
    result = body.get("result") or {}
    text = "".join(
        block.get("text", "") for block in result.get("content") or [] if isinstance(block, dict)
    )
    if result.get("isError"):
        kind = "denied" if DENIAL.match(text.strip()) else (
            "refused" if REFUSAL.match(text.strip()) else "error")
        return Outcome(kind, status, text[:300])
    try:
        echo = json.loads(text)
    except ValueError:
        return Outcome("error", status, f"unreadable tool result: {text[:200]}")
    return Outcome("ok", status, echo=echo if isinstance(echo, dict) else None)


@dataclass
class Observations:
    """The outcomes of every probe, keyed by probe name, plus setup facts."""

    outcomes: Dict[str, Outcome] = field(default_factory=dict)
    binding_policy_accepted: bool = False
    listed_tools: List[str] = field(default_factory=list)

    def get(self, name: str) -> Optional[Outcome]:
        return self.outcomes.get(name)


@dataclass(frozen=True)
class Verdict:
    """One row of the verdict table."""

    key: str
    question: str
    finding: str
    status: str


def _traveler(outcome: Optional[Outcome]) -> Optional[str]:
    if outcome is None or outcome.kind != "ok" or not outcome.echo:
        return None
    return (outcome.echo.get("event") or {}).get("travelerId")


def _ordering(obs: Observations) -> Verdict:
    outcome = obs.get("decoy_names_jordan")
    question = "Does the interceptor run before Cedar?"
    if outcome is None:
        return Verdict("Q1", question, "not probed", INFO)
    if outcome.kind == "ok" and _traveler(outcome) == DECOY:
        return Verdict("Q1", question, "Yes. Cedar saw the rewritten id, so the deny rule "
                       "passed and the target ran with the decoy's id.", INFO)
    if outcome.kind == "denied":
        return Verdict("Q1", question, "No. Cedar evaluated the model's id and denied the call "
                       "before the interceptor's rewrite mattered.", INFO)
    return Verdict("Q1", question, f"Unexpected outcome: {outcome.kind} {outcome.message}", FAIL)


def _revalidation(obs: Observations) -> Verdict:
    question = "Are rewritten arguments checked against the tool schema?"
    omitted, extra = obs.get("omitted_required"), obs.get("extra_property")
    if omitted is None or extra is None:
        return Verdict("Q2", question, "not probed", INFO)
    before = "No" if omitted.kind == "ok" else "Yes"
    after = "No" if extra.kind == "ok" and (extra.echo or {}).get("event", {}).get(
        "unexpectedField") else "Yes"
    finding = (
        f"A required argument left out by the caller is checked before the interceptor: {before}"
        f" ({omitted.kind}). A property the interceptor adds that the schema forbids is "
        f"rejected after it: {after} ({extra.kind})."
    )
    return Verdict("Q2", question, finding, INFO)


def _target_view(obs: Observations) -> Verdict:
    question = "What does the target Lambda see?"
    outcome = obs.get("jordan_names_jordan")
    if outcome is None or outcome.kind != "ok" or not outcome.echo:
        return Verdict("Q3", question, "the control call did not reach the target", FAIL)
    event = outcome.echo.get("event") or {}
    custom = sorted((outcome.echo.get("custom") or {}).keys())
    finding = (
        f"The event is the bare argument object (keys: {', '.join(sorted(event))}); "
        f"client_context.custom keys: {', '.join(custom) or 'none'}. "
        "No claim or token reaches the target."
    )
    return Verdict("Q3", question, finding, INFO)


def _decoy(obs: Observations) -> Verdict:
    question = "Is a decoy token naming Jordan's travelerId kept from Jordan's records?"
    outcome = obs.get("decoy_names_jordan")
    if outcome is None:
        return Verdict("Q4", question, "not probed", FAIL)
    if outcome.kind == "denied":
        return Verdict("Q4", question, "Yes. Cedar denied it.", PASS)
    if outcome.kind == "ok" and _traveler(outcome) == DECOY:
        return Verdict("Q4", question, "Yes. The interceptor replaced Jordan's id with the "
                       "decoy's before the target saw it.", PASS)
    reached = _traveler(outcome)
    return Verdict("Q4", question, f"No. Outcome {outcome.kind}; target saw {reached}.", FAIL)


def _control(obs: Observations) -> Verdict:
    question = "Does Jordan's own token reach the target as Jordan?"
    outcome = obs.get("jordan_names_jordan")
    ok = outcome is not None and _traveler(outcome) == JORDAN
    return Verdict("C1", question, "Yes." if ok else "No: the positive control failed.",
                   PASS if ok else FAIL)


def _refusal_shape(obs: Observations) -> Verdict:
    question = "Is the interceptor's refusal understood by an MCP client?"
    outcome = obs.get("forced_refusal")
    ok = outcome is not None and outcome.kind == "refused"
    finding = "Yes. The tool error arrived intact." if ok else (
        f"No. Got {outcome.kind if outcome else 'nothing'}: "
        f"{outcome.message if outcome else ''}")
    return Verdict("C2", question, finding, PASS if ok else FAIL)


def _policy_accepted(obs: Observations) -> Verdict:
    question = "Did Policy accept the template's deny rule (FAIL_ON_ANY_FINDINGS)?"
    ok = obs.binding_policy_accepted
    return Verdict("C3", question, "Yes." if ok else "No.", PASS if ok else FAIL)


def _pass_through(obs: Observations) -> Verdict:
    question = "Do initialize and tools/list pass through the interceptor?"
    ok = "EchoTarget___echo" in obs.listed_tools
    return Verdict("C4", question, "Yes. The echo tool was listed." if ok else
                   "No: tools/list did not return the echo tool.", PASS if ok else FAIL)


def derive_verdicts(obs: Observations) -> List[Verdict]:
    """The four open questions, then the controls that make the answers trustworthy."""
    return [
        _ordering(obs), _revalidation(obs), _target_view(obs), _decoy(obs),
        _control(obs), _refusal_shape(obs), _policy_accepted(obs), _pass_through(obs),
    ]


def passed(verdicts: List[Verdict]) -> bool:
    """True when no row failed."""
    return all(v.status != FAIL for v in verdicts)


def format_table(verdicts: List[Verdict]) -> str:
    """A fixed-width table, one row per verdict."""
    lines = [f"{'#':<3} {'status':<6} question / finding", "-" * 78]
    for verdict in verdicts:
        lines.append(f"{verdict.key:<3} {verdict.status:<6} {verdict.question}")
        lines.append(f"{'':<10}{verdict.finding}")
    lines.append("-" * 78)
    lines.append("RESULT: " + ("PASS" if passed(verdicts) else "FAIL"))
    return "\n".join(lines)
