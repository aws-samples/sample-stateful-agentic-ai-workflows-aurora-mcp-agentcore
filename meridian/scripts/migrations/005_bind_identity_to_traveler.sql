-- Bind authenticated workload identities to traveler records before RLS.
-- Idempotent migration for existing Meridian databases.

CREATE TABLE IF NOT EXISTS traveler_identity_bindings (
    binding_id VARCHAR(50) PRIMARY KEY,
    identity_provider VARCHAR(50) NOT NULL,
    subject_id VARCHAR(255) NOT NULL,
    traveler_id VARCHAR(50) NOT NULL REFERENCES travelers(traveler_id),
    status VARCHAR(20) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'revoked')),
    granted_by VARCHAR(255),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMPTZ,
    UNIQUE(identity_provider, subject_id, traveler_id)
);

CREATE INDEX IF NOT EXISTS idx_identity_bindings_subject
    ON traveler_identity_bindings(identity_provider, subject_id, status);

CREATE TABLE IF NOT EXISTS traveler_access_audit (
    audit_id VARCHAR(50) PRIMARY KEY,
    identity_provider VARCHAR(50) NOT NULL,
    subject_id VARCHAR(255) NOT NULL,
    principal TEXT,
    requested_traveler_id VARCHAR(50) NOT NULL,
    decision VARCHAR(10) NOT NULL CHECK (decision IN ('allow', 'deny')),
    reason TEXT,
    decided_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_traveler_access_audit_subject
    ON traveler_access_audit(identity_provider, subject_id, decided_at DESC);

ALTER TABLE agent_audit_log
    ADD COLUMN IF NOT EXISTS authorization_provider VARCHAR(50),
    ADD COLUMN IF NOT EXISTS authorization_subject VARCHAR(255),
    ADD COLUMN IF NOT EXISTS authorization_decision VARCHAR(10);

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

COMMENT ON TABLE traveler_identity_bindings IS
    'Authorization grants from authenticated workload subjects to traveler records';
COMMENT ON TABLE traveler_access_audit IS
    'Allow and deny decisions recorded before an RLS traveler scope is accepted';
