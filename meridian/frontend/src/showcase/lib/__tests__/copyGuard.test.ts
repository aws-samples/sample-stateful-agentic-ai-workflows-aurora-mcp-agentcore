import { describe, expect, it } from 'vitest';

const globs = {
  ...import.meta.glob('../../../**/*.{ts,tsx,css}', { query: '?raw', import: 'default', eager: true }),
  ...import.meta.glob('../../../../e2e/**/*.ts', { query: '?raw', import: 'default', eager: true }),
  ...import.meta.glob('../../../../index.html', { query: '?raw', import: 'default', eager: true }),
};
const sources = globs as Record<string, string>;

// spanTitles.ts is the one place that still names the wording saved journeys were stored with.
const LEGACY_TABLE = '../spanTitles.ts';
const BANNED = [
  /LangGraph/i, /\u00b7/, /&middot;/i, /&#183;/, /&#xb7;/i,
  /\\00b7/i, // CSS escape
  /\\u00b7/i, /\\u\{b7\}/i, /\\xb7/i, // JS string escapes
];

// Words people read. Comments are not copy, so a comment may still say what it needs to.
const USER_FACING_BANNED = [/\u2014/, /&mdash;/i, /\\u2014/i, /\bdemo\b/i];
const isComment = (line: string) => /^\s*(\/\/|\/\*|\*)/.test(line);

const isTest = (path: string) => /__tests__|\.test\.[tj]sx?$|\.spec\.ts$/.test(path);

function lineIsCommentThatSaysWhy(line: string): boolean {
  return /^\s*(\/\/|\/\*|\*)/.test(line) && /legacy|saved journeys/i.test(line);
}

const shortName = (path: string) => path.replace(/^(\.\.\/)+/, '');

describe('showcase copy', () => {
  it('names no LangGraph and uses no middle dot outside the legacy-title table', () => {
    const files = Object.keys(sources).filter(path => !isTest(path) && path !== LEGACY_TABLE);
    expect(files.length).toBeGreaterThan(50);
    expect(files.some(path => path.endsWith('index.html'))).toBe(true);
    const offences = files.flatMap(path => sources[path].split('\n')
      .map((line, index) => ({ line, at: `${shortName(path)}:${index + 1}` }))
      .filter(({ line }) => !lineIsCommentThatSaysWhy(line)
        && BANNED.some(pattern => pattern.test(line)))
      .map(({ line, at }) => `${at} ${line.trim().slice(0, 100)}`));
    expect(offences).toEqual([]);
  });

  it('uses no em dash and never says demo in the words people read', () => {
    const copy = Object.keys(sources).filter(
      path => /\.tsx?$|index\.html$/.test(path) && !isTest(path) && path !== LEGACY_TABLE,
    );
    expect(copy.length).toBeGreaterThan(50);
    const offences = copy.flatMap(path => sources[path].split('\n')
      .map((line, index) => ({ line, at: `${shortName(path)}:${index + 1}` }))
      .filter(({ line }) => !isComment(line)
        && USER_FACING_BANNED.some(pattern => pattern.test(line)))
      .map(({ line, at }) => `${at} ${line.trim().slice(0, 100)}`));
    expect(offences).toEqual([]);
  });

  it('keeps the e2e fixtures free of the same wording', () => {
    const fixtures = Object.keys(sources).filter(path => /\/e2e\/fixtures\//.test(path));
    expect(fixtures.length).toBeGreaterThan(0);
    const offences = fixtures.filter(path => BANNED.some(pattern => pattern.test(sources[path])));
    expect(offences).toEqual([]);
  });
});
