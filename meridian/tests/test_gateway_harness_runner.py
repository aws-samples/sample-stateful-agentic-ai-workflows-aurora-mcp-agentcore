"""A harness run guards first, tears down in every outcome, and masks account ids and tokens."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import httpx
import pytest

from scripts.gateway_harness import guards, runner
from scripts.gateway_harness import resources as res
from scripts.gateway_harness.verdicts import DECOY, JORDAN
from tests.gateway_harness_fakes import (
    ACCOUNT,
    REGION,
    Fake,
    FakeControl,
    FakeIam,
    FakeLambda,
    Pager,
    client_error,
)

TEMPLATE = Path(__file__).resolve().parents[1] / (
    "meridian_agentcore/agentcore/agentcore.template.json")
ENV = {
    "AURORA_CLUSTER_ARN": f"arn:aws:rds:{REGION}:{ACCOUNT}:cluster:meridian",
    "MERIDIAN_COGNITO_REGION": REGION,
    "MERIDIAN_COGNITO_USER_POOL_ID": "us-east-1_AbCdEfGhI",
    "MERIDIAN_COGNITO_APP_CLIENT_ID": "client-web",
}
NAME = "meridian-throwaway-00000000"
RUN_ID = "00000000"
TOKENS = {"jordan": "tok-jordan-SECRET", "decoy": "tok-decoy-SECRET"}


def logged_event(method, tool=None):
    body = {"jsonrpc": "2.0", "id": 1, "method": method}
    if tool:
        body["params"] = {"name": tool, "arguments": {}}
    event = {"mcp": {"gatewayRequest": {"body": body}}}
    return {"message": "RECORDED_EVENT " + json.dumps(event)}


class World:
    """Fake AWS plus a fake Gateway endpoint that behaves as 'interceptor first, Cedar second'."""

    def __init__(self, account=ACCOUNT, existing=(), binding_status="ACTIVE",
                 session_region=REGION):
        self.control = FakeControl(binding_status)
        self.control.answers["get_paginator"] = lambda name: Pager(
            [{"items": [{"name": n} for n in existing]}])
        self.lambda_ = FakeLambda()
        self.logs = Fake({"get_paginator": lambda name: Pager([{"events": [
            logged_event("tools/call", "EchoTarget___echo"), logged_event("tools/list"),
            logged_event("initialize")]}])})
        self.iam = FakeIam()
        self.sts = Fake({"get_caller_identity": {"Account": account}})
        self.session_region = session_region
        self.factory_calls = []
        self.minted = []
        self.requests = []

    def session(self, region):
        clients = {"sts": self.sts, "iam": self.iam, "lambda": self.lambda_,
                   "bedrock-agentcore-control": self.control, "logs": self.logs}

        def client(_, name):
            self.factory_calls.append(name)
            return clients[name]

        return type("Session", (), {"region_name": self.session_region, "client": client})()

    def mint(self, who):
        self.minted.append(who)
        return TOKENS[who]

    def mode(self):
        updates = self.lambda_.args("update_function_configuration")
        return updates[-1]["Environment"]["Variables"]["HARNESS_MODE"] if updates else "pin"

    def handle(self, request):
        self.requests.append(request)
        auth = request.headers["authorization"]
        who = {f"Bearer {TOKENS['jordan']}": JORDAN, f"Bearer {TOKENS['decoy']}": DECOY}[auth]
        body = json.loads(request.content)
        if body["method"] == "tools/list":
            tools = [{"name": "EchoTarget___echo"}]
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"tools": tools}})
        if body["method"] != "tools/call":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {}})
        return httpx.Response(200, json=self.tool_reply(body["params"]["arguments"], who))

    def tool_reply(self, arguments, who):
        mode = self.mode()
        if mode == "pin" and "travelerId" not in arguments:
            error = {"code": -32602, "message": "travelerId is a required property"}
            return {"jsonrpc": "2.0", "id": 1, "error": error}
        if mode == "refuse":
            text = "Identity Check Failed: the access token carries no single traveler."
            return {"jsonrpc": "2.0", "id": 1, "result": {
                "isError": True, "content": [{"type": "text", "text": text}]}}
        if mode == "off" and arguments.get("travelerId") != who:
            text = "Tool Execution Denied: Tool call not allowed due to policy enforcement"
            return {"jsonrpc": "2.0", "id": 1, "result": {
                "isError": True, "content": [{"type": "text", "text": text}]}}
        event = {**arguments, "travelerId": who}
        if mode == "bad_type":
            event["travelerId"] = 12345
        if mode == "drop_required":
            del event["travelerId"]
        text = json.dumps({"event": event, "custom": {"bedrockAgentCoreToolName": "t"}})
        return {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": text}]}}

    def deps(self):
        return runner.Dependencies(
            session=self.session, mint=self.mint,
            transport=httpx.MockTransport(self.handle), sleep=lambda s: None,
            token_hex=lambda n: "00000000")


def no_calls(world):
    return all(not fake.calls for fake in (world.iam, world.lambda_, world.control, world.logs))


def write_ledger(path, entries, run_id=RUN_ID):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"run_id": run_id, "entries": entries}))


def tag(world, run_id=RUN_ID):
    world.iam.tags[f"{NAME}-lambda"] = res.tag_list(run_id)
    world.lambda_.tags[f"{NAME}-echo"] = {res.RUN_TAG: run_id}


# ------------------------------------------------------------------ dry run


def test_the_dry_run_prints_the_plan_and_the_tag_permissions_and_makes_no_aws_call(capsys):
    assert runner.dry_run(ENV, TEMPLATE, token_hex=lambda n: "0a0b0c0d") == 0
    out = capsys.readouterr().out
    assert "DRY RUN" in out and "meridian-throwaway-0a0b0c0d" in out
    assert out.count("\n  ") >= 9
    assert ACCOUNT not in out and "<acct>" in out
    for action in ("lambda:TagResource", "lambda:ListTags", "iam:TagRole", "iam:ListRoleTags",
                   "bedrock-agentcore:TagResource", "bedrock-agentcore:ListTagsForResource"):
        assert action in out
    assert runner.CONFIRM_FLAG in out and "--apply" in out


@pytest.mark.parametrize("drop", ["AURORA_CLUSTER_ARN", "MERIDIAN_COGNITO_APP_CLIENT_ID"])
def test_a_dry_run_with_missing_settings_is_refused(drop, capsys):
    env = {k: v for k, v in ENV.items() if k != drop}
    assert runner.dry_run(env, TEMPLATE) == runner.EXIT_REFUSED
    assert "REFUSED" in capsys.readouterr().out


# ----------------------------------------------------------------- live run


def test_a_live_run_prints_the_table_saves_events_and_deletes_everything(tmp_path, capsys):
    world = World()
    code = runner.run_live(ENV, TEMPLATE, tmp_path, world.deps())
    out = capsys.readouterr().out
    assert code == runner.EXIT_PASS, out
    assert "RESULT: PASS" in out and f"Deleted every resource named {NAME}" in out
    assert "C4" in out and "An accepted probe does not show that additionalProperties" in out
    assert ACCOUNT not in out
    assert world.control.names().count("delete_gateway") == 1
    assert world.lambda_.names().count("delete_function") == 2
    assert world.iam.names().count("delete_role") == 2
    saved = json.loads((tmp_path / NAME / "ledger.json").read_text())
    assert saved["run_id"] == RUN_ID and len(saved["entries"]) == 9
    assert [kind for kind, _ in saved["entries"]][:2] == ["iam-role", "iam-role"]
    assert len(list((tmp_path / NAME / "recorded").glob("event_*.json"))) == 3
    rows = json.loads((tmp_path / NAME / "verdicts.json").read_text())
    assert {row["key"]: row["status"] for row in rows}["Q4"] == "PASS"
    assert {row["key"]: row["finding"] for row in rows}["Q5"].startswith("Yes. Cedar denied")
    assert {row["key"]: row["status"] for row in rows}["C5"] == "PASS"
    assert len(world.control.args("update_gateway")) == 2
    assert len(world.requests) == 9
    assert world.minted == ["jordan", "decoy"]


def test_no_token_reaches_the_output_or_any_saved_file(tmp_path, capsys):
    runner.run_live(ENV, TEMPLATE, tmp_path, World().deps())
    text = capsys.readouterr().out + "".join(
        path.read_text() for path in tmp_path.rglob("*") if path.is_file())
    assert "SECRET" not in text


def test_the_ledger_file_exists_with_the_run_id_before_the_first_create(tmp_path):
    world = World()
    ledger = tmp_path / NAME / "ledger.json"
    seen = []

    def create_role(**kwargs):
        seen.append(json.loads(ledger.read_text()) if ledger.exists() else None)
        return {"Role": {"Arn": "arn:role"}}

    world.iam.answers["create_role"] = create_role
    runner.run_live(ENV, TEMPLATE, tmp_path, world.deps())
    assert seen and seen[0] is not None, "no ledger file on disk at the first create call"
    assert seen[0]["run_id"] == RUN_ID and seen[0]["entries"][0] == ["iam-role", f"{NAME}-lambda"]


def test_the_first_ledger_write_has_the_run_id_and_no_entries_before_any_create(
        tmp_path, monkeypatch):
    writes = []
    real_writer = runner._ledger_writer

    def recording_writer(path):
        save = real_writer(path)
        return lambda payload: (writes.append(json.loads(json.dumps(payload))), save(payload))[1]

    monkeypatch.setattr(runner, "_ledger_writer", recording_writer)
    runner.run_live(ENV, TEMPLATE, tmp_path, World().deps())
    assert writes[0] == {"run_id": RUN_ID, "entries": []}


def test_the_ledger_verdicts_and_events_are_owner_only_files_in_owner_only_folders(tmp_path):
    previous = os.umask(0)
    try:
        base = tmp_path / "out"
        assert runner.run_live(ENV, TEMPLATE, base, World().deps()) == runner.EXIT_PASS
    finally:
        os.umask(previous)
    folder = base / NAME
    files = [folder / "ledger.json", folder / "verdicts.json",
             *sorted((folder / "recorded").glob("event_*.json"))]
    assert len(files) == 5
    for path in files:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600, path
    for path in (base, folder, folder / "recorded"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o700, path
    assert not list(folder.rglob("*.tmp"))


def test_failed_checks_exit_one_and_the_table_says_so(tmp_path, capsys):
    world = World()
    deps = world.deps()
    deps.transport = httpx.MockTransport(lambda request: httpx.Response(401, json={}))
    assert runner.run_live(ENV, TEMPLATE, tmp_path, deps) == runner.EXIT_FAIL
    out = capsys.readouterr().out
    assert "RESULT: FAIL" in out and world.control.names().count("delete_gateway") == 1


# ------------------------------------------------------------------- guards


def test_the_wrong_account_is_refused_with_sts_the_only_client_built(tmp_path, capsys):
    world = World(account="999999999999")
    assert runner.run_live(ENV, TEMPLATE, tmp_path, world.deps()) == runner.EXIT_REFUSED
    out = capsys.readouterr().out
    assert "<acct>" in out and "999999999999" not in out
    assert world.factory_calls == ["sts"] and no_calls(world)
    assert world.minted == [] and not (tmp_path / NAME).exists()


def test_the_wrong_region_is_refused_with_sts_the_only_client_built(tmp_path):
    world = World(session_region="eu-west-1")
    assert runner.run_live(ENV, TEMPLATE, tmp_path, world.deps()) == runner.EXIT_REFUSED
    assert world.factory_calls == ["sts"] and no_calls(world) and world.minted == []


def test_an_existing_gateway_with_the_same_name_is_refused(tmp_path):
    world = World(existing=[NAME])
    assert runner.run_live(ENV, TEMPLATE, tmp_path, world.deps()) == runner.EXIT_REFUSED
    assert world.iam.calls == [] and world.minted == [] and not (tmp_path / NAME).exists()


def test_the_real_gateway_name_can_never_be_produced(tmp_path):
    world = World()
    deps = world.deps()
    deps.token_hex = lambda n: "meridianv2"
    assert runner.run_live(ENV, TEMPLATE, tmp_path, deps) == runner.EXIT_REFUSED
    assert world.factory_calls == [] and no_calls(world)


def test_a_token_that_cannot_be_minted_refuses_before_anything_is_created(tmp_path, capsys):
    world = World()
    deps = world.deps()

    def failing(who):
        raise RuntimeError(f"cannot read secret in {ACCOUNT}; re-run seed_cognito_users.py")

    deps.mint = failing
    assert runner.run_live(ENV, TEMPLATE, tmp_path, deps) == runner.EXIT_REFUSED
    out = capsys.readouterr().out
    assert "seed_cognito_users.py" in out and ACCOUNT not in out
    assert world.iam.calls == [] and not (tmp_path / NAME).exists()


# ------------------------------------------------------------ failure paths


def test_a_rejected_deny_rule_fails_the_run_sends_no_probe_and_still_tears_down(tmp_path, capsys):
    world = World(binding_status="CREATE_FAILED")
    code = runner.run_live(ENV, TEMPLATE, tmp_path, world.deps())
    out = capsys.readouterr().out
    assert code == runner.EXIT_FAIL
    assert "Policy rejected the template's deny rule" in out and "RESULT: FAIL" in out
    assert world.requests == []
    assert world.control.names().count("delete_gateway") == 1


def test_a_failure_while_creating_still_deletes_what_exists(tmp_path, capsys):
    world = World()
    world.control.answers["create_gateway_target"] = lambda **kw: (_ for _ in ()).throw(
        res.HarnessFailure("target ended in FAILED: bad schema"))
    code = runner.run_live(ENV, TEMPLATE, tmp_path, world.deps())
    assert code == runner.EXIT_FAIL
    assert "FAILED: target ended in FAILED" in capsys.readouterr().out
    assert world.control.names().count("delete_gateway") == 1
    assert world.lambda_.names().count("delete_function") == 2


def test_raw_aws_error_text_is_scrubbed_before_it_is_printed(tmp_path, capsys):
    world = World()
    message = (f"User arn:aws:sts::{ACCOUNT}:assumed-role/x is not authorized; "
               "Bearer abc.def.ghi and token 111122223333")
    world.iam.answers["create_role"] = lambda **kw: (_ for _ in ()).throw(
        client_error("AccessDenied", message))
    code = runner.run_live(ENV, TEMPLATE, tmp_path, world.deps())
    out = capsys.readouterr().out
    assert code == runner.EXIT_FAIL and "FAILED:" in out and "AccessDenied" in out
    assert ACCOUNT not in out and "111122223333" not in out and "abc.def.ghi" not in out
    assert "<acct>" in out


def test_a_template_without_the_two_production_actions_is_refused_before_any_create(
        tmp_path, capsys):
    world = World()
    drifted = tmp_path / "template.json"
    drifted.write_text(TEMPLATE.read_text().replace("MeridianHolds___confirm_booking", "Other"))
    assert runner.run_live(ENV, drifted, tmp_path, world.deps()) == runner.EXIT_REFUSED
    assert world.iam.calls == [] and not (tmp_path / NAME / "ledger.json").exists()
    assert "REFUSED" in capsys.readouterr().out


def test_an_unreadable_template_is_refused_by_name(tmp_path, capsys):
    world = World()
    missing = tmp_path / "missing.json"
    assert runner.run_live(ENV, missing, tmp_path, world.deps()) == runner.EXIT_REFUSED
    assert "missing.json" in capsys.readouterr().out and world.factory_calls == []


def test_credentials_that_cannot_call_sts_are_refused_with_the_text_masked(tmp_path, capsys):
    world = World()
    world.sts.answers["get_caller_identity"] = lambda **kw: (_ for _ in ()).throw(
        client_error("ExpiredToken", f"token for {ACCOUNT} expired"))
    assert runner.run_live(ENV, TEMPLATE, tmp_path, world.deps()) == runner.EXIT_REFUSED
    out = capsys.readouterr().out
    assert "cannot identify the AWS caller" in out and ACCOUNT not in out
    assert world.factory_calls == ["sts"]


def test_an_unexpected_error_propagates_after_teardown(tmp_path):
    world = World()
    world.control.answers["create_gateway_target"] = lambda **kw: (_ for _ in ()).throw(
        RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        runner.run_live(ENV, TEMPLATE, tmp_path, world.deps())
    assert world.control.names().count("delete_gateway") == 1


@pytest.mark.parametrize("error", [RuntimeError("probe bug"), KeyboardInterrupt()])
def test_teardown_runs_even_when_the_probes_raise(tmp_path, monkeypatch, error):
    world = World()

    def explode(*args, **kwargs):
        raise error

    monkeypatch.setattr(runner.probes, "run_probes", explode)
    with pytest.raises(type(error)):
        runner.run_live(ENV, TEMPLATE, tmp_path, world.deps())
    assert world.control.names().count("delete_gateway") == 1
    assert world.lambda_.names().count("delete_function") == 2
    assert world.iam.names().count("delete_role") == 2


def test_a_network_failure_in_the_probes_fails_the_run_and_tears_down(tmp_path, capsys):
    world = World()
    deps = world.deps()

    def unreachable(request):
        raise httpx.ConnectError("no route to host")

    deps.transport = httpx.MockTransport(unreachable)
    assert runner.run_live(ENV, TEMPLATE, tmp_path, deps) == runner.EXIT_FAIL
    assert world.control.names().count("delete_gateway") == 1


def test_leftovers_are_reported_with_a_distinct_exit_code(tmp_path, capsys):
    world = World()
    world.lambda_.answers["delete_function"] = lambda **kw: (_ for _ in ()).throw(
        client_error("AccessDeniedException"))
    code = runner.run_live(ENV, TEMPLATE, tmp_path, world.deps())
    assert code == runner.EXIT_LEFTOVERS
    assert "TEARDOWN INCOMPLETE" in capsys.readouterr().out


# ------------------------------------------------------ teardown from ledger


def test_a_saved_ledger_can_be_torn_down_later_after_the_tags_are_checked(tmp_path, capsys):
    world = World()
    tag(world)
    ledger = tmp_path / NAME / "ledger.json"
    write_ledger(ledger, [["iam-role", f"{NAME}-lambda"], ["lambda", f"{NAME}-echo"]])
    assert runner.teardown_from_ledger(ENV, TEMPLATE, ledger, world.deps()) == 0
    assert world.lambda_.names().count("delete_function") == 1
    assert world.iam.names().count("delete_role") == 1
    assert world.iam.names().index("list_role_tags") < world.iam.names().index("delete_role")


def test_a_ledger_entry_with_another_runs_tag_is_not_deleted(tmp_path, capsys):
    world = World()
    tag(world, run_id="someone-else")
    ledger = tmp_path / NAME / "ledger.json"
    write_ledger(ledger, [["iam-role", f"{NAME}-lambda"], ["lambda", f"{NAME}-echo"]])
    assert runner.teardown_from_ledger(
        ENV, TEMPLATE, ledger, world.deps()) == runner.EXIT_LEFTOVERS
    assert "delete_role" not in world.iam.names()
    assert "delete_function" not in world.lambda_.names()
    assert "not tagged for this run" in capsys.readouterr().out


def test_a_ledger_entry_that_is_not_this_throwaways_exact_name_is_not_deleted(tmp_path):
    world = World()
    ledger = tmp_path / NAME / "ledger.json"
    write_ledger(ledger, [["iam-role", "meridian-aurora-gateway-role"]])
    assert runner.teardown_from_ledger(
        ENV, TEMPLATE, ledger, world.deps()) == runner.EXIT_LEFTOVERS
    assert "delete_role" not in world.iam.names()


@pytest.mark.parametrize("content", [None, "", "not json", "[]", "{}", '{"run_id": "x"}',
                                     '{"run_id": "x", "entries": []}',
                                     '{"run_id": "", "entries": [["lambda", "a"]]}',
                                     '{"run_id": "x", "entries": [["lambda"]]}',
                                     '[["iam-role", "old-format"]]'])
def test_a_missing_unreadable_or_empty_ledger_refuses_with_no_aws_call(tmp_path, capsys,
                                                                       content):
    world = World()
    ledger = tmp_path / NAME / "ledger.json"
    ledger.parent.mkdir()
    if content is not None:
        ledger.write_text(content)
    assert runner.teardown_from_ledger(ENV, TEMPLATE, ledger, world.deps()) == runner.EXIT_REFUSED
    out = capsys.readouterr().out
    assert "REFUSED" in out and "ledger" in out and "nothing was deleted" in out
    assert world.factory_calls == [] and no_calls(world)


def test_an_unreadable_ledger_file_refuses_with_no_aws_call(tmp_path, capsys):
    world = World()
    ledger = tmp_path / NAME / "ledger.json"
    ledger.parent.mkdir()
    ledger.mkdir()
    assert runner.teardown_from_ledger(ENV, TEMPLATE, ledger, world.deps()) == runner.EXIT_REFUSED
    assert world.factory_calls == [] and "nothing was deleted" in capsys.readouterr().out


def test_teardown_from_a_ledger_in_a_folder_that_is_not_a_throwaway_is_refused(tmp_path, capsys):
    world = World()
    ledger = tmp_path / "meridian-aurora" / "ledger.json"
    write_ledger(ledger, [["lambda", "meridian-aurora-echo"]])
    assert runner.teardown_from_ledger(ENV, TEMPLATE, ledger, world.deps()) == runner.EXIT_REFUSED
    assert guards.REAL_GATEWAY_NAME in capsys.readouterr().out
    assert world.factory_calls == []


def test_teardown_with_the_wrong_account_builds_only_the_sts_client(tmp_path):
    world = World(account="999999999999")
    tag(world)
    ledger = tmp_path / NAME / "ledger.json"
    write_ledger(ledger, [["lambda", f"{NAME}-echo"]])
    assert runner.teardown_from_ledger(ENV, TEMPLATE, ledger, world.deps()) == runner.EXIT_REFUSED
    assert world.factory_calls == ["sts"] and no_calls(world)


# ------------------------------------------------- unexpected teardown errors


def test_one_resource_failing_with_an_unexpected_error_does_not_stop_the_others(
        tmp_path, capsys):
    world = World()
    calls = []

    def delete_function(**kwargs):
        calls.append(kwargs["FunctionName"])
        if kwargs["FunctionName"].endswith("-echo"):
            raise KeyError("Configuration")
        return {}

    world.lambda_.answers["delete_function"] = delete_function
    code = runner.run_live(ENV, TEMPLATE, tmp_path, world.deps())
    out = capsys.readouterr().out
    assert code == runner.EXIT_LEFTOVERS
    assert len(calls) == 2
    assert world.control.names().count("delete_gateway") == 1
    assert world.iam.names().count("delete_role") == 2
    assert "TEARDOWN INCOMPLETE" in out and f"lambda {NAME}-echo: KeyError" in out


def test_a_teardown_that_raises_outright_prints_the_ledger_and_the_run_tag(
        tmp_path, capsys, monkeypatch):
    def broken(self, *, from_file=False):
        raise AttributeError("'NoneType' object has no attribute 'delete'")

    monkeypatch.setattr(res.ThrowawayGateway, "teardown", broken)
    code = runner.run_live(ENV, TEMPLATE, tmp_path, World().deps())
    out = capsys.readouterr().out
    assert code == runner.EXIT_LEFTOVERS
    assert "TEARDOWN INCOMPLETE" in out and "AttributeError" in out
    assert f"{res.RUN_TAG}={RUN_ID}" in out
    assert f"iam-role {NAME}-lambda" in out and f"lambda {NAME}-echo" in out
    assert "--teardown" in out and "Traceback" not in out


def test_a_ledger_teardown_that_raises_outright_still_reports_leftovers(
        tmp_path, capsys, monkeypatch):
    def broken(self, *, from_file=False):
        raise TypeError("bad entry")

    monkeypatch.setattr(res.ThrowawayGateway, "teardown", broken)
    ledger = tmp_path / NAME / "ledger.json"
    write_ledger(ledger, [["lambda", f"{NAME}-echo"]])
    code = runner.teardown_from_ledger(ENV, TEMPLATE, ledger, World().deps())
    out = capsys.readouterr().out
    assert code == runner.EXIT_LEFTOVERS
    assert f"lambda {NAME}-echo" in out and f"{res.RUN_TAG}={RUN_ID}" in out


def test_a_transport_url_error_fails_the_run_and_tears_down(tmp_path, capsys):
    world = World()
    deps = world.deps()

    def bad_url(request):
        raise httpx.InvalidURL("Invalid non-printable ASCII character in URL")

    deps.transport = httpx.MockTransport(bad_url)
    assert runner.run_live(ENV, TEMPLATE, tmp_path, deps) == runner.EXIT_FAIL
    assert world.control.names().count("delete_gateway") == 1
    assert "Traceback" not in capsys.readouterr().out
