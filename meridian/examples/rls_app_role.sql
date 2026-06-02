-- =============================================================================
-- meridian_app — a least-privilege role so Aurora RLS actually engages.
--
-- WHY: the RDS Data API connects as the user its secretArn maps to. OUR secret
-- is the cluster MASTER user (meridian_admin). On this Aurora cluster the master
-- role is NOT subject to RLS — PostgreSQL's row_security_active() returns false
-- for it — even though the tables are ENABLE + FORCE'd and the role is neither
-- superuser nor BYPASSRLS (\du confirms). We don't assert the exact Aurora
-- internal; the fix is simply to run as a non-privileged role, which IS subject
-- to RLS. (Pointing the secret at this role would also fix it — SET LOCAL ROLE
-- keeps the master secret and steps down per-transaction so it's visible live.)
-- Proven live:
--     as meridian_admin, GUC set : row_security_active = false, 22 rows
--     as a NOBYPASSRLS role, same : row_security_active = true,  17 rows
-- So the fix is NOT another table flag — the app must run scoped reads/writes
-- as a role that does NOT bypass RLS. scoped_session() does:
--     SET LOCAL ROLE meridian_app;   -- step off the master role for the txn
--     set_config('app.current_traveler_id', :tid, true);
-- and RLS filters for real, for both the /rls-probe AND the Phase 4 concierge
-- memory reads/writes.
--
-- Idempotent: safe to run repeatedly.
-- Run once on the cluster:
--   python scripts/apply_rls_force_and_decoy.py   (FORCE + decoy)  -- already done
--   <run this file>                                 (app role + grants)
-- =============================================================================

-- 1. The role. NOLOGIN (we never connect AS it — we SET ROLE into it from the
--    master connection), NOBYPASSRLS (the whole point), NOINHERIT so it can't
--    pick up bypass from any granted parent.
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

-- 3. Table privileges for everything scoped_session touches (memory store +
--    concierge + memory MCP). RLS still filters rows — these grants only say
--    "this role may attempt the operation"; the policies decide which rows.
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

-- 5. Read access to the catalog views the probe uses (pg_policies is a system
--    view, already world-readable — no grant needed). Nothing else required.

-- Verify (optional):
--   SET ROLE meridian_app;
--   SET app.current_traveler_id = 'trv_meridian_demo';
--   SELECT row_security_active('public.traveler_preferences'), count(*)
--     FROM traveler_preferences;     -- expect: true, 17
--   RESET ROLE;
