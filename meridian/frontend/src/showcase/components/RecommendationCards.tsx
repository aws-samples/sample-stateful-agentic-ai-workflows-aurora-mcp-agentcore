import type { Product } from '../../types';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { TripVisual } from './TripVisual';

function money(price: number): string {
  return `$${price.toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
}

function dateRangeFor(product: Product): string {
  // Deterministic hash of the product_id so demos stay stable across renders.
  const seed = Array.from(product.product_id).reduce(
    (acc, ch) => (acc * 31 + ch.charCodeAt(0)) >>> 0,
    7,
  );
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const monthIndex = seed % months.length;
  const startDay = (seed % 18) + 5;
  const durationStr = product.available_sizes?.[0] ?? '7 nights';
  const nights = Number((durationStr.match(/\d+/) ?? ['7'])[0]);
  const endDay = Math.min(startDay + nights, 28);
  return `${months[monthIndex]} ${startDay} – ${endDay}`;
}

export function RecommendationCards({
  state,
  compact = false,
  limit,
}: {
  state: MeridianShowcaseState;
  compact?: boolean;
  limit?: number;
}) {
  // Show every recommendation Aurora returned. Limiting to 3 used to cause
  // a desync between the chat reply ("I found 4 trips for you") and the UI,
  // because the 4th card was silently dropped. The grid wraps when the
  // backend returns more than fit on one row, so this stays responsive.
  const cards = limit != null ? state.recommendations.slice(0, limit) : state.recommendations;

  // True clean slate: render nothing when there are no recommendations.
  // The chat surface above speaks for itself; no extra empty-state copy.
  if (!cards.length) return null;

  return (
    <div className={`mds-recommendations${compact ? ' is-compact' : ''}`}>
      {cards.map((product, index) => (
        <RecommendationCard
          key={product.product_id}
          product={product}
          state={state}
          compact={compact}
          priority={index === 0}
        />
      ))}
    </div>
  );
}

function RecommendationCard({
  product,
  state,
  compact,
  priority,
}: {
  product: Product;
  state: MeridianShowcaseState;
  compact: boolean;
  priority: boolean;
}) {
  const selected = state.selectedTrip?.product_id === product.product_id;
  const saved = state.savedTripIds.has(product.product_id);
  const matchPct = product.similarity != null ? Math.round(product.similarity * 100) : null;
  const dateRange = dateRangeFor(product);

  return (
    <article
      className={`mds-rec-card${selected ? ' is-selected' : ''}${priority ? ' is-priority' : ''}`}
      tabIndex={0}
      role="button"
      aria-label={`Open ${product.name}`}
      onClick={() => state.selectTrip(product)}
      onKeyDown={(event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          state.selectTrip(product);
        }
      }}
    >
      <TripVisual product={product} compact={compact} />
      <div className="mds-rec-fade" aria-hidden="true" />
      <div className="mds-rec-overlay">
        {matchPct != null && (
          <div className="mds-rec-match-badge">
            <span className="mds-rec-match-dot" aria-hidden="true" />
            {matchPct}% match
          </div>
        )}
        <div className="mds-rec-overlay-meta">
          <span className="mds-rec-date">{dateRange}</span>
        </div>
        <strong className="mds-rec-title">{product.name}</strong>
        <span className="mds-rec-sub">{product.brand}</span>
        <div className="mds-rec-overlay-row">
          <div className="mds-rec-price">
            <span>From</span>
            <b>{money(product.price)}</b>
          </div>
          {!compact && (
            <div
              className="mds-rec-actions"
              onClick={(event) => event.stopPropagation()}
            >
              <button
                type="button"
                onClick={() => state.holdTrip(product)}
                disabled={state.isLoading}
              >
                Hold
              </button>
              <button
                type="button"
                onClick={() => state.planTrip(product)}
                disabled={state.isLoading}
              >
                Plan
              </button>
              <button
                type="button"
                onClick={() => state.saveTrip(product)}
                aria-pressed={saved}
              >
                {saved ? 'Saved' : 'Save'}
              </button>
              <button type="button" onClick={() => state.compareTrip(product)}>
                Compare
              </button>
            </div>
          )}
        </div>
      </div>
    </article>
  );
}
