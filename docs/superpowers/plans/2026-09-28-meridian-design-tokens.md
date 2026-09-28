# Meridian Design Tokens Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Meridian showcase's hard-coded styling with one set of role variables, a seven-step type ramp on the system font, three radii and tone-based depth, as specified in `docs/superpowers/specs/2026-09-28-meridian-design-tokens-design.md`.

**Architecture:** A permanent PostCSS-based token check (`frontend/scripts/design-tokens/`) gates every stage through a shrinking exemption list. Temporary PostCSS codemods (`frontend/scripts/design-migration/`) do the mechanical rewriting and write review reports; anything they cannot decide is marked `REVIEW` and resolved by hand. A Playwright screenshot harness (`frontend/e2e/visual/`) proves pixel identity for the deletion stages and produces review sets for the visual stages. The codemods and the exemption list are deleted in the last code task.

**Tech Stack:** CSS custom properties, PostCSS 8.5, Node 22 ESM scripts, Vitest 4, Playwright 1.63 with axe-core, React 18 + Vite 7.

## Global Constraints

- Work on branch `meridian-design-tokens`. Never push. Never amend or rebase. Use `GIT_EDITOR=true` for git commands that could open an editor.
- Run every command from `meridian/frontend` unless a step says otherwise.
- Every commit message: imperative subject of 72 characters or fewer, a plain factual body, no em dashes, ending with the line `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- New code: lines of 100 characters or fewer, functions of 100 lines or fewer, no `..` import paths (import siblings with `./`), no commented-out code.
- Zero warnings from every tool. Delete files with `trash`, never `rm -rf`.
- Color values, type ramp, radii and thresholds come from the spec; the tokens are written out in full in Tasks 5 to 8.
- Contrast requirements are enforced by `e2e/accessibility.spec.ts` and may not be loosened: secondary text 7:1 on `--mds-surface-2`, primary action label 7:1, action edge 3:1 on `--mds-surface`, control edge 3:1 and focus indicator 3:1 on `--mds-surface-2`.
- The full verification gate, used at the end of each task:
  ```bash
  npm run lint && npm run typecheck && npm run test:run && npm run test:accessibility
  ```
  Expected: ESLint silent, `design tokens: N files pass`, TypeScript silent, all Vitest files pass, all Playwright accessibility tests pass (32 at the start of this plan).
- Screenshot review means opening the PNGs with the Read tool and looking at them. At minimum: all five views dark desktop, all five light desktop, and projector dark for `concierge` and `recovery`.

---

### Task 1: Token check with a ratchet

**Files:**
- Create: `meridian/frontend/scripts/design-tokens/rules.mjs`
- Create: `meridian/frontend/scripts/design-tokens/rules.test.mjs`
- Create: `meridian/frontend/scripts/design-tokens/check.mjs`
- Create: `meridian/frontend/scripts/design-tokens/exemptions.mjs`
- Modify: `meridian/frontend/package.json` (lint script, `postcss` devDependency)
- Modify: `meridian/frontend/vitest.config.ts` (include pattern)
- Modify: `meridian/frontend/src/showcase/meridianShowcase.css:1581`, `presenterProof.css:196,332,370`, `surfaceSwitch.css:428`, `recoveryWorkspace.css:5506` (undefined `--mds-mono`)

**Interfaces:**
- Produces: `checkCss(css: string, context: { file: string, isTokensFile: boolean, definedVars: Set<string> }): Violation[]` where `Violation = { rule, file, line, prop, value }` and `rule` is one of `color`, `gradient`, `type`, `radius`, `shadow`, `blur`, `undefined-var`, `var-fallback`.
- Produces: `collectDefinedVars(css: string): Set<string>`.
- Produces: `EXEMPTIONS: Record<rule, string[]>` of frontend-relative CSS paths. Later tasks empty one rule each.

- [ ] **Step 1: Add PostCSS as a direct dependency and include script tests in Vitest**

PostCSS 8.5.26 is already installed through Vite; the check imports it directly, so it must be declared.

```bash
npm install --save-dev postcss@^8.5.26
```

In `vitest.config.ts`, change the `include` line to:

```ts
    include: ['src/**/*.{test,spec}.{ts,tsx}', 'scripts/**/*.test.mjs'],
```

- [ ] **Step 2: Write the failing rule tests**

Create `scripts/design-tokens/rules.test.mjs`:

```js
// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { checkCss, collectDefinedVars } from './rules.mjs';

const DEFINED = new Set([
  '--mds-label', '--mds-radius-m', '--mds-type-body', '--mds-weight-bold', '--mds-shadow-float',
  '--mds-tracking-title',
]);
const rules = (css, extra = {}) => checkCss(css, {
  file: 'x.css', isTokensFile: false, definedVars: DEFINED, ...extra,
}).map(violation => violation.rule);

describe('checkCss', () => {
  it('accepts token-only declarations', () => {
    expect(rules(`.a {
      color: var(--mds-label); border-radius: var(--mds-radius-m) 0;
      font: var(--mds-type-body); font-weight: var(--mds-weight-bold);
      box-shadow: var(--mds-shadow-float); letter-spacing: var(--mds-tracking-title);
    }`)).toEqual([]);
  });

  it('flags color literals outside the tokens file only', () => {
    expect(rules('.a { color: #fff; background: rgb(0 0 0 / 0.5); border-color: white; }'))
      .toEqual(['color', 'color', 'color']);
    expect(rules(':root { --mds-label: #fff; }', { isTokensFile: true })).toEqual([]);
  });

  it('flags gradients except on masks', () => {
    expect(rules('.a { background: linear-gradient(#000, #fff); }')).toEqual(['color', 'gradient']);
    expect(rules('.a { mask-image: linear-gradient(black, transparent); }')).toEqual(['color']);
  });

  it('flags literal type values and allows inherit', () => {
    expect(rules('.a { font-size: 13px; font-weight: 600; letter-spacing: 0.04em; }'))
      .toEqual(['type', 'type', 'type']);
    expect(rules('.a { font: 14px sans-serif; }')).toEqual(['type']);
    expect(rules('.a { font-size: inherit; font-weight: inherit; letter-spacing: 0; }')).toEqual([]);
  });

  it('flags radius literals and allows 0, 50% and tokens', () => {
    expect(rules('.a { border-radius: 8px; }')).toEqual(['radius']);
    expect(rules('.a { border-radius: 50%; border-top-left-radius: 0; }')).toEqual([]);
  });

  it('flags literal shadows and any backdrop blur', () => {
    expect(rules('.a { box-shadow: 0 1px 2px var(--mds-label); text-shadow: 0 0 1px #f00; }'))
      .toEqual(['shadow', 'color', 'shadow']);
    expect(rules('.a { box-shadow: none; backdrop-filter: blur(4px); }')).toEqual(['blur']);
  });

  it('flags undefined variables and fallbacks on mds tokens', () => {
    expect(rules('.a { font-family: var(--mds-mono); color: var(--mds-label, #fff); }'))
      .toEqual(['undefined-var', 'color', 'var-fallback']);
  });

  it('reports the source line', () => {
    const [violation] = checkCss('.a {\n  color: #000;\n}', {
      file: 'x.css', isTokensFile: false, definedVars: DEFINED,
    });
    expect(violation).toMatchObject({ rule: 'color', line: 2, prop: 'color', value: '#000' });
  });
});

describe('collectDefinedVars', () => {
  it('collects custom property names', () => {
    expect([...collectDefinedVars('.a { --x: 1; } :root { --y: 2; color: red; }')])
      .toEqual(['--x', '--y']);
  });
});
```

- [ ] **Step 3: Run the tests and confirm they fail**

Run: `npx vitest --run scripts/design-tokens/rules.test.mjs`
Expected: FAIL, `Failed to load url ./rules.mjs` (the module does not exist yet).

- [ ] **Step 4: Implement the rules**

Create `scripts/design-tokens/rules.mjs`:

```js
import postcss from 'postcss';

const COLOR_LITERAL =
  /#[0-9a-fA-F]{3,8}\b|\b(?:rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(|(?<![-\w])(?:white|black)(?![-\w])/;
const GRADIENT = /\b(?:repeating-)?(?:linear|radial|conic)-gradient\(/;
const VAR_REF = /var\(\s*(--[\w-]+)\s*(,)?/g;
const TYPE_TOKEN = /^var\(--mds-type-[a-z0-9-]+\)$/;
const WEIGHT_TOKEN = /^var\(--mds-weight-(?:regular|medium|semibold|bold)\)$/;
const TRACKING_OK = /^(?:0|normal|var\(--mds-tracking-(?:title|display)\))$/;
const RADIUS_PART = '(?:0|50%|var\\(--mds-radius-(?:s|m|l|full)\\))';
const RADIUS_OK = new RegExp(`^${RADIUS_PART}(?:\\s+${RADIUS_PART}){0,3}$`);
const MASK_PROPS = new Set(['mask', 'mask-image', '-webkit-mask', '-webkit-mask-image']);
const SHADOW_OK = new Set(['none', 'var(--mds-shadow-float)']);

function isTypeViolation(prop, value) {
  if (prop === 'font-size') return value !== 'inherit';
  if (prop === 'font') return value !== 'inherit' && !TYPE_TOKEN.test(value);
  if (prop === 'font-weight') return value !== 'inherit' && !WEIGHT_TOKEN.test(value);
  if (prop === 'letter-spacing') return !TRACKING_OK.test(value);
  return false;
}

function isShadowViolation(prop, value) {
  if (prop === 'box-shadow') return !SHADOW_OK.has(value);
  return prop === 'text-shadow' && value !== 'none';
}

function declarationRules(prop, value, { isTokensFile, definedVars }) {
  const found = [];
  if (!isTokensFile && COLOR_LITERAL.test(value)) found.push('color');
  if (!isTokensFile && GRADIENT.test(value) && !MASK_PROPS.has(prop)) found.push('gradient');
  if (isTypeViolation(prop, value)) found.push('type');
  if (/^border(?:-[a-z]+)*-radius$/.test(prop) && !RADIUS_OK.test(value)) found.push('radius');
  if (isShadowViolation(prop, value)) found.push('shadow');
  if (prop === 'backdrop-filter' || prop === '-webkit-backdrop-filter') found.push('blur');
  for (const [, name, fallback] of value.matchAll(VAR_REF)) {
    if (!definedVars.has(name)) found.push('undefined-var');
    if (fallback && name.startsWith('--mds-')) found.push('var-fallback');
  }
  return found;
}

/**
 * Checks one stylesheet against the Meridian token rules.
 *
 * @param {string} css Stylesheet source.
 * @param {{ file: string, isTokensFile: boolean, definedVars: Set<string> }} context
 * @returns {{ rule: string, file: string, line: number, prop: string, value: string }[]}
 */
export function checkCss(css, context) {
  const violations = [];
  postcss.parse(css, { from: context.file }).walkDecls(decl => {
    const prop = decl.prop.startsWith('--') ? decl.prop : decl.prop.toLowerCase();
    const value = decl.value.trim();
    for (const rule of declarationRules(prop, value, context)) {
      violations.push({ rule, file: context.file, line: decl.source?.start?.line ?? 0, prop, value });
    }
  });
  return violations;
}

/**
 * Lists the custom properties a stylesheet defines.
 *
 * @param {string} css Stylesheet source.
 * @returns {Set<string>}
 */
export function collectDefinedVars(css) {
  const names = new Set();
  postcss.parse(css).walkDecls(/^--/, decl => { names.add(decl.prop); });
  return names;
}
```

- [ ] **Step 5: Run the tests and confirm they pass**

Run: `npx vitest --run scripts/design-tokens/rules.test.mjs`
Expected: PASS, 9 tests.

- [ ] **Step 6: Write the command-line check**

Create `scripts/design-tokens/check.mjs`:

```js
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { EXEMPTIONS } from './exemptions.mjs';
import { checkCss, collectDefinedVars } from './rules.mjs';

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const SRC = path.join(FRONTEND, 'src');
const TOKENS_FILE = path.join(SRC, 'showcase', 'tokens.css');

function cssFiles(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return cssFiles(full);
    return entry.name.endsWith('.css') ? [full] : [];
  });
}

const read = file => fs.readFileSync(file, 'utf8');
const rel = file => path.relative(FRONTEND, file);
const scanned = [
  ...cssFiles(path.join(SRC, 'showcase')), path.join(SRC, 'index.css'), path.join(SRC, 'preflight.css'),
];
const definedVars = new Set(
  [...scanned, ...cssFiles(path.join(SRC, 'stage'))].flatMap(file => [...collectDefinedVars(read(file))]),
);
const all = scanned.flatMap(file => checkCss(read(file), {
  file: rel(file), isTokensFile: file === TOKENS_FILE, definedVars,
}));

if (process.argv.includes('--seed')) {
  const seed = {};
  for (const violation of all) (seed[violation.rule] ??= new Set()).add(violation.file);
  const lists = Object.entries(seed).map(([rule, files]) =>
    `  '${rule}': [\n${[...files].sort().map(file => `    '${file}',`).join('\n')}\n  ],`);
  console.log(`export const EXEMPTIONS = {\n${lists.join('\n')}\n};`);
  process.exit(0);
}

const exempt = violation => (EXEMPTIONS[violation.rule] ?? []).includes(violation.file);
const violations = all.filter(violation => !exempt(violation));
const stale = Object.entries(EXEMPTIONS).flatMap(([rule, files]) => files
  .filter(file => !all.some(violation => violation.rule === rule && violation.file === file))
  .map(file => `${rule}: ${file}`));

