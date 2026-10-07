"""The backend, gateway and identity logins are provisioned without a password reaching Postgres."""

import base64
import hashlib
import hmac
import json
import os
import re
import stat
from datetime import datetime, timedelta

import pytest
from botocore.exceptions import ClientError

from scripts import provision_service_logins as prov

CLUSTER = "arn:aws:rds:us-east-1:111122223333:cluster:c1"
ACCOUNT = "111122223333"
PASSWORD = "correct-horse-battery-staple-0123456789AB"


def secret_arn(spec):
    return f"arn:aws:secretsmanager:us-east-1:{ACCOUNT}:secret:{spec.secret_name}-AbC123"


def policy_arn(spec):
    return f"arn:aws:iam::{ACCOUNT}:policy/{spec.policy_name}"


@pytest.fixture(params=sorted(prov.LOGINS))
def spec(request):
    return prov.LOGINS[request.param]


class Recorder:
    def __init__(self, answers=None):
        self.calls = []
        self.answers = answers or {}

    def __getattr__(self, name):
        def record(**kwargs):
            self.calls.append((name, kwargs))
            return self.answers.get(name, {})
        return record


@pytest.fixture
def aws(spec):
    sm = Recorder({"create_secret": {"ARN": secret_arn(spec)}, "describe_secret": {}})
    iam = Recorder({"create_policy": {"Policy": {"Arn": policy_arn(spec)}}})
    master = Recorder({"execute_statement": {"records": []}})
    return sm, iam, master


def run(spec, aws, **overrides):
    sm, iam, master = aws
    arguments = {"sm": sm, "iam": iam, "master": master, "cluster_arn": CLUSTER,
                 "database": "meridian", "apply": True, "password": PASSWORD,
                 "master_secret_arn": "arn:master", **overrides}
    return prov.provision_login(spec, **arguments)


def test_there_are_exactly_three_logins_each_with_its_own_names():
    assert sorted(prov.LOGINS) == ["backend", "gateway", "identity"]
    specs = list(prov.LOGINS.values())
    for field in ("role", "secret_name", "policy_name", "env_key"):
        assert len({getattr(s, field) for s in specs}) == 3
    assert [s.role for s in specs] == ["meridian_backend", "meridian_gateway", "meridian_identity"]
    assert [s.env_key for s in specs] == [
        "AURORA_BACKEND_SECRET_ARN", "AURORA_GATEWAY_SECRET_ARN", "AURORA_IDENTITY_SECRET_ARN"]


def test_the_policy_names_one_cluster_and_the_logins_one_secret(spec):
    doc = prov.policy_document(spec, CLUSTER, secret_arn(spec))
    prefix = spec.key.capitalize()
    assert doc["Statement"] == [
        {"Sid": f"{prefix}DataApi", "Effect": "Allow",
         "Action": list(prov.DATA_API_ACTIONS), "Resource": CLUSTER},
        {"Sid": f"{prefix}LoginSecret", "Effect": "Allow",
         "Action": "secretsmanager:GetSecretValue", "Resource": secret_arn(spec)},
    ]


def test_a_dry_run_changes_nothing(spec, aws, capsys):
    run(spec, aws, apply=False, master_secret_arn="")
    assert [c for r in aws for c in r.calls
            if not c[0].startswith(("describe", "get", "list"))] == []
    out = capsys.readouterr()
    assert not re.search(r"\d{12}", out.out + out.err)


def scram_keys(password, salt, iterations):
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("ascii"), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    return hashlib.sha256(client_key).digest(), server_key


def test_postgres_receives_a_verifier_and_never_the_password(spec, aws, monkeypatch):
    monkeypatch.setattr(prov, "_verify_login", lambda *a, **k: spec.role)
    run(spec, aws)
    sm, iam, master = aws
    sql = [kw["sql"] for name, kw in master.calls if name == "execute_statement"]
    assert len(sql) == 1 and re.fullmatch(
        rf"ALTER ROLE {spec.role} LOGIN PASSWORD "
        r"'SCRAM-SHA-256\$4096:[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+'",
        sql[0])
    assert PASSWORD not in json.dumps(master.calls + iam.calls)
    created = next(kw for name, kw in sm.calls if name == "create_secret")
    assert created["Name"] == spec.secret_name
    stored = json.loads(created["SecretString"])
    assert stored == {"username": spec.role, "password": PASSWORD}
    match = re.search(r"SCRAM-SHA-256\$(\d+):([^$]+)\$([^:]+):([^']+)'", sql[0])
    iterations, salt = int(match.group(1)), base64.b64decode(match.group(2))
    stored_key, server_key = scram_keys(stored["password"], salt, iterations)
    assert base64.b64decode(match.group(3)) == stored_key
    assert base64.b64decode(match.group(4)) == server_key


