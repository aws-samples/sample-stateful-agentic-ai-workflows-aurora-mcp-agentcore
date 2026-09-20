import { act, renderHook } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { usePrefersReducedMotion } from './prefersReducedMotion';

afterEach(() => vi.restoreAllMocks());

it('updates mounted consumers when the system preference changes and unsubscribes', () => {
  let reduced = false;
  const listeners = new Set<() => void>();
  vi.spyOn(window, 'matchMedia').mockImplementation((query) => ({
    get matches() { return reduced; }, media: query, onchange: null,
    addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: () => true,
    addEventListener: (_event: string, listener: EventListenerOrEventListenerObject) => listeners.add(listener as () => void),
    removeEventListener: (_event: string, listener: EventListenerOrEventListenerObject) => listeners.delete(listener as () => void),
  } as MediaQueryList));
  const first = renderHook(usePrefersReducedMotion);
  const second = renderHook(usePrefersReducedMotion);
  expect(first.result.current).toBe(false);
  act(() => { reduced = true; listeners.forEach(listener => listener()); });
  expect(first.result.current).toBe(true);
  expect(second.result.current).toBe(true);
  act(() => { reduced = false; listeners.forEach(listener => listener()); });
  expect(first.result.current).toBe(false);
  first.unmount(); second.unmount();
  expect(listeners.size).toBe(0);
});
