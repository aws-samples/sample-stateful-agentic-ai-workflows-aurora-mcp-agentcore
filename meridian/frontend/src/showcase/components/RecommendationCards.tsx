import type { Product } from '../../types';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { resultRankLabel } from '../lib/resultRankLabel';
import { TripResultCardContent } from './TripResultCardContent';

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
          index={index}
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
  index,
  compact,
  priority,
}: {
  product: Product;
  state: MeridianShowcaseState;
  index: number;
  compact: boolean;
  priority: boolean;
}) {
  const selected = state.selectedTrip?.product_id === product.product_id;

  return (
    <article
      className={`mds-trip-result-card${selected ? ' is-selected' : ''}${priority ? ' is-priority' : ''}`}
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
      <TripResultCardContent
        product={product}
        state={state}
        matchPct={null}
        matchLabel={resultRankLabel(state.selectedPhase, index)}
        compact={compact}
        featured={priority}
      />
    </article>
  );
}
