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


class FakeRds:
    """A Data API that enforces the travelers policy: rows show only under a pinned traveler."""

    def __init__(self, travelers=("trv_meridian_demo", "trv_demo_decoy"), other_bindings=()):
        self.travelers, self.other_bindings, self.calls = travelers, other_bindings, []
        self.pins, self.open_transactions, self.finished = {}, set(), []

    def begin_transaction(self, **kwargs):
        self.calls.append(("begin_transaction", kwargs))
        txn = f"txn-{len(self.pins) + len(self.open_transactions) + 1}"
        self.open_transactions.add(txn)
        return {"transactionId": txn}

    def commit_transaction(self, **kwargs):
        assert set(kwargs) == {"resourceArn", "secretArn", "transactionId"}, kwargs
        self.calls.append(("commit_transaction", kwargs))
        self.open_transactions.discard(kwargs["transactionId"])
        self.finished.append("commit")
        return {}

    def rollback_transaction(self, **kwargs):
        assert set(kwargs) == {"resourceArn", "secretArn", "transactionId"}, kwargs
        self.calls.append(("rollback_transaction", kwargs))
        self.open_transactions.discard(kwargs["transactionId"])
        self.finished.append("rollback")
        return {}

    def execute_statement(self, **kwargs):
        self.calls.append(("execute_statement", kwargs))
        sql, txn = kwargs["sql"], kwargs.get("transactionId")
        values = {p["name"]: p["value"]["stringValue"] for p in kwargs.get("parameters", [])}
        if "set_config('app.current_traveler_id'" in sql:
            self.pins[txn] = values["traveler_id"]
            return {"records": [[{"stringValue": values["traveler_id"]}]]}
        if sql.startswith("SELECT 1 FROM travelers"):
            visible = txn in self.open_transactions and self.pins.get(txn) == values["traveler_id"]
            found = ["1"] if visible and values["traveler_id"] in self.travelers else []
        elif sql.startswith("SELECT traveler_id FROM traveler_identity_bindings"):
            found = list(self.other_bindings)
        else:
            return {}
        return {"records": [[{"stringValue": t}] for t in found]}

    def inserts(self):
        return [kw for name, kw in self.calls if name == "execute_statement"
                and kw["sql"].startswith("INSERT")]

    def statements(self):
        return [(kw["sql"], kw.get("transactionId")) for name, kw in self.calls
                if name == "execute_statement"]


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


def run(user, *, idp, apply=True, password="Generated-Password-1234-Aa1", keychain=None,
        rds=None):
    sm = Recorder({"describe_secret": _error("ResourceNotFoundException")})
    rds = rds or FakeRds()
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
    writes = [c for r in (idp, sm) for c in r.calls
              if not c[0].startswith(("describe", "get", "admin_get"))]
    assert writes == [] and rds.inserts() == []
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
    call, = rds.inserts()
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
        assert len(password) == 23
        assert re.search(r"[a-z]", password) and re.search(r"[A-Z]", password)
        assert re.search(r"\d", password) and re.search(r"[-_]", password)


