"""The sign-in users are seeded without their passwords reaching a log, a file or a process list."""

import json
import re
import sys
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError

from scripts import seed_cognito_users as seed

POOL = "us-east-1_AbCdEfGhI"
CLUSTER = "arn:aws:rds:us-east-1:111122223333:cluster:c1"


class Recorder:
    def __init__(self, answers=None):
        self.calls = []
        self.answers = answers or {}

    def __getattr__(self, name):
        def record(**kwargs):
            self.calls.append((name, kwargs))
            answer = self.answers.get(name, {})
            if isinstance(answer, Exception):
                raise answer
            return answer
        return record


def _error(code):
    return ClientError({"Error": {"Code": code, "Message": "x"}}, "Op")


class FakeCognito:
    """A user pool that remembers whether the user exists."""

    def __init__(self, existing=False, sub="11111111-aaaa-bbbb-cccc-222222222222"):
        self.exists, self.sub, self.calls = existing, sub, []

    def admin_get_user(self, **kwargs):
        self.calls.append(("admin_get_user", kwargs))
        if not self.exists:
            raise _error("UserNotFoundException")
        return {"UserAttributes": [{"Name": "sub", "Value": self.sub}]}

    def admin_create_user(self, **kwargs):
        self.calls.append(("admin_create_user", kwargs))
        self.exists = True

    def admin_set_user_password(self, **kwargs):
        self.calls.append(("admin_set_user_password", kwargs))


def cognito(existing=False, sub="11111111-aaaa-bbbb-cccc-222222222222"):
    return FakeCognito(existing, sub)


def run(user, *, idp, apply=True, password="Generated-Password-1234-Aa1", keychain=None):
    sm = Recorder({"describe_secret": _error("ResourceNotFoundException")})
    rds = Recorder()
    kept = []
    subject = seed.seed_user(
        user, idp=idp, sm=sm, rds=rds, pool_id=POOL, cluster_arn=CLUSTER,
        master_secret_arn="arn:master", database="meridian", apply=apply,
        keychain=keychain or (lambda u, p: kept.append((u.key, p))), password=password)
    return subject, sm, rds, kept


def test_there_are_two_users_bound_to_two_different_travelers():
    jordan, decoy = seed.USERS["jordan"], seed.USERS["decoy"]
    assert (jordan.traveler_id, decoy.traveler_id) == ("trv_meridian_demo", "trv_demo_decoy")
    assert jordan.email != decoy.email and jordan.secret_name != decoy.secret_name
    assert decoy.picture is None and jordan.picture == "/travel/jordan-morgan.jpg"


def test_a_dry_run_reads_but_changes_nothing(capsys):
    idp = cognito()
    subject, sm, rds, kept = run(seed.USERS["jordan"], idp=idp, apply=False)
    assert subject is None and kept == []
    writes = [c for r in (idp, sm, rds) for c in r.calls
              if not c[0].startswith(("describe", "get", "admin_get"))]
    assert writes == []
    assert "would create" in capsys.readouterr().out


def test_a_new_user_is_created_without_an_email_and_given_a_permanent_password():
    idp = cognito(sub="sub-new")
    subject, *_ = run(seed.USERS["jordan"], idp=idp)
    assert subject == "sub-new"
    create = next(kw for n, kw in idp.calls if n == "admin_create_user")
    assert create["MessageAction"] == "SUPPRESS"
    assert create["Username"] == "jordan.morgan@example.com"
    attributes = {a["Name"]: a["Value"] for a in create["UserAttributes"]}
    assert attributes == {"email": "jordan.morgan@example.com", "email_verified": "true",
                          "name": "Jordan Morgan", "picture": "/travel/jordan-morgan.jpg"}
    password = next(kw for n, kw in idp.calls if n == "admin_set_user_password")
    assert password["Permanent"] is True


def test_the_decoy_has_no_picture_attribute():
    attributes = {a["Name"] for a in seed._attributes(seed.USERS["decoy"])}
    assert attributes == {"email", "email_verified", "name"}


