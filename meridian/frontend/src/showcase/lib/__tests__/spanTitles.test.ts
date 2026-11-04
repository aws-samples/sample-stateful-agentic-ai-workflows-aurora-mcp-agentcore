import { describe, expect, it } from 'vitest';
import type { ShowcaseTraceSpan } from '../showcaseAdapters';
import { deriveRecoverySteps } from '../recoveryState';
import { deriveWorkflowState, hasDurableSnapshot } from '../showcaseProof';
import { lastTitlePart, MCP_ARGS_DETAILS, RUNTIME_TURN_COMPLETE } from '../spanTitles';
import { stepService, stepSourceLabel } from '../stepSource';

function span(name: string, overrides: Partial<ShowcaseTraceSpan> = {}): ShowcaseTraceSpan {
  return {
    id: name, name, category: 'orchestration', type: 'tool_call', status: 'ok', latencyMs: null,
    fields: [], ...overrides,
  };
}

const nb = (text: string) => text.replace(/(\d) (ms|s)\b/g, '$1 $2');
const flow = { resumeMode: false, failed: false };

// The Phase 5 spans the backend writes.
const spans: ShowcaseTraceSpan[] = [
  span('Workflow node: classify → plan', {
    component: 'Strands Graph', latencyMs: 0,
    fields: [{ label: 'node', value: 'classify' }, { label: 'intent', value: 'plan' }],
  }),
  span('Workflow node: search', {
    component: 'Strands Graph → AgentCore Gateway: hybrid_trip_search', latencyMs: 956,
  }),
  span('Snapshot saved: AuroraSnapshotStorage.write', {
    component: 'Aurora workflow_snapshots', latencyMs: 458,
    sql: 'INSERT INTO workflow_snapshots (storage_key) VALUES ($1);',
    fields: [
      { label: 'snapshot_store', value: 'Aurora workflow_snapshots' },
      { label: 'snapshot_durable', value: 'true' },
      { label: 'snapshot_table', value: 'workflow_snapshots' },
    ],
  }),
  span('Workflow paused at a saved step', {
    component: 'Strands Graph', status: 'held',
    fields: [{ label: 'snapshot_durable', value: 'true' }],
  }),
];

describe('Phase 5 titles', () => {
  it('labels workflow nodes Strands Graph and the snapshot Aurora Data API', () => {
    expect(stepService(spans[0])).toBe('Strands Graph');
    expect(stepService(spans[2])).toBe('Aurora Data API');
    expect(stepSourceLabel(spans[2])).toBe(nb('Aurora Data API, 458 ms'));
  });

  it('confirms the search and snapshot steps from the spans', () => {
    const steps = deriveRecoverySteps(spans, 'saved', flow);
    expect(steps.map(step => step.state))
      .toEqual(['is-visited', 'is-visited', 'is-pending', 'is-pending', 'is-pending']);
    expect(steps[1].source).toBe(nb('Aurora Data API, 458 ms'));
  });

  it('reads a durable snapshot and counts the ok snapshot span', () => {
    expect(hasDurableSnapshot(spans)).toBe(true);
    const state = deriveWorkflowState(spans);
    expect(state.status).toBe('saved');
    expect(state.snapshotCount).toBe(1);
    expect(state.durable).toBe(true);
  });

  it('reads the store and the table', () => {
    const workflow = deriveWorkflowState(spans);
    expect(workflow.snapshot).toBe('Aurora workflow_snapshots');
    expect(workflow.table).toBe('workflow_snapshots');
  });

  it('refuses a snapshot that reports itself non-durable', () => {
    const notDurable = spans.map(item => ({
      ...item,
      fields: item.fields.map(field => (field.label === 'snapshot_durable'
        ? { ...field, value: 'false' } : field)),
    }));
    expect(hasDurableSnapshot(notDurable)).toBe(false);
  });

  it('reports no durable snapshot when no span says so', () => {
    expect(hasDurableSnapshot(spans.slice(0, 2))).toBe(false);
  });
});

describe('title and details parts', () => {
  it('takes the part after the last colon', () => {
    expect(lastTitlePart('postgres-mcp: run_query')).toBe('run_query');
    expect(lastTitlePart('run_query')).toBe('run_query');
  });

  it('finds the Runtime turn', () => {
    expect(RUNTIME_TURN_COMPLETE.test('AgentCore Runtime: turn complete')).toBe(true);
    expect(RUNTIME_TURN_COMPLETE.test('AgentCore Runtime: turn started')).toBe(false);
  });

  it('splits the MCP details line', () => {
    const parts = MCP_ARGS_DETAILS.exec("args={'a': 1}, result: Compared 2 packages");
    expect([parts?.[1], parts?.[2]]).toEqual(["{'a': 1}", 'Compared 2 packages']);
  });
});
