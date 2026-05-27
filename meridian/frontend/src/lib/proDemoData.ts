/**
 * Meridian Pro demo constants — prompts, MCP catalog, Aurora schema reference.
 *
 * Runtime data normally comes from live API calls. The fallback fixtures below
 * keep the product surface useful when FastAPI/Aurora are unavailable.
 */
import type {
  ActivityEntry,
  ChatResponse,
  LongTermMemoryFact,
  OrderResponse,
  Phase,
  Product,
  TravelerProfile,
} from '../types';

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

export const DEMO_MEMORY_FACTS: LongTermMemoryFact[] = [
  {
    key: 'travel_style',
    value: 'Boutique hotels, walkable neighborhoods, relaxed pace',
    source: 'fixture.traveler_preferences',
    confidence: 0.96,
  },
  {
    key: 'constraint_no_red_eye',
    value: "Avoid red-eye flights for Jordan",
    source: 'fixture.traveler_preferences',
    confidence: 0.99,
  },
  {
    key: 'dietary_preference',
    value: 'Vegetarian-friendly restaurants preferred',
    source: 'fixture.traveler_preferences',
    confidence: 0.91,
  },
  {
    key: 'budget_band',
    value: '$2,000-$3,500 per person for week-long leisure trips',
    source: 'fixture.traveler_preferences',
    confidence: 0.88,
  },
  {
    key: 'prior_trip_signal',
    value: 'Loved Tuscany and Kyoto for food, design, and slow mornings',
    source: 'fixture.trip_interactions',
    confidence: 0.86,
  },
  {
    key: 'seat_preference',
    value: 'Aisle on long-haul flights',
    source: 'fixture.traveler_profiles',
    confidence: 0.93,
  },
];

export const DEMO_PRODUCTS: Product[] = [
  {
    product_id: 'CTY-001',
    name: 'Tuscan Vineyards',
    brand: 'Borgo San Felice',
    price: 2840,
    description: 'Florence arrival, Chianti wine villages, spa afternoons, and slow dinners.',
    image_url: '',
    category: 'City Breaks',
    available_sizes: ['7 nights', '5 nights'],
    similarity: 0.93,
  },
  {
    product_id: 'RIV-007',
    name: 'Douro River Slow Week',
    brand: 'Quinta da Ponte',
    price: 2460,
    description: 'Port tastings, river-view suites, rail transfers, and lazy vineyard lunches.',
    image_url: '',
    category: 'Wellness & Luxury',
    available_sizes: ['6 nights', '8 nights'],
    similarity: 0.9,
  },
  {
    product_id: 'CTY-019',
    name: 'Alsace Wine Route',
    brand: 'Maison Colmar',
    price: 2190,
    description: 'Half-timbered towns, Riesling producers, market dinners, and spa access.',
    image_url: '',
    category: 'City Breaks',
    available_sizes: ['5 nights'],
    similarity: 0.88,
  },
  {
    product_id: 'BCH-001',
    name: 'Azores Blue Coast',
    brand: 'Atlântico Retreats',
    price: 1980,
    description: 'Thermal pools, coastal hikes, whale watching, and quiet oceanfront lodging.',
    image_url: '',
    category: 'Beach & Resort',
    available_sizes: ['6 nights'],
  },
  {
    product_id: 'ADV-001',
    name: 'Dolomites Soft Adventure',
    brand: 'Alta Via Collective',
    price: 3150,
    description: 'Scenic lifts, hut lunches, lakeside walks, and refundable boutique stays.',
    image_url: '',
    category: 'Adventure & Outdoors',
    available_sizes: ['7 nights'],
  },
  {
    product_id: 'WEL-001',
    name: 'Provence Spa Villages',
    brand: 'Luberon House',
    price: 2710,
    description: 'Lavender roads, spa rituals, market cooking, and village-to-village drives.',
    image_url: '',
    category: 'Wellness & Luxury',
    available_sizes: ['6 nights'],
  },
  {
    product_id: 'BUS-004',
    name: 'Singapore Stopover',
    brand: 'Meridian Business',
    price: 840,
    description: 'Late checkout, lounge access, and one-night recovery near the waterfront.',
    image_url: '',
    category: 'Business travel',
    available_sizes: ['1 night', '2 nights'],
  },
  {
    product_id: 'BCH-004',
    name: 'Madeira Cliffside',
    brand: 'Funchal Design Hotels',
    price: 2050,
    description: 'Ocean cliffs, direct flights, garden walks, and calm shoulder-season weather.',
    image_url: '',
    category: 'Beach & Resort',
    available_sizes: ['5 nights'],
  },
  {
    product_id: 'ADV-008',
    name: 'Iceland Thermal Ring',
    brand: 'North Light Trips',
    price: 3380,
    description: 'Geothermal stays, waterfall drives, aurora evenings, and flexible routing.',
    image_url: '',
    category: 'Adventure & Outdoors',
    available_sizes: ['8 nights'],
  },
];

