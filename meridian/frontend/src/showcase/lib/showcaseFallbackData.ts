import type { ActivityEntry, ChatResponse, LongTermMemoryFact, OrderResponse, Phase, Product } from '../../types';

export const SHOWCASE_TRAVELER_ID = 'trv_meridian_demo';

export const SHOWCASE_FALLBACK_FACTS: LongTermMemoryFact[] = [
  { key: 'no_red_eye', value: 'true', source: 'fixture.traveler_preferences', confidence: 0.99 },
  { key: 'vegetarian_friendly', value: 'true', source: 'fixture.traveler_preferences', confidence: 0.94 },
  { key: 'style', value: 'boutique > chain', source: 'fixture.traveler_preferences', confidence: 0.96 },
  { key: 'pace', value: 'slow', source: 'fixture.traveler_preferences', confidence: 0.91 },
  { key: 'budget_cap', value: '$3,200', source: 'fixture.traveler_preferences', confidence: 0.88 },
  { key: 'home_airport', value: 'BOS', source: 'fixture.traveler_profiles', confidence: 1 },
  {
    key: 'interests',
    value: 'wine country, walkable old towns',
    source: 'fixture.traveler_preferences',
    confidence: 0.9,
  },
  { key: 'avoid_connections', value: 'LHR, JFK', source: 'fixture.traveler_preferences', confidence: 0.87 },
];

export const SHOWCASE_FALLBACK_RECOMMENDATIONS: Product[] = [
  {
    product_id: 'CTY-001',
    name: 'Tuscan Vineyards',
    brand: 'Florence + Chianti',
    price: 2840,
    description: 'A slow wine-country week with a Florence landing, Chianti villages, spa afternoons, and walkable dinners.',
    image_url: '',
    category: 'City Breaks',
    available_sizes: ['7 nights', '5 nights'],
    similarity: 0.96,
  },
  {
    product_id: 'RIV-007',
    name: 'Douro River',
    brand: 'Porto + river hotel',
    price: 2460,
    description: 'Port tastings, river-view rooms, rail transfers, and calm lunches along terraced vineyards.',
    image_url: '',
    category: 'Wellness & Luxury',
    available_sizes: ['6 nights', '8 nights'],
    similarity: 0.91,
  },
  {
    product_id: 'CTY-019',
    name: 'Alsace Wine Route',
    brand: 'Strasbourg base',
    price: 2610,
    description: 'Half-timbered towns, Riesling producers, market dinners, spa access, and gentle rail hops.',
    image_url: '',
    category: 'City Breaks',
    available_sizes: ['5 nights'],
    similarity: 0.88,
  },
];

export const SHOWCASE_INITIAL_PROMPT =
  "A slow week somewhere we can drink good wine - Jordan can't do red-eyes.";

const FIXTURE_TIMESTAMP = '2026-05-27T12:05:11.000Z';

function activity(
  phase: Phase,
  index: number,
  activity_type: ActivityEntry['activity_type'],
  title: string,
  details: string,
  execution_time_ms: number,
  sql_query?: string,
): ActivityEntry {
  const categoryByType: Record<ActivityEntry['activity_type'], NonNullable<ActivityEntry['telemetry']>['category']> = {
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
    id: `showcase-fallback-p${phase}-${index}`,
    timestamp: FIXTURE_TIMESTAMP,
    activity_type,
    title,
    details,
    sql_query,
    execution_time_ms,
    agent_name: phase >= 4 ? 'ProductionAgent' : phase >= 3 ? 'RetrievalAgent' : 'CatalogAgent',
    agent_file:
      phase === 1
        ? 'backend/agents/phase1/agent.py'
        : phase === 2
          ? 'backend/agents/phase2/agent.py'
          : phase === 3
            ? 'backend/agents/phase3/supervisor.py'
            : 'backend/agents/phase4/concierge.py',
    telemetry: {
      category: categoryByType[activity_type],
      component:
        activity_type === 'database'
          ? 'Aurora PostgreSQL'
          : activity_type === 'mcp'
            ? 'MCP Gateway'
            : activity_type === 'embedding'
              ? 'Cohere Embed v4'
              : activity_type === 'tool_call'
                ? 'Traveler Memory'
                : 'Meridian runtime',
      status: 'preview',
      fields: [
        { label: 'source', value: 'fixture' },
        { label: 'phase', value: String(phase) },
      ],
      tokens: activity_type === 'reasoning' ? { input: 612, output: 184 } : undefined,
    },
  };
}

