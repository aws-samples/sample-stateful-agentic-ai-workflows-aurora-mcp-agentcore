import { defineConfig } from '@playwright/test';

// Offline states are deterministic in CI. Explicit live runs exercise real APIs.
const live = process.env.MERIDIAN_A11Y_LIVE === '1';
const baseURL = process.env.MERIDIAN_A11Y_URL || 'http://127.0.0.1:4174';
const gatedURL = 'http://127.0.0.1:4175';
const hosted = Boolean(process.env.MERIDIAN_A11Y_URL);
const credentials = process.env.MERIDIAN_HOSTED_AUTH ? JSON.parse(process.env.MERIDIAN_HOSTED_AUTH) : undefined;
export default defineConfig({
  testDir: './e2e', timeout: 60_000, workers: 1, retries: 0,
  reporter: credentials ? [['list']] : [['list'], ['json', { outputFile: 'test-results/accessibility.json' }]],
  use: { baseURL, browserName: 'chromium', reducedMotion: 'reduce',
    httpCredentials: credentials ? { ...credentials, origin: new URL(baseURL).origin } : undefined,
    // Do not record authentication headers in traces or screenshots automatically.
    trace: 'off', screenshot: 'off' },
  // The suite runs ungated. Mode e2e never loads .env.development.local, so a machine that ran
  // sync_cognito_env.py still gets the showcase. The gated project proves the sign-in screen.
  projects: [
    { name: 'ungated', testIgnore: /gated\//u },
    ...(hosted ? [] : [{ name: 'gated', testMatch: /gated\/.*\.spec\.ts$/u, use: { baseURL: gatedURL } }]),
  ],
  webServer: hosted ? undefined : [
    {
      command: 'npm run dev -- --mode e2e --host 127.0.0.1 --port 4174 --strictPort', url: baseURL,
      reuseExistingServer: !process.env.CI, timeout: 30_000,
    },
    {
      // Placeholder sign-in settings, never the real ones. The process environment outranks any .env file.
      command: 'npm run dev -- --mode gated-e2e --host 127.0.0.1 --port 4175 --strictPort', url: gatedURL,
      env: { VITE_COGNITO_DOMAIN: 'gate.auth.example.test', VITE_COGNITO_CLIENT_ID: 'gate-client' },
      reuseExistingServer: !process.env.CI, timeout: 30_000,
    },
  ],
  metadata: { mode: live ? 'live APIs' : 'unavailable service fixtures' },
});
