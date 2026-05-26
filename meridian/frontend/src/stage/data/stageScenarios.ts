/**
 * Demo Stage scenario prompts — metadata only.
 *
 * Trace spans, recommendations, and assistant replies come from live
 * `POST /api/chat` responses via `traceAdapter.ts`.
 */
import { PHASE_EYEBROW } from '../../lib/phaseLabels';
import type { StageScenario } from '../types';

const PHASE_4_LABEL = PHASE_EYEBROW[4];

const GOVERNANCE = {
  scope: 'traveler_preferences · RLS-scoped',
  budgetCap: 'Per-traveler cap from Aurora',
  confirmation: 'Confirm-before-purchase',
  audit: 'agent_traces · append-only',
} as const;

export const STAGE_SCENARIOS: StageScenario[] = [
  {
    id: 'wine',
    phaseLabel: PHASE_4_LABEL,
    traceId: '',
    prompt: "A slow week somewhere we can drink good wine – Jordan can't do red-eyes.",
    assistantReply: '',
    reasoning: '',
    traveler: {
      id: 'trv_meridian_demo',
      name: 'Alex & Jordan Chen',
      initials: 'AJ',
      origin: 'BOS',
      budgetCapUsd: 3200,
      facts: [],
    },
    spans: [],
    recommendations: [],
    governance: GOVERNANCE,
  },
  {
    id: 'family',
    phaseLabel: PHASE_4_LABEL,
    traceId: '',
    prompt: 'Family beach trip under $2,500 — kids need shallow water and a kids club.',
    assistantReply: '',
    reasoning: '',
    traveler: {
      id: 'trv_meridian_demo',
      name: 'Alex & Jordan Chen',
      initials: 'AJ',
      origin: 'BOS',
      budgetCapUsd: 2500,
      facts: [],
    },
    spans: [],
    recommendations: [],
    governance: GOVERNANCE,
  },
  {
    id: 'business',
    phaseLabel: PHASE_4_LABEL,
    traceId: '',
    prompt: 'One-night business stopover in Singapore with lounge access and late checkout.',
    assistantReply: '',
    reasoning: '',
    traveler: {
      id: 'trv_meridian_demo',
      name: 'Alex & Jordan Chen',
      initials: 'AJ',
      origin: 'BOS',
      budgetCapUsd: 900,
      facts: [],
    },
    spans: [],
    recommendations: [],
    governance: GOVERNANCE,
  },
];

export const DEFAULT_SCENARIO_ID: StageScenario['id'] = 'wine';

export function getStageScenarioById(id: StageScenario['id']): StageScenario {
  return STAGE_SCENARIOS.find((s) => s.id === id) ?? STAGE_SCENARIOS[0];
}
