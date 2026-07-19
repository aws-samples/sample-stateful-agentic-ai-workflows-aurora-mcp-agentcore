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
    willamette: '/travel/tuscany-vineyard.jpg',
    napa: '/travel/napa.jpg',
    mendoza: '/travel/mendoza.jpg',
    vineyard: '/travel/tuscany-vineyard.jpg',
    douro: '/travel/tuscany-vineyard.jpg',
    alsace: '/travel/alsace.jpg',
    coast: '/travel/coast.jpg',
  };
  // Prefer the live image_url returned by the backend if it looks like an HTTPS URL,
  // otherwise fall back to the variant-keyed showcase photo.
  const curatedOverride =
    key.includes('tuscany') || key.includes('chianti')
      ? '/travel/tuscany-vineyard.jpg'
      : null;
  const photoUrl =
    curatedOverride ??
    (product.image_url && (/^https?:\/\//.test(product.image_url) || product.image_url.startsWith('/'))
      ? product.image_url
      : imageByVariant[variant] || imageByVariant.vineyard);

  return (
    <div
      className={`mds-trip-visual mds-trip-visual-${variant}${compact ? ' is-compact' : ''}`}
      aria-hidden="true"
    >
      <img
        className="mds-trip-photo"
        src={photoUrl}
        alt=""
        loading="lazy"
        onError={(event) => {
          event.currentTarget.src = imageByVariant[variant] || imageByVariant.vineyard;
        }}
      />
      <span className="mds-trip-sun" />
      <span className="mds-trip-ridge one" />
      <span className="mds-trip-ridge two" />
      <span className="mds-trip-field" />
    </div>
  );
}
