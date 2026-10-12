import { describe, expect, it } from 'vitest';
import type { Product } from '../../../types';
import { tripCardPresentation } from '../tripCardPresentation';

function product(overrides: Partial<Product> = {}): Product {
  return {
    product_id: 'TKY-001',
    name: 'Tokyo Indie Neighborhood Walk',
    brand: 'JAL Tours',
    price: 1599,
    description: 'A live package.',
    image_url: '/travel/tokyo.jpg',
    category: 'City Breaks',
    destination: 'Tokyo',
    region: 'Asia-Pacific',
    available_sizes: ['4 nights', '6 nights'],
    availability: { '4 nights': 9, '6 nights': 6 },
    highlights: ['rail pass', 'self-guided'],
    ...overrides,
  };
}

describe('tripCardPresentation', () => {
  it('uses real package fields for the booking-card facts', () => {
    expect(tripCardPresentation(product())).toEqual({
      destination: 'Tokyo',
      region: 'Asia-Pacific',
      duration: '4 nights or 6 nights',
      availability: '15 spots open',
      availabilityLow: false,
      highlights: ['rail pass', 'self-guided'],
    });
  });

  it('keeps layout height stable when a package has no highlights', () => {
    expect(tripCardPresentation(product({ highlights: [] })).highlights).toEqual([
      'City Breaks',
    ]);
  });

  it('calls out genuinely low live inventory', () => {
    const facts = tripCardPresentation(
      product({ availability: { '6 nights': 2, '8 nights': 1 } }),
    );
    expect(facts.availability).toBe('3 spots open');
    expect(facts.availabilityLow).toBe(true);
  });
});
