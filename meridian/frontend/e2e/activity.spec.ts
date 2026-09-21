import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const activities = [
  ['recall', 'AgentCore Memory session restored', 'memory_short', 'Bedrock AgentCore Memory'],
  ['query', 'Aurora SQL query', 'data', 'Aurora'],
  ['start', 'AgentCore Runtime turn started', 'runtime', 'Bedrock AgentCore Runtime'],
  ['rank', 'Cohere rerank', 'model', 'Amazon Bedrock'],
  ['reply', 'Memory-grounded reply ready', 'synthesis', 'Meridian app'],
].map(([id, title, category, component]) => ({ id, title, activity_type: 'tool_call', telemetry: { category, component, status: 'ok' } }));

for (const theme of ['light', 'dark']) {
  test(`${theme} activity: waiting, completed service sources, replay and failed steps`, async ({ page }) => {
    for (const width of [1440, 320]) {
      let probes = 0;
      let release: () => void = () => {};
      const gate = new Promise<void>(resolve => { release = resolve; });
      await page.unrouteAll();
      await page.route(url => url.pathname.startsWith('/api/'), async route => {
        const path = new URL(route.request().url()).pathname;
        if (path.endsWith('/rls-probe')) { probes += 1; return route.fulfill({ status: 503, json: { detail: 'Probe unavailable in this fixture' } }); }
        if (path === '/api/chat') {
          await gate;
          const failed = route.request().postDataJSON().message.includes('failed');
          return route.fulfill({ json: { message: failed ? 'The query failed.' : 'Your Tokyo travel brief is ready.', products: [],
            activities: failed ? [{ ...activities[1], telemetry: { ...activities[1].telemetry, status: 'error' } }] : activities,
            conversation_id: 'activity-test' } });
        }
        return route.fulfill({ json: path.endsWith('/health')
          ? { status: 'healthy', bedrock_model_id: 'fixture', embedding_model_id: 'fixture', checkpoint_backend: 'fixture' }
          : path.includes('/memory/') ? { traveler_id: 'trv_meridian_demo', facts: [] } : { products: [] } });
      });
      await page.setViewportSize({ width, height: 1000 });
      await page.goto(`/showcase?view=ladder&theme=${theme}`);
      await page.getByRole('button', { name: /^Phase 4, Production:/ }).click();
      await page.getByRole('switch', { name: 'Use traveler context: off' }).click();
      const input = page.getByRole('textbox', { name: 'Ask Meridian anything' });
      await input.fill('Recall my Tokyo plan');
      await input.press('Enter');
      await expect(page.getByRole('status').filter({ hasText: 'Activity appears with the response' })).toBeVisible();
      await expect(page.locator('.mds-thinking-item.is-active')).toHaveCount(0);
      release();
      const steps = page.getByRole('list', { name: 'Recorded request steps' });
      await expect(steps.locator('.is-done')).toHaveCount(5);
      await expect(steps.getByText('AgentCore', { exact: true })).toHaveCount(2);
      await expect(steps.getByText('Aurora', { exact: true })).toHaveCount(1);
      await expect(steps.getByText('Bedrock', { exact: true })).toHaveCount(1);
      await expect.poll(() => steps.locator('img').evaluateAll(imgs => imgs.every(img => (img as HTMLImageElement).complete && (img as HTMLImageElement).naturalWidth > 0))).toBe(true);
      await expect(page.locator('.mds-span-list')).toHaveCount(0);
      await expect(steps.locator('.mds-activity-group[open]')).toHaveCount(0);
      const queryStep = steps.locator('.mds-activity-group > summary').filter({ hasText: 'Querying live travel data' });
      await queryStep.focus();
      await page.keyboard.press('Enter');
      const queryEvent = steps.locator('.mds-activity-event > summary').filter({ hasText: 'Aurora SQL query' });
      await expect(queryEvent).toBeVisible();
      await queryEvent.focus();
      await page.keyboard.press('Space');
      await expect(steps.locator('.mds-activity-event-detail').filter({ hasText: 'data · ok' })).toBeVisible();
      await expect(page.getByRole('button', { name: 'Run RLS probe' })).toBeHidden();
      await page.getByText('Inspect evidence', { exact: true }).click();
      await expect(page.getByRole('button', { name: 'Run RLS probe' })).toBeVisible();
      expect(probes).toBe(0);
      await page.getByRole('button', { name: 'Run RLS probe', exact: true }).click();
      await expect.poll(() => probes).toBeGreaterThan(0);
      await expect(page.getByText(/Governance probe unavailable:/)).toBeVisible();
      await page.getByText('Inspect evidence', { exact: true }).click();
      await queryStep.click();
      await page.getByRole('button', { name: 'Replay trace', exact: true }).click();
      await expect(steps.locator('[aria-current="step"]')).toBeVisible();
      await expect(page.getByText(/Replaying recorded activity/)).toBeVisible();
      await expect(page.locator('.mds-thinking-caption')).toHaveText(/Recorded activity · 5 events/, { timeout: 10000 });
      await expect(steps.locator('.is-done')).toHaveCount(5);
      const audit = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa', 'best-practice']).analyze();
      expect(audit.violations.map(v => ({ id: v.id, targets: v.nodes.map(n => n.target) }))).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await input.fill('Show a failed query');
      await input.press('Enter');
      await expect(steps.getByText('Failed', { exact: true })).toBeVisible();
      await expect(steps.locator('.is-done')).toHaveCount(0);
    }
  });
}


