"""`release_identity.py snapshot` and `rollback`: private file, dry run by default, guarded."""

from __future__ import annotations

import json
import stat

import pytest

from scripts import release_identity
from scripts.identity_release import lambda_release, rollback, settings, snapshot
from tests import release_support as rs
from tests import snapshot_support as ss
from tests.aws_recorders import client_error
from tests.test_release_identity_cli import NOW, env

FLAG = settings.CONFIRM_FLAG
SNAP_NAME = "snapshot-20261008T120000Z.json"


def SAVED(tmp_path):  # noqa: N802 - reads like the constant it stands for
    """The snapshot file the helpers above write."""
    return tmp_path / "release-b2" / SNAP_NAME


def run(argv, world, tmp_path, environment=None):
    deps = release_identity.Dependencies(
        env=environment or env(MERIDIAN_AGENTCORE_AUTH="iam"), session=world.session,
        now=lambda: NOW, head_sha=lambda: rs.SHA, proof_path=tmp_path / "proof.json",
        release_dir=tmp_path / "release-b2", sleep=lambda seconds: None,
        hosted_release_path=world.hosted_release)
    return release_identity.main(argv, deps)


def save(world, tmp_path):
    """Take a snapshot with the real command; returns the path."""
    assert run(["snapshot", "--service-arn", ss.SERVICE_ARN], world, tmp_path) == 0
    return tmp_path / "release-b2" / SNAP_NAME


@pytest.fixture
def world(tmp_path):
    return ss.SnapWorld(tmp_path)


# ------------------------------------------------------------------- snapshot


def test_the_snapshot_command_only_reads_and_writes_a_private_file(world, tmp_path, capsys):
    path = save(world, tmp_path)

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert snapshot.load(path)["gateway"]["authorizerType"] == "AWS_IAM"
    out = capsys.readouterr().out
    assert SNAP_NAME in out and rs.ACCOUNT not in out
    assert world.writes() == [] and world.violations() == []


def test_the_snapshot_command_needs_the_service_arn(world, tmp_path):
    with pytest.raises(SystemExit) as stopped:
        run(["snapshot"], world, tmp_path)

    assert stopped.value.code == 3


def test_a_misspelled_flag_is_a_usage_error(world, tmp_path):
    with pytest.raises(SystemExit) as stopped:
        run(["rollback", "--appl", FLAG], world, tmp_path)

    assert stopped.value.code == 3


def test_a_service_arn_of_another_account_stops_the_snapshot_before_any_read(world, tmp_path,
                                                                           capsys):
    other = ss.SERVICE_ARN.replace(rs.ACCOUNT, "999999999999")

    assert run(["snapshot", "--service-arn", other], world, tmp_path) == 2

    assert world.built == [] or world.built == ["sts"]
    assert "meridian-web" in capsys.readouterr().err and not world.clients[
        "apprunner"].calls


def test_credentials_for_another_account_stop_every_command_after_sts(tmp_path, capsys):
    foreign = ss.SnapWorld(tmp_path, account="999999999999")

    assert run(["snapshot", "--service-arn", ss.SERVICE_ARN], foreign, tmp_path) == 2

    assert foreign.built == ["sts"]
    assert "999999999999" not in capsys.readouterr().err


# ------------------------------------------------------------------- rollback


def test_the_rollback_is_a_dry_run_unless_it_is_told_twice(world, tmp_path, capsys):
    save(world, tmp_path)
    world.release()
    capsys.readouterr()

    assert run(["rollback"], world, tmp_path) == 0

    out = capsys.readouterr().out
    assert "DRY RUN" in out and SNAP_NAME in out and FLAG in out
    assert "would restore" in out and rs.ACCOUNT not in out
    assert world.writes() == []


def test_apply_without_the_confirmation_builds_no_client(world, tmp_path, capsys):
    save(world, tmp_path)
    world.built.clear()

    assert run(["rollback", "--apply"], world, tmp_path) == 3

    assert world.built == [] and FLAG in capsys.readouterr().out


def test_a_rollback_with_no_snapshot_says_how_to_take_one(world, tmp_path, capsys):
    assert run(["rollback"], world, tmp_path) == 2

    assert "release_identity.py snapshot" in capsys.readouterr().err
    assert world.built == []


@pytest.mark.parametrize("field,value", [("account", "999999999999"), ("region", "eu-west-1")])
def test_a_snapshot_of_another_account_or_region_is_refused_before_any_client(
        world, tmp_path, capsys, field, value):
    saved = ss.taken(world)
    saved[field] = value
    snapshot.write(saved, tmp_path / "release-b2", NOW)

    assert run(["rollback"], world, tmp_path) == 2

    assert world.built == [] and "999999999999" not in capsys.readouterr().err