for (const v of violations) console.error(`${v.file}:${v.line} [${v.rule}] ${v.prop}: ${v.value}`);
for (const entry of stale) console.error(`stale exemption, delete it from exemptions.mjs: ${entry}`);
if (violations.length || stale.length) {
  console.error(`design tokens: ${violations.length} violations, ${stale.length} stale exemptions`);
  process.exit(1);
}
console.log(`design tokens: ${scanned.length} files pass`);
```

- [ ] **Step 7: Fix the undefined `--mds-mono` references**

The defined variable is `--mds-font-mono`. Replace all six references so the undefined-variable and fallback rules start with no exemptions:

```bash
sed -i '' -E 's/var\(--mds-mono(, [^)]*)?\)/var(--mds-font-mono)/' \
  src/showcase/meridianShowcase.css src/showcase/presenterProof.css \
  src/showcase/surfaceSwitch.css src/showcase/recoveryWorkspace.css
command grep -rn -- '--mds-mono' src || echo "no --mds-mono left"
```

Expected: `no --mds-mono left`.

- [ ] **Step 8: Seed the exemption list**

`exemptions.mjs` must exist before `check.mjs` can import it. Create a placeholder, then overwrite it with the seed output:

```bash
printf 'export const EXEMPTIONS = {};\n' > scripts/design-tokens/exemptions.mjs
node scripts/design-tokens/check.mjs --seed > scripts/design-tokens/exemptions.tmp.mjs
mv scripts/design-tokens/exemptions.tmp.mjs scripts/design-tokens/exemptions.mjs
cat scripts/design-tokens/exemptions.mjs
```

Expected: rules `color`, `gradient`, `type`, `radius`, `shadow` and `blur`, each listing the showcase stylesheets plus `src/index.css` where applicable. There must be no `undefined-var` or `var-fallback` key. If either appears, the listed file references a variable nothing defines: fix it the way Step 7 did, then reseed.

Add this first line to `exemptions.mjs` (the file is deleted in Task 9):

```js
// Stylesheets still waiting for their migration task. Each task empties one rule.
```

- [ ] **Step 9: Wire the check into lint and run it**

In `package.json`, change the `lint` script to:

```json
"lint": "eslint . --ext ts,tsx --report-unused-disable-directives --max-warnings 0 && node scripts/design-tokens/check.mjs",
```

Run: `npm run lint`
Expected: ends with `design tokens: 15 files pass` (the count is the number of scanned stylesheets; record the number you see).

- [ ] **Step 10: Prove the check catches a regression**

```bash
printf '.x { color: #123456; }\n' >> src/showcase/solutionBriefing.css
node scripts/design-tokens/check.mjs; echo "exit $?"
git checkout src/showcase/solutionBriefing.css
```

Expected: `src/showcase/solutionBriefing.css:<line> [color] color: #123456` and `exit 1`. (`solutionBriefing.css` has no color literals today, so it is not exempt.)

- [ ] **Step 11: Run the full gate and commit**

Run the full verification gate from Global Constraints. Expected: all green.

```bash
git add package.json package-lock.json vitest.config.ts scripts/design-tokens \
  src/showcase/meridianShowcase.css src/showcase/presenterProof.css \
  src/showcase/surfaceSwitch.css src/showcase/recoveryWorkspace.css
GIT_EDITOR=true git commit -q -F - <<'EOF'
Add a design token check with a per-rule exemption ratchet

scripts/design-tokens/check.mjs fails lint on color literals outside
tokens.css, gradients, literal type values, radii and shadows, backdrop
blur, undefined variables and fallbacks on --mds tokens. Existing
stylesheets are exempt per rule until their migration task empties the
list; a stale exemption also fails.

The six --mds-mono references now use the defined --mds-font-mono.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 2: Screenshot harness and baseline

**Files:**
- Create: `meridian/frontend/playwright.visual.config.ts`
- Create: `meridian/frontend/e2e/visual/showcase.visual.ts`
- Modify: `meridian/frontend/package.json` (`visual` script)
- Modify: `meridian/frontend/tsconfig.e2e.json` (include the new config)

**Interfaces:**
- Produces: `npm run visual` with env `VISUAL_DIR` (default `../.local/visual/latest`, resolved from `meridian/frontend`) and optional `MERIDIAN_VISUAL_JOURNEY`.
- Produces in `VISUAL_DIR`: `<view>-<theme>-<mode>.png` for 5 views x 2 themes x 2 modes, `stage.png`, `classes-<view>-<theme>-<mode>.json` (every class rendered with all `<details>` open), `clipped-<view>-<theme>-<mode>.json` (leaf elements whose text overflows a clipping box).
- Views: `briefing`, `concierge`, `ladder`, `recovery`, `proof`. Themes: `dark`, `light`. Modes: `desktop` (1440x1000), `projector` (1920x1080 with `present=1`).

- [ ] **Step 1: Write the config**

Create `playwright.visual.config.ts`:

```ts
import path from 'node:path';
import { defineConfig } from '@playwright/test';

const baseURL = process.env.MERIDIAN_A11Y_URL || 'http://127.0.0.1:4174';
const dir = path.resolve(process.env.VISUAL_DIR ?? '../.local/visual/latest');

export default defineConfig({
  testDir: './e2e/visual',
  testMatch: '**/*.visual.ts',
  snapshotPathTemplate: path.join(dir, '{arg}{ext}'),
  timeout: 120_000,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: { baseURL, browserName: 'chromium', reducedMotion: 'reduce', trace: 'off', screenshot: 'off' },
  expect: { toHaveScreenshot: { maxDiffPixels: 0, animations: 'disabled', caret: 'hide' } },
  webServer: process.env.MERIDIAN_A11Y_URL ? undefined : {
    command: 'npm run dev -- --host 127.0.0.1 --port 4174 --strictPort', url: baseURL,
    reuseExistingServer: true, timeout: 30_000,
  },
});
```

- [ ] **Step 2: Write the capture spec**

Create `e2e/visual/showcase.visual.ts`:

```ts
import fs from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';

const VIEWS = ['briefing', 'concierge', 'ladder', 'recovery', 'proof'] as const;
const THEMES = ['dark', 'light'] as const;
const MODES = [
  { name: 'desktop', width: 1440, height: 1000, query: '' },
  { name: 'projector', width: 1920, height: 1080, query: '&present=1' },
] as const;
const journey = process.env.MERIDIAN_VISUAL_JOURNEY;
const outDir = path.resolve(process.env.VISUAL_DIR ?? '../.local/visual/latest');

test.beforeEach(async ({ page }) => {
  await page.clock.setFixedTime(new Date('2026-09-28T18:00:00'));
  if (journey) return;
  await page.route(url => url.pathname.startsWith('/api/') || url.pathname === '/health', route =>
    route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"offline capture"}' }));
});

async function settle(page: Page) {
  await expect(page.locator('.mds-root')).toBeVisible();
  await page.waitForLoadState('networkidle');
  await page.evaluate(async () => { await document.fonts.ready; });
  await page.waitForFunction(() => Array.from(document.images).every(image => image.complete));
}

function write(name: string, data: unknown) {
  fs.mkdirSync(outDir, { recursive: true });
  fs.writeFileSync(path.join(outDir, name), `${JSON.stringify(data, null, 2)}\n`);
}

async function recordClipped(page: Page, name: string) {
  const clipped = await page.evaluate(() => Array.from(document.querySelectorAll<HTMLElement>('body *'))
    .filter(el => el.childElementCount === 0 && (el.textContent ?? '').trim() !== '')
    .filter(el => {
      const style = getComputedStyle(el);
      const hides = style.overflowX !== 'visible' || style.overflowY !== 'visible';
      return hides && (el.scrollWidth > el.clientWidth + 1 || el.scrollHeight > el.clientHeight + 1);
    })
    .map(el => `${el.tagName.toLowerCase()}.${Array.from(el.classList).join('.')} `
      + `"${(el.textContent ?? '').trim().slice(0, 40)}"`));
  write(`clipped-${name}.json`, clipped.sort());
}

async function recordClasses(page: Page, name: string) {
  await page.evaluate(() => {
    document.querySelectorAll('details:not([open])').forEach(details => details.setAttribute('open', ''));
  });
  const classes = await page.evaluate(() => Array.from(new Set(Array.from(document.querySelectorAll('[class]'))
    .flatMap(el => Array.from(el.classList)))).sort());
  write(`classes-${name}.json`, classes);
}

for (const mode of MODES) for (const theme of THEMES) for (const view of VIEWS) {
  const name = `${view}-${theme}-${mode.name}`;
  test(name, async ({ page }) => {
    await page.setViewportSize({ width: mode.width, height: mode.height });
    const journeyQuery = journey ? `&journey=${encodeURIComponent(journey)}` : '';
    await page.goto(`/showcase?view=${view}&theme=${theme}${mode.query}${journeyQuery}`);
    await settle(page);
    await expect(page).toHaveScreenshot(`${name}.png`);
    await recordClipped(page, name);
    await recordClasses(page, name);
  });
}

test('stage', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 });
  await page.goto('/stage');
  await page.waitForLoadState('networkidle');
  await page.evaluate(async () => { await document.fonts.ready; });
  fs.mkdirSync(outDir, { recursive: true });
  await page.screenshot({ path: path.join(outDir, 'stage.png') });
});
```

`/stage` is saved with `page.screenshot` rather than compared, because the stage player animates on a timer. It is reviewed by eye only.

- [ ] **Step 3: Register the script and type-check the new files**

In `package.json` `scripts`, add:

```json
"visual": "playwright test -c playwright.visual.config.ts",
```

In `tsconfig.e2e.json`, change `include` to:

```json
"include": ["playwright.config.ts", "playwright.visual.config.ts", "e2e"]
```

Run: `npm run typecheck`
Expected: no output, exit 0. The main `playwright.config.ts` only matches `*.spec.ts` and `*.test.ts`, so `npm run test:accessibility` does not pick up `*.visual.ts`.

- [ ] **Step 4: Capture the baseline**

```bash
VISUAL_DIR=../.local/visual/baseline npm run visual -- --update-snapshots=all
ls ../.local/visual/baseline | wc -l
```

Expected: 21 passed, and 61 files (20 PNG + `stage.png` + 20 `classes-*.json` + 20 `clipped-*.json`).

- [ ] **Step 5: Prove the captures are deterministic**

```bash
VISUAL_DIR=../.local/visual/baseline npm run visual
```

Expected: 21 passed with zero pixel differences. If a view differs, open its `*-diff.png` under `test-results/` and fix the source of drift in the spec (for example add `mask: [page.locator('<selector>')]` to that `toHaveScreenshot` call for a relative timestamp), recapture with Step 4, and repeat this step until two consecutive runs pass.

- [ ] **Step 6: Review the baseline**

Open `briefing-dark-desktop.png`, `concierge-dark-desktop.png`, `recovery-dark-projector.png`, `concierge-light-desktop.png` and `stage.png` with the Read tool. Confirm each shows a rendered page, not a loading or error screen.

- [ ] **Step 7 (only when the backend is running on 8013): Capture a live-journey reference set**

From `meridian/`:

```bash
venv/bin/python scripts/kill_and_resume_demo.py --keep
```

Note the journey id it prints (`jrn_...`). Then, from `meridian/frontend`:

```bash
MERIDIAN_VISUAL_JOURNEY=<jrn id> VISUAL_DIR=../.local/visual/baseline-live \
  npm run visual -- --update-snapshots=all
```

Expected: 21 passed. This set is for review only; live data is not deterministic.

- [ ] **Step 8: Commit**

```bash
git add playwright.visual.config.ts e2e/visual package.json tsconfig.e2e.json
GIT_EDITOR=true git commit -q -F - <<'EOF'
Add a screenshot harness for the showcase views

npm run visual captures five views in both themes on desktop and
projector layouts with a fixed clock and offline API responses, compares
them pixel for pixel against VISUAL_DIR, and records rendered classes
and clipped text for each view. A journey id switches it to live data.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 3: Delete unrendered components

**Files:**
- Delete: `src/showcase/components/RecoveryRouteMap.tsx`, `RecommendationCards.tsx`, `AuroraEvidenceStrip.tsx`, `SessionReceipt.tsx`, `JourneyPanel.tsx`, `PhaseSelector.tsx`
- Delete: `src/showcase/components/__tests__/PhaseSelector.test.tsx`, `src/showcase/components/__tests__/ProofPanels.test.tsx`
- Modify: `src/showcase/components/RecoveryDecisionCards.tsx` (remove `RecoveryGuardrailsCard`, `CheckpointedPlanCard`, `CheckpointedPlanCardProps`)
- Modify: `src/showcase/components/__tests__/ExperiencePolish.test.tsx` (remove four tests and two imports)
- Modify: `package.json`, `package-lock.json` (remove `d3-geo`, `topojson-client`, `world-atlas`, `@types/d3-geo`, `@types/topojson-client`)

**Interfaces:**
- Consumes: the baseline in `../.local/visual/baseline` from Task 2.
- Produces: no rendered change; a smaller source tree for Task 4's dead-class scan.

- [ ] **Step 1: Confirm each component is unused**

```bash
for c in RecoveryRouteMap RecommendationCards AuroraEvidenceStrip SessionReceipt JourneyPanel \
  RecoveryGuardrailsCard CheckpointedPlanCard PhaseSelector; do
  printf '%-24s %s\n' "$c" "$(command grep -rlw "$c" src --include='*.ts' --include='*.tsx' \
    | command grep -v __tests__ | tr '\n' ' ')"
done
```

Expected: each name lists only its own defining file.

- [ ] **Step 2: Delete the component files and their dedicated tests**

```bash
cd src/showcase/components
trash RecoveryRouteMap.tsx RecommendationCards.tsx AuroraEvidenceStrip.tsx SessionReceipt.tsx \
  JourneyPanel.tsx PhaseSelector.tsx __tests__/PhaseSelector.test.tsx __tests__/ProofPanels.test.tsx
cd -
```

