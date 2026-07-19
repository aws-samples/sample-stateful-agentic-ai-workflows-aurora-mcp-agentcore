-- =============================================================================
-- Row-Level Security for Meridian agents — how per-traveler isolation is
-- ACTUALLY enforced, and the privileged-role gotcha that nearly made it a no-op.
-- =============================================================================
--
-- ┌──────────────────────────┬───────────────────────────┬──────────────────────────────────┐
-- │ Table                    │ Policy                    │ Scoped by                        │
-- ├──────────────────────────┼───────────────────────────┼──────────────────────────────────┤
-- │ traveler_preferences     │ rls_prefs_traveler        │ app.current_traveler_id          │
-- │ trip_interactions        │ rls_interactions_traveler │ app.current_traveler_id          │
-- │ conversations            │ rls_conversations_traveler│ app.current_traveler_id          │
-- │ conversation_messages    │ rls_messages_traveler     │ via conversations FK → same GUC  │
-- │ bookings                 │ rls_bookings_agent_type   │ app.agent_type ∈ agent_access[]  │
-- └──────────────────────────┴───────────────────────────┴──────────────────────────────────┘
--
-- READ THIS IF YOU'RE STRONG ON ONE SIDE AND NEW TO THE OTHER ------------------
--
--   • You know Postgres, new to agents: an "agent turn" is one user prompt
--     handled by an LLM that may emit several tool calls (search, recall
--     memory, book). Each turn runs in ONE short DB transaction. We pin the
--     traveler's id into a session variable for that transaction so the LLM
--     physically cannot read another traveler's rows — even if the SQL it
--     generates forgets a WHERE clause. RLS is the backstop for code whose
--     output you can't fully predict.
--
--   • You build agents, new to RLS: Row-Level Security is a PostgreSQL feature
--     where the DATABASE filters rows per query from a policy attached to the
--     table — enforcement lives in the engine, not your app code. A GUC (a
--     session config variable) is the policy's input; we use a custom one,
--     app.current_traveler_id. The policy says "only return rows where
--     traveler_id = that variable."
--
-- THE ENFORCEMENT CHAIN — all of these must hold, or RLS does NOTHING ---------
--
--   1. ENABLE ROW LEVEL SECURITY   → the table's policies get consulted
--   2. a policy with a USING (...) → defines which rows pass
--   3. the connecting role is one RLS actually applies to — i.e. NOT the
--      cluster master role, which stays exempt here   ← THE GOTCHA (below)
--   (We also set FORCE ROW LEVEL SECURITY as defense-in-depth.)
--
-- THE GOTCHA WE HIT (why the policies alone weren't enough) -------------------
--
--   The RDS Data API connects as whatever DB user the secret in secretArn
--   maps to — it is NOT inherently privileged. OUR secret maps to the cluster
--   MASTER user (meridian_admin). That privileged role is NOT getting RLS
--   applied to it — PostgreSQL's own row_security_active() returns false for
--   it — even though the tables have RLS both ENABLED and FORCED, and the
--   master user is neither a superuser nor BYPASSRLS (\du and pg_roles confirm
--   both). The exact reason the Aurora master role stays exempt under FORCE is
--   an Aurora-specific behavior we don't assert a mechanism for; what matters
--   is the OBSERVABLE, demonstrable fact:
--
--       as meridian_admin  (master)     : row_security_active = false, 22 rows
--       as meridian_app    (non-priv)   : row_security_active = true,  17 rows
--                                          (same GUC, same policy)
--
--   So the lesson is not to debug why the master role is special — it's to
--   NOT run as it. We step down to a dedicated non-privileged role, and
--   row_security_active() flips to true and the policy filters.
--
--   => scoped_session() (backend/db/rds_data_client.py) does, per transaction:
--        SELECT set_config('app.current_traveler_id', :tid, true);  -- the GUC
--        SET LOCAL ROLE meridian_app;       -- step off the privileged role
--      Then every query runs as the non-privileged meridian_app and the policy
--      filters. The least-privilege role + grants live in
--      examples/rls_app_role.sql — RUN IT, or RLS will not filter.
--
--   Production best practice: give the app its OWN restricted DB user with its
--   OWN secret, so it never holds master credentials. We keep the master
--   secret and step down per-transaction so the role switch is visible live.
--
-- WHY TRANSACTION-SCOPED (set_config(..., true) and SET LOCAL) ----------------
--   • Agent crashes mid-turn → the GUC and the role both revert with the
--     aborted transaction.
--   • Pooled connections carry no residual identity → no cross-request leakage.
--   • No cleanup code; Postgres reverts everything on COMMIT/ROLLBACK.
--
-- FAIL-OPEN ESCAPE HATCH ----------------------
--   Each policy also passes rows when the GUC is empty:
--       OR current_setting('app.current_traveler_id', true) = ''
--   This lets seed scripts and admin tooling (master user, no GUC) read across
--   travelers. So an UNSET GUC is fail-OPEN, not fail-closed. The app stays
--   safe two ways: it ALWAYS sets the GUC, AND it runs as the non-BYPASSRLS
--   meridian_app role. For a stricter production posture, drop the '' branch
--   (fail-closed) and give seed/admin tooling its own privileged path.
--
-- IDENTITY, AUTHORIZATION, AND RLS ARE THREE DISTINCT CONTROLS ---------------
--   1. AgentCore Identity / AWS STS authenticates WHO the workload is.
--   2. traveler_identity_bindings authorizes WHICH traveler that subject may
--      claim. scoped_session() fails before setting the GUC when no active
--      binding exists, and writes the allow/deny to traveler_access_audit.
--   3. Aurora RLS enforces WHAT rows the authorized traveler scope may see.
--
--   This is workload authorization. A multi-user hosted application should
--   additionally authenticate the end user (for example with Cognito) and map
--   that user subject to a traveler rather than treating one workload as all
--   users.
--
-- TWO PATTERNS THIS FILE DEPLOYS ---------------------------------------------
--   A) Per-traveler memory isolation (Phase 4): traveler_preferences,
--      trip_interactions, conversations, conversation_messages — scoped by
--      app.current_traveler_id.
--   B) Agent-type scoping on booking writes: search-only agents cannot read or
--      mutate confirmed bookings even though they share one DB role — scoped by
--      app.agent_type against the row's agent_access[] allow-list.
--
-- COMPANION FILES -------------------------------------------------------------
--   examples/rls_app_role.sql      — meridian_app role + grants   (REQUIRED)
--   backend/db/rds_data_client.py  — scoped_session(): GUC + SET LOCAL ROLE
--   scripts/init_aurora_schema.py  — runs THIS file at schema init
--
-- AWS / PostgreSQL docs:
--   RDS Data API transactions:
--     https://docs.aws.amazon.com/rdsdataservice/latest/APIReference/API_BeginTransaction.html
--   PostgreSQL RLS:
--     https://www.postgresql.org/docs/current/ddl-rowsecurity.html
--   PostgreSQL roles & BYPASSRLS:
--     https://www.postgresql.org/docs/current/sql-createrole.html
-- =============================================================================
-- ----------------------------------------------------------------------------
-- A. Per-traveler isolation on Phase 4 memory tables
-- ----------------------------------------------------------------------------
ALTER TABLE traveler_preferences ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE trip_interactions ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
-- FORCE is set as defense-in-depth. NOTE: on this cluster the master role is
-- still not subject to RLS even with FORCE (see header "THE GOTCHA") — the
-- real guarantee comes from running queries as the non-privileged meridian_app
-- role via SET LOCAL ROLE in scoped_session(). See examples/rls_app_role.sql.
ALTER TABLE traveler_preferences FORCE ROW LEVEL SECURITY;
ALTER TABLE conversation_messages FORCE ROW LEVEL SECURITY;
ALTER TABLE trip_interactions FORCE ROW LEVEL SECURITY;
ALTER TABLE conversations FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS rls_prefs_traveler ON traveler_preferences;
DROP POLICY IF EXISTS rls_messages_traveler ON conversation_messages;
DROP POLICY IF EXISTS rls_interactions_traveler ON trip_interactions;
DROP POLICY IF EXISTS rls_conversations_traveler ON conversations;
CREATE POLICY rls_prefs_traveler ON traveler_preferences FOR ALL USING (
    traveler_id = current_setting('app.current_traveler_id', true)
    OR current_setting('app.current_traveler_id', true) = ''
);
CREATE POLICY rls_interactions_traveler ON trip_interactions FOR ALL USING (
    traveler_id = current_setting('app.current_traveler_id', true)
    OR current_setting('app.current_traveler_id', true) = ''
);
CREATE POLICY rls_conversations_traveler ON conversations FOR ALL USING (
    traveler_id = current_setting('app.current_traveler_id', true)
    OR current_setting('app.current_traveler_id', true) = ''
);
-- conversation_messages joins to conversations to derive the traveler.
CREATE POLICY rls_messages_traveler ON conversation_messages FOR ALL USING (
    current_setting('app.current_traveler_id', true) = ''
    OR conversation_id IN (
        SELECT conversation_id
        FROM conversations
        WHERE traveler_id = current_setting('app.current_traveler_id', true)
    )
);
-- The empty-string branch above is FAIL-OPEN: an unset GUC sees all rows, so
-- seed scripts / admin tooling (master user, no GUC) can read across travelers.
-- The app stays scoped because it always sets the GUC AND runs as the
-- non-BYPASSRLS meridian_app role. Drop this branch for a fail-closed posture.
-- ----------------------------------------------------------------------------
-- B. Agent-type scoping on bookings
-- ----------------------------------------------------------------------------
ALTER TABLE bookings
ADD COLUMN IF NOT EXISTS agent_access TEXT [] DEFAULT ARRAY ['booking_agent', 'supervisor_agent', 'concierge_agent'];
UPDATE bookings
SET agent_access = ARRAY ['booking_agent', 'supervisor_agent', 'concierge_agent']
WHERE agent_access IS NULL;
DROP POLICY IF EXISTS rls_bookings_agent_type ON bookings;
CREATE POLICY rls_bookings_agent_type ON bookings FOR ALL USING (
    current_setting('app.agent_type', true) = ''
    OR current_setting('app.agent_type', true) = ANY(agent_access)
);
ALTER TABLE bookings ENABLE ROW LEVEL SECURITY;
ALTER TABLE bookings FORCE ROW LEVEL SECURITY;
-- ----------------------------------------------------------------------------
-- C. Lightweight audit log written by the agent runtime
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS agent_audit_log (
    audit_id VARCHAR(50) PRIMARY KEY,
    traveler_id VARCHAR(50),
    agent_name VARCHAR(100) NOT NULL,
    operation VARCHAR(100) NOT NULL,
    rls_traveler TEXT,
    rls_agent_type TEXT,
    iam_identity TEXT,
    authorization_provider VARCHAR(50),
    authorization_subject VARCHAR(255),
    authorization_decision VARCHAR(10),
    rows_returned INTEGER,
    ran_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
ALTER TABLE agent_audit_log
    ADD COLUMN IF NOT EXISTS authorization_provider VARCHAR(50),
    ADD COLUMN IF NOT EXISTS authorization_subject VARCHAR(255),
    ADD COLUMN IF NOT EXISTS authorization_decision VARCHAR(10);
CREATE INDEX IF NOT EXISTS idx_audit_traveler ON agent_audit_log(traveler_id, ran_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_agent ON agent_audit_log(agent_name, ran_at DESC);
DROP VIEW IF EXISTS agent_iam_audit;
CREATE VIEW agent_iam_audit AS
SELECT audit_id,
    ran_at,
    agent_name,
    operation,
    traveler_id,
    authorization_provider,
    authorization_subject,
    authorization_decision,
    rls_traveler,
    rls_agent_type,
    iam_identity,
    rows_returned
FROM agent_audit_log
ORDER BY ran_at DESC;
COMMENT ON VIEW agent_iam_audit IS 'Per-turn record linking workload identity, traveler authorization, and RLS session scope.';
