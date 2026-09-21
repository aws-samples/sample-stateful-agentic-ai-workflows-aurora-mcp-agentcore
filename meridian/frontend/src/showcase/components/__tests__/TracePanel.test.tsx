import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { MeridianShowcaseState } from '../../hooks/useMeridianShowcase';
import type { ShowcaseTraceSpan } from '../../lib/showcaseAdapters';
import { TracePanel } from '../TracePanel';

const traceSpan: ShowcaseTraceSpan = {
  id: 'span-1',
  name: 'Aurora SQL query',
  category: 'data',
  type: 'database',
  status: 'ok',
  latencyMs: 42,
  agent: 'SQLAgent',
  file: 'backend/agents/sql_01/agent.py',
  sql: 'SELECT * FROM trip_packages',
  details: 'Read trip packages from Aurora.',
  fields: [],
};

function makeState(overrides: Partial<MeridianShowcaseState> = {}): MeridianShowcaseState {
  return {
    tripHolds: [],
    bookingPrompt: null,
    budgetCeilingPerTravelerCents: 320000,
    travelersCount: 1,
    restoreJourney: vi.fn(),
    selectedPhase: 1,
    phaseLabel: 'SQL',
    phaseHint: null,
    dismissPhaseHint: vi.fn(),
    travelerId: 'traveler-demo',
    messages: [],
    currentPrompt: '',
    recommendations: [],
    catalog: [],
    selectedTrip: null,
    tripDetailsOpen: false,
    savedTrips: [],
    savedTripIds: new Set(),
    comparedTrips: [],
    comparisonOpen: false,
    memoryFacts: [],
    travelerProfile: null,
    previewFacts: [],
    previewProfile: null,
    memoryEnabled: false,
    memoryLoading: false,
    memoryToggleError: null,
    memoryMutationError: null,
    workspaceNotice: null,
    traceSpans: [traceSpan],
    traceTab: null,
    expandedSpanId: null,
    replayIndex: -1,
    isReplaying: false,
    isLoading: false,
    requestStartedAt: null,
    stopWaiting: vi.fn(),
    error: null,
    backendStatus: 'online',
    connectionIssue: null,
    connectionRefreshing: false,
    refreshConnection: vi.fn(),
    backendHealth: null,
    isFallbackMode: false,
    conversationId: null,
    workflowStatus: null,
    workflowResumedAfterRestart: false,
    lastPrompt: 'Show me city trips under $2,000 per traveler.',
    actionDrawer: null,
    modelLabel: 'Claude Sonnet 5',
    embedLabel: 'Cohere Embed v4',
    totalLatencyMs: 42,
    phaseExamples: [],
    chatFilters: {
      travelers: 0,
      startDate: null,
      endDate: null,
      spa: false,
      directFlights: false,
    },
    setChatFilters: vi.fn(),
    resetChatFilters: vi.fn(),
    setCurrentPrompt: vi.fn(),
    setTraceTab: vi.fn(),
    setExpandedSpanId: vi.fn(),
    setSelectedTrip: vi.fn(),
    setSelectedPhase: vi.fn(),
    setMemoryEnabled: vi.fn(),
    submitPrompt: vi.fn(),
    applyPhaseExample: vi.fn(),
    replayLastPrompt: vi.fn(),
    replayTrace: vi.fn(),
    selectTrip: vi.fn(),
    openTripDetails: vi.fn(),
    closeTripDetails: vi.fn(),
    holdTrip: vi.fn(),
    requestBookingConfirmation: vi.fn(),
    dismissBookingConfirmation: vi.fn(),
    confirmTrip: vi.fn(),
    adoptJourneyHold: vi.fn(),
    planTrip: vi.fn(),
    saveTrip: vi.fn(),
    compareTrip: vi.fn(),
    removeComparedTrip: vi.fn(),
    openComparison: vi.fn(),
    closeComparison: vi.fn(),
    updateMemoryPreference: vi.fn(),
    deleteMemoryPreference: vi.fn(),
    clearMemoryMutationError: vi.fn(),
    closeActionDrawer: vi.fn(),
    clearError: vi.fn(),
    clearChat: vi.fn(),
    latestStreamComplete: true,
    markLatestStreamComplete: vi.fn(),
    ...overrides,
  };
}