def test_an_existing_user_is_reset_not_recreated():
    idp = cognito(existing=True)
    subject, *_ = run(seed.USERS["decoy"], idp=idp)
    names = [n for n, _ in idp.calls]
    assert "admin_create_user" not in names and "admin_set_user_password" in names
    assert subject == "11111111-aaaa-bbbb-cccc-222222222222"


def test_the_password_goes_to_cognito_secrets_manager_and_the_keychain_only():
    idp = cognito(existing=True)
    _, sm, rds, kept = run(seed.USERS["jordan"], idp=idp, password="Generated-Password-1234-Aa1")
    secret = json.loads(next(kw for n, kw in sm.calls if n == "create_secret")["SecretString"])
    assert secret == {"username": "jordan.morgan@example.com",
                      "password": "Generated-Password-1234-Aa1"}
    assert kept == [("jordan", "Generated-Password-1234-Aa1")]
    assert "Generated-Password" not in json.dumps(rds.calls)


def test_an_existing_secret_is_overwritten_not_duplicated():
    sm = Recorder({"describe_secret": {"ARN": "arn:x"}})
    seed.store_secret(sm, seed.USERS["jordan"], "pw")
    assert [n for n, _ in sm.calls] == ["describe_secret", "put_secret_value"]


def test_the_binding_row_grants_the_cognito_subject_its_one_traveler_as_the_master():
    idp = cognito(existing=True, sub="sub-decoy")
    _, _, rds, _ = run(seed.USERS["decoy"], idp=idp)
    (name, call), = rds.calls
    assert call["secretArn"] == "arn:master" and "ON CONFLICT" in call["sql"]
    values = {p["name"]: p["value"]["stringValue"] for p in call["parameters"]}
    assert values == {
        "binding_id": seed.binding_id("sub-decoy", "trv_demo_decoy"),
        "provider": "cognito", "subject_id": "sub-decoy",
        "traveler_id": "trv_demo_decoy", "granted_by": "scripts/seed_cognito_users.py"}
    assert re.fullmatch(r"bind_[0-9a-f]{16}", values["binding_id"])


def test_binding_ids_differ_per_subject_and_traveler():
    assert seed.binding_id("a", "t1") != seed.binding_id("b", "t1")
    assert seed.binding_id("a", "t1") != seed.binding_id("a", "t2")


def test_generated_passwords_meet_the_pool_policy_and_differ():
    passwords = {seed.generate_password() for _ in range(20)}
    assert len(passwords) == 20
    for password in passwords:
        assert len(password) >= 14
        assert re.search(r"[a-z]", password) and re.search(r"[A-Z]", password)
        assert re.search(r"\d", password) and re.search(r"[-_]", password)


