import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PresenterControls } from '../../components/PresenterControls';
import { usePresentationMode } from '../usePresentationMode';

function Harness() {
  const mode = usePresentationMode();
  return (
    <>
      <PresenterControls mode={mode} />
      <output aria-label="Audience layout">{String(mode.fullscreen || mode.preview)}</output>
    </>
  );
}

function fullscreen(element: Element | null) {
  Object.defineProperty(document, 'fullscreenElement', { configurable: true, value: element });
  document.dispatchEvent(new Event('fullscreenchange'));
}

describe('Presenter controls', () => {
  beforeEach(() => fullscreen(null));
  afterEach(() => {
    vi.restoreAllMocks();
    delete (document as unknown as Record<string, unknown>).fullscreenElement;
    delete (document.documentElement as unknown as Record<string, unknown>).requestFullscreen;
  });

  it('keeps preparation controls available during audience preview', () => {
    render(<Harness />);
    expect(screen.getByRole('checkbox', { name: 'Projector readability' })).toBeChecked();
    fireEvent.click(screen.getByRole('button', { name: 'Preview audience layout' }));
    expect(screen.getByLabelText('Audience layout')).toHaveTextContent('true');
    expect(screen.getByRole('region', { name: 'Presenter controls' })).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Preview audience layout' }));
    expect(screen.getByLabelText('Audience layout')).toHaveTextContent('false');
  });

  it('hides every preparation control in fullscreen and restores the previous choices on exit', async () => {
    Object.defineProperty(document.documentElement, 'requestFullscreen', {
      configurable: true,
      value: vi.fn(async () => fullscreen(document.documentElement)),
    });
    render(<Harness />);
    fireEvent.click(screen.getByRole('checkbox', { name: 'Projector readability' }));
    fireEvent.click(screen.getByText('Room check'));
    fireEvent.click(screen.getByRole('button', { name: 'Present fullscreen' }));
    await waitFor(() => expect(screen.queryByRole('region', { name: 'Presenter controls' })).toBeNull());
    expect(screen.getByLabelText('Audience layout')).toHaveTextContent('true');
    act(() => fullscreen(null));
    expect(screen.getByRole('region', { name: 'Presenter controls' })).toBeVisible();
    expect(screen.getByRole('checkbox', { name: 'Projector readability' })).not.toBeChecked();
    expect(screen.getByLabelText('Audience layout')).toHaveTextContent('false');
  });

  it('preserves an enabled windowed preview after fullscreen ends', () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: 'Preview audience layout' }));
    act(() => fullscreen(document.documentElement));
    act(() => fullscreen(null));
    expect(screen.getByLabelText('Audience layout')).toHaveTextContent('true');
    expect(screen.getByRole('button', { name: 'Preview audience layout' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('keeps controls visible and reports a rejected fullscreen request', async () => {
    Object.defineProperty(document.documentElement, 'requestFullscreen', {
      configurable: true,
      value: vi.fn().mockRejectedValue(new Error('Not allowed')),
    });
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: 'Present fullscreen' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Fullscreen could not start');
    expect(screen.getByRole('region', { name: 'Presenter controls' })).toBeVisible();
    expect(screen.getByLabelText('Audience layout')).toHaveTextContent('false');
  });

  it('explains when the Fullscreen API is unavailable', async () => {
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: 'Present fullscreen' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Fullscreen is unavailable');
  });

  it('follows browser display-mode changes without a fullscreen element', () => {
    const media = new EventTarget() as MediaQueryList;
    Object.defineProperty(media, 'matches', { configurable: true, value: false });
    vi.spyOn(window, 'matchMedia').mockReturnValue(media);
    render(<Harness />);
    act(() => {
      Object.defineProperty(media, 'matches', { configurable: true, value: true });
      media.dispatchEvent(new Event('change'));
    });
    expect(screen.queryByRole('region', { name: 'Presenter controls' })).toBeNull();
    act(() => {
      Object.defineProperty(media, 'matches', { configurable: true, value: false });
      media.dispatchEvent(new Event('change'));
    });
    expect(screen.getByRole('region', { name: 'Presenter controls' })).toBeVisible();
  });
});
