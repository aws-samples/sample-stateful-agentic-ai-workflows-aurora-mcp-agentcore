"""The audit export Lambda: watermark, hash chain, retries, empty batches and size limits.

Only the two AWS boundaries are faked: the RDS Data API (a fake table that applies the same
watermark and settle rules the SQL states) and S3 (a fake bucket that refuses to overwrite a key,
as If-None-Match does). The SQL itself runs against Aurora in test_migration_033_live.py.
"""

import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest
from botocore.exceptions import EndpointConnectionError

from tests.audit_export_support import (
    CLUSTER,
    SECRET,
    Context,
    FakeAurora,
    FakeBucket,
    client_error,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "infra" / "functions" / "audit_export" / "audit_export.py"
MIGRATIONS = ROOT / "scripts" / "migrations"


@pytest.fixture
def audit(monkeypatch):
    monkeypatch.setenv("AURORA_CLUSTER_ARN", CLUSTER)
    monkeypatch.setenv("AURORA_SECRET_ARN", SECRET)
    monkeypatch.setenv("AURORA_DATABASE", "meridian")
    monkeypatch.setenv("AUDIT_EXPORT_BUCKET", "audit-bucket")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    spec = importlib.util.spec_from_file_location("audit_export_under_test", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def world(audit, monkeypatch):
    aurora, bucket = FakeAurora(), FakeBucket()
    monkeypatch.setattr(audit, "_clients", lambda: (aurora, bucket))
    return audit, aurora, bucket


def settings(audit, **overrides):
    values = dict(cluster_arn=CLUSTER, secret_arn=SECRET, database="meridian",
                  bucket="audit-bucket", batch_rows=200, settle_seconds=180, max_batches=20)
    return audit.Settings(**{**values, **overrides})


def run_table(audit, aurora, bucket, name="agent_audit_log", **overrides):
    table = next(t for t in audit.TABLES if t.name == name)
    return audit.export_table(aurora, bucket, settings(audit, **overrides), table, lambda: 10**6)


def lines(body):
    return [json.loads(line) for line in body.decode().splitlines()]


def chain(bucket, table="agent_audit_log"):
    return [bucket.objects[k] for k in sorted(bucket.objects) if f"/{table}/" in k]


def test_the_first_run_writes_a_genesis_object_with_a_header_then_the_rows(world):
    audit, aurora, bucket = world
    aurora.add("agent_audit_log", "aud_b", "2026-11-14 10:00:02.000002", agent="x")
    aurora.add("agent_audit_log", "aud_a", "2026-11-14 10:00:01.000001", agent="y")
    result = run_table(audit, aurora, bucket)
    (key,) = bucket.objects
    assert key == "audit-export/v1/agent_audit_log/00000000/000000000001.jsonl"
    header, *rows = lines(bucket.objects[key])
    assert header == {
        "type": "header", "format": 1, "table": "agent_audit_log", "seq": 1,
        "prev_sha256": "0" * 64, "row_count": 2, "exported_at": header["exported_at"],
        "first": {"ts": "2026-11-14 10:00:01.000001", "id": "aud_a"},
        "last": {"ts": "2026-11-14 10:00:02.000002", "id": "aud_b"},
    }
    assert [r["id"] for r in rows] == ["aud_a", "aud_b"]
    assert rows[0]["row"]["agent"] == "y"
    assert (result.rows, result.objects) == (2, 1)
    metadata = bucket.puts[0]["Metadata"]
    assert metadata["seq"] == "1" and metadata["last-id"] == "aud_b"
    assert metadata["sha256"] == hashlib.sha256(bucket.objects[key]).hexdigest()


def test_each_object_carries_the_hash_of_the_previous_objects_bytes(world):
    audit, aurora, bucket = world
    aurora.add("agent_audit_log", "aud_1", "2026-11-14 10:00:01")
    run_table(audit, aurora, bucket)
    aurora.add("agent_audit_log", "aud_2", "2026-11-14 10:00:02")
    run_table(audit, aurora, bucket)
    aurora.add("agent_audit_log", "aud_3", "2026-11-14 10:00:03")
    run_table(audit, aurora, bucket)
    first, second, third = chain(bucket)
    assert lines(second)[0]["prev_sha256"] == hashlib.sha256(first).hexdigest()
    assert lines(third)[0]["prev_sha256"] == hashlib.sha256(second).hexdigest()
    assert [lines(b)[0]["seq"] for b in (first, second, third)] == [1, 2, 3]
    assert [lines(b)[1]["id"] for b in (first, second, third)] == ["aud_1", "aud_2", "aud_3"]


def test_the_watermark_comes_from_the_bucket_so_a_new_process_repeats_no_row(world):
    audit, aurora, bucket = world
    aurora.add("agent_audit_log", "aud_1", "2026-11-14 10:00:01")
    run_table(audit, aurora, bucket)
    again = run_table(audit, aurora, bucket)
    assert (again.rows, again.objects) == (0, 0)
    last = aurora.calls[-1]["parameters"]
    assert {"name": "wm_ts", "value": {"stringValue": "2026-11-14 10:00:01"}} in last
    assert {"name": "wm_id", "value": {"stringValue": "aud_1"}} in last
    assert len(bucket.objects) == 1


def test_a_first_read_starts_before_every_row(world):
    audit, aurora, bucket = world
    run_table(audit, aurora, bucket)
    params = {p["name"]: p["value"] for p in aurora.calls[0]["parameters"]}
    assert params["wm_ts"] == {"stringValue": "-infinity"}
    assert params["wm_id"] == {"stringValue": ""}


def test_an_empty_batch_writes_nothing_and_is_not_an_error(world):
    audit, aurora, bucket = world
    result = run_table(audit, aurora, bucket)
    assert (result.rows, result.objects, result.conflict) == (0, 0, False)
    assert bucket.puts == [] and result.head == audit.GENESIS


def test_rows_with_one_timestamp_split_across_batches_are_neither_lost_nor_repeated(world):
    audit, aurora, bucket = world
    for ident in ("aud_c", "aud_a", "aud_e", "aud_b", "aud_d"):
        aurora.add("agent_audit_log", ident, "2026-11-14 10:00:00.5")
    result = run_table(audit, aurora, bucket, batch_rows=2)
    seen = [row["id"] for body in chain(bucket) for row in lines(body)[1:]]
    assert seen == ["aud_a", "aud_b", "aud_c", "aud_d", "aud_e"]
    assert result.objects == 3


def test_rows_younger_than_the_settle_time_wait_for_a_later_run(world):
    audit, aurora, bucket = world
    aurora.add("agent_audit_log", "aud_old", "2026-11-14 10:00:01", age=600)
    aurora.add("agent_audit_log", "aud_new", "2026-11-14 10:00:02", age=10)
    run_table(audit, aurora, bucket)
    assert [r["id"] for r in lines(chain(bucket)[0])[1:]] == ["aud_old"]
    aurora.tables["agent_audit_log"][1]["age"] = 400
    run_table(audit, aurora, bucket)
    assert [r["id"] for r in lines(chain(bucket)[1])[1:]] == ["aud_new"]
    assert {"name": "settle", "value": {"longValue": 180}} in aurora.calls[0]["parameters"]


def test_a_failed_write_is_retried_to_the_same_object_without_a_gap_or_a_repeat(world):
    audit, aurora, bucket = world
    aurora.add("agent_audit_log", "aud_1", "2026-11-14 10:00:01")
    bucket.put_failures.append(client_error("InternalError", "secret detail"))
    with pytest.raises(audit.ExportError, match="bucket write was refused"):
        run_table(audit, aurora, bucket)
    assert bucket.objects == {}
    run_table(audit, aurora, bucket)
    run_table(audit, aurora, bucket)
    (only,) = chain(bucket)
    assert [r["id"] for r in lines(only)[1:]] == ["aud_1"]


def test_a_write_whose_reply_was_lost_is_found_by_the_next_run_not_written_twice(world):
    audit, aurora, bucket = world
    aurora.add("agent_audit_log", "aud_1", "2026-11-14 10:00:01")
    bucket.lose_response = True
    lost = run_table(audit, aurora, bucket)
    assert lost.conflict is True and len(bucket.objects) == 1
    aurora.add("agent_audit_log", "aud_2", "2026-11-14 10:00:02")
    run_table(audit, aurora, bucket)
    first, second = chain(bucket)
    assert lines(second)[0]["prev_sha256"] == hashlib.sha256(first).hexdigest()
    assert [r["id"] for r in lines(second)[1:]] == ["aud_2"]


def test_an_existing_key_is_never_overwritten(world):
    audit, aurora, bucket = world
    aurora.add("agent_audit_log", "aud_1", "2026-11-14 10:00:01")
    run_table(audit, aurora, bucket)
    original = dict(bucket.objects)
    head = audit.GENESIS
    new = audit.build_object(
        "agent_audit_log", head, [{"wm_ts": "t", "wm_id": "i", "row_json": "{}",
                                   "age_seconds": "0"}], "now")
    assert audit.put_object(bucket, "audit-bucket", new) is False
    assert bucket.objects == original


def test_an_oversize_response_halves_the_batch_and_keeps_the_smaller_size(world):
    audit, aurora, bucket = world
    for n in range(10):
        aurora.add("agent_audit_log", f"aud_{n:02d}", f"2026-11-14 10:00:{n:02d}")
    aurora.max_rows = 3
    result = run_table(audit, aurora, bucket, batch_rows=8)
    assert result.rows == 10
    sizes = [dict((p["name"], next(iter(p["value"].values()))) for p in c["parameters"])["batch"]
             for c in aurora.calls]
    assert sizes[:3] == [8, 4, 2]
    assert 8 not in sizes[3:] and 4 not in sizes[3:]


@pytest.mark.parametrize("message", [
    # The Aurora User Guide's Data API troubleshooting heading for the 1 MiB result set limit.
    "Database response exceeded size limit",
    # A variant wording: the check keys on "size" or "large" in a BadRequestException.
    "Database returned more than the allowed response size limit",
])
def test_the_documented_oversize_errors_halve_the_batch(world, message):
    audit, aurora, bucket = world
    for n in range(4):
        aurora.add("agent_audit_log", f"aud_{n}", f"2026-11-14 10:00:0{n}")
    aurora.max_rows, aurora.oversize_message = 2, message
    assert run_table(audit, aurora, bucket, batch_rows=8).rows == 4


def test_a_timestamp_column_is_compared_with_utc_by_name_not_the_session_zone(audit):
    table = next(t for t in audit.TABLES if t.ts_type == "timestamp")
    assert "AT TIME ZONE 'UTC'" in table.sql and "LOCALTIMESTAMP" not in table.sql


def test_each_run_logs_the_boto3_version(world, caplog):
    audit, _aurora, _bucket = world
    with caplog.at_level("INFO"):
        audit.lambda_handler({}, Context())
    start = next(json.loads(r.message) for r in caplog.records
                 if r.message.startswith("{") and '"audit_export_start"' in r.message)
    assert start["boto3"] == audit.boto3.__version__


def test_one_row_over_the_limit_fails_with_a_fixed_phrase(world):
    audit, aurora, bucket = world
    aurora.add("agent_audit_log", "aud_1", "2026-11-14 10:00:01")
    aurora.max_rows = 0
    with pytest.raises(audit.ExportError, match="one audit row is too large"):
        run_table(audit, aurora, bucket, batch_rows=4)
    assert bucket.objects == {}


def test_a_refused_read_is_not_mistaken_for_a_size_error(world):
    audit, aurora, bucket = world
    aurora.fail_with = client_error("BadRequestException", "permission denied for table x")
    with pytest.raises(audit.ExportError, match="Aurora read was refused"):
        run_table(audit, aurora, bucket)
    assert len(aurora.calls) == 1


def test_a_network_failure_on_the_read_is_a_fixed_phrase(world):
    audit, aurora, bucket = world
    aurora.fail_with = EndpointConnectionError(endpoint_url="https://rds-data.example.test")
    with pytest.raises(audit.ExportError) as caught:
        run_table(audit, aurora, bucket)
    assert "example.test" not in str(caught.value)


@pytest.mark.parametrize("body", [b"not json\n", b'{"type":"header","table":"other","seq":1,'
                                  b'"last":{"ts":"a","id":"b"}}\n', b"\n"])
def test_a_latest_object_that_is_not_ours_stops_the_table_before_any_write(world, body):
    audit, aurora, bucket = world
    bucket.objects[audit.object_key("agent_audit_log", 1)] = body
    aurora.add("agent_audit_log", "aud_1", "2026-11-14 10:00:01")
    with pytest.raises(audit.ExportError, match="latest export object"):
        run_table(audit, aurora, bucket)
    assert bucket.puts == []


def test_the_latest_object_is_found_across_listing_pages_and_key_groups(world, monkeypatch):
    audit, aurora, bucket = world
    monkeypatch.setattr(audit, "OBJECTS_PER_GROUP", 2)
    for n in range(1, 8):
        aurora.add("agent_audit_log", f"aud_{n}", f"2026-11-14 10:00:0{n}")
        run_table(audit, aurora, bucket)
    keys = sorted(bucket.objects)
    assert len(keys) == 7 and keys[-1].endswith("000000000007.jsonl")
    assert audit.read_head(bucket, "audit-bucket", "agent_audit_log").seq == 7
    assert audit.read_head(bucket, "audit-bucket", "traveler_access_audit") == audit.GENESIS


def test_the_run_stops_when_the_function_is_nearly_out_of_time(world):
    audit, aurora, bucket = world
    aurora.add("agent_audit_log", "aud_1", "2026-11-14 10:00:01")
    table = audit.TABLES[0]
    result = audit.export_table(aurora, bucket, settings(audit), table, lambda: 1000)
    assert result.objects == 0 and aurora.calls == []


def test_a_run_writes_at_most_max_batches_objects_and_the_rest_waits(world):
    audit, aurora, bucket = world
    for n in range(6):
        aurora.add("agent_audit_log", f"aud_{n}", f"2026-11-14 10:00:0{n}")
    result = run_table(audit, aurora, bucket, batch_rows=1, max_batches=4)
    assert result.objects == 4
    assert result.backlog_age_seconds == 1000.0
    assert run_table(audit, aurora, bucket, batch_rows=1, max_batches=4).rows == 2


def test_the_handler_exports_every_table_into_its_own_chain(world):
    audit, aurora, bucket = world
    aurora.add("agent_audit_log", "aud_1", "2026-11-14 10:00:01")
    aurora.add("traveler_access_audit", "acc_1", "2026-11-14 10:00:01+00")
    aurora.add("booking_action_audit", "act_1", "2026-11-14 10:00:01+00")
    outcome = audit.lambda_handler({}, Context())
    assert outcome == {"rows": 3, "objects": 3}
    assert sorted(k.split("/")[2] for k in bucket.objects) == [
        "agent_audit_log", "booking_action_audit", "traveler_access_audit"]


def test_one_failing_table_does_not_block_the_others_but_fails_the_run(world, caplog):
    audit, aurora, bucket = world
    aurora.add("agent_audit_log", "aud_1", "2026-11-14 10:00:01")
    aurora.add("booking_action_audit", "act_1", "2026-11-14 10:00:01+00")
    bucket.objects[audit.object_key("traveler_access_audit", 1)] = b"garbage\n"
    with caplog.at_level("INFO"), pytest.raises(audit.ExportError, match="at least one table"):
        audit.lambda_handler({}, Context())
    assert any("/agent_audit_log/" in k for k in bucket.objects)
    assert any("/booking_action_audit/" in k for k in bucket.objects)
    metric = next(json.loads(r.message) for r in caplog.records if "_aws" in r.message)
    assert metric["RunsCompleted"] == 0 and metric["RowsExported"] == 2


def test_a_completed_run_emits_the_heartbeat_and_the_chain_heads(world, caplog):
    audit, aurora, bucket = world
    aurora.add("agent_audit_log", "aud_1", "2026-11-14 10:00:01")
    with caplog.at_level("INFO"):
        audit.lambda_handler({}, Context())
    messages = [json.loads(r.message) for r in caplog.records if r.message.startswith("{")]
    metric = next(m for m in messages if "_aws" in m)
    assert metric["RunsCompleted"] == 1 and metric["RowsExported"] == 1
    assert metric["_aws"]["CloudWatchMetrics"][0]["Namespace"] == "Meridian/AuditExport"
    head = next(m for m in messages if m.get("event") == "audit_export_head")
    assert head["sha256"] == hashlib.sha256(chain(bucket)[0]).hexdigest()


def test_the_environment_is_validated(audit, monkeypatch):
    monkeypatch.setenv("AUDIT_EXPORT_BATCH_ROWS", "0")
    with pytest.raises(ValueError, match="AUDIT_EXPORT_BATCH_ROWS"):
        audit.Settings.from_env()
    monkeypatch.setenv("AUDIT_EXPORT_BATCH_ROWS", "250")
    monkeypatch.delenv("AUDIT_EXPORT_BUCKET")
    with pytest.raises(KeyError):
        audit.Settings.from_env()


def test_every_statement_is_a_read_of_one_audit_table(audit):
    for table in audit.TABLES:
        sql = table.sql.lower()
        assert sql.startswith("select ")
        assert not re.search(r"\b(insert|update|delete|truncate|merge|call|copy)\b", sql)
        assert f"from {table.name} as t" in sql
        ts, ident = table.ts_column, table.id_column
        assert (f"where ({ts}, {ident}) > (cast(:wm_ts as {table.ts_type}), "
                "cast(:wm_id as text))") in sql
        assert "localtimestamp" not in sql
        assert (f"{ts} <= {table.now_expression.lower()} - cast(:settle as integer) "
                "* interval '1 second'") in sql
        assert f"order by {ts}, {ident} limit :batch" in sql


def test_the_exporter_reads_every_audit_table_the_migrations_create(audit):
    created = set()
    for path in [*sorted(MIGRATIONS.glob("*.sql")), ROOT / "examples" / "rls_for_agents.sql"]:
        text = path.read_text()
        created |= set(re.findall(
            r"CREATE TABLE IF NOT EXISTS (\w*audit\w*)", text, re.IGNORECASE))
    assert created == {t.name for t in audit.TABLES}
    grants = (MIGRATIONS / "033_audit_export_login.sql").read_text()
    for name in created:
        assert re.search(rf"CREATE POLICY \w+ ON {name}\s+FOR SELECT TO meridian_audit_export",
                         grants), name
