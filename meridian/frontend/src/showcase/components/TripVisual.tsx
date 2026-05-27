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

  const imageByVariant: Record<string, string> = {
    vineyard:
      'https://images.unsplash.com/photo-1473448912268-2022ce9509d8?auto=format&fit=crop&w=1400&q=80',
    douro:
      'https://images.unsplash.com/photo-1507608158173-1dcec673a2e5?auto=format&fit=crop&w=1400&q=80',
    alsace:
      'https://images.unsplash.com/photo-1467269204594-9661b134dd2b?auto=format&fit=crop&w=1400&q=80',
    coast:
      'https://images.unsplash.com/photo-1469474968028-56623f02e42e?auto=format&fit=crop&w=1400&q=80',
  };
  // Keep showcase visuals consistently photoreal for stage demos.
  const photoUrl = imageByVariant[variant] || imageByVariant.vineyard;

  return (
    <div className={`mds-trip-visual mds-trip-visual-${variant}${compact ? ' is-compact' : ''}`} aria-hidden="true">
      <img className="mds-trip-photo" src={photoUrl} alt="" loading="lazy" />
      <span className="mds-trip-sun" />
      <span className="mds-trip-ridge one" />
      <span className="mds-trip-ridge two" />
      <span className="mds-trip-field" />
    </div>
  );
}
