// Captures the sign-in, the signed-in traveler, the RLS probe and the identity receipt.
//   venv/bin/python scripts/identity_capture/capture_session.py <base-url> <out-dir> <summary-html>
// capture_session.py creates a pipe, hands the write end to mint_session.py and the read end to
// this script as --token-fd N. The tokens never touch standard input or output, argv, the
// environment or a file, and stay in memory. No password is typed or read. Do not run this by hand.
// Conventions are those of RIV/_deck-kit/tools/capture_storyboard.mjs: system Chrome, 1920 by 1080
// at device scale 2 (3840 by 2160 pixels), dark theme, presentation mode, a problems log.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { chromium } from '../../frontend/node_modules/playwright-core/index.mjs';
import {
  cognitoDomain, hostedOrigin, installSession, readTokenPipe, tokenFdFromArgs,
} from './session_routes.mjs';

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PHASE_4_PROMPT =
  'Recall my Tokyo plan and saved preferences: home airport, food needs, and budget.';
const MERIDIAN = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..', '..');

function refuse(message) {
  console.error(message);
  process.exit(3);
}

let tokenFd;
let positional;
try {
  const parsed = tokenFdFromArgs(process.argv.slice(2));
  tokenFd = parsed.fd;
  positional = parsed.rest;
} catch (error) {
  refuse(error.message);
}
const [base, outDir, summaryHtml] = positional;

if (!base || !outDir || !summaryHtml) {
  refuse('usage: capture_session.py <base-url> <out-dir> <summary-html>');
}
let pin;
try {
  const record = fs.readFileSync(path.join(MERIDIAN, '.local/hosted-release.json'), 'utf8');
  const origin = hostedOrigin(record);
  if (new URL(base).origin !== origin) {
    refuse(`refusing ${new URL(base).origin}: the capture runs only against the hosted site`);
  }
  const settings = fs.readFileSync(
    path.join(MERIDIAN, 'frontend/.env.development.local'), 'utf8');
  pin = { host: cognitoDomain(settings), origin };
} catch (error) {
  refuse(`cannot validate the hosted site settings: ${error.message}`);
}
let tokens;
try {
  tokens = readTokenPipe(tokenFd, fs);
} catch (error) {
  refuse(error.message);
}
fs.mkdirSync(outDir, { recursive: true });

const browser = await chromium.launch({ executablePath: CHROME, headless: true });
const log = [];
const problems = [];

async function newPage(user, extra = {}) {
  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 }, deviceScaleFactor: 2, colorScheme: 'dark',
  });
  const page = await context.newPage();
  page.on('console', (m) => {
    if (m.type() === 'error') problems.push({ kind: 'console', text: m.text().slice(0, 300) });
  });
  page.on('pageerror', (e) => {
    problems.push({ kind: 'pageerror', text: String(e).slice(0, 300) });
  });
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) {
      problems.push({ kind: 'http', status: r.status(), path: new URL(r.url()).pathname });
    }
  });
  if (user) await installSession(page, tokens[user], extra, pin);
  return page;
}

async function present(page) {
  const button = page.getByRole('button', { name: 'Present fullscreen' });
  if (await button.count()) { await button.first().click(); await page.waitForTimeout(600); }
  await page.waitForTimeout(1500);
}

async function signIn(page, extra) {
  const query = new URLSearchParams(extra).toString();
  await page.goto(`${base}/showcase?${query}`, { waitUntil: 'networkidle', timeout: 60000 });
  const heading = page.getByRole('heading', { name: 'Sign in to Meridian' });
  await heading.waitFor({ timeout: 30000 });
  await page.getByRole('button', { name: 'Sign in' }).click();
  await heading.waitFor({ state: 'detached', timeout: 60000 });
  await present(page);
}

async function settle(page, minMs = 3000) {
  const start = Date.now();
  let last = '';
  let since = Date.now();
  while (Date.now() - start < 120000) {
    await page.waitForTimeout(500);
    const busy = await page.getByRole('button', { name: /stop/i }).count();
    const now = await page.evaluate(() => document.body.innerText);
    if (busy || now !== last) { last = now; since = Date.now(); continue; }
    if (Date.now() - since > 4000 && Date.now() - start > minMs) break;
  }
  await page.waitForTimeout(800);
}

async function shot(page, id) {
  await page.screenshot({ path: `${outDir}/${id}.png` });
  const where = new URL(page.url());
  for (const name of ['code', 'state']) where.searchParams.delete(name);
  const entry = { id, at: new Date().toISOString(), url: where.pathname + where.search };
  log.push(entry);
  console.log(JSON.stringify(entry));
}

const SCENE = { present: '1', theme: 'dark' };

// 11: the sign-in screen, before any request.
const signedOut = await newPage(null);
await signedOut.goto(`${base}/showcase?${new URLSearchParams(SCENE)}`,
  { waitUntil: 'networkidle', timeout: 60000 });
await signedOut.getByRole('heading', { name: 'Sign in to Meridian' }).waitFor({ timeout: 30000 });
await shot(signedOut, '11-sign-in');

// 12: Jordan signed in, on the Concierge home.
const home = await newPage('jordan', SCENE);
await signIn(home, SCENE);
await shot(home, '12-signed-in-jordan');

// 13: the RLS probe card in Phase 4, with the signed-in person.
const ladder = { ...SCENE, view: 'ladder' };
const rls = await newPage('jordan', ladder);
await signIn(rls, ladder);
await rls.getByRole('button', { name: /^Phase 4,/ }).click();
await rls.waitForTimeout(800);
const context = rls.getByRole('switch', { name: /Use traveler context/ });
if ((await context.count()) && (await context.getAttribute('aria-checked')) !== 'true') {
  await context.click();
  await rls.waitForTimeout(600);
}
await rls.getByRole('textbox', { name: 'Ask Meridian anything' }).fill(PHASE_4_PROMPT);
await rls.getByRole('button', { name: 'Send message' }).click();
await settle(rls, 6000);
await rls.getByText('Inspect evidence').first().click();
await rls.getByRole('button', { name: 'RLS probe' }).click();
await rls.getByRole('button', { name: 'Run RLS probe' }).click();
await rls.getByText('Signed-in person').waitFor({ timeout: 60000 });
await rls.getByText('Signed-in person').scrollIntoViewIfNeeded();
await rls.waitForTimeout(1200);
await shot(rls, '13-rls-signed-in-person');

// 14: the recorded receipt, rendered from the real JSON by identity_proof.py.
const receipt = await browser.newPage({
  viewport: { width: 1920, height: 336 }, deviceScaleFactor: 2, colorScheme: 'dark',
});
await receipt.goto(pathToFileURL(path.resolve(summaryHtml)).href);
await receipt.locator('#receipt').screenshot({ path: `${outDir}/14-identity-receipt.png` });
log.push({ id: '14-identity-receipt', at: new Date().toISOString(), url: 'receipt page' });
console.log(JSON.stringify(log[log.length - 1]));

fs.writeFileSync(`${outDir}/capture-log.json`, JSON.stringify({ log, problems }, null, 1));
console.log(`PROBLEMS ${problems.length}`);
for (const p of problems) console.log(JSON.stringify(p));
await browser.close();
