import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { WORKFLOW_READ_BACK_LIMIT_MS, useMeridianShowcase } from '../useMeridianShowcase';
import { ReadBackRequiredError } from '../../../api/chatStream';
import {
  confirmBooking, fetchHealth, fetchMemoryProfile, fetchProducts, processOrder, readBooking,
  readHold, sendChatMessage,
} from '../../../api/client';
import { ApiError, STREAM_HARD_CAP_MS, STREAM_IDLE_TIMEOUT_MS } from '../../../api/request';

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

const tokyo = { product_id: 'CTY-002', name: 'Tokyo Neon Nights', price: 2499, brand: 'Meridian', category: 'city', description: '', image_url: '', available_sizes: ['5 nights'] };
const held = { order_id: 'HLD-9', items: [{ product_id: 'CTY-002', name: 'Tokyo Neon Nights', size: '5 nights', quantity: 2, unit_price: 2499 }], subtotal: 4998, tax: 0, shipping: 0, total: 4998, status: 'held', hold_expires_at: '2099-01-01T00:00:00Z', payment_required: false };
const confirmed = { ...held, status: 'confirmed' };
const UNKNOWN_HOLD = 'We could not confirm whether the hold was placed. Check your trips before trying again.';
const UNKNOWN_BOOKING = 'We could not confirm whether the booking was confirmed. Check your trips before trying again.';
const tookTooLong = () => new DOMException('The response took too long.', 'TimeoutError');

beforeEach(() => {
  vi.clearAllMocks();
  window.localStorage.clear();
  window.history.replaceState(null, '', '/showcase');
  vi.mocked(readHold).mockReset().mockResolvedValue({ message: 'Not recorded yet.', activities: [] });
  vi.mocked(readBooking).mockReset().mockResolvedValue({ message: 'Held.', order: held, activities: [] });
  vi.mocked(processOrder).mockReset();
  vi.mocked(confirmBooking).mockReset();
  vi.mocked(sendChatMessage).mockReset().mockResolvedValue({ message: 'Options.', conversation_id: 'conv-1', products: [], activities: [] });
  vi.mocked(fetchHealth).mockResolvedValue({ status: 'healthy' });
  vi.mocked(fetchMemoryProfile).mockResolvedValue({ traveler_id: 'trv_meridian_demo', profile: { party_size: 2 }, facts: [] });
  vi.mocked(fetchProducts).mockResolvedValue([tokyo]);
});
afterEach(() => { vi.useRealTimers(); });

async function ready() {
  const view = renderHook(() => useMeridianShowcase());
  await waitFor(() => expect(view.result.current.travelersCount).toBe(2));
  return view;
}

describe('a hold the page stopped waiting for', () => {
  it('shows the receipt Aurora holds instead of a failure', async () => {
    vi.mocked(processOrder).mockRejectedValueOnce(tookTooLong());
    const { result } = await ready();
    vi.mocked(readHold).mockResolvedValueOnce({ message: 'Hold read from Aurora.', order: held, activities: [] });
    await act(async () => { await result.current.holdTrip(tokyo); });
    const identity = vi.mocked(processOrder).mock.calls[0][0].conversation_id;
    expect(readHold).toHaveBeenCalledExactlyOnceWith(
      expect.objectContaining({ conversationId: identity }), expect.any(AbortSignal));
    expect(result.current.error).toBeNull();
    expect(result.current.tripHolds[0].order.order_id).toBe('HLD-9');
    expect(result.current.actionDrawer).toMatchObject({ kind: 'hold', live: true });
    expect(processOrder).toHaveBeenCalledTimes(1);
  });

  it('keeps the old wording when Aurora has no hold, or cannot be read', async () => {
    vi.mocked(processOrder).mockRejectedValue(tookTooLong());
    const { result } = await ready();
    await act(async () => { await result.current.holdTrip(tokyo); });
    expect(result.current.error).toBe(UNKNOWN_HOLD);
    expect(result.current.tripHolds).toEqual([]);
    vi.mocked(readHold).mockRejectedValue(new TypeError('Failed to fetch'));
    await act(async () => { await result.current.holdTrip(tokyo); });
    expect(result.current.error).toBe(UNKNOWN_HOLD);
  });

  it('does not read back after a refusal the server worded', async () => {
    vi.mocked(processOrder).mockRejectedValueOnce(new ApiError('Not your hold.', 403, true));
    const { result } = await ready();
    await act(async () => { await result.current.holdTrip(tokyo); });
    expect(result.current.error).toBe('Not your hold.');
    expect(readHold).not.toHaveBeenCalled();
  });

  it('reads before it writes again and reuses the saved request identity', async () => {
    vi.mocked(processOrder).mockRejectedValueOnce(tookTooLong())
      .mockResolvedValueOnce({ message: 'Held.', order: held, activities: [] });
    const { result } = await ready();
    await act(async () => { await result.current.holdTrip(tokyo); });
    const identity = vi.mocked(processOrder).mock.calls[0][0].conversation_id;
    vi.mocked(readHold).mockClear();
    await act(async () => { await result.current.holdTrip(tokyo); });
    expect(readHold).toHaveBeenCalledWith(
      expect.objectContaining({ conversationId: identity }), expect.any(AbortSignal));
    expect(vi.mocked(processOrder).mock.calls[1][0].conversation_id).toBe(identity);
    expect(vi.mocked(readHold).mock.invocationCallOrder[0])
      .toBeLessThan(vi.mocked(processOrder).mock.invocationCallOrder[1]);
  });

  it('turns an idle stall into a read-back, not a timeout message', async () => {
    const { result } = await ready();
    vi.mocked(processOrder).mockImplementationOnce((_request, signal) => new Promise((_, reject) => {
      signal?.addEventListener('abort', () => reject(signal.reason));
    }));
    vi.mocked(readHold).mockResolvedValueOnce({ message: 'Hold read from Aurora.', order: held, activities: [] });
    vi.useFakeTimers();
    let pending!: Promise<void>;
    act(() => { pending = result.current.holdTrip(tokyo); });
    await act(async () => { await vi.advanceTimersByTimeAsync(STREAM_IDLE_TIMEOUT_MS); });
    await act(async () => { await pending; });
    expect(result.current.error).toBeNull();
    expect(result.current.tripHolds[0].order.order_id).toBe('HLD-9');
  });
});

