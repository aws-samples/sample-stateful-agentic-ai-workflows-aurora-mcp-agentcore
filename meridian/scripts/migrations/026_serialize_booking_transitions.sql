-- Serialize confirmation and allocation on the same package/duration lock.
-- Expiry uses wall-clock time after waiting for locks, never transaction start.
-- Missing agent scope is rejected explicitly in both business functions.
-- This replaces definitions, not historical migration files; apply transactionally.

CREATE OR REPLACE FUNCTION create_courtesy_hold(
    p_booking_id TEXT,
    p_traveler_id TEXT,
    p_journey_id TEXT,
    p_hold_request_id TEXT,
    p_fingerprint TEXT,
    p_package_id TEXT,
    p_duration TEXT,
    p_quantity INTEGER,
    p_unit_price NUMERIC,
    p_total_amount NUMERIC,
    p_hold_expires_at TIMESTAMPTZ
) RETURNS TABLE (
    booking_id TEXT,
    status TEXT,
    replayed BOOLEAN,
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
    v_inserted BOOLEAN;
    v_existing RECORD;
    v_now TIMESTAMPTZ;
BEGIN
    IF v_scope IS NULL OR v_scope = '' OR v_scope <> p_traveler_id THEN
        RAISE EXCEPTION 'traveler_scope_mismatch';
    END IF;
    IF COALESCE(v_agent_type, '') NOT IN ('booking_agent', 'supervisor_agent', 'concierge_agent') THEN
        RAISE EXCEPTION 'booking_agent_not_authorized';
    END IF;
    IF p_quantity IS NULL OR p_quantity <= 0 THEN
        RAISE EXCEPTION 'invalid_hold_quantity';
    END IF;

    -- Authorize the journey before either path. Knowing a request id must not
    -- return another traveler's booking.
    PERFORM 1 FROM journeys
     WHERE journey_id = p_journey_id AND traveler_id = p_traveler_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'journey_not_owned';
    END IF;

    INSERT INTO hold_requests (
        journey_id, hold_request_id, booking_id, fingerprint, thread_id,
        execution_id
    ) VALUES (
        p_journey_id, p_hold_request_id, p_booking_id, p_fingerprint,
        COALESCE(current_setting('app.thread_id', true), ''),
        NULLIF(current_setting('app.execution_id', true), '')
    )
    ON CONFLICT (journey_id, hold_request_id) DO NOTHING;

    GET DIAGNOSTICS v_inserted = ROW_COUNT;

    IF NOT v_inserted THEN
        SELECT hr.booking_id, hr.fingerprint, b.status
          INTO v_existing
          FROM hold_requests hr
          JOIN bookings b ON b.booking_id = hr.booking_id
         WHERE hr.journey_id = p_journey_id
           AND hr.hold_request_id = p_hold_request_id;

        IF v_existing.fingerprint IS DISTINCT FROM p_fingerprint THEN
            RAISE EXCEPTION 'hold_request_parameter_mismatch';
        END IF;

        -- The columns are VARCHAR on the tables and TEXT in the OUT list, and
        -- RETURN QUERY requires an exact type match, so cast explicitly. The
        -- non-replay path returns the TEXT parameter and never hit this.
        RETURN QUERY SELECT
            v_existing.booking_id::TEXT, v_existing.status::TEXT, TRUE,
            NULL::INTEGER, NULL::INTEGER, NULL::INTEGER;
        RETURN;
    END IF;

    PERFORM pg_advisory_xact_lock(
        hashtextextended(p_package_id || ':' || p_duration, 0)
    );

    v_now := clock_timestamp();
    IF p_hold_expires_at IS NULL OR p_hold_expires_at <= v_now THEN
        RAISE EXCEPTION 'hold_expired';
    END IF;

    SELECT CASE
        WHEN jsonb_typeof(availability -> p_duration) = 'number'
        THEN (availability ->> p_duration)::INTEGER
        ELSE NULL
    END
    INTO v_capacity
    FROM trip_packages
    WHERE package_id = p_package_id AND durations ? p_duration;

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
          OR (b.status = 'held' AND b.hold_expires_at > v_now)
      );

    IF p_quantity > (v_capacity - v_reserved) THEN
        RAISE EXCEPTION 'insufficient_inventory';
    END IF;

    INSERT INTO bookings (
        booking_id, traveler_id, status, total_amount, hold_expires_at, created_at
    ) VALUES (
        p_booking_id, p_traveler_id, 'held', p_total_amount,
        p_hold_expires_at, v_now
    );

    INSERT INTO booking_lines (
        booking_id, package_id, duration, travelers_count, unit_price
    ) VALUES (
        p_booking_id, p_package_id, p_duration, p_quantity, p_unit_price
    );

    RETURN QUERY SELECT
        p_booking_id, 'held'::TEXT, FALSE,
        v_capacity, v_reserved + p_quantity, v_capacity - v_reserved - p_quantity;
END;
$$;

REVOKE ALL ON FUNCTION create_courtesy_hold(
    TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER, NUMERIC, NUMERIC, TIMESTAMPTZ
) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION create_courtesy_hold(
    TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER, NUMERIC, NUMERIC, TIMESTAMPTZ
) TO meridian_app;


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
    v_line RECORD;
    v_now TIMESTAMPTZ;
BEGIN
    IF v_scope IS NULL OR v_scope = '' OR v_scope <> p_traveler_id THEN
        RAISE EXCEPTION 'traveler_scope_mismatch';
    END IF;
    IF COALESCE(v_agent_type, '') NOT IN ('booking_agent', 'supervisor_agent', 'concierge_agent') THEN
        RAISE EXCEPTION 'booking_agent_not_authorized';
    END IF;

    -- Use the same inventory locks as hold creation, before the booking row lock.
    -- A confirmation keeps its places reserved until its transaction ends, even if
    -- the old hold expires while the confirmation's commit is in flight.
    FOR v_line IN
        SELECT bl.package_id, bl.duration
          FROM booking_lines bl JOIN bookings b ON b.booking_id = bl.booking_id
         WHERE b.booking_id = p_booking_id AND b.traveler_id = p_traveler_id
         ORDER BY bl.package_id, bl.duration
    LOOP
        PERFORM pg_advisory_xact_lock(
            hashtextextended(v_line.package_id || ':' || v_line.duration, 0)
        );
    END LOOP;

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
    v_now := clock_timestamp();
    IF v_booking.hold_expires_at IS NULL OR v_booking.hold_expires_at <= v_now THEN
        RAISE EXCEPTION 'hold_expired';
    END IF;

    UPDATE bookings b
       SET status = 'confirmed', confirmed_at = v_now
     WHERE b.booking_id = p_booking_id;

    RETURN QUERY SELECT
        v_booking.booking_id::TEXT, 'confirmed'::TEXT, FALSE,
        v_now, v_booking.hold_expires_at, v_booking.total_amount;
END;
$$;

REVOKE ALL ON FUNCTION confirm_booking(TEXT, TEXT, NUMERIC) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION confirm_booking(TEXT, TEXT, NUMERIC) TO meridian_app;
