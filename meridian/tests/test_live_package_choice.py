"""A confirm test picks the roomiest package; a hold-only test keeps the first fit."""

import pytest

from tests.live_support import choose_package

ROWS = [
    {"package_id": "A", "cents": 100, "availability": {"5 nights": 3, "7 nights": 4}},
    {"package_id": "B", "cents": 900, "availability": {"5 nights": 20}},
    {"package_id": "C", "cents": 200, "availability": {"5 nights": 12, "7 nights": 12}},
    {"package_id": "D", "cents": 300, "availability": {"5 nights": 1}},
]


def test_the_default_choice_is_the_first_package_with_room():
    assert choose_package(ROWS) == ("A", "5 nights", 100)


def test_roomiest_picks_the_duration_with_the_most_places_left():
    assert choose_package(ROWS, roomiest=True) == ("B", "5 nights", 900)


def test_roomiest_respects_the_price_bounds_and_breaks_ties_by_order():
    assert choose_package(ROWS, within_cents=500, roomiest=True) == ("C", "5 nights", 200)
    assert choose_package(ROWS, above_cents=500, roomiest=True) == ("B", "5 nights", 900)


def test_nothing_with_room_returns_none_so_the_test_can_skip():
    assert choose_package(ROWS, seats=50, roomiest=True) is None
    assert choose_package([], roomiest=True) is None


@pytest.mark.parametrize(("path", "name"), [
    ("tests/test_gateway_login_aurora.py",
     "test_the_holds_lambda_places_and_confirms_a_hold_as_the_least_privilege_login"),
    ("tests/test_holds_release_aurora.py", "test_release_hold_leaves_a_confirmed_booking_alone"),
])
def test_each_confirm_test_documents_its_residual_cost_and_asks_for_the_roomiest_package(
        path, name):
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / path).read_text(encoding="utf-8")
    body = source.split(f"async def {name}(")[1].split("\nasync def ")[0]
    held = source.split("async def _held(")[1].split("\nasync def ")[0] if "_held(" in body else ""

    assert "roomiest=True" in body
    assert "Residual cost" in body and "--delete-from-disposable-database" in body
    assert "raising availability" in body
    assert held == "" or "roomiest=roomiest" in held


def test_the_readme_states_the_residual_cost_where_the_database_tests_are_described():
    from pathlib import Path

    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")

    assert "Each run leaves one confirmed booking for one" in readme
    assert "--delete-from-disposable-database` on a disposable database, or by" in readme


def test_the_readme_lists_the_audit_rows_that_remain_and_the_synthetic_traveler():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    readme = (root / "README.md").read_text(encoding="utf-8")
    probe = (root / "meridian/tests/test_migration_033_live.py").read_text(encoding="utf-8")

    assert "two\n`agent_audit_log` rows per run" in readme and "trv_itest_synthetic" in readme
    assert "'trv_itest_synthetic'" in probe and "'trv_meridian_demo'" not in probe.split(
        "async def test_the_settle_time")[1]
