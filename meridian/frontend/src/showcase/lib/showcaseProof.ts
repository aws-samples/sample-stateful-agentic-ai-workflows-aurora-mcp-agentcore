import type { ShowcaseTraceSpan } from './showcaseAdapters';

export interface McpContract {
  server: string;
  tool: string;
  request: string;
  auroraOperation: string;
  result: string;
  observed: boolean;
}

export interface WorkflowStateProof {
  status: 'ready' | 'running' | 'checkpointed' | 'ephemeral';
  intent: string;
  path: string[];
  visited: string[];
  nextNode: string;
  checkpoint: string;
  checkpointCount: number;
  table: string;
  durable: boolean;
  /** The LangGraph thread. Identical before and after a restart is the proof. */
  threadId: string;
  /** A committed courtesy hold, if the recovery path reached the hold node. */
  holdId: string;
  holdExpiresAt: string;
  holdSeatsRemaining: string;
  holdCreatedAt: string;
  holdObservedAt: string;
  holdStatus: string;
}

const WORKFLOW_PATHS: Record<string, string[]> = {
  search: ['classify', 'search', 'synthesize'],
  plan: ['classify', 'search', 'availability', 'synthesize'],
  availability: ['classify', 'availability', 'synthesize'],
  memory_recall: ['classify', 'memory_recall', 'synthesize'],
};

/** The classification trace declares recovery before any business write runs. */
export function workflowPathFor(intent: string, spans: ShowcaseTraceSpan[]): string[] {
  const observed = spans.filter(span => span.status === 'ok');
  const recovery = observed.some(span => fieldValue(span, 'recovery') === 'true'
    || ['prepare_hold', 'hold'].includes(workflowNodeFromSpan(span) ?? ''));
  if (intent === 'plan' && recovery) return ['classify', 'search', 'availability', 'prepare_hold', 'hold', 'synthesize'];
  return WORKFLOW_PATHS[intent] ?? ['classify', 'branch', 'synthesize'];
}

export function deriveMcpContracts(traceSpans: ShowcaseTraceSpan[]): McpContract[] {
  const observed = traceSpans
    .filter((span) => span.status === 'ok' && /postgres-mcp|meridian-concierge/i.test(span.name))
    .map(contractFromSpan)
    .filter((contract): contract is McpContract => Boolean(contract));

  return observed.length ? observed : defaultMcpContracts();
}

/** Explicit run telemetry wins; known legacy saver names are only a fallback. */
export function hasDurableCheckpoint(traceSpans: ShowcaseTraceSpan[]): boolean {
  const spans = traceSpans.filter(isCheckpointSpan);
  const explicit = spans.flatMap(span => span.fields.filter(field => field.label === 'checkpoint_durable'));
  if (explicit.length) return explicit[explicit.length - 1].value === 'true';
  return spans.some(span => /\b(AuroraDataApiSaver|AsyncPostgresSaver|PostgresSaver)\b/i.test(fieldValue(span, 'checkpointer') ?? span.name));
}

export function deriveWorkflowState(traceSpans: ShowcaseTraceSpan[]): WorkflowStateProof {
  const visited = unique(
    traceSpans
      .map(workflowNodeFromSpan)
      .filter((node): node is string => Boolean(node)),
  );
  const intent =
    traceSpans
      .map((span) => fieldValue(span, 'intent'))
      .find(Boolean) ?? 'awaiting prompt';
  const path = workflowPathFor(intent, traceSpans);
  const checkpointSpans = traceSpans.filter(isCheckpointSpan);
  const checkpoint =
    traceSpans
      .map((span) => fieldValue(span, 'checkpointer'))
      .find(Boolean) ?? 'not observed';
  const durable = hasDurableCheckpoint(traceSpans);
  const table =
    traceSpans
      .map((span) => fieldValue(span, 'checkpoint_store'))
      .find(Boolean) ??
    (checkpoint.toLowerCase().includes('memorysaver')
      ? 'process memory'
      : durable ? 'checkpoints' : 'not observed');
  const holdSpan = [...traceSpans].reverse().find(span => fieldValue(span, 'hold_id'));
  const holdField = (key: string) => holdSpan ? fieldValue(holdSpan, key) ?? '' : '';
  const nextNode = path.find((node) => !visited.includes(node)) ?? 'complete';

  return {
    status: checkpointSpans.length
      ? durable ? 'checkpointed' : 'ephemeral'
      : visited.length ? 'running' : 'ready',
    intent,
    path,
    visited,
    nextNode,
    threadId:
      traceSpans
        .map((span) => fieldValue(span, 'thread_id'))
        .find(Boolean) ?? '',
    holdId: holdField('hold_id'),
    holdExpiresAt: holdField('expires_at'),
    holdCreatedAt: holdField('hold_created_at'),
    holdObservedAt: holdField('hold_observed_at'),
    holdStatus: holdField('hold_status'),
    holdSeatsRemaining: holdField('seats_remaining'),
    checkpoint,
    checkpointCount: checkpointSpans.length,
    table,
    durable,
  };
}

