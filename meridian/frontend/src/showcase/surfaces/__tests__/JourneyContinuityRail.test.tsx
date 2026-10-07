import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { JourneyContinuityRail } from '../JourneyContinuityRail';
import type { JourneyDocument, JourneyExecution, SessionStop } from '../../journey/types';

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

const pausedDocument = () => makeDocument({ executions: {
  status: 'observed', source: 'journey_executions', items: [execution({ status: 'paused' })],
} });

const rows = () => screen.queryAllByRole('listitem')
  .map(row => row.querySelector('strong')?.textContent);

describe('JourneyContinuityRail', () => {
  it('shows one line, not five empty rows, before a journey exists', () => {
    render(<JourneyContinuityRail document={null} error={null} />);
    expect(screen.getByText('Start a recovery to see each step persist.')).toBeInTheDocument();
    expect(screen.queryAllByRole('listitem')).toHaveLength(0);
    expect(screen.queryByText('No journey yet')).not.toBeInTheDocument();
    expect(screen.getByText('thread: —')).toBeInTheDocument();
    expect(screen.queryByText(/no recovery selected/i)).not.toBeInTheDocument();
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
      .toEqual(['Traveler authorized', 'Alternatives retrieved', 'Saved step']);
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
      'Traveler authorized', 'Alternatives retrieved', 'Saved step',
      'Worker interrupted', 'Saved plan resumed',
    ]);
  });

  it('says a worker was interrupted only when one was abandoned', () => {
    const leaseEnds = new Date(Date.now() + 60_000).toISOString();
    render(<JourneyContinuityRail error={null} document={makeDocument({
      authorization: authorized, recommendations, checkpoint,
      executions: {
        status: 'observed', source: 'journey_executions',
        items: [execution({ status: 'running', lease_expires_at: leaseEnds })],
      },
    } as Partial<JourneyDocument>)} />);
    expect(rows())
      .toEqual(['Traveler authorized', 'Alternatives retrieved', 'Saved step']);
    expect(screen.queryByText(/still running/)).toBeNull();
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

  it('offers the stop only while the latest execution runs or waits', () => {
    for (const [status, offered] of [
      ['paused', true], ['running', true], ['succeeded', false], ['abandoned', false],
    ] as const) {
      const { unmount } = render(
        <JourneyContinuityRail document={makeDocument({ executions: {
          status: 'observed', source: 'journey_executions', items: [execution({ status })],
        } })} error={null} onStopSession={vi.fn()} />,
      );
      expect(!!screen.queryByRole('button', { name: 'Stop runtime session' })).toBe(offered);
      unmount();
    }
  });

  it('asks before stopping and calls the handler once on confirm', async () => {
    const onStop = vi.fn().mockResolvedValue(undefined);
    render(<JourneyContinuityRail document={pausedDocument()} error={null} onStopSession={onStop} />);
    fireEvent.click(screen.getByRole('button', { name: 'Stop runtime session' }));
    expect(screen.getByRole('group', { name: 'Confirm stopping the runtime session' }))
      .toBeInTheDocument();
    expect(screen.getByText(/saved steps stay in Aurora/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Keep running' }));
    expect(onStop).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Stop runtime session' }));
    fireEvent.click(screen.getByRole('button', { name: 'Stop session' }));
    await waitFor(() => expect(onStop).toHaveBeenCalledTimes(1));
  });

  it('shows a failed stop without claiming it happened', async () => {
    const onStop = vi.fn().mockRejectedValue(
      new Error('Stopping the workflow Runtime session failed: AccessDeniedException'));
    render(<JourneyContinuityRail document={pausedDocument()} error={null} onStopSession={onStop} />);
    fireEvent.click(screen.getByRole('button', { name: 'Stop runtime session' }));
    fireEvent.click(screen.getByRole('button', { name: 'Stop session' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('AccessDeniedException');
    expect(rows()).not.toContain('Session stopped mid-run');
    expect(rows()).not.toContain('Session stopped while waiting');
  });

  it('records the stop only from Aurora, naming the case', () => {
    const document = pausedDocument();
    const stopped = (item: Partial<SessionStop>) => ({
      ...document,
      session_stops: { status: 'observed', source: 'workflow_session_stops',
        items: [{ runtime_session_id: 'rt-wf-x', outcome: 'stopped', stopped_at: '2026-10-07 10:00:00+00',
          stopped_during: 'waiting', last_step: null, ...item }] },
    } as JourneyDocument);
    const { rerender } = render(<JourneyContinuityRail document={document} error={null} />);
    expect(rows().join()).not.toMatch(/Session stopped/);

    rerender(<JourneyContinuityRail document={stopped({})} error={null} />);
    expect(rows()).toContain('Session stopped while waiting');
    expect(screen.getByText('for the review answer')).toBeInTheDocument();

    rerender(<JourneyContinuityRail document={stopped({ stopped_during: 'running', last_step: 'search' })}
      error={null} />);
    expect(rows()).toContain('Session stopped mid-run');
    expect(screen.getByText('after search, lease released')).toBeInTheDocument();

    rerender(<JourneyContinuityRail document={stopped({ stopped_during: 'running', last_step: null })}
      error={null} />);
    expect(screen.getByText('lease released')).toBeInTheDocument();

    rerender(<JourneyContinuityRail document={stopped({ stopped_during: 'finished', last_step: 'synthesize' })}
      error={null} />);
    expect(rows()).toContain('Session stopped after the run finished');
    expect(screen.getByText('the hold was already recorded')).toBeInTheDocument();

    rerender(<JourneyContinuityRail document={stopped({ outcome: 'not_running', stopped_during: 'waiting' })}
      error={null} />);
    expect(rows()).toContain('Session stopped while waiting');
    expect(screen.getByText('the session had already ended')).toBeInTheDocument();
  });

  it('moves focus to the progress region after a stop, not to the page body', async () => {
    const onStop = vi.fn().mockResolvedValue(undefined);
    render(<JourneyContinuityRail document={pausedDocument()} error={null} onStopSession={onStop} />);
    fireEvent.click(screen.getByRole('button', { name: 'Stop runtime session' }));
    fireEvent.click(screen.getByRole('button', { name: 'Stop session' }));
    await waitFor(() => expect(onStop).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(screen.getByRole('region', { name: 'Journey progress' })).toHaveFocus());
  });

  it('keeps focus on the stop control after a failed stop', async () => {
    const onStop = vi.fn().mockRejectedValue(new Error('Stopping failed'));
    render(<JourneyContinuityRail document={pausedDocument()} error={null} onStopSession={onStop} />);
    fireEvent.click(screen.getByRole('button', { name: 'Stop runtime session' }));
    fireEvent.click(screen.getByRole('button', { name: 'Stop session' }));
    await screen.findByRole('alert');
    await waitFor(() =>
      expect(screen.getByRole('button', { name: 'Stop runtime session' })).toHaveFocus());
  });

  it('hides the stop control once a stop is recorded for the paused run', () => {
    const stopped = {
      ...pausedDocument(),
      session_stops: { status: 'observed', source: 'workflow_session_stops',
        items: [{ runtime_session_id: 'rt-wf-x', outcome: 'stopped',
          stopped_at: '2026-10-07 10:05:00+00', stopped_during: 'waiting', last_step: null }] },
    } as JourneyDocument;
    stopped.executions = { status: 'observed', source: 'journey_executions',
      items: [execution({ status: 'paused', started_at: '2026-10-07 10:00:00+00' })] };
    render(<JourneyContinuityRail document={stopped} error={null} onStopSession={vi.fn()} />);
    expect(screen.queryByRole('button', { name: 'Stop runtime session' })).toBeNull();
  });
});
