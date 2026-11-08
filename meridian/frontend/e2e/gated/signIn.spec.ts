import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('a build with both sign-in settings shows the sign-in screen instead of the showcase', async ({ page }) => {
  await page.goto('/showcase');
  await expect(page.getByRole('heading', { name: 'Sign in to Meridian' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeEnabled();
  await expect(page.getByLabel('Email', { exact: true })).toBeVisible();
  await expect(page.getByLabel('Password', { exact: true })).toHaveAttribute('type', 'password');
  await expect(page.getByRole('navigation', { name: 'Meridian capability ladder', exact: true })).toHaveCount(0);
});

for (const width of [390, 1366]) test(`inline form at ${width}px stays accessible after refusal`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 });
  await page.route('https://cognito-idp.us-east-1.amazonaws.com/', route => route.fulfill({
    status: 400, json: { __type: 'NotAuthorizedException', message: 'service detail must stay private' },
  }));
  await page.goto('/showcase');
  await page.getByLabel('Email', { exact: true }).fill('alex@example.test');
  await page.getByLabel('Password', { exact: true }).fill('fixture-only-password');
  await page.getByRole('button', { name: 'Sign in', exact: true }).click();
  await expect(page.getByRole('alert')).toHaveText('Check your email and password and try again.');
  await expect(page.getByLabel('Password', { exact: true })).toHaveValue('');
  await expect(page).toHaveURL(/\/showcase$/u);
  await expect(page.getByRole('heading', { name: 'Sign in to Meridian' })).toBeFocused();
  expect((await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze()).violations).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
