import { describe, expect, it } from 'vitest';
import type { Product } from '../../../types';
import type { ShowcaseTraceSpan } from '../showcaseAdapters';
import {
  deriveAuroraEvidence,
  deriveMcpContracts,
  deriveWorkflowState,
  getPhaseProof,
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
