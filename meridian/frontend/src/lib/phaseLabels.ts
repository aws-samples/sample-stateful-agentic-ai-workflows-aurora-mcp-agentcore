/**
 * Shared phase names — keep kiosk, workspace, and marketing sections aligned.
 */
import type { Phase } from '../types';

/** Section eyebrows and demo-stage live pill (e.g. "Phase 4 · Production"). */
export const PHASE_EYEBROW: Record<Phase, string> = {
  1: 'Phase 1 · SQL',
  2: 'Phase 2 · MCP',
  3: 'Phase 3 · Retrieval',
  4: 'Phase 4 · Production',
  5: 'Phase 5 · Orchestration',
};

/** Journey rail subtitle (zero-padded). */
export const PHASE_JOURNEY_SUB: Record<Phase, string> = {
  1: 'Phase 01 · Filters',
  2: 'Phase 02 · MCP',
  3: 'Phase 03 · Intent',
  4: 'Phase 04 · Production',
  5: 'Phase 05 · Orchestration',
};

/** Workspace Run config “Mode” row. */
export const PHASE_AGENT_MODE: Record<Phase, string> = {
  1: 'SQL Agent',
  2: 'MCP Agent',
  3: 'Retrieval Agent',
  4: 'Production Agent',
  5: 'Orchestration Agent',
};

/** Short pill label in the workspace top bar. */
export const PHASE_PILL: Record<Phase, string> = {
  1: 'SQL',
  2: 'MCP',
  3: 'Retrieval',
  4: 'Production',
  5: 'Orchestration',
};

/** One-line stack summary for Phase 4 tooltips / subtitles. */
export const PHASE_4_STACK =
  'AgentCore Runtime · Gateway · Memory · Aurora RLS';
