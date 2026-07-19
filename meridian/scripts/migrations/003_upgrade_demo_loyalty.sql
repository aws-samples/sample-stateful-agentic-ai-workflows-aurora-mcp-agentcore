-- Upgrade the seeded demo traveler from compact tier strings to a stable
-- profile contract used by the MCP loyalty tool and journey UI.
UPDATE traveler_profiles
SET loyalty_programs = jsonb_build_object(
        'united_mileageplus', jsonb_build_object(
            'program', 'United MileagePlus',
            'member_id', 'MP-xx7314',
            'tier', 'Premier 1K',
            'points_balance', 124600
        ),
        'marriott_bonvoy', jsonb_build_object(
            'program', 'Marriott Bonvoy',
            'member_id', 'MB-xx4821',
            'tier', 'Platinum Elite',
            'points_balance', 86240
        )
    ),
    updated_at = CURRENT_TIMESTAMP
WHERE traveler_id = 'trv_meridian_demo';

UPDATE traveler_preferences
SET preference_value =
        'Marriott Bonvoy Platinum Elite; United MileagePlus Premier 1K',
    confidence = 0.96,
    source = 'profile',
    last_seen_at = CURRENT_TIMESTAMP
WHERE traveler_id = 'trv_meridian_demo'
  AND preference_type = 'loyalty'
  AND preference_key = 'loyalty_programs';
