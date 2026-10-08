"""The probe plan runs against fakes, and a broken layer is caught by the probe that watches it."""

import pytest

from scripts import stop_and_resume_proof as recovery
from scripts.identity_probes.probes import (
    PLAN,
    THREAD_PREFIX,
    Context,
    build_hold_arguments,
    run_probe,
    select,
)
from scripts.identity_probes.receipt import ALLOWED, DECOY, ERROR, JORDAN, LAYERS, REFUSED
from tests.identity_proof_support import PACKAGE, good_world, text_result


def run_all(ports, design="both", only=None):
    ctx = Context(run_id="abc12345", design=design)
    chosen = [spec for spec in PLAN if only is None or spec.id in only]
    return ctx, {spec.id: run_probe(spec, ports, ctx) for spec in chosen}


def test_the_plan_has_fifteen_unique_probes_and_every_layer_has_both_kinds():
    ids = [spec.id for spec in PLAN]

    assert len(ids) == 15 and len(set(ids)) == 15
    for layer in LAYERS:
        mine = [spec for spec in PLAN if spec.layer == layer]
        assert any(s.actor == DECOY and s.expected == REFUSED for s in mine), layer
        assert any(s.actor == JORDAN and s.expected == ALLOWED for s in mine), layer


def test_the_gateway_reads_the_package_before_any_hold_is_built():
    order = [spec.id for spec in PLAN if spec.layer == "gateway"]

    assert order == ["gateway.jordan_reads_package", "gateway.decoy_holds_for_jordan",
                     "gateway.jordan_places_hold"]


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
    assert ctx.created_bookings == {"HLD-TEST0001"}
    assert any(t.startswith(THREAD_PREFIX + "abc12345") for t in ctx.threads)


def test_jordan_only_selects_just_the_controls():
    assert {spec.actor for spec in select(jordan_only=True)} == {JORDAN}
    assert len(select(jordan_only=True)) == 8 and len(select(jordan_only=False)) == 15


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
        database.bookings.add("HLD-LEAK0001")
        return text_result({"bookingId": "HLD-LEAK0001"})

    broken = ports.__class__(**{**ports.__dict__, "gateway": accept_everything})

    ctx, outcomes = run_all(broken, only={"gateway.jordan_reads_package",
                                           "gateway.decoy_holds_for_jordan"})

    assert outcomes["gateway.decoy_holds_for_jordan"].result == ERROR
    assert "HLD-LEAK0001" in outcomes["gateway.decoy_holds_for_jordan"].detail
    assert ctx.created_bookings == {"HLD-LEAK0001"}


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
