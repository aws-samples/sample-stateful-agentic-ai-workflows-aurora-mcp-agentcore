import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import { fetchJourneyDocument, fetchJourneys } from '../../api/client';
import { useJourney } from './useJourney';
import type { JourneyDocument, JourneySummary } from './types';

vi.mock('../../api/client', () => ({ fetchJourneyDocument: vi.fn(), fetchJourneys: vi.fn() }));
beforeEach(() => vi.resetAllMocks());

it('loads evidence for the active recovery instead of an older journey in the URL', async () => {
  vi.mocked(fetchJourneys).mockResolvedValue([
    { journey_id: 'newest-unrelated', active_thread_id: 'other-thread' },
    { journey_id: 'current-journey', active_thread_id: 'current-thread' },
  ] as JourneySummary[]);
  vi.mocked(fetchJourneyDocument).mockResolvedValue({ journey_id: 'current-journey', active_thread_id: 'current-thread' } as JourneyDocument);
  const resolve = vi.fn();
  const { result } = renderHook(() => useJourney('old-journey', resolve, true, 'current-thread'));
  await waitFor(() => expect(result.current.document?.journey_id).toBe('current-journey'));
  expect(fetchJourneyDocument).toHaveBeenCalledWith('current-journey', expect.any(AbortSignal));
  expect(fetchJourneyDocument).not.toHaveBeenCalledWith('old-journey');
  expect(resolve).toHaveBeenCalledWith('current-journey');
});

it('reports missing current-thread evidence without falling back to another recovery', async () => {
  vi.mocked(fetchJourneys).mockResolvedValue([{ journey_id: 'other', active_thread_id: 'other-thread' }] as JourneySummary[]);
  const { result } = renderHook(() => useJourney('old-journey', vi.fn(), true, 'current-thread'));
  await waitFor(() => expect(result.current.error).toMatch(/for this recovery yet/));
  expect(result.current.document).toBeNull();
  expect(fetchJourneyDocument).not.toHaveBeenCalled();
});


it('ends a stalled read and allows a fresh request after the timeout', async () => {
  vi.useFakeTimers();
  try {
    vi.mocked(fetchJourneyDocument).mockImplementationOnce((_id, signal) => new Promise((_resolve, reject) => {
      signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
    }));
    const { result, unmount } = renderHook(() => useJourney('journey', vi.fn(), true));
    await act(async () => { await vi.advanceTimersByTimeAsync(20000); });
    expect(result.current.loading).toBe(false);
    expect(result.current.error).toContain('took too long');
    vi.mocked(fetchJourneyDocument).mockResolvedValue({ journey_id: 'journey', active_thread_id: 'thread' } as JourneyDocument);
    await act(async () => { result.current.refresh(); });
    expect(result.current.document?.journey_id).toBe('journey');
    expect(result.current.error).toBeNull();
    unmount();
  } finally {
    vi.useRealTimers();
  }
});
