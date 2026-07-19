"""
Meridian trip catalog — 30 packages across 6 trip types.
Native travel fields (no product-catalog retrofit).
"""

def _img(photo_id: str) -> str:
    return (
        f"https://images.unsplash.com/photo-{photo_id}"
        "?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&h=600&q=80"
    )


def _pkg(
    package_id: str,
    name: str,
    trip_type: str,
    destination: str,
    region: str,
    price: float,
    operator: str,
    description: str,
    image_id: str,
    durations: list[str],
    availability: dict[str, int],
    highlights: list[str] | None = None,
) -> dict:
    return {
        "package_id": package_id,
        "name": name,
        "trip_type": trip_type,
        "destination": destination,
        "region": region,
        "price_per_person": price,
        "operator": operator,
        "description": description,
        "image_url": _img(image_id),
        "durations": durations,
        "availability": availability,
        "highlights": highlights or [],
    }


TRIP_PACKAGES = [
    _pkg("CTY-001", "Paris Long Weekend", "City Breaks", "Paris", "Europe", 1899.0,
         "Air France Vacations",
         "Four nights in Le Marais with Louvre tickets, Seine dinner cruise, and Montmartre food walk.",
         "1502602898657-3e91760cbb34", ["3 nights", "4 nights", "5 nights"],
         {"3 nights": 8, "4 nights": 12, "5 nights": 6}, ["museum passes", "food tour"]),
    _pkg("CTY-002", "Tokyo Culture & Cuisine", "City Breaks", "Tokyo", "Asia-Pacific", 2499.0,
         "ANA Holidays",
         "Shibuya base, Tsukiji breakfast tour, teamLab, and day trip to Hakone. Rail pass included.",
         "1540959733332-eab4deabeeaf", ["5 nights", "7 nights"],
         {"5 nights": 10, "7 nights": 7}, ["rail pass", "kaiseki dinner"]),
    _pkg("CTY-003", "New York City Explorer", "City Breaks", "New York", "North America", 1699.0,
         "Delta Vacations",
         "Midtown hotel, Broadway show credit, High Line walk, and Brooklyn day pass.",
         "1496442226666-8d4d0e62e6e9", ["3 nights", "4 nights"],
         {"3 nights": 15, "4 nights": 9}),
    _pkg("CTY-004", "Barcelona Architecture Week", "City Breaks", "Barcelona", "Europe", 1599.0,
         "Iberia Escapes",
         "Gothic Quarter boutique stay, Sagrada Família skip-the-line, tapas tour.",
         "1449824913935-59a10b8d2000", ["4 nights", "6 nights"],
         {"4 nights": 11, "6 nights": 5}),
    _pkg("CTY-005", "Rome Ancient & Modern", "City Breaks", "Rome", "Europe", 1749.0,
         "Alitalia Tours",
         "Colosseum and Vatican small-group tours, Trastevere evenings, Tuscan wine day trip.",
         "1552832230-c0197dd311b5", ["4 nights", "5 nights"],
         {"4 nights": 8, "5 nights": 6}),
    _pkg("BCH-001", "Maldives Overwater Villa", "Beach & Resort", "Malé Atolls", "Indian Ocean", 4299.0,
         "Emirates Holidays",
         "Five nights overwater bungalow, seaplane transfer, snorkeling, sunset dolphin cruise.",
         "1514282401047-d79a71a590e8", ["5 nights", "7 nights"],
         {"5 nights": 4, "7 nights": 3}),
    _pkg("BCH-002", "Cancún All-Inclusive Escape", "Beach & Resort", "Cancún", "Caribbean", 2199.0,
         "Sunwing",
         "Beachfront resort, meals and drinks included, Chichen Itza excursion optional.",
         "1507525428034-b723cf961d3e", ["5 nights", "7 nights"],
         {"5 nights": 20, "7 nights": 14}),
    _pkg("BCH-003", "Bali Rice Terrace Retreat", "Beach & Resort", "Ubud", "Asia-Pacific", 1899.0,
         "Garuda Getaways",
         "Ubud villa, yoga mornings, temple tour, and Seminyak beach club day.",
         "1528181304800-259b08848526", ["6 nights", "8 nights"],
         {"6 nights": 9, "8 nights": 6}),
    _pkg("BCH-004", "Santorini Caldera Views", "Beach & Resort", "Santorini", "Europe", 2799.0,
         "Aegean Blue",
         "Cave suite in Oia, catamaran sunset sail, winery tasting in volcanic soil.",
         "1613395877344-13d4a8e0d49e", ["4 nights", "6 nights"],
         {"4 nights": 7, "6 nights": 4}),
    _pkg("BCH-005", "Hawaiian Island Hopper", "Beach & Resort", "Oahu & Maui", "Pacific", 3199.0,
         "Hawaiian Airlines",
         "Split stay, luau, Pearl Harbor tour, and Road to Hana drive day.",
         "1559827260-dc66d52bef19", ["7 nights", "10 nights"],
         {"7 nights": 8, "10 nights": 5}),
    _pkg("ADV-001", "Patagonia Trek Expedition", "Adventure & Outdoors", "Torres del Paine", "South America", 3899.0,
         "REI Adventures",
         "Guided W Trek highlights, refugio lodging, gear list provided.",
         "1516026672322-bc52d61a55d5", ["8 nights", "10 nights"],
         {"8 nights": 6, "10 nights": 4}),
    _pkg("ADV-002", "Iceland Ring Road", "Adventure & Outdoors", "Reykjavik", "Europe", 2799.0,
         "Nordic Trails",
         "4x4 camper or guided mini-bus, glacier hike, Blue Lagoon, northern lights chase.",
         "1439066615861-d1af74d74000", ["6 nights", "8 nights"],
         {"6 nights": 10, "8 nights": 7}),
    _pkg("ADV-003", "Costa Rica Rainforest & Zip", "Adventure & Outdoors", "Arenal", "Central America", 2199.0,
         "EcoVenture",
         "Volcano lodge, canopy zip lines, wildlife night walk, Pacific beach finale.",
         "1501785888041-af3ef285b470", ["7 nights", "9 nights"],
         {"7 nights": 12, "9 nights": 8}),
    _pkg("ADV-004", "New Zealand South Island", "Adventure & Outdoors", "Queenstown", "Oceania", 3599.0,
         "Air New Zealand",
         "Queenstown adrenaline, Milford Sound cruise, Franz Josef glacier heli-hike option.",
         "1506905925346-21bda4d32df4", ["10 nights", "12 nights"],
         {"10 nights": 5, "12 nights": 3}),
    _pkg("ADV-005", "Nepal Everest Base Camp Trek", "Adventure & Outdoors", "Khumbu", "Asia", 3299.0,
         "Himalaya Guides",
         "Teahouse trek with acclimatization days, permits included.",
         "1544735716-392fe2489ffa", ["14 nights"],
         {"14 nights": 4}),
    _pkg("WEL-001", "Swiss Alps Spa Retreat", "Wellness & Luxury", "Zermatt", "Europe", 4499.0,
         "Swissôtel",
         "Alpine thermal spa, daily treatments, mountain air hikes, gourmet half-board.",
         "1519681393784-d120267933ba", ["5 nights", "7 nights"],
         {"5 nights": 6, "7 nights": 4}),
    _pkg("WEL-002", "Amalfi Coast Villa Week", "Wellness & Luxury", "Positano", "Europe", 3999.0,
         "Belmond",
         "Cliffside villa, private boat day, Positano dining, Ravello concert evening.",
         "1570077188670-e3a8d69ac5ff", ["6 nights", "8 nights"],
         {"6 nights": 3, "8 nights": 2}),
    _pkg("WEL-003", "Dubai Luxury Stopover", "Wellness & Luxury", "Dubai", "Middle East", 2899.0,
         "Emirates",
         "Burj view suite, desert safari, spa day, fine dining credit at DIFC.",
         "1518684079-3c830dcef090", ["3 nights", "5 nights"],
         {"3 nights": 14, "5 nights": 9}),
    _pkg("WEL-004", "Kyoto Ryokan & Onsen", "Wellness & Luxury", "Kyoto", "Asia-Pacific", 3299.0,
         "JAL Packages",
         "Traditional ryokan, kaiseki dinners, tea ceremony, bamboo forest morning walk.",
         "1571896349842-33c89424de2d", ["5 nights", "7 nights"],
         {"5 nights": 7, "7 nights": 5}),
    _pkg("WEL-005", "Tuscany Wine & Wellness", "Wellness & Luxury", "Chianti", "Europe", 3699.0,
         "Trafalgar",
         "Villa stay, vineyard tours, cooking class, truffle season optional add-on.",
         "1523531294919-4bcd7c65e216", ["6 nights", "8 nights"],
         {"6 nights": 5, "8 nights": 3}),
    _pkg("FAM-001", "Orlando Theme Park Week", "Family Trips", "Orlando", "North America", 2499.0,
         "Disney Travel",
         "Resort hotel, multi-park tickets, character breakfast, pool days built in.",
         "1520250497591-112f2f40a3f4", ["5 nights", "7 nights"],
         {"5 nights": 18, "7 nights": 12}),
    _pkg("FAM-002", "Yellowstone Wildlife Safari", "Family Trips", "Yellowstone", "North America", 2799.0,
         "National Park Tours",
         "Lodge near park gate, guided wildlife drives, junior ranger program, geysers day.",
         "1500534314209-a25ddb2bd429", ["6 nights", "8 nights"],
         {"6 nights": 8, "8 nights": 5}),
    _pkg("FAM-003", "Mediterranean Family Cruise", "Family Trips", "Western Mediterranean", "Europe", 3199.0,
         "Royal Caribbean",
         "Balcony cabin, kids club, Rome and Barcelona ports, all-inclusive shipboard dining.",
         "1566073771259-6a8506099945", ["7 nights"],
         {"7 nights": 22}),
    _pkg("FAM-004", "Costa del Sol Beach Club", "Family Trips", "Marbella", "Europe", 1999.0,
         "TUI Family",
         "Family suite, kids pool, beach access, optional water park passes.",
         "1507525428034-b723cf961d3e", ["7 nights", "10 nights"],
         {"7 nights": 16, "10 nights": 10}),
    _pkg("FAM-005", "London & Harry Potter Studio", "Family Trips", "London", "Europe", 2299.0,
         "British Airways",
         "Central London stay, studio tour tickets, Thames river cruise, museum day.",
         "1513635269975-59663e0ac1ad", ["5 nights", "6 nights"],
         {"5 nights": 11, "6 nights": 7}),
    _pkg("BIZ-001", "London Executive Quick Trip", "Business Travel", "London", "Europe", 1499.0,
         "British Airways",
         "City airport hotel, lounge access, express train credit, flexible change policy.",
         "1513635269975-59663e0ac1ad", ["2 nights", "3 nights"],
         {"2 nights": 25, "3 nights": 18}),
    _pkg("BIZ-002", "Singapore Hub Stopover", "Business Travel", "Singapore", "Asia-Pacific", 1299.0,
         "Singapore Airlines",
         "Changi-area hotel, fast Wi‑Fi, meeting room credit, Gardens by the Bay evening.",
         "1524231757912-21f4fe3a7200", ["2 nights", "3 nights"],
         {"2 nights": 30, "3 nights": 20}),
    _pkg("BIZ-003", "Dubai Conference Package", "Business Travel", "Dubai", "Middle East", 1899.0,
         "Emirates Business",
         "DWC-adjacent hotel, conference shuttle, late checkout, visa assistance.",
         "1542314831-068cd1dbfeeb", ["3 nights", "4 nights"],
         {"3 nights": 14, "4 nights": 9}),
    _pkg("BIZ-004", "NYC Meetings Marathon", "Business Travel", "New York", "North America", 1699.0,
         "United Business",
         "Midtown business hotel, car service credit, quiet-floor room, same-day laundry.",
         "1485871981521-5b1fd3805eee", ["3 nights", "5 nights"],
         {"3 nights": 20, "5 nights": 12}),
    _pkg("BIZ-005", "Frankfurt Trade Fair Stay", "Business Travel", "Frankfurt", "Europe", 1399.0,
         "Lufthansa",
         "Messe-connected hotel, fair shuttle pass, early breakfast, rail pass to city center.",
         "1488646953014-85cb44e25828", ["3 nights", "4 nights"],
         {"3 nights": 16, "4 nights": 10}),

    # ---- Extra Tokyo coverage ----------------------------------------
    # Lets seasonal_price_band("Tokyo", ...) return a real low/median/high
    # spread instead of three identical numbers, and gives the stratified
    # compare a Tokyo entry in multiple trip_types if the demo focuses
    # there.
    _pkg("TKY-001", "Tokyo Indie Neighborhood Walk", "City Breaks", "Tokyo", "Asia-Pacific", 1599.0,
         "JAL Tours",
         "Yanaka and Shimokitazawa boutique stays, late-morning starts, indie coffee bars,"
         " vintage shops, no group tours. Rail pass included.",
         "1542051841857-5f90071e7989", ["4 nights", "6 nights"],
         {"4 nights": 9, "6 nights": 6}, ["rail pass", "self-guided"]),
    _pkg("TKY-002", "Tokyo Family Discovery Week", "Family Trips", "Tokyo", "Asia-Pacific", 2899.0,
         "ANA Holidays",
         "Family-friendly Asakusa hotel, teamLab Planets, Ueno Zoo, Ghibli Museum tickets,"
         " Pokémon Center half-day, day trip to Yokohama Cup Noodles Museum.",
         "1492571350019-22de08371fd3", ["5 nights", "7 nights"],
         {"5 nights": 7, "7 nights": 5}, ["family rooms", "Ghibli", "kid passes"]),
    _pkg("TKY-003", "Tokyo Executive Stopover", "Business Travel", "Tokyo", "Asia-Pacific", 1949.0,
         "JAL Premium",
         "Marunouchi business hotel, Haneda lounge access, car service to Otemachi,"
         " late check-out, fast Wi-Fi, same-day laundry, optional teamLab evening.",
         "1503899036084-c55cdd92da26", ["2 nights", "3 nights", "4 nights"],
         {"2 nights": 14, "3 nights": 11, "4 nights": 8},
         ["lounge access", "car service", "quiet floor"]),
    _pkg("TKY-004", "Tokyo Ryokan & Onsen Slow Week", "Wellness & Luxury", "Tokyo", "Asia-Pacific", 3899.0,
         "Hoshino Resorts",
         "Hoshinoya ryokan in central Tokyo with private onsen, kaiseki dinners,"
         " day trip to Hakone hot springs, Shinjuku garden tea ceremony, slow pace.",
         "1480796927426-f609979314bd", ["6 nights", "8 nights"],
         {"6 nights": 4, "8 nights": 3},
         ["private onsen", "kaiseki", "tea ceremony"]),
]

