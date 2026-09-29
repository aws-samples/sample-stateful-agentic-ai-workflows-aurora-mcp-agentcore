/// <reference types="vitest" />
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

/**
 * Vitest config.
 *
 * Lives next to vite.config.ts so the dev server keeps its own minimal
 * config and tests get their own jsdom + testing-library setup.
 */
export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    // Use jsdom's browser storage, not Node 25+'s file-backed native storage.
    execArgv: process.allowedNodeEnvironmentFlags.has('--no-experimental-webstorage')
      ? ['--no-experimental-webstorage'] : [],
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.{test,spec}.{ts,tsx}', 'scripts/**/*.test.mjs'],
    css: false,
  },
})
