"""The workflow login writes its audit row, and the row reads back as the run described it."""

import json
import os
import uuid

import pytest

from backend.agents.phase_05_workflow.run_audit import AuroraRunAuditor, RunAuditRecord
from backend.audit_vocabulary import ToolCall
from backend.db.rds_data_client import RDSDataClient, get_rds_data_client
from backend.memory.store import MemoryStore

pytestmark = pytest.mark.database

TRAVELER = "trv_meridian_demo"


@pytest.fixture
def login() -> RDSDataClient:
    secret = os.environ.get("AURORA_WORKFLOW_SECRET_ARN")
    if not secret:
        pytest.fail(
            "AURORA_WORKFLOW_SECRET_ARN is not set: run scripts/provision_workflow_login.py")
    return RDSDataClient(secret_arn=secret)


async def test_the_workflow_login_writes_a_row_that_names_the_person(login):
    store = MemoryStore()
    store.db = login
    subject = f"sub-test-{uuid.uuid4().hex[:10]}"
    calls = (ToolCall("hybrid_trip_search", "allowed"),
             ToolCall("create_courtesy_hold", "refused", "policy"))
    record = RunAuditRecord(
        traveler_id=TRAVELER, subject=subject, tool_calls=calls, turn_error=None, rows_returned=3,
    )
    master = get_rds_data_client()
    try:
        await AuroraRunAuditor(store=store).record(record)
        rows = await master.execute(
            "SELECT operation, agent_name, traveler_id, rls_traveler, rls_agent_type, "
            "authorization_provider, authorization_subject, authorization_decision, "
            "rows_returned, tool_calls::text AS tool_calls, turn_error "
            "FROM agent_audit_log WHERE authorization_subject = %s", (subject,))
        [row] = rows
        assert json.loads(row.pop("tool_calls")) == [call.as_json() for call in calls]
        assert row == {
            "operation": "workflow_run", "agent_name": "WorkflowRunner", "traveler_id": TRAVELER,
            "rls_traveler": TRAVELER, "rls_agent_type": "booking_agent",
            "authorization_provider": "cognito", "authorization_subject": subject,
            "authorization_decision": "deny", "rows_returned": 3, "turn_error": None,
        }
    finally:
        await master.execute(
            "DELETE FROM agent_audit_log WHERE authorization_subject = %s", (subject,))


async def test_a_failed_run_reads_back_with_its_error_and_an_error_decision(login):
    store = MemoryStore()
    store.db = login
    subject = f"sub-test-{uuid.uuid4().hex[:10]}"
    record = RunAuditRecord(
        traveler_id=TRAVELER, subject=subject, tool_calls=(), rows_returned=0,
        turn_error="ExecutionLeaseLostError: The recovery worker lost its lease.",
    )
    master = get_rds_data_client()
    try:
        await AuroraRunAuditor(store=store).record(record)
        rows = await master.execute(
            "SELECT authorization_decision, turn_error FROM agent_audit_log "
            "WHERE authorization_subject = %s", (subject,))
        assert rows == [{
            "authorization_decision": "error", "turn_error": record.turn_error}]
    finally:
        await master.execute(
            "DELETE FROM agent_audit_log WHERE authorization_subject = %s", (subject,))
