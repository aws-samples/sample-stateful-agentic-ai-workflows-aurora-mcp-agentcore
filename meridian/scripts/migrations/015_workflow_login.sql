-- 015: the MeridianWorkflow Runtime's own Aurora login, and the session stop record.
--
-- meridian_workflow replaces the master role for the Phase 5 workflow. It owns
-- nothing, cannot bypass RLS, and reaches meridian_app's privileges only through
-- SET ROLE inside a scoped session. Its snapshot reads and writes pin
-- app.current_traveler_id in their own transaction, so the policies below decide
-- which rows it sees and appends. This file creates the role with NOLOGIN;
-- scripts/provision_workflow_login.py sets LOGIN with a SCRAM verifier and keeps
-- the credential only in Secrets Manager.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'meridian_workflow') THEN
        CREATE ROLE meridian_workflow NOLOGIN NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE;
    END IF;
END
$$;
ALTER ROLE meridian_workflow NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE;

GRANT meridian_app TO meridian_workflow WITH INHERIT FALSE, SET TRUE;

-- scoped_session checks the grant and writes the audit row as the login, before SET ROLE.
GRANT SELECT ON traveler_identity_bindings TO meridian_workflow;
GRANT INSERT ON traveler_access_audit TO meridian_workflow;
DROP POLICY IF EXISTS traveler_access_audit_workflow_insert ON traveler_access_audit;
CREATE POLICY traveler_access_audit_workflow_insert ON traveler_access_audit
    FOR INSERT TO meridian_workflow WITH CHECK (true);

-- Retrieval reads the catalog; semantic_trip_search runs with the caller's rights.
GRANT SELECT ON trip_packages TO meridian_workflow;

-- The snapshot fence and policies read the journey tables under their traveler policies.
GRANT SELECT ON journeys, journey_threads, journey_executions TO meridian_workflow;

GRANT SELECT, INSERT ON workflow_snapshots TO meridian_workflow;
GRANT USAGE ON SEQUENCE workflow_snapshots_snapshot_seq_seq TO meridian_workflow;
DROP POLICY IF EXISTS workflow_snapshots_workflow_select ON workflow_snapshots;
CREATE POLICY workflow_snapshots_workflow_select ON workflow_snapshots
    FOR SELECT TO meridian_workflow
    USING (
        traveler_id = current_setting('app.current_traveler_id', true)
        AND EXISTS (SELECT 1 FROM journey_threads jt WHERE jt.thread_id = workflow_snapshots.session_id)
    );
DROP POLICY IF EXISTS workflow_snapshots_workflow_insert ON workflow_snapshots;
CREATE POLICY workflow_snapshots_workflow_insert ON workflow_snapshots
    FOR INSERT TO meridian_workflow
    WITH CHECK (
        traveler_id = current_setting('app.current_traveler_id', true)
        AND EXISTS (SELECT 1 FROM journey_threads jt WHERE jt.thread_id = workflow_snapshots.session_id)
    );

-- A presenter's stop of a journey's Runtime session, recorded so the
-- continuity rail can show it from Aurora.
CREATE TABLE IF NOT EXISTS workflow_session_stops (
    stop_id            BIGSERIAL    PRIMARY KEY,
    journey_id         VARCHAR(64)  NOT NULL REFERENCES journeys(journey_id),
    thread_id          VARCHAR(200) NOT NULL REFERENCES journey_threads(thread_id),
    runtime_session_id VARCHAR(128) NOT NULL,
    outcome            VARCHAR(16)  NOT NULL CHECK (outcome IN ('stopped', 'not_running')),
    requested_by       VARCHAR(255) NOT NULL,
    stopped_at         TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX IF NOT EXISTS workflow_session_stops_journey
    ON workflow_session_stops (journey_id, stop_id DESC);
ALTER TABLE workflow_session_stops ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS workflow_session_stops_traveler_scope ON workflow_session_stops;
CREATE POLICY workflow_session_stops_traveler_scope ON workflow_session_stops
    USING (journey_id IN (
        SELECT journey_id FROM journeys
         WHERE traveler_id = current_setting('app.current_traveler_id', true)
    ));
REVOKE ALL ON workflow_session_stops FROM meridian_app;
GRANT SELECT, INSERT ON workflow_session_stops TO meridian_app;
GRANT USAGE ON SEQUENCE workflow_session_stops_stop_id_seq TO meridian_app;
