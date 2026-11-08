-- Apply before deploying the action-auditing Gateway Lambda. Each new action
-- and replay inserts this record before committing its business transaction.
CREATE TABLE IF NOT EXISTS booking_action_audit (
    action_audit_id TEXT PRIMARY KEY,
    traveler_id VARCHAR(50) NOT NULL REFERENCES travelers(traveler_id),
    booking_id VARCHAR(50) NOT NULL REFERENCES bookings(booking_id),
    journey_id TEXT NOT NULL REFERENCES journeys(journey_id),
    hold_request_id TEXT NOT NULL,
    execution_id TEXT,
    operation TEXT NOT NULL CHECK (operation IN ('hold', 'confirm', 'release')),
    replayed BOOLEAN NOT NULL,
    identity_provider TEXT NOT NULL CHECK (identity_provider IN ('aws_iam', 'cognito')),
    verified_subject TEXT,
    workload_subject TEXT NOT NULL,
    access_audit_id TEXT NOT NULL REFERENCES traveler_access_audit(audit_id),
    authorization_ref TEXT,
    gateway_request_id TEXT,
    lambda_request_id TEXT,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK ((identity_provider = 'cognito') = (verified_subject IS NOT NULL)),
    FOREIGN KEY (journey_id, hold_request_id)
        REFERENCES hold_requests(journey_id, hold_request_id)
);

CREATE INDEX IF NOT EXISTS booking_action_audit_booking
    ON booking_action_audit(booking_id, recorded_at);
ALTER TABLE booking_action_audit ENABLE ROW LEVEL SECURITY;
ALTER TABLE booking_action_audit FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS booking_action_audit_scope ON booking_action_audit;
CREATE POLICY booking_action_audit_scope ON booking_action_audit
    USING (traveler_id = current_setting('app.current_traveler_id', true))
    WITH CHECK (
        traveler_id = current_setting('app.current_traveler_id', true)
        AND EXISTS (SELECT 1 FROM bookings b
                    WHERE b.booking_id = booking_action_audit.booking_id
                      AND b.traveler_id = booking_action_audit.traveler_id)
    );
REVOKE ALL ON booking_action_audit FROM PUBLIC, meridian_app,
    meridian_backend, meridian_workflow, meridian_gateway;
GRANT SELECT ON booking_action_audit TO meridian_app;
GRANT SELECT, INSERT ON booking_action_audit TO meridian_gateway;
GRANT SELECT ON bookings, hold_requests, journeys TO meridian_gateway;
COMMENT ON TABLE booking_action_audit IS
    'Application action evidence committed with the booking. JWT subject is bound by the '
    'Gateway interceptor/Cedar rule. Service request IDs correlate native logs; this table '
    'does not attest a Cedar decision or provide tamper resistance against administrators.';
