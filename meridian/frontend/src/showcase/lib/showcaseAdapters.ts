import type {
  ActivityEntry,
  ChatResponse,
  LongTermMemoryFact,
  MemoryProfileResponse,
  Message,
  Phase,
  Product,
} from '../../types';

export type ShowcasePhaseLabel = 'SQL' | 'MCP' | 'Retrieval' | 'Production' | 'Workflow';
export type ShowcaseTraceTab = 'spans' | 'memory' | 'sql' | 'cost';
export type BackendStatus = 'checking' | 'online' | 'offline';

export interface ShowcasePhaseOption {
  label: ShowcasePhaseLabel;
  phase: Phase;
  description: string;
}

// Each phase has three example prompts in a deliberate order:
//   1. A query the phase handles cleanly.
//   2. A second query the phase handles cleanly (so the presenter has
//      two known-good demos for the same mode).
//   3. A query that exposes the phase's limit and motivates the next
//      phase. The walk-through narrative becomes:
//        SQL solved keyword filters... but couldn't read natural-language
//        intent. Watch what MCP changes (and doesn't). Now Retrieval.
//        Now memory. Now multi-step workflow.
//
// Wording is kept consistent across phases so a presenter can run the
// SAME prompt across all five modes to compare behaviors live.
export const SHOWCASE_EXAMPLE_PROMPTS: Record<Phase, string[]> = {
  // SQL: trip_type + price keyword filter. Fails on intent words.
  1: [
    'City breaks under $2000',
    'Beach & Resort trips under $2500',
    'A romantic slow week somewhere with great wine',
  ],
  // MCP: two MCP servers in one agent turn — both pills exercise the
  // CUSTOM meridian-concierge server (something postgres-mcp can't
  // answer), so the contrast is "generic SQL transport" vs "domain
  // tools" — not "with vs without filters".
  //   - Compare top trips ... → compare_packages + currency_convert.
  //   - Cheapest month for Tokyo → seasonal_price_band (pure domain,
  //     no SQL search needed).
  //   - Romantic slow week (stretch) → tooling can't fix the intent
  //     gap; motivates Phase 3 retrieval.
  2: [
    'Compare our top trips and show prices in EUR',
    'What is the cheapest month to visit Tokyo?',
    'A romantic slow week somewhere with great wine',
  ],
  // Retrieval: pgvector + Cohere rerank handle intent. No memory.
  // Stretch is now a query that explicitly probes for memory - Phase 3
  // genuinely cannot answer "what did we discuss last time" so the gap
  // is visible, not subtle.
  3: [
    'A romantic slow week somewhere with great wine',
    'Family-friendly beach resort with snorkeling',
    'What did we discuss last time? Pick up where we left off.',
  ],
  // Production: AgentCore + Aurora RLS + traveler memory. First pill
  // demos the conversation-memory recall that Phase 3 just failed.
  // Second exercises traveler-preference recall (no_red_eye, etc.).
  // Stretch hits availability checking which Production handles in a
  // single shot - the LangGraph StateGraph in Phase 5 wraps it with
  // explicit intent routing + checkpointing.
  4: [
    'What did we discuss last time? Pick up where we left off.',
    'Beach escape under $2500 — apply my saved preferences',
    'What dates are open for the Tokyo trip in October?',
  ],
  // Workflow: LangGraph StateGraph classifies intent and branches to
  // search / availability / memory_recall, checkpointing each step to
  // Aurora. Pills exercise each branch so the trace shows the routing
  // explicitly. Stretch crosses two destinations - even LangGraph runs
  // a single search node, so this exposes "branching ≠ multi-step
  // tool composition" honestly.
  5: [
    'What dates are open for Kyoto in November? Show the slots.',
    'Refine our last Iceland conversation with a winter focus',
    'Compare Kyoto and Tokyo for a 10-day cultural trip',
  ],
};

export interface ShowcaseTraceSpan {
  id: string;
  name: string;
  category: string;
  type: string;
  status: string;
  latencyMs: number;
  agent?: string;
  file?: string;
  component?: string;
  sql?: string;
  details?: string;
  fields: { label: string; value: string; mono?: boolean }[];
  input?: string;
  output?: string;
  costUsd?: number;
}

export const SHOWCASE_PHASES: ShowcasePhaseOption[] = [
  { label: 'SQL', phase: 1, description: 'Direct SQL filters over trip_packages' },
  { label: 'MCP', phase: 2, description: 'Catalog access through MCP tools' },
  { label: 'Retrieval', phase: 3, description: 'Hybrid retrieval and specialist routing' },
  { label: 'Production', phase: 4, description: 'Traveler memory, RLS, and AgentCore' },
  { label: 'Workflow', phase: 5, description: 'LangGraph orchestration with checkpointing' },
];

