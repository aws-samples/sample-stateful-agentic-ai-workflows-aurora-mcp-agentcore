import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useMeridianShowcase } from '../useMeridianShowcase';
import { fetchHealth, fetchMemoryProfile, fetchProducts, processOrder, readBooking, readHold, sendChatMessage } from '../../../api/client';

vi.mock('../../../api/client', () => ({
  fetchHealth: vi.fn(), fetchMemoryProfile: vi.fn(), fetchProducts: vi.fn(), sendChatMessage: vi.fn(),
  processOrder: vi.fn(), readHold: vi.fn(), readBooking: vi.fn(), confirmBooking: vi.fn(), deleteMemoryFact: vi.fn(), updateMemoryFact: vi.fn(),
}));

vi.mock('../../lib/tripWorkspace', async importOriginal => ({
  ...(await importOriginal<typeof import('../../lib/tripWorkspace')>()),
  loadTripWorkspace: () => ({ savedTrips: [], compareTrips: [] }),
  saveTripWorkspace: vi.fn(),
}));

const tokyo = { product_id: 'CTY-002', name: 'Tokyo Neon Nights', price: 2499, brand: 'Meridian', category: 'city', description: '', image_url: '', available_sizes: ['5 nights'] };

function neverSettlingHold(): { signals: AbortSignal[] } {
  const signals: AbortSignal[] = [];
  vi.mocked(processOrder).mockImplementation((_request, signal) => {
    signals.push(signal as AbortSignal);
    return new Promise(() => undefined);
  });
  return { signals };
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  window.history.replaceState(null, '', '/showcase');
  vi.mocked(readHold).mockReset().mockResolvedValue({ message: 'Not recorded yet.', activities: [] });
  vi.mocked(readBooking).mockReset();
  vi.mocked(processOrder).mockReset();
  vi.mocked(fetchHealth).mockResolvedValue({ status: 'healthy' });
  vi.mocked(fetchMemoryProfile).mockResolvedValue({ traveler_id: 'trv_meridian_demo', profile: { party_size: 2 }, facts: [] });
  vi.mocked(fetchProducts).mockResolvedValue([tokyo]);
  vi.mocked(sendChatMessage).mockResolvedValue({ message: 'Options.', conversation_id: 'conv-1', products: [], activities: [] });
});

describe.each([
  ['changing the phase', (state: ReturnType<typeof useMeridianShowcase>) => state.setSelectedPhase(2)],
  ['clearing the chat', (state: ReturnType<typeof useMeridianShowcase>) => state.clearChat()],
])('a hold still in flight after %s', (_name, reset) => {
  it('tells the traveler why a new request waits, and stopWaiting aborts the write', async () => {
    const { signals } = neverSettlingHold();
    const { result } = renderHook(() => useMeridianShowcase());
    await waitFor(() => expect(result.current.travelersCount).toBe(2));
    act(() => { void result.current.holdTrip(tokyo); });
    await waitFor(() => expect(signals).toHaveLength(1));

    act(() => reset(result.current));

    await act(async () => { await result.current.submitPrompt('Plan Tokyo', 2); });
    expect(sendChatMessage).not.toHaveBeenCalled();
    expect(result.current.error).toMatch(/still waiting for Aurora/);

    act(() => result.current.stopWaiting());
    expect(signals[0].aborted).toBe(true);
    await waitFor(() => expect(processOrder).toHaveBeenCalledTimes(1));
    await act(async () => { await result.current.submitPrompt('Plan Tokyo', 2); });
    await waitFor(() => expect(sendChatMessage).toHaveBeenCalledTimes(1));
  });
});

describe('a hold that settles after the traveler switched phase', () => {
  const heldOrder = {
    order_id: 'HLD-1', items: [], subtotal: 100, tax: 0, shipping: 0, total: 100, status: 'held',
    hold_expires_at: new Date(Date.now() + 43200000).toISOString(),
  };

  function deferredHold(): { settle: () => void; signals: AbortSignal[] } {
    const signals: AbortSignal[] = [];
    let settle: () => void = () => undefined;
    vi.mocked(processOrder).mockImplementation((_request, signal) => {
      signals.push(signal as AbortSignal);
      return new Promise(resolve => {
        settle = () => resolve({ message: 'Held.', activities: [], order: heldOrder });
      });
    });
    return { settle: () => settle(), signals };
  }

  it('clears the wait message once the hold settles', async () => {
    const { settle, signals } = deferredHold();
    const { result } = renderHook(() => useMeridianShowcase());
    await waitFor(() => expect(result.current.travelersCount).toBe(2));
    let pending!: Promise<void>;
    act(() => { pending = result.current.holdTrip(tokyo); });
    await waitFor(() => expect(signals).toHaveLength(1));
    act(() => result.current.setSelectedPhase(2));
    await act(async () => { await result.current.submitPrompt('Plan Tokyo', 2); });
    expect(result.current.error).toMatch(/still waiting for Aurora/);

    await act(async () => { settle(); await pending; });
    expect(result.current.error).toBeNull();
  });

  it.each([
    ['changing the phase', (state: ReturnType<typeof useMeridianShowcase>) => state.setSelectedPhase(2)],
    ['clearing the chat', (state: ReturnType<typeof useMeridianShowcase>) => state.clearChat()],
  ])('keeps the stop control reachable after %s until the write settles', async (_name, reset) => {
    const { settle, signals } = deferredHold();
    const { result } = renderHook(() => useMeridianShowcase());
    await waitFor(() => expect(result.current.travelersCount).toBe(2));
    let pending!: Promise<void>;
    act(() => { pending = result.current.holdTrip(tokyo); });
    await waitFor(() => expect(signals).toHaveLength(1));

    act(() => reset(result.current));
    expect(result.current.isLoading).toBe(true);
    expect(result.current.pendingWrite).toBe('hold');

    act(() => result.current.stopWaiting());
    expect(signals[0].aborted).toBe(true);

    await act(async () => { settle(); await pending; });
    expect(result.current.isLoading).toBe(false);
    expect(result.current.pendingWrite).toBeNull();
  });
});
