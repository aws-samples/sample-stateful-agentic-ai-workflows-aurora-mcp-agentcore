import { useCallback, useEffect, useState } from 'react';
import { Database, Lock, RefreshCw } from 'lucide-react';
import {
  fetchSessionReceipt,
  type SessionReceiptResponse,
} from '../../api/client';

/**
 * The closing beat: what the talk actually wrote to Aurora.
 *
 * Every phase claims to persist something - authorization decisions,
 * RLS-scoped reads, conversation turns, interaction embeddings, a courtesy
 * hold, workflow checkpoints. This counts the rows behind those claims so the
 * close can point at them instead of restating them.
 *
 * The counts are read under the same governance they report on: traveler-scoped
 * tables are counted inside a scoped session, and `bookings` is read as the
 * agent type entitled to it, because its policy gates on agent type as well as
 * traveler.
 */
export function SessionReceipt({
  travelerId,
  windowMinutes = 90,
}: {
  travelerId: string;
  windowMinutes?: number;
}) {
  const [receipt, setReceipt] = useState<SessionReceiptResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setReceipt(await fetchSessionReceipt(travelerId, windowMinutes));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not read the receipt');
    } finally {
      setLoading(false);
    }
  }, [travelerId, windowMinutes]);

  useEffect(() => {
    void load();
  }, [load]);

  const total = receipt?.lines.reduce((sum, line) => sum + line.count, 0) ?? 0;

  // Self-gating: during the cold open nothing has been written yet, and an
  // empty receipt would be a worse opener than no receipt at all. It appears
  // when there is something to point at.
  if (!error && (!receipt || total === 0)) return null;

  return (
    <section className="mds-receipt" aria-label="What this session wrote to Aurora">
      <header className="mds-receipt-head">
        <span className="mds-receipt-icon" aria-hidden="true">
          <Database size={17} strokeWidth={2.1} />
        </span>
        <div>
          <strong>Everything this session wrote to Aurora</strong>
          <small>
            {receipt
              ? `${receipt.since} · one cluster · workload ${receipt.authorization_subject ?? 'unidentified'}`
              : 'Reading the audit trail…'}
          </small>
        </div>
        <button
          type="button"
          className="mds-receipt-refresh"
          onClick={() => void load()}
          disabled={loading}
          aria-label="Re-read the session receipt"
        >
          <RefreshCw size={14} aria-hidden="true" />
        </button>
      </header>

      {error && (
        <p className="mds-receipt-error" role="alert">
          {error}
        </p>
      )}

      {receipt && (
        <>
          <ol className="mds-receipt-lines">
            {receipt.lines.map((line) => (
              <li key={line.table + line.label} className={line.count ? 'is-written' : ''}>
                <b>{line.count.toLocaleString()}</b>
                <span className="mds-receipt-line-copy">
                  <strong>{line.label}</strong>
                  {line.detail && <small>{line.detail}</small>}
                </span>
                <code>{line.table}</code>
                {line.scoped && (
                  <span className="mds-receipt-scoped" title="Counted inside an RLS-scoped session">
                    <Lock size={11} aria-hidden="true" />
                    RLS
                  </span>
                )}
              </li>
            ))}
          </ol>
          <p className="mds-receipt-footer">
            {total.toLocaleString()} rows, every one attributable to a workload
            authorized for a single traveler.
            {receipt.durable_checkpoints
              ? ' Workflow position included.'
              : ' Checkpoints were in-process only, so none were written.'}
          </p>
        </>
      )}
    </section>
  );
}
