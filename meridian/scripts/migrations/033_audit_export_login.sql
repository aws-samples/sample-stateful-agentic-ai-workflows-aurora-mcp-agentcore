-- 033: a read-only Aurora login for the audit export Lambda.
--
-- The audit tables (agent_audit_log, traveler_access_audit, booking_action_audit) live in Aurora,
-- where the master login can edit or delete rows. The audit export Lambda copies new rows to an
-- S3 bucket with Object Lock, as a hash chain, so an edit or a deletion made later can be found
-- (docs/OPERATIONS.md, "Audit export"). meridian_audit_export is the login that Lambda reads
-- with. It owns nothing, cannot bypass RLS, is a member of no role, and holds SELECT on the three
-- audit tables and nothing else: no sequence, no function, no write of any kind.
--
-- The three tables restrict reads by traveler, so the login reads every traveler's rows only
-- through one explicit SELECT policy per table, FOR SELECT TO meridian_audit_export
-- USING (true). The policies name this one role; no policy grants the other logins more. The
-- role is created with NOLOGIN; scripts/provision_service_logins.py --login audit sets LOGIN
-- with a SCRAM verifier and keeps the credential only in Secrets Manager.
--
-- The login gets the same session limits as the other service logins (statement_timeout 30s,
-- lock_timeout 10s, idle_in_transaction_session_timeout 120s) and TimeZone UTC. The exporter
-- compares agent_audit_log.ran_at, a timestamp without time zone, against UTC, so the writers'
-- sessions must store UTC too; tests/test_migration_033_live.py asserts it for every login.
--
-- The export reads each table in (timestamp, id) order, so the migration adds one index per
-- table on those two columns. The runner applies a migration in one transaction, which
-- rules out CREATE INDEX CONCURRENTLY. The audit tables are small, and the plain CREATE INDEX
-- blocks writes to each table for the moment the build takes. Run the migration when no
-- workflow is running. A 5 second lock limit makes an index build that would queue behind a
-- writer fail fast, so it never holds writers back; run the migration again if it does.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'meridian_audit_export') THEN
        CREATE ROLE meridian_audit_export
            NOLOGIN NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE;
    ELSIF EXISTS (
        SELECT FROM pg_roles WHERE rolname = 'meridian_audit_export'
           AND (rolsuper OR rolbypassrls OR rolinherit OR rolcreatedb OR rolcreaterole
                OR rolreplication)
    ) THEN
        RAISE EXCEPTION
            'meridian_audit_export exists with attributes 033 does not allow; fix the role by hand';
    ELSIF EXISTS (
        SELECT FROM pg_auth_members m JOIN pg_roles r ON r.oid = m.member
         WHERE r.rolname = 'meridian_audit_export'
    ) OR EXISTS (
        SELECT FROM pg_class c
          CROSS JOIN LATERAL aclexplode(c.relacl) a
          JOIN pg_roles r ON r.oid = a.grantee
         WHERE r.rolname = 'meridian_audit_export'
           AND NOT (c.relname IN ('agent_audit_log', 'traveler_access_audit',
                                  'booking_action_audit')
                    AND a.privilege_type = 'SELECT')
    ) THEN
        RAISE EXCEPTION
            'meridian_audit_export exists with memberships or table grants 033 does not allow';
    END IF;
END
$$;

GRANT SELECT ON agent_audit_log, traveler_access_audit, booking_action_audit
    TO meridian_audit_export;

DROP POLICY IF EXISTS agent_audit_log_export_select ON agent_audit_log;
CREATE POLICY agent_audit_log_export_select ON agent_audit_log
    FOR SELECT TO meridian_audit_export USING (true);
DROP POLICY IF EXISTS traveler_access_audit_export_select ON traveler_access_audit;
CREATE POLICY traveler_access_audit_export_select ON traveler_access_audit
    FOR SELECT TO meridian_audit_export USING (true);
DROP POLICY IF EXISTS booking_action_audit_export_select ON booking_action_audit;
CREATE POLICY booking_action_audit_export_select ON booking_action_audit
    FOR SELECT TO meridian_audit_export USING (true);

ALTER ROLE meridian_audit_export SET statement_timeout = '30s';
ALTER ROLE meridian_audit_export SET lock_timeout = '10s';
ALTER ROLE meridian_audit_export SET idle_in_transaction_session_timeout = '120s';
ALTER ROLE meridian_audit_export SET timezone = 'UTC';

SET LOCAL lock_timeout = '5s';
CREATE INDEX IF NOT EXISTS agent_audit_log_export_order
    ON agent_audit_log (ran_at, audit_id);
CREATE INDEX IF NOT EXISTS traveler_access_audit_export_order
    ON traveler_access_audit (decided_at, audit_id);
CREATE INDEX IF NOT EXISTS booking_action_audit_export_order
    ON booking_action_audit (recorded_at, action_audit_id);
