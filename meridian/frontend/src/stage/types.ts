/**
 * Demo Stage span model.
 *
 * The Demo Stage trace is intentionally narrower than the full
 * `ActivityEntry` telemetry — it's the "cinematic" shape audiences see on the
 * monitor. `traceAdapter.ts` is responsible for converting whatever the
 * backend returns into this shape.
 */
import type { Product } from '../types';

export type StageSpanKind =
  | 'orchestration'
  | 'memory'
  | 'tool'
  | 'data'
  | 'model'
  | 'synthesis'
  | 'security';

export type StageSystemId = 'orchestration' | 'memory' | 'mcp' | 'aurora' | 'model' | 'governance';

export interface StageSpan {
  id: string;
  kind: StageSpanKind;
  /** System highlighted while this span is active. */
  system: StageSystemId;
  name: string;
  detail?: string;
  latencyMs: number;
  /** Optional human label for status (e.g. "ok", "cache hit", "held"). */
  status?: string;
  /** Optional component/agent that emitted this span. */
  component?: string;
  /** Optional source (SQL, tool name, model). */
  source?: string;
  /** Optional SQL/tool input we want to show in the inspector. */
  input?: string;
  /** Optional output preview for the inspector. */
  output?: string;
  /** Optional dollar cost for this span. */
  costUsd?: number;
  /** Optional input/output token counts for model spans. */
  tokensIn?: number;
  tokensOut?: number;
}

export interface StageRecommendation {
  id: string;
  title: string;
  region: string;
  nights: number;
  matchPct: number;
  priceUsd: number;
  rationale: string[];
  hero?: 'wine' | 'beach' | 'river' | 'mountain' | 'city';
  primary?: boolean;
  /** The live Product, carried through so the deck can render the same
   *  full-bleed TripVisual photo card the showcase uses. */
  product?: Product;
}

export interface StageTraveler {
  id: string;
  name: string;
  initials: string;
  origin: string;
  budgetCapUsd: number;
  facts: string[];
}

export interface StageScenario {
  id: 'tokyo' | 'recall' | 'plan';
  phaseLabel: string;
  traceId: string;
  prompt: string;
  /** Natural-language reply the concierge ends up composing. */
  assistantReply: string;
  /** One-line provenance summary, e.g. "supervisor.plan → memory.recall → trips.hybrid_search → claude.compose". */
  reasoning: string;
  traveler: StageTraveler;
  spans: StageSpan[];
  recommendations: StageRecommendation[];
  governance: {
    scope: string;
    budgetCap: string;
    confirmation: string;
    audit: string;
  };
}

export type StageView = 'audience' | 'builder';
