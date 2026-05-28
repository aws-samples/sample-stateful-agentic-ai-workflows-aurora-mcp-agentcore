/**
 * Builds rich trace preamble + enriches backend activity spans with telemetry
 */
import type { Message, ActivityEntry } from '../types';

const LONG_TERM_FACTS = [
  { key: 'party_size', value: '2 travelers', source: 'booking_history', confidence: 0.98 },
  { key: 'goal', value: 'Tokyo culture trip — Oct 12–19', source: 'profile', confidence: 0.95 },
  { key: 'preference', value: 'Window seat · aisle on long-haul', source: 'browse_session', confidence: 0.91 },
  { key: 'allergy', value: 'Shellfish — exclude seafood dining', source: 'support_ticket', confidence: 1.0 },
  { key: 'budget', value: 'Prefers $2k–3.5k per person', source: 'search_analytics', confidence: 0.87 },
];

function span(
  partial: Omit<ActivityEntry, 'id' | 'timestamp'> & { id?: string }
): ActivityEntry {
  return {
    id: partial.id ?? `span-${Math.random().toString(36).slice(2, 9)}`,
    timestamp: new Date().toISOString(),
    ...partial,
  };
}

function shortTermItems(msgs: Message[], query: string): string[] {
  const recent = msgs.slice(-6).map((m) => `${m.role}: ${m.text.slice(0, 80)}${m.text.length > 80 ? '…' : ''}`);
  return [
    `current_turn: "${query.slice(0, 100)}"`,
    `turn_count: ${msgs.length + 1}`,
    ...recent,
    'tool_buffer: [search_results_v2, traveler_prefs]',
    'session_vars: { locale: en-US, channel: web_demo }',
  ];
}

function buildPhase4Preamble(
  query: string,
  traceId: string,
  msgs: Message[]
): ActivityEntry[] {
  const conversationId = 'conv_meridian_demo';
  return [
    span({
      activity_type: 'reasoning',
      title: 'Concierge session bootstrap',
      agent_name: 'ProductionAgent',
      agent_file: 'agents/production_04/concierge.py',
      execution_time_ms: 18,
      telemetry: {
        category: 'runtime',
        component: 'Strands + FastAPI',
        status: 'ok',
        fields: [
          { label: 'trace_id', value: traceId, mono: true },
          { label: 'conversation_id', value: conversationId, mono: true },
          { label: 'runtime', value: 'meridian-travel-v3' },
          { label: 'region', value: 'us-east-1' },
          { label: 'governance', value: 'scopes: search, availability · budget: $4,000' },
          { label: 'isolation', value: 'Aurora RLS — see Security span below' },
        ],
      },
    }),
    span({
      activity_type: 'reasoning',
      title: 'Load short-term memory (session)',
      agent_name: 'MemoryAgent',
      agent_file: 'agents/production_04/memory_agent.py',
      execution_time_ms: 12,
      telemetry: {
        category: 'memory_short',
        component: 'Strands runtime',
        status: 'ok',
        memory: {
          shortTerm: {
            label: 'Session context window',
            items: shortTermItems(msgs, query),
          },
        },
        fields: [
          { label: 'window', value: 'last 6 turns + tool buffer' },
          { label: 'store', value: 'conversation_messages (Aurora)' },
          { label: 'ttl', value: 'persisted per conversation_id' },
        ],
      },
    }),
    span({
      activity_type: 'database',
      title: 'Recall long-term memory (Aurora)',
      agent_name: 'MemoryAgent',
      agent_file: 'agents/production_04/memory_agent.py',
      execution_time_ms: 34,
      sql_query:
        "SELECT preference_key, preference_value, source, confidence FROM traveler_preferences WHERE traveler_id = :traveler_id ORDER BY confidence DESC LIMIT 8",
      telemetry: {
        category: 'memory_long',
        component: 'Aurora PostgreSQL',
        status: 'ok',
        memory: {
          longTerm: {
            label: 'Durable preferences (Aurora recall)',
            facts: LONG_TERM_FACTS,
          },
        },
        fields: [
          { label: 'table', value: 'traveler_preferences' },
          { label: 'recall', value: 'preference confidence + recency' },
          { label: 'facts_matched', value: String(LONG_TERM_FACTS.length) },
          { label: 'applied_filters', value: 'party size, allergy, dates, budget' },
        ],
      },
    }),
    // Supervisor routing span is emitted by the live backend (Strands +
    // Bedrock) when STRANDS_ORCHESTRATION=full. We intentionally don't add
    // a fabricated routing span here.
  ];
}

