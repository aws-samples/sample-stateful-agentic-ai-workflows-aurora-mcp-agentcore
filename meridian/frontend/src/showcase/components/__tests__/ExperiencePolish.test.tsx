import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { Message } from '../../../types';
import {
  EMPTY_FILTERS,
  type MeridianShowcaseState,
} from '../../hooks/useMeridianShowcase';
import {
  SHOWCASE_EXAMPLE_PROMPTS,
  SHOWCASE_FINALE_PROMPT,
  showcasePromptLabel,
} from '../../lib/showcaseAdapters';
import { deriveRecoveryStage } from '../../lib/recoveryState';
import { ChatComposer } from '../ChatComposer';
import { ChatTranscript } from '../ChatTranscript';
import { JourneyPanel } from '../JourneyPanel';
import { RecoveryRouteMap } from '../RecoveryRouteMap';
import { RecoveryWorkspace } from '../RecoveryWorkspace';
import { TripResultCardContent } from '../TripResultCardContent';

function makeState(
  overrides: Partial<MeridianShowcaseState> = {},
): MeridianShowcaseState {
  return {
    selectedPhase: 1,
    phaseLabel: 'SQL',
    phaseExamples: SHOWCASE_EXAMPLE_PROMPTS[1],
    messages: [],
    currentPrompt: '',
    recommendations: [],
    traceSpans: [],
    memoryFacts: [],
    isLoading: false,
    error: null,
    lastPrompt: null,
    workflowStatus: null,
    workflowResumedAfterRestart: false,
    latestStreamComplete: true,
    markLatestStreamComplete: vi.fn(),
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
    setSelectedPhase: vi.fn(),
    submitPrompt: vi.fn(),
    applyPhaseExample: vi.fn(),
    openTripDetails: vi.fn(),
    saveTrip: vi.fn(),
    openComparison: vi.fn(),
    ...overrides,
  } as unknown as MeridianShowcaseState;
}

