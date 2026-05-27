import type { Product } from '../../types';

export function TripVisual({ product, compact = false }: { product: Product; compact?: boolean }) {
  const key = `${product.category} ${product.name}`.toLowerCase();
  const variant = key.includes('douro')
    ? 'douro'
    : key.includes('alsace')
      ? 'alsace'
      : key.includes('beach') || key.includes('azores')
        ? 'coast'
        : 'vineyard';

  return (
    <div className={`mds-trip-visual mds-trip-visual-${variant}${compact ? ' is-compact' : ''}`} aria-hidden="true">
      <span className="mds-trip-sun" />
      <span className="mds-trip-ridge one" />
      <span className="mds-trip-ridge two" />
      <span className="mds-trip-field" />
    </div>
  );
}
