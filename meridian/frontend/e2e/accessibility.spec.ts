import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { pausedRecovery } from './fixtures/pausedRecovery';

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
        // Projector readability multiplies the type ramp by 1.2: title-3 18px, body 15px.
        await expect(page.locator('.mds-brief-head p')).toHaveCSS('font-size', '21.6px');
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

// The featured trip card only renders from a live catalog fetch - the
// showcase has no bundled preview inventory to fall back to (see
// DiscoveryWorkspace.tsx). This suite otherwise runs every API call
// offline (beforeEach above), so this one test overrides that with a
// realistic successful catalog response to exercise the real card the
// contrast checks below target, the same way a live backend would.
async function mockLiveCatalog(page: import('@playwright/test').Page) {
  await page.route('**/api/products**', route => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({
      products: [{
        product_id: 'TST-001', name: 'Fixture Wine Country Retreat', brand: 'Fixture Tours',
        price: 2500, description: 'A fixture trip used only to exercise the contrast check.',
        image_url: '/travel/catalog/TST-001.jpg', category: 'Wellness & Luxury',
        destination: 'Testville', region: 'Test Region',
        available_sizes: ['5 nights'], availability: { '5 nights': 4 }, highlights: ['fixture'],
      }],
      total: 1,
    }),
  }));
  await page.route('**/api/health', route => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({
      status: 'healthy', version: '1.0.0', environment: 'development',
      bedrock_model_id: 'global.anthropic.claude-sonnet-5', bedrock_model_label: 'Claude Sonnet 5',
      embedding_model_id: 'cohere.embed-v4:0', checkpoint_backend: 'AuroraDataApiSaver',
      checkpoint_durable: true, checkpoint_required: true, aurora_reachable: true,
      degraded_component: null, degraded_error_class: null,
    }),
  }));
  await page.route('**/api/memory/**', route => route.fulfill({
    status: 200, contentType: 'application/json', body: JSON.stringify({
      traveler_id: 'trv_meridian_demo', facts: [], profile: null,
      budget_ceiling_per_traveler_cents: null,
    }),
  }));
}

const stages = [[1920, 1080, 'live'], [1920, 1080, 'offline'], [1280, 720, 'live']] as const;
for (const [width, height, connection] of stages) {
  const title = `recovery hero, photo and workflow share one ${connection} stage`
    + ` at ${width}x${height}`;
  test(title, async ({ page }) => {
    await page.setViewportSize({ width, height });
    if (connection === 'live') await mockLiveCatalog(page);
    await page.goto('/showcase?present=1&view=recovery');
    await expect(page.locator('.mds-status-pill'))
      .toContainText(connection === 'live' ? 'Meridian live' : 'Meridian offline');
    const photo = page.getByRole('img', { name: 'Aircraft on final approach' });
    for (const mode of ['presenter controls', 'fullscreen']) {
      if (mode === 'fullscreen') {
        await page.getByRole('button', { name: 'Present fullscreen' }).click();
        await expect(page.locator('.mds-root')).toHaveAttribute('data-fullscreen', 'true');
      }
      await expect(photo).toBeVisible();
      const fold = await page.evaluate(() => ({
        steps: document.querySelector('.mds-recovery-launch-steps')!
          .getBoundingClientRect().bottom,
        card: document.querySelector('.mds-recovery-launch-card')!
          .getBoundingClientRect().bottom,
        photo: document.querySelector('.mds-mobile-disruption-media')!
          .getBoundingClientRect().height,
        viewport: innerHeight,
      }));
      expect(fold.steps, `${mode}: workflow row below the fold`).toBeLessThanOrEqual(fold.viewport);
      // The whole card, down to its no-booking promise, not a row that ends on the edge.
      expect(fold.card, `${mode}: launch card below the fold`).toBeLessThanOrEqual(fold.viewport);
      expect(fold.photo, `${mode}: photo shrunk away`).toBeGreaterThanOrEqual(200);
    }
  });
}

// The options the paused recovery dashboard renders.
const tokyoOptions = ['TYO-001', 'TYO-002', 'TYO-003', 'TYO-004'].map((id, index) => ({
  product_id: id, name: `Tokyo option ${index + 1}`, brand: 'Meridian partner',
  price: 1900 + index * 300, category: 'City & Culture', destination: 'Tokyo', region: 'Asia',
  description: 'Fixture package.', image_url: '/travel/catalog/TYO-001.jpg',
  available_sizes: ['5 nights', '7 nights'], availability: { '5 nights': 3 },
}));

