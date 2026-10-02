import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { MeridianShowcaseState } from '../../hooks/useMeridianShowcase';
import { SHOWCASE_EXAMPLE_PROMPTS, showcasePromptLabel } from '../../lib/showcaseAdapters';
import { LadderEmptyStage } from '../LadderEmptyStage';

function state(overrides: Partial<MeridianShowcaseState> = {}) {
  return {
    selectedPhase: 1, phaseLabel: 'SQL', phaseExamples: SHOWCASE_EXAMPLE_PROMPTS[1],
    isLoading: false, applyPhaseExample: vi.fn(), ...overrides,
  } as unknown as MeridianShowcaseState;
}

const accessibleName = (prompt: string) => `${showcasePromptLabel(prompt)}: ${prompt}`;
const PHASE_ONE = SHOWCASE_EXAMPLE_PROMPTS[1];
const PHASE_THREE = SHOWCASE_EXAMPLE_PROMPTS[3];

describe('LadderEmptyStage', () => {
  it('offers the working and the boundary prompt of Phase 1 as labelled cards', () => {
    render(<LadderEmptyStage state={state()} />);
    const works = screen.getByRole('button', { name: accessibleName(PHASE_ONE[0]) });
    const stretch = screen.getByRole('button', { name: accessibleName(PHASE_ONE[2]) });
    expect(works).toHaveTextContent('Works here');
    expect(stretch).toHaveTextContent('Needs MCP');
    expect(screen.getByText('Run a phase prompt to generate live system evidence.'))
      .toBeVisible();
  });

  it('runs the prompt exactly like the Try a query chip', () => {
    const current = state();
    render(<LadderEmptyStage state={current} />);
    fireEvent.click(screen.getByText('City trips under $2,000 per traveler'));
    expect(current.applyPhaseExample).toHaveBeenCalledWith(PHASE_ONE[0], true, undefined);
  });

  it('uses the Phase 3 prompt pair and disables cards while a request runs', () => {
    const loading = state({
      selectedPhase: 3, phaseLabel: 'Retrieval', phaseExamples: PHASE_THREE, isLoading: true,
    });
    render(<LadderEmptyStage state={loading} />);
    const cards = screen.getAllByRole('button');
    expect(cards).toHaveLength(2);
    cards.forEach(card => expect(card).toBeDisabled());
    expect(cards[1]).toHaveTextContent('Needs Production');
  });
});
