#!/usr/bin/env python3
"""Measure which tool the Concierge's model chooses, against the versioned eval set.

Dry run (default): print the cases, the tool surface the model would see, the model id and what
the run would send. No Amazon Bedrock call, no AWS credentials needed.
    venv/bin/python scripts/run_tool_choice_eval.py

Live run (ask the account owner first; it bills model invocations to the calling account):
    AWS_PROFILE=<profile> venv/bin/python scripts/run_tool_choice_eval.py \\
        --apply --i-understand-this-changes-aws --repeats 3

Each case runs the real Concierge agent (the Runtime's prompt, hooks and model settings) over
stub tools with the Gateway's names and schemas. The stubs return canned catalog results and
write nothing. The model is ``BEDROCK_MODEL_ID`` or the Runtime's default, as deployed.

Thresholds apply to the whole run: selection accuracy and argument accuracy must reach their
minimums and the forbidden-call rate must not pass its maximum. Set them from a recorded
baseline of the current prompt and tool descriptions, then hold changes to that baseline.

Each case also has a floor, because averages can hide one bad case: it must pass (right tools,
every argument check, no forbidden call) in a majority of its runs, N // 2 + 1 of N. That is 1
of 1, 2 of 3 and 3 of 5. The failing case ids are printed, and a miss exits 1.

It refuses to run when ``CI`` is set, so it cannot run in a pipeline by accident. The report
file, if asked for, must be inside .local (git-ignored).

Exit codes: 0 every threshold met, 1 a threshold missed, 2 inconclusive (a run errored and none
of the others missed a threshold), 3 refused (missing confirmation, CI, or a usage error).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

MERIDIAN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(MERIDIAN_DIR))

from scripts.identity_release.settings import CONFIRM_FLAG  # noqa: E402
from scripts.tool_choice_eval import runner  # noqa: E402
from scripts.tool_choice_eval.cases import CaseError, load_cases  # noqa: E402
from scripts.tool_choice_eval.scoring import (  # noqa: E402
    CaseScore,
    Summary,
    failing_cases,
    required_passes,
    summarize,
)

LOCAL_DIR = MERIDIAN_DIR / ".local"
EXIT_PASS, EXIT_MISSED, EXIT_INCONCLUSIVE, EXIT_REFUSED = 0, 1, 2, 3
# Set from the baseline of 15 Nov 2026 (prompt d4c88f717866, tools f7102d25b38e, cases
# 1458d7689217, global.anthropic.claude-sonnet-5-5, 29 cases x 3): selection 1.00, argument
# accuracy 1.00 over 93 checks, forbidden-call rate 0.00. The minimums leave room for one or
# two nondeterministic misses; a forbidden call is never accepted.
DEFAULT_MIN_SELECTION = 0.95
DEFAULT_MIN_ARGUMENTS = 0.95
DEFAULT_MAX_FORBIDDEN = 0.0


class Refused(Exception):
    """The invocation is not allowed to run."""


def parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Parse the command line; any usage error exits 3, never 2."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0], allow_abbrev=False)
    parser.add_argument("--apply", action="store_true", help="send the cases to Amazon Bedrock")
    parser.add_argument(CONFIRM_FLAG, dest="confirmed", action="store_true")
    parser.add_argument("--repeats", type=int, default=1, help="runs per case (default 1)")
    parser.add_argument("--case", action="append", default=[], help="run only this case id")
    parser.add_argument("--min-selection", type=float, default=DEFAULT_MIN_SELECTION)
    parser.add_argument("--min-arguments", type=float, default=DEFAULT_MIN_ARGUMENTS)
    parser.add_argument("--max-forbidden", type=float, default=DEFAULT_MAX_FORBIDDEN)
    parser.add_argument("--report", type=Path, help="write the JSON report here (inside .local)")
    try:
        return parser.parse_args(argv)
    except SystemExit as exit_:
        if exit_.code in (0, None):
            raise
        raise SystemExit(EXIT_REFUSED) from None


def refusal(args: argparse.Namespace, environ: dict[str, str]) -> str | None:
    """Why this invocation may not run, or None."""
    if args.repeats < 1:
        return "--repeats must be at least 1"
    if args.report is not None:
        resolved = args.report.resolve()
        if LOCAL_DIR.resolve() not in resolved.parents:
            return f"--report must be inside {LOCAL_DIR}"
    if not args.apply:
        return None
    if environ.get("CI"):
        return "refusing to call Amazon Bedrock while CI is set"
    if not args.confirmed:
        return f"--apply needs {CONFIRM_FLAG}: it bills model invocations to the calling account"
    return None


def select(cases: list, ids: list[str]) -> list:
    """The cases named by ``ids``, or all of them."""
    if not ids:
        return cases
    known = {case.id for case in cases}
    unknown = sorted(set(ids) - known)
    if unknown:
        raise Refused(f"unknown case ids {unknown}")
    return [case for case in cases if case.id in ids]


def print_plan(cases: list, runtime: runner.Runtime, repeats: int) -> None:
    """What a live run would do."""
    marks = runner.fingerprint(runtime, cases)
    print(f"model id (BEDROCK_MODEL_ID or the Runtime default): {runner.resolved_model_id()}")
    print(f"cases: {len(cases)} x {repeats} run(s); prompt {marks.prompt}, "
          f"tools {marks.tools}, cases {marks.cases}")
    print("tools the model sees:")
    for name in marks.tool_names:
        print(f"  {name}")
    for case in cases:
        tools = ", ".join(case.expected_tools) or "none"
        print(f"  {case.id}: {case.turn}; expects {tools}")
    print("dry run: nothing was sent. Add --apply " + CONFIRM_FLAG + " to run it.")


def verdict(summary: Summary, args: argparse.Namespace) -> list[str]:
    """The thresholds a summary misses, as sentences; empty when it meets them all."""
    missed = []
    if summary.selection_accuracy < args.min_selection:
        missed.append(
            f"selection accuracy {summary.selection_accuracy:.2f} < {args.min_selection:.2f}")
    if summary.forbidden_call_rate > args.max_forbidden:
        missed.append(
            f"forbidden-call rate {summary.forbidden_call_rate:.2f} > {args.max_forbidden:.2f}")
    accuracy = summary.argument_accuracy
    if accuracy is not None and accuracy < args.min_arguments:
        missed.append(f"argument accuracy {accuracy:.2f} < {args.min_arguments:.2f}")
    return missed


def case_floor_misses(scores: list[CaseScore]) -> list[str]:
    """One sentence for each case that passed too few of its runs."""
    totals: dict[str, int] = {}
    for score in scores:
        totals[score.case_id] = totals.get(score.case_id, 0) + 1
    return [
        f"case {case_id} passed too few runs (needs {required_passes(totals[case_id])} "
        f"of {totals[case_id]})"
        for case_id in failing_cases(scores)
    ]


def describe(score: CaseScore) -> str:
    """One line for a run."""
    if score.error:
        return f"ERROR   {score.case_id}: {score.error}"
    bad_args = [f"{t}.{a}" for t, a, ok in score.argument_checks if not ok]
    status = "PASS" if score.selected and not score.forbidden and not bad_args else "FAIL"
    detail = []
    if score.missing:
        detail.append(f"missing {', '.join(score.missing)}")
    if score.unexpected:
        detail.append(f"unexpected {', '.join(score.unexpected)}")
    if score.forbidden:
        detail.append(f"FORBIDDEN {', '.join(score.forbidden)}")
    if bad_args:
        detail.append(f"arguments {', '.join(bad_args)}")
    return f"{status:7} {score.case_id}" + (f": {'; '.join(detail)}" if detail else "")


def write_report(
    path: Path, summary: Summary, scores: list[CaseScore], marks, model_id: str
) -> None:
    """Save the run as JSON, with what it was measured against."""
    body = {
        "model_id": model_id,
        "prompt": marks.prompt, "tools": marks.tools, "cases": marks.cases,
        "summary": summary.__dict__,
        "runs": [score.__dict__ for score in scores],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, indent=2, default=list) + "\n", encoding="utf-8")


def live_run(cases: list, runtime: runner.Runtime, args: argparse.Namespace) -> int:
    """Send the cases to Amazon Bedrock and report. Only reached with both flags."""
    model = runner.load_runtime_model()
    model_id = runner.resolved_model_id()
    print(f"model id: {model_id}")
    scores = runner.run_cases(
        cases, model, runtime, repeats=args.repeats, progress=lambda s: print(describe(s)))
    summary = summarize(scores)
    arguments = "n/a" if summary.argument_accuracy is None else f"{summary.argument_accuracy:.2f}"
    print(f"runs {summary.runs} (errored {summary.errors}); "
          f"selection {summary.selection_accuracy:.2f}; "
          f"forbidden-call rate {summary.forbidden_call_rate:.2f}; "
          f"argument accuracy {arguments} over {summary.argument_checks} checks")
    if args.report:
        write_report(args.report, summary, scores, runner.fingerprint(runtime, cases), model_id)
    if summary.runs == 0:
        return EXIT_INCONCLUSIVE
    missed = verdict(summary, args) + case_floor_misses(scores)
    for line in missed:
        print(f"MISSED: {line}")
    if missed:
        return EXIT_MISSED
    return EXIT_INCONCLUSIVE if summary.errors else EXIT_PASS


def main(argv: Sequence[str] | None = None, environ: dict[str, str] | None = None) -> int:
    """Run the dry run or the live run; return the exit code."""
    try:
        args = parse_args(argv)
    except SystemExit as exit_:
        return int(exit_.code or 0)
    reason = refusal(args, dict(os.environ) if environ is None else environ)
    if reason:
        print(f"refused: {reason}", file=sys.stderr)
        return EXIT_REFUSED
    try:
        cases = select(load_cases(), args.case)
    except (CaseError, Refused) as error:
        print(f"refused: {error}", file=sys.stderr)
        return EXIT_REFUSED
    runtime = runner.load_runtime()
    if not args.apply:
        print_plan(cases, runtime, args.repeats)
        return EXIT_PASS
    return live_run(cases, runtime, args)


if __name__ == "__main__":
    sys.exit(main())
