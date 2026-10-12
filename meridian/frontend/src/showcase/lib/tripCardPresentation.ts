import type { Product } from '../../types';

export interface TripCardPresentation {
  destination: string;
  region: string;
  duration: string;
  availability: string;
  availabilityLow: boolean;
  highlights: string[];
}

function durationLabel(durations: string[]): string {
  if (!durations.length) return 'Flexible duration';
  if (durations.length === 1) return durations[0];
  if (durations.length === 2) return `${durations[0]} or ${durations[1]}`;
  return `${durations[0]} to ${durations[durations.length - 1]}`;
}

function availabilityLabel(
  availability: Product['availability'],
): { label: string; low: boolean } {
  if (!availability) return { label: 'Check live dates', low: false };
  const values = Object.values(availability)
    .map((value) => Number(value))
    .filter((value) => Number.isFinite(value) && value >= 0);
  if (!values.length) return { label: 'Check live dates', low: false };
  const total = values.reduce((sum, value) => sum + value, 0);
  if (total === 0) return { label: 'Waitlist only', low: true };
  return {
    label: `${total} ${total === 1 ? 'spot' : 'spots'} open`,
    low: total <= 5,
  };
}

export function tripCardPresentation(product: Product): TripCardPresentation {
  const inventory = availabilityLabel(product.availability);
  const highlights = (product.highlights ?? []).filter(Boolean).slice(0, 2);
  return {
    destination: product.destination || product.region || product.category,
    region:
      product.region && product.region !== product.destination
        ? product.region
        : product.category,
    duration: durationLabel(product.available_sizes ?? []),
    availability: inventory.label,
    availabilityLow: inventory.low,
    highlights: highlights.length ? highlights : [product.category],
  };
}
