import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { MeridianShowcaseState } from '../../hooks/useMeridianShowcase';
import { PhaseSelector } from '../PhaseSelector';

describe('PhaseSelector', () => {
  it('keeps compact labels available without losing the phase name or selected state', () => {
    const setSelectedPhase = vi.fn();
    render(
      <PhaseSelector
        state={{
          selectedPhase: 4,
          setSelectedPhase,
        } as unknown as MeridianShowcaseState}
      />,
    );

    const selector = screen.getByRole('group', { name: 'Planning phase' });
    const production = screen.getByRole('button', {
      name: /Trust - Production: Workload grants, traveler memory, and RLS/i,
    });

    expect(selector).toContainElement(production);
    expect(production).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('Trust')).toHaveClass('mds-phase-selector-label-compact');

    fireEvent.click(screen.getByRole('button', { name: /Intent - Retrieval:/i }));
    expect(setSelectedPhase).toHaveBeenCalledWith(3);
  });
});
