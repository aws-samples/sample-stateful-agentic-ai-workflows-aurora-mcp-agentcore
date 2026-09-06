"""Assemble the evidence document for one journey.

This is what Presenter proof renders. Its value is entirely in traceability:
every section names the source it came from, the ids it describes, and when it
was observed. Evidence that does not exist is reported as unavailable with a
reason, never omitted and never inferred, because a surface that quietly drops
a missing fact is indistinguishable from one that has the fact.

The read has no workflow side effects.
"""

from typing import Any, Dict, Optional

from backend.agentcore.identity import get_agentcore_identity

JOURNEY_SQL = """
SELECT journey_id, traveler_id, checkpoint_backend, active_thread_id, status,
       created_at, updated_at
  FROM journeys WHERE journey_id = %s
"""

EXECUTIONS_SQL = """
SELECT execution_id, attempt, worker_id, status, started_at, ended_at,
       lease_expires_at
  FROM journey_executions WHERE journey_id = %s
 ORDER BY attempt
"""

CHECKPOINT_SQL = """
SELECT checkpoint_id, parent_checkpoint_id, checkpoint_ns,
       checkpoint ->> 'ts' AS committed_at
  FROM checkpoints WHERE thread_id = %s
 ORDER BY checkpoint_id DESC LIMIT 1
"""

HOLD_SQL = """
SELECT hr.hold_request_id, hr.booking_id, hr.execution_id, hr.created_at,
       b.status, b.hold_expires_at, bl.package_id, bl.duration,
       bl.travelers_count
  FROM hold_requests hr
  JOIN bookings b ON b.booking_id = hr.booking_id
  LEFT JOIN booking_lines bl ON bl.booking_id = hr.booking_id
 WHERE hr.journey_id = %s
 ORDER BY hr.created_at DESC
"""

MESSAGES_SQL = """
SELECT message_id, role, content, created_at
  FROM conversation_messages WHERE conversation_id = %s
 ORDER BY created_at
"""

AUDIT_SQL = """
SELECT audit_id, identity_provider, subject_id, principal, decision, reason,
       decided_at
  FROM traveler_access_audit WHERE requested_traveler_id = %s
 ORDER BY decided_at DESC LIMIT 1
"""


def _unavailable(reason: str) -> Dict[str, Any]:
    """Evidence that does not exist, said out loud."""
    return {"status": "unavailable", "reason": reason}


def _iso(value: Any) -> Optional[str]:
    return None if value is None else str(value)


def _channel(values: Any, name: str) -> Any:
    """Read one channel out of a checkpoint's loaded values."""
    return values.get(name) if isinstance(values, dict) else None


