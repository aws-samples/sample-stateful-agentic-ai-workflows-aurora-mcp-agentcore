import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useMeridianShowcase } from '../useMeridianShowcase';
import { fetchHealth, fetchMemoryProfile, fetchProducts, processOrder, readHold, sendChatMessage } from '../../../api/client';
import { QuoteReviewRequired } from '../../../api/request';

vi.mock('../../../api/client', () => ({
  fetchHealth: vi.fn(), fetchMemoryProfile: vi.fn(), fetchProducts: vi.fn(), sendChatMessage: vi.fn(),
  processOrder: vi.fn(), readHold: vi.fn(), readBooking: vi.fn(), confirmBooking: vi.fn(),
  deleteMemoryFact: vi.fn(), updateMemoryFact: vi.fn(),
}));

vi.mock('../../lib/tripWorkspace', async importOriginal => ({
  ...(await importOriginal<typeof import('../../lib/tripWorkspace')>()),
  loadTripWorkspace: () => ({ savedTrips: [], compareTrips: [] }),
  saveTripWorkspace: vi.fn(),
}));

const tokyo = {
  product_id: 'CTY-002', name: 'Tokyo Neon Nights', price: 2500, brand: 'Meridian', category: 'city',
  description: '', image_url: '', available_sizes: ['5 nights', '7 nights'],
  availability: { '5 nights': 10, '7 nights': 7 },
};
const terms = (size: string, unit = 250000, quantity = 2) => ({
  unit_price_cents: unit, total_cents: unit * quantity, currency: 'USD' as const, size, quantity,
});
const heldAt = (unit: number, size = '5 nights') => ({
  order_id: 'HLD-9', status: 'held', hold_expires_at: '2099-01-01T00:00:00Z', payment_required: false,
  items: [{ product_id: 'CTY-002', name: 'Tokyo Neon Nights', size, quantity: 2, unit_price: unit / 100 }],
  subtotal: unit * 2 / 100, tax: 0, shipping: 0, total: unit * 2 / 100,
});

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  window.history.replaceState(null, '', '/showcase');
  vi.mocked(readHold).mockReset().mockResolvedValue({ message: 'Not recorded yet.', activities: [] });
  vi.mocked(processOrder).mockReset();
  vi.mocked(fetchHealth).mockResolvedValue({ status: 'healthy' });
  vi.mocked(fetchMemoryProfile).mockResolvedValue({ traveler_id: 'trv_meridian_demo', profile: { party_size: 2 }, facts: [] });
  vi.mocked(fetchProducts).mockResolvedValue([tokyo]);
  vi.mocked(sendChatMessage).mockResolvedValue({ message: 'Options.', conversation_id: 'conv-1', products: [], activities: [] });
});

async function mounted() {
  const rendered = renderHook(() => useMeridianShowcase());
  await waitFor(() => expect(rendered.result.current.travelersCount).toBe(2));
  return rendered;
}

const sent = (call: number) => vi.mocked(processOrder).mock.calls[call][0];

describe('a hold carries the terms the traveler saw', () => {
  it('sends the displayed duration and quote in one request', async () => {
    vi.mocked(processOrder).mockResolvedValue({ message: 'Held.', order: heldAt(250000), activities: [] });
    const { result } = await mounted();
    await act(async () => { await result.current.holdTrip(tokyo, terms('5 nights')); });
    expect(processOrder).toHaveBeenCalledTimes(1);
    expect(sent(0)).toMatchObject({ product_id: 'CTY-002', size: '5 nights', quantity: 2, quote: terms('5 nights') });
  });

  it('builds the default terms when the caller names none', async () => {
    vi.mocked(processOrder).mockResolvedValue({ message: 'Held.', order: heldAt(250000), activities: [] });
    const { result } = await mounted();
    await act(async () => { await result.current.holdTrip(tokyo); });
    expect(sent(0)).toMatchObject({ size: '5 nights', quantity: 2, quote: terms('5 nights') });
  });

  it('starts on the first duration with places when the first is sold out', async () => {
    vi.mocked(processOrder).mockResolvedValue({ message: 'Held.', order: heldAt(250000, '7 nights'), activities: [] });
    const { result } = await mounted();
    const soldOut = { ...tokyo, availability: { '5 nights': 0, '7 nights': 7 } };
    await act(async () => { await result.current.holdTrip(soldOut); });
    expect(sent(0)).toMatchObject({ size: '7 nights', quote: terms('7 nights') });
  });

  it('sends the new duration when the traveler changed it before submitting', async () => {
    vi.mocked(processOrder).mockResolvedValue({ message: 'Held.', order: heldAt(250000, '7 nights'), activities: [] });
    const { result } = await mounted();
    await act(async () => { await result.current.holdTrip(tokyo, terms('7 nights')); });
    expect(sent(0)).toMatchObject({ size: '7 nights', quote: terms('7 nights') });
    expect(readHold).not.toHaveBeenCalled();
  });
});

