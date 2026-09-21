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
      let release: () => void = () => {};
      const gate = new Promise<void>(resolve => { release = resolve; });
      await page.unrouteAll();
      await page.route(url => url.pathname.startsWith('/api/'), async route => {
        const path = new URL(route.request().url()).pathname;
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
      expect(await steps.locator('img').evaluateAll(imgs => imgs.every(img => (img as HTMLImageElement).complete && (img as HTMLImageElement).naturalWidth > 0))).toBe(true);
      await page.getByRole('button', { name: 'Replay trace', exact: true }).click();
      await expect(steps.locator('[aria-current="step"]')).toBeVisible();
      await expect(page.getByText('Replaying recorded activity')).toBeVisible();
      await expect(page.locator('.mds-thinking-caption')).toHaveText('Recorded activity', { timeout: 10000 });
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
