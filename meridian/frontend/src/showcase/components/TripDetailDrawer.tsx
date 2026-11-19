import { useEffect, useRef, useState, type KeyboardEvent } from 'react';
import { Bookmark, GitCompareArrows, ShieldCheck, X } from 'lucide-react';
import type { Product } from '../../types';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { useDialogA11y } from '../hooks/useDialogA11y';
import { TripVisual } from './TripVisual';
import { TripHoldReceipt } from './TripHoldReceipt';
import { BookingConfirmation } from './BookingConfirmation';
import { useHoldClock } from '../hooks/useHoldClock';
import { firstNameOf } from '../lib/travelerIdentity';
import {
  defaultDuration, dollars, holdTerms, placesLabel, publishedDurations, termsLabel,
} from '../lib/holdTerms';

const NO_DURATION = 'Flexible duration';

/**
 * The duration the traveler picked for this trip and party, or the default. A pick for another
 * trip or party size, or for a duration the trip no longer lists, is ignored.
 */
function useDurationChoice(product: Product | null, party: number) {
  const [picked, setPicked] = useState<{ key: string; duration: string } | null>(null);
  const key = `${product?.product_id}|${party}`;
  const durations = product ? publishedDurations(product) : [];
  const valid = picked !== null && picked.key === key && durations.includes(picked.duration);
  const chosen = valid ? picked.duration : product ? defaultDuration(product, party) : undefined;
  return { chosen, durations, choose: (duration: string) => setPicked({ key, duration }) };
}

/** Arrow keys, Home and End move the choice through the options, wrapping at the ends. */
function nextOption(key: string, index: number, count: number): number | null {
  if (key === 'ArrowRight' || key === 'ArrowDown') return (index + 1) % count;
  if (key === 'ArrowLeft' || key === 'ArrowUp') return (index - 1 + count) % count;
  if (key === 'Home') return 0;
  if (key === 'End') return count - 1;
  return null;
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
  const party = state.travelersCount;
  const { chosen, durations, choose } = useDurationChoice(product ?? null, party);
  const optionRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const reviewed = state.holdReview;
  const review = product && reviewed?.productId === product.product_id
    && reviewed.quote.size === chosen && reviewed.quote.quantity === party ? reviewed : null;
  // A review belongs to one duration and party size; changing either drops it.
  useEffect(() => {
    if (reviewed && !review) state.clearHoldReview();
  }, [reviewed, review, state]);
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
  const catalogCents = Math.round(product.price * 100);
  const terms = review?.quote ?? (chosen ? holdTerms(product, chosen, party) : undefined);
  const shownParty = receiptItem?.quantity ?? party;
  const unitPrice = receiptItem
    ? `$${receiptItem.unit_price.toLocaleString()}` : dollars(terms?.unit_price_cents ?? catalogCents);
  const tripDuration = receiptItem?.size ?? chosen ?? NO_DURATION;
  const total = receiptItem && hold
    ? `$${hold.order.total.toLocaleString()}` : dollars(terms?.total_cents ?? catalogCents * party);
  const onOptionKey = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    const target = nextOption(event.key, index, durations.length);
    if (target === null) return;
    event.preventDefault();
    choose(durations[target]);
    optionRefs.current[target]?.focus();
  };
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
              budgetUnclear={state.budgetUnclear}
              busy={state.isLoading}
              onConfirm={() => void state.confirmTrip(product)}
              onCancel={state.dismissBookingConfirmation}
            />
          )}
          <div className="mds-trip-facts">
            <div><span>Package</span><b>{unitPrice} / traveler</b></div>
            <div><span>Duration</span><b>{tripDuration}</b></div>
            <div><span>{receiptItem ? 'Recorded total' : 'Total'} for {shownParty} {shownParty === 1 ? 'traveler' : 'travelers'}</span><b>{total}</b></div>
          </div>
          {!receiptItem && durations.length > 1 && (
            <section className="mds-trip-section">
              <h3 id="trip-length-title">Trip length</h3>
              <div className="mds-duration-options" role="radiogroup" aria-labelledby="trip-length-title">
                {durations.map((option, index) => (
                  <button
                    key={option}
                    ref={(node) => { optionRefs.current[index] = node; }}
                    className="mds-duration-option"
                    type="button"
                    role="radio"
                    aria-checked={option === chosen}
                    tabIndex={option === chosen ? 0 : -1}
                    onClick={() => choose(option)}
                    onKeyDown={(event) => onOptionKey(event, index)}
                  >
                    <b>{option}</b>{' '}<span>{placesLabel(product, option)}</span>
                  </button>
                ))}
              </div>
            </section>
          )}
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
          {review && (
            <div className="mds-hold-review" role="alert">
              <b>{review.message}</b>
              <span>
                Now {dollars(review.quote.unit_price_cents)} per traveler, {dollars(review.quote.total_cents)} total
                for {review.quote.quantity} {review.quote.quantity === 1 ? 'traveler' : 'travelers'}.
                {review.code === 'price_changed' && catalogCents !== review.quote.unit_price_cents
                  && ` Before: ${dollars(catalogCents)} per traveler.`}
              </span>
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
                className="is-primary mds-hold-action"
                type="button"
                onClick={() => activeHold ? state.requestBookingConfirmation(product) : void state.holdTrip(product, terms)}
                disabled={state.isLoading || confirmed}
              >
                {state.pendingWrite === 'confirm' ? 'Confirming…'
                  : state.pendingWrite === 'hold' ? 'Creating hold…'
                  : confirmed ? 'Trip confirmed' : activeHold ? confirmLabel : (
                    <>
                      <span>{review ? 'Approve the new price and request hold' : 'Request 12-hour hold'}</span>
                      {terms && <small>{termsLabel(terms)}</small>}
                    </>
                  )}
              </button>
            )}
          </footer>
        </div>
      </section>
    </div>
  );
}