export function buildShowcaseFallbackActivities(message: string, phase: Phase): ActivityEntry[] {
  const phaseName =
    phase === 1 ? 'Filters' : phase === 2 ? 'MCP' : phase === 3 ? 'Intent' : phase === 4 ? 'Personal' : 'Orchestration';

  return [
    activity(
      phase,
      1,
      'delegation',
      `${phaseName} mode selected`,
      `Routing "${message}" through the ${phaseName} planner.`,
      18,
    ),
    activity(
      phase,
      2,
      phase >= 3 ? 'embedding' : 'search',
      phase >= 3 ? 'Embed traveler intent' : 'Extract deterministic filters',
      phase >= 3
        ? 'Generated a 1024d intent vector for slow wine-country travel.'
        : 'Mapped prompt to trip_type and budget filters.',
      phase >= 3 ? 44 : 21,
    ),
    activity(
      phase,
      3,
      phase >= 2 ? 'mcp' : 'database',
      phase >= 2 ? 'Query package catalog through MCP' : 'Query trip_packages',
      'Retrieved three package candidates in the Meridian trip schema.',
      phase >= 2 ? 92 : 66,
      phase <= 2
        ? "SELECT package_id, name, price_per_person\nFROM trip_packages\nWHERE trip_type = 'City Breaks'\nORDER BY price_per_person\nLIMIT 3;"
        : undefined,
    ),
    ...(phase >= 4
      ? [
          activity(
            phase,
            4,
            'tool_call',
            'Recall traveler memory',
            'Applied no_red_eye, vegetarian_friendly, boutique style, and BOS home airport.',
            38,
          ),
        ]
      : []),
    activity(
      phase,
      5,
      'reasoning',
      'Rank recommendations',
      'Scored trips by wine-country fit, flight comfort, pace, and walkability.',
      142,
    ),
    activity(phase, 6, 'result', 'Compose concierge response', 'Returned cards, trace, memory context, and next actions.', 26),
  ];
}

export function buildShowcaseFallbackChatResponse(message: string, phase: Phase): ChatResponse {
  return {
    message:
      `Demo fallback: I matched "${message}" to slow wine-country trips. ` +
      'Tuscan Vineyards is the strongest fit, with Douro River and Alsace Wine Route close behind.',
    products: SHOWCASE_FALLBACK_RECOMMENDATIONS,
    activities: buildShowcaseFallbackActivities(message, phase),
    follow_ups: ['Compare flight timing', 'Show vegetarian dinner options', 'Keep only refundable stays'],
    conversation_id: 'showcase-fallback-conversation',
    memory_facts: phase >= 4 ? SHOWCASE_FALLBACK_FACTS : undefined,
  };
}

export function buildShowcaseFallbackOrder(product: Product, phase: Phase): OrderResponse {
  const subtotal = product.price;
  const tax = Math.round(subtotal * 0.0825);
  return {
    message: `Demo fallback: ${product.name} is held for 12 hours. Live booking will use /api/chat/order.`,
    order: {
      order_id: `hold-${product.product_id.toLowerCase()}`,
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
      total: subtotal + tax,
      status: 'held',
      estimated_delivery: 'May 14, 2026',
    },
    activities: [
      activity(phase, 1, 'order', 'Create trip hold', 'Created a local hold because the backend is unavailable.', 42),
      activity(phase, 2, 'security', 'Guard payment state', 'No payment collected in demo fallback mode.', 11),
    ],
  };
}
