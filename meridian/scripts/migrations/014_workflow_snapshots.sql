-- Workflow snapshots for the Strands Graph that runs Phase 5.
--
-- Strands' SnapshotSessionManager writes the whole orchestrator state after
-- every node through a Storage backend. AuroraSnapshotStorage
-- (backend/agents/phase_05_workflow/snapshot_storage.py) appends each write as
-- a row, so this table is the step-by-step history of a run, and the newest
-- row for a key is what a resume restores.
--
-- Who touches it, by role, the same split migration 013 made for checkpoints:
--   * The workflow writes and reads as the master role, after its lease claim
--     verified the traveler grant and the thread binding inside a scoped
--     session. Each row is stamped with that traveler, execution and worker.
--     The master role is exempt from RLS and needs no grant from here.
--   * journey_document.py reads inside scoped_session() as meridian_app, so
--     meridian_app gets SELECT only, behind a policy.
--
-- ENABLE but not FORCE, for the reason 013 gives: FORCE would add nothing for
-- the master role and would risk locking the workflow out of its own state.

CREATE TABLE IF NOT EXISTS workflow_snapshots (
    snapshot_seq  BIGSERIAL    PRIMARY KEY,
    storage_key   TEXT         NOT NULL,
    session_id    VARCHAR(128) NOT NULL,
    traveler_id   VARCHAR(64)  NOT NULL,
    execution_id  VARCHAR(64),
    worker_id     VARCHAR(128),
    snapshot      JSONB        NOT NULL,
    status        TEXT GENERATED ALWAYS AS (snapshot #>> '{data,state,status}') STORED,
    saved_at      TIMESTAMPTZ  NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX IF NOT EXISTS workflow_snapshots_key_seq
    ON workflow_snapshots (storage_key, snapshot_seq DESC);
CREATE INDEX IF NOT EXISTS workflow_snapshots_session_seq
    ON workflow_snapshots (session_id, snapshot_seq DESC);

REVOKE ALL ON workflow_snapshots FROM meridian_app;
GRANT SELECT ON workflow_snapshots TO meridian_app;

ALTER TABLE workflow_snapshots ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS workflow_snapshots_traveler_select ON workflow_snapshots;
CREATE POLICY workflow_snapshots_traveler_select ON workflow_snapshots
    FOR SELECT TO meridian_app
    USING (
        traveler_id = current_setting('app.current_traveler_id', true)
        AND EXISTS (
            SELECT 1 FROM journey_threads jt WHERE jt.thread_id = workflow_snapshots.session_id
        )
    );
