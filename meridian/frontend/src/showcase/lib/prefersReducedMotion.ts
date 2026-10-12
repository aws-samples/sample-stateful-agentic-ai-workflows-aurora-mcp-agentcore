import { useSyncExternalStore } from 'react';

const query = '(prefers-reduced-motion: reduce)';
const media = () => typeof window !== 'undefined' && typeof window.matchMedia === 'function'
  ? window.matchMedia(query) : null;

function subscribe(onChange: () => void) {
  const preference = media();
  preference?.addEventListener('change', onChange);
  return () => preference?.removeEventListener('change', onChange);
}

/** Honor preference changes during a session, including active timers. */
export function usePrefersReducedMotion(): boolean {
  return useSyncExternalStore(subscribe, () => media()?.matches ?? false, () => true);
}
