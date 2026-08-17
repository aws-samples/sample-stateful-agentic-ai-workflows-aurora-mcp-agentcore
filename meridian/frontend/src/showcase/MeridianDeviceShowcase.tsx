import { Component, useState } from 'react';
import type { ReactNode } from 'react';
import { DesktopMeridianApp } from './DesktopMeridianApp';
import { useMeridianShowcase } from './hooks/useMeridianShowcase';
import './meridianShowcase.css';
import './recoveryWorkspace.css';
import './discoveryWorkspace.css';
import './recoveryDecisionRefresh.css';

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
  // Theme is session-local and scoped through CSS tokens.
  const [theme, setTheme] = useState<ShowcaseTheme>(initialTheme);
  const toggleTheme = () => setTheme((t) => (t === 'dark' ? 'light' : 'dark'));

  return (
    <main
      className="mds-root mds-fullbleed-route"
      data-theme={theme}
      aria-label="Meridian product showcase"
    >
      <ShowcaseErrorBoundary>
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
