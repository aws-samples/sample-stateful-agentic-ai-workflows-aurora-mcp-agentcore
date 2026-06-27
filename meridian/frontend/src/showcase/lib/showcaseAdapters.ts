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
export type ShowcaseTraceTab = 'spans' | 'memory' | 'sql' | 'rls';
export type BackendStatus = 'checking' | 'online' | 'offline';

export interface ShowcasePhaseOption {
  label: ShowcasePhaseLabel;
  phase: Phase;
  description: string;
  /** Presenter ladder shorthand: Query -> Tool -> Intent -> Trust -> Durable Workflow. */
  capability: string;
  /** The message the audience should retain after this phase. */
  takeaway: string;
  /** Concrete evidence to look for in the live trace. */
  proofPoint: string;
  /** Short line explaining what this phase adds over the previous one. */
  adds?: string;
  /** Headline technology shown in the phase callout. */
  tech?: string;
}

// Prompt design:
// - the first two prompts are known-good phase wins.
// - the third prompt exposes the limit that the next phase fixes.
// Adjacent phases intentionally pair: SQL stretch -> MCP success,
// MCP stretch -> Retrieval success, Retrieval stretch -> Production success.
export const SHOWCASE_EXAMPLE_PROMPTS: Record<Phase, string[]> = {
  // Direct filters work; comparison plus FX needs a domain tool contract.
  1: [
    'City breaks under $2000',
    'Beach & Resort trips under $2500',
    'Compare our top trips and show prices in EUR',
  ],
  // Custom MCP tools solve comparison, FX, and seasonality; mood intent remains retrieval's job.
  2: [
    'Compare our top trips and show prices in EUR',
    'What is the cheapest month to visit Tokyo?',
    'A romantic slow week somewhere with great wine',
  ],
  // Intent routing works; persisted conversation memory is still out of scope.
  3: [
    'A romantic slow week somewhere with great wine',
    'Check availability for the Tuscany Wine & Wellness week',
    'What did we discuss last time? Pick up where we left off.',
  ],
  // Tokyo storyline proves memory and RLS; the multi-step prompt tees up Workflow.
  4: [
    'Tokyo culture trip for two — boutique stays, local food, walkable neighborhoods',
    'What did we discuss last time? Pick up where we left off.',
    'Plan our October Tokyo trip — find open dates, pick a Marriott property, and hold a Kyoto side trip',
  ],
  // Each prompt lands on a distinct branch: availability, memory_recall, plan.
  5: [
    'What dates are open for the Amalfi Coast Villa Week?',
    'What did we discuss last time? Pick up where we left off.',
    'Plan a Kyoto cultural trip end-to-end: find matching trips, then check which November departures are open.',
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
    capability: 'Query',
    takeaway: 'Ground the assistant in live Aurora rows before adding agent abstractions.',
    proofPoint: 'SQL executed',
    // Phase 1 is the base rung — nothing to diff against.
    tech: 'RDS Data API',
  },
  {
    label: 'MCP',
    phase: 2,
    description: 'Catalog access through MCP tools',
    capability: 'Tool',
    takeaway: 'Expose Aurora through governed tool contracts that agents can call safely.',
    proofPoint: 'MCP tool invoked',
    adds: 'Same Aurora — now reached through versioned, IAM-authed MCP tools instead of hand-written SQL.',
    tech: 'postgres-mcp + meridian-concierge',
  },
  {
    label: 'Retrieval',
    phase: 3,
    description: 'Hybrid retrieval and specialist routing',
    capability: 'Intent',
    takeaway: 'Match traveler intent with vectors, text search, reranking, and specialist routing.',
    proofPoint: 'pgvector + rerank',
    adds: 'Adds intent: pgvector + tsvector candidates, reranked. Matches what you mean, not what you type.',
    tech: 'Cohere Embed v4 + Rerank 3.5',
  },
  {
    label: 'Production',
    phase: 4,
    description: 'Traveler memory, RLS, and AgentCore',
    capability: 'Trust',
    takeaway: 'Add memory, isolation, RLS, and auditability so personalization is governable.',
    proofPoint: 'RLS scoped + audited',
    adds: 'Adds memory + trust: recalls who you are, scopes every query under Aurora RLS, audits each turn.',
    tech: 'AgentCore + Aurora RLS',
  },
  {
    label: 'Workflow',
    phase: 5,
    description: 'LangGraph orchestration with checkpointing',
    capability: 'Durable Workflow',
    takeaway: 'Make multi-step work explicit, inspectable, checkpointed, and resumable.',
    proofPoint: 'Checkpoint written',
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
  pre_rerank_position?: number | null;
  pre_rerank_similarity?: number | null;
  rank_delta?: number | null;
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
    pre_rerank_position: pkg.pre_rerank_position,
    pre_rerank_similarity: pkg.pre_rerank_similarity,
    rank_delta: pkg.rank_delta,
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
