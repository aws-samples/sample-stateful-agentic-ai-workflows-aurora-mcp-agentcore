import { ShieldCheck } from 'lucide-react';
import type { Product } from '../../types';
import type { TripHold } from '../hooks/useMeridianShowcase';
import { BudgetCeiling } from './BudgetCeiling';

/** The traveler's confirmation, restated in full before the platform carries it to the gateway. */
export function BookingConfirmation({ product, hold, budgetPerTravelerCents, busy, onConfirm, onCancel }: {
  product: Product;
  hold: TripHold;
  /** The saved per-traveler cap; the policy judges this total against it times the party. */
  budgetPerTravelerCents?: number | null;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const line = hold.order.items[0];
  const quantity = line?.quantity ?? 1;
  return (
    <section className="mds-trip-confirm" aria-labelledby="trip-confirm-title">
      <header>
        <ShieldCheck size={18} aria-hidden="true" />
        <h3 id="trip-confirm-title">Confirm this trip for Alex?</h3>
      </header>
      <dl>
        <div><dt>Package</dt><dd>{product.name}</dd></div>
        <div><dt>Duration</dt><dd>{line?.size ?? 'As held'}</dd></div>
        <div><dt>Travelers</dt><dd>{quantity}</dd></div>
        <div><dt>Total</dt><dd>${hold.order.total.toLocaleString('en-US')}</dd></div>
        <div><dt>Budget ceiling</dt><dd><BudgetCeiling perTravelerCents={budgetPerTravelerCents} travelers={quantity} /></dd></div>
        <div><dt>Hold</dt><dd>{hold.order.order_id}</dd></div>
      </dl>
      <p>
        Meridian books catalog inventory in its own database. No supplier is contacted and no payment is taken.
        AgentCore Gateway checks the Cedar booking policy against this total and the saved budget, then Aurora marks the booking confirmed.
      </p>
      <footer className="mds-trip-modal-actions">
        <button type="button" onClick={onCancel} disabled={busy}>Not yet</button>
        <button type="button" className="is-primary" onClick={onConfirm} disabled={busy}>
          {busy ? 'Confirming...' : 'Yes, confirm this trip'}
        </button>
      </footer>
    </section>
  );
}
