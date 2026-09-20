"""Presenter setup/reset must preserve existing data and propagate failures."""
from unittest.mock import AsyncMock, Mock

import pytest
from botocore.exceptions import ClientError

from scripts import cleanup_resources, init_aurora_schema, release_demo_bookings, seed_data


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


def test_cleanup_preserves_resources_when_cluster_still_exists(monkeypatch):
    rds = Mock()
    rds.describe_db_clusters.return_value = {"DBClusters": [{"Status": "available"}]}
    factory = Mock(return_value=rds)
    monkeypatch.setattr(cleanup_resources.boto3, "client", factory)

    with pytest.raises(RuntimeError, match="still exists"):
        cleanup_resources.cleanup_resources(apply=True)

    factory.assert_called_once_with("rds", region_name="us-east-1")
    rds.delete_db_subnet_group.assert_not_called()


def test_access_denied_is_not_treated_as_resource_absence():
    call = Mock(side_effect=ClientError({"Error": {"Code": "AccessDenied"}}, "Describe"))
    with pytest.raises(ClientError, match="AccessDenied"):
        cleanup_resources._optional(call, {"DBClusterNotFoundFault"})


@pytest.mark.parametrize("apply", [False, True])
def test_cleanup_dry_run_and_failed_apply(monkeypatch, apply):
    rds, secrets, ec2 = Mock(), Mock(), Mock()
    rds.describe_db_clusters.side_effect = ClientError(
        {"Error": {"Code": "DBClusterNotFoundFault"}}, "DescribeDBClusters",
    )
    secrets.describe_secret.return_value = {"ARN": "secret-reference"}
    rds.describe_db_subnet_groups.return_value = {"DBSubnetGroups": [{}]}
    ec2.describe_security_groups.return_value = {"SecurityGroups": []}
    services = {"rds": rds, "secretsmanager": secrets, "ec2": ec2}
    monkeypatch.setattr(cleanup_resources.boto3, "client", lambda name, **kw: services[name])
    rds.delete_db_subnet_group.side_effect = ClientError(
        {"Error": {"Code": "AccessDenied"}}, "DeleteDBSubnetGroup",
    )

    if apply:
        with pytest.raises(ClientError, match="AccessDenied"):
            cleanup_resources.cleanup_resources(apply=True)
    else:
        assert cleanup_resources.cleanup_resources() == 1
        rds.delete_db_subnet_group.assert_not_called()
    # Deleting the source must not delete credentials used by a snapshot restore.
    secrets.delete_secret.assert_not_called()


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


def test_unreviewed_provisioning_stops_before_any_aws_call(tmp_path):
    import os
    import subprocess
    from pathlib import Path

    marker = tmp_path / "aws-called"
    aws = tmp_path / "aws"
    aws.write_text('#!/bin/sh\ntouch "' + str(marker) + '"\nexit 1\n')
    aws.chmod(0o755)
    script = Path(__file__).resolve().parents[1] / "scripts/create_cluster.sh"
    result = subprocess.run(["/bin/bash", str(script), "--apply", "123456789012"],
                            env={**os.environ, "PATH": str(tmp_path)},
                            capture_output=True, text=True, check=False)
    assert result.returncode == 2
    assert "Provisioning is disabled" in result.stderr
    assert not marker.exists()
