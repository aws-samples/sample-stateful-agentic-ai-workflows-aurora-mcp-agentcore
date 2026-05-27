import type {
  ActivityEntry,
  ChatResponse,
  LongTermMemoryFact,
  MemoryProfileResponse,
  Message,
  Phase,
  Product,
  TripPackage,
} from '../../types';
import { SHOWCASE_FALLBACK_FACTS, SHOWCASE_FALLBACK_RECOMMENDATIONS } from './showcaseFallbackData';

export type ShowcasePhaseLabel = 'Filters' | 'MCP' | 'Intent' | 'Personal';
export type ShowcaseTraceTab = 'spans' | 'memory' | 'sql' | 'cost';
export type BackendStatus = 'checking' | 'online' | 'offline';

export interface ShowcasePhaseOption {
  label: ShowcasePhaseLabel;
  phase: Phase;
  description: string;
}

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
  { label: 'Filters', phase: 1, description: 'SQL filters over trip_packages' },
  { label: 'MCP', phase: 2, description: 'Catalog access through MCP tools' },
  { label: 'Intent', phase: 3, description: 'Hybrid retrieval and specialist routing' },
  { label: 'Personal', phase: 4, description: 'Traveler memory, RLS, and AgentCore' },
];

export function phaseLabelFor(phase: Phase): ShowcasePhaseLabel {
  return SHOWCASE_PHASES.find((p) => p.phase === phase)?.label ?? 'Personal';
}

function tripPackageToProduct(pkg: TripPackage): Product {
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

export function packagesResponseToRecommendations(input: Product[] | TripPackage[] | null | undefined): Product[] {
  if (!input?.length) return SHOWCASE_FALLBACK_RECOMMENDATIONS;
  const normalized = input.map((item) =>
    'package_id' in item ? tripPackageToProduct(item) : item,
  );
  return normalized.slice(0, 6);
}

export function memoryResponseToFacts(response: MemoryProfileResponse | null | undefined): LongTermMemoryFact[] {
  return response?.facts?.length ? response.facts : SHOWCASE_FALLBACK_FACTS;
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