def test_a_changed_snapshot_is_refused(world, tmp_path, capsys):
    path = save(world, tmp_path)
    document = json.loads(path.read_text())
    document["mode"] = "jwt"
    path.write_text(json.dumps(document))
    world.built.clear()

    assert run(["rollback"], world, tmp_path) == 2

    assert "integrity" in capsys.readouterr().err and world.built == []


def test_credentials_for_another_account_stop_the_rollback_after_sts(tmp_path, capsys):
    taken_in = ss.SnapWorld(tmp_path)
    save(taken_in, tmp_path)
    foreign = ss.SnapWorld(tmp_path, account="999999999999")

    assert run(["rollback", "--apply", FLAG], foreign, tmp_path) == 2

    assert foreign.built == ["sts"] and foreign.writes() == []


def test_apply_puts_everything_back_and_exits_zero_when_all_of_it_can_be_restored(
        world, tmp_path, capsys):
    save(world, tmp_path)
    before = world.state()
    world.release(manual=False)
    capsys.readouterr()

    assert run(["rollback", "--apply", FLAG], world, tmp_path) == 0

    assert world.state() == before and "Rollback complete" in capsys.readouterr().out
    result = tmp_path / "release-b2" / rollback.RESULT_NAME
    assert stat.S_IMODE(result.stat().st_mode) == 0o600
    assert json.loads(result.read_text())["code"] == 0


def test_apply_exits_one_and_prints_the_manual_commands_for_what_the_api_cannot_restore(
        world, tmp_path, capsys):
    save(world, tmp_path)
    world.release()
    capsys.readouterr()

    assert run(["rollback", "--apply", FLAG], world, tmp_path) == 1

    out = capsys.readouterr().out
    assert "roles stack" in out and "publish.py" in out and "agentcore deploy -y" in out
    assert f"Run python scripts/release_identity.py rollback --snapshot {SAVED(tmp_path)}" in out
    assert rs.ACCOUNT not in out


def test_an_explicit_snapshot_is_used_instead_of_the_newest(world, tmp_path, capsys):
    older = save(world, tmp_path)
    renamed = older.with_name("snapshot-20261001T000000Z.json")
    older.rename(renamed)
    save(world, tmp_path)
    capsys.readouterr()

    assert run(["rollback", "--snapshot", str(renamed)], world, tmp_path) == 0

    assert "snapshot-20261001T000000Z.json" in capsys.readouterr().out


def test_a_newer_broken_snapshot_is_named_and_skipped(world, tmp_path, capsys):
    save(world, tmp_path)
    world.release()
    (tmp_path / "release-b2" / "snapshot-20261009T000000Z.json").write_text("{")
    capsys.readouterr()

    assert run(["rollback"], world, tmp_path) == 0

    out = capsys.readouterr().out
    assert "skipped" in out and "snapshot-20261009T000000Z.json" in out and SNAP_NAME in out


def test_an_unexpected_error_prints_one_masked_line_and_no_traceback(
        world, tmp_path, capsys, monkeypatch):
    save(world, tmp_path)
    world.release()

    def boom(*args, **kwargs):
        raise RuntimeError(f"failed in {rs.ACCOUNT} with Bearer abc")

    monkeypatch.setattr(rollback, "run", boom)

    assert run(["rollback", "--apply", FLAG], world, tmp_path) == 2

    err = capsys.readouterr().err
    assert "unexpected (RuntimeError)" in err
    assert rs.ACCOUNT not in err and "Traceback" not in err and "Bearer" not in err


def test_a_jwt_snapshot_without_the_pool_settings_stops_before_any_client(tmp_path, capsys):
    jwt_world = ss.SnapWorld(tmp_path, mode="jwt")
    assert run(["snapshot", "--service-arn", ss.SERVICE_ARN], jwt_world, tmp_path) == 0
    jwt_world.built.clear()
    bare = {key: value for key, value in env(MERIDIAN_AGENTCORE_AUTH="iam").items()
            if key not in rs.COGNITO_ENV}

    assert run(["rollback", "--snapshot", str(SAVED(tmp_path))], jwt_world, tmp_path, bare) == 2

    assert jwt_world.built == [] and "MERIDIAN_COGNITO" in capsys.readouterr().err


# ------------------------------------------------------- baseline, older file, pending restart


def test_a_snapshot_taken_with_findings_exits_one_and_saves_nothing(world, tmp_path, capsys):
    world.service = ss.service_state("jwt")

    assert run(["snapshot", "--service-arn", ss.SERVICE_ARN], world, tmp_path) == 1

    out = capsys.readouterr().out
    assert "BASELINE" in out and "--accept-baseline" in out
    assert not list((tmp_path / "release-b2").glob("snapshot-*.json"))


