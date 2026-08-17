/**
 * Map backend ActivityEntry[] → StageSpan[] for cinematic trace UIs
 * used by the Demo Stage.
 */
import type { ActivityEntry, TraceSpanCategory } from '../types';
import type { StageSpan, StageSpanKind, StageSystemId } from '../stage/types';

const CATEGORY_TO_KIND: Record<TraceSpanCategory, StageSpanKind> = {
  runtime: 'orchestration',
  memory_short: 'memory',
  memory_long: 'memory',
  orchestration: 'orchestration',
  model: 'model',
  tool: 'tool',
  data: 'data',
  synthesis: 'synthesis',
  security: 'security',
};

const KIND_TO_SYSTEM: Record<StageSpanKind, StageSystemId> = {
  orchestration: 'orchestration',
  memory: 'memory',
  tool: 'mcp',
  data: 'aurora',
  model: 'model',
  synthesis: 'orchestration',
  security: 'governance',
};

function inferKindFromTitle(entry: ActivityEntry): StageSpanKind {
  const t = (entry.title ?? '').toLowerCase();
  const type = (entry.activity_type ?? '').toLowerCase();
  if (t.includes('memor') || type === 'embedding') return 'memory';
  if (t.includes('sql') || type === 'database') return 'data';
  if (t.includes('mcp') || type === 'tool_call' || type === 'mcp') return 'tool';
  if (t.includes('plan') || t.includes('orchestr') || type === 'delegation') return 'orchestration';
  if (t.includes('claude') || t.includes('compose') || t.includes('bedrock') || type === 'reasoning') return 'model';
  if (t.includes('polic') || type === 'security') return 'security';
  if (t.includes('respond') || type === 'result') return 'synthesis';
  return 'orchestration';
}

export function activityToStageSpan(entry: ActivityEntry, idx: number): StageSpan {
  const category = entry.telemetry?.category;
  const kind: StageSpanKind = category
    ? CATEGORY_TO_KIND[category] ?? 'orchestration'
    : inferKindFromTitle(entry);
  const system: StageSystemId = KIND_TO_SYSTEM[kind];
  const latency = entry.execution_time_ms ?? 60 + (idx % 4) * 20;

  return {
    id: entry.id || `sp_${idx}`,
    kind,
    system,
    name: entry.title,
    detail: entry.details ?? entry.telemetry?.component,
    latencyMs: latency,
    status: entry.telemetry?.status ?? 'ok',
    component: entry.agent_name ?? entry.telemetry?.component,
    source: entry.agent_file ?? entry.telemetry?.component,
    output: entry.details,
    tokensIn: entry.telemetry?.tokens?.input,
    tokensOut: entry.telemetry?.tokens?.output,
  };
}

export function activitiesToStageSpans(activities: ActivityEntry[]): StageSpan[] {
  return activities.map(activityToStageSpan);
}

export function buildReasoningChain(spans: StageSpan[]): string {
  if (!spans.length) return 'awaiting trace…';
  return spans
    .slice(0, 6)
    .map((s) => s.name.replace(/\s+/g, ' ').toLowerCase().replace(/[^a-z0-9]+/g, '_').slice(0, 24))
    .join(' → ');
}

export function sumSpanLatency(spans: StageSpan[]): number {
  return spans.reduce((acc, s) => acc + (s.latencyMs ?? 0), 0);
}
