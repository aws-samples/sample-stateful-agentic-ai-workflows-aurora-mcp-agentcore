import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

// Deterministic UI fixtures: no model invocation or inventory write is needed.
const trip = {
  product_id: 'CTY-002', name: 'Tokyo Culture & Cuisine', brand: 'ANA Holidays',
  price: 2499, category: 'City & Culture', destination: 'Tokyo', region: 'Asia',
  description: 'Shibuya base, neighborhood walks, and a day trip to Hakone.',
  image_url: '/travel/catalog/CTY-002.jpg', available_sizes: ['5 nights'],
  availability: { '5 nights': 4 }, highlights: ['neighborhood walks'],
};
const reply = 'Here is your Tokyo travel brief.\n\n- **Home airport:** JFK\n- **Food needs:** shellfish allergy\n- **Budget:** $3,200 per traveler\n\nExplore the trip below, then choose a duration to see its details.';
const trips = [trip,
  { ...trip, product_id: 'TKY-001', name: 'Tokyo Indie Neighborhood Walk', price: 1599 },
  { ...trip, product_id: 'TKY-005', name: 'Tokyo Ryokan & Onsen Slow Week', price: 3899 },
];

for (const view of ['concierge', 'ladder']) for (const theme of ['light', 'dark']) {
  test(`${view} ${theme}: readable replies, multiline input and usable send at every size`, async ({ page }) => {
    const requests: string[] = [];
    await page.route(url => url.pathname.startsWith('/api/'), async route => {
      const path = new URL(route.request().url()).pathname;
      if (path === '/api/chat') requests.push(route.request().postDataJSON().message as string);
      const body = path === '/api/chat'
        ? { message: reply, products: trips, activities: [], follow_ups: [], conversation_id: 'chat-layout-test' }
        : path === '/api/health'
          ? { status: 'healthy', bedrock_model_id: 'fixture', embedding_model_id: 'fixture', checkpoint_backend: 'fixture' }
          : path.includes('/memory/')
            ? { traveler_id: 'trv_meridian_demo', facts: [] }
            : { products: trips };
      await route.fulfill({ json: body });
    });
    for (const width of [1920, 1440, 900, 320]) {
      await page.setViewportSize({ width, height: 1000 });
      await page.goto(`/showcase?view=${view}&theme=${theme}`);
      const input = page.getByRole('textbox', { name: 'Ask Meridian anything' });
      await expect(input).toBeEnabled();
      await input.fill(Array.from({ length: 20 }, () => 'A longer travel request').join('\n'));
      expect((await input.boundingBox())!.height).toBeLessThan(240);
      expect(await input.evaluate(el => el.scrollHeight > el.clientHeight)).toBe(true);
      await input.fill('Plan a Tokyo trip');
      const oneLineHeight = (await input.boundingBox())!.height;
      await input.press('End');
      await input.press('Shift+Enter');
      await input.pressSequentially('Departing JFK');
      expect((await input.boundingBox())!.height).toBeGreaterThan(oneLineHeight);
      await expect(input).toHaveValue('Plan a Tokyo trip\nDeparting JFK');
      const priorRequests = requests.length;
      await page.getByRole('button', { name: 'Send message', exact: true }).click();
      await expect.poll(() => requests.length).toBe(priorRequests + 1);
      expect(requests[requests.length - 1]).toContain('Plan a Tokyo trip\nDeparting JFK');
      await expect(page.getByText('Here is your Tokyo travel brief.', { exact: true })).toBeVisible();
      await expect(input).toBeEnabled();
      await expect(page.locator('.mds-message-bubble.is-thinking')).toHaveCount(0);
      await expect(input).toHaveValue('');
      const assistant = page.locator(view === 'ladder' ? '.mds-message.bot' : '.mc-message.is-bot');
      const user = page.locator(view === 'ladder' ? '.mds-message.user' : '.mc-message.is-user');
      const userText = user.locator(view === 'ladder' ? '.mds-message-text' : 'p');
      await expect(userText).toHaveCSS('white-space', 'pre-wrap');
      expect(await userText.textContent()).toContain('Plan a Tokyo trip\nDeparting JFK');
      const answer = (await assistant.boundingBox())!;
      const prompt = (await user.boundingBox())!;
      const composer = (await page.locator('.mds-chat-composer').boundingBox())!;
      expect(Math.abs(answer.x - composer.x)).toBeLessThan(2);
      expect(Math.abs(answer.width - composer.width)).toBeLessThan(2);
      expect(prompt.width).toBeLessThan(answer.width);
      expect(Math.abs(prompt.x + prompt.width - answer.x - answer.width)).toBeLessThan(2);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      if (view === 'concierge') {
        for (const card of await page.locator('.mc-supporting-trips .mc-trip').all()) {
          const bounds = (await card.boundingBox())!;
          const action = (await card.getByRole('button', { name: /^Details:/ }).boundingBox())!;
          expect(action.x + action.width).toBeLessThanOrEqual(bounds.x + bounds.width);
        }
      }
      const audit = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa', 'best-practice']).analyze();
      expect(audit.violations.map(v => ({ id: v.id, targets: v.nodes.map(n => n.target) }))).toEqual([]);
      // A second turn can be sent with Enter without losing its line breaks.
      await input.fill('Keep the boutique option\nTwo travelers');
      await input.press('Enter');
      await expect.poll(() => requests.length).toBe(priorRequests + 2);
      expect(requests[requests.length - 1]).toContain('Keep the boutique option\nTwo travelers');
    }
  });
}
