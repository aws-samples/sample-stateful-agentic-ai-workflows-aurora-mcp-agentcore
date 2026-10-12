"""Diagnostics API — prove Aurora RLS is enforced, live.

The ``/rls-probe`` endpoint first proves the authenticated workload is allowed
to claim Jordan and denied when it claims the decoy traveler. It then runs the
SAME ``COUNT(*)`` twice against a table:

  1. SCOPED   — inside ``scoped_session(traveler_id=...)``, so the GUC
                ``app.current_traveler_id`` is set and the RLS policy filters
                rows to that traveler.
  2. BASELINE — through ``backend_admin_count`` (migration 018), a definer
                function that returns every traveler's row count and nothing
                else. The backend login holds no other cross-traveler read.

The difference between the two counts is the live proof that RLS is doing the
filtering, not a claim in a comment. The endpoint also returns the real
``CREATE POLICY`` USING clause from ``pg_policies`` so the viewer sees the
actual rule.

The app role itself is fail closed when the traveler GUC is unset. The broader
baseline is available only through the definer function.

This endpoint is READ-ONLY (COUNT + pg_policies SELECT only).

AWS docs:
  - RDS Data API: https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.html
  - PostgreSQL RLS: https://www.postgresql.org/docs/current/ddl-rowsecurity.html
"""

from typing import Any, List, NamedTuple, Optional, Sequence

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.agentcore.identity import get_agentcore_identity
from backend.authorization import TravelerAuthorizationError
from backend.db.admin_counts import RLS_BASELINE_KINDS, admin_count
from backend.db.rds_data_client import get_rds_data_client
from backend.logging_config import log_exception
from backend.http_auth import (
    HttpPrincipal,
    authorize_traveler,
    require_http_principal,
)

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])

# Strict allow-list. Table names can't be passed as bound parameters, so we
# only ever interpolate names from this set — never user input. These are the
# RLS-protected tables from examples/rls_for_agents.sql scoped by traveler_id.
ALLOWED_TABLES = (
    "traveler_preferences",
    "trip_interactions",
    "conversations",
    "conversation_messages",
)
DEFAULT_TABLES = ("traveler_preferences", "trip_interactions")
NEGATIVE_CONTROL_TRAVELER_ID = "trv_demo_decoy"
# Returned to the UI so the probe card renders the decoy's real name
# instead of hard-coding it client side and lying if this changes.
NEGATIVE_CONTROL_DISPLAY_NAME = "Jordan Lee"


class RlsProbeRequest(BaseModel):
    traveler_id: Optional[str] = Field(default=None, min_length=1, max_length=50)
    tables: Optional[List[str]] = Field(default=None, max_length=len(ALLOWED_TABLES))


class RlsTableResult(BaseModel):
    table: str
    scoped_count: int
    unscoped_count: int
    error: Optional[str] = None


class RlsPolicy(BaseModel):
    table: str
    policy: str
    using_clause: Optional[str] = None


class RlsProbeResponse(BaseModel):
    traveler_id: str
    authorization: dict
    negative_control: dict
    tables: List[RlsTableResult]
    policies: List[RlsPolicy]
    # Proof RLS is engaged: the effective role inside the scoped txn (the
    # least-privilege app role, not the privileged master user) + PostgreSQL's
    # own row_security_active() verdict + the active traveler scope.
    debug: Optional[dict] = None


async def _negative_control(db, traveler_id: str, authorization) -> dict:
    """Ask for the decoy's record; not applicable when the principal is the decoy itself."""
    if traveler_id == NEGATIVE_CONTROL_TRAVELER_ID:
        return {
            "requested_traveler_id": NEGATIVE_CONTROL_TRAVELER_ID,
            "display_name": NEGATIVE_CONTROL_DISPLAY_NAME,
            "decision": "not_applicable",
            "reason": "the signed-in traveler is the decoy, so this control cannot be denied",
            "audit_id": None,
        }
    negative = await db.check_traveler_authorization(
        NEGATIVE_CONTROL_TRAVELER_ID, authorization, write_audit=True
    )
    return {
        "requested_traveler_id": negative.traveler_id,
        "display_name": NEGATIVE_CONTROL_DISPLAY_NAME,
        "decision": negative.decision,
        "reason": negative.reason,
        "audit_id": negative.audit_id,
    }


