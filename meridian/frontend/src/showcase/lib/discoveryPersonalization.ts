import type { LongTermMemoryFact, Product, TravelerProfile } from '../../types';

/**
 * Why a stored fact is being shown against the trip on screen.
 *
 * - `match`   the trip satisfies something Aurora knows about this traveler
 * - `caution` the trip conflicts with something on file, and should be flagged
 * - `context` the fact applies to the traveler generally, not to this trip
 */
export type PersonalizationTone = 'match' | 'caution' | 'context';

export interface PersonalizationPill {
  /** Stable key so React can animate the set without remounting every pill. */
  id: string;
  label: string;
  tone: PersonalizationTone;
  /** Where Aurora got this from - the fact's own `source` column. */
  source: string;
}

const SEAFOOD_MARKERS = [
  'kaiseki',
  'seafood',
  'sushi',
  'oyster',
  'shellfish',
  'crab',
  'lobster',
  'tsukiji',
];

const BOUTIQUE_MARKERS = [
  'boutique',
  'villa',
  'ryokan',
  'guesthouse',
  'townhouse',
  'riad',
];

function haystack(product: Product): string {
  return [
    product.name,
    product.description,
    product.category,
    product.destination,
    product.region,
    product.brand,
    ...(product.highlights ?? []),
  ]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();
}

function mentions(text: string, markers: string[]): string | null {
  return markers.find((marker) => text.includes(marker)) ?? null;
}

function factsByKey(
  facts: LongTermMemoryFact[],
): Map<string, LongTermMemoryFact> {
  return new Map(facts.map((fact) => [fact.key, fact]));
}

function sourceLabel(fact: LongTermMemoryFact | undefined): string {
  const raw = fact?.source?.trim();
  if (!raw) return 'Aurora';
  return raw.replace(/_/g, ' ');
}

function numeric(value: unknown): number | null {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * Derive the personalization pills for one trip.
 *
 * Every pill is backed by a row Aurora actually returned - there is no filler.
 * When the traveler profile has not loaded, the result is empty and the caller
 * should say so rather than invent context.
 */
export function derivePersonalization(
  product: Product | undefined,
  profile: TravelerProfile | null,
  facts: LongTermMemoryFact[],
  limit = 4,
): PersonalizationPill[] {
  if (!product || (!profile && facts.length === 0)) return [];

  const text = haystack(product);
  const byKey = factsByKey(facts);
  const pills: PersonalizationPill[] = [];

  // 1. Active plan. The strongest signal when the trip matches the stored goal.
  const goalFact = byKey.get('tokyo_culture');
  const goal = profile?.trip_goal ?? goalFact?.value;
  if (goal) {
    const destination = (product.destination ?? '').toLowerCase();
    const goalMatchesTrip =
      destination.length > 0 && goal.toLowerCase().includes(destination);
    if (goalMatchesTrip) {
      pills.push({
        id: 'goal',
        label: `Matches your saved plan · ${goal}`,
        tone: 'match',
        source: sourceLabel(goalFact),
      });
    }
  }

  // 2. Dietary. A conflict is worth more attention than a clean match.
  const dietaryFact = byKey.get('shellfish_allergy');
  const dietary = profile?.dietary_notes ?? dietaryFact?.value;
  if (dietary) {
    const clash = mentions(text, SEAFOOD_MARKERS);
    pills.push(
      clash
        ? {
            id: 'dietary',
            label: `Shellfish allergy · check the ${clash}`,
            tone: 'caution',
            source: sourceLabel(dietaryFact),
          }
        : {
            id: 'dietary',
            label: 'Shellfish allergy on file',
            tone: 'context',
            source: sourceLabel(dietaryFact),
          },
    );
  }

  // 3. Lodging style, matched against what this trip actually offers.
  const lodgingFact = byKey.get('lodging_style');
  if (lodgingFact) {
    const marker = mentions(text, BOUTIQUE_MARKERS);
    if (marker) {
      pills.push({
        id: 'lodging',
        label: `Boutique over chain · ${marker} stay`,
        tone: 'match',
        source: sourceLabel(lodgingFact),
      });
    }
  }

  // 4. Budget, compared against the stored range.
  const min = numeric(profile?.budget_min);
  const max = numeric(profile?.budget_max);
  if (max !== null && Number.isFinite(product.price)) {
    const withinBudget = product.price <= max && (min === null || product.price >= min);
    pills.push(
      withinBudget
        ? {
            id: 'budget',
            label: `Within your saved budget`,
            tone: 'match',
            source: 'profile',
          }
        : {
            id: 'budget',
            label: `Above your saved budget`,
            tone: 'caution',
            source: 'profile',
          },
    );
  }

  // 5. Previously travelled, from booking history.
  const recentFact = byKey.get('recent_trips');
  if (recentFact && product.destination) {
    const destination = product.destination.toLowerCase();
    if (recentFact.value.toLowerCase().includes(destination)) {
      pills.push({
        id: 'recent',
        label: `You have travelled ${product.destination} before`,
        tone: 'match',
        source: sourceLabel(recentFact),
      });
    }
  }

  // 6. Loyalty, only when the operator lines up with a stored programme.
  const loyaltyFact = byKey.get('loyalty_programs');
  if (loyaltyFact && product.brand) {
    const brand = product.brand.toLowerCase();
    const matched = loyaltyFact.value
      .split(';')
      .map((entry) => entry.trim())
      .find((entry) => {
        const [operator] = entry.split(' ');
        return operator ? brand.includes(operator.toLowerCase()) : false;
      });
    if (matched) {
      pills.push({
        id: 'loyalty',
        label: `${matched} applies here`,
        tone: 'match',
        source: sourceLabel(loyaltyFact),
      });
    }
  }

  // 7. Departure, as general context when there is still room.
  const airportFact = byKey.get('home_airport');
  const airport = profile?.home_airport ?? airportFact?.value;
  if (airport) {
    pills.push({
      id: 'airport',
      label: `Departs ${airport}`,
      tone: 'context',
      source: sourceLabel(airportFact),
    });
  }

  // Matches and cautions earn their place ahead of general context.
  const weight: Record<PersonalizationTone, number> = {
    caution: 0,
    match: 1,
    context: 2,
  };
  return pills
    .sort((a, b) => weight[a.tone] - weight[b.tone])
    .slice(0, limit);
}
