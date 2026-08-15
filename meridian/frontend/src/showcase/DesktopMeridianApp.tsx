import { useState } from 'react';
import {
  Briefcase,
  Compass,
  Mail,
  Moon,
  Settings2,
  Sparkles,
  Sun,
  UserRound,
  X,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import { AuroraEvidenceStrip } from './components/AuroraEvidenceStrip';
import { ChatComposer } from './components/ChatComposer';
import { ChatTranscript } from './components/ChatTranscript';
import { ComparisonDialog } from './components/ComparisonDialog';
import { JourneyPanel } from './components/JourneyPanel';
import { MemoryDrawer } from './components/MemoryDrawer';
import { NavPanelDrawer } from './components/NavPanelDrawer';
import type { NavPanelId } from './components/NavPanelDrawer';
import { PhaseSelector } from './components/PhaseSelector';
import { RecoveryWorkspace } from './components/RecoveryWorkspace';
import { TracePanel } from './components/TracePanel';
import { TravelerContextPanel } from './components/TravelerContextPanel';
import { TripDetailDrawer } from './components/TripDetailDrawer';
import type { MeridianShowcaseState } from './hooks/useMeridianShowcase';
import { MERIDIAN_MARK_SRC } from '../lib/meridianBrand';
import { ALEX_IMAGE_URL, ALEX_NAME } from './lib/personas';

type NavItemId = 'concierge' | 'trips' | 'discover' | 'profile' | 'preferences' | 'messages';
type ShowcaseTheme = 'dark' | 'light';
type DemoStep = 'ladder' | 'finale';

const navItems: { id: NavItemId; label: string; icon: LucideIcon }[] = [
  { id: 'concierge', label: 'Concierge', icon: Sparkles },
  { id: 'trips', label: 'Trips', icon: Briefcase },
  { id: 'discover', label: 'Discover', icon: Compass },
  { id: 'profile', label: 'Profile', icon: UserRound },
  { id: 'preferences', label: 'Preferences', icon: Settings2 },
  { id: 'messages', label: 'Messages', icon: Mail },
];

function BrandMark() {
  return (
    <img
      className="mds-brand-mark"
      src={MERIDIAN_MARK_SRC}
      alt=""
      width="36"
      height="36"
      loading="eager"
      decoding="async"
    />
  );
}

function greetingForHour(hour: number): string {
  if (hour >= 5 && hour < 12) return 'morning';
  if (hour >= 12 && hour < 17) return 'afternoon';
  return 'evening';
}

export function DesktopMeridianApp({
  state,
  theme,
  onToggleTheme,
}: {
  state: MeridianShowcaseState;
  theme: ShowcaseTheme;
  onToggleTheme: () => void;
}) {
  const [demoStep, setDemoStep] = useState<DemoStep>('ladder');
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [forYouCollapsed, setForYouCollapsed] = useState(false);
  const [activityCollapsed, setActivityCollapsed] = useState(false);
  const [auroraEvidenceCollapsed, setAuroraEvidenceCollapsed] = useState(false);
  const [navPanel, setNavPanel] = useState<NavPanelId | null>(null);
  const greetingPart = greetingForHour(new Date().getHours());
  const isLadder = demoStep === 'ladder';
  const showAudienceRuntimeStatus = state.backendStatus !== 'offline';

  const openLadder = () => setDemoStep('ladder');
  const openFinale = () => {
    state.setSelectedPhase(5);
    setDemoStep('finale');
  };

  const openNavItem = (id: NavItemId) => {
    if (id === 'concierge') {
      setNavPanel(null);
      setMemoryOpen(false);
      return;
    }
    if (id === 'preferences') {
      setNavPanel(null);
      setMemoryOpen(true);
      return;
    }
    setMemoryOpen(false);
    setNavPanel(id as NavPanelId);
  };

  return (
    <div className={`mds-desktop-app is-projector ${isLadder ? 'is-proof is-ladder' : 'is-experience is-finale'}`}>
      <aside className="mds-desktop-sidebar">
        <div className="mds-brand">
          <BrandMark />
          Meridian
        </div>
        <nav className="mds-nav-items" aria-label="Desktop navigation">
          {navItems.map((item) => {
            const isActive =
              (item.id === 'concierge' && navPanel === null && !memoryOpen) ||
              (item.id === 'preferences' && memoryOpen) ||
              navPanel === (item.id as NavPanelId);
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                type="button"
                className={`mds-nav-item${isActive ? ' is-active' : ''}`}
                aria-current={isActive ? 'page' : undefined}
                onClick={() => openNavItem(item.id)}
              >
                <span className="mds-nav-icon" aria-hidden="true">
                  <Icon size={18} strokeWidth={1.8} />
                </span>
                {item.label}
                {item.id === 'messages' && state.messages.length > 0 && (
                  <b>{state.messages.length}</b>
                )}
              </button>
            );
          })}
        </nav>
        <div className="mds-sidebar-spacer" />
        <button
          type="button"
          className="mds-account-mini"
          onClick={() => openNavItem('profile')}
          aria-label="Open Alex Morgan profile"
        >
          <span className="mds-avatar is-photo" aria-hidden="true">
            <img src={ALEX_IMAGE_URL} alt={ALEX_NAME} loading="lazy" />
          </span>
          <div className="mds-account-copy">
            <strong>Alex Morgan</strong>
            <span className="mds-account-loyalty">
              <span>Hotel Platinum</span>
              <span>Airline Premier</span>
            </span>
          </div>
          <span className="mds-account-chevron" aria-hidden="true">›</span>
        </button>
      </aside>

      <main className="mds-desktop-main">
        <div className="mds-desktop-scroll">
          <div className="mds-top-actions">
            <div className="mds-top-status">
              {showAudienceRuntimeStatus && (
                <span
                  className={`mds-status-pill${
                    state.backendStatus === 'online'
                      ? ' is-live'
                      : ' is-checking'
                  }`}
                >
                  <span className="mds-status-dot" aria-hidden="true" />
                  {state.backendStatus === 'online' ? 'Reasoning live' : 'Connecting…'}
                  <span className="mds-status-sep" aria-hidden="true">·</span>
                  <span className="mds-status-unit">
                    {state.backendStatus === 'online' ? 'USD' : 'Live data pending'}
                  </span>
                </span>
              )}
              <button
                type="button"
                className="mds-theme-toggle"
                onClick={onToggleTheme}
                aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
                title={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
              >
                {theme === 'dark'
                  ? <Sun size={17} aria-hidden="true" />
                  : <Moon size={17} aria-hidden="true" />}
              </button>
            </div>
          </div>

          <nav className="mds-demo-sequence" aria-label="Chalk-talk sequence">
            <button
              type="button"
              className={isLadder ? 'is-active' : ''}
              aria-current={isLadder ? 'step' : undefined}
              onClick={openLadder}
            >
              <span>1</span>
              Capability ladder
            </button>
            <i aria-hidden="true" />
            <button
              type="button"
              className={!isLadder ? 'is-active' : ''}
              aria-current={!isLadder ? 'step' : undefined}
              onClick={openFinale}
            >
              <span>2</span>
              Stateful Recovery finale
            </button>
          </nav>

          {isLadder ? (
            <>
              <div className="mds-headline-row mds-ladder-headline">
                <div>
                  <h1>{`Good ${greetingPart}, Alex.`}</h1>
                  <p>Capability ladder · SQL → MCP → Retrieval → Production → Durable workflow</p>
                </div>
                <PhaseSelector state={state} />
              </div>

              <AuroraEvidenceStrip
                state={state}
                collapsed={auroraEvidenceCollapsed}
                onToggleCollapsed={() => setAuroraEvidenceCollapsed((prev) => !prev)}
              />

              {state.phaseHint && (
                <div className="mds-phase-hint" role="status" aria-live="polite">
                  <span className="mds-phase-hint-badge">{state.phaseHint.label}</span>
                  <span className="mds-phase-hint-copy">{state.phaseHint.adds}</span>
                  {state.phaseHint.tech && (
                    <span className="mds-phase-hint-tech">{state.phaseHint.tech}</span>
                  )}
                  <button
                    type="button"
                    className="mds-phase-hint-dismiss"
                    onClick={state.dismissPhaseHint}
                    aria-label="Dismiss"
                  >
                    <X size={13} />
                  </button>
                </div>
              )}

              {state.error && (
                <div className="mds-error-banner" role="alert">
                  <span className="mds-error-banner-copy">
                    Meridian could not reach the live concierge.
                  </span>
                  <span className="mds-error-banner-actions">
                    {state.lastPrompt && (
                      <button
                        type="button"
                        className="mds-error-retry"
                        onClick={() => {
                          state.clearError();
                          void state.replayLastPrompt();
                        }}
                        disabled={state.isLoading}
                      >
                        Retry
                      </button>
                    )}
                    <button
                      type="button"
                      className="mds-error-dismiss"
                      onClick={state.clearError}
                    >
                      Dismiss
                    </button>
                  </span>
                </div>
              )}

              <ChatTranscript state={state} proofMode />

              <div className="mds-main-actions">
                <button
                  type="button"
                  onClick={() => void state.replayLastPrompt()}
                  disabled={!state.lastPrompt || state.isLoading}
                >
                  Rerun across {state.phaseLabel}
                </button>
                <button type="button" onClick={() => setMemoryOpen(true)}>
                  Inspect memory
                </button>
                <button
                  type="button"
                  onClick={state.clearChat}
                  disabled={state.isLoading || (state.messages.length === 0 && state.traceSpans.length === 0)}
                >
                  Clear chat
                </button>
              </div>
            </>
          ) : (
            <RecoveryWorkspace
              state={state}
              greetingPart={greetingPart}
              onOpenProof={openLadder}
            />
          )}
        </div>

        {isLadder && (
          <div className="mds-desktop-dock">
            <ChatComposer state={state} proofMode />
          </div>
        )}
      </main>

      <aside className="mds-desktop-right">
        {isLadder ? (
          <>
            <TravelerContextPanel
              state={state}
              onOpenMemory={() => setMemoryOpen(true)}
              collapsed={forYouCollapsed}
              onToggleCollapsed={() => setForYouCollapsed((prev) => !prev)}
            />
            <TracePanel
              state={state}
              collapsed={activityCollapsed}
              onToggleCollapsed={() => setActivityCollapsed((prev) => !prev)}
            />
          </>
        ) : (
          <JourneyPanel state={state} onOpenProof={openLadder} />
        )}
      </aside>

      <TripDetailDrawer state={state} />
      <ComparisonDialog state={state} />
      <MemoryDrawer state={state} open={memoryOpen} onClose={() => setMemoryOpen(false)} />
      <NavPanelDrawer state={state} panel={navPanel} onClose={() => setNavPanel(null)} />
      {state.workspaceNotice && (
        <div className="mds-toast" role="status">{state.workspaceNotice}</div>
      )}
    </div>
  );
}
