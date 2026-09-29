import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { JourneyContinuityRail } from '../JourneyContinuityRail';
import type { JourneyDocument, JourneyExecution } from '../../journey/types';

const unavailable = (reason: string) => ({ status: 'unavailable' as const, reason });

function execution(overrides: Partial<JourneyExecution>): JourneyExecution {
  return {
    execution_id: 'exe_01', attempt: 1, worker_id: 'worker_01', status: 'succeeded',
    started_at: null, ended_at: null, lease_expires_at: null, ...overrides,
  };
}

function makeDocument(overrides: Partial<JourneyDocument> = {}): JourneyDocument {
  return {
    journey_id: 'jrn_test',
    traveler_id: 'trv_meridian_demo',
    status: 'active',
    checkpoint_backend: { kind: 'AuroraDataApiSaver', durable: true },
    active_thread_id: 'thread_test',
    executions: { status: 'observed', source: 'journey_executions', items: [execution({})] },
    checkpoint: unavailable('none'),
    selected_plan: unavailable('none'),
    recommendations: unavailable('none'),
    pending_decision: unavailable('none'),
    conversation: unavailable('none'),
    hold: unavailable('none'),
    authorization: unavailable('none'),
    rls: unavailable('none'),
    ...overrides,
  } as JourneyDocument;
}

const authorized = {
  status: 'observed', source: 'traveler_access_audit', audit_id: 'aud_1',
  identity_provider: 'aws_iam', subject: 'role', decision: 'allow', reason: null,
  observed_at: '2026-09-28T18:00:00Z',
};
const recommendations = {
  status: 'observed', source: 'checkpoint', items: [{ package_id: 'TKY-003' }],
};
const checkpoint = {
  status: 'committed', source: 'checkpoints', thread_id: 'thread_test', checkpoint_id: 'cp_01',
  parent_checkpoint_id: null, checkpoint_ns: '', committed_at: '2026-09-28T18:00:00Z',
};

const rows = () => screen.queryAllByRole('listitem')
  .map(row => row.querySelector('strong')?.textContent);

describe('JourneyContinuityRail', () => {
  it('shows one line, not five empty rows, before a journey exists', () => {
    render(<JourneyContinuityRail document={null} error={null} />);
    expect(screen.getByText('Start a recovery to see each step persist.')).toBeInTheDocument();
    expect(screen.queryAllByRole('listitem')).toHaveLength(0);
    expect(screen.queryByText('No journey yet')).not.toBeInTheDocument();
  });

  it('says it is waiting, not to start one, while a run is in flight', () => {
    render(<JourneyContinuityRail document={null} error={null} loading />);
    expect(screen.getByText('Waiting for Aurora to record the first step.')).toBeInTheDocument();
    expect(screen.queryByText('Start a recovery to see each step persist.'))
      .not.toBeInTheDocument();
  });

  it('reveals only the steps Aurora has recorded', () => {
    render(<JourneyContinuityRail error={null} document={makeDocument({
      authorization: authorized, recommendations, checkpoint,
    } as Partial<JourneyDocument>)} />);
    expect(rows())
      .toEqual(['Traveler authorized', 'Alternatives retrieved', 'Checkpoint persisted']);
    const list = screen.getByRole('list');
    expect(within(list).queryByText(/No journey yet|Nothing checkpointed|Awaiting/)).toBeNull();
  });

  it('adds the interruption and the verified resume when they are recorded', () => {
    render(<JourneyContinuityRail error={null} document={makeDocument({
      authorization: authorized, recommendations, checkpoint,
      executions: {
        status: 'observed', source: 'journey_executions',
        items: [
          execution({ status: 'abandoned', worker_id: 'worker_01' }),
          execution({ execution_id: 'exe_02', attempt: 2, worker_id: 'worker_02' }),
        ],
      },
      workflow: {
        status: 'observed', source: 'checkpoint', conversation_id: 'thread_test', query: 'q',
        message: 'm', workflow_status: 'resumed', next_nodes: [], activities: [],
        travelers_count: 1, execution_id: 'exe_02', resumed_from_checkpoint: 'cp_01',
        resumed_after_restart: true,
      },
    } as Partial<JourneyDocument>)} />);
    expect(rows()).toEqual([
      'Traveler authorized', 'Alternatives retrieved', 'Checkpoint persisted',
      'Worker interrupted', 'Saved plan resumed',
    ]);
  });

  it('paints recorded rows still when it opens onto a saved journey', () => {
    render(<JourneyContinuityRail error={null} document={makeDocument({
      authorization: authorized, recommendations, checkpoint,
    } as Partial<JourneyDocument>)} />);
    for (const row of screen.getAllByRole('listitem')) expect(row.style.opacity).not.toBe('0');
  });

  it('fades each row in as a watched run records it', () => {
    const { rerender } = render(<JourneyContinuityRail
      document={null} error={null} loading thread="thread_test" running />);
    rerender(<JourneyContinuityRail error={null} thread="thread_test" running={false}
      document={makeDocument({ authorization: authorized } as Partial<JourneyDocument>)} />);
    const [row] = screen.getAllByRole('listitem');
    expect(row.style.opacity).toBe('0');
  });
});

