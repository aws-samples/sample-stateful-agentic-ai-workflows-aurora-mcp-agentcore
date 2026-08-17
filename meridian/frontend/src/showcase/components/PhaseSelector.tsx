import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { SHOWCASE_PHASES } from '../lib/showcaseAdapters';

const MOBILE_PHASE_LABELS = {
  1: 'SQL',
  2: 'MCP',
  3: 'Intent',
  4: 'Trust',
  5: 'Flow',
} as const;

export function PhaseSelector({ state, compact = false }: { state: MeridianShowcaseState; compact?: boolean }) {
  return (
    <div className={`mds-phase-selector-wrap${compact ? ' is-compact' : ''}`}>
      <div className="mds-phase-selector" role="group" aria-label="Planning phase">
        {SHOWCASE_PHASES.map((phase) => {
          const mobileLabel = MOBILE_PHASE_LABELS[phase.phase];
          const accessibleLabel = mobileLabel === phase.label
            ? `${phase.label}: ${phase.description}. ${phase.proofPoint}.`
            : `${mobileLabel} - ${phase.label}: ${phase.description}. ${phase.proofPoint}.`;

          return (
            <button
              key={phase.label}
              type="button"
              aria-pressed={state.selectedPhase === phase.phase}
              className={state.selectedPhase === phase.phase ? 'is-active' : ''}
              onClick={() => state.setSelectedPhase(phase.phase)}
              aria-label={accessibleLabel}
              title={`${phase.description}. ${phase.proofPoint}.`}
            >
              <span className="mds-phase-selector-label-full">{phase.label}</span>
              <span className="mds-phase-selector-label-compact" aria-hidden="true">
                {mobileLabel}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
