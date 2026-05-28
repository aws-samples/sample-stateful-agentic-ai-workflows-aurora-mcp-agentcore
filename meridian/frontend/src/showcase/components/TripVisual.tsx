import type { Product } from '../../types';

export function TripVisual({ product, compact = false }: { product: Product; compact?: boolean }) {
  const key = `${product.category} ${product.name} ${product.brand ?? ''}`.toLowerCase();
  const variant = key.includes('willamette') || key.includes('oregon') || key.includes('pinot')
    ? 'willamette'
    : key.includes('napa') || key.includes('california')
      ? 'napa'
      : key.includes('mendoza') || key.includes('argentina') || key.includes('andes') || key.includes('malbec')
        ? 'mendoza'
        : key.includes('douro')
          ? 'douro'
          : key.includes('alsace')
            ? 'alsace'
            : key.includes('beach') || key.includes('azores') || key.includes('coast')
              ? 'coast'
              : 'vineyard';

  const imageByVariant: Record<string, string> = {
    // Sunlit vineyard rows on a hill — matches Willamette Valley pinot country.
    willamette:
      'https://images.unsplash.com/photo-1473448912268-2022ce9509d8?auto=format&fit=crop&w=1400&q=80',
    // Estate vineyard with rolling green California hills.
    napa:
      'https://images.unsplash.com/photo-1507608158173-1dcec673a2e5?auto=format&fit=crop&w=1400&q=80',
    // Snow-capped Andes with vines / mountain backdrop for Mendoza.
    mendoza:
      'https://images.unsplash.com/photo-1467269204594-9661b134dd2b?auto=format&fit=crop&w=1400&q=80',
    vineyard:
      'https://images.unsplash.com/photo-1473448912268-2022ce9509d8?auto=format&fit=crop&w=1400&q=80',
    douro:
      'https://images.unsplash.com/photo-1523906834658-6e24ef2386f9?auto=format&fit=crop&w=1400&q=80',
    alsace:
      'https://images.unsplash.com/photo-1499696010180-025ef6e1a8f9?auto=format&fit=crop&w=1400&q=80',
    coast:
      'https://images.unsplash.com/photo-1469474968028-56623f02e42e?auto=format&fit=crop&w=1400&q=80',
  };
  // Prefer the live image_url returned by the backend if it looks like an HTTPS URL,
  // otherwise fall back to the variant-keyed showcase photo.
  const photoUrl =
    product.image_url && /^https?:\/\//.test(product.image_url)
      ? product.image_url
      : imageByVariant[variant] || imageByVariant.vineyard;

  return (
    <div
      className={`mds-trip-visual mds-trip-visual-${variant}${compact ? ' is-compact' : ''}`}
      aria-hidden="true"
    >
      <img className="mds-trip-photo" src={photoUrl} alt="" loading="lazy" />
      <span className="mds-trip-sun" />
      <span className="mds-trip-ridge one" />
      <span className="mds-trip-ridge two" />
      <span className="mds-trip-field" />
    </div>
  );
}
