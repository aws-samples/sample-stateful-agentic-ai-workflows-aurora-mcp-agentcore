import { fireEvent, render, screen } from '@testing-library/react';
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
    selectedPhase: 1,
    phaseLabel: 'SQL',
    phaseHint: null,
    dismissPhaseHint: vi.fn(),
    travelerId: 'traveler-demo',
    messages: [],
    currentPrompt: '',
    recommendations: [],
    selectedTrip: null,
    tripDetailsOpen: false,
    savedTrips: [],
    savedTripIds: new Set(),
    comparedTrips: [],
    comparisonOpen: false,
    memoryFacts: [],
    travelerProfile: null,
    memoryMutationError: null,
    workspaceNotice: null,
    traceSpans: [traceSpan],
    traceTab: 'spans',
    expandedSpanId: null,
    replayIndex: -1,
    isReplaying: false,
    isLoading: false,
    error: null,
    backendStatus: 'online',
    backendHealth: null,
    isFallbackMode: false,
    conversationId: null,
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
    submitPrompt: vi.fn(),
    applyPhaseExample: vi.fn(),
    replayLastPrompt: vi.fn(),
    replayTrace: vi.fn(),
    selectTrip: vi.fn(),
    openTripDetails: vi.fn(),
    closeTripDetails: vi.fn(),
    holdTrip: vi.fn(),
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

    expect(screen.getByText('Aurora SQL query')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /collapse meridian activity panel/i }));
    expect(onToggleCollapsed).toHaveBeenCalledTimes(1);

    rerender(
      <TracePanel
        state={state}
        collapsed
        onToggleCollapsed={onToggleCollapsed}
      />,
    );

    expect(screen.queryByText('Aurora SQL query')).not.toBeInTheDocument();
    expect(screen.getByText('1 spans')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /expand meridian activity panel/i })).toHaveAttribute(
      'aria-expanded',
      'false',
    );
  });
});
