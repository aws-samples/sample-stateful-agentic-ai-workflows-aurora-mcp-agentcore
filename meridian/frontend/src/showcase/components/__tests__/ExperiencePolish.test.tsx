import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import { AnimatePresence, motion } from 'motion/react';
import {
  EMPTY_FILTERS,
  type MeridianShowcaseState,
} from '../../hooks/useMeridianShowcase';
import {
  SHOWCASE_EXAMPLE_PROMPTS,
  SHOWCASE_FINALE_PROMPT,
  showcasePromptLabel,
} from '../../lib/showcaseAdapters';
import { deriveRecoveryEvidence } from '../../lib/recoveryState';
import { DesktopMeridianApp } from '../../DesktopMeridianApp';
import { ChatComposer } from '../ChatComposer';
import { DiscoveryWorkspace } from '../DiscoveryWorkspace';
import { ConciergeConversation } from '../ConciergeConversation';
import { ConciergeRail } from '../../surfaces/ConciergeRail';
import { RecoveryBoardingPass } from '../RecoveryBoardingPass';
import { ChatTranscript } from '../ChatTranscript';
import { RecoveryWorkspace } from '../RecoveryWorkspace';
import type { JourneyDocument } from '../../journey/types';
import { TripResultCardContent } from '../TripResultCardContent';
import { TripDetailDrawer } from '../TripDetailDrawer';
import { ConciergeAssistanceCard } from '../RecoveryDecisionCards';
import { SessionClose } from '../../surfaces/SessionClose';
import { ALEX_IDENTITY, JORDAN_IDENTITY, UNKNOWN_IDENTITY } from '../../../test/signedIn';

