"""The replaced configuration is saved before the window: complete, private, with no secrets."""

from __future__ import annotations

import json
import re
import stat
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from scripts.identity_release import lambda_release, snapshot
from tests import release_support as rs
from tests import snapshot_support as ss
from tests.aws_recorders import client_error

NOW = ss.STAMPED


@pytest.fixture
def world(tmp_path):
    return ss.SnapWorld(tmp_path)


def sensitive_values(node, path=""):
    """Every (path, value) where a credential-looking name holds a plain, non-ARN string."""
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}"
            if isinstance(value, str) and value and snapshot.SENSITIVE_NAME.search(key) \
                    and not value.startswith("arn:") and value != snapshot.REDACTED:
                yield here, value
            yield from sensitive_values(value, here)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            yield from sensitive_values(item, f"{path}[{index}]")


# ------------------------------------------------------------------------ taking


def test_the_snapshot_holds_every_hop_the_release_changes(world):
    saved = ss.taken(world)

    assert saved["schema"] == snapshot.SCHEMA and saved["complete"] is True
    assert (saved["account"], saved["region"], saved["mode"]) == (rs.ACCOUNT, rs.REGION, "iam")
    assert saved["takenAt"] == NOW.isoformat() and saved["commit"] == "abc1234"
    assert saved["gateway"]["authorizerType"] == "AWS_IAM"
    assert saved["gateway"]["description"] == "Gateway for meridian-aurora"
    assert saved["gateway"]["policyEngineConfiguration"]["mode"] == "ENFORCE"
    assert set(saved["runtimes"]) == {"MeridianConcierge", "MeridianWorkflow"}
    runtime = saved["runtimes"]["MeridianConcierge"]
    assert runtime["agentRuntimeArtifact"]["containerConfiguration"]["containerUri"].endswith("old")
    assert runtime["environmentVariables"]["MERIDIAN_AGENTCORE_AUTH"] == "iam"
    image = saved["service"]["SourceConfiguration"]["ImageRepository"]
    assert image["ImageIdentifier"] == "ecr/meridian:old"
    assert image["ImageConfiguration"]["RuntimeEnvironmentSecrets"] == {
        "MERIDIAN_API_TOKEN": ss.API_SECRET}
    assert saved["service"]["InstanceConfiguration"]["InstanceRoleArn"].endswith("-instance")


def test_the_snapshot_holds_the_site_the_roles_the_lambdas_and_the_rules(world):
    saved = ss.taken(world)

    site = saved["site"]
    assert site["distributionId"] == ss.DISTRIBUTION
    assert site["viewerFunction"]["code"] == ss.IAM_CODE
    assert site["viewerFunction"]["config"]["Comment"] == "Meridian edge iam"
    assert site["responseHeadersPolicy"]["config"]["Name"] == "MeridianWebResponseHeaders"
    assert site["behaviors"][1] == {
        "pathPattern": "/api/*", "responseHeadersPolicyId": "rhp-1",
        "functionAssociations": [{"eventType": "viewer-request", "functionArn": ss.VIEWER_ARN}]}
    assert site["hostedRelease"] == {"status": "verified", "identityMode": "iam",
                                     "previousImage": "ecr/meridian:older",
                                     "image": "ecr/meridian:old"}
    assert saved["roles"]["stackName"] == "MeridianWebRoles"
    assert re.fullmatch(r"[0-9a-f]{64}", saved["roles"]["templateSha256"])
    assert saved["lambdas"]["ssmSecretArn"]["value"] == rs.MASTER_SECRET
    assert saved["lambdas"]["holds"]["arn"] == ss.HOLDS_ARN
    assert saved["lambdas"]["holds"]["environment"]["POOL_SIZE"] == "4"
    assert saved["lambdas"]["semantic"]["environment"]["AURORA_SECRET_ARN"] == rs.MASTER_SECRET
    assert saved["policies"] == {name: "ACTIVE" for name in rs.BASE_POLICIES}


def test_the_site_copy_leaves_out_the_origin_headers_and_the_store(world):
    text = json.dumps(ss.taken(world))

    assert ss.PLANTED[0] not in text and "X-Origin-Token" not in text
    assert all(call[0] not in ("list_keys", "get_key") for call in
               world.clients["cloudfront"].calls)


