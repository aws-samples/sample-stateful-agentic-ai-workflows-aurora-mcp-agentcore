import { describe, expect, it } from 'vitest';
import type { ShowcaseTraceSpan } from '../showcaseAdapters';
import { deriveRecoverySteps } from '../recoveryState';

function span(name: string, overrides: Partial<ShowcaseTraceSpan> = {}): ShowcaseTraceSpan {
  return {
    id: name, name, category: 'orchestration', type: 'tool_call', status: 'ok', latencyMs: null,
    fields: [], ...overrides,
  };
}

const durable = [{ label: 'checkpoint_durable', value: 'true' }];
const CHECKPOINT_TABLES = 'Aurora · LangGraph checkpoint tables';
const checkpointed = { resumeMode: false, failed: false };

// The spans a paused recovery returns, in order, as the live backend records them.
const paused: ShowcaseTraceSpan[] = [
  span('Workflow node: classify → plan', { component: 'LangGraph StateGraph', latencyMs: 0 }),
  span('Workflow node: search', { component: 'LangGraph → SearchAgent', latencyMs: 956 }),
  span('Delegating to SearchAgent', { agent: 'RetrievalAgent' }),
  span('Embedding generated', { agent: 'SearchAgent', latencyMs: 221 }),
  span('Hybrid candidates fetched', { agent: 'SearchAgent', latencyMs: 338, sql: 'SELECT 1' }),
  span('Cohere rerank applied', { agent: 'SearchAgent', latencyMs: 396 }),
  span('Checkpoint · AuroraDataApiSaver.put', {
    component: CHECKPOINT_TABLES, latencyMs: 106, fields: durable,
  }),
  span('Workflow paused at checkpoint', {
    component: 'LangGraph durable execution', status: 'held',
  }),
];

const resumed: ShowcaseTraceSpan[] = [
  ...paused.slice(0, 7),
  span('Workflow node: availability fan-out', {
    component: 'LangGraph → PackageAgent fan-out', latencyMs: 54,
  }),
  span('PackageAgent: Finding package', { agent: 'PackageAgent', latencyMs: 40 }),
  span('Checkpoint · AuroraDataApiSaver.put#2', {
    component: CHECKPOINT_TABLES, latencyMs: 88, fields: durable,
  }),
];

describe('deriveRecoverySteps', () => {
  it('names each step\'s service and measured time once the backend confirms it', () => {
    const steps = deriveRecoverySteps(paused, 'checkpointed', checkpointed);
    expect(steps.map(step => step.state))
      .toEqual(['is-visited', 'is-visited', 'is-visited', 'is-pending']);
    expect(steps.map(step => step.source)).toEqual([
      'LangGraph · <1 ms', 'Bedrock + Aurora · 956 ms', 'Aurora Data API · 106 ms', null,
    ]);
  });

  it('completes verification only when the resumed run reports it', () => {
    const steps = deriveRecoverySteps(resumed, 'ready', checkpointed);
    expect(steps.map(step => step.state))
      .toEqual(['is-visited', 'is-visited', 'is-visited', 'is-visited']);
    expect(steps[3].source).toBe('Aurora · 54 ms');
  });

  it('does not claim an Aurora checkpoint for an in-process one', () => {
    const inProcess = paused.map(item => (item.name.startsWith('Checkpoint')
      ? {
        ...item, component: 'LangGraph MemorySaver (in-process)',
        fields: [{ label: 'checkpoint_durable', value: 'false' }],
      }
      : item));
    const steps = deriveRecoverySteps(inProcess, 'checkpointed', checkpointed);
    expect(steps[2]).toEqual({ state: 'is-pending', source: null });
  });

  it('claims no progress while a fresh run is in flight', () => {
    const steps = deriveRecoverySteps([], 'running', checkpointed);
    expect(steps.every(step => step.state === 'is-pending' && step.source === null)).toBe(true);
  });

  it('keeps the confirmed steps while resuming and marks verification in progress', () => {
    const steps = deriveRecoverySteps(paused, 'running', { resumeMode: true, failed: false });
    expect(steps.map(step => step.state))
      .toEqual(['is-visited', 'is-visited', 'is-visited', 'is-current']);
    expect(steps[2].source).toBe('Aurora Data API · 106 ms');
    expect(steps[3].source).toBeNull();
  });

  it('names no service it did not read from a span while resuming', () => {
    const steps = deriveRecoverySteps([], 'running', { resumeMode: true, failed: false });
    expect(steps.map(step => step.state))
      .toEqual(['is-visited', 'is-visited', 'is-pending', 'is-current']);
    expect(steps.every(step => step.source === null)).toBe(true);
  });

  it('does not claim an Aurora checkpoint for an in-process one while resuming', () => {
    const inProcess = paused.map(item => (item.name.startsWith('Checkpoint')
      ? { ...item, fields: [{ label: 'checkpoint_durable', value: 'false' }] }
      : item));
    const steps = deriveRecoverySteps(inProcess, 'running', { resumeMode: true, failed: false });
    expect(steps[2]).toEqual({ state: 'is-pending', source: null });
    expect(steps[1].source).toBe('Bedrock + Aurora · 956 ms');
  });

  it('marks nothing complete after a failure or before the traveler starts', () => {
    expect(deriveRecoverySteps(paused, 'action', { resumeMode: false, failed: true })
      .every(step => step.state === 'is-pending')).toBe(true);
    expect(deriveRecoverySteps([], 'action', checkpointed).map(step => step.state))
      .toEqual(['is-ready', 'is-pending', 'is-pending', 'is-pending']);
  });
});
