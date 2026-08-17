/**
 * Test setup - runs once before every Vitest worker.
 *
 * Extends `expect` with `@testing-library/jest-dom` matchers and silences a
 * couple of React-in-test warnings that show up in jsdom but aren't actually
 * useful signal.
 */
import '@testing-library/jest-dom/vitest'

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