const FIXTURE_TIMESTAMP = '2026-05-27T12:00:00.000Z';

function fallbackActivity(
  phase: Phase,
  index: number,
  activity_type: ActivityEntry['activity_type'],
  title: string,
  details: string,
  execution_time_ms: number,
  sql_query?: string,
): ActivityEntry {
  const categoryByType: Record<
    ActivityEntry['activity_type'],
    NonNullable<ActivityEntry['telemetry']>['category']
  > = {
    search: 'data',
    embedding: 'data',
    tool_call: 'tool',
    database: 'data',
    error: 'tool',
    inventory: 'data',
    order: 'tool',
    delegation: 'orchestration',
    mcp: 'tool',
    reasoning: 'model',
    result: 'synthesis',
    security: 'security',
  };

  return {
    id: `fixture-p${phase}-${index}`,
    timestamp: FIXTURE_TIMESTAMP,
    activity_type,
    title,
    details,
    sql_query,
    execution_time_ms,
    agent_name: phase >= 4 ? 'ProductionAgent' : phase >= 3 ? 'RetrievalAgent' : 'CatalogAgent',
    telemetry: {
      category: categoryByType[activity_type],
      component: phase >= 4 ? 'offline.fixture.production' : 'offline.fixture.catalog',
      status: 'preview',
      fields: [
        { label: 'source', value: 'fixture' },
        { label: 'phase', value: String(phase) },
      ],
    },
  };
}

export function buildFallbackChatResponse(message: string, phase: Phase): ChatResponse {
  const products = DEMO_PRODUCTS.slice(0, 3);
  const activities: ActivityEntry[] = [
    fallbackActivity(
      phase,
      1,
      phase >= 3 ? 'embedding' : 'search',
      phase >= 3 ? 'Embed traveler intent' : 'Parse catalog filters',
      phase >= 3
        ? 'Cohere Embed v4 style vector generated from the request.'
        : 'Mapped the prompt to deterministic catalog filters.',
      phase >= 3 ? 42 : 18,
    ),
    fallbackActivity(
      phase,
      2,
      phase >= 2 ? 'mcp' : 'database',
      phase >= 2 ? 'Query Aurora package catalog' : 'Run SQL filter',
      'Returned fixture trip rows using the same product shape as the live API.',
      phase >= 2 ? 84 : 63,
      phase <= 2
        ? "SELECT package_id, name, price_per_person FROM trip_packages WHERE trip_type = 'City Breaks' LIMIT 3"
        : undefined,
    ),
    ...(phase >= 4
      ? [
          fallbackActivity(
            phase,
            3,
            'tool_call',
            'Recall traveler memory',
            'Loaded preference facts for Alex and Jordan from offline fixture memory.',
            36,
          ),
        ]
      : []),
    fallbackActivity(
      phase,
      4,
      'reasoning',
      'Compose grounded reply',
      'Ranked trips by wine-country fit, no red-eye constraint, and boutique preference.',
      128,
    ),
    fallbackActivity(
      phase,
      5,
      'result',
      'Return recommendations',
      'Prepared three deterministic recommendations for the light Pro workspace.',
      21,
    ),
  ];

  return {
    message:
      `Fallback mode: I matched "${message}" against the seeded Meridian trip catalog. ` +
      'Tuscan Vineyards is the strongest fit, followed by Douro River and Alsace Wine Route.',
    products,
    activities,
    follow_ups: [
      'Compare Tuscany and Douro by flight timing',
      'Show only refundable boutique stays',
      'Add spa options and vegetarian dinners',
    ],
    conversation_id: 'fixture-conv-pro',
    memory_facts: phase >= 4 ? DEMO_MEMORY_FACTS : undefined,
  };
}

export function buildFallbackOrderResponse(product: Product, phase: Phase): OrderResponse {
  const subtotal = product.price;
  const tax = Math.round(subtotal * 0.0825);
  const total = subtotal + tax;

  return {
    message: `Fallback hold created for ${product.name}. Live booking requires the backend.`,
    order: {
      order_id: `fixture-${product.product_id.toLowerCase()}`,
      items: [
        {
          product_id: product.product_id,
          name: product.name,
          size: product.available_sizes?.[0],
          quantity: 1,
          unit_price: product.price,
        },
      ],
      subtotal,
      tax,
      shipping: 0,
      total,
      status: 'held',
      estimated_delivery: 'May 14, 2026',
    },
    activities: [
      fallbackActivity(
        phase,
        1,
        'order',
        'Create fixture booking hold',
        'Simulated the confirm-before-purchase hold path without calling the backend.',
        44,
      ),
      fallbackActivity(
        phase,
        2,
        'security',
        'Apply confirmation guardrail',
        'No card is charged in fallback mode.',
        12,
      ),
    ],
  };
}

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