describe('a hold the server stopped streaming', () => {
  it('reads the receipt back when the server ends the stream at its ceiling, without a second write', async () => {
    vi.mocked(processOrder).mockRejectedValueOnce(new ReadBackRequiredError('Read it back.'));
    const { result } = await ready();
    vi.mocked(readHold).mockResolvedValueOnce({ message: 'Hold read from Aurora.', order: held, activities: [] });
    await act(async () => { await result.current.holdTrip(tokyo); });
    expect(result.current.error).toBeNull();
    expect(result.current.tripHolds[0].order.order_id).toBe('HLD-9');
    expect(processOrder).toHaveBeenCalledTimes(1);
  });

  it('gives up at the hard ceiling even while keepalives keep arriving', async () => {
    const { result } = await ready();
    vi.mocked(processOrder).mockImplementationOnce((_request, signal, touch) => new Promise((_, reject) => {
      const keepalive = setInterval(() => touch?.(), 10_000);
      signal?.addEventListener('abort', () => { clearInterval(keepalive); reject(signal.reason); });
    }));
    vi.mocked(readHold).mockResolvedValueOnce({ message: 'Hold read from Aurora.', order: held, activities: [] });
    vi.useFakeTimers();
    let pending!: Promise<void>;
    act(() => { pending = result.current.holdTrip(tokyo); });
    await act(async () => { await vi.advanceTimersByTimeAsync(STREAM_HARD_CAP_MS - 1); });
    expect(readHold).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    await act(async () => { await pending; });
    expect(readHold).toHaveBeenCalledTimes(1);
    expect(result.current.tripHolds[0].order.order_id).toBe('HLD-9');
  });
});

describe('a failure the server worded after the write was placed', () => {
  const placed = (write: string) => new ApiError(
    `The ${write} was placed, but saving its audit row failed. Retry to read its receipt.`, 503, true);

  it('shows the words and skips the read-back for a hold', async () => {
    vi.mocked(processOrder).mockRejectedValueOnce(placed('hold'));
    const { result } = await ready();
    await act(async () => { await result.current.holdTrip(tokyo); });
    expect(result.current.error).toBe(placed('hold').message);
    expect(readHold).not.toHaveBeenCalled();
  });

  it('shows the words and skips the read-back for a booking', async () => {
    vi.mocked(processOrder).mockResolvedValue({ message: 'Held.', order: held, activities: [] });
    const { result } = await ready();
    await act(async () => { await result.current.holdTrip(tokyo); });
    vi.mocked(confirmBooking).mockRejectedValueOnce(placed('booking'));
    vi.mocked(readBooking).mockClear();
    await act(async () => { await result.current.confirmTrip(tokyo); });
    expect(result.current.error).toBe(placed('booking').message);
    expect(readBooking).toHaveBeenCalledTimes(1);
  });

  it('does not keep reading a recovery the server answered with a worded failure', async () => {
    vi.mocked(sendChatMessage).mockRejectedValueOnce(placed('hold'));
    const { result } = await ready();
    await act(async () => { await result.current.submitPrompt('Plan a recovery', 5); });
    expect(result.current.awaitingWorkflow).toBe(false);
  });
});

