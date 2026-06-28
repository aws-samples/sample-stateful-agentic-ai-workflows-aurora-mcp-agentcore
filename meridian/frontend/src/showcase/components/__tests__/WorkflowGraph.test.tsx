import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { MeridianShowcaseState } from '../../hooks/useMeridianShowcase';
import type { ShowcaseTraceSpan } from '../../lib/showcaseAdapters';
import { WorkflowGraph } from '../WorkflowGraph';

function span(overrides: Partial<ShowcaseTraceSpan>): ShowcaseTraceSpan {
  return {
    id: overrides.id ?? 'span',
    name: overrides.name ?? 'Workflow node: classify',
    category: overrides.category ?? 'orchestration',
    type: overrides.type ?? 'reasoning',
    status: overrides.status ?? 'ok',
    latencyMs: overrides.latencyMs ?? 25,
    fields: overrides.fields ?? [],
    ...overrides,
  };
}

function state(traceSpans: ShowcaseTraceSpan[], overrides: Partial<MeridianShowcaseState> = {}) {
  return {
    traceSpans,
    isReplaying: false,
    replayIndex: -1,
    ...overrides,
  } as unknown as MeridianShowcaseState;
}

describe('WorkflowGraph', () => {
  it('renders the routed LangGraph path with branch labels and checkpoint badges', () => {
    render(
      <WorkflowGraph
        state={state([
          span({
            id: 'classify',
            name: 'Workflow node: classify → plan',
            fields: [
              { label: 'node', value: 'classify' },
              { label: 'intent', value: 'plan' },
              { label: 'checkpointer', value: 'PostgresSaver' },
            ],
          }),
          span({
            id: 'search',
            name: 'Workflow node: search',
            fields: [
              { label: 'node', value: 'search' },
              { label: 'packages', value: '5' },
            ],
          }),
          span({
            id: 'checkpoint-search',
            name: 'Checkpoint · PostgresSaver.put',
            fields: [{ label: 'checkpointer', value: 'PostgresSaver' }],
          }),
          span({
            id: 'availability',
            name: 'Workflow node: availability',
            fields: [
              { label: 'node', value: 'availability' },
              { label: 'rows', value: '3' },
              { label: 'step', value: '2 of 2' },
            ],
          }),
          span({
            id: 'synthesize',
            name: 'Workflow node: synthesize',
            fields: [
              { label: 'node', value: 'synthesize' },
              { label: 'packages', value: '5' },
            ],
          }),
        ])}
      />,
    );

    expect(screen.getByText('START')).toBeInTheDocument();
    expect(screen.getByText('END')).toBeInTheDocument();
    expect(screen.getByText('intent: plan')).toBeInTheDocument();
    expect(screen.getByText('intent=search | plan')).toBeInTheDocument();
    expect(screen.getByText('plan step 2')).toBeInTheDocument();
    expect(screen.getAllByText('packages=5')).toHaveLength(2);
    expect(screen.getByText('rows=3 · 2 of 2')).toBeInTheDocument();
    expect(screen.getByText('checkpoint')).toBeInTheDocument();
    expect(screen.getByText('checkpointed · PostgresSaver')).toBeInTheDocument();
  });

  it('marks the current node during trace replay', () => {
    const { container } = render(
      <WorkflowGraph
        state={state(
          [
            span({ id: 'classify', fields: [{ label: 'node', value: 'classify' }, { label: 'intent', value: 'search' }] }),
            span({ id: 'search', name: 'Workflow node: search', fields: [{ label: 'node', value: 'search' }] }),
          ],
          { isReplaying: true, replayIndex: 1 },
        )}
      />,
    );

    expect(container.querySelector('.mds-wfgraph-node.is-current')).toBeTruthy();
  });
});