def test_taking_only_reads_and_every_call_matches_the_service_models(world):
    ss.taken(world)

    assert world.writes() == []
    assert world.violations() == []
    assert sorted({op for client in world.clients.values() for op, _ in client.calls}) == [
        "describe_function", "describe_service", "describe_stacks", "get_agent_runtime",
        "get_distribution_config", "get_function", "get_function_configuration", "get_gateway",
        "get_gateway_target", "get_parameter", "get_response_headers_policy", "get_template",
        "list_gateway_targets", "list_policies"]


def test_the_input_descriptions_are_not_changed(world):
    before = deepcopy(world.service)
    ss.taken(world)

    assert world.service == before


def test_datetimes_become_text(world):
    world.gateway["createdAt"] = NOW
    saved = ss.taken(world)

    assert json.loads(json.dumps(saved))["gateway"]["createdAt"] == NOW.isoformat()


def test_the_mode_is_read_from_the_live_gateway(tmp_path):
    jwt_world = ss.SnapWorld(tmp_path, mode="jwt")

    assert ss.taken(jwt_world)["mode"] == "jwt"


def test_a_mixed_state_is_saved_with_its_known_findings_named(world):
    world.service = ss.service_state("jwt")
    saved = ss.taken(world)

    assert saved["baselineFindings"]
    assert all(line.startswith(("Service", "Gateway", "Runtime", "Policy"))
               for line in saved["baselineFindings"])


# --------------------------------------------------------------------- the secrets


def test_a_plain_secret_looking_value_is_redacted_everywhere_but_a_reference_is_kept(world):
    variables = world.service["SourceConfiguration"]["ImageRepository"]["ImageConfiguration"][
        "RuntimeEnvironmentVariables"]
    variables.update({"DB_PASSWORD": ss.PLANTED[0], "AURORA_SECRET_ARN": rs.MASTER_SECRET})
    world.runtimes[rs.RUNTIME_IDS["MeridianWorkflow"]]["environmentVariables"]["API_KEY"] = "k-1"
    world.lambdas[ss.HOLDS_ARN]["Environment"]["Variables"]["SERVICE_TOKEN"] = "t-1"
    saved = ss.taken(world)

    text = json.dumps(saved)
    assert ss.PLANTED[0] not in text and '"k-1"' not in text and '"t-1"' not in text
    saved_variables = saved["service"]["SourceConfiguration"]["ImageRepository"][
        "ImageConfiguration"]["RuntimeEnvironmentVariables"]
    assert saved_variables["DB_PASSWORD"] == snapshot.REDACTED
    assert saved_variables["AURORA_SECRET_ARN"] == rs.MASTER_SECRET
    assert sorted(saved["redacted"]) == [
        "lambdas.holds.environment.SERVICE_TOKEN",
        "runtimes.MeridianWorkflow.environmentVariables.API_KEY",
        "service.SourceConfiguration.ImageRepository.ImageConfiguration."
        "RuntimeEnvironmentVariables.DB_PASSWORD"]


def test_token_shaped_and_access_key_shaped_strings_are_masked(world):
    world.runtimes[rs.RUNTIME_IDS["MeridianConcierge"]]["environmentVariables"].update(
        {"NOTE": ss.PLANTED[2], "WHO": ss.PLANTED[1]})
    saved = ss.taken(world)

    text = json.dumps(saved)
    assert "e" + "yJ" not in text and ss.PLANTED[1] not in text
    assert saved["runtimes"]["MeridianConcierge"]["environmentVariables"]["NOTE"] == "<token>"
    assert saved["runtimes"]["MeridianConcierge"]["environmentVariables"]["WHO"] == "<token>"


def test_the_whole_snapshot_passes_a_secret_scan(world):
    world.service["SourceConfiguration"]["ImageRepository"]["ImageConfiguration"][
        "RuntimeEnvironmentVariables"]["MERIDIAN_API_TOKEN"] = ss.PLANTED[0]
    world.lambdas[ss.HOLDS_ARN]["Environment"]["Variables"]["PASSWORD"] = ss.PLANTED[0]
    saved = ss.taken(world)

    text = json.dumps(saved)
    assert list(sensitive_values(saved)) == []
    assert not any(secret in text for secret in ss.PLANTED)
    assert re.search("e" + "yJ|AKIA[0-9A-Z]{16}|Bearer ", text) is None
    assert not re.search(r"-----BEGIN", text)


def test_a_non_arn_in_the_secret_parameter_is_refused_without_printing_it(world):
    world.parameters[lambda_release.SSM_SECRET_PARAMETER]["Value"] = ss.PLANTED[0]

    with pytest.raises(snapshot.SnapshotError) as caught:
        ss.taken(world)

    assert ss.PLANTED[0] not in str(caught.value) and "ARN" in str(caught.value)


