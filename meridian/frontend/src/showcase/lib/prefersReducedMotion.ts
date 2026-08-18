/**
 * Module-scope read (not a live listener): the showcase is reloaded between
 * stage runs, so honoring the setting at load time is sufficient and keeps
 * every motion-aware component on the same answer.
 */
export const prefersReducedMotion =
  typeof window !== 'undefined' &&
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;
