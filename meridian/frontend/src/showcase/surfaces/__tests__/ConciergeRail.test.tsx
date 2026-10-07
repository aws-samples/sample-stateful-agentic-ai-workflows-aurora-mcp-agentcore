import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { EMPTY_FILTERS, type MeridianShowcaseState } from '../../hooks/useMeridianShowcase';
import { ConciergeRail } from '../ConciergeRail';
import { JORDAN_IDENTITY } from '../../../test/signedIn';

function makeState(overrides: Partial<MeridianShowcaseState> = {}): MeridianShowcaseState {
  return {
    traveler: JORDAN_IDENTITY,
    tripHolds: [],
    travelersCount: 1,
    chatFilters: EMPTY_FILTERS,
    travelerProfile: null,
    previewProfile: null,
    previewFacts: [],
    memoryFacts: [],
    budgetCeilingPerTravelerCents: null,
    savedTrips: [],
    ...overrides,
  } as unknown as MeridianShowcaseState;
}

describe('ConciergeRail traveler', () => {
  it('names the signed-in traveler and nobody else', () => {
    const { container } = render(<ConciergeRail state={makeState({
      traveler: {
        id: 'trv_demo_decoy', name: 'Jordan Lee', initials: 'JL', avatarUrl: null, idVerified: true,
      },
    })} />);
    expect(container.querySelector('.mc-traveler strong')?.textContent).toBe('Jordan Lee');
    expect(container.querySelector('.mc-traveler .mds-traveler-initials')?.textContent).toBe('JL');
    expect(container.textContent).not.toContain('Jordan Morgan');
  });

  it('shows initials and a neutral name for an account with no profile and no photo', () => {
    const { container } = render(<ConciergeRail state={makeState({
      traveler: { id: 'trv_demo_decoy', name: 'Your account', initials: 'YA', avatarUrl: null,
        idVerified: true },
    })} />);
    expect(container.querySelector('.mc-traveler strong')?.textContent).toBe('Your account');
    expect(container.querySelector('.mc-traveler .mds-traveler-initials')?.textContent).toBe('YA');
    expect(container.querySelector('.mc-traveler img')).toBeNull();
    expect(container.textContent).not.toContain('Jordan');
  });

  it('shows the photo from the sign-in when there is one', () => {
    const { container } = render(<ConciergeRail state={makeState({
      traveler: {
        id: 'trv_meridian_demo', name: 'Jordan Morgan', initials: 'JM',
        avatarUrl: '/travel/jordan-morgan.jpg', idVerified: true,
      },
    })} />);
    expect(container.querySelector('.mc-traveler img'))
      .toHaveAttribute('src', '/travel/jordan-morgan.jpg');
    expect(container.querySelector('.mc-traveler img')).toHaveAttribute('referrerpolicy', 'no-referrer');
    expect(container.querySelector('.mc-traveler img')).toHaveAttribute('decoding', 'async');
    expect(container.querySelector('.mds-traveler-initials')).toBeNull();
  });
});

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

  it('shows authorized remembered preferences once and links to editing', () => {
    const edit = vi.fn();
    render(<ConciergeRail state={makeState({
      previewProfile: { dietary_notes: 'No shellfish' },
      previewFacts: [{ key: 'dietary', value: 'No shellfish', confidence: 1 },
        { key: 'no_red_eye', value: 'true', confidence: 1 },
        { key: 'hotel_style', value: 'Quiet boutique hotels', confidence: 1 }],
    })} onPreferences={edit} />);
    expect(screen.getAllByText('No shellfish')).toHaveLength(1);
    expect(screen.getByText('Quiet boutique hotels')).toBeVisible();
    expect(screen.getByText('Avoid overnight flights: Yes')).toBeVisible();
    expect(screen.queryByText('true')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: 'Edit preferences' }));
    expect(edit).toHaveBeenCalledOnce();
  });
});