describe('TracePanel collapse behavior', () => {
  it('hides activity details while keeping the panel header actionable', () => {
    const onToggleCollapsed = vi.fn();
    const state = makeState();
    const { rerender } = render(
      <TracePanel
        state={state}
        collapsed={false}
        onToggleCollapsed={onToggleCollapsed}
      />,
    );

    expect(screen.getByText('Aurora SQL query', { selector: '.mds-activity-event summary span' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /collapse activity panel/i }));
    expect(onToggleCollapsed).toHaveBeenCalledTimes(1);

    rerender(
      <TracePanel
        state={state}
        collapsed
        onToggleCollapsed={onToggleCollapsed}
      />,
    );

    expect(screen.queryByText('Aurora SQL query')).not.toBeInTheDocument();
    expect(screen.getByText('1 event')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /expand activity panel/i })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
  });

  it('only marks capabilities represented by the completed trace', () => {
    render(<TracePanel state={makeState()} />);

    expect(screen.getByText('Querying live travel data').closest('li')).toHaveClass('is-done');
    expect(screen.queryByText('Recalling traveler context')).not.toBeInTheDocument();
    expect(screen.queryByText('Evaluating options')).not.toBeInTheDocument();
  });

  it('lands every step of a completed production turn', () => {
    // Regression: the runtime's opening and closing spans share the "runtime"
    // category, so the first step claimed both and "Evaluating options" could
    // never land on any Phase 4 turn. A finished turn showed a grey step
    // between green ones, reading as if the agent skipped a stage.
    const span = (id: string, category: string, name: string): ShowcaseTraceSpan => ({
      ...traceSpan, id, category, name, sql: undefined, type: 'tool_call',
    });
    const state = makeState({
      selectedPhase: 4,
      memoryEnabled: true,
      traceSpans: [
        span('s1', 'security', 'Workload traveler grant allowed'),
        span('s2', 'memory_long', 'Strands @tool recall_traveler_preferences'),
        span('s3', 'gateway', 'AgentCore Gateway · tools/call → semantic_trip_search'),
        span('s4', 'runtime', 'AgentCore Runtime · turn complete'),
        span('s5', 'synthesis', 'Memory-grounded reply ready'),
      ],
    });
    render(<TracePanel state={state} />);

    for (const label of [
      'Understanding request',
      'Recalling traveler context',
      'Querying live travel data',
      'Evaluating options',
      'Preparing response',
    ]) {
      expect(screen.getByText(label).closest('li')).toHaveClass('is-done');
    }
  });

  it('does not claim recall when the context switch is off', () => {
    render(<TracePanel state={makeState({ selectedPhase: 4, memoryEnabled: false,
      traceSpans: [{ ...traceSpan, category: 'memory_long', name: 'Traveler memory disabled for this run' }],
    })} />);
    expect(screen.queryByText('Recalling traveler context')).not.toBeInTheDocument();
  });

  it('does not credit a failed query or a checkpoint write as completed traveler recall', () => {
    render(<TracePanel state={makeState({ selectedPhase: 5, traceSpans: [
      { ...traceSpan, status: 'error' },
      { ...traceSpan, id: 'cp', category: 'memory_short', name: 'Checkpoint persisted', sql: undefined },
    ] })} />);
    expect(screen.getByText('Querying live travel data').closest('li')).toHaveClass('is-error');
    expect(screen.queryByText('Recalling traveler context')).not.toBeInTheDocument();
  });

  it('still credits the opening step to the runtime turn that started it', () => {
    const state = makeState({
      traceSpans: [{ ...traceSpan, id: 'r1', category: 'runtime', name: 'AgentCore Runtime · turn started', sql: undefined }],
    });
    render(<TracePanel state={state} />);
    expect(screen.getByText('Understanding request').closest('li')).toHaveClass('is-done');
    expect(screen.queryByText('Evaluating options')).not.toBeInTheDocument();
  });

  it('waits for the response without inventing live step progress', () => {
    render(<TracePanel state={makeState({ isLoading: true, traceSpans: [] })} />);
    expect(screen.getByRole('status')).toHaveTextContent('Activity appears with the response');
    expect(screen.queryByRole('list', { name: 'Recorded request steps' })).not.toBeInTheDocument();
  });

  it('keeps reached steps when replay revisits an earlier group and does not credit future spans', () => {
    const state = makeState({ selectedPhase: 4, memoryEnabled: true, isReplaying: true, replayIndex: 0,
      traceSpans: [
        { ...traceSpan, id: 'recall', category: 'memory_short', name: 'AgentCore Memory session restored', component: 'AgentCore Memory', sql: undefined },
        { ...traceSpan, id: 'query' },
        { ...traceSpan, id: 'start', category: 'runtime', name: 'AgentCore Runtime turn started', sql: undefined },
        { ...traceSpan, id: 'end', category: 'synthesis', name: 'Response ready', sql: undefined },
      ],
    });
    const { rerender } = render(<TracePanel state={state} />);
    expect(screen.getByText('Understanding request').closest('li')).toHaveClass('is-pending');
    expect(screen.getByText('Recalling traveler context').closest('li')).toHaveClass('is-active');
    expect(screen.getByText('Querying live travel data').closest('li')).toHaveClass('is-pending');
    rerender(<TracePanel state={{ ...state, replayIndex: 2 }} />);
    expect(screen.getByText('Understanding request').closest('li')).toHaveClass('is-active');
    expect(screen.getByText('Recalling traveler context').closest('li')).toHaveClass('is-done');
    expect(screen.getByText('Querying live travel data').closest('li')).toHaveClass('is-done');
    expect(screen.getByText('Preparing response').closest('li')).toHaveClass('is-pending');
  });

  it('identifies service sources from recorded spans without mislabeling AgentCore as a model call', () => {
    const { container } = render(<TracePanel state={makeState({ selectedPhase: 4, memoryEnabled: true,
      traceSpans: [
        { ...traceSpan, id: 'memory', category: 'memory_short', name: 'AgentCore Memory session restored', component: 'Bedrock AgentCore Memory', sql: undefined },
        traceSpan,
        { ...traceSpan, id: 'rank', category: 'model', name: 'Cohere rerank', sql: undefined },
      ],
    })} />);
    const recall = screen.getByText('Recalling traveler context').closest('li')!;
    expect(recall).toHaveTextContent('AgentCore');
    expect(recall.querySelector('summary')).not.toHaveTextContent('Bedrock');
    expect(container.querySelectorAll('.mds-thinking-service img')).toHaveLength(3);
    expect(screen.getByText('Querying live travel data').closest('li')).toHaveTextContent('Aurora');
    expect(screen.getByText('Evaluating options').closest('li')).toHaveTextContent('Bedrock');
  });

  it('opens directly to SQL without a redundant selector or empty overview', () => {
    render(<TracePanel state={makeState()} />);
    fireEvent.click(screen.getByText('Inspect evidence'));
    expect(screen.getByRole('heading', { name: 'SQL' })).toBeVisible();
    expect(screen.getByText('SELECT * FROM trip_packages', { selector: '.mds-sql-list pre' })).toBeVisible();
    expect(screen.queryByRole('button', { name: 'Overview' })).not.toBeInTheDocument();
    expect(screen.queryByRole('group', { name: 'Evidence views' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Memory' })).not.toBeInTheDocument();
  });

  it('hides the inspector when the turn has no supporting evidence', () => {
    render(<TracePanel state={makeState({ traceSpans: [{ ...traceSpan, sql: undefined }] })} />);
    expect(screen.queryByText('Inspect evidence')).not.toBeInTheDocument();
  });

  it('falls back to available evidence when a selected view disappears', () => {
    const state = makeState({ selectedPhase: 4, memoryEnabled: true, traceTab: 'memory',
      memoryFacts: [{ key: 'home_airport', value: 'JFK', source: 'profile' }],
    });
    const { rerender } = render(<TracePanel state={state} />);
    fireEvent.click(screen.getByText('Inspect evidence'));
    expect(screen.getByText('JFK')).toBeVisible();
    rerender(<TracePanel state={{ ...state, memoryFacts: [] }} />);
    expect(screen.queryByRole('button', { name: 'Memory' })).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'SQL' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('SELECT * FROM trip_packages', { selector: '.mds-sql-list pre' })).toBeVisible();
  });

  it('offers only recorded memory while keeping the live RLS probe deliberate', () => {
    const setTraceTab = vi.fn();
    render(<TracePanel state={makeState({ selectedPhase: 4, memoryEnabled: true,
      traceSpans: [], memoryFacts: [{ key: 'home_airport', value: 'JFK', source: 'profile' }], setTraceTab,
    })} />);
    fireEvent.click(screen.getByText('Inspect evidence'));
    expect(screen.getByRole('button', { name: 'Memory' })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByText('JFK')).toBeVisible();
    expect(screen.queryByRole('button', { name: 'SQL' })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'RLS probe' }));
    expect(setTraceTab).toHaveBeenCalledWith('rls');
  });

  it('does not run the RLS diagnostic merely by opening its view', () => {
    render(<TracePanel state={makeState({ selectedPhase: 4, traceSpans: [] })} />);
    fireEvent.click(screen.getByText('Inspect evidence'));
    expect(screen.getByRole('button', { name: 'Run RLS probe' })).toBeVisible();
    expect(screen.queryByText('Workload authorization + RLS · live')).not.toBeInTheDocument();
  });

  it('preserves the observed MCP contract as the initial evidence view', () => {
    render(<TracePanel state={makeState({ selectedPhase: 2, traceSpans: [{ ...traceSpan, name: 'postgres-mcp · run_query' }] })} />);
    fireEvent.click(screen.getByText('Inspect evidence'));
    expect(screen.getByRole('region', { name: 'MCP tool contract' })).toBeVisible();
    expect(screen.getByRole('button', { name: 'MCP tools' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('preserves the observed workflow state without an overview tab', () => {
    render(<TracePanel state={makeState({ selectedPhase: 5, traceSpans: [{ ...traceSpan, name: 'Workflow node: classify', sql: undefined }] })} />);
    fireEvent.click(screen.getByText('Inspect evidence'));
    expect(screen.getByRole('region', { name: 'Workflow state inspector' })).toBeVisible();
    expect(screen.queryByRole('group', { name: 'Evidence views' })).not.toBeInTheDocument();
  });

  it('renders a Cedar denial as denied by policy and links the CloudWatch trace', () => {
    const denied: ShowcaseTraceSpan = {
      id: 'span-deny',
      name: 'Hold refused by Cedar policy',
      category: 'security',
      type: 'security',
      status: 'denied',
      latencyMs: 210,
      agent: 'ProductionAgent',
      file: 'meridian_agentcore/app/MeridianConcierge/main.py',
      details: 'No Cedar policy permits this hold, so the gateway denied it by default.',
      fields: [
        { label: 'trace_console', value: 'https://us-east-1.console.aws.amazon.com/cloudwatch/home' },
        { label: 'tool', value: 'MeridianHolds___create_courtesy_hold', mono: true },
      ],
    };
    render(<TracePanel state={makeState({ traceSpans: [denied], expandedSpanId: 'span-deny' })} />);

    fireEvent.click(screen.getByText('Understanding request'));
    fireEvent.click(screen.getByText('Hold refused by Cedar policy'));
    expect(screen.getByText(/Denied by policy/)).toBeVisible();
    expect(screen.getByText('Hold refused by Cedar policy').closest('details')).toHaveClass('is-denied');
    const link = screen.getByRole('link', { name: 'Open in CloudWatch' });
    expect(link).toHaveAttribute('href', 'https://us-east-1.console.aws.amazon.com/cloudwatch/home');
    expect(link).toHaveAttribute('target', '_blank');
  });
  it('groups every Phase 3 event once, with searches and reranking under their actual responsibilities', () => {
    const events = [
      ['Processing with Hybrid (pgvector + tsvector) + Cohere Rerank via Strands Supervisor', 'model', 'RetrievalAgent'],
      ['RetrievalAgent invoked (Strands + Bedrock)', 'model', 'RetrievalAgent'],
      ['Supervisor processing search request', 'orchestration', 'RetrievalAgent'],
      ['Delegating to Search Agent', 'orchestration', 'RetrievalAgent'],
      ['Generating text embedding', 'data', 'SearchAgent'],
      ['Text embedding generated', 'data', 'SearchAgent'],
      ["Semantic search: 'quiet romantic wine-country retreat with private villa'", 'orchestration', 'SearchAgent'],
      ['Catalog card details hydrated', 'orchestration', 'SearchAgent'],
      ['Lexical candidates merged', 'orchestration', 'SearchAgent'],
      ['Cohere rerank applied', 'model', 'SearchAgent'],
      ['Search Agent completed', 'orchestration', 'RetrievalAgent'],
      ['Supervisor completed coordination', 'orchestration', 'RetrievalAgent'],
      ['Supervisor returned 5 trips', 'synthesis', 'RetrievalAgent'],
      ['Bedrock · concierge polish (global.anthropic.claude-sonnet-5)', 'model', 'RetrievalAgent'],
    ];
    const { container } = render(<TracePanel state={makeState({ selectedPhase: 3, traceSpans: events.map(([name, category, agent], index) => ({
      ...traceSpan, id: `retrieval-${index}`, name, category, agent, sql: undefined, latencyMs: null,
    })) })} />);
    expect(container.querySelectorAll('.mds-activity-event')).toHaveLength(14);
    expect(container.querySelectorAll('.mds-span-list')).toHaveLength(0);
    expect(container.querySelectorAll('.mds-activity-group[open]')).toHaveLength(0);
    const search = screen.getByText('Querying live travel data').closest('li')!;
    expect(search).toHaveTextContent('Aurora');
    expect(search).toHaveTextContent('Bedrock');
    for (const index of [4, 5, 6, 7, 8, 10]) expect(within(search).getByText(events[index][0])).toBeInTheDocument();
    expect(within(screen.getByText('Evaluating options').closest('li')!).getByText('Cohere rerank applied')).toBeInTheDocument();
    expect(within(screen.getByText('Preparing response').closest('li')!).getByText(events[13][0])).toBeInTheDocument();
    expect(screen.queryByText(/timing not recorded/)).not.toBeInTheDocument();
  });

  it('preserves unknown events and does not claim a group is complete with unconfirmed evidence', () => {
    render(<TracePanel state={makeState({ traceSpans: [traceSpan,
      { ...traceSpan, id: 'unknown', name: 'A new kind of evidence', category: 'new_category', type: 'new_type', status: 'error', sql: undefined },
      { ...traceSpan, id: 'uncertain', name: 'Aurora query status unknown', status: 'unknown' },
    ] })} />);
    expect(screen.getByText('Additional activity').closest('li')).toHaveClass('is-error');
    expect(screen.getByText('Querying live travel data').closest('li')).toHaveClass('is-unconfirmed');
    expect(screen.getByText('A new kind of evidence')).toBeInTheDocument();
  });

});
