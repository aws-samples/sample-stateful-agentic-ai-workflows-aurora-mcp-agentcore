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

for (const theme of ['light', 'dark']) for (const width of [1920, 1280, 960, 320]) {
  test(`${theme} projector preset at ${width}px: all surfaces retain contrast and reflow`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 1920 ? 1080 : 900 });
    for (const view of views) {
      await page.goto(`/showcase?present=1&view=${view}&theme=${theme}`);
      await expect(page.locator('.mds-root')).toHaveAttribute('data-theme', theme);
      await expect(page.locator('.mds-root')).toHaveAttribute('data-projector-readability', 'true');
      await expect(page.locator('.mds-desktop-sidebar')).toBeHidden();
      const audit = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa', 'best-practice']).analyze();
      expect(audit.violations.map(v => ({ id: v.id, targets: v.nodes.map(n => n.target) })), view).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), view).toBe(true);
      if (width >= 1280) {
        expect(await page.locator('.mds-shell-surface-nav').evaluate(el => el.scrollWidth <= el.clientWidth), `${view}: clipped surface navigation`).toBe(true);
      }
      if (view === 'briefing' && width > 860) {
        await expect(page.locator('.mds-brief-head p')).toHaveCSS('font-size', '20px');
        await page.getByText('Tool contracts & Cedar policies', { exact: true }).click();
        for (const code of await page.locator('.mds-brief-policy pre').all()) {
          await expect(code).toHaveCSS('font-size', '18px');
          expect(await code.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
        }
      }
    }
  });
}

function contrastRatio(a: string, b: string): number {
  const luminance = (color: string) => {
    const rgb = color.startsWith('#')
      ? [1, 3, 5].map(offset => parseInt(color.slice(offset, offset + 2), 16))
      : (color.match(/[\d.]+/g) ?? []).slice(0, 3).map(Number);
    const linear = rgb.map(value => {
      const channel = value / 255;
      return channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4;
    });
    return linear[0] * .2126 + linear[1] * .7152 + linear[2] * .0722;
  };
  const values = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (values[0] + .05) / (values[1] + .05);
}

for (const theme of ['light', 'dark']) for (const present of [false, true]) {
  test(`${theme} ${present ? 'projector' : 'desktop'}: blue actions, focus and secondary text keep contrast`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto(`/showcase?view=concierge&theme=${theme}${present ? '&present=1' : ''}`);
    const action = page.locator('.mc-trip.is-featured .mc-trip-open');
    await expect(action).toBeVisible();
    const colors = await page.locator('.mds-root').evaluate(el => {
      const css = getComputedStyle(el);
      return Object.fromEntries(['surface', 'soft', 'muted', 'line', 'accent'].map(name => [name, css.getPropertyValue(`--mc-${name}`).trim()]));
    });
    for (const state of ['default', 'hover', 'focus']) {
      if (state === 'hover') await action.hover();
      if (state === 'focus') {
        await page.mouse.move(0, 0);
        await action.focus();
      }
      const computed = await action.evaluate(el => {
        const css = getComputedStyle(el);
        return { foreground: css.color, background: css.backgroundColor,
          edge: parseFloat(css.borderTopWidth) > 0 ? css.borderTopColor : css.backgroundColor };
      });
      expect(contrastRatio(computed.foreground, computed.background), `${state} action label`).toBeGreaterThanOrEqual(7);
      expect(contrastRatio(computed.edge, colors.surface), `${state} action boundary`).toBeGreaterThanOrEqual(3);
    }
    expect(contrastRatio(colors.muted, colors.soft), 'secondary copy').toBeGreaterThanOrEqual(7);
    expect(contrastRatio(colors.line, colors.soft), 'field boundary').toBeGreaterThanOrEqual(3);
    expect(contrastRatio(colors.accent, colors.soft), 'focus indicator').toBeGreaterThanOrEqual(3);
  });
}

test('room link overrides a saved dark theme while explicit dark and fullscreen remain available', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('meridian.theme', 'dark'));
  await page.goto('/showcase?present=1&view=briefing');
  await expect(page.locator('.mds-root')).toHaveAttribute('data-theme', 'light');
  await page.getByRole('button', { name: 'Present fullscreen' }).click();
  await expect(page.locator('.mds-root')).toHaveAttribute('data-fullscreen', 'true');
  await expect(page.getByRole('region', { name: 'Presenter controls' })).toBeHidden();
  await page.evaluate(() => document.exitFullscreen());
  await expect(page.getByRole('region', { name: 'Presenter controls' })).toBeVisible();
  await expect(page.locator('.mds-root')).toHaveAttribute('data-audience-layout', 'true');
  await page.getByRole('button', { name: 'Switch to dark mode' }).click();
  await expect(page.locator('.mds-root')).toHaveAttribute('data-theme', 'dark');
  await page.goto('/showcase?present=1&theme=dark&view=briefing');
  await expect(page.locator('.mds-root')).toHaveAttribute('data-theme', 'dark');
  await page.locator('summary').filter({ hasText: 'Display settings' }).click();
  await page.getByRole('checkbox', { name: 'Projector readability' }).uncheck();
  await expect(page.locator('.mds-root')).not.toHaveAttribute('data-projector-readability', 'true');
});

test('light room preset also applies while the showcase bundle is loading', async ({ page }) => {
  let release: () => void = () => {};
  const bundle = new Promise<void>(resolve => { release = resolve; });
  await page.route(/\/(?:src\/showcase\/MeridianDeviceShowcase\.tsx|assets\/MeridianDeviceShowcase-[^/]+\.js)(?:\?.*)?$/, async route => {
    await bundle;
    await route.continue();
  });
  try {
    await page.goto('/showcase?present=1', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('status', { name: '' })).toContainText('Loading Meridian');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
    await expect(page.locator('.route-skeleton')).toHaveCSS('background-color', 'rgb(245, 245, 242)');
  } finally {
    release();
  }
  await expect(page.locator('.mds-root')).toHaveAttribute('data-theme', 'light');
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
      await expect(page.locator('.mds-brief details[open]')).toHaveCount(1);
      await expect(page.getByRole('heading', { name: 'Prepare the data before the question' })).toBeVisible();
      await expect(page.getByRole('heading', { name: 'Phase 3 · Retrieval' })).toBeHidden();
      const architectureToggle = page.locator('.mds-brief-overview > summary');
      await architectureToggle.focus();
      await page.keyboard.press('Enter');
      await expect(architecture).toBeHidden();
      await page.keyboard.press('Space');
      await expect(architecture).toBeVisible();
      const summaries = page.locator('.mds-brief summary');
      await expect(summaries).toHaveCount(6);
      for (const summary of await page.locator('.mds-brief details:not(.mds-brief-overview) > summary').all()) {
        await summary.focus();
        await page.keyboard.press('Enter');
        await expect(summary.locator('..')).toHaveAttribute('open', '');
      }
      await expect(page.getByRole('heading', { name: 'Phase 3 · Retrieval' })).toBeVisible();
      const candidate = page.getByRole('img', { name: 'Green rice terraces and palms in Bali' });
      await candidate.scrollIntoViewIfNeeded();
      await expect.poll(() => candidate.evaluate((img: HTMLImageElement) => img.complete && img.naturalWidth > 0)).toBe(true);
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
