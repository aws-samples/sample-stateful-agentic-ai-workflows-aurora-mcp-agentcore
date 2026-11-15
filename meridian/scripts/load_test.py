"""Measure the hosted Meridian stack under a small, read-only, open-loop load.

Dry run by default: it prints the plan and sends nothing. A live run needs
``--apply --i-understand-this-changes-aws``, never runs when ``CI`` is set, and calls only the
read endpoints in ``ALLOWED_READS`` plus read-only chat turns for Phases 1 to 3. It never creates,
confirms or releases a hold or booking, starts a Phase 5 workflow or writes a preference: every
request passes ``guard`` first, which refuses anything else.

Scenarios: ``catalog`` (read endpoints, no model calls), ``retrieval`` (Phase 3 chat turns, which
run the hybrid search) and ``chat`` (turns for Phases 1 to 3). The last two invoke Amazon Bedrock
and bill the account, so they need ``--max-turns``.

Hosted:  MERIDIAN_AGENTCORE_AUTH=jwt python scripts/load_test.py --hosted --scenario catalog \
             --apply --i-understand-this-changes-aws

Exit codes: 0 the run finished, 1 it stopped early on its error limits, 3 refused or usage error.
"""

from __future__ import annotations

import argparse
import http.client
import json
import math
import os
import re
import sys
import threading
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.prompt_ladder import PROMPT_LADDER  # noqa: E402
from scripts import published, warm_demo  # noqa: E402
from scripts.agentcore_caller import (  # noqa: E402
    TRAVELER_USERS, require_token_safe_url, traveler_for_user,
)

CONFIRM_FLAG = "--i-understand-this-changes-aws"
OVERRIDE_FLAG = "--allow-high-load"
LOCAL_DIR = Path(__file__).resolve().parents[1] / ".local"
EXIT_OK, EXIT_ABORTED, EXIT_REFUSED = 0, 1, 3

DEFAULT_RATE, DEFAULT_DURATION, DEFAULT_CONCURRENCY = 2.0, 60.0, 4
MAX_RATE, MAX_DURATION, MAX_CONCURRENCY, MAX_TURNS = 20.0, 600.0, 16, 200
DEFAULT_MAX_ERROR_RATE, DEFAULT_MAX_STREAK, MIN_SAMPLES = 0.05, 3, 10

NETWORK_ERROR = 0
BAD_REPLY = -1
STREAK_STATUSES = frozenset({NETWORK_ERROR, 401, 403, 429})
CHAT_PHASES = (1, 2, 3)
CHAT_BODY_KEYS = frozenset({"message", "phase", "customer_id"})
CHAT_PATH = "/api/chat"
ALLOWED_READS = (
    re.compile(r"/api/health"),
    re.compile(r"/api/products"),
    re.compile(r"/api/packages"),
    re.compile(r"/api/journeys"),
    re.compile(r"/api/me"),
    re.compile(r"/api/memory/[A-Za-z0-9_]+"),
)
SCENARIOS = ("catalog", "retrieval", "chat")
MODEL_SCENARIOS = frozenset({"retrieval", "chat"})


class WriteRefused(Exception):
    """A request the load test must never send."""


class Refused(Exception):
    """An invocation that may not run, with the reason."""


@dataclass(frozen=True)
class Req:
    """One request in a scenario: a label for the report, the call and an optional reply check."""

    name: str
    method: str
    path: str
    body: dict | None = None
    check: Callable[[dict], str | None] | None = None


@dataclass(frozen=True)
class Sample:
    """The outcome of one completed request."""

    name: str
    status: int
    seconds: float

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


@dataclass
class Recorder:
    """Collects samples and decides when the run must stop; safe across worker threads."""

    max_error_rate: float
    max_streak: int
    samples: list[Sample] = field(default_factory=list)
    abort_reason: str | None = None
    _streak: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, sample: Sample) -> None:
        """Record ``sample`` and set ``abort_reason`` when a limit is crossed."""
        with self._lock:
            self.samples.append(sample)
            bad = sample.status in STREAK_STATUSES or sample.status >= 500
            self._streak = self._streak + 1 if bad else 0
            if self.abort_reason:
                return
            total = len(self.samples)
            rate = sum(1 for s in self.samples if not s.ok) / total
            if self._streak >= self.max_streak:
                self.abort_reason = (
                    f"{self._streak} failures in a row (429, 5xx, auth or network); "
                    f"last status {sample.status}")
            elif total >= MIN_SAMPLES and rate > self.max_error_rate:
                self.abort_reason = (
                    f"error rate {rate:.0%} above {self.max_error_rate:.0%} "
                    f"after {total} requests")


