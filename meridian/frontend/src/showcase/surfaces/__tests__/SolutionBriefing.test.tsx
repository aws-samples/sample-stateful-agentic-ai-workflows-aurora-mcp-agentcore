import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { SolutionBriefing } from '../SolutionBriefing';

describe('SolutionBriefing', () => {
  it('keeps the configured policies and tools behind compact disclosures', () => {
    render(<SolutionBriefing onOpenLadder={() => {}} />);
    expect(screen.getByRole('heading', { level: 1, name: 'Solution briefing' })).toBeInTheDocument();
    for (const heading of ['The request, the tools and the state', 'Prepare the data before the question', 'Five phases, one traveler', 'A confirmed action, then a policy decision', 'Remember context. Resume execution.', 'Follow the result back to its evidence']) {
      expect(screen.getByRole('heading', { level: 2, name: heading })).toBeInTheDocument();
    }
    expect(screen.getByText('meridian_hold_governance')).not.toBeVisible();
    fireEvent.click(screen.getByText('Read the three Cedar policies'));
    expect(screen.getByText('meridian_hold_governance')).toBeVisible();
    fireEvent.click(screen.getByText('Inspect the four MCP tools'));
    expect(screen.getByText('MeridianHolds___create_courtesy_hold')).toBeInTheDocument();
    expect(screen.getAllByText(/context\.input\.totalCents <= context\.input\.budgetCeilingCents/)).toHaveLength(2);
    expect(screen.getByText('meridian_booking_governance')).toBeInTheDocument();
    expect(screen.getByText('MeridianHolds___confirm_booking')).toBeInTheDocument();
  });

  it('moves focus to the chosen section without leaving the briefing', () => {
    const scroll = vi.fn();
    render(<SolutionBriefing onOpenLadder={() => {}} />);
    const heading = screen.getByRole('heading', { name: 'Remember context. Resume execution.' });
    heading.scrollIntoView = scroll;
    fireEvent.click(screen.getByRole('button', { name: 'Memory & recovery' }));
    expect(scroll).toHaveBeenCalledWith({ block: 'start', behavior: 'instant' });
    expect(heading).toHaveFocus();
  });

  it('hands off to the capability ladder', () => {
    const onOpenLadder = vi.fn();
    render(<SolutionBriefing onOpenLadder={onOpenLadder} />);
    fireEvent.click(screen.getByRole('button', { name: /Open the capability ladder/ }));
    expect(onOpenLadder).toHaveBeenCalledTimes(1);
  });
});
