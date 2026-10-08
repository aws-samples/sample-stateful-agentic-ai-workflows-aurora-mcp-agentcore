"""The identity proof's receipt: pass rules, coverage, secrets and the printed table."""

import json
import stat

import pytest

from scripts.identity_probes.receipt import (
    ALLOWED,
    DECOY,
    ERROR,
    JORDAN,
    JORDAN_ONLY,
    LAYERS,
    REFUSED,
    Outcome,
    Receipt,
    leaks,
    render_table,
    scrub,
    summary_rows,
    write_receipt,
)

FAKE_TOKEN = "e" + "yJhbGciOiJSUzI1NiJ9" + "." + "e" + "yJzdWIiOiJ4In0" + ".c2lnbmF0dXJl"


def outcome(*, layer="backend", actor=DECOY, expected=REFUSED, result=REFUSED,
            refused_by="backend_identity_check", probe=None, detail="HTTP 403") -> Outcome:
    return Outcome(probe=probe or f"{layer}.{actor}", layer=layer, actor=actor, expected=expected,
                   result=result, refused_by=refused_by, detail=detail)


def receipt(mode="full") -> Receipt:
    return Receipt(at="2026-10-08T12:00:00+00:00", git_sha="a" * 40, region="us-east-1",
                   design="both", site_host="site.example.net", mode=mode)


def complete() -> Receipt:
    built = receipt()
    for layer in LAYERS:
        refuser = "database_rls" if layer == "database" else "backend_identity_check"
        built.outcomes.append(outcome(layer=layer, refused_by=refuser))
        built.outcomes.append(outcome(layer=layer, actor=JORDAN, expected=ALLOWED, result=ALLOWED,
                                      refused_by=None, probe=f"{layer}.jordan"))
    return built


@pytest.mark.parametrize(("expected", "result", "refused_by", "passed"), [
    (REFUSED, REFUSED, "backend_identity_check", True),
    (REFUSED, REFUSED, "not_attributed", False),
    (REFUSED, REFUSED, "made_up_layer", False),
    (REFUSED, REFUSED, None, False),
    (REFUSED, ALLOWED, None, False),
    (REFUSED, ERROR, None, False),
    (ALLOWED, ALLOWED, None, True),
    (ALLOWED, REFUSED, "gateway_cedar", False),
])
def test_an_outcome_passes_only_when_the_result_is_the_expectation(
        expected, result, refused_by, passed):
    assert outcome(expected=expected, result=result, refused_by=refused_by).passed is passed


def test_a_complete_receipt_is_ok():
    assert complete().ok is True and complete().coverage_gaps() == []


def test_a_layer_without_a_decoy_refusal_is_a_gap_and_not_ok():
    built = complete()
    built.outcomes = [o for o in built.outcomes if not (o.layer == "database" and o.actor == DECOY)]

    assert built.coverage_gaps() == ["database: no decoy refusal probe ran"]
    assert built.ok is False


def test_a_jordan_only_receipt_needs_only_the_controls():
    built = receipt(mode=JORDAN_ONLY)
    built.outcomes = [o for o in complete().outcomes if o.actor == JORDAN]

    assert built.coverage_gaps() == [] and built.ok is True


def test_a_layer_without_a_jordan_control_is_a_gap():
    built = complete()
    built.outcomes = [o for o in built.outcomes if not (o.layer == "gateway" and o.actor == JORDAN)]

    assert built.coverage_gaps() == ["gateway: no Jordan control ran"]
    assert built.ok is False


def test_a_receipt_fails_when_jordan_is_refused_at_a_layer_even_if_the_decoy_was_refused():
    built = complete()
    index = next(i for i, o in enumerate(built.outcomes)
                 if o.layer == "runtime" and o.actor == JORDAN)
    built.outcomes[index] = outcome(layer="runtime", actor=JORDAN, expected=ALLOWED,
                                    result=REFUSED, refused_by="runtime_traveler_check")

    assert built.ok is False
    assert built.coverage_gaps() == []
    assert render_table(built).rstrip().endswith("RESULT: FAIL")


def test_a_decoy_refusal_is_unproven_where_jordan_was_not_allowed():
    built = complete()
    index = next(i for i, o in enumerate(built.outcomes)
                 if o.layer == "runtime" and o.actor == JORDAN)
    built.outcomes[index] = outcome(layer="runtime", actor=JORDAN, expected=ALLOWED,
                                    result=ERROR, refused_by=None)

    row = next(r for r in summary_rows(built) if r["layer"] == "runtime")

    assert row["decoy"] == "unproven" and row["jordan"] == "FAILED" and row["refused_by"] == ""


