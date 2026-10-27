"""Audit rows as Aurora stores them, written and read back in one transaction that is rolled back.

agent_audit_log is sealed: no role may update or delete a row, so a committed test row would stay
in the evidence for good. Each test writes its row inside a traveler-scoped session and reads it
back with the same transaction, where the agent_audit_traveler_select policy applies. The session
is then rolled back, whatever happened. Nothing is committed and nothing is deleted.

Run only after migration 021 is applied (Task 21).
"""

import json
import uuid

import pytest

from backend.agentcore.identity import get_agentcore_identity
from backend.audit_vocabulary import ToolCall
from backend.db.rds_data_client import get_rds_data_client
from backend.memory.store import get_memory_store
from tests.rollback_support import uncommitted_scope

pytestmark = pytest.mark.database

JORDAN = "trv_meridian_demo"
SUB = "11111111-2222-3333-4444-555555555555"
READ_SQL = (
    "SELECT authorization_provider, authorization_subject, authorization_decision, "
    "tool_calls IS NULL AS no_calls, tool_calls::text AS tool_calls "
    "FROM agent_audit_log WHERE audit_id = %s"
)


async def write_and_read(**extra) -> dict:
    """Write one audit row and read it back, both inside a transaction that is rolled back."""
    client = get_rds_data_client()
    async with uncommitted_scope(
        client, traveler_id=JORDAN, agent_type="concierge_agent",
        authorization=get_agentcore_identity().authorization_context(),
    ) as tx:
        audit_id = await get_memory_store().write_audit(
            agent_name="ProductionAgent", operation=f"itest_{uuid.uuid4().hex[:6]}",
            traveler_id=JORDAN, rls_traveler=JORDAN, rls_agent_type="concierge_agent",
            iam_identity="arn:aws:sts::<acct>:assumed-role/test/session",
            authorization_provider="cognito", authorization_subject=SUB,
            authorization_decision=extra.pop("authorization_decision", "allow"),
            rows_returned=1, transaction_id=tx, **extra,
        )
        rows = await client.execute(READ_SQL, (audit_id,), transaction_id=tx)
    assert len(rows) == 1, "the audit row was not visible inside its own transaction"
    row = rows[0]
    row["tool_calls"] = None if row["no_calls"] else json.loads(row["tool_calls"])
    return row


async def test_every_call_is_stored_in_order_with_the_person_and_the_decision():
    calls = [ToolCall("create_courtesy_hold", "refused", "policy"),
             ToolCall("get_package_details", "allowed")]
    row = await write_and_read(authorization_decision="deny", tool_calls=calls)

    assert row["authorization_subject"] == SUB and row["authorization_provider"] == "cognito"
    assert row["authorization_decision"] == "deny"
    assert row["tool_calls"] == [call.as_json() for call in calls]


async def test_a_turn_with_no_calls_stores_an_empty_array_not_null():
    row = await write_and_read(tool_calls=[])
    assert row["no_calls"] is False and row["tool_calls"] == []


async def test_unknown_calls_stay_null():
    row = await write_and_read(authorization_decision="error")
    assert row["no_calls"] is True
