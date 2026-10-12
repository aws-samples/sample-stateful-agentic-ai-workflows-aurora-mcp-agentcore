"""`release_identity.py holds-logs`: the old holds function's retained log group goes first.

Stage 1 of the Gateway replacement deletes the holds Lambda (fixed name ``meridianv2-
MeridianHolds``) but the CDK's explicitly named log group for it is retained. Stage 2 creates the
function again together with a log group of that very name, which CloudFormation refuses. The
command deletes that one group, by its exact name, once the function is gone.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from scripts import release_identity
from scripts.identity_release import holds_logs, settings
from tests import holds_logs_support as hs
from tests import release_support as rs
from tests.aws_recorders import client_error
from tests.test_release_identity_cli import env

FLAG = settings.CONFIRM_FLAG


class Cloud:
    def __init__(self, *groups, function=False, account=rs.ACCOUNT, **keywords):
        self.sts = hs.account_check(account)
        self.logs = hs.FakeLogs(*groups, **keywords)
        self.lam = hs.FakeFunctions(function)
        self.built = []

    def session(self, region):
        clients = {"sts": self.sts, "logs": self.logs, "lambda": self.lam}

        def client(name, **kwargs):
            self.built.append(name)
            return clients[name]
        return Mock(client=client)


def run(argv, cloud):
    deps = release_identity.Dependencies(
        env=env(), session=cloud.session, sleep=lambda seconds: None)
    return release_identity.main(["holds-logs", *argv], deps)


def test_the_names_come_from_the_settings_and_are_exact():
    assert holds_logs.function_name() == hs.FUNCTION
    assert holds_logs.log_group_name() == hs.GROUP


def test_the_dry_run_names_the_group_and_deletes_nothing(capsys):
    cloud = Cloud(hs.GROUP, *hs.NEIGHBOURS)

    assert run([], cloud) == 0

    out = capsys.readouterr().out
    assert "DRY RUN" in out and hs.GROUP in out and cloud.logs.deleted() == []
    assert f"holds-logs --apply {FLAG}" in out and rs.ACCOUNT not in out


def test_apply_without_the_confirmation_exits_three_before_any_client(capsys):
    cloud = Cloud(hs.GROUP)

    assert run(["--apply"], cloud) == 3

    assert cloud.built == [] and FLAG in capsys.readouterr().out


def test_apply_deletes_exactly_the_one_group_and_reads_back_that_it_is_gone(capsys):
    cloud = Cloud(*hs.NEIGHBOURS, hs.GROUP)

    assert run(["--apply", FLAG], cloud) == 0

    assert cloud.logs.deleted() == [hs.GROUP]
    assert cloud.logs.groups == list(hs.NEIGHBOURS)
    assert cloud.logs.names()[-1] == "describe_log_groups"
    assert cloud.logs.names().index("delete_log_group") < len(cloud.logs.names()) - 1
    assert "gone" in capsys.readouterr().out


def test_the_group_is_found_on_a_later_page_of_a_crowded_prefix():
    cloud = Cloud(f"{hs.GROUP}-a", f"{hs.GROUP}-b", f"{hs.GROUP}-c", hs.GROUP)

    assert run(["--apply", FLAG], cloud) == 0

    assert cloud.logs.deleted() == [hs.GROUP]


def test_a_group_that_is_already_gone_is_a_clean_no_op(capsys):
    cloud = Cloud(*hs.NEIGHBOURS)

    assert run(["--apply", FLAG], cloud) == 0

    assert cloud.logs.deleted() == [] and "already absent" in capsys.readouterr().out


def test_a_function_that_still_exists_stops_the_delete(capsys):
    cloud = Cloud(hs.GROUP, function=True)

    assert run(["--apply", FLAG], cloud) == 2

    assert cloud.logs.deleted() == []
    assert "still exists" in capsys.readouterr().err


def test_the_dry_run_with_the_function_still_there_says_blocked(capsys):
    cloud = Cloud(hs.GROUP, function=True)

    assert run([], cloud) == 2

    assert "BLOCKED" in capsys.readouterr().out and cloud.logs.deleted() == []


def test_the_function_is_checked_before_the_group_is_touched():
    cloud = Cloud(hs.GROUP)

    run(["--apply", FLAG], cloud)

    assert cloud.lam.names()[0] == "get_function_configuration"
    assert cloud.lam.calls[0][1] == {"FunctionName": hs.FUNCTION}


def test_a_group_that_stays_after_the_delete_is_drift(capsys):
    cloud = Cloud(hs.GROUP, stubborn=True)

    assert run(["--apply", FLAG], cloud) == 1

    assert "DRIFT" in capsys.readouterr().out


def test_credentials_for_another_account_stop_before_any_read(capsys):
    cloud = Cloud(hs.GROUP, account="999999999999")

    assert run(["--apply", FLAG], cloud) == 2

    assert cloud.built == ["sts"] and cloud.logs.calls == []
    assert "999999999999" not in capsys.readouterr().err


def test_an_aws_refusal_is_one_line_with_the_permission_hint(capsys):
    cloud = Cloud(hs.GROUP, failures={"delete_log_group": client_error("AccessDeniedException")})

    assert run(["--apply", FLAG], cloud) == 2

    assert "logs:DeleteLogGroup" in capsys.readouterr().err


def test_a_name_that_is_not_exactly_the_expected_one_is_never_deleted():
    logs = hs.FakeLogs(hs.GROUP.lower(), f"{hs.GROUP}/")

    with pytest.raises(holds_logs.HoldsLogsError, match="exact"):
        holds_logs.delete_exact(logs, hs.GROUP.lower())

    assert logs.deleted() == []


def test_a_listing_that_returns_a_look_alike_is_not_the_group(capsys):
    cloud = Cloud(hs.GROUP.lower(), f"{hs.GROUP}-x")

    assert run(["--apply", FLAG], cloud) == 0

    assert cloud.logs.deleted() == []


def test_the_gate_finding_is_empty_when_the_group_is_gone_and_names_the_command_when_not():
    assert holds_logs.gate_findings(hs.FakeLogs(*hs.NEIGHBOURS), hs.FakeFunctions()) == []

    found = holds_logs.gate_findings(hs.FakeLogs(hs.GROUP), hs.FakeFunctions())

    assert len(found) == 1 and hs.GROUP in found[0]
    assert f"holds-logs --apply {FLAG}" in found[0]


def test_the_gate_finding_for_a_live_function_says_to_read_the_stack_instead():
    found = holds_logs.gate_findings(hs.FakeLogs(hs.GROUP), hs.FakeFunctions(exists=True))

    assert len(found) == 1 and "holds-logs --apply" not in found[0]
    assert "still exists" in found[0]
