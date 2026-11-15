"""The load test paces arrivals, computes percentiles, stops early and never writes."""

from __future__ import annotations

import json
import threading
import urllib.error

import pytest

from backend import main as backend_main
from scripts import load_test, published, warm_demo
from scripts.load_test import Plan, Recorder, Req, Sample, WriteRefused
from tests.test_route_inventory import ROUTERS, api_routes

LIVE = ["--apply", load_test.CONFIRM_FLAG]
TRAVELER = "trv_meridian_demo"


def plan(rate=2.0, duration=5.0, concurrency=64, max_turns=None, scenario="catalog"):
    requests = load_test.build_requests(scenario, TRAVELER)
    return Plan(scenario, requests, rate, duration, concurrency, max_turns)


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def run(the_plan, send, recorder=None):
    clock = FakeClock()
    recorder = recorder or Recorder(0.05, 3)
    result = load_test.run_load(the_plan, send, recorder, clock=clock, sleep=clock.sleep)
    return result, recorder, clock


def test_arrivals_follow_the_rate_not_the_reply_time():
    sent = []
    result, _recorder, clock = run(plan(rate=4, duration=5), lambda req: sent.append(req) or 200)

    assert result["sent"] == 20
    assert len(sent) == 20
    assert sum(clock.sleeps) == pytest.approx(19 / 4)
    assert all(s == pytest.approx(0.25) for s in clock.sleeps[1:])


def test_requests_cycle_through_the_whole_scenario():
    seen = []
    run(plan(rate=7, duration=1), lambda req: seen.append(req.name) or 200)

    assert sorted(seen) == sorted(r.name for r in plan().requests)


def test_max_turns_stops_a_model_scenario():
    result, _r, _c = run(plan(rate=10, duration=60, max_turns=5, scenario="chat"), lambda r: 200)

    assert result["sent"] == 5


def test_a_busy_stack_sheds_arrivals_instead_of_queueing():
    gate = threading.Event()

    def slow(_req):
        gate.wait(5)
        return 200

    clock = FakeClock()
    recorder = Recorder(0.05, 3)

    def sleep_then_release(seconds):
        clock.sleep(seconds)
        if len(clock.sleeps) == 9:
            gate.set()

    result = load_test.run_load(plan(rate=10, duration=1, concurrency=1), slow, recorder,
                                clock=clock, sleep=sleep_then_release)

    assert result["sent"] + result["shed"] == 10
    assert result["sent"] >= 1 and result["shed"] >= 8


@pytest.mark.parametrize(("values", "pct", "expected"), [
    ([], 50, None),
    ([5.0], 99, 5.0),
    (list(map(float, range(1, 101))), 50, 50.0),
    (list(map(float, range(1, 101))), 95, 95.0),
    (list(map(float, range(1, 101))), 99, 99.0),
    ([3.0, 1.0, 2.0, 4.0], 50, 2.0),
    ([3.0, 1.0, 2.0, 4.0], 100, 4.0),
])
def test_percentile_is_nearest_rank(values, pct, expected):
    assert load_test.percentile(values, pct) == expected


def test_summary_counts_errors_by_status_and_times_only_successes():
    samples = [Sample("a", 200, 1.0), Sample("a", 200, 3.0), Sample("a", 503, 0.1),
               Sample("a", load_test.NETWORK_ERROR, 9.0), Sample("a", load_test.BAD_REPLY, 2.0),
               Sample("b", 200, 0.5)]

    out = load_test.summarize(samples, wall=10.0)

    a = out["endpoints"]["a"]
    assert a["count"] == 5 and a["ok"] == 2
    assert a["errors_by_status"] == {"503": 1, "network": 1, "bad_reply": 1}
    assert a["p50_s"] == 1.0 and a["p99_s"] == 3.0
    assert out["total"]["achieved_rps"] == 0.6


def test_a_streak_of_5xx_aborts_the_run():
    result, recorder, _c = run(plan(rate=10, duration=10), lambda req: 503)

    assert recorder.abort_reason and "in a row" in recorder.abort_reason
    assert result["sent"] < 100


def test_a_429_streak_aborts_even_after_many_successes():
    statuses = iter([200] * 6 + [429] * 200)
    _r, recorder, _c = run(plan(rate=10, duration=10), lambda req: next(statuses))

    assert "in a row" in recorder.abort_reason


