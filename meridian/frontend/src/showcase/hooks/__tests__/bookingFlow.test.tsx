import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useMeridianShowcase } from '../useMeridianShowcase';
import { confirmBooking, fetchHealth, fetchMemoryProfile, fetchProducts, processOrder, sendChatMessage } from '../../../api/client';

vi.mock('../../../api/client', () => ({
  fetchHealth: vi.fn(), fetchMemoryProfile: vi.fn(), fetchProducts: vi.fn(), sendChatMessage: vi.fn(),
  processOrder: vi.fn(), confirmBooking: vi.fn(), deleteMemoryFact: vi.fn(), updateMemoryFact: vi.fn(),
}));

vi.mock('../../lib/tripWorkspace', async importOriginal => ({
  ...(await importOriginal<typeof import('../../lib/tripWorkspace')>()),
  loadTripWorkspace: () => ({ savedTrips: [], compareTrips: [] }),
  saveTripWorkspace: vi.fn(),
}));

const tokyo = { product_id: 'CTY-002', name: 'Tokyo Neon Nights', price: 2499, brand: 'Meridian', category: 'city', description: '', image_url: '', available_sizes: ['5 nights'] };
const held = { order_id: 'HLD-9', items: [{ product_id: 'CTY-002', name: 'Tokyo Neon Nights', size: '5 nights', quantity: 2, unit_price: 2499 }], subtotal: 4998, tax: 0, shipping: 0, total: 4998, status: 'held', hold_expires_at: '2099-01-01T00:00:00Z', payment_required: false };

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(processOrder).mockReset();
  vi.mocked(confirmBooking).mockReset();
  vi.mocked(fetchHealth).mockResolvedValue({ status: 'healthy' });
  vi.mocked(fetchMemoryProfile).mockResolvedValue({ traveler_id: 'trv_meridian_demo', profile: { party_size: 2 }, facts: [] });
  vi.mocked(fetchProducts).mockResolvedValue([tokyo]);
  vi.mocked(sendChatMessage).mockResolvedValue({ message: 'Options.', conversation_id: 'conv-1', products: [], activities: [] });
});

