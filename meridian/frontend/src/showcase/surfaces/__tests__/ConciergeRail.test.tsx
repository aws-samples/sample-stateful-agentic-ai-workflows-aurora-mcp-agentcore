import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { EMPTY_FILTERS, type MeridianShowcaseState } from '../../hooks/useMeridianShowcase';
import { ConciergeRail } from '../ConciergeRail';

function makeState(overrides: Partial<MeridianShowcaseState> = {}): MeridianShowcaseState {
  return {
    tripHolds: [],
    travelersCount: 1,
    chatFilters: EMPTY_FILTERS,
    travelerProfile: null,
    previewProfile: null,
    budgetCeilingPerTravelerCents: null,
    savedTrips: [],
    ...overrides,
  } as unknown as MeridianShowcaseState;
}

describe('ConciergeRail unset values', () => {
  it('renders every unset value as quiet words, never as display type or a dash', () => {
    const { container } = render(<ConciergeRail state={makeState()} />);
    const unset = screen.getAllByText('Not set');
    expect(unset).toHaveLength(2);
    for (const value of unset) {
      expect(value).toHaveClass('mc-unset');
      expect(value.closest('strong')).toBeNull();
    }
    expect(container.querySelector('.mc-departure strong')?.textContent).toBe('Possibility.');
    expect(container.textContent).not.toMatch(/[—–]\s*$|:\s*[—–-]/m);
  });

  it('keeps real values loud', () => {
    render(<ConciergeRail state={makeState({
      travelerProfile: { home_airport: 'JFK' },
      budgetCeilingPerTravelerCents: 320000,
    })} />);
    expect(screen.getByText('JFK').tagName).toBe('STRONG');
    expect(screen.getByText('$3,200 per traveler', { exact: false })).not.toHaveClass('mc-unset');
    expect(screen.queryByText('Not set')).toBeNull();
  });
});
