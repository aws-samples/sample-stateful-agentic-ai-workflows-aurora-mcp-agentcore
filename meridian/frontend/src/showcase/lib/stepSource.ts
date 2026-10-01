import type { ShowcaseTraceSpan } from './showcaseAdapters';

// Joins a value to its unit so a narrow stage never wraps "601" away from "ms".
const NO_BREAK = '\u00a0';

/** A recorded duration as the room reads it: whole milliseconds under a second,
 *  one decimal second above. No measured value, no number: null. */
export function formatLatency(ms: number | null | undefined): string | null {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return null;
  const whole = Math.round(ms);
  if (whole === 0) return `<1${NO_BREAK}ms`;
  if (whole < 1000) return `${whole}${NO_BREAK}ms`;
  return `${(Math.round(ms / 100) / 10).toFixed(1)}${NO_BREAK}s`;
}

type ServiceRule = { service: string; matches: (span: ShowcaseTraceSpan) => boolean };

const component = (span: ShowcaseTraceSpan) => span.component ?? '';
const named = (span: ShowcaseTraceSpan) => `${span.name} ${component(span)}`;

const STRANDS_SUPERVISOR = /^(Supervisor (processing|completed)|RetrievalAgent invoked)/i;
const BEDROCK_CALL = /^Bedrock|concierge polish|embedding|^Cohere rerank/i;
const AURORA_QUERY = new RegExp('^(Semantic search|Hybrid candidate|Catalog card|'
  + 'Lexical candidates|PackageAgent: (Finding|Checking)|Aurora recall)', 'i');

// First match wins. Routing and in-memory steps fall through to the app itself.
const SERVICE_RULES: ServiceRule[] = [
  { service: 'Meridian app', matches: span => /^Processing with/i.test(span.name) },
  {
    service: 'Cedar',
    matches: span => span.status === 'denied' || /AgentCore Policy/i.test(component(span)),
  },
  { service: 'AgentCore Gateway', matches: span => /AgentCore Gateway/i.test(named(span)) },
  { service: 'AgentCore Runtime', matches: span => /AgentCore Runtime/i.test(named(span)) },
  { service: 'AgentCore Memory', matches: span => /AgentCore Memory/i.test(named(span)) },
  { service: 'AgentCore Identity', matches: span => /AgentCore Identity/i.test(component(span)) },
  { service: 'AWS STS', matches: span => /AWS STS/i.test(component(span)) },
  { service: 'LangGraph MemorySaver', matches: span => /MemorySaver/i.test(component(span)) },
  { service: 'Aurora RLS', matches: span => /^Aurora RLS/i.test(component(span)) },
  { service: 'Aurora Data API', matches: span => /^Aurora/i.test(component(span)) },
  { service: 'MCP', matches: span => /^(postgres-mcp|meridian-concierge|MCP )/i.test(span.name) },
  { service: 'Strands · Bedrock', matches: span => STRANDS_SUPERVISOR.test(span.name) },
  { service: 'Bedrock', matches: span => BEDROCK_CALL.test(span.name) },
  {
    service: 'LangGraph',
    matches: span => /LangGraph/i.test(component(span)) || /^Workflow node:/i.test(span.name),
  },
  {
    service: 'Aurora Data API',
    matches: span => Boolean(span.sql) || span.agent === 'SQLAgent' || span.type === 'database'
      || AURORA_QUERY.test(span.name),
  },
];

/** The service that did the work a span records. */
export function stepService(span: ShowcaseTraceSpan): string {
  return SERVICE_RULES.find(rule => rule.matches(span))?.service ?? 'Meridian app';
}

// Steps a model wrote: the Retrieval reply's polish, and the Runtime's turn.
const MODEL_STEP = /concierge polish|AgentCore Runtime · turn complete/i;

function stepDetail(span: ShowcaseTraceSpan, service: string, replyModel?: string): string | null {
  const decision = span.fields.find(field => field.label === 'cedar_decision')?.value;
  if (service === 'Cedar') return decision ?? 'deny';
  if (service === 'AgentCore Gateway' && decision) return `Cedar ${decision}`;
  if (service === 'MCP') return span.name.match(/postgres-mcp|meridian-concierge/i)?.[0] ?? null;
  return MODEL_STEP.test(span.name) && replyModel ? replyModel : null;
}

/** "Service · detail · time" for one recorded step, for example
 *  "Aurora Data API · 38 ms" or "Bedrock · Claude Sonnet 5 · 1.4 s". The model
 *  is the one that wrote this reply; the time is only ever a measured one. */
export function stepSourceLabel(span: ShowcaseTraceSpan, replyModel?: string): string {
  const service = stepService(span);
  return [service, stepDetail(span, service, replyModel), formatLatency(span.latencyMs)]
    .filter(Boolean)
    .join(' · ');
}
