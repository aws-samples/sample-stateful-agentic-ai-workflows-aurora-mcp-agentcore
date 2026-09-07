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
export const SHOWCASE_FINALE_PROMPT =
  'My JFK-to-Tokyo flight was canceled. Rework the trip, then check duration availability for the best three options.';

export const SHOWCASE_EXAMPLE_PROMPTS: Record<Phase, string[]> = {
  // Direct filters work; comparison plus FX needs a domain tool contract.
  1: [
    'Show me city trips under $2,000 per traveler.',
    'Show me beach trips under $2,500 per traveler.',
    'Compare three trip types and convert each price to euros.',
  ],
  // Custom MCP tools solve comparison, FX, and seasonality; mood intent remains retrieval's job.
  2: [
    'Compare three trip types and convert each price to euros.',
    'What is the off-season price range for Tokyo trips in November?',
    'Find a quiet, romantic wine-country retreat with a private villa.',
  ],
  // Intent routing works; persisted conversation memory is still out of scope.
  3: [
    'Find a quiet, romantic wine-country retreat with a private villa.',
    'Which trip lengths are still available for Tuscany Wine & Wellness?',
    'Recall my Tokyo plan and saved preferences: home airport, food needs, and budget.',
  ],
  // Tokyo proves memory and RLS; the flight disruption is the same prompt Phase 5
  // owns - Production answers it in one turn, teeing up the checkpointed A/B.
  4: [
    'Find Tokyo trips that fit my saved preferences.',
    'Recall my Tokyo plan and saved preferences: home airport, food needs, and budget.',
    SHOWCASE_FINALE_PROMPT,
  ],
  // Each prompt lands on a distinct branch: availability, memory_recall, plan.
  5: [
    'Which trip lengths are still available for Amalfi Coast Villa Week?',
    'Recall my Tokyo plan and saved preferences: home airport, food needs, and budget.',
    SHOWCASE_FINALE_PROMPT,
  ],
};

export const PHASE_QUERY_BOUNDARIES: Record<Phase, string> = {
  1: 'This phase filters catalog rows. MCP adds comparison and currency tools.',
  2: 'These tools take explicit inputs. Retrieval adds matches by meaning.',
  3: 'Search uses this request. Production adds authorized traveler memory.',
  4: 'Memory saves the conversation. Workflow also saves which step runs next.',
  5: 'Pause, then resume the same thread. Airline ticketing remains outside this demo.',
};

const SHOWCASE_PROMPT_LABELS: Record<string, string> = {
  [SHOWCASE_EXAMPLE_PROMPTS[1][0]]: 'City trips under $2,000',
  [SHOWCASE_EXAMPLE_PROMPTS[1][1]]: 'Beach trips under $2,500',
  [SHOWCASE_EXAMPLE_PROMPTS[1][2]]: 'Compare trips in euros',
  [SHOWCASE_EXAMPLE_PROMPTS[2][1]]: 'Tokyo off-season pricing',
  [SHOWCASE_EXAMPLE_PROMPTS[2][2]]: 'Romantic wine-country villa',
  [SHOWCASE_EXAMPLE_PROMPTS[3][1]]: 'Tuscany trip lengths',
  [SHOWCASE_EXAMPLE_PROMPTS[3][2]]: 'Recall my plan & preferences',
  [SHOWCASE_EXAMPLE_PROMPTS[4][0]]: 'Tokyo with my preferences',
  [SHOWCASE_FINALE_PROMPT]: 'Canceled flight replan',
  [SHOWCASE_EXAMPLE_PROMPTS[5][0]]: 'Amalfi trip lengths',
};

export function showcasePromptLabel(prompt: string): string {
  return SHOWCASE_PROMPT_LABELS[prompt] ?? prompt;
}

