import { describe, expect, it } from 'vitest';
import type { Product } from '../../../types';
import { tripVisualPhoto, tripVisualVariant } from '../tripVisualPhoto';

function product(overrides: Partial<Product> = {}): Product {
  return {
    product_id: 'CTY-002',
    name: 'Tokyo Culture & Cuisine',
    brand: 'Tokyo + Asia-Pacific',
    price: 2499,
    description: 'A live package.',
    image_url: '',
    category: 'City Breaks',
    destination: 'Tokyo',
    region: 'Asia-Pacific',
    ...overrides,
  };
}

describe('tripVisualVariant', () => {
  it('classifies city trips as city, not vineyard', () => {
    expect(tripVisualVariant(product())).toBe('city');
    expect(tripVisualVariant(product({ name: 'Paris Long Weekend', destination: 'Paris' }))).toBe(
      'city',
    );
  });

  it('classifies coast, mountain, and wine trips distinctly', () => {
    expect(tripVisualVariant(product({ name: 'Amalfi Coast Villa Week', destination: 'Positano' }))).toBe(
      'coast',
    );
    expect(
      tripVisualVariant(product({ name: 'Patagonia Trek Expedition', category: 'Adventure & Outdoors' })),
    ).toBe('mountain');
    expect(tripVisualVariant(product({ name: 'Tuscany Wine & Wellness', destination: 'Chianti' }))).toBe(
      'vineyard',
    );
  });
});

describe('tripVisualPhoto', () => {
  it('prefers the live image_url for non-curated variants', () => {
    const src = 'https://images.unsplash.com/photo-1540959733332';
    expect(tripVisualPhoto(product({ image_url: src })).src).toBe(src);
  });

  it('never falls a city trip back to the Tuscany vineyard photo', () => {
    // No live URL and no city photo on disk: expect the gradient (null src),
    // not tuscany-vineyard.jpg.
    expect(tripVisualPhoto(product({ image_url: '' })).src).toBeNull();
  });

  it('pins the curated Tuscany hero photo over any live URL', () => {
    const tuscany = product({
      name: 'Tuscany Wine & Wellness',
      destination: 'Chianti',
      category: 'Wellness & Luxury',
      image_url: 'https://images.unsplash.com/photo-1523531294919',
    });
    expect(tripVisualPhoto(tuscany).src).toBe('/travel/tuscany-vineyard.jpg');
  });

  it('routes willamette to the vineyard photo, not tuscany', () => {
    const oregon = product({
      name: 'Willamette Pinot Weekend',
      destination: 'Oregon',
      category: 'Wine',
      image_url: '',
    });
    expect(tripVisualPhoto(oregon).src).toBe('/travel/vineyard.jpg');
  });
});
