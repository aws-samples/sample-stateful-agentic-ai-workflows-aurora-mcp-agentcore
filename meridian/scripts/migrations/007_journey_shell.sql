-- Journey identity, execution leasing, hold request identity, and the
-- LangGraph checkpoint tables.
--
-- The checkpoint DDL is copied from langgraph-checkpoint-postgres 3.1.2 so the
-- Data API saver and AsyncPostgresSaver share one schema. Two deviations are
-- required for Data API execution: indexes are created within transactions,
-- and the packaged ALTER that drops NOT NULL from checkpoint_blobs.blob is
-- folded into the CREATE.

CREATE TABLE IF NOT EXISTS checkpoint_migrations (
    v INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS checkpoints (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    parent_checkpoint_id TEXT,
    type TEXT,
    checkpoint JSONB NOT NULL,
    metadata JSONB NOT NULL DEFAULT '{}',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
);

CREATE TABLE IF NOT EXISTS checkpoint_blobs (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    channel TEXT NOT NULL,
    version TEXT NOT NULL,
    type TEXT NOT NULL,
    blob BYTEA,
    PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
);

CREATE TABLE IF NOT EXISTS checkpoint_writes (
    thread_id TEXT NOT NULL,
    checkpoint_ns TEXT NOT NULL DEFAULT '',
    checkpoint_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    channel TEXT NOT NULL,
    type TEXT,
    blob BYTEA NOT NULL,
    task_path TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
);

CREATE INDEX IF NOT EXISTS checkpoints_thread_id_idx ON checkpoints(thread_id);
CREATE INDEX IF NOT EXISTS checkpoint_blobs_thread_id_idx ON checkpoint_blobs(thread_id);
CREATE INDEX IF NOT EXISTS checkpoint_writes_thread_id_idx ON checkpoint_writes(thread_id);

-- Record the packaged migration versions so a later AsyncPostgresSaver.setup()
-- does not re-apply DDL this migration already created.
INSERT INTO checkpoint_migrations (v)
SELECT generate_series(0, 9)
ON CONFLICT (v) DO NOTHING;

CREATE TABLE IF NOT EXISTS journeys (
    journey_id         VARCHAR(64) PRIMARY KEY,
    traveler_id        VARCHAR(50) NOT NULL REFERENCES travelers(traveler_id),
    checkpoint_backend VARCHAR(32) NOT NULL,
    active_thread_id   VARCHAR(200),
    status             VARCHAR(32) NOT NULL DEFAULT 'active',
    created_at         TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS journey_threads (
    thread_id  VARCHAR(200) PRIMARY KEY,
    journey_id VARCHAR(64) NOT NULL REFERENCES journeys(journey_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS journey_executions (
    execution_id     VARCHAR(64) PRIMARY KEY,
    journey_id       VARCHAR(64) NOT NULL REFERENCES journeys(journey_id),
    thread_id        VARCHAR(200) NOT NULL REFERENCES journey_threads(thread_id),
    attempt          INTEGER NOT NULL,
    phase            SMALLINT,
    worker_id        VARCHAR(128) NOT NULL,
    status           VARCHAR(16) NOT NULL,
    lease_expires_at TIMESTAMPTZ,
    started_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at         TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS journey_executions_one_running
    ON journey_executions (thread_id) WHERE status = 'running';
CREATE INDEX IF NOT EXISTS journey_executions_journey_idx
    ON journey_executions (journey_id, started_at DESC);

CREATE TABLE IF NOT EXISTS hold_requests (
    journey_id       VARCHAR(64) NOT NULL REFERENCES journeys(journey_id),
    hold_request_id  VARCHAR(64) NOT NULL,
    booking_id       VARCHAR(50) NOT NULL,
    fingerprint      TEXT NOT NULL,
    thread_id        VARCHAR(200) NOT NULL,
    execution_id     VARCHAR(64),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (journey_id, hold_request_id),
    CONSTRAINT hold_requests_booking_fk
        FOREIGN KEY (booking_id) REFERENCES bookings(booking_id)
        DEFERRABLE INITIALLY DEFERRED
);

ALTER TABLE journeys ENABLE ROW LEVEL SECURITY;
ALTER TABLE journey_threads ENABLE ROW LEVEL SECURITY;
ALTER TABLE journey_executions ENABLE ROW LEVEL SECURITY;
ALTER TABLE hold_requests ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS journeys_traveler_scope ON journeys;
CREATE POLICY journeys_traveler_scope ON journeys
    USING (traveler_id = current_setting('app.current_traveler_id', true));

DROP POLICY IF EXISTS journey_threads_traveler_scope ON journey_threads;
CREATE POLICY journey_threads_traveler_scope ON journey_threads
    USING (journey_id IN (
        SELECT journey_id FROM journeys
        WHERE traveler_id = current_setting('app.current_traveler_id', true)
    ));

DROP POLICY IF EXISTS journey_executions_traveler_scope ON journey_executions;
CREATE POLICY journey_executions_traveler_scope ON journey_executions
    USING (journey_id IN (
        SELECT journey_id FROM journeys
        WHERE traveler_id = current_setting('app.current_traveler_id', true)
    ));

DROP POLICY IF EXISTS hold_requests_traveler_scope ON hold_requests;
CREATE POLICY hold_requests_traveler_scope ON hold_requests
    USING (journey_id IN (
        SELECT journey_id FROM journeys
        WHERE traveler_id = current_setting('app.current_traveler_id', true)
    ));

GRANT SELECT, INSERT, UPDATE ON journeys, journey_threads, journey_executions,
    hold_requests TO meridian_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON checkpoints, checkpoint_blobs,
    checkpoint_writes, checkpoint_migrations TO meridian_app;
