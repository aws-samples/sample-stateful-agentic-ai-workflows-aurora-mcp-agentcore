-- 031: bound what any workload login can hold on the database.
--
-- Each limit applies to new sessions of that login; RDS Data API connections pick it up as
-- the service opens them. SET ROLE does not apply role settings, so the limits are set on
-- the login roles, never on meridian_app. The master login keeps the defaults for migrations
-- and operations scripts.
--
-- statement_timeout 30s: below the Data API's 45 second call timeout, so Aurora cancels a
--   runaway statement itself, even when a caller sets continueAfterTimeout.
-- lock_timeout 10s: a write waiting on a held inventory or journey lock fails with an error
--   instead of queueing behind it until the statement limit.
-- idle_in_transaction_session_timeout 120s: below the Data API's three minute idle rollback,
--   and the guard a PostgreSQL driver or RDS Proxy path needs, since PostgreSQL leaves it off.
DO $$
DECLARE
    login_name TEXT;
BEGIN
    FOREACH login_name IN ARRAY ARRAY[
        'meridian_backend', 'meridian_gateway', 'meridian_identity', 'meridian_catalog',
        'meridian_workflow'
    ] LOOP
        IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = login_name) THEN
            RAISE EXCEPTION '% does not exist; apply migrations 015, 018 and 022 first', login_name;
        END IF;
    END LOOP;
END
$$;

ALTER ROLE meridian_backend SET statement_timeout = '30s';
ALTER ROLE meridian_backend SET lock_timeout = '10s';
ALTER ROLE meridian_backend SET idle_in_transaction_session_timeout = '120s';
ALTER ROLE meridian_gateway SET statement_timeout = '30s';
ALTER ROLE meridian_gateway SET lock_timeout = '10s';
ALTER ROLE meridian_gateway SET idle_in_transaction_session_timeout = '120s';
ALTER ROLE meridian_identity SET statement_timeout = '30s';
ALTER ROLE meridian_identity SET lock_timeout = '10s';
ALTER ROLE meridian_identity SET idle_in_transaction_session_timeout = '120s';
ALTER ROLE meridian_catalog SET statement_timeout = '30s';
ALTER ROLE meridian_catalog SET lock_timeout = '10s';
ALTER ROLE meridian_catalog SET idle_in_transaction_session_timeout = '120s';
ALTER ROLE meridian_workflow SET statement_timeout = '30s';
ALTER ROLE meridian_workflow SET lock_timeout = '10s';
ALTER ROLE meridian_workflow SET idle_in_transaction_session_timeout = '120s';