def test_accepting_the_baseline_saves_the_snapshot_with_its_findings(world, tmp_path, capsys):
    world.service = ss.service_state("jwt")

    code = run(["snapshot", "--service-arn", ss.SERVICE_ARN, "--accept-baseline"], world, tmp_path)

    assert code == 0
    saved = snapshot.load(tmp_path / "release-b2" / SNAP_NAME)
    assert saved["baselineFindings"] and "BASELINE" in capsys.readouterr().out


def test_an_older_snapshot_is_announced_with_its_time_and_commit_before_any_hop_is_read(
        world, tmp_path, capsys):
    save(world, tmp_path)
    world.release()
    (tmp_path / "release-b2" / "snapshot-20261009T000000Z.json").write_text("{")
    capsys.readouterr()

    assert run(["rollback"], world, tmp_path) == 0

    out = capsys.readouterr().out
    assert out.index("OLDER SNAPSHOT") < out.index("step 1 of")
    assert "2026-10-08T12:00:00+00:00" in out and rs.SHA[:12] in out
    assert "snapshot-20261009T000000Z.json" in out


def test_a_rollback_with_a_malformed_hop_still_writes_the_result_file(world, tmp_path):
    save(world, tmp_path)
    world.release(manual=False)
    path = tmp_path / "release-b2" / SNAP_NAME
    saved = json.loads(path.read_text())
    del saved["integrity"]
    del saved["site"]["viewerFunction"]["code"]
    path.unlink()
    snapshot.write(saved, tmp_path / "release-b2", NOW.replace(hour=12))

    assert run(["rollback", "--apply", FLAG], world, tmp_path) == 1

    result = json.loads((tmp_path / "release-b2" / rollback.RESULT_NAME).read_text())
    assert {step["name"]: step["status"] for step in result["steps"]}["site"] == "failed"


def holds_failed_after_the_secret_was_restored(world, tmp_path):
    """SSM restored, the holds restart failed; the holds variables already match the saved ones."""
    save(world, tmp_path)
    world.release(manual=False)
    holds = world.lambdas[ss.HOLDS_ARN]["Environment"]["Variables"]
    holds["AURORA_SECRET_ARN"] = rs.MASTER_SECRET
    world.failures["update_function_configuration"] = client_error("ThrottlingException")
    assert run(["rollback", "--apply", FLAG], world, tmp_path) == 1
    return tmp_path / "release-b2" / rollback.RESULT_NAME


def test_a_failed_holds_restart_is_remembered_and_done_by_the_next_run(world, tmp_path, capsys):
    result = holds_failed_after_the_secret_was_restored(world, tmp_path)
    assert json.loads(result.read_text())["pending"] == ["ssm"]
    holds = world.lambdas[ss.HOLDS_ARN]["Environment"]["Variables"]
    assert lambda_release.MARKER not in holds

    assert run(["rollback", "--snapshot", str(SAVED(tmp_path)), "--apply", FLAG],
               world, tmp_path) == 0

    holds = world.lambdas[ss.HOLDS_ARN]["Environment"]["Variables"]
    assert holds[lambda_release.MARKER] == "20261008T120000Z"
    assert json.loads(result.read_text())["pending"] == []
    assert "Rollback complete" in capsys.readouterr().out


def test_a_dry_run_after_a_failed_holds_restart_says_the_restart_is_needed(
        world, tmp_path, capsys):
    holds_failed_after_the_secret_was_restored(world, tmp_path)
    capsys.readouterr()

    assert run(["rollback", "--snapshot", str(SAVED(tmp_path))], world, tmp_path) == 0

    assert "restart needed" in capsys.readouterr().out


def test_an_unreadable_result_file_means_the_restart_is_done_to_be_safe(world, tmp_path, capsys):
    result = holds_failed_after_the_secret_was_restored(world, tmp_path)
    result.write_text("{")
    capsys.readouterr()

    assert run(["rollback", "--snapshot", str(SAVED(tmp_path)), "--apply", FLAG],
               world, tmp_path) == 0

    out = capsys.readouterr().out
    assert "rollback-result.json" in out and "restart" in out
    assert lambda_release.MARKER in world.lambdas[ss.HOLDS_ARN]["Environment"]["Variables"]


def test_a_pending_restart_of_another_snapshot_is_ignored(world, tmp_path):
    result = holds_failed_after_the_secret_was_restored(world, tmp_path)
    document = json.loads(result.read_text())
    document["snapshot"] = "snapshot-20200101T000000Z.json"
    result.write_text(json.dumps(document))

    assert run(["rollback", "--snapshot", str(SAVED(tmp_path)), "--apply", FLAG],
               world, tmp_path) == 0

    assert lambda_release.MARKER not in world.lambdas[ss.HOLDS_ARN]["Environment"]["Variables"]


# --------------------------------------- the snapshot is chosen by mode, never by recency alone