DEMO_TRAVELER_ID = "trv_meridian_demo"

TRAVELERS = [
    {
        "traveler_id": DEMO_TRAVELER_ID,
        "full_name": "Alex Morgan",
        "email": "alex.morgan@example.com",
        "home_airport": "JFK",
    },
]

TRAVELER_PROFILES = [
    {
        "traveler_id": DEMO_TRAVELER_ID,
        "party_size": 2,
        "budget_min": 2000.0,
        "budget_max": 3500.0,
        "preferred_cabin": "economy_plus",
        "seat_preference": "Window on short-haul · aisle on long-haul",
        "dietary_notes": "Shellfish allergy — exclude seafood dining",
        "trip_goal": "Tokyo culture trip — target Oct 12–19",
        "loyalty_programs": {
            "united_mileageplus": {
                "program": "United MileagePlus",
                "member_id": "MP••7314",
                "tier": "Premier 1K",
                "points_balance": 124600,
            },
            "marriott_bonvoy": {
                "program": "Marriott Bonvoy",
                "member_id": "MB xxxx4821",
                "tier": "Platinum Elite",
                "points_balance": 86240,
            },
        },
    },
]

TRAVELER_PREFERENCES = [
    # Logistics — flight and seat preferences the Production concierge
    # agent uses to filter recommendations.
    {"preference_type": "logistics", "preference_key": "no_red_eye", "preference_value": "true",
     "confidence": 0.99, "source": "support_ticket"},
    {"preference_type": "logistics", "preference_key": "home_airport", "preference_value": "JFK",
     "confidence": 1.0, "source": "profile"},
    {"preference_type": "logistics", "preference_key": "avoid_connections", "preference_value": "LHR, EWR",
     "confidence": 0.87, "source": "booking_history"},
    {"preference_type": "logistics", "preference_key": "party_size", "preference_value": "2 travelers",
     "confidence": 0.98, "source": "booking_history"},
    {"preference_type": "logistics", "preference_key": "seat_pref", "preference_value": "Window · aisle on long-haul",
     "confidence": 0.91, "source": "browse_session"},

    # Dining
    {"preference_type": "dining", "preference_key": "vegetarian_friendly", "preference_value": "true",
     "confidence": 0.94, "source": "past_trips"},
    {"preference_type": "dining", "preference_key": "shellfish_allergy", "preference_value": "Exclude shellfish",
     "confidence": 1.0, "source": "support_ticket"},

    # Style + pace
    {"preference_type": "style", "preference_key": "lodging_style", "preference_value": "boutique > chain",
     "confidence": 0.96, "source": "search_analytics"},
    {"preference_type": "style", "preference_key": "pace", "preference_value": "slow",
     "confidence": 0.91, "source": "past_trips"},

    # Budget
    {"preference_type": "budget", "preference_key": "budget_cap", "preference_value": "$3,200",
     "confidence": 0.88, "source": "search_analytics"},
    {"preference_type": "budget", "preference_key": "per_person_range", "preference_value": "Prefers $2k-3.5k per person",
     "confidence": 0.87, "source": "search_analytics"},

    # Interests
    {"preference_type": "activity", "preference_key": "interests",
     "preference_value": "wine country, walkable old towns",
     "confidence": 0.9, "source": "past_trips"},
    {"preference_type": "activity", "preference_key": "culture_food",
     "preference_value": "Food tours and museums over nightlife",
     "confidence": 0.84, "source": "past_trips"},

    # Loyalty programs (used by meridian-concierge MCP loyalty_balance tool)
    {"preference_type": "loyalty", "preference_key": "loyalty_programs",
     "preference_value": "Marriott Bonvoy Platinum Elite; United MileagePlus Premier 1K",
     "confidence": 0.96, "source": "profile"},

    # Recent trips (gives Phase 4/5 something to recall + refine on)
    {"preference_type": "history", "preference_key": "recent_trips",
     "preference_value": "Tuscany (Feb 2026), Kyoto (held)",
     "confidence": 0.99, "source": "booking_history"},

    # Soft destination signals (so semantic recall has something to ground in)
    {"preference_type": "destination", "preference_key": "tokyo_culture",
     "preference_value": "Tokyo culture trip Oct 12-19",
     "confidence": 0.95, "source": "profile"},
    {"preference_type": "destination", "preference_key": "iceland_planning",
     "preference_value": "Iceland ring road, prefers winter aurora viewing",
     "confidence": 0.82, "source": "browse_session"},
]


