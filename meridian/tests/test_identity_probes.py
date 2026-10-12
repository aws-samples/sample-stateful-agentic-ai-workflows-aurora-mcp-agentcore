"""The probe plan runs against fakes, and a broken layer is caught by the probe that watches it."""

import pytest

from scripts import stop_and_resume_proof as recovery
from scripts.identity_probes.probes import (
    DECOY_TRAVELER,
    JORDAN_TRAVELER,
    PLAN,
    THREAD_PREFIX,
    Context,
    build_hold_arguments,
    run_probe,
    select,
)
from scripts.identity_probes.receipt import ALLOWED, DECOY, ERROR, JORDAN, LAYERS, REFUSED
from scripts.identity_probes.runner import tidy
from tests.identity_proof_support import (
    PACKAGE,
    FakeCleanup,
    good_world,
    lambda_hold,
    text_result,
)


def run_all(ports, design="both", only=None):
    ctx = Context(run_id="abc12345", design=design)
    chosen = [spec for spec in PLAN if only is None or spec.id in only]
    return ctx, {spec.id: run_probe(spec, ports, ctx) for spec in chosen}


def test_the_plan_has_seventeen_unique_probes_and_every_layer_has_both_kinds():
    ids = [spec.id for spec in PLAN]

    assert len(ids) == 17 and len(set(ids)) == 17
    for layer in LAYERS:
        mine = [spec for spec in PLAN if spec.layer == layer]
        assert any(s.actor == DECOY and s.expected == REFUSED for s in mine), layer
        assert any(s.actor == JORDAN and s.expected == ALLOWED for s in mine), layer


def test_the_gateway_reads_the_package_before_any_hold_is_built():
    order = [spec.id for spec in PLAN if spec.layer == "gateway"]

    assert order == ["gateway.jordan_reads_package", "gateway.decoy_reads_package",
                     "gateway.decoy_holds_for_jordan", "gateway.jordan_places_hold"]


def test_the_runtime_and_the_gateway_each_have_a_control_the_decoy_is_allowed():
    allowed = {spec.id for spec in PLAN if spec.actor == DECOY and spec.expected == ALLOWED}

    assert {"runtime.decoy_pings_workflow", "gateway.decoy_reads_package"} <= allowed


def test_no_two_probes_share_a_thread_or_a_conversation_name():
    ports, _ = good_world()
    ctx, _ = run_all(ports)

    assert len(ctx.threads) == len(set(ctx.threads))
    assert len(ctx.memory_sessions) == len(set(ctx.memory_sessions)) == 2


def test_probe_threads_are_ones_the_recovery_purge_accepts():
    assert THREAD_PREFIX.startswith(recovery.PROOF_THREAD_PREFIX)


def test_in_a_correct_system_every_probe_passes_and_each_refusal_names_its_layer():
    ports, _ = good_world()

    ctx, outcomes = run_all(ports)

    failed = [(o.probe, o.result, o.detail) for o in outcomes.values() if not o.passed]
    assert failed == []
    assert outcomes["backend.decoy_reads_jordan_memory"].refused_by == "backend_identity_check"
    assert outcomes["runtime.decoy_tampers_workflow"].refused_by == "runtime_traveler_check"
    assert outcomes["gateway.decoy_holds_for_jordan"].refused_by == "gateway_workload_grant"
    assert outcomes["database.decoy_sees_jordan_rows"].refused_by == "database_rls"
    assert outcomes["gateway.decoy_holds_for_jordan"].evidence["deny_audit_rows"] == 1
    assert outcomes["gateway.decoy_holds_for_jordan"].evidence["shape"] == "result.isError"
    assert ctx.owned_bookings == {(JORDAN_TRAVELER, "HLD-TEST0001")}
    assert any(t.startswith(THREAD_PREFIX + "abc12345") for t in ctx.threads)


def test_jordan_only_selects_just_the_controls():
    assert {spec.actor for spec in select(jordan_only=True)} == {JORDAN}
    assert len(select(jordan_only=True)) == 8 and len(select(jordan_only=False)) == 17


def test_rls_that_hides_nothing_fails_the_database_probe():
    ports, database = good_world()
    database.rls_hides = False

    _, outcomes = run_all(ports, only={"database.decoy_sees_jordan_rows"})

    assert outcomes["database.decoy_sees_jordan_rows"].result == ALLOWED
    assert not outcomes["database.decoy_sees_jordan_rows"].passed


def test_a_backend_that_lets_the_decoy_through_fails_its_probe():
    ports, _ = good_world()
    broken = ports.__class__(**{**ports.__dict__, "http": lambda *a: (200, {"ok": True})})

    _, outcomes = run_all(broken, only={"backend.decoy_reads_jordan_memory"})

    assert outcomes["backend.decoy_reads_jordan_memory"].result == ALLOWED
    assert not outcomes["backend.decoy_reads_jordan_memory"].passed


