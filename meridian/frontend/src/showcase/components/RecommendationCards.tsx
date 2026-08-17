import type { Product } from '../../types';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { resultRankLabel } from '../lib/resultRankLabel';
import { TripResultCardContent } from './TripResultCardContent';

export function RecommendationCards({
  state,
  compact = false,
  limit = 3,
}: {
  state: MeridianShowcaseState;
  compact?: boolean;
  limit?: number;
}) {
  const cards = state.recommendations.slice(0, limit);

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
      aria-label={product.name}
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
