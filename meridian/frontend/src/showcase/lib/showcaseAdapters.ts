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
export type ShowcaseTraceTab = 'spans' | 'memory' | 'sql';
export type BackendStatus = 'checking' | 'online' | 'offline';

export interface ShowcasePhaseOption {
  label: ShowcasePhaseLabel;
  phase: Phase;
  description: string;
  /** Short "what this rung adds over the previous one" line, surfaced as a
   *  transient callout when the presenter switches phases. Reinforces the
   *  core narrative: each mode composes onto the last, nothing is rewritten.
   *  Phase 1 (the base) has no delta. */
  adds?: string;
  /** The headline tech that powers this rung — shown as a chip in the
   *  callout so the audience anchors the capability to a concrete tool. */
  tech?: string;
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
  // Retrieval: supervised multi-agent search. The three pills now show
  // THREE distinct behaviours so the trace differs each time (not three
  // identical hybrid searches):
  //   1. SearchAgent — hybrid pgvector + tsvector + Cohere rerank on intent.
  //   2. PackageAgent — the supervisor DELEGATES an availability question to
  //      a different specialist, drilling into the Tuscany result from pill 1.
  //      Different specialist, different tool, different trace = the
  //      "supervised multi-agent" payoff made visible.
  //   3. Stretch — a memory-recall prompt Phase 3 genuinely cannot answer
  //      (no conversation store); the honest failure motivates Production.
  3: [
    'A romantic slow week somewhere with great wine',
    'Check availability for the Tuscany Wine & Wellness week',
    'What did we discuss last time? Pick up where we left off.',
  ],
  // Production: AgentCore + Aurora RLS + traveler memory. The pills are
  // ordered as a single Tokyo-themed storyline so each turn builds on
  // the last AND lines up with the seeded preferences (tokyo_culture
  // = "Tokyo culture trip Oct 12-19", recent_trips = "Kyoto (held)").
  //   1. Concrete Tokyo query — Phase 4 persists "Tokyo culture trip"
  //      into conversation_messages + trip_interactions, where the
  //      embeddings will favor Tokyo packages on subsequent searches.
  //   2. Recall query — pgvector + persisted thread + seeded
  //      tokyo_culture preference all converge on Tokyo Culture &
  //      Cuisine, Tokyo Indie Walk, etc. The recall punchline lands
  //      because Aurora has Tokyo to point at — not generic stopovers.
  //   3. Multi-intent stretch — same Tokyo thread, now with three
  //      jobs Strands chains implicitly in one Bedrock turn. Phase 5's
  //      LangGraph routes the same prompt through named classify /
  //      search / availability / memory nodes with checkpoints, which
  //      is what makes the upgrade legible.
  4: [
    'Tokyo culture trip for two — boutique stays, local food, walkable neighborhoods',
    'What did we discuss last time? Pick up where we left off.',
    'Plan our October Tokyo trip — find open dates, pick a Marriott property, and hold a Kyoto side trip',
  ],
  // Workflow: LangGraph StateGraph classifies intent and branches to
  // search / availability / memory_recall / plan, checkpointing each step
  // to Aurora. Phase 5 is the FINALE — there's no next phase to motivate,
  // so all three pills are solid successes (no stretch/amber). Each one
  // lands on a distinct branch so the trace shows routing explicitly, and
  // the deck ends on the "plan" intent — the case LangGraph genuinely
  // EXCELS at: a multi-step chain (search → availability) that runs two
  // sequential worker nodes with a PostgresSaver checkpoint between each.
  // That's composition a single tool call can't make visible.
  //   1. availability branch — single node, lists open departures.
  //   2. memory_recall branch — "remember/last time" loads the prior
  //      thread, then matches against it (classify → memory_recall).
  //   3. plan branch — search → availability, multi-node, the payoff.
  5: [
    'What dates are open for Kyoto in November? Show the slots.',
    'Remember our last Tokyo conversation? Pick it up with a culture focus',
    'Plan a Kyoto cultural trip and check which November departures are open',
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
}

export const SHOWCASE_PHASES: ShowcasePhaseOption[] = [
  {
    label: 'SQL',
    phase: 1,
    description: 'Direct SQL filters over trip_packages',
    // Phase 1 is the base rung — nothing to diff against.
    tech: 'RDS Data API',
  },
  {
    label: 'MCP',
    phase: 2,
    description: 'Catalog access through MCP tools',
    adds: 'Same Aurora — now reached through versioned, IAM-authed MCP tools instead of hand-written SQL.',
    tech: 'postgres-mcp + meridian-concierge',
  },
  {
    label: 'Retrieval',
    phase: 3,
    description: 'Hybrid retrieval and specialist routing',
    adds: 'Adds intent: pgvector + tsvector candidates, reranked. Matches what you mean, not what you type.',
    tech: 'Cohere Embed v4 + Rerank 3.5',
  },
  {
    label: 'Production',
    phase: 4,
    description: 'Traveler memory, RLS, and AgentCore',
    adds: 'Adds memory + trust: recalls who you are, scopes every query under Aurora RLS, audits each turn.',
    tech: 'AgentCore + Aurora RLS',
  },
  {
    label: 'Workflow',
    phase: 5,
    description: 'LangGraph orchestration with checkpointing',
    adds: 'Adds durability: explicit graph nodes, checkpointed to Aurora — pause Tuesday, resume Thursday.',
    tech: 'LangGraph + PostgresSaver',
  },
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

export function healthResponseToStatus(response: unknown): BackendStatus {
  if (!response || typeof response !== 'object') return 'offline';
  const status = 'status' in response ? String(response.status).toLowerCase() : 'healthy';
  return status.includes('healthy') || status.includes('ok') ? 'online' : 'offline';
}

export function productsFromChatResponse(response: ChatResponse): Product[] {
  return response.products?.length ? response.products : [];
}
