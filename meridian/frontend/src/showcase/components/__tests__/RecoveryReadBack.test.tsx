import { act, render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { DesktopMeridianApp } from '../../DesktopMeridianApp';
import { JOURNEY_POLL_MS } from '../../journey/useJourney';
import { makeState } from './makeState';

const THREAD = 'phase5-gave-up';
const execution = (status: string, lease: string) => ({
  execution_id: 'exe_1', attempt: 1, worker_id: 'w1', status, started_at: null, ended_at: null,
  lease_expires_at: lease,
});

function journey(status: string, lease: string) {
  return {
    workflow: {
      status: 'observed', source: 's', conversation_id: THREAD, query: 'q', message: 'paused',
      workflow_status: 'paused', travelers_count: 2, activities: [],
    },
    journey_id: 'jrn_1', traveler_id: 'trv_meridian_demo', status, active_thread_id: THREAD,
    snapshot_store: { kind: 'Aurora workflow_snapshots', durable: true },
    observed_at: '2026-10-28T12:00:00Z',
    executions: { status: 'observed', source: 'journey_executions', items: [execution(status, lease)] },
  };
}

let current = journey('running', '2026-10-28T12:01:00Z');
const fetchMock = vi.fn();

beforeEach(() => {
  vi.useFakeTimers();
  window.history.replaceState(null, '', `/showcase?view=recovery&thread=${THREAD}`);
  fetchMock.mockReset().mockImplementation(async (url: string) => new Response(
    JSON.stringify(String(url).includes('/journeys?')
      ? { journeys: [{ journey_id: 'jrn_1', active_thread_id: new URL(String(url)).searchParams.get('thread_id') }] }
      : current),
    { status: 200 }));
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

const reads = () => fetchMock.mock.calls.filter(([url]) => /\/journeys\/jrn_1$/.test(String(url))).length;

function renderGaveUp(settleWorkflow = vi.fn(), conversationId = THREAD) {
  const state = makeState({
    selectedPhase: 5, lastRequestPhase: 5, conversationId, awaitingWorkflow: true,
    isLoading: false, error: 'The recovery is taking longer than this page waits.', settleWorkflow,
  });
  render(<DesktopMeridianApp state={state} theme="dark" onToggleTheme={vi.fn()} />);
  return settleWorkflow;
}

describe('a recovery the browser stopped waiting for', () => {
  it('keeps reading its journey while a worker still holds the lease, then applies the result', async () => {
    const settle = renderGaveUp();
    await act(async () => { await vi.advanceTimersByTimeAsync(JOURNEY_POLL_MS * 3 + 50); });
    expect(reads()).toBeGreaterThanOrEqual(3);
    expect(settle).not.toHaveBeenCalled();
    current = journey('succeeded', '2026-10-28T12:01:00Z');
    await act(async () => { await vi.advanceTimersByTimeAsync(JOURNEY_POLL_MS * 2 + 50); });
    expect(settle).toHaveBeenCalledWith(expect.objectContaining({ journey_id: 'jrn_1' }));
  });

  it('applies a journey whose lease has lapsed without waiting for a status change', async () => {
    current = journey('running', '2026-10-28T11:59:00Z');
    const settle = renderGaveUp();
    await act(async () => { await vi.advanceTimersByTimeAsync(JOURNEY_POLL_MS + 50); });
    expect(settle).toHaveBeenCalled();
  });

  it('does not apply a document that belongs to another thread', async () => {
    // The list names this recovery's thread, but the document it points to is another one.
    current = journey('succeeded', '2026-10-28T11:59:00Z');
    const settle = renderGaveUp(vi.fn(), 'phase5-another-thread');
    await act(async () => { await vi.advanceTimersByTimeAsync(JOURNEY_POLL_MS * 3 + 50); });
    expect(reads()).toBeGreaterThanOrEqual(1);
    expect(settle).not.toHaveBeenCalled();
  });
});

describe('the recovery desk replays a saved step only when no worker holds the journey', () => {
  const renderWithError = (restoreJourney = vi.fn()) => {
    const state = makeState({
      selectedPhase: 5, lastRequestPhase: 5, conversationId: THREAD, awaitingWorkflow: true,
      isLoading: false, error: 'The workflow is still running.', restoreJourney,
      settleWorkflow: vi.fn(),
    });
    render(<DesktopMeridianApp state={state} theme="dark" onToggleTheme={vi.fn()} />);
    return restoreJourney;
  };

  it('does not restore while the latest execution holds a live lease', async () => {
    current = journey('running', '2026-10-28T12:01:00Z');
    const restore = renderWithError();
    await act(async () => { await vi.advanceTimersByTimeAsync(JOURNEY_POLL_MS * 2 + 50); });
    expect(reads()).toBeGreaterThanOrEqual(2);
    expect(restore).not.toHaveBeenCalled();
  });

  it('restores on an error once the lease has lapsed', async () => {
    current = journey('running', '2026-10-28T11:59:00Z');
    const restore = renderWithError();
    await act(async () => { await vi.advanceTimersByTimeAsync(JOURNEY_POLL_MS + 50); });
    expect(restore).toHaveBeenCalledWith(expect.objectContaining({ journey_id: 'jrn_1' }));
  });
});
