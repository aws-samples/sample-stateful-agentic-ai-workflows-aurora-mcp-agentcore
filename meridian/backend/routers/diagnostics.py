"""Diagnostics API — prove Aurora RLS is enforced, live.

The ``/rls-probe`` endpoint first proves the authenticated workload is allowed
to claim Alex and denied when it claims the decoy traveler. It then runs the
SAME ``COUNT(*)`` twice against a table:

  1. SCOPED   — inside ``scoped_session(traveler_id=...)``, so the GUC
                ``app.current_traveler_id`` is set and the RLS policy filters
                rows to that traveler.
  2. BASELINE — through the privileged migration connection, outside the app
                role. This sees all rows for a count-only comparison.

The difference between the two counts is the live proof that RLS is doing the
filtering — not a comment in a slide. The endpoint also returns the real
``CREATE POLICY`` USING clause from ``pg_policies`` so the audience sees the
actual rule.

The app role itself is fail closed when the traveler GUC is unset. The broader
baseline is available only through the privileged administrative connection.

This endpoint is READ-ONLY (COUNT + pg_policies SELECT only).

AWS docs:
  - RDS Data API: https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.html
  - PostgreSQL RLS: https://www.postgresql.org/docs/current/ddl-rowsecurity.html
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from backend.agentcore.identity import get_agentcore_identity
from backend.authorization import TravelerAuthorizationError
from backend.db.rds_data_client import get_rds_data_client
from backend.memory.store import DEMO_TRAVELER_ID
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
    traveler_id = authorize_traveler(
        principal, request.traveler_id or DEMO_TRAVELER_ID
    )
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
    negative = await db.check_traveler_authorization(
        NEGATIVE_CONTROL_TRAVELER_ID,
        authorization,
        write_audit=True,
    )

    for table in tables:
        try:
            # Scoped: GUC set → RLS filters to this traveler.
            async with db.scoped_session(
                traveler_id=traveler_id,
                agent_type="memory_agent",
                authorization=authorization,
            ) as tx:
                scoped = await _count(db, table, tx)
            # Privileged baseline: the Data API secret maps to the migration
            # role, so this count is intentionally outside the app role.
            unscoped = await _count(db, table, None)
            results.append(
                RlsTableResult(table=table, scoped_count=scoped, unscoped_count=unscoped)
            )
        except Exception as exc:  # one bad table shouldn't 500 the whole probe
            results.append(
                RlsTableResult(
                    table=table, scoped_count=0, unscoped_count=0, error=str(exc)[:200]
                )
            )

    # Pull the real USING clause for each table's policy from pg_catalog.
    policies: List[RlsPolicy] = []
    for table in tables:
        try:
            rows = await db.execute(
                "SELECT tablename, policyname, qual "
                "FROM pg_policies WHERE schemaname = 'public' AND tablename = %s",
                (table,),
            )
            for r in rows:
                policies.append(
                    RlsPolicy(
                        table=r.get("tablename", table),
                        policy=r.get("policyname", ""),
                        using_clause=r.get("qual"),
                    )
                )
        except Exception:
            # pg_policies read is best-effort; the counts are the real proof.
            pass

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
    except Exception as exc:
        debug = {"error": str(exc)[:200]}

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
        negative_control={
            "requested_traveler_id": negative.traveler_id,
            "display_name": NEGATIVE_CONTROL_DISPLAY_NAME,
            "decision": negative.decision,
            "reason": negative.reason,
            "audit_id": negative.audit_id,
        },
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

# LangGraph's checkpoint tables only exist once PostgresSaver has run. Their
# absence is a legitimate answer ("the durable store was never configured"),
# not an error.
CHECKPOINT_TABLES = ("checkpoints", "checkpoint_writes")


class ReceiptLine(BaseModel):
    label: str
    table: str
    count: int
    detail: Optional[str] = None
    scoped: bool = False


class SessionReceiptResponse(BaseModel):
    traveler_id: str
    since: str
    lines: List[ReceiptLine]
    authorization_subject: Optional[str] = None
    durable_checkpoints: bool = False


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
    db, sql: str, params: tuple, transaction_id: Optional[str] = None
) -> Optional[int]:
    """Count rows, returning None when the relation does not exist."""
    try:
        rows = await db.execute(sql, params, transaction_id=transaction_id)
    except Exception:  # noqa: BLE001 - a missing table is an answer, not a fault
        return None
    return int(rows[0]["n"]) if rows else 0


@router.post("/session-receipt", response_model=SessionReceiptResponse)
async def session_receipt(
    request: SessionReceiptRequest = SessionReceiptRequest(),
    principal: HttpPrincipal = Depends(require_http_principal),
) -> SessionReceiptResponse:
    """Count the durable state this session produced, table by table."""
    traveler_id = authorize_traveler(
        principal, request.traveler_id or DEMO_TRAVELER_ID
    )
    db = get_rds_data_client()
    authorization = get_agentcore_identity().authorization_context()
    window = f"{request.window_minutes} minutes"
    lines: List[ReceiptLine] = []

    # Governance evidence is not traveler-scoped: a DENY is precisely a row for
    # a traveler this workload may not claim, so it is read on the admin path.
    allow = await _count_since(
        db,
        "SELECT COUNT(*) AS n FROM traveler_access_audit "
        "WHERE decision = 'allow' AND decided_at > CURRENT_TIMESTAMP - %s::interval",
        (window,),
    )
    deny = await _count_since(
        db,
        "SELECT COUNT(*) AS n FROM traveler_access_audit "
        "WHERE decision = 'deny' AND decided_at > CURRENT_TIMESTAMP - %s::interval",
        (window,),
    )
    lines.append(ReceiptLine(
        label="Authorization decisions",
        table="traveler_access_audit",
        count=(allow or 0) + (deny or 0),
        detail=f"{allow or 0} allow · {deny or 0} deny",
    ))

    audited = await _count_since(
        db,
        "SELECT COUNT(*) AS n FROM agent_audit_log "
        "WHERE ran_at > CURRENT_TIMESTAMP - %s::interval",
        (window,),
    )
    lines.append(ReceiptLine(
        label="RLS-scoped operations audited",
        table="agent_audit_log",
        count=audited or 0,
        detail="workload identity linked to the traveler scope it ran under",
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
        )
        interactions = await _count_since(
            db,
            "SELECT COUNT(*) AS n FROM trip_interactions "
            "WHERE created_at > CURRENT_TIMESTAMP - %s::interval",
            (window,),
            transaction_id=tx,
        )

    lines.append(ReceiptLine(
        label="Conversation turns persisted",
        table="conversation_messages",
        count=turns or 0,
        detail="each with a 1024d embedding",
        scoped=True,
    ))
    lines.append(ReceiptLine(
        label="Interactions written for semantic recall",
        table="trip_interactions",
        count=interactions or 0,
        detail="pgvector rows Phase 4 recalls against",
        scoped=True,
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
        )
    lines.append(ReceiptLine(
        label="Courtesy holds still live",
        table="bookings",
        count=holds or 0,
        detail="inventory committed by the workflow, still inside its TTL",
        scoped=True,
    ))

    # Scoped to this session's workflow thread. Counting these tables whole -
    # every thread, every traveler, all of time - let a rehearsal from an hour
    # earlier satisfy a durability claim made about the run on screen, which is
    # the one number on this receipt that has to be beyond argument.
    checkpoint_total = 0
    checkpoints_exist = False
    thread_id = request.conversation_id
    for table in CHECKPOINT_TABLES:
        count = await _count_since(
            db,
            f"SELECT COUNT(*) AS n FROM {table} WHERE thread_id = %s",
            (thread_id,),
        ) if thread_id else None
        if count is None and thread_id is None:
            # Distinguish "no thread to count" from "no such table": probe the
            # relation so the copy can say which is true.
            count = await _count_since(db, f"SELECT COUNT(*) AS n FROM {table} WHERE false", ())
            if count is not None:
                checkpoints_exist = True
            continue
        if count is not None:
            checkpoints_exist = True
            checkpoint_total += count

    if not checkpoints_exist:
        checkpoint_detail = "PostgresSaver was never configured, so nothing was written"
    elif thread_id is None:
        checkpoint_detail = "no workflow thread ran in this session"
    elif checkpoint_total:
        checkpoint_detail = f"workflow position externalized into Aurora for thread {thread_id}"
    else:
        checkpoint_detail = f"thread {thread_id} wrote no checkpoint rows"

    lines.append(ReceiptLine(
        label="LangGraph checkpoint rows",
        table=", ".join(CHECKPOINT_TABLES),
        count=checkpoint_total,
        detail=checkpoint_detail,
    ))

    return SessionReceiptResponse(
        traveler_id=traveler_id,
        since=f"last {request.window_minutes} minutes",
        lines=lines,
        authorization_subject=authorization.subject_id if authorization else None,
        durable_checkpoints=checkpoints_exist and checkpoint_total > 0,
    )
