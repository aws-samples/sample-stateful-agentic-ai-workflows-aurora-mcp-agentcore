import type { Product } from '../../types';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { TripVisual } from './TripVisual';

function money(price: number): string {
  return `$${price.toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
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
  const cards = state.recommendations.slice(0, limit ?? (compact ? 2 : 3));

  if (!cards.length) {
    return <div className="mds-empty">No recommendations yet.</div>;
  }

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
      <div className="mds-rec-copy">
        <div className="mds-rec-kicker">
          {product.similarity != null ? `${Math.round(product.similarity * 100)}% match` : 'Catalog'}
        </div>
        <strong>{product.name}</strong>
        <span>{product.brand}</span>
        <div className="mds-rec-price">{money(product.price)}</div>
      </div>
      {!compact && (
        <div className="mds-rec-actions" onClick={(event) => event.stopPropagation()}>
          <button type="button" onClick={() => state.holdTrip(product)} disabled={state.isLoading}>
            Hold
          </button>
          <button type="button" onClick={() => state.planTrip(product)} disabled={state.isLoading}>
            Plan trip
          </button>
          <button type="button" onClick={() => state.saveTrip(product)} aria-pressed={saved}>
            {saved ? 'Saved' : 'Save'}
          </button>
          <button type="button" onClick={() => state.compareTrip(product)}>
            Compare
          </button>
        </div>
      )}
    </article>
  );
}
