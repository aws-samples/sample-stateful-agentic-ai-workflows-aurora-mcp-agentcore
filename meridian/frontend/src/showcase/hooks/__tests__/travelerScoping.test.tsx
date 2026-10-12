import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useMeridianShowcase } from '../useMeridianShowcase';
import {
  confirmBooking, deleteMemoryFact, fetchHealth, fetchMemoryProfile, fetchProducts, processOrder,
  readBooking, readHold, sendChatMessage, updateMemoryFact,
} from '../../../api/client';
import { signedInAs } from '../../../test/signedIn';

vi.mock('../../../api/client', () => ({
  fetchHealth: vi.fn(), fetchMemoryProfile: vi.fn(), fetchProducts: vi.fn(),
  sendChatMessage: vi.fn(), processOrder: vi.fn(), readHold: vi.fn(), readBooking: vi.fn(),
  confirmBooking: vi.fn(), deleteMemoryFact: vi.fn(), updateMemoryFact: vi.fn(),
}));

const tokyo = {
  product_id: 'CTY-002', name: 'Tokyo Neon Nights', price: 2499, brand: 'Meridian',
  category: 'city', description: '', image_url: '', available_sizes: ['5 nights'],
};
const held = {
  order_id: 'HLD-9', status: 'held', hold_expires_at: '2099-01-01T00:00:00Z',
  items: [{ product_id: 'CTY-002', name: tokyo.name, size: '5 nights', quantity: 2, unit_price: 2499 }],
  subtotal: 4998, tax: 0, shipping: 0, total: 4998, payment_required: false,
};

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  window.history.replaceState(null, '', '/showcase');
  vi.mocked(fetchHealth).mockResolvedValue({ status: 'healthy' });
  vi.mocked(fetchMemoryProfile).mockResolvedValue({
    traveler_id: 'trv_a', profile: { party_size: 2 }, facts: [{ key: 'seat', value: 'aisle', confidence: 1 }],
  });
  vi.mocked(fetchProducts).mockResolvedValue([tokyo]);
  vi.mocked(sendChatMessage).mockResolvedValue({
    message: 'Options.', conversation_id: 'c-1', products: [], activities: [],
  });
  vi.mocked(readHold).mockResolvedValue({ message: 'Not recorded yet.', activities: [] });
  vi.mocked(readBooking).mockResolvedValue({ message: 'Held.', order: held, activities: [] } as never);
});

describe('saved and compared trips', () => {
  it('stay with the traveler who saved them', async () => {
    const first = renderHook(() => useMeridianShowcase(), { wrapper: signedInAs('trv_a') });
    await waitFor(() => expect(first.result.current.catalog.length).toBe(1));
    act(() => first.result.current.saveTrip(tokyo));
    expect(first.result.current.savedTrips).toHaveLength(1);
    first.unmount();

    const other = renderHook(() => useMeridianShowcase(), { wrapper: signedInAs('trv_b') });
    await waitFor(() => expect(other.result.current.catalog.length).toBe(1));
    expect(other.result.current.savedTrips).toEqual([]);
    other.unmount();

    const again = renderHook(() => useMeridianShowcase(), { wrapper: signedInAs('trv_a') });
    expect(again.result.current.savedTrips.map(trip => trip.product_id)).toEqual(['CTY-002']);
  });

  it('are not carried across when the identity changes under a mounted page', async () => {
    let session = signedInAs('trv_a');
    const { result, rerender } = renderHook(() => useMeridianShowcase(), {
      wrapper: ({ children }) => session({ children }),
    });
    await waitFor(() => expect(result.current.catalog.length).toBe(1));
    act(() => result.current.saveTrip(tokyo));
    session = signedInAs('trv_b');
    rerender();
    expect(result.current.savedTrips).toEqual([]);
    expect(window.localStorage.getItem('meridian:trip-workspace:v1:trv_b'))
      .not.toContain('CTY-002');
  });

  it('are kept in memory only while nobody is identified', async () => {
    const { result } = renderHook(() => useMeridianShowcase());
    await waitFor(() => expect(result.current.catalog.length).toBe(1));
    act(() => result.current.saveTrip(tokyo));
    expect(result.current.savedTrips).toHaveLength(1);
    expect(Object.keys(window.localStorage).filter(key => key.includes('trip-workspace')))
      .toEqual([]);
  });
});

describe('the verified identity', () => {
  it('shows the traveler id the API confirmed when it differs from the sign-in claim', async () => {
    const { result } = renderHook(() => useMeridianShowcase(), {
      wrapper: signedInAs('trv_claimed', 'Alex Lee', 'trv_from_api'),
    });
    await waitFor(() => expect(fetchMemoryProfile).toHaveBeenCalled());
    expect(result.current.traveler).toMatchObject({ id: 'trv_from_api', idVerified: true });
  });

  it('keeps the claim marked unconfirmed until the API answers', async () => {
    const { result } = renderHook(() => useMeridianShowcase(), {
      wrapper: signedInAs('trv_claimed', 'Alex Lee'),
    });
    await waitFor(() => expect(fetchMemoryProfile).toHaveBeenCalled());
    expect(result.current.traveler).toMatchObject({ id: 'trv_claimed', idVerified: false });
  });
});

describe('writes ask for the current traveler, never an id', () => {
  const wrapper = signedInAs('trv_a', 'Alex Lee', 'trv_a');

  it('holds a trip as "me"', async () => {
    vi.mocked(processOrder).mockResolvedValue({ message: 'Held.', order: held, activities: [] });
    const { result } = renderHook(() => useMeridianShowcase(), { wrapper });
    await waitFor(() => expect(result.current.travelersCount).toBe(2));
    await act(async () => { await result.current.holdTrip(tokyo); });
    const body = vi.mocked(processOrder).mock.calls[0][0];
    expect(body.traveler_id).toBe('me');
    expect(JSON.stringify(body)).not.toContain('trv_a');
  });

  it('updates and deletes a memory fact as "me"', async () => {
    vi.mocked(updateMemoryFact).mockResolvedValue({ key: 'seat', value: 'window', confidence: 1 });
    vi.mocked(deleteMemoryFact).mockResolvedValue(undefined);
    const { result } = renderHook(() => useMeridianShowcase(), { wrapper });
    await waitFor(() => expect(result.current.travelersCount).toBe(2));
    await act(async () => { await result.current.updateMemoryPreference('seat', 'window'); });
    expect(updateMemoryFact).toHaveBeenCalledWith('me', 'seat', 'window');
    await act(async () => { await result.current.deleteMemoryPreference('seat'); });
    expect(deleteMemoryFact).toHaveBeenCalledWith('me', 'seat');
  });

  it('confirms a trip with a notice that names only the signed-in traveler', async () => {
    vi.mocked(processOrder).mockResolvedValue({ message: 'Held.', order: held, activities: [] });
    vi.mocked(confirmBooking).mockResolvedValue({
      message: 'Confirmed.', order: { ...held, status: 'confirmed' }, activities: [],
    });
    const { result } = renderHook(() => useMeridianShowcase(), { wrapper });
    await waitFor(() => expect(result.current.travelersCount).toBe(2));
    await act(async () => { await result.current.submitPrompt('Plan Tokyo', 4); });
    await act(async () => { await result.current.holdTrip(tokyo); });
    await act(async () => { await result.current.confirmTrip(tokyo); });
    expect(result.current.workspaceNotice).toBe('Tokyo Neon Nights is confirmed for Alex.');
    expect(result.current.workspaceNotice).not.toContain('Jordan');
  });
});
