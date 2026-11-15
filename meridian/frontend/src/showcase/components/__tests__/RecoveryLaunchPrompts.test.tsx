import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import {
  SHOWCASE_EXAMPLE_PROMPTS,
  SHOWCASE_FINALE_PROMPT,
  showcasePromptLabel,
} from '../../lib/showcaseAdapters';
import { ChatComposer } from '../ChatComposer';
import { LadderEmptyStage } from '../LadderEmptyStage';
import { makeState } from './makeState';

const RECOVERY = { intent: 'recovery' };

function starter(prompt: string) {
  const button = screen.getByText(showcasePromptLabel(prompt)).closest('button');
  if (!button) throw new Error(`Missing starter for "${prompt}".`);
  return button;
}

describe('the ladder prompts that launch a recovery', () => {
  const phaseFive = {
    selectedPhase: 5 as const, phaseLabel: 'Workflow', phaseExamples: SHOWCASE_EXAMPLE_PROMPTS[5],
  };

  it('sends the finale starter chip to Phase 5 with the recovery intent', () => {
    const state = makeState(phaseFive);
    render(<ChatComposer state={state} proofMode />);

    fireEvent.click(starter(SHOWCASE_FINALE_PROMPT));

    expect(state.applyPhaseExample).toHaveBeenCalledWith(
      SHOWCASE_FINALE_PROMPT, true, undefined, RECOVERY,
    );
  });

  it('sends the finale card of the empty ladder stage with the recovery intent', () => {
    const state = makeState(phaseFive);
    render(<LadderEmptyStage state={state} />);

    fireEvent.click(starter(SHOWCASE_FINALE_PROMPT));

    expect(state.applyPhaseExample).toHaveBeenCalledWith(
      SHOWCASE_FINALE_PROMPT, true, undefined, RECOVERY,
    );
  });

  it('declares no intent for any other Phase 5 prompt', () => {
    const state = makeState(phaseFive);
    render(<ChatComposer state={state} proofMode />);
    const read = SHOWCASE_EXAMPLE_PROMPTS[5][1];

    fireEvent.click(starter(read));

    expect(state.applyPhaseExample).toHaveBeenCalledWith(read, true, undefined, undefined);
  });

  it('declares no intent for the same sentence on an earlier rung', () => {
    const state = makeState({
      selectedPhase: 4, phaseLabel: 'Production', phaseExamples: SHOWCASE_EXAMPLE_PROMPTS[4],
    });
    render(<ChatComposer state={state} proofMode />);

    fireEvent.click(starter(SHOWCASE_FINALE_PROMPT));

    expect(state.applyPhaseExample).toHaveBeenCalledWith(
      SHOWCASE_FINALE_PROMPT, true, undefined, undefined,
    );
  });

  it('carries the recovery intent through "Continue in Workflow"', () => {
    const state = makeState({
      selectedPhase: 4, phaseLabel: 'Production', phaseExamples: SHOWCASE_EXAMPLE_PROMPTS[4],
      lastPrompt: SHOWCASE_FINALE_PROMPT, isLoading: false,
    });
    render(<ChatComposer state={state} proofMode />);

    fireEvent.click(screen.getByRole('button', { name: /Continue in Workflow/ }));

    expect(state.applyPhaseExample).toHaveBeenCalledWith(
      SHOWCASE_FINALE_PROMPT, false, 5, RECOVERY,
    );
  });
});