async def _count(db, table: str, transaction_id: Optional[str]) -> int:
    # table is whitelisted (see ALLOWED_TABLES) so this f-string is safe.
    rows = await db.execute(
        f"SELECT COUNT(*) AS n FROM {table}",
        transaction_id=transaction_id,
    )
    return int(rows[0]["n"]) if rows else 0


@router.post("/rls-probe", response_model=RlsProbeResponse)
async def rls_probe(
    request: RlsProbeRequest = RlsProbeRequest(),
    principal: HttpPrincipal = Depends(require_http_principal),
) -> RlsProbeResponse:
    """Run scoped vs unscoped COUNT(*) per table + return the live policies."""
    traveler_id = authorize_traveler(principal, request.traveler_id)
    requested = request.tables or list(DEFAULT_TABLES)
    # Drop anything not on the allow-list (injection guard).
    tables = list(dict.fromkeys(t for t in requested if t in ALLOWED_TABLES))
    if not tables:
        tables = list(DEFAULT_TABLES)

    db = get_rds_data_client()
    authorization = get_agentcore_identity().authorization_context()
    results: List[RlsTableResult] = []

    # Layer 1 + 2 proof: authenticated AWS subject -> explicit traveler grant.
    # The negative control asks the same subject for Jordan's decoy record and
    # should be denied before any RLS scope can be set.
    decision = await db.check_traveler_authorization(
        traveler_id,
        authorization,
        write_audit=True,
    )
    if not decision.allowed:
        raise HTTPException(status_code=403, detail=str(TravelerAuthorizationError(decision)))
    negative = await _negative_control(db, traveler_id, authorization)

    for table in tables:
        try:
            # Scoped: GUC set → RLS filters to this traveler.
            async with db.scoped_session(
                traveler_id=traveler_id,
                agent_type="memory_agent",
                authorization=authorization,
            ) as tx:
                scoped = await _count(db, table, tx)
            # Baseline: every traveler's rows, counted by the definer function
            # because the backend login itself is subject to RLS.
            unscoped = await admin_count(db, RLS_BASELINE_KINDS[table])
            results.append(
                RlsTableResult(table=table, scoped_count=scoped, unscoped_count=unscoped)
            )
        except Exception:  # one bad table shouldn't 500 the whole probe
            ref = log_exception(f"rls_probe_{table}")
            results.append(
                RlsTableResult(
                    table=table,
                    scoped_count=0,
                    unscoped_count=0,
                    error=f"Probe query failed. Reference {ref}.",
                )
            )

    policies = await _load_policies(db, tables)

    # Proof, inside the scoped transaction, that RLS is genuinely engaged:
    # the effective role (should be the least-privilege app role, NOT the
    # privileged master user) and PostgreSQL's own row_security_active() verdict.
    # On this Aurora cluster the master role isn't subject to RLS even with
    # ENABLE+FORCE — scoped_session() steps down to meridian_app so the policies
    # actually apply (see backend/db/rds_data_client.py + examples/rls_app_role.sql).
    debug: Optional[dict] = None
    try:
        async with db.scoped_session(
            traveler_id=traveler_id,
            agent_type="memory_agent",
            authorization=authorization,
        ) as tx:
            seen = await db.execute(
                "SELECT current_user AS effective_role, "
                "row_security_active('traveler_preferences') AS rls_active, "
                "current_setting('app.current_traveler_id', true) AS scope, "
                "current_setting('app.authorization_provider', true) AS auth_provider, "
                "current_setting('app.authorization_subject', true) AS auth_subject",
                transaction_id=tx,
            )
        debug = {
            "effective_role": (seen[0].get("effective_role") if seen else None),
            "rls_active": (seen[0].get("rls_active") if seen else None),
            "scope": (seen[0].get("scope") if seen else None),
            "authorization_provider": (
                seen[0].get("auth_provider") if seen else None
            ),
            "authorization_subject": (
                seen[0].get("auth_subject") if seen else None
            ),
        }
    except Exception:
        debug = {"error": f"Scope check failed. Reference {log_exception('rls_probe_scope')}."}

    return RlsProbeResponse(
        traveler_id=traveler_id,
        authorization={
            "provider": decision.provider,
            "subject_id": decision.subject_id,
            "principal": decision.principal,
            "requested_traveler_id": decision.traveler_id,
            "decision": decision.decision,
            "binding_id": decision.binding_id,
            "audit_id": decision.audit_id,
        },
        negative_control=negative,
        tables=results,
        policies=policies,
        debug=debug,
    )


