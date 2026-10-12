import { expect, test } from '@playwright/test';

for (const viewport of [{ width: 1440, height: 900 }, { width: 1280, height: 720 }]) {
  test(`ladder loading and memory writeback fit at ${viewport.width}px`, async ({ page }) => {
    let finishRequest!: () => void;
    const pending = new Promise<void>(resolve => { finishRequest = resolve; });
    const facts = [
      { key: 'home_airport', value: 'JFK', confidence: 1 },
      { key: 'boutique_hotels', value: 'Independent boutique stays in quieter neighborhoods', confidence: 1 },
      { key: 'shellfish_allergy', value: 'Avoid shellfish', confidence: 1 },
    ];
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/api/chat') {
        await pending;
        return route.fulfill({ json: {
          message: 'Tokyo matches your boutique preference.', conversation_id: 'layout-fixture', memory_facts: facts,
          activities: [{ id: 'writeback-fixture', timestamp: '2026-10-01T12:00:00Z',
            activity_type: 'result', title: 'Strands @tool persist_turn', execution_time_ms: 20,
            telemetry: { category: 'memory_long', component: 'Aurora', status: 'ok' } }],
        } });
      }
      return route.fulfill({ json: path.endsWith('/health') ? { status: 'healthy', bedrock_model_id: 'fixture' }
        : path.includes('/memory/') ? { traveler_id: 'trv_meridian_demo', profile: { party_size: 2 }, facts }
        : path.includes('/journeys') ? { journeys: [] } : { products: [] } });
    });
    await page.setViewportSize(viewport);
    await page.goto('/showcase?view=ladder&theme=dark');
    await page.getByRole('button', { name: /^Phase 4,/ }).click();
    await page.getByRole('switch', { name: 'Use traveler context: off', exact: true }).click();
    await expect(page.getByRole('switch', { name: 'Use traveler context: on', exact: true })).toBeChecked();
    const input = page.getByRole('textbox', { name: 'Ask Meridian anything' });
    await input.fill('Show me Tokyo trips');
    await input.press('Enter');
    const waiting = page.getByRole('status').filter({ hasText: 'Connecting to your concierge' });
    try {
      await expect(waiting).toBeVisible();
      expect(await waiting.evaluate(element => parseFloat(getComputedStyle(element).fontSize))).toBeLessThanOrEqual(16);
      await expect(page.locator('.mds-message-bubble.is-thinking > svg')).toHaveCSS('width', '16px');
    } finally {
      finishRequest();
    }
    await expect(page.getByText('Tokyo matches your boutique preference.', { exact: true })).toBeVisible();
    const panel = page.locator('.mds-traveler-panel');
    await expect(panel.getByText('+3 written to Aurora', { exact: false })).toBeVisible();
    const bounds = await panel.evaluate(element => {
      const outer = element.getBoundingClientRect();
      const header = element.querySelector('.mds-panel-head')!;
      return {
        fits: element.scrollWidth <= element.clientWidth,
        buttonsFit: Array.from(header.querySelectorAll('button')).every(button => {
          const rect = button.getBoundingClientRect();
          return rect.left >= outer.left && rect.right <= outer.right && rect.top >= outer.top;
        }),
      };
    });
    expect(bounds).toEqual({ fits: true, buttonsFit: true });
    // A short window must allow every saved fact to be reached without
    // collapsing and reopening the panel to trigger a layout recalculation.
    const lastFact = panel.getByText('Avoid shellfish', { exact: true });
    await lastFact.scrollIntoViewIfNeeded();
    await expect(lastFact).toBeInViewport();
    const surfaces = page.getByRole('list', { name: 'Meridian surfaces' });
    await surfaces.getByRole('button', { name: 'Concierge', exact: true }).click();
    await surfaces.getByRole('button', { name: 'Capability ladder', exact: true }).click();
    await expect(page.getByRole('button', { name: /^Phase 4,/ })).toHaveAttribute('aria-current', 'step');
    await expect(panel.getByRole('button', { name: 'Memory', exact: true })).toBeInViewport();
    expect(await panel.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  });
}
