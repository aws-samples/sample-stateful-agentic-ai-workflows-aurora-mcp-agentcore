import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { BudgetCeiling } from '../BudgetCeiling';

describe('Budget ceiling', () => {
  it('states the saved cap and the party ceiling the policy judges', () => {
    render(<BudgetCeiling perTravelerCents={320000} travelers={2} />);
    expect(screen.getByText('$3,200 per traveler', { exact: false })).toBeInTheDocument();
    expect(screen.getByText('$6,400 for 2 travelers')).toBeInTheDocument();
  });

  it('drops the party line for a solo traveler, where the two numbers are one', () => {
    render(<BudgetCeiling perTravelerCents={320000} travelers={1} />);
    expect(screen.getByText('$3,200 per traveler', { exact: false })).toBeInTheDocument();
    expect(screen.queryByText(/for 1 traveler/)).not.toBeInTheDocument();
  });

  it('says nothing is set rather than inventing a number', () => {
    const { container } = render(<BudgetCeiling perTravelerCents={null} travelers={2} />);
    expect(container).toHaveTextContent('Not set');
    expect(container).not.toHaveTextContent('$');
  });
});
