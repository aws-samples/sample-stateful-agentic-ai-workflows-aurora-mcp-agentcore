import { describe, expect, it } from 'vitest';
import {
  SHOWCASE_EXAMPLE_PROMPTS,
  genericizeLoyaltyText,
  healthResponseToStatus,
  showcasePromptLabel,
} from '../showcaseAdapters';

describe('SHOWCASE_EXAMPLE_PROMPTS phase ladder', () => {
  it('uses the SQL failure to tee up custom MCP tools', () => {
    const sqlBreak = SHOWCASE_EXAMPLE_PROMPTS[1][2].toLowerCase();
    expect(sqlBreak).toContain('compare');
    expect(sqlBreak).toContain('euros');
    expect(SHOWCASE_EXAMPLE_PROMPTS[2][0]).toBe(SHOWCASE_EXAMPLE_PROMPTS[1][2]);
  });

  it('uses the MCP failure to tee up retrieval intent matching', () => {
    const mcpBreak = SHOWCASE_EXAMPLE_PROMPTS[2][2].toLowerCase();
    expect(mcpBreak).toContain('romantic');
    expect(mcpBreak).toContain('wine');
    expect(mcpBreak).not.toContain('wellness');
    expect(SHOWCASE_EXAMPLE_PROMPTS[3][0]).toBe(SHOWCASE_EXAMPLE_PROMPTS[2][2]);
  });

  it('uses the retrieval failure to tee up production memory', () => {
    const retrievalBreak = SHOWCASE_EXAMPLE_PROMPTS[3][2].toLowerCase();
    expect(retrievalBreak).toContain('recall');
    expect(retrievalBreak).toContain('saved preferences');
    expect(SHOWCASE_EXAMPLE_PROMPTS[4][1]).toBe(SHOWCASE_EXAMPLE_PROMPTS[3][2]);
  });

  it('uses the Production failure unchanged as the Workflow plan success', () => {
    expect(SHOWCASE_EXAMPLE_PROMPTS[5][2]).toBe(SHOWCASE_EXAMPLE_PROMPTS[4][2]);
  });

  it('ends with distinct workflow branches that demonstrate durable orchestration', () => {
    expect(SHOWCASE_EXAMPLE_PROMPTS[5][0].toLowerCase()).toContain('trip lengths');
    expect(SHOWCASE_EXAMPLE_PROMPTS[5][1].toLowerCase()).toContain('recall');
    // The plan branch is the flight-disruption replan: a re-search step plus a
    // departure-availability step, the two dependent operations Phase 5 owns.
    const planBreak = SHOWCASE_EXAMPLE_PROMPTS[5][2].toLowerCase();
    expect(planBreak).toContain('cancelled');
    expect(planBreak).toContain('duration availability');
    expect(planBreak).toContain('best three');
  });

  it('uses plain traveler language and explicit units', () => {
    expect(SHOWCASE_EXAMPLE_PROMPTS[1][0]).toContain('$2,000 per traveler');
    expect(SHOWCASE_EXAMPLE_PROMPTS[1][0].toLowerCase()).not.toContain('city break');
    expect(SHOWCASE_EXAMPLE_PROMPTS[2][1].toLowerCase()).toContain('november');
  });

  it('uses concise projector labels without changing submitted prompts', () => {
    expect(showcasePromptLabel(SHOWCASE_EXAMPLE_PROMPTS[4][1])).toBe(
      'Recall my Tokyo plan',
    );
    expect(showcasePromptLabel(SHOWCASE_EXAMPLE_PROMPTS[5][2])).toBe(
      'Cancelled flight replan',
    );
  });
});

describe('healthResponseToStatus', () => {
  it('only accepts the Meridian backend health contract', () => {
    expect(
      healthResponseToStatus({
        status: 'healthy',
        bedrock_model_id: 'global.anthropic.claude-sonnet-5',
        embedding_model_id: 'cohere.embed-v4:0',
        checkpoint_backend: 'postgres',
      }),
    ).toBe('online');

    expect(
      healthResponseToStatus({
        status: 'ok',
        service: 'another-local-service',
      }),
    ).toBe('offline');
  });
});

describe('generic loyalty labels', () => {
  it('removes real loyalty brands from showcase-facing text', () => {
    expect(
      genericizeLoyaltyText(
        'Marriott Bonvoy Platinum Elite; United MileagePlus Premier 1K',
      ),
    ).toBe('Hotel Platinum; Airline Premier');
    expect(genericizeLoyaltyText('United MileagePlus account')).toBe(
      'airline loyalty account',
    );
  });
});
