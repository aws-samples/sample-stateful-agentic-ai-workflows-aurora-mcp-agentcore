import type { ReactNode } from 'react';
import {
  ArrowRight,
  Bookmark,
  CalendarDays,
  CheckCircle2,
  GitCompareArrows,
  MapPin,
} from 'lucide-react';
import type { Product } from '../../types';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { tripCardPresentation } from '../lib/tripCardPresentation';
import { TripVisual } from './TripVisual';

function money(price: number): string {
  return `$${price.toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
}

export function TripResultCardContent({
  product,
  state,
  matchPct,
  matchExtra,
  compact = false,
}: {
  product: Product;
  state: MeridianShowcaseState;
  matchPct: number | null;
  matchExtra?: ReactNode;
  compact?: boolean;
}) {
  const facts = tripCardPresentation(product);
  const saved = state.savedTripIds.has(product.product_id);

  return (
    <>
      <div className="mds-trip-result-media" aria-hidden="true">
        <TripVisual product={product} compact />
        <span className="mds-trip-result-media-shade" />
        <span className="mds-trip-result-destination">
          <MapPin size={12} />
          {facts.destination}
        </span>
        <span className="mds-trip-result-match">
          <i aria-hidden="true" />
          {matchPct != null ? `${matchPct}% match` : 'Live catalog'}
          {matchExtra}
        </span>
      </div>

      <div className="mds-trip-result-body">
        <div className="mds-trip-result-heading">
          <span>{facts.region}</span>
          <strong>{product.name}</strong>
          <small>{product.brand}</small>
        </div>

        <div className="mds-trip-result-facts">
          <span>
            <CalendarDays size={14} aria-hidden="true" />
            <b>Stay</b>
            {facts.duration}
          </span>
          <span className={facts.availabilityLow ? 'is-low' : ''}>
            <CheckCircle2 size={14} aria-hidden="true" />
            <b>Live inventory</b>
            {facts.availability}
          </span>
        </div>

        {facts.highlights.length > 0 && (
          <div className="mds-trip-result-highlights" aria-label="Package highlights">
            {facts.highlights.map((highlight) => (
              <span key={highlight}>{highlight}</span>
            ))}
          </div>
        )}

        <div className="mds-trip-result-footer">
          <div className="mds-trip-result-price">
            <span>From</span>
            <b>{money(product.price)}</b>
            <small>per traveler</small>
          </div>
          {!compact && (
            <div
              className="mds-trip-result-actions"
              onClick={(event) => event.stopPropagation()}
            >
              <button
                type="button"
                className="is-details"
                onClick={() => state.openTripDetails(product)}
              >
                Details
                <ArrowRight size={14} aria-hidden="true" />
              </button>
              <button
                type="button"
                className="is-icon"
                onClick={() => state.compareTrip(product)}
                aria-label={`Compare ${product.name}`}
                title="Add to comparison"
              >
                <GitCompareArrows size={14} aria-hidden="true" />
              </button>
              <button
                type="button"
                className="is-icon"
                onClick={() => state.saveTrip(product)}
                aria-label={saved ? `Remove ${product.name} from saved trips` : `Save ${product.name}`}
                aria-pressed={saved}
                title={saved ? 'Saved' : 'Save trip'}
              >
                <Bookmark size={15} fill={saved ? 'currentColor' : 'none'} aria-hidden="true" />
              </button>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