/** Visible text below the footnote step, and presenter controls under 32px. */
async function projectorLegibility(page: import('@playwright/test').Page) {
  return page.evaluate(() => {
    const root = document.querySelector('.mds-root')!;
    const probe = document.createElement('span');
    probe.style.font = 'var(--mds-type-footnote)';
    root.appendChild(probe);
    const footnote = parseFloat(getComputedStyle(probe).fontSize);
    probe.remove();
    const shown = (el: Element) => {
      const box = el.getBoundingClientRect();
      if (box.width < 1 || box.height < 1) return false;
      for (let node: Element | null = el; node; node = node.parentElement) {
        const css = getComputedStyle(node);
        if (css.visibility === 'hidden' || Number(css.opacity) === 0) return false;
      }
      return true;
    };
    const ownText = (el: Element) => Array.from(el.childNodes)
      .filter(node => node.nodeType === Node.TEXT_NODE)
      .map(node => node.textContent!.trim()).join(' ').trim();
    const name = (el: Element) => `${el.tagName.toLowerCase()}.${el.className} `
      + `"${(el.textContent ?? '').trim().slice(0, 40)}"`;
    const texts = Array.from(root.querySelectorAll('*'))
      .filter(el => !el.closest('svg') && ownText(el) && shown(el));
    const hidden = (el: Element) => Boolean(el.closest('[aria-hidden="true"]'));
    const spoken = new Set(texts.filter(el => !hidden(el)).map(ownText));
    // Text hidden from assistive technology still reaches the room. It is
    // skipped only when decorative: a glyph with no letter or digit, or a copy
    // of text shown elsewhere on screen, which is checked in its own right.
    const decorative = (el: Element) => hidden(el)
      && (!/[\p{L}\p{N}]/u.test(ownText(el)) || spoken.has(ownText(el)));
    const small = texts
      .filter(el => !decorative(el))
      .filter(el => parseFloat(getComputedStyle(el).fontSize) < footnote - 0.05)
      .map(el => `${name(el)} ${getComputedStyle(el).fontSize}`);
    // Only a link inside running text is exempt, as WCAG 2.5.8 exempts inline targets.
    const inlineLink = (el: Element) => {
      const sentence = el.matches('a[href]') ? el.closest('p') : null;
      return Boolean(sentence)
        && sentence!.textContent!.trim().length > (el.textContent ?? '').trim().length;
    };
    const tiny = Array.from(root.querySelectorAll(
      'button, a[href], summary, [role="switch"], [role="tab"]',
    ))
      .filter(el => shown(el) && !inlineLink(el))
      .filter(el => {
        const box = el.getBoundingClientRect();
        return box.width < 32 || box.height < 32;
      })
      .map(el => {
        const box = el.getBoundingClientRect();
        return `${name(el)} ${Math.round(box.width)}x${Math.round(box.height)}`;
      });
    return { small, tiny };
  });
}

for (const theme of ['dark', 'light']) {
  const title = `${theme} projector: meaningful text reaches the footnote step,`
    + ' controls reach 32px';
  test(title, async ({ page }) => {
    await mockLiveCatalog(page);
    await page.route(url => url.pathname === '/api/chat', route => route.fulfill({
      json: pausedRecovery(tokyoOptions, route.request().postDataJSON().conversation_id),
    }));
    for (const [width, height] of [[1920, 1080], [1280, 720]]) {
      await page.setViewportSize({ width, height });
      for (const view of views) {
        await page.goto(`/showcase?present=1&view=${view}&theme=${theme}`);
        await expect(page.locator('.mds-status-pill')).toContainText('Meridian live');
        const found = await projectorLegibility(page);
        expect(found.small, `${view} at ${width}: text below footnote`).toEqual([]);
        expect(found.tiny, `${view} at ${width}: controls under 32px`).toEqual([]);
      }
      await page.goto(`/showcase?present=1&view=recovery&theme=${theme}`);
      await page.getByRole('button', { name: 'Start recovery' }).click();
      await expect(page.locator('.mds-recovery-decision-system')).toBeVisible();
      const found = await projectorLegibility(page);
      expect(found.small, `paused recovery at ${width}: text below footnote`).toEqual([]);
      expect(found.tiny, `paused recovery at ${width}: controls under 32px`).toEqual([]);
    }
  });
}