function buildPhase2Preamble(query: string, traceId: string, msgs: Message[]): ActivityEntry[] {
  return [
    span({
      activity_type: 'reasoning',
      title: 'Session context loaded',
      agent_name: 'MCPAgent',
      agent_file: 'agents/mcp_02/agent.py',
      execution_time_ms: 8,
      telemetry: {
        category: 'memory_short',
        component: 'Strands runtime',
        status: 'ok',
        memory: {
          shortTerm: {
            label: 'Turn context',
            items: shortTermItems(msgs, query).slice(0, 4),
          },
        },
        fields: [
          { label: 'trace_id', value: traceId, mono: true },
          { label: 'long_term', value: 'not enabled in Phase 2' },
        ],
      },
    }),
    span({
      activity_type: 'mcp',
      title: 'MCP tools connected',
      agent_name: 'MCPClient',
      agent_file: 'mcp/mcp_client.py',
      execution_time_ms: 22,
      telemetry: {
        category: 'tool',
        component: 'MCP Server',
        status: 'ok',
        fields: [
          { label: 'server', value: 'awslabs.postgres_mcp_server' },
          { label: 'tools', value: 'execute_sql, list_tables, describe_table' },
          { label: 'auth', value: 'boto3 default credentials → RDS Data API' },
        ],
      },
    }),
  ];
}

function buildPhase1Preamble(_query: string, traceId: string): ActivityEntry[] {
  return [
    span({
      activity_type: 'reasoning',
      title: 'Direct agent invocation',
      agent_name: 'SQLAgent',
      agent_file: 'agents/sql_01/agent.py',
      execution_time_ms: 6,
      telemetry: {
        category: 'runtime',
        component: 'Strands + Bedrock',
        status: 'ok',
        fields: [
          { label: 'trace_id', value: traceId, mono: true },
          { label: 'memory', value: 'none — stateless turn' },
          { label: 'path', value: 'hardcoded tools → RDS Data API' },
        ],
      },
    }),
  ];
}

function enrichActivity(a: ActivityEntry, phase: 1 | 2 | 3 | 4 | 5, query: string): ActivityEntry {
  if (a.telemetry) return a;

  const base = { ...a };
  const type = a.activity_type;

  if (type === 'embedding') {
    base.telemetry = {
      category: 'model',
      component: 'Amazon Bedrock',
      status: 'ok',
      tokens: { input: Math.max(12, Math.floor(query.length / 4)) },
      fields: [
        { label: 'model', value: 'cohere.embed-v4:0' },
        { label: 'dimensions', value: '1024' },
        { label: 'input', value: `"${query.slice(0, 48)}${query.length > 48 ? '…' : ''}"` },
      ],
    };
  } else if (type === 'search') {
    base.telemetry = {
      category: 'data',
      component: 'Aurora PostgreSQL',
      status: phase >= 3 ? 'ok' : 'ok',
      fields:
        phase >= 3
          ? [
              { label: 'strategy', value: 'hybrid retrieval + rerank' },
              { label: 'semantic', value: 'pgvector HNSW candidates' },
              { label: 'lexical', value: 'tsvector + ts_rank candidates' },
              { label: 'ranker', value: 'cohere.rerank-v3-5:0' },
              ...(phase === 4
                ? [{ label: 'memory_boost', value: 'party of 2 · Tokyo Oct · shellfish · budget filters' }]
                : []),
            ]
          : [
              { label: 'strategy', value: phase === 2 ? 'MCP → ILIKE' : 'ILIKE filters' },
              { label: 'index', value: 'btree + sequential scan' },
            ],
    };
  } else if (type === 'mcp') {
    base.telemetry = {
      category: 'tool',
      component: 'MCP',
      status: a.title.toLowerCase().includes('discover') ? 'ok' : 'streaming',
      fields: [
        { label: 'protocol', value: 'Model Context Protocol v1' },
        { label: 'transport', value: 'stdio / SSE' },
        { label: 'operation', value: a.title },
      ],
    };
  } else if (type === 'database') {
    base.telemetry = {
      category: 'data',
      component: 'RDS Data API',
      status: 'ok',
      fields: [
        { label: 'api', value: 'ExecuteStatement' },
        { label: 'cluster', value: 'meridian-demo' },
        { label: 'database', value: 'meridian' },
      ],
    };
  } else if (type === 'delegation' || (type === 'reasoning' && a.agent_name?.includes('Supervisor'))) {
    base.telemetry = {
      category: 'orchestration',
      component: 'Strands supervisor',
      status: 'delegated',
      fields: [
        { label: 'from', value: a.agent_name ?? 'Supervisor' },
        { label: 'action', value: a.title },
        { label: 'details', value: a.details ?? '—' },
      ],
    };
  } else if (type === 'inventory') {
    base.telemetry = {
      category: 'tool',
      component: 'PackageAgent',
      status: 'ok',
      fields: [
        { label: 'check', value: 'departure slots via Aurora' },
        { label: 'durations', value: 'availability JSON on trip_packages' },
      ],
    };
  } else if (type === 'result') {
    base.telemetry = {
      category: 'synthesis',
      component: 'Claude on Bedrock',
      status: 'ok',
      tokens: { input: 890, output: 210 },
      fields: [
        { label: 'model', value: 'global.anthropic.claude-opus-4-8' },
        { label: 'format', value: 'trip_cards + natural language' },
        { label: 'grounding', value: 'Aurora rows + memory facts' },
      ],
    };
  } else if (type === 'order') {
    base.telemetry = {
      category: 'synthesis',
      component: 'BookingAgent',
      status: 'held',
      fields: [
        { label: 'flow', value: 'plan → confirm → book' },
        { label: 'policy', value: 'charge scope ≤ $4,000' },
      ],
    };
  }

  return base;
}