test('evidence opens to recorded SQL and disappears when the next turn has none', async ({ page }) => {
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/chat') return route.fulfill({ json: { message: 'The recorded result is ready.', products: [], conversation_id: 'inspector-test',
      activities: [{ ...activities[1], sql_query: route.request().postDataJSON().message.includes('without') ? undefined : 'SELECT package_id FROM trip_packages LIMIT 5' }],
    } });
    return route.fulfill({ json: path.endsWith('/health') ? { status: 'healthy', bedrock_model_id: 'fixture', embedding_model_id: 'fixture' } : { products: [] } });
  });
  for (const [width, theme] of [[1440, 'light'], [320, 'dark']] as const) {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto(`/showcase?view=ladder&theme=${theme}`);
    const input = page.getByRole('textbox', { name: 'Ask Meridian anything' });
    await input.fill('Show trips with SQL'); await input.press('Enter');
    const inspector = page.locator('.mds-trace-inspector');
    const disclosure = inspector.locator(':scope > summary');
    await expect(disclosure).toBeVisible();
    await disclosure.focus(); await page.keyboard.press('Enter');
    await expect(inspector.getByRole('heading', { name: 'SQL', exact: true })).toBeVisible();
    await expect(inspector.locator('pre')).toHaveText('SELECT package_id FROM trip_packages LIMIT 5');
    await expect(inspector.getByRole('group', { name: 'Evidence views' })).toHaveCount(0);
    await expect(page.getByRole('button', { name: 'Overview', exact: true })).toHaveCount(0);
    const audit = await new AxeBuilder({ page }).include('.mds-trace-panel').withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa', 'best-practice']).analyze();
    expect(audit.violations).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await input.fill('Show trips without SQL'); await input.press('Enter');
    await expect(inspector).toHaveCount(0);
  }
});


test('all five phases animate waiting and replay spinners and respect reduced motion', async ({ page }) => {
  let release: () => void = () => {};
  let gate: Promise<void>;
  let releaseProbe: () => void = () => {};
  const probeGate = new Promise<void>(resolve => { releaseProbe = resolve; });
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/rls-probe')) { await probeGate; return route.fulfill({ status: 503, json: { detail: 'Probe fixture' } }); }
    if (path === '/api/chat') { await gate; return route.fulfill({ json: { message: 'Search complete.', products: [], activities, conversation_id: 'spinner-test' } }); }
    return route.fulfill({ json: path.endsWith('/health') ? { status: 'healthy', bedrock_model_id: 'fixture', embedding_model_id: 'fixture' } : { products: [] } });
  });
  for (const phase of [1, 2, 3, 4, 5]) {
    gate = new Promise<void>(resolve => { release = resolve; });
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await page.goto('/showcase?view=ladder');
    await page.getByRole('button', { name: new RegExp(`^Phase ${phase},`) }).click();
    if (phase === 5) await page.getByRole('button', { name: 'Run to checkpoint', exact: true }).click();
    else {
      const input = page.getByRole('textbox', { name: 'Ask Meridian anything' });
      await input.fill('Find a quiet wine-country retreat'); await input.press('Enter');
    }
    const spinner = page.locator('.mds-thinking-wait .mds-activity-spinner');
    try {
      await expect(spinner).toBeVisible();
      await expect(spinner).toHaveCSS('animation-name', 'mds-activity-spin');
      const initial = await spinner.evaluate(el => getComputedStyle(el).transform);
      await expect.poll(() => spinner.evaluate(el => getComputedStyle(el).transform)).not.toBe(initial);
      await page.emulateMedia({ reducedMotion: 'reduce' });
      await expect(spinner).toHaveCSS('animation-name', 'none');
      await expect(spinner).toHaveCSS('transform', 'none');
      await page.emulateMedia({ reducedMotion: 'no-preference' });
      await expect(spinner).toHaveCSS('animation-name', 'mds-activity-spin');
    } finally { release(); }
    await expect(page.locator('.mds-thinking-wait')).toHaveCount(0);
    await page.getByRole('button', { name: 'Replay trace', exact: true }).click();
    await expect(page.locator('.mds-thinking-item.is-active .mds-activity-spinner')).toHaveCSS('animation-name', 'mds-activity-spin');
    await expect(page.locator('.mds-thinking-caption')).toHaveText('Recorded activity · 5 events');
    if (phase === 4) {
      await page.getByText('Inspect evidence', { exact: true }).click();
      await page.getByRole('button', { name: 'Run RLS probe', exact: true }).click();
      const probeSpinner = page.getByRole('button', { name: 'Running governance probe', exact: true }).locator('svg');
      try {
        await expect(probeSpinner).toHaveCSS('animation-name', 'mds-send-spin');
        const initial = await probeSpinner.evaluate(el => getComputedStyle(el).transform);
        await expect.poll(() => probeSpinner.evaluate(el => getComputedStyle(el).transform)).not.toBe(initial);
        await page.emulateMedia({ reducedMotion: 'reduce' });
        await expect(probeSpinner).toHaveCSS('animation-name', 'none');
      } finally { releaseProbe(); }
      await expect(page.getByText(/Governance probe unavailable:/)).toBeVisible();
    }
  }
});
