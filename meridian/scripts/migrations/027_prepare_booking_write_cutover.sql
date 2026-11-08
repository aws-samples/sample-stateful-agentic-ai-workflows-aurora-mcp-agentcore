-- Expand before the Gateway Lambda cutover: add the bounded release and give
-- its login EXECUTE. Existing writers still work until migration 029 revokes
-- the common role's old privileges after the new Lambda is deployed.

CREATE OR REPLACE FUNCTION release_courtesy_hold(
    p_journey_id TEXT, p_hold_request_id TEXT, p_traveler_id TEXT
) RETURNS TABLE (booking_id TEXT, replayed BOOLEAN)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_scope TEXT := current_setting('app.current_traveler_id', true);
    v_agent_type TEXT := current_setting('app.agent_type', true);
    v_booking RECORD;
BEGIN
    IF v_scope IS NULL OR v_scope = '' OR v_scope IS DISTINCT FROM p_traveler_id THEN
        RAISE EXCEPTION 'traveler_scope_mismatch';
    END IF;
    IF COALESCE(v_agent_type, '') NOT IN ('booking_agent', 'supervisor_agent', 'concierge_agent') THEN
        RAISE EXCEPTION 'booking_agent_not_authorized';
    END IF;
    SELECT b.booking_id, b.status INTO v_booking
      FROM hold_requests hr
      JOIN journeys j ON j.journey_id = hr.journey_id
      JOIN bookings b ON b.booking_id = hr.booking_id
     WHERE hr.journey_id = p_journey_id AND hr.hold_request_id = p_hold_request_id
       AND j.traveler_id = p_traveler_id AND b.traveler_id = p_traveler_id
       FOR UPDATE OF b;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'hold_not_found';
    END IF;
    IF v_booking.status = 'released' THEN
        RETURN QUERY SELECT v_booking.booking_id::TEXT, TRUE;
        RETURN;
    END IF;
    IF v_booking.status <> 'held' THEN
        RAISE EXCEPTION 'booking_not_held';
    END IF;
    UPDATE bookings b SET status = 'released' WHERE b.booking_id = v_booking.booking_id;
    RETURN QUERY SELECT v_booking.booking_id::TEXT, FALSE;
END;
$$;

REVOKE ALL ON FUNCTION release_courtesy_hold(TEXT, TEXT, TEXT)
    FROM PUBLIC, meridian_app, meridian_backend, meridian_workflow;

GRANT EXECUTE ON FUNCTION create_courtesy_hold(
    TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER, NUMERIC, NUMERIC, TIMESTAMPTZ
) TO meridian_gateway;
GRANT EXECUTE ON FUNCTION confirm_booking(TEXT, TEXT, NUMERIC) TO meridian_gateway;
GRANT EXECUTE ON FUNCTION release_courtesy_hold(TEXT, TEXT, TEXT) TO meridian_gateway;
