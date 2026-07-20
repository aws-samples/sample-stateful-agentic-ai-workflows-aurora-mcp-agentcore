import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { Message } from '../../../types';
import {
  EMPTY_FILTERS,
  type MeridianShowcaseState,
} from '../../hooks/useMeridianShowcase';
import { ChatTranscript } from '../ChatTranscript';

function makeState(
  overrides: Partial<MeridianShowcaseState> = {},
): MeridianShowcaseState {
  return {
    selectedPhase: 5,
    phaseLabel: 'Workflow',
    messages: [],
    isLoading: false,
    error: null,
    latestStreamComplete: true,
    memoryFacts: [],
    totalLatencyMs: 0,
    chatFilters: EMPTY_FILTERS,
    submitPrompt: vi.fn(),
    markLatestStreamComplete: vi.fn(),
    setSelectedTrip: vi.fn(),
    selectTrip: vi.fn(),
    ...overrides,
  } as unknown as MeridianShowcaseState;
}

const PAUSED_TURN: Message[] = [
  { role: 'user', text: 'My JFK flight to Tokyo just got cancelled.' },
  {
    role: 'bot',
    text: 'Recovery paused at the checkpoint.',
    follow_ups: ['Resume workflow from checkpoint'],
  },
];

describe('follow-up chips', () => {
  it('renders backend follow-ups as clickable chips on the latest bot turn', () => {
    const state = makeState({ messages: PAUSED_TURN });
    render(<ChatTranscript state={state} />);

    expect(
      screen.getByRole('button', { name: /Resume workflow from checkpoint/i }),
    ).toBeInTheDocument();
  });

  it('submits the follow-up phrase verbatim when clicked', () => {
    const submitPrompt = vi.fn();
    const state = makeState({ messages: PAUSED_TURN, submitPrompt });
    render(<ChatTranscript state={state} />);

    fireEvent.click(
      screen.getByRole('button', { name: /Resume workflow from checkpoint/i }),
    );

    expect(submitPrompt).toHaveBeenCalledWith('Resume workflow from checkpoint');
  });

  it('hides follow-ups while a turn is in flight', () => {
    const state = makeState({
      messages: PAUSED_TURN,
      isLoading: true,
    });
    render(<ChatTranscript state={state} />);

    // While loading, the prior bot turn is no longer the "latest" turn, so
    // its follow-ups drop away and the thinking bubble takes over.
    expect(
      screen.queryByRole('button', { name: /Resume workflow from checkpoint/i }),
    ).not.toBeInTheDocument();
  });

  it('does not render follow-ups on older, non-latest bot turns', () => {
    const state = makeState({
      messages: [
        ...PAUSED_TURN,
        { role: 'user', text: 'Resume workflow from checkpoint' },
        { role: 'bot', text: 'Resumed from the checkpoint.' },
      ],
    });
    render(<ChatTranscript state={state} />);

    // The paused turn is no longer the latest bot turn, so its follow-up
    // chip must not linger and act on stale conversation state.
    expect(
      screen.queryByRole('button', { name: /Resume workflow from checkpoint/i }),
    ).not.toBeInTheDocument();
  });
});
