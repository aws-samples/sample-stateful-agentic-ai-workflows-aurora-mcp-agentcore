import type { TripHold } from '../hooks/useMeridianShowcase';
import { HoldReceipt } from './HoldReceipt';

export function TripHoldReceipt({ hold, compact = false }: { hold: TripHold; compact?: boolean }) {
  return <div className="mc-trip-hold">
    {compact && <strong>{hold.order.items.map(item => item.name).join(', ')}</strong>}
    <HoldReceipt holdId={hold.order.order_id} expiresAt={hold.order.hold_expires_at} status={hold.order.status} kind="direct" compact={compact} />
  </div>;
}
