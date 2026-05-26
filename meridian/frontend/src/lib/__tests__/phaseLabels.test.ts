import { describe, expect, it } from 'vitest';
import { PHASE_AGENT_MODE, PHASE_EYEBROW, PHASE_JOURNEY_SUB, PHASE_PILL } from '../phaseLabels';

describe('phaseLabels', () => {
  it('phase 4 is Production (AgentCore stack), not Personal or Memory-only', () => {
    expect(PHASE_EYEBROW[4]).toBe('Phase 4 · Production');
    expect(PHASE_JOURNEY_SUB[4]).toBe('Phase 04 · Production');
    expect(PHASE_PILL[4]).toBe('Production');
    expect(PHASE_EYEBROW[4]).not.toContain('Personal');
    expect(PHASE_AGENT_MODE[4]).toBe('Production Agent');
  });
});
