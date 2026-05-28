/**
 * Shared phase names — keep kiosk, workspace, and marketing sections aligned.
 */
import type { Phase } from '../types';

/** Section eyebrows and demo-stage live pill. */
export const PHASE_EYEBROW: Record<Phase, string> = {
  1: 'SQL',
  2: 'MCP',
  3: 'Retrieval',
  4: 'Production',
  5: 'Workflow',
};

/** Journey rail subtitle (zero-padded). */
export const PHASE_JOURNEY_SUB: Record<Phase, string> = {
  1: 'SQL · Filters',
  2: 'MCP · Tool protocol',
  3: 'Retrieval · Intent',
  4: 'Production · AgentCore',
  5: 'Workflow · LangGraph',
};

/** Workspace Run config “Mode” row. */
export const PHASE_AGENT_MODE: Record<Phase, string> = {
  1: 'SQL Agent',
  2: 'MCP Agent',
  3: 'Retrieval Agent',
  4: 'Production Agent',
  5: 'Workflow Agent',
};

/** Short pill label in the workspace top bar. */
export const PHASE_PILL: Record<Phase, string> = {
  1: 'SQL',
  2: 'MCP',
  3: 'Retrieval',
  4: 'Production',
  5: 'Workflow',
};

/** One-line stack summary for Phase 4 tooltips / subtitles. */
export const PHASE_4_STACK =
  'AgentCore Runtime · Gateway · Memory · Aurora RLS';
