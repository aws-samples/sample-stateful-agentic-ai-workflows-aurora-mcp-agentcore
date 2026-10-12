"""publish_gateway_parameters.py is a guarded SSM write: dry run, confirm flag, read-back."""

from __future__ import annotations

import pytest

from scripts import publish_gateway_parameters as publisher
from scripts.identity_release import settings
from tests import release_support as rs
from tests.aws_recorders import Recorder, client_error, violations
from tests.lambda_release_support import GATEWAY, MASTER, World

CLUSTER = f"arn:aws:rds:{rs.REGION}:{rs.ACCOUNT}:cluster:meridian"
ENV = {"AURORA_CLUSTER_ARN": CLUSTER, "AURORA_SECRET_ARN": MASTER,
       "AURORA_GATEWAY_SECRET_ARN": GATEWAY, "AURORA_DATABASE": "meridian",
       "AGENTCORE_GATEWAY_URL": f"https://{rs.GATEWAY_ID}.gateway.bedrock-agentcore."
                                f"{rs.REGION}.amazonaws.com/mcp",
       "AGENTCORE_RUNTIME_ARN": f"arn:aws:bedrock-agentcore:{rs.REGION}:{rs.ACCOUNT}:runtime/"
                                + rs.RUNTIME_IDS["MeridianConcierge"],
       "AGENTCORE_WORKFLOW_RUNTIME_ARN": f"arn:aws:bedrock-agentcore:{rs.REGION}:{rs.ACCOUNT}:"
                                         "runtime/" + rs.RUNTIME_IDS["MeridianWorkflow"]}
APPLY = ["--apply", settings.CONFIRM_FLAG]
SECRET = "/meridian/aurora/secret_arn"


class Ssm(Recorder):
    """Stores what is put and returns it; ``drop`` makes a parameter read back differently."""

    def __init__(self, drop=None):
        self.store = {}
        self.drop = drop or {}
        super().__init__(answers={"put_parameter": self.put, "get_parameter": self.get})

    def put(self, Name, Value, **kwargs):
        self.store[Name] = Value
        return {"Version": 1}

    def get(self, Name):
        if Name not in self.store:
            raise client_error("ParameterNotFound", "none", "GetParameter")
        return {"Parameter": {"Value": self.drop.get(Name, self.store[Name])}}


class Session:
    def __init__(self, ssm, account=rs.ACCOUNT, world=None):
        self.ssm, self.regions = ssm, []
        self.sts = Recorder({"get_caller_identity": {"Account": account}})
        self.world = world or World()
        self.created = []

    def client(self, name, **kwargs):
        self.created.append(name)
        return {"sts": self.sts, "ssm": self.ssm, "iam": self.world.iam,
                "lambda": self.world.lam,
                "bedrock-agentcore-control": self.world.control}[name]


def run(argv, ssm=None, *, env=None, account=rs.ACCOUNT, world=None):
    session = Session(ssm or Ssm(), account, world)
    code = publisher.main(argv, env=env or ENV, session=lambda region: session)
    return code, session


def test_the_default_is_a_dry_run_that_makes_no_client_and_no_write(capsys):
    code, session = run([])

    assert code == 0 and session.created == []
    out = capsys.readouterr().out
    for name in ("/meridian/aurora/cluster_arn", SECRET, "/meridian/aurora/database"):
        assert name in out
    assert "DRY RUN" in out and settings.CONFIRM_FLAG in out
    assert rs.ACCOUNT not in out and MASTER not in out


def test_the_dry_run_with_the_gateway_login_says_which_secret_it_names(capsys):
    run(["--gateway-login"])

    assert "gateway login" in capsys.readouterr().out


def test_apply_without_the_confirmation_is_refused_before_any_client(capsys):
    code, session = run(["--apply"])

    assert code == 3 and session.created == []
    assert settings.CONFIRM_FLAG in capsys.readouterr().out