def test_the_keychain_receives_the_password_on_stdin_never_in_arguments(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    seen = []

    def fake_run(argv, **kwargs):
        seen.append((argv, kwargs))
        return SimpleNamespace(returncode=0)

    seed.store_keychain(seed.USERS["jordan"], "Secret-Pw_1234-Aa1", run=fake_run)
    (add_argv, add), (find_argv, find) = seen
    assert add_argv == ["security", "-i"]
    assert "Secret-Pw_1234-Aa1" not in " ".join(add_argv)
    assert "-w 'Secret-Pw_1234-Aa1'" in add["input"]
    assert "-s meridian-cognito" in add["input"] and "-U" in add["input"]
    assert find_argv == ["security", "find-generic-password", "-s", "meridian-cognito",
                         "-a", "jordan.morgan@example.com"]
    assert "Secret-Pw_1234-Aa1" not in " ".join(find_argv)


def test_an_item_missing_after_a_zero_exit_add_stops_the_run_without_the_password(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")

    def interactive_lies(argv, **kwargs):
        return SimpleNamespace(returncode=0 if argv == ["security", "-i"] else 44)

    with pytest.raises(SystemExit) as exit_info:
        seed.store_keychain(seed.USERS["jordan"], "Secret-Pw_1234-Aa1", run=interactive_lies)
    message = str(exit_info.value)
    assert "Secret-Pw" not in message
    assert message == (
        "Keychain item for jordan missing after add; Cognito and Secrets Manager already hold "
        "the new password, fix the Keychain and re-run "
        "scripts/seed_cognito_users.py --user jordan --apply")


def test_a_keychain_failure_stops_the_run_without_echoing_the_password(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    failing = lambda argv, **kw: SimpleNamespace(returncode=45)  # noqa: E731
    with pytest.raises(SystemExit) as exit_info:
        seed.store_keychain(seed.USERS["jordan"], "Secret-Pw_1234-Aa1", run=failing)
    message = str(exit_info.value)
    assert "Secret-Pw" not in message and "exit 45" in message
    assert ("Cognito's password for jordan is currently stored nowhere safe; "
            "re-run with --apply to reset and store it again") in message


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


def _main_env(monkeypatch, tmp_path, idp, rds=None):
    clients = {"sts": Recorder({"get_caller_identity": {"Account": "111122223333"}}),
               "cognito-idp": idp, "secretsmanager": Recorder(), "rds-data": rds or FakeRds()}
    monkeypatch.setattr(seed, "store_keychain", lambda user, password: None)
    clients["secretsmanager"].answers = {"describe_secret": _error("ResourceNotFoundException")}
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


def test_a_missing_traveler_stops_before_cognito_secrets_or_keychain(monkeypatch, tmp_path):
    idp, rds = cognito(), FakeRds(travelers=("trv_demo_decoy",))
    _main_env(monkeypatch, tmp_path, idp, rds)
    with pytest.raises(SystemExit) as exit_info:
        seed.main(["--user", "jordan", "--apply"])
    message = str(exit_info.value)
    assert "trv_meridian_demo" in message and "scripts/seed_data.py" in message
    assert [n for n, _ in idp.calls if n.startswith("admin_") and n != "admin_get_user"] == []
    assert rds.inserts() == []


def test_the_decoy_names_its_own_seeding_script(monkeypatch, tmp_path):
    _main_env(monkeypatch, tmp_path, cognito(), FakeRds(travelers=()))
    with pytest.raises(SystemExit, match="apply_rls_force_and_decoy.py"):
        seed.main(["--user", "decoy", "--apply"])


def test_all_users_are_checked_before_the_first_is_changed(monkeypatch, tmp_path):
    idp = cognito()
    _main_env(monkeypatch, tmp_path, idp, FakeRds(travelers=("trv_meridian_demo",)))
    with pytest.raises(SystemExit, match="trv_demo_decoy"):
        seed.main(["--apply"])
    assert "admin_create_user" not in [n for n, _ in idp.calls]


def test_an_extra_active_binding_for_the_same_sub_stops_the_run_before_the_reset():
    idp = cognito(existing=True, sub="sub-shared")
    rds = FakeRds(other_bindings=("trv_demo_decoy",))
    with pytest.raises(SystemExit) as exit_info:
        seed.preflight(seed.USERS["jordan"], idp=idp, rds=rds, pool_id=POOL,
                       cluster_arn=CLUSTER, master_secret_arn="arn:master", database="meridian")
    message = str(exit_info.value)
    assert "trv_demo_decoy" in message and "revoke" in message.lower()
    assert "admin_set_user_password" not in [n for n, _ in idp.calls]


def test_a_new_user_has_no_sub_to_check_for_extra_bindings():
    idp, rds = cognito(), FakeRds(other_bindings=("trv_demo_decoy",))
    seed.preflight(seed.USERS["jordan"], idp=idp, rds=rds, pool_id=POOL,
                   cluster_arn=CLUSTER, master_secret_arn="arn:master", database="meridian")


def test_a_failure_after_cognito_changed_carries_the_repair_hint_without_the_password(
        monkeypatch, tmp_path):
    failure = ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}},
                          "CreateSecret")
    idp = cognito(existing=True)
    _main_env(monkeypatch, tmp_path, idp)
    secrets_client = Recorder({"describe_secret": _error("ResourceNotFoundException"),
                               "create_secret": failure})
    real = seed.boto3.client
    monkeypatch.setattr(seed.boto3, "client",
                        lambda name, **kw: secrets_client if name == "secretsmanager"
                        else real(name, **kw))
    monkeypatch.setattr(seed, "generate_password", lambda: "Generated-Password-1234-Aa1")
    with pytest.raises(SystemExit) as exit_info:
        seed.main(["--user", "jordan", "--apply"])
    message = str(exit_info.value)
    assert "CreateSecret" in message and "Generated-Password" not in message
    assert ("Cognito's password for jordan is currently stored nowhere safe; "
            "re-run with --apply to reset and store it again") in message


def test_a_failure_before_cognito_changed_has_no_repair_hint(monkeypatch, tmp_path):
    failure = ClientError({"Error": {"Code": "AccessDenied", "Message": "no"}}, "AdminGetUser")
    _main_env(monkeypatch, tmp_path, Recorder({"admin_get_user": failure}))
    with pytest.raises(SystemExit) as exit_info:
        seed.main(["--user", "jordan", "--apply"])
    assert "stored nowhere safe" not in str(exit_info.value)


def test_the_traveler_check_pins_the_traveler_in_one_transaction_before_the_select():
    rds = FakeRds()
    seed.require_traveler(rds, {"resourceArn": CLUSTER, "secretArn": "s", "database": "d"},
                          seed.USERS["jordan"])
    names = [n for n, _ in rds.calls]
    assert names == ["begin_transaction", "execute_statement", "execute_statement",
                     "commit_transaction"]
    (pin_sql, pin_txn), (read_sql, read_txn) = rds.statements()
    assert "set_config('app.current_traveler_id', :traveler_id, true)" in pin_sql
    assert read_sql.startswith("SELECT 1 FROM travelers WHERE traveler_id = :traveler_id")
    assert pin_txn == read_txn is not None
    assert rds.calls[-1][1]["transactionId"] == pin_txn
    assert "trv_meridian_demo" not in pin_sql + read_sql


def test_a_missing_traveler_is_reported_and_the_transaction_is_closed():
    rds = FakeRds(travelers=())
    with pytest.raises(SystemExit, match="trv_meridian_demo"):
        seed.require_traveler(rds, {"resourceArn": CLUSTER, "secretArn": "s", "database": "d"},
                              seed.USERS["jordan"])
    assert rds.open_transactions == set() and len(rds.finished) == 1


def test_a_failed_read_rolls_the_transaction_back():
    class Failing(FakeRds):
        def execute_statement(self, **kwargs):
            if kwargs["sql"].startswith("SELECT 1 FROM travelers"):
                raise _error("StatementTimeoutException")
            return super().execute_statement(**kwargs)

    rds = Failing()
    with pytest.raises(ClientError):
        seed.require_traveler(rds, {"resourceArn": CLUSTER, "secretArn": "s", "database": "d"},
                              seed.USERS["jordan"])
    assert rds.finished == ["rollback"] and rds.open_transactions == set()