function makeState(
  overrides: Partial<MeridianShowcaseState> = {},
): MeridianShowcaseState {
  return {
    traveler: JORDAN_IDENTITY,
    tripHolds: [],
    travelersCount: overrides.chatFilters?.travelers || 2,
    restoreJourney: vi.fn(),
    selectedPhase: 1,
    lastRequestPhase: null,
    recoveryRequest: null,
    inspectCurrentRun: vi.fn(),
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
    pendingWrite: null,
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

it('hands the original disruption request to Phase 5 from the follow-up chip', () => {
  const state = makeState({
    selectedPhase: 4,
    lastPrompt: SHOWCASE_FINALE_PROMPT,
    messages: [{ role: 'bot', text: 'Use Workflow for these dependent steps.', follow_ups: ['Run this in Workflow'] }],
  });
  render(<ChatTranscript state={state} />);
  fireEvent.click(screen.getByRole('button', { name: 'Run this in Workflow' }));
  expect(state.setSelectedPhase).toHaveBeenCalledWith(5);
  expect(state.applyPhaseExample).toHaveBeenCalledWith(SHOWCASE_FINALE_PROMPT, true, 5);
  expect(state.submitPrompt).not.toHaveBeenCalled();
});

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
  it('sends multiline requests with Enter and preserves Shift+Enter and IME composition', () => {
    const state = makeState({ currentPrompt: 'Tokyo for two\nDeparting JFK' });
    render(<ChatComposer state={state} conciergeMode />);
    const input = screen.getByRole('textbox', { name: 'Ask Meridian anything' });
    expect(input.tagName).toBe('TEXTAREA');
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true });
    fireEvent.keyDown(input, { key: 'Enter', isComposing: true });
    fireEvent.keyDown(input, { key: 'Enter', keyCode: 229 });
    expect(state.submitPrompt).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(state.submitPrompt).toHaveBeenCalledExactlyOnceWith(undefined, 4);
    expect(input).toHaveValue('Tokyo for two\nDeparting JFK');
  });

  it('does not submit blank or busy requests from the keyboard', () => {
    const state = makeState({ currentPrompt: '  \n  ' });
    const { rerender } = render(<ChatComposer state={state} />);
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    rerender(<ChatComposer state={{ ...state, currentPrompt: 'Tokyo', isLoading: true }} />);
    fireEvent.keyDown(screen.getByRole('textbox'), { key: 'Enter' });
    expect(state.submitPrompt).not.toHaveBeenCalled();
  });

  it('blocks prompt submission while traveler context is being authorized', () => {
    const state = makeState({ selectedPhase: 4, memoryLoading: true, currentPrompt: 'Recall my plan' });
    render(<ChatComposer state={state} proofMode />);
    expect(screen.getByRole('textbox', { name: 'Ask Meridian anything' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Send message' })).toBeDisabled();
    expect(screen.getByRole('status')).toHaveTextContent('Connecting traveler context');
  });

  it('does not infer lounge access or preference matching from unrelated catalog amenities', () => {
    const product = { product_id: 'amenities', name: 'Villa week', brand: 'Meridian', price: 100, category: 'Wellness', description: 'Villa stay, late checkout, fast wi-fi, cooking class.', image_url: '', available_sizes: ['6 nights'] };
    render(<TripResultCardContent product={product} state={makeState({ selectedPhase: 3 })} featured matchPct={null} matchLabel="Ranked #1" />);
    expect(screen.getByText('Dining experiences')).toBeInTheDocument();
    expect(screen.queryByText('Lounge access')).not.toBeInTheDocument();
    expect(screen.queryByText('Dining match')).not.toBeInTheDocument();
    expect(screen.queryByText('Traveler context recalled')).not.toBeInTheDocument();
  });

  it('shows booking reconciliation guidance without replaying an unrelated chat', () => {
    const clearError = vi.fn();
    const replayLastPrompt = vi.fn();
    const error = 'A saved booking request still needs reconciliation. Open its trip and retry the same hold to check Aurora before sending it again.';
    render(<ConciergeConversation state={makeState({ error, clearError, replayLastPrompt })} onSaved={vi.fn()} onRecovery={vi.fn()} />);

    expect(screen.getByRole('alert')).toHaveTextContent(error);
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(clearError).toHaveBeenCalledOnce();
    expect(replayLastPrompt).not.toHaveBeenCalled();
  });

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
      screen.getByRole('complementary', { name: 'Concierge conversation and travel brief' }),
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
    const inspectCurrentRun = vi.fn();
    render(
      <DesktopMeridianApp
        state={makeState({ selectedPhase: 3, lastRequestPhase: 4, clearChat, setSelectedPhase, setMemoryEnabled, inspectCurrentRun })}
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
    expect(inspectCurrentRun).not.toHaveBeenCalled();
    expect(clearChat).not.toHaveBeenCalled();
    expect(setSelectedPhase).not.toHaveBeenCalled();
    expect(setMemoryEnabled).not.toHaveBeenCalled();

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

  it('shows System evidence as loading, not empty, while a recovery run is in flight', () => {
    window.history.replaceState(null, '', '/showcase?view=proof');
    render(
      <DesktopMeridianApp
        state={makeState({
          selectedPhase: 5,
          phaseLabel: 'Workflow',
          conversationId: 'phase5-thread',
          lastPrompt: SHOWCASE_FINALE_PROMPT,
          isLoading: true,
        })}
        theme="dark"
        onToggleTheme={vi.fn()}
      />,
    );

    expect(screen.queryByText('No recovery selected')).not.toBeInTheDocument();
    expect(screen.getByText('Reading the journey from Aurora…')).toBeInTheDocument();
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
      screen.getByRole('heading', { name: "Jordan's JFK to Tokyo recovery" }),
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
    expect(screen.getByText('Save a step in Aurora')).toBeInTheDocument();
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
    // No span on this desk confirmed understanding, so clicking Resume does not either.
    expect(
      screen.getByText('Understand disruption').closest('li'),
    ).toHaveClass('is-pending');
  });

  it('fills in each recovery step with its service and time as the backend confirms it', () => {
    const traceSpan = (name: string, extra: Record<string, unknown> = {}) => ({
      id: name, name, category: 'orchestration', type: 'tool_call', status: 'ok',
      latencyMs: null, fields: [], ...extra,
    });
    const checkpoint = traceSpan('Checkpoint · AuroraDataApiSaver.put', {
      component: 'Aurora · LangGraph checkpoint tables', latencyMs: 106,
      fields: [{ label: 'checkpoint_durable', value: 'true' }],
    });
    const paused = [
      traceSpan('Workflow node: classify → plan', {
        component: 'LangGraph StateGraph', latencyMs: 0,
      }),
      traceSpan('Workflow node: search', {
        component: 'LangGraph → SearchAgent', latencyMs: 956,
      }),
      traceSpan('Embedding generated', { latencyMs: 221 }),
      traceSpan('Hybrid candidates fetched', { latencyMs: 338, sql: 'SELECT 1' }),
      checkpoint,
    ];
    const base = {
      selectedPhase: 5 as const, phaseLabel: 'Workflow' as const, conversationId: 'phase5-thread',
      messages: [
        { role: 'user' as const, text: SHOWCASE_FINALE_PROMPT },
        { role: 'bot' as const, text: 'Paused.' },
      ],
    };
    const step = (label: string) => screen.getByText(label).closest('li');
    const { rerender } = render(<RecoveryWorkspace state={makeState({
      ...base, lastPrompt: SHOWCASE_FINALE_PROMPT, workflowStatus: 'paused', traceSpans: paused,
    })} />);
    expect(step('Save a step in Aurora')).toHaveClass('is-visited');
    expect(step('Save a step in Aurora')).toHaveTextContent('Aurora Data API, 106 ms');
    expect(step('Search and rank')).toHaveTextContent('Bedrock + Aurora, 956 ms');
    expect(step('Verify after resume')).toHaveClass('is-pending');
    expect(screen.getByText('Paused at a saved step')).toBeInTheDocument();

    rerender(<RecoveryWorkspace state={makeState({
      ...base, lastPrompt: 'Resume workflow from checkpoint', workflowStatus: 'paused',
      isLoading: true, traceSpans: [],
    })} />);
    expect(step('Save a step in Aurora')).toHaveTextContent('Aurora Data API, 106 ms');
    expect(step('Verify after resume')).toHaveAttribute('aria-current', 'step');

    // The resumed run returns the paused run's spans without the write time.
    rerender(<RecoveryWorkspace state={makeState({
      ...base, lastPrompt: 'Resume workflow from checkpoint', workflowStatus: 'resumed',
      traceSpans: [
        ...paused.slice(0, 4), { ...checkpoint, latencyMs: null },
        traceSpan('Workflow node: availability fan-out', { latencyMs: 54 }),
        traceSpan('PackageAgent: Finding package', { latencyMs: 40 }),
      ],
    })} />);
    expect(step('Save a step in Aurora')).toHaveTextContent('Aurora Data API, 106 ms');
    expect(step('Verify after resume')).toHaveClass('is-visited');
    expect(step('Verify after resume')).toHaveTextContent('Aurora, 54 ms');
  });

  it('glows the Aurora mark once, when a watched run confirms the checkpoint', () => {
    const checkpoint = {
      id: 'cp', name: 'Checkpoint · AuroraDataApiSaver.put', category: 'memory_short',
      type: 'tool_call', status: 'ok', latencyMs: 106,
      component: 'Aurora · LangGraph checkpoint tables',
      fields: [{ label: 'checkpoint_durable', value: 'true' }],
    };
    const search = {
      id: 'search', name: 'Workflow node: search', category: 'orchestration',
      type: 'delegation', status: 'ok', latencyMs: 956, fields: [],
    };
    const base = {
      selectedPhase: 5 as const, phaseLabel: 'Workflow' as const, conversationId: 'phase5-glow',
      lastPrompt: SHOWCASE_FINALE_PROMPT,
    };
    const checkpointed = makeState({
      ...base, workflowStatus: 'paused', traceSpans: [search, checkpoint],
      messages: [
        { role: 'user' as const, text: SHOWCASE_FINALE_PROMPT },
        { role: 'bot' as const, text: 'Paused.' },
      ],
    });
    const { container, unmount } = render(<RecoveryWorkspace state={checkpointed} />);
    expect(container.querySelector('.mds-aurora-glow')).toBeNull();
    unmount();

    const watched = render(<RecoveryWorkspace state={makeState({
      ...base, isLoading: true, messages: [{ role: 'user' as const, text: SHOWCASE_FINALE_PROMPT }],
    })} />);
    watched.rerender(<RecoveryWorkspace state={checkpointed} />);
    expect(watched.container.querySelectorAll('.mds-aurora-glow')).toHaveLength(1);
    const mark = screen.getByText('Save a step in Aurora').closest('li')!
      .querySelector<HTMLElement>('.mds-recovery-step-icon')!;
    expect(mark.style.opacity).toBe('0');
  });

  it('still moves the recovery cues when the desk is the first view the app paints', () => {
    // The app's view swap starts with AnimatePresence initial={false}. That must
    // not freeze cues that mount later inside the view it first painted.
    const inFirstView = (child: ReactNode) => (
      <AnimatePresence initial={false}>
        <motion.div key="recovery" initial={{ opacity: 0 }} animate={{ opacity: 1 }}>
          {child}
        </motion.div>
      </AnimatePresence>
    );
    const checkpoint = {
      id: 'cp', name: 'Checkpoint · AuroraDataApiSaver.put', category: 'memory_short',
      type: 'tool_call', status: 'ok', latencyMs: 106,
      component: 'Aurora · LangGraph checkpoint tables',
      fields: [{ label: 'checkpoint_durable', value: 'true' }],
    };
    const search = {
      id: 'search', name: 'Workflow node: search', category: 'orchestration',
      type: 'delegation', status: 'ok', latencyMs: 956, fields: [],
    };
    const base = {
      selectedPhase: 5 as const, phaseLabel: 'Workflow' as const, conversationId: 'phase5-first',
      lastPrompt: SHOWCASE_FINALE_PROMPT,
    };
    const view = render(inFirstView(<RecoveryWorkspace state={makeState(base)} />));
    view.rerender(inFirstView(<RecoveryWorkspace state={makeState({
      ...base, isLoading: true, messages: [{ role: 'user' as const, text: SHOWCASE_FINALE_PROMPT }],
    })} />));
    view.rerender(inFirstView(<RecoveryWorkspace state={makeState({
      ...base, workflowStatus: 'paused', traceSpans: [search, checkpoint],
      messages: [
        { role: 'user' as const, text: SHOWCASE_FINALE_PROMPT },
        { role: 'bot' as const, text: 'Paused.' },
      ],
    })} />));
    const glow = view.container.querySelector<HTMLElement>('.mds-aurora-glow');
    expect(glow?.style.opacity).toBe('0.9');
    const mark = screen.getByText('Search and rank').closest('li')!
      .querySelector<HTMLElement>('.mds-recovery-step-icon')!;
    expect(mark.style.opacity).toBe('0');
  });

  it('does not glow or animate a checkpoint confirmed before the resume it watches', () => {
    const checkpoint = {
      id: 'cp', name: 'Checkpoint · AuroraDataApiSaver.put', category: 'memory_short',
      type: 'tool_call', status: 'ok', latencyMs: 106,
      component: 'Aurora · LangGraph checkpoint tables',
      fields: [{ label: 'checkpoint_durable', value: 'true' }],
    };
    const search = {
      id: 'search', name: 'Workflow node: search', category: 'orchestration',
      type: 'delegation', status: 'ok', latencyMs: 956, fields: [],
    };
    const base = {
      selectedPhase: 5 as const, phaseLabel: 'Workflow' as const,
      conversationId: 'phase5-restored', traceSpans: [search, checkpoint],
      messages: [
        { role: 'user' as const, text: SHOWCASE_FINALE_PROMPT },
        { role: 'bot' as const, text: 'Paused.' },
      ],
    };
    // Opened onto a journey Aurora already recorded, then resumed in front of the room.
    const view = render(<RecoveryWorkspace state={makeState({
      ...base, lastPrompt: SHOWCASE_FINALE_PROMPT, workflowStatus: 'paused',
    })} />);
    view.rerender(<RecoveryWorkspace state={makeState({
      ...base, lastPrompt: 'Resume workflow from checkpoint', workflowStatus: 'paused',
      isLoading: true,
    })} />);
    expect(view.container.querySelector('.mds-aurora-glow')).toBeNull();
    const icons = () => Array.from(
      view.container.querySelectorAll<HTMLElement>('.mds-recovery-step-icon'),
    );
    expect(icons().some(icon => icon.style.opacity === '0')).toBe(false);
  });

  it('starts a watched run without animating any step the backend has not confirmed', () => {
    const base = {
      selectedPhase: 5 as const, phaseLabel: 'Workflow' as const, conversationId: 'phase5-fresh',
    };
    const view = render(<RecoveryWorkspace state={makeState(base)} />);
    view.rerender(<RecoveryWorkspace state={makeState({
      ...base, isLoading: true, lastPrompt: SHOWCASE_FINALE_PROMPT,
      messages: [{ role: 'user' as const, text: SHOWCASE_FINALE_PROMPT }],
    })} />);
    const icons = Array.from(
      view.container.querySelectorAll<HTMLElement>('.mds-recovery-step-icon'),
    );
    expect(icons).toHaveLength(4);
    expect(icons.some(icon => icon.style.opacity === '0')).toBe(false);
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
      screen.getByText('Save a step in Aurora').closest('li'),
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

    expect(screen.getByText('Traveler context recalled')).toBeInTheDocument();
    expect(screen.getByText('2 travelers')).toBeInTheDocument();
    expect(screen.getByText('Stay details listed')).toBeInTheDocument();
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
    expect(screen.getByText('Saved step ready')).toBeInTheDocument();
    expect(within(screen.getByRole('region', { name: 'Recovery briefing' })).getByText('Tokyo Executive Stopover')).toBeVisible();
    expect(screen.getByText('$1,949 / traveler')).toBeInTheDocument();

    const summary = screen.getByText('Full agent response').closest('summary');
    expect(summary?.parentElement).not.toHaveAttribute('open');
    fireEvent.click(summary as HTMLElement);
    expect(summary?.parentElement).toHaveAttribute('open');

    expect(
      screen.getAllByRole('button', { name: 'Resume and request hold' }),
    ).toHaveLength(1);
    expect(
      screen.queryByRole('button', { name: 'Resume recovery' }),
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Resume and request hold' }));
    expect(submitPrompt).toHaveBeenCalledWith(
      'Resume workflow from the saved step',
      5,
    );
    expect(screen.queryByText('JORDAN')).not.toBeInTheDocument();
  });

  it('requires a matching persisted receipt before handing a hold to Concierge', () => {
    const onOpenProof = vi.fn();
    const onOpenConcierge = vi.fn();
    const state = makeState({
      selectedPhase: 5,
      conversationId: 'current-thread',
      traceSpans: [{
        id: 'hold', name: 'Courtesy hold', category: 'orchestration',
        type: 'tool_call', status: 'ok', latencyMs: 1, component: 'Gateway',
        fields: [{ label: 'hold_id', value: 'HLD-current' }],
      }],
    });
    const { rerender } = render(<RecoveryWorkspace state={state}
      onOpenProof={onOpenProof} onOpenConcierge={onOpenConcierge} />);
    expect(screen.queryByRole('button', { name: 'Take it back to Jordan' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Read booking receipt' }));
    expect(onOpenProof).toHaveBeenCalledOnce();

    const hold = {
      status: 'held', booking_id: 'HLD-current', package_id: 'CTY-002',
      duration: '3 nights', travelers_count: 2, unit_price: '2000.00',
      total_amount: '4000.00', hold_expires_at: '2099-01-01T00:00:00Z',
    };
    const document = { active_thread_id: 'different-thread', hold } as JourneyDocument;
    rerender(<RecoveryWorkspace state={state} journeyDocument={document}
      onOpenProof={onOpenProof} onOpenConcierge={onOpenConcierge} />);
    expect(screen.queryByRole('button', { name: 'Take it back to Jordan' })).not.toBeInTheDocument();

    rerender(<RecoveryWorkspace state={state}
      journeyDocument={{ ...document, active_thread_id: 'current-thread' }}
      onOpenProof={onOpenProof} onOpenConcierge={onOpenConcierge} />);
    fireEvent.click(screen.getByRole('button', { name: 'Take it back to Jordan' }));
    expect(onOpenConcierge).toHaveBeenCalledWith(hold);
  });

  it.each(['2020-01-01T00:00:00Z', null, 'invalid']) (
    'does not offer a Concierge handoff for an expired or unverified expiry: %s', (expiry) => {
      const state = makeState({ selectedPhase: 5, conversationId: 'current-thread' });
      const document = {
        active_thread_id: 'current-thread',
        hold: { status: 'held', booking_id: 'HLD-expired', hold_expires_at: expiry },
      } as JourneyDocument;
      render(<RecoveryWorkspace state={state} journeyDocument={document}
        onOpenConcierge={vi.fn()} />);
      expect(screen.queryByRole('button', { name: 'Take it back to Jordan' })).not.toBeInTheDocument();
      expect(screen.queryByText('Bring it home.')).not.toBeInTheDocument();
    },
  );

  it('shows recorded terms in trip details when catalog prices and party have changed', () => {
    const product = {
      product_id: 'CTY-002', name: 'Tokyo trip', price: 2499,
      available_sizes: ['5 nights'], description: '', image_url: '',
      brand: 'Meridian', category: 'city',
    };
    const state = makeState({
      selectedTrip: product, tripDetailsOpen: true, travelersCount: 4,
      tripHolds: [{ productId: product.product_id, order: {
        order_id: 'HLD-recorded', status: 'held', hold_expires_at: '2099-01-01T00:00:00Z',
        items: [{ product_id: product.product_id, name: product.name, size: '3 nights', quantity: 2, unit_price: 2000 }],
        subtotal: 4000, total: 4000, tax: 0, shipping: 0,
      } }],
    });
    const { container } = render(<TripDetailDrawer state={state} />);
    const facts = container.querySelector('.mds-trip-facts');
    expect(facts).toHaveTextContent('$2,000 / traveler');
    expect(facts).toHaveTextContent('3 nights');
    expect(facts).toHaveTextContent('Recorded total for 2 travelers');
    expect(facts).toHaveTextContent('$4,000');
    expect(facts).not.toHaveTextContent('$2,499');
    // The photo label sits on the black scrim, so it keeps the dark roles in
    // both themes (Important 4, tokens-task-6-review.md).
    expect(container.querySelector('.mds-trip-modal-visual')).toHaveAttribute('data-theme', 'dark');
  });

  it('scopes the dark photo roles to the hotel media region', () => {
    const state = makeState({ selectedPhase: 5 });
    const evidence = deriveRecoveryEvidence(state);
    const { container } = render(
      <ConciergeAssistanceCard
        stage="ready"
        evidence={evidence}
        product={null}
        onHotel={vi.fn()}
        onProtection={vi.fn()}
      />,
    );
    const media = container.querySelector('.mds-concierge-hotel-media');
    expect(media).toHaveAttribute('data-theme', 'dark');
  });

  it('marks only the Checkpointed chip with the checkpoint tone', () => {
    const product = {
      product_id: 'TKY-005', name: 'Tokyo Ryokan & Onsen Slow Week', price: 3899,
      brand: 'ANA Holidays',
      description: 'Lounge access included with an easy airport transfer.',
      image_url: '/travel/catalog/TKY-005.jpg', category: 'City & Culture',
      destination: 'Tokyo', region: 'Asia', available_sizes: ['5 nights'],
      availability: { '5 nights': 4 }, highlights: ['lounge access'],
    };
    const state = makeState({ selectedPhase: 5, workflowStatus: 'paused' });

    render(
      <article>
        <TripResultCardContent product={product} state={state} matchPct={null} featured />
      </article>,
    );

    expect(screen.getByText('Saved step').closest('span')).toHaveClass('is-checkpoint');
    expect(screen.getByText('Lounge access').closest('span')).not.toHaveClass('is-checkpoint');
    expect(screen.getByText('Lounge access').closest('span')?.className).toBe('');
  });

  it.each([
    'Snapshot saved: AuroraSnapshotStorage.write',
    'Workflow resumed from a saved step',
    'Checkpoint · AuroraSnapshotStorage.write',
  ])('shows the Checkpointed chip once a "%s" span is in the trace', (name) => {
    const product = {
      product_id: 'TKY-005', name: 'Tokyo Ryokan & Onsen Slow Week', price: 3899,
      brand: 'ANA Holidays', description: 'Lounge access included.',
      image_url: '/travel/catalog/TKY-005.jpg', category: 'City & Culture',
      destination: 'Tokyo', region: 'Asia', available_sizes: ['5 nights'],
      availability: { '5 nights': 4 }, highlights: ['lounge access'],
    };
    const state = makeState({
      selectedPhase: 5,
      traceSpans: [{
        id: 'saved', name, category: 'memory_short', type: 'tool_call', status: 'ok',
        latencyMs: 12, fields: [],
      }],
    });
    render(<article><TripResultCardContent product={product} state={state} matchPct={null} featured /></article>);
    expect(screen.getByText('Saved step')).toBeInTheDocument();
  });

  it('does not present a previous Concierge booking as a new recovery receipt', () => {
    const state = makeState({
      selectedPhase: 5,
      tripHolds: [{ productId: 'CTY-002', order: {
        order_id: 'HLD-previous-concierge', status: 'confirmed',
        items: [{ product_id: 'CTY-002', name: 'Previous trip', size: '3 nights', quantity: 2, unit_price: 2000 }],
        subtotal: 4000, total: 4000, tax: 0, shipping: 0,
      } }],
    });
    render(<RecoveryWorkspace state={state} />);
    expect(screen.getByRole('button', { name: 'Start recovery' })).toBeVisible();
    expect(screen.queryByRole('region', { name: 'Aurora booking receipt' })).not.toBeInTheDocument();
    expect(screen.queryByText('Previous trip')).not.toBeInTheDocument();
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
    expect(screen.queryByText('Saved step not observed yet')).not.toBeInTheDocument();
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
    expect(screen.getByText(/JAL Premium, Tokyo/i)).toBeInTheDocument();
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
    expect(screen.getByRole('region', { name: 'Recovery workflow saved steps' })).toBeInTheDocument();
    expect(screen.queryByText('Not valid for boarding')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Continue at recovery desk' }));
    expect(await screen.findByRole('heading', { name: "Jordan's JFK to Tokyo recovery" })).toBeInTheDocument();
    expect(new URL(window.location.href).searchParams.get('view')).toBe('recovery');
    expect(state.applyPhaseExample).not.toHaveBeenCalled();
    expect(state.submitPrompt).not.toHaveBeenCalled();
    expect(state.clearChat).not.toHaveBeenCalled();
    expect(state.setSelectedPhase).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Resume and request hold' }));
    expect(state.submitPrompt).toHaveBeenCalledWith('Resume workflow from the saved step', 5);
  });

  it('opens the closing screen from evidence and can return without clearing the journey', async () => {
    window.history.replaceState(null, '', '/showcase?view=proof&journey=same-journey');
    const state = makeState();
    render(<DesktopMeridianApp state={state} theme="dark" onToggleTheme={vi.fn()} />);
    fireEvent.click(screen.getByRole('button', { name: 'Session takeaways' }));
    expect(await screen.findByRole('region', { name: 'Session takeaways' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Open for questions' }));
    expect(screen.getByRole('heading', { name: /What would you build\s+for your customers\?/ })).toHaveFocus();
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
    const liveCatalog = [{
      product_id: 'WEL-005',
      name: 'Wellness Retreat Fixture',
      brand: 'Fixture Tours',
      price: 3699,
      description: 'A live catalog row used only in this test.',
      image_url: '/travel/catalog/WEL-005.jpg',
      category: 'Wellness & Luxury',
    }];
    const state = makeState({
      saveTrip,
      catalog: liveCatalog,
      savedTripIds: new Set(['WEL-005']),
      travelerProfile: null,
    });
    render(<DiscoveryWorkspace state={state} greeting="morning" onClear={vi.fn()} />);
    const button = screen.getByRole('button', { name: 'Unsave Wellness Retreat Fixture' });
    expect(button).toHaveAttribute('aria-pressed', 'true');
    fireEvent.click(button);
    expect(saveTrip).toHaveBeenCalledWith(expect.objectContaining({ product_id: 'WEL-005' }));
  });

  it('carries the active party and date selections into the trip brief', () => {
    render(<ConciergeRail state={makeState({ chatFilters: { ...EMPTY_FILTERS, travelers: 3, startDate: '2026-10-12', endDate: '2026-10-19' } })} />);
    expect(screen.getByText('3 adults')).toBeInTheDocument();
    expect(screen.getByText('Oct 12 to Oct 19')).toBeInTheDocument();
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


it('does not present a SQL result as a recovery plan', () => {
  const product = { product_id: 'BCN-1', name: 'Unrelated Barcelona trip', price: 1599,
    brand: 'Meridian', category: 'city', description: '', image_url: '' };
  render(<RecoveryWorkspace state={makeState({
    selectedPhase: 1,
    recommendations: [product],
    messages: [{ role: 'user', text: 'Show city trips' }, { role: 'bot', text: 'SQL results', products: [product] }],
  })} />);
  expect(screen.getByRole('button', { name: 'Start recovery' })).toBeInTheDocument();
  expect(screen.queryByText('Unrelated Barcelona trip')).not.toBeInTheDocument();
  expect(screen.queryByText('SQL results')).not.toBeInTheDocument();
});

it('keeps one concierge composer and sends its request through the production phase', () => {
  const state = makeState({ currentPrompt: 'Keep the boutique option', selectedPhase: 1 });
  render(<ConciergeConversation state={state} onSaved={vi.fn()} onRecovery={vi.fn()} />);
  expect(screen.getAllByRole('textbox', { name: 'Ask Meridian anything' })).toHaveLength(1);
  expect(screen.getAllByRole('button', { name: 'Help me plan a culture trip to Tokyo' })).toHaveLength(1);
  fireEvent.click(screen.getByRole('button', { name: 'Send message' }));
  expect(state.submitPrompt).toHaveBeenCalledWith(undefined, 4);
  expect(screen.getByRole('heading', { name: 'Your travel brief' })).not.toBeVisible();
});

it('blocks destination-studio starters until traveler context authorization completes', () => {
  const state = makeState({ memoryLoading: true });
  const { rerender } = render(<ConciergeConversation state={state} onSaved={vi.fn()} onRecovery={vi.fn()} />);
  const starter = screen.getByRole('button', { name: 'Help me plan a culture trip to Tokyo' });
  expect(starter).toBeDisabled();
  fireEvent.click(starter);
  expect(state.applyPhaseExample).not.toHaveBeenCalled();
  rerender(<ConciergeConversation state={{ ...state, memoryLoading: false }} onSaved={vi.fn()} onRecovery={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Help me plan a culture trip to Tokyo' }));
  expect(state.applyPhaseExample).toHaveBeenCalledWith('Help me plan a culture trip to Tokyo', true, 4);
});

it('keeps one response slot from waiting through streaming and offers stop beside an editable draft', () => {
  const state = makeState({ isLoading: true, chatProgress: 'Checking your trip options…',
    stopWaiting: vi.fn(), messages: [{ role: 'user', text: 'Plan Tokyo' }] });
  const { container, rerender } = render(<ConciergeConversation state={state} onSaved={vi.fn()} onRecovery={vi.fn()} />);
  const response = container.querySelector('.mc-message.is-bot');
  expect(response?.querySelector('.mc-response-spinner')).toBeInTheDocument();
  expect(screen.getByRole('textbox', { name: 'Ask Meridian anything' })).toBeEnabled();
  fireEvent.click(screen.getByRole('button', { name: 'Stop waiting' }));
  expect(state.stopWaiting).toHaveBeenCalledOnce();
  rerender(<ConciergeConversation state={{ ...state,
    messages: [...state.messages, { role: 'bot', text: 'Your Tokyo trip', streaming: true }],
  }} onSaved={vi.fn()} onRecovery={vi.fn()} />);
  expect(container.querySelectorAll('.mc-message.is-bot')).toHaveLength(1);
  expect(container.querySelector('.mc-message.is-bot')).toBe(response);
  expect(container.querySelector('.mc-loading')).not.toBeInTheDocument();
});

it('reveals provisional trips with the reply and enables actions only after the authoritative result', () => {
  const product = { product_id: 'CTY-002', name: 'Tokyo Culture & Cuisine', brand: 'Meridian',
    price: 2499, description: 'A catalog trip.', category: 'City', image_url: '/travel/catalog/CTY-002.jpg' };
  const state = makeState({ isLoading: true, streamingRecommendations: [product],
    messages: [{ role: 'user', text: 'Plan Tokyo' }] });
  const { rerender } = render(<DiscoveryWorkspace state={state} onClear={vi.fn()} />);
  expect(screen.queryByRole('article')).not.toBeInTheDocument();
  expect(screen.getByRole('status', { name: 'Updating your trip recommendations' })).toBeInTheDocument();
  const streaming = { ...state, messages: [...state.messages, { role: 'bot' as const, text: 'Here is Tokyo.', streaming: true }] };
  rerender(<DiscoveryWorkspace state={streaming} onClear={vi.fn()} />);
  const card = screen.getByRole('article', { name: product.name });
  expect(screen.getByRole('button', { name: `Explore this trip: ${product.name}` })).toBeDisabled();
  expect(screen.getByRole('button', { name: `Save ${product.name}` })).toBeDisabled();
  rerender(<DiscoveryWorkspace state={{ ...streaming, isLoading: false, recommendations: [product] }} onClear={vi.fn()} />);
  expect(screen.getByRole('article', { name: product.name })).toBe(card);
  expect(screen.getByRole('button', { name: `Explore this trip: ${product.name}` })).toBeEnabled();
  expect(screen.getByRole('button', { name: `Save ${product.name}` })).toBeEnabled();
});

describe('Navigation, waits and focus', () => {
  const tokyo = {
    product_id: 'CTY-002', name: 'Tokyo trip', price: 2499, available_sizes: ['5 nights'],
    description: '', image_url: '', brand: 'Meridian', category: 'city',
  };
  const heldTokyo = { productId: tokyo.product_id, order: {
    order_id: 'HLD-1', status: 'held', hold_expires_at: '2099-01-01T00:00:00Z',
    items: [{
      product_id: tokyo.product_id, name: tokyo.name, size: '5 nights',
      quantity: 2, unit_price: 2499,
    }],
    subtotal: 4998, total: 4998, tax: 0, shipping: 0,
  } };

  it.each(['ladder', 'recovery', 'proof'])('marks only the %s tab as the current page', view => {
    window.history.replaceState(null, '', `/showcase?view=${view}`);
    const { container } = render(
      <DesktopMeridianApp state={makeState()} theme="dark" onToggleTheme={vi.fn()} />,
    );
    const current = container.querySelectorAll('[aria-current="page"]');
    expect(current).toHaveLength(1);
    expect(current[0]).toHaveClass('mds-surface-tab');
  });

  it('does not repeat the empty ladder starters in the composer', () => {
    window.history.replaceState(null, '', '/showcase?view=ladder');
    render(<DesktopMeridianApp state={makeState()} theme="dark" onToggleTheme={vi.fn()} />);
    expect(screen.getByRole('region', { name: 'Starter queries for this phase' }))
      .toBeInTheDocument();
    expect(screen.queryByLabelText('Query starters for this phase')).not.toBeInTheDocument();
  });

  it('offers Stop waiting in the trip dialog and keeps focus inside it while confirming', () => {
    const base = makeState({ selectedTrip: tokyo, tripDetailsOpen: true, tripHolds: [heldTokyo],
      stopWaiting: vi.fn(), confirmTrip: vi.fn(), dismissBookingConfirmation: vi.fn(),
      closeTripDetails: vi.fn() });
    const { rerender } = render(<TripDetailDrawer state={{ ...base, bookingPrompt: heldTokyo }} />);
    screen.getByRole('button', { name: 'Yes, confirm this trip' }).focus();
    rerender(<TripDetailDrawer state={{ ...base, isLoading: true, pendingWrite: 'confirm' }} />);
    const stop = screen.getByRole('button', { name: 'Stop waiting' });
    expect(stop).toHaveFocus();
    expect(screen.getByRole('button', { name: 'Confirming…' })).toBeDisabled();
    fireEvent.click(stop);
    expect(base.stopWaiting).toHaveBeenCalledOnce();
    rerender(<TripDetailDrawer state={base} />);
    expect(screen.queryByRole('button', { name: 'Stop waiting' })).not.toBeInTheDocument();
    expect(screen.getByRole('dialog')).toHaveFocus();
  });

  it('does not label a chat request in the trip dialog as a hold', () => {
    const state = makeState({ selectedTrip: tokyo, tripDetailsOpen: true, isLoading: true });
    render(<TripDetailDrawer state={state} />);
    expect(screen.getByRole('button', { name: 'Request 12-hour hold' })).toBeDisabled();
    expect(screen.queryByRole('button', { name: 'Stop waiting' })).not.toBeInTheDocument();
  });

  it('does not show a concierge reply placeholder while a hold is written', () => {
    const state = makeState({
      isLoading: true, pendingWrite: 'hold', messages: [{ role: 'user', text: 'Plan Tokyo' }],
    });
    const { container } = render(
      <ConciergeConversation state={state} onSaved={vi.fn()} onRecovery={vi.fn()} />,
    );
    expect(container.querySelector('.mc-message.is-bot')).not.toBeInTheDocument();
  });

  it('returns focus to the ladder composer after a request it sent', () => {
    const state = makeState({ currentPrompt: 'City trips under $2,000' });
    const { rerender } = render(<ChatComposer state={state} proofMode />);
    const input = screen.getByRole('textbox', { name: 'Ask Meridian anything' });
    input.focus();
    fireEvent.keyDown(input, { key: 'Enter' });
    // Browsers drop focus from a control as it becomes disabled; jsdom does not.
    input.blur();
    rerender(<ChatComposer state={{ ...state, isLoading: true }} proofMode />);
    expect(input).toBeDisabled();
    expect(document.body).toHaveFocus();
    rerender(<ChatComposer state={state} proofMode />);
    expect(input).toHaveFocus();
  });
});


describe('copy names only the signed-in traveler', () => {
  const tokyo = {
    product_id: 'CTY-002', name: 'Tokyo trip', price: 2499, available_sizes: ['5 nights'],
    description: '', image_url: '', brand: 'Meridian', category: 'city',
  };
  const heldTokyo = { productId: tokyo.product_id, order: {
    order_id: 'HLD-1', status: 'held', hold_expires_at: '2099-01-01T00:00:00Z',
    items: [{
      product_id: tokyo.product_id, name: tokyo.name, size: '5 nights', quantity: 2, unit_price: 2499,
    }],
    subtotal: 4998, total: 4998, tax: 0, shipping: 0,
  } };
  const heldHandoff = {
    status: 'held', booking_id: 'HLD-current', package_id: 'CTY-002', duration: '3 nights',
    travelers_count: 2, unit_price: '2000.00', total_amount: '4000.00',
    hold_expires_at: '2099-01-01T00:00:00Z',
  };

  it('asks Alex, not Jordan, to confirm a held trip', () => {
    render(<TripDetailDrawer state={makeState({
      traveler: ALEX_IDENTITY, selectedTrip: tokyo, tripDetailsOpen: true, tripHolds: [heldTokyo],
    })} />);
    expect(screen.getByRole('button', { name: 'Confirm this trip for Alex' })).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('Jordan');
  });

  it('asks without a name when the account is unnamed', () => {
    render(<TripDetailDrawer state={makeState({
      traveler: UNKNOWN_IDENTITY, selectedTrip: tokyo, tripDetailsOpen: true, tripHolds: [heldTokyo],
    })} />);
    expect(screen.getByRole('button', { name: 'Confirm this trip' })).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('Jordan');
  });

  it.each([
    [ALEX_IDENTITY, 'Confirm this trip for Alex?'],
    [UNKNOWN_IDENTITY, 'Confirm this trip?'],
  ])('restates the booking to %j with its own name', (traveler, heading) => {
    render(<TripDetailDrawer state={makeState({
      traveler, selectedTrip: tokyo, tripDetailsOpen: true, tripHolds: [heldTokyo],
      bookingPrompt: heldTokyo,
    })} />);
    expect(screen.getByRole('heading', { name: heading })).toBeInTheDocument();
    expect(document.body.textContent).not.toContain('Jordan');
  });

  it.each([
    [ALEX_IDENTITY, "Alex's JFK to Tokyo recovery", 'Take it back to Alex'],
    [UNKNOWN_IDENTITY, 'Your JFK to Tokyo recovery', 'Take it back to your trip'],
  ])('titles and hands off the recovery for %j', (traveler, title, handoff) => {
    const document = { active_thread_id: 'current-thread', hold: heldHandoff } as JourneyDocument;
    const { container } = render(<RecoveryWorkspace
      state={makeState({ traveler, selectedPhase: 5, conversationId: 'current-thread' })}
      journeyDocument={document} onOpenConcierge={vi.fn()} />);
    expect(screen.getByRole('heading', { name: title })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: handoff })).toBeInTheDocument();
    expect(container.textContent).not.toContain('Jordan');
  });

  it('names nobody in the session close', () => {
    const { container } = render(<SessionClose onEvidence={vi.fn()} onConcierge={vi.fn()} />);
    expect(container.textContent).not.toContain('Jordan');
  });
});
