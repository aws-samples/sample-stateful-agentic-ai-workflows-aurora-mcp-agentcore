import { Component } from 'react';
import type { ReactNode } from 'react';
import { DesktopMeridianApp } from './DesktopMeridianApp';
import { useMeridianShowcase } from './hooks/useMeridianShowcase';
import './meridianShowcase.css';

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

  return (
    <main className="mds-root mds-fullbleed-route" aria-label="Meridian product showcase">
      <ShowcaseErrorBoundary>
        <DesktopMeridianApp state={state} />
      </ShowcaseErrorBoundary>
    </main>
  );
}

export default MeridianDeviceShowcase;
