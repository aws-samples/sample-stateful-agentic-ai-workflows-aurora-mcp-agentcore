import { renderHook } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { useLiveCues } from '../useLiveCues';

afterEach(() => vi.restoreAllMocks());

const cues = (identity: string | null, running: boolean) => ({ identity, running });

it('stays still until it watches a run for the same recovery', () => {
  const { result, rerender } = renderHook(
    ({ identity, running }) => useLiveCues(identity, running),
    { initialProps: cues('thread-a', false) },
  );
  expect(result.current).toBe(false);
  rerender(cues('thread-a', true));
  expect(result.current).toBe(true);
  rerender(cues('thread-a', false));
  expect(result.current).toBe(true);
  rerender(cues('thread-b', false));
  expect(result.current).toBe(false);
  rerender(cues(null, true));
  expect(result.current).toBe(false);
});

it('never animates when the viewer prefers reduced motion', () => {
  vi.spyOn(window, 'matchMedia').mockImplementation(query => ({
    matches: true, media: query, onchange: null, addListener: vi.fn(), removeListener: vi.fn(),
    addEventListener: vi.fn(), removeEventListener: vi.fn(), dispatchEvent: () => true,
  } as MediaQueryList));
  const { result } = renderHook(() => useLiveCues('thread-a', true));
  expect(result.current).toBe(false);
});
