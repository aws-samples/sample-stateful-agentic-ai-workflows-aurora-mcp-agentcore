import path from 'node:path';
import { defineConfig } from '@playwright/test';

const baseURL = process.env.MERIDIAN_A11Y_URL || 'http://127.0.0.1:4174';
const dir = path.resolve(process.env.VISUAL_DIR ?? '../.local/visual/latest');

export default defineConfig({
  testDir: './e2e/visual',
  testMatch: '**/*.visual.ts',
  snapshotPathTemplate: path.join(dir, '{arg}{ext}'),
  timeout: 120_000,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL, browserName: 'chromium', reducedMotion: 'reduce', trace: 'off', screenshot: 'off',
  },
  expect: { toHaveScreenshot: { maxDiffPixels: 0, animations: 'disabled', caret: 'hide' } },
  webServer: process.env.MERIDIAN_A11Y_URL ? undefined : {
    command: 'npm run dev -- --host 127.0.0.1 --port 4174 --strictPort', url: baseURL,
    reuseExistingServer: true, timeout: 30_000,
  },
});
