/**
 * Test setup - runs once before every Vitest worker.
 *
 * Extends `expect` with `@testing-library/jest-dom` matchers and silences a
 * couple of React-in-test warnings that show up in jsdom but aren't actually
 * useful signal.
 */
import '@testing-library/jest-dom/vitest'

// Some Node versions expose an unavailable native localStorage property that
// prevents jsdom's implementation from being installed on the test global.
if (typeof window !== 'undefined' && !window.localStorage) {
  const values = new Map<string, string>();
  Object.defineProperty(window, 'localStorage', { configurable: true, value: {
    get length() { return values.size; },
    getItem: (key: string) => values.get(String(key)) ?? null,
    setItem: (key: string, value: string) => { values.set(String(key), String(value)); },
    removeItem: (key: string) => { values.delete(String(key)); },
    clear: () => values.clear(),
    key: (index: number) => [...values.keys()][index] ?? null,
  } });
}

if (typeof window !== 'undefined' && !window.matchMedia) {
  // jsdom doesn't ship matchMedia; useStagePlayer reads it for
  // prefers-reduced-motion. Provide a no-match stub.
  window.matchMedia = (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })
}

if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
}
