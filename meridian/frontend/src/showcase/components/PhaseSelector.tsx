import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { SHOWCASE_PHASES } from '../lib/showcaseAdapters';

export function PhaseSelector({ state, compact = false }: { state: MeridianShowcaseState; compact?: boolean }) {
  return (
    <div className={`mds-phase-selector${compact ? ' is-compact' : ''}`} role="tablist" aria-label="Planning phase">
      {SHOWCASE_PHASES.map((phase) => (
        <button
          key={phase.label}
          type="button"
          role="tab"
          aria-selected={state.selectedPhase === phase.phase}
          className={state.selectedPhase === phase.phase ? 'is-active' : ''}
          onClick={() => state.setSelectedPhase(phase.phase)}
          title={phase.description}
        >
          <span>{phase.label}</span>
        </button>
      ))}
    </div>
  );
}
