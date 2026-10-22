-- 020: places left per duration, counted the way a hold counts them.
--
-- trip_packages.availability holds each duration's capacity. create_courtesy_hold
-- (migration 008) admits a hold only when capacity minus confirmed bookings and
-- unexpired holds covers it, and it never decrements the column. A read of the raw
-- column therefore keeps advertising places that are already reserved.
--
-- package_places_left returns the column's shape with every counted duration reduced
-- by every traveler's confirmed bookings and unexpired holds, never below zero.
-- Released, expired and lapsed holds count nothing. A value that is not a number, or
-- an availability that is not an object, comes back unchanged. bookings and
-- booking_lines force row level security per traveler, so the function runs as its
-- owner, as create_courtesy_hold does, and it returns counts only.
CREATE OR REPLACE FUNCTION package_places_left(p_package_id TEXT)
RETURNS JSONB
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    SELECT CASE
        WHEN jsonb_typeof(p.availability) <> 'object' THEN p.availability
        ELSE COALESCE((
            SELECT jsonb_object_agg(
                capacity.duration,
                CASE
                    WHEN jsonb_typeof(capacity.places) = 'number' THEN to_jsonb(GREATEST(
                        (capacity.places #>> '{}')::INTEGER - COALESCE((
                            SELECT SUM(bl.travelers_count)
                              FROM booking_lines bl
                              JOIN bookings b ON b.booking_id = bl.booking_id
                             WHERE bl.package_id = p.package_id
                               AND bl.duration = capacity.duration
                               AND (
                                   b.status = 'confirmed'
                                   OR (b.status = 'held'
                                       AND b.hold_expires_at > CURRENT_TIMESTAMP)
                               )
                        ), 0)::INTEGER,
                        0
                    ))
                    ELSE capacity.places
                END
            )
              FROM jsonb_each(p.availability) AS capacity(duration, places)
        ), '{}'::JSONB)
    END
      FROM trip_packages p
     WHERE p.package_id = p_package_id
$$;

REVOKE ALL ON FUNCTION package_places_left(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION package_places_left(TEXT)
    TO meridian_app, meridian_backend, meridian_gateway, meridian_workflow;
