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
       created_at, updated_at, CURRENT_TIMESTAMP::TEXT AS observed_at
  FROM journeys WHERE journey_id = %s
"""

EXECUTIONS_SQL = """
SELECT execution_id, attempt, worker_id, status, started_at::TEXT, ended_at::TEXT,
       lease_expires_at::TEXT
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
       b.status, b.created_at::TIMESTAMPTZ::TEXT AS hold_created_at,
       b.hold_expires_at::TEXT AS hold_expires_at,
       b.confirmed_at::TIMESTAMPTZ::TEXT AS confirmed_at,
       CURRENT_TIMESTAMP::TEXT AS observed_at, bl.package_id, bl.duration,
       bl.travelers_count, bl.unit_price, b.total_amount
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


def checkpoint_backend_is_durable(kind: str) -> bool:
    """Only the durable backends adopted by the workflow can support this claim."""
    return kind in {"AuroraDataApiSaver", "PostgresSaver (Aurora · pooled)"}


def _channel(values: Any, name: str) -> Any:
    """Read one channel out of a checkpoint's loaded values."""
    return values.get(name) if isinstance(values, dict) else None


async def _workflow_snapshot(client: Any, thread_id: str, checkpoint_id: str):
    """Read pending nodes and values using the same graph definition, without running it."""
    from backend.agents.orchestration_05.workflow import OrchestrationAgent
    from backend.db.aurora_dataapi_saver import AuroraDataApiSaver

    async def read_only(*args, **kwargs):
        raise RuntimeError("Journey inspection cannot execute workflow nodes")

    workflow = OrchestrationAgent(read_only, read_only)
    workflow.checkpointer = AuroraDataApiSaver(client)
    workflow.graph = workflow._build_graph()
    return await workflow.graph.aget_state({"configurable": {"thread_id": thread_id, "checkpoint_id": checkpoint_id}})


def _workflow_document(snapshot, checkpoint):
    if snapshot is None or not snapshot.values:
        return _unavailable("no saved workflow to restore")
    values = snapshot.values
    pending = list(snapshot.next)
    return {
        "status": "observed",
        "source": _channel_source(checkpoint, "workflow"),
        "conversation_id": values.get("conversation_id"),
        "query": values.get("query", ""),
        "message": (
            "Your shortlist is saved. Resume to continue from the checkpoint."
            if pending else values.get("response", "Workflow finished.")
        ),
        "workflow_status": "paused" if pending else (values.get("workflow_status") or "complete"),
        "next_nodes": pending,
        "activities": values.get("activities", []),
        "travelers_count": values.get("travelers_count") or (values.get("hold_intent") or {}).get("quantity", 1),
        "execution_id": values.get("execution_id"),
        "resumed_from_checkpoint": values.get("resumed_from_checkpoint"),
        "resumed_after_restart": bool(values.get("resumed_after_restart")),
    }


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
            "observed_at": _iso(journey.get("observed_at")),
            "journey_id": journey["journey_id"],
            "traveler_id": journey["traveler_id"],
            "status": journey["status"],
            "checkpoint_backend": {
                "kind": journey["checkpoint_backend"],
                "durable": checkpoint_backend_is_durable(journey["checkpoint_backend"]),
            },
            "active_thread_id": thread_id,
        }

        document["executions"] = await _executions(q, journey_id)
        checkpoint = await _checkpoint(q, thread_id)
        snapshot = (
            await _workflow_snapshot(client, thread_id, checkpoint["checkpoint_id"])
            if checkpoint.get("status") == "committed"
            else None
        )
        inline = dict(snapshot.values) if snapshot else {}
        document["workflow"] = _workflow_document(snapshot, checkpoint)
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
    intent = _channel(inline, "hold_intent") or {}
    for channel, package_id in (
        ("hold_package", _channel(inline, "hold_package")),
        ("selected_package", _channel(inline, "selected_package")),
        ("hold_intent", intent.get("package_id")),
    ):
        if package_id:
            return {
                "status": "observed",
                "source": _channel_source(checkpoint, channel),
                "package_id": package_id,
            }
    return _unavailable("the checkpoint carries no selected package or hold intent")


def _recommendations(checkpoint: Dict[str, Any], inline: Any) -> Dict[str, Any]:
    if checkpoint.get("status") != "committed":
        return _unavailable("no committed checkpoint to read recommendations from")
    channel = "packages" if _channel(inline, "packages") else "recommendations"
    items = _channel(inline, channel)
    if not items:
        return _unavailable("the checkpoint carries no recommendation channel")
    return {
        "status": "observed",
        "source": _channel_source(checkpoint, channel),
        "items": items,
    }


def _pending_decision(checkpoint: Dict[str, Any], inline: Any) -> Dict[str, Any]:
    if checkpoint.get("status") != "committed":
        return _unavailable("no committed checkpoint to read a pending step from")
    # Retain hold_intent for idempotent replay, but never present a completed
    # operation as a new decision merely because its intent is still saved.
    if _channel(inline, "hold_id"):
        return _unavailable("the hold intent has already produced a booking")
    if _channel(inline, "workflow_status") in ("complete", "resumed"):
        return _unavailable("the workflow has finished with no pending hold decision")
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
        "source": "hold_requests + bookings + booking_lines",
        "hold_request_id": row["hold_request_id"],
        "booking_id": row["booking_id"],
        "created_by_execution_id": row["execution_id"],
        "hold_created_at": _iso(row.get("hold_created_at")),
        "observed_at": _iso(row.get("observed_at")),
        "hold_expires_at": _iso(row["hold_expires_at"]),
        "confirmed_at": _iso(row.get("confirmed_at")),
        "package_id": row["package_id"],
        "duration": row["duration"],
        # Preserve the booked amounts, including when catalog prices change.
        "unit_price": _iso(row.get("unit_price")),
        "total_amount": _iso(row.get("total_amount")),
        "travelers_count": (
            int(row["travelers_count"]) if row["travelers_count"] is not None else None
        ),
        # A journey can contain multiple distinct requests; this count alone
        # does not establish whether any one request was replayed.
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
