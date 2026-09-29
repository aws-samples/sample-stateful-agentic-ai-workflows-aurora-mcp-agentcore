import { expect, test } from '@playwright/test';

// Deterministic UI fixture, using real catalog destination and duration
// strings (including the longest single-word destination, Yellowstone). These
// broke mid-word between 1280 and 1440px before the I1 fix: the command
// layout squeezed the alternatives into a narrow column, and a specificity
// bug rendered the compact route at title-2 instead of title-3.
function alternative(
  id: string,
  name: string,
  destination: string,
  nights: string,
  price: number,
) {
  return {
    product_id: id, name, brand: 'Meridian partner', price, category: 'City & Culture',
    destination, region: destination, description: `${name} package.`,
    image_url: `/travel/catalog/${id}.jpg`, available_sizes: [nights],
    availability: { [nights]: 3 },
  };
}
const products = [
  alternative('TYO-001', 'Tokyo Culture & Cuisine', 'Tokyo', '5 nights', 2499),
  alternative('AML-002', 'Amalfi Coast Villa Week', 'Positano', '5 nights', 1599),
  alternative('FAM-002', 'Yellowstone Wildlife Safari', 'Yellowstone', '8 nights', 2799),
  alternative('TUS-004', 'Tuscany Wine & Wellness', 'Chianti', '6 nights', 3299),
];

test.beforeEach(async ({ page }) => {
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/chat') {
      return route.fulfill({ json: {
        message: 'Your Tokyo replacement plan is ready.',
        products,
        activities: [],
        conversation_id: 'i1-recovery-test',
        workflow_status: 'complete',
      } });
    }
    const health = { status: 'healthy', bedrock_model_id: 'fixture',
      embedding_model_id: 'fixture', checkpoint_backend: 'fixture' };
    const memory = { traveler_id: 'trv_meridian_demo', facts: [] };
    return route.fulfill({ json: path.endsWith('/health')
      ? health
      : path.includes('/memory/') ? memory : { products: [] } });
  });
});

test('recovery alternative cards keep the route on one line at 1440', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/showcase?view=recovery');
  await page.getByRole('button', { name: 'Start recovery' }).click();
  const routeLabels = page.locator('.mds-flight-option-card .mds-decision-route b');
  await expect(routeLabels.first()).toBeVisible();
  expect(await routeLabels.count()).toBeGreaterThan(0);
  for (const label of await routeLabels.all()) {
    const isSingleLine = await label.evaluate((el) => {
      const lineHeight = parseFloat(window.getComputedStyle(el).lineHeight);
      return el.getBoundingClientRect().height <= lineHeight + 1;
    });
    expect(isSingleLine, `"${await label.textContent()}" wraps to more than one line`).toBe(true);
  }
});

const pausedActivities = [
  ['Workflow node: classify → plan', 'LangGraph StateGraph', 0, []],
  ['Workflow node: search', 'LangGraph → SearchAgent', 956, []],
  ['Checkpoint · AuroraDataApiSaver.put', 'Aurora · LangGraph checkpoint tables', 458,
    [{ label: 'checkpoint_durable', value: 'true' }]],
].map(([title, component, ms, fields], index) => ({
  id: `paused-${index}`, timestamp: '2026-09-28T18:00:00Z', activity_type: 'tool_call', title,
  execution_time_ms: ms, telemetry: { category: 'orchestration', component, status: 'ok', fields },
}));

for (const motion of ['no-preference', 'reduce'] as const) {
  test(`checkpoint confirmation with ${motion} motion`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: motion });
    await page.route(url => url.pathname === '/api/chat', route => route.fulfill({ json: {
      message: 'Workflow paused after a committed checkpoint.', products, activities: pausedActivities,
      conversation_id: route.request().postDataJSON().conversation_id, workflow_status: 'paused',
    } }));
    await page.setViewportSize({ width: 1920, height: 1080 });
    await page.goto('/showcase?view=recovery&present=1');
    await page.getByRole('button', { name: 'Start recovery' }).click();
    const checkpoint = page.locator('.mds-recovery-launch-steps li')
      .filter({ hasText: 'Save an Aurora checkpoint' });
    await expect(checkpoint).toHaveClass(/is-visited/);
    await expect(checkpoint.locator('.mds-step-source')).toHaveText('Aurora Data API · 458 ms');
    await expect(page.locator('.mds-aurora-glow')).toHaveCount(motion === 'reduce' ? 0 : 1);
    if (motion === 'reduce') {
      await expect.poll(() => page.evaluate(() => document.getAnimations()
        .filter(animation => animation.playState === 'running').length)).toBe(0);
      expect(await checkpoint.locator('.mds-recovery-step-icon').evaluate(el => el.style.opacity))
        .not.toBe('0');
    }
  });
}
