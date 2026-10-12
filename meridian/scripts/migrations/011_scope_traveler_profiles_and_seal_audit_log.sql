-- Put the traveler's own profile under the same row-level policy as the rest
-- of their data, and stop the application from rewriting its own audit log.
--
-- travelers and traveler_profiles were granted to meridian_app with no RLS, so
-- a session scoped to one traveler could read every traveler's identity and
-- profile. The only thing keeping the demo correct was the WHERE clause in each
-- query. Verified live before this migration: a session scoped to
-- trv_meridian_demo, selecting from travelers with no predicate, returned
-- trv_demo_decoy as well. traveler_profiles showed no leak only because the
-- decoy had no profile row - it had no policy at all. That table holds the
-- dietary notes and loyalty member ids, the most sensitive data here.
--
-- FORCE matches the other traveler tables as defense in depth. On this cluster
-- the master role is not subject to RLS even with FORCE, so seeding and the
-- identity-binding checks that run before a scope exists are unaffected; the
-- guarantee comes from scoped_session() dropping to meridian_app.
--
-- agent_audit_log recorded what the agents did, and meridian_app could UPDATE
-- and DELETE it - the actor could rewrite the record of its own actions. It
-- keeps INSERT, to write the log, and SELECT, to read it back. Nothing in the
-- application updates or deletes audit rows. This makes the log append-only for
-- the application; it is still an application event log, not tamper-evident
-- storage, and a privileged administrator can still change it.

ALTER TABLE travelers ENABLE ROW LEVEL SECURITY;
ALTER TABLE travelers FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS rls_travelers_traveler ON travelers;
CREATE POLICY rls_travelers_traveler ON travelers FOR ALL USING (
    traveler_id = current_setting('app.current_traveler_id', true)
) WITH CHECK (
    traveler_id = current_setting('app.current_traveler_id', true)
);

ALTER TABLE traveler_profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE traveler_profiles FORCE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS rls_profiles_traveler ON traveler_profiles;
CREATE POLICY rls_profiles_traveler ON traveler_profiles FOR ALL USING (
    traveler_id = current_setting('app.current_traveler_id', true)
) WITH CHECK (
    traveler_id = current_setting('app.current_traveler_id', true)
);

REVOKE UPDATE, DELETE ON agent_audit_log FROM meridian_app;
