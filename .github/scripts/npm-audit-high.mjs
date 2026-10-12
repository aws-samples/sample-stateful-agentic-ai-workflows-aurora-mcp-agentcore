// Fail on any high or critical npm audit finding except one documented exception.
//
// Exception: aws-cdk-lib bundles brace-expansion 5.0.9, which is affected by the three
// advisories below. No aws-cdk-lib release (checked through 2.272.0 on 2026-10-02) bundles
// the patched 5.0.12, and a bundled dependency cannot be overridden. The code runs only
// while the CDK app synthesizes on a developer machine or in CI, over globs written in
// this repository; it never ships in a deployed artifact. When the advisories stop
// appearing (aws-cdk-lib picked up the fix), this script fails so the exception is removed.
//
// Usage, from a package directory: node <repo>/.github/scripts/npm-audit-high.mjs
// Tests may pass a saved `npm audit --json` report with --report <file>.
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';

const ALLOWED_PATH = 'node_modules/aws-cdk-lib/node_modules/brace-expansion';
const ALLOWED_ADVISORIES = new Set([
  'GHSA-q2hr-2g5m-vwhr',
  'GHSA-qhr7-859c-m2p7',
  'GHSA-6j4f-fj2g-mc7p',
]);
const BLOCKING = new Set(['high', 'critical']);

function loadReport() {
  const at = process.argv.indexOf('--report');
  if (at !== -1) return JSON.parse(readFileSync(process.argv[at + 1], 'utf8'));
  const run = spawnSync('npm', ['audit', '--json'], { encoding: 'utf8' });
  if (!run.stdout) throw new Error(`npm audit produced no report: ${run.stderr}`);
  return JSON.parse(run.stdout);
}

function advisoryId(via) {
  return typeof via === 'object' ? (via.url || '').split('/').pop() : null;
}

function isAllowed(vulnerability) {
  const nodes = vulnerability.nodes || [];
  const onlyBundledCopy = nodes.length > 0 && nodes.every((node) => node === ALLOWED_PATH);
  const onlyKnownAdvisories = vulnerability.via.every(
    (via) => ALLOWED_ADVISORIES.has(advisoryId(via)),
  );
  return vulnerability.name === 'brace-expansion' && onlyBundledCopy && onlyKnownAdvisories;
}

const report = loadReport();
const findings = Object.values(report.vulnerabilities || {});
const blocking = findings.filter((v) => BLOCKING.has(v.severity) && !isAllowed(v));
const excepted = findings.filter(isAllowed);

if (blocking.length) {
  for (const v of blocking) {
    console.error(`${v.severity}: ${v.name} (${(v.nodes || []).join(', ')})`);
  }
  console.error(`npm audit: ${blocking.length} high or critical finding(s) outside the exception.`);
  process.exit(1);
}
if (!excepted.length) {
  console.error('npm audit: the aws-cdk-lib brace-expansion advisories are gone.');
  console.error('Remove the exception in .github/scripts/npm-audit-high.mjs and use');
  console.error('`npm audit --audit-level=high` again.');
  process.exit(1);
}
console.log('npm audit: no high or critical findings outside the documented exception');
console.log(`(${[...ALLOWED_ADVISORIES].join(', ')} in aws-cdk-lib's bundled brace-expansion).`);