def test_a_layer_with_no_jordan_control_shows_the_decoy_as_unproven():
    built = complete()
    built.outcomes = [o for o in built.outcomes if not (o.layer == "gateway" and o.actor == JORDAN)]

    row = next(r for r in summary_rows(built) if r["layer"] == "gateway")

    assert row["decoy"] == "unproven" and row["jordan"] == "not run"


def test_from_dict_recomputes_what_a_stored_file_claims():
    stored = complete().to_dict()
    stored["ok"] = True
    stored["outcomes"][0].update(result="allowed", refused_by=None, passed=True)

    rebuilt = Receipt.from_dict(stored)

    assert rebuilt.ok is False and rebuilt.to_dict()["outcomes"][0]["passed"] is False


@pytest.mark.parametrize("damage", [
    lambda d: d.pop("outcomes"), lambda d: d.update(schema=99),
    lambda d: d["outcomes"][0].pop("probe"), lambda d: d.update(mode="sideways"),
])
def test_from_dict_rejects_what_is_not_a_receipt(damage):
    stored = complete().to_dict()
    damage(stored)

    with pytest.raises((KeyError, TypeError, ValueError)):
        Receipt.from_dict(stored)


def test_a_leftover_fails_the_receipt():
    built = complete()
    built.cleanup = {"leftovers": 1}

    assert built.ok is False


def test_a_cleanup_problem_fails_the_receipt_even_with_no_leftover():
    built = complete()
    built.cleanup = {"leftovers": 0, "problems": ["release: RuntimeError: gone"]}

    assert built.ok is False


def test_one_failed_probe_fails_the_receipt():
    built = complete()
    built.outcomes[0] = outcome(result=ALLOWED, refused_by=None)

    assert built.ok is False


def test_leaks_finds_a_token_an_account_id_and_a_key_id():
    assert leaks("sub " + FAKE_TOKEN) == ["token"]
    assert leaks("account 123456789012 here") == ["account id"]
    assert leaks("key " + "AKIA" + "ABCDEFGHIJKLMNOP") == ["access key id"]
    assert leaks("nothing here") == []


def test_scrub_masks_and_truncates():
    text = scrub("denied for 123456789012 with " + FAKE_TOKEN + " " + "x" * 400)

    assert "123456789012" not in text and FAKE_TOKEN not in text and len(text) <= 160


def test_the_account_is_always_masked():
    assert complete().to_dict()["account"] == "<acct>"


def test_write_receipt_refuses_a_leak_and_writes_nothing(tmp_path):
    built = complete()
    built.outcomes[0] = Outcome(
        probe="backend.decoy", layer="backend", actor=DECOY, expected=REFUSED, result=REFUSED,
        refused_by="backend_identity_check", detail="ok", evidence={"note": FAKE_TOKEN})
    target = tmp_path / "receipt.json"

    with pytest.raises(ValueError, match="token"):
        write_receipt(target, built)

    assert not target.exists() and not list(tmp_path.iterdir())


def test_write_receipt_is_private_and_round_trips(tmp_path):
    target = tmp_path / "out" / "receipt.json"

    write_receipt(target, complete())

    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    data = json.loads(target.read_text())
    assert data["ok"] is True and data["schema"] == 1 and len(data["outcomes"]) == 8
    assert [row["layer"] for row in data["summary"]] == list(LAYERS)


def test_summary_rows_name_the_refusing_mechanism():
    rows = summary_rows(complete())

    assert rows[0] == {"layer": "backend", "decoy": REFUSED, "jordan": ALLOWED,
                       "refused_by": "Backend: token traveler check"}
    assert rows[3]["refused_by"] == "AWS Aurora: row-level security"


def test_the_table_lists_every_probe_and_the_result():
    text = render_table(complete())

    assert "LAYER" in text and "backend.decoy" in text and text.rstrip().endswith("RESULT: PASS")


def test_the_table_lists_the_gaps_when_the_run_fails():
    built = complete()
    built.outcomes = built.outcomes[:-2]

    text = render_table(built)

    assert "database: no decoy refusal probe ran" in text and text.rstrip().endswith("RESULT: FAIL")