function contractFromSpan(span: ShowcaseTraceSpan): McpContract | null {
  const name = span.name;
  const text = spanText(span);
  if (/server discovered|session connected/i.test(name)) return null;

  if (/postgres-mcp/i.test(name)) {
    const tool = lastToken(name) || 'run_query';
    return {
      server: 'awslabs.postgres-mcp-server',
      tool,
      request: span.sql ? compactSql(span.sql) : span.details ?? 'tools/call over MCP',
      auroraOperation: tool === 'connect_to_database'
        ? 'Open Aurora PostgreSQL connection through the MCP transport.'
        : 'Execute SQL against trip_packages through RDS Data API.',
      result: span.details ?? 'Aurora rows returned as MCP content.',
      observed: true,
    };
  }

  if (/meridian-concierge/i.test(name)) {
    const tool = lastToken(name) || 'domain_tool';
    const { request, result } = splitDomainDetails(span.details);
    return {
      server: 'meridian-concierge',
      tool,
      request,
      auroraOperation: domainOperation(tool),
      result: result || 'Typed domain response returned to the agent.',
      observed: true,
    };
  }

  if (/tools\/call|mcp/i.test(text)) {
    return {
      server: span.agent ?? 'MCP server',
      tool: name,
      request: span.sql ? compactSql(span.sql) : span.details ?? 'tools/call',
      auroraOperation: 'MCP tool call reached Aurora-backed data.',
      result: span.details ?? 'Tool result returned.',
      observed: true,
    };
  }

  return null;
}

function defaultMcpContracts(): McpContract[] {
  return [
    {
      server: 'awslabs.postgres-mcp-server',
      tool: 'run_query',
      request: '{ sql: "SELECT ... FROM trip_packages" }',
      auroraOperation: 'RDS Data API executes SQL against Aurora PostgreSQL.',
      result: 'Trip rows return as MCP content blocks.',
      observed: false,
    },
    {
      server: 'meridian-concierge',
      tool: 'compare_packages / currency_convert',
      request: '{ package_ids, target_currency }',
      auroraOperation: 'Custom MCP composes package, price, FX, and price-range facts.',
      result: 'Typed domain readout plus product cards.',
      observed: false,
    },
  ];
}

function domainOperation(tool: string): string {
  if (/compare/i.test(tool)) return 'Read package rows and compare price, region, and fit.';
  if (/currency|fx/i.test(tool)) return 'Convert Aurora-backed package prices into the requested currency.';
  if (/price_range|price range/i.test(tool)) return 'Aggregate the real price range for a destination from the catalog.';
  if (/inventory|region/i.test(tool)) return 'Count available catalog inventory by region.';
  if (/loyalty/i.test(tool)) return 'Read loyalty context beside trip recommendations.';
  return 'Execute a custom Aurora-backed travel-domain tool.';
}

function splitDomainDetails(details?: string): { request: string; result: string } {
  if (!details) return { request: 'args={...}', result: '' };
  const match = /^args=(.*?)\s+·\s+(.*)$/s.exec(details);
  if (!match) return { request: details, result: '' };
  return { request: `args=${match[1]}`, result: match[2] };
}

function workflowNodeFromSpan(span: ShowcaseTraceSpan): string | null {
  if (span.status !== 'ok') return null;
  const field = fieldValue(span, 'node');
  if (field) return field;
  const match = /Workflow node:\s*(classify|search|availability|memory_recall|prepare_hold|hold|synthes)/i.exec(span.name);
  if (!match) return null;
  return match[1].startsWith('synthes') ? 'synthesize' : match[1];
}

function fieldValue(span: ShowcaseTraceSpan, label: string): string | null {
  const found = span.fields.find((f) => f.label.toLowerCase() === label.toLowerCase());
  return found?.value ?? null;
}

function isCheckpointSpan(span: ShowcaseTraceSpan): boolean {
  return span.status === 'ok' && /checkpoint/i.test(
    [span.name, span.details, span.sql, span.component].filter(Boolean).join(' '),
  );
}

function spanText(span: ShowcaseTraceSpan): string {
  return [
    span.name,
    span.category,
    span.type,
    span.agent,
    span.file,
    span.component,
    span.sql,
    span.details,
    ...span.fields.flatMap((f) => [f.label, f.value]),
  ]
    .filter(Boolean)
    .join(' ');
}

function lastToken(name: string): string {
  return name.split('·').pop()?.trim() ?? name;
}

function compactSql(sql: string): string {
  return sql.replace(/\s+/g, ' ').trim();
}

function unique<T>(items: T[]): T[] {
  return Array.from(new Set(items));
}
