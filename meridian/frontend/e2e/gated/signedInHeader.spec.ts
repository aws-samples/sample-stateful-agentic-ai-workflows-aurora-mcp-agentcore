import { expect, test, type Page } from '@playwright/test';

// Unsigned tokens in the shape the sign-in service returns. The page reads their claims only for
// display; nothing here reaches a real API, which is routed to fixtures below.
const encode = (value: object) => Buffer.from(JSON.stringify(value)).toString('base64url');
const token = (claims: object) =>
  `${encode({ alg: 'none', typ: 'JWT' })}.${encode(claims)}.fixture`;
const TOKENS = {
  access_token: token({ traveler_id: 'trv_fixture', token_use: 'access' }),
  id_token: token({ name: 'Alex Rivera', token_use: 'id' }),
  token_type: 'Bearer', expires_in: 3600,
};
const MEMORY = {
  traveler_id: 'trv_fixture', profile: { home_airport: 'JFK' },
  facts: [{ key: 'hotel_style', value: 'Quiet boutique hotels', confidence: 1 }],
  budget_ceiling_per_traveler_cents: null,
};

function fixtureFor(path: string): object {
  if (path === '/api/me') return { traveler_id: 'trv_fixture' };
  if (path.endsWith('/health')) return { status: 'healthy' };
  if (path.includes('/memory/')) return MEMORY;
  return path.includes('/journeys') ? { journeys: [] } : { products: [] };
}

async function signIn(page: Page) {
  await page.route('https://gate.auth.example.test/oauth2/authorize**', route => {
    const authorize = new URL(route.request().url());
    const back = new URL(authorize.searchParams.get('redirect_uri')!);
    back.searchParams.set('code', 'fixture-code');
    back.searchParams.set('state', authorize.searchParams.get('state')!);
    return route.fulfill({ status: 302, headers: { location: back.toString() } });
  });
  await page.route('https://gate.auth.example.test/oauth2/token', route => route.fulfill({
    json: TOKENS, headers: { 'access-control-allow-origin': '*' },
  }));
  await page.route(url => url.pathname.startsWith('/api/'), route => route.fulfill({
    json: fixtureFor(new URL(route.request().url()).pathname),
  }));
  await page.setViewportSize({ width: 1600, height: 900 });
  await page.goto('/showcase');
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('group', { name: 'Account' })).toBeVisible();
}

const headerAvatars = (page: Page) =>
  page.locator('.mds-shell-header .mds-traveler-initials:visible');
const BRIEF_BUTTON = { name: 'Open Alex Rivera travel brief', exact: true };

test('the signed-in Concierge header shows the traveler once; the name opens the brief', async ({
  page,
}) => {
  await signIn(page);
  await expect(page.getByRole('heading', { name: 'Your concierge', exact: true })).toBeVisible();
  await expect(headerAvatars(page)).toHaveCount(1);
  const account = page.getByRole('group', { name: 'Account' });
  await expect(account.getByText('Alex Rivera', { exact: true })).toBeVisible();
  await account.getByRole('button', BRIEF_BUTTON).click();
  const brief = page.getByRole('region', { name: 'Travel brief details', exact: true });
  await expect(brief.getByText('Quiet boutique hotels', { exact: true })).toBeVisible();
});

test('in fullscreen the pill steps aside and the profile button carries the traveler', async ({
  page,
}) => {
  await signIn(page);
  await page.getByRole('button', { name: 'Present fullscreen' }).click();
  await expect(page.locator('.mds-root')).toHaveAttribute('data-fullscreen', 'true');
  await expect(page.getByRole('group', { name: 'Account' })).toBeHidden();
  await expect(headerAvatars(page)).toHaveCount(1);
  await expect(page.getByRole('button', BRIEF_BUTTON)).toBeVisible();
});

test('every other surface shows the traveler once, in the account pill', async ({ page }) => {
  await signIn(page);
  const surfaces = page.getByRole('navigation', {
    name: 'Meridian capability ladder', exact: true,
  });
  const account = page.getByRole('group', { name: 'Account' });
  const others = ['Capability ladder', 'Recovery desk', 'System evidence', 'Solution briefing'];
  for (const surface of others) {
    await surfaces.getByRole('button', { name: new RegExp(`^${surface}`) }).click();
    await expect(headerAvatars(page)).toHaveCount(1);
    await expect(account.getByText('Alex Rivera', { exact: true })).toBeVisible();
  }
});
