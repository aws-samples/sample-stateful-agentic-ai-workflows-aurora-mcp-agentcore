import { describe, expect, it } from 'vitest';

const sources = import.meta.glob('../../**/*.{ts,tsx,css}', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>;

// spanTitles.ts is the one place that still names the wording saved journeys were stored with.
const LEGACY_TABLE = '../spanTitles.ts';
const BANNED = [/LangGraph/i, /·/];

const isTest = (path: string) => /__tests__|\.test\.[tj]sx?$/.test(path);

function lineIsCommentThatSaysWhy(line: string): boolean {
  return /^\s*(\/\/|\/\*|\*)/.test(line) && /legacy|saved journeys|earlier/i.test(line);
}

describe('showcase copy', () => {
  it('names no LangGraph and uses no middle dot outside the legacy-title table', () => {
    const files = Object.keys(sources).filter(path => !isTest(path) && path !== LEGACY_TABLE);
    expect(files.length).toBeGreaterThan(50);
    const offences = files.flatMap(path => sources[path].split('\n')
      .map((line, index) => ({ line, at: `${path.replace('../../', '')}:${index + 1}` }))
      .filter(({ line }) => !lineIsCommentThatSaysWhy(line)
        && BANNED.some(pattern => pattern.test(line)))
      .map(({ line, at }) => `${at} ${line.trim().slice(0, 100)}`));
    expect(offences).toEqual([]);
  });
});
