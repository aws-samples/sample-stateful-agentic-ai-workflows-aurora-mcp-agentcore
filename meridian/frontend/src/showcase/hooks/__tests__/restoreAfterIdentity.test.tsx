import { act, render, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AuthGate } from '../../../auth/AuthGate';
import { useMeridianShowcase } from '../useMeridianShowcase';
import { holdIntentKey, saveBookingRecovery } from '../../lib/bookingRecovery';
import { fetchHealth, fetchMemoryProfile, fetchProducts, fetchSessionIdentity, processOrder, readHold } from '../../../api/client';

vi.mock('../../../api/client', () => ({
  fetchHealth: vi.fn(), fetchMemoryProfile: vi.fn(), fetchProducts: vi.fn(), sendChatMessage: vi.fn(),
  processOrder: vi.fn(), readHold: vi.fn(), readBooking: vi.fn(), confirmBooking: vi.fn(),
  deleteMemoryFact: vi.fn(), updateMemoryFact: vi.fn(), fetchSessionIdentity: vi.fn(),
}));

vi.mock('../../lib/tripWorkspace', async importOriginal => ({
  ...(await importOriginal<typeof import('../../lib/tripWorkspace')>()),
  loadTripWorkspace: () => ({ savedTrips: [], compareTrips: [] }),
  saveTripWorkspace: vi.fn(),
}));

let showcase!: ReturnType<typeof useMeridianShowcase>;

function Showcase() {
  showcase = useMeridianShowcase();
  return null;
}

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  window.history.replaceState(null, '', '/showcase');
  vi.mocked(readHold).mockReset().mockResolvedValue({ message: 'Not recorded yet.', activities: [] });
  vi.mocked(fetchHealth).mockResolvedValue({ status: 'healthy' });
  vi.mocked(fetchMemoryProfile).mockResolvedValue({ traveler_id: 'trv_real', profile: {}, facts: [] });
  vi.mocked(fetchProducts).mockResolvedValue([]);
});

it('reads the holds saved for the traveler the API names once it answers', async () => {
  const intent = {
    conversationId: 'conv_hold_saved', productId: 'CTY-002', duration: '5 nights', quantity: 2,
  };
  saveBookingRecovery('trv_real', {
    intents: { [holdIntentKey(intent.productId, intent.duration, intent.quantity)]: intent },
    bookings: {},
  });
  let answer!: (value: { traveler_id: string; authentication: 'bearer' }) => void;
  vi.mocked(fetchSessionIdentity).mockImplementation(
    () => new Promise(resolve => { answer = resolve; }),
  );

  saveBookingRecovery('me', { intents: { stray: intent }, bookings: {} });

  render(<AuthGate config={null}><Showcase /></AuthGate>);
  expect(readHold).not.toHaveBeenCalled();
  answer({ traveler_id: 'trv_real', authentication: 'bearer' });

  await waitFor(() => expect(readHold).toHaveBeenCalledWith(intent, expect.any(AbortSignal)));
});

describe('a hold or booking before the API names the traveler', () => {
  const product = {
    product_id: 'CTY-002', name: 'Tokyo Neon Nights', price: 2499, brand: 'Meridian', category: 'city',
    description: '', image_url: '', available_sizes: ['5 nights'],
  };
  const heldOrder = {
    order_id: 'HLD-1', items: [], subtotal: 0, tax: 0, shipping: 0, total: 0, status: 'held',
    hold_expires_at: new Date(Date.now() + 43200000).toISOString(),
  };

  it('refuses the hold until the traveler id is known, then places it', async () => {
    let answer!: (value: { traveler_id: string; authentication: 'bearer' }) => void;
    vi.mocked(fetchSessionIdentity).mockImplementation(
      () => new Promise(resolve => { answer = resolve; }),
    );
    vi.mocked(processOrder).mockResolvedValue({ message: 'Held.', activities: [], order: heldOrder });
    vi.mocked(fetchProducts).mockResolvedValue([product]);
    render(<AuthGate config={null}><Showcase /></AuthGate>);
    await waitFor(() => expect(showcase.travelersCount).toBeGreaterThan(0));

    await act(async () => { await showcase.holdTrip(product); });
    expect(showcase.error).toMatch(/Wait for traveler context/);
    expect(processOrder).not.toHaveBeenCalled();

    await act(async () => { answer({ traveler_id: 'trv_real', authentication: 'bearer' }); });
    await act(async () => { await showcase.holdTrip(product); });
    expect(processOrder).toHaveBeenCalledTimes(1);
  });

  it('refuses a booking confirmation until the traveler id is known', async () => {
    vi.mocked(fetchProducts).mockResolvedValue([product]);
    vi.mocked(fetchSessionIdentity).mockImplementation(() => new Promise(() => undefined));
    render(<AuthGate config={null}><Showcase /></AuthGate>);
    await waitFor(() => expect(showcase.travelersCount).toBeGreaterThan(0));
    await waitFor(() => expect(showcase.catalog.length).toBeGreaterThan(0));
    act(() => {
      showcase.adoptJourneyHold({
        booking_id: 'HLD-1', package_id: product.product_id, duration: '5 nights', travelers_count: 2,
        unit_price: '10.00', total_amount: '20.00', status: 'held',
        hold_expires_at: heldOrder.hold_expires_at, hold_created_at: null, confirmed_at: null,
      });
    });

    await act(async () => { await showcase.confirmTrip(product); });
    expect(showcase.error).toMatch(/Wait for traveler context/);
    expect(processOrder).not.toHaveBeenCalled();
  });
});
