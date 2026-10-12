-- One hold per intended request, enforced inside the inventory transaction.
--
-- The old eight-argument function is dropped rather than replaced. CREATE OR
-- REPLACE with a changed parameter list creates an overload, which would leave
-- a callable path that bypasses idempotency.

-- Guarded so the migration can be re-run: once the old function is dropped,
-- a bare REVOKE against its signature raises undefined_function.
DO $$
BEGIN
    EXECUTE 'REVOKE ALL ON FUNCTION create_courtesy_hold(TEXT, TEXT, TEXT, TEXT, INTEGER, NUMERIC, NUMERIC, TIMESTAMPTZ) FROM meridian_app';
EXCEPTION WHEN undefined_function THEN
    NULL;
END
$$;

DROP FUNCTION IF EXISTS create_courtesy_hold(
    TEXT, TEXT, TEXT, TEXT, INTEGER, NUMERIC, NUMERIC, TIMESTAMPTZ
);

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
          OR (b.status = 'held' AND b.hold_expires_at > CURRENT_TIMESTAMP)
      );

    IF p_quantity > (v_capacity - v_reserved) THEN
        RAISE EXCEPTION 'insufficient_inventory';
    END IF;

    INSERT INTO bookings (
        booking_id, traveler_id, status, total_amount, hold_expires_at, created_at
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

-- Link legacy holds only where the mapping is unambiguous: exactly one active
-- held booking for a traveler who has exactly one journey. Anything else is
-- left unlinked rather than guessed, because a wrong link would let a resume
-- adopt a hold it cannot prove belongs to the request.
INSERT INTO hold_requests (
    journey_id, hold_request_id, booking_id, fingerprint, thread_id
)
SELECT j.journey_id,
       'hrq_legacy_' || b.booking_id,
       b.booking_id,
       'legacy:unverified',
       COALESCE(j.active_thread_id, '')
  FROM bookings b
  JOIN journeys j ON j.traveler_id = b.traveler_id
 WHERE b.status = 'held'
   AND NOT EXISTS (
       SELECT 1 FROM hold_requests hr WHERE hr.booking_id = b.booking_id
   )
   AND (SELECT COUNT(*) FROM journeys j2 WHERE j2.traveler_id = b.traveler_id) = 1
   AND (SELECT COUNT(*) FROM bookings b2
         WHERE b2.traveler_id = b.traveler_id AND b2.status = 'held') = 1
ON CONFLICT (journey_id, hold_request_id) DO NOTHING;
