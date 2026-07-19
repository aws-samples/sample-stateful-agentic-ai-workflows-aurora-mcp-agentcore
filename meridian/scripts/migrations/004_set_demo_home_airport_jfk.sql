-- Standardize the seeded demo traveler on JFK without disturbing the RLS decoy
-- traveler or any unrelated production-style demo history.
UPDATE travelers
SET full_name = 'Alex Morgan',
    email = 'alex.morgan@example.com',
    home_airport = 'JFK'
WHERE traveler_id = 'trv_meridian_demo';

INSERT INTO traveler_preferences (
    preference_id,
    traveler_id,
    preference_type,
    preference_key,
    preference_value,
    confidence,
    signal_count,
    source
)
SELECT
    'pref_demo_home_airport',
    'trv_meridian_demo',
    'logistics',
    'home_airport',
    'JFK',
    1.0,
    1,
    'profile'
WHERE EXISTS (
    SELECT 1
    FROM travelers
    WHERE traveler_id = 'trv_meridian_demo'
)
ON CONFLICT (traveler_id, preference_type, preference_key) DO UPDATE SET
    preference_value = EXCLUDED.preference_value,
    confidence = EXCLUDED.confidence,
    source = EXCLUDED.source,
    last_seen_at = CURRENT_TIMESTAMP;

INSERT INTO traveler_preferences (
    preference_id,
    traveler_id,
    preference_type,
    preference_key,
    preference_value,
    confidence,
    signal_count,
    source
)
SELECT
    'pref_demo_avoid_connections',
    'trv_meridian_demo',
    'logistics',
    'avoid_connections',
    'LHR, EWR',
    0.87,
    1,
    'booking_history'
WHERE EXISTS (
    SELECT 1
    FROM travelers
    WHERE traveler_id = 'trv_meridian_demo'
)
ON CONFLICT (traveler_id, preference_type, preference_key) DO UPDATE SET
    preference_value = EXCLUDED.preference_value,
    confidence = EXCLUDED.confidence,
    source = EXCLUDED.source,
    last_seen_at = CURRENT_TIMESTAMP;
