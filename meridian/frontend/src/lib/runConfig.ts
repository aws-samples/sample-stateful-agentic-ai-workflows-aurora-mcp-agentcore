/**
 * Run config labels for the workspace sidebar (aligned with backend chat routing).
 */
import type { Phase } from '../types';

export interface BackendHealth {
  status: string;
  bedrock_model_id?: string;
  bedrock_model_label?: string;
  embedding_model_id?: string;
  checkpoint_backend?: string;
  checkpoint_durable?: boolean;
  checkpoint_required?: boolean;
}

/** Phases 1–2: procedural SQL/MCP only — no Bedrock LLM on the live /api/chat path. */
export function phaseUsesLlm(phase: Phase): boolean {
  return phase >= 3;
}

export function runConfigModelLabel(phase: Phase, health?: BackendHealth | null): string {
  if (!phaseUsesLlm(phase)) {
    return phase === 1 ? 'None · RDS Data API' : 'None · MCP tools';
  }
  return health?.bedrock_model_label ?? 'Claude via Bedrock';
}

export function runConfigEmbedLabel(phase: Phase, health?: BackendHealth | null): string {
  if (phase < 3) return '—';
  const id = health?.embedding_model_id ?? 'cohere.embed-v4:0';
  if (id.includes('embed-v4')) return 'Cohere Embed v4';
  if (id.includes('titan')) return 'Amazon Titan Embed';
  return id;
}
