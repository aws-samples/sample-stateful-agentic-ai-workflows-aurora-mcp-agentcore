"""verify_audit_export.py finds edited, deleted and missing audit evidence, and writes nothing."""

import hashlib

import pytest

from scripts import verify_audit_export as verifier
from tests.audit_export_support import (
    CLUSTER,
    SECRET,
    Context,
    FakeAurora,
    FakeBucket,
    client_error,
)

TABLE = "agent_audit_log"


@pytest.fixture
def exporter():
    return verifier.load_exporter()


@pytest.fixture
def world(exporter, monkeypatch):
    """An export of three rows in three objects, and the Aurora rows it came from."""
    aurora, bucket = FakeAurora(), FakeBucket()
    monkeypatch.setattr(exporter, "_clients", lambda: (aurora, bucket))
    monkeypatch.setenv("AURORA_CLUSTER_ARN", CLUSTER)
    monkeypatch.setenv("AURORA_SECRET_ARN", SECRET)
    monkeypatch.setenv("AURORA_AUDIT_EXPORT_SECRET_ARN", SECRET)
    monkeypatch.setenv("AURORA_DATABASE", "meridian")
    monkeypatch.setenv("AUDIT_EXPORT_BUCKET", "audit-bucket")
    monkeypatch.setattr(verifier, "load_dotenv", lambda *_a, **_k: None)
    monkeypatch.setattr(verifier.boto3, "client",
                        lambda service, **_k: bucket if service == "s3" else aurora)
    for n in (1, 2, 3):
        aurora.add(TABLE, f"aud_{n}", f"2026-11-14 10:00:0{n}", agent="a", rows=n)
        exporter.lambda_handler({}, Context())
    return aurora, bucket


def keys(bucket):
    return sorted(bucket.objects)


def run(capsys, *args):
    code = verifier.main(["--bucket", "audit-bucket", "--table", TABLE, *args])
    return code, capsys.readouterr()


def codes(output):
    return sorted(line.split()[2].rstrip(":") for line in output.out.splitlines()
                  if line.startswith("FINDING"))


def test_an_untouched_chain_verifies_with_exit_zero(world, capsys):
    code, out = run(capsys)
    assert code == 0 and codes(out) == []
    assert f"{TABLE}: 3 objects, 3 rows, 0 findings" in out.out


def test_an_untouched_chain_matches_aurora(world, capsys):
    code, out = run(capsys, "--compare-aurora")
    assert code == 0 and "3 rows, 0 pending, 0 findings" in out.out


def test_an_edited_object_is_found_by_its_version_hash_and_the_next_header(world, capsys):
    _aurora, bucket = world
    key = keys(bucket)[1]
    forged = bucket.objects[key].replace(b'"rows":2', b'"rows":9')
    assert forged != bucket.objects[key]
    bucket.overwrite(key, forged)
    code, out = run(capsys)
    assert code == 1
    assert codes(out) == ["chain-broken", "extra-version", "metadata-mismatch"]


def test_a_removed_object_leaves_a_gap_and_a_broken_link(world, capsys):
    _aurora, bucket = world
    del bucket.objects[keys(bucket)[1]]
    code, out = run(capsys)
    assert code == 1 and codes(out) == ["chain-broken", "chain-gap"]


def test_a_delete_marker_is_reported_even_though_the_old_version_still_reads(world, capsys):
    _aurora, bucket = world
    bucket.delete(keys(bucket)[2])
    code, out = run(capsys)
    assert code == 1 and codes(out) == ["delete-marker"]


def test_a_chain_cut_at_the_tail_shows_only_against_a_recorded_head(world, capsys):
    _aurora, bucket = world
    recorded = hashlib.sha256(bucket.objects[keys(bucket)[2]]).hexdigest()
    del bucket.objects[keys(bucket)[2]]
    code, out = run(capsys)
    assert code == 0, "the chain alone cannot show a removed tail"
    code, out = run(capsys, "--head", f"{TABLE}=3:{recorded}")
    assert code == 1 and codes(out) == ["head-mismatch"]


def test_a_matching_recorded_head_passes(world, capsys):
    _aurora, bucket = world
    recorded = hashlib.sha256(bucket.objects[keys(bucket)[2]]).hexdigest()
    assert run(capsys, "--head", f"{TABLE}=3:{recorded}")[0] == 0


