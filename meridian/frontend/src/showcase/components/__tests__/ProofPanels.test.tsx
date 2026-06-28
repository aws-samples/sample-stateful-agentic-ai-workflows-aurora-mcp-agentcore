import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { MeridianShowcaseState } from '../../hooks/useMeridianShowcase';
import { AuroraEvidenceStrip } from '../AuroraEvidenceStrip';
import { PhaseProofPanel } from '../PhaseProofPanel';

const state = {
  selectedPhase: 2,
  traceSpans: [],
  recommendations: [],
} as unknown as MeridianShowcaseState;

describe('proof panels', () => {
  it('keeps build proof collapsed until the presenter expands it', () => {
    const onToggleCollapsed = vi.fn();
    const { rerender } = render(
      <PhaseProofPanel state={state} collapsed onToggleCollapsed={onToggleCollapsed} />,
    );

    expect(screen.getByRole('button', { name: /expand build proof/i })).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('Data path')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /expand build proof/i }));
    expect(onToggleCollapsed).toHaveBeenCalledTimes(1);

    rerender(<PhaseProofPanel state={state} collapsed={false} onToggleCollapsed={onToggleCollapsed} />);
    expect(screen.getByRole('button', { name: /collapse build proof/i })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('Data path')).toBeInTheDocument();
  });

  it('keeps Aurora evidence collapsed until expanded', () => {
    const { rerender } = render(
      <AuroraEvidenceStrip state={state} collapsed onToggleCollapsed={vi.fn()} />,
    );

    expect(screen.getByRole('button', { name: /expand aurora evidence/i })).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('SQL rows')).not.toBeInTheDocument();

    rerender(<AuroraEvidenceStrip state={state} collapsed={false} onToggleCollapsed={vi.fn()} />);
    expect(screen.getByRole('button', { name: /collapse aurora evidence/i })).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('SQL rows')).toBeInTheDocument();
  });
});
