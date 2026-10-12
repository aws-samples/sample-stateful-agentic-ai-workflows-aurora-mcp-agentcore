import type { HoldLookup } from '../../api/client';

export type SavedHoldIntent = HoldLookup & { replacesBookingId?: string };
export interface BookingRecovery {
  intents: Record<string, SavedHoldIntent>;
  bookings: Record<string, string>;
}

const keyFor = (travelerId: string) => `meridian:booking-recovery:v1:${travelerId}`;
export const holdIntentKey = (productId: string, duration: string, quantity: number) =>
  JSON.stringify([productId, duration, quantity]);

/** Only identifiers/terms are cached. Displayed receipts are always read from Aurora. */
export function loadBookingRecovery(travelerId: string): BookingRecovery {
  const raw = window.localStorage.getItem(keyFor(travelerId));
  if (!raw) return { intents: {}, bookings: {} };
  const value = JSON.parse(raw) as BookingRecovery;
  if (!value || typeof value.intents !== 'object' || !value.intents || typeof value.bookings !== 'object' || !value.bookings) {
    throw new Error('Saved booking references could not be read. Recover the booking in System evidence before starting another hold.');
  }
  for (const [key, intent] of Object.entries(value.intents)) {
    if (!intent || typeof intent.conversationId !== 'string' || !intent.conversationId
      || typeof intent.productId !== 'string' || typeof intent.duration !== 'string'
      || !Number.isInteger(intent.quantity) || intent.quantity < 1 || intent.quantity > 12
      || key !== holdIntentKey(intent.productId, intent.duration, intent.quantity)) {
      throw new Error('Saved hold identity is invalid. Inspect the saved journey before making another request.');
    }
  }
  if (Object.values(value.bookings).some(id => typeof id !== 'string' || !id)) throw new Error('Saved booking identity is invalid.');
  return value;
}

export function saveBookingRecovery(travelerId: string, value: BookingRecovery): void {
  // Persist before dispatch. If storage is unavailable, do not start a write
  // whose retry identity would disappear on refresh.
  window.localStorage.setItem(keyFor(travelerId), JSON.stringify(value));
}