for (const theme of ['light', 'dark']) for (const present of [false, true]) {
  test(`${theme} ${present ? 'projector' : 'desktop'}: blue actions, focus and secondary text keep contrast`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await mockLiveCatalog(page);
    await page.goto(`/showcase?view=concierge&theme=${theme}${present ? '&present=1' : ''}`);
    const action = page.locator('.mc-trip.is-featured .mc-trip-open');
    await expect(action).toBeVisible();
    const colors = await page.locator('.mds-root').evaluate(el => {
      const css = getComputedStyle(el);
      const tokens = {
        surface: '--mds-surface', soft: '--mds-surface-2', muted: '--mds-label-2',
        line: '--mds-control-line', accent: '--mds-tint',
      };
      return Object.fromEntries(Object.entries(tokens)
        .map(([name, token]) => [name, css.getPropertyValue(token).trim()]));
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

test('offline notice is one content-height strip in every surface', async ({ page }) => {
  for (const [width, height] of [[1920, 1080], [1280, 720]]) {
    await page.setViewportSize({ width, height });
    const heights: number[] = [];
    for (const view of views) {
      await page.goto(`/showcase?present=1&view=${view}`);
      const notice = page.locator('.mc-connection-notice');
      await expect(notice).toHaveAttribute('role', 'status');
      await expect(notice.getByRole('button', { name: 'Reconnect' })).toBeVisible();
      // True whether or not anything ever loaded: the catalog has no stand-in trips.
      await expect(notice).toContainText('Only data from the last successful load is shown.');
      await expect(notice).not.toContainText('preview');
      const box = await notice.evaluate(el => ({
        height: el.getBoundingClientRect().height,
        content: Array.from(el.children)
          .reduce((tallest, child) => Math.max(tallest, child.getBoundingClientRect().height), 0),
        clipped: Array.from(el.querySelectorAll('*'))
          .some(child => child.getBoundingClientRect().bottom
            > el.getBoundingClientRect().bottom + 1),
      }));
      expect(box.clipped, `${view} at ${width}: notice text overflows its strip`).toBe(false);
      expect(box.height - box.content, `${view} at ${width}: notice is taller than its content`)
        .toBeLessThanOrEqual(40);
      heights.push(Math.round(box.height));
    }
    expect(new Set(heights).size, `notice heights at ${width}: ${heights.join(', ')}`).toBe(1);
  }
});

for (const theme of ['light', 'dark']) {
  test(`${theme}: unset traveler values are quieter than their labels`, async ({ page }) => {
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto(`/showcase?present=1&view=concierge&theme=${theme}`);
    const tertiary = await page.locator('.mds-root').evaluate(el => {
      const probe = document.createElement('span');
      probe.style.color = getComputedStyle(el).getPropertyValue('--mds-label-3');
      el.appendChild(probe);
      const color = getComputedStyle(probe).color;
      probe.remove();
      return color;
    });
    const rows = [
      {
        value: '.mc-departure .mc-unset',
        label: '.mc-departure > div:first-child > span:first-child',
      },
      { value: '.mc-brief-details .mc-unset', label: '.mc-brief-details > div:last-child dt' },
    ];
    for (const row of rows) {
      const value = page.locator(row.value);
      await expect(value).toHaveText('Not set');
      const [valueCss, labelCss] = await Promise.all([row.value, row.label].map(selector =>
        page.locator(selector).evaluate(el => {
          const css = getComputedStyle(el);
          return { size: css.fontSize, color: css.color };
        })));
      expect(valueCss.size, `${row.value} size`).toBe(labelCss.size);
      expect(valueCss.color, `${row.value} color`).toBe(tertiary);
    }
  });
}

for (const theme of ['light', 'dark']) {
  const title = `${theme}: headings focused from code draw no ring while keyboard focus keeps one`;
  test(title, async ({ page }) => {
    const outline = (selector: string) => page.locator(selector).evaluate(el => {
      const css = getComputedStyle(el);
      return { style: css.outlineStyle, width: parseFloat(css.outlineWidth) };
    });
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto(`/showcase?present=1&view=recovery&theme=${theme}`);
    const title = page.getByRole('heading', { name: "Alex's JFK to Tokyo recovery" });
    await expect(title).toBeFocused();
    expect((await outline('.mds-recovery-overview-title h1')).style).toBe('none');
    await page.keyboard.press('Tab');
    await expect(page.getByRole('button', { name: 'Start recovery' })).toBeFocused();
    const ring = await outline('.mds-mobile-disruption-primary');
    expect(ring.style).toBe('solid');
    expect(ring.width).toBeGreaterThanOrEqual(2);

    await page.goto(`/showcase?present=1&view=proof&theme=${theme}`);
    await page.getByRole('button', { name: 'Session takeaways' }).click();
    await expect(page.locator('.mc-session-close h1')).toBeFocused();
    expect((await outline('.mc-session-close h1')).style).toBe('none');
  });
}

test('dark room link overrides a saved light theme while explicit light and fullscreen remain available', async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem('meridian.theme', 'light'));
  await page.goto('/showcase?present=1&view=briefing');
  await expect(page.locator('.mds-root')).toHaveAttribute('data-theme', 'dark');
  await page.getByRole('button', { name: 'Present fullscreen' }).click();
  await expect(page.locator('.mds-root')).toHaveAttribute('data-fullscreen', 'true');
  await expect(page.getByRole('region', { name: 'Presenter controls' })).toBeHidden();
  await page.evaluate(() => document.exitFullscreen());
  await expect(page.getByRole('region', { name: 'Presenter controls' })).toBeVisible();
  await expect(page.locator('.mds-root')).toHaveAttribute('data-audience-layout', 'true');
  await page.getByRole('button', { name: 'Switch to light mode' }).click();
  await expect(page.locator('.mds-root')).toHaveAttribute('data-theme', 'light');
  await page.goto('/showcase?present=1&theme=light&view=briefing');
  await expect(page.locator('.mds-root')).toHaveAttribute('data-theme', 'light');
  await page.locator('summary').filter({ hasText: 'Display settings' }).click();
  await page.getByRole('checkbox', { name: 'Projector readability' }).uncheck();
  await expect(page.locator('.mds-root')).not.toHaveAttribute('data-projector-readability', 'true');
});

test('dark room preset also applies while the showcase bundle is loading', async ({ page }) => {
  let release: () => void = () => {};
  const bundle = new Promise<void>(resolve => { release = resolve; });
  await page.route(/\/(?:src\/showcase\/MeridianDeviceShowcase\.tsx|assets\/MeridianDeviceShowcase-[^/]+\.js)(?:\?.*)?$/, async route => {
    await bundle;
    await route.continue();
  });
  try {
    await page.goto('/showcase?present=1', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('status', { name: '' })).toContainText('Loading Meridian');
    await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
    await expect(page.locator('.route-skeleton')).toHaveCSS('background-color', 'rgb(0, 0, 0)');
  } finally {
    release();
  }
  await expect(page.locator('.mds-root')).toHaveAttribute('data-theme', 'dark');
});

for (const theme of ['dark', 'light']) {
  const title = `${theme}: idle live views run no looping motion with full motion allowed`;
  test(title, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await page.setViewportSize({ width: 1920, height: 1080 });
    await mockLiveCatalog(page);
    for (const view of views) {
      await page.goto(`/showcase?present=1&view=${view}&theme=${theme}`);
      await expect(page.locator('.mds-status-pill')).toContainText('Meridian live');
      // Only work in progress may loop; an idle, connected stage holds still.
      await expect.poll(() => page.evaluate(() => document.getAnimations()
        .filter(animation => animation.playState === 'running')
        .map(animation => {
          const target = (animation.effect as KeyframeEffect | null)?.target as Element | null;
          return `${(animation as CSSAnimation).animationName} on ${target?.className ?? '?'}`;
        })), { message: view }).toEqual([]);
    }
  });
}

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
      await expect(page.getByRole('heading', { name: 'Prepare the data' })).toBeVisible();
      await expect(page.getByRole('heading', { name: 'Phase 3 - Retrieval' })).toBeHidden();
      const architectureToggle = page.locator('.mds-brief-overview > summary');
      await architectureToggle.focus();
      await page.keyboard.press('Enter');
      await expect(architecture).toBeHidden();
      await page.keyboard.press('Space');
      await expect(architecture).toBeVisible();
      const summaries = page.locator('.mds-brief summary');
      await expect(summaries).toHaveCount(11);
      for (const summary of await page.locator('.mds-brief details:not(.mds-brief-overview) > summary').all()) {
        await summary.focus();
        await page.keyboard.press('Enter');
        await expect(summary.locator('..')).toHaveAttribute('open', '');
      }
      await expect(page.getByRole('heading', { name: 'Phase 3 - Retrieval' })).toBeVisible();
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
