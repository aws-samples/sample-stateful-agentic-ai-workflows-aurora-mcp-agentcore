"""The login rotation Lambda: four steps, retries of each, and the alternating users guarantee.

Only the two AWS boundaries are faked (tests/login_rotation_support.py). The SQL itself, and
whether Aurora accepts a CREATEROLE login's ALTER ROLE, is covered by the live checks in
tests/test_migration_034_live.py.
"""

import importlib.util
import sys
from pathlib import Path

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from scripts.provision_workflow_login import scram_sha256_verifier as provisioning_verifier
from tests.login_rotation_support import (
    CLONE,
    CLUSTER,
    LOGIN,
    LOGIN_SECRET,
    ROTATOR_SECRET,
    FakeDataApi,
    FakeSecrets,
    client_error,
    role_row,
    verifier_matches,
)

SOURCE = (
    Path(__file__).resolve().parents[1] / "infra" / "functions" / "login_rotation"
    / "login_rotation.py"
)
NEW_PASSWORD = "p" * 43


@pytest.fixture
def lr(monkeypatch):
    monkeypatch.setattr(sys, "dont_write_bytecode", True)
    spec = importlib.util.spec_from_file_location("login_rotation_under_test", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class World:
    """A rotating secret, a database and the module's own settings."""

    def __init__(self, lr):
        self.lr = lr
        self.sm = FakeSecrets()
        self.data = FakeDataApi({LOGIN_SECRET: self.sm})
        self.data.verifiers[LOGIN] = lr.scram_sha256_verifier("original-password")
        self.settings = lr.Settings(CLUSTER, "meridian", ROTATOR_SECRET, LOGIN)
        self.sleeps = []

    def step(self, name, token="t1", staged=True):
        if staged:
            self.sm.begin(token)
        self.lr.rotate(name, LOGIN_SECRET, token, sm=self.sm, data=self.data,
                       settings=self.settings, sleep=self.sleeps.append)

    def full_rotation(self, token):
        for name in ("createSecret", "setSecret", "testSecret", "finishSecret"):
            self.step(name, token)

    def alters(self):
        return [sql for _arn, sql in self.data.statements if sql.startswith("ALTER ROLE")]

    def can_connect(self, username, password):
        verifier = self.data.verifiers.get(username)
        return verifier is not None and verifier_matches(password, verifier)


@pytest.fixture
def world(lr, monkeypatch):
    monkeypatch.setattr(lr.secrets, "token_urlsafe", lambda _n: NEW_PASSWORD)
    return World(lr)


def start(world, token="t1"):
    """Run createSecret, the step that stages the pending version."""
    world.step("createSecret", token)


def test_a_full_rotation_moves_the_secret_to_the_clone_and_both_users_connect(world):
    world.full_rotation("t1")
    assert world.sm.value("AWSCURRENT")["username"] == CLONE
    assert world.sm.value("AWSCURRENT")["password"] == NEW_PASSWORD
    assert world.sm.value("AWSCURRENT")["note"] == "kept"
    assert world.can_connect(CLONE, NEW_PASSWORD)
    assert world.can_connect(LOGIN, "original-password")
    previous = world.sm.value("AWSPREVIOUS")
    assert (previous["username"], previous["password"]) == (LOGIN, "original-password")


def test_the_next_rotation_alternates_back_to_the_login(world, monkeypatch):
    world.full_rotation("t1")
    monkeypatch.setattr(world.lr.secrets, "token_urlsafe", lambda _n: "q" * 43)
    world.full_rotation("t2")
    current = world.sm.value("AWSCURRENT")
    assert (current["username"], current["password"]) == (LOGIN, "q" * 43)
    assert world.can_connect(CLONE, NEW_PASSWORD)
    assert world.can_connect(LOGIN, "q" * 43)
    assert not world.can_connect(LOGIN, "original-password")


def test_the_previous_credential_works_during_the_whole_rotation(world):
    for name in ("createSecret", "setSecret", "testSecret"):
        world.step(name)
        assert world.can_connect(LOGIN, world.sm.value("AWSCURRENT")["password"])
    world.step("finishSecret")
    assert world.can_connect(LOGIN, "original-password")


def test_only_the_verifier_reaches_postgres(world):
    world.full_rotation("t1")
    for _arn, sql in world.data.statements:
        assert NEW_PASSWORD not in sql
        assert "original-password" not in sql
    [alter] = world.alters()
    assert alter.startswith(f"ALTER ROLE {CLONE} LOGIN PASSWORD 'SCRAM-SHA-256$4096:")


def test_every_statement_before_the_connect_check_uses_the_rotator_secret(world):
    world.full_rotation("t1")
    for arn, sql in world.data.statements:
        expected = LOGIN_SECRET if sql.startswith("SELECT current_user") else ROTATOR_SECRET
        assert arn == expected


def test_the_verifier_matches_the_provisioning_script_for_the_same_salt(lr):
    salt = bytes(range(16))
    assert lr.scram_sha256_verifier("pw", salt=salt) == provisioning_verifier("pw", salt=salt)
    assert lr.VERIFIER.fullmatch(lr.scram_sha256_verifier("pw"))


# createSecret


def test_create_secret_twice_makes_one_pending_version_with_one_password(world, monkeypatch):
    start(world)
    first = world.sm.value("AWSPENDING")
    monkeypatch.setattr(world.lr.secrets, "token_urlsafe", lambda _n: "z" * 43)
    start(world)
    assert world.sm.value("AWSPENDING") == first
    assert len(world.sm.versions) == 2


def test_create_secret_retries_after_a_failed_put(world):
    world.sm.fail["put_secret_value"] = [client_error("InternalServiceError")]
    with pytest.raises(ClientError) as caught:
        start(world)
    assert caught.value.response["Error"]["Code"] == "InternalServiceError"
    assert world.sm.versions["t1"]["value"] is None
    start(world)
    assert world.sm.value("AWSPENDING")["username"] == CLONE


def test_create_secret_refuses_a_username_outside_the_pair(world):
    world.sm = FakeSecrets(username="meridian_admin")
    with pytest.raises(world.lr.RotationError, match="does not rotate"):
        world.lr.create_secret(world.sm, LOGIN_SECRET, "t1", world.settings)
    assert len(world.sm.versions) == 1


def test_create_secret_refuses_an_unusable_current_value(world):
    world.sm.versions["v1"]["value"] = '{"username": "meridian_backend"}'
    with pytest.raises(world.lr.RotationError, match="not in the expected state"):
        start(world)


# setSecret


def test_set_secret_twice_leaves_the_clone_with_the_pending_password(world):
    start(world)
    world.step("setSecret")
    world.step("setSecret")
    assert len(world.alters()) == 2
    assert world.can_connect(CLONE, NEW_PASSWORD)
    assert world.can_connect(LOGIN, "original-password")


def test_set_secret_retries_after_a_failed_alter(world):
    start(world)
    world.data.fail["alter"] = [client_error("StatementTimeoutException")]
    with pytest.raises(ClientError) as caught:
        world.step("setSecret")
    assert caught.value.response["Error"]["Code"] == "StatementTimeoutException"
    assert not world.can_connect(CLONE, NEW_PASSWORD)
    world.step("setSecret")
    assert world.can_connect(CLONE, NEW_PASSWORD)


def test_set_secret_retries_after_a_failed_role_read(world):
    start(world)
    world.data.fail["roles"] = [EndpointConnectionError(endpoint_url="x")]
    with pytest.raises(EndpointConnectionError):
        world.step("setSecret")
    assert world.alters() == []
    world.step("setSecret")
    assert len(world.alters()) == 1


@pytest.mark.parametrize("change", [
    {"rolbypassrls": True},
    {"rolsuper": True},
    {"rolcreaterole": True},
    {"rolcreatedb": True},
    {"rolreplication": True},
])
def test_set_secret_refuses_a_clone_or_login_with_a_forbidden_attribute(world, change):
    start(world)
    world.data.roles[CLONE].update(change)
    with pytest.raises(world.lr.RotationError, match="differ or are missing"):
        world.step("setSecret")
    assert world.alters() == []
    world.data.roles[CLONE].update({key: False for key in change})
    world.data.roles[LOGIN].update(change)
    with pytest.raises(world.lr.RotationError, match="differ or are missing"):
        world.step("setSecret")
    assert world.alters() == []


@pytest.mark.parametrize("change", [
    {"config": "statement_timeout=30s"},
    {"config": ""},
    {"rolconnlimit": 5},
    {"rolinherit": True},
    {"clone_is_member": False},
])
def test_set_secret_refuses_a_clone_that_differs_from_the_login(world, change):
    start(world)
    world.data.roles[CLONE].update(change)
    with pytest.raises(world.lr.RotationError, match="differ or are missing"):
        world.step("setSecret")
    assert world.alters() == []


def test_set_secret_refuses_when_the_clone_does_not_exist(world):
    start(world)
    del world.data.roles[CLONE]
    with pytest.raises(world.lr.RotationError, match="differ or are missing"):
        world.step("setSecret")
    assert world.alters() == []


def test_set_secret_refuses_a_pending_user_that_is_not_the_other_one(world):
    start(world)
    pending = world.sm.holder("AWSPENDING")
    world.sm.versions[pending]["value"] = world.sm.versions[pending]["value"].replace(
        CLONE, "meridian_admin")
    with pytest.raises(world.lr.RotationError, match="does not rotate"):
        world.step("setSecret")
    assert world.alters() == []


# testSecret


def test_test_secret_passes_after_set_secret(world):
    start(world)
    world.step("setSecret")
    world.step("testSecret")
    world.step("testSecret")


def test_test_secret_fails_when_the_pending_user_cannot_log_in(world):
    start(world)
    with pytest.raises(world.lr.RotationError, match="differ or are missing"):
        world.step("testSecret")


def test_test_secret_fails_on_a_clone_that_drifted(world):
    start(world)
    world.step("setSecret")
    world.data.roles[CLONE]["config"] = ""
    with pytest.raises(world.lr.RotationError, match="differ or are missing"):
        world.step("testSecret")


# finishSecret


def prepare(world):
    for name in ("createSecret", "setSecret", "testSecret"):
        world.step(name)


def test_finish_secret_twice_keeps_the_new_version_current(world):
    prepare(world)
    world.step("finishSecret")
    world.step("finishSecret")
    assert world.sm.value("AWSCURRENT")["username"] == CLONE
    assert world.sm.value("AWSPREVIOUS")["username"] == LOGIN


def test_finish_secret_restores_the_old_version_when_the_new_one_cannot_connect(world):
    prepare(world)
    world.data.verifiers[CLONE] = world.lr.scram_sha256_verifier("some-other-password")
    with pytest.raises(world.lr.RotationError, match="old one is restored"):
        world.step("finishSecret")
    assert world.sm.value("AWSCURRENT")["username"] == LOGIN
    assert world.sm.value("AWSCURRENT")["password"] == "original-password"
    assert world.can_connect(LOGIN, "original-password")
    assert world.sleeps == [world.lr.VERIFY_DELAY_SECONDS] * (world.lr.VERIFY_ATTEMPTS - 1)


def test_finish_secret_succeeds_when_retried_after_the_cause_is_fixed(world):
    prepare(world)
    good = world.data.verifiers[CLONE]
    world.data.verifiers[CLONE] = world.lr.scram_sha256_verifier("some-other-password")
    with pytest.raises(world.lr.RotationError):
        world.step("finishSecret")
    world.data.verifiers[CLONE] = good
    world.step("finishSecret")
    assert world.sm.value("AWSCURRENT")["username"] == CLONE


def test_finish_secret_retries_a_transient_connect_failure(world):
    prepare(world)
    world.data.fail["connect"] = [EndpointConnectionError(endpoint_url="x")]
    world.step("finishSecret")
    assert world.sm.value("AWSCURRENT")["username"] == CLONE
    assert world.sleeps == [world.lr.VERIFY_DELAY_SECONDS]


def test_finish_secret_retries_after_a_failed_promotion(world):
    prepare(world)
    world.sm.fail["update_secret_version_stage"] = [client_error("InternalServiceError")]
    with pytest.raises(ClientError) as caught:
        world.step("finishSecret")
    assert caught.value.response["Error"]["Code"] == "InternalServiceError"
    assert world.sm.value("AWSCURRENT")["username"] == LOGIN
    world.step("finishSecret")
    assert world.sm.value("AWSCURRENT")["username"] == CLONE


def test_finish_secret_refuses_a_clone_that_connects_with_bypass(world):
    prepare(world)
    world.data.roles[CLONE]["rolbypassrls"] = True
    with pytest.raises(world.lr.RotationError, match="old one is restored"):
        world.step("finishSecret")
    assert world.sm.value("AWSCURRENT")["username"] == LOGIN


# the guard around every step


def test_a_secret_without_rotation_enabled_is_refused(world):
    world.sm.rotation_enabled = False
    with pytest.raises(world.lr.RotationError, match="not in the expected state"):
        start(world)


def test_an_unknown_token_is_refused(world):
    with pytest.raises(world.lr.RotationError, match="not in the expected state"):
        world.step("setSecret", token="never-staged", staged=False)


def test_an_unknown_step_is_refused(world):
    start(world)
    with pytest.raises(world.lr.RotationError, match="not in the expected state"):
        world.step("dropEverything")


def test_a_step_for_a_version_that_is_already_current_does_nothing_but_finish(world):
    world.full_rotation("t1")
    count = len(world.data.statements)
    world.step("setSecret", "t1")
    assert len(world.data.statements) == count


# configuration and the handler


def env(**overrides):
    values = {
        "ROTATION_CLUSTER_ARN": CLUSTER, "ROTATION_DATABASE": "meridian",
        "ROTATION_ROTATOR_SECRET_ARN": ROTATOR_SECRET, "ROTATION_LOGIN_ROLE": LOGIN,
    }
    return {**values, **overrides}


def test_settings_come_from_the_environment(lr):
    settings = lr.settings_from_env(env())
    assert settings.clone_role == CLONE
    assert settings.login_role == LOGIN


@pytest.mark.parametrize("bad", [
    {"ROTATION_LOGIN_ROLE": "meridian_backend_clone"},
    {"ROTATION_LOGIN_ROLE": "Robert'); DROP TABLE x;--"},
    {"ROTATION_LOGIN_ROLE": ""},
])
def test_settings_refuse_a_role_that_is_not_a_plain_login_name(lr, bad):
    with pytest.raises(lr.RotationError, match="not configured"):
        lr.settings_from_env(env(**bad))


def test_settings_refuse_a_missing_variable(lr):
    values = env()
    del values["ROTATION_DATABASE"]
    with pytest.raises(lr.RotationError, match="not configured"):
        lr.settings_from_env(values)


def test_the_handler_logs_a_fixed_phrase_and_never_the_service_text(lr, monkeypatch, caplog):
    class Failing:
        def describe_secret(self, **_):
            raise client_error("AccessDeniedException")

    monkeypatch.setattr(lr.boto3, "client", lambda *_a, **_k: Failing())
    for name, value in env().items():
        monkeypatch.setenv(name, value)
    event = {"SecretId": LOGIN_SECRET, "ClientRequestToken": "t1", "Step": "createSecret"}
    with pytest.raises(lr.RotationError, match="Data API refused"):
        lr.lambda_handler(event, None)
    assert "AccessDeniedException" in caplog.text
    assert "not for logs" not in caplog.text


def test_require_consistent_pair_accepts_matching_rows(lr):
    settings = lr.Settings(CLUSTER, "meridian", ROTATOR_SECRET, LOGIN)
    lr.require_consistent_pair({LOGIN: role_row(LOGIN), CLONE: role_row(CLONE)}, settings)
