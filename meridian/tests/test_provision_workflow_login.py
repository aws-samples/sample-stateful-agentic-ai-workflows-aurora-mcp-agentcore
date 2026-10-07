"""The workflow login is provisioned without its password reaching Postgres or the logs."""

import base64
import hashlib
import hmac
import json
import re
from datetime import datetime, timedelta

import pytest
from botocore.exceptions import ClientError

from scripts import provision_workflow_login as prov

RFC7677_SALT = base64.b64decode("W22ZaJ0SNY7soEsUEjb6gQ==")
RFC7677_AUTH = (
    "n=user,r=rOprNGfwEbeRWgbNEkqO,r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0,"
    "s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096,c=biws,"
    "r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0"
)
CLUSTER = "arn:aws:rds:us-east-1:111122223333:cluster:c1"
SECRET_ARN = "arn:aws:secretsmanager:us-east-1:111122223333:secret:meridian/aurora/wf-AbC123"
POLICY_ARN = "arn:aws:iam::111122223333:policy/MeridianWorkflowAuroraAccess"


def test_the_verifier_matches_the_rfc_7677_server_signature():
    verifier = prov.scram_sha256_verifier("pencil", salt=RFC7677_SALT)
    server_key = base64.b64decode(verifier.rsplit(":", 1)[1])
    signature = hmac.new(server_key, RFC7677_AUTH.encode(), hashlib.sha256).digest()
    assert base64.b64encode(signature).decode() == "6rriTRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4="
    stored_key = base64.b64decode(verifier.split("$")[2].split(":")[0])
    client_signature = hmac.new(stored_key, RFC7677_AUTH.encode(), hashlib.sha256).digest()
    proof = base64.b64decode("dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ=")
    client_key = bytes(a ^ b for a, b in zip(proof, client_signature))
    assert hashlib.sha256(client_key).digest() == stored_key
    assert verifier.startswith("SCRAM-SHA-256$4096:W22ZaJ0SNY7soEsUEjb6gQ==$")


def test_each_verifier_gets_a_fresh_salt():
    assert prov.scram_sha256_verifier("x") != prov.scram_sha256_verifier("x")


def test_the_policy_names_one_cluster_and_one_secret():
    doc = prov.policy_document(CLUSTER,
                               "arn:aws:secretsmanager:us-east-1:111122223333:secret:s-AbC123")
    assert doc["Statement"] == [
        {"Sid": "WorkflowDataApi", "Effect": "Allow", "Action": list(prov.DATA_API_ACTIONS),
         "Resource": "arn:aws:rds:us-east-1:111122223333:cluster:c1"},
        {"Sid": "WorkflowLoginSecret", "Effect": "Allow",
         "Action": "secretsmanager:GetSecretValue",
         "Resource": "arn:aws:secretsmanager:us-east-1:111122223333:secret:s-AbC123"},
    ]


class Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def record(**kwargs):
            self.calls.append((name, kwargs))
            return self.answers.get(name, {})
        return record


@pytest.fixture
def aws():
    sm, iam, master = Recorder(), Recorder(), Recorder()
    sm.answers = {"create_secret": {"ARN": SECRET_ARN}, "describe_secret": {}}
    iam.answers = {"create_policy": {"Policy": {"Arn": POLICY_ARN}}}
    master.answers = {"execute_statement": {"records": []}}
    return sm, iam, master


def test_a_dry_run_changes_nothing(aws):
    sm, iam, master = aws
    prov.provision(sm=sm, iam=iam, master=master, cluster_arn=CLUSTER,
                   database="meridian", apply=False, password="p" * 43)
    assert [c for c in sm.calls + iam.calls + master.calls
            if not c[0].startswith(("describe", "get", "list"))] == []


def test_postgres_receives_a_verifier_and_never_the_password(aws, monkeypatch):
    sm, iam, master = aws
    monkeypatch.setattr(prov, "_verify_login", lambda *a, **k: "meridian_workflow")
    monkeypatch.setattr(prov, "_find_secret_arn", lambda sm: None)
    monkeypatch.setattr(prov, "_find_policy_arn", lambda iam, account: None)
    password = "correct-horse-battery-staple-0123456789AB"
    prov.provision(sm=sm, iam=iam, master=master, cluster_arn=CLUSTER,
                   database="meridian", apply=True, password=password,
                   master_secret_arn="arn:master")
    sql = [kw["sql"] for name, kw in master.calls if name == "execute_statement"]
    assert len(sql) == 1 and re.fullmatch(
        r"ALTER ROLE meridian_workflow LOGIN PASSWORD "
        r"'SCRAM-SHA-256\$4096:[A-Za-z0-9+/=]+\$[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+'",
        sql[0])
    assert password not in json.dumps(master.calls + iam.calls)
    created = next(kw for name, kw in sm.calls if name == "create_secret")
    secret = json.loads(created["SecretString"])
    assert secret == {"username": "meridian_workflow", "password": password}


