import type { HoldQuote, Product } from '../../types';

/** The durations the package publishes, in published order. */
export function publishedDurations(product: Product): string[] {
  return product.available_sizes ?? [];
}

/**
 * Whether `duration` can take the whole party. When the product carries no inventory at all the
 * answer is yes: Aurora checks the places when the hold is requested.
 */
export function hasPlacesFor(product: Product, duration: string, party: number): boolean {
  const inventory = product.availability;
  if (!inventory || Object.keys(inventory).length === 0) return true;
  return (inventory[duration] ?? 0) >= party;
}

/**
 * The duration the Hold action starts on: the first published duration when it has places for
 * the whole party, otherwise the first duration that does. When none does, the first published
 * duration, so the request is refused for the real reason.
 */
export function defaultDuration(product: Product, party: number): string | undefined {
  const durations = publishedDurations(product);
  return durations.find(duration => hasPlacesFor(product, duration, party)) ?? durations[0];
}

/** The terms the Hold action shows for `duration` and `party`, in cents. */
export function holdTerms(product: Product, duration: string, party: number): HoldQuote {
  const unit = Math.round(product.price * 100);
  return {
    unit_price_cents: unit, total_cents: unit * party, currency: 'USD', size: duration, quantity: party,
  };
}

/** Dollars for a cent amount, grouped like the rest of the drawer ($2,499 or $2,499.50). */
export function dollars(cents: number): string {
  return `$${(cents / 100).toLocaleString()}`;
}

/** What the Hold action says about its terms, for example "7 nights, $5,000 total". */
export function termsLabel(terms: HoldQuote): string {
  return `${terms.size}, ${dollars(terms.total_cents)} total`;
}

/** The places a duration has left, worded for its option in the duration picker. */
export function placesLabel(product: Product, duration: string): string {
  const inventory = product.availability;
  if (!inventory || Object.keys(inventory).length === 0) return 'Checked at hold';
  const places = inventory[duration] ?? 0;
  if (places <= 0) return 'Sold out';
  return `${places} ${places === 1 ? 'place' : 'places'}`;
}
