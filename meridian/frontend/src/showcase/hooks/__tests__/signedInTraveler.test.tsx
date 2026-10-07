import type { JourneyDocument } from '../../journey/types';
import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useMeridianShowcase } from '../useMeridianShowcase';
import {
  fetchHealth, fetchMemoryProfile, fetchProducts, sendChatMessage,
} from '../../../api/client';
import { signedInAs } from '../../../test/signedIn';

vi.mock('../../../api/client', () => ({
  fetchHealth: vi.fn(), fetchMemoryProfile: vi.fn(), fetchProducts: vi.fn(),
  sendChatMessage: vi.fn(), processOrder: vi.fn(), readHold: vi.fn(), readBooking: vi.fn(),
  confirmBooking: vi.fn(), deleteMemoryFact: vi.fn(), updateMemoryFact: vi.fn(),
}));

vi.mock('../../lib/tripWorkspace', async importOriginal => ({
  ...(await importOriginal<typeof import('../../lib/tripWorkspace')>()),
  loadTripWorkspace: () => ({ savedTrips: [], compareTrips: [] }),
  saveTripWorkspace: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  window.history.replaceState(null, '', '/showcase');
  vi.mocked(fetchHealth).mockResolvedValue({ status: 'healthy' });
  vi.mocked(fetchMemoryProfile).mockResolvedValue({
    traveler_id: 'trv_demo_decoy', profile: { full_name: 'Profile Name', party_size: 1 }, facts: [],
  });
  vi.mocked(fetchProducts).mockResolvedValue([]);
  vi.mocked(sendChatMessage).mockResolvedValue({
    message: 'Options.', conversation_id: 'c-1', products: [], activities: [],
  });
});

const savedJourney = (travelerId: string) => ({
  traveler_id: travelerId, active_thread_id: 'saved-thread',
  workflow: {
    status: 'observed', source: 'checkpoint', conversation_id: 'saved-thread',
    query: 'Rework the trip.', message: 'Shortlist saved.', workflow_status: 'paused',
    next_nodes: ['availability'], activities: [], travelers_count: 2,
  },
  recommendations: { status: 'observed', source: 'checkpoint', items: [] },
}) as unknown as JourneyDocument;

describe('a signed-in traveler', () => {
  it('is shown by the name on their sign-in and never asked for by id', async () => {
    const { result } = renderHook(() => useMeridianShowcase(), {
      wrapper: signedInAs('trv_demo_decoy', 'Jordan Lee'),
    });
    await waitFor(() => expect(fetchMemoryProfile).toHaveBeenCalled());
    expect(result.current.traveler).toEqual({
      id: 'trv_demo_decoy', name: 'Jordan Lee', initials: 'JL', avatarUrl: null, idVerified: false,
    });
    expect(vi.mocked(fetchMemoryProfile).mock.calls.every(([id]) => id === 'me')).toBe(true);
  });

  it('is named from the Aurora profile when the build has no sign-in', async () => {
    const { result } = renderHook(() => useMeridianShowcase());
    await waitFor(() => expect(result.current.previewProfile).not.toBeNull());
    expect(result.current.traveler).toMatchObject({ id: null, name: 'Profile Name' });
  });

  it('sends the current-traveler alias on a chat turn, not a traveler id', async () => {
    const { result } = renderHook(() => useMeridianShowcase(), {
      wrapper: signedInAs('trv_demo_decoy', 'Jordan Lee'),
    });
    await waitFor(() => expect(result.current.previewProfile).not.toBeNull());
    await act(async () => { await result.current.setMemoryEnabled(true); });
    act(() => result.current.setSelectedPhase(4));
    await act(async () => { await result.current.submitPrompt('Plan Tokyo', 4); });
    const request = vi.mocked(sendChatMessage).mock.calls[0][0];
    expect(request.customer_id).toBe('me');
    expect(JSON.stringify(request)).not.toContain('trv_meridian_demo');
  });

  it('does not restore a saved recovery that belongs to another traveler', async () => {
    const { result } = renderHook(() => useMeridianShowcase(), {
      wrapper: signedInAs('trv_demo_decoy', 'Jordan Lee'),
    });
    await waitFor(() => expect(result.current.previewProfile).not.toBeNull());
    act(() => result.current.restoreJourney(savedJourney('trv_meridian_demo')));
    expect(result.current.selectedPhase).not.toBe(5);
    act(() => result.current.restoreJourney(savedJourney('trv_demo_decoy')));
    expect(result.current.selectedPhase).toBe(5);
  });

  it('shows initials and the neutral name for an account with an empty profile and no photo', async () => {
    vi.mocked(fetchMemoryProfile).mockResolvedValue({
      traveler_id: 'trv_demo_decoy', profile: {}, facts: [],
    });
    const { result } = renderHook(() => useMeridianShowcase(), {
      wrapper: signedInAs('trv_demo_decoy'),
    });
    await waitFor(() => expect(result.current.previewProfile).not.toBeNull());
    expect(result.current.traveler).toMatchObject({
      id: 'trv_demo_decoy', name: 'Your account', initials: 'YA', avatarUrl: null,
    });
  });
});