def after_a_jwt_release(tmp_path):
    """A jwt release is live; the iam snapshot was taken before it, a jwt one for the gate."""
    before = tmp_path / "before-the-window"
    before.mkdir()
    saved_iam = ss.taken(ss.SnapWorld(before, mode="iam"))
    live = ss.SnapWorld(tmp_path, mode="jwt")
    saved_jwt = ss.taken(live)
    release = tmp_path / "release-b2"
    iam_file = snapshot.write(saved_iam, release, NOW.replace(day=7))
    jwt_file = snapshot.write(saved_jwt, release, NOW)
    return live, iam_file, jwt_file


def test_the_default_rollback_after_a_jwt_release_uses_the_iam_snapshot_not_the_newer_jwt_one(
        tmp_path, capsys):
    live, iam_file, jwt_file = after_a_jwt_release(tmp_path)
    assert jwt_file.name > iam_file.name

    assert run(["rollback"], live, tmp_path) == 0

    out = capsys.readouterr().out
    assert f"Rolling back from {iam_file.name}" in out and "mode iam" in out
    assert "Rolling back from " + jwt_file.name not in out


def test_the_default_rollback_after_the_iam_rebuild_goes_the_other_way(tmp_path, capsys):
    live, iam_file, jwt_file = after_a_jwt_release(tmp_path)
    rebuilt = ss.SnapWorld(tmp_path / "rebuilt-iam", mode="iam") if (
        tmp_path / "rebuilt-iam").mkdir() is None else None

    assert run(["rollback"], rebuilt, tmp_path) == 0

    assert f"Rolling back from {jwt_file.name}" in capsys.readouterr().out


def test_the_default_rollback_never_picks_a_snapshot_of_the_mode_that_is_live(tmp_path, capsys):
    live, iam_file, jwt_file = after_a_jwt_release(tmp_path)
    iam_file.unlink()
    live.built.clear()

    assert run(["rollback"], live, tmp_path) == 2

    err = capsys.readouterr().err
    assert "jwt release, which is the one live" in err and "--snapshot FILE" in err
    assert live.writes() == []


def test_an_explicit_snapshot_of_the_live_mode_is_still_allowed(tmp_path, capsys):
    live, iam_file, jwt_file = after_a_jwt_release(tmp_path)

    assert run(["rollback", "--snapshot", str(jwt_file)], live, tmp_path) == 0

    assert f"Rolling back from {jwt_file.name}" in capsys.readouterr().out


def test_two_live_releases_make_the_default_ambiguous(tmp_path, capsys):
    live, iam_file, jwt_file = after_a_jwt_release(tmp_path)
    live.other_gateways.append({"gatewayId": "gw-iam", "name": settings.gateway_physical_name(
        "iam")})
    live.clients["bedrock-agentcore-control"].get_gateway = lambda **kw: (
        ss.runtime_gateway("iam") if kw["gatewayIdentifier"] == "gw-iam"
        else ss.runtime_gateway("jwt"))

    assert run(["rollback"], live, tmp_path) == 2

    assert "cannot tell which release is live" in capsys.readouterr().err


def test_a_default_rollback_with_a_foreign_snapshot_still_builds_no_client(tmp_path, capsys):
    live, iam_file, jwt_file = after_a_jwt_release(tmp_path)
    for path in (iam_file, jwt_file):
        document = json.loads(path.read_text())
        document["account"] = "999999999999"
        path.unlink()
        document.pop("integrity")
        snapshot.write(document, tmp_path / "release-b2", NOW.replace(day=3 if path == iam_file
                                                                      else 4))
    live.built.clear()

    assert run(["rollback"], live, tmp_path) == 2

    assert live.built == [] and "another account" in capsys.readouterr().err


def test_every_rollback_command_the_tool_prints_names_the_snapshot_file(tmp_path, capsys):
    live, iam_file, jwt_file = after_a_jwt_release(tmp_path)

    assert run(["rollback"], live, tmp_path) == 0

    out = capsys.readouterr().out
    commands = [line for line in out.splitlines() if "release_identity.py rollback" in line]
    assert len(commands) >= 2
    for line in commands:
        assert f"--snapshot {iam_file}" in line, line
    assert f"rollback --snapshot {iam_file} --apply {FLAG}" in out


def test_the_remedy_for_an_intact_gateway_also_names_the_snapshot_file(world, tmp_path, capsys):
    path = save(world, tmp_path)
    world.release()
    capsys.readouterr()

    assert run(["rollback", "--snapshot", str(path)], world, tmp_path) == 0

    out = capsys.readouterr().out
    reruns = [line for line in out.splitlines() if "release_identity.py rollback" in line]
    assert len(reruns) >= 2 and all(f"--snapshot {path}" in line for line in reruns)
    assert any("then verify: python scripts/release_identity.py rollback" in line
               for line in reruns)
