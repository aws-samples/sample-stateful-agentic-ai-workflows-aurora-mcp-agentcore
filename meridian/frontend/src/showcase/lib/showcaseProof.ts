import type { Phase, Product } from '../../types';
import type { ShowcaseTraceSpan } from './showcaseAdapters';

export type ProofStatus = 'locked' | 'ready' | 'observed';

export interface PhaseProof {
  phase: Phase;
  headline: string;
  dataPath: string;
  auroraCapability: string;
  agentBoundary: string;
  proof: string;
  source: string;
}

export interface AuroraEvidence {
  key: 'sql' | 'mcp' | 'vector' | 'rerank' | 'runtime' | 'rls' | 'checkpoint';
  label: string;
  value: string;
  detail: string;
  status: ProofStatus;
}

export interface McpContract {
  server: string;
  tool: string;
  request: string;
  auroraOperation: string;
  result: string;
  observed: boolean;
}

export interface WorkflowStateProof {
  status: 'ready' | 'running' | 'checkpointed';
  intent: string;
  path: string[];
  visited: string[];
  nextNode: string;
  checkpoint: string;
  checkpointCount: number;
  table: string;
}

export const PHASE_PROOFS: Record<Phase, PhaseProof> = {
  1: {
    phase: 1,
    headline: 'Direct Aurora query',
    dataPath: 'Prompt -> SQL filters -> trip_packages',
    auroraCapability: 'RDS Data API executes scoped catalog reads.',
    agentBoundary: 'Single SQL agent owns parsing and query execution.',
    proof: 'SQL text and row count appear in the trace.',
    source: 'backend/agents/sql_01/agent.py',
  },
  2: {
    phase: 2,
    headline: 'Aurora behind MCP tools',
    dataPath: 'Prompt -> MCP tools/list -> tools/call -> Aurora',
    auroraCapability: 'Same Aurora schema, exposed through tool contracts.',
    agentBoundary: 'Generic postgres-mcp plus custom meridian-concierge MCP.',
    proof: 'Tool name, request args, and Aurora operation are visible.',
    source: 'backend/mcp/concierge_server.py',
  },
  3: {
    phase: 3,
    headline: 'Intent retrieval',
    dataPath: 'Prompt -> embedding -> pgvector + tsvector -> rerank',
    auroraCapability: 'Aurora PostgreSQL stores vectors and full-text indexes.',
    agentBoundary: 'Supervisor routes to specialist retrieval agents.',
    proof: 'Candidate retrieval and Cohere rerank spans land in order.',
    source: 'backend/agents/retrieval_03/search_agent.py',
  },
  4: {
    phase: 4,
    headline: 'Production trust boundary',
    dataPath: 'Identity -> traveler grant -> RLS-scoped Aurora transaction',
    auroraCapability: 'Aurora grants the workload access to Alex, then RLS isolates rows.',
    agentBoundary: 'AgentCore runtime, gateway, memory, and identity adapters.',
    proof: 'ALLOW Alex, DENY Jordan, then scoped row counts and audit records.',
    source: 'backend/memory/store.py',
  },
  5: {
    phase: 5,
    headline: 'Durable workflow',
    dataPath: 'Classify -> branch -> worker node -> checkpoint -> synthesize',
    auroraCapability: 'Aurora stores LangGraph checkpoints between nodes.',
    agentBoundary: 'LangGraph makes routing explicit and resumable.',
    proof: 'Executed node path and PostgresSaver checkpoint are visible.',
    source: 'backend/agents/orchestration_05/workflow.py',
  },
};

const WORKFLOW_PATHS: Record<string, string[]> = {
  search: ['classify', 'search', 'synthesize'],
  plan: ['classify', 'search', 'availability', 'synthesize'],
  availability: ['classify', 'availability', 'synthesize'],
  memory_recall: ['classify', 'memory_recall', 'synthesize'],
};

export function getPhaseProof(phase: Phase): PhaseProof {
  return PHASE_PROOFS[phase] ?? PHASE_PROOFS[5];
}

