import { defineConfig } from '@playwright/test';

// Offline states are deterministic in CI. Explicit live runs exercise real APIs.
const live = process.env.MERIDIAN_A11Y_LIVE === '1';
const baseURL = process.env.MERIDIAN_A11Y_URL || 'http://127.0.0.1:4174';
const credentials = process.env.MERIDIAN_HOSTED_AUTH ? JSON.parse(process.env.MERIDIAN_HOSTED_AUTH) : undefined;
export default defineConfig({
  testDir: './e2e', timeout: 60_000, workers: 1, retries: 0,
  reporter: credentials ? [['list']] : [['list'], ['json', { outputFile: 'test-results/accessibility.json' }]],
  use: { baseURL, browserName: 'chromium', reducedMotion: 'reduce',
    httpCredentials: credentials ? { ...credentials, origin: new URL(baseURL).origin } : undefined,
    // Do not record authentication headers in traces or screenshots automatically.
    trace: 'off', screenshot: 'off' },
  webServer: process.env.MERIDIAN_A11Y_URL ? undefined : {
    command: 'npm run dev -- --host 127.0.0.1 --port 4174 --strictPort', url: baseURL,
    reuseExistingServer: !process.env.CI, timeout: 30_000,
  },
  metadata: { mode: live ? 'live APIs' : 'unavailable service fixtures' },
});