def test_a_row_repeated_in_the_chain_is_reported(world, capsys, exporter):
    aurora, bucket = world
    head = exporter.read_head(bucket, "audit-bucket", TABLE)
    row = {"wm_ts": "2026-11-14 10:00:09", "wm_id": "aud_1", "row_json": "{}", "age_seconds": "1"}
    new = exporter.build_object(TABLE, head, [row], "now")
    exporter.put_object(bucket, "audit-bucket", new)
    code, out = run(capsys)
    assert code == 1 and "duplicate-row" in codes(out)


def test_an_object_whose_header_lies_about_its_rows_is_reported(world, capsys):
    _aurora, bucket = world
    key = keys(bucket)[0]
    bucket.objects[key] = bucket.objects[key].replace(b'"row_count":1', b'"row_count":4')
    code, out = run(capsys)
    assert code == 1
    assert {"row-count", "metadata-mismatch", "chain-broken"} <= set(codes(out))


def test_a_malformed_object_is_reported_not_a_crash(world, capsys):
    _aurora, bucket = world
    bucket.objects[keys(bucket)[0]] = b"not json at all\n"
    code, out = run(capsys)
    assert code == 1 and "object-malformed" in codes(out)


def test_an_exported_row_edited_in_aurora_is_named_by_column_not_value(world, capsys):
    aurora, _bucket = world
    aurora.tables[TABLE][1]["doc"]["agent"] = "attacker-chosen-value"
    code, out = run(capsys, "--compare-aurora")
    assert code == 1 and codes(out) == ["row-edited"]
    assert "agent" in out.out and "attacker-chosen-value" not in out.out


def test_an_exported_row_deleted_from_aurora_is_reported(world, capsys):
    aurora, _bucket = world
    del aurora.tables[TABLE][0]
    code, out = run(capsys, "--compare-aurora")
    assert code == 1 and codes(out) == ["row-deleted"]


def test_a_row_inserted_behind_the_watermark_is_reported_and_one_ahead_is_pending(world, capsys):
    aurora, _bucket = world
    aurora.add(TABLE, "aud_backdated", "2026-11-14 10:00:01.5")
    aurora.add(TABLE, "aud_new", "2026-11-14 10:30:00")
    code, out = run(capsys, "--compare-aurora")
    assert code == 1 and codes(out) == ["row-unexported"]
    assert "1 pending" in out.out


def test_a_column_added_after_the_export_is_not_an_edit(world, capsys):
    aurora, _bucket = world
    for row in aurora.tables[TABLE]:
        row["doc"]["added_later"] = None
    assert run(capsys, "--compare-aurora")[0] == 0


def test_a_dry_run_calls_nothing(world, monkeypatch, capsys):
    def refuse(*_a, **_k):
        raise AssertionError("a dry run must not create an AWS client")

    monkeypatch.setattr(verifier.boto3, "client", refuse)
    code, out = run(capsys, "--dry-run", "--compare-aurora")
    assert code == 0 and "nothing was called" in out.out


@pytest.mark.parametrize("args", [
    ["--table", "nope"], ["--head", "bad"], ["--head", "t=1:abc"],
])
def test_usage_errors_exit_three(world, capsys, args):
    code = None
    with pytest.raises(SystemExit) as stop:
        verifier.main(["--bucket", "audit-bucket", *args])
    code = stop.value.code
    assert code == 3
    assert "Traceback" not in capsys.readouterr().err


def test_a_missing_bucket_exits_three(monkeypatch):
    monkeypatch.delenv("AUDIT_EXPORT_BUCKET", raising=False)
    with pytest.raises(SystemExit) as stop:
        verifier.main([])
    assert stop.value.code == 3


def test_an_aws_failure_exits_two_with_the_code_and_no_account_id(world, monkeypatch, capsys):
    def deny(*_a, **_k):
        raise client_error("AccessDenied", "arn:aws:iam::123456789012:user/x is not allowed")

    _aurora, bucket = world
    monkeypatch.setattr(bucket, "list_object_versions", deny)
    code, out = run(capsys)
    assert code == 2 and "AccessDenied" in out.err and "123456789012" not in out.err


def test_a_failed_aurora_read_exits_two(world, capsys):
    aurora, _bucket = world
    aurora.fail_with = client_error("BadRequestException", "permission denied for table x")
    code, out = run(capsys, "--compare-aurora")
    assert code == 2 and "could not read Aurora" in out.err


