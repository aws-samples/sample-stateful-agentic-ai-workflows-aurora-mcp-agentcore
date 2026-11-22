import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

async function openPhase(page: Page, name: string) {
  await page.goto('/showcase?view=briefing&present=1&theme=dark');
  await page.getByRole('heading', { name: 'Build five capabilities', exact: true }).click();
  await page.getByRole('heading', { name, exact: true }).click();
  return page.getByRole('heading', { name, exact: true }).locator('xpath=ancestor::details[1]');
}

test('a path is fully operable from the keyboard and keeps focus at its end', async ({ page }) => {
  const phase = await openPhase(page, 'Phase 4 - Production');
  const next = phase.getByRole('button', { name: 'Next step' });
  await next.focus();
  await expect(phase.getByText('Step 1 of 8', { exact: true })).toBeVisible();
  await page.keyboard.press('Enter');
  await expect(phase.getByText('Step 2 of 8', { exact: true })).toBeVisible();
  for (let step = 2; step < 10; step += 1) await page.keyboard.press('Space');
  await expect(phase.getByText('Step 8 of 8', { exact: true })).toBeVisible();
  await expect(next).toHaveAttribute('aria-disabled', 'true');
  await expect(next).toBeFocused();
  await expect(phase.getByText('Aurora commits the hold with its action audit row.')).toBeVisible();
  await page.keyboard.press('Shift+Tab');
  await expect(phase.getByRole('button', { name: 'Back' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(phase.getByText('Step 7 of 8', { exact: true })).toBeVisible();
});

test('the scenarios switch from the keyboard and announce the outcome', async ({ page }) => {
  await page.goto('/showcase?view=briefing&present=1&theme=light');
  await page.getByRole('heading', { name: 'Verify the boundaries', exact: true }).click();
  const budget = page.getByRole('button', { name: 'Over the saved budget' });
  await budget.focus();
  await page.keyboard.press('Space');
  await expect(budget).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByText('Refused by Cedar')).toBeVisible();
  await page.keyboard.press('Tab');
  await page.keyboard.press('Enter');
  await expect(page.getByRole('button', { name: 'Second traveler' })).toHaveAttribute('aria-pressed', 'true');
  await expect(page.getByText('Refused by the grant check')).toBeVisible();
});

test('the chip does not move under reduced motion', async ({ page }) => {
  const phase = await openPhase(page, 'Phase 1 - SQL');
  await expect(phase.locator('.mds-step-chip')).toHaveCSS('transition-duration', '0s');
});

test.describe('with motion allowed', () => {
  test.use({ reducedMotion: 'no-preference' });
  test('the chip moves to the next hop', async ({ page }) => {
    const phase = await openPhase(page, 'Phase 1 - SQL');
    const chip = phase.locator('.mds-step-chip');
    await expect(chip).toHaveCSS('transition-duration', '0.45s');
    const before = await chip.boundingBox();
    await phase.getByRole('button', { name: 'Next step' }).click();
    await expect.poll(async () => (await chip.boundingBox())?.x ?? 0).toBeGreaterThan((before?.x ?? 0) + 100);
  });
});

// Axe skips closed details, so each path is opened and stepped into a mixed state (done, current,
// paused, stopped) before the scan, wide and at 320px where the paths stack.
const MIXED_STEPS: [string, number][] = [
  ['Phase 1 - SQL', 2], ['Phase 2 - MCP', 2], ['Phase 3 - Retrieval', 3],
  ['Phase 4 - Production', 4], ['Phase 5 - Workflow', 4],
];

async function audit(page: Page, label: string) {
  const result = await new AxeBuilder({ page }).include('.mds-brief')
    .withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa', 'best-practice']).analyze();
  expect(result.violations.map(v => ({ id: v.id, targets: v.nodes.map(n => n.target) })), label).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), `${label}: overflow`).toBe(true);
}

for (const theme of ['dark', 'light']) for (const width of [1920, 320]) {
  test(`${theme} at ${width}px: every opened path passes axe without overflow`, async ({ page }) => {
    await page.setViewportSize({ width, height: width === 1920 ? 1080 : 900 });
    await page.goto(`/showcase?view=briefing&present=1&theme=${theme}`);
    await page.getByRole('heading', { name: 'Build five capabilities', exact: true }).click();
    for (const [name, clicks] of MIXED_STEPS) {
      const heading = page.getByRole('heading', { name, exact: true });
      await heading.click();
      const phase = heading.locator('xpath=ancestor::details[1]');
      for (let click = 0; click < clicks; click += 1) await phase.getByRole('button', { name: 'Next step' }).click();
      await audit(page, name);
    }
    await page.getByRole('heading', { name: 'Verify the boundaries', exact: true }).click();
    for (const scenario of ['Over the saved budget', 'Second traveler']) {
      await page.getByRole('button', { name: scenario }).click();
      await audit(page, scenario);
    }
  });
}
