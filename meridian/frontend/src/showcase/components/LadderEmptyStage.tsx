import { ArrowRight, Check } from 'lucide-react';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { ladderStarters } from '../lib/ladderStarters';
import { PHASE_QUERY_BOUNDARIES, showcasePromptLabel } from '../lib/showcaseAdapters';

/** Centered starter prompts for a ladder phase that has no conversation yet. */
export function LadderEmptyStage({ state }: { state: MeridianShowcaseState }) {
  return (
    <section className="mc-ladder-empty" aria-label="Starter queries for this phase">
      <p className="mc-ladder-empty-lead">Run a phase prompt to generate live system evidence.</p>
      <div className="mc-ladder-empty-cards">
        {ladderStarters(state).map(({ prompt, stretch, badge }) => {
          const label = showcasePromptLabel(prompt);
          return (
            <button
              key={prompt}
              type="button"
              className={`mc-ladder-empty-card${stretch ? ' is-stretch' : ' is-works'}`}
              disabled={state.isLoading}
              onClick={() => void state.applyPhaseExample(prompt, true, undefined)}
              aria-label={label === prompt ? prompt : `${label}: ${prompt}`}
              title={stretch ? `${PHASE_QUERY_BOUNDARIES[state.selectedPhase]} ${prompt}` : prompt}
            >
              <small>
                {stretch
                  ? <ArrowRight size={18} aria-hidden="true" />
                  : <Check size={18} strokeWidth={2.4} aria-hidden="true" />}
                {badge}
              </small>
              <span>{label}</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}
