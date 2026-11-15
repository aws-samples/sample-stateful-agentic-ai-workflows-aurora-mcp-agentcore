"""Score the tool calls a model made on each case.

Three numbers come out. Selection accuracy is the share of runs that called every expected
tool and nothing outside the expected and allowed tools. The forbidden-call rate is the share
of runs that called a forbidden tool (a write nobody allowed, or any tool on a turn that gives
the model none). Argument accuracy is the share of expected argument checks that a call met.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from scripts.tool_choice_eval.cases import Case


@dataclass(frozen=True)
class ToolCall:
    """One tool call the model made, with the tool's bare name."""

    name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True)
class CaseScore:
    """The outcome of one run of one case.

    Attributes:
        case_id: The case.
        selected: Every expected tool was called and no tool outside expected and allowed.
        missing: Expected tools that were not called.
        unexpected: Tools called outside the expected and allowed tools.
        forbidden: Forbidden tools that were called.
        argument_checks: ``(tool, argument, passed)`` for each expected argument.
        error: Why the run could not be scored, or None.
    """

    case_id: str
    selected: bool
    missing: tuple[str, ...]
    unexpected: tuple[str, ...]
    forbidden: tuple[str, ...]
    argument_checks: tuple[tuple[str, str, bool], ...]
    error: str | None = None


@dataclass(frozen=True)
class Summary:
    """The metrics over every scored run; errored runs are counted apart."""

    runs: int
    errors: int
    selection_accuracy: float
    forbidden_call_rate: float
    argument_accuracy: float | None
    argument_checks: int


def matches(value: Any, matcher: Mapping[str, Any]) -> bool:
    """Whether ``value`` satisfies one matcher such as ``{"equals_ci": "tokyo"}``."""
    kind, expected = next(iter(matcher.items()))
    if kind == "equals":
        return _same_scalar(value, expected)
    if not isinstance(value, (str, list)):
        return False
    if kind == "set_equals":
        return isinstance(value, list) and {str(v) for v in value} == {str(v) for v in expected}
    if not isinstance(value, str):
        return False
    if kind == "equals_ci":
        return value.strip().casefold() == str(expected).casefold()
    return str(expected).casefold() in value.casefold()


def _same_scalar(value: Any, expected: Any) -> bool:
    numeric = (int, float)
    if isinstance(value, numeric) and isinstance(expected, numeric):
        if isinstance(value, bool) or isinstance(expected, bool):
            return value is expected
        return float(value) == float(expected)
    return value == expected


def _argument_checks(case: Case, calls: Iterable[ToolCall]) -> tuple[tuple[str, str, bool], ...]:
    calls = list(calls)
    checks = []
    for tool, arguments in case.expected_arguments.items():
        made = [call for call in calls if call.name == tool]
        for argument, matcher in arguments.items():
            passed = any(
                argument in call.arguments and matches(call.arguments[argument], matcher)
                for call in made
            )
            checks.append((tool, argument, passed))
    return tuple(checks)


def score_case(case: Case, calls: Iterable[ToolCall]) -> CaseScore:
    """Score the calls one run made on ``case``."""
    calls = list(calls)
    called = {call.name for call in calls}
    expected, allowed = set(case.expected_tools), set(case.allowed_tools)
    forbidden = called if not case.offers_tools else called & case.forbidden()
    missing = expected - called
    unexpected = called - expected - allowed
    return CaseScore(
        case_id=case.id,
        selected=not missing and not unexpected,
        missing=tuple(sorted(missing)),
        unexpected=tuple(sorted(unexpected)),
        forbidden=tuple(sorted(forbidden)),
        argument_checks=_argument_checks(case, calls),
    )


def errored(case: Case, message: str) -> CaseScore:
    """The score of a run that failed before the model finished, for example a Bedrock error."""
    return CaseScore(case.id, False, (), (), (), (), error=message)


def summarize(scores: Iterable[CaseScore]) -> Summary:
    """The metrics over ``scores``. Errored runs count in ``errors``, not in the rates."""
    scores = list(scores)
    scored = [score for score in scores if score.error is None]
    checks = [passed for score in scored for _, _, passed in score.argument_checks]
    runs = len(scored)
    return Summary(
        runs=runs,
        errors=len(scores) - runs,
        selection_accuracy=sum(s.selected for s in scored) / runs if runs else 0.0,
        forbidden_call_rate=sum(bool(s.forbidden) for s in scored) / runs if runs else 0.0,
        argument_accuracy=sum(checks) / len(checks) if checks else None,
        argument_checks=len(checks),
    )