def test_the_keychain_receives_the_password_on_stdin_never_in_arguments(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    seen = {}

    def fake_run(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        return SimpleNamespace(returncode=0)

    seed.store_keychain(seed.USERS["jordan"], "Secret-Pw_1234-Aa1", run=fake_run)
    assert seen["argv"] == ["security", "-i"]
    assert "Secret-Pw_1234-Aa1" not in " ".join(seen["argv"])
    assert "-w 'Secret-Pw_1234-Aa1'" in seen["input"]
    assert "-s meridian-cognito" in seen["input"] and "-U" in seen["input"]


def test_a_keychain_failure_stops_the_run_without_echoing_the_password(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    failing = lambda argv, **kw: SimpleNamespace(returncode=45)  # noqa: E731
    with pytest.raises(SystemExit) as exit_info:
        seed.store_keychain(seed.USERS["jordan"], "Secret-Pw_1234-Aa1", run=failing)
    assert "Secret-Pw" not in str(exit_info.value) and "exit 45" in str(exit_info.value)


def test_the_keychain_is_required_so_other_platforms_stop(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(SystemExit, match="Keychain"):
        seed.store_keychain(seed.USERS["jordan"], "pw")


def test_an_unexpected_password_alphabet_is_not_quoted_into_a_command(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    with pytest.raises(SystemExit, match="alphabet"):
        seed.store_keychain(seed.USERS["jordan"], "pw'; add-generic-password -a x")


def test_a_full_apply_prints_no_password_and_no_account_id(capsys):
    idp = cognito(existing=True)
    run(seed.USERS["jordan"], idp=idp, password="Generated-Password-1234-Aa1")
    out = capsys.readouterr()
    assert "Generated-Password" not in out.out + out.err
    assert not re.search(r"\d{12}", out.out + out.err)


def test_main_needs_the_pool_id_before_it_touches_aws(monkeypatch, tmp_path):
    monkeypatch.setattr(seed, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setenv("AURORA_CLUSTER_ARN", CLUSTER)
    monkeypatch.delenv("MERIDIAN_COGNITO_USER_POOL_ID", raising=False)
    monkeypatch.setattr(seed.boto3, "client", lambda *a, **k: pytest.fail("touched AWS"))
    with pytest.raises(SystemExit, match="MERIDIAN_COGNITO_USER_POOL_ID"):
        seed.main(["--apply"])


def test_the_wrong_account_stops_main_before_any_write(monkeypatch, tmp_path):
    clients = {"sts": Recorder({"get_caller_identity": {"Account": "999999999999"}}),
               "cognito-idp": Recorder(), "secretsmanager": Recorder(), "rds-data": Recorder()}
    monkeypatch.setattr(seed.boto3, "client", lambda name, **kw: clients[name])
    monkeypatch.setattr(seed, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setenv("AURORA_CLUSTER_ARN", CLUSTER)
    monkeypatch.setenv("AURORA_SECRET_ARN", "arn:master")
    monkeypatch.setenv("MERIDIAN_COGNITO_USER_POOL_ID", POOL)
    with pytest.raises(SystemExit) as exit_info:
        seed.main(["--apply"])
    assert "999999999999" not in str(exit_info.value)
    untouched = ("cognito-idp", "secretsmanager", "rds-data")
    assert [c for n in untouched for c in clients[n].calls] == []


def _main_env(monkeypatch, tmp_path, idp):
    clients = {"sts": Recorder({"get_caller_identity": {"Account": "111122223333"}}),
               "cognito-idp": idp, "secretsmanager": Recorder(), "rds-data": Recorder()}
    monkeypatch.setattr(seed.boto3, "client", lambda name, **kw: clients[name])
    monkeypatch.setattr(seed, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setenv("AURORA_CLUSTER_ARN", CLUSTER)
    monkeypatch.setenv("AURORA_SECRET_ARN", "arn:master")
    monkeypatch.setenv("MERIDIAN_COGNITO_USER_POOL_ID", POOL)


def test_an_aws_failure_stops_main_with_a_redacted_actionable_message(monkeypatch, tmp_path):
    failure = ClientError({"Error": {"Code": "AccessDenied", "Message":
                                     "no access to arn:aws:rds:us-east-1:111122223333:cluster:c1"}},
                          "AdminGetUser")
    _main_env(monkeypatch, tmp_path, Recorder({"admin_get_user": failure}))
    with pytest.raises(SystemExit) as exit_info:
        seed.main(["--apply"])
    message = str(exit_info.value)
    assert "AdminGetUser" in message and "AccessDenied" in message and "AWS_PROFILE" in message
    assert not re.search(r"\d{12}", message)


def test_a_transport_failure_stops_main_without_a_traceback(monkeypatch, tmp_path):
    down = EndpointConnectionError(endpoint_url="https://cognito-idp.us-east-1.amazonaws.com")
    _main_env(monkeypatch, tmp_path, Recorder({"admin_get_user": down}))
    with pytest.raises(SystemExit, match="AWS_PROFILE"):
        seed.main(["--apply"])


def test_a_missing_setting_stops_main_with_its_name(monkeypatch, tmp_path):
    _main_env(monkeypatch, tmp_path, Recorder())
    monkeypatch.delenv("AURORA_SECRET_ARN")
    with pytest.raises(SystemExit, match="AURORA_SECRET_ARN"):
        seed.main(["--apply"])
