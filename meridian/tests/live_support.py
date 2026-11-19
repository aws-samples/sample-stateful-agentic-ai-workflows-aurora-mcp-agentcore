"""Shared preconditions for the database-marked tests that read Amazon Aurora back."""

import json
import os

import pytest

from backend.agentcore.identity import get_agentcore_identity
from backend.db.rds_data_client import RDSDataClient, get_rds_data_client

JORDAN = "trv_meridian_demo"
DECOY = "trv_demo_decoy"
MASTER_ENV = ("AURORA_CLUSTER_ARN", "AURORA_SECRET_ARN")


def require_env(*names: str) -> None:
    """Fail the test, naming every missing variable at once."""
    missing = [name for name in (*MASTER_ENV, *names) if not os.environ.get(name)]
    if missing:
        pytest.fail(
            f"live tests need {', '.join(missing)}; the login secrets come from "
            "scripts/provision_service_logins.py")


def live_region() -> str:
    """The Region every live client uses."""
    return os.getenv("AGENTCORE_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"


def laptop_subject() -> str:
    """The IAM role id this machine's workload is bound by."""
    return get_agentcore_identity().authorization_context().subject_id


async def require_bound_to_jordan_only() -> str:
    """Fail unless this workload has an active binding to Jordan and none to the decoy.

    Returns:
        The workload's subject id.
    """
    subject = laptop_subject()
    rows = await get_rds_data_client().execute(
        "SELECT traveler_id FROM traveler_identity_bindings WHERE identity_provider = 'aws_iam' "
        "AND subject_id = %s AND status = 'active' "
        "AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP)", (subject,))
    bound = sorted(row["traveler_id"] for row in rows)
    if JORDAN not in bound or DECOY in bound:
        pytest.fail(
            f"the workload (subject ending {subject[-4:]}) must be bound to {JORDAN} and not to "
            f"{DECOY}; it holds active bindings to {bound}")
    return subject


async def saved_program(traveler: str) -> dict:
    """The traveler's first saved loyalty program that has a name and a balance.

    Fails, rather than skips, when there is none: both travelers are seeded with programs.
    """
    rows = await get_rds_data_client().execute(
        "SELECT loyalty_programs FROM traveler_profiles WHERE traveler_id = %s", (traveler,))
    programs = (rows[0]["loyalty_programs"] if rows else None) or {}
    for value in programs.values():
        if isinstance(value, dict) and value.get("program") and (
                value.get("points_balance") is not None):
            return value
    pytest.fail(f"{traveler} has no saved loyalty program with a balance: the seed is missing")


async def access_audit_ids(subject: str, traveler: str, decision: str) -> set[str]:
    """Audit ids of this workload's rows for one traveler and decision."""
    rows = await get_rds_data_client().execute(
        "SELECT audit_id FROM traveler_access_audit WHERE subject_id = %s "
        "AND requested_traveler_id = %s AND decision = %s", (subject, traveler, decision))
    return {row["audit_id"] for row in rows}


BOOKING_ROW_TABLES = ("booking_action_audit", "hold_requests", "booking_lines", "bookings")
APP_ROLE = "meridian_app"
GATEWAY_ROLE = "meridian_gateway"


def gateway_login() -> RDSDataClient:
    """A Data API client on the meridian_gateway login, the only one that may write bookings."""
    require_env("AURORA_GATEWAY_SECRET_ARN")
    return RDSDataClient(secret_arn=os.environ["AURORA_GATEWAY_SECRET_ARN"])


async def as_gateway(client: RDSDataClient, transaction_id: str, sql: str, params=()) -> list:
    """Run ``sql`` with the Gateway role's grants inside an open scoped transaction.

    The holds Lambda does the same: it steps to the Gateway role for one statement and back to
    ``meridian_app``. A statement that raises leaves the transaction aborted, as there.

    Args:
        client: A client on the meridian_gateway login (:func:`gateway_login`).
        transaction_id: A transaction from ``client.scoped_session``.
        sql: The statement.
        params: Its parameters.

    Returns:
        The statement's rows.
    """
    await client.execute(f"SET LOCAL ROLE {GATEWAY_ROLE}", transaction_id=transaction_id)
    rows = await client.execute(sql, params, transaction_id=transaction_id)
    await client.execute(f"SET LOCAL ROLE {APP_ROLE}", transaction_id=transaction_id)
    return rows


async def delete_booking_rows(client, booking_id: str) -> None:
    """Delete one booking's rows, referencing tables first (migration 028 foreign keys)."""
    for table in BOOKING_ROW_TABLES:
        await client.execute(f"DELETE FROM {table} WHERE booking_id = %s", (booking_id,))


async def delete_journey_rows(client, journey_id: str) -> None:
    """Delete a journey's action audit and hold identity rows, before the journey itself."""
    for table in ("booking_action_audit", "hold_requests"):
        await client.execute(f"DELETE FROM {table} WHERE journey_id = %s", (journey_id,))


RUN_LOG_ENV = "MERIDIAN_LIVE_RUN_LOG"


def record_live_ids(test: str, **ids: str) -> None:
    """Append the ids a live test created to the file ``MERIDIAN_LIVE_RUN_LOG`` names, if set.

    The run that sets it can then reconcile exactly its own rows afterwards.
    """
    path = os.environ.get(RUN_LOG_ENV)
    if path:
        with open(path, "a", encoding="utf-8") as log:
            log.write(json.dumps({"test": test, **ids}) + "\n")


async def booking_is_active(client, booking_id: str) -> bool:
    """A hold that still reserves places: held and not past its expiry, by Aurora's clock."""
    rows = await client.execute(
        "SELECT (status = 'held' AND hold_expires_at > CURRENT_TIMESTAMP) AS active "
        "FROM bookings WHERE booking_id = %s",
        (booking_id,),
    )
    return bool(rows) and bool(rows[0]["active"])


RELEASE_CALLS_PER_HOLD = 2


def _cause(exc: BaseException) -> str:
    """A cause that is safe to print: the exception type, never its text, which can carry ids."""
    return type(exc).__name__


async def _active_or_unknown(client, booking_id: str) -> tuple[bool | None, str]:
    """Whether the hold is active, or None with a cause when Aurora could not be read."""
    try:
        return await booking_is_active(client, booking_id), ""
    except Exception as exc:  # noqa: BLE001 - an unknown state is reported, never assumed
        return None, f"read-back failed ({_cause(exc)})"


def _call_release(release, call_tool, args) -> str:
    """One governed release call: '' when the Gateway answered released, else a fixed cause."""
    try:
        outcome = release(call_tool, args)
    except Exception as exc:  # noqa: BLE001 - an ambiguous reply: the next read-back decides
        return f"release reply lost ({_cause(exc)})"
    return "" if outcome.released else f"release not confirmed ({outcome.error})"


async def _release_one(client, call_tool, release, args, booking_id: str) -> str:
    """Release one hold with at most two governed calls, reading it back before each call.

    A second call goes out only when a read-back succeeded and showed the hold still active,
    so a release whose outcome is unknown is never repeated blind.

    Returns '' once the hold is no longer active, else every cause seen, in order.
    """
    causes: list[str] = []
    for call in range(RELEASE_CALLS_PER_HOLD + 1):
        active, read_cause = await _active_or_unknown(client, booking_id)
        if active is False:
            return ""
        causes.append(read_cause)
        if call == RELEASE_CALLS_PER_HOLD or (call and active is None):
            break
        causes.append(_call_release(release, call_tool, args))
    return "; ".join(dict.fromkeys(cause for cause in causes if cause)) or "still held"


async def _release_each(client, traveler_id: str, holds: list[dict], call_tool) -> dict:
    """Attempt every hold, whatever happened to the ones before it."""
    from backend.agents.phase_05_workflow.governed_hold import (
        release_arguments,
        release_governed_hold,
    )

    unresolved: dict[str, str] = {}
    for hold in holds:
        try:
            args = release_arguments(traveler_id=traveler_id,
                                     hold_request_id=hold["hold_request_id"],
                                     journey_id=hold["journey_id"])
            cause = await _release_one(client, call_tool, release_governed_hold, args,
                                       hold["booking_id"])
        except Exception as exc:  # noqa: BLE001 - one bad hold never stops the others
            cause = f"cleanup error ({_cause(exc)})"
        if cause:
            unresolved[hold["booking_id"]] = cause
    return unresolved


async def release_active_holds(client, traveler_id: str, holds: list[dict],
                               user_key: str = "jordan") -> None:
    """Give back each test hold still active through the governed Gateway release tool.

    Nothing is deleted: the booking, its hold request, its journey and the action audit rows
    the audit export may already hold all stay. Every hold is attempted. Each gets at most two
    release calls, and the second goes out only after a read-back shows the hold still
    active, so an ambiguous reply is never repeated blind.

    Args:
        client: A master Data API client, for the read-backs.
        traveler_id: The traveler the holds belong to.
        holds: Dicts with ``booking_id``, ``hold_request_id`` and ``journey_id``.
        user_key: The seeded user whose token the Gateway call carries in ``jwt`` mode.

    Raises:
        AssertionError: After all holds were attempted, naming every booking still active
            and why, as an exception type or the Gateway's fixed error code, and a failure
            to close the caller token scope.
    """
    unresolved: dict[str, str] | None = None
    closing = ""
    try:
        from backend.agentcore.gateway import get_agentcore_gateway
        from scripts.agentcore_caller import caller_scope

        with caller_scope(user_key):
            unresolved = await _release_each(client, traveler_id, holds,
                                             get_agentcore_gateway().call_tool)
    except Exception as exc:  # noqa: BLE001 - reported below; the run's cleanup goes on
        if unresolved is None:
            unresolved = {hold["booking_id"]: f"no governed release possible ({_cause(exc)})"
                          for hold in holds}
        else:
            closing = f"closing the caller token scope failed ({_cause(exc)})"
    problems = [f"{booking} ({cause})" for booking, cause in (unresolved or {}).items()]
    if closing:
        problems.append(closing)
    if problems:
        raise AssertionError("governed release cleanup: " + "; ".join(problems))


THREAD_HOLDS_SQL = (
    "SELECT hr.booking_id, hr.hold_request_id, hr.journey_id FROM hold_requests hr "
    "JOIN journey_threads jt ON jt.journey_id = hr.journey_id WHERE jt.thread_id = %s")
BOOKING_HOLDS_SQL = (
    "SELECT booking_id, hold_request_id, journey_id FROM hold_requests WHERE booking_id = %s")
CLOSE_LEASE_SQL = (
    "UPDATE journey_executions SET status = 'abandoned', ended_at = CURRENT_TIMESTAMP "
    "WHERE execution_id = %s AND status = 'running'")


async def _lookup(client, sql: str, key: str, kind: str, problems: list[str]):
    """Rows for one thread or booking, or None (with a problem noted) when Aurora failed."""
    try:
        return [dict(row) for row in await client.execute(sql, (key,))]
    except Exception as exc:  # noqa: BLE001 - the other lookups still run
        problems.append(f"reading holds for {kind} {key} failed ({_cause(exc)})")
        return None


async def _run_holds(client, threads, bookings, problems: list[str]) -> list[dict]:
    """The holds a run owns: every hold on its threads and every booking it tracked.

    A tracked booking with no hold request cannot be released through the Gateway; it is
    noted as a problem when it is still active or cannot be read, and ignored otherwise.
    """
    found: dict[str, dict] = {}
    for ref in threads:
        for row in await _lookup(client, THREAD_HOLDS_SQL, ref, "thread", problems) or []:
            found.setdefault(row["booking_id"], row)
    for booking in bookings:
        rows = await _lookup(client, BOOKING_HOLDS_SQL, booking, "booking", problems)
        for row in rows or []:
            found.setdefault(row["booking_id"], row)
        if rows == [] and booking not in found:
            active, read_cause = await _active_or_unknown(client, booking)
            if active is not False:
                problems.append(f"{booking} has no hold request to release it by "
                                f"({read_cause or 'still held'})")
    return list(found.values())


async def _close_leases(client, executions, problems: list[str]) -> None:
    for execution_id in executions:
        try:
            await client.execute(CLOSE_LEASE_SQL, (execution_id,))
        except Exception as exc:  # noqa: BLE001 - every lease is still attempted
            problems.append(f"closing lease {execution_id} failed ({_cause(exc)})")


def _log_ids(test: str, holds: list[dict], executions, problems: list[str]) -> None:
    entries = [dict(hold) for hold in holds] + [{"execution_id": e} for e in executions]
    for entry in entries:
        try:
            record_live_ids(test, **entry)
        except Exception as exc:  # noqa: BLE001 - logging never blocks cleanup
            problems.append(f"recording ids {entry} failed ({_cause(exc)})")


async def cleanup_run(client, traveler_id: str, *, test: str, threads=(), bookings=(),
                      executions=()) -> list[str]:
    """Clean up one live test's holds without stopping at the first failure.

    In order: find every hold the run owns (on its journey threads and its tracked bookings),
    release the active ones through the governed path, close the run's worker leases, then
    record the ids. Each step runs whatever happened in the one before. Nothing is deleted.

    Returns:
        The problems, each with a sanitized cause; empty when everything was cleaned up.
    """
    problems: list[str] = []
    holds = await _run_holds(client, threads, bookings, problems)
    try:
        await release_active_holds(client, traveler_id, holds)
    except AssertionError as exc:
        problems.append(str(exc))
    await _close_leases(client, executions, problems)
    _log_ids(test, holds, executions, problems)
    return problems


def report_cleanup(problems: list[str], test_error: BaseException | None = None) -> None:
    """Surface cleanup problems without hiding the test's own failure.

    With a test failure in flight the problems become a note on it; otherwise they fail the test.
    """
    if not problems:
        return
    message = "cleanup: " + "; ".join(problems)
    if test_error is not None:
        test_error.add_note(message)
        return
    raise AssertionError(message)
