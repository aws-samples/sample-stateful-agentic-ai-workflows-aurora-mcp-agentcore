-- Give Meridian a distinct fictional traveler while keeping the stable identity,
-- saved trips, preferences, authorizations, and recovery checkpoints intact.
-- Historical conversations and audit records retain what was actually recorded.
UPDATE travelers
SET full_name = 'Jordan Morgan',
    email = 'jordan.morgan@example.com'
WHERE traveler_id = 'trv_meridian_demo';