`ProofPanels.test.tsx` contains one test, and it renders only `AuroraEvidenceStrip`.

- [ ] **Step 3: Remove the two unused cards from `RecoveryDecisionCards.tsx`**

Delete the `CheckpointedPlanCardProps` interface (starts at line 72), the whole `export function RecoveryGuardrailsCard` (starts at line 460) and the whole `export function CheckpointedPlanCard` (starts at line 911). Then run `npm run typecheck` and delete every import and helper that TypeScript now reports as unused (`noUnusedLocals` is on). Repeat until typecheck is silent.

- [ ] **Step 4: Remove the tests of deleted components from `ExperiencePolish.test.tsx`**

Delete these four `it(...)` blocks in full, identified by their titles:
- `renders an offline geographic JFK-to-Tokyo recovery map`
- `progresses the current trip from disruption through recovery`
- `keeps travel context collapsed until the presenter opens it`
- `marks only observed activity as verified`

Delete the imports `import { JourneyPanel } from '../JourneyPanel';` and `import { RecoveryRouteMap } from '../RecoveryRouteMap';`. Run `npm run typecheck` and remove any fixture helper it now reports as unused.

- [ ] **Step 5: Sweep for exports orphaned by the deletions**

```bash
for f in $(git ls-files 'src/showcase/*.ts' 'src/showcase/*.tsx' | command grep -v __tests__); do
  for name in $(command grep -oE 'export (function|const) [A-Za-z0-9_]+' "$f" | awk '{print $3}'); do
    others=$(command grep -rlw "$name" src --include='*.ts' --include='*.tsx' \
      | command grep -v __tests__ | command grep -vx "$f" | wc -l | tr -d ' ')
    own=$(command grep -cw "$name" "$f")
    [ "$others" -eq 0 ] && [ "$own" -le 1 ] && echo "$f $name"
  done
done
```

Expected: no output. Each line printed is an export used nowhere, not even in its own file: delete it, re-run typecheck, and repeat the sweep until it prints nothing.

- [ ] **Step 6: Remove the map dependencies**

```bash
command grep -rln "d3-geo\|topojson\|world-atlas" src || echo "no map imports left"
npm uninstall d3-geo topojson-client world-atlas @types/d3-geo @types/topojson-client
```

Expected: `no map imports left`, then npm removes the five packages.

- [ ] **Step 7: Verify nothing rendered changed**

Run the full verification gate. Expected: all green, with fewer Vitest tests than before (the deleted tests).

```bash
VISUAL_DIR=../.local/visual/baseline npm run visual
```

Expected: 21 passed, zero pixel differences. This run also refreshes `classes-*.json` in the baseline directory for Task 4.

- [ ] **Step 8: Commit**

```bash
git add -A src/showcase package.json package-lock.json
GIT_EDITOR=true git commit -q -F - <<'EOF'
Delete eight showcase components that nothing renders

RecoveryRouteMap, RecommendationCards, AuroraEvidenceStrip,
SessionReceipt, JourneyPanel, PhaseSelector, RecoveryGuardrailsCard and
CheckpointedPlanCard were imported only by their own tests. Their tests
and the map-only dependencies d3-geo, topojson-client and world-atlas go
with them. Screenshots of every view are unchanged.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 4: Delete dead CSS

**Files:**
- Create: `meridian/frontend/scripts/design-migration/lib.mjs`
- Create: `meridian/frontend/scripts/design-migration/lib.test.mjs`
- Create: `meridian/frontend/scripts/design-migration/remove-dead-rules.mjs`
- Modify: every stylesheet under `src/showcase/` that holds dead rules

**Interfaces:**
- Consumes: `classes-*.json` in `../.local/visual/baseline` (Task 2, refreshed in Task 3).
- Produces (`lib.mjs`), used by Tasks 5 to 8:
  - `FRONTEND`, `SRC`, `REPORT_DIR`, `TOKENS_FILE` (absolute paths)
  - `migrationCssFiles(): string[]` (showcase CSS, `index.css`, `preflight.css`; never `tokens.css`)
  - `transformCss(files: string[], visit: (root: postcss.Root, file: string) => void): number`
  - `writeReport(name: string, lines: string[]): string`
  - `subjectOf(branch: string): string` (last compound selector, parentheses respected)
  - `baseSelector(selector: string): string` (theme and projector attributes and a leading `.mds-root` removed)
  - `unscopeSelector(selector: string): string` (theme and projector attributes removed, `.mds-root` kept)
  - regexes `CONTROL`, `FLOAT`, `SHELL`, `LIGHT`, `DARK`, `PROJECTOR`

- [ ] **Step 1: Write the failing helper tests**

Create `scripts/design-migration/lib.test.mjs`:

```js
// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { baseSelector, CONTROL, FLOAT, SHELL, subjectOf, unscopeSelector } from './lib.mjs';

describe('selector helpers', () => {
  it('finds the subject compound outside parentheses and brackets', () => {
    expect(subjectOf('.mds-root .mds-card > .mds-title')).toBe('.mds-title');
    expect(subjectOf('.mds-root :is(.a, .b)')).toBe(':is(.a, .b)');
    expect(subjectOf(".mds-root[data-theme='light'] .mds-x:hover")).toBe('.mds-x:hover');
  });

  it('normalises theme-scoped selectors to their base form', () => {
    expect(baseSelector('.mds-root[data-theme="light"] .mds-foo')).toBe('.mds-foo');
    expect(baseSelector('.mds-root .mds-foo, .mds-bar')).toBe('.mds-bar, .mds-foo');
    expect(baseSelector(".mds-root[data-projector-readability='true'] .mds-foo")).toBe('.mds-foo');
  });

  it('removes only the theme attribute when unscoping', () => {
    expect(unscopeSelector('.mds-root[data-theme="light"] .mds-foo')).toBe('.mds-root .mds-foo');
  });

  it('classifies subjects', () => {
    expect(CONTROL.test('textarea')).toBe(true);
    expect(CONTROL.test('.mds-chat-send')).toBe(true);
    expect(CONTROL.test('.mds-card')).toBe(false);
    expect(FLOAT.test('.mds-popover')).toBe(true);
    expect(FLOAT.test('.mds-popover-primary')).toBe(false);
    expect(SHELL.test('.mds-desktop-app.is-discovery')).toBe(true);
    expect(SHELL.test('.mds-desktop-app-card')).toBe(false);
  });
});
```

- [ ] **Step 2: Run and confirm the failure**

Run: `npx vitest --run scripts/design-migration/lib.test.mjs`
Expected: FAIL, `Failed to load url ./lib.mjs`.

- [ ] **Step 3: Implement the helpers**

Create `scripts/design-migration/lib.mjs`:

```js
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import postcss from 'postcss';

export const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
export const SRC = path.join(FRONTEND, 'src');
export const REPORT_DIR = path.resolve(FRONTEND, '../.local/design-migration');
export const TOKENS_FILE = path.join(SRC, 'showcase', 'tokens.css');

export const CONTROL = new RegExp('(?:^|[^\\w-])(?:input|textarea|select|button)(?![\\w-])'
  + '|[.-](?:btn|toggle|switch|checkbox|radio|fields?|composer|search|stepper|segmented|send'
  + '|controls?|picker)(?![\\w])', 'i');
export const FLOAT = /(?:^|[.\s])[\w-]*(?:tooltip|popover|toast|menu|dropdown|flyout)(?![\w-])/i;
export const SHELL = new RegExp('(?:^|[^\\w-])(?:html|body)(?![\\w-])|:root|\\.(?:mds-root'
  + '|mds-desktop-app|mds-desktop-main|mds-desktop-sidebar|mds-desktop-right|mds-desktop-dock'
  + '|mds-shell-header|route-skeleton)(?![\\w-])');
