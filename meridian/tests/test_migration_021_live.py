"""Migration 021 as applied: tool_calls, turn_error, their checks and the view on the live cluster.

Run only after the migration is applied (Task 21). Every insert runs as meridian_app with Jordan
pinned, inside a transaction that is rolled back: the row passes the row-level security insert
check, so a rejection comes from the CHECK constraints, and nothing is committed.
"""

import json
import uuid

import pytest

from backend.agentcore.identity import get_agentcore_identity
from backend.db.rds_data_client import get_rds_data_client
from tests.rollback_support import uncommitted_scope

pytestmark = pytest.mark.database

JORDAN = "trv_meridian_demo"
INSERT_SQL = (
    "INSERT INTO agent_audit_log (audit_id, traveler_id, agent_name, operation, "
    "authorization_decision, tool_calls, turn_error) "
    "VALUES (%s, %s, 'test', 'itest_021', %s, %s::jsonb, %s)"
)
READ_SQL = (
    "SELECT tool_calls::text AS tool_calls, turn_error FROM agent_audit_log WHERE audit_id = %s"
)
ANY_CHECK = (r"agent_audit_log_tool_calls_"
             r"(access_audit_id|array|decision|object|refusal_code|refused_by|result|tool)")


def call(result="allowed", refused_by=None, **extra):
    return {"tool": "create_courtesy_hold", "result": result, "refused_by": refused_by,
            "refusal_code": None, "access_audit_id": None, **extra}


def without(member, element):
    return {key: value for key, value in element.items() if key != member}


GRANT_REFUSAL = call("refused", "workload_grant", refusal_code="traveler_not_authorized")


async def insert_row(tool_calls, decision="allow", turn_error=None):
    """Insert one row as meridian_app, read it back, and roll both back."""
    client = get_rds_data_client()
    audit_id = f"aud_t{uuid.uuid4().hex[:10]}"
    calls = None if tool_calls is None else json.dumps(tool_calls)
    async with uncommitted_scope(
        client, traveler_id=JORDAN, agent_type="concierge_agent",
        authorization=get_agentcore_identity().authorization_context(),
    ) as tx:
        await client.execute(INSERT_SQL, (audit_id, JORDAN, decision, calls, turn_error),
                             transaction_id=tx)
        rows = await client.execute(READ_SQL, (audit_id,), transaction_id=tx)
    stored = rows[0]["tool_calls"]
    return {"tool_calls": None if stored is None else json.loads(stored),
            "turn_error": rows[0]["turn_error"]}


async def insert(tool_calls, decision="allow"):
    """Insert one row without a turn_error; return its tool_calls as stored."""
    return (await insert_row(tool_calls, decision))["tool_calls"]


async def test_the_columns_exist_and_the_earlier_draft_columns_do_not():
    rows = await get_rds_data_client().execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'agent_audit_log' "
        "AND column_name IN ('tool_calls', 'turn_error', 'tools_called', 'outcome', "
        "'refused_by') ORDER BY column_name"
    )
    assert rows == [{"column_name": "tool_calls", "data_type": "jsonb"},
                    {"column_name": "turn_error", "data_type": "text"}]


async def test_calls_in_the_vocabulary_are_stored_in_order():
    calls = [
        call("refused", "business_rule", refusal_code="insufficient_inventory"),
        call("refused", "runtime_guard", refusal_code="write_already_attempted"),
        call("refused", "workload_grant", refusal_code="traveler_not_authorized",
             access_audit_id="authz_test"),
        call("error", refusal_code="execution_lease_lost"),
        {"tool": "get_package_details", "result": "allowed"},
        {"tool": "create_courtesy_hold", "result": "error",
         "refusal_code": "invalid_package_inventory"},
    ]
    assert await insert(calls, "deny") == calls
    assert await insert([]) == []


@pytest.mark.parametrize(("tool_calls", "decision"), [
    ([call("refused", "business_rule", refusal_code="insufficient_inventory"),
      call("refused", "runtime_guard", refusal_code="write_already_attempted")], "allow"),
    ([call("error"), call()], "error"),
    ([call("error"), call("refused", "database_guard", refusal_code="hold_price_mismatch")],
     "deny"),
    (None, "error"),
    (None, None),
])
async def test_a_decision_that_follows_from_the_calls_is_stored(tool_calls, decision):
    assert await insert(tool_calls, decision) == tool_calls


def rejected(tool_calls, constraint, decision="allow"):
    """A row the CHECK named by ``constraint`` refuses first.

    CHECKs run in name order and the decision CHECK sorts before most element CHECKs, so
    ``decision`` is what the calls give, except in the rows that test the decision CHECK itself.
    """
    return pytest.param(tool_calls, decision, constraint)