def test_a_wrong_account_is_refused_before_the_ssm_client_is_made(capsys):
    code, session = run(APPLY, account="999999999999")

    assert code == 2 and session.created == ["sts"] and session.ssm.calls == []
    assert "999999999999" not in capsys.readouterr().err


def test_a_confirmed_apply_writes_the_three_parameters_and_reads_them_back(capsys):
    ssm = Ssm()

    code, session = run(APPLY, ssm)

    assert code == 0 and session.created == ["sts", "ssm"]
    assert ssm.store == {"/meridian/aurora/cluster_arn": CLUSTER, SECRET: MASTER,
                         "/meridian/aurora/database": "meridian"}
    assert ssm.names().count("get_parameter") >= 3
    assert violations("ssm", ssm.calls) == []
    assert "OK" in capsys.readouterr().out


def test_the_gateway_login_dry_run_says_the_apply_waits_for_the_holds_role_grant(capsys):
    code, session = run(["--gateway-login"])

    assert code == 0 and session.created == []
    out = capsys.readouterr().out
    assert "refuses until the MeridianHolds role can read the gateway secret" in out
    assert "first jwt `agentcore deploy -y`" in out


def test_the_gateway_login_is_refused_while_the_holds_role_cannot_read_its_secret(capsys):
    ssm = Ssm()
    world = World(holds_grants=(MASTER,))

    code, session = run([*APPLY, "--gateway-login"], ssm, world=world)

    assert code == 2
    assert ssm.store == {} and "put_parameter" not in ssm.names()
    err = capsys.readouterr().err
    assert "Lambda MeridianHolds: its role cannot read the meridian_gateway secret" in err
    assert "agentcore deploy" in err and "after" in err
    assert "ssm" not in session.created


def test_the_master_login_apply_is_not_gated_on_the_gateway_grant():
    ssm = Ssm()

    code, session = run(APPLY, ssm, world=World(holds_grants=(MASTER,)))

    assert code == 0 and session.created == ["sts", "ssm"]


def test_a_holds_function_that_cannot_be_found_also_blocks_the_gateway_login(capsys):
    world = World()
    world.control.answers["list_gateway_targets"] = {"items": []}

    code, _ = run([*APPLY, "--gateway-login"], Ssm(), world=world)

    assert code == 2
    assert "MeridianHolds target" in capsys.readouterr().err


def test_the_gateway_login_flag_writes_the_gateway_secret():
    ssm = Ssm()

    assert run([*APPLY, "--gateway-login"], ssm)[0] == 0

    assert ssm.store[SECRET] == GATEWAY


def test_a_value_that_reads_back_differently_exits_one(capsys):
    ssm = Ssm(drop={SECRET: MASTER + " "})

    code, _ = run([*APPLY, "--gateway-login"], ssm)

    assert code == 1
    assert "DRIFT" in capsys.readouterr().out


def test_an_aws_failure_is_masked_and_exits_two(capsys):
    ssm = Ssm()
    ssm.failures["put_parameter"] = [client_error(
        "AccessDeniedException", f"User arn:aws:iam::{rs.ACCOUNT}:user/x is not allowed")]

    code, _ = run(APPLY, ssm)

    err = capsys.readouterr().err
    assert code == 2 and rs.ACCOUNT not in err and "Traceback" not in err
    assert "AccessDeniedException" in err


def test_a_missing_setting_is_reported_without_a_value(capsys):
    env = {k: v for k, v in ENV.items() if k != "AURORA_DATABASE"}

    code, session = run(APPLY, env=env)

    assert code == 2 and session.created == []
    assert "AURORA_DATABASE" in capsys.readouterr().err


def test_a_cluster_arn_that_is_not_a_cluster_is_refused():
    code, session = run(APPLY, env={**ENV, "AURORA_CLUSTER_ARN": "nonsense"})

    assert code == 2 and session.created == []


@pytest.mark.parametrize("argv", [["--gateway"], ["--app"], ["--bogus"]])
def test_abbreviated_and_unknown_flags_exit_three(argv, capsys):
    with pytest.raises(SystemExit) as stopped:
        run(argv)

    assert stopped.value.code == 3
