import { describe, expect, it } from 'vitest';
import { SHOWCASE_EXAMPLE_PROMPTS } from '../showcaseAdapters';

describe('SHOWCASE_EXAMPLE_PROMPTS phase ladder', () => {
  it('uses the SQL failure to tee up custom MCP tools', () => {
    const sqlBreak = SHOWCASE_EXAMPLE_PROMPTS[1][2].toLowerCase();
    expect(sqlBreak).toContain('compare');
    expect(sqlBreak).toContain('eur');
    expect(SHOWCASE_EXAMPLE_PROMPTS[2][0]).toBe(SHOWCASE_EXAMPLE_PROMPTS[1][2]);
  });

  it('uses the MCP failure to tee up retrieval intent matching', () => {
    const mcpBreak = SHOWCASE_EXAMPLE_PROMPTS[2][2].toLowerCase();
    expect(mcpBreak).toContain('romantic');
    expect(mcpBreak).toContain('wine');
    expect(SHOWCASE_EXAMPLE_PROMPTS[3][0]).toBe(SHOWCASE_EXAMPLE_PROMPTS[2][2]);
  });

  it('uses the retrieval failure to tee up production memory', () => {
    const retrievalBreak = SHOWCASE_EXAMPLE_PROMPTS[3][2].toLowerCase();
    expect(retrievalBreak).toContain('last time');
    expect(SHOWCASE_EXAMPLE_PROMPTS[4][1]).toBe(SHOWCASE_EXAMPLE_PROMPTS[3][2]);
  });

  it('uses the Production failure unchanged as the Workflow plan success', () => {
    expect(SHOWCASE_EXAMPLE_PROMPTS[5][2]).toBe(SHOWCASE_EXAMPLE_PROMPTS[4][2]);
  });

  it('ends with distinct workflow branches that demonstrate durable orchestration', () => {
    expect(SHOWCASE_EXAMPLE_PROMPTS[5][0].toLowerCase()).toContain('duration options');
    expect(SHOWCASE_EXAMPLE_PROMPTS[5][1].toLowerCase()).toContain('last time');
    // The plan branch is the flight-disruption replan: a re-search step plus a
    // departure-availability step, the two dependent operations Phase 5 owns.
    const planBreak = SHOWCASE_EXAMPLE_PROMPTS[5][2].toLowerCase();
    expect(planBreak).toContain('cancelled');
    expect(planBreak).toContain('departures');
  });

  it('uses plain traveler language and explicit units', () => {
    expect(SHOWCASE_EXAMPLE_PROMPTS[1][0]).toContain('$2,000 per traveler');
    expect(SHOWCASE_EXAMPLE_PROMPTS[1][0].toLowerCase()).not.toContain('city break');
    expect(SHOWCASE_EXAMPLE_PROMPTS[2][1].toLowerCase()).toContain('november');
  });
});