export function deriveAuroraEvidence({
  selectedPhase,
  traceSpans,
  recommendations,
}: {
  selectedPhase: Phase;
  traceSpans: ShowcaseTraceSpan[];
  recommendations: Product[];
}): AuroraEvidence[] {
  const sqlSpans = traceSpans.filter((s) => Boolean(s.sql) || s.type === 'database');
  const mcpSpans = traceSpans.filter((s) => /mcp|tools\/call|postgres-mcp|meridian-concierge/i.test(spanText(s)));
  const vectorSpans = traceSpans.filter((s) => /pgvector|semantic_trip_search|embedding|hybrid|tsvector/i.test(spanText(s)));
  const rerankSpans = traceSpans.filter((s) => /rerank|rank/i.test(spanText(s)));
  const rlsSpans = traceSpans.filter((s) => /rls|scoped|identity|traveler_preferences|conversation_messages|persist[_ ]turn|agentcore/i.test(spanText(s)));
  const runtimeSpans = traceSpans.filter((s) => /agentcore|strands|runtime|gateway|identity/i.test(spanText(s)));
  const checkpointSpans = traceSpans.filter(isCheckpointSpan);
  const checkpointKind =
    traceSpans
      .map((span) => fieldValue(span, 'checkpointer'))
      .find(Boolean) ?? 'PostgresSaver (Aurora)';
  const checkpointStore =
    traceSpans
      .map((span) => fieldValue(span, 'checkpoint_store'))
      .find(Boolean) ??
    (checkpointKind.toLowerCase().includes('memorysaver')
      ? 'process memory'
      : 'langgraph_checkpoints');
  const hasRankDeltas = recommendations.some((p) => p.rank_delta != null || p.pre_rerank_position != null);

  return [
    {
      key: 'sql',
      label: 'SQL rows',
      value: sqlSpans.length ? `${sqlSpans.length} span${plural(sqlSpans.length)}` : 'ready',
      detail: sqlSpans.length
        ? `${recommendations.length} trip result${plural(recommendations.length)} surfaced`
        : 'Aurora query evidence appears after the first run',
      status: statusFor(true, sqlSpans.length > 0),
    },
    {
      key: 'mcp',
      label: 'MCP calls',
      value: mcpSpans.length ? `${mcpSpans.length} call${plural(mcpSpans.length)}` : selectedPhase >= 2 ? 'ready' : 'later',
      detail: mcpSpans.length
        ? `${countMcpServers(traceSpans)} server${plural(countMcpServers(traceSpans))} involved`
        : 'Tool contracts unlock at the MCP phase',
      status: statusFor(selectedPhase >= 2, mcpSpans.length > 0),
    },
    {
      key: 'vector',
      label: 'Hybrid retrieval',
      value: vectorSpans.length ? 'pgvector + FTS' : selectedPhase >= 3 ? 'ready' : 'later',
      detail: firstMatchingDetail(vectorSpans, /candidate|embedding|pgvector|tsvector/i) ?? 'Semantic vectors plus PostgreSQL full-text search',
      status: statusFor(selectedPhase >= 3, vectorSpans.length > 0),
    },
    {
      key: 'rerank',
      label: 'Rerank',
      value: rerankSpans.length || hasRankDeltas ? 'applied' : selectedPhase >= 3 ? 'ready' : 'later',
      detail: firstMatchingDetail(rerankSpans, /rerank/i) ?? 'Cohere rerank orders the candidate set',
      status: statusFor(selectedPhase >= 3, rerankSpans.length > 0 || hasRankDeltas),
    },
    {
      key: 'runtime',
      label: 'Agent runtime',
      value: runtimeSpans.length ? 'AgentCore + Strands' : selectedPhase >= 4 ? 'ready' : 'later',
      detail: runtimeSpans.length
        ? 'Identity, runtime, gateway, and agent execution observed'
        : 'AgentCore and Strands unlock at Production',
      status: statusFor(selectedPhase >= 4, runtimeSpans.length > 0),
    },
    {
      key: 'rls',
      label: 'Governance',
      value: rlsSpans.length ? 'workload granted + scoped' : selectedPhase >= 4 ? 'ready' : 'later',
      detail: rlsSpans.length ? 'Workload grant, RLS scope, and audit context enforced' : 'Workload authorization, RLS, and audit proof unlock at Production',
      status: statusFor(selectedPhase >= 4, rlsSpans.length > 0),
    },
    {
      key: 'checkpoint',
      label: 'Checkpoint',
      value: checkpointSpans.length ? `${checkpointSpans.length} saved` : selectedPhase >= 5 ? 'ready' : 'later',
      detail: checkpointSpans.length
        ? `Workflow state checkpointed via ${checkpointKind} (${checkpointStore})`
        : 'LangGraph checkpoints unlock at Workflow',
      status: statusFor(selectedPhase >= 5, checkpointSpans.length > 0),
    },
  ];
}

