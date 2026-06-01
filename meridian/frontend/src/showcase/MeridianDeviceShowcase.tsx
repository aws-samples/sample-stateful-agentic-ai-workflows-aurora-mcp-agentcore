import { Component, useState } from 'react';
import type { ReactNode } from 'react';
import { DesktopMeridianApp } from './DesktopMeridianApp';
import { useMeridianShowcase } from './hooks/useMeridianShowcase';
import './meridianShowcase.css';

type ShowcaseTheme = 'dark' | 'light';

// Page-level error boundary. If anything below the showcase root throws
// during render, show a recovery card instead of blanking the entire page
// (default React 18 behaviour on unhandled child errors). The error and
// stack are written to the console so the presenter can copy them out.
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
            browser console. Click below to retry — your conversation history
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
  // Local theme state — dark by default. Scoped to .mds-root via data-theme,
  // so the whole showcase re-skins from CSS tokens. No persistence (the
  // codebase forbids localStorage); a presenter sets it per session.
  const [theme, setTheme] = useState<ShowcaseTheme>('dark');
  const toggleTheme = () => setTheme((t) => (t === 'dark' ? 'light' : 'dark'));

  return (
    <main
      className="mds-root mds-fullbleed-route"
      data-theme={theme}
      aria-label="Meridian product showcase"
    >
      <button
        type="button"
        className="mds-theme-toggle"
        onClick={toggleTheme}
        aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
        title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
      >
        {theme === 'dark' ? (
          /* sun — click for light */
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <circle cx="12" cy="12" r="4" />
            <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
          </svg>
        ) : (
          /* moon — click for dark */
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
          </svg>
        )}
      </button>
      <ShowcaseErrorBoundary>
        <DesktopMeridianApp state={state} />
      </ShowcaseErrorBoundary>
    </main>
  );
}

export default MeridianDeviceShowcase;