def test_write_env_replaces_exactly_one_line(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("A=1\nAURORA_WORKFLOW_SECRET_ARN=old\nB=2\n")
    monkeypatch.setattr(prov, "ENV_FILE", env)
    prov._write_env("arn:new")
    assert env.read_text() == "A=1\nAURORA_WORKFLOW_SECRET_ARN=arn:new\nB=2\n"


def test_write_env_appends_when_the_line_is_absent(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("A=1")
    monkeypatch.setattr(prov, "ENV_FILE", env)
    prov._write_env("arn:new")
    assert env.read_text() == "A=1\nAURORA_WORKFLOW_SECRET_ARN=arn:new\n"


def test_a_malformed_verifier_never_reaches_the_database(aws):
    _, _, master = aws
    with pytest.raises(SystemExit):
        prov._set_login(master, CLUSTER, "arn:s", "meridian", "x'; DROP ROLE y; --")
    assert master.calls == []


def test_a_rerun_rotates_the_existing_secret_and_adds_a_policy_version(aws, monkeypatch):
    sm, iam, master = aws
    sm.answers["describe_secret"] = {"ARN": SECRET_ARN}
    iam.answers["get_policy"] = {"Policy": {"Arn": POLICY_ARN}}
    iam.answers["list_policy_versions"] = {"Versions": []}
    monkeypatch.setattr(prov, "_verify_login", lambda *a, **k: "meridian_workflow")
    prov.provision(sm=sm, iam=iam, master=master, cluster_arn=CLUSTER,
                   database="meridian", apply=True, password="p" * 43,
                   master_secret_arn="arn:master")
    names = [c[0] for c in sm.calls + iam.calls]
    assert "put_secret_value" in names and "create_secret" not in names
    assert "create_policy_version" in names and "create_policy" not in names


def _client_error(code):
    return ClientError({"Error": {"Code": code, "Message": "x"}}, "Op")


class Raiser:
    def __init__(self, code):
        self.code = code

    def describe_secret(self, **kw):
        raise _client_error(self.code)

    get_policy = describe_secret


def test_missing_secret_and_policy_are_treated_as_absent():
    assert prov._find_secret_arn(Raiser("ResourceNotFoundException")) is None
    assert prov._find_policy_arn(Raiser("NoSuchEntity"), "111122223333") is None


def test_other_lookup_errors_propagate():
    with pytest.raises(ClientError):
        prov._find_secret_arn(Raiser("AccessDeniedException"))


def test_five_versions_prune_the_oldest_non_default(aws):
    _, iam, _ = aws
    base = datetime(2026, 1, 1)
    iam.answers["list_policy_versions"] = {"Versions": [
        {"VersionId": f"v{i}", "IsDefaultVersion": i == 1, "CreateDate": base + timedelta(days=i)}
        for i in range(1, 6)]}
    prov._upsert_policy(iam, POLICY_ARN, {"Version": "x"}, "111122223333")
    deleted = [kw["VersionId"] for name, kw in iam.calls if name == "delete_policy_version"]
    assert deleted == ["v2"]
    assert iam.calls[-1][0] == "create_policy_version"


def test_a_policy_created_in_another_account_stops_the_run(aws):
    _, iam, _ = aws
    with pytest.raises(SystemExit):
        prov._upsert_policy(iam, None, {"Version": "x"}, "999999999999")


def test_apply_without_a_master_secret_fails_before_any_call(aws):
    sm, iam, master = aws
    with pytest.raises(ValueError):
        prov.provision(sm=sm, iam=iam, master=master, cluster_arn=CLUSTER,
                       database="meridian", apply=True)
    assert sm.calls + iam.calls + master.calls == []


def test_a_full_apply_prints_neither_the_password_nor_an_account_id(aws, monkeypatch, capsys):
    sm, iam, master = aws
    monkeypatch.setattr(prov, "_verify_login", lambda *a, **k: "meridian_workflow")
    password = "correct-horse-battery-staple-0123456789AB"
    prov.provision(sm=sm, iam=iam, master=master, cluster_arn=CLUSTER, database="meridian",
                   apply=True, password=password, master_secret_arn="arn:master")
    out = capsys.readouterr()
    assert password not in out.out + out.err
    assert not re.search(r"\d{12}", out.out + out.err)
    assert "verified: meridian_workflow" in out.out


def test_the_wrong_account_stops_main_before_any_write(monkeypatch, tmp_path):
    clients = {"sts": Recorder(), "secretsmanager": Recorder(), "iam": Recorder(),
               "rds-data": Recorder()}
    clients["sts"].answers = {"get_caller_identity": {"Account": "999999999999"}}
    monkeypatch.setattr(prov.boto3, "client", lambda name, **kw: clients[name])
    monkeypatch.setattr(prov, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setenv("AURORA_CLUSTER_ARN", CLUSTER)
    monkeypatch.setenv("AURORA_SECRET_ARN", "arn:master")
    monkeypatch.setattr("sys.argv", ["prov", "--apply", "--write-env"])
    with pytest.raises(SystemExit) as exc:
        prov.main()
    assert "999999999999" not in str(exc.value) and "3333" in str(exc.value)
    assert [c for n in ("secretsmanager", "iam", "rds-data") for c in clients[n].calls] == []
    assert not (tmp_path / ".env").exists()


class FailingClient:
    attempts = 0

    def __init__(self, **kw):
        pass

    async def execute_one(self, query):
        FailingClient.attempts += 1
        raise _client_error("BadRequestException")


def test_verify_retries_then_gives_an_actionable_error(monkeypatch):
    FailingClient.attempts = 0
    monkeypatch.setattr(prov, "RDSDataClient", FailingClient)
    delays = []
    with pytest.raises(SystemExit) as exc:
        prov._verify_login(CLUSTER, "arn:secret", "meridian", sleep=delays.append)
    assert FailingClient.attempts == 3 and delays == [2, 2]
    assert "BadRequestException" in str(exc.value)
    assert "re-run this script to repair" in str(exc.value)