export function deriveMcpContracts(traceSpans: ShowcaseTraceSpan[]): McpContract[] {
  const observed = traceSpans
    .filter((span) => /postgres-mcp|meridian-concierge/i.test(span.name))
    .map(contractFromSpan)
    .filter((contract): contract is McpContract => Boolean(contract));

  return observed.length ? observed : defaultMcpContracts();
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
  const path = WORKFLOW_PATHS[intent] ?? ['classify', 'branch', 'synthesize'];
  const checkpointSpans = traceSpans.filter(isCheckpointSpan);
  const checkpoint =
    traceSpans
      .map((span) => fieldValue(span, 'checkpointer'))
      .find(Boolean) ?? 'PostgresSaver (Aurora)';
  const table =
    traceSpans
      .map((span) => fieldValue(span, 'checkpoint_store'))
      .find(Boolean) ??
    (checkpoint.toLowerCase().includes('memorysaver')
      ? 'process memory'
      : 'langgraph_checkpoints');
  const nextNode = path.find((node) => !visited.includes(node)) ?? 'complete';

  return {
    status: checkpointSpans.length ? 'checkpointed' : visited.length ? 'running' : 'ready',
    intent,
    path,
    visited,
    nextNode,
    checkpoint,
    checkpointCount: checkpointSpans.length,
    table,
  };
}

function contractFromSpan(span: ShowcaseTraceSpan): McpContract | null {
  const name = span.name;
  const text = spanText(span);
  if (/server discovered/i.test(name)) return null;

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
      auroraOperation: 'Custom MCP composes package, price, FX, and seasonal facts.',
      result: 'Typed domain readout plus product cards.',
      observed: false,
    },
  ];
}

function domainOperation(tool: string): string {
  if (/compare/i.test(tool)) return 'Read package rows and compare price, region, and fit.';
  if (/currency|fx/i.test(tool)) return 'Convert Aurora-backed package prices into the requested currency.';
  if (/seasonal/i.test(tool)) return 'Aggregate seasonal price bands from the catalog.';
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
  const field = fieldValue(span, 'node');
  if (field) return field;
  const match = /Workflow node:\s*(classify|search|availability|memory_recall|synthes)/i.exec(span.name);
  if (!match) return null;
  return match[1].startsWith('synthes') ? 'synthesize' : match[1];
}

function fieldValue(span: ShowcaseTraceSpan, label: string): string | null {
  const found = span.fields.find((f) => f.label.toLowerCase() === label.toLowerCase());
  return found?.value ?? null;
}

function isCheckpointSpan(span: ShowcaseTraceSpan): boolean {
  return /checkpoint|langgraph_checkpoints/i.test(
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

function statusFor(unlocked: boolean, observed: boolean): ProofStatus {
  if (!unlocked) return 'locked';
  return observed ? 'observed' : 'ready';
}

function countMcpServers(spans: ShowcaseTraceSpan[]): number {
  const servers = new Set<string>();
  spans.forEach((span) => {
    if (/postgres-mcp|awslabs/i.test(spanText(span))) servers.add('postgres-mcp');
    if (/meridian-concierge/i.test(spanText(span))) servers.add('meridian-concierge');
  });
  return servers.size;
}

function firstMatchingDetail(spans: ShowcaseTraceSpan[], re: RegExp): string | null {
  return spans.find((s) => re.test(spanText(s)))?.details ?? null;
}

function lastToken(name: string): string {
  return name.split('·').pop()?.trim() ?? name;
}

function compactSql(sql: string): string {
  return sql.replace(/\s+/g, ' ').trim();
}

function plural(count: number): string {
  return count === 1 ? '' : 's';
}

function unique<T>(items: T[]): T[] {
  return Array.from(new Set(items));
}
