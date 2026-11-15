import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import {
  allocateHoldIntent, holdIntentKey, loadBookingRecovery, saveBookingRecovery,
  type SavedHoldIntent,
} from '../bookingRecovery';

const TRAVELER = 'trv_1';
const KEY = holdIntentKey('prod_1', 'standard', 2);
const make = (id: string): SavedHoldIntent =>
  ({ conversationId: id, productId: 'prod_1', duration: 'standard', quantity: 2 });

/** Minimal Web Locks stand-in: one holder per name, FIFO, shared by every "tab". */
function installLocks(): void {
  const tails = new Map<string, Promise<unknown>>();
  const request = (name: string, cb: () => Promise<unknown>) => {
    const run = (tails.get(name) ?? Promise.resolve()).then(cb);
    tails.set(name, run.catch(() => undefined));
    return run;
  };
  Object.defineProperty(navigator, 'locks', { value: { request }, configurable: true });
}

function removeLocks(): void {
  Object.defineProperty(navigator, 'locks', { value: undefined, configurable: true });
}

describe('allocateHoldIntent across tabs sharing one storage', () => {
  beforeEach(() => window.localStorage.clear());
  afterEach(removeLocks);

  it('gives both tabs one idempotency id when they ask at the same time (locks)', async () => {
    installLocks();
    const [a, b] = await Promise.all([
      allocateHoldIntent(TRAVELER, KEY, () => make('conv_hold_a')),
      allocateHoldIntent(TRAVELER, KEY, () => make('conv_hold_b')),
    ]);
    expect(a.conversationId).toBe(b.conversationId);
    expect(loadBookingRecovery(TRAVELER).intents[KEY].conversationId).toBe(a.conversationId);
  });

  it('keeps both tabs entries without locks, when different holds interleave', async () => {
    removeLocks();
    const otherKey = holdIntentKey('prod_2', 'standard', 2);
    const other = { ...make('conv_hold_c'), productId: 'prod_2' };
    await Promise.all([
      allocateHoldIntent(TRAVELER, KEY, () => make('conv_hold_a')),
      allocateHoldIntent(TRAVELER, otherKey, () => other),
    ]);
    expect(Object.keys(loadBookingRecovery(TRAVELER).intents).sort()).toEqual([KEY, otherKey].sort());
  });

  it('lets a stale tab adopt the replacement another tab already saved', async () => {
    installLocks();
    saveBookingRecovery(TRAVELER, { intents: { [KEY]: make('conv_hold_old') }, bookings: {} });
    const first = await allocateHoldIntent(TRAVELER, KEY, () => make('conv_hold_new1'), 'conv_hold_old');
    const second = await allocateHoldIntent(TRAVELER, KEY, () => make('conv_hold_new2'), 'conv_hold_old');
    expect(first.conversationId).toBe('conv_hold_new1');
    expect(second.conversationId).toBe('conv_hold_new1');
  });

  it('reuses a saved intent and never creates another', async () => {
    saveBookingRecovery(TRAVELER, { intents: { [KEY]: make('conv_hold_saved') }, bookings: {} });
    const intent = await allocateHoldIntent(TRAVELER, KEY, () => make('conv_hold_other'));
    expect(intent.conversationId).toBe('conv_hold_saved');
  });

  it('rejects when storage cannot be written', async () => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = () => { throw new Error('quota'); };
    try {
      await expect(allocateHoldIntent(TRAVELER, KEY, () => make('conv_hold_a'))).rejects.toThrow('quota');
    } finally {
      Storage.prototype.setItem = original;
    }
  });
});

describe('saveBookingRecovery merges', () => {
  beforeEach(() => window.localStorage.clear());

  it('keeps entries another tab saved after this tab loaded', () => {
    const stale = loadBookingRecovery(TRAVELER);
    saveBookingRecovery(TRAVELER, { intents: { [KEY]: make('conv_hold_a') }, bookings: { prod_1: 'ord_1' } });
    stale.bookings.prod_2 = 'ord_2';
    saveBookingRecovery(TRAVELER, stale);
    const saved = loadBookingRecovery(TRAVELER);
    expect(saved.intents[KEY].conversationId).toBe('conv_hold_a');
    expect(saved.bookings).toEqual({ prod_1: 'ord_1', prod_2: 'ord_2' });
  });
});
