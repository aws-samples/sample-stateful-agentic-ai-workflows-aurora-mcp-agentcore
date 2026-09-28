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
  // Native lazy loading never starts for <img> elements inside scrolled-out panels, so
  // settle()'s images.complete check hangs until the 120s test timeout. Force eager loading;
  // the attribute value has no bearing on the pixels a viewport screenshot captures.
  await page.addInitScript(() => {
    const setAttribute = Element.prototype.setAttribute;
    Element.prototype.setAttribute = function patchedSetAttribute(name: string, value: string) {
      const isLazyImage = name === 'loading' && this instanceof HTMLImageElement;
      return setAttribute.call(this, name, isLazyImage ? 'eager' : value);
    };
  });
  if (journey) return;
  await page.route(url => url.pathname.startsWith('/api/') || url.pathname === '/health', route =>
    route.fulfill({
      status: 503, contentType: 'application/json', body: '{"detail":"offline capture"}',
    }));
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
  const clipped = await page.evaluate(() => Array.from(
    document.querySelectorAll<HTMLElement>('body *'),
  )
    .filter(el => el.childElementCount === 0 && (el.textContent ?? '').trim() !== '')
    .filter(el => {
      const style = getComputedStyle(el);
      const hides = style.overflowX !== 'visible' || style.overflowY !== 'visible';
      const overflowsX = el.scrollWidth > el.clientWidth + 1;
      const overflowsY = el.scrollHeight > el.clientHeight + 1;
      return hides && (overflowsX || overflowsY);
    })
    .map(el => `${el.tagName.toLowerCase()}.${Array.from(el.classList).join('.')} `
      + `"${(el.textContent ?? '').trim().slice(0, 40)}"`));
  write(`clipped-${name}.json`, clipped.sort());
}

async function recordClasses(page: Page, name: string) {
  await page.evaluate(() => {
    document.querySelectorAll('details:not([open])')
      .forEach(details => details.setAttribute('open', ''));
  });
  const classes = await page.evaluate(() => {
    const elements = Array.from(document.querySelectorAll('[class]'));
    return Array.from(new Set(elements.flatMap(el => Array.from(el.classList)))).sort();
  });
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
