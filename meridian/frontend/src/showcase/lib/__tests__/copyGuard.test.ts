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
const USER_FACING_BANNED = [
  /\u2014/, /&mdash;/i, /\\u2014/i, /&#8212;/, /&#x2014;/i, /\u2013/, /&ndash;/i, /&#8211;/,
  /&#x2013;/i, /\\u2013/i, /\bdemo\b/i,
];
const isComment = (line: string) => /^\s*(\/\/|\/\*|\*)/.test(line);

const isTest = (path: string) => /__tests__|\.test\.[tj]sx?$|\.spec\.ts$/.test(path);

function lineIsCommentThatSaysWhy(line: string): boolean {
  return /^\s*(\/\/|\/\*|\*)/.test(line) && /legacy|saved journeys/i.test(line);
}

const shortName = (path: string) => path.replace(/^(\.\.\/)+/, '');

const isPageCode = (path: string) => /\.tsx?$/.test(path) && !isTest(path)
  && !/\.\.\/test\//.test(path) && !/\/e2e\//.test(path);

const NAMED_TRAVELER = [
  /trv_meridian_demo/, /\bJordan\b/, /SHOWCASE_TRAVELER_ID/, /lib\/personas/,
];

// How callers are authenticated differs by release, so the screens say what holds in both.
const AUTH_CLAIMS = [
  /IAM[- ]signed/i, /\bSigV4\b/i, /\bAWS IAM\b/i, /AWS credentials/i, /shared[- ]token/i,
  /\bBasic (auth|credential)/i, /Workload identity/i, /Authenticate the workload/i,
  /Authenticated workload/i, /Workload authorization/i,
];

function namedTravelerOffences(files: Record<string, string>, paths: string[]): string[] {
  return paths.flatMap(path => files[path].split('\n')
    .map((line, index) => ({ line, at: `${shortName(path)}:${index + 1}` }))
    .filter(({ line }) => !isComment(line) && NAMED_TRAVELER.some(pattern => pattern.test(line)))
    .map(({ line, at }) => `${at} ${line.trim().slice(0, 100)}`));
}

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

  it('names no particular traveler in the page code; the API says who is signed in', () => {
    const files = Object.keys(sources).filter(path => isPageCode(path));
    expect(files.length).toBeGreaterThan(50);
    expect(namedTravelerOffences(sources, files)).toEqual([]);
  });

  it('makes no claim about IAM, SigV4, shared tokens or workload identity in the page code', () => {
    const files = Object.keys(sources).filter(path => isPageCode(path));
    expect(files.length).toBeGreaterThan(50);
    const offences = files.flatMap(path => sources[path].split('\n')
      .map((line, index) => ({ line, at: `${shortName(path)}:${index + 1}` }))
      .filter(({ line }) => !isComment(line) && AUTH_CLAIMS.some(pattern => pattern.test(line)))
      .map(({ line, at }) => `${at} ${line.trim().slice(0, 100)}`));
    expect(offences).toEqual([]);
  });

  it('flags a planted first name in page code, and ignores tests and comments', () => {
    const planted: Record<string, string> = {
      '../../components/Scratch.tsx': "export const hello = 'Welcome back, Jordan.';\n",
      '../../components/Possessive.tsx': "export const t = `Jordan's trip`;\n",
      '../../components/Quiet.tsx': "// Jordan used to be named here.\nexport const t = 'Hello.';\n",
      '../../components/__tests__/Scratch.test.tsx': "it('Jordan', () => {});\n",
    };
    const files = Object.keys(planted).filter(path => isPageCode(path));
    expect(namedTravelerOffences(planted, files).map(entry => entry.split(' ')[0]))
      .toEqual(['components/Scratch.tsx:1', 'components/Possessive.tsx:1']);
  });
});