def test_the_policy_is_created_for_this_logins_secret(spec, aws, monkeypatch):
    monkeypatch.setattr(prov, "_verify_login", lambda *a, **k: spec.role)
    run(spec, aws)
    _, iam, _ = aws
    created = next(kw for name, kw in iam.calls if name == "create_policy")
    assert created["PolicyName"] == spec.policy_name
    resources = [s["Resource"] for s in json.loads(created["PolicyDocument"])["Statement"]]
    assert resources == [CLUSTER, secret_arn(spec)]


def test_a_malformed_verifier_never_reaches_the_database(spec, aws):
    _, _, master = aws
    with pytest.raises(SystemExit):
        prov._set_login(master, spec, CLUSTER, "arn:s", "meridian", "x'; DROP ROLE y; --")
    assert master.calls == []


def test_a_rerun_rotates_the_existing_secret_and_adds_a_policy_version(spec, aws, monkeypatch):
    sm, iam, _ = aws
    sm.answers["describe_secret"] = {"ARN": secret_arn(spec)}
    iam.answers["get_policy"] = {"Policy": {"Arn": policy_arn(spec)}}
    iam.answers["list_policy_versions"] = {"Versions": []}
    monkeypatch.setattr(prov, "_verify_login", lambda *a, **k: spec.role)
    run(spec, aws)
    names = [c[0] for c in sm.calls + iam.calls]
    assert "put_secret_value" in names and "create_secret" not in names
    assert "create_policy_version" in names and "create_policy" not in names


def test_apply_without_a_master_secret_fails_before_any_call(spec, aws):
    with pytest.raises(ValueError):
        run(spec, aws, master_secret_arn="")
    assert [c for r in aws for c in r.calls] == []


def test_a_full_apply_prints_neither_the_password_nor_an_account_id(spec, aws, monkeypatch, capsys):
    monkeypatch.setattr(prov, "_verify_login", lambda *a, **k: spec.role)
    run(spec, aws)
    out = capsys.readouterr()
    assert PASSWORD not in out.out + out.err
    assert not re.search(r"\d{12}", out.out + out.err)
    assert f"verified: {spec.role}" in out.out


def test_a_login_that_connects_as_someone_else_fails_the_run(spec, monkeypatch):
    class Wrong:
        def __init__(self, **kw):
            pass

        async def execute_one(self, query):
            return {"u": "meridian_admin", "b": False}

    monkeypatch.setattr(prov, "RDSDataClient", Wrong)
    with pytest.raises(SystemExit, match="did not connect as"):
        prov._verify_login(spec, CLUSTER, "arn:secret", "meridian")


def test_a_login_with_bypassrls_fails_the_run(spec, monkeypatch):
    class Bypass:
        def __init__(self, **kw):
            pass

        async def execute_one(self, query):
            return {"u": spec.role, "b": True}

    monkeypatch.setattr(prov, "RDSDataClient", Bypass)
    with pytest.raises(SystemExit, match="without BYPASSRLS"):
        prov._verify_login(spec, CLUSTER, "arn:secret", "meridian")


def test_write_env_replaces_exactly_one_line(tmp_path):
    env = tmp_path / ".env"
    env.write_text("A=1\nAURORA_GATEWAY_SECRET_ARN=old\nB=2\n")
    prov.write_env(env, "AURORA_GATEWAY_SECRET_ARN", "arn:new")
    assert env.read_text() == "A=1\nAURORA_GATEWAY_SECRET_ARN=arn:new\nB=2\n"