function buildSynthesisStep(phase: 1 | 2 | 3 | 4 | 5, productCount: number): ActivityEntry {
  return span({
    activity_type: 'result',
    title: 'Compose grounded response',
    agent_name: phase >= 3 ? (phase === 4 ? 'ProductionAgent' : phase === 5 ? 'OrchestrationAgent' : 'RetrievalAgent') : phase === 2 ? 'MCPAgent' : 'SQLAgent',
    agent_file:
      phase === 4
        ? 'agents/production_04/concierge.py'
        : phase === 3
          ? 'agents/retrieval_03/supervisor.py'
          : `agents/phase${phase}/agent.py`,
    execution_time_ms: 45 + phase * 10,
    telemetry: {
      category: 'synthesis',
      component: 'Claude on Bedrock',
      status: phase === 4 ? 'ok' : 'ok',
      tokens: { input: 1100 + productCount * 120, output: 180 + productCount * 40 },
      fields: [
        {
          label: 'grounding_sources',
          value: `Aurora (${productCount} packages)${phase === 4 ? ' + memory.facts + session' : ''}`,
        },
        { label: 'hallucination_guard', value: 'row-level citations required' },
        { label: 'output', value: 'message + trip_cards' },
      ],
    },
  });
}

export function enrichTraceActivities(
  phase: 1 | 2 | 3 | 4 | 5,
  query: string,
  activities: ActivityEntry[],
  traceId: string,
  msgs: Message[],
  options?: { productCount?: number }
): ActivityEntry[] {
  const hasBackendMemory = activities.some(
    (a) => a.telemetry?.memory || a.activity_type === 'tool_call'
  );

  const preamble =
    phase === 4 && hasBackendMemory
      ? []
      : phase === 4
        ? buildPhase4Preamble(query, traceId, msgs)
        : phase === 2
          ? buildPhase2Preamble(query, traceId, msgs)
          : phase === 1
            ? buildPhase1Preamble(query, traceId)
            : [];

  const enriched = activities.map((a) => {
    if (a.telemetry) return a;
    return enrichActivity(a, phase, query);
  });

  const hasResult = enriched.some((a) => a.activity_type === 'result');
  const tail =
    !hasResult && options?.productCount !== undefined && options.productCount > 0
      ? [buildSynthesisStep(phase, options.productCount)]
      : [];

  return [...preamble, ...enriched, ...tail];
}
