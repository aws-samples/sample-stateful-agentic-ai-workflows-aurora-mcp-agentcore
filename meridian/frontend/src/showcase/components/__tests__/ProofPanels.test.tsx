import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { MeridianShowcaseState } from '../../hooks/useMeridianShowcase';
import { AuroraEvidenceStrip } from '../AuroraEvidenceStrip';

const state = {
  selectedPhase: 2,
  traceSpans: [],
  recommendations: [],
} as unknown as MeridianShowcaseState;

describe('proof panels', () => {
  it('keeps Aurora evidence collapsed until expanded', () => {
    const { rerender } = render(
      <AuroraEvidenceStrip state={state} collapsed onToggleCollapsed={vi.fn()} />,
    );

    expect(
      screen.getByRole('button', {
        name: 'Aurora evidence waiting for first run Latest trace only, not cumulative',
      }),
    ).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('SQL rows')).not.toBeInTheDocument();

    rerender(<AuroraEvidenceStrip state={state} collapsed={false} onToggleCollapsed={vi.fn()} />);
    expect(
      screen.getByRole('button', {
        name: 'Aurora evidence waiting for first run Latest trace only, not cumulative',
      }),
    ).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('SQL rows')).toBeInTheDocument();
  });
});
