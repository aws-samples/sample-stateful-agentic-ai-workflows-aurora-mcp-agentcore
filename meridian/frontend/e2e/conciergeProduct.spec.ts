import { expect, test } from '@playwright/test';
import { pausedRecovery } from './fixtures/pausedRecovery';

test('Concierge leads with remembered context and offers a reviewable recovery without teaching controls', async ({ page }) => {
  const requests: Record<string, unknown>[] = [];
  const disruption = 'My flight was canceled. Rework my Tokyo trip, then check availability.';
  await page.route(url => url.pathname.startsWith('/api/'), route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/chat' || path === '/api/chat/stream') {
      const request = route.request().postDataJSON();
      requests.push(request);
      const response = request.phase === 5 ? pausedRecovery([], request.conversation_id)
        : { message: request.message === disruption ? 'Review a recovery plan before requesting a hold.' : 'Your saved boutique preference shapes these Tokyo options.',
          conversation_id: 'traveler-conversation', activities: [],
          recovery_request: request.message === disruption ? disruption : null };
      return route.fulfill(path.endsWith('/stream')
        ? { contentType: 'text/event-stream', body: `data: ${JSON.stringify({ type: 'complete', response })}\n\n` }
        : { json: response });
    }
    return route.fulfill({ json: path.endsWith('/health') ? { status: 'healthy' }
      : path.includes('/memory/') ? { traveler_id: 'trv_meridian_demo', profile: { home_airport: 'JFK', party_size: 2 },
        facts: [{ key: 'hotel_style', value: 'Quiet boutique hotels', confidence: 1 }, { key: 'dietary', value: 'No shellfish', confidence: 1 },
          { key: 'no_red_eye', value: 'true', confidence: 1 }],
        budget_ceiling_per_traveler_cents: 320000 }
      : path.includes('/journeys') ? { journeys: [] } : { products: [] } });
  });
  await page.goto('/showcase?view=ladder');
  await expect(page.getByRole('button', { name: /^Phase 1,/ })).toHaveAttribute('aria-current', 'step');
  await page.getByRole('button', { name: 'Open Jordan Morgan travel brief', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Your concierge', exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: /^Phase 1,/ })).toHaveCount(0);
  const brief = page.getByRole('region', { name: 'Travel brief details', exact: true });
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(page.locator('.mc-studio-brief > summary')).toBeFocused();
  await expect(brief.getByText('Quiet boutique hotels', { exact: true })).toBeVisible();
  await expect(brief.getByText('No shellfish', { exact: true })).toBeVisible();
  expect(requests).toHaveLength(0);
  await page.getByText('Your travel context', { exact: true }).click();
  await page.getByRole('button', { name: 'Open Jordan Morgan travel brief', exact: true }).click();
  await expect(brief).toBeVisible();
  await expect(brief.getByRole('button', { name: 'Edit preferences', exact: true })).toBeVisible();
  await brief.getByRole('button', { name: 'Edit preferences', exact: true }).click();
  const preferences = page.getByRole('dialog', { name: 'Your preferences', exact: true });
  await expect(preferences.getByText('Quiet boutique hotels', { exact: true })).toBeVisible();
  await expect(preferences.getByText('Avoid overnight flights: Yes', { exact: true })).toBeVisible();
  await expect(preferences.getByText(/Production|Aurora|confidence/)).toHaveCount(0);
  const closeAlignment = await preferences.getByRole('button', { name: 'Close memory drawer' }).evaluate(button => {
    const bounds = button.getBoundingClientRect();
    const icon = button.querySelector('svg')!.getBoundingClientRect();
    return { x: Math.abs((bounds.left + bounds.right - icon.left - icon.right) / 2),
      y: Math.abs((bounds.top + bounds.bottom - icon.top - icon.bottom) / 2) };
  });
  expect(closeAlignment.x).toBeLessThan(1);
  expect(closeAlignment.y).toBeLessThan(1);
  await preferences.getByRole('button', { name: 'Close memory drawer' }).click();
  await page.getByText('Your travel context', { exact: true }).click();
  const input = page.getByRole('textbox', { name: 'Ask Meridian anything' });
  await input.fill('Plan Tokyo'); await input.press('Enter');
  await expect(page.getByText('Your saved boutique preference shapes these Tokyo options.', { exact: true })).toBeVisible();
  await input.fill(disruption); await input.press('Enter');
  await expect(page.getByRole('button', { name: 'Review recovery plan', exact: true })).toBeEnabled();
  await expect(page.getByText('Review your recovery options', { exact: true })).toBeVisible();
  await expect(page.getByText('No matching trips', { exact: true })).toHaveCount(0);
  expect(requests).toHaveLength(2);
  expect(requests[0]).toMatchObject({ phase: 4, experience: 'concierge', memory_enabled: true, travelers_count: 2 });
  expect(requests[1]).toMatchObject({ phase: 4, conversation_id: 'traveler-conversation', memory_enabled: true });
  await expect(page.getByText(/Switch to Workflow/)).toHaveCount(0);
  await page.getByRole('button', { name: 'Review recovery plan', exact: true }).click();
  await expect(page.getByRole('button', { name: 'Resume and request hold', exact: true })).toBeVisible();
  expect(requests).toHaveLength(3);
  expect(requests[2]).toMatchObject({ phase: 5, message: disruption, review_only: true, travelers_count: 2 });
  expect(requests[2].resume).toBeUndefined();
  expect(requests[2].conversation_id).not.toBe('traveler-conversation');
  const thread = new URL(page.url()).searchParams.get('thread');
  await page.getByRole('list', { name: 'Meridian surfaces' }).getByRole('button', { name: 'Concierge', exact: true }).click();
  expect(new URL(page.url()).searchParams.get('thread')).toBe(thread);
  expect(requests).toHaveLength(3);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole('button', { name: 'Open Jordan Morgan travel brief', exact: true }).click();
  await expect(brief.getByRole('heading', { name: 'Your travel brief', exact: true })).toBeInViewport();
  await expect(brief.getByText('Quiet boutique hotels', { exact: true })).toBeVisible();
  await expect(page.getByRole('dialog')).toHaveCount(0);
  expect(requests).toHaveLength(3);
});