def test_a_gateway_that_accepts_the_decoy_and_books_for_jordan_is_an_error_and_is_tracked():
    ports, database = good_world()

    def accept_everything(user, tool, arguments):
        if tool.endswith("get_package_details"):
            return text_result({"package": PACKAGE})
        database.add_booking(JORDAN_TRAVELER, "HLD-LEAK0001", arguments["journeyRef"])
        return text_result(lambda_hold("HLD-LEAK0001"))

    broken = ports.__class__(**{**ports.__dict__, "gateway": accept_everything})

    ctx, outcomes = run_all(broken, only={"gateway.jordan_reads_package",
                                           "gateway.decoy_holds_for_jordan"})

    assert outcomes["gateway.decoy_holds_for_jordan"].result == ERROR
    assert "HLD-LEAK0001" in outcomes["gateway.decoy_holds_for_jordan"].detail
    assert ctx.owned_bookings == {(JORDAN_TRAVELER, "HLD-LEAK0001")}


def test_a_hold_booked_for_the_decoy_is_tracked_under_the_decoy():
    ports, database = good_world()

    def rewrites_to_the_decoy(user, tool, arguments):
        if tool.endswith("get_package_details"):
            return text_result({"package": PACKAGE})
        database.add_booking(DECOY_TRAVELER, "HLD-DECOY001", arguments["journeyRef"])
        return text_result(lambda_hold("HLD-DECOY001"))

    broken = ports.__class__(**{**ports.__dict__, "gateway": rewrites_to_the_decoy})

    ctx, outcomes = run_all(broken, only={"gateway.jordan_reads_package",
                                           "gateway.decoy_holds_for_jordan"})

    assert outcomes["gateway.decoy_holds_for_jordan"].result == ERROR
    assert ctx.owned_bookings == {(DECOY_TRAVELER, "HLD-DECOY001")}


def test_a_booking_is_recorded_even_when_the_gateway_call_then_raises():
    ports, database = good_world()

    def books_then_fails(user, tool, arguments):
        if tool.endswith("get_package_details"):
            return text_result({"package": PACKAGE})
        database.add_booking(JORDAN_TRAVELER, "HLD-LATE0001", arguments["journeyRef"])
        raise TimeoutError("the response never arrived")

    broken = ports.__class__(**{**ports.__dict__, "gateway": books_then_fails})

    ctx, outcomes = run_all(broken, only={"gateway.jordan_reads_package",
                                           "gateway.jordan_places_hold"})

    assert outcomes["gateway.jordan_places_hold"].result == ERROR
    assert ctx.owned_bookings == {(JORDAN_TRAVELER, "HLD-LATE0001")}


def test_a_booking_this_run_cannot_tie_to_itself_is_reported_and_never_owned():
    ports, database = good_world()
    real_gateway = ports.gateway

    def with_a_concurrent_booking(user, tool, arguments):
        if tool.endswith("create_courtesy_hold"):
            database.add_booking(JORDAN_TRAVELER, "HLD-REALUSER", None)
        return real_gateway(user, tool, arguments)

    noisy = ports.__class__(**{**ports.__dict__, "gateway": with_a_concurrent_booking})

    ctx, outcomes = run_all(noisy, only={"gateway.jordan_reads_package",
                                          "gateway.jordan_places_hold"})

    assert (JORDAN_TRAVELER, "HLD-REALUSER") not in ctx.owned_bookings
    assert ctx.owned_bookings == {(JORDAN_TRAVELER, "HLD-TEST0001")}
    assert outcomes["gateway.jordan_places_hold"].result == ERROR
    assert "HLD-REALUSER" in outcomes["gateway.jordan_places_hold"].detail


def test_jordans_hold_without_a_visible_booking_is_an_error_not_a_pass():
    ports, database = good_world()
    real_gateway = ports.gateway

    def books_nothing_visible(user, tool, arguments):
        raw = real_gateway(user, tool, arguments)
        database.bookings[JORDAN_TRAVELER].clear()
        database.holds.clear()
        return raw

    blind = ports.__class__(**{**ports.__dict__, "gateway": books_nothing_visible})

    _, outcomes = run_all(blind, only={"gateway.jordan_reads_package",
                                        "gateway.jordan_places_hold"})

    assert outcomes["gateway.jordan_places_hold"].result == ERROR
    assert "no booking" in outcomes["gateway.jordan_places_hold"].detail