@dataclass(frozen=True)
class Plan:
    """What a run will do."""

    scenario: str
    requests: list[Req]
    rate: float
    duration: float
    concurrency: int
    max_turns: int | None

    @property
    def attempts(self) -> int:
        """How many arrival slots the rate and duration give."""
        return max(1, int(self.rate * self.duration))

    @property
    def cap(self) -> int:
        """The most requests a run may send: every slot, or ``max_turns`` for model scenarios."""
        return min(self.attempts, self.max_turns) if self.max_turns else self.attempts


def guard(req: Req) -> None:
    """Refuse every request that is not an allowlisted read or a read-only Phase 1 to 3 turn.

    Raises:
        WriteRefused: When the method, path or chat body could change state.
    """
    path = req.path.split("?", 1)[0]
    if req.method == "GET" and req.body is None:
        if any(rule.fullmatch(path) for rule in ALLOWED_READS):
            return
    elif req.method == "POST" and path == CHAT_PATH:
        body = req.body or {}
        if set(body) <= CHAT_BODY_KEYS and body.get("phase") in CHAT_PHASES and body.get("message"):
            return
    raise WriteRefused(f"{req.method} {req.path} is not on the read-only allowlist")


def turn(phase: int, prompt: str, traveler: str) -> Req:
    """A read-only chat turn for ``phase``."""
    return Req(f"chat phase {phase}", "POST", CHAT_PATH,
               {"message": prompt, "phase": phase, "customer_id": traveler}, warm_demo.check_turn)


def build_requests(scenario: str, traveler: str) -> list[Req]:
    """The requests a scenario cycles through, each one checked by ``guard``."""
    if scenario == "catalog":
        requests = [
            Req("GET /api/health", "GET", "/api/health"),
            Req("GET /api/me", "GET", "/api/me"),
            Req("GET /api/products", "GET", "/api/products?limit=50"),
            Req("GET /api/packages", "GET", "/api/packages?limit=20"),
            Req("GET /api/packages featured", "GET", "/api/packages?featured=true&limit=6"),
            Req("GET /api/memory/{traveler}", "GET", f"/api/memory/{traveler}"),
            Req("GET /api/journeys", "GET", "/api/journeys?limit=5"),
        ]
    elif scenario == "retrieval":
        requests = [turn(3, prompt, traveler) for prompt in PROMPT_LADDER[3].works]
        requests = [Req("chat phase 3 retrieval", r.method, r.path, r.body, r.check)
                    for r in requests]
    else:
        requests = [turn(phase, PROMPT_LADDER[phase].works[0], traveler) for phase in CHAT_PHASES]
    for req in requests:
        guard(req)
    return requests


def percentile(values: list[float], pct: float) -> float | None:
    """The nearest-rank ``pct`` percentile of ``values``, or None when there are none."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def summarize_group(samples: list[Sample], wall: float) -> dict:
    """Count, errors by status, latency percentiles of the 2xx replies and achieved rate."""
    errors: dict[str, int] = {}
    for sample in samples:
        if not sample.ok:
            key = {NETWORK_ERROR: "network", BAD_REPLY: "bad_reply"}.get(
                sample.status, str(sample.status))
            errors[key] = errors.get(key, 0) + 1
    latencies = [s.seconds for s in samples if s.ok]
    return {
        "count": len(samples),
        "ok": len(latencies),
        "errors_by_status": errors,
        "p50_s": percentile(latencies, 50),
        "p95_s": percentile(latencies, 95),
        "p99_s": percentile(latencies, 99),
        "achieved_rps": round(len(samples) / wall, 3) if wall > 0 else 0.0,
    }


def summarize(samples: list[Sample], wall: float) -> dict:
    """The whole run and each endpoint label, as report sections."""
    by_name: dict[str, list[Sample]] = {}
    for sample in samples:
        by_name.setdefault(sample.name, []).append(sample)
    return {
        "total": summarize_group(samples, wall),
        "endpoints": {
            name: summarize_group(group, wall) for name, group in sorted(by_name.items())},
    }


def run_load(
    plan: Plan,
    send: Callable[[Req], int],
    recorder: Recorder,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Send ``plan`` open loop: arrivals at ``rate`` per second whether or not replies are back.

    A slot that finds every worker busy is counted as ``shed`` and not sent, so a slow stack
    meets a bounded number of in-flight requests. The run stops at the first abort reason.
    """
    slots = threading.BoundedSemaphore(plan.concurrency)
    counts = {"sent": 0, "shed": 0}

    def work(req: Req) -> None:
        started = clock()
        try:
            status = send(req)
        finally:
            slots.release()
        recorder.add(Sample(req.name, status, clock() - started))

    start = clock()
    with ThreadPoolExecutor(max_workers=plan.concurrency) as pool:
        for slot in range(plan.attempts):
            if counts["sent"] >= plan.cap or recorder.abort_reason:
                break
            wait = start + slot / plan.rate - clock()
            if wait > 0:
                sleep(wait)
            if not slots.acquire(blocking=False):
                counts["shed"] += 1
                continue
            pool.submit(work, plan.requests[counts["sent"] % len(plan.requests)])
            counts["sent"] += 1
    return {**counts, "wall_s": max(clock() - start, 1e-9)}


