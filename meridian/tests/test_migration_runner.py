"""Contract tests for the non-destructive RDS Data API migration runner."""

from pathlib import Path

import pytest

from scripts import apply_migrations as migrations


class FakeRdsDataClient:
    def __init__(self, fail_on_sql: str | None = None) -> None:
        self.fail_on_sql = fail_on_sql
        self.executed: list[dict] = []
        self.commits: list[dict] = []
        self.rollbacks: list[dict] = []

    def begin_transaction(self, **kwargs):
        assert kwargs["resourceArn"] == "cluster-arn"
        assert kwargs["secretArn"] == "secret-arn"
        assert kwargs["database"] == "meridian"
        return {"transactionId": "tx-migration"}

    def execute_statement(self, **kwargs):
        self.executed.append(kwargs)
        if self.fail_on_sql and self.fail_on_sql in kwargs["sql"]:
            raise RuntimeError("migration statement failed")
        return {}

    def commit_transaction(self, **kwargs):
        self.commits.append(kwargs)

    def rollback_transaction(self, **kwargs):
        self.rollbacks.append(kwargs)


def test_apply_migration_uses_one_transaction_and_marks_after_sql(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(migrations, "CLUSTER_ARN", "cluster-arn")
    monkeypatch.setattr(migrations, "SECRET_ARN", "secret-arn")
    monkeypatch.setattr(migrations, "DATABASE", "meridian")
    path = tmp_path / "007_transaction_contract.sql"
    path.write_text(
        """
        CREATE TABLE migration_contract (id INTEGER);
        CREATE OR REPLACE FUNCTION migration_contract_fn()
        RETURNS INTEGER
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RETURN 1;
        END;
        $$;
        """
    )
    client = FakeRdsDataClient()

    migrations._apply_migration(client, path)

    assert [call["transactionId"] for call in client.executed] == [
        "tx-migration",
        "tx-migration",
        "tx-migration",
    ]
    assert client.executed[-1]["parameters"] == [
        {
            "name": "migration_name",
            "value": {"stringValue": "007_transaction_contract.sql"},
        }
    ]
    assert len(client.commits) == 1
    assert client.rollbacks == []


def test_apply_migration_rolls_back_on_statement_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(migrations, "CLUSTER_ARN", "cluster-arn")
    monkeypatch.setattr(migrations, "SECRET_ARN", "secret-arn")
    monkeypatch.setattr(migrations, "DATABASE", "meridian")
    path = tmp_path / "008_rollback_contract.sql"
    path.write_text("CREATE TABLE migration_failure (id INTEGER);")
    client = FakeRdsDataClient(fail_on_sql="migration_failure")

    with pytest.raises(RuntimeError, match="migration statement failed"):
        migrations._apply_migration(client, path)

    assert client.commits == []
    assert [call["transactionId"] for call in client.rollbacks] == ["tx-migration"]


class ScriptedClient(FakeRdsDataClient):
    def __init__(self, table: str | None, applied: list[str]) -> None:
        super().__init__()
        self.table, self.applied = table, applied

    def execute_statement(self, **kwargs):
        self.executed.append(kwargs)
        if "to_regclass" in kwargs["sql"]:
            cell = {"stringValue": self.table} if self.table else {"isNull": True}
            return {"records": [[cell]]}
        return {"records": [[{"stringValue": name}] for name in self.applied]}


def _pending_run(monkeypatch, tmp_path, client, capsys) -> tuple[int, list[str]]:
    for name in ("001_a.sql", "002_b.sql", "003_c.sql"):
        (tmp_path / name).write_text("SELECT 1;")
    monkeypatch.setattr(migrations, "MIGRATIONS_DIR", tmp_path)
    monkeypatch.setattr(migrations, "CLUSTER_ARN", "cluster-arn")
    monkeypatch.setattr(migrations, "SECRET_ARN", "secret-arn")
    monkeypatch.setattr(migrations.boto3, "client", lambda *a, **k: client)
    code = migrations.main(["--pending"])
    return code, capsys.readouterr().out.split()


def test_pending_lists_unapplied_names_and_writes_nothing(monkeypatch, tmp_path, capsys):
    client = ScriptedClient("schema_migrations", ["001_a.sql"])
    code, names = _pending_run(monkeypatch, tmp_path, client, capsys)
    assert (code, names) == (0, ["002_b.sql", "003_c.sql"])
    statements = " ".join(call["sql"] for call in client.executed).upper()
    assert "CREATE" not in statements and "INSERT" not in statements
    assert client.commits == [] and client.executed[0].get("transactionId") is None


def test_pending_with_no_tracking_table_lists_everything_without_creating_it(
    monkeypatch, tmp_path, capsys
):
    client = ScriptedClient(None, [])
    code, names = _pending_run(monkeypatch, tmp_path, client, capsys)
    assert (code, names) == (0, ["001_a.sql", "002_b.sql", "003_c.sql"])
    assert all("CREATE" not in call["sql"].upper() for call in client.executed)
