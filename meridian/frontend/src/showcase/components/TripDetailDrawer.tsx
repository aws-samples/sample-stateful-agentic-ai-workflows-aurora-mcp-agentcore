import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { TripVisual } from './TripVisual';

export function TripDetailDrawer({ state }: { state: MeridianShowcaseState }) {
  if (!state.actionDrawer) return null;

  const product = state.actionDrawer.product;

  return (
    <aside className="mds-trip-drawer is-open" aria-label="Selected trip details">
      <TripVisual product={product} compact />
      <div className="mds-trip-drawer-copy">
        <span>{product.category}</span>
        <strong>{product.name}</strong>
        <p>{product.description}</p>
        <div className="mds-trip-drawer-grid">
          <div>
            <span>From</span>
            <b>${product.price.toLocaleString('en-US', { maximumFractionDigits: 0 })}</b>
          </div>
          <div>
            <span>Match</span>
            <b>{product.similarity != null ? `${Math.round(product.similarity * 100)}%` : 'catalog'}</b>
          </div>
        </div>
        <div className="mds-trip-confirm">
          <b>{state.actionDrawer.live ? 'Live action' : 'Demo fallback'}</b>
          <span>{state.actionDrawer.message}</span>
          {state.actionDrawer.order && <code>{state.actionDrawer.order.order_id}</code>}
          <button type="button" onClick={state.closeActionDrawer}>
            Done
          </button>
        </div>
      </div>
    </aside>
  );
}
