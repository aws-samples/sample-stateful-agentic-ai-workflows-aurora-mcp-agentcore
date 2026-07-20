import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { Message } from '../../../types';
import {
  EMPTY_FILTERS,
  type MeridianShowcaseState,
} from '../../hooks/useMeridianShowcase';
import {
  SHOWCASE_EXAMPLE_PROMPTS,
  SHOWCASE_FINALE_PROMPT,
} from '../../lib/showcaseAdapters';
import { deriveRecoveryStage } from '../../lib/recoveryState';
import { ChatComposer } from '../ChatComposer';
import { ChatTranscript } from '../ChatTranscript';
import { JourneyPanel } from '../JourneyPanel';

function makeState(
  overrides: Partial<MeridianShowcaseState> = {},
): MeridianShowcaseState {
  return {
    selectedPhase: 1,
    phaseLabel: 'SQL',
    phaseExamples: SHOWCASE_EXAMPLE_PROMPTS[1],
    messages: [],
    currentPrompt: '',
    isLoading: false,
    error: null,
    lastPrompt: null,
    selectedTrip: null,
    savedTrips: [],
    savedTripIds: new Set(),
    comparedTrips: [],
    travelerProfile: {
      home_airport: 'JFK',
      party_size: 2,
      budget_max: 3200,
      loyalty_programs: {
        marriott_bonvoy: {
          program: 'Marriott Bonvoy',
          tier: 'Platinum Elite',
          member_id: 'MB xxxx4821',
          points_balance: 86240,
        },
        united_mileageplus: {
          program: 'United MileagePlus',
          tier: 'Premier 1K',
          member_id: 'MP••7314',
          points_balance: 124600,
        },
      },
    },
    chatFilters: EMPTY_FILTERS,
    setChatFilters: vi.fn(),
    resetChatFilters: vi.fn(),
    setCurrentPrompt: vi.fn(),
    submitPrompt: vi.fn(),
    applyPhaseExample: vi.fn(),
    openTripDetails: vi.fn(),
    saveTrip: vi.fn(),
    openComparison: vi.fn(),
    ...overrides,
  } as unknown as MeridianShowcaseState;
}

describe('Experience presentation polish', () => {
  it('keeps Experience customer-facing with exactly two prompt examples', () => {
    const state = makeState();
    const firstPrompt = SHOWCASE_EXAMPLE_PROMPTS[1][0];

    render(
      <>
        <ChatTranscript state={state} />
        <ChatComposer state={state} />
      </>,
    );

    expect(screen.getByText(/search live availability/i)).toBeInTheDocument();
    expect(screen.queryByText(/SQL mode/i)).not.toBeInTheDocument();
    expect(screen.getAllByText(firstPrompt)).toHaveLength(1);

    const promptButtons = SHOWCASE_EXAMPLE_PROMPTS[1].slice(0, 2).map((prompt) =>
      screen.getByRole('button', { name: prompt }),
    );
    expect(promptButtons).toHaveLength(2);
    promptButtons.forEach((button) => {
      expect(button).not.toHaveClass('is-stretch');
    });
  });

  it('reserves the dashed stretch treatment for System proof', () => {
    const state = makeState();
    render(<ChatComposer state={state} proofMode />);

    const working = screen.getByRole('button', {
      name: SHOWCASE_EXAMPLE_PROMPTS[1][0],
    });
    const stretch = screen.getByRole('button', {
      name: SHOWCASE_EXAMPLE_PROMPTS[1][2],
    });

    expect(working).not.toHaveClass('is-stretch');
    expect(stretch).toHaveClass('is-stretch');
  });

  it('progresses the current trip from disruption through recovery', () => {
    const initial = makeState();
    expect(deriveRecoveryStage(initial)).toBe('action');

    const runningMessages: Message[] = [
      { role: 'user', text: SHOWCASE_FINALE_PROMPT },
    ];
    const running = makeState({
      selectedPhase: 5,
      phaseLabel: 'Workflow',
      phaseExamples: SHOWCASE_EXAMPLE_PROMPTS[5],
      lastPrompt: SHOWCASE_FINALE_PROMPT,
      isLoading: true,
      messages: runningMessages,
    });
    expect(deriveRecoveryStage(running)).toBe('running');

    const ready = makeState({
      ...running,
      isLoading: false,
      messages: [
        ...runningMessages,
        { role: 'bot', text: 'Two live alternatives are ready.' },
      ],
    });
    expect(deriveRecoveryStage(ready)).toBe('ready');

    const { rerender } = render(<JourneyPanel state={initial} />);
    expect(screen.getByText('ANA · NH 109')).toBeInTheDocument();
    expect(screen.getByText('Action needed')).toBeInTheDocument();
    expect(screen.getByText('Cancelled')).toBeInTheDocument();
    expect(screen.getByText('Premier 1K recognized')).toBeInTheDocument();
    expect(screen.queryByText(/No shortlist/i)).not.toBeInTheDocument();

    rerender(<JourneyPanel state={running} />);
    expect(screen.getByText('Checking alternatives')).toBeInTheDocument();

    rerender(<JourneyPanel state={ready} />);
    expect(screen.getByText(/Recovery plan ready/i)).toBeInTheDocument();
  });
});
