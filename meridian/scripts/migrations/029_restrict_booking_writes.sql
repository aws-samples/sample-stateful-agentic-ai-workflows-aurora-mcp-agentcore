-- Contract after the Gateway Lambda uses the functions and its own EXECUTE
-- grant. Deploy that code after 027 and 028, verify it, then apply this file.
-- The common app role can read scoped business rows but cannot change them.

REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON bookings, booking_lines, hold_requests
    FROM PUBLIC, meridian_app, meridian_backend, meridian_workflow, meridian_gateway;

REVOKE ALL ON FUNCTION create_courtesy_hold(
    TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER, NUMERIC, NUMERIC, TIMESTAMPTZ
) FROM PUBLIC, meridian_app, meridian_backend, meridian_workflow;
REVOKE ALL ON FUNCTION confirm_booking(TEXT, TEXT, NUMERIC)
    FROM PUBLIC, meridian_app, meridian_backend, meridian_workflow;
REVOKE ALL ON FUNCTION release_courtesy_hold(TEXT, TEXT, TEXT)
    FROM PUBLIC, meridian_app, meridian_backend, meridian_workflow;
