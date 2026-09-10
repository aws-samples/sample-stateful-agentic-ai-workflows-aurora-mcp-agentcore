-- Turn a held booking into a confirmed one, once, for the traveler who holds it.
--
-- Confirmation books catalog inventory in this demo database. No supplier is
-- contacted and no payment is taken. The function is SECURITY DEFINER like
-- create_courtesy_hold, so the application role needs nothing beyond EXECUTE,
-- and it repeats the scope checks the RLS policies make so a caller cannot
-- confirm a booking outside its pinned traveler scope.

ALTER TABLE bookings
    ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMP;

CREATE OR REPLACE FUNCTION confirm_booking(
    p_booking_id TEXT,
    p_traveler_id TEXT,
    p_total_amount NUMERIC
) RETURNS TABLE (
    booking_id TEXT,
    status TEXT,
    replayed BOOLEAN,
    confirmed_at TIMESTAMPTZ,
    hold_expires_at TIMESTAMPTZ,
    total_amount NUMERIC
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_scope TEXT := current_setting('app.current_traveler_id', true);
    v_agent_type TEXT := current_setting('app.agent_type', true);
    v_booking RECORD;
BEGIN
    IF v_scope IS NULL OR v_scope = '' OR v_scope <> p_traveler_id THEN
        RAISE EXCEPTION 'traveler_scope_mismatch';
    END IF;
    IF v_agent_type NOT IN ('booking_agent', 'supervisor_agent', 'concierge_agent') THEN
        RAISE EXCEPTION 'booking_agent_not_authorized';
    END IF;

    SELECT b.booking_id, b.status, b.total_amount, b.hold_expires_at,
           b.confirmed_at::TIMESTAMPTZ AS confirmed_at
      INTO v_booking
      FROM bookings b
     WHERE b.booking_id = p_booking_id AND b.traveler_id = p_traveler_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'booking_not_found';
    END IF;

    -- The total the traveler confirmed must be the total Aurora holds. The
    -- policy compared that number against the budget ceiling.
    IF v_booking.total_amount IS DISTINCT FROM p_total_amount THEN
        RAISE EXCEPTION 'booking_amount_mismatch';
    END IF;

    -- A retried confirmation reports the original record and changes nothing.
    IF v_booking.status = 'confirmed' THEN
        RETURN QUERY SELECT
            v_booking.booking_id::TEXT, 'confirmed'::TEXT, TRUE,
            v_booking.confirmed_at, v_booking.hold_expires_at, v_booking.total_amount;
        RETURN;
    END IF;
    IF v_booking.status <> 'held' THEN
        RAISE EXCEPTION 'booking_not_held';
    END IF;
    IF v_booking.hold_expires_at IS NULL OR v_booking.hold_expires_at <= CURRENT_TIMESTAMP THEN
        RAISE EXCEPTION 'hold_expired';
    END IF;

    UPDATE bookings b
       SET status = 'confirmed', confirmed_at = CURRENT_TIMESTAMP
     WHERE b.booking_id = p_booking_id;

    RETURN QUERY SELECT
        v_booking.booking_id::TEXT, 'confirmed'::TEXT, FALSE,
        CURRENT_TIMESTAMP::TIMESTAMPTZ, v_booking.hold_expires_at, v_booking.total_amount;
END;
$$;

REVOKE ALL ON FUNCTION confirm_booking(TEXT, TEXT, NUMERIC) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION confirm_booking(TEXT, TEXT, NUMERIC) TO meridian_app;
