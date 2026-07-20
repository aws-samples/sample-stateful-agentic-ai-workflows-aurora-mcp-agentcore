import { describe, expect, it } from 'vitest';
import { splitMemoryPhrases } from '../memoryHighlight';

function marks(value: string): string[] {
  const parts = splitMemoryPhrases(value);
  if (!parts) return [];
  return parts
    .filter((p) => p.type === 'element' && p.tagName === 'mark')
    .map((p) => (p.children?.[0]?.value ?? '') as string);
}

describe('splitMemoryPhrases', () => {
  it('marks the recalled facts in a realistic Production reply', () => {
    const reply =
      'For your Tokyo Oct 12-19 window I kept it to your party of two out of ' +
      'JFK with no red-eyes, boutique-over-chain stays, and flagged your ' +
      'shellfish allergy on dining.';
    const found = marks(reply).map((m) => m.toLowerCase());

    expect(found).toContain('oct 12-19');
    expect(found).toContain('party of two');
    expect(found).toContain('jfk');
    expect(found).toContain('no red-eyes');
    expect(found).toContain('boutique-over-chain');
    expect(found).toContain('shellfish allergy');
  });

  it('prefers the specific phrase over its shorter prefix', () => {
    // "shellfish allergy" should win as one mark, not split into "shellfish".
    expect(marks('noted your shellfish allergy')).toEqual(['shellfish allergy']);
    expect(marks('boutique-over-chain lodging')).toEqual(['boutique-over-chain']);
  });

  it('returns null when nothing memory-sourced appears', () => {
    expect(splitMemoryPhrases('Here are three calm options under budget.')).toBeNull();
  });

  it('matches JFK only as a whole word', () => {
    expect(marks('depart JFK on Friday')).toEqual(['JFK']);
    expect(splitMemoryPhrases('the JFKennedy museum')).toBeNull();
  });

  it('wraps matches in mark element nodes and preserves surrounding text', () => {
    const parts = splitMemoryPhrases('noted your shellfish allergy on dining');
    expect(parts).not.toBeNull();
    expect(parts![0]).toEqual({ type: 'text', value: 'noted your ' });
    expect(parts![1]).toMatchObject({
      type: 'element',
      tagName: 'mark',
      properties: { className: ['mds-memory-highlight'] },
    });
    expect(parts![2]).toEqual({ type: 'text', value: ' on dining' });
  });
});
