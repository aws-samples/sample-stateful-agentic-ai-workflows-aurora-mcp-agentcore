"""The rollback never silently does less or more than it claims, even with odd service behavior."""

from __future__ import annotations

import json

import pytest

from scripts.identity_release import lambda_release, rollback, snapshot
from tests import release_support as rs
from tests import snapshot_support as ss
from tests.aws_recorders import client_error
from tests.test_release_rollback import go, released

JWT = "e" + "yJhbGciOiJSUzI1NiJ9." + "e" + "yJzdWIiOiJ4In0." + "sig_-9"
KEY = "AKIA" + "ABCDEFGHIJKLMNOP"
SECRET_SHAPES = (f"prefix {JWT}", f"https://x.example.test?k={KEY}")
CONCIERGE = rs.RUNTIME_IDS["MeridianConcierge"]
SITE_WRITES = ("update_function", "publish_function", "update_response_headers_policy")


def plant_runtime(world, value):
    world.runtimes[CONCIERGE]["environmentVariables"]["NOTE"] = value


def plant_service(world, value):
    world.service["SourceConfiguration"]["ImageRepository"]["ImageConfiguration"][
        "RuntimeEnvironmentVariables"]["NOTE"] = value


def plant_gateway(world, value):
    world.gateway["description"] = value


def plant_holds(world, value):
    world.lambdas[ss.HOLDS_ARN]["Environment"]["Variables"]["NOTE"] = value


def plant_semantic(world, value):
    world.lambdas[lambda_release.SEMANTIC_FUNCTION]["Environment"]["Variables"]["NOTE"] = value


def plant_viewer(world, value):
    for stage in ("development", "live"):
        world.fn[stage]["config"]["Comment"] = value


def plant_viewer_code(world, value):
    for stage in ("development", "live"):
        world.fn[stage]["code"] += f" // {value}"


def plant_policy(world, value):
    world.rhp["config"]["Comment"] = value


LAMBDA_WRITE = ("update_function_configuration",)
HOPS = [
    ("runtime MeridianConcierge", plant_runtime, ("update_agent_runtime",), 1),
    ("service", plant_service, ("update_service",), 0),
    ("gateway", plant_gateway, ("update_gateway",), 0),
    ("lambda holds", plant_holds, LAMBDA_WRITE, 1),
    ("lambda semantic", plant_semantic, LAMBDA_WRITE, 1),
    ("site", plant_viewer, SITE_WRITES, 0),
    ("site", plant_viewer_code, SITE_WRITES, 0),
    ("site", plant_policy, SITE_WRITES, 0),
]


def planted(tmp_path, plant, value):
    world = ss.SnapWorld(tmp_path)
    plant(world, value)
    saved = json.loads(json.dumps(ss.taken(world)))
    world.release(manual=False)
    return world, saved


def ids(hop):
    return f"{hop[0]}-{hop[1].__name__}"


# ----------------------------------------------- a masked value is never written back


@pytest.mark.parametrize("value", SECRET_SHAPES, ids=["jwt-substring", "key-in-url"])
@pytest.mark.parametrize("hop", HOPS, ids=[ids(h) for h in HOPS])
def test_a_hop_saved_with_a_masked_substring_fails_and_is_never_written(tmp_path, hop, value):
    name, plant, operations, others = hop
    world, saved = planted(tmp_path, plant, value)
    assert saved["redacted"]

    outcome, said = go(world, saved)

    assert name in [r.name for r in outcome.results if r.status == "failed"]
    assert outcome.code == 1
    assert sum(1 for write in world.writes() if write in operations) == others
    assert JWT not in "\n".join(said) and KEY not in "\n".join(said)


@pytest.mark.parametrize("hop", HOPS, ids=[ids(h) for h in HOPS])
def test_the_substring_check_does_not_depend_on_the_saved_path_list(tmp_path, hop):
    name, plant, _, _ = hop
    world, saved = planted(tmp_path, plant, SECRET_SHAPES[0])
    saved["redacted"] = []

    outcome, _ = go(world, saved)

    assert name in [r.name for r in outcome.results if r.status == "failed"]


def test_every_redacted_path_of_a_hop_refuses_that_hop_and_only_that_hop(tmp_path):
    world, saved = planted(tmp_path, plant_runtime, SECRET_SHAPES[0])

    outcome, said = go(world, saved)

    assert [r.name for r in outcome.results if r.status == "failed"] == [
        "runtime MeridianConcierge"]
    assert "NOTE" in "\n".join(said)
    assert world.writes().count("update_agent_runtime") == 1


# ------------------------------------------------------ dependencies between the steps