def make_sender(base: str, headers: dict[str, str]) -> Callable[[Req], int]:
    """A sender that passes each request through ``guard`` and returns its HTTP status."""
    def send(req: Req) -> int:
        guard(req)
        try:
            reply = warm_demo.call(base, headers, req.path, req.body)
        except urllib.error.HTTPError as error:
            return error.code
        except (OSError, http.client.HTTPException, ValueError):
            return NETWORK_ERROR
        if req.check and isinstance(reply, dict) and req.check(reply):
            return BAD_REPLY
        return 200
    return send


def redact_host(base: str) -> str:
    """The target host with any 12-digit account id masked."""
    return re.sub(r"\d{12}", "<acct>", urlparse(base).hostname or "unknown")


def build_report(plan: Plan, run: dict, recorder: Recorder, base: str, user: str) -> dict:
    """The JSON report: what ran, against which build, and what it measured."""
    return {
        "git_sha": published.head_commit(),
        "target_host": redact_host(base),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "scenario": plan.scenario,
        "user": user,
        "rate_rps": plan.rate,
        "duration_s": plan.duration,
        "concurrency": plan.concurrency,
        "max_turns": plan.max_turns,
        "sent": run["sent"],
        "shed": run["shed"],
        "wall_s": round(run["wall_s"], 3),
        "aborted": recorder.abort_reason,
        **summarize(recorder.samples, run["wall_s"]),
    }


def fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def print_table(report: dict) -> None:
    """A short summary table, one row per endpoint and a total."""
    print(f"{'endpoint':<30}{'count':>6}{'p50 s':>8}{'p95 s':>8}{'p99 s':>8}{'rps':>7}  errors")
    rows = [*report["endpoints"].items(), ("TOTAL", report["total"])]
    for name, row in rows:
        errors = ", ".join(f"{k}:{v}" for k, v in row["errors_by_status"].items()) or "none"
        print(f"{name:<30}{row['count']:>6}{fmt(row['p50_s']):>8}{fmt(row['p95_s']):>8}"
              f"{fmt(row['p99_s']):>8}{row['achieved_rps']:>7.2f}  {errors}")
    print(f"sent {report['sent']}, shed {report['shed']} (workers busy), "
          f"wall {report['wall_s']} s")
    print(f"STOPPED EARLY: {report['aborted']}" if report["aborted"] else "Run finished.")


