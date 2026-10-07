import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { SessionContext } from './SessionContext';
import { SignOutButton } from './SignOutButton';

describe('SignOutButton', () => {
  it('ends the session', () => {
    const signOut = vi.fn();
    render(
      <SessionContext.Provider value={{ traveler: null, source: 'cognito', signOut }}>
        <SignOutButton />
      </SessionContext.Provider>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    expect(signOut).toHaveBeenCalledTimes(1);
  });

  it('is absent when the build has no sign-in of its own', () => {
    render(<SignOutButton />);
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});