export interface ShowcaseTraceSpan {
  id: string;
  name: string;
  category: string;
  type: string;
  status: string;
  latencyMs: number | null;
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
    // Phase 1 is the base rung - nothing to diff against.
    tech: 'RDS Data API',
  },
  {
    label: 'MCP',
    phase: 2,
    description: 'Catalog access through MCP tools',
    capability: 'Tool',
    takeaway: 'Expose Aurora through named tool contracts with explicit inputs and results.',
    proofPoint: 'MCP tool invoked',
    adds: 'Named MCP tools reuse the same Aurora catalog, with explicit inputs and results.',
    tech: 'postgres-mcp + meridian-concierge',
  },
  {
    label: 'Retrieval',
    phase: 3,
    description: 'Hybrid retrieval and specialist routing',
    capability: 'Intent',
    takeaway: 'Match traveler intent with vectors, text search, reranking, and specialist routing.',
    proofPoint: 'pgvector + rerank',
    adds: 'Combines matches by meaning and keywords, then reranks the results.',
    tech: 'Cohere Embed v4 + Rerank 3.5',
  },
  {
    label: 'Production',
    phase: 4,
    description: 'Workload grants, traveler memory, and RLS',
    capability: 'Trust',
    takeaway: 'Authenticate the workload, authorize Alex, then apply RLS and audit every turn.',
    proofPoint: 'Workload grant + RLS',
    adds: 'Checks the agent’s identity and permission to access Alex’s saved preferences.',
    tech: 'AgentCore · Aurora authorization · RLS',
  },
  {
    label: 'Workflow',
    phase: 5,
    description: 'LangGraph orchestration with checkpointing',
    capability: 'Durable Workflow',
    takeaway: 'Make multi-step work explicit, inspectable, checkpointed, and resumable.',
    proofPoint: 'Checkpoint written',
    adds: 'LangGraph saves progress in Aurora so a replacement worker can resume the same thread.',
    tech: 'LangGraph · Aurora PostgreSQL',
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

export function genericizeLoyaltyText(value: string): string {
  return value
    .replace(/Marriott Bonvoy Platinum Elite/gi, 'Hotel Platinum')
    .replace(/Marriott Bonvoy Platinum/gi, 'Hotel Platinum')
    .replace(/Bonvoy Platinum Elite/gi, 'Hotel Platinum')
    .replace(/Bonvoy Platinum/gi, 'Hotel Platinum')
    .replace(/Platinum Elite/gi, 'Hotel Platinum')
    .replace(/United MileagePlus Premier 1K/gi, 'Airline Premier')
    .replace(/United Premier 1K/gi, 'Airline Premier')
    .replace(/MileagePlus Premier 1K/gi, 'Airline Premier')
    .replace(/Premier 1K/gi, 'Airline Premier')
    .replace(/Marriott Bonvoy/gi, 'hotel loyalty')
    .replace(/\bBonvoy\b/gi, 'hotel loyalty')
    .replace(/United MileagePlus/gi, 'airline loyalty')
    .replace(/\bMileagePlus\b/gi, 'airline loyalty');
}

export function memoryResponseToFacts(response: MemoryProfileResponse | null | undefined): LongTermMemoryFact[] {
  return response?.facts?.length
    ? response.facts.map((fact) => ({
        ...fact,
        value: genericizeLoyaltyText(fact.value),
      }))
    : [];
}

export function chatResponseToMessages(prior: Message[], userText: string, response: ChatResponse): Message[] {
  const userMsg: Message = { role: 'user', text: userText };
  const assistantText = genericizeLoyaltyText(response.message);
  const assistant: Message =
    response.products?.length
      ? { role: 'bot', type: 'products', text: assistantText, products: response.products }
      : response.order
        ? { role: 'bot', type: 'order', text: assistantText, order: response.order }
        : { role: 'bot', type: 'text', text: assistantText };

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
  const latencyMs = activity.execution_time_ms ?? activity.executionTimeMs ?? null;
  const status = telemetry?.status ?? (activity.activity_type === 'error' ? 'error' : 'ok');
  const category = telemetry?.category ?? inferCategory(activity);
  const sql = activity.sql_query ?? activity.sqlQuery;
  const details = activity.details ? genericizeLoyaltyText(activity.details) : undefined;
  const fields = (telemetry?.fields ?? []).map((field) => ({
    ...field,
    value: genericizeLoyaltyText(field.value),
  }));
  return {
    id: activity.id || `showcase-span-${index}`,
    name: genericizeLoyaltyText(activity.title || `Trace span ${index + 1}`),
    category,
    type: activity.activity_type ?? activity.type ?? 'tool_call',
    status,
    latencyMs,
    agent: activity.agent_name ?? activity.agentName,
    file: activity.agent_file ?? activity.agentFile,
    component: telemetry?.component,
    sql,
    details,
    fields,
    input: index === 0
      ? genericizeLoyaltyText(prompt)
      : fields.find((field) => field.label.toLowerCase().includes('input'))?.value,
    output: details,
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
  const isMeridianHealth =
    'bedrock_model_id' in response &&
    'embedding_model_id' in response &&
    'checkpoint_backend' in response;
  if (!isMeridianHealth) return 'offline';
  const status = 'status' in response ? String(response.status).toLowerCase() : '';
  return status === 'healthy' || status === 'ok' ? 'online' : 'offline';
}

export function productsFromChatResponse(response: ChatResponse): Product[] {
  return response.products?.length ? response.products : [];
}
