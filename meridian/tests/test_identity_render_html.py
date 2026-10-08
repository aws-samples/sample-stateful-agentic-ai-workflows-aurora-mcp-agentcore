"""The receipt pages are self-contained, honest about failures and free of banned wording."""

import re

import pytest

from scripts.identity_probes.probes import Context
from scripts.identity_probes.render_html import full_html, pages, summary_html
from scripts.identity_probes.runner import Header, run_proof
from tests.identity_proof_support import FakeCleanup, good_world

HEADER = Header(at="2026-10-08T12:00:00+00:00", git_sha="abcdef0123456789" * 2 + "abcdef01",
                region="us-east-1", design="both", site_host="site.example.net")
BANNED = ("\u00b7", "\u2014", "\u2013", "demo", "Amazon Aurora")


def recorded(ports=None):
    world, _ = good_world()
    receipt = run_proof(ports or world, FakeCleanup(), Context(run_id="abc12345", design="both"),
                        HEADER)
    return receipt.to_dict()


def test_the_summary_has_one_row_per_layer_with_plain_words():
    page = summary_html(recorded())

    for word in ("Backend", "Runtimes", "Gateway", "AWS Aurora", "Refused", "Allowed"):
        assert word in page
    assert page.count("<tr>") == 5 and 'id="receipt"' in page
    assert "Recorded run of 2026-10-08, commit abcdef01" in page


def test_the_summary_is_free_of_banned_wording():
    page = summary_html(recorded())
    text = re.sub(r"<style>.*?</style>", "", page, flags=re.DOTALL)

    assert [word for word in BANNED if word.lower() in text.lower()] == []


def test_the_pages_make_no_external_request_and_run_no_script():
    for page in (summary_html(recorded()), full_html(recorded())):
        assert "http://" not in page and "https://" not in page and "<script" not in page


def test_a_failed_layer_is_shown_as_failed_and_not_hidden():
    world, _ = good_world()
    broken = world.__class__(**{**world.__dict__, "http": lambda *a: (200, {"ok": True})})

    page = summary_html(recorded(broken))

    assert "Failed" in page and 'class="bad"' in page


def test_values_are_escaped():
    data = recorded()
    data["summary"][0]["refused_by"] = "<b>x</b> & y"

    page = summary_html(data)

    assert "<b>x</b>" not in page and "&lt;b&gt;x&lt;/b&gt; &amp; y" in page


def test_the_full_page_lists_every_probe_and_the_cleanup():
    page = full_html(recorded())

    assert page.count("<tr>") == 18
    assert "gateway.decoy_holds_for_jordan" in page and "Holds Lambda: workload grant" in page
    assert "threads purged" in page and "leftovers 0" in page


def test_the_full_page_escapes_every_receipt_value():
    data = recorded()
    data["outcomes"][0]["detail"] = "<script>alert(1)</script>"
    data["outcomes"][0]["probe"] = '"><img src=x>'
    data["cleanup"]["threads_purged"] = "<i>9</i>"

    page = full_html(data)

    assert "<script" not in page and "<img" not in page and "<i>" not in page
    assert "&lt;script&gt;" in page


def failing_data():
    world, _ = good_world()
    broken = world.__class__(**{**world.__dict__, "http": lambda *a: (200, {"ok": True})})
    return recorded(broken)


def test_a_failing_receipt_shows_a_fail_banner_and_red_rows_on_the_full_page():
    page = full_html(failing_data())

    assert "Result: FAIL" in page and "Result: PASS" not in page and 'class="banner fail"' in page
    assert 'class="bad">allowed' in page


def test_a_passing_receipt_shows_a_pass_banner():
    page = full_html(recorded())

    assert "Result: PASS" in page and 'class="banner pass"' in page


def test_the_full_page_lists_the_problems_gaps_and_leftovers_of_a_failed_run():
    data = recorded()
    data["ok"] = False
    data["coverage_gaps"] = ["gateway: no Jordan control ran"]
    data["cleanup"].update(problems=["release: RuntimeError: gone"], leftovers=1,
                           leftover_items=["booking HLD-9 of trv_meridian_demo"])

    page = full_html(data)

    for text in ("gateway: no Jordan control ran", "release: RuntimeError: gone",
                 "booking HLD-9 of trv_meridian_demo"):
        assert text in page


def test_the_full_page_lists_the_residue_notes():
    page = full_html(recorded())

    assert "AgentCore Memory" in page


def test_the_summary_says_overall_pass_or_fail_and_shows_an_unproven_decoy():
    assert "Overall: Pass" in summary_html(recorded())
    data = failing_data()
    data["summary"][0]["decoy"] = "unproven"

    page = summary_html(data)

    assert "Overall: Fail" in page and "Unproven" in page


def test_pages_refuses_to_build_a_page_that_would_leak():
    data = recorded()
    data["cleanup"]["problems"] = ["arn:aws:rds:us-east-1:123456789012:cluster:meridian"]

    with pytest.raises(ValueError, match="arn"):
        pages(data)


def test_pages_returns_both_pages_for_a_clean_receipt():
    built = pages(recorded())

    assert set(built) == {".summary.html", ".full.html"}
