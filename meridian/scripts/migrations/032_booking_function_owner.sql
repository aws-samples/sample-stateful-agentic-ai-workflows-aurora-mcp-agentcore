-- 032: the booking write functions run as an owner that row-level security still binds.
--
-- create_courtesy_hold, confirm_booking and release_courtesy_hold are SECURITY DEFINER and were
-- owned by the master login, which Aurora does not hold to RLS. Each function checks the
-- traveler scope itself; this migration adds a second check that does not depend on the
-- function body. The new owner cannot log in, cannot bypass RLS and holds only what the three
-- functions read and write:
--   * it reads every booking and booking line, through explicit FOR SELECT policies, because the
--     seat count sums all travelers' held and confirmed lines for a package;
--   * every INSERT and UPDATE it makes must still pass rls_bookings_scope,
--     rls_booking_lines_scope and hold_requests_traveler_scope, so a booking row for a traveler
--     outside the transaction's scope is refused by Aurora even if a function had a bug.
-- EXECUTE grants on the functions are unchanged: only meridian_gateway may call them.
-- CREATE on the schema is held only while the owner changes, as ALTER FUNCTION ... OWNER needs.
-- The master inherits the owner role, so later migrations run as the master can still replace,
-- grant or revoke on these functions; the master already bypasses RLS, so this adds nothing to
-- what it can reach. A later CREATE OR REPLACE also needs the same temporary CREATE grant.
-- UPDATE on bookings is limited to the two columns the functions change.
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'meridian_booking_owner') THEN
        CREATE ROLE meridian_booking_owner NOLOGIN NOBYPASSRLS NOINHERIT NOCREATEDB NOCREATEROLE;
    ELSIF EXISTS (
        SELECT FROM pg_roles WHERE rolname = 'meridian_booking_owner'
           AND (rolcanlogin OR rolsuper OR rolbypassrls OR rolinherit OR rolcreatedb
                OR rolcreaterole OR rolreplication)
    ) THEN
        RAISE EXCEPTION 'meridian_booking_owner exists with attributes 032 does not allow';
    END IF;
END
$$;

DO $$
BEGIN
    EXECUTE format('GRANT meridian_booking_owner TO %I WITH INHERIT TRUE, SET TRUE',
                   current_user);
END
$$;

GRANT SELECT ON trip_packages TO meridian_booking_owner;
GRANT SELECT ON journeys TO meridian_booking_owner;
GRANT SELECT, INSERT ON hold_requests TO meridian_booking_owner;
GRANT SELECT, INSERT, UPDATE (status, confirmed_at) ON bookings TO meridian_booking_owner;
GRANT SELECT, INSERT ON booking_lines TO meridian_booking_owner;

DO $$
BEGIN
    EXECUTE format('GRANT USAGE ON SEQUENCE %s TO meridian_booking_owner',
                   pg_get_serial_sequence('booking_lines', 'line_id'));
END
$$;

DROP POLICY IF EXISTS bookings_owner_read ON bookings;
CREATE POLICY bookings_owner_read ON bookings
    FOR SELECT TO meridian_booking_owner USING (true);
DROP POLICY IF EXISTS booking_lines_owner_read ON booking_lines;
CREATE POLICY booking_lines_owner_read ON booking_lines
    FOR SELECT TO meridian_booking_owner USING (true);

GRANT CREATE ON SCHEMA public TO meridian_booking_owner;
ALTER FUNCTION create_courtesy_hold(
    TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER, NUMERIC, NUMERIC, TIMESTAMPTZ
) OWNER TO meridian_booking_owner;
ALTER FUNCTION confirm_booking(TEXT, TEXT, NUMERIC) OWNER TO meridian_booking_owner;
ALTER FUNCTION release_courtesy_hold(TEXT, TEXT, TEXT) OWNER TO meridian_booking_owner;
REVOKE CREATE ON SCHEMA public FROM meridian_booking_owner;
