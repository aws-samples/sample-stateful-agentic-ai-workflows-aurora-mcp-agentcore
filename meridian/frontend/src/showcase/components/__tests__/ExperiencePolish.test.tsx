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
import { deriveRecoveryEvidence, deriveRecoveryStage } from '../../lib/recoveryState';
import { DesktopMeridianApp } from '../../DesktopMeridianApp';
import { ChatComposer } from '../ChatComposer';
import { DiscoveryWorkspace } from '../DiscoveryWorkspace';
import { ConciergeRail } from '../../surfaces/ConciergeRail';
import { RecoveryBoardingPass } from '../RecoveryBoardingPass';
import { ChatTranscript } from '../ChatTranscript';
import { JourneyPanel } from '../JourneyPanel';
import { RecoveryRouteMap } from '../RecoveryRouteMap';
import { RecoveryWorkspace } from '../RecoveryWorkspace';
import { TripResultCardContent } from '../TripResultCardContent';
import { SessionClose } from '../../surfaces/SessionClose';

function makeState(
  overrides: Partial<MeridianShowcaseState> = {},
): MeridianShowcaseState {
  return {
    tripHolds: [],
    travelersCount: overrides.chatFilters?.travelers || 2,
    restoreJourney: vi.fn(),
    selectedPhase: 1,
    phaseLabel: 'SQL',
    phaseExamples: SHOWCASE_EXAMPLE_PROMPTS[1],
    messages: [],
    currentPrompt: '',
    recommendations: [],
    catalog: [],
    traceSpans: [],
    memoryFacts: [],
    previewFacts: [],
    previewProfile: null,
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
    clearChat: vi.fn(),
    setCurrentPrompt: vi.fn(),
    setSelectedPhase: vi.fn(),
    setMemoryEnabled: vi.fn(),
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
  it('starts with Concierge and clears into Phase 1 of the capability ladder', () => {
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
      screen.getByRole('region', { name: 'Meridian concierge' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('list', { name: 'Conversation with Meridian' }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole('complementary', { name: 'Your trip brief' }),
    ).toBeInTheDocument();
    // The sidebar also has a Concierge entry - that one is the traveler's
    // product nav. Scope to the surface axis.
    const surfaces = screen.getByRole('list', { name: 'Meridian surfaces' });
    expect(
      within(surfaces).getByRole('button', { name: /^Concierge/ }),
    ).toHaveAttribute('aria-current', 'page');

    fireEvent.click(
      screen.getByRole('button', {
        name: 'How it works: start the capability ladder at Phase 1',
      }),
    );

    expect(clearChat).toHaveBeenCalledOnce();
    expect(setSelectedPhase).toHaveBeenCalledWith(1);
    expect(container.querySelector('.mds-desktop-app')).toHaveClass(
      'is-proof',
      'is-ladder',
    );
    expect(
      screen.getByRole('button', { name: /^Phase 1, SQL/ }),
    ).toHaveAttribute('aria-current', 'step');
  });

  it('puts the five phases at the top level with the product entry un-numbered', () => {
    window.history.replaceState(null, '', '/showcase');
    const clearChat = vi.fn();
    const setSelectedPhase = vi.fn();
    const setMemoryEnabled = vi.fn();
    render(
      <DesktopMeridianApp
        state={makeState({ selectedPhase: 3, clearChat, setSelectedPhase, setMemoryEnabled })}
        theme="dark"
        onToggleTheme={vi.fn()}
      />,
    );

    // The rungs belong to the Capability ladder surface, one level below the
    // four surfaces, so open it before looking for them.
    const surfaces = screen.getByRole('list', { name: 'Meridian surfaces' });
    fireEvent.click(
      within(surfaces).getByRole('button', { name: /^Capability ladder/ }),
    );
    expect(clearChat).toHaveBeenCalledOnce();
    expect(setSelectedPhase).toHaveBeenCalledWith(1);
    expect(setMemoryEnabled).toHaveBeenCalledWith(false);

    const nav = screen.getByRole('navigation', {
      name: 'Capability ladder phases',
    });

    // All five rungs are top level - none of them nests inside a journey step.
    for (const label of ['SQL', 'MCP', 'Retrieval', 'Production', 'Workflow']) {
      expect(
        within(nav).getByRole('button', { name: new RegExp(`^Phase \\d, ${label}`) }),
      ).toBeInTheDocument();
    }

    // The Concierge is a peer surface, not a numbered rung of the ladder.
    const concierge = within(surfaces).getByRole('button', {
      name: /^Concierge/,
    });
    expect(concierge).toBeInTheDocument();
    expect(concierge).not.toHaveAttribute('aria-current', 'step');
  });

  it('carries the rungs already climbed so capability reads as cumulative', () => {
    const { container } = render(
      <DesktopMeridianApp
        state={makeState({ selectedPhase: 4 })}
        theme="dark"
        onToggleTheme={vi.fn()}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: /^Phase 4, Production/ }));

    const rungs = Array.from(
      container.querySelectorAll('.mds-ladder-nav-rung'),
    );
    // 1-3 carried, 4 active, 5 neither.
    expect(rungs.slice(0, 3).every((r) => r.classList.contains('is-carried'))).toBe(true);
    expect(rungs[3].classList.contains('is-active')).toBe(true);
    expect(rungs[4].classList.contains('is-carried')).toBe(false);
    expect(rungs[4].classList.contains('is-active')).toBe(false);
  });

  it('opens with the sidebar collapsed into an accessible icon rail', () => {
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

    // The rail starts closed so the transcript and result cards get the width.
    expect(app).toHaveClass('is-sidebar-collapsed');
    expect(
      screen.getByRole('button', { name: 'Expand navigation sidebar' }),
    ).toBeInTheDocument();
    // Collapsed is an icon rail, not a hidden nav - the items stay reachable.
    expect(screen.getByRole('button', { name: 'Trips' })).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole('button', { name: /^Phase 5, Workflow/ }),
    );
    expect(app).toHaveClass('is-sidebar-collapsed');

    fireEvent.click(
      screen.getByRole('button', { name: 'Expand navigation sidebar' }),
    );
    expect(app).not.toHaveClass('is-sidebar-collapsed');

    fireEvent.click(
      screen.getByRole('button', { name: 'Collapse navigation sidebar' }),
    );
    expect(app).toHaveClass('is-sidebar-collapsed');
  });

  it('keeps the live-service status visible and contextual when offline', () => {
    const { rerender } = render(
      <DesktopMeridianApp
        state={makeState({ backendStatus: 'online' })}
        theme="dark"
        onToggleTheme={vi.fn()}
      />,
    );

    const liveStatus = screen.getByRole('status');
    expect(liveStatus).toHaveTextContent('Meridian live');
    expect(liveStatus).toHaveTextContent('USD');
    expect(liveStatus).toHaveAttribute('aria-atomic', 'true');
    expect(liveStatus).toHaveClass('is-live');

    rerender(
      <DesktopMeridianApp
        state={makeState({ backendStatus: 'offline' })}
        theme="dark"
        onToggleTheme={vi.fn()}
      />,
    );

    const offlineStatus = screen.getByRole('status');
    expect(offlineStatus).toHaveTextContent('Meridian offline');
    expect(offlineStatus).toHaveTextContent('Live data unavailable');
    expect(offlineStatus).toHaveClass('is-off');
  });

  it('shows the recovery composer only after the plan is ready', () => {
    window.history.replaceState(null, '', '/showcase?view=recovery');
    const { container, rerender } = render(
      <DesktopMeridianApp
        state={makeState({ selectedPhase: 5 })}
        theme="dark"
        onToggleTheme={vi.fn()}
      />,
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
        '.mds-recovery-workspace .mds-chat-composer-wrap.is-recovery',
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

  it('shows one successful query and one boundary query in each phase', () => {
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
    expect(starters).toHaveClass('has-2');
    expect(starters?.querySelectorAll('.mds-chat-starter-chip')).toHaveLength(2);
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
    expect(screen.getByText('Saved loyalty profile')).toBeInTheDocument();
    expect(screen.getByText('Partner benefits need confirmation')).toBeInTheDocument();
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
    const startRecovery = screen.getByRole('button', { name: 'Start recovery' });
    expect(startRecovery.closest('.mds-mobile-disruption-message')).not.toBeNull();
    expect(startRecovery.closest('.mds-mobile-disruption-flight')).toBeNull();
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
        name: 'Let’s get your trip moving again.',
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
    expect(screen.getByText('Waiting for saved results')).toBeInTheDocument();
    expect(
      screen.getByText('Understand disruption').closest('li'),
    ).toHaveClass('is-pending');
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
        name: 'Let’s get your trip moving again.',
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

  it('reports an interrupted request without claiming no changes or completed steps', () => {
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
      screen.getByText('Recovery interrupted'),
    ).toBeInTheDocument();
    expect(screen.getByText('Workflow stopped')).toBeInTheDocument();
    expect(screen.getByText('Check saved progress before retrying')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Retry recovery' })).toBeInTheDocument();
    expect(
      screen.getByText('Save an Aurora checkpoint').closest('li'),
    ).toHaveClass('is-pending');
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
      screen.getByText('Let’s get your trip moving again.'),
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

  it('keeps failed inventory, memory, and checkpoint operations unverified', () => {
    const state = makeState({ traceSpans: [{
      id: 'failed', name: 'Checkpoint · AuroraDataApiSaver.put',
      details: 'availability fan-out, traveler memory, and loyalty failed',
      category: 'orchestration', type: 'tool_call', status: 'error',
      latencyMs: 1, fields: [{ label: 'checkpoint_durable', value: 'true' }],
    }] });
    expect(deriveRecoveryEvidence(state)).toMatchObject({
      availabilityObserved: false, loyaltyObserved: false, memoryObserved: false,
      checkpointObserved: false, durableCheckpoint: false,
    });
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
      // Seeded packages carry their own commissioned artwork, so a fixture
      // standing in for one has to as well.
      image_url: `/travel/catalog/${product.product_id}.jpg`,
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
      travelerProfile: { dietary_notes: 'Vegetarian' },
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

    expect(screen.getAllByText('Boutique hotels').length).toBeGreaterThan(0);
    expect(screen.queryByText('boutique > chain')).not.toBeInTheDocument();
    expect(screen.getByText('Vegetarian')).toBeInTheDocument();
    expect(screen.queryByText(/Shellfish allergy/)).not.toBeInTheDocument();
    expect(screen.queryByText(/benefits checked/)).not.toBeInTheDocument();
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


describe('Concierge travel states', () => {
  it('hands off a paused Workflow to the desk without starting or resuming a request', async () => {
    window.history.replaceState(null, '', '/showcase?view=ladder');
    const state = makeState({ selectedPhase: 5, workflowStatus: 'paused', conversationId: 'same-thread', lastPrompt: SHOWCASE_FINALE_PROMPT });
    render(<DesktopMeridianApp state={state} theme="dark" onToggleTheme={vi.fn()} />);
    expect(screen.getByRole('region', { name: 'Workflow checkpoint demonstration' })).toBeInTheDocument();
    expect(screen.queryByText('Not valid for boarding')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Continue at recovery desk' }));
    expect(await screen.findByRole('heading', { name: "Alex's JFK to Tokyo recovery" })).toBeInTheDocument();
    expect(new URL(window.location.href).searchParams.get('view')).toBe('recovery');
    expect(state.applyPhaseExample).not.toHaveBeenCalled();
    expect(state.submitPrompt).not.toHaveBeenCalled();
    expect(state.clearChat).not.toHaveBeenCalled();
    expect(state.setSelectedPhase).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Resume and verify' }));
    expect(state.submitPrompt).toHaveBeenCalledWith('Resume workflow from checkpoint', 5);
  });

  it('opens the closing screen from evidence and can return without clearing the journey', async () => {
    window.history.replaceState(null, '', '/showcase?view=proof&journey=same-journey');
    const state = makeState();
    render(<DesktopMeridianApp state={state} theme="dark" onToggleTheme={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Session takeaways' }));
    expect(await screen.findByRole('region', { name: 'Session takeaways' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Open for questions' }));
    expect(screen.getByRole('heading', { name: 'Where would you use this?' })).toHaveFocus();
    fireEvent.click(screen.getByRole('button', { name: 'Explore the live evidence' }));
    expect(await screen.findByRole('region', { name: 'System evidence' })).toBeInTheDocument();
    expect(new URL(window.location.href).searchParams.get('journey')).toBe('same-journey');
    expect(state.clearChat).not.toHaveBeenCalled();
  });

  it('lets Q&A revisit the takeaways and return to the concierge', () => {
    const onConcierge = vi.fn();
    render(<SessionClose onEvidence={vi.fn()} onConcierge={onConcierge} />);
    fireEvent.click(screen.getByRole('button', { name: 'Open for questions' }));
    fireEvent.click(screen.getByRole('button', { name: 'Back to takeaways' }));
    expect(screen.getByRole('region', { name: 'Session takeaways' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Return to Meridian' }));
    expect(onConcierge).toHaveBeenCalledOnce();
  });

  it('opens System evidence from the recovery proof action', async () => {
    window.history.replaceState(null, '', '/showcase?view=recovery');
    render(
      <DesktopMeridianApp
        state={makeState({
          selectedPhase: 5,
          phaseLabel: 'Workflow',
          lastPrompt: SHOWCASE_FINALE_PROMPT,
          workflowStatus: 'paused',
        })}
        theme="dark"
        onToggleTheme={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'View system evidence' }));
    expect(new URL(window.location.href).searchParams.get('view')).toBe('proof');
    expect(await screen.findByRole('region', { name: 'System evidence' })).toBeInTheDocument();
  });

  it('does not replace an empty live search with unrelated preview recommendations', () => {
    render(<DiscoveryWorkspace state={makeState({ messages: [{ role: 'user', text: 'Find trips to the moon' }, { role: 'bot', text: 'No matches found.' }], recommendations: [] })} greeting="morning" onClear={vi.fn()} />);
    expect(screen.getByText('A different direction?')).toBeInTheDocument();
    expect(screen.queryByRole('article')).not.toBeInTheDocument();
  });

  it('reflects saved state and allows removing the same trip', () => {
    const saveTrip = vi.fn();
    const state = makeState({ saveTrip, savedTripIds: new Set(['WEL-005']), travelerProfile: null });
    render(<DiscoveryWorkspace state={state} greeting="morning" onClear={vi.fn()} />);
    const button = screen.getByRole('button', { name: 'Unsave Tuscany Wine & Wellness' });
    expect(button).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(button);
    expect(saveTrip).toHaveBeenCalledWith(expect.objectContaining({ product_id: 'WEL-005' }));
  });

  it('carries the active party and date selections into the trip brief', () => {
    render(<ConciergeRail state={makeState({ chatFilters: { ...EMPTY_FILTERS, travelers: 3, startDate: '2026-10-12', endDate: '2026-10-19' } })} />);
    expect(screen.getByText('3 adults')).toBeInTheDocument();
    expect(screen.getByText('Oct 12 – Oct 19')).toBeInTheDocument();
  });

  it('renders the reported itinerary without inventing a flight or seat assignment', () => {
    render(<RecoveryBoardingPass state={makeState()} />);
    expect(screen.getByText('Not provided')).toBeInTheDocument();
    expect(screen.getByText('Not valid for boarding')).toBeInTheDocument();
    expect(screen.getByText('Traveler-reported')).toBeInTheDocument();
  });
});

it('carries the boundary question into the next capability without running it in the old phase', () => {
  const state = makeState({ lastPrompt: SHOWCASE_EXAMPLE_PROMPTS[1][2], messages: [{ role: 'bot', text: 'Switch to MCP.' }] });
  render(<ChatComposer state={state} proofMode />);
  fireEvent.click(screen.getByRole('button', { name: 'Continue in MCP' }));
  expect(state.setSelectedPhase).toHaveBeenCalledWith(2);
  expect(state.applyPhaseExample).toHaveBeenCalledWith(SHOWCASE_EXAMPLE_PROMPTS[2][0], false, 2);
});