# =============================================================================
# Session receipt — everything the talk actually wrote to Aurora.
#
# The closing beat. Each phase claims to persist something: authorization
# decisions, RLS-scoped reads, conversation turns, interaction embeddings, a
# courtesy hold, workflow checkpoints. This counts the rows behind those claims
# so the close can point at them rather than restate them.
#
# READ-ONLY. Traveler-scoped tables are counted inside a scoped session, so the
# receipt is itself subject to the governance it reports on.
# =============================================================================

# The workflow snapshot table exists once migration 014 has run. Its absence is
# a legitimate answer ("the durable store was never configured"), not an error.
CHECKPOINT_TABLES = ("workflow_snapshots",)


class ReceiptLine(BaseModel):
    label: str
    table: str
    # None means Aurora could not answer; it is never a stand-in for zero.
    count: Optional[int]
    detail: Optional[str] = None
    scoped: bool = False


class SessionReceiptResponse(BaseModel):
    traveler_id: str
    since: str
    lines: List[ReceiptLine]
    authorization_subject: Optional[str] = None
    durable_checkpoints: bool = False
    # Named so the receipt can say which backend it is talking about rather
    # than asserting a mechanism it never checked.
    checkpoint_backend: Optional[str] = None
    checkpoint_backend_durable: bool = False


class SessionReceiptRequest(BaseModel):
    traveler_id: Optional[str] = Field(default=None, min_length=1, max_length=50)
    # Minutes of history to attribute to this session. The default covers a
    # 60-minute slot plus setup.
    window_minutes: int = Field(default=90, ge=1, le=1440)
    # The workflow thread this session ran, so checkpoint rows can be counted
    # for it rather than for the whole table. Without one there is no thread to
    # make a durability claim about.
    conversation_id: Optional[str] = Field(default=None, min_length=1, max_length=200)


async def _count_since(
    db, sql: str, params: tuple, transaction_id: Optional[str] = None, *, context: str = "count"
) -> "Probed":
    """Count rows; on failure return no value and the log reference of why."""
    try:
        rows = await db.execute(sql, params, transaction_id=transaction_id)
    except Exception:  # noqa: BLE001 - an unavailable count degrades its line, not the receipt
        return Probed(None, log_exception(f"session_receipt_{context}"))
    return Probed(int(rows[0]["n"]) if rows else 0)


def _scoped_line(label: str, table: str, probed: "Probed", detail: str) -> ReceiptLine:
    return ReceiptLine(
        label=label,
        table=table,
        count=probed.value,
        detail=detail if probed.value is not None else f"could not be counted (ref {probed.ref})",
        scoped=True,
    )


