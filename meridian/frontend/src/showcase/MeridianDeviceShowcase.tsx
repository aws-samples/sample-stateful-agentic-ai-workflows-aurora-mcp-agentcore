import { Component, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { DesktopMeridianApp } from './DesktopMeridianApp';
import { useMeridianShowcase } from './hooks/useMeridianShowcase';
import { usePresentationMode } from './hooks/usePresentationMode';
import { PresenterControls } from './components/PresenterControls';
import './meridianShowcase.css';
import './recoveryWorkspace.css';
import './discoveryWorkspace.css';
import './recoveryDecisionRefresh.css';
import './presenterProof.css';
import './surfaceSwitch.css';
import './airlineConcierge.css';
import './presentationMode.css';

type ShowcaseTheme = 'dark' | 'light';

// Start from the OS theme; presenters can still toggle per session.
function initialTheme(): ShowcaseTheme {
  if (typeof window === 'undefined' || !window.matchMedia) return 'dark';
  return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
}

// Keep a render error from blanking the live showcase.
class ShowcaseErrorBoundary extends Component<
  { children: ReactNode },
  { error: Error | null }
> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: { componentStack?: string }) {
    console.error('[showcase] render crashed', error, info?.componentStack);
  }

  reset = () => this.setState({ error: null });

  render() {
    if (!this.state.error) return this.props.children;
    const message = this.state.error.message || String(this.state.error);
    return (
      <div className="mds-error-screen" role="alert">
        <div className="mds-error-card">
          <h2>Something rendered out of bounds.</h2>
          <p>
            The showcase caught a render-time error. The full stack is in the
            browser console. Click below to retry - your conversation history
            and traveler memory are preserved.
          </p>
          <pre>{message}</pre>
          <button type="button" onClick={this.reset}>Retry render</button>
        </div>
      </div>
    );
  }
}

export function MeridianDeviceShowcase() {
  const state = useMeridianShowcase();
  const presentation = usePresentationMode();
  const audienceLayout = presentation.fullscreen || presentation.preview;
  // Theme is session-local and scoped through CSS tokens.
  const [theme, setTheme] = useState<ShowcaseTheme>(initialTheme);
  const toggleTheme = () => setTheme((t) => (t === 'dark' ? 'light' : 'dark'));

  // Mirror the theme onto <html> so the document's own background and text
  // colour flip with the app. Without this the page keeps its dark ground
  // behind the showcase's translucent panels, and anything inheriting the
  // body colour renders near-white on near-white in light mode.
  useEffect(() => {
    const root = document.documentElement;
    root.dataset.theme = theme;
    return () => {
      delete root.dataset.theme;
    };
  }, [theme]);

  return (
    <main
      className="mds-root mds-fullbleed-route"
      data-theme={theme}
      data-audience-layout={audienceLayout ? 'true' : undefined}
      data-fullscreen={presentation.fullscreen ? 'true' : undefined}
      data-projector-readability={audienceLayout && presentation.projector ? 'true' : undefined}
      aria-label="Meridian product showcase"
    >
      <ShowcaseErrorBoundary>
        <PresenterControls mode={presentation} />
        <DesktopMeridianApp
          state={state}
          theme={theme}
          onToggleTheme={toggleTheme}
        />
      </ShowcaseErrorBoundary>
    </main>
  );
}

export default MeridianDeviceShowcase;
