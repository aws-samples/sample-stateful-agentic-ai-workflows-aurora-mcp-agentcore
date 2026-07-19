import { describe, expect, it } from 'vitest';
import { typewriterCadence } from '../../lib/streamingCadence';

describe('typewriterCadence', () => {
  it('keeps normal concierge replies under three seconds', () => {
    const cadence = typewriterCadence(600);

    expect(cadence.charsPerStep).toBeGreaterThanOrEqual(5);
    expect(cadence.naturalDurationMs).toBeLessThanOrEqual(2600);
  });

  it('bounds long replies without revealing them in one block', () => {
    const cadence = typewriterCadence(2400);

    expect(cadence.charsPerStep).toBeGreaterThan(10);
    expect(cadence.naturalDurationMs).toBeLessThanOrEqual(3300);
    expect(cadence.naturalDurationMs).toBeGreaterThan(2500);
  });
});
