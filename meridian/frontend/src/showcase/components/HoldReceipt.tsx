import { AuroraIcon } from './ServiceMark';
import { parseDatabaseTime as timestamp } from '../journey/evidence';
import { useHoldClock } from '../hooks/useHoldClock';

function utcTime(value?: string | null): string {
  const time = timestamp(value);
  return Number.isFinite(time) ? new Date(time).toISOString().replace('T', ' ').replace(/\.\d{3}Z$/, ' UTC') : 'Not recorded';
}

/** The persisted booking is the receipt; the countdown only visualizes its TTL. */
export function HoldReceipt({
  holdId, createdAt, expiresAt, observedAt, receivedAt, status, kind = 'workflow', compact = false,
}: {
  holdId: string;
  createdAt?: string | null;
  expiresAt?: string | null;
  observedAt?: string | null;
  status?: string;
  /** Client time captured when this database response arrived, not on mount. */
  receivedAt?: number;
  kind?: 'workflow' | 'direct';
  compact?: boolean;
}) {
  const { remaining, expired, knownExpiry, hasServerClock } = useHoldClock(expiresAt, observedAt, receivedAt);
  const windowMinutes = (timestamp(expiresAt) - timestamp(createdAt)) / 60000;
  const knownWindow = Number.isFinite(windowMinutes) && windowMinutes > 0;
  const held = status === 'held';
  const showHours = kind === 'direct' || windowMinutes >= 60 || remaining >= 3600000;
  const seconds = Math.ceil(remaining / 1000);
  const minutes = Math.floor(seconds / 60);
  const clock = `${showHours ? `${String(Math.floor(minutes / 60)).padStart(2, '0')}:` : ''}${showHours ? String(minutes % 60).padStart(2, '0') : minutes}:${String(seconds % 60).padStart(2, '0')}`;
  const windowLabel = knownWindow
    ? windowMinutes % 60 === 0 ? `${windowMinutes / 60}-hour` : `${Number(windowMinutes.toFixed(2))}-minute`
    : kind === 'direct' ? '12-hour' : null;
  const dates = <dl>
    {createdAt && <div><dt>Created in Aurora</dt><dd>{utcTime(createdAt)}</dd></div>}
    <div><dt>Expires in Aurora</dt><dd>{utcTime(expiresAt)}</dd></div>
  </dl>;

  return (
    <section className={`mc-hold-receipt${expired || !held ? ' is-inactive' : ''}${compact ? ' is-compact' : ''}`} aria-label="Aurora hold receipt">
      <header>
        <strong><AuroraIcon size={20} aria-hidden="true" />
          {windowLabel ? `${windowLabel} package hold` : 'Package hold receipt'}
        </strong>
        <span role="timer" aria-live="off">
          {status && !held ? `Status: ${status}` : expired ? 'Expired' : held && knownExpiry ? <><span>{clock}</span> <small>remaining</small></> : 'Status not verified'}
        </span>
      </header>
      {!compact && dates}
      <p>{held && expired ? 'This hold no longer counts against package capacity.' : 'Package inventory only. Flight seats are not reserved.'}</p>
      <details>
        <summary>{compact ? 'Expiry and receipt' : 'How to verify this hold'}</summary>
        {compact && dates}
        <p>{kind === 'direct' ? 'The hold service returns the saved expiry. This clock continues when you close the receipt or switch views.' : <>The window is expiry minus creation time, read from <code>bookings</code>. A retry uses the same request and original expiry; it does not restart the clock.</>}</p>
        <p>At expiry, inventory queries exclude this hold. The booking row remains as evidence.</p>
        <p>Booking <code>{holdId}</code></p>
        <p>{hasServerClock ? `Database read: ${utcTime(observedAt)}. Countdown estimated from that read.` : 'Countdown uses this device’s clock and the expiry on the saved receipt.'}</p>
      </details>
    </section>
  );
}
