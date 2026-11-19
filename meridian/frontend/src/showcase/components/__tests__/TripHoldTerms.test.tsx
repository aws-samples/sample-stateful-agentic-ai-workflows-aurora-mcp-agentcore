import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { Product } from '../../../types';
import { TripDetailDrawer } from '../TripDetailDrawer';
import { makeState } from './makeState';

const tokyo: Product = {
  product_id: 'CTY-002', name: 'Tokyo trip', price: 2500, brand: 'Meridian', category: 'city',
  description: '', image_url: '', available_sizes: ['5 nights', '7 nights'],
  availability: { '5 nights': 10, '7 nights': 7 },
};
const terms = (size: string, unit = 250000, quantity = 2) => ({
  unit_price_cents: unit, total_cents: unit * quantity, currency: 'USD', size, quantity,
});

function open(product: Product = tokyo, extra = {}) {
  const state = makeState({
    selectedTrip: product, tripDetailsOpen: true, holdTrip: vi.fn(), clearHoldReview: vi.fn(),
    closeTripDetails: vi.fn(), holdReview: null, ...extra,
  });
  render(<TripDetailDrawer state={state} />);
  return state;
}

const holdButton = () => screen.getByRole('button', { name: /^Request 12-hour hold/ });

describe('the duration picker on the Hold action', () => {
  it('starts on the first published duration and keeps the one-click hold', () => {
    const state = open();
    const group = screen.getByRole('radiogroup', { name: 'Trip length' });
    expect(within(group).getByRole('radio', { name: /5 nights/ })).toHaveAttribute('aria-checked', 'true');
    expect(within(group).getByRole('radio', { name: /7 nights/ })).toHaveAttribute('aria-checked', 'false');
    expect(holdButton()).toHaveTextContent('5 nights, $5,000 total');
    fireEvent.click(holdButton());
    expect(state.holdTrip).toHaveBeenCalledTimes(1);
    expect(state.holdTrip).toHaveBeenCalledWith(tokyo, terms('5 nights'));
  });

  it('starts on the first duration with places for the party when the first is sold out', () => {
    const state = open({ ...tokyo, availability: { '5 nights': 0, '7 nights': 7 } });
    expect(screen.getByRole('radio', { name: /7 nights/ })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('radio', { name: /5 nights/ })).toHaveTextContent('Sold out');
    expect(holdButton()).toHaveTextContent('7 nights, $5,000 total');
    fireEvent.click(holdButton());
    expect(state.holdTrip).toHaveBeenCalledWith(expect.anything(), terms('7 nights'));
  });

  it('moves the choice with the arrow keys and sends the new duration', () => {
    const state = open();
    const first = screen.getByRole('radio', { name: /5 nights/ });
    first.focus();
    fireEvent.keyDown(first, { key: 'ArrowRight' });
    const second = screen.getByRole('radio', { name: /7 nights/ });
    expect(second).toHaveAttribute('aria-checked', 'true');
    expect(second).toHaveFocus();
    expect(holdButton()).toHaveTextContent('7 nights, $5,000 total');
    fireEvent.click(holdButton());
    expect(state.holdTrip).toHaveBeenCalledWith(tokyo, terms('7 nights'));
    fireEvent.keyDown(second, { key: 'ArrowRight' });
    expect(screen.getByRole('radio', { name: /5 nights/ })).toHaveAttribute('aria-checked', 'true');
    fireEvent.keyDown(screen.getByRole('radio', { name: /5 nights/ }), { key: 'End' });
    expect(screen.getByRole('radio', { name: /7 nights/ })).toHaveAttribute('aria-checked', 'true');
  });

  it('keeps one radio in the tab order and selects on click', () => {
    open();
    expect(screen.getByRole('radio', { name: /5 nights/ })).toHaveAttribute('tabindex', '0');
    expect(screen.getByRole('radio', { name: /7 nights/ })).toHaveAttribute('tabindex', '-1');
    fireEvent.click(screen.getByRole('radio', { name: /7 nights/ }));
    expect(screen.getByRole('radio', { name: /7 nights/ })).toHaveAttribute('tabindex', '0');
  });

  it('shows no picker for a single published duration', () => {
    open({ ...tokyo, available_sizes: ['5 nights'] });
    expect(screen.queryByRole('radiogroup')).not.toBeInTheDocument();
    expect(holdButton()).toHaveTextContent('5 nights, $5,000 total');
  });

  it('prices the total for the whole party', () => {
    open(tokyo, { travelersCount: 3 });
    expect(holdButton()).toHaveTextContent('5 nights, $7,500 total');
    expect(screen.getByText('Total for 3 travelers')).toBeInTheDocument();
  });
});

describe('the exception when the price changed after it was shown', () => {
  const review = (size = '5 nights') => ({
    productId: 'CTY-002', code: 'price_changed', message: 'The price changed after it was shown. Review the new price to continue.',
    quote: terms(size, 260000),
  });

  it('shows the new terms and only an explicit approval click sends them', () => {
    const state = open(tokyo, { holdReview: review() });
    expect(screen.getByRole('alert')).toHaveTextContent('The price changed after it was shown');
    expect(screen.getByText('$2,600 / traveler')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /^Request 12-hour hold/ })).not.toBeInTheDocument();
    expect(state.holdTrip).not.toHaveBeenCalled();
    const approve = screen.getByRole('button', { name: /^Approve the new price and request hold/ });
    expect(approve).toHaveTextContent('5 nights, $5,200 total');
    fireEvent.click(approve);
    expect(state.holdTrip).toHaveBeenCalledTimes(1);
    expect(state.holdTrip).toHaveBeenCalledWith(tokyo, terms('5 nights', 260000));
  });

  it('ignores a review for another duration and clears it', () => {
    const state = open(tokyo, { holdReview: review('7 nights') });
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByText('$2,500 / traveler')).toBeInTheDocument();
    expect(state.clearHoldReview).toHaveBeenCalled();
  });

  it('is dropped when the traveler picks another duration', () => {
    const state = open(tokyo, { holdReview: review() });
    fireEvent.click(screen.getByRole('radio', { name: /7 nights/ }));
    expect(state.clearHoldReview).toHaveBeenCalled();
  });
});
