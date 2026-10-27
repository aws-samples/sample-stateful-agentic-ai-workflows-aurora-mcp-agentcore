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


def test_pending_says_so_when_every_migration_is_applied(monkeypatch, tmp_path, capsys):
    client = ScriptedClient("schema_migrations", ["001_a.sql", "002_b.sql", "003_c.sql"])
    code, words = _pending_run(monkeypatch, tmp_path, client, capsys)
    assert (code, " ".join(words)) == (0, "No pending migrations.")
    assert client.commits == []


def test_a_dry_run_runs_every_statement_then_rolls_back_without_marking_it_applied(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(migrations, "CLUSTER_ARN", "cluster-arn")
    monkeypatch.setattr(migrations, "SECRET_ARN", "secret-arn")
    monkeypatch.setattr(migrations, "DATABASE", "meridian")
    path = tmp_path / "021_dry.sql"
    path.write_text("ALTER TABLE t ADD COLUMN a INTEGER;\nCREATE VIEW v AS SELECT 1;\n")
    client = FakeRdsDataClient()

    count = migrations._dry_run_migration(client, path)

    assert count == 2
    assert [call["sql"] for call in client.executed] == [
        "ALTER TABLE t ADD COLUMN a INTEGER;", "CREATE VIEW v AS SELECT 1;"]
    assert all(call["transactionId"] == "tx-migration" for call in client.executed)
    assert client.commits == []
    assert client.rollbacks == [
        {"resourceArn": "cluster-arn", "secretArn": "secret-arn", "transactionId": "tx-migration"}]
    assert not any("schema_migrations" in call["sql"] for call in client.executed)


def test_a_dry_run_that_fails_midway_still_rolls_back(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(migrations, "CLUSTER_ARN", "cluster-arn")
    monkeypatch.setattr(migrations, "SECRET_ARN", "secret-arn")
    path = tmp_path / "021_dry.sql"
    path.write_text("ALTER TABLE t ADD COLUMN a INTEGER;\nCREATE VIEW v AS SELECT 1;\n")
    client = FakeRdsDataClient(fail_on_sql="CREATE VIEW")

    with pytest.raises(RuntimeError, match="migration statement failed"):
        migrations._dry_run_migration(client, path)

    assert client.commits == [] and len(client.rollbacks) == 1


def test_the_dry_run_flag_lists_what_would_run_and_commits_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr(migrations, "CLUSTER_ARN", "cluster-arn")
    monkeypatch.setattr(migrations, "SECRET_ARN", "secret-arn")
    monkeypatch.setattr(migrations, "DATABASE", "meridian")
    monkeypatch.setattr(migrations, "MIGRATIONS_DIR", tmp_path)
    (tmp_path / "021_dry.sql").write_text("ALTER TABLE t ADD COLUMN a INTEGER;")
    client = FakeRdsDataClient()
    monkeypatch.setattr(migrations.boto3, "client", lambda *args, **kwargs: client)

    assert migrations.main(["--dry-run"]) == 0

    assert "Dry run: 021_dry.sql ran 1 statement(s) and was rolled back." in capsys.readouterr().out
    assert client.commits == [] and len(client.rollbacks) == 1


def test_dry_run_and_pending_cannot_be_combined() -> None:
    with pytest.raises(SystemExit) as stopped:
        migrations.main(["--dry-run", "--pending"])
    assert stopped.value.code == 2


def _spy_begin_calls(monkeypatch: pytest.MonkeyPatch, client: FakeRdsDataClient) -> list[dict]:
    calls: list[dict] = []
    begin = client.begin_transaction

    def spy(**kwargs):
        calls.append(kwargs)
        return begin(**kwargs)

    monkeypatch.setattr(client, "begin_transaction", spy)
    return calls


@pytest.mark.parametrize("runner", ["_apply_migration", "_dry_run_migration"])
def test_an_unreadable_migration_never_opens_a_transaction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, runner: str
) -> None:
    monkeypatch.setattr(migrations, "CLUSTER_ARN", "cluster-arn")
    monkeypatch.setattr(migrations, "SECRET_ARN", "secret-arn")
    monkeypatch.setattr(migrations, "DATABASE", "meridian")
    client = FakeRdsDataClient()
    begun = _spy_begin_calls(monkeypatch, client)

    with pytest.raises(FileNotFoundError):
        getattr(migrations, runner)(client, tmp_path / "099_missing.sql")

    assert begun == [] and client.rollbacks == []


@pytest.mark.parametrize("runner", ["_apply_migration", "_dry_run_migration"])
def test_a_migration_that_fails_to_split_never_opens_a_transaction(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, runner: str
) -> None:
    monkeypatch.setattr(migrations, "CLUSTER_ARN", "cluster-arn")
    monkeypatch.setattr(migrations, "SECRET_ARN", "secret-arn")
    monkeypatch.setattr(migrations, "DATABASE", "meridian")
    path = tmp_path / "099_unsplittable.sql"
    path.write_text("SELECT 1;\n")

    def refuse(sql: str) -> list[str]:
        raise ValueError("unbalanced dollar quote")

    monkeypatch.setattr(migrations, "split_sql", refuse)
    client = FakeRdsDataClient()
    begun = _spy_begin_calls(monkeypatch, client)

    with pytest.raises(ValueError, match="unbalanced dollar quote"):
        getattr(migrations, runner)(client, path)

    assert begun == [] and client.rollbacks == []