describe('Booking confirmation', () => {
  it('retries an unacknowledged direct hold with the same identity, even after clearing chat', async () => {
    vi.mocked(processOrder)
      .mockRejectedValueOnce(new Error('Response lost after commit'))
      .mockResolvedValueOnce({ message: 'Replayed.', order: held, activities: [] });
    const { result } = renderHook(() => useMeridianShowcase());
    await waitFor(() => expect(result.current.travelersCount).toBe(2));
    await act(async () => { await result.current.holdTrip(tokyo); });
    const first = vi.mocked(processOrder).mock.calls[0][0];
    expect(first.conversation_id).toEqual(expect.any(String));
    expect(result.current.error).toMatch(/may have been saved/i);
    act(() => result.current.clearChat());
    await act(async () => { await result.current.holdTrip(tokyo); });
    expect(vi.mocked(processOrder).mock.calls[1][0].conversation_id).toBe(first.conversation_id);
    expect(result.current.tripHolds[0].order.order_id).toBe('HLD-9');
  });

  it('starts a new hold intent after a known hold expires', async () => {
    vi.mocked(processOrder)
      .mockResolvedValueOnce({ message: 'Held.', order: { ...held, hold_expires_at: '2000-01-01T00:00:00Z' }, activities: [] })
      .mockRejectedValueOnce(new Error('Renewal response lost'))
      .mockResolvedValueOnce({ message: 'Held again.', order: held, activities: [] });
    const { result } = renderHook(() => useMeridianShowcase());
    await waitFor(() => expect(result.current.travelersCount).toBe(2));
    await act(async () => { await result.current.submitPrompt('Plan Tokyo', 4); });
    await act(async () => { await result.current.holdTrip(tokyo); });
    await act(async () => { await result.current.holdTrip(tokyo); });
    await act(async () => { await result.current.holdTrip(tokyo); });
    const [first, second] = vi.mocked(processOrder).mock.calls.map(([request]) => request);
    expect(second.conversation_id).toEqual(expect.any(String));
    expect(second.conversation_id).not.toBe(first.conversation_id);
    expect(vi.mocked(processOrder).mock.calls[2][0].conversation_id).toBe(second.conversation_id);
  });

  it('asks for confirmation on a held trip, then confirms it through the booking service', async () => {
    vi.mocked(processOrder).mockResolvedValue({ message: 'Held.', order: held, activities: [] });
    vi.mocked(confirmBooking).mockResolvedValue({ message: 'Confirmed.', order: { ...held, status: 'confirmed', confirmed_at: '2026-09-10 13:00:00+00' }, activities: [] });
    const { result } = renderHook(() => useMeridianShowcase());
    await waitFor(() => expect(result.current.travelersCount).toBe(2));
    await act(async () => { await result.current.submitPrompt('Plan Tokyo', 4); });
    await act(async () => { await result.current.holdTrip(tokyo); });
    expect(result.current.tripHolds[0].order.status).toBe('held');

    act(() => result.current.requestBookingConfirmation(tokyo));
    expect(result.current.bookingPrompt?.order.order_id).toBe('HLD-9');
    expect(confirmBooking).not.toHaveBeenCalled();

    await act(async () => { await result.current.confirmTrip(tokyo); });
    expect(confirmBooking).toHaveBeenCalledWith({ booking_id: 'HLD-9', phase: 4, traveler_id: 'trv_meridian_demo', conversation_id: 'conv-1' });
    expect(result.current.bookingPrompt).toBeNull();
    expect(result.current.tripHolds[0].order.status).toBe('confirmed');
    expect(result.current.tripHolds[0].order.confirmed_at).toBe('2026-09-10 13:00:00+00');
    expect(result.current.actionDrawer?.kind).toBe('book');
    expect(result.current.messages[result.current.messages.length - 1]).toMatchObject({ role: 'bot', type: 'order', text: 'Confirmed.' });
  });

  it('refuses to confirm a trip that is not held and keeps a refused confirmation unconfirmed', async () => {
    const { result } = renderHook(() => useMeridianShowcase());
    await waitFor(() => expect(result.current.catalog.length).toBe(1));
    act(() => result.current.requestBookingConfirmation(tokyo));
    expect(result.current.bookingPrompt).toBeNull();
    await act(async () => { await result.current.confirmTrip(tokyo); });
    expect(confirmBooking).not.toHaveBeenCalled();

    let adopted = false;
    act(() => { adopted = result.current.adoptJourneyHold({ booking_id: 'HLD-5', package_id: 'CTY-002', duration: '3 nights', travelers_count: 2, unit_price: '2000.00', total_amount: '4000.00', hold_expires_at: '2099-01-01T00:00:00Z', status: 'held' }); });
    expect(adopted).toBe(true);
    expect(result.current.tripHolds[0]).toMatchObject({ productId: 'CTY-002', order: { order_id: 'HLD-5', status: 'held', total: 4000, items: [{ size: '3 nights', unit_price: 2000, quantity: 2 }] } });
    expect(result.current.selectedTrip?.product_id).toBe('CTY-002');
    expect(result.current.tripDetailsOpen).toBe(true);
    act(() => { adopted = result.current.adoptJourneyHold({ booking_id: 'HLD-6', package_id: 'UNKNOWN', duration: null, travelers_count: null, hold_expires_at: null, status: 'held' }); });
    expect(adopted).toBe(false);
    act(() => { adopted = result.current.adoptJourneyHold({ booking_id: 'incomplete', package_id: 'CTY-002', duration: null, travelers_count: null, hold_expires_at: null, status: 'held' }); });
    expect(adopted).toBe(false);
    expect(result.current.tripHolds[0].order.order_id).toBe('HLD-5');

    vi.mocked(confirmBooking).mockResolvedValue({ message: 'That hold has expired.', activities: [] });
    await act(async () => { await result.current.confirmTrip(tokyo); });
    expect(result.current.tripHolds[0].order.status).toBe('held');
    expect(result.current.messages[result.current.messages.length - 1]).toMatchObject({ role: 'bot', type: 'text', text: 'That hold has expired.' });
  });
});