def test_a_failed_gateway_skips_the_runtimes_and_says_why(tmp_path):
    world, saved, _ = released(tmp_path, manual=False)
    world.failures["update_gateway"] = client_error("ThrottlingException")

    outcome, said = go(world, saved)

    statuses = {r.name: r.status for r in outcome.results}
    assert statuses["gateway"] == "failed"
    assert statuses["runtime MeridianConcierge"] == "skipped"
    assert statuses["runtime MeridianWorkflow"] == "skipped"
    assert "update_agent_runtime" not in world.writes()
    assert "skipped because gateway failed" in "\n".join(said)
    assert statuses["lambda semantic"] == "restored" and outcome.code == 1


def test_a_failed_secret_parameter_skips_both_lambdas(tmp_path):
    world, saved, _ = released(tmp_path, manual=False)
    world.failures["put_parameter"] = client_error("ThrottlingException")

    outcome, said = go(world, saved)

    statuses = {r.name: r.status for r in outcome.results}
    assert statuses["lambda secret parameter"] == "failed"
    assert statuses["lambda holds"] == statuses["lambda semantic"] == "skipped"
    assert "update_function_configuration" not in world.writes()
    assert "skipped because lambda secret parameter failed" in "\n".join(said)
    assert statuses["gateway"] == "restored" and outcome.code == 1


def test_a_step_that_depends_on_nothing_failed_still_runs(tmp_path):
    world, saved, _ = released(tmp_path, manual=False)
    world.failures["update_service"] = client_error("ThrottlingException")

    outcome, _ = go(world, saved)

    assert {r.name: r.status for r in outcome.results}["gateway"] == "restored"


# ------------------------------------------- service behavior the fakes can now model


def test_hops_that_report_updating_are_polled_until_ready(tmp_path):
    world, saved, before = released(tmp_path, manual=False)
    world.settle_reads = 3
    sleeps: list[float] = []

    outcome, _ = go(world, saved, sleeps=sleeps)

    assert outcome.code == 0 and world.state() == before
    assert len(sleeps) >= 6


def test_a_service_that_keeps_an_omitted_field_fails_with_a_clear_finding_in_bounded_time(
        tmp_path):
    world, saved, _ = released(tmp_path, manual=False)
    world.keeps_omitted = True
    sleeps: list[float] = []

    outcome, said = go(world, saved, sleeps=sleeps)

    statuses = {r.name: r.status for r in outcome.results}
    assert statuses["gateway"] == "failed" and outcome.code == 1
    assert statuses["runtime MeridianConcierge"] == "skipped"
    text = "\n".join(said)
    assert "did not take effect" in text and "interceptorConfigurations" in text
    assert len(sleeps) <= rollback.WAIT_ATTEMPTS * len(outcome.results) * 2


def test_a_lambda_that_never_settles_fails_instead_of_hanging(tmp_path):
    world, saved, _ = released(tmp_path, manual=False)
    world.lambda_stuck = True

    outcome, said = go(world, saved)

    statuses = {r.name: r.status for r in outcome.results}
    assert statuses["lambda holds"] == statuses["lambda semantic"] == "failed"
    assert "did not settle" in "\n".join(said)


# ------------------------------------------------ the Lambda environment is read in full


@pytest.mark.parametrize("environment", [
    {"Error": {"ErrorCode": "KMSAccessDeniedException", "Message": "cannot decrypt"}},
    {}], ids=["error", "no-variables"])
def test_an_environment_that_cannot_be_read_in_full_is_never_replaced(tmp_path, environment):
    world, saved, _ = released(tmp_path, manual=False)
    world.lambdas[ss.HOLDS_ARN]["Environment"] = environment

    outcome, said = go(world, saved)

    assert "lambda holds" in [r.name for r in outcome.results if r.status == "failed"]
    holds_writes = [kw for op, kw in world.clients["lambda"].calls
                    if op == "update_function_configuration"
                    and kw["FunctionName"] == ss.HOLDS_ARN]
    assert holds_writes == []
    assert "cannot read its environment" in "\n".join(said)


@pytest.mark.parametrize("environment", [{"Error": {"ErrorCode": "X", "Message": "x"}}, {}])
def test_taking_a_snapshot_refuses_an_environment_it_cannot_read_in_full(tmp_path, environment):
    world = ss.SnapWorld(tmp_path)
    world.lambdas[lambda_release.SEMANTIC_FUNCTION]["Environment"] = environment

    with pytest.raises(snapshot.SnapshotError, match="environment"):
        ss.taken(world)


def test_a_function_without_any_environment_is_read_as_empty():
    assert snapshot.read_environment({"FunctionName": "f"}) == {}


def test_the_whole_environment_is_verified_after_the_update(tmp_path):
    world, saved, _ = released(tmp_path, manual=False)
    world.lambda_drops = {"POOL_SIZE"}

    outcome, said = go(world, saved)

    assert "lambda holds" in [r.name for r in outcome.results if r.status == "failed"]
    assert "after the update" in "\n".join(said)


# ----------------------------------------------------------- what is printed in a dry run


