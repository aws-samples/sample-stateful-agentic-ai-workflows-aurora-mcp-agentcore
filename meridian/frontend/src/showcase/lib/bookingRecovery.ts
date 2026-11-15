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

/** Merges into what storage holds now, so a save never drops another tab's entries. */
export function saveBookingRecovery(travelerId: string, value: BookingRecovery): void {
  // Persist before dispatch. If storage is unavailable, do not start a write
  // whose retry identity would disappear on refresh.
  const stored = loadBookingRecovery(travelerId);
  const merged: BookingRecovery = {
    intents: { ...stored.intents, ...value.intents },
    bookings: { ...stored.bookings, ...value.bookings },
  };
  window.localStorage.setItem(keyFor(travelerId), JSON.stringify(merged));
}

const lockNameFor = (travelerId: string) => `meridian:booking-recovery-lock:v1:${travelerId}`;

async function withTravelerLock<T>(travelerId: string, work: () => T): Promise<T> {
  const locks = typeof navigator === 'undefined' ? undefined : navigator.locks;
  if (!locks?.request) return work();
  return locks.request(lockNameFor(travelerId), async () => work());
}

/**
 * Returns the saved hold intent for `intentKey`, or creates and saves one.
 *
 * Read, allocation and write run under a per-traveler Web Lock, so two tabs asking for the same
 * hold agree on one idempotency ID. A saved intent is reused unless its conversation ID is
 * `staleConversationId` (the expired receipt this click replaces), which another tab may already
 * have replaced. Without `navigator.locks` the same steps run unlocked: the merge in
 * `saveBookingRecovery` still keeps both tabs' entries, but a simultaneous first request can
 * still pick two IDs.
 */
export function allocateHoldIntent(
  travelerId: string,
  intentKey: string,
  create: () => SavedHoldIntent,
  staleConversationId?: string,
): Promise<SavedHoldIntent> {
  return withTravelerLock(travelerId, () => {
    const current = loadBookingRecovery(travelerId).intents[intentKey];
    if (current && current.conversationId !== staleConversationId) return current;
    const intent = create();
    saveBookingRecovery(travelerId, { intents: { [intentKey]: intent }, bookings: {} });
    return intent;
  });
}
