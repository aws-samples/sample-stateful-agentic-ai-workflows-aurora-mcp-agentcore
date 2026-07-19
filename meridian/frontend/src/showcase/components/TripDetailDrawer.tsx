import { Bookmark, GitCompareArrows, ShieldCheck, X } from 'lucide-react';
import type { Product } from '../../types';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { useDialogA11y } from '../hooks/useDialogA11y';
import { TripVisual } from './TripVisual';

function duration(product: Product) {
  return product.available_sizes?.[0] ?? 'Flexible duration';
}

export function TripDetailDrawer({ state }: { state: MeridianShowcaseState }) {
  const product = state.selectedTrip;
  const open = state.tripDetailsOpen && Boolean(product);
  const ref = useDialogA11y(open, state.closeTripDetails);
  if (!open || !product) return null;

  const saved = state.savedTripIds.has(product.product_id);
  const compared = state.comparedTrips.some((item) => item.product_id === product.product_id);
  const party = state.travelerProfile?.party_size ?? 1;
  const availability = Object.entries(product.availability ?? {});
  const highlights = product.highlights?.length
    ? product.highlights
    : ['Curated lodging', 'Local experiences', 'Concierge support'];

  return (
    <div className="mds-modal-backdrop" onMouseDown={state.closeTripDetails}>
      <section
        ref={ref}
        className="mds-trip-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="trip-detail-title"
        tabIndex={-1}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <button className="mds-modal-close" type="button" onClick={state.closeTripDetails} aria-label="Close trip details">
          <X size={19} />
        </button>
        <div className="mds-trip-modal-visual">
          <TripVisual product={product} />
          <span>{product.destination || product.region || product.category}</span>
        </div>
        <div className="mds-trip-modal-body">
          <header>
            <span>{product.category}</span>
            <h2 id="trip-detail-title">{product.name}</h2>
            <p>{product.description}</p>
          </header>
          <div className="mds-trip-facts">
            <div><span>Package</span><b>${product.price.toLocaleString()} / traveler</b></div>
            <div><span>Duration</span><b>{duration(product)}</b></div>
            <div><span>Party estimate</span><b>${(product.price * party).toLocaleString()}</b></div>
          </div>
          <section className="mds-trip-section">
            <h3>What is included</h3>
            <ul>{highlights.slice(0, 6).map((item) => <li key={item}>{item}</li>)}</ul>
          </section>
          <section className="mds-trip-section">
            <h3>Current inventory</h3>
            {availability.length ? (
              <div className="mds-availability-list">
                {availability.slice(0, 4).map(([label, count]) => (
                  <span key={label}><b>{label}</b>{count} places</span>
                ))}
              </div>
            ) : <p>Departure inventory is checked when you request a hold.</p>}
          </section>
          <div className="mds-trip-disclosure">
            <ShieldCheck size={17} />
            A courtesy hold reserves catalog inventory for 12 hours. No payment is charged.
          </div>
          {state.actionDrawer?.product.product_id === product.product_id && (
            <div className="mds-hold-receipt" role="status">
              <b>{state.actionDrawer.order?.order_id ?? 'Hold status'}</b>
              <span>{state.actionDrawer.message}</span>
            </div>
          )}
          <footer className="mds-trip-modal-actions">
            <button type="button" onClick={() => state.saveTrip(product)} aria-pressed={saved}>
              <Bookmark size={17} />{saved ? 'Saved' : 'Save trip'}
            </button>
            <button type="button" onClick={() => state.compareTrip(product)} aria-pressed={compared}>
              <GitCompareArrows size={17} />{compared ? 'Comparing' : 'Compare'}
            </button>
            <button className="is-primary" type="button" onClick={() => void state.holdTrip(product)} disabled={state.isLoading}>
              {state.isLoading ? 'Creating hold...' : 'Request 12-hour hold'}
            </button>
          </footer>
        </div>
      </section>
    </div>
  );
}
