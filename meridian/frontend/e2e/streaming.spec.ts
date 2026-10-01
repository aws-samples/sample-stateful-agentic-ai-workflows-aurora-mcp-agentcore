import { createServer, type ServerResponse } from 'node:http';
import { expect, test, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const trip = { product_id: 'CTY-002', name: 'Tokyo Culture & Cuisine', brand: 'Meridian',
  price: 2499, category: 'City', destination: 'Tokyo', description: 'Neighborhood walks and boutique stays.',
  image_url: '/travel/catalog/CTY-002.jpg', available_sizes: ['5 nights'], availability: { '5 nights': 4 } };

// A real HTTP stream lets the browser consume separate chunks. route.fulfill
// would deliver one buffered response and miss the interaction being tested.
async function streamingFixture(page: Page) {
  let peer: ServerResponse | undefined;
  const server = createServer((request, response) => {
    response.setHeader('Access-Control-Allow-Origin', '*');
    response.setHeader('Access-Control-Allow-Headers', '*');
    if (request.method === 'OPTIONS') { response.writeHead(204).end(); return; }
    response.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' });
    response.flushHeaders();
    peer = response;
  });
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve));
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('Fixture server did not bind');
  await page.route(url => url.pathname.startsWith('/api/'), route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/chat/stream') return route.continue({ url: `http://127.0.0.1:${address.port}/api/chat/stream` });
    return route.fulfill({ json: path.endsWith('/health') ? { status: 'healthy', bedrock_model_id: 'fixture' }
      : path.includes('/memory/') ? { traveler_id: 'trv_meridian_demo', facts: [] } : { products: [trip] } });
  });
  return {
    async ready() { await expect.poll(() => Boolean(peer)).toBe(true); },
    emit(event: object) { peer!.write(`data: ${JSON.stringify(event)}\n\n`); },
    close() { peer?.end(); server.closeAllConnections(); server.close(); },
  };
}

test('live chunks, delayed photos, completion and reader-controlled scrolling stay coordinated', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'no-preference' });
  await page.setViewportSize({ width: 1440, height: 1000 });
  const stream = await streamingFixture(page);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  try {
    let releasePhoto: (() => void) | undefined;
    await page.route('**/travel/catalog/CTY-002.jpg', async route => {
      await new Promise<void>(resolve => { releasePhoto = resolve; });
      await route.continue();
    });
    await page.goto('/showcase?view=concierge&theme=dark');
    const input = page.getByRole('textbox', { name: 'Ask Meridian anything' });
    await input.fill('Plan Tokyo');
    await input.press('Enter');
    await stream.ready();
    const response = page.locator('.mc-message.is-bot');
    const slot = await response.elementHandle();
    await expect(response.locator('.mc-response-spinner')).toHaveCSS('animation-name', 'mds-send-spin');
    await expect(page.getByRole('button', { name: 'Stop waiting', exact: true })).toBeEnabled();
    await input.fill('Keep this draft for my next question');
    stream.emit({ type: 'candidates', package_ids: ['CTY-002'] });
    await expect(page.getByRole('article', { name: trip.name })).toHaveCount(0);
    const answer = Array.from({ length: 18 }, (_, i) => `Option ${i + 1}: Take time to explore Tokyo, compare the duration and plan your neighborhood walks.`).join('\n\n');
    stream.emit({ type: 'delta', text: answer });
    await expect.poll(async () => (await response.locator('.mds-message-text').textContent() ?? '').length).toBeGreaterThan(40);
    expect((await response.locator('.mds-message-text').textContent())!.length).toBeLessThan(answer.length);
    await expect(page.getByRole('article', { name: trip.name })).toBeVisible();
    await expect(page.getByRole('button', { name: `Explore this trip: ${trip.name}` })).toBeDisabled();
    const photo = page.locator('.mc-destination-photo');
    await expect(photo).toHaveCSS('opacity', '0');
    releasePhoto?.();
    await expect(photo).toHaveClass(/is-loaded/);
    await expect(photo).toHaveCSS('opacity', '1');
    const pane = page.getByRole('region', { name: 'Concierge responses' });
    await expect(response.locator('.mds-message-text')).toContainText('Option 18:');
    await expect.poll(() => pane.evaluate(el => el.scrollHeight - el.clientHeight - el.scrollTop)).toBeLessThan(2);
    await pane.evaluate(el => { el.scrollTop = 100; el.dispatchEvent(new Event('scroll')); });
    const readingPosition = await pane.evaluate(el => el.scrollTop);
    stream.emit({ type: 'delta', text: '\n\nYour itinerary can be adjusted to suit your pace.' });
    await expect(response.locator('.mds-message-text')).toContainText('Your itinerary can be adjusted');
    expect(await pane.evaluate(el => el.scrollTop)).toBe(readingPosition);
    await expect(page.getByRole('button', { name: 'Latest activity', exact: true })).toBeVisible();
    await page.getByRole('button', { name: 'Latest activity', exact: true }).click();
    const finalAnswer = answer + '\n\nYour itinerary can be adjusted to suit your pace.';
    stream.emit({ type: 'complete', response: { message: finalAnswer, products: [trip], activities: [] } });
    await expect(response.locator('.mc-response-spinner')).toHaveCount(0);
    await expect(response.locator('.mc-response-body')).toHaveAttribute('aria-busy', 'false');
    await expect(page.getByRole('button', { name: `Explore this trip: ${trip.name}` })).toBeEnabled();
    await expect(input).toHaveValue('Keep this draft for my next question');
    expect(await slot!.evaluate(el => el.isConnected)).toBe(true);
    const audit = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa']).analyze();
    expect(audit.violations.map(v => v.id)).toEqual([]);
    expect(errors).toEqual([]);
  } finally { stream.close(); }
});

test('mobile reduced motion and stop preserve the partial response without enabling provisional actions', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: 'reduce' });
  const stream = await streamingFixture(page);
  try {
    await page.goto('/showcase?view=concierge&theme=light');
    const input = page.getByRole('textbox', { name: 'Ask Meridian anything' });
    await input.fill('Plan Tokyo');
    await input.press('Enter');
    await stream.ready();
    stream.emit({ type: 'delta', text: 'Here are the Tokyo options received so far.' });
    stream.emit({ type: 'candidates', package_ids: ['CTY-002'] });
    await expect(page.locator('.mc-response-spinner')).toHaveCSS('animation-name', 'none');
    await expect(page.locator('.mc-message.is-bot .mds-message-text')).toHaveText('Here are the Tokyo options received so far.');
    await page.getByRole('button', { name: 'Stop waiting', exact: true }).click();
    await expect(page.getByText('· Incomplete response', { exact: true })).toBeVisible();
    await expect(page.locator('.mc-response-spinner')).toHaveCount(0);
    await expect(page.getByRole('article', { name: trip.name })).toHaveCount(0);
    await expect(page.locator('.mc-message.is-bot .mds-message-text')).toHaveText('Here are the Tokyo options received so far.');
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  } finally { stream.close(); }
});
