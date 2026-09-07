import { useEffect, useState } from 'react';
import { AuroraIcon } from './ServiceMark';

function timestamp(value?: string | null): number {
  return value ? Date.parse(value.replace(' ', 'T').replace(/([+-]\d{2})$/, '$1:00')) : NaN;
}

function utcTime(value?: string | null): string {
  const time = timestamp(value);
  return Number.isFinite(time) ? new Date(time).toISOString().replace('T', ' ').replace(/\.\d{3}Z$/, ' UTC') : 'Not recorded';
}

/** The persisted booking is the receipt; the countdown only visualizes its TTL. */
export function HoldReceipt({
  holdId, createdAt, expiresAt, observedAt, receivedAt, status,
}: {
  holdId: string;
  createdAt?: string | null;
  expiresAt?: string | null;
  observedAt?: string | null;
  status?: string;
  /** Client time captured when this database response arrived, not on mount. */
  receivedAt?: number;
}) {
  const [now, setNow] = useState(Date.now);
  const serverTime = timestamp(observedAt);
  const hasServerClock = Number.isFinite(serverTime) && receivedAt != null;
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);
  const clockNow = hasServerClock ? serverTime + Math.max(0, now - receivedAt) : now;
  const expiry = timestamp(expiresAt);
  const remaining = Math.max(0, expiry - clockNow);
  const windowMinutes = (expiry - timestamp(createdAt)) / 60000;
  const knownWindow = Number.isFinite(windowMinutes) && windowMinutes > 0;
  const expired = Number.isFinite(expiry) && remaining === 0;
  const held = status === 'held';
  const clock = `${Math.floor(remaining / 60000)}:${String(Math.floor((remaining % 60000) / 1000)).padStart(2, '0')}`;

  return (
    <section className={`mc-hold-receipt${expired || !held ? ' is-inactive' : ''}`} aria-label="Aurora hold receipt">
      <header>
        <strong><AuroraIcon size={20} aria-hidden="true" />
          {knownWindow ? `${Number(windowMinutes.toFixed(2))}-minute package hold` : 'Package hold receipt'}
        </strong>
        <span role="timer" aria-live="off">
          {status && !held ? `Status: ${status}` : expired ? 'Expired' : held && Number.isFinite(expiry) ? `${clock} remaining` : 'Status not verified'}
        </span>
      </header>
      <dl>
        <div><dt>Created in Aurora</dt><dd>{utcTime(createdAt)}</dd></div>
        <div><dt>Expires in Aurora</dt><dd>{utcTime(expiresAt)}</dd></div>
      </dl>
      <p>{held && expired ? 'This hold no longer counts against package capacity.' : 'Package inventory only. Flight seats are not reserved.'}</p>
      <details>
        <summary>How to verify this hold</summary>
        <p>The window is expiry minus creation time, read from <code>bookings</code>. A retry uses the same request and original expiry; it does not restart the clock.</p>
        <p>At expiry, inventory queries exclude this hold. The booking row remains as evidence.</p>
        <p>Booking <code>{holdId}</code></p>
        <p>{hasServerClock ? `Database read: ${utcTime(observedAt)}. Countdown estimated from that read.` : 'Countdown uses this device’s clock. Re-read System evidence for current database state.'}</p>
      </details>
    </section>
  );
}
