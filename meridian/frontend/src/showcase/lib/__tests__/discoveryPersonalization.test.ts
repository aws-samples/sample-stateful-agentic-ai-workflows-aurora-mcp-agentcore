import { describe, expect, it } from 'vitest';
import type { LongTermMemoryFact, Product, TravelerProfile } from '../../../types';
import { derivePersonalization } from '../discoveryPersonalization';

/** The real rows `/api/memory/trv_meridian_demo` returns for Alex. */
const FACTS: LongTermMemoryFact[] = [
  { key: 'shellfish_allergy', value: 'Exclude shellfish', source: 'support_ticket', confidence: 1 },
  { key: 'home_airport', value: 'JFK', source: 'profile', confidence: 1 },
  {
    key: 'recent_trips',
    value: 'Tuscany (Feb 2026), Kyoto (held)',
    source: 'booking_history',
    confidence: 0.99,
  },
  { key: 'lodging_style', value: 'boutique > chain', source: 'search_analytics', confidence: 0.96 },
  {
    key: 'tokyo_culture',
    value: 'Tokyo culture trip Oct 12-19',
    source: 'profile',
    confidence: 0.95,
  },
];

const PROFILE: TravelerProfile = {
  full_name: 'Alex Morgan',
  home_airport: 'JFK',
  party_size: 2,
  budget_min: '2000.00' as unknown as number,
  budget_max: '3500.00' as unknown as number,
  dietary_notes: 'Shellfish allergy — exclude seafood dining',
};

function trip(overrides: Partial<Product> = {}): Product {
  return {
    product_id: 'TST-001',
    name: 'Test Trip',
    brand: 'Test Operator',
    price: 2500,
    description: 'A trip.',
    image_url: '',
    category: 'City Breaks',
    destination: 'Lisbon',
    region: 'Europe',
    available_sizes: ['5 nights'],
    availability: { '5 nights': 4 },
    highlights: [],
    ...overrides,
  } as Product;
}

const labels = (pills: ReturnType<typeof derivePersonalization>) =>
  pills.map((p) => p.label);

describe('derivePersonalization', () => {
  it('returns nothing before traveler context is authorized', () => {
    // The showcase starts disconnected on purpose; inventing pills here would
    // undercut the Phase 4 governance beat.
    expect(derivePersonalization(trip(), null, [])).toEqual([]);
  });

  it('returns nothing without a trip', () => {
    expect(derivePersonalization(undefined, PROFILE, FACTS)).toEqual([]);
  });

  it('flags a seafood highlight against the stored allergy', () => {
    const pills = derivePersonalization(
      trip({ destination: 'Tokyo', highlights: ['kaiseki dinner'] }),
      PROFILE,
      FACTS,
    );
    const dietary = pills.find((p) => p.id === 'dietary');
    expect(dietary?.tone).toBe('caution');
    expect(dietary?.label).toContain('kaiseki');
    expect(dietary?.source).toBe('support ticket');
  });

  it('keeps the allergy as context when the trip has no seafood', () => {
    const pills = derivePersonalization(trip(), PROFILE, FACTS);
    const dietary = pills.find((p) => p.id === 'dietary');
    expect(dietary?.tone).toBe('context');
    expect(dietary?.label).toBe('Shellfish allergy on file');
  });

  describe('budget', () => {
    const budgetPill = (price: number) =>
      derivePersonalization(trip({ price }), PROFILE, FACTS).find(
        (p) => p.id === 'budget',
      );

    it('cautions only when the trip exceeds the maximum', () => {
      expect(budgetPill(3999)).toMatchObject({
        label: 'Above your saved budget',
        tone: 'caution',
      });
    });

    it('does not call a cheaper trip "above" budget', () => {
      // $1,899 is below the $2,000 floor, not above the $3,500 ceiling.
      const pill = budgetPill(1899);
      expect(pill?.label).not.toContain('Above');
      expect(pill).toMatchObject({ label: 'Under your usual spend', tone: 'match' });
    });

    it('matches inside the range', () => {
      expect(budgetPill(2500)).toMatchObject({
        label: 'Within your saved budget',
        tone: 'match',
      });
    });
  });

  it('matches the lodging preference against what the trip offers', () => {
    const pills = derivePersonalization(
      trip({ description: 'Cliffside villa, private boat day.' }),
      PROFILE,
      FACTS,
    );
    const lodging = pills.find((p) => p.id === 'lodging');
    expect(lodging).toMatchObject({ tone: 'match', source: 'search analytics' });
    expect(lodging?.label).toContain('villa');
  });

  it('surfaces the saved plan when the destination lines up', () => {
    const pills = derivePersonalization(
      trip({ destination: 'Tokyo' }),
      PROFILE,
      FACTS,
    );
    expect(labels(pills).some((l) => l.startsWith('Matches your saved plan'))).toBe(true);
  });

  it('recognises a destination the traveler has been to before', () => {
    const pills = derivePersonalization(
      trip({ destination: 'Tuscany' }),
      PROFILE,
      FACTS,
    );
    expect(pills.find((p) => p.id === 'recent')).toMatchObject({
      tone: 'match',
      source: 'booking history',
    });
  });

  it('orders cautions first, then matches, then context', () => {
    const pills = derivePersonalization(
      trip({ destination: 'Tokyo', price: 3999, highlights: ['kaiseki dinner'] }),
      PROFILE,
      FACTS,
    );
    const tones = pills.map((p) => p.tone);
    expect(tones).toEqual([...tones].sort((a, b) => {
      const w = { caution: 0, match: 1, context: 2 } as const;
      return w[a] - w[b];
    }));
    expect(tones[0]).toBe('caution');
  });

  it('caps the pill count so the strip stays one row', () => {
    const pills = derivePersonalization(
      trip({ destination: 'Tokyo', highlights: ['kaiseki dinner'] }),
      PROFILE,
      FACTS,
    );
    expect(pills.length).toBeLessThanOrEqual(4);
  });

  it('cites the Aurora source for every pill', () => {
    const pills = derivePersonalization(trip(), PROFILE, FACTS);
    expect(pills.length).toBeGreaterThan(0);
    for (const pill of pills) {
      expect(pill.source).toBeTruthy();
      expect(pill.source).not.toContain('_');
    }
  });
});
