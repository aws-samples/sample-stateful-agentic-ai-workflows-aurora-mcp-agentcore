import { CheckCircle2, ChevronDown, Circle, Clock3 } from 'lucide-react';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { deriveAuroraEvidence, type ProofStatus } from '../lib/showcaseProof';

function StatusIcon({ status }: { status: ProofStatus }) {
  if (status === 'observed') return <CheckCircle2 size={15} strokeWidth={2.2} />;
  if (status === 'ready') return <Clock3 size={15} strokeWidth={2.2} />;
  return <Circle size={15} strokeWidth={2.2} />;
}

export function AuroraEvidenceStrip({
  state,
  collapsed = false,
  onToggleCollapsed,
}: {
  state: MeridianShowcaseState;
  collapsed?: boolean;
  onToggleCollapsed?: () => void;
}) {
  const evidence = deriveAuroraEvidence({
    selectedPhase: state.selectedPhase,
    traceSpans: state.traceSpans,
    recommendations: state.recommendations,
  });
  const observedCount = evidence.filter((item) => item.status === 'observed').length;
  const observedLabel = observedCount
    ? `${observedCount} observed this turn`
    : 'waiting for first run';

  return (
    <section
      className={`mds-aurora-strip${collapsed ? ' is-collapsed' : ''}`}
      aria-label="Aurora proof points"
    >
      <div className="mds-aurora-strip-title">
        {onToggleCollapsed ? (
          <button
            type="button"
            className="mds-proof-toggle"
            onClick={onToggleCollapsed}
            aria-expanded={!collapsed}
            aria-label={collapsed ? 'Expand Aurora evidence' : 'Collapse Aurora evidence'}
          >
            <ChevronDown size={16} strokeWidth={2.4} aria-hidden="true" />
            <span>
              <em>Aurora evidence</em>
              <strong>{observedLabel}</strong>
              <small>Latest trace only, not cumulative</small>
            </span>
          </button>
        ) : (
          <span>Aurora evidence</span>
        )}
        {!collapsed && (
          <b>{state.traceSpans.length ? `${state.traceSpans.length} spans` : 'waiting for first run'}</b>
        )}
      </div>
      {!collapsed && (
        <div className="mds-aurora-strip-items">
          {evidence.map((item) => (
            <div
              key={item.key}
              className={`mds-aurora-chip is-${item.status}`}
              title={item.detail}
            >
              <span className="mds-aurora-chip-icon" aria-hidden="true">
                <StatusIcon status={item.status} />
              </span>
              <span className="mds-aurora-chip-copy">
                <b>{item.label}</b>
                <em>{item.value}</em>
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
