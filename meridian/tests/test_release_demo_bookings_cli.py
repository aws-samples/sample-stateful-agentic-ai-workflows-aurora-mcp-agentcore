"""release_demo_bookings lists by default, releases holds with --apply, deletes only on request."""

from unittest.mock import AsyncMock, Mock

import pytest

from scripts import release_demo_bookings as rdb

ROW = {"booking_id": "bk_1", "status": "confirmed", "total_amount": "20", "created_at": "today"}


@pytest.fixture
def client(monkeypatch):
    fake = Mock()
    fake.execute = AsyncMock(side_effect=lambda sql, *a, **k: [ROW] if "SELECT" in sql else [])
    fake.execute_one = AsyncMock(return_value={"booking_id": "bk_1"})
    fake.begin_transaction.return_value = "tx"
    monkeypatch.setattr(rdb, "get_rds_data_client", lambda: fake)
    return fake


def deletes(client):
    return [c for c in client.execute.call_args_list if "DELETE" in c.args[0]]


def test_no_flags_issues_no_delete(client):
    rdb.main([])
    assert deletes(client) == []
    client.begin_transaction.assert_not_called()


def test_apply_without_the_confirm_flag_exits_3_and_deletes_nothing(client):
    with pytest.raises(SystemExit) as exit_info:
        rdb.main(["--apply"])
    assert exit_info.value.code == 3
    assert deletes(client) == []


def test_the_disposable_database_flag_with_both_confirm_flags_deletes(client):
    rdb.main(["--apply", "--i-understand-this-changes-aws", "--delete-from-disposable-database"])
    assert len(deletes(client)) == 4
    client.commit_transaction.assert_called_once()


def test_apply_releases_held_bookings_through_the_gateway_and_deletes_nothing(client, monkeypatch):
    held = {**ROW, "booking_id": "bk_held", "status": "held"}
    client.execute = AsyncMock(side_effect=lambda sql, *a, **k: [ROW, held] if "SELECT" in sql
                               else [])
    released = []

    async def fake_release(db, traveler, bookings, ledger=None):
        released.append((traveler, bookings))
        return []

    monkeypatch.setattr(rdb, "release_bookings", fake_release)

    rdb.main(["--apply", "--i-understand-this-changes-aws"])

    assert released == [("trv_meridian_demo", ["bk_held"])]
    assert deletes(client) == []
    client.begin_transaction.assert_not_called()


def test_a_hold_that_cannot_be_released_exits_one_and_deletes_nothing(client, monkeypatch):
    client.execute = AsyncMock(side_effect=lambda sql, *a, **k: [{**ROW, "status": "held"}]
                               if "SELECT" in sql else [])

    async def failing(db, traveler, bookings, ledger=None):
        return ["booking bk_1 still held or unknown (release not confirmed)"]

    monkeypatch.setattr(rdb, "release_bookings", failing)

    with pytest.raises(SystemExit) as exit_info:
        rdb.main(["--apply", "--i-understand-this-changes-aws"])

    assert exit_info.value.code == 1 and deletes(client) == []


def test_dry_run_is_accepted_lists_and_cannot_be_combined_with_apply(client):
    rdb.main(["--dry-run"])
    assert deletes(client) == []
    with pytest.raises(SystemExit) as exit_info:
        rdb.main(["--dry-run", "--apply", "--i-understand-this-changes-aws"])
    assert exit_info.value.code == 3


@pytest.mark.parametrize("argv", [
    ["--delete-from-disposable-database"],
    ["--apply", "--i-understand-this-changes-aws", "--confirmed-only"],
])
def test_a_delete_without_apply_or_confirmed_only_without_it_is_refused(client, argv):
    with pytest.raises(SystemExit) as exit_info:
        rdb.main(argv)
    assert exit_info.value.code == 3
    assert deletes(client) == []


def test_an_unseeded_traveler_is_refused(client):
    with pytest.raises(SystemExit) as exit_info:
        rdb.main(["--traveler", "trv_someone_else"])
    assert exit_info.value.code == 3
    assert deletes(client) == []


def test_the_summary_counts_real_releases_apart_from_lapsed_holds(client, monkeypatch, capsys):
    active = {**ROW, "booking_id": "bk_active", "status": "held"}
    lapsed = {**ROW, "booking_id": "bk_lapsed", "status": "held"}
    client.execute = AsyncMock(side_effect=lambda sql, *a, **k: [ROW, active, lapsed]
                               if "SELECT" in sql else [])

    async def fake_release(db, traveler, bookings, ledger=None):
        ledger.record("bk_active")
        return []

    monkeypatch.setattr(rdb, "release_bookings", fake_release)

    rdb.main(["--apply", "--i-understand-this-changes-aws"])

    out = " ".join(capsys.readouterr().out.split())
    assert "Released 1 active hold(s) for trv_meridian_demo" in out
    assert "1 held booking(s) had already lapsed and needed no release" in out
