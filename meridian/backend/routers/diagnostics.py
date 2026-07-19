"""Diagnostics API — prove Aurora RLS is enforced, live.

The ``/rls-probe`` endpoint first proves the authenticated workload is allowed
to claim Alex and denied when it claims the decoy traveler. It then runs the
SAME ``COUNT(*)`` twice against a table:

  1. SCOPED   — inside ``scoped_session(traveler_id=...)``, so the GUC
                ``app.current_traveler_id`` is set and the RLS policy filters
                rows to that traveler.
  2. UNSCOPED — outside any transaction, so the GUC is empty. The policies in
                ``examples/rls_for_agents.sql`` deliberately allow the empty
                string as an admin/seed bypass (``... OR current_setting(
                'app.current_traveler_id', true) = ''``), so an unscoped read
                sees ALL rows.

The difference between the two counts is the live proof that RLS is doing the
filtering — not a comment in a slide. The endpoint also returns the real
``CREATE POLICY`` USING clause from ``pg_policies`` so the audience sees the
actual rule.

NOTE: the empty-string bypass IS the mechanism this endpoint demonstrates — do
not "fix" it. It's how seed scripts and this diagnostic read across travelers
while agent turns stay scoped.

This endpoint is READ-ONLY (COUNT + pg_policies SELECT only).

AWS docs:
  - RDS Data API: https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.html
  - PostgreSQL RLS: https://www.postgresql.org/docs/current/ddl-rowsecurity.html
"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.agentcore.identity import get_agentcore_identity
from backend.authorization import TravelerAuthorizationError
from backend.db.rds_data_client import get_rds_data_client
from backend.memory.store import DEMO_TRAVELER_ID

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


class RlsProbeRequest(BaseModel):
    traveler_id: Optional[str] = None
    tables: Optional[List[str]] = None


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
async def rls_probe(request: RlsProbeRequest = RlsProbeRequest()) -> RlsProbeResponse:
    """Run scoped vs unscoped COUNT(*) per table + return the live policies."""
    traveler_id = request.traveler_id or DEMO_TRAVELER_ID
    requested = request.tables or list(DEFAULT_TABLES)
    # Drop anything not on the allow-list (injection guard).
    tables = [t for t in requested if t in ALLOWED_TABLES] or list(DEFAULT_TABLES)

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
            # Unscoped: no transaction → GUC empty → empty-string bypass → all rows.
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
            "decision": negative.decision,
            "reason": negative.reason,
            "audit_id": negative.audit_id,
        },
        tables=results,
        policies=policies,
        debug=debug,
    )
