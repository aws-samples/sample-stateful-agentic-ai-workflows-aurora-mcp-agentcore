"""Turn probe outcomes into the answers to the open questions and the controls."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

DENIAL = re.compile(r"^(?:AuthorizeActionException\s*-\s*)?Tool Execution Denied:", re.I)
REFUSAL = re.compile(r"^Identity Check Failed:")
JORDAN = "trv_meridian_demo"
DECOY = "trv_demo_decoy"
ACCOUNT_ID = re.compile(r"\b[0-9]{12}\b")
JWT_SHAPE = re.compile(r"eyJ[\w-]+\.[\w-]+\.[\w-]*")
CREDENTIAL_KEY = re.compile(r"token|claim|authorization|jwt", re.I)
OMITTED_FIELD_NAMED = re.compile(r"travelerId|required|schema|missing|validation", re.I)
WRONG_TYPE_NAMED = re.compile(r"travelerId|type|string|integer|schema|validation", re.I)
PASS, FAIL, INFO, UNKNOWN = "PASS", "FAIL", "INFO", "UNKNOWN"
KEYS = ("Q1", "Q2", "Q3", "Q4", "Q5", "C1", "C2", "C3", "C4")
# Q5 only chooses between fallback designs after Q4 or C5 has already failed the run (plan
# decision table, cases C to E), so an inconclusive Q5 must not fail an otherwise passing run.
MAY_BE_UNKNOWN = ("Q5",)


def scrub(text: str) -> str:
    """Mask account ids and token-shaped strings, and collapse whitespace to single spaces."""
    text = JWT_SHAPE.sub("<token>", text)
    text = ACCOUNT_ID.sub("<acct>", text)
    return " ".join(text.split())


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
    echo: dict[str, Any] | None = None


def classify(status: int, body: Any) -> Outcome:
    """Classify one MCP ``tools/call`` HTTP reply."""
    if status != 200 or not isinstance(body, dict):
        return Outcome("http_error", status, scrub(str(body))[:300])
    error = body.get("error")
    if isinstance(error, dict):
        message = scrub(str(error.get("message") or ""))
        kind = "denied" if error.get("code") == -32002 or DENIAL.match(message) else "error"
        return Outcome(kind, status, message[:300])
    result = body.get("result") or {}
    content = result.get("content") if isinstance(result, dict) else None
    if not isinstance(result, dict) or not isinstance(content or [], list):
        return Outcome("error", status, "unreadable tool result: unexpected reply shape")
    text = "".join(
        block["text"] for block in content or []
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    )
    if result.get("isError"):
        kind = "denied" if DENIAL.match(text.strip()) else (
            "refused" if REFUSAL.match(text.strip()) else "error")
        return Outcome(kind, status, scrub(text)[:300])
    try:
        echo = json.loads(text)
    except ValueError:
        return Outcome("error", status, f"unreadable tool result: {scrub(text)[:200]}")
    return Outcome("ok", status, echo=echo if isinstance(echo, dict) else None)


@dataclass
class Observations:
    """The outcomes of every probe, keyed by probe name, plus setup facts."""

    outcomes: dict[str, Outcome] = field(default_factory=dict)
    binding_policy_accepted: bool = False
    listed_tools: list[str] = field(default_factory=list)
    interceptors_after_detach: int | None = None

    def get(self, name: str) -> Outcome | None:
        """The outcome recorded for probe ``name``, or ``None`` if it was never run."""
        return self.outcomes.get(name)


@dataclass(frozen=True)
class Verdict:
    """One row of the verdict table."""

    key: str
    question: str
    finding: str
    status: str


def _traveler(outcome: Outcome | None) -> str | None:
    if outcome is None or outcome.kind != "ok" or not outcome.echo:
        return None
    event = outcome.echo.get("event")
    return event.get("travelerId") if isinstance(event, dict) else None


def _echo_keys(outcome: Outcome, part: str) -> list[str]:
    section = (outcome.echo or {}).get(part)
    return sorted(section) if isinstance(section, dict) else []


def _ordering(obs: Observations) -> Verdict:
    outcome = obs.get("decoy_names_jordan")
    question = "Does the interceptor run before Cedar?"
    if outcome is None:
        return Verdict("Q1", question, "not probed", UNKNOWN)
    if outcome.kind == "ok" and _traveler(outcome) == DECOY:
        return Verdict("Q1", question, "Yes. Cedar saw the rewritten id, so the deny rule "
                       "passed and the target ran with the decoy's id.", INFO)
    if outcome.kind == "denied":
        return Verdict("Q1", question, "No. Cedar evaluated the model's id and denied the call "
                       "before the interceptor's rewrite mattered.", INFO)
    return Verdict("Q1", question, f"Unexpected outcome: {outcome.kind} {outcome.message}", FAIL)


def _half(outcome: Outcome, named: re.Pattern[str], reached: bool) -> str:
    """``Yes``/``No`` when the outcome settles the question, else ``inconclusive (...)``."""
    if outcome.kind == "ok":
        return "No" if reached else "inconclusive (ok, but the probe did not reach the target)"
    if outcome.kind == "error" and named.search(outcome.message):
        return "Yes"
    return f"inconclusive ({outcome.kind} {outcome.status})"


def _rewritten_event(outcome: Outcome) -> dict[str, Any] | None:
    event = (outcome.echo or {}).get("event")
    return event if isinstance(event, dict) else None


def _revalidation(obs: Observations) -> Verdict:
    question = "Are rewritten arguments checked against the declared tool schema?"
    omitted, typed, dropped = (
        obs.get("omitted_required"), obs.get("bad_type"), obs.get("drop_required"))
    if omitted is None or typed is None or dropped is None:
        return Verdict("Q2", question, "not probed", UNKNOWN)
    traveler = (_rewritten_event(typed) or {}).get("travelerId")
    wrong_type_arrived = isinstance(traveler, int) and not isinstance(traveler, bool)
    arrived = _rewritten_event(dropped)
    removed_arrived = arrived is not None and "travelerId" not in arrived
    halves = (
        _half(omitted, OMITTED_FIELD_NAMED, True),
        _half(typed, WRONG_TYPE_NAMED, wrong_type_arrived),
        _half(dropped, OMITTED_FIELD_NAMED, removed_arrived),
    )
    finding = (
        f"A required argument left out by the caller is checked before the interceptor: "
        f"{halves[0]}. A travelerId the interceptor rewrote to the wrong type is rejected after "
        f"it: {halves[1]}. A call with the required travelerId removed by the interceptor is "
        f"rejected after it: {halves[2]}. A rejection shows the Gateway re-validates rewritten "
        "arguments against the declared schema. An accepted probe does not show that "
        "additionalProperties:false is enforced (the tool schema is declared without it); it only "
        "answers whether re-validation happens at all."
    )
    inconclusive = any("inconclusive" in half for half in halves)
    return Verdict("Q2", question, finding, UNKNOWN if inconclusive else INFO)


def _target_view(obs: Observations) -> Verdict:
    question = "What does the target Lambda see?"
    outcome = obs.get("jordan_names_jordan")
    if outcome is None or outcome.kind != "ok" or not outcome.echo:
        return Verdict("Q3", question, "the control call did not reach the target", FAIL)
    event, custom = _echo_keys(outcome, "event"), _echo_keys(outcome, "custom")
    flagged = [key for key in event + custom if CREDENTIAL_KEY.search(key)]
    reach = (
        f"Credential-like keys reach the target: {', '.join(flagged)}." if flagged
        else "No claim or token key reaches the target."
    )
    finding = (
        f"The event is the argument object (keys: {', '.join(event)}); "
        f"client_context.custom keys: {', '.join(custom) or 'none'}. {reach}"
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


def _cedar_alone(obs: Observations) -> Verdict:
    question = "Does Cedar alone keep the decoy out when the interceptor changes nothing?"
    outcome, control = obs.get("cedar_alone"), obs.get("cedar_alone_control")
    if outcome is None:
        return Verdict("Q5", question, "not probed", UNKNOWN)
    if outcome.kind == "ok" and _traveler(outcome) == JORDAN:
        return Verdict("Q5", question, "No. Cedar did not stop the decoy's call naming Jordan's "
                       "id; the target received it. Cedar alone is not enough.", INFO)
    if outcome.kind == "denied" and not (control and control.kind == "ok"
                                         and _traveler(control) == JORDAN):
        return Verdict("Q5", question, "Inconclusive: the off-mode control did not reach the "
                       "target", UNKNOWN)
    if outcome.kind == "denied" and DENIAL.match(outcome.message.strip()):
        return Verdict("Q5", question, "Yes. Cedar denied the decoy's call naming Jordan's id, "
                       "so a Cedar-only release is available.", INFO)
    return Verdict("Q5", question, f"Inconclusive: {outcome.kind} {outcome.status} "
                   f"{outcome.message}".rstrip(), UNKNOWN)


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


def _detached(obs: Observations) -> Verdict:
    question = "Does updating the Gateway without an interceptor remove it (the rollback)?"
    remaining = obs.interceptors_after_detach
    if remaining is None:
        return Verdict("C5", question, "not probed", INFO)
    if remaining == 0:
        return Verdict("C5", question, "Yes. An update without interceptorConfigurations left "
                       "none attached, so one update_gateway call is the rollback.", PASS)
    return Verdict("C5", question, f"No. The Gateway still reports {remaining} interceptor(s) "
                   "after an update that omitted them; the rollback cannot detach it.", FAIL)


def derive_verdicts(obs: Observations) -> list[Verdict]:
    """The five open questions (Q1 to Q5), then the five controls (C1 to C5) behind them."""
    return [
        _ordering(obs), _revalidation(obs), _target_view(obs), _decoy(obs), _cedar_alone(obs),
        _control(obs), _refusal_shape(obs), _policy_accepted(obs), _pass_through(obs),
        _detached(obs),
    ]


def passed(verdicts: list[Verdict]) -> bool:
    """True when all nine rows are present and each is PASS or INFO.

    FAIL and UNKNOWN fail the run, except an UNKNOWN row named in ``MAY_BE_UNKNOWN`` (Q5).
    """
    return {v.key for v in verdicts} >= set(KEYS) and all(
        v.status in (PASS, INFO) or (v.status == UNKNOWN and v.key in MAY_BE_UNKNOWN)
        for v in verdicts)


def format_table(verdicts: list[Verdict]) -> str:
    """A fixed-width table, one row per verdict."""
    lines = [f"{'#':<3} {'status':<6} question / finding", "-" * 78]
    for verdict in verdicts:
        lines.append(f"{verdict.key:<3} {verdict.status:<6} {scrub(verdict.question)}")
        lines.append(f"{'':<10}{scrub(verdict.finding)}")
    lines.append("-" * 78)
    lines.append("RESULT: " + ("PASS" if passed(verdicts) else "FAIL"))
    return "\n".join(lines)
