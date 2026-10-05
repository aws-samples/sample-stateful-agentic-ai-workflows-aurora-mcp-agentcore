import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Product } from '../../../types';
import {
  loadTripWorkspace,
  parseTripWorkspace,
  saveTripWorkspace,
  toggleComparedTrip,
  toggleSavedTrip,
} from '../tripWorkspace';

const product = (id: string): Product => ({
  product_id: id,
  name: `Trip ${id}`,
  brand: 'Meridian',
  price: 1200,
  description: 'A trip',
  image_url: '',
  category: 'City Breaks',
});

describe('trip workspace persistence', () => {
  it('rejects malformed storage and deduplicates products', () => {
    expect(parseTripWorkspace('{bad')).toEqual({ savedTrips: [], compareTrips: [] });
    const parsed = parseTripWorkspace(JSON.stringify({
      savedTrips: [product('a'), product('a')],
      compareTrips: [product('a'), product('b')],
    }));
    expect(parsed.savedTrips).toHaveLength(1);
    expect(parsed.compareTrips).toHaveLength(2);
  });

  it('toggles saved trips and caps comparison at three', () => {
    expect(toggleSavedTrip([], product('a'))).toHaveLength(1);
    expect(toggleSavedTrip([product('a')], product('a'))).toHaveLength(0);
    expect(toggleComparedTrip(
      [product('a'), product('b'), product('c')],
      product('d'),
    ).map((item) => item.product_id)).toEqual(['b', 'c', 'd']);
  });

  describe('with browser storage blocked', () => {
    afterEach(() => vi.restoreAllMocks());

    it('starts with an empty workspace and keeps working without saving', () => {
      const blocked = new DOMException('The operation is insecure.', 'SecurityError');
      // Spy on the method owner, including the fallback storage some Node versions use.
      const owner = (name: 'getItem' | 'setItem') => (
        Object.prototype.hasOwnProperty.call(window.localStorage, name)
          ? window.localStorage : Object.getPrototypeOf(window.localStorage)
      );
      vi.spyOn(owner('getItem'), 'getItem').mockImplementation(() => { throw blocked; });
      vi.spyOn(owner('setItem'), 'setItem').mockImplementation(() => { throw blocked; });
      expect(loadTripWorkspace()).toEqual({ savedTrips: [], compareTrips: [] });
      const workspace = { savedTrips: [product('a')], compareTrips: [] };
      expect(() => saveTripWorkspace(workspace)).not.toThrow();
    });
  });
});