# ------------------------------------------------------------------ refusing to take


def test_a_gateway_with_a_field_the_restore_would_drop_is_refused(world):
    world.gateway["somethingNew"] = {"x": 1}

    with pytest.raises(snapshot.SnapshotError, match="somethingNew"):
        ss.taken(world)


def test_a_gateway_that_is_not_ready_is_refused(world):
    world.gateway["status"] = "UPDATING"

    with pytest.raises(snapshot.SnapshotError, match="READY"):
        ss.taken(world)


def test_a_runtime_with_a_field_the_restore_would_drop_is_refused(world):
    world.runtimes[rs.RUNTIME_IDS["MeridianWorkflow"]]["somethingNew"] = {"x": 1}

    with pytest.raises(snapshot.SnapshotError, match="MeridianWorkflow.*somethingNew"):
        ss.taken(world)


def test_a_missing_release_receipt_names_the_file(world):
    world.hosted_release.unlink()

    with pytest.raises(snapshot.SnapshotError, match="hosted-release.json"):
        ss.taken(world)


def test_a_receipt_without_the_distribution_is_refused(world):
    world.hosted_release.write_text(json.dumps({"status": "verified", "site": {}}))

    with pytest.raises(snapshot.SnapshotError, match="DistributionId"):
        ss.taken(world)


def test_a_missing_viewer_function_is_refused(world):
    world.failures["get_function"] = client_error("NoSuchFunctionExists")

    with pytest.raises(Exception, match="NoSuchFunctionExists"):
        ss.taken(world)


# --------------------------------------------------------- writing, loading, finding


def test_the_snapshot_is_written_privately_under_a_sortable_name(world, tmp_path):
    saved = ss.taken(world)

    path = snapshot.write(saved, tmp_path / "release-b2", NOW)

    assert path.name == "snapshot-20261008T123005Z.json"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert snapshot.load(path) == json.loads(json.dumps(saved))
    assert [p.name for p in path.parent.iterdir()] == [path.name]


def test_the_written_file_carries_an_integrity_hash(world, tmp_path):
    path = snapshot.write(ss.taken(world), tmp_path, NOW)

    document = json.loads(path.read_text())
    assert document["integrity"]["algorithm"] == "sha256"
    assert re.fullmatch(r"[0-9a-f]{64}", document["integrity"]["digest"])


def test_a_changed_snapshot_is_refused(world, tmp_path):
    path = snapshot.write(ss.taken(world), tmp_path, NOW)
    document = json.loads(path.read_text())
    document["gateway"]["authorizerType"] = "CUSTOM_JWT"
    path.write_text(json.dumps(document))

    with pytest.raises(snapshot.SnapshotError, match="integrity"):
        snapshot.load(path)


def test_a_snapshot_without_the_complete_mark_is_refused(world, tmp_path):
    saved = ss.taken(world)
    saved["complete"] = False
    path = snapshot.write(saved, tmp_path, NOW)

    with pytest.raises(snapshot.SnapshotError, match="not complete"):
        snapshot.load(path)


def test_a_snapshot_missing_a_section_is_refused(world, tmp_path):
    saved = ss.taken(world)
    del saved["lambdas"]
    path = snapshot.write(saved, tmp_path, NOW)

    with pytest.raises(snapshot.SnapshotError, match="lambdas"):
        snapshot.load(path)


def test_a_snapshot_of_another_schema_is_refused(world, tmp_path):
    saved = ss.taken(world)
    saved["schema"] = "something-else/9"
    path = snapshot.write(saved, tmp_path, NOW)

    with pytest.raises(snapshot.SnapshotError, match="schema"):
        snapshot.load(path)


@pytest.mark.parametrize("text", ["{", "[]", ""])
def test_an_unreadable_snapshot_is_refused_with_its_name(tmp_path, text):
    broken = tmp_path / "snapshot-20261008T000000Z.json"
    broken.write_text(text)

    with pytest.raises(snapshot.SnapshotError, match="snapshot-20261008T000000Z.json"):
        snapshot.load(broken)


def test_a_missing_file_is_refused_with_its_name(tmp_path):
    with pytest.raises(snapshot.SnapshotError, match="snapshot-20261008T000000Z.json"):
        snapshot.load(tmp_path / "snapshot-20261008T000000Z.json")