# =============================================================================
# Conversation history (Phase 4/5 short-term memory + semantic recall demos).
#
# Three demo threads scoped to DEMO_TRAVELER_ID. Each thread is a real
# multi-turn back-and-forth so the "what did we decide last time?" prompt returns
# something coherent on the very first stage demo run, before any new
# turn has been written.
#
# Topics map onto destinations the traveler_preferences seed already
# mentions (Iceland, Tokyo, Tuscany), so memory recall feels grounded.
# =============================================================================

DEMO_CONVERSATIONS = [
    {
        "conversation_id": "conv_iceland_winter",
        "started_at": "2026-04-12 10:08:00",
        "last_message_at": "2026-04-12 10:21:00",
        "summary": "Iceland ring road in winter — aurora viewing, Reykjavik base, "
                   "concerned about red-eye connections.",
    },
    {
        "conversation_id": "conv_tokyo_october",
        "started_at": "2026-05-02 09:14:00",
        "last_message_at": "2026-05-02 09:33:00",
        "summary": "Tokyo culture trip in October — Shibuya base, vegetarian dining, "
                   "Hakone day trip, prefers boutique over chain.",
    },
    {
        "conversation_id": "conv_tuscany_anniversary",
        "started_at": "2026-02-19 18:42:00",
        "last_message_at": "2026-02-19 19:01:00",
        "summary": "Tuscany wine + wellness anniversary trip — Chianti base, "
                   "slow-pace boutique stay, capped at $3.5k per person.",
    },
]


