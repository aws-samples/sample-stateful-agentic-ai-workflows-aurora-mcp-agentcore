import { describe, expect, it } from 'vitest';
import type { Product } from '../../../types';
import type { ShowcaseTraceSpan } from '../showcaseAdapters';
import {
  deriveAuroraEvidence,
  deriveMcpContracts,
  deriveWorkflowState,
  getPhaseProof,
  hasDurableCheckpoint,
} from '../showcaseProof';

function span(overrides: Partial<ShowcaseTraceSpan>): ShowcaseTraceSpan {
  return {
    id: overrides.id ?? `span-${Math.random()}`,
    name: overrides.name ?? 'Trace span',
    category: overrides.category ?? 'orchestration',
    type: overrides.type ?? 'tool_call',
    status: overrides.status ?? 'ok',
    latencyMs: overrides.latencyMs ?? 25,
    fields: overrides.fields ?? [],
    ...overrides,
  };
}

const product: Product = {
  product_id: 'trip-1',
  name: 'Tuscany Wine Week',
  brand: 'Tuscany + Italy',
  price: 2400,
  description: 'Wine-focused slow travel.',
  image_url: '',
  category: 'Wine',
  rank_delta: -2,
};

describe('showcase proof helpers', () => {
  it('returns presenter-facing proof metadata for each phase', () => {
    expect(getPhaseProof(2).headline).toContain('MCP');
    expect(getPhaseProof(5).auroraCapability).toContain('checkpoints');
  });

  it('extracts observed MCP tool contracts from trace spans', () => {
    const contracts = deriveMcpContracts([
      span({
        name: 'MCP server discovered: meridian-concierge (custom)',
        details: 'tools/list returned compare_packages',
      }),
      span({
        name: 'meridian-concierge · compare_packages',
        details: "args={'package_ids': ['trip-1', 'trip-2']} · Compared 2 packages",
        agent: 'MCPAgent',
      }),
    ]);

    expect(contracts).toHaveLength(1);
    expect(contracts[0]).toMatchObject({
      server: 'meridian-concierge',
      tool: 'compare_packages',
      observed: true,
    });
    expect(contracts[0].request).toContain('package_ids');
    expect(contracts[0].auroraOperation).toContain('compare');
  });

  it('marks Aurora evidence as observed from the live trace shape', () => {
    const evidence = deriveAuroraEvidence({
      selectedPhase: 5,
      recommendations: [product],
      traceSpans: [
        span({ name: 'postgres-mcp · run_query', type: 'mcp', sql: 'SELECT * FROM trip_packages' }),
        span({ name: 'Hybrid candidates fetched', details: '25 unique candidates (semantic=25, lexical=3)' }),
        span({ name: 'Cohere rerank applied', details: 'Reranked to top 5 trips' }),
        span({ name: 'Aurora RLS scoped transaction', details: 'traveler_id set' }),
        span({ name: 'Workload traveler grant allowed', fields: [{ label: 'authorization.decision', value: 'allow' }] }),
        span({ name: 'Checkpoint · PostgresSaver.put', details: 'Workflow state serialized' }),
      ],
    });

    expect(Object.fromEntries(evidence.map((item) => [item.key, item.status]))).toMatchObject({
      sql: 'observed',
      mcp: 'observed',
      vector: 'observed',
      rerank: 'observed',
      rls: 'observed',
      checkpoint: 'observed',
    });
  });

  it('derives workflow path, intent, and checkpoint state', () => {
    const workflow = deriveWorkflowState([
      span({
        name: 'Workflow node: classify → plan',
        fields: [
          { label: 'node', value: 'classify' },
          { label: 'intent', value: 'plan' },
          { label: 'checkpointer', value: 'PostgresSaver' },
        ],
      }),
      span({ name: 'Workflow node: search', fields: [{ label: 'node', value: 'search' }] }),
      span({ name: 'Checkpoint · PostgresSaver.put', fields: [{ label: 'checkpointer', value: 'PostgresSaver' }] }),
    ]);

    expect(workflow.status).toBe('checkpointed');
    expect(workflow.intent).toBe('plan');
    expect(workflow.path).toEqual(['classify', 'search', 'availability', 'synthesize']);
    expect(workflow.visited).toEqual(['classify', 'search']);
    expect(workflow.nextNode).toBe('availability');
    expect(workflow.checkpointCount).toBe(1);
    expect(workflow.durable).toBe(true);
    expect(workflow.table).toBe('checkpoints');
  });

  it('does not present MemorySaver as durable Aurora proof', () => {
    const spans = [
      span({
        name: 'Checkpoint · MemorySaver.put',
        fields: [
          { label: 'checkpointer', value: 'MemorySaver (in-process)' },
          { label: 'checkpoint_store', value: 'process memory' },
        ],
      }),
    ];
    const workflow = deriveWorkflowState(spans);
    const checkpoint = deriveAuroraEvidence({
      selectedPhase: 5,
      recommendations: [],
      traceSpans: spans,
    }).find((item) => item.key === 'checkpoint');

    expect(workflow.status).toBe('ephemeral');
    expect(workflow.durable).toBe(false);
    expect(checkpoint?.status).toBe('ready');
    expect(checkpoint?.detail).toContain('Aurora durability not observed');
  });
});

it.each(['AuroraDataApiSaver', 'AsyncPostgresSaver (Aurora)', 'NextSaver'])('uses explicit durable telemetry for %s', (kind) => {
  const trace = [span({ name: `Checkpoint · ${kind}.put`, fields: [
    { label: 'checkpointer', value: kind }, { label: 'checkpoint_durable', value: 'true' },
  ] })];
  expect(deriveWorkflowState(trace).durable).toBe(true);
  expect(deriveAuroraEvidence({ selectedPhase: 5, traceSpans: trace, recommendations: [] }).find(item => item.key === 'checkpoint')?.value).toContain('saved to Aurora');
});

it('recognizes old Aurora receipts but respects an explicit non-durable flag', () => {
  const legacy = span({ name: 'Checkpoint · AuroraDataApiSaver.put', fields: [{ label: 'checkpointer', value: 'AuroraDataApiSaver' }] });
  expect(deriveWorkflowState([legacy]).durable).toBe(true);
  expect(deriveWorkflowState([{ ...legacy, fields: [...legacy.fields, { label: 'checkpoint_durable', value: 'false' }] }]).durable).toBe(false);
});


it('does not treat a failed checkpoint operation as a durable save', () => {
  expect(hasDurableCheckpoint([span({
    name: 'Checkpoint · AuroraDataApiSaver.put',
    status: 'error',
    fields: [{ label: 'checkpoint_durable', value: 'true' }],
  })])).toBe(false);
});

it('requires the actual runtime call and an allowed grant with RLS, beyond identity alone', () => {
  const evidence = deriveAuroraEvidence({ selectedPhase: 4, recommendations: [], traceSpans: [
    span({ name: 'AgentCore Identity resolved' }),
    span({ name: 'AgentCore Runtime failed', status: 'error' }),
    span({ name: 'Aurora RLS scoped transaction' }),
    span({ name: 'Workload traveler grant denied', fields: [{ label: 'authorization.decision', value: 'deny' }] }),
  ] });
  expect(evidence.find(item => item.key === 'runtime')?.status).toBe('ready');
  expect(evidence.find(item => item.key === 'rls')?.status).toBe('ready');
});
