-- Scope the LangGraph checkpoint tables and agent_audit_log to the traveler.
--
-- Migration 007 granted meridian_app SELECT, INSERT, UPDATE and DELETE on all
-- four checkpoint tables, and none of them has a traveler column or a policy.
-- A session scoped to one traveler could therefore read, rewrite or delete
-- every traveler's workflow state. agent_audit_log had the same gap on SELECT:
-- migration 011 removed UPDATE and DELETE but left no policy, so any scoped
-- session could read every traveler's audit rows.
--
-- Who touches these tables, by role:
--   * AuroraDataApiSaver (backend/db/aurora_dataapi_saver.py) reads and writes
--     all four checkpoint tables as the master role, outside any scoped
--     session. The master role is not subject to RLS, so it needs no grant
--     from here and is unaffected by anything below.
--   * journey_document.py reads checkpoints inside scoped_session() as
--     meridian_app. That is the only scoped access: SELECT on checkpoints.
--   * agent_audit_log is INSERTed inside scoped_session() (store.write_audit)
--     with traveler_id set to the scope's traveler. Nothing scoped reads it;
--     the session receipt counts it on the master path.
--
-- checkpoints gets ENABLE but not FORCE. The saver connects as the master
-- role, which owns the tables and is exempt from RLS, so FORCE would add
-- nothing for it and would risk locking the saver out if that ever changed.
-- The restriction comes from meridian_app dropping into a role that has a
-- policy. agent_audit_log keeps FORCE, matching travelers and traveler_profiles
-- in migration 011.
--
-- checkpoints has no traveler column, so its policy asks journey_threads,
-- which maps thread_id to a journey. The subquery runs as the calling role,
-- so journey_threads' own policy (and journeys' beneath it) decides which
-- threads are visible: a thread belongs to the traveler iff its journey does.
-- A thread that is not registered in journey_threads is invisible to scoped
-- sessions.

REVOKE INSERT, UPDATE, DELETE ON checkpoints FROM meridian_app;
REVOKE ALL ON checkpoint_blobs, checkpoint_writes, checkpoint_migrations FROM meridian_app;

ALTER TABLE checkpoints ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS checkpoints_traveler_select ON checkpoints;
CREATE POLICY checkpoints_traveler_select ON checkpoints
    FOR SELECT TO meridian_app
    USING (EXISTS (
        SELECT 1 FROM journey_threads jt WHERE jt.thread_id = checkpoints.thread_id
    ));

ALTER TABLE agent_audit_log ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_audit_log FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS agent_audit_traveler_select ON agent_audit_log;
CREATE POLICY agent_audit_traveler_select ON agent_audit_log
    FOR SELECT TO meridian_app
    USING (traveler_id = current_setting('app.current_traveler_id', true));
DROP POLICY IF EXISTS agent_audit_traveler_insert ON agent_audit_log;
CREATE POLICY agent_audit_traveler_insert ON agent_audit_log
    FOR INSERT TO meridian_app
    WITH CHECK (traveler_id = current_setting('app.current_traveler_id', true));