def test_the_newest_complete_snapshot_is_the_one_found(world, tmp_path):
    saved = ss.taken(world)
    older = snapshot.write(saved, tmp_path, datetime(2026, 10, 7, tzinfo=timezone.utc))
    newer = snapshot.write(saved, tmp_path, NOW)

    assert snapshot.latest_complete(tmp_path) == (newer, [])
    assert newer != older


def test_a_newer_broken_snapshot_is_skipped_and_named(world, tmp_path):
    saved = ss.taken(world)
    good = snapshot.write(saved, tmp_path, datetime(2026, 10, 7, tzinfo=timezone.utc))
    bad = tmp_path / "snapshot-20261008T000000Z.json"
    bad.write_text("{")

    found, skipped = snapshot.latest_complete(tmp_path)

    assert found == good and [name for name in skipped] == [bad.name]


def test_no_snapshot_directory_means_none(tmp_path):
    assert snapshot.latest_complete(tmp_path / "none") == (None, [])


def test_files_that_are_not_snapshots_are_ignored(world, tmp_path):
    (tmp_path / "gateway-move.json").write_text("{}")
    (tmp_path / "snapshot-notes.json").write_text("{}")

    assert snapshot.latest_complete(tmp_path) == (None, [])


# ------------------------------------------------------- placeholders, names, structure


def test_a_placeholder_inside_a_longer_string_is_found():
    node = {"a": "see <token> here", "b": ["x", {"c": "k=<redacted>"}], "d": "clean"}

    assert snapshot.placeholders(node) == ["a", "b[1].c"]


def test_a_non_secret_name_that_contains_token_is_kept(world):
    world.runtimes[rs.RUNTIME_IDS["MeridianConcierge"]]["environmentVariables"][
        "TOKEN_USE"] = "access"
    saved = ss.taken(world)

    assert saved["runtimes"]["MeridianConcierge"]["environmentVariables"]["TOKEN_USE"] == "access"
    assert not [path for path in saved["redacted"] if "TOKEN_USE" in path]


def test_the_redacted_paths_are_covered_by_the_integrity_hash(world, tmp_path):
    world.runtimes[rs.RUNTIME_IDS["MeridianConcierge"]]["environmentVariables"][
        "NOTE"] = "prefix " + ss.PLANTED[2]
    path = snapshot.write(ss.taken(world), tmp_path, NOW)
    document = json.loads(path.read_text())
    assert document["redacted"] == ["runtimes.MeridianConcierge.environmentVariables.NOTE"]
    document["redacted"] = []
    path.write_text(json.dumps(document))

    with pytest.raises(snapshot.SnapshotError, match="integrity"):
        snapshot.load(path)


def test_the_name_and_the_time_are_in_utc_whatever_the_zone_of_now(world, tmp_path):
    local = datetime(2026, 10, 8, 17, 30, 5, tzinfo=timezone(timedelta(hours=5)))

    path = snapshot.write(ss.taken(world), tmp_path, local)

    assert path.name == "snapshot-20261008T123005Z.json"
    assert snapshot.utc_stamp(local) == "20261008T123005Z"


def test_a_time_without_a_zone_is_refused(world, tmp_path):
    with pytest.raises(snapshot.SnapshotError, match="time zone"):
        snapshot.write(ss.taken(world), tmp_path, datetime(2026, 10, 8, 12, 30, 5))


def test_a_snapshot_file_others_can_read_is_refused(world, tmp_path):
    path = snapshot.write(ss.taken(world), tmp_path, NOW)
    path.chmod(0o644)

    with pytest.raises(snapshot.SnapshotError, match="0600"):
        snapshot.load(path)


@pytest.mark.parametrize("change", [
    lambda d: d.update(mode="sideways"),
    lambda d: d.pop("takenAt"),
    lambda d: d.update(account=12),
    lambda d: d.update(gateway=[]),
    lambda d: d.update(runtimes=[]),
    lambda d: d["service"].pop("ServiceArn"),
    lambda d: d.update(baselineFindings="x"),
    lambda d: d.update(redacted="x"),
])
def test_a_snapshot_with_a_malformed_top_level_is_refused_by_load(world, tmp_path, change):
    saved = json.loads(json.dumps(ss.taken(world)))
    change(saved)
    path = snapshot.write(saved, tmp_path, NOW)

    with pytest.raises(snapshot.SnapshotError, match="snapshot-"):
        snapshot.load(path)
