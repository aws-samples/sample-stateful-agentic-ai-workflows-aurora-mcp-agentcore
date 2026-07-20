/**
 * Meridian Pro static config — example prompt, MCP catalog, Aurora schema reference.
 *
 * No fixture trips, memory, or fallback responses live here anymore. Every data
 * surface (trips, traveler memory, traces) renders from live `/api/*` calls so
 * the Pro page matches the live-only behavior of `/showcase`. The constants below
 * are static reference metadata (tool names, table names, the headline example
 * query) — not stand-in demo data.
 */

export const DEMO_TRAVELER_ID = 'trv_meridian_demo';

/** Headline example query used by the hero + catalog CTAs.
 *  The Workflow mode routes this flight-disruption replan through named
 *  classify → search → availability nodes with a checkpoint after each. */
export const DEMO_PROMPT =
  'My JFK flight to Tokyo just got cancelled. Rework the trip and check which departures are still open.';

/** MCP tool catalog displayed in the System section. */
export interface McpToolEntry {
  name: string;
  sub: string;
  ver: string;
  p50: string;
  health: 'healthy' | 'warn' | 'down';
  sampleInput: string;
  sampleOutput: string;
}

export const MCP_TOOL_CATALOG: McpToolEntry[] = [
  {
    name: 'postgres.run_query',
    sub: 'aurora data api',
    ver: 'v3.1',
    p50: '96ms',
    health: 'healthy',
    sampleInput:
      'SELECT package_id, name, price_per_person FROM trip_packages\nWHERE trip_type = $1 AND price_per_person <= $2\nLIMIT 5;',
    sampleOutput:
      '[\n  { package_id: "CTY-001", name: "Tuscan Vineyards", price_per_person: 2840 },\n  { package_id: "CTY-014", name: "Provence Slow Week", price_per_person: 2610 }\n]',
  },
  {
    name: 'trips.hybrid_search',
    sub: 'pgvector + tsvector',
    ver: 'v1.4',
    p50: '186ms',
    health: 'healthy',
    sampleInput:
      '{\n  "query": "slow week wine country",\n  "weights": { "vector": 0.62, "tsvector": 0.38 },\n  "k": 8\n}',
    sampleOutput:
      '[\n  { package_id: "CTY-001", score: 0.91, name: "Tuscan Vineyards" },\n  { package_id: "RIV-007", score: 0.88, name: "Douro River" },\n  { package_id: "CTY-019", score: 0.86, name: "Alsace Wine Route" }\n]',
  },
  {
    name: 'memory.recall',
    sub: 'strands @tool',
    ver: 'v1.2',
    p50: '42ms',
    health: 'healthy',
    sampleInput: '{ "traveler_id": "trv_meridian_demo", "limit": 8 }',
    sampleOutput:
      '[\n  { key: "no_red_eye", value: true, conf: 0.99 },\n  { key: "budget_cap", value: 3200, conf: 0.84 },\n  { key: "style", value: "boutique", conf: 0.92 }\n]',
  },
  {
    name: 'memory.write_fact',
    sub: 'strands @tool',
    ver: 'v1.0',
    p50: '22ms',
    health: 'healthy',
    sampleInput:
      '{\n  "traveler_id": "trv_meridian_demo",\n  "key": "style",\n  "value": "boutique > chain",\n  "source": "conv_18ab2"\n}',
    sampleOutput:
      '{ "fact_id": "pref_8c41", "written_at": "2026-05-25T22:11:04Z", "audit_row": "aud_4f2c" }',
  },
  {
    name: 'availability.lookup',
    sub: 'aurora · live',
    ver: 'v1.0',
    p50: '62ms',
    health: 'healthy',
    sampleInput:
      '{ "package_ids": ["CTY-001"], "window": ["2026-05-14", "2026-05-21"] }',
    sampleOutput:
      '{ "CTY-001": { "available": true, "rooms": 3, "refundable_until": "2026-05-11" } }',
  },
  {
    name: 'bookings.hold',
    sub: 'aurora + provider',
    ver: 'v1.1',
    p50: '240ms',
    health: 'healthy',
    sampleInput:
      '{\n  "traveler_id": "trv_meridian_demo",\n  "package_id": "CTY-001",\n  "scope": "hold_only",\n  "expires_in": "12h"\n}',
    sampleOutput:
      '{ "hold_id": "bk_2614", "status": "held", "confirm_before": "2026-05-26T10:00:00Z" }',
  },
  {
    name: 'claude.compose',
    sub: 'bedrock',
    ver: 'opus-4.8',
    p50: '132ms',
    health: 'healthy',
    sampleInput:
      '{\n  "system": "Meridian concierge — ground replies in trip_cards + memory facts",\n  "user": "My JFK flight to Tokyo just got cancelled. Rework the trip and check which departures are still open."\n}',
    sampleOutput:
      '"For your Tokyo Oct 12-19 window, the closest matches are Tokyo Executive Stopover ($1,949) and Tokyo Ryokan & Onsen Slow Week ($3,899), with shellfish allergy on dining and JFK departures with no red-eyes…"',
  },
];

/** Aurora table catalog for the System section. */
export const AURORA_TABLES = [
  'trip_packages',
  'travelers',
  'traveler_profiles',
  'traveler_preferences',
  'conversations',
  'conversation_messages',
  'trip_interactions',
  'bookings',
  'booking_lines',
  'agent_traces',
] as const;
