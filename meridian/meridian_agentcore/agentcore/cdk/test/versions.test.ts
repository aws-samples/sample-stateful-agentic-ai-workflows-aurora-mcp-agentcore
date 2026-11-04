import { readFileSync } from 'node:fs';
import { join } from 'node:path';

type Manifest = {
  dependencies: Record<string, string>;
  devDependencies: Record<string, string>;
  overrides?: Record<string, string>;
};

const manifest: Manifest = JSON.parse(readFileSync(join(__dirname, '..', 'package.json'), 'utf8'));
const lock = JSON.parse(readFileSync(join(__dirname, '..', 'package-lock.json'), 'utf8'));
const EXACT = /^\d+\.\d+\.\d+(-[0-9A-Za-z.]+)?$/;

/**
 * The one pre-release this app may depend on (versions rule 14). The AgentCore CLI's 0.x line,
 * from npm's v0-latest tag, vends @aws/agentcore-cdk only as 0.1.0-alpha.N builds. alpha.47 ran
 * every live deploy (under CLI 0.27.0), so it stays at exactly that version: CLI 0.31.2's own CDK
 * app names alpha.55, but an exception never moves to a newer pre-release. Exit: the move to the
 * stable 1.x CLI and library after re:Invent 2026, which needs `agentcore migrate`.
 */
const AGENTCORE_CDK_EXCEPTION = { name: '@aws/agentcore-cdk', version: '0.1.0-alpha.47' };

const pins = (): [string, string][] => [
  ...Object.entries(manifest.dependencies),
  ...Object.entries(manifest.devDependencies),
  ...Object.entries(manifest.overrides ?? {}),
];

test('every dependency is pinned to one exact version', () => {
  expect(pins().filter(([, spec]) => !EXACT.test(spec))).toEqual([]);
});

test('no pre-release other than the named AgentCore CDK exception', () => {
  const prereleases = pins().filter(([, spec]) => spec.includes('-'));
  expect(prereleases).toEqual([[AGENTCORE_CDK_EXCEPTION.name, AGENTCORE_CDK_EXCEPTION.version]]);
});

test('the lockfile installs exactly the pinned versions', () => {
  for (const [name, spec] of [...Object.entries(manifest.dependencies), ...Object.entries(manifest.devDependencies)]) {
    expect([name, lock.packages[`node_modules/${name}`]?.version]).toEqual([name, spec]);
  }
});