def test_compare_without_the_login_secret_stops_before_any_read(world, monkeypatch):
    monkeypatch.delenv("AURORA_AUDIT_EXPORT_SECRET_ARN")
    with pytest.raises(SystemExit, match="AURORA_AUDIT_EXPORT_SECRET_ARN"):
        verifier.main(["--bucket", "audit-bucket", "--compare-aurora"])


def test_the_verifier_never_calls_a_write_api(world, capsys):
    aurora, bucket = world
    writes = []
    for name in ("put_object", "delete_object", "delete_objects", "copy_object",
                 "put_object_retention", "put_object_legal_hold"):
        setattr(bucket, name, lambda *a, _n=name, **k: writes.append(_n))
    run(capsys, "--compare-aurora")
    assert writes == []
    assert all(not call["sql"].lstrip().lower().startswith(("insert", "update", "delete"))
               for call in aurora.calls[3:])


def test_head_values_round_trip(world):
    value = "a" * 64
    assert verifier.parse_heads([f"{TABLE}=12:{value}"]) == {TABLE: (12, value)}
    with pytest.raises(ValueError):
        verifier.parse_heads([f"{TABLE}=12:{value[:-1]}"])


def test_the_loader_gives_the_same_tables_the_exporter_uses(exporter):
    assert [t.name for t in exporter.TABLES] == [
        "agent_audit_log", "traveler_access_audit", "booking_action_audit"]


SHA = "a" * 64


@pytest.mark.parametrize("head_table", ["agnet_audit_log", "traveler_access_audit"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_a_head_that_names_no_selected_table_is_a_usage_error_before_any_client(
        world, monkeypatch, capsys, head_table, dry_run):
    def refuse(*_a, **_k):
        raise AssertionError("no AWS client may be built for a bad --head")

    monkeypatch.setattr(verifier.boto3, "client", refuse)
    extra = ["--dry-run"] if dry_run else []
    with pytest.raises(SystemExit) as stop:
        verifier.main(["--bucket", "audit-bucket", "--table", TABLE, "--head",
                       f"{head_table}=9:{SHA}", *extra])
    assert stop.value.code == 3
    assert head_table in capsys.readouterr().err


def test_a_head_given_twice_for_one_table_is_a_usage_error(world, capsys):
    with pytest.raises(SystemExit) as stop:
        verifier.main(["--bucket", "audit-bucket", "--table", TABLE,
                       "--head", f"{TABLE}=1:{SHA}", "--head", f"{TABLE}=2:{SHA}"])
    assert stop.value.code == 3
    assert "twice" in capsys.readouterr().err


def test_a_head_for_a_table_in_the_default_selection_is_checked(world, capsys):
    code = verifier.main(["--bucket", "audit-bucket", "--head", f"{TABLE}=9:{SHA}"])
    out = capsys.readouterr()
    assert code == 1 and "head-mismatch" in out.out


def test_an_empty_chain_does_not_satisfy_a_recorded_head(exporter, monkeypatch, capsys):
    bucket = FakeBucket()
    monkeypatch.setattr(verifier.boto3, "client", lambda *_a, **_k: bucket)
    code = verifier.main(["--bucket", "audit-bucket", "--table", TABLE,
                          "--head", f"{TABLE}=9:{SHA}"])
    out = capsys.readouterr()
    assert code == 1 and codes(out) == ["head-mismatch"]


def test_an_empty_chain_matches_a_recorded_genesis_head(exporter, monkeypatch):
    bucket = FakeBucket()
    monkeypatch.setattr(verifier.boto3, "client", lambda *_a, **_k: bucket)
    assert verifier.main(["--bucket", "audit-bucket", "--table", TABLE,
                          "--head", f"{TABLE}=0:{'0' * 64}"]) == 0


def test_rows_at_the_watermark_timestamp_are_split_by_their_place_in_database_order(
        world, capsys):
    aurora, _bucket = world
    aurora.add(TABLE, "aud_0", "2026-11-14 10:00:03")
    aurora.add(TABLE, "aud_9", "2026-11-14 10:00:03")
    code, out = run(capsys, "--compare-aurora")
    assert code == 1 and codes(out) == ["row-unexported"]
    assert "aud_0" in out.out and "aud_9" not in out.out
    assert "1 pending" in out.out