export function phaseLabelFor(phase: Phase): ShowcasePhaseLabel {
  return SHOWCASE_PHASES.find((p) => p.phase === phase)?.label ?? 'Workflow';
}

type TripPackageLike = {
  package_id: string;
  name: string;
  destination?: string;
  region?: string;
  operator?: string;
  price_per_person: number;
  description?: string;
  image_url?: string;
  trip_type?: string;
  durations?: string[] | null;
  similarity?: number;
};

function tripPackageToProduct(pkg: TripPackageLike): Product {
  return {
    product_id: pkg.package_id,
    name: pkg.name,
    brand: [pkg.destination, pkg.region].filter(Boolean).join(' + ') || pkg.operator || 'Meridian Travel',
    price: Number(pkg.price_per_person) || 0,
    description: pkg.description ?? '',
    image_url: pkg.image_url ?? '',
    category: pkg.trip_type ?? 'Trip',
    available_sizes: pkg.durations,
    similarity: pkg.similarity,
  };
}

export function packagesResponseToRecommendations(input: Product[] | TripPackageLike[] | null | undefined): Product[] {
  if (!input?.length) return [];
  const normalized = input.map((item) =>
    'package_id' in item ? tripPackageToProduct(item) : item,
  );
  return normalized.slice(0, 6);
}

export function memoryResponseToFacts(response: MemoryProfileResponse | null | undefined): LongTermMemoryFact[] {
  return response?.facts?.length ? response.facts : [];
}

export function chatResponseToMessages(prior: Message[], userText: string, response: ChatResponse): Message[] {
  const userMsg: Message = { role: 'user', text: userText };
  const assistant: Message =
    response.products?.length
      ? { role: 'bot', type: 'products', text: response.message, products: response.products }
      : response.order
        ? { role: 'bot', type: 'order', text: response.message, order: response.order }
        : { role: 'bot', type: 'text', text: response.message };

  if (response.follow_ups?.length) assistant.follow_ups = response.follow_ups;
  return [...prior, userMsg, assistant];
}

export function chatResponseToTraceSpans(response: ChatResponse | null | undefined, prompt: string): ShowcaseTraceSpan[] {
  const activities = response?.activities ?? [];
  if (!activities.length) return [];
  return activities.map((activity, index) => activityToShowcaseTraceSpan(activity, index, prompt));
}

export function activityToShowcaseTraceSpan(activity: ActivityEntry, index: number, prompt: string): ShowcaseTraceSpan {
  const telemetry = activity.telemetry;
  const latencyMs = activity.execution_time_ms ?? activity.executionTimeMs ?? 48 + index * 19;
  const status = telemetry?.status ?? (activity.activity_type === 'error' ? 'error' : 'ok');
  const category = telemetry?.category ?? inferCategory(activity);
  const sql = activity.sql_query ?? activity.sqlQuery;
  return {
    id: activity.id || `showcase-span-${index}`,
    name: activity.title || `Trace span ${index + 1}`,
    category,
    type: activity.activity_type ?? activity.type ?? 'tool_call',
    status,
    latencyMs,
    agent: activity.agent_name ?? activity.agentName,
    file: activity.agent_file ?? activity.agentFile,
    component: telemetry?.component,
    sql,
    details: activity.details,
    fields: telemetry?.fields ?? [],
    input: index === 0 ? prompt : telemetry?.fields?.find((f) => f.label.toLowerCase().includes('input'))?.value,
    output: activity.details,
    costUsd: estimateCost(activity, latencyMs),
  };
}

function inferCategory(activity: ActivityEntry): string {
  const title = (activity.title ?? '').toLowerCase();
  const type = (activity.activity_type ?? activity.type ?? '').toLowerCase();
  if (title.includes('memor')) return 'memory_long';
  if (title.includes('sql') || type === 'database') return 'data';
  if (title.includes('mcp') || type === 'mcp' || type === 'tool_call') return 'tool';
  if (title.includes('embed') || type === 'embedding') return 'data';
  if (title.includes('compose') || title.includes('rank') || type === 'reasoning') return 'model';
  if (type === 'security') return 'security';
  if (type === 'result') return 'synthesis';
  return 'orchestration';
}

function estimateCost(activity: ActivityEntry, latencyMs: number): number {
  const tokens = activity.telemetry?.tokens;
  if (tokens?.input || tokens?.output) {
    return ((tokens.input ?? 0) * 0.000003 + (tokens.output ?? 0) * 0.000015);
  }
  return latencyMs * 0.00002;
}

export function healthResponseToStatus(response: unknown): BackendStatus {
  if (!response || typeof response !== 'object') return 'offline';
  const status = 'status' in response ? String(response.status).toLowerCase() : 'healthy';
  return status.includes('healthy') || status.includes('ok') ? 'online' : 'offline';
}

export function productsFromChatResponse(response: ChatResponse): Product[] {
  return response.products?.length ? response.products : [];
}
