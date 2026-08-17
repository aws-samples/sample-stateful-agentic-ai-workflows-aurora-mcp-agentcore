import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { MeridianShowcaseState } from '../../hooks/useMeridianShowcase';
import { TravelerContextPanel } from '../TravelerContextPanel';

function state(
  overrides: Partial<MeridianShowcaseState> = {},
): MeridianShowcaseState {
  return {
    selectedPhase: 3,
    phaseLabel: 'Retrieval',
    travelerId: 'trv_meridian_demo',
    memoryEnabled: false,
    memoryLoading: false,
    memoryToggleError: null,
    memoryMutationError: null,
    memoryFacts: [],
    traceSpans: [],
    modelLabel: 'Claude Sonnet 5',
    embedLabel: 'Cohere Embed v4',
    setMemoryEnabled: vi.fn(),
    ...overrides,
  } as unknown as MeridianShowcaseState;
}

describe('TravelerContextPanel memory capability', () => {
  it('keeps traveler facts locked before Production', () => {
    render(<TravelerContextPanel state={state()} onOpenMemory={vi.fn()} />);

    expect(screen.getByText('Unlocks in Production')).toBeInTheDocument();
    expect(screen.getByRole('switch', { name: 'Use traveler context: off' })).toBeDisabled();
    expect(screen.queryByText('Alex Morgan')).not.toBeInTheDocument();
  });

  it('enables the real memory path before rendering grouped facts', () => {
    const setMemoryEnabled = vi.fn();
    const { rerender } = render(
      <TravelerContextPanel
        state={state({
          selectedPhase: 4,
          phaseLabel: 'Production',
          setMemoryEnabled,
        })}
        onOpenMemory={vi.fn()}
      />,
    );

    const toggle = screen.getByRole('switch', { name: 'Use traveler context: off' });
    expect(toggle).toHaveAttribute('aria-checked', 'false');
    expect(screen.getByText('Context disconnected')).toBeInTheDocument();

    fireEvent.click(toggle);
    expect(setMemoryEnabled).toHaveBeenCalledWith(true);

    rerender(
      <TravelerContextPanel
        state={state({
          selectedPhase: 4,
          phaseLabel: 'Production',
          memoryEnabled: true,
          setMemoryEnabled,
          memoryFacts: [
            { key: 'home_airport', value: 'JFK', source: 'profile' },
            { key: 'no_red_eye', value: 'true', source: 'preference' },
            { key: 'tokyo_culture', value: 'Tokyo culture trip Oct 12-19', source: 'memory' },
          ],
        })}
        onOpenMemory={vi.fn()}
      />,
    );

    expect(toggle).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('switch', { name: 'Use traveler context: on' })).toBeInTheDocument();
    expect(screen.getByText('Profile')).toBeInTheDocument();
    expect(screen.getByText('Preferences')).toBeInTheDocument();
    expect(screen.getByText('Prior plans')).toBeInTheDocument();
    expect(screen.getByText('Aurora · RLS scoped')).toBeInTheDocument();
    expect(screen.getByText('Tokyo culture trip Oct 12-19')).toBeInTheDocument();
  });
});
