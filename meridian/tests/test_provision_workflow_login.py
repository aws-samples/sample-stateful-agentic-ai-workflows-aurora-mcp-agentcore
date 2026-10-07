"""The workflow login is provisioned without its password reaching Postgres or the logs."""

import base64
import hashlib
import hmac
import json
import re

import pytest

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
                   database="meridian", apply=True, password=password)
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
                   database="meridian", apply=True, password="p" * 43)
    names = [c[0] for c in sm.calls + iam.calls]
    assert "put_secret_value" in names and "create_secret" not in names
    assert "create_policy_version" in names and "create_policy" not in names
