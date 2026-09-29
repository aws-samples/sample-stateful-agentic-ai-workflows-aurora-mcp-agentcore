import { act, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { fetchJourneyDocument, fetchJourneys } from '../../api/client';
import { JourneyContinuityRail } from '../surfaces/JourneyContinuityRail';
import { JOURNEY_POLL_MS, useJourney } from './useJourney';
import type { JourneyDocument, JourneySummary } from './types';

vi.mock('../../api/client', () => ({ fetchJourneyDocument: vi.fn(), fetchJourneys: vi.fn() }));

const THREAD = 'phase5-live';
const unavailable = { status: 'unavailable', reason: 'not yet' };
const leaseEnds = '2099-01-01T00:00:00Z';

/** What Aurora holds after each commit of one run, in the order the backend commits. */
const recorded: Partial<JourneyDocument>[] = [
  {
    authorization: {
      status: 'observed', source: 'traveler_access_audit', audit_id: 'a1',
      identity_provider: 'aws_iam', subject: 'role', decision: 'allow', reason: null,
      observed_at: '2026-09-29T07:00:00Z',
    },
    executions: {
      status: 'observed', source: 'journey_executions',
      items: [{
        execution_id: 'exe_1', attempt: 1, worker_id: 'worker_1', status: 'running',
        started_at: null, ended_at: null, lease_expires_at: leaseEnds,
      }],
    },
  },
  {
    checkpoint: {
      status: 'committed', source: 'checkpoints', thread_id: THREAD, checkpoint_id: 'cp_1',
      parent_checkpoint_id: null, checkpoint_ns: '', committed_at: '2026-09-29T07:00:01Z',
    },
  },
  { recommendations: { status: 'observed', source: 'checkpoint', items: [{}, {}, {}] } },
] as Partial<JourneyDocument>[];

let commits = 0;

function documentAfter(count: number): JourneyDocument {
  return Object.assign({
    journey_id: 'jrn_live', traveler_id: 'trv_meridian_demo', status: 'running',
    active_thread_id: THREAD, checkpoint_backend: { kind: 'AuroraDataApiSaver', durable: true },
    executions: unavailable, checkpoint: unavailable, recommendations: unavailable,
    authorization: unavailable, hold: unavailable,
  }, ...recorded.slice(0, count)) as JourneyDocument;
}

/** The recovery desk's rail, fed the way the app feeds it. */
function Desk({ running }: { running: boolean }) {
  const [journeyId, setJourneyId] = useState<string | null>(null);
  const journey = useJourney(journeyId, setJourneyId, true, THREAD, running);
  return (
    <JourneyContinuityRail
      document={journey.document} error={journey.error}
      loading={journey.loading || running} thread={THREAD} running={running}
    />
  );
}

const rows = () => screen.queryAllByRole('listitem')
  .map(row => row.querySelector('strong')?.textContent);

async function tick(ms: number) {
  await act(async () => { await vi.advanceTimersByTimeAsync(ms); });
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.useFakeTimers();
  commits = 0;
  vi.mocked(fetchJourneys).mockImplementation(async () => (commits
    ? [{ journey_id: 'jrn_live', active_thread_id: THREAD } as JourneySummary] : []));
  vi.mocked(fetchJourneyDocument).mockImplementation(async () => documentAfter(commits));
});
afterEach(() => vi.useRealTimers());

it('fills the rail in during the run, in the order Aurora records each step', async () => {
  render(<Desk running />);
  await tick(0);
  // The journey is not recorded yet. That is the wait, not a failed read.
  expect(rows()).toEqual([]);
  expect(screen.getByText('Waiting for Aurora to record the first step.')).toBeInTheDocument();
  expect(screen.queryByText(/Read failed/)).toBeNull();

  const seen: (string | undefined)[][] = [];
  for (commits = 1; commits <= recorded.length; commits += 1) {
    await tick(JOURNEY_POLL_MS);
    seen.push(rows());
  }
  expect(seen).toEqual([
    ['Traveler authorized'],
    ['Traveler authorized', 'Checkpoint persisted'],
    ['Traveler authorized', 'Alternatives retrieved', 'Checkpoint persisted'],
  ]);
});

it('keeps one read in flight at a time', async () => {
  let release = () => {};
  vi.mocked(fetchJourneys).mockImplementation(() => new Promise((resolve) => {
    release = () => resolve([]);
  }));
  render(<Desk running />);
  await tick(5 * JOURNEY_POLL_MS);
  expect(fetchJourneys).toHaveBeenCalledTimes(1);
  await act(async () => { release(); });
  await tick(JOURNEY_POLL_MS);
  expect(fetchJourneys).toHaveBeenCalledTimes(2);
});

it('reads the final committed state once after the response, then stops polling', async () => {
  commits = 1;
  const { rerender } = render(<Desk running />);
  await tick(0);
  await tick(JOURNEY_POLL_MS);
  const reads = vi.mocked(fetchJourneyDocument).mock.calls.length;
  expect(reads).toBeGreaterThanOrEqual(2);
  commits = 3;
  rerender(<Desk running={false} />);
  await tick(0);
  expect(rows()).toHaveLength(3);
  await tick(10 * JOURNEY_POLL_MS);
  expect(fetchJourneyDocument).toHaveBeenCalledTimes(reads + 1);
});

it('does not re-read a journey when no request is in flight', async () => {
  commits = 3;
  render(<Desk running={false} />);
  await tick(0);
  expect(rows()).toHaveLength(3);
  await tick(10 * JOURNEY_POLL_MS);
  expect(fetchJourneyDocument).toHaveBeenCalledTimes(1);
});

it('reports a failed read and recovers on the next one without ending the run', async () => {
  commits = 1;
  render(<Desk running />);
  await tick(0);
  expect(rows()).toEqual(['Traveler authorized']);

  vi.mocked(fetchJourneyDocument).mockRejectedValueOnce(new Error('Data API throttled'));
  await tick(JOURNEY_POLL_MS);
  expect(screen.getByText('Read failed; any displayed evidence is from the last load.'))
    .toBeInTheDocument();
  expect(rows()).toEqual(['Traveler authorized']);

  commits = 2;
  await tick(JOURNEY_POLL_MS);
  expect(screen.queryByText(/Read failed/)).toBeNull();
  expect(rows()).toEqual(['Traveler authorized', 'Checkpoint persisted']);
});