describe('Experience presentation polish', () => {
  it('renders an offline geographic JFK-to-HND recovery map', () => {
    const { container } = render(<RecoveryRouteMap />);

    expect(screen.getByRole('img', { name: /John F. Kennedy.*Haneda/i })).toBeInTheDocument();
    expect(screen.getByText('JFK')).toBeInTheDocument();
    expect(screen.getByText('HND')).toBeInTheDocument();
    expect(container.querySelectorAll('.mds-route-geography').length).toBeGreaterThan(100);
    expect(container.querySelector('.mds-route-line')).toBeInTheDocument();
  });

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
    expect(screen.getAllByText(showcasePromptLabel(firstPrompt))).toHaveLength(1);

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
      workflowStatus: 'resumed',
      messages: [
        ...runningMessages,
        { role: 'bot', text: 'Two live alternatives are ready.' },
      ],
    });
    expect(deriveRecoveryStage(ready)).toBe('ready');

    const checkpointed = makeState({
      ...running,
      isLoading: false,
      workflowStatus: 'paused',
      conversationId: 'phase5-demo',
      messages: [
        ...runningMessages,
        { role: 'bot', text: 'The shortlist is saved.' },
      ],
    });
    expect(deriveRecoveryStage(checkpointed)).toBe('checkpointed');

    const { rerender } = render(<JourneyPanel state={initial} />);
    expect(screen.getByText('ANA · NH 109')).toBeInTheDocument();
    expect(screen.getByText('Action needed')).toBeInTheDocument();
    expect(screen.getByText('Cancelled')).toBeInTheDocument();
    expect(screen.getByText('Airline Premier')).toBeInTheDocument();
    expect(screen.getByText('Elite status recognized')).toBeInTheDocument();
    expect(screen.queryByText(/No shortlist/i)).not.toBeInTheDocument();

    rerender(<JourneyPanel state={running} />);
    expect(screen.getByText('Checking alternatives')).toBeInTheDocument();

    rerender(<JourneyPanel state={checkpointed} />);
    expect(screen.getByText('Shortlist saved')).toBeInTheDocument();
    expect(screen.queryByText(/Recovery plan ready/i)).not.toBeInTheDocument();

    rerender(<JourneyPanel state={ready} />);
    expect(screen.getByText(/Recovery plan ready/i)).toBeInTheDocument();
  });

  it('keeps travel context collapsed until the presenter opens it', () => {
    render(<JourneyPanel state={makeState()} />);

    const toggle = screen.getByRole('button', { name: /Travel context/i });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('Budget')).not.toBeInTheDocument();

    fireEvent.click(toggle);

    expect(toggle).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('Budget')).toBeInTheDocument();
    expect(screen.getByText('$3,200')).toBeInTheDocument();
  });

  it('renders the reusable airline recovery decision-card system', () => {
    render(<RecoveryWorkspace state={makeState()} greetingPart="morning" />);

    expect(
      screen.getByRole('article', { name: 'Recommended recovery plan' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('article', { name: 'Recovery option 2' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('article', { name: 'Concierge assistance' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('article', { name: 'Agent proof' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('article', { name: 'Checkpointed plan progress' }),
    ).toBeInTheDocument();
    expect(
      document.querySelector('.mds-recovery-decision-system img'),
    ).toBeInTheDocument();
    expect(screen.getByText('Airport-area stay search')).toBeInTheDocument();
  });

  it('renders evidence-driven traveler signals on the featured recommendation', () => {
    const product = {
      product_id: 'TKY-003',
      name: 'Tokyo Executive Stopover',
      brand: 'JAL Premium',
      price: 1949,
      description:
        'Marunouchi business hotel with Haneda lounge access and car service.',
      image_url: '/travel/catalog/TKY-003.jpg',
      category: 'Business Travel',
      destination: 'Tokyo',
      region: 'Asia-Pacific',
      available_sizes: ['2 nights'],
      availability: { '2 nights': 14 },
      highlights: ['lounge access', 'car service'],
    };
    const state = makeState({
      selectedPhase: 4,
      phaseLabel: 'Production',
      memoryEnabled: true,
      memoryFacts: [
        {
          key: 'lodging_style',
          value: 'Boutique hotels',
          source: 'profile',
        },
      ],
    });

    render(
      <article>
        <TripResultCardContent
          product={product}
          state={state}
          matchPct={null}
          matchLabel="Personalized"
          featured
        />
      </article>,
    );

    expect(screen.getByText('Memory match')).toBeInTheDocument();
    expect(screen.getByText('2 travelers')).toBeInTheDocument();
    expect(screen.getByText('Preferred stay')).toBeInTheDocument();
    expect(screen.getByText('Lounge access')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /View details/i })).toBeInTheDocument();
  });

  it('renders the recovery conversation as a scannable decision brief', () => {
    const submitPrompt = vi.fn();
    const product = {
      product_id: 'tokyo-executive-stopover',
      name: 'Tokyo Executive Stopover',
      brand: 'Meridian Select',
      price: 1949,
      description: 'A premium Tokyo recovery option.',
      image_url: '/travel/tokyo-executive-stopover.jpg',
      category: 'city',
      destination: 'Tokyo',
      available_sizes: ['5 nights'],
    };
    const state = makeState({
      selectedPhase: 5,
      phaseLabel: 'Workflow',
      lastPrompt: SHOWCASE_FINALE_PROMPT,
      workflowStatus: 'paused',
      conversationId: 'phase5-demo',
      backendHealth: {
        status: 'healthy',
        checkpoint_durable: true,
      },
      recommendations: [product],
      traceSpans: [
        {
          id: 'checkpoint',
          name: 'Checkpoint · PostgresSaver.put',
          category: 'memory_short',
          type: 'tool_call',
          status: 'ok',
          latencyMs: 12,
          component: 'Aurora · LangGraph checkpoint tables',
          fields: [
            { label: 'checkpointer', value: 'PostgresSaver (Aurora · pooled)' },
          ],
        },
      ],
      messages: [
        {
          role: 'user',
          text: 'My flight to Tokyo was cancelled. Rebuild the trip.',
        },
        {
          role: 'bot',
          text: 'I ranked the Tokyo options and saved the workflow checkpoint.',
          products: [product],
          follow_ups: ['Resume workflow from checkpoint'],
        },
      ],
      submitPrompt,
    });

    render(<RecoveryWorkspace state={state} greetingPart="morning" />);

    expect(screen.getByText('Request')).toBeInTheDocument();
    expect(screen.getByText('Checkpoint ready')).toBeInTheDocument();
    expect(screen.getAllByText('Tokyo Executive Stopover')).toHaveLength(2);
    expect(screen.getByText('$1,949 / traveler')).toBeInTheDocument();

    const summary = screen.getByText('Full agent response').closest('summary');
    expect(summary?.parentElement).not.toHaveAttribute('open');
    fireEvent.click(summary as HTMLElement);
    expect(summary?.parentElement).toHaveAttribute('open');

    fireEvent.click(screen.getByRole('button', { name: 'Resume and verify' }));
    expect(submitPrompt).toHaveBeenCalledWith(
      'Resume workflow from checkpoint',
      5,
    );
    expect(screen.queryByText('ALEX')).not.toBeInTheDocument();
  });

  it('uses honest placeholders before recovery and the live top result afterward', () => {
    const product = {
      product_id: 'tokyo-executive-stopover',
      name: 'Tokyo Executive Stopover',
      brand: 'JAL Premium',
      price: 1949,
      description: 'A premium Tokyo recovery option.',
      image_url: '/travel/catalog/tokyo-executive-stopover.jpg',
      category: 'city',
      destination: 'Tokyo',
      available_sizes: ['2 nights', '3 nights'],
      availability: { '2 nights': 3, '3 nights': 2 },
    };
    const initial = makeState({
      selectedPhase: 5,
      phaseLabel: 'Workflow',
      phaseExamples: SHOWCASE_EXAMPLE_PROMPTS[5],
    });

    const { rerender } = render(
      <RecoveryWorkspace state={initial} greetingPart="morning" />,
    );

    expect(screen.getByRole('button', { name: 'Recover my trip' })).toBeInTheDocument();
    expect(
      screen.getByText('Your recommended plan will appear here'),
    ).toBeInTheDocument();
    expect(screen.getByText('No live result observed yet')).toBeInTheDocument();
    expect(screen.getByText('Checkpoint not observed yet')).toBeInTheDocument();
    expect(screen.getByText('Preference match pending')).toBeInTheDocument();
    expect(screen.queryByText('Seats available')).not.toBeInTheDocument();
    expect(screen.queryByText('Active')).not.toBeInTheDocument();

    const ready = makeState({
      selectedPhase: 5,
      phaseLabel: 'Workflow',
      phaseExamples: SHOWCASE_EXAMPLE_PROMPTS[5],
      lastPrompt: SHOWCASE_FINALE_PROMPT,
      workflowStatus: 'resumed',
      recommendations: [product],
      traceSpans: [
        {
          id: 'availability',
          name: 'Workflow node: availability fan-out',
          category: 'orchestration',
          type: 'delegation',
          status: 'ok',
          latencyMs: 18,
          details: 'Checked duration inventory for 3 top-ranked trips',
          fields: [],
        },
      ],
      messages: [
        { role: 'user', text: SHOWCASE_FINALE_PROMPT },
        { role: 'bot', text: 'Recovery complete.', products: [product] },
      ],
    });
    rerender(<RecoveryWorkspace state={ready} greetingPart="morning" />);

    expect(screen.getByRole('button', { name: 'Review this plan' })).toBeInTheDocument();
    expect(screen.getAllByText('Tokyo Executive Stopover').length).toBeGreaterThan(0);
    expect(screen.getByText(/JAL Premium · Tokyo/i)).toBeInTheDocument();
    expect(screen.getAllByText('$1,949').length).toBeGreaterThan(0);
    expect(screen.getByText('5 places across 2 stays')).toBeInTheDocument();
    expect(screen.queryByText('NH110')).not.toBeInTheDocument();
    expect(screen.getByText('Top-option inventory verified')).toBeInTheDocument();
    expect(
      screen.getByText('Policy review remains a traveler decision'),
    ).toBeInTheDocument();
  });

  it('marks only observed activity as verified', () => {
    const state = makeState({
      selectedPhase: 5,
      phaseLabel: 'Workflow',
      lastPrompt: SHOWCASE_FINALE_PROMPT,
      workflowStatus: 'paused',
      recommendations: [
        {
          product_id: 'tokyo',
          name: 'Tokyo option',
          brand: 'Meridian',
          price: 1800,
          description: 'Tokyo',
          image_url: '',
          category: 'city',
        },
      ],
      traceSpans: [
        {
          id: 'checkpoint',
          name: 'Checkpoint · PostgresSaver.put',
          category: 'memory_short',
          type: 'tool_call',
          status: 'ok',
          latencyMs: 10,
          fields: [],
        },
      ],
      messages: [
        { role: 'user', text: SHOWCASE_FINALE_PROMPT },
        { role: 'bot', text: 'Paused.' },
      ],
    });

    render(<JourneyPanel state={state} />);

    const loyaltyRow = screen
      .getByText('Loyalty perks')
      .closest('.mds-agent-activity-row');
    expect(loyaltyRow).toHaveClass('is-unobserved');
    expect(loyaltyRow).toHaveTextContent('Not observed in this run');
    expect(loyaltyRow).toHaveTextContent('not observed');
  });

  it('limits verified inventory to the top three plans and polishes memory context', () => {
    const products = Array.from({ length: 4 }, (_, index) => ({
      product_id: `tokyo-${index + 1}`,
      name: `Tokyo recovery option ${index + 1}`,
      brand: index === 0 ? 'JAL Premium' : 'ANA Holidays',
      price: 1900 + index * 200,
      description: 'A ranked Tokyo recovery option.',
      image_url: '',
      category: 'city',
      destination: 'Tokyo',
      available_sizes: ['3 nights', '5 nights'],
      availability: { '3 nights': 4, '5 nights': 2 },
    }));
    const state = makeState({
      selectedPhase: 5,
      phaseLabel: 'Workflow',
      lastPrompt: SHOWCASE_FINALE_PROMPT,
      workflowStatus: 'resumed',
      recommendations: products,
      memoryFacts: [
        {
          key: 'lodging_preference',
          value: 'boutique > chain',
          source: 'aurora',
        },
      ],
      traceSpans: [
        {
          id: 'availability',
          name: 'Workflow node: availability fan-out',
          category: 'orchestration',
          type: 'delegation',
          status: 'ok',
          latencyMs: 18,
          details: 'Checked duration inventory for 3 top-ranked trips',
          fields: [],
        },
        {
          id: 'memory',
          name: 'Aurora recall',
          category: 'memory_long',
          type: 'database',
          status: 'ok',
          latencyMs: 9,
          details: 'Traveler memory and loyalty profile applied',
          fields: [],
        },
      ],
      messages: [
        { role: 'user', text: SHOWCASE_FINALE_PROMPT },
        { role: 'bot', text: 'Recovery complete.', products },
      ],
    });

    render(<RecoveryWorkspace state={state} greetingPart="morning" />);

    expect(screen.getByText('Boutique hotels')).toBeInTheDocument();
    expect(screen.queryByText('boutique > chain')).not.toBeInTheDocument();
    expect(
      within(screen.getByRole('article', { name: 'Recovery option 2' }))
        .getByText('6 places across 2 stays'),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole('article', { name: 'Recovery option 3' }))
        .getByText('6 places across 2 stays'),
    ).toBeInTheDocument();
    expect(
      within(screen.getByRole('article', { name: 'Recovery option 4' }))
        .getByText('Live duration inventory pending'),
    ).toBeInTheDocument();
  });
});
