import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { SignInScreen } from './SignInScreen';

describe('SignInScreen', () => {
  it('names the product and offers one way in', () => {
    const onSignIn = vi.fn();
    render(<SignInScreen onSignIn={onSignIn} />);
    expect(
      screen.getByRole('heading', { level: 1, name: 'Sign in to Meridian' }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(onSignIn).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('says why the person is back, as an alert', () => {
    render(<SignInScreen message="Your session ended. Sign in again." />);
    expect(screen.getByRole('alert')).toHaveTextContent('Your session ended. Sign in again.');
  });

  it('waits while sign-in is in progress', () => {
    render(<SignInScreen busy />);
    expect(screen.getByRole('button', { name: 'Signing you in' })).toBeDisabled();
  });

  it('announces progress in a status region', () => {
    const { rerender } = render(<SignInScreen />);
    expect(screen.getByRole('status')).toBeEmptyDOMElement();
    rerender(<SignInScreen busy />);
    expect(screen.getByRole('status')).toHaveTextContent('Signing you in');
  });

  it('moves focus to the heading when a message appears', () => {
    const { rerender } = render(<SignInScreen />);
    expect(screen.getByRole('heading', { level: 1 })).not.toHaveFocus();
    rerender(<SignInScreen message="We could not complete sign-in. Try again." />);
    const heading = screen.getByRole('heading', { level: 1 });
    expect(heading).toHaveAttribute('tabindex', '-1');
    expect(heading).toHaveFocus();
  });
});
