import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { SessionContext } from '../../../auth/SessionContext';
import type { TravelerIdentity } from '../../lib/travelerIdentity';
import { HeaderAccount } from '../HeaderAccount';

const traveler: TravelerIdentity = {
  id: 'trv_fixture', idVerified: true, name: 'Alex Rivera', initials: 'AR', avatarUrl: null,
};

describe('HeaderAccount', () => {
  it('shows the traveler and ends the session from a labelled button', () => {
    const signOut = vi.fn();
    render(
      <SessionContext.Provider value={{ traveler: null, source: 'cognito', signOut }}>
        <HeaderAccount traveler={traveler} />
      </SessionContext.Provider>,
    );
    expect(screen.getByText('Alex Rivera')).toBeInTheDocument();
    const button = screen.getByRole('button', { name: 'Sign out' });
    button.focus();
    expect(button).toHaveFocus();
    fireEvent.click(button);
    expect(signOut).toHaveBeenCalledTimes(1);
  });

  it('is absent when the build has no sign-in of its own', () => {
    render(<HeaderAccount traveler={traveler} />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(screen.queryByText('Alex Rivera')).not.toBeInTheDocument();
  });
});
