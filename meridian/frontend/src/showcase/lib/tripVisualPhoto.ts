import type { Product } from '../../types';

export type TripVisualVariant =
  | 'willamette'
  | 'napa'
  | 'mendoza'
  | 'douro'
  | 'alsace'
  | 'vineyard'
  | 'coast'
  | 'city'
  | 'mountain'
  | 'landscape';

// Local photos that genuinely match a variant. city/mountain/landscape have
// no on-disk photo on purpose: when the live image_url is unreachable we let
// the themed CSS gradient stand in rather than force an unrelated stock photo
// (a Tokyo card must never fall back to a Tuscany vineyard).
export const LOCAL_PHOTO: Partial<Record<TripVisualVariant, string>> = {
  willamette: '/travel/vineyard.jpg',
  napa: '/travel/napa.jpg',
  mendoza: '/travel/mendoza.jpg',
  douro: '/travel/douro.jpg',
  alsace: '/travel/alsace.jpg',
  vineyard: '/travel/tuscany-vineyard.jpg',
  coast: '/travel/coast.jpg',
};

// Curated variants use the local photo even when a live image_url exists:
// the Tuscany villa is the hero package in the demo narrative, so we pin its
// look instead of accepting whatever the live catalog URL returns.
const CURATED: ReadonlySet<TripVisualVariant> = new Set(['vineyard']);

// Cache the exact trips used by the five presenter queries. The wider catalog
// can still use its live image_url, while the stage-critical cards stay
// reliable on venue Wi-Fi and retain the same catalog photography.
const SHOWCASE_PRODUCT_PHOTO: Readonly<Record<string, string>> = {
  'ADV-001': '/travel/catalog/ADV-001.jpg',
  'BCH-001': '/travel/catalog/BCH-001.jpg',
  'BCH-003': '/travel/catalog/BCH-003.jpg',
  'BCH-004': '/travel/catalog/BCH-004.jpg',
  'CTY-001': '/travel/catalog/CTY-001.jpg',
  'CTY-002': '/travel/catalog/CTY-002.jpg',
  'CTY-003': '/travel/catalog/CTY-003.jpg',
  'CTY-004': '/travel/catalog/CTY-004.jpg',
  'CTY-005': '/travel/catalog/CTY-005.jpg',
  'TKY-001': '/travel/catalog/TKY-001.jpg',
  'TKY-002': '/travel/catalog/TKY-002.jpg',
  'TKY-003': '/travel/catalog/TKY-003.jpg',
  'TKY-004': '/travel/catalog/TKY-004.jpg',
  'WEL-001': '/travel/catalog/WEL-001.jpg',
  'WEL-002': '/travel/catalog/WEL-002.jpg',
  'WEL-005': '/travel/catalog/WEL-005.jpg',
};

/** Classify a trip into a visual variant from its native travel fields. */
export function tripVisualVariant(product: Product): TripVisualVariant {
  const key = [product.category, product.name, product.brand, product.destination, product.region]
    .filter(Boolean)
    .join(' ')
    .toLowerCase();

  if (/willamette|oregon|pinot/.test(key)) return 'willamette';
  if (/napa|sonoma/.test(key)) return 'napa';
  if (/mendoza|argentina|andes|malbec/.test(key)) return 'mendoza';
  if (/douro|porto|portugal/.test(key)) return 'douro';
  if (/alsace/.test(key)) return 'alsace';
  if (/tuscany|chianti|vineyard|winery|wine country/.test(key)) return 'vineyard';
  if (/beach|coast|amalfi|positano|island|atoll|caldera|santorini|maldives|cancun|bali|hawaii|resort/.test(key))
    return 'coast';
  if (/trek|mountain|alps|glacier|patagonia|everest|rainforest|ring road|outdoor|adventure/.test(key))
    return 'mountain';
  if (/city|tokyo|paris|new york|barcelona|rome|kyoto|dubai|urban|break/.test(key)) return 'city';
  return 'landscape';
}

/**
 * Resolve the photo a card should try first: a curated local photo when the
 * variant is pinned, otherwise the live backend image_url, otherwise a
 * category-matched local photo, otherwise null so the themed gradient shows
 * rather than a mismatched image.
 */
export function tripVisualPhoto(product: Product): { variant: TripVisualVariant; src: string | null } {
  const variant = tripVisualVariant(product);
  const local = LOCAL_PHOTO[variant] ?? null;
  const showcasePhoto = SHOWCASE_PRODUCT_PHOTO[product.product_id];
  if (showcasePhoto) return { variant, src: showcasePhoto };
  if (CURATED.has(variant) && local) return { variant, src: local };

  const liveUrl =
    product.image_url && (/^https?:\/\//.test(product.image_url) || product.image_url.startsWith('/'))
      ? product.image_url
      : null;
  return { variant, src: liveUrl ?? local };
}
