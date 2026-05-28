/**
 * Demo Stage scenario prompts — metadata only.
 *
 * Trace spans, recommendations, and assistant replies come from live
 * `POST /api/chat` responses via `traceAdapter.ts`. The three prompts
 * below mirror the Phase 4 (Production) pills in /showcase EXACTLY and
 * are ordered to tell the same Tokyo storyline arc — so the kiosk and
 * the showcase narrate one consistent demo:
 *
 *   1. tokyo   — concrete Tokyo culture query that persists into Aurora
 *                via AgentCore Memory; seeds the thread the recall picks
 *                up. Matches showcase Production pill #1.
 *   2. recall  — "what did we discuss last time" — depends on the prior
 *                Tokyo turn being in conversation_messages, so it only
 *                lands AFTER scenario 1. Matches showcase pill #2.
 *   3. plan    — multi-intent Tokyo prompt (find dates + Marriott pick +
 *                Kyoto hold) that Strands chains implicitly in one
 *                Bedrock turn; motivates the upgrade to Phase 5
 *                LangGraph. Matches showcase pill #3.
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

const ALEX_TRAVELER = {
  id: 'trv_meridian_demo',
  name: 'Alex Morgan',
  initials: 'AM',
  origin: 'BOS',
  facts: [],
};

export const STAGE_SCENARIOS: StageScenario[] = [
  {
    id: 'tokyo',
    phaseLabel: PHASE_4_LABEL,
    traceId: '',
    prompt: 'Tokyo culture trip for two — boutique stays, local food, walkable neighborhoods',
    assistantReply: '',
    reasoning: '',
    traveler: { ...ALEX_TRAVELER, budgetCapUsd: 4500 },
    spans: [],
    recommendations: [],
    governance: GOVERNANCE,
  },
  {
    id: 'recall',
    phaseLabel: PHASE_4_LABEL,
    traceId: '',
    prompt: 'What did we discuss last time? Pick up where we left off.',
    assistantReply: '',
    reasoning: '',
    traveler: { ...ALEX_TRAVELER, budgetCapUsd: 3200 },
    spans: [],
    recommendations: [],
    governance: GOVERNANCE,
  },
  {
    id: 'plan',
    phaseLabel: PHASE_4_LABEL,
    traceId: '',
    prompt:
      'Plan our October Tokyo trip — find open dates, pick a Marriott property, and hold a Kyoto side trip',
    assistantReply: '',
    reasoning: '',
    traveler: { ...ALEX_TRAVELER, budgetCapUsd: 4500 },
    spans: [],
    recommendations: [],
    governance: GOVERNANCE,
  },
];

export const DEFAULT_SCENARIO_ID: StageScenario['id'] = 'tokyo';

export function getStageScenarioById(id: StageScenario['id']): StageScenario {
  return STAGE_SCENARIOS.find((s) => s.id === id) ?? STAGE_SCENARIOS[0];
}
