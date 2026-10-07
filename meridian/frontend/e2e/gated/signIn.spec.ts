import { expect, test } from '@playwright/test';

test('a build with both sign-in settings shows the sign-in screen instead of the showcase', async ({ page }) => {
  await page.goto('/showcase');
  await expect(page.getByRole('heading', { name: 'Sign in to Meridian' })).toBeVisible();
  await expect(page.getByRole('button', { name: 'Sign in' })).toBeEnabled();
  await expect(page.getByRole('navigation', { name: 'Meridian capability ladder', exact: true })).toHaveCount(0);
});
