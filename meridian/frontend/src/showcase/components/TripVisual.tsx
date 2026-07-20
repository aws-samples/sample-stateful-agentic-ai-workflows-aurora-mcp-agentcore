import type { Product } from '../../types';
import { LOCAL_PHOTO, tripVisualPhoto } from '../lib/tripVisualPhoto';

export function TripVisual({ product, compact = false }: { product: Product; compact?: boolean }) {
  const { variant, src } = tripVisualPhoto(product);
  const localPhoto = LOCAL_PHOTO[variant] ?? null;

  return (
    <div
      className={`mds-trip-visual mds-trip-visual-${variant}${compact ? ' is-compact' : ''}`}
      aria-hidden="true"
    >
      {src && (
        <img
          className="mds-trip-photo"
          src={src}
          alt=""
          loading="lazy"
          onError={(event) => {
            // Try the local category photo once; if there is none (or it also
            // fails), hide the image so the themed gradient shows through
            // instead of an unrelated fallback photo.
            const img = event.currentTarget;
            if (localPhoto && img.src !== new URL(localPhoto, window.location.origin).href) {
              img.src = localPhoto;
            } else {
              img.style.display = 'none';
            }
          }}
        />
      )}
      <span className="mds-trip-sun" />
      <span className="mds-trip-ridge one" />
      <span className="mds-trip-ridge two" />
      <span className="mds-trip-field" />
    </div>
  );
}
