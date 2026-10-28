"""The rollback never puts the catalog Lambda on a login other than meridian_catalog."""

from __future__ import annotations

import pytest

from scripts.identity_release import lambda_release, restore_hops, rollback, snapshot
from tests import release_support as rs
from tests import snapshot_support as ss
from tests.aws_recorders import client_error
from tests.test_release_rollback import context, released

CATALOG = lambda_release.CATALOG_FUNCTION


def go_with(world, saved, env=None, apply=True):
    ctx = context(world, saved, apply=apply)
    if env is not None:
        ctx.where = snapshot.Where(
            ctx.where.account, ctx.where.region, ctx.where.gateway_id, ctx.where.runtime_ids,
            ctx.where.service_arn, env)
    said: list[str] = []
    outcome = rollback.run(ctx, said.append)
    return {r.name: r for r in outcome.results}, said


def catalog_writes(world):
    return [kw for op, kw in world.clients["lambda"].calls
            if op == "update_function_configuration" and kw["FunctionName"] == CATALOG]


@pytest.mark.parametrize("secret, named", [
    (rs.MASTER_SECRET, "master"),
    (ss.GATEWAY_SECRET, "meridian_gateway"),
])
def test_a_snapshot_with_the_catalog_lambda_on_another_login_is_never_restored(
        tmp_path, secret, named):
    world, saved, _ = released(tmp_path, manual=False)
    saved["lambdas"]["catalog"]["environment"]["AURORA_SECRET_ARN"] = secret

    results, said = go_with(world, saved)

    assert results["lambda catalog"].status == "failed"
    assert catalog_writes(world) == []
    text = "\n".join(results["lambda catalog"].lines)
    assert named in text and "meridian_catalog" in text and secret not in text


@pytest.mark.parametrize("environment", [
    {"AURORA_SECRET_ARN": ss.API_SECRET},
    {"POOL_SIZE": "4"},
])
def test_a_saved_secret_that_is_not_the_catalog_logins_is_never_restored(tmp_path, environment):
    world, saved, _ = released(tmp_path, manual=False)
    saved["lambdas"]["catalog"]["environment"] = environment

    results, _ = go_with(world, saved)

    assert results["lambda catalog"].status == "failed"
    assert catalog_writes(world) == []
    assert "not the meridian_catalog login's secret" in "\n".join(results["lambda catalog"].lines)


def test_without_the_catalog_secret_in_the_env_the_saved_one_cannot_be_confirmed(tmp_path):
    world, saved, _ = released(tmp_path, manual=False)
    env = {key: value for key, value in ss.ENV.items() if key != "AURORA_CATALOG_SECRET_ARN"}

    results, _ = go_with(world, saved, env)

    assert results["lambda catalog"].status == "failed"
    assert catalog_writes(world) == []
    assert "AURORA_CATALOG_SECRET_ARN" in "\n".join(results["lambda catalog"].lines)


def test_the_dry_run_reports_the_same_refusal(tmp_path):
    world, saved, _ = released(tmp_path, manual=False)
    saved["lambdas"]["catalog"]["environment"]["AURORA_SECRET_ARN"] = rs.MASTER_SECRET

    results, _ = go_with(world, saved, apply=False)

    assert results["lambda catalog"].status == "failed"
    assert world.writes() == []


def test_a_snapshot_on_the_catalog_login_is_restored(tmp_path):
    world, saved, _ = released(tmp_path, manual=False)
    assert saved["lambdas"]["catalog"]["environment"]["AURORA_SECRET_ARN"] == ss.CATALOG_SECRET

    results, _ = go_with(world, saved)

    assert results["lambda catalog"].status == "restored"
    live = world.lambdas[CATALOG]["Environment"]["Variables"]["AURORA_SECRET_ARN"]
    assert live == ss.CATALOG_SECRET


def test_the_refusal_does_not_stop_the_holds_restore(tmp_path):
    world, saved, _ = released(tmp_path, manual=False)
    saved["lambdas"]["catalog"]["environment"]["AURORA_SECRET_ARN"] = rs.MASTER_SECRET

    results, _ = go_with(world, saved)

    assert results["lambda holds"].status == "restored"


# ---------------------------------------------------------------- the dependency


def test_the_catalog_restore_does_not_wait_for_the_secret_parameter():
    by_name = {step.name: step for step in restore_hops.steps(["MeridianConcierge"])}

    assert by_name["lambda holds"].needs == (restore_hops.SECRET_STEP,)
    assert by_name["lambda catalog"].needs == ()


def test_a_failed_secret_parameter_skips_the_holds_lambda_and_still_restores_the_catalog_one(
        tmp_path):
    world, saved, _ = released(tmp_path, manual=False)
    world.failures["put_parameter"] = client_error("ThrottlingException")

    results, said = go_with(world, saved)

    assert results["lambda secret parameter"].status == "failed"
    assert results["lambda holds"].status == "skipped"
    assert results["lambda catalog"].status == "restored"
    assert [kw["FunctionName"] for op, kw in world.clients["lambda"].calls
            if op == "update_function_configuration"] == [CATALOG]
    assert "skipped because lambda secret parameter failed" in "\n".join(said)
