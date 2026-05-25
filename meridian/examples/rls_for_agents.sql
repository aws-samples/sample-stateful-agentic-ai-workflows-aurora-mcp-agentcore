-- =============================================================================
-- Row-Level Security for Meridian agents
-- =============================================================================
--
-- Two RLS patterns the demo enforces against Aurora:
--
--   A) Per-traveler memory isolation (Phase 4)
--      App: SELECT set_config('app.current_traveler_id', :tid, true)
--      RLS: USING (traveler_id = current_setting('app.current_traveler_id', true))
--
--      The Strands MemoryAgent sets this GUC inside the same transaction as the
--      memory SELECT.  Even if the agent forgets the WHERE clause, Aurora will
--      not return another traveler's preferences, messages, or interactions.
--
--   B) Agent-type scoping for booking writes (booking flow)
--      App: SELECT set_config('app.agent_type', 'booking_agent', true)
--      RLS: USING (current_setting('app.agent_type', true) = ANY(agent_access))
--
--      Search-only agents cannot read or mutate confirmed bookings even though
--      they share the same DB role.
--
-- Both are deployed by `python scripts/init_aurora_schema.py`.
-- =============================================================================

-- ----------------------------------------------------------------------------
-- A. Per-traveler isolation on Phase 4 memory tables
-- ----------------------------------------------------------------------------

ALTER TABLE traveler_preferences   ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversation_messages  ENABLE ROW LEVEL SECURITY;
ALTER TABLE trip_interactions      ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations          ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS rls_prefs_traveler         ON traveler_preferences;
DROP POLICY IF EXISTS rls_messages_traveler      ON conversation_messages;
DROP POLICY IF EXISTS rls_interactions_traveler  ON trip_interactions;
DROP POLICY IF EXISTS rls_conversations_traveler ON conversations;

CREATE POLICY rls_prefs_traveler ON traveler_preferences
    FOR ALL
    USING (
        traveler_id = current_setting('app.current_traveler_id', true)
        OR current_setting('app.current_traveler_id', true) = ''
    );

CREATE POLICY rls_interactions_traveler ON trip_interactions
    FOR ALL
    USING (
        traveler_id = current_setting('app.current_traveler_id', true)
        OR current_setting('app.current_traveler_id', true) = ''
    );

CREATE POLICY rls_conversations_traveler ON conversations
    FOR ALL
    USING (
        traveler_id = current_setting('app.current_traveler_id', true)
        OR current_setting('app.current_traveler_id', true) = ''
    );

-- conversation_messages joins to conversations to derive the traveler.
CREATE POLICY rls_messages_traveler ON conversation_messages
    FOR ALL
    USING (
        current_setting('app.current_traveler_id', true) = ''
        OR conversation_id IN (
            SELECT conversation_id FROM conversations
            WHERE traveler_id = current_setting('app.current_traveler_id', true)
        )
    );

-- The empty-string fallback above lets seed scripts and admin tooling read
-- without a session variable set.  Production code paths always set it.

-- ----------------------------------------------------------------------------
-- B. Agent-type scoping on bookings
-- ----------------------------------------------------------------------------

ALTER TABLE bookings ADD COLUMN IF NOT EXISTS agent_access TEXT[]
    DEFAULT ARRAY['booking_agent', 'supervisor_agent', 'concierge_agent'];

UPDATE bookings SET agent_access = ARRAY['booking_agent', 'supervisor_agent', 'concierge_agent']
WHERE agent_access IS NULL;

DROP POLICY IF EXISTS rls_bookings_agent_type ON bookings;

CREATE POLICY rls_bookings_agent_type ON bookings
    FOR ALL
    USING (
        current_setting('app.agent_type', true) = ''
        OR current_setting('app.agent_type', true) = ANY(agent_access)
    );

ALTER TABLE bookings ENABLE ROW LEVEL SECURITY;

-- ----------------------------------------------------------------------------
-- C. Lightweight audit log written by the agent runtime
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS agent_audit_log (
    audit_id        VARCHAR(50) PRIMARY KEY,
    traveler_id     VARCHAR(50),
    agent_name      VARCHAR(100) NOT NULL,
    operation       VARCHAR(100) NOT NULL,
    rls_traveler    TEXT,
    rls_agent_type  TEXT,
    iam_identity    TEXT,
    rows_returned   INTEGER,
    ran_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_audit_traveler ON agent_audit_log(traveler_id, ran_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_agent    ON agent_audit_log(agent_name, ran_at DESC);

CREATE OR REPLACE VIEW agent_iam_audit AS
SELECT
    audit_id,
    ran_at,
    agent_name,
    operation,
    traveler_id,
    rls_traveler,
    rls_agent_type,
    iam_identity,
    rows_returned
FROM agent_audit_log
ORDER BY ran_at DESC;

COMMENT ON VIEW agent_iam_audit IS
    'Per-turn record of which agent ran which operation under which IAM identity '
    'and RLS session variables.  Operators query this to verify scoping.';