def test_an_order_that_books_something_for_the_decoy_probe_is_an_error_with_the_booking_named():
    ports, database = good_world()

    def books(user, method, path, body):
        database.add_booking(DECOY_TRAVELER, "HLD-ORDER001", None)
        return 200, {"message": "Held.", "order": {"order_id": "HLD-ORDER001", "status": "held"},
                     "activities": []}

    broken = ports.__class__(**{**ports.__dict__, "http": books})

    ctx, outcomes = run_all(broken, only={"backend.decoy_orders_for_jordan"})

    outcome = outcomes["backend.decoy_orders_for_jordan"]
    assert outcome.result == ERROR and "HLD-ORDER001" in outcome.detail
    assert ctx.owned_bookings == {(DECOY_TRAVELER, "HLD-ORDER001")}


def test_the_concierge_session_names_are_recorded_for_the_residue_note():
    ports, _ = good_world()

    ctx, _ = run_all(ports, only={"runtime.decoy_tampers_concierge",
                                   "runtime.jordan_opens_concierge"})

    notes = ctx.residue_notes()
    assert any("AgentCore Memory" in n and ctx.memory_sessions[1] in n for n in notes)
    assert any("append-only" in n for n in notes)


def test_the_concierge_is_read_to_its_result_not_abandoned_at_the_first_event():
    ports, _ = good_world()
    seen = []

    def recording(user, runtime, payload, limit):
        seen.append((runtime, limit))
        return [{"type": "result", "message": "hi"}]

    probe = ports.__class__(**{**ports.__dict__, "runtime": recording})

    run_all(probe, only={"runtime.jordan_opens_concierge"})

    assert seen[0][0] == "concierge" and seen[0][1] >= 1000


@pytest.mark.parametrize("probe", ["runtime.decoy_tampers_workflow",
                                   "runtime.decoy_tampers_concierge"])
@pytest.mark.parametrize("message", [
    "The forwarded access token was refused: traveler.",
    "No signed-in caller: no bearer token was forwarded.",
])
def test_a_runtime_that_refuses_the_token_does_not_pass_a_tamper_probe(probe, message):
    ports, _ = good_world()
    token_trouble = ports.__class__(**{**ports.__dict__, "runtime": lambda *a: [
        {"type": "error", "code": "authorization", "message": message}]})

    _, outcomes = run_all(token_trouble, only={probe})

    assert outcomes[probe].result == ERROR and not outcomes[probe].passed


@pytest.mark.parametrize("probe", ["backend.decoy_reads_jordan_memory",
                                   "backend.decoy_orders_for_jordan"])
@pytest.mark.parametrize(("status", "body"), [
    (401, {"detail": "Invalid Bearer token"}), (500, "boom"),
    (403, {"error": "Forbidden by something else"}),
])
def test_a_backend_failure_that_is_not_the_identity_check_does_not_pass(probe, status, body):
    ports, _ = good_world()
    failing = ports.__class__(**{**ports.__dict__, "http": lambda *a: (status, body)})

    _, outcomes = run_all(failing, only={probe})

    assert outcomes[probe].result == ERROR and not outcomes[probe].passed


@pytest.mark.parametrize("raw", [
    {"error": {"http_status": 401, "message": "Invalid Bearer token"}},
    {"error": {"http_status": 500, "message": "boom"}},
    text_result({"error": "validation: bad arg"}),
])
def test_a_gateway_failure_without_refusal_evidence_does_not_pass_the_decoy_probe(raw):
    ports, _ = good_world()

    def failing(user, tool, arguments):
        return text_result({"package": PACKAGE}) if tool.endswith("details") else raw

    broken = ports.__class__(**{**ports.__dict__, "gateway": failing})

    _, outcomes = run_all(broken, only={"gateway.jordan_reads_package",
                                         "gateway.decoy_holds_for_jordan"})

    assert outcomes["gateway.decoy_holds_for_jordan"].result == ERROR
    assert not outcomes["gateway.decoy_holds_for_jordan"].passed


def test_a_probe_that_raises_becomes_an_error_outcome_and_hides_no_other():
    ports, _ = good_world()

    def explode(*args):
        raise RuntimeError("Runtime said 123456789012 is unreachable")

    broken = ports.__class__(**{**ports.__dict__, "runtime": explode})

    _, outcomes = run_all(broken)

    runtime = outcomes["runtime.decoy_tampers_workflow"]
    assert runtime.result == ERROR and "<acct>" in runtime.detail
    assert outcomes["backend.jordan_me_is_jordan"].passed


def test_a_hold_cannot_be_built_before_the_package_was_read():
    ports, _ = good_world()

    _, outcomes = run_all(ports, only={"gateway.decoy_holds_for_jordan"})

    assert outcomes["gateway.decoy_holds_for_jordan"].result == ERROR
    assert "package details were not read" in outcomes["gateway.decoy_holds_for_jordan"].detail