def test_a_dry_run_prints_full_values_and_every_difference(tmp_path):
    world = ss.SnapWorld(tmp_path)
    environment = world.runtimes[CONCIERGE]["environmentVariables"]
    environment["LONG"] = "a" * 200
    environment.update({f"V{number}": str(number) for number in range(20)})
    saved = json.loads(json.dumps(ss.taken(world)))
    world.release(manual=False)

    _, said = go(world, saved, apply=False)

    text = "\n".join(said)
    assert "a" * 200 in text and "..." not in text
    assert len([line for line in said if "environmentVariables.V" in line]) == 20


def test_a_dry_run_validates_the_requests_it_would_send(tmp_path):
    world, saved, _ = released(tmp_path, manual=False)
    saved["gateway"]["exceptionLevel"] = 5

    outcome, said = go(world, saved, apply=False)

    assert "gateway" in [r.name for r in outcome.results if r.status == "failed"]
    assert "would be invalid" in "\n".join(said) and world.writes() == []


# ------------------------------------------------------------- the site behaviors


def test_a_distribution_drift_is_reported_at_once_not_after_five_minutes(tmp_path):
    world, saved, _ = released(tmp_path, manual=False)
    world.distribution["CacheBehaviors"]["Items"][0]["ResponseHeadersPolicyId"] = "rhp-2"
    sleeps: list[float] = []

    outcome, said = go(world, saved, sleeps=sleeps)

    site = next(r for r in outcome.results if r.name == "site")
    assert site.status == "manual" and outcome.code == 1
    assert "publish.py" in "\n".join(said)
    assert len(sleeps) < rollback.WAIT_ATTEMPTS


def test_a_site_with_restorable_and_unrestorable_drift_restores_then_asks(tmp_path):
    world, saved, _ = released(tmp_path, manual=False)
    world.distribution["CacheBehaviors"]["Items"][0]["ResponseHeadersPolicyId"] = "rhp-2"
    sleeps: list[float] = []

    outcome, _ = go(world, saved, sleeps=sleeps)

    assert world.fn["live"]["code"] == ss.IAM_CODE
    assert next(r for r in outcome.results if r.name == "site").status == "manual"
    assert len(sleeps) < rollback.WAIT_ATTEMPTS


# --------------------------------------------------------------- the remedy text


def test_the_remedy_names_the_commit_and_takes_ids_from_the_operators_environment(tmp_path):
    world, saved, _ = released(tmp_path)

    outcome, said = go(world, saved)

    text = "\n".join(said)
    remedies = "\n".join(line for r in outcome.results if r.status == "manual" for line in r.lines)
    assert saved["commit"] in text and "git worktree add" in text
    assert '--account "$ACCOUNT"' in text and '--service-arn "$SERVICE_ARN"' in text
    assert "<acct>" not in remedies and rs.ACCOUNT not in text
    assert ("then re-attach the interceptor and verify: python scripts/release_identity.py "
            "rollback --snapshot FILE") in text
    assert "checkout of the snapshot's commit" in text


# --------------------------------------------------------------- malformed copies


def test_a_malformed_sub_key_fails_only_its_hop(tmp_path):
    world, saved, _ = released(tmp_path, manual=False)
    del saved["site"]["viewerFunction"]["code"]

    outcome, said = go(world, saved)

    statuses = {r.name: r.status for r in outcome.results}
    assert statuses["site"] == "failed" and statuses["gateway"] == "restored"
    assert "malformed" in "\n".join(said) and outcome.code == 1



# ------------------------------------ a replaced Gateway never gets a saved environment back

FORBIDDEN = ("update_gateway", "update_agent_runtime", "update_service")


@pytest.mark.parametrize("apply", [True, False])
@pytest.mark.parametrize("gateway_gone", [True, False])
def test_a_replaced_gateway_never_causes_a_gateway_runtime_or_service_write(
        tmp_path, apply, gateway_gone):
    world = ss.SnapWorld(tmp_path)
    saved = json.loads(json.dumps(ss.taken(world)))
    world.release(manual=False)
    world.replace_gateway("jwt" if gateway_gone else "iam")

    go(world, saved, apply=apply)

    assert not [w for w in world.writes() if w in FORBIDDEN]
    sent = [kw for op, kw in world.clients["bedrock-agentcore-control"].calls
            if op == "update_agent_runtime"]
    assert sent == []
    assert world.violations() == []


def test_a_replaced_gateway_leaves_the_new_runtime_environment_exactly_as_it_was(tmp_path):
    world = ss.SnapWorld(tmp_path)
    saved = json.loads(json.dumps(ss.taken(world)))
    world.release(manual=False)
    world.replace_gateway("jwt")
    runtimes = json.loads(json.dumps(world.runtimes))
    service = json.loads(json.dumps(world.service))

    go(world, saved, gateway_id=ss.NEW_GATEWAY_ID)

    assert world.runtimes == runtimes and world.service == service