def test_write_env_appends_when_the_line_is_absent(tmp_path):
    env = tmp_path / ".env"
    env.write_text("A=1")
    prov.write_env(env, "AURORA_BACKEND_SECRET_ARN", "arn:new")
    assert env.read_text() == "A=1\nAURORA_BACKEND_SECRET_ARN=arn:new\n"


def _client_error(code):
    return ClientError({"Error": {"Code": code, "Message": "x"}}, "Op")


class Raiser:
    def __init__(self, code):
        self.code = code

    def describe_secret(self, **kw):
        raise _client_error(self.code)

    get_policy = describe_secret


def test_missing_secret_and_policy_are_treated_as_absent(spec):
    assert prov._find_secret_arn(Raiser("ResourceNotFoundException"), spec) is None
    assert prov._find_policy_arn(Raiser("NoSuchEntity"), spec, ACCOUNT) is None


def test_other_lookup_errors_propagate(spec):
    with pytest.raises(ClientError):
        prov._find_secret_arn(Raiser("AccessDeniedException"), spec)


def test_five_versions_prune_the_oldest_non_default(spec, aws):
    _, iam, _ = aws
    base = datetime(2026, 1, 1)
    iam.answers["list_policy_versions"] = {"Versions": [
        {"VersionId": f"v{i}", "IsDefaultVersion": i == 1, "CreateDate": base + timedelta(days=i)}
        for i in range(1, 6)]}
    prov._upsert_policy(iam, spec, policy_arn(spec), {"Version": "x"}, ACCOUNT)
    deleted = [kw["VersionId"] for name, kw in iam.calls if name == "delete_policy_version"]
    assert deleted == ["v2"]
    assert iam.calls[-1][0] == "create_policy_version"


def test_a_policy_created_in_another_account_stops_the_run(spec, aws):
    _, iam, _ = aws
    with pytest.raises(SystemExit):
        prov._upsert_policy(iam, spec, None, {"Version": "x"}, "999999999999")