def test_hold_arguments_use_the_catalog_price_an_open_duration_and_a_fresh_reference():
    arguments = build_hold_arguments(PACKAGE, "trv_x", "phase5-proof-idpabc12345j")

    assert arguments == {
        "travelerId": "trv_x", "packageId": "CTY-002", "duration": "5 nights", "travelers": 1,
        "unitPriceCents": 159900, "totalCents": 159900, "holdMinutes": 15,
        "travelerConfirmed": True, "budgetCeilingCents": 259900,
        "journeyRef": "phase5-proof-idpabc12345j",
    }


@pytest.mark.parametrize("package", [
    {}, {"package_id": "X", "price_per_person": "10", "availability": {"3 nights": 0}},
    {"package_id": "X", "availability": {"3 nights": 2}},
    {"package_id": "X", "price_per_person": "n/a", "availability": {"3 nights": 2}},
])
def test_a_package_that_cannot_make_a_hold_is_refused_with_a_reason(package):
    with pytest.raises(ValueError):
        build_hold_arguments(package, "trv_x", "ref")


def test_a_hold_is_tied_by_the_bookingId_in_the_lambdas_real_payload():
    ports, database = good_world()

    def books_without_a_journey_record(user, tool, arguments):
        if tool.endswith("get_package_details"):
            return text_result({"package": PACKAGE})
        database.add_booking(JORDAN_TRAVELER, "HLD-PAYLOAD1", None)
        return text_result(lambda_hold("HLD-PAYLOAD1"))

    world = ports.__class__(**{**ports.__dict__, "gateway": books_without_a_journey_record})

    ctx, outcomes = run_all(world, only={"gateway.jordan_reads_package",
                                          "gateway.jordan_places_hold"})
    cleanup = FakeCleanup()
    tidy(cleanup, ctx)

    assert outcomes["gateway.jordan_places_hold"].result == ALLOWED
    assert ctx.owned_bookings == {(JORDAN_TRAVELER, "HLD-PAYLOAD1")}
    assert cleanup.released == [(JORDAN_TRAVELER, "HLD-PAYLOAD1")]


def test_a_top_level_bookingId_is_not_where_the_lambda_puts_it():
    ports, database = good_world()

    def wrong_shape(user, tool, arguments):
        if tool.endswith("get_package_details"):
            return text_result({"package": PACKAGE})
        database.add_booking(JORDAN_TRAVELER, "HLD-FLAT0001", None)
        return text_result({"bookingId": "HLD-FLAT0001"})

    world = ports.__class__(**{**ports.__dict__, "gateway": wrong_shape})

    ctx, outcomes = run_all(world, only={"gateway.jordan_reads_package",
                                          "gateway.jordan_places_hold"})

    assert ctx.owned_bookings == set()
    assert outcomes["gateway.jordan_places_hold"].result == ERROR


def test_an_order_is_tied_by_the_order_id_of_the_real_order_response():
    ports, database = good_world()

    def books(user, method, path, body):
        database.add_booking(DECOY_TRAVELER, "HLD-ORDER002", None)
        return 200, {"message": "Held.", "activities": [],
                     "order": {"order_id": "HLD-ORDER002", "status": "held", "items": []}}

    world = ports.__class__(**{**ports.__dict__, "http": books})

    ctx, _ = run_all(world, only={"backend.decoy_orders_for_jordan"})

    assert ctx.owned_bookings == {(DECOY_TRAVELER, "HLD-ORDER002")}


def test_a_decoy_ping_the_runtime_refuses_does_not_pass():
    ports, _ = good_world()
    refusing = ports.__class__(**{**ports.__dict__, "runtime": lambda *a: [
        {"type": "error", "code": "authorization", "message": "The token was refused."}]})

    _, outcomes = run_all(refusing, only={"runtime.decoy_pings_workflow"})

    assert outcomes["runtime.decoy_pings_workflow"].result == ERROR
    assert not outcomes["runtime.decoy_pings_workflow"].passed


def test_a_decoy_package_read_the_gateway_refuses_does_not_pass():
    ports, _ = good_world()

    def refuses_the_decoy(user, tool, arguments):
        if user == DECOY:
            return {"result": {"isError": True, "content": [
                {"type": "text", "text": "Tool Execution Denied: not allowed"}]}}
        return text_result({"package": PACKAGE})

    world = ports.__class__(**{**ports.__dict__, "gateway": refuses_the_decoy})

    _, outcomes = run_all(world, only={"gateway.decoy_reads_package"})

    assert outcomes["gateway.decoy_reads_package"].result == REFUSED
    assert not outcomes["gateway.decoy_reads_package"].passed
