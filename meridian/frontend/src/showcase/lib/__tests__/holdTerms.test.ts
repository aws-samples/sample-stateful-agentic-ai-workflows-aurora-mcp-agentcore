import { describe, expect, it } from 'vitest';
import type { Product } from '../../../types';
import { defaultDuration, dollars, hasPlacesFor, holdTerms, placesLabel, termsLabel } from '../holdTerms';

const trip = (overrides: Partial<Product> = {}): Product => ({
  product_id: 'CTY-002', name: 'Tokyo', brand: 'Meridian', category: 'city', description: '', image_url: '',
  price: 2500, available_sizes: ['5 nights', '7 nights'], availability: { '5 nights': 10, '7 nights': 7 },
  ...overrides,
});

describe('the duration the Hold action starts on', () => {
  it('is the first published duration when it has places for the whole party', () => {
    expect(defaultDuration(trip(), 2)).toBe('5 nights');
  });

  it('is the first duration with places for the whole party when the first is sold out', () => {
    expect(defaultDuration(trip({ availability: { '5 nights': 0, '7 nights': 7 } }), 2)).toBe('7 nights');
  });

  it('skips a duration that cannot take the whole party', () => {
    expect(defaultDuration(trip({ availability: { '5 nights': 1, '7 nights': 7 } }), 2)).toBe('7 nights');
  });

  it('stays on the first published duration when none has room, so the refusal names the real reason', () => {
    expect(defaultDuration(trip({ availability: { '5 nights': 0, '7 nights': 1 } }), 2)).toBe('5 nights');
  });

  it('treats a duration missing from the inventory as having no places', () => {
    expect(defaultDuration(trip({ availability: { '7 nights': 7 } }), 2)).toBe('7 nights');
  });

  it('stays on the first published duration when the product carries no inventory', () => {
    expect(defaultDuration(trip({ availability: null }), 2)).toBe('5 nights');
    expect(defaultDuration(trip({ availability: {} }), 2)).toBe('5 nights');
  });

  it('is undefined when no duration is published', () => {
    expect(defaultDuration(trip({ available_sizes: null }), 2)).toBeUndefined();
  });
});

describe('the terms shown on the Hold action', () => {
  it('are in cents, with the total equal to the price times the party', () => {
    expect(holdTerms(trip({ price: 2499.99 }), '7 nights', 3)).toEqual({
      unit_price_cents: 249999, total_cents: 749997, currency: 'USD', size: '7 nights', quantity: 3,
    });
  });

  it('read as dollars and name the duration and the total', () => {
    expect(dollars(250000)).toBe('$2,500');
    expect(termsLabel(holdTerms(trip(), '7 nights', 2))).toBe('7 nights, $5,000 total');
  });

  it('word each duration\'s places', () => {
    const product = trip({ availability: { '5 nights': 1, '7 nights': 0 } });
    expect(placesLabel(product, '5 nights')).toBe('1 place');
    expect(placesLabel(product, '7 nights')).toBe('Sold out');
    expect(placesLabel(trip({ availability: { '5 nights': 3 } }), '7 nights')).toBe('Sold out');
  });

  it('say "Checked at hold" only when the product carries no inventory at all', () => {
    expect(placesLabel(trip({ availability: undefined }), '7 nights')).toBe('Checked at hold');
    expect(placesLabel(trip({ availability: {} }), '7 nights')).toBe('Checked at hold');
  });

  it('agree with hasPlacesFor for every duration, listed or not', () => {
    const inventories: Array<Record<string, number> | undefined> = [undefined, {}, { '5 nights': 3 }, { '5 nights': 0, '7 nights': 2 }];
    for (const availability of inventories) {
      const product = trip({ availability });
      for (const duration of ['5 nights', '7 nights', '9 nights']) {
        const soldOut = placesLabel(product, duration) === 'Sold out';
        expect(soldOut).toBe(!hasPlacesFor(product, duration, 1));
      }
    }
  });
});
