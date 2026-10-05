import type { Product } from '../../types';

const STORAGE_KEY = 'meridian.trip-workspace.v1';
const MAX_COMPARE_TRIPS = 3;

export interface TripWorkspace {
  savedTrips: Product[];
  compareTrips: Product[];
}

export const EMPTY_TRIP_WORKSPACE: TripWorkspace = {
  savedTrips: [],
  compareTrips: [],
};

function isProduct(value: unknown): value is Product {
  if (!value || typeof value !== 'object') return false;
  const item = value as Partial<Product>;
  return (
    typeof item.product_id === 'string' &&
    typeof item.name === 'string' &&
    typeof item.price === 'number'
  );
}

function uniqueProducts(products: Product[]): Product[] {
  const byId = new Map<string, Product>();
  products.forEach((product) => byId.set(product.product_id, product));
  return [...byId.values()];
}

export function parseTripWorkspace(raw: string | null): TripWorkspace {
  if (!raw) return EMPTY_TRIP_WORKSPACE;
  try {
    const parsed = JSON.parse(raw) as Partial<TripWorkspace>;
    const savedTrips = Array.isArray(parsed.savedTrips)
      ? uniqueProducts(parsed.savedTrips.filter(isProduct))
      : [];
    const compareTrips = Array.isArray(parsed.compareTrips)
      ? uniqueProducts(parsed.compareTrips.filter(isProduct)).slice(0, MAX_COMPARE_TRIPS)
      : [];
    return { savedTrips, compareTrips };
  } catch {
    return EMPTY_TRIP_WORKSPACE;
  }
}

/** Saved and compared trips are a convenience; blocked storage must not stop the app. */
export function loadTripWorkspace(): TripWorkspace {
  if (typeof window === 'undefined') return EMPTY_TRIP_WORKSPACE;
  try {
    return parseTripWorkspace(window.localStorage.getItem(STORAGE_KEY));
  } catch {
    return EMPTY_TRIP_WORKSPACE;
  }
}

export function saveTripWorkspace(workspace: TripWorkspace): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(workspace));
  } catch {
    // Blocked or full storage keeps the workspace for this page only.
  }
}

export function toggleSavedTrip(savedTrips: Product[], product: Product): Product[] {
  const exists = savedTrips.some((item) => item.product_id === product.product_id);
  if (exists) return savedTrips.filter((item) => item.product_id !== product.product_id);
  return [product, ...savedTrips];
}

export function toggleComparedTrip(compareTrips: Product[], product: Product): Product[] {
  const exists = compareTrips.some((item) => item.product_id === product.product_id);
  if (exists) return compareTrips.filter((item) => item.product_id !== product.product_id);
  return [...compareTrips, product].slice(-MAX_COMPARE_TRIPS);
}
