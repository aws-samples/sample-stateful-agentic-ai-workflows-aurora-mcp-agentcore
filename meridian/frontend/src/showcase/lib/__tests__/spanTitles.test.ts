import { describe, expect, it } from 'vitest';
import type { ShowcaseTraceSpan } from '../showcaseAdapters';
import { deriveRecoverySteps } from '../recoveryState';
import { deriveWorkflowState, hasDurableCheckpoint } from '../showcaseProof';
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

// What the backend emits now.
const current: ShowcaseTraceSpan[] = [
  span('Workflow node: classify → plan', {
    component: 'Strands Graph', latencyMs: 0,
    fields: [{ label: 'node', value: 'classify' }, { label: 'intent', value: 'plan' }],
  }),
  span('Workflow node: search', { component: 'Strands Graph → SearchAgent', latencyMs: 956 }),
  span('Snapshot saved: AuroraSnapshotStorage.write', {
    component: 'Aurora workflow_snapshots', latencyMs: 458,
    sql: 'INSERT INTO workflow_snapshots (storage_key) VALUES ($1);',
    fields: [
      { label: 'checkpointer', value: 'Aurora workflow_snapshots' },
      { label: 'snapshot_durable', value: 'true' },
      { label: 'checkpoint_store', value: 'workflow_snapshots' },
    ],
  }),
  span('Workflow paused at a saved step', {
    component: 'Strands Graph', status: 'held',
    fields: [{ label: 'snapshot_durable', value: 'true' }],
  }),
];

// What a journey saved before the rename replays from its stored snapshot.
const legacy: ShowcaseTraceSpan[] = [
  span('Workflow node: classify → plan', {
    component: 'Strands Graph', latencyMs: 0,
    fields: [{ label: 'node', value: 'classify' }, { label: 'intent', value: 'plan' }],
  }),
  span('Workflow node: search', { component: 'Strands Graph → SearchAgent', latencyMs: 956 }),
  span('Checkpoint · AuroraSnapshotStorage.write', {
    component: 'Aurora workflow_snapshots', latencyMs: 458,
    sql: 'INSERT INTO workflow_snapshots (storage_key) VALUES ($1);',
    fields: [
      { label: 'checkpointer', value: 'Aurora workflow_snapshots' },
      { label: 'checkpoint_durable', value: 'true' },
      { label: 'checkpoint_store', value: 'workflow_snapshots' },
    ],
  }),
  span('Workflow paused at checkpoint', {
    component: 'Strands Graph', status: 'held',
    fields: [{ label: 'checkpoint_durable', value: 'true' }],
  }),
];

describe.each([['current', current], ['legacy', legacy]])('%s Phase 5 titles', (_name, spans) => {
  it('labels workflow nodes Strands Graph and the snapshot AWS Aurora Data API', () => {
    expect(stepService(spans[0])).toBe('Strands Graph');
    expect(stepService(spans[2])).toBe('AWS Aurora Data API');
    expect(stepSourceLabel(spans[2])).toBe(nb('AWS Aurora Data API, 458 ms'));
  });

  it('confirms the understand, search and snapshot steps from the spans', () => {
    const steps = deriveRecoverySteps(spans, 'checkpointed', flow);
    expect(steps.map(step => step.state))
      .toEqual(['is-visited', 'is-visited', 'is-visited', 'is-pending']);
    expect(steps[0].source).toBe(nb('Strands Graph, <1 ms'));
    expect(steps[2].source).toBe(nb('AWS Aurora Data API, 458 ms'));
  });

  it('reads a durable snapshot and counts the ok snapshot span', () => {
    expect(hasDurableCheckpoint(spans)).toBe(true);
    const state = deriveWorkflowState(spans);
    expect(state.status).toBe('checkpointed');
    expect(state.checkpointCount).toBe(1);
    expect(state.durable).toBe(true);
  });

  it('refuses a snapshot that reports itself non-durable', () => {
    const notDurable = spans.map(item => ({
      ...item,
      fields: item.fields.map(field => (/_durable$/.test(field.label)
        ? { ...field, value: 'false' } : field)),
    }));
    expect(hasDurableCheckpoint(notDurable)).toBe(false);
  });
});

describe('titles with a colon or the saved-journey middle dot', () => {
  it('takes the part after either separator', () => {
    expect(lastTitlePart('postgres-mcp: run_query')).toBe('run_query');
    expect(lastTitlePart('postgres-mcp \u00b7 run_query')).toBe('run_query');
    expect(lastTitlePart('run_query')).toBe('run_query');
  });

  it('finds the Runtime turn in both forms', () => {
    expect(RUNTIME_TURN_COMPLETE.test('AgentCore Runtime: turn complete')).toBe(true);
    expect(RUNTIME_TURN_COMPLETE.test('AgentCore Runtime \u00b7 turn complete')).toBe(true);
    expect(RUNTIME_TURN_COMPLETE.test('AgentCore Runtime: turn started')).toBe(false);
  });

  it('splits the MCP details line in both forms', () => {
    const now = MCP_ARGS_DETAILS.exec("args={'a': 1}, result: Compared 2 packages");
    expect([now?.[1], now?.[2]]).toEqual(["{'a': 1}", 'Compared 2 packages']);
    const saved = MCP_ARGS_DETAILS.exec("args={'a': 1} \u00b7 Compared 2 packages");
    expect([saved?.[1], saved?.[2]]).toEqual(["{'a': 1}", 'Compared 2 packages']);
  });
});
