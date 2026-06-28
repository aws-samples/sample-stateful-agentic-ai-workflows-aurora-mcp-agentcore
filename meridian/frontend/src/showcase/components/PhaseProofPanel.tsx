import { Braces, ChevronDown, Database, GitBranch, ShieldCheck } from 'lucide-react';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { getPhaseProof } from '../lib/showcaseProof';

const proofRows = [
  { key: 'dataPath', label: 'Data path', Icon: GitBranch },
  { key: 'auroraCapability', label: 'Aurora capability', Icon: Database },
  { key: 'agentBoundary', label: 'Agent boundary', Icon: Braces },
  { key: 'proof', label: 'Proof to show', Icon: ShieldCheck },
] as const;

export function PhaseProofPanel({
  state,
  collapsed = false,
  onToggleCollapsed,
}: {
  state: MeridianShowcaseState;
  collapsed?: boolean;
  onToggleCollapsed?: () => void;
}) {
  const proof = getPhaseProof(state.selectedPhase);

  return (
    <section
      className={`mds-phase-proof-panel${collapsed ? ' is-collapsed' : ''}`}
      aria-label="Phase architecture proof"
    >
      <div className="mds-phase-proof-panel-head">
        {onToggleCollapsed ? (
          <button
            type="button"
            className="mds-proof-toggle"
            onClick={onToggleCollapsed}
            aria-expanded={!collapsed}
            aria-label={collapsed ? 'Expand build proof' : 'Collapse build proof'}
          >
            <ChevronDown size={16} strokeWidth={2.4} aria-hidden="true" />
            <span>
              <em>Build proof</em>
              <strong>{proof.headline}</strong>
            </span>
          </button>
        ) : (
          <div>
            <span>Build proof</span>
            <strong>{proof.headline}</strong>
          </div>
        )}
        {!collapsed && <code>{proof.source}</code>}
      </div>
      {!collapsed && (
        <div className="mds-phase-proof-grid">
          {proofRows.map(({ key, label, Icon }) => (
            <div className="mds-phase-proof-cell" key={key}>
              <span className="mds-proof-icon" aria-hidden="true">
                <Icon size={15} strokeWidth={2.1} />
              </span>
              <div>
                <span>{label}</span>
                <b>{proof[key]}</b>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
