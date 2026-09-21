import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const views = ['concierge', 'ladder', 'recovery', 'proof', 'briefing'];
const live = process.env.MERIDIAN_A11Y_LIVE === '1';
test.beforeEach(async ({ page }) => {
  if (!live) await page.route(url => url.pathname.startsWith('/api/') || url.pathname === '/health', route => route.fulfill({
    status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'Unavailable for offline accessibility checks' }),
  }));
});

for (const theme of ['light', 'dark']) for (const width of [1366, 640, 320]) {
  test(`${theme} surfaces at ${width}px: semantics, contrast, reflow and reduced motion`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    for (const view of views) {
      await page.goto(`/showcase?view=${view}&theme=${theme}`);
      await expect(page.getByRole('navigation', { name: 'Meridian capability ladder', exact: true })).toBeVisible();
      await expect(page.getByRole('region', { name: 'Travel workspace' })).toBeVisible();
      const audit = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa', 'best-practice']).analyze();
      expect(audit.violations, `${view}: ${JSON.stringify(audit.violations.map(v => ({ id: v.id, nodes: v.nodes.map(n => n.target) })))}`).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `${view}: document overflow`).toBe(true);
      await expect.poll(() => page.evaluate(() => document.getAnimations().filter(a => a.playState === 'running').length)).toBe(0);
    }
  });
}

test('display settings remain available by keyboard and close with Escape', async ({ page }) => {
  await page.goto('/showcase?view=concierge');
  const disclosure = page.locator('summary').filter({ hasText: 'Display settings' });
  await disclosure.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('checkbox', { name: 'Projector readability' })).toBeVisible();
  await page.keyboard.press('Tab');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('checkbox', { name: 'Projector readability' })).not.toBeVisible();
  await expect(disclosure).toBeFocused();
});

test('reduced-motion preference can change without reloading the page', async ({ page }) => {
  await page.goto('/showcase?view=concierge');
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await expect.poll(() => page.evaluate(() => matchMedia('(prefers-reduced-motion: reduce)').matches)).toBe(false);
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await page.getByRole('button', { name: 'Recovery desk', exact: true }).click();
  await expect.poll(() => page.evaluate(() => document.getAnimations().filter(a => a.playState === 'running').length)).toBe(0);
});

for (const theme of ['light', 'dark']) {
  test(`${theme} briefing: keyboard reference, readable architecture and surface handoffs`, async ({ page }) => {
    for (const width of [1440, 900, 320]) {
      await page.setViewportSize({ width, height: 900 });
      await page.goto(`/showcase?view=briefing&theme=${theme}`);
      await expect(page.locator('.mds-desktop-sidebar')).toBeHidden();
      const architecture = width > 900
        ? page.getByRole('img', { name: /Meridian request and state architecture/ })
        : page.getByRole('list', { name: 'Meridian request and state architecture' });
      await expect(architecture).toBeVisible();
      const summaries = page.locator('.mds-brief summary');
      await expect(summaries).toHaveCount(3);
      for (const summary of await summaries.all()) {
        await summary.focus();
        await page.keyboard.press('Enter');
        await expect(summary.locator('..')).toHaveAttribute('open', '');
      }
      await expect(page.getByText('meridian_hold_governance')).toBeVisible();
      await expect(architecture).toBeVisible();
      const audit = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa', 'best-practice']).analyze();
      expect(audit.violations).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.getByRole('button', { name: 'Inspect system evidence' }).click();
      await expect(page.locator('.mds-desktop-app')).not.toHaveClass(/is-solution-briefing/);
      await page.getByRole('button', { name: 'Solution briefing', exact: true }).click();
      await page.getByRole('button', { name: 'Open the capability ladder' }).click();
      await expect(page.locator('.mds-desktop-app')).not.toHaveClass(/is-solution-briefing/);
    }
  });
}
