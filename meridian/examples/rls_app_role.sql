-- =============================================================================
-- meridian_app — least-privilege role for Aurora RLS-scoped app traffic.
--
-- The RDS Data API connects as the user behind secretArn. In local workshop
-- deployments that is often the cluster admin role, which can bypass RLS even
-- when tables are ENABLE + FORCE'd. Each scoped request therefore steps down
-- into meridian_app before running traveler-scoped reads and writes.
--
-- scoped_session() applies:
--     SET LOCAL ROLE meridian_app;   -- step off the master role for the txn
--     set_config('app.current_traveler_id', :tid, true);
-- RLS then filters both the /rls-probe and Phase 4 concierge memory access.
--
-- Idempotent: safe to run repeatedly.
-- =============================================================================

-- 1. App role. NOLOGIN keeps direct connections out; NOBYPASSRLS is required.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'meridian_app') THEN
        CREATE ROLE meridian_app NOLOGIN NOBYPASSRLS NOINHERIT;
    ELSE
        ALTER ROLE meridian_app NOLOGIN NOBYPASSRLS NOINHERIT;
    END IF;
END
$$;

-- 2. The master user must be allowed to SET ROLE meridian_app.
GRANT meridian_app TO meridian_admin;

-- 3. Table privileges for scoped reads/writes; RLS policies still decide rows.
GRANT SELECT, INSERT, UPDATE, DELETE ON
    travelers,
    traveler_profiles,
    traveler_preferences,
    conversations,
    conversation_messages,
    trip_interactions,
    agent_audit_log,
    bookings
TO meridian_app;

-- 4. Sequences (e.g. bookings/booking_lines SERIAL) so INSERTs can get ids.
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO meridian_app;

-- Verify (optional):
--   SET ROLE meridian_app;
--   SET app.current_traveler_id = 'trv_meridian_demo';
--   SELECT row_security_active('public.traveler_preferences'), count(*)
--     FROM traveler_preferences;     -- expect: true, 17
--   RESET ROLE;
