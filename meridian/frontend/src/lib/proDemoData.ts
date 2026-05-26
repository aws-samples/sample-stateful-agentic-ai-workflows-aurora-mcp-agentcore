/**
 * Meridian Pro demo constants — prompts, MCP catalog, Aurora schema reference.
 *
 * All runtime data (memory facts, traces, products) comes from live API calls.
 */
import type { TravelerProfile } from '../types';

export const DEMO_TRAVELER_ID = 'trv_meridian_demo';

/** Default demo traveler profile (overridden by GET /api/memory when live). */
export const DEMO_TRAVELER: TravelerProfile = {
  full_name: 'Alex & Jordan Chen',
  home_airport: 'BOS',
  party_size: 2,
  budget_min: 2000,
  budget_max: 3500,
  seat_preference: 'aisle on long-haul',
  dietary_notes: 'Vegetarian-friendly preferred',
  trip_goal: 'Tuscan Vineyards · slow week of wine country',
};

export const DEMO_TRAVELER_TAGS = [
  'Slow travel',
  'Wine country',
  'No red-eyes',
  'Veg-friendly',
  'Boutique',
];

/** Default prompt used by hero + workspace starter. */
export const DEMO_PROMPT =
  "A slow week somewhere we can drink good wine – Jordan can't do red-eyes.";

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
      'SELECT id, name, price_cents FROM trip_packages\nWHERE category = $1 AND price_cents <= $2\nLIMIT 5;',
    sampleOutput:
      '[\n  { id: "CTY-001", name: "Tuscan Vineyards", price_cents: 284000 },\n  { id: "CTY-014", name: "Provence Slow Week", price_cents: 261000 }\n]',
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
      '[\n  { id: "CTY-001", score: 0.91, name: "Tuscan Vineyards" },\n  { id: "RIV-007", score: 0.88, name: "Douro River" },\n  { id: "CTY-019", score: 0.86, name: "Alsace Wine Route" }\n]',
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
    ver: 'opus-4.7',
    p50: '132ms',
    health: 'healthy',
    sampleInput:
      '{\n  "system": "Meridian concierge — ground replies in trip_cards + memory facts",\n  "user": "A slow week somewhere we can drink good wine – Jordan can\'t do red-eyes."\n}',
    sampleOutput:
      '"Tuscany fits both of you. Florence + Chianti, May 14–21 — boutique, refundable, no red-eye out of BOS…"',
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