def _msg(conv_id: str, role: str, content: str, offset_minutes: int) -> dict:
    return {
        "conversation_id": conv_id,
        "role": role,
        "content": content,
        "offset_minutes": offset_minutes,
    }


# Realistic dialogue. Phase 4's recall_recent_turns will surface these
# verbatim; the embedding column lets semantic_recall_interactions match
# new prompts to past topics by meaning, not keyword.
DEMO_CONVERSATION_MESSAGES = [
    # ---- Iceland winter thread ----
    _msg("conv_iceland_winter", "user",
         "We are thinking about Iceland in late January — mainly to chase the aurora. "
         "Anything that avoids long overnight connections from New York?", 0),
    _msg("conv_iceland_winter", "assistant",
         "Iceland in late January is excellent for aurora — clear nights, lower crowds. "
         "I would anchor on Reykjavik for the first three nights with a Golden Circle day, "
         "then a Vik south-coast night, and a final night near Thingvellir for darker skies. "
         "From JFK, Icelandair offers nonstop service; I will avoid itineraries that add "
         "an overnight connection and flag the eastbound flight timing before booking.", 4),
    _msg("conv_iceland_winter", "user",
         "Sounds great. Can you keep it under $3,200 per person?", 9),
    _msg("conv_iceland_winter", "assistant",
         "The Iceland Ring Road package fits at $2,799 per person for 7 nights with the "
         "winter add-on, including aurora hunts, Blue Lagoon entry, and a nonstop JFK-KEF "
         "flight. Let me know if you want me to hold a slot.", 13),

    # ---- Tokyo October thread ----
    _msg("conv_tokyo_october", "user",
         "Help me plan a Tokyo culture trip in October. Just for me. "
         "Vegetarian-friendly dining and somewhere walkable.", 0),
    _msg("conv_tokyo_october", "assistant",
         "October is one of the best windows for Tokyo — clear skies, autumn leaves "
         "starting in Hakone. I will base you in Shibuya at a boutique ryokan-style "
         "stay (matching your boutique-over-chain preference), book a vegetarian "
         "kaiseki experience in Yanaka, and add a Hakone day trip with the rail pass. "
         "A nonstop ANA flight from JFK avoids connections and respects your routing preference.", 5),
    _msg("conv_tokyo_october", "user",
         "Perfect. What dates have availability?", 12),
    _msg("conv_tokyo_october", "assistant",
         "The Tokyo Culture & Cuisine package shows 10 slots for the 5-night option "
         "and 7 slots for the 7-night option in October. October 12 to 19 is the "
         "shoulder-week we have been holding for you.", 18),

    # ---- Tuscany anniversary thread ----
    _msg("conv_tuscany_anniversary", "user",
         "Anniversary trip — slow week in Tuscany. Wine country, "
         "good food, no rushing. Cap around $3.5k each.", 0),
    _msg("conv_tuscany_anniversary", "assistant",
         "Tuscany in late spring is exactly your slow-pace, wine-country style. "
         "I have a Chianti boutique villa with cellar tastings, a private cooking "
         "afternoon in Greve, and a wellness half-day at a thermal spa near Bagno "
         "Vignoni. Comes in at $3,699 per person for the 7-night version — slightly "
         "above your cap, but I can move to the 5-night format at $2,899 if "
         "preferred.", 6),
    _msg("conv_tuscany_anniversary", "user",
         "Let us hold the 5-night version for now and revisit closer to the date.", 14),
    _msg("conv_tuscany_anniversary", "assistant",
         "Held the 5-night Tuscany Wine & Wellness slot for 14 days under your "
         "Bonvoy account. I will keep watching for a Chianti-region upgrade if "
         "anything opens up.", 19),
]


