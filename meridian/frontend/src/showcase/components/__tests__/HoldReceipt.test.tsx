import { act, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { HoldReceipt } from '../HoldReceipt';

const receipt = { holdId: 'booking-original', createdAt: '2026-09-06 12:00:00+00', expiresAt: '2026-09-06 12:15:00+00', observedAt: '2026-09-06 12:10:00+00', receivedAt: Date.parse('2026-09-07T00:00:00Z'), status: 'held' };

afterEach(() => vi.useRealTimers());

describe('Aurora hold receipt', () => {
  it('keeps a 12-hour receipt counting across close, reopen, and expiry', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-09-07T00:00:00Z'));
    const props = { holdId: 'direct-hold', kind: 'direct' as const, expiresAt: '2026-09-07T12:00:00Z', status: 'held' };
    const first = render(<HoldReceipt {...props} compact />);
    expect(screen.getByRole('timer')).toHaveTextContent('12:00:00 remaining');
    act(() => vi.advanceTimersByTime(1000));
    expect(screen.getByRole('timer')).toHaveTextContent('11:59:59 remaining');
    first.unmount();
    vi.setSystemTime(new Date('2026-09-07T11:59:59Z'));
    render(<HoldReceipt {...props} />);
    expect(screen.getByText('12-hour package hold')).toBeInTheDocument();
    expect(screen.getByRole('timer')).toHaveTextContent('00:00:01 remaining');
    act(() => vi.advanceTimersByTime(1000));
    expect(screen.getByRole('timer')).toHaveTextContent('Expired');
    expect(screen.queryByText(/retry uses the same request/)).not.toBeInTheDocument();
  });

  it('shows the recorded window and advances from database time despite a skewed device clock', () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-09-07T00:00:00Z'));
    const first = render(<HoldReceipt {...receipt} />);
    expect(screen.getByText('15-minute package hold')).toBeInTheDocument();
    expect(screen.getByRole('timer')).toHaveTextContent('5:00 remaining');
    expect(screen.getByText('2026-09-06 12:15:00 UTC')).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(120000));
    first.unmount();
    render(<HoldReceipt {...receipt} />);
    expect(screen.getByRole('timer')).toHaveTextContent('3:00 remaining');
    act(() => vi.advanceTimersByTime(180000));
    expect(screen.getByRole('timer')).toHaveTextContent('Expired');
    expect(screen.getByText(/no longer counts against package capacity/)).toBeInTheDocument();
  });

  it('does not revive an expired hold when the view mounts again', () => {
    render(<HoldReceipt {...receipt} observedAt="2026-09-06T12:20:00Z" />);
    expect(screen.getByRole('timer')).toHaveTextContent('Expired');
  });

  it('reports a released hold without a live countdown', () => {
    render(<HoldReceipt {...receipt} status="cancelled" />);
    expect(screen.getByRole('timer')).toHaveTextContent('Status: cancelled');
    expect(screen.queryByText(/remaining/)).not.toBeInTheDocument();
  });

  it('does not invent a 15-minute window when timestamps are missing or invalid', () => {
    render(<HoldReceipt holdId="older-booking" expiresAt="invalid" />);
    expect(screen.getByText('Package hold receipt')).toBeInTheDocument();
    expect(screen.getByRole('timer')).toHaveTextContent('Status not verified');
    expect(screen.queryByText(/NaN/)).not.toBeInTheDocument();
  });
});