@pytest.mark.parametrize(("tool_calls", "decision", "constraint"), [
    rejected({"tool": "x", "result": "allowed"}, "agent_audit_log_tool_calls_array"),
    rejected([call("maybe")], "agent_audit_log_tool_calls_result"),
    rejected([call(5)], "agent_audit_log_tool_calls_result"),
    rejected([{"tool": "x"}], ANY_CHECK),
    rejected(["create_courtesy_hold"], ANY_CHECK),
    rejected([[]], "agent_audit_log_tool_calls_object"),
    rejected([[call()]], "agent_audit_log_tool_calls_object"),
    rejected([call("refused")], "agent_audit_log_tool_calls_refused_by"),
    rejected([call("refused", "cedar")], "agent_audit_log_tool_calls_refused_by"),
    rejected([call("refused", "")], "agent_audit_log_tool_calls_refused_by"),
    rejected([call("allowed", "policy")], "agent_audit_log_tool_calls_refused_by", "deny"),
    rejected([call("error", "")], "agent_audit_log_tool_calls_refused_by", "error"),
    rejected([call(), call("refused")], "agent_audit_log_tool_calls_refused_by"),
    rejected([without("tool", call())], "agent_audit_log_tool_calls_tool"),
    rejected([call(tool="")], "agent_audit_log_tool_calls_tool"),
    rejected([call(tool=5)], "agent_audit_log_tool_calls_tool"),
    rejected([call(tool=["create_courtesy_hold"])], "agent_audit_log_tool_calls_tool"),
    rejected([call("error", refusal_code=7)], "agent_audit_log_tool_calls_refusal_code", "error"),
    rejected([call("error", refusal_code={"code": "hold_expired"})],
             "agent_audit_log_tool_calls_refusal_code", "error"),
    rejected([call("error", refusal_code="")], "agent_audit_log_tool_calls_refusal_code", "error"),
    rejected([call(access_audit_id=7)], "agent_audit_log_tool_calls_access_audit_id"),
    rejected([call(access_audit_id="authz_test")], "agent_audit_log_tool_calls_access_audit_id"),
    rejected([call("refused", "policy", access_audit_id="authz_test")],
             "agent_audit_log_tool_calls_access_audit_id", "deny"),
    rejected([GRANT_REFUSAL], "agent_audit_log_tool_calls_access_audit_id", "deny"),
    rejected([without("access_audit_id", GRANT_REFUSAL)],
             "agent_audit_log_tool_calls_access_audit_id", "deny"),
    rejected([{**GRANT_REFUSAL, "access_audit_id": 7}],
             "agent_audit_log_tool_calls_access_audit_id", "deny"),
    rejected([{**GRANT_REFUSAL, "access_audit_id": ""}],
             "agent_audit_log_tool_calls_access_audit_id", "deny"),
    rejected([call("refused", "policy")], "agent_audit_log_tool_calls_decision"),
    rejected([call("error")], "agent_audit_log_tool_calls_decision"),
    rejected([call("refused", "business_rule", refusal_code="insufficient_inventory")],
             "agent_audit_log_tool_calls_decision", "deny"),
    rejected([], "agent_audit_log_tool_calls_decision", "deny"),
    rejected([call()], "agent_audit_log_tool_calls_decision", None),
])
async def test_a_row_outside_the_rules_is_refused_by_the_database(tool_calls, decision,
                                                                   constraint):
    with pytest.raises(Exception, match=constraint):
        await insert(tool_calls, decision)


FAILED = "ClientError: An error occurred (AccessDeniedException)"


@pytest.mark.parametrize(("tool_calls", "decision", "turn_error"), [
    ([call()], "error", "ModelThrottledException: Too many requests"),
    ([], "error", FAILED),
    ([call("refused", "policy")], "deny", "RuntimeTurnError: the model call was throttled"),
    (None, "error", "CancelledError"),
    (None, "deny", FAILED),
    (None, "error", "x" * 400),
    ([call()], "allow", None),
])
async def test_a_turn_error_that_follows_the_rules_is_stored(tool_calls, decision, turn_error):
    stored = await insert_row(tool_calls, decision, turn_error)
    assert stored == {"tool_calls": tool_calls, "turn_error": turn_error}


@pytest.mark.parametrize(("tool_calls", "decision", "turn_error", "constraint"), [
    # CHECKs run in name order, so a rule broken on tool_calls is named before turn_error's.
    ([call()], "allow", FAILED, "agent_audit_log_tool_calls_decision"),
    ([], "allow", FAILED, "agent_audit_log_tool_calls_decision"),
    ([call("refused", "policy")], "error", FAILED, "agent_audit_log_tool_calls_decision"),
    (None, "allow", FAILED, "agent_audit_log_turn_error_decision"),
    (None, "error", "", "agent_audit_log_turn_error_text"),
    (None, "error", "x" * 401, "agent_audit_log_turn_error_text"),
])
async def test_a_turn_error_outside_the_rules_is_refused_by_the_database(
        tool_calls, decision, turn_error, constraint):
    with pytest.raises(Exception, match=constraint):
        await insert_row(tool_calls, decision, turn_error)


async def test_the_audit_view_exposes_the_columns():
    rows = await get_rds_data_client().execute(
        "SELECT tool_calls, turn_error FROM agent_iam_audit LIMIT 0")
    assert rows == []