# Past trip interactions — the embedding-backed table that powers
# `semantic_recall_interactions` in Phase 4. These give the agent
# fragments of past searches it can recall semantically: when the user
# now asks about "winter aurora trips" the embedding match lights up
# the Iceland row even if no exact keyword appears.
DEMO_TRIP_INTERACTIONS = [
    {
        "interaction_id": "int_iceland_aurora",
        "conversation_id": "conv_iceland_winter",
        "query_text": "Winter aurora trip from New York, no overnight connections, "
                      "ring road style with Reykjavik base.",
        "response_summary": "Suggested Iceland Ring Road (ADV-002), 7 nights, "
                            "Icelandair nonstop JFK-KEF, aurora hunts plus Blue Lagoon. "
                            "Held a slot for late January.",
        "packages_shown": [
            {"package_id": "ADV-002", "name": "Iceland Ring Road", "was_selected": True},
        ],
    },
    {
        "interaction_id": "int_tokyo_culture",
        "conversation_id": "conv_tokyo_october",
        "query_text": "Tokyo culture week in October, vegetarian-friendly, "
                      "walkable, boutique not chain.",
        "response_summary": "Recommended Tokyo Culture & Cuisine (CTY-002) with "
                            "Shibuya boutique base and Hakone day trip. October 12-19 "
                            "shoulder week pinned.",
        "packages_shown": [
            {"package_id": "CTY-002", "name": "Tokyo Culture & Cuisine", "was_selected": True},
        ],
    },
    {
        "interaction_id": "int_tuscany_anniversary",
        "conversation_id": "conv_tuscany_anniversary",
        "query_text": "Slow anniversary week in Tuscany wine country under $3.5k.",
        "response_summary": "Suggested Tuscany Wine & Wellness (WEL-005) — 5-night "
                            "Chianti format at $2,899 to stay under cap. Held for "
                            "14 days under Marriott Bonvoy account.",
        "packages_shown": [
            {"package_id": "WEL-005", "name": "Tuscany Wine & Wellness", "was_selected": True},
        ],
    },
    {
        "interaction_id": "int_kyoto_browse",
        "conversation_id": None,
        "query_text": "Kyoto ryokan onsen experience, slow pace, autumn leaves.",
        "response_summary": "Browsed Kyoto Ryokan & Onsen (WEL-004). No booking yet — "
                            "saved to wishlist for later.",
        "packages_shown": [
            {"package_id": "WEL-004", "name": "Kyoto Ryokan & Onsen", "was_selected": False},
        ],
    },
    {
        "interaction_id": "int_dubai_stopover",
        "conversation_id": None,
        "query_text": "Quick wellness stopover with spa access between flights.",
        "response_summary": "Surfaced Dubai Luxury Stopover (WEL-003) as a 2-3 night "
                            "spa transit option. Saved to wishlist.",
        "packages_shown": [
            {"package_id": "WEL-003", "name": "Dubai Luxury Stopover", "was_selected": False},
        ],
    },
]


# Historical bookings + lines. The Plan-trip flow appends to these
# tables in real time, but seeding two prior bookings means the
# System panel and Phase 4 booking-history tools have something to
# show on first run.
DEMO_BOOKINGS = [
    {
        "booking_id": "bkg_tuscany_2025",
        "status": "completed",
        "total_amount": 5798.00,  # 2 travelers x 2899
        "created_at": "2026-02-19 19:02:00",
        "confirmed_at": "2026-02-21 10:15:00",
        "lines": [
            {
                "package_id": "WEL-005",  # Tuscany Wine & Wellness
                "duration": "5 nights",
                "travelers_count": 2,
                "unit_price": 2899.00,
            },
        ],
    },
    {
        "booking_id": "bkg_kyoto_browse_held",
        "status": "held",
        "total_amount": 3299.00,
        "created_at": "2026-03-04 16:08:00",
        "confirmed_at": None,
        "lines": [
            {
                "package_id": "WEL-004",  # Kyoto Ryokan & Onsen
                "duration": "5 nights",
                "travelers_count": 1,
                "unit_price": 3299.00,
            },
        ],
    },
]