def test_an_error_rate_above_the_limit_aborts_without_a_streak():
    statuses = iter([200, 200, 404] * 40)
    _r, recorder, _c = run(plan(rate=10, duration=10), lambda req: next(statuses))

    assert "error rate" in recorder.abort_reason


def test_a_healthy_run_does_not_abort():
    result, recorder, _c = run(plan(rate=10, duration=5), lambda req: 200)

    assert recorder.abort_reason is None and len(recorder.samples) == result["sent"] == 50


def test_the_error_rate_is_not_judged_on_a_tiny_sample():
    recorder = Recorder(0.05, 3)
    recorder.add(Sample("a", 404, 0.1))

    assert recorder.abort_reason is None


def app_routes():
    """Every route the application serves, from its routers and the app itself."""
    return [r for router in ROUTERS for r in api_routes(router)] + api_routes(backend_main.app)


def write_routes():
    return [(method, route.path) for route in app_routes()
            for method in sorted(route.methods - {"HEAD", "OPTIONS", "GET"})
            if route.path != load_test.CHAT_PATH]


def test_the_route_walk_covers_every_served_path():
    served = set(backend_main.app.openapi()["paths"])

    assert served <= {route.path for route in app_routes()}


def test_every_other_write_route_in_the_app_is_refused():
    routes = write_routes()

    assert ("POST", "/api/chat/order") in routes
    assert ("PATCH", "/api/memory/{traveler_id}/facts/{preference_key}") in routes
    for method, path in routes:
        concrete = path.replace("{", "").replace("}", "")
        for body in (None, {"message": "hi", "phase": 3}):
            with pytest.raises(WriteRefused):
                load_test.guard(Req("w", method, concrete, body))


@pytest.mark.parametrize("path", [
    "/api/chat/book", "/api/chat/order", "/api/chat/order/stream", "/api/chat/book/stream",
    "/api/chat/stream", "/api/chat/holds", "/api/chat/bookings/b1", "/api/journeys/j1",
    "/api/memory/trv_x/facts", "/api/diagnostics/rls-probe", "/api/packages/p1",
])
def test_paths_beyond_the_allowlist_are_refused(path):
    for method in ("GET", "POST", "DELETE", "PATCH"):
        body = {"message": "m", "phase": 1} if method == "POST" else None
        with pytest.raises(WriteRefused):
            load_test.guard(Req("x", method, path, body))


@pytest.mark.parametrize("body", [
    {"message": "m", "phase": 4}, {"message": "m", "phase": 5},
    {"message": "m", "phase": 3, "intent": "recovery"},
    {"message": "m", "phase": 3, "resume": True},
    {"message": "m", "phase": 3, "selected_package_id": "p1"},
    {"message": "", "phase": 3}, None,
])
def test_chat_turns_outside_phases_one_to_three_or_with_extras_are_refused(body):
    with pytest.raises(WriteRefused):
        load_test.guard(Req("c", "POST", "/api/chat", body))


def test_every_scenario_request_passes_the_guard_and_names_a_real_get_route():
    templates = [r.path.replace("{traveler_id}", TRAVELER) for r in app_routes()
                 if "GET" in r.methods]
    for scenario in load_test.SCENARIOS:
        for req in load_test.build_requests(scenario, TRAVELER):
            load_test.guard(req)
            if req.method == "GET":
                assert req.path.split("?")[0] in templates


def test_the_sender_refuses_a_write_before_any_network_call(monkeypatch):
    monkeypatch.setattr(warm_demo, "call", lambda *a: pytest.fail("sent"))
    send = load_test.make_sender("https://example.com", {})

    with pytest.raises(WriteRefused):
        send(Req("w", "POST", "/api/chat/book", {"x": 1}))


def raising(error):
    def call(*_args):
        raise error
    return call


def test_the_sender_maps_outcomes_to_statuses(monkeypatch):
    send = load_test.make_sender("https://example.com", {})
    req = Req("r", "GET", "/api/health")
    chat = Req("c", "POST", "/api/chat", {"message": "m", "phase": 1}, warm_demo.check_turn)

    monkeypatch.setattr(warm_demo, "call", raising(urllib.error.HTTPError("u", 503, "m", {}, None)))
    assert send(req) == 503
    monkeypatch.setattr(warm_demo, "call", raising(TimeoutError()))
    assert send(req) == load_test.NETWORK_ERROR
    monkeypatch.setattr(warm_demo, "call", lambda *a: {"status": "healthy"})
    assert send(req) == 200
    monkeypatch.setattr(warm_demo, "call", lambda *a: {"message": ""})
    assert send(chat) == load_test.BAD_REPLY


