import { vi } from 'vitest';
import { EMPTY_FILTERS, type MeridianShowcaseState } from '../../hooks/useMeridianShowcase';
import { SHOWCASE_EXAMPLE_PROMPTS } from '../../lib/showcaseAdapters';
import { JORDAN_IDENTITY } from '../../../test/signedIn';

/** A showcase state with every field a surface reads, for rendering a surface on its own. */
export function makeState(
  overrides: Partial<MeridianShowcaseState> = {},
): MeridianShowcaseState {
  return {
    traveler: JORDAN_IDENTITY,
    tripHolds: [],
    travelersCount: overrides.chatFilters?.travelers || 2,
    restoreJourney: vi.fn(),
    awaitingWorkflow: false,
    settleWorkflow: vi.fn(),
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
    workflowPlanPackageId: null,
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
