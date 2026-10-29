"""release_demo_bookings lists by default and deletes only with both confirm flags."""

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


def test_both_flags_delete(client):
    rdb.main(["--apply", "--i-understand-this-changes-aws"])
    assert len(deletes(client)) == 3
    client.commit_transaction.assert_called_once()


def test_an_unseeded_traveler_is_refused(client):
    with pytest.raises(SystemExit) as exit_info:
        rdb.main(["--traveler", "trv_someone_else"])
    assert exit_info.value.code == 3
    assert deletes(client) == []


def test_the_deprecated_dry_run_flag_lists_and_deletes_nothing(client):
    rdb.main(["--dry-run"])
    assert deletes(client) == []
    client.begin_transaction.assert_not_called()


def test_dry_run_with_apply_is_refused(client):
    with pytest.raises(SystemExit) as exit_info:
        rdb.main(["--dry-run", "--apply", "--i-understand-this-changes-aws"])
    assert exit_info.value.code == 3
    assert deletes(client) == []
