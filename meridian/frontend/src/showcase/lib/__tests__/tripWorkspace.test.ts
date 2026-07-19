import { describe, expect, it } from 'vitest';
import type { Product } from '../../../types';
import { parseTripWorkspace, toggleComparedTrip, toggleSavedTrip } from '../tripWorkspace';

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
});