export const LIGHT = /\[data-theme=(['"]?)light\1\]/;
export const DARK = /\[data-theme=(['"]?)dark\1\]/;
export const PROJECTOR = /\[data-projector-readability=(['"]?)true\1\]/;
const SCOPE_ATTRIBUTE = /\[data-(?:theme|projector-readability)=(['"]?)[\w-]+\1\]/g;

export function migrationCssFiles() {
  const walk = dir => fs.readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return walk(full);
    return entry.name.endsWith('.css') && full !== TOKENS_FILE ? [full] : [];
  });
  return [...walk(path.join(SRC, 'showcase')), path.join(SRC, 'index.css'), path.join(SRC, 'preflight.css')];
}

export function transformCss(files, visit) {
  let changed = 0;
  for (const file of files) {
    const before = fs.readFileSync(file, 'utf8');
    const root = postcss.parse(before, { from: file });
    visit(root, path.relative(FRONTEND, file));
    const after = root.toString();
    if (after !== before) {
      fs.writeFileSync(file, after);
      changed += 1;
    }
  }
  return changed;
}

export function writeReport(name, lines) {
  fs.mkdirSync(REPORT_DIR, { recursive: true });
  const file = path.join(REPORT_DIR, `${name}.md`);
  fs.writeFileSync(file, `${lines.join('\n')}\n`);
  return file;
}

export function subjectOf(branch) {
  const parts = [];
  let depth = 0;
  let current = '';
  for (const ch of branch.trim()) {
    if (ch === '(' || ch === '[') depth += 1;
    if (ch === ')' || ch === ']') depth -= 1;
    if (depth === 0 && /[\s>+~]/.test(ch)) {
      if (current) parts.push(current);
      current = '';
    } else {
      current += ch;
    }
  }
  if (current) parts.push(current);
  return parts.at(-1) ?? '';
}

export function baseSelector(selector) {
  return postcss.list.comma(selector)
    .map(branch => branch.replace(SCOPE_ATTRIBUTE, '').replace(/^\.mds-root\s+/, '').replace(/\s+/g, ' ').trim())
    .sort()
    .join(', ');
}

export function unscopeSelector(selector) {
  return postcss.list.comma(selector).map(branch => branch.replace(SCOPE_ATTRIBUTE, '').trim()).join(', ');
}
```

- [ ] **Step 4: Run and confirm the pass**

Run: `npx vitest --run scripts/design-migration/lib.test.mjs`
Expected: PASS, 4 tests.

- [ ] **Step 5: Write the dead-rule codemod**

Create `scripts/design-migration/remove-dead-rules.mjs`:

```js
import fs from 'node:fs';
import path from 'node:path';
import { FRONTEND, SRC, migrationCssFiles, transformCss, writeReport } from './lib.mjs';

const probeDir = path.resolve(FRONTEND, process.argv[2] ?? '../.local/visual/baseline');

function sourceFiles(dir) {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap(entry => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === '__tests__' ? [] : sourceFiles(full);
    return /\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name) ? [full] : [];
  });
}

const source = sourceFiles(SRC).map(file => fs.readFileSync(file, 'utf8')).join('\n');
const dynamicPrefixes = [
  ...source.matchAll(/((?:mds|mc)-[\w-]*-)\$\{/g),
  ...source.matchAll(/['"]((?:mds|mc)-[\w-]*-)['"]\s*\+/g),
].map(match => match[1]);
const referenced = cls => new RegExp(`(?<![\\w-])${cls}(?![\\w-])`).test(source)
  || dynamicPrefixes.some(prefix => cls.startsWith(prefix));

const cssClasses = new Set(migrationCssFiles().flatMap(file =>
  [...fs.readFileSync(file, 'utf8').matchAll(/\.((?:mds|mc)-[\w-]+)/g)].map(match => match[1])));
const dead = new Set([...cssClasses].filter(cls => !referenced(cls)));

const probed = new Set(fs.readdirSync(probeDir).filter(file => file.startsWith('classes-'))
  .flatMap(file => JSON.parse(fs.readFileSync(path.join(probeDir, file), 'utf8'))));
if (probed.size === 0) throw new Error(`No classes-*.json in ${probeDir}; run npm run visual first.`);
const contradicted = [...dead].filter(cls => probed.has(cls));
if (contradicted.length) {
  throw new Error(`Rendered classes the static scan calls dead: ${contradicted.join(', ')}`);
}

function outsideParens(branch) {
  let text = branch;
  while (/\([^()]*\)/.test(text)) text = text.replace(/\([^()]*\)/g, '');
  return text;
}
const isDeadBranch = branch =>
  [...outsideParens(branch).matchAll(/\.((?:mds|mc)-[\w-]+)/g)].some(match => dead.has(match[1]));

let removed = 0;
let trimmed = 0;
transformCss(migrationCssFiles(), root => {
  root.walkRules(rule => {
    if (rule.parent?.type === 'atrule' && /keyframes$/i.test(rule.parent.name)) return;
    const kept = rule.selectors.filter(branch => !isDeadBranch(branch));
    if (kept.length === 0) {
      rule.remove();
      removed += 1;
    } else if (kept.length < rule.selectors.length) {
      rule.selectors = kept;
      trimmed += 1;
    }
  });
  let emptied = true;
  while (emptied) {
    emptied = false;
    root.walkAtRules(atRule => {
      if (atRule.nodes && atRule.nodes.length === 0) {
        atRule.remove();
        emptied = true;
      }
    });
  }
});

const report = writeReport('dead-css', [
  '# Dead CSS', '', `${dead.size} dead classes, ${removed} rules removed, ${trimmed} lists trimmed.`, '',
  ...[...dead].sort().map(cls => `- ${cls}`),
]);
console.log(`dead classes ${dead.size}; removed ${removed} rules; trimmed ${trimmed}; report ${report}`);
```

A class inside `:not()` or `:is()` does not make a branch dead, which is why parenthesized content is ignored.

- [ ] **Step 6: Run the codemod**

```bash
node scripts/design-migration/remove-dead-rules.mjs
git diff --stat -- src
```

Expected: at least 164 dead classes and roughly 2,200 or more deleted lines across the showcase stylesheets. If it throws `Rendered classes the static scan calls dead`, those classes are built in a way the scan misses: add their prefix pattern to `dynamicPrefixes` and rerun.

- [ ] **Step 7: Verify nothing rendered changed**

Run the full verification gate (`npm run lint` must still pass: removing rules never adds violations, but a file whose last color literal was dead now has a stale exemption; delete each stale entry the check names from `exemptions.mjs` and rerun).

```bash
VISUAL_DIR=../.local/visual/baseline npm run visual
```

Expected: 21 passed, zero pixel differences.

- [ ] **Step 8: Commit**

```bash
git add scripts/design-migration src/showcase src/index.css scripts/design-tokens/exemptions.mjs
GIT_EDITOR=true git commit -q -F - <<'EOF'
Delete CSS rules for classes no component renders

remove-dead-rules.mjs deletes rules whose every selector names a class
absent from the TS sources and unseen by the screenshot harness's class
probe. Screenshots of every view are unchanged.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 5: Depth: flat fills, one float shadow, focus outlines

**Files:**
- Create: `meridian/frontend/src/showcase/tokens.css`
- Create: `meridian/frontend/scripts/design-migration/flatten-depth.mjs`
- Modify: `meridian/frontend/src/index.css` (import tokens)
- Modify: showcase stylesheets (codemod), `src/showcase/projectorReadability.css` (delete the decorative-edge block)
- Modify: `meridian/frontend/scripts/design-tokens/exemptions.mjs` (delete `blur`, `shadow`, `gradient`)

**Interfaces:**
- Consumes: `CONTROL`, `FLOAT`, `migrationCssFiles`, `subjectOf`, `transformCss`, `writeReport` from `lib.mjs`.
- Produces: `--mds-shadow-float`, `--mds-image-scrim` on `:root`; focus styles as `outline: 2px solid var(--mds-blue); outline-offset: 2px` (Task 6 turns `--mds-blue` in outlines into `--mds-tint`).

- [ ] **Step 1: Create the token file with depth tokens**

Create `src/showcase/tokens.css`:

```css
:root {
  --mds-shadow-float: 0 12px 32px rgb(0 0 0 / 0.6), 0 0 0 1px rgb(255 255 255 / 0.12);
  --mds-image-scrim: linear-gradient(to top, rgb(0 0 0 / 0.72), rgb(0 0 0 / 0) 60%);
}

:root[data-theme='light'] {
  --mds-shadow-float: 0 12px 32px rgb(0 0 0 / 0.14), 0 0 0 1px rgb(0 0 0 / 0.08);
}
```

In `src/index.css`, add directly under `@import './preflight.css';`:

```css
@import './showcase/tokens.css';
```

- [ ] **Step 2: Write the depth codemod**

Create `scripts/design-migration/flatten-depth.mjs`:

```js
import { CONTROL, FLOAT, migrationCssFiles, subjectOf, transformCss, writeReport } from './lib.mjs';

const GRADIENT = /\b(?:repeating-)?(?:linear|radial|conic)-gradient\(/;
const MASK_PROPS = new Set(['mask', 'mask-image', '-webkit-mask', '-webkit-mask-image']);
const IMAGE_SUBJECT = /(?:photo|visual|image|img|hero|thumb|media|cover)/i;
const STATE = /\.is-(?:selected|active|featured|current|open|checked)|\[aria-(?:selected|current|pressed)|:checked/;
const STOP = /#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b|rgba?\([^)]*\)|var\(--[\w-]+\)|\btransparent\b/;
const FADES_OUT = /\btransparent\b|,\s*0\)|\/\s*0\)/;
const notes = [];

function note(file, decl, text) {
  const where = decl.parent.selector ?? `@${decl.parent.name} ${decl.parent.params}`;
  notes.push(`- ${file}:${decl.source?.start?.line} \`${where}\` ${decl.prop}: ${text}`);
}

function splitTopLevel(value) {
  const layers = [];
  let depth = 0;
  let current = '';
  for (const ch of value) {
    if (ch === '(') depth += 1;
    if (ch === ')') depth -= 1;
    if (ch === ',' && depth === 0) {
      layers.push(current.trim());
      current = '';
    } else {
      current += ch;
    }
  }
  return [...layers, current.trim()];
}

function flattenShadow(decl, rule, subjects, file) {
  if (decl.prop.startsWith('--')) return decl.remove();
  if (decl.value.trim() === 'none') return undefined;
  if (/:focus/.test(rule.selector ?? '')) {
    if (!rule.some(node => node.type === 'decl' && node.prop === 'outline')) {
      decl.cloneBefore({ prop: 'outline', value: '2px solid var(--mds-blue)' });
      decl.cloneBefore({ prop: 'outline-offset', value: '2px' });
    }
    note(file, decl, 'focus shadow became an outline');
    return decl.remove();
  }
  if (subjects.some(subject => FLOAT.test(subject))) {
    decl.value = 'var(--mds-shadow-float)';
    return undefined;
  }
  if (subjects.some(subject => CONTROL.test(subject))) {
    note(file, decl, `REVIEW control shadow kept: ${decl.value}`);
    return undefined;
  }
  return decl.remove();
}

function flattenGradient(decl, subjects, file) {
  const layers = splitTopLevel(decl.value);
  if (layers.some(layer => /url\(/.test(layer))) {
    note(file, decl, 'REVIEW layered image background left untouched');
    return;
  }
  if (subjects.some(subject => IMAGE_SUBJECT.test(subject)) && FADES_OUT.test(decl.value)) {
    decl.value = 'var(--mds-image-scrim)';
    note(file, decl, 'image scrim');
    return;
  }
  if (decl.prop === 'border-image') {
    decl.remove();
    return;
  }
  const base = layers.at(-1);
  const source = GRADIENT.test(base) ? base.slice(base.search(GRADIENT)) : base;
  const stop = source.match(STOP)?.[0] ?? 'transparent';
  if (decl.prop === 'background-image') decl.prop = 'background-color';
  decl.value = stop;
  if (/background-clip/.test(decl.parent.toString())) note(file, decl, 'REVIEW gradient text');
}

function dropBorder(decl, rule, file) {
  if (STATE.test(rule.selector)) {
    note(file, decl, 'REVIEW selection border kept');
    return;
  }
  if (decl.prop === 'border') {
    if (/^(?:0|none)$/.test(decl.value.trim())) return;
    decl.value = '0';
    note(file, decl, 'decorative border removed');
  } else {
    decl.remove();
  }
  rule.walkDecls(/^border-color$/, sibling => { sibling.remove(); });
}

function visitDecl(decl, file) {
  const rule = decl.parent;
  const subjects = rule.type === 'rule' ? rule.selectors.map(subjectOf) : [];
  const prop = decl.prop.toLowerCase();
  if (prop === 'backdrop-filter' || prop === '-webkit-backdrop-filter' || prop === 'text-shadow') {
    decl.remove();
  } else if (prop === 'filter' && /drop-shadow\(/.test(decl.value)) {
    note(file, decl, 'drop-shadow removed');
    decl.remove();
  } else if (prop === 'box-shadow' || (prop.startsWith('--') && /shadow/.test(prop))) {
    flattenShadow(decl, rule, subjects, file);
  } else if (GRADIENT.test(decl.value) && !MASK_PROPS.has(prop)) {
    flattenGradient(decl, subjects, file);
  } else if (/^border(?:-width|-style)?$/.test(prop) && rule.type === 'rule'
    && !subjects.some(subject => CONTROL.test(subject) || FLOAT.test(subject))) {
    dropBorder(decl, rule, file);
  }
}

transformCss(migrationCssFiles(), (root, file) => {
  root.walkAtRules('supports', atRule => { if (/backdrop-filter/.test(atRule.params)) atRule.remove(); });
  root.walkDecls(decl => { visitDecl(decl, file); });
  root.walkRules(rule => { if (rule.nodes.length === 0) rule.remove(); });
});

const review = notes.filter(line => line.includes('REVIEW'));
const report = writeReport('depth', ['# Depth', '', `${review.length} REVIEW items`, '', ...notes]);
console.log(`${notes.length} notes, ${review.length} to review; report ${report}`);
```

- [ ] **Step 3: Run the codemod and remove the decorative-edge patch**

```bash
node scripts/design-migration/flatten-depth.mjs
```

In `src/showcase/projectorReadability.css`, delete every rule under the comment `/* Panel edges are decorative. ...` whose declarations are only `border-color: transparent`, `box-shadow: none` or `--mc-divider`, and delete the comment. These rules existed to hide borders that no longer exist.

- [ ] **Step 4: Resolve every REVIEW line**

Open `../.local/design-migration/depth.md`. For each line containing `REVIEW`:
- `control shadow kept`: an inset ring used as a control edge becomes `border: 1px solid var(--mds-line)` (keep the old variable name; Task 6 maps it to `--mds-control-line`); any other control shadow is deleted.
- `layered image background`: keep the `url()` layer and replace each gradient layer with `var(--mds-image-scrim)` if it darkens the image for text, otherwise delete that layer.
- `gradient text`: delete `background-clip: text`, `-webkit-background-clip: text` and `-webkit-text-fill-color: transparent` from that rule and set `color` to the flattened stop value.
- `selection border kept`: keep it if it is the only indication of the selected state; otherwise delete it.

- [ ] **Step 5: Tighten the ratchet**

Delete the `blur`, `shadow` and `gradient` keys from `scripts/design-tokens/exemptions.mjs`.

Run: `npm run lint`
Expected: `design tokens: N files pass`. Any remaining violation of those three rules is fixed by hand using the Step 4 rules.

- [ ] **Step 6: Verify and review**

Run the full verification gate. Then capture the review set:

```bash
VISUAL_DIR=../.local/visual/task-05 npm run visual -- --update-snapshots=all
```

Review the screenshots against the baseline. Expected differences: no glows, no gradient panels, no blurred panels, cards without outlines. Unexpected differences to fix before committing: missing text, controls without visible edges, invisible selected states, text on photos without the scrim.

- [ ] **Step 7: Commit**

```bash
git add src/showcase src/index.css scripts/design-migration scripts/design-tokens/exemptions.mjs
GIT_EDITOR=true git commit -q -F - <<'EOF'
Flatten showcase depth to fills, one float shadow and outlines

Decorative gradients become their base color, backdrop blur and text
shadows are gone, and literal shadows are removed except on menus,
tooltips, popovers and toasts, which use --mds-shadow-float. Focus rings
become a 2px outline. Cards and panels lose their all-side borders.
Photos keep a --mds-image-scrim for text.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 6: Color roles

**Files:**
- Modify: `meridian/frontend/src/showcase/tokens.css` (color roles)
- Create: `meridian/frontend/scripts/design-migration/color.mjs`
- Create: `meridian/frontend/scripts/design-migration/color.test.mjs`
- Create: `meridian/frontend/scripts/design-migration/variable-map.mjs`
- Create: `meridian/frontend/scripts/design-migration/map-colors.mjs`
- Modify: `meridian/frontend/e2e/accessibility.spec.ts:86-88`
- Modify: `meridian/frontend/src/stage/demo-stage.css:1527-1534`
- Modify: showcase stylesheets, `src/index.css`, `src/preflight.css` (codemod)
- Modify: `meridian/frontend/scripts/design-tokens/exemptions.mjs` (delete `color`)

**Interfaces:**
- Consumes: `lib.mjs` from Task 4; the depth tokens from Task 5.
- Produces the color tokens every later task and all stylesheets use: `--mds-ground`, `--mds-surface`, `--mds-surface-2`, `--mds-separator`, `--mds-control-line`, `--mds-label`, `--mds-label-2`, `--mds-label-3`, `--mds-blue`, `--mds-tint`, `--mds-action`, `--mds-action-hover`, `--mds-on-action`, `--mds-green`, `--mds-yellow`, `--mds-red`, `--mds-cyan`, `--mds-<hue>-fill` for blue, green, yellow, red and cyan, `--mds-scrim`, `--mds-skeleton`, `--mds-skeleton-shimmer`.
- Produces (`color.mjs`): `parseColor(text): {r,g,b,a}`, `mapLiteral({ color, role, theme, subject }): { token, review, why }`, `roleOf(prop, subject): 'text'|'fill'|'line'|'accent'|null`, `COLOR_IN_VALUE`.

- [ ] **Step 1: Point the contrast test at the new tokens and watch it fail**

In `e2e/accessibility.spec.ts`, replace the `colors` evaluation (currently reading `--mc-${name}`) with:

```ts
    const colors = await page.locator('.mds-root').evaluate(el => {
      const css = getComputedStyle(el);
      const tokens = {
        surface: '--mds-surface', soft: '--mds-surface-2', muted: '--mds-label-2',
        line: '--mds-control-line', accent: '--mds-tint',
      };
      return Object.fromEntries(Object.entries(tokens)
        .map(([name, token]) => [name, css.getPropertyValue(token).trim()]));
    });
```

Run: `npx playwright test -g "blue actions"`
Expected: 4 failed, because none of these tokens exist yet (`contrastRatio` returns `NaN`).

- [ ] **Step 2: Add the color roles to `tokens.css`**

Replace the whole of `src/showcase/tokens.css` with:

```css
:root {
  color-scheme: dark;
  --mds-ground: #000000;
  --mds-surface: #252527;
  --mds-surface-2: #363638;
  --mds-separator: #3f3f41;
  --mds-control-line: #848486;
  --mds-label: #ffffff;
  --mds-label-2: #c8c8ca;
  --mds-label-3: #a4a4a6;
  --mds-blue: #74acfe;
  --mds-tint: var(--mds-blue);
  --mds-action: #92beff;
  --mds-action-hover: #aecfff;
  --mds-on-action: #000000;
  --mds-green: #6ee750;
  --mds-yellow: #f3c300;
  --mds-red: #fe897a;
  --mds-cyan: #64d2ff;
  --mds-blue-fill: color-mix(in srgb, var(--mds-blue) 16%, var(--mds-surface));
  --mds-green-fill: color-mix(in srgb, var(--mds-green) 16%, var(--mds-surface));
  --mds-yellow-fill: color-mix(in srgb, var(--mds-yellow) 16%, var(--mds-surface));
  --mds-red-fill: color-mix(in srgb, var(--mds-red) 16%, var(--mds-surface));
  --mds-cyan-fill: color-mix(in srgb, var(--mds-cyan) 16%, var(--mds-surface));
  --mds-scrim: rgb(0 0 0 / 0.5);
  --mds-skeleton: var(--mds-surface-2);
  --mds-skeleton-shimmer: color-mix(in srgb, var(--mds-label) 8%, var(--mds-surface-2));
  --mds-shadow-float: 0 12px 32px rgb(0 0 0 / 0.6), 0 0 0 1px var(--mds-separator);
  --mds-image-scrim: linear-gradient(to top, rgb(0 0 0 / 0.72), rgb(0 0 0 / 0) 60%);
}

:root[data-theme='light'] {
  color-scheme: light;
  --mds-ground: #ededef;
  --mds-surface: #ffffff;
  --mds-surface-2: #ededef;
  --mds-separator: #d9d9db;
  --mds-control-line: #838385;
  --mds-label: #1d1d1f;
  --mds-label-2: #4d4d4f;
  --mds-label-3: #676769;
  --mds-blue: #005dca;
  --mds-action: #014fae;
  --mds-action-hover: #00459a;
  --mds-on-action: #ffffff;
  --mds-green: #207101;
  --mds-yellow: #785f03;
  --mds-red: #c60213;
  --mds-cyan: #056897;
  --mds-blue-fill: color-mix(in srgb, var(--mds-blue) 10%, var(--mds-surface));
  --mds-green-fill: color-mix(in srgb, var(--mds-green) 10%, var(--mds-surface));
  --mds-yellow-fill: color-mix(in srgb, var(--mds-yellow) 10%, var(--mds-surface));
  --mds-red-fill: color-mix(in srgb, var(--mds-red) 10%, var(--mds-surface));
  --mds-cyan-fill: color-mix(in srgb, var(--mds-cyan) 10%, var(--mds-surface));
  --mds-scrim: rgb(0 0 0 / 0.32);
  --mds-shadow-float: 0 12px 32px rgb(0 0 0 / 0.14), 0 0 0 1px var(--mds-separator);
}
```

`--mds-tint`, `--mds-skeleton`, `--mds-skeleton-shimmer` and `--mds-image-scrim` are declared once on `:root`; the theme attribute is on the same element, so they resolve against each theme's values.

- [ ] **Step 3: Write the failing color-mapping tests**

Create `scripts/design-migration/color.test.mjs`:

```js
// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { mapLiteral, parseColor, roleOf } from './color.mjs';

const map = (literal, role, theme = 'dark', subject = '.mds-x') =>
  mapLiteral({ color: parseColor(literal), role, theme, subject });

describe('parseColor', () => {
  it('reads hex, rgb, rgba and named colors', () => {
    expect(parseColor('#fff')).toEqual({ r: 255, g: 255, b: 255, a: 1 });
    expect(parseColor('#00000080').a).toBeCloseTo(0.502, 2);
    expect(parseColor('rgba(47, 140, 255, 0.12)')).toEqual({ r: 47, g: 140, b: 255, a: 0.12 });
    expect(parseColor('rgb(0 0 0 / 50%)')).toEqual({ r: 0, g: 0, b: 0, a: 0.5 });
    expect(parseColor('white')).toEqual({ r: 255, g: 255, b: 255, a: 1 });
  });
});

describe('mapLiteral', () => {
  it('maps dark text by contrast against the black ground', () => {
    expect(map('#ffffff', 'text').token).toBe('--mds-label');
    expect(map('#d0d0d0', 'text').token).toBe('--mds-label-2');
    expect(map('rgba(255, 255, 255, 0.5)', 'text').token).toBe('--mds-label-3');
    expect(map('#8fa0b4', 'text').token).toBe('--mds-label-3');
    expect(map('#9bc9ff', 'text').token).toBe('--mds-blue');
    expect(map('#141414', 'text')).toMatchObject({ token: '--mds-ground', review: true });
  });

  it('maps light text by contrast against the white ground', () => {
    expect(map('#111111', 'text', 'light').token).toBe('--mds-label');
    expect(map('#555555', 'text', 'light').token).toBe('--mds-label-2');
    expect(map('#6c7d8f', 'text', 'light').token).toBe('--mds-label-3');
  });

  it('maps fills by tint, alpha and subject', () => {
    expect(map('rgba(255, 255, 255, 0.04)', 'fill').token).toBe('--mds-surface-2');
    expect(map('rgba(47, 140, 255, 0.12)', 'fill').token).toBe('--mds-blue-fill');
    expect(map('#2f8cff', 'fill', 'dark', '.mds-trip-dot').token).toBe('--mds-blue');
    expect(map('#000000', 'fill', 'dark', '.mds-desktop-app').token).toBe('--mds-ground');
    expect(map('#000000', 'fill', 'dark', '.mds-card')).toMatchObject({ token: '--mds-surface', review: true });
    expect(map('#176fb7', 'fill', 'dark', '.mds-chat-send')).toMatchObject({ token: '--mds-action', review: true });
    expect(map('#ffffff', 'fill', 'light', '.mds-card').token).toBe('--mds-surface');
  });

  it('maps lines by subject and focus by tint', () => {
    expect(map('rgba(255, 255, 255, 0.08)', 'line', 'dark', '.mds-card').token).toBe('--mds-separator');
    expect(map('rgba(255, 255, 255, 0.08)', 'line', 'dark', 'textarea').token).toBe('--mds-control-line');
    expect(map('#2f8cff', 'accent').token).toBe('--mds-tint');
  });
});

describe('roleOf', () => {
  it('derives a role from the property and SVG subject', () => {
    expect(roleOf('color', '.x')).toBe('text');
    expect(roleOf('background-color', '.x')).toBe('fill');
    expect(roleOf('border-top', '.x')).toBe('line');
    expect(roleOf('outline', '.x')).toBe('accent');
    expect(roleOf('fill', '.x rect')).toBe('fill');
    expect(roleOf('stroke', '.x rect')).toBe('line');
    expect(roleOf('fill', '.x text')).toBe('text');
    expect(roleOf('content', '.x')).toBe(null);
  });
});
```

Run: `npx vitest --run scripts/design-migration/color.test.mjs`
Expected: FAIL, `Failed to load url ./color.mjs`.

- [ ] **Step 4: Implement color parsing and mapping**

Create `scripts/design-migration/color.mjs`:

```js
import { CONTROL, SHELL } from './lib.mjs';

const WHITE = { r: 255, g: 255, b: 255 };
const BLACK = { r: 0, g: 0, b: 0 };
const TEXT_THRESHOLDS = { dark: { label: 15, label2: 9 }, light: { label: 13, label2: 6.5 } };
const SVG_SHAPE = /(?:^|[^\w-])(?:rect|circle|ellipse|polygon|path|line|marker)(?![\w-])/;

export const COLOR_IN_VALUE = new RegExp('#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\\b'
  + '|rgba?\\(\\s*[\\d.]+%?[\\s,]+[\\d.]+%?[\\s,]+[\\d.]+%?\\s*(?:[,/]\\s*[\\d.]+%?)?\\s*\\)'
  + '|(?<![-\\w])(?:white|black)(?![-\\w])', 'g');

export function parseColor(text) {
  const value = text.trim().toLowerCase();
  if (value === 'white') return { ...WHITE, a: 1 };
  if (value === 'black') return { ...BLACK, a: 1 };
  if (value.startsWith('#')) {
    let hex = value.slice(1);
    if (hex.length <= 4) hex = [...hex].map(ch => ch + ch).join('');
    const channel = index => parseInt(hex.slice(index, index + 2), 16);
    return { r: channel(0), g: channel(2), b: channel(4), a: hex.length === 8 ? channel(6) / 255 : 1 };
  }
  const parts = value.match(/[\d.]+%?/g) ?? [];
  const number = part => (part.endsWith('%') ? parseFloat(part) * 2.55 : parseFloat(part));
  const [r, g, b] = parts.slice(0, 3).map(number);
  const alpha = parts[3];
  const a = alpha === undefined ? 1 : alpha.endsWith('%') ? parseFloat(alpha) / 100 : parseFloat(alpha);
  return { r, g, b, a };
}

const linear = channel => {
  const c = channel / 255;
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
};
const luminance = ({ r, g, b }) => 0.2126 * linear(r) + 0.7152 * linear(g) + 0.0722 * linear(b);
const contrast = (x, y) => {
  const [hi, lo] = [luminance(x), luminance(y)].sort((p, q) => q - p);
  return (hi + 0.05) / (lo + 0.05);
};
const composite = ({ r, g, b, a }, ground) => ({
  r: a * r + (1 - a) * ground.r, g: a * g + (1 - a) * ground.g, b: a * b + (1 - a) * ground.b,
});

function oklch({ r, g, b }) {
  const [lr, lg, lb] = [r, g, b].map(linear);
  const l = Math.cbrt(0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb);
  const m = Math.cbrt(0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb);
  const s = Math.cbrt(0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb);
  const A = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s;
  const B = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s;
  return { C: Math.hypot(A, B), H: ((Math.atan2(B, A) * 180) / Math.PI + 360) % 360 };
}

function hueFamily(H) {
  if (H < 55 || H >= 340) return 'red';
  if (H < 115) return 'yellow';
  if (H < 175) return 'green';
  if (H < 240) return 'cyan';
  if (H < 290) return 'blue';
  return 'purple';
}

const ok = token => ({ token, review: false, why: '' });
const review = (token, why) => ({ token, review: true, why });

export function roleOf(prop, subject) {
  if (prop === 'fill' || prop === 'stroke') {
    if (!SVG_SHAPE.test(subject)) return 'text';
    return prop === 'fill' ? 'fill' : 'line';
  }
  if (['color', '-webkit-text-fill-color', 'caret-color', 'text-decoration-color', 'stop-color']
    .includes(prop)) return 'text';
  if (prop.startsWith('background')) return 'fill';
  if (prop.startsWith('outline') || prop === 'accent-color') return 'accent';
  if (prop.startsWith('border') || prop === 'column-rule') return 'line';
  return null;
}

function mapText(ratio, tint, theme) {
  const t = TEXT_THRESHOLDS[theme];
  if (tint && ratio >= 3 && ratio < t.label) return ok(`--mds-${tint}`);
  if (ratio >= t.label) return ok('--mds-label');
  if (ratio >= t.label2) return ok('--mds-label-2');
  if (ratio >= 3) return ok('--mds-label-3');
  return review('--mds-ground', 'text under 3:1 on the page; likely inverse text on a light fill');
}

function mapLine(color, tint, subject) {
  if (tint) {
    return color.a >= 0.4 ? ok(`--mds-${tint}`) : review(`--mds-${tint}`, 'faint tinted border');
  }
  return ok(CONTROL.test(subject) ? '--mds-control-line' : '--mds-separator');
}

function mapNeutralFill(color, solid, theme, subject) {
  const whiteish = color.r + color.g + color.b > 382;
  if (color.a < 1) {
    if (whiteish) return ok(theme === 'light' ? '--mds-surface' : '--mds-surface-2');
    return color.a >= 0.3 ? review('--mds-scrim', 'dark overlay') : ok('--mds-surface-2');
  }
  const y = luminance(solid);
  if (theme === 'light') {
    if (y >= 0.97) return ok(SHELL.test(subject) ? '--mds-ground' : '--mds-surface');
    if (y >= 0.7) return ok('--mds-surface-2');
    return review(y < 0.1 ? '--mds-label' : '--mds-surface-2', 'dark fill in the light theme');
  }
  if (y <= 0.005) {
    return SHELL.test(subject) ? ok('--mds-ground')
      : review('--mds-surface', 'black panel becomes a grouped surface');
  }
  if (y <= 0.06) return ok('--mds-surface-2');
  return review(y > 0.4 ? '--mds-label' : '--mds-surface-2', 'light fill in the dark theme');
}

function mapFill({ color, solid, ratio, tint, theme, subject }) {
  if (!tint) return mapNeutralFill(color, solid, theme, subject);
  const faint = color.a < 0.35 || ratio < (theme === 'light' ? 1.5 : 2);
  if (faint) return ok(`--mds-${tint}-fill`);
  if (tint === 'blue' && CONTROL.test(subject)) {
    return review('--mds-action', 'solid blue control fill; its label must use --mds-on-action');
  }
  return ok(`--mds-${tint}`);
}

/**
 * Chooses the role token for one color literal.
 *
 * @param {{ color: {r:number,g:number,b:number,a:number}, role: string|null,
 *   theme: 'dark'|'light', subject: string }} input
 * @returns {{ token: string, review: boolean, why: string }}
 */
export function mapLiteral({ color, role, theme, subject }) {
  const ground = theme === 'light' ? WHITE : BLACK;
  const solid = composite(color, ground);
  const ratio = contrast(solid, ground);
  const { C, H } = oklch(color);
  const family = C >= 0.045 ? hueFamily(H) : null;
  const tint = family && family !== 'purple' ? family : null;
  if (role === 'accent') return ok('--mds-tint');
  if (role === 'text') return mapText(ratio, tint, theme);
  if (role === 'line') return mapLine(color, tint, subject);
  if (role === 'fill') return mapFill({ color, solid, ratio, tint, theme, subject });
  return review('--mds-label', 'property has no color role');
}
```

Run: `npx vitest --run scripts/design-migration/color.test.mjs`
Expected: PASS, 6 tests. If a threshold case fails, compute the contrast the test implies and adjust the test only when the spec's role table supports the other token; never adjust the thresholds to fit one color.

- [ ] **Step 5: Write the variable map**

Create `scripts/design-migration/variable-map.mjs`:

```js
export const RENAME = {
  '--mc-ink': '--mds-label', '--mds-on-surface': '--mds-label', '--mds-ink': '--mds-label',
  '--proof-ink': '--mds-label', '--app-fg': '--mds-label', '--mds-chrome-nav-hover': '--mds-label',
  '--mds-chrome-nav-active': '--mds-label', '--mds-chrome-brand': '--mds-label',
  '--mds-chrome-account': '--mds-label', '--mds-tooltip-fg': '--mds-label',
  '--mc-muted': '--mds-label-2', '--mds-on-surface-soft': '--mds-label-2', '--mds-muted': '--mds-label-2',
  '--mds-chrome-nav': '--mds-label-2', '--mds-chrome-control': '--mds-label-2',
  '--mds-chrome-status': '--mds-label-2', '--proof-dim': '--mds-label-2',
  '--mds-on-surface-faint': '--mds-label-3', '--mds-dim': '--mds-label-3',
  '--mds-chrome-account-muted': '--mds-label-3',
  '--mc-ground': '--mds-ground', '--mds-bg': '--mds-ground', '--mds-app-surface': '--mds-ground',
  '--app-bg': '--mds-ground', '--mds-shell': '--mds-ground', '--mds-chrome-sidebar-background': '--mds-ground',
  '--mds-chrome-right-background': '--mds-ground', '--mds-chrome-main-background': '--mds-ground',
  '--mc-surface': '--mds-surface', '--mds-card': '--mds-surface', '--mds-panel': '--mds-surface',
  '--mds-experience-panel': '--mds-surface', '--mds-side-panel-background': '--mds-surface',
  '--proof-panel': '--mds-surface', '--mds-chrome-control-background': '--mds-surface',
  '--mc-soft': '--mds-surface-2', '--mds-shell-elevated': '--mds-surface-2', '--mds-panel-2': '--mds-surface-2',
  '--mds-experience-panel-raised': '--mds-surface-2', '--mds-fill-1': '--mds-surface-2',
  '--mds-fill-2': '--mds-surface-2', '--mds-fill-3': '--mds-surface-2',
  '--mds-chrome-nav-hover-background': '--mds-surface-2', '--mds-chrome-nav-active-background': '--mds-surface-2',
  '--mds-tooltip-bg': '--mds-surface-2',
  '--app-skeleton': '--mds-skeleton', '--app-skeleton-shimmer': '--mds-skeleton-shimmer',
  '--mc-accent': '--mds-tint', '--mds-recovery-blue': '--mds-blue', '--mc-status': '--mds-blue',
  '--mds-blue-soft': '--mds-blue-fill', '--mds-blue-line': '--mds-blue',
  '--mc-action': '--mds-action', '--mc-action-hover': '--mds-action-hover', '--mc-action-edge': '--mds-action',
  '--mc-on-action': '--mds-on-action',
  '--mds-recovery-green': '--mds-green', '--proof-good': '--mds-green',
  '--mds-recovery-yellow': '--mds-yellow', '--mds-memory': '--mds-yellow', '--mc-caution': '--mds-yellow',
  '--proof-stop': '--mds-yellow', '--mds-trip-dot': '--mds-yellow', '--mds-memory-line': '--mds-yellow',
  '--mds-memory-soft': '--mds-yellow-fill', '--mc-caution-bg': '--mds-yellow-fill',
  '--mds-danger': '--mds-red',
};

export const LINE_VARS = new Set([
  '--mds-line', '--mds-line-strong', '--mc-line', '--mc-divider', '--proof-line',
  '--mds-chrome-sidebar-border', '--mds-chrome-right-border', '--mds-chrome-account-border',
  '--mds-side-panel-border', '--mds-experience-border', '--mds-chrome-nav-hover-border',
  '--mds-chrome-nav-active-border', '--mds-chrome-control-border', '--mds-tooltip-border',
  '--mds-chrome-brand-border', '--mds-chrome-avatar-border',
]);

export const CHANNEL_HUE = {
  '--mds-blue-rgb': 'blue', '--mds-green-rgb': 'green', '--mds-recovery-green-rgb': 'green',
  '--mds-recovery-yellow-rgb': 'yellow', '--mds-teal-rgb': 'cyan',
};

export const DEFINITION_RENAME = { '--mc-controls-height': '--mds-controls-height' };

export const DELETE_DEFINITIONS = new Set([
  ...Object.keys(RENAME), ...LINE_VARS, ...Object.keys(CHANNEL_HUE),
  '--mds-blue', '--mds-green', '--mds-yellow', '--mds-scrim', '--mds-recovery-rail', '--mds-bg-2',
  '--mds-recovery-yellow-strong', '--mds-recovery-red', '--mds-chrome-breadcrumb', '--mds-prose-measure',
]);
```

- [ ] **Step 6: Write the color codemod**

Create `scripts/design-migration/map-colors.mjs`:

```js
import postcss from 'postcss';
import { COLOR_IN_VALUE, mapLiteral, parseColor, roleOf } from './color.mjs';
import {
  CONTROL, DARK, LIGHT, PROJECTOR, baseSelector, migrationCssFiles, subjectOf, transformCss,
  unscopeSelector, writeReport,
} from './lib.mjs';
import { CHANNEL_HUE, DEFINITION_RENAME, DELETE_DEFINITIONS, LINE_VARS, RENAME } from './variable-map.mjs';

const CHANNEL = /rgba?\(\s*var\((--[\w-]+)\)\s*\/\s*([\d.]+%?)\s*\)/g;
const notes = [];
const note = (file, decl, text) => notes.push(
  `- ${file}:${decl.source?.start?.line} \`${decl.parent.selector ?? decl.parent.params}\` ${decl.prop}: ${text}`);

const themeOf = rule => (LIGHT.test(rule.selector ?? '') ? 'light' : 'dark');
const scoped = rule => [LIGHT, DARK, PROJECTOR].some(pattern => pattern.test(rule.selector ?? ''));
const contextOf = node => {
  const chain = [];
  for (let parent = node.parent; parent && parent.type !== 'root'; parent = parent.parent) {
    if (parent.type === 'atrule') chain.push(`@${parent.name} ${parent.params}`);
  }
  return chain.join(' | ');
};
const keyOf = (rule, prop) => `${contextOf(rule)}|${baseSelector(rule.selector)}|${prop}`;
const subjectsOf = decl => (decl.parent.type === 'rule' ? decl.parent.selectors.map(subjectOf) : ['']);

function renameVars(decl, subjects) {
  const control = subjects.some(subject => CONTROL.test(subject));
  const role = roleOf(decl.prop, subjects[0]);
  decl.value = decl.value
    .replace(/var\((--[\w-]+)\)/g, (match, name) => {
      if (RENAME[name]) return `var(${RENAME[name]})`;
      if (DEFINITION_RENAME[name]) return `var(${DEFINITION_RENAME[name]})`;
      if (LINE_VARS.has(name)) return control ? 'var(--mds-control-line)' : 'var(--mds-separator)';
      return match;
    })
    .replace(CHANNEL, (match, name, alpha) => {
      const hue = CHANNEL_HUE[name];
      if (!hue) return match;
      const a = alpha.endsWith('%') ? parseFloat(alpha) / 100 : parseFloat(alpha);
      return role === 'fill' && a < 0.35 ? `var(--mds-${hue}-fill)` : `var(--mds-${hue})`;
    });
  if (role === 'accent') decl.value = decl.value.replace('var(--mds-blue)', 'var(--mds-tint)');
}

function mapLiterals(decl, subjects, theme, file) {
  const role = roleOf(decl.prop, subjects[0]);
  decl.value = decl.value.replace(COLOR_IN_VALUE, literal => {
    const result = mapLiteral({ color: parseColor(literal), role, theme, subject: subjects[0] });
    if (result.review) note(file, decl, `REVIEW ${literal} -> ${result.token}: ${result.why}`);
    return `var(${result.token})`;
  });
}

function collapseScoped(root, file) {
  const base = new Map();
  root.walkRules(rule => {
    if (scoped(rule)) return;
    rule.walkDecls(decl => { base.set(keyOf(rule, decl.prop), decl.value); });
  });
  root.walkRules(rule => {
    if (!scoped(rule)) return;
    const moved = [];
    rule.each(decl => {
      if (decl.type !== 'decl' || decl.prop.startsWith('--') || !roleOf(decl.prop, '')) return;
      if (!/var\(--mds-/.test(decl.value)) return;
      const baseValue = base.get(keyOf(rule, decl.prop));
      if (PROJECTOR.test(rule.selector)) note(file, decl, 'projector color override removed');
      else if (baseValue !== undefined && baseValue !== decl.value) {
        note(file, decl, `REVIEW theme disagreement, kept base ${baseValue} over ${decl.value}`);
      } else if (baseValue === undefined) {
        moved.push(decl.clone());
        note(file, decl, 'REVIEW theme-only color now applies to both themes');
      }
      decl.remove();
    });
    if (moved.length) rule.after(postcss.rule({ selector: unscopeSelector(rule.selector), nodes: moved }));
    if (rule.nodes.length === 0) rule.remove();
  });
}

transformCss(migrationCssFiles(), (root, file) => {
  root.walkDecls(decl => {
    if (decl.prop.startsWith('--') && DELETE_DEFINITIONS.has(decl.prop)) return decl.remove();
    if (DEFINITION_RENAME[decl.prop]) decl.prop = DEFINITION_RENAME[decl.prop];
    const subjects = subjectsOf(decl);
    renameVars(decl, subjects);
    mapLiterals(decl, subjects, decl.parent.type === 'rule' ? themeOf(decl.parent) : 'dark', file);
    return undefined;
  });
  collapseScoped(root, file);
  root.walkRules(rule => { if (rule.nodes.length === 0) rule.remove(); });
});

const reviewCount = notes.filter(line => line.includes('REVIEW')).length;
const report = writeReport('colors', ['# Colors', '', `${reviewCount} REVIEW items`, '', ...notes]);
console.log(`${notes.length} notes, ${reviewCount} to review; report ${report}`);
```

- [ ] **Step 7: Run the codemod**

```bash
node scripts/design-migration/map-colors.mjs
command grep -rnoE -- '--(mc|proof|app)-[a-z0-9-]+' src/showcase src/index.css src/preflight.css \
  | command grep -vE -- '--mc-(type-scale|heading-weight|label-weight|heading-tracking)' \
  || echo "no old namespaces left"
```

Expected: the report path, then `no old namespaces left`. The four `--mc-` type variables are excluded here because Task 7 removes them. Any other remaining name is a variable the map does not cover: add it to `variable-map.mjs` and rerun on a clean tree (`git checkout src` first).

- [ ] **Step 8: Update the `/stage` remap**

In `src/stage/demo-stage.css`, replace the five `--mc-*` lines inside `.ds-kiosk-architecture` (lines 1529 to 1534) with:

```css
  --mds-label: var(--ds-text);
  --mds-label-2: var(--ds-text-2);
  --mds-surface-2: var(--ds-bg-1);
  --mds-surface: var(--ds-bg-2);
  --mds-separator: var(--ds-line-2);
  --mds-control-line: var(--ds-line-2);
  --mds-tint: var(--ds-blue);
```

Keep the `--mds-font: var(--ds-font);` line for now (Task 7 removes it).

- [ ] **Step 9: Resolve every REVIEW line**

Open `../.local/design-migration/colors.md`. For each `REVIEW` line, confirm the proposed token or change it by hand:
- `black panel becomes a grouped surface`: keep `--mds-surface` for cards, rail sections and grouped blocks; use `--mds-ground` for bars and containers that should merge with the page.
- `likely inverse text on a light fill`: text sitting on `--mds-label`, `--mds-action` or a solid hue uses `--mds-ground` or `--mds-on-action`.
- `solid blue control fill`: primary actions use `--mds-action` with `--mds-on-action` text and `--mds-action-hover` on hover; other blue controls use `--mds-blue-fill` with `--mds-blue` text.
- `theme disagreement` and `theme-only color`: pick one token that reads correctly in both themes.
- `property has no color role`: set the literal by hand to the nearest role token.

- [ ] **Step 10: Tighten the ratchet and verify**

Delete the `color` key from `scripts/design-tokens/exemptions.mjs`.

Run the full verification gate. Expected: all green, including the 4 `blue actions` contrast tests from Step 1 and every axe `color-contrast` check. Fix any axe contrast failure by moving the text to a stronger label token, never by adding a new color.

```bash
VISUAL_DIR=../.local/visual/task-06 npm run visual -- --update-snapshots=all
```

Review the screenshots. Expected: one neutral grey family in each theme (no warm brown or slate text), white cards on the light grey page in light mode, `#252527` grouped surfaces on black in dark mode, blue only for links, focus and actions, and color only on status.

- [ ] **Step 11: Commit**

```bash
git add src/showcase src/index.css src/preflight.css src/stage/demo-stage.css e2e/accessibility.spec.ts \
  scripts/design-migration scripts/design-tokens/exemptions.mjs
GIT_EDITOR=true git commit -q -F - <<'EOF'
Replace showcase colors with one set of role tokens

tokens.css defines ground, surfaces, separators, three labels, the tint,
the primary action and five status hues with opaque fills for both
themes. Every hard-coded color and every --mc, --proof and --app
variable is mapped to a role, theme overrides collapse into the shared
rules, and the projector preset no longer changes colors. The contrast
test reads the new tokens with unchanged thresholds.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 7: System font and type ramp

**Files:**
- Modify: `meridian/frontend/src/showcase/tokens.css` (type tokens)
- Create: `meridian/frontend/scripts/design-migration/type.mjs`
- Create: `meridian/frontend/scripts/design-migration/type.test.mjs`
- Create: `meridian/frontend/scripts/design-migration/map-type.mjs`
- Create: `meridian/frontend/scripts/design-migration/compare-clipping.mjs`
- Modify: showcase stylesheets and `src/index.css` (codemod)
- Modify: `meridian/frontend/src/stage/demo-stage.css:1528` (remove `--mds-font` remap)
- Modify: `meridian/frontend/scripts/design-tokens/exemptions.mjs` (delete `type`)

**Interfaces:**
- Consumes: `lib.mjs`; color tokens `--mds-label-2`, `--mds-label-3` (to detect secondary text).
- Produces: `--mds-font`, `--mds-font-mono`, `--mds-type-scale`, `--mds-type-{caption,footnote,body,headline,title-3,title-2,title-1,large-title}`, `--mds-weight-{regular,medium,semibold,bold}`, `--mds-tracking-{title,display}`.
- Produces (`type.mjs`): `effectivePx(value, selector): number|null`, `roundWeight(value): 400|500|600|700|null`, `styleFor(px, { weight, secondary }): string`, `trackingFor(style): string|null`, `weightToken(weight): string`, `STYLE_WEIGHT`.

- [ ] **Step 1: Write the failing type tests**

Create `scripts/design-migration/type.test.mjs`:

```js
// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { effectivePx, roundWeight, styleFor, trackingFor, weightToken } from './type.mjs';

describe('effectivePx', () => {
  it('applies the multiplier each variable had on the laptop', () => {
    expect(effectivePx('calc(12px * var(--mds-fs))', '.mds-card')).toBeCloseTo(13.92);
    expect(effectivePx('calc(12px * var(--mds-fs))', '.mds-desktop-app.is-discovery .x')).toBe(12);
    expect(effectivePx('calc(10px * var(--mds-fs-chrome))', '.x')).toBeCloseTo(10.8);
    expect(effectivePx('calc(13px * var(--mc-type-scale))', '.x')).toBeCloseTo(15.34);
    expect(effectivePx('15px', '.x')).toBe(15);
    expect(effectivePx('clamp(20px, 3vw, 28px)', '.x')).toBe(28);
    expect(effectivePx('0.9em', '.x')).toBe(null);
  });
});

describe('roundWeight', () => {
  it('rounds to the four weights', () => {
    expect([300, 450, 460, 550, 570, 580, 650, 680, 760, 900].map(String).map(roundWeight))
      .toEqual([400, 400, 500, 500, 500, 600, 600, 700, 700, 700]);
    expect(roundWeight('bold')).toBe(700);
    expect(roundWeight('var(--unknown)')).toBe(null);
  });
});

describe('styleFor', () => {
  it('snaps sizes to the ramp', () => {
    expect(styleFor(9, { weight: 400 })).toBe('caption');
    expect(styleFor(12.2, { weight: 400 })).toBe('caption');
    expect(styleFor(13.9, { weight: 400 })).toBe('footnote');
    expect(styleFor(14, { weight: 400, secondary: true })).toBe('footnote');
    expect(styleFor(14, { weight: 400 })).toBe('body');
    expect(styleFor(15.3, { weight: 600 })).toBe('headline');
    expect(styleFor(18, { weight: 400 })).toBe('title-3');
    expect(styleFor(24, { weight: 400 })).toBe('title-2');
    expect(styleFor(30, { weight: 400 })).toBe('title-1');
    expect(styleFor(42, { weight: 400 })).toBe('large-title');
  });
});

describe('tracking and weight tokens', () => {
  it('names the tokens', () => {
    expect(trackingFor('title-2')).toBe('var(--mds-tracking-title)');
    expect(trackingFor('large-title')).toBe('var(--mds-tracking-display)');
    expect(trackingFor('body')).toBe(null);
    expect(weightToken(600)).toBe('var(--mds-weight-semibold)');
  });
});
```

Run: `npx vitest --run scripts/design-migration/type.test.mjs`
Expected: FAIL, `Failed to load url ./type.mjs`.

- [ ] **Step 2: Implement the type helpers**

Create `scripts/design-migration/type.mjs`:

```js
export const STYLE_WEIGHT = {
  caption: 400, footnote: 400, body: 400, headline: 600,
  'title-3': 600, 'title-2': 700, 'title-1': 700, 'large-title': 700,
};
const WEIGHT_NAME = { 400: 'regular', 500: 'medium', 600: 'semibold', 700: 'bold' };
const KNOWN_WEIGHT_VARS = { 'var(--mc-heading-weight)': 520, 'var(--mc-label-weight)': 550 };

function scaleFor(variable, selector) {
  const discovery = /is-discovery/.test(selector);
  if (variable === '--mds-fs') return discovery ? 1 : 1.16;
  if (variable === '--mds-fs-chrome') return discovery ? 1 : 1.08;
  if (variable === '--mc-type-scale') return 1.18;
  return 1;
}

export function effectivePx(value, selector) {
  const scaled = value.match(/^calc\(\s*([\d.]+)px\s*\*\s*var\((--[\w-]+)\)\s*\)$/);
  if (scaled) return parseFloat(scaled[1]) * scaleFor(scaled[2], selector);
  const plain = value.match(/^([\d.]+)px$/);
  if (plain) return parseFloat(plain[1]);
  const clamp = value.match(/^clamp\([^,()]+,[^,()]+,\s*([\d.]+)px\s*\)$/);
  if (clamp) return parseFloat(clamp[1]);
  const rem = value.match(/^([\d.]+)rem$/);
  return rem ? parseFloat(rem[1]) * 16 : null;
}

export function roundWeight(value) {
  const known = KNOWN_WEIGHT_VARS[value];
  const weight = known ?? (value === 'bold' ? 700 : value === 'normal' ? 400 : Number(value));
  if (!Number.isFinite(weight)) return null;
  if (weight <= 450) return 400;
  if (weight < 580) return 500;
  if (weight < 680) return 600;
  return 700;
}

export function styleFor(px, { weight, secondary = false }) {
  if (px < 12.5) return 'caption';
  if (px < 14) return 'footnote';
  if (px < 14.5 && secondary) return 'footnote';
  if (px < 16.5) return weight >= 600 ? 'headline' : 'body';
  if (px < 20.5) return 'title-3';
  if (px < 25.5) return 'title-2';
  if (px < 32.5) return 'title-1';
  return 'large-title';
}

export function trackingFor(style) {
  if (style === 'title-3' || style === 'title-2') return 'var(--mds-tracking-title)';
  if (style === 'title-1' || style === 'large-title') return 'var(--mds-tracking-display)';
  return null;
}

export const weightToken = weight => `var(--mds-weight-${WEIGHT_NAME[weight]})`;
```

Run: `npx vitest --run scripts/design-migration/type.test.mjs`
Expected: PASS, 4 tests.

- [ ] **Step 3: Add the type tokens**

Append to `src/showcase/tokens.css`:

```css
:root {
  --mds-type-scale: 1;
  --mds-weight-regular: 400;
  --mds-weight-medium: 500;
  --mds-weight-semibold: 600;
  --mds-weight-bold: 700;
  --mds-tracking-title: -0.01em;
  --mds-tracking-display: -0.02em;
}

@media (max-width: 860px) {
  :root { --mds-type-scale: 0.9; }
}

.mds-root[data-projector-readability='true'] { --mds-type-scale: 1.1; }

:root,
.mds-root {
  --mds-font: system-ui, -apple-system, sans-serif;
  --mds-font-mono: ui-monospace, Menlo, monospace;
  --mds-type-caption: 400 max(12px, calc(12px * var(--mds-type-scale))) / 1.333 var(--mds-font);
  --mds-type-footnote: 400 max(12px, calc(13px * var(--mds-type-scale))) / 1.385 var(--mds-font);
  --mds-type-body: 400 calc(15px * var(--mds-type-scale)) / 1.467 var(--mds-font);
  --mds-type-headline: 600 calc(15px * var(--mds-type-scale)) / 1.467 var(--mds-font);
  --mds-type-title-3: 600 calc(18px * var(--mds-type-scale)) / 1.333 var(--mds-font);
  --mds-type-title-2: 700 calc(22px * var(--mds-type-scale)) / 1.273 var(--mds-font);
  --mds-type-title-1: 700 calc(28px * var(--mds-type-scale)) / 1.214 var(--mds-font);
  --mds-type-large-title: 700 calc(36px * var(--mds-type-scale)) / 1.167 var(--mds-font);
}
```

The type tokens are declared on `.mds-root` as well as `:root` because `var()` inside a custom property resolves where it is declared; without `.mds-root`, the projector multiplier would never reach them.

- [ ] **Step 4: Write the type codemod**

Create `scripts/design-migration/map-type.mjs`:

```js
import postcss from 'postcss';
import { migrationCssFiles, transformCss, writeReport } from './lib.mjs';
import { STYLE_WEIGHT, effectivePx, roundWeight, styleFor, trackingFor, weightToken } from './type.mjs';

const OLD_TYPE_DEFINITIONS = new Set([
  '--mds-fs', '--mds-fs-chrome', '--mc-type-scale', '--mc-heading-weight', '--mc-label-weight',
  '--mc-heading-tracking', '--mds-serif', '--mds-font', '--mds-font-mono',
]);
const HUE_TEXT = /var\(--mds-(?:blue|green|yellow|red|cyan|tint)\)/;
const notes = [];
const note = (file, node, text) => notes.push(`- ${file}:${node.source?.start?.line} \`${node.selector}\` ${text}`);

function last(rule, prop) {
  return rule.nodes.filter(node => node.type === 'decl' && node.prop === prop).at(-1);
}

function keepLineHeight(decl) {
  if (!decl) return false;
  const value = Number(decl.value.trim());
  return Number.isFinite(value) && value <= 1.15;
}

function rewriteSized(rule, file, parts) {
  const { size, weight, family, spacing, lineHeight, color, uppercase } = parts;
  const px = effectivePx(size.value.trim(), rule.selector);
  if (px === null) {
    note(file, rule, `REVIEW font-size ${size.value} not mapped`);
    return;
  }
  const rounded = weight ? roundWeight(weight.value.trim()) : null;
  const secondary = /--mds-label-[23]/.test(color?.value ?? '');
  const style = uppercase ? 'footnote' : styleFor(px, { weight: rounded ?? 400, secondary });
  const finalWeight = uppercase ? 600 : (rounded ?? STYLE_WEIGHT[style]);
  const overrides = [];
  if (finalWeight !== STYLE_WEIGHT[style]) overrides.push({ prop: 'font-weight', value: weightToken(finalWeight) });
  if (family && /mono/.test(family.value)) overrides.push({ prop: 'font-family', value: 'var(--mds-font-mono)' });
  if (keepLineHeight(lineHeight)) overrides.push({ prop: 'line-height', value: lineHeight.value.trim() });
  const tracking = trackingFor(style);
  if (tracking) overrides.push({ prop: 'letter-spacing', value: tracking });
  const font = postcss.decl({ prop: 'font', value: `var(--mds-type-${style})` });
  size.replaceWith(font);
  [weight, family, spacing, lineHeight].forEach(decl => decl?.remove());
  overrides.reverse().forEach(override => font.after(postcss.decl(override)));
  if (uppercase && color && !HUE_TEXT.test(color.value)) color.value = 'var(--mds-label-2)';
}

function rewriteUnsized(rule, file, { weight, family, spacing }) {
  if (weight && weight.value.trim() !== 'inherit') {
    const rounded = roundWeight(weight.value.trim());
    if (rounded) weight.value = weightToken(rounded);
    else note(file, rule, `REVIEW font-weight ${weight.value}`);
  }
  if (family) {
    if (/mono/.test(family.value)) family.value = 'var(--mds-font-mono)';
    else if (!/^(?:inherit|var\(--mds-font\))$/.test(family.value.trim())) family.remove();
  }
  if (spacing && !/^(?:0|normal)$/.test(spacing.value.trim())) spacing.remove();
}

function visitRule(rule, file) {
  const shorthand = last(rule, 'font');
  if (shorthand && !/^var\(--mds-type-/.test(shorthand.value)) note(file, rule, `REVIEW font: ${shorthand.value}`);
  const transform = last(rule, 'text-transform');
  const uppercase = Boolean(transform && /uppercase/.test(transform.value));
  if (uppercase) transform.remove();
  rule.walkDecls('font-feature-settings', decl => { if (/ss0\d|cv\d/.test(decl.value)) decl.remove(); });
  const parts = {
    size: last(rule, 'font-size'), weight: last(rule, 'font-weight'), family: last(rule, 'font-family'),
    spacing: last(rule, 'letter-spacing'), lineHeight: last(rule, 'line-height'), color: last(rule, 'color'),
    uppercase,
  };
  if (parts.size && parts.size.value.trim() !== 'inherit') rewriteSized(rule, file, parts);
  else rewriteUnsized(rule, file, parts);
}

transformCss(migrationCssFiles(), (root, file) => {
  root.walkDecls(decl => { if (OLD_TYPE_DEFINITIONS.has(decl.prop)) decl.remove(); });
  root.walkRules(rule => { visitRule(rule, file); });
  root.walkRules(rule => { if (rule.nodes.length === 0) rule.remove(); });
});

const reviewCount = notes.filter(line => line.includes('REVIEW')).length;
const report = writeReport('type', ['# Type', '', `${reviewCount} REVIEW items`, '', ...notes]);
console.log(`${notes.length} notes, ${reviewCount} to review; report ${report}`);
```

- [ ] **Step 5: Write the clipping comparison**

Create `scripts/design-migration/compare-clipping.mjs`:

```js
import fs from 'node:fs';
import path from 'node:path';

const [baseDir, nextDir] = process.argv.slice(2).map(dir => path.resolve(dir));
const read = (dir, file) => {
  const full = path.join(dir, file);
  return fs.existsSync(full) ? JSON.parse(fs.readFileSync(full, 'utf8')) : [];
};

let added = 0;
for (const file of fs.readdirSync(nextDir).filter(name => name.startsWith('clipped-')).sort()) {
  const before = new Set(read(baseDir, file));
  const fresh = read(nextDir, file).filter(entry => !before.has(entry));
  if (fresh.length === 0) continue;
  console.log(`\n${file}`);
  for (const entry of fresh) console.log(`  ${entry}`);
  added += fresh.length;
}
console.log(`\n${added} newly clipped elements`);
```

- [ ] **Step 6: Run the codemod and fix the stage and base font**

```bash
node scripts/design-migration/map-type.mjs
command grep -rnE -- 'var\(--(mds-fs|mds-fs-chrome|mc-type-scale|mds-serif)\)' src/showcase src/index.css \
  || echo "no old type variables left"
```

Expected: the report path, then `no old type variables left`.

In `src/stage/demo-stage.css`, delete the line `--mds-font: var(--ds-font);` inside `.ds-kiosk-architecture`: the type tokens resolve on `:root`, so the remap had no effect.

In `src/index.css`, set the `body` `font-family` to `var(--mds-font)`.

- [ ] **Step 7: Resolve REVIEW lines and sentence-case copy**

Open `../.local/design-migration/type.md` and map each `REVIEW` font-size, weight or shorthand by hand to a `font: var(--mds-type-*)` token plus weight, family or tracking overrides.

Then find copy authored in capitals that relied on the removed `text-transform`:

```bash
command grep -rnE ">[A-Z][A-Z0-9 &·/-]{3,}<|'[A-Z][A-Z0-9 &·/-]{3,}'" src/showcase --include='*.tsx' \
  | command grep -v __tests__
```

Change each user-facing string that is all capitals for styling reasons to sentence case. Leave acronyms (`SQL`, `MCP`, `JFK`, `USD`) as they are. Update any Vitest or Playwright assertion that matched the old text.

- [ ] **Step 8: Tighten the ratchet and verify**

Delete the `type` key from `scripts/design-tokens/exemptions.mjs`. Run the full verification gate. The 320px reflow checks in `accessibility.spec.ts` guard against the larger sizes overflowing; fix any failure by letting the layout wrap, never by lowering a type token.

```bash
VISUAL_DIR=../.local/visual/task-07 npm run visual -- --update-snapshots=all
node scripts/design-migration/compare-clipping.mjs ../.local/visual/baseline ../.local/visual/task-07
```

For every newly clipped element that hides meaningful text, fix the layout rule so it wraps or grows. Rerun the capture and comparison until the remaining list contains only intentional truncation (long identifiers with ellipsis).

Review the screenshots: SF Pro throughout, SF Mono for identifiers, headings visibly heavier than body, no all-caps eyebrows, and the projector captures larger than the desktop ones.

- [ ] **Step 9: Commit**

```bash
git add src/showcase src/index.css src/stage/demo-stage.css scripts/design-migration \
  scripts/design-tokens/exemptions.mjs e2e src
GIT_EDITOR=true git commit -q -F - <<'EOF'
Set showcase type on the system font with a seven-step ramp

Text uses font tokens from caption to large title on system-ui, with SF
Mono for identifiers, four weights and two tracking values. One
--mds-type-scale replaces the three old multipliers: 1 on the laptop,
0.9 below 860px with a 12px floor, and 1.1 with projector readability.
All-caps eyebrows become sentence-case footnotes.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 8: Radii

**Files:**
- Modify: `meridian/frontend/src/showcase/tokens.css`
- Create: `meridian/frontend/scripts/design-migration/radius.mjs`
- Create: `meridian/frontend/scripts/design-migration/radius.test.mjs`
- Create: `meridian/frontend/scripts/design-migration/map-radii.mjs`
- Modify: showcase stylesheets (codemod)
- Modify: `meridian/frontend/scripts/design-tokens/exemptions.mjs` (delete `radius`; the object is now empty)

**Interfaces:**
- Produces: `--mds-radius-s` (6px), `--mds-radius-m` (10px), `--mds-radius-l` (14px), `--mds-radius-full` (999px); `mapRadius(value): string|null`.

- [ ] **Step 1: Write the failing test**

Create `scripts/design-migration/radius.test.mjs`:

```js
// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { mapRadius } from './radius.mjs';

describe('mapRadius', () => {
  it('maps pixel radii onto the scale', () => {
    expect(mapRadius('4px')).toBe('var(--mds-radius-s)');
    expect(mapRadius('7px')).toBe('var(--mds-radius-s)');
    expect(mapRadius('8px')).toBe('var(--mds-radius-m)');
    expect(mapRadius('11px')).toBe('var(--mds-radius-m)');
    expect(mapRadius('14px')).toBe('var(--mds-radius-l)');
    expect(mapRadius('24px')).toBe('var(--mds-radius-l)');
    expect(mapRadius('999px')).toBe('var(--mds-radius-full)');
  });

  it('keeps 0 and 50% and maps each corner', () => {
    expect(mapRadius('0')).toBe('0');
    expect(mapRadius('50%')).toBe('50%');
    expect(mapRadius('16px 16px 4px 16px'))
      .toBe('var(--mds-radius-l) var(--mds-radius-l) var(--mds-radius-s) var(--mds-radius-l)');
  });

  it('refuses shapes it cannot express', () => {
    expect(mapRadius('55% 55% 0 0')).toBe(null);
    expect(mapRadius('8px / 4px')).toBe(null);
  });
});
```

Run: `npx vitest --run scripts/design-migration/radius.test.mjs`
Expected: FAIL, `Failed to load url ./radius.mjs`.

- [ ] **Step 2: Implement the mapping**

Create `scripts/design-migration/radius.mjs`:

```js
function mapPart(part) {
  if (part === '0' || part === '50%') return part;
  const px = part.match(/^([\d.]+)px$/);
  if (!px) return null;
  const n = parseFloat(px[1]);
  if (n === 0) return '0';
  if (n >= 100) return 'var(--mds-radius-full)';
  if (n <= 7) return 'var(--mds-radius-s)';
  if (n <= 11) return 'var(--mds-radius-m)';
  return 'var(--mds-radius-l)';
}

export function mapRadius(value) {
  const parts = value.trim().split(/\s+/).map(mapPart);
  return parts.includes(null) ? null : parts.join(' ');
}
```

Run: `npx vitest --run scripts/design-migration/radius.test.mjs`
Expected: PASS, 3 tests.

- [ ] **Step 3: Add the tokens and write the codemod**

Append to `src/showcase/tokens.css`:

```css
:root {
  --mds-radius-s: 6px;
  --mds-radius-m: 10px;
  --mds-radius-l: 14px;
  --mds-radius-full: 999px;
}
```

Create `scripts/design-migration/map-radii.mjs`:

```js
import { migrationCssFiles, transformCss, writeReport } from './lib.mjs';
import { mapRadius } from './radius.mjs';

const notes = [];
transformCss(migrationCssFiles(), (root, file) => {
  root.walkDecls(/^border(?:-[a-z]+)*-radius$/, decl => {
    const mapped = mapRadius(decl.value);
    if (mapped) decl.value = mapped;
    else notes.push(`- ${file}:${decl.source?.start?.line} REVIEW ${decl.prop}: ${decl.value}`);
  });
});
const report = writeReport('radii', ['# Radii', '', ...notes]);
console.log(`${notes.length} to review; report ${report}`);
```

- [ ] **Step 4: Run, resolve and verify**

```bash
node scripts/design-migration/map-radii.mjs
```

Resolve each REVIEW line by hand: percentage shapes that are not circles become `var(--mds-radius-l)` or `50%`; slash syntax becomes a single token.

Delete the `radius` key from `scripts/design-tokens/exemptions.mjs`; the exported object is now `{}`. Run the full verification gate.

```bash
VISUAL_DIR=../.local/visual/task-08 npm run visual -- --update-snapshots=all
```

Review: chips at 6px, buttons, inputs and rows at 10px, cards at 14px, pills and avatars unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/showcase scripts/design-migration scripts/design-tokens/exemptions.mjs
GIT_EDITOR=true git commit -q -F - <<'EOF'
Put showcase corner radii on a three-step scale

Radii map to 6px for chips, 10px for controls and rows, 14px for cards
and a full pill, replacing 25 distinct values. Circles keep 50%.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 9: Remove migration tooling and meet the done criteria

**Files:**
- Delete: `meridian/frontend/scripts/design-migration/` (whole directory)
- Delete: `meridian/frontend/scripts/design-tokens/exemptions.mjs`
- Modify: `meridian/frontend/scripts/design-tokens/check.mjs` (drop exemptions and `--seed`)
- Modify: `meridian/docs/PRESENTER_GUIDE.md` (theme and projector section)

**Interfaces:**
- Consumes: everything above.
- Produces: the permanent check with no exemption mechanism.

- [ ] **Step 1: Delete the temporary tooling**

```bash
trash scripts/design-migration scripts/design-tokens/exemptions.mjs
```

- [ ] **Step 2: Simplify the check**

In `scripts/design-tokens/check.mjs`: delete the `EXEMPTIONS` import, the whole `if (process.argv.includes('--seed')) { ... }` block, the `exempt` and `stale` constants and the stale-exemption output. The tail becomes:

```js
for (const v of all) console.error(`${v.file}:${v.line} [${v.rule}] ${v.prop}: ${v.value}`);
if (all.length) {
  console.error(`design tokens: ${all.length} violations`);
  process.exit(1);
}
console.log(`design tokens: ${scanned.length} files pass`);
```

- [ ] **Step 3: Check the done criteria**

```bash
command grep -rnoE -- '--(mc|proof|app)-[a-z0-9-]+' src e2e | command grep -v 'src/stage/demo-stage.css' \
  || echo "no old namespaces"
command grep -rn "Geist" src/showcase src/index.css || echo "showcase free of Geist"
npm run lint
```

Expected: `no old namespaces`, `showcase free of Geist`, and `design tokens: N files pass`. `demo-stage.css` keeps its own `--ds-*` variables and may still mention `--mc-` only inside comments; delete any such comment.

- [ ] **Step 4: Update the presenter guide**

In `meridian/docs/PRESENTER_GUIDE.md`, in the theme and projector readability passage, state what the app does now: both themes use the same color roles; projector readability enlarges type by 10% and no longer changes colors; dark matches the deck. Keep the existing link and URL instructions.

- [ ] **Step 5: Full verification and commit**

Run the full verification gate, then:

```bash
VISUAL_DIR=../.local/visual/final npm run visual -- --update-snapshots=all
```

Review the final set once more. Then:

```bash
git add -A scripts src ../docs/PRESENTER_GUIDE.md
GIT_EDITOR=true git commit -q -F - <<'EOF'
Remove design migration tooling now that tokens are enforced

The codemods and the exemption list are gone. check.mjs enforces every
rule on every showcase stylesheet with no exemptions. The presenter
guide describes the shared color roles and the type-only projector
preset.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 10: Recapture presentation screenshots (requires the presenter's go-ahead)

This task creates a demo hold in Aurora. Do not start it until the presenter says to.

**Files:**
- Replace: `meridian/docs/presentation/reinvent-2026/screenshots/01-solution-briefing.png` through `09-business-receipt.png`
- Modify: `meridian/docs/presentation/reinvent-2026/README.md` (Captures and evidence section)

- [ ] **Step 1: Start the stack**

From `meridian/`:

```bash
LANGGRAPH_CHECKPOINT_DSN= LANGGRAPH_AUTO_CHECKPOINT_DSN=false LANGGRAPH_CHECKPOINT_DATA_API=true \
LANGGRAPH_CHECKPOINT_REQUIRED=true LANGGRAPH_CHECKPOINT_INIT_ON_STARTUP=true \
venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8013
```

From `meridian/frontend`, in a second shell:

```bash
VITE_API_ORIGIN=http://127.0.0.1:8013 npm run dev -- --host 127.0.0.1 --port 5176 --strictPort
```

Confirm `curl --fail http://127.0.0.1:8013/api/health` reports `checkpoint_backend: "AuroraDataApiSaver"` and `checkpoint_durable: true`.

- [ ] **Step 2: Capture the nine states at their recorded dimensions**

Follow the capture table in `docs/presentation/reinvent-2026/README.md`: the same nine states, the same pixel dimensions (2466x1387, and 1444x1387 for `02-concierge-dark.png`), dark theme with `present=1`. Use the prompts in `docs/presentation/reinvent-2026/SPEAKER_NOTES.md` for the SQL, retrieval and recovery states.

- [ ] **Step 3: Release the hold created for the captures**

From `meridian/`:

```bash
venv/bin/python scripts/release_demo_bookings.py --dry-run
venv/bin/python scripts/release_demo_bookings.py --booking-id <the hold's booking id>
```

- [ ] **Step 4: Update the capture record and commit**

In the README's Captures and evidence section, record the new capture date, the journey, thread, worker and hold identifiers from this run, and that the hold was released. Commit the screenshots and README with a message describing the recapture.
