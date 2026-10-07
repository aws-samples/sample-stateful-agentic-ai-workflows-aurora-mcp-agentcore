import { useEffect, useRef } from 'react';
import { Bookmark, GitCompareArrows, ShieldCheck, X } from 'lucide-react';
import type { Product } from '../../types';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { useDialogA11y } from '../hooks/useDialogA11y';
import { TripVisual } from './TripVisual';
import { TripHoldReceipt } from './TripHoldReceipt';
import { BookingConfirmation } from './BookingConfirmation';
import { useHoldClock } from '../hooks/useHoldClock';
import { firstNameOf } from '../lib/travelerIdentity';

function duration(product: Product) {
  return product.available_sizes?.[0] ?? 'Flexible duration';
}

export function TripDetailDrawer({ state }: { state: MeridianShowcaseState }) {
  const product = state.selectedTrip;
  const open = state.tripDetailsOpen && Boolean(product);
  const ref = useDialogA11y(open, state.closeTripDetails);
  const hold = state.tripHolds?.find(item => item.productId === product?.product_id);
  const { expired, knownExpiry } = useHoldClock(hold?.order.hold_expires_at);
  const activeHold = hold?.order.status === 'held' && knownExpiry && !expired;
  const confirmed = hold?.order.status === 'confirmed';
  const confirming = Boolean(hold) && state.bookingPrompt?.order.order_id === hold?.order.order_id;
  const stopRef = useRef<HTMLButtonElement>(null);
  const firstName = firstNameOf(state.traveler);
  const confirmLabel = firstName ? `Confirm this trip for ${firstName}` : 'Confirm this trip';
  // Confirming removes the focused button, and a finished write removes Stop
  // waiting. Keep keyboard focus in the dialog instead of dropping it on the page.
  useEffect(() => {
    const dialog = ref.current;
    if (!open || !dialog || dialog.contains(document.activeElement)) return;
    (stopRef.current ?? dialog).focus();
  }, [open, ref, state.pendingWrite]);
  if (!open || !product) return null;

  const saved = state.savedTripIds.has(product.product_id);
  const compared = state.comparedTrips.some((item) => item.product_id === product.product_id);
  const receiptItem = activeHold || confirmed
    ? hold?.order.items.find(item => item.product_id === product.product_id)
    : undefined;
  const party = receiptItem?.quantity ?? state.travelersCount;
  const unitPrice = receiptItem?.unit_price ?? product.price;
  const tripDuration = receiptItem?.size ?? duration(product);
  const total = receiptItem && hold ? hold.order.total : product.price * party;
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
        <div className="mds-trip-modal-visual" data-theme="dark">
          <TripVisual product={product} />
          <span>{product.destination || product.region || product.category}</span>
        </div>
        <div className="mds-trip-modal-body">
          <header>
            <span>{product.category}</span>
            <h2 id="trip-detail-title">{product.name}</h2>
            <p>{product.description}</p>
          </header>
          {hold && !confirming && <TripHoldReceipt hold={hold} />}
          {hold && confirming && (
            <BookingConfirmation
              product={product}
              traveler={state.traveler}
              hold={hold}
              budgetPerTravelerCents={state.budgetCeilingPerTravelerCents}
              busy={state.isLoading}
              onConfirm={() => void state.confirmTrip(product)}
              onCancel={state.dismissBookingConfirmation}
            />
          )}
          <div className="mds-trip-facts">
            <div><span>Package</span><b>${unitPrice.toLocaleString()} / traveler</b></div>
            <div><span>Duration</span><b>{tripDuration}</b></div>
            <div><span>{receiptItem ? 'Recorded total' : 'Estimate'} for {party} {party === 1 ? 'traveler' : 'travelers'}</span><b>${total.toLocaleString()}</b></div>
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
            {confirmed
              ? 'This trip is confirmed in Meridian’s database. No supplier was contacted and no payment was taken.'
              : activeHold
                ? 'Confirming books the held catalog inventory in Meridian’s database. No payment is charged.'
                : 'A courtesy hold reserves catalog inventory for 12 hours. No payment is charged.'}
          </div>
          {!hold && state.actionDrawer?.product.product_id === product.product_id && (
            <div className="mds-hold-receipt" role="status">
              <b>{state.actionDrawer.order?.order_id ?? 'Hold status'}</b>
              <span>{state.actionDrawer.message}</span>
            </div>
          )}
          {state.error && <div className="mds-error-banner" role="alert">{state.error}</div>}
          <footer className="mds-trip-modal-actions">
            <button type="button" onClick={() => state.saveTrip(product)} aria-pressed={saved}>
              <Bookmark size={17} />{saved ? 'Saved' : 'Save trip'}
            </button>
            <button type="button" onClick={() => state.compareTrip(product)} aria-pressed={compared}>
              <GitCompareArrows size={17} />{compared ? 'Comparing' : 'Compare'}
            </button>
            {state.pendingWrite && (
              <button
                ref={stopRef}
                className="mds-trip-stop"
                type="button"
                onClick={state.stopWaiting}
                title="Stop waiting for this response. Saved actions may still finish."
              >
                Stop waiting
              </button>
            )}
            {!confirming && (
              <button
                className="is-primary"
                type="button"
                onClick={() => activeHold ? state.requestBookingConfirmation(product) : void state.holdTrip(product)}
                disabled={state.isLoading || confirmed}
              >
                {state.pendingWrite === 'confirm' ? 'Confirming…'
                  : state.pendingWrite === 'hold' ? 'Creating hold…'
                  : confirmed ? 'Trip confirmed' : activeHold ? confirmLabel : 'Request 12-hour hold'}
              </button>
            )}
          </footer>
        </div>
      </section>
    </div>
  );
}