describe('a booking the page stopped waiting for', () => {
  async function holdFirst() {
    vi.mocked(processOrder).mockResolvedValue({ message: 'Held.', order: held, activities: [] });
    const view = await ready();
    await act(async () => { await view.result.current.holdTrip(tokyo); });
    return view;
  }

  it('shows the confirmed booking Aurora holds', async () => {
    const { result } = await holdFirst();
    vi.mocked(confirmBooking).mockRejectedValueOnce(tookTooLong());
    vi.mocked(readBooking).mockResolvedValueOnce({ message: 'Held.', order: held, activities: [] })
      .mockResolvedValueOnce({ message: 'Booking read.', order: confirmed, activities: [] });
    await act(async () => { await result.current.confirmTrip(tokyo); });
    expect(result.current.error).toBeNull();
    expect(result.current.tripHolds[0].order.status).toBe('confirmed');
    expect(result.current.actionDrawer).toMatchObject({ kind: 'book' });
    expect(confirmBooking).toHaveBeenCalledTimes(1);
  });

  it('keeps the old wording while Aurora still shows the hold', async () => {
    const { result } = await holdFirst();
    vi.mocked(confirmBooking).mockRejectedValueOnce(tookTooLong());
    await act(async () => { await result.current.confirmTrip(tokyo); });
    expect(result.current.error).toBe(UNKNOWN_BOOKING);
    expect(result.current.tripHolds[0].order.status).toBe('held');
  });

  it('does not read back when the confirmation was never sent', async () => {
    const { result } = await holdFirst();
    vi.mocked(readBooking).mockClear();
    vi.mocked(readBooking).mockRejectedValueOnce(new TypeError('Failed to fetch'));
    await act(async () => { await result.current.confirmTrip(tokyo); });
    expect(confirmBooking).not.toHaveBeenCalled();
    expect(readBooking).toHaveBeenCalledTimes(1);
  });
});

describe('a recovery the page stopped waiting for', () => {
  it('says it keeps running and asks the page to read its saved progress', async () => {
    vi.mocked(sendChatMessage).mockRejectedValueOnce(tookTooLong());
    const { result } = await ready();
    await act(async () => { await result.current.submitPrompt('Plan a recovery', 5); });
    expect(result.current.awaitingWorkflow).toBe(true);
    expect(result.current.error).toContain('keeps running');
    expect(result.current.error).not.toContain('took too long');
  });

  it('shows the server wording of an already-running resume and keeps reading', async () => {
    vi.mocked(sendChatMessage).mockRejectedValueOnce(new ApiError('This recovery is already running.', 409, true));
    const { result } = await ready();
    await act(async () => { await result.current.submitPrompt('Plan a recovery', 5); });
    expect(result.current.error).toBe('This recovery is already running.');
    expect(result.current.awaitingWorkflow).toBe(true);
  });

  it('does not keep reading after the server refused the request', async () => {
    vi.mocked(sendChatMessage).mockRejectedValueOnce(new ApiError('Bad request.', 422, true));
    const { result } = await ready();
    await act(async () => { await result.current.submitPrompt('Plan a recovery', 5); });
    expect(result.current.awaitingWorkflow).toBe(false);
  });

  it('stops reading after five minutes if the recovery never finishes', async () => {
    vi.mocked(sendChatMessage).mockRejectedValueOnce(tookTooLong());
    const { result } = await ready();
    vi.useFakeTimers();
    await act(async () => { await result.current.submitPrompt('Plan a recovery', 5); });
    expect(result.current.awaitingWorkflow).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(WORKFLOW_READ_BACK_LIMIT_MS - 1); });
    expect(result.current.awaitingWorkflow).toBe(true);
    await act(async () => { await vi.advanceTimersByTimeAsync(1); });
    expect(result.current.awaitingWorkflow).toBe(false);
  });

  it('stops reading once the saved recovery is applied', async () => {
    vi.mocked(sendChatMessage).mockRejectedValueOnce(tookTooLong());
    const { result } = await ready();
    await act(async () => { await result.current.submitPrompt('Plan a recovery', 5); });
    expect(result.current.awaitingWorkflow).toBe(true);
    act(() => result.current.settleWorkflow({ journey_id: 'jrn_1' } as never));
    expect(result.current.awaitingWorkflow).toBe(false);
  });
});
