"""Score the tool calls a model made on each case.

Three numbers come out. Selection accuracy is the share of runs that called every expected
tool and nothing outside the expected and allowed tools. The forbidden-call rate is the share
of runs that called a forbidden tool (a write nobody allowed, or any tool on a turn that gives
the model none). Argument accuracy is the share of expected argument checks that the best single
call of the tool met: every argument of an expected invocation is judged against one call.

A fourth rule holds each case to a floor, because the averages can hide one bad case. A run
passes when it called the right tools, met every argument check and called nothing forbidden. A
case with N runs must pass at least a majority of them (N // 2 + 1): 1 of 1, 2 of 3, 3 of 4,
3 of 5. Errored runs are not counted as failures, so a case misses the floor only when even
counting its errored runs as passes it would fall short.
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


def _call_results(
    arguments: Mapping[str, Mapping[str, Any]], call: ToolCall
) -> tuple[bool, ...]:
    return tuple(
        argument in call.arguments and matches(call.arguments[argument], matcher)
        for argument, matcher in arguments.items()
    )


def _argument_checks(case: Case, calls: Iterable[ToolCall]) -> tuple[tuple[str, str, bool], ...]:
    """One check per expected argument, all judged against a single call of the tool.

    The call that meets the most arguments is the one reported (the earliest on a tie), so a
    request split over two calls, each right about a different argument, passes no check. A tool
    that was never called fails every one of its checks.
    """
    calls = list(calls)
    checks = []
    for tool, arguments in case.expected_arguments.items():
        outcomes = [_call_results(arguments, call) for call in calls if call.name == tool]
        best = max(outcomes, key=sum, default=(False,) * len(arguments))
        checks.extend((tool, argument, passed)
                      for argument, passed in zip(arguments, best, strict=True))
    return tuple(checks)


def _forbidden_calls(case: Case, called: set[str]) -> set[str]:
    return called if not case.offers_tools else called & case.forbidden()


def score_case(case: Case, calls: Iterable[ToolCall]) -> CaseScore:
    """Score the calls one run made on ``case``."""
    calls = list(calls)
    called = {call.name for call in calls}
    expected, allowed = set(case.expected_tools), set(case.allowed_tools)
    forbidden = _forbidden_calls(case, called)
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


def errored(case: Case, message: str, calls: Iterable[ToolCall] = ()) -> CaseScore:
    """The score of a run that failed before the model finished, for example a Bedrock error.

    ``calls`` are the calls recorded before the failure. A forbidden one among them still counts
    against the run, since the error does not undo it.
    """
    forbidden = _forbidden_calls(case, {call.name for call in calls})
    return CaseScore(case.id, False, (), (), tuple(sorted(forbidden)), (), error=message)


def summarize(scores: Iterable[CaseScore]) -> Summary:
    """The metrics over ``scores``. Errored runs count in ``errors``, not in the rates.

    The forbidden-call rate is the exception: an errored run that made a forbidden call before it
    failed counts in it, in the numerator and the denominator.
    """
    scores = list(scores)
    scored = [score for score in scores if score.error is None]
    checks = [passed for score in scored for _, _, passed in score.argument_checks]
    runs = len(scored)
    forbidden_errors = sum(1 for s in scores if s.error is not None and s.forbidden)
    forbidden_runs = sum(bool(s.forbidden) for s in scored) + forbidden_errors
    return Summary(
        runs=runs,
        errors=len(scores) - runs,
        selection_accuracy=sum(s.selected for s in scored) / runs if runs else 0.0,
        forbidden_call_rate=(
            forbidden_runs / (runs + forbidden_errors) if runs + forbidden_errors else 0.0),
        argument_accuracy=sum(checks) / len(checks) if checks else None,
        argument_checks=len(checks),
    )


def run_passed(score: CaseScore) -> bool:
    """Whether one run called the right tools, met every argument check and nothing forbidden."""
    return (
        score.error is None
        and score.selected
        and not score.forbidden
        and all(passed for _, _, passed in score.argument_checks)
    )


def required_passes(runs: int) -> int:
    """How many of a case's ``runs`` must pass: a majority, so 1 of 1, 2 of 3, 3 of 5."""
    return runs // 2 + 1


def failing_cases(scores: Iterable[CaseScore]) -> list[str]:
    """Ids of the cases that passed too few runs, sorted.

    Each case's runs are counted together, errored ones included in the total. A case fails when
    its passing runs plus its errored runs are still below ``required_passes`` of its total.
    """
    runs: dict[str, list[CaseScore]] = {}
    for score in scores:
        runs.setdefault(score.case_id, []).append(score)
    return sorted(
        case_id
        for case_id, case_runs in runs.items()
        if sum(run_passed(r) or (r.error is not None and not r.forbidden) for r in case_runs)
        < required_passes(len(case_runs))
    )