async def _channel_values(client: Any, thread_id: str) -> Dict[str, Any]:
    """Load the checkpoint's channel values through the saver.

    `aput` stores channel values in `checkpoint_blobs`, not inline in the
    `checkpoints` JSONB, so reading the row alone yields nothing. Going through
    the saver reuses the windowed read that the rest of the system relies on
    rather than reimplementing blob reassembly here.
    """
    from backend.db.aurora_dataapi_saver import AuroraDataApiSaver

    tup = await AuroraDataApiSaver(client).aget_tuple(
        {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
    )
    if tup is None:
        return {}
    return dict(tup.checkpoint.get("channel_values") or {})


async def assemble_journey_document(
    client: Any, journey_id: str, traveler_id: str
) -> Dict[str, Any]:
    """Build the journey evidence document.

    Args:
        client: Data API client.
        journey_id: The journey to describe.
        traveler_id: The caller's authenticated traveler.

    Returns:
        The document described in the journey shell spec, section 4.

    Raises:
        LookupError: No such journey.
        PermissionError: The journey belongs to another traveler. Knowing a
            journey id is not authorization to read it.
    """
    async with client.scoped_session(
        traveler_id=traveler_id,
        agent_type="booking_agent",
        authorization=get_agentcore_identity().authorization_context(),
    ) as tx:

        async def q(sql: str, params: tuple) -> list:
            return await client.execute(sql, params, transaction_id=tx)

        rows = await q(JOURNEY_SQL, (journey_id,))
        if not rows:
            # RLS hides another traveler's journey, so absence here is either
            # "no such journey" or "not yours". Distinguishing them would leak
            # the existence of someone else's journey.
            raise LookupError(f"No journey {journey_id} for this traveler.")

        journey = rows[0]
        if journey["traveler_id"] != traveler_id:
            raise PermissionError(f"Journey {journey_id} belongs to another traveler.")

        thread_id = journey["active_thread_id"]
        document: Dict[str, Any] = {
            "journey_id": journey["journey_id"],
            "traveler_id": journey["traveler_id"],
            "status": journey["status"],
            "checkpoint_backend": {
                "kind": journey["checkpoint_backend"],
                "durable": journey["checkpoint_backend"] != "MemorySaver (in-process)",
            },
            "active_thread_id": thread_id,
        }

        document["executions"] = await _executions(q, journey_id)
        checkpoint = await _checkpoint(q, thread_id)
        inline = (
            await _channel_values(client, thread_id)
            if checkpoint.get("status") == "committed"
            else {}
        )
        document["checkpoint"] = checkpoint
        document["selected_plan"] = _selected_plan(checkpoint, inline)
        document["recommendations"] = _recommendations(checkpoint, inline)
        document["pending_decision"] = _pending_decision(checkpoint, inline)
        document["conversation"] = await _conversation(q, thread_id)
        document["hold"] = await _hold(q, journey_id)
        document["authorization"] = await _authorization(q, traveler_id)
        document["rls"] = _unavailable("no scoped probe run this session")
        return document


async def _executions(q, journey_id: str) -> Dict[str, Any]:
    rows = await q(EXECUTIONS_SQL, (journey_id,))
    if not rows:
        return _unavailable("no execution has claimed this journey yet")
    return {
        "status": "observed",
        "source": "journey_executions",
        "items": [
            {
                "execution_id": r["execution_id"],
                "attempt": int(r["attempt"]),
                "worker_id": r["worker_id"],
                "status": r["status"],
                "started_at": _iso(r["started_at"]),
                "ended_at": _iso(r["ended_at"]),
                "lease_expires_at": _iso(r["lease_expires_at"]),
            }
            for r in rows
        ],
    }


async def _checkpoint(q, thread_id: Optional[str]) -> Dict[str, Any]:
    if not thread_id:
        return _unavailable("the journey has no active thread")
    rows = await q(CHECKPOINT_SQL, (thread_id,))
    if not rows:
        return _unavailable(f"no checkpoint committed on thread {thread_id}")
    row = rows[0]
    return {
        "status": "committed",
        "source": "checkpoints",
        "thread_id": thread_id,
        "checkpoint_id": row["checkpoint_id"],
        "parent_checkpoint_id": row["parent_checkpoint_id"],
        "checkpoint_ns": row["checkpoint_ns"],
        "committed_at": _iso(row["committed_at"]),
    }


def _channel_source(checkpoint: Dict[str, Any], channel: str) -> str:
    return (
        f"checkpoint:{checkpoint['thread_id']}/{checkpoint['checkpoint_id']}"
        f"#channel:{channel}"
    )


def _selected_plan(checkpoint: Dict[str, Any], inline: Any) -> Dict[str, Any]:
    if checkpoint.get("status") != "committed":
        return _unavailable("no committed checkpoint to read a selection from")
    package_id = _channel(inline, "selected_package")
    if not package_id:
        return _unavailable("the checkpoint carries no selected_package channel")
    return {
        "status": "observed",
        "source": _channel_source(checkpoint, "selected_package"),
        "package_id": package_id,
    }


def _recommendations(checkpoint: Dict[str, Any], inline: Any) -> Dict[str, Any]:
    if checkpoint.get("status") != "committed":
        return _unavailable("no committed checkpoint to read recommendations from")
    items = _channel(inline, "packages") or _channel(inline, "recommendations")
    if not items:
        return _unavailable("the checkpoint carries no recommendation channel")
    return {
        "status": "observed",
        "source": _channel_source(checkpoint, "recommendations"),
        "items": items,
    }


def _pending_decision(checkpoint: Dict[str, Any], inline: Any) -> Dict[str, Any]:
    if checkpoint.get("status") != "committed":
        return _unavailable("no committed checkpoint to read a pending step from")
    intent = _channel(inline, "hold_intent")
    if not intent:
        return _unavailable("the checkpoint carries no pending hold intent")
    return {
        "status": "observed",
        "source": _channel_source(checkpoint, "hold_intent"),
        "hold_request_id": intent.get("hold_request_id"),
        "package_id": intent.get("package_id"),
        "prompt": f"Confirm the hold on {intent.get('package_id')}",
    }


async def _conversation(q, thread_id: Optional[str]) -> Dict[str, Any]:
    if not thread_id:
        return _unavailable("the journey has no active thread")
    rows = await q(MESSAGES_SQL, (thread_id,))
    if not rows:
        return _unavailable(f"no messages recorded for conversation {thread_id}")
    return {
        "status": "observed",
        "source": "conversation_messages",
        "messages": [
            {
                "message_id": r["message_id"],
                "role": r["role"],
                "content": r["content"],
                "created_at": _iso(r["created_at"]),
            }
            for r in rows
        ],
    }


async def _hold(q, journey_id: str) -> Dict[str, Any]:
    rows = await q(HOLD_SQL, (journey_id,))
    if not rows:
        return _unavailable("no hold has been placed for this journey")
    row = rows[0]
    return {
        "status": row["status"],
        # The claim the demo makes, stated as data rather than as prose.
        "label": "one hold for this request",
        "source": "hold_requests + bookings",
        "hold_request_id": row["hold_request_id"],
        "booking_id": row["booking_id"],
        "created_by_execution_id": row["execution_id"],
        "hold_expires_at": _iso(row["hold_expires_at"]),
        "package_id": row["package_id"],
        "duration": row["duration"],
        "travelers_count": (
            int(row["travelers_count"]) if row["travelers_count"] is not None else None
        ),
        # Every retry of one request replays the first hold, so this is 1 for a
        # journey that survived a restart. More than 1 would be the bug.
        "hold_records": len(rows),
    }


async def _authorization(q, traveler_id: str) -> Dict[str, Any]:
    rows = await q(AUDIT_SQL, (traveler_id,))
    if not rows:
        return {
            **_unavailable("no authorization decision recorded for this traveler"),
            "source": "traveler_access_audit",
        }
    row = rows[0]
    return {
        "status": "observed",
        "source": "traveler_access_audit",
        "audit_id": row["audit_id"],
        "identity_provider": row["identity_provider"],
        "subject": row["principal"] or row["subject_id"],
        "decision": row["decision"],
        "reason": row["reason"],
        "observed_at": _iso(row["decided_at"]),
    }