describe('a price that changed after it was shown', () => {
  it('shows the new terms, places no hold, and sends them only after the approval click', async () => {
    vi.mocked(processOrder)
      .mockRejectedValueOnce(new QuoteReviewRequired('The price changed.', 'price_changed', terms('5 nights', 260000)))
      .mockResolvedValueOnce({ message: 'Held.', order: heldAt(260000), activities: [] });
    const { result } = await mounted();
    await act(async () => { await result.current.holdTrip(tokyo, terms('5 nights')); });
    expect(result.current.holdReview).toMatchObject({
      productId: 'CTY-002', code: 'price_changed', quote: terms('5 nights', 260000),
    });
    expect(result.current.error).toBeNull();
    expect(result.current.tripHolds).toHaveLength(0);
    expect(processOrder).toHaveBeenCalledTimes(1);

    await act(async () => { await result.current.holdTrip(tokyo, terms('5 nights', 260000)); });
    expect(processOrder).toHaveBeenCalledTimes(2);
    expect(sent(1).quote).toEqual(terms('5 nights', 260000));
    expect(sent(1).conversation_id).toBe(sent(0).conversation_id);
    expect(result.current.holdReview).toBeNull();
    expect(result.current.tripHolds[0].order.total).toBe(5200);
  });

  it('sends the reviewed terms on approval even when clearing the saved quote failed', async () => {
    vi.mocked(processOrder)
      .mockImplementationOnce(async () => {
        vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('storage full'); });
        throw new QuoteReviewRequired('The price changed.', 'price_changed', terms('5 nights', 260000));
      })
      .mockResolvedValueOnce({ message: 'Held.', order: heldAt(260000), activities: [] });
    const { result } = await mounted();
    await act(async () => { await result.current.holdTrip(tokyo, terms('5 nights')); });
    vi.restoreAllMocks();
    expect(result.current.holdReview?.quote).toEqual(terms('5 nights', 260000));

    await act(async () => { await result.current.holdTrip(tokyo, terms('5 nights', 260000)); });
    expect(sent(1).quote).toEqual(terms('5 nights', 260000));
    expect(sent(1).conversation_id).toBe(sent(0).conversation_id);
  });

  it('goes back to the saved quote for a lost-reply retry after the approval was sent', async () => {
    vi.mocked(processOrder)
      .mockRejectedValueOnce(new QuoteReviewRequired('The price changed.', 'price_changed', terms('5 nights', 260000)))
      .mockRejectedValueOnce(new TypeError('Response lost after commit'))
      .mockResolvedValueOnce({ message: 'Replayed.', order: heldAt(260000), activities: [] });
    const { result } = await mounted();
    await act(async () => { await result.current.holdTrip(tokyo, terms('5 nights')); });
    await act(async () => { await result.current.holdTrip(tokyo, terms('5 nights', 260000)); });
    await act(async () => { await result.current.holdTrip(tokyo, terms('5 nights', 270000)); });
    expect(sent(2).quote).toEqual(terms('5 nights', 260000));
  });

  it('asks for the same review when an older client has no quote on file', async () => {
    vi.mocked(processOrder).mockRejectedValueOnce(
      new QuoteReviewRequired('Review the current price.', 'quote_required', terms('5 nights')));
    const { result } = await mounted();
    await act(async () => { await result.current.holdTrip(tokyo); });
    expect(result.current.holdReview?.code).toBe('quote_required');
    expect(result.current.tripHolds).toHaveLength(0);
  });

  it('is dropped by clearHoldReview and when the trip dialog closes', async () => {
    vi.mocked(processOrder).mockRejectedValue(
      new QuoteReviewRequired('The price changed.', 'price_changed', terms('5 nights', 260000)));
    const { result } = await mounted();
    await act(async () => { await result.current.holdTrip(tokyo); });
    act(() => result.current.clearHoldReview());
    expect(result.current.holdReview).toBeNull();
    await act(async () => { await result.current.holdTrip(tokyo); });
    expect(result.current.holdReview).not.toBeNull();
    act(() => result.current.closeTripDetails());
    expect(result.current.holdReview).toBeNull();
  });
});

describe('a lost reply', () => {
  it('is retried with the original quote even when the catalog price moved', async () => {
    vi.mocked(processOrder)
      .mockRejectedValueOnce(new TypeError('Response lost after commit'))
      .mockResolvedValueOnce({ message: 'Replayed.', order: heldAt(250000), activities: [] });
    const { result } = await mounted();
    await act(async () => { await result.current.holdTrip(tokyo, terms('5 nights')); });
    expect(result.current.error).toContain('could not confirm');
    const repriced = { ...tokyo, price: 2600 };
    await act(async () => { await result.current.holdTrip(repriced, terms('5 nights', 260000)); });
    expect(sent(1).quote).toEqual(terms('5 nights'));
    expect(sent(1).conversation_id).toBe(sent(0).conversation_id);
    expect(result.current.tripHolds[0].order.total).toBe(5000);
  });

  it('still loads a saved hold identity written before quotes existed', async () => {
    vi.mocked(processOrder).mockResolvedValue({ message: 'Held.', order: heldAt(250000), activities: [] });
    const { result } = await mounted();
    const key = Object.keys(window.localStorage).find(name => name.startsWith('meridian:booking-recovery:v1:'));
    expect(key).toBeUndefined();
    window.localStorage.setItem('meridian:booking-recovery:v1:me', JSON.stringify({
      intents: { [JSON.stringify(['CTY-002', '5 nights', 2])]: {
        conversationId: 'conv_hold_old', productId: 'CTY-002', duration: '5 nights', quantity: 2 } },
      bookings: {},
    }));
    await act(async () => { await result.current.holdTrip(tokyo, terms('5 nights')); });
    expect(sent(0).conversation_id).toBe('conv_hold_old');
    expect(sent(0).quote).toEqual(terms('5 nights'));
  });
});
