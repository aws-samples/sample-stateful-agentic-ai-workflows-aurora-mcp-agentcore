/**
 * RlsProbeCard — Phase 4 "prove RLS is live" panel.
 *
 * Calls POST /api/diagnostics/rls-probe, which runs the SAME COUNT(*) twice
 * per table — once scoped (app.current_traveler_id set → RLS filters to the
 * traveler) and once unscoped (GUC empty → admin bypass → all rows). The bar
 * animates from the unscoped total down to the scoped count, so the audience
 * watches the row set collapse to just this traveler's data. Below each table
 * we show the real CREATE POLICY USING clause from pg_policies.
 *
 * Self-contained: owns its own fetch/loading/error state (not threaded through
 * the showcase hook).
 */
import { useEffect, useState } from 'react';
import { motion } from 'framer-motion';
import { fetchRlsProbe, type RlsProbeResponse } from '../../api/client';

const prefersReducedMotion =
  typeof window !== 'undefined' &&
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

export function RlsProbeCard({ travelerId }: { travelerId: string }) {
  const [data, setData] = useState<RlsProbeResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = () => {
    setLoading(true);
    setError(null);
    fetchRlsProbe(travelerId)
      .then((d) => setData(d))
      .catch((e) => setError(e?.message ?? 'Probe failed'))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [travelerId]);

  return (
    <div className="mds-rls">
      <div className="mds-rls-head">
        <span className="mds-rls-title">Row-Level Security · live</span>
        <button type="button" className="mds-rls-run" onClick={run} disabled={loading}>
          {loading ? 'Probing…' : 'Re-run probe'}
        </button>
      </div>

      {error && <div className="mds-empty">RLS probe unavailable: {error}</div>}

      {!error && !data && loading && (
        <div className="mds-empty">Running scoped vs unscoped counts…</div>
      )}

      {!error && data && (
        <>
          {data.tables.map((t) => {
            const denom = Math.max(t.unscoped_count, 1);
            const scopedPct = Math.round((t.scoped_count / denom) * 100);
            const hidden = Math.max(t.unscoped_count - t.scoped_count, 0);
            return (
              <div className="mds-rls-row" key={t.table}>
                <div className="mds-rls-row-head">
                  <code>{t.table}</code>
                  {t.error ? (
                    <span className="mds-rls-err">{t.error}</span>
                  ) : (
                    <span className="mds-rls-counts">
                      <b>{t.scoped_count}</b> of {t.unscoped_count} rows
                    </span>
                  )}
                </div>
                {!t.error && (
                  <div className="mds-rls-bar" role="img"
                    aria-label={`Without scope ${t.unscoped_count} rows, with RLS ${t.scoped_count} rows`}>
                    <motion.div
                      className="mds-rls-bar-scoped"
                      initial={prefersReducedMotion ? { width: `${scopedPct}%` } : { width: '100%' }}
                      animate={{ width: `${scopedPct}%` }}
                      transition={prefersReducedMotion ? { duration: 0 } : { duration: 0.7, ease: 'easeOut' }}
                    />
                  </div>
                )}
                {!t.error && (
                  <div className="mds-rls-legend">
                    <span><i className="mds-rls-dot is-scoped" />With RLS: {t.scoped_count}</span>
                    <span><i className="mds-rls-dot is-hidden" />Hidden by policy: {hidden}</span>
                  </div>
                )}
              </div>
            );
          })}

          {data.policies.length > 0 && (
            <div className="mds-rls-policies">
              <div className="mds-rls-policies-title">CREATE POLICY · USING</div>
              {data.policies.map((p) => (
                <div className="mds-rls-policy" key={`${p.table}-${p.policy}`}>
                  <small>{p.policy}</small>
                  <pre>{p.using_clause ?? '(no USING expression)'}</pre>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}