@pytest.fixture
def no_traffic(monkeypatch):
    monkeypatch.setattr(warm_demo, "call", lambda *a: pytest.fail("sent a request"))
    monkeypatch.setattr(warm_demo, "request_headers", lambda *a: pytest.fail("signed in"))


def test_a_dry_run_sends_nothing_and_exits_zero(no_traffic, capsys):
    code = load_test.main(["--scenario", "catalog"], environ={})

    assert code == 0
    out = capsys.readouterr().out
    assert "dry run: nothing was sent" in out and "GET /api/products" in out


def test_apply_without_the_confirmation_is_refused(no_traffic, capsys):
    assert load_test.main(["--scenario", "catalog", "--apply"], environ={}) == 3
    assert load_test.CONFIRM_FLAG in capsys.readouterr().err


def test_ci_refuses_a_confirmed_run(no_traffic, capsys):
    assert load_test.main(["--scenario", "catalog", *LIVE], environ={"CI": "true"}) == 3
    assert "CI" in capsys.readouterr().err


@pytest.mark.parametrize("scenario", ["chat", "retrieval"])
def test_model_scenarios_require_max_turns(scenario, no_traffic):
    assert load_test.main(["--scenario", scenario], environ={}) == 3
    assert load_test.main(["--scenario", scenario, "--max-turns", "5"], environ={}) == 0


@pytest.mark.parametrize("flags", [["--rate", "21"], ["--duration", "601"],
                                   ["--concurrency", "17"], ["--rate", "0"]])
def test_hard_caps_refuse_unless_overridden(flags, no_traffic):
    assert load_test.main(["--scenario", "catalog", *flags], environ={}) == 3


def test_the_override_lifts_a_cap(no_traffic):
    argv = ["--scenario", "catalog", "--rate", "30", load_test.OVERRIDE_FLAG]

    assert load_test.main(argv, environ={}) == 0


def test_a_report_outside_local_is_refused(no_traffic, tmp_path):
    argv = ["--scenario", "catalog", "--report", str(tmp_path / "r.json")]

    assert load_test.main(argv, environ={}) == 3


def test_a_usage_error_exits_three():
    with pytest.raises(SystemExit) as stopped:
        load_test.parse_args(["--scenario", "book"])

    assert stopped.value.code == 3


def test_an_abbreviated_flag_is_an_error():
    with pytest.raises(SystemExit) as stopped:
        load_test.parse_args(["--scenario", "catalog", "--app"])

    assert stopped.value.code == 3


def test_a_live_run_writes_a_report_with_no_account_id(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(load_test, "LOCAL_DIR", tmp_path / ".local")
    monkeypatch.setattr(warm_demo, "call", lambda *a: {"status": "healthy"})
    monkeypatch.setattr(published, "head_commit", lambda: "abc123")
    monkeypatch.setattr(published, "load_record", lambda: {})
    monkeypatch.setattr(published, "release_url",
                        lambda _r: "https://svc-123456789012.example.com/")
    monkeypatch.setattr(warm_demo, "request_headers", lambda *a: {})
    argv = ["--hosted", "--scenario", "catalog", "--rate", "20", "--duration", "1", *LIVE]

    code = load_test.main(argv, environ={})

    assert code == 0
    report = json.loads(next((tmp_path / ".local").glob("load-test-catalog-*.json")).read_text())
    assert report["git_sha"] == "abc123" and report["scenario"] == "catalog"
    assert report["target_host"] == "svc-<acct>.example.com"
    assert report["sent"] == 20 and report["total"]["ok"] == 20
    assert "123456789012" not in json.dumps(report)
    assert "TOTAL" in capsys.readouterr().out


def test_an_aborted_live_run_exits_one(monkeypatch, tmp_path):
    monkeypatch.setattr(load_test, "LOCAL_DIR", tmp_path / ".local")
    monkeypatch.setattr(published, "head_commit", lambda: "abc123")
    monkeypatch.setattr(warm_demo, "call", raising(urllib.error.HTTPError("u", 503, "m", {}, None)))
    monkeypatch.setattr(warm_demo, "request_headers", lambda *a: {})
    argv = ["--base-url", "http://127.0.0.1:9", "--scenario", "catalog", "--rate", "20",
            "--duration", "1", *LIVE]

    assert load_test.main(argv, environ={}) == 1
