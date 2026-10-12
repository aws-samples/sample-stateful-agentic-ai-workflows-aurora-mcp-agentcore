import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { pausedRecovery } from './fixtures/pausedRecovery';

test('one run connects Concierge, every capability, evidence, briefing and recovery', async ({ page }) => {
  const requests: { phase: number; message: string }[] = [];
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/chat' || path === '/api/chat/stream') {
      const request = route.request().postDataJSON();
      requests.push(request);
      const response = request.phase === 5 ? pausedRecovery([], request.conversation_id) : {
        message: `Recorded response from capability ${request.phase}.`, products: [], conversation_id: 'connected-run',
        activities: [{ id: `step-${requests.length}`, timestamp: '2026-10-01T12:00:00Z',
          activity_type: 'search', title: `Observed capability ${request.phase} query`,
          sql_query: 'SELECT package_id FROM trip_packages LIMIT 3',
          execution_time_ms: 34, telemetry: { category: 'data', component: 'Aurora', status: 'ok' } }],
      };
      return route.fulfill(path.endsWith('/stream')
        ? { contentType: 'text/event-stream', body: `data: ${JSON.stringify({ type: 'complete', response })}\n\n` }
        : { json: response });
    }
    return route.fulfill({ json: path.endsWith('/health') ? { status: 'healthy', bedrock_model_id: 'fixture' }
      : path.includes('/memory/') ? { traveler_id: 'trv_meridian_demo', facts: [] }
      : path.includes('/journeys') ? { journeys: [] } : { products: [] } });
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto('/showcase?view=concierge&theme=dark');
  const input = page.getByRole('textbox', { name: 'Ask Meridian anything' });
  await input.fill('Plan Tokyo');
  await input.press('Enter');
  await expect(page.getByText('Recorded response from capability 4.', { exact: true }).last()).toBeVisible();
  const surfaces = page.getByRole('list', { name: 'Meridian surfaces' });
  await surfaces.getByRole('button', { name: 'Capability ladder', exact: true }).click();
  await expect(page.getByRole('button', { name: /^Phase 1,/ })).toHaveAttribute('aria-current', 'step');
  await expect(page.getByText('Recorded response from capability 4.', { exact: true })).toHaveCount(0);
  await expect(page.getByText('Observed capability 4 query', { exact: true })).toHaveCount(0);
  await surfaces.getByRole('button', { name: 'Concierge', exact: true }).click();
  await expect(page.getByText('Recorded response from capability 4.', { exact: true }).last()).toBeVisible();
  expect(requests).toHaveLength(1);
  await surfaces.getByRole('button', { name: 'System evidence', exact: true }).click();
  await expect(page.getByText('Recorded activity for your production response.')).toBeVisible();
  await page.getByRole('button', { name: 'Rerun query', exact: true }).click();
  await expect.poll(() => requests.length).toBe(2);
  expect(requests[1].phase).toBe(4);
  await expect(page.getByRole('button', { name: 'Open this capability' })).toBeEnabled();
  await page.getByRole('list', { name: 'Recorded request steps' }).locator('.mds-activity-group > summary').click();
  await expect(page.getByRole('list', { name: 'Recorded request steps' }).getByText('Observed capability 4 query', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Open this capability' }).click();
  await expect(page.getByRole('button', { name: /^Phase 4,/ })).toHaveAttribute('aria-current', 'step');
  await expect(page.getByText('Recorded response from capability 4.', { exact: true }).last()).toBeVisible();
  await surfaces.getByRole('button', { name: 'Concierge', exact: true }).click();
  await surfaces.getByRole('button', { name: 'Capability ladder', exact: true }).click();
  await expect(page.getByText('Recorded response from capability 4.', { exact: true }).last()).toBeVisible();
  expect(requests).toHaveLength(2);

  for (const [index, name] of ['SQL', 'MCP', 'Retrieval', 'Production', 'Workflow'].entries()) {
    const phase = index + 1;
    await surfaces.getByRole('button', { name: 'Solution briefing', exact: true }).click();
    await page.getByRole('heading', { name: 'Build five capabilities', exact: true }).click();
    await page.getByRole('heading', { name: `Phase ${phase} - ${name}`, exact: true }).click();
    await page.getByRole('button', { name: `Open Phase ${phase}: ${name}`, exact: true }).click();
    await expect(page.getByRole('button', { name: new RegExp(`^Phase ${phase},`) })).toHaveAttribute('aria-current', 'step');
    const before = requests.length;
    if (phase === 5) {
      await page.getByRole('button', { name: 'Run to saved step', exact: true }).click();
      await expect(page.getByRole('button', { name: 'Continue at recovery desk' })).toBeEnabled();
      const thread = new URL(page.url()).searchParams.get('thread');
      await page.getByRole('button', { name: 'Continue at recovery desk' }).click();
      await expect(page.getByRole('button', { name: 'Resume and request hold' })).toBeVisible();
      await surfaces.getByRole('button', { name: 'System evidence', exact: true }).click();
      await surfaces.getByRole('button', { name: 'Solution briefing', exact: true }).click();
      await page.getByRole('button', { name: 'Open the capability ladder' }).click();
      await expect(page.getByRole('button', { name: 'Continue at recovery desk' })).toBeVisible();
      expect(new URL(page.url()).searchParams.get('thread')).toBe(thread);
    } else {
      await input.fill(`Run capability ${phase}`);
      await input.press('Enter');
      await expect(page.getByText(`Recorded response from capability ${phase}.`, { exact: true })).toBeVisible();
      await surfaces.getByRole('button', { name: 'System evidence', exact: true }).click();
      await expect(page.getByText(`Recorded activity for your ${name.toLowerCase()} response.`)).toBeVisible();
      const audit = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze();
      expect(audit.violations.map(v => v.id)).toEqual([]);
      await page.getByRole('button', { name: 'See the solution briefing' }).click();
      await page.getByRole('button', { name: 'Open the capability ladder' }).click();
      await expect(page.getByText(`Recorded response from capability ${phase}.`, { exact: true })).toBeVisible();
    }
    expect(requests).toHaveLength(before + 1);
  }
});
