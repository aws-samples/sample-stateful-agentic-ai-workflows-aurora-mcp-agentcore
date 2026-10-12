import { describe, expect, it } from 'vitest';
import type { ShowcaseTraceSpan } from '../showcaseAdapters';
import {
  deriveMcpContracts,
  deriveWorkflowState,
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

describe('showcase proof helpers', () => {
  it('requires the recorded intent and hold steps before calling recovery complete', () => {
    const spans = [
      span({ name: 'Workflow node: classify', fields: [
        { label: 'intent', value: 'plan' }, { label: 'recovery', value: 'true' },
      ] }),
      span({ name: 'Workflow node: search' }),
      span({ name: 'Workflow node: availability' }),
    ];
    expect(deriveWorkflowState(spans).nextNode).toBe('prepare_hold');
    spans.push(span({ name: 'Workflow node: prepare_hold' }));
    spans.push(span({ name: 'Workflow node: hold', status: 'error' }));
    expect(deriveWorkflowState(spans).nextNode).toBe('hold');
    spans.push(span({ name: 'Workflow node: hold' }));
    spans.push(span({ name: 'Workflow node: synthesize' }));
    expect(deriveWorkflowState(spans).nextNode).toBe('complete');
    expect(deriveWorkflowState(spans).visited).toHaveLength(6);
  });

  it('extracts observed MCP tool contracts from trace spans', () => {
    const contracts = deriveMcpContracts([
      span({
        name: 'MCP server discovered: meridian-concierge (custom)',
        details: 'tools/list returned compare_packages',
      }),
      span({
        name: 'meridian-concierge: compare_packages',
        details: "args={'package_ids': ['trip-1', 'trip-2']}, result: Compared 2 packages",
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

  it('still reads the middle-dot MCP spans that saved journeys carry', () => {
    const contracts = deriveMcpContracts([
      span({ name: 'MCP server discovered: meridian-concierge (custom)' }),
      span({
        name: 'meridian-concierge · compare_packages',
        details: "args={'package_ids': ['trip-1', 'trip-2']} · Compared 2 packages",
        agent: 'MCPAgent',
      }),
      span({ name: 'postgres-mcp · session connected' }),
      span({ name: 'postgres-mcp · run_query', sql: 'SELECT 1' }),
    ]);
    expect(contracts.map(contract => contract.tool)).toEqual(['compare_packages', 'run_query']);
    expect(contracts[0].request).toContain('package_ids');
    expect(contracts[0].result).toBe('Compared 2 packages');
  });

  it('does not count session setup as an executed SQL tool', () => {
    const contracts = deriveMcpContracts([
      span({ name: 'MCP server discovered: awslabs.postgres-mcp-server' }),
      span({ name: 'postgres-mcp: session connected' }),
      span({ name: 'postgres-mcp: run_query', sql: 'SELECT 1' }),
    ]);
    expect(contracts).toHaveLength(1);
    expect(contracts[0].tool).toBe('run_query');
    expect(contracts[0].observed).toBe(true);
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
    expect(workflow.table).toBe('workflow_snapshots');
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

    expect(workflow.status).toBe('ephemeral');
    expect(workflow.durable).toBe(false);
  });
});

it.each(['AuroraDataApiSaver', 'AsyncPostgresSaver (Aurora)', 'NextSaver'])('uses explicit durable telemetry for %s', (kind) => {
  const trace = [span({ name: `Checkpoint · ${kind}.put`, fields: [
    { label: 'checkpointer', value: kind }, { label: 'checkpoint_durable', value: 'true' },
  ] })];
  expect(deriveWorkflowState(trace).durable).toBe(true);
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

it('reads a new-title snapshot as a durable save and counts it', () => {
  const trace = [span({
    name: 'Snapshot saved: AuroraSnapshotStorage.write',
    fields: [{ label: 'checkpointer', value: 'Aurora workflow_snapshots' }, { label: 'snapshot_durable', value: 'true' }],
  })];
  expect(hasDurableCheckpoint(trace)).toBe(true);
  const workflow = deriveWorkflowState(trace);
  expect(workflow.status).toBe('checkpointed');
  expect(workflow.checkpointCount).toBe(1);
});

it('treats a new-title snapshot with no snapshot_durable field as non-durable, on purpose', () => {
  // Deliberate: only an explicit snapshot_durable (or the old checkpoint_durable) of "true"
  // proves a save reached Aurora. The title and the store name alone prove nothing.
  expect(hasDurableCheckpoint([span({
    name: 'Snapshot saved: AuroraSnapshotStorage.write',
    fields: [{ label: 'checkpointer', value: 'Aurora workflow_snapshots' }],
  })])).toBe(false);
});

it('does not count a read that only touches workflow_snapshots as a saved step', () => {
  const read = span({
    name: 'Aurora recall: workflow history', sql: 'SELECT snapshot FROM workflow_snapshots',
    component: 'Aurora workflow_snapshots',
  });
  expect(deriveWorkflowState([read]).checkpointCount).toBe(0);
});
