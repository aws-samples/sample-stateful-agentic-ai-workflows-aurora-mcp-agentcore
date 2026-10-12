-- Let the application read its own authorization history, and only its own.
--
-- Presenter proof asserts that a past action was authorized, which only the
-- audit row recorded at the time can establish. The application role therefore
-- needs SELECT here. A bare grant would expose every traveler's authorization
-- history to a scoped session, so the table gains the same traveler-scoped RLS
-- policy the other traveler-owned tables use.
--
-- Writes are unaffected. check_traveler_authorization runs before a scope is
-- established, as the table owner, which bypasses RLS.

ALTER TABLE traveler_access_audit ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS traveler_access_audit_traveler_scope ON traveler_access_audit;
CREATE POLICY traveler_access_audit_traveler_scope ON traveler_access_audit
    USING (requested_traveler_id = current_setting('app.current_traveler_id', true));

GRANT SELECT ON traveler_access_audit TO meridian_app;