def build_parser() -> argparse.ArgumentParser:
    """The command line."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], allow_abbrev=False)
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--hosted", action="store_true",
                        help="target the hosted site from the local release record")
    target.add_argument("--base-url", default=warm_demo.LOCAL_URL,
                        help=f"target this backend instead of {warm_demo.LOCAL_URL}")
    parser.add_argument("--scenario", required=True, choices=SCENARIOS)
    parser.add_argument("--rate", type=float, default=DEFAULT_RATE, help="arrivals per second")
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION, help="seconds")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY,
                        help="most requests in flight; a slot that finds none free is shed")
    parser.add_argument("--max-turns", type=int,
                        help="most chat turns to send; required for retrieval and chat")
    parser.add_argument("--max-error-rate", type=float, default=DEFAULT_MAX_ERROR_RATE)
    parser.add_argument("--max-streak", type=int, default=DEFAULT_MAX_STREAK,
                        help="stop after this many 429, 5xx, auth or network failures in a row")
    parser.add_argument("--as", dest="as_user", choices=sorted(set(TRAVELER_USERS.values())),
                        default=warm_demo.DEFAULT_USER, help="the seeded user to sign in as")
    parser.add_argument("--report", type=Path, help="write the JSON report here (inside .local)")
    parser.add_argument(OVERRIDE_FLAG, dest="high_load", action="store_true",
                        help="lift the caps on rate, duration, concurrency and turns")
    parser.add_argument("--apply", action="store_true", help="send requests (default: dry run)")
    parser.add_argument(CONFIRM_FLAG, dest="confirmed", action="store_true")
    return parser


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    """Parse the command line; any usage error exits 3, never 2."""
    try:
        return build_parser().parse_args(argv)
    except SystemExit as exit_:
        if exit_.code in (0, None):
            raise
        raise SystemExit(EXIT_REFUSED) from None


def limit_problem(args: argparse.Namespace) -> str | None:
    """Why the load numbers are out of range, or None."""
    if args.rate <= 0 or args.duration <= 0 or args.concurrency < 1:
        return "--rate, --duration and --concurrency must be positive"
    if not 0 <= args.max_error_rate <= 1 or args.max_streak < 1:
        return "--max-error-rate must be 0 to 1 and --max-streak at least 1"
    caps = (("--rate", args.rate, MAX_RATE), ("--duration", args.duration, MAX_DURATION),
            ("--concurrency", args.concurrency, MAX_CONCURRENCY),
            ("--max-turns", args.max_turns or 0, MAX_TURNS))
    over = [f"{flag} {value:g} is above {cap:g}" for flag, value, cap in caps if value > cap]
    if over and not args.high_load:
        return "; ".join(over) + f" (add {OVERRIDE_FLAG} to allow it)"
    return None


def refusal(args: argparse.Namespace, environ: dict[str, str]) -> str | None:
    """Why this invocation may not run, or None."""
    if args.scenario in MODEL_SCENARIOS and not args.max_turns:
        return f"--scenario {args.scenario} invokes Amazon Bedrock: pass --max-turns"
    if args.max_turns is not None and args.max_turns < 1:
        return "--max-turns must be at least 1"
    if args.report is not None and LOCAL_DIR.resolve() not in args.report.resolve().parents:
        return f"--report must be inside {LOCAL_DIR}"
    problem = limit_problem(args)
    if problem or not args.apply:
        return problem
    if environ.get("CI"):
        return "refusing to load the stack while CI is set"
    if not args.confirmed:
        return f"--apply needs {CONFIRM_FLAG}: it sends traffic, and chat turns bill Bedrock"
    return None


def print_plan(plan: Plan, base: str) -> None:
    """What a live run would do."""
    print(f"target {redact_host(base)}; scenario {plan.scenario}")
    print(f"up to {plan.cap} request(s): {plan.rate:g}/s for {plan.duration:g} s, "
          f"{plan.concurrency} in flight, stop at {plan.max_turns or 'no'} turn cap")
    for req in plan.requests:
        print(f"  {req.method} {req.path.split('?')[0]}  ({req.name})")
    print(f"dry run: nothing was sent. Add --apply {CONFIRM_FLAG} to run it.")


def default_report_path(scenario: str) -> Path:
    """A timestamped file inside ``.local``."""
    return LOCAL_DIR / f"load-test-{scenario}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"


def main(argv: list[str] | None = None, environ: dict[str, str] | None = None) -> int:
    """Plan, and with the confirmation run, a read-only load test; see the module docstring."""
    args = parse_args(argv)
    env = os.environ if environ is None else environ
    problem = refusal(args, env)
    if problem:
        print(f"Refused: {problem}", file=sys.stderr)
        return EXIT_REFUSED
    base = published.release_url(published.load_record()).rstrip("/") if args.hosted \
        else args.base_url.rstrip("/")
    plan = Plan(args.scenario, build_requests(args.scenario, traveler_for_user(args.as_user)),
                args.rate, args.duration, args.concurrency, args.max_turns)
    if not args.apply:
        print_plan(plan, base)
        return EXIT_OK
    require_token_safe_url(base, always=args.hosted)
    headers = warm_demo.request_headers(args.hosted, args.as_user)
    recorder = Recorder(args.max_error_rate, args.max_streak)
    run = run_load(plan, make_sender(base, headers), recorder)
    report = build_report(plan, run, recorder, base, args.as_user)
    path = args.report or default_report_path(plan.scenario)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2) + "\n")
    print_table(report)
    print(f"Report: {path}")
    return EXIT_ABORTED if recorder.abort_reason else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
