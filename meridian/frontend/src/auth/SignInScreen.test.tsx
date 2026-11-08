import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { SignInScreen } from './SignInScreen';

describe('SignInScreen', () => {
  it('shows credentials inline and clears the password after submitting', () => {
    const hosted = vi.fn();
    const password = vi.fn();
    render(<SignInScreen onSignIn={hosted} onPasswordSignIn={password} />);
    expect(hosted).not.toHaveBeenCalled();
    expect(screen.getByLabelText('Email')).toHaveFocus();
    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'jordan@example.test' } });
    fireEvent.change(screen.getByLabelText('Password', { exact: true }), { target: { value: 'private' } });
    fireEvent.submit(screen.getByLabelText('Email').closest('form')!);
    expect(password).toHaveBeenCalledWith('jordan@example.test', 'private');
    expect(screen.getByLabelText('Password', { exact: true })).toHaveValue('');
    expect(hosted).not.toHaveBeenCalled();
  });
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

  describe('accessibility of the inline form', () => {
    it('keeps one name on the reveal button and reports state with aria-pressed', () => {
      render(<SignInScreen onPasswordSignIn={vi.fn()} />);
      const reveal = screen.getByRole('button', { name: 'Show password' });
      expect(reveal).toHaveAttribute('aria-pressed', 'false');
      fireEvent.click(reveal);
      expect(screen.getByRole('button', { name: 'Show password' }))
        .toHaveAttribute('aria-pressed', 'true');
      expect(screen.queryByRole('button', { name: 'Hide password' })).not.toBeInTheDocument();
    });

    it('does not name the form with text that repeats the sign-in button', () => {
      render(<SignInScreen onPasswordSignIn={vi.fn()} />);
      expect(screen.queryByRole('form', { name: 'Sign in' })).not.toBeInTheDocument();
    });

    it('labels the main landmark once and not the inner section as well', () => {
      const { container } = render(<SignInScreen />);
      expect(container.querySelectorAll('[aria-labelledby="signin-title"]')).toHaveLength(1);
    });

    it('marks both inputs invalid and points them at the alert on an error', () => {
      render(<SignInScreen onPasswordSignIn={vi.fn()} message="Check your email and password." />);
      const alert = screen.getByRole('alert');
      for (const input of [screen.getByLabelText('Email'),
        screen.getByLabelText('Password', { exact: true })]) {
        expect(input).toHaveAttribute('aria-invalid', 'true');
        expect(input).toHaveAttribute('aria-describedby', alert.id);
      }
    });

    it('leaves the inputs valid when there is no error', () => {
      render(<SignInScreen onPasswordSignIn={vi.fn()} />);
      const email = screen.getByLabelText('Email');
      expect(email).not.toHaveAttribute('aria-invalid');
      expect(email).not.toHaveAttribute('aria-describedby');
    });

    it('hides the decorative photograph from assistive technology', () => {
      const { container } = render(<SignInScreen />);
      expect(container.querySelector('figure img')?.getAttribute('alt')).toBe('');
    });
  });
});
