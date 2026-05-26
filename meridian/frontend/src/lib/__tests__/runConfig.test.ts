import { describe, expect, it } from 'vitest';
import { phaseUsesLlm, runConfigModelLabel, runConfigEmbedLabel } from '../runConfig';

describe('runConfig', () => {
  it('phases 1–2 do not use an LLM on the live chat path', () => {
    expect(phaseUsesLlm(1)).toBe(false);
    expect(phaseUsesLlm(2)).toBe(false);
    expect(phaseUsesLlm(3)).toBe(true);
  });

  it('shows no LLM for SQL and MCP phases', () => {
    expect(runConfigModelLabel(1)).toContain('None');
    expect(runConfigModelLabel(2)).toContain('MCP');
  });

  it('shows bedrock label from health for phase 3+', () => {
    expect(
      runConfigModelLabel(4, {
        status: 'healthy',
        bedrock_model_label: 'Claude Opus 4.7',
      }),
    ).toBe('Claude Opus 4.7');
  });

  it('embed label only for retrieval phases', () => {
    expect(runConfigEmbedLabel(1)).toBe('—');
    expect(runConfigEmbedLabel(3, { status: 'healthy', embedding_model_id: 'cohere.embed-v4:0' })).toBe(
      'Cohere Embed v4',
    );
  });
});
