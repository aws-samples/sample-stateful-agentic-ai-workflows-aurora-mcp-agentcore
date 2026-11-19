import { act, fireEvent, render, renderHook, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { RecoveryWorkspace } from '../../components/RecoveryWorkspace';
import { SHOWCASE_FINALE_PROMPT } from '../../lib/showcaseAdapters';
import { useMeridianShowcase } from '../useMeridianShowcase';
import { fetchHealth, fetchMemoryProfile, fetchProducts, sendChatMessage } from '../../../api/client';

vi.mock('../../../api/client', () => ({
  fetchHealth: vi.fn(), fetchMemoryProfile: vi.fn(), fetchProducts: vi.fn(), sendChatMessage: vi.fn(),
  processOrder: vi.fn(), readHold: vi.fn(), readBooking: vi.fn(), deleteMemoryFact: vi.fn(), updateMemoryFact: vi.fn(),
}));

vi.mock('../../lib/tripWorkspace', async importOriginal => ({
  ...(await importOriginal<typeof import('../../lib/tripWorkspace')>()),
  loadTripWorkspace: () => ({ savedTrips: [], compareTrips: [] }),
  saveTripWorkspace: vi.fn(),
}));

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  window.history.replaceState(null, '', '/showcase?view=recovery');
  vi.mocked(fetchHealth).mockResolvedValue({ status: 'healthy' });
  vi.mocked(fetchMemoryProfile).mockResolvedValue({ traveler_id: 'trv_meridian_demo', profile: { home_airport: 'JFK' }, facts: [] });
  vi.mocked(fetchProducts).mockResolvedValue([]);
  vi.mocked(sendChatMessage).mockResolvedValue({
    message: 'Paused for review.', activities: [], conversation_id: 'phase5-thread',
  });
});

describe('the Recovery desk launch the guides document', () => {
  it('sends one request carrying the canceled-flight prompt and the recovery intent', async () => {
    const { result } = renderHook(() => useMeridianShowcase());
    render(<RecoveryWorkspace state={result.current} />);

    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: 'Start recovery' }));
    });

    expect(sendChatMessage).toHaveBeenCalledTimes(1);
    expect(sendChatMessage).toHaveBeenCalledWith(
      expect.objectContaining({ message: SHOWCASE_FINALE_PROMPT, phase: 5, intent: 'recovery' }),
      expect.any(AbortSignal), expect.any(Function), expect.any(Function),
    );
  });
});
