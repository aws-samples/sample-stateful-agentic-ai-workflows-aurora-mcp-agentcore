import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { SolutionBriefing } from '../SolutionBriefing';

describe('SolutionBriefing', () => {
  it('names the deployed controls and the provisioned policies', () => {
    render(<SolutionBriefing onOpenLadder={() => {}} />);
    expect(screen.getByRole('heading', { level: 1, name: 'Solution briefing' })).toBeInTheDocument();
    for (const heading of ['The five phases', 'Governance with Cedar', 'Durable workflow', 'Observability', 'Key AWS services']) {
      expect(screen.getByRole('heading', { level: 2, name: heading })).toBeInTheDocument();
    }
    expect(screen.getByText('meridian_hold_governance')).toBeInTheDocument();
    expect(screen.getByText('MeridianHolds___create_courtesy_hold')).toBeInTheDocument();
    expect(screen.getAllByText(/context\.input\.totalCents <= context\.input\.budgetCeilingCents/)).toHaveLength(2);
    expect(screen.getByText('meridian_booking_governance')).toBeInTheDocument();
    expect(screen.getByText('MeridianHolds___confirm_booking')).toBeInTheDocument();
  });

  it('hands off to the capability ladder', () => {
    const onOpenLadder = vi.fn();
    render(<SolutionBriefing onOpenLadder={onOpenLadder} />);
    fireEvent.click(screen.getByRole('button', { name: /Open the capability ladder/ }));
    expect(onOpenLadder).toHaveBeenCalledTimes(1);
  });
});
