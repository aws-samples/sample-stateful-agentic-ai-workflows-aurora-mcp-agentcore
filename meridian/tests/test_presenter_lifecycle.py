"""Setup and reset scripts must preserve existing data and propagate failures."""
from unittest.mock import AsyncMock, Mock

import pytest

from scripts import init_aurora_schema, release_demo_bookings, seed_data


def test_schema_initialization_refuses_existing_tables_before_ddl(monkeypatch):
    client = Mock()
    client.execute_statement.return_value = {"records": [[{"booleanValue": True}]]}
    monkeypatch.setattr(init_aurora_schema, "CLUSTER_ARN", "cluster")
    monkeypatch.setattr(init_aurora_schema, "SECRET_ARN", "secret-reference")
    monkeypatch.setattr(init_aurora_schema.boto3, "client", lambda *a, **kw: client)

    with pytest.raises(SystemExit, match="empty database"):
        init_aurora_schema.initialize_database()

    client.execute_statement.assert_called_once()
    assert client.execute_statement.call_args.kwargs["sql"].startswith("SELECT")


def test_seed_refuses_existing_data_without_deleting_it(monkeypatch):
    execute = Mock(return_value={"records": [[{"booleanValue": True}]]})
    monkeypatch.setattr(seed_data, "run_sql", execute)
    with pytest.raises(SystemExit, match="Existing data was left unchanged"):
        seed_data.require_empty_seed_tables()
    execute.assert_called_once()
    assert execute.call_args.args[0].startswith("SELECT")


def test_seed_failure_rolls_back_all_writes_and_releases_transaction(monkeypatch):
    client = Mock()
    client.begin_transaction.return_value = {"transactionId": "seed-tx"}
    client.execute_statement.return_value = {"records": [[{"booleanValue": True}]]}
    monkeypatch.setattr(seed_data, "rds", lambda: client)

    with pytest.raises(RuntimeError, match="model unavailable"), seed_data.seed_transaction():
        seed_data.run_sql("INSERT INTO example VALUES (1)")
        raise RuntimeError("model unavailable")

    assert all(c.kwargs["transactionId"] == "seed-tx" for c in client.execute_statement.call_args_list)
    client.commit_transaction.assert_not_called()
    client.rollback_transaction.assert_called_once()
    assert seed_data._seed_transaction_id is None


def test_concurrent_seed_is_refused_without_running_the_body(monkeypatch):
    client = Mock()
    client.begin_transaction.return_value = {"transactionId": "seed-tx"}
    client.execute_statement.return_value = {"records": [[{"booleanValue": False}]]}
    monkeypatch.setattr(seed_data, "rds", lambda: client)
    with pytest.raises(RuntimeError, match="Another seed"), seed_data.seed_transaction():
        pytest.fail("Concurrent seed body must not execute")
    client.rollback_transaction.assert_called_once()


@pytest.mark.asyncio
async def test_booking_release_is_targeted_and_atomic_on_failure(monkeypatch):
    client = Mock()
    row = {"booking_id": "owned", "status": "held", "total_amount": "20", "created_at": "today"}
    client.execute = AsyncMock(side_effect=[[row], [], RuntimeError("database unavailable")])
    client.execute_one = AsyncMock(return_value={"booking_id": "owned"})
    client.begin_transaction.return_value = "release-tx"
    monkeypatch.setattr(release_demo_bookings, "get_rds_data_client", lambda: client)

    with pytest.raises(RuntimeError, match="database unavailable"):
        await release_demo_bookings.release("traveler", False, False, "owned")

    assert client.execute.call_args_list[0].args[1] == ("traveler", "owned", "owned")
    assert client.execute_one.call_args.kwargs["transaction_id"] == "release-tx"
    client.commit_transaction.assert_not_called()
    client.rollback_transaction.assert_called_once_with("release-tx")


@pytest.mark.asyncio
async def test_booking_release_dry_run_never_opens_write_transaction(monkeypatch):
    client = Mock()
    client.execute = AsyncMock(return_value=[{
        "booking_id": "owned", "status": "held", "total_amount": "20", "created_at": "today",
    }])
    monkeypatch.setattr(release_demo_bookings, "get_rds_data_client", lambda: client)
    assert await release_demo_bookings.release("traveler", False, True, "owned") == 1
    client.begin_transaction.assert_not_called()
