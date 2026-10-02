import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { SHOWCASE_PHASES } from './showcaseAdapters';

export type LadderStarter = {
  prompt: string;
  stretch: boolean;
  badge: string;
};

type StarterState = Pick<MeridianShowcaseState, 'selectedPhase' | 'phaseLabel' | 'phaseExamples'>;

/** The prompts a ladder phase offers before any conversation exists. */
export function ladderStarters(state: StarterState): LadderStarter[] {
  const examples = state.phaseExamples;
  const prompts = (state.selectedPhase <= 3
    ? [examples[0], examples[2]]
    : [examples[1], examples[2]]).filter(Boolean);
  const nextPhase = SHOWCASE_PHASES.find(phase => phase.phase === state.selectedPhase + 1);
  return prompts.map(prompt => {
    const stretch = state.phaseLabel !== 'Workflow' && prompt === examples[2];
    return {
      prompt,
      stretch,
      badge: stretch
        ? `Needs ${nextPhase?.label}`
        : state.selectedPhase === 4 ? 'Uses traveler context' : 'Works here',
    };
  });
}