async def _load_policies(db, tables: Sequence[str]) -> List[RlsPolicy]:
    """The real USING clause for each table's policy, from pg_catalog.

    The read is best-effort: the counts are the real proof, so a failed lookup is skipped.
    """
    policies: List[RlsPolicy] = []
    for table in tables:
        try:
            rows = await db.execute(
                "SELECT tablename, policyname, qual "
                "FROM pg_policies WHERE schemaname = 'public' AND tablename = %s",
                (table,),
            )
        except Exception:  # noqa: BLE001 - see the docstring
            log_exception("rls_probe_policies")
            continue
        policies.extend(
            RlsPolicy(
                table=r.get("tablename", table),
                policy=r.get("policyname", ""),
                using_clause=r.get("qual"),
            )
            for r in rows
        )
    return policies


class Probed(NamedTuple):
    """An answer from Aurora, or None plus the log reference of why there is none."""

    value: Optional[Any]
    ref: Optional[str] = None


async def _admin_count_or_none(
    db, kind: str, *, window: Optional[str] = None, key: Optional[str] = None
) -> Probed:
    """A cross-traveler count, or no value and a log reference when Aurora cannot answer."""
    try:
        return Probed(await admin_count(db, kind, window=window, key=key))
    except Exception:  # noqa: BLE001 - an unavailable count degrades its line, not the receipt
        return Probed(None, log_exception(f"session_receipt_{kind}"))


async def _tables_exist(db, tables: Sequence[str]) -> Probed:
    """True when every named table exists, False when one is absent, None when unanswerable.

    ``to_regclass`` needs no privilege on the table.
    """
    try:
        for table in tables:
            rows = await db.execute(
                "SELECT to_regclass(%s) IS NOT NULL AS present", (f"public.{table}",)
            )
            if not rows or rows[0]["present"] is not True:
                return Probed(False)
    except Exception:  # noqa: BLE001 - an unanswerable probe degrades its line, not the receipt
        return Probed(None, log_exception("session_receipt_tables_exist"))
    return Probed(True)


def _decision_line(allow: Probed, deny: Probed) -> ReceiptLine:
    """The line that proves denies were audited; unknown is never rendered as zero."""
    if allow.value is None or deny.value is None:
        ref = allow.ref or deny.ref
        return ReceiptLine(
            label="Authorization decisions",
            table="traveler_access_audit",
            count=None,
            detail=f"audit counts unavailable (ref {ref})",
        )
    return ReceiptLine(
        label="Authorization decisions",
        table="traveler_access_audit",
        count=allow.value + deny.value,
        detail=f"{allow.value} allow, {deny.value} deny",
    )


def _checkpoint_detail(exists: Probed, total: Probed, thread_id: Optional[str]) -> str:
    """What to say about the snapshot rows, distinguishing absent, unknown and zero."""
    if exists.value is None:
        return f"snapshot table could not be checked (ref {exists.ref})"
    if not exists.value:
        return "no workflow snapshot table in this database, so nothing was written"
    if thread_id is None:
        return "no workflow thread ran in this session"
    if total.value is None:
        return f"workflow snapshots for thread {thread_id} could not be counted (ref {total.ref})"
    if total.value:
        return f"workflow position externalized into Aurora for thread {thread_id}"
    return f"thread {thread_id} wrote no workflow snapshot rows"


