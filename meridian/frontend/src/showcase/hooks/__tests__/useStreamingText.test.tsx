import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useStreamingText } from '../useStreamingText';

const answer = 'Your Tokyo trip includes neighborhood walks, boutique stays and a day in Hakone. '.repeat(30);
const advance = (ms: number) => act(() => { vi.advanceTimersByTime(ms); });

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['requestAnimationFrame', 'cancelAnimationFrame', 'performance'] });
});
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe('live response presentation', () => {
  it('paces a burst, keeps the revealed prefix, and finishes without replaying the answer', () => {
    const { result, rerender } = renderHook(({ text, streaming }) => useStreamingText(text, streaming), {
      initialProps: { text: 'Your Tokyo trip ', streaming: true },
    });
    advance(100);
    const first = result.current;
    rerender({ text: answer, streaming: true });
    advance(160);
    expect(result.current.startsWith(first)).toBe(true);
    expect(result.current.length).toBeGreaterThan(first.length);
    expect(result.current.length).toBeLessThan(answer.length);
    const inFlight = result.current;
    rerender({ text: answer, streaming: false });
    expect(result.current).toBe(inFlight);
    advance(2000);
    expect(result.current).toBe(answer);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('resumes after a gap between network events without deleting visible text', () => {
    const { result, rerender } = renderHook(({ text }) => useStreamingText(text, true), { initialProps: { text: 'Tokyo for two. ' } });
    advance(500);
    expect(result.current).toBe('Tokyo for two. ');
    expect(vi.getTimerCount()).toBe(0);
    rerender({ text: `Tokyo for two. ${answer}` });
    expect(result.current).toBe('Tokyo for two. ');
    advance(2500);
    expect(result.current).toBe(`Tokyo for two. ${answer}`);
  });

  it('shows historical replies and complete-only responses immediately', () => {
    const { result, rerender } = renderHook(({ text, streaming }) => useStreamingText(text, streaming), {
      initialProps: { text: '', streaming: true },
    });
    rerender({ text: answer, streaming: false });
    expect(result.current).toBe(answer);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('uses an authoritative correction immediately and cancels stale text', () => {
    const { result, rerender } = renderHook(({ text, streaming }) => useStreamingText(text, streaming), {
      initialProps: { text: answer, streaming: true },
    });
    advance(64);
    rerender({ text: 'No matching trips are available.', streaming: false });
    expect(result.current).toBe('No matching trips are available.');
    advance(2500);
    expect(result.current).toBe('No matching trips are available.');
    expect(vi.getTimerCount()).toBe(0);
  });

  it('preserves all received text when interrupted and cancels work on unmount', () => {
    const { result, rerender, unmount } = renderHook(({ interrupted }) => useStreamingText(answer, !interrupted, interrupted), {
      initialProps: { interrupted: false },
    });
    advance(64);
    rerender({ interrupted: true });
    expect(result.current).toBe(answer);
    expect(vi.getTimerCount()).toBe(0);
    unmount();
    const next = renderHook(() => useStreamingText(answer, true));
    expect(vi.getTimerCount()).toBeGreaterThan(0);
    next.unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it('honors a reduced-motion preference change during the response', () => {
    let reduce = false;
    let notify = () => {};
    vi.spyOn(window, 'matchMedia').mockImplementation(query => ({
      matches: reduce, media: query, onchange: null,
      addEventListener: (_event: string, listener: EventListenerOrEventListenerObject) => { notify = listener as () => void; },
      removeEventListener: vi.fn(), addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: () => true,
    }));
    const { result, rerender } = renderHook(({ text }) => useStreamingText(text, true), { initialProps: { text: answer } });
    expect(result.current.length).toBeLessThan(answer.length);
    act(() => { reduce = true; notify(); });
    expect(result.current).toBe(answer);
    expect(vi.getTimerCount()).toBe(0);
    rerender({ text: `${answer}Ready.` });
    expect(result.current).toBe(`${answer}Ready.`);
  });

  it('never exposes a split Unicode character', () => {
    const text = '東京'.repeat(12) + '𠮷'.repeat(45);
    const { result } = renderHook(() => useStreamingText(text, true));
    for (let tick = 0; tick < 30; tick++) {
      advance(48);
      expect(result.current).not.toMatch(/[\uD800-\uDBFF]$/);
    }
    expect(result.current).toBe(text);
  });
});
