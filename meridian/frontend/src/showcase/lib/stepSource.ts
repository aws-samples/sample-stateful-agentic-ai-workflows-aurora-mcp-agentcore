import type { ShowcaseTraceSpan } from './showcaseAdapters';
import {
  isLegacyGraphComponent, isSnapshotTitle, isSqlStep, RUNTIME_TURN_COMPLETE,
} from './spanTitles';

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
// The Gateway's request interceptor refused the call before Cedar ever saw it.
const identityRefused = (span: ShowcaseTraceSpan) =>
  span.fields.some(field => field.label === 'refused_by' && field.value === 'identity_check');

// The Retrieval agent's own turn. Saved journeys and earlier backends carry the supervisor titles.
const STRANDS_AGENT = new RegExp('^(Retrieval agent: (turn started|reply written)|'
  + 'Supervisor (processing|completed)|RetrievalAgent invoked)', 'i');
const CONCIERGE_SERVER = /meridian-concierge MCP server/i;
const BEDROCK_CALL = /^Bedrock|concierge polish|embedding|^Cohere rerank/i;
const AURORA_QUERY = new RegExp('^(Semantic search|Hybrid candidate|Catalog card|'
  + 'Lexical candidates|(Availability|PackageAgent): (Finding|Checking)|Aurora recall)', 'i');

// First match wins. Routing and in-memory steps fall through to the app itself.
const SERVICE_RULES: ServiceRule[] = [
  { service: 'Meridian app', matches: span => /^Processing with/i.test(span.name) },
  { service: 'AgentCore Gateway', matches: identityRefused },
  {
    service: 'Cedar',
    matches: span => span.status === 'denied' || /AgentCore Policy/i.test(component(span)),
  },
  { service: 'AgentCore Gateway', matches: span => /AgentCore Gateway/i.test(named(span)) },
  { service: 'AgentCore Runtime', matches: span => /AgentCore Runtime/i.test(named(span)) },
  { service: 'AgentCore Memory', matches: span => /AgentCore Memory/i.test(named(span)) },
  { service: 'AWS STS', matches: span => /AWS STS/i.test(component(span)) },
  { service: 'Worker memory', matches: span => /MemorySaver/i.test(component(span)) },
  { service: 'Aurora Data API', matches: span => isSnapshotTitle(span.name) },
  { service: 'Aurora RLS', matches: span => /^Aurora RLS/i.test(component(span)) },
  { service: 'Aurora Data API', matches: span => /^Aurora/i.test(component(span)) },
  { service: 'MCP', matches: span => /^(postgres-mcp|meridian-concierge|MCP )/i.test(span.name) },
  { service: 'MCP', matches: span => CONCIERGE_SERVER.test(component(span)) },
  { service: 'Strands on Bedrock', matches: span => STRANDS_AGENT.test(span.name) },
  { service: 'Bedrock', matches: span => BEDROCK_CALL.test(span.name) },
  {
    service: 'Strands Graph',
    matches: span => /^Strands Graph/i.test(component(span))
      || isLegacyGraphComponent(component(span))
      || /^Workflow node:/i.test(span.name),
  },
  {
    service: 'Aurora Data API',
    matches: span => Boolean(span.sql) || isSqlStep(span.agent) || span.type === 'database'
      || AURORA_QUERY.test(span.name),
  },
];

/** The service that did the work a span records. */
export function stepService(span: ShowcaseTraceSpan): string {
  return SERVICE_RULES.find(rule => rule.matches(span))?.service ?? 'Meridian app';
}

// Steps a model wrote: the Retrieval agent's reply (and the polish saved journeys carry), and the Runtime's turn.
const MODEL_STEP = new RegExp(
  `concierge polish|Retrieval agent: reply written|${RUNTIME_TURN_COMPLETE.source}`, 'i');

function stepDetail(span: ShowcaseTraceSpan, service: string, replyModel?: string): string | null {
  const decision = span.fields.find(field => field.label === 'cedar_decision')?.value;
  if (service === 'Cedar') return decision ?? 'deny';
  if (identityRefused(span)) return 'identity check refused';
  if (service === 'AgentCore Gateway' && decision) return `Cedar ${decision}`;
  if (service === 'MCP') {
    const fromName = span.name.match(/postgres-mcp|meridian-concierge/i)?.[0];
    return fromName ?? (CONCIERGE_SERVER.test(component(span)) ? 'meridian-concierge' : null);
  }
  return MODEL_STEP.test(span.name) && replyModel ? replyModel : null;
}

/** "Service, detail, time" for one recorded step, for example
 *  "Aurora Data API, 38 ms" or "Bedrock, Claude Sonnet 5, 1.4 s". The model
 *  is the one that wrote this reply; the time is only ever a measured one. */
export function stepSourceLabel(span: ShowcaseTraceSpan, replyModel?: string): string {
  const service = stepService(span);
  return [service, stepDetail(span, service, replyModel), formatLatency(span.latencyMs)]
    .filter(Boolean)
    .join(', ');
}
