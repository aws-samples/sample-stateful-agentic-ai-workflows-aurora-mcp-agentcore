-- Make courtesy holds available on existing databases without recreating data.

ALTER TABLE bookings
    ADD COLUMN IF NOT EXISTS hold_expires_at TIMESTAMPTZ;

CREATE OR REPLACE FUNCTION create_courtesy_hold(
    p_booking_id TEXT,
    p_traveler_id TEXT,
    p_package_id TEXT,
    p_duration TEXT,
    p_quantity INTEGER,
    p_unit_price NUMERIC,
    p_total_amount NUMERIC,
    p_hold_expires_at TIMESTAMPTZ
) RETURNS TABLE (
    seats_available INTEGER,
    seats_reserved INTEGER,
    seats_remaining INTEGER
)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
DECLARE
    v_scope TEXT := current_setting('app.current_traveler_id', true);
    v_agent_type TEXT := current_setting('app.agent_type', true);
    v_capacity INTEGER;
    v_reserved INTEGER;
BEGIN
    IF v_scope IS NULL OR v_scope = '' OR v_scope <> p_traveler_id THEN
        RAISE EXCEPTION 'traveler_scope_mismatch';
    END IF;
    IF v_agent_type NOT IN ('booking_agent', 'supervisor_agent', 'concierge_agent') THEN
        RAISE EXCEPTION 'booking_agent_not_authorized';
    END IF;
    IF p_quantity IS NULL OR p_quantity <= 0 THEN
        RAISE EXCEPTION 'invalid_hold_quantity';
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtextextended(p_package_id || ':' || p_duration, 0)
    );

    SELECT CASE
        WHEN jsonb_typeof(availability -> p_duration) = 'number'
        THEN (availability ->> p_duration)::INTEGER
        ELSE NULL
    END
    INTO v_capacity
    FROM trip_packages
    WHERE package_id = p_package_id
      AND durations ? p_duration;

    IF v_capacity IS NULL OR v_capacity < 0 THEN
        RAISE EXCEPTION 'invalid_package_inventory';
    END IF;

    SELECT COALESCE(SUM(bl.travelers_count), 0)::INTEGER
    INTO v_reserved
    FROM booking_lines bl
    JOIN bookings b ON b.booking_id = bl.booking_id
    WHERE bl.package_id = p_package_id
      AND bl.duration = p_duration
      AND (
          b.status = 'confirmed'
          OR (
              b.status = 'held'
              AND b.hold_expires_at > CURRENT_TIMESTAMP
          )
      );

    IF p_quantity > (v_capacity - v_reserved) THEN
        RAISE EXCEPTION 'insufficient_inventory';
    END IF;

    INSERT INTO bookings (
        booking_id, traveler_id, status, total_amount,
        hold_expires_at, created_at
    ) VALUES (
        p_booking_id, p_traveler_id, 'held', p_total_amount,
        p_hold_expires_at, CURRENT_TIMESTAMP
    );

    INSERT INTO booking_lines (
        booking_id, package_id, duration, travelers_count, unit_price
    ) VALUES (
        p_booking_id, p_package_id, p_duration, p_quantity, p_unit_price
    );

    RETURN QUERY SELECT
        v_capacity,
        v_reserved + p_quantity,
        v_capacity - v_reserved - p_quantity;
END;
$$;

REVOKE ALL ON FUNCTION create_courtesy_hold(
    TEXT, TEXT, TEXT, TEXT, INTEGER, NUMERIC, NUMERIC, TIMESTAMPTZ
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION create_courtesy_hold(
    TEXT, TEXT, TEXT, TEXT, INTEGER, NUMERIC, NUMERIC, TIMESTAMPTZ
) TO meridian_app;
