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
    const src = 'https://example.com/photo/tokyo-live.jpg';
    expect(tripVisualPhoto(product({ product_id: 'CITY-LIVE', image_url: src })).src).toBe(src);
  });

  it('uses the commissioned catalog artwork the seed points at', () => {
    // Every package ships its own image under /travel/catalog. Nothing is
    // fetched from a stock photo CDN, so the seeded path is what renders.
    expect(
      tripVisualPhoto(
        product({ product_id: 'WEL-005', image_url: '/travel/catalog/WEL-005.jpg' }),
      ).src,
    ).toBe('/travel/catalog/WEL-005.jpg');
  });

  it('does not let a variant photo shadow a package that has its own artwork', () => {
    // WEL-005 classifies as 'vineyard', which has tuscany-vineyard.jpg on disk.
    // The package's own photograph has to win, or commissioning artwork for it
    // achieves nothing.
    const resolved = tripVisualPhoto(
      product({
        product_id: 'WEL-005',
        name: 'Tuscany Wine & Wellness',
        destination: 'Chianti',
        image_url: '/travel/catalog/WEL-005.jpg',
      }),
    );
    expect(resolved.variant).toBe('vineyard');
    expect(resolved.src).toBe('/travel/catalog/WEL-005.jpg');
  });

  it('never falls a city trip back to the Tuscany vineyard photo', () => {
    // No live URL and no city photo on disk: expect the gradient (null src),
    // not tuscany-vineyard.jpg.
    expect(tripVisualPhoto(product({ product_id: 'CITY-NO-PHOTO', image_url: '' })).src).toBeNull();
  });

  it('routes willamette to the vineyard photo, not tuscany', () => {
    const oregon = product({
      product_id: 'WINE-OREGON',
      name: 'Willamette Pinot Weekend',
      destination: 'Oregon',
      category: 'Wine',
      image_url: '',
    });
    expect(tripVisualPhoto(oregon).src).toBe('/travel/vineyard.jpg');
  });
});