@router.post("/session-receipt", response_model=SessionReceiptResponse)
async def session_receipt(
    request: SessionReceiptRequest = SessionReceiptRequest(),
    principal: HttpPrincipal = Depends(require_http_principal),
) -> SessionReceiptResponse:
    """Count the durable state this session produced, table by table."""
    traveler_id = authorize_traveler(principal, request.traveler_id)
    db = get_rds_data_client()
    authorization = get_agentcore_identity().authorization_context()
    window = f"{request.window_minutes} minutes"
    lines: List[ReceiptLine] = []

    # Governance evidence is not traveler-scoped: a DENY is precisely a row for
    # a traveler this workload may not claim, so it is counted by the definer
    # function rather than read through the backend login's own rights.
    allow = await _admin_count_or_none(db, "audit_allow", window=window)
    deny = await _admin_count_or_none(db, "audit_deny", window=window)
    lines.append(_decision_line(allow, deny))

    audited = await _admin_count_or_none(db, "agent_audit", window=window)
    lines.append(ReceiptLine(
        label="RLS-scoped operations audited",
        table="agent_audit_log",
        count=audited.value,
        detail=(
            "workload identity linked to the traveler scope it ran under"
            if audited.value is not None
            else f"could not be counted (ref {audited.ref})"
        ),
    ))

    # Traveler-scoped counts run under RLS, so the receipt obeys the same rule
    # it is reporting on.
    async with db.scoped_session(
        traveler_id=traveler_id,
        agent_type="memory_agent",
        authorization=authorization,
    ) as tx:
        turns = await _count_since(
            db,
            "SELECT COUNT(*) AS n FROM conversation_messages "
            "WHERE created_at > CURRENT_TIMESTAMP - %s::interval",
            (window,),
            transaction_id=tx,
            context="conversation_messages",
        )
        interactions = await _count_since(
            db,
            "SELECT COUNT(*) AS n FROM trip_interactions "
            "WHERE created_at > CURRENT_TIMESTAMP - %s::interval",
            (window,),
            transaction_id=tx,
            context="trip_interactions",
        )

    lines.append(_scoped_line(
        "Conversation turns persisted", "conversation_messages", turns,
        "each with a 1024d embedding",
    ))
    lines.append(_scoped_line(
        "Interactions written for semantic recall", "trip_interactions", interactions,
        "pgvector rows Phase 4 recalls against",
    ))
    # `bookings` is scoped by traveler AND by agent type, so it has to be read
    # as the agent entitled to it. Reading as memory_agent returns nothing,
    # which is the policy working, not an empty table.
    async with db.scoped_session(
        traveler_id=traveler_id,
        agent_type="booking_agent",
        authorization=authorization,
    ) as booking_tx:
        holds = await _count_since(
            db,
            "SELECT COUNT(*) AS n FROM bookings "
            "WHERE status = 'held' AND hold_expires_at > CURRENT_TIMESTAMP",
            (),
            transaction_id=booking_tx,
            context="bookings",
        )
    lines.append(_scoped_line(
        "Courtesy holds still live", "bookings", holds,
        "inventory committed by the workflow, still inside its TTL",
    ))

    # Scoped to this session's workflow thread. Counting these tables whole -
    # every thread, every traveler, all of time - let a rehearsal from an hour
    # earlier satisfy a durability claim made about the run on screen, which is
    # the one number on this receipt that has to be beyond argument.
    # "No thread to count" and "no such table" are different answers, so the
    # relation is probed first and the count follows only for a real thread.
    thread_id = request.conversation_id
    checkpoints_exist = await _tables_exist(db, CHECKPOINT_TABLES)
    checkpoint_total = Probed(0)
    if checkpoints_exist.value and thread_id:
        checkpoint_total = await _admin_count_or_none(db, "workflow_snapshots", key=thread_id)

    from backend.agents.phase_05_workflow.service import workflow_store_status

    backend_status = workflow_store_status()
    backend_kind = str(backend_status.get("kind") or "not initialized")
    backend_durable = bool(backend_status.get("durable"))
    unknown = checkpoints_exist.value is None or checkpoint_total.value is None
    checkpoint_detail = _checkpoint_detail(checkpoints_exist, checkpoint_total, thread_id)

    lines.append(ReceiptLine(
        label="Workflow snapshot rows",
        table=", ".join(CHECKPOINT_TABLES),
        count=None if unknown else checkpoint_total.value,
        detail=checkpoint_detail,
    ))

    return SessionReceiptResponse(
        traveler_id=traveler_id,
        since=f"last {request.window_minutes} minutes",
        lines=lines,
        authorization_subject=authorization.subject_id if authorization else None,
        durable_checkpoints=bool(checkpoints_exist.value and checkpoint_total.value),
        checkpoint_backend=backend_kind,
        checkpoint_backend_durable=backend_durable,
    )
