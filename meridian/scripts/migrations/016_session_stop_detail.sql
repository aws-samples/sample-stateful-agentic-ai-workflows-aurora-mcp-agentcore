-- 016: what a presenter's stop caught. A stop lands either while the run waits
-- for the traveler's review or while a worker is mid-run; the rail and the proof
-- name which, and the step the run had saved last.
ALTER TABLE workflow_session_stops
    ADD COLUMN IF NOT EXISTS stopped_during VARCHAR(16) NOT NULL
        CHECK (stopped_during IN ('waiting', 'running')),
    ADD COLUMN IF NOT EXISTS last_step VARCHAR(32),
    ADD COLUMN IF NOT EXISTS released_execution_id VARCHAR(64);
