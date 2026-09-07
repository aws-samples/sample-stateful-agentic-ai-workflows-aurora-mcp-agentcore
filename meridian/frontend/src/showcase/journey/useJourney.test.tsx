import { renderHook, waitFor } from '@testing-library/react';
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
  expect(fetchJourneyDocument).toHaveBeenCalledWith('current-journey');
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
