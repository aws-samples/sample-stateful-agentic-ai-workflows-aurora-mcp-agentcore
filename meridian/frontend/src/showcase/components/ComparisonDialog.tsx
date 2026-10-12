import { Bookmark, Check, X } from 'lucide-react';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { useDialogA11y } from '../hooks/useDialogA11y';
import { TripVisual } from './TripVisual';

export function ComparisonDialog({ state }: { state: MeridianShowcaseState }) {
  const ref = useDialogA11y(state.comparisonOpen, state.closeComparison);
  if (!state.comparisonOpen) return null;
  return (
    <div className="mds-modal-backdrop" onMouseDown={state.closeComparison}>
      <section ref={ref} className="mds-compare-modal" role="dialog" aria-modal="true" aria-labelledby="compare-title" tabIndex={-1} onMouseDown={(e) => e.stopPropagation()}>
        <header>
          <div><span>Trip workspace</span><h2 id="compare-title">Compare your shortlist</h2></div>
          <button type="button" onClick={state.closeComparison} aria-label="Close comparison"><X size={19} /></button>
        </header>
        {state.comparedTrips.length === 0 ? (
          <div className="mds-compare-empty">Add up to three recommendations to compare price, duration, inventory, and fit.</div>
        ) : (
          <div className="mds-compare-grid">
            {state.comparedTrips.map((product) => (
              <article key={product.product_id}>
                <TripVisual product={product} compact />
                <button className="mds-compare-remove" type="button" onClick={() => state.removeComparedTrip(product.product_id)} aria-label={`Remove ${product.name}`}><X size={15} /></button>
                <div className="mds-compare-copy">
                  <span>{product.destination || product.category}</span>
                  <h3>{product.name}</h3>
                  <dl>
                    <div><dt>Price</dt><dd>${product.price.toLocaleString()} pp</dd></div>
                    <div><dt>Duration</dt><dd>{product.available_sizes?.[0] ?? 'Flexible'}</dd></div>
                    <div><dt>Match</dt><dd>{product.similarity ? `${Math.round(product.similarity * 100)}%` : 'Catalog'}</dd></div>
                  </dl>
                  <ul>{(product.highlights ?? ['Curated lodging', 'Local experiences']).slice(0, 3).map((item) => <li key={item}><Check size={13} />{item}</li>)}</ul>
                  <div>
                    <button type="button" onClick={() => state.saveTrip(product)}><Bookmark size={15} />{state.savedTripIds.has(product.product_id) ? 'Saved' : 'Save'}</button>
                    <button type="button" className="is-primary" onClick={() => { state.closeComparison(); state.openTripDetails(product); }}>View trip</button>
                  </div>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
