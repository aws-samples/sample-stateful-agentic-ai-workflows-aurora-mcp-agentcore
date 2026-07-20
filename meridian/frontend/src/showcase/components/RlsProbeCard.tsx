/**
 * RlsProbeCard — Phase 4 workload authorization and RLS proof panel.
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
import { BadgeCheck, Database, Fingerprint, RefreshCw, ShieldX } from 'lucide-react';
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
        <span className="mds-rls-title">Workload authorization + RLS · live</span>
        <button
          type="button"
          className="mds-rls-run"
          onClick={run}
          disabled={loading}
          aria-label={loading ? 'Running governance probe' : 'Re-run governance probe'}
          title={loading ? 'Running governance probe' : 'Re-run governance probe'}
        >
          <RefreshCw size={15} aria-hidden="true" />
        </button>
      </div>

      {error && <div className="mds-empty">Governance probe unavailable: {error}</div>}

      {!error && !data && loading && (
        <div className="mds-empty">Running scoped vs unscoped counts…</div>
      )}

      {!error && data && (
        <>
          <div className="mds-authz-chain" aria-label="Identity and authorization proof">
            <div className="mds-authz-step">
              <span className="mds-authz-icon"><Fingerprint size={16} aria-hidden="true" /></span>
              <div>
                <small>1 · Authenticated workload</small>
                <strong>{data.authorization.provider}</strong>
                <code>{data.authorization.subject_id}</code>
              </div>
            </div>
            <div className="mds-authz-step is-allow">
              <span className="mds-authz-icon"><BadgeCheck size={16} aria-hidden="true" /></span>
              <div>
                <small>2 · Traveler grant</small>
                <strong>
                  <span className="mds-authz-decision is-allow">ALLOW</span>
                  Alex Morgan
                </strong>
                <code>{data.authorization.binding_id ?? 'traveler_identity_bindings'}</code>
              </div>
            </div>
            <div className="mds-authz-step is-deny">
              <span className="mds-authz-icon"><ShieldX size={16} aria-hidden="true" /></span>
              <div>
                <small>Negative control</small>
                <strong>
                  <span
                    className={`mds-authz-decision ${
                      data.negative_control.decision.toUpperCase() === 'ALLOW' ? 'is-allow' : 'is-deny'
                    }`}
                  >
                    {data.negative_control.decision.toUpperCase()}
                  </span>
                  Jordan Lee
                </strong>
                <code>{data.negative_control.reason ?? 'no active identity binding'}</code>
              </div>
            </div>
          </div>

          <div className="mds-rls-layer-label">
            <Database size={14} aria-hidden="true" />
            <span>3 · Aurora RLS filters the authorized traveler scope</span>
          </div>

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
                      <b className="mds-rls-count-scoped">{t.scoped_count}</b>
                      <span className="mds-rls-count-arrow" aria-hidden="true">←</span>
                      <s className="mds-rls-count-unscoped">{t.unscoped_count}</s>
                      <span className="mds-rls-count-unit">rows</span>
                    </span>
                  )}
                </div>
                {!t.error && (
                  <div className="mds-rls-bar" role="img"
                    aria-label={`Without scope ${t.unscoped_count} rows, with RLS ${t.scoped_count} rows`}>
                    <div className="mds-rls-bar-ghost" aria-hidden="true" />
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
