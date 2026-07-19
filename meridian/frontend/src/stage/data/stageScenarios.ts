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
 *   2. recall  — "what did we decide last time" — depends on the prior
 *                Tokyo turn being in conversation_messages, so it only
 *                lands AFTER scenario 1. Matches showcase pill #2.
 *   3. plan    — two-step Kyoto extension prompt (find + availability)
 *                that needs explicit, checkpointed control flow in Phase 5
 *                LangGraph. Matches showcase pill #3.
 */
import { PHASE_EYEBROW } from '../../lib/phaseLabels';
import type { StageScenario } from '../types';

const PHASE_4_LABEL = PHASE_EYEBROW[4];

// Governance values shown in the SystemProofRail. Worded to match what
// the backend ACTUALLY does today vs. what's the documented pattern:
//   - scope:  REAL — scoped_session() sets the app.current_traveler_id
//             GUC in a transaction (backend/db/rds_data_client.py).
//   - rls:    PATTERN — RLS policies live in examples/rls_for_agents.sql;
//             the GUC is set so policies WOULD filter. Labeled honestly.
//   - identity: REAL — AgentCore Identity resolves the IAM/workload
//             principal per turn (concierge.py).
//   - audit:  REAL — every turn's spans persist to the trace store.
const GOVERNANCE = {
  scope: 'set_config(app.current_traveler_id)',
  budgetCap: 'RLS policy pattern · examples/rls_for_agents.sql',
  confirmation: 'AgentCore Identity · IAM principal',
  audit: 'per-turn trace spans',
} as const;

const ALEX_TRAVELER = {
  id: 'trv_meridian_demo',
  name: 'Alex Morgan',
  initials: 'AM',
  origin: 'JFK',
  facts: [],
};

export const STAGE_SCENARIOS: StageScenario[] = [
  {
    id: 'tokyo',
    phaseLabel: PHASE_4_LABEL,
    traceId: '',
    prompt: 'Find a Tokyo culture trip for two with boutique stays, local food, and walkable neighborhoods.',
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
    prompt: 'What did we decide about my October Tokyo trip last time? Continue from there.',
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
    prompt: 'Plan the Kyoto extension: find matching packages, then verify available duration options.',
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