def test_the_wrong_account_stops_main_before_any_write(monkeypatch, tmp_path):
    clients = {"sts": Recorder({"get_caller_identity": {"Account": "999999999999"}}),
               "secretsmanager": Recorder(), "iam": Recorder(), "rds-data": Recorder()}
    monkeypatch.setattr(prov.boto3, "client", lambda name, **kw: clients[name])
    monkeypatch.setattr(prov, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setenv("AURORA_CLUSTER_ARN", CLUSTER)
    monkeypatch.setenv("AURORA_SECRET_ARN", "arn:master")
    with pytest.raises(SystemExit) as exc:
        prov.main(["--apply", "--write-env"])
    assert "999999999999" not in str(exc.value) and "3333" in str(exc.value)
    assert [c for n in ("secretsmanager", "iam", "rds-data") for c in clients[n].calls] == []
    assert not (tmp_path / ".env").exists()


def test_main_provisions_one_chosen_login_and_writes_only_its_line(monkeypatch, tmp_path):
    clients = {"sts": Recorder({"get_caller_identity": {"Account": ACCOUNT}}),
               "secretsmanager": Recorder({"describe_secret": {}}),
               "iam": Recorder(), "rds-data": Recorder()}
    gateway = prov.LOGINS["gateway"]
    clients["secretsmanager"].answers["create_secret"] = {"ARN": secret_arn(gateway)}
    clients["iam"].answers["create_policy"] = {"Policy": {"Arn": policy_arn(gateway)}}
    monkeypatch.setattr(prov.boto3, "client", lambda name, **kw: clients[name])
    monkeypatch.setattr(prov, "_verify_login", lambda *a, **k: "meridian_gateway")
    env = tmp_path / ".env"
    monkeypatch.setattr(prov, "ENV_FILE", env)
    monkeypatch.setenv("AURORA_CLUSTER_ARN", CLUSTER)
    monkeypatch.setenv("AURORA_SECRET_ARN", "arn:master")
    prov.main(["--login", "gateway", "--apply", "--write-env"])
    assert env.read_text() == f"AURORA_GATEWAY_SECRET_ARN={secret_arn(gateway)}\n"
    sql = [kw["sql"] for n, kw in clients["rds-data"].calls if n == "execute_statement"]
    assert len(sql) == 1 and sql[0].startswith("ALTER ROLE meridian_gateway LOGIN")


class FailingClient:
    attempts = 0

    def __init__(self, **kw):
        pass

    async def execute_one(self, query):
        FailingClient.attempts += 1
        raise _client_error("BadRequestException")


def test_verify_retries_then_gives_an_actionable_error(spec, monkeypatch):
    FailingClient.attempts = 0
    monkeypatch.setattr(prov, "RDSDataClient", FailingClient)
    delays = []
    with pytest.raises(SystemExit) as exc:
        prov._verify_login(spec, CLUSTER, "arn:secret", "meridian", sleep=delays.append)
    assert FailingClient.attempts == 3 and delays == [2, 2]
    assert "BadRequestException" in str(exc.value)
    assert "re-run this script to repair" in str(exc.value)


def _main_env(monkeypatch, tmp_path, clients):
    monkeypatch.setattr(prov.boto3, "client", lambda name, **kw: clients[name])
    monkeypatch.setattr(prov, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setenv("AURORA_CLUSTER_ARN", CLUSTER)
    monkeypatch.setenv("AURORA_SECRET_ARN", "arn:master")


def _clients(**overrides):
    base = {"sts": Recorder({"get_caller_identity": {"Account": ACCOUNT}}),
            "secretsmanager": Recorder({"describe_secret": {}}),
            "iam": Recorder(), "rds-data": Recorder()}
    base.update(overrides)
    return base


class DenyingSecrets:
    def describe_secret(self, **kw):
        message = "User: arn:aws:sts::123456789012:assumed-role/dev/me is not authorized"
        raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": message}},
                          "DescribeSecret")


def test_an_aws_error_exits_with_the_operation_code_and_no_account_id(monkeypatch, tmp_path):
    _main_env(monkeypatch, tmp_path, _clients(secretsmanager=DenyingSecrets()))
    with pytest.raises(SystemExit) as exc:
        prov.main(["--apply"])
    text = str(exc.value)
    assert "DescribeSecret" in text and "AccessDeniedException" in text
    assert not re.search(r"\d{12}", text)


@pytest.mark.parametrize("missing", ["AURORA_CLUSTER_ARN", "AURORA_SECRET_ARN"])
def test_a_missing_environment_variable_is_named_in_the_exit(monkeypatch, tmp_path, missing):
    _main_env(monkeypatch, tmp_path, _clients())
    monkeypatch.delenv(missing)
    with pytest.raises(SystemExit) as exc:
        prov.main(["--apply"])
    assert missing in str(exc.value) and "Traceback" not in str(exc.value)


def test_a_dry_run_does_not_need_the_master_secret(monkeypatch, tmp_path, capsys):
    clients = _clients()
    _main_env(monkeypatch, tmp_path, clients)
    monkeypatch.delenv("AURORA_SECRET_ARN")
    prov.main(["--login", "gateway"])
    assert "dry run" in capsys.readouterr().out
    assert clients["rds-data"].calls == []


def test_an_unverified_apply_writes_no_env_file(monkeypatch, tmp_path):
    gateway = prov.LOGINS["gateway"]
    clients = _clients()
    clients["secretsmanager"].answers["create_secret"] = {"ARN": secret_arn(gateway)}
    clients["iam"].answers["create_policy"] = {"Policy": {"Arn": policy_arn(gateway)}}
    _main_env(monkeypatch, tmp_path, clients)
    monkeypatch.setattr(prov, "_verify_login", lambda *a, **k: "")
    prov.main(["--login", "gateway", "--apply", "--write-env"])
    assert not (tmp_path / ".env").exists()


def test_a_failing_write_env_leaves_the_old_file_intact(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("A=1\n")

    def broken_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(prov.os, "replace", broken_replace)
    with pytest.raises(OSError):
        prov.write_env(env, "AURORA_BACKEND_SECRET_ARN", "arn:new")
    assert env.read_text() == "A=1\n"
    assert os.listdir(tmp_path) == [".env"]


def test_write_env_keeps_the_file_mode(tmp_path):
    env = tmp_path / ".env"
    env.write_text("A=1\n")
    env.chmod(0o640)
    prov.write_env(env, "AURORA_BACKEND_SECRET_ARN", "arn:new")
    assert stat.S_IMODE(env.stat().st_mode) == 0o640
