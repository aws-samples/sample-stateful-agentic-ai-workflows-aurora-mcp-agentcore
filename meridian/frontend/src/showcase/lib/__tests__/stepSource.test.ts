import { describe, expect, it } from 'vitest';
import type { ShowcaseTraceSpan } from '../showcaseAdapters';
import { formatLatency, stepSourceLabel } from '../stepSource';

function span(overrides: Partial<ShowcaseTraceSpan>): ShowcaseTraceSpan {
  return {
    id: 'span', name: 'Span', category: 'orchestration', type: 'tool_call', status: 'ok',
    latencyMs: null, fields: [], ...overrides,
  };
}

const label = (overrides: Partial<ShowcaseTraceSpan>, replyModel?: string) =>
  stepSourceLabel(span(overrides), replyModel);

// The value and its unit never wrap apart on a narrow stage: a no-break space joins them.
const nb = (text: string) => text.replace(/(\d) (ms|s)\b/g, '$1\u00a0$2');

describe('formatLatency', () => {
  it('keeps each value on one line with its unit', () => {
    expect(formatLatency(601)).toBe('601\u00a0ms');
    expect(formatLatency(1449)).toBe('1.4\u00a0s');
    expect(formatLatency(0)).toBe('<1\u00a0ms');
  });

  it('writes whole milliseconds under a second and one decimal second above', () => {
    expect(formatLatency(38)).toBe(nb('38 ms'));
    expect(formatLatency(999)).toBe(nb('999 ms'));
    expect(formatLatency(1000)).toBe(nb('1.0 s'));
    expect(formatLatency(1449)).toBe(nb('1.4 s'));
    expect(formatLatency(1450)).toBe(nb('1.5 s'));
    expect(formatLatency(16827)).toBe(nb('16.8 s'));
  });

  it('never rounds a sub-second value up to "1000 ms"', () => {
    expect(formatLatency(999.6)).toBe(nb('1.0 s'));
  });

  it('writes a measured zero as under one millisecond, not as zero', () => {
    expect(formatLatency(0)).toBe(nb('<1 ms'));
  });

  it('shows no number when there is no measured value', () => {
    for (const value of [null, undefined, Number.NaN, -1, Number.POSITIVE_INFINITY]) {
      expect(formatLatency(value)).toBeNull();
    }
  });
});

describe('stepSourceLabel', () => {
  it('names the Aurora Data API and the measured time for SQL work', () => {
    expect(label({ name: 'Found 5 trips', agent: 'SQLAgent', type: 'result', latencyMs: 323 }))
      .toBe(nb('Aurora Data API · 323 ms'));
    expect(label({ name: 'Trip type filter: City Breaks', agent: 'SQLAgent', sql: 'SELECT 1' }))
      .toBe('Aurora Data API');
    expect(label({
      name: 'Strands @tool recall_session_context', component: 'Aurora · conversation_messages',
      category: 'memory_short', latencyMs: 59,
    })).toBe(nb('Aurora Data API · 59 ms'));
    expect(label({
      name: 'Checkpoint · AuroraDataApiSaver.put',
      component: 'Aurora · LangGraph checkpoint tables', category: 'memory_short', latencyMs: 106,
    })).toBe(nb('AWS Aurora Data API · 106 ms'));
    expect(label({
      name: 'Snapshot saved: AuroraSnapshotStorage.write',
      component: 'Aurora workflow_snapshots', category: 'memory_short', latencyMs: 106,
    })).toBe(nb('AWS Aurora Data API · 106 ms'));
  });

  it('names the model that wrote the reply on the step it wrote, and only there', () => {
    const polish = {
      name: 'Bedrock · concierge polish (global.anthropic.claude-sonnet-5)', category: 'model',
      agent: 'RetrievalAgent', latencyMs: 1400,
    };
    expect(label(polish, 'Claude Sonnet 5')).toBe(nb('Bedrock · Claude Sonnet 5 · 1.4 s'));
    expect(label({ name: 'Cohere rerank applied', agent: 'SearchAgent', latencyMs: 209 },
      'Claude Sonnet 5')).toBe(nb('Bedrock · 209 ms'));
    const runtime = {
      name: 'AgentCore Runtime · turn complete', category: 'runtime',
      component: 'Bedrock AgentCore Runtime · MeridianConcierge', latencyMs: 18363,
    };
    expect(label(runtime, 'Claude Sonnet 5'))
      .toBe(nb('AgentCore Runtime · Claude Sonnet 5 · 18.4 s'));
    expect(label(runtime)).toBe(nb('AgentCore Runtime · 18.4 s'));
  });

  it('reads the Cedar decision the gateway reported', () => {
    expect(label({
      name: 'semantic_trip_search · result', category: 'gateway',
      component: 'Bedrock AgentCore Gateway', latencyMs: 1143,
      fields: [{ label: 'cedar_decision', value: 'allow' }],
    })).toBe(nb('AgentCore Gateway · Cedar allow · 1.1 s'));
    expect(label({
      name: 'Hold refused by Cedar policy', category: 'security', status: 'denied',
      component: 'Bedrock AgentCore Policy', latencyMs: 210,
      fields: [{ label: 'cedar_decision', value: 'deny' }],
    })).toBe(nb('Cedar · deny · 210 ms'));
    expect(label({
      name: 'AgentCore Gateway · tools/call → semantic_trip_search', category: 'gateway',
      component: 'Bedrock AgentCore Gateway',
    })).toBe('AgentCore Gateway');
  });

  it('names MCP servers and managed services by their spans', () => {
    expect(label({
      name: 'meridian-concierge · price_range', agent: 'MCPAgent', type: 'mcp', latencyMs: 425,
    })).toBe(nb('MCP · meridian-concierge · 425 ms'));
    expect(label({
      name: 'postgres-mcp · run_query', agent: 'MCPAgent', sql: 'SELECT 1', latencyMs: 75,
    })).toBe(nb('MCP · postgres-mcp · 75 ms'));
    expect(label({ name: 'MCP turn complete · 1 server', agent: 'MCPAgent', latencyMs: 1293 }))
      .toBe(nb('MCP · 1.3 s'));
    expect(label({
      name: 'Hydrated compared packages into product cards', agent: 'MCPAgent',
      type: 'database', sql: 'SELECT … FROM trip_packages',
    })).toBe('Aurora Data API');
    expect(label({
      name: 'AgentCore Memory · session restored', component: 'Bedrock AgentCore Memory',
    })).toBe('AgentCore Memory');
    expect(label({ name: 'Workload identity · AWS STS', component: 'AWS STS' })).toBe('AWS STS');
    expect(label({
      name: 'Workflow node: classify → plan', component: 'LangGraph StateGraph', latencyMs: 0,
    })).toBe(nb('Strands Graph · <1 ms'));
  });

  it('credits the app itself, not a service, for routing and in-memory steps', () => {
    for (const name of [
      'Processing with Hybrid (pgvector + tsvector) + Cohere Rerank via Strands Supervisor',
      'Search Agent completed',
      'PackageAgent: Duration inventory verified',
    ]) {
      expect(label({ name, agent: 'RetrievalAgent' })).toBe('Meridian app');
    }
  });

  it('credits the Strands supervisor, which runs on a Bedrock model, for its own turn', () => {
    expect(label({
      name: 'Supervisor completed coordination', agent: 'RetrievalAgent', latencyMs: 10269,
    })).toBe(nb('Strands · Bedrock · 10.3 s'));
  });
});
