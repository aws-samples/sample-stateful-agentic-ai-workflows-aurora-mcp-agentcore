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
import { DesktopMeridianApp } from '../../DesktopMeridianApp';
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

function getQueryStarter(prompt: string) {
  const label = showcasePromptLabel(prompt);
  const button = screen.getByText(label).closest('button');
  if (!button) throw new Error(`Missing query starter for "${prompt}".`);

  expect(button).toHaveAttribute(
    'aria-label',
    label === prompt ? prompt : `${label}: ${prompt}`,
  );
  return button;
}

describe('Experience presentation polish', () => {
  it('starts with discovery and clears into Phase 1 of the capability ladder', () => {
    const clearChat = vi.fn();
    const setSelectedPhase = vi.fn();
    const { container } = render(
      <DesktopMeridianApp
        state={makeState({ clearChat, setSelectedPhase })}
        theme="dark"
        onToggleTheme={vi.fn()}
      />,
    );

    expect(
      screen.getByRole('region', { name: 'Meridian discovery' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: '1 Discovery, Experience' }),
    ).toHaveAttribute('aria-current', 'step');

    fireEvent.click(
      screen.getByRole('button', {
        name: 'Clear discovery and start the capability ladder',
      }),
    );

    expect(clearChat).toHaveBeenCalledOnce();
    expect(setSelectedPhase).toHaveBeenCalledWith(1);
    expect(container.querySelector('.mds-desktop-app')).toHaveClass(
      'is-proof',
      'is-ladder',
    );
    expect(
      screen.getByRole('button', { name: '2 Capability ladder, Architecture' }),
    ).toHaveAttribute('aria-current', 'step');
  });

  it('collapses the sidebar into an accessible icon rail across both demo steps', () => {
    const state = makeState({
      backendStatus: 'online',
    });
    const { container } = render(
      <DesktopMeridianApp
        state={state}
        theme="dark"
        onToggleTheme={vi.fn()}
      />,
    );
    const app = container.querySelector('.mds-desktop-app');

    fireEvent.click(
      screen.getByRole('button', { name: 'Collapse navigation sidebar' }),
    );

    expect(app).toHaveClass('is-sidebar-collapsed');
    expect(
      screen.getByRole('button', { name: 'Expand navigation sidebar' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Trips' })).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole('button', { name: '3 Stateful recovery, Proof' }),
    );
    expect(app).toHaveClass('is-sidebar-collapsed');

    fireEvent.click(
      screen.getByRole('button', { name: 'Expand navigation sidebar' }),
    );
    expect(app).not.toHaveClass('is-sidebar-collapsed');
  });

  it('shows the recovery composer only after the plan is ready', () => {
    const { container, rerender } = render(
      <DesktopMeridianApp
        state={makeState()}
        theme="dark"
        onToggleTheme={vi.fn()}
      />,
    );

    fireEvent.click(
      screen.getByRole('button', { name: '3 Stateful recovery, Proof' }),
    );

    const dock = container.querySelector('.mds-desktop-dock');
    const scroll = container.querySelector('.mds-desktop-scroll');

    expect(dock).not.toBeInTheDocument();
    expect(scroll?.querySelector('.mds-chat-composer-wrap.is-recovery'))
      .not.toBeInTheDocument();

    rerender(
      <DesktopMeridianApp
        state={makeState({
          selectedPhase: 5,
          lastPrompt: SHOWCASE_FINALE_PROMPT,
          workflowStatus: 'resumed',
        })}
        theme="dark"
        onToggleTheme={vi.fn()}
      />,
    );

    expect(
      container.querySelector(
        '.mds-desktop-dock .mds-chat-composer-wrap.is-recovery',
      ),
    ).toBeInTheDocument();
  });

  it('renders an offline geographic JFK-to-Tokyo recovery map', () => {
    const { container } = render(<RecoveryRouteMap />);

    expect(screen.getByRole('img', { name: /New York.*Tokyo/i })).toBeInTheDocument();
    expect(screen.getByText('JFK')).toBeInTheDocument();
    expect(screen.getByText('TYO')).toBeInTheDocument();
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

    const promptButtons = SHOWCASE_EXAMPLE_PROMPTS[1]
      .slice(0, 2)
      .map(getQueryStarter);
    expect(promptButtons).toHaveLength(2);
    promptButtons.forEach((button) => {
      expect(button).not.toHaveClass('is-stretch');
    });
  });

  it('shows one featured and two supporting trips until the user expands the result set', () => {
    const products = Array.from({ length: 4 }, (_, index) => ({
      product_id: `trip-${index + 1}`,
      name: `Trip ${index + 1}`,
      brand: 'Meridian Travel',
      price: 1200 + index * 100,
      description: `Catalog trip ${index + 1}`,
      image_url: '',
      category: 'city',
      destination: 'Tokyo',
      available_sizes: ['3 nights'],
    }));
    const state = makeState({
      recommendations: products,
      messages: [
        { role: 'user', text: 'Show me Tokyo trips.' },
        {
          role: 'bot',
          text: 'I found four live catalog options.',
          products,
        },
      ],
    });

    render(<ChatTranscript state={state} />);

    const results = screen.getByRole('region', { name: 'Trips for this turn' });
    expect(within(results).getAllByRole('article')).toHaveLength(3);
    expect(screen.getByText('Showing top 3 of 4 trips')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Show all 4' }));

    expect(within(results).getAllByRole('article')).toHaveLength(4);
    expect(screen.getByText('Showing all 4 trips')).toBeInTheDocument();
  });

  it('reserves the dashed stretch treatment for System proof', () => {
    const state = makeState();
    render(<ChatComposer state={state} proofMode />);

    const working = getQueryStarter(SHOWCASE_EXAMPLE_PROMPTS[1][0]);
    const stretch = getQueryStarter(SHOWCASE_EXAMPLE_PROMPTS[1][2]);

    expect(working).not.toHaveClass('is-stretch');
    expect(stretch).toHaveClass('is-stretch');
  });

  it('keeps projector query starters in stable two and three column groups', () => {
    const { container, rerender } = render(
      <ChatComposer state={makeState()} proofMode />,
    );

    let starters = container.querySelector('.mds-chat-query-starters');
    expect(starters).toHaveClass('has-2');
    expect(within(starters as HTMLElement).getByText('Try a query'))
      .toBeInTheDocument();
    expect(starters?.querySelectorAll('.mds-chat-starter-chip')).toHaveLength(2);

    rerender(
      <ChatComposer
        state={makeState({
          selectedPhase: 4,
          phaseLabel: 'Production',
          phaseExamples: SHOWCASE_EXAMPLE_PROMPTS[4],
        })}
        proofMode
      />,
    );

    starters = container.querySelector('.mds-chat-query-starters');
    expect(starters).toHaveClass('has-3');
    expect(starters?.querySelectorAll('.mds-chat-starter-chip')).toHaveLength(3);
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
    expect(screen.getByText('Traveler report')).toBeInTheDocument();
    expect(screen.getByText('Action needed')).toBeInTheDocument();
    expect(screen.getByText('Canceled')).toBeInTheDocument();
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

  it('renders an intentional recovery launch state before live results exist', () => {
    render(<RecoveryWorkspace state={makeState()} />);

    expect(
      screen.getByRole('article', { name: 'Start travel recovery' }),
    ).toBeInTheDocument();
    expect(
      screen.getAllByRole('button', { name: 'Start recovery' }),
    ).toHaveLength(1);
    expect(
      screen.getByRole('img', { name: 'Aircraft on final approach' }),
    ).toHaveAttribute('src', '/travel/recovery-flight.jpg');
    expect(
      screen.queryByRole('article', { name: 'Concierge assistance' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('article', { name: 'Recovery guardrails' }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole('article', { name: 'Agent proof' })).not.toBeInTheDocument();
    expect(screen.queryByRole('article', { name: 'Recovery option 2' })).not.toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: "Alex's JFK to Tokyo recovery" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('heading', {
        name: 'Your flight has been canceled.',
      }),
    ).toBeInTheDocument();
    expect(screen.getByText('Traveler-reported disruption')).toBeInTheDocument();
    expect(screen.queryByText(/ANA NH 109/i)).not.toBeInTheDocument();
    expect(
      screen.getByRole('list', { name: 'Recovery workflow progress' }),
    ).toBeInTheDocument();
    expect(screen.getByText('Search and rank')).toBeInTheDocument();
    expect(screen.getByText('Save an Aurora checkpoint')).toBeInTheDocument();
    expect(
      screen.queryByRole('textbox', { name: 'Ask Meridian anything' }),
    ).not.toBeInTheDocument();
  });

  it('shows the live recovery timeline while the workflow request is running', () => {
    render(
      <RecoveryWorkspace
        state={makeState({
          selectedPhase: 5,
          phaseLabel: 'Workflow',
          lastPrompt: SHOWCASE_FINALE_PROMPT,
          isLoading: true,
        })}
      />,
    );

    expect(screen.getByText('Live workflow')).toBeInTheDocument();
    expect(screen.getByText('Step 1 of 4')).toBeInTheDocument();
    expect(
      screen.getByText('Understand disruption').closest('li'),
    ).toHaveAttribute('aria-current', 'step');
    expect(
      screen.getByRole('article', { name: 'Live recovery progress' }),
    ).toHaveClass('is-compact');
    expect(
      screen.queryByRole('region', { name: 'Recovery decisions' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('article', { name: 'Recommended recovery plan' }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('article', { name: 'Recovery option 2' }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole('region', { name: 'Recovery briefing' }),
    ).toHaveTextContent('Building the recovery plan');
    expect(
      screen.queryByRole('heading', {
        name: 'Your flight has been canceled.',
      }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('textbox', { name: 'Ask Meridian anything' }),
    ).not.toBeInTheDocument();
  });

  it('continues at verification while resuming a checkpoint', () => {
    render(
      <RecoveryWorkspace
        state={makeState({
          selectedPhase: 5,
          phaseLabel: 'Workflow',
          lastPrompt: 'Resume workflow from checkpoint',
          workflowStatus: 'paused',
          isLoading: true,
          messages: [
            { role: 'user', text: SHOWCASE_FINALE_PROMPT },
          ],
        })}
      />,
    );

    expect(screen.getByText('Step 4 of 4')).toBeInTheDocument();
    expect(
      screen.getByText('Verify after resume').closest('li'),
    ).toHaveAttribute('aria-current', 'step');
    expect(
      screen.getByText('Understand disruption').closest('li'),
    ).toHaveClass('is-visited');
  });

  it('renders a precise failed checkpoint step with a retry action', () => {
    render(
      <RecoveryWorkspace
        state={makeState({
          selectedPhase: 5,
          phaseLabel: 'Workflow',
          lastPrompt: SHOWCASE_FINALE_PROMPT,
          messages: [
            { role: 'user', text: SHOWCASE_FINALE_PROMPT },
            {
              role: 'bot',
              text: 'Recovery stopped safely before changing the trip.',
            },
          ],
          traceSpans: [
            {
              id: 'checkpoint-error',
              name: 'LangGraph workflow error',
              category: 'error',
              type: 'error',
              status: 'error',
              latencyMs: 3000,
              details: 'Aurora checkpoint connection unavailable.',
              fields: [],
            },
          ],
        })}
      />,
    );

    expect(
      screen.getByText('Aurora checkpoint connection unavailable'),
    ).toBeInTheDocument();
    expect(screen.getByText('Workflow stopped')).toBeInTheDocument();
    expect(screen.getByText('No trip change was made')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry recovery' })).toBeInTheDocument();
    expect(
      screen.getByText('Save an Aurora checkpoint').closest('li'),
    ).toHaveClass('is-failed');
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

    render(<RecoveryWorkspace state={state} />);

    expect(screen.getByText('Request')).toBeInTheDocument();
    expect(screen.getByText('Checkpoint ready')).toBeInTheDocument();
    expect(screen.getAllByText('Tokyo Executive Stopover')).toHaveLength(2);
    expect(screen.getByText('$1,949 / traveler')).toBeInTheDocument();

    const summary = screen.getByText('Full agent response').closest('summary');
    expect(summary?.parentElement).not.toHaveAttribute('open');
    fireEvent.click(summary as HTMLElement);
    expect(summary?.parentElement).toHaveAttribute('open');

    expect(
      screen.getAllByRole('button', { name: 'Resume and verify' }),
    ).toHaveLength(1);
    expect(
      screen.queryByRole('button', { name: 'Resume recovery' }),
    ).not.toBeInTheDocument();
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
      <RecoveryWorkspace state={initial} />,
    );

    expect(screen.getByRole('button', { name: 'Start recovery' })).toBeInTheDocument();
    expect(
      screen.getByText('Your flight has been canceled.'),
    ).toBeInTheDocument();
    expect(screen.queryByText('Alternative pending')).not.toBeInTheDocument();
    expect(screen.queryByText('No live result observed yet')).not.toBeInTheDocument();
    expect(screen.queryByText('Checkpoint not observed yet')).not.toBeInTheDocument();
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
    rerender(<RecoveryWorkspace state={ready} />);

    expect(screen.getByRole('button', { name: 'Review this plan' })).toBeInTheDocument();
    expect(screen.getAllByText('Tokyo Executive Stopover').length).toBeGreaterThan(0);
    expect(screen.getByText(/JAL Premium · Tokyo/i)).toBeInTheDocument();
    expect(screen.getAllByText('$1,949').length).toBeGreaterThan(0);
    expect(screen.getByText('5 places across 2 stays')).toBeInTheDocument();
    expect(
      screen.getByText('Package inventory only. Flights not checked.'),
    ).toBeInTheDocument();
    expect(screen.queryByText('NH110')).not.toBeInTheDocument();
    expect(screen.getByText('Top-option inventory verified')).toBeInTheDocument();
    expect(
      screen.getByText('Flight and policy review remain traveler decisions'),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('textbox', { name: 'Ask Meridian anything' }),
    ).toBeEnabled();
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
    const products = [
      {
        product_id: 'TKY-003',
        name: 'Tokyo Executive Stopover',
        brand: 'JAL Premium',
        price: 1949,
      },
      {
        product_id: 'TKY-001',
        name: 'Tokyo Indie Neighborhood Walk',
        brand: 'JAL Tours',
        price: 1599,
      },
      {
        product_id: 'CTY-002',
        name: 'Tokyo Culture & Cuisine',
        brand: 'ANA Holidays',
        price: 2499,
      },
      {
        product_id: 'TKY-002',
        name: 'Tokyo Family Discovery Week',
        brand: 'ANA Holidays',
        price: 2899,
      },
    ].map((product) => ({
      ...product,
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

    render(<RecoveryWorkspace state={state} />);

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
    [
      ['Recovery option 2', '/travel/catalog/TKY-001.jpg'],
      ['Recovery option 3', '/travel/catalog/CTY-002.jpg'],
      ['Recovery option 4', '/travel/catalog/TKY-002.jpg'],
    ].forEach(([label, src]) => {
      const card = screen.getByRole('article', { name: label });
      expect(
        card.querySelector('.mds-flight-option-card-media'),
      ).toBeInTheDocument();
      expect(card.querySelector('.mds-flight-option-card-media img'))
        .toHaveAttribute('src', src);
    });
  });
});
