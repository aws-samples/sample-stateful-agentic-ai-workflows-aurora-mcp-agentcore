import { useEffect, useState } from 'react';
import {
  Briefcase,
  Compass,
  Mail,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
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
import { DiscoveryWorkspace } from './components/DiscoveryWorkspace';
import { MemoryDrawer } from './components/MemoryDrawer';
import { NavPanelDrawer } from './components/NavPanelDrawer';
import type { NavPanelId } from './components/NavPanelDrawer';
import { PhaseSelector } from './components/PhaseSelector';
import { RecoveryWorkspace } from './components/RecoveryWorkspace';
import { TracePanel } from './components/TracePanel';
import { TravelerContextPanel } from './components/TravelerContextPanel';
import { TripDetailDrawer } from './components/TripDetailDrawer';
import { IconTooltip } from './components/ShowcaseTooltip';
import type { MeridianShowcaseState } from './hooks/useMeridianShowcase';
import { MERIDIAN_MARK_SRC } from '../lib/meridianBrand';
import { ALEX_IMAGE_URL, ALEX_NAME } from './lib/personas';
import { deriveRecoveryStage } from './lib/recoveryState';

type NavItemId = 'concierge' | 'trips' | 'discover' | 'profile' | 'preferences' | 'messages';
type ShowcaseTheme = 'dark' | 'light';
type DemoStep = 'discovery' | 'ladder' | 'finale';

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
  const [demoStep, setDemoStep] = useState<DemoStep>('discovery');
  const [memoryOpen, setMemoryOpen] = useState(false);
  const [forYouCollapsed, setForYouCollapsed] = useState(false);
  const [activityCollapsed, setActivityCollapsed] = useState(false);
  const [auroraEvidenceCollapsed, setAuroraEvidenceCollapsed] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(
    () =>
      typeof window !== 'undefined' &&
      typeof window.matchMedia === 'function' &&
      window.matchMedia('(max-width: 1180px)').matches,
  );
  const [navPanel, setNavPanel] = useState<NavPanelId | null>(null);
  const greetingPart = greetingForHour(new Date().getHours());
  const isDiscovery = demoStep === 'discovery';
  const isLadder = demoStep === 'ladder';
  const isFinale = demoStep === 'finale';
  const recoveryStage = deriveRecoveryStage(state);
  const runtimeStatus =
    state.backendStatus === 'online'
      ? {
          className: 'is-live',
          label: 'Meridian live',
          detail: 'USD',
        }
      : state.backendStatus === 'offline'
        ? {
            className: 'is-off',
            label: 'Meridian offline',
            detail: 'Live data unavailable',
          }
        : {
            className: 'is-checking',
            label: 'Connecting to Meridian',
            detail: 'Live data pending',
          };

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;

    const compactSidebar = window.matchMedia('(max-width: 1180px)');
    const syncSidebar = () => setSidebarCollapsed(compactSidebar.matches);

    syncSidebar();
    compactSidebar.addEventListener('change', syncSidebar);
    return () => compactSidebar.removeEventListener('change', syncSidebar);
  }, []);

  const openDiscovery = () => setDemoStep('discovery');
  const openLadder = () => setDemoStep('ladder');
  const openFinale = () => {
    state.setSelectedPhase(5);
    setDemoStep('finale');
  };
  const clearIntoLadder = () => {
    state.clearChat();
    state.setSelectedPhase(1);
    setMemoryOpen(false);
    setNavPanel(null);
    setDemoStep('ladder');
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
    <div
      className={`mds-desktop-app is-projector ${
        isDiscovery
          ? 'is-discovery'
          : isLadder
            ? 'is-proof is-ladder'
            : 'is-experience is-finale'
      }${sidebarCollapsed ? ' is-sidebar-collapsed' : ''}`}
    >
      <aside className="mds-desktop-sidebar">
        <div className="mds-sidebar-head">
          <div className="mds-brand">
            <BrandMark />
            <span className="mds-brand-name">Meridian</span>
          </div>
          <IconTooltip label={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
            <button
              type="button"
              className="mds-sidebar-toggle"
              onClick={() => setSidebarCollapsed((collapsed) => !collapsed)}
              aria-label={sidebarCollapsed ? 'Expand navigation sidebar' : 'Collapse navigation sidebar'}
              aria-expanded={!sidebarCollapsed}
            >
              {sidebarCollapsed
                ? <PanelLeftOpen size={18} aria-hidden="true" />
                : <PanelLeftClose size={18} aria-hidden="true" />}
            </button>
          </IconTooltip>
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
                aria-label={item.label}
                title={sidebarCollapsed ? item.label : undefined}
                onClick={() => openNavItem(item.id)}
              >
                <span className="mds-nav-icon" aria-hidden="true">
                  <Icon size={18} strokeWidth={1.8} />
                </span>
                <span className="mds-nav-label">{item.label}</span>
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
            <img
              src={ALEX_IMAGE_URL}
              alt={ALEX_NAME}
              width="640"
              height="960"
              loading="lazy"
            />
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
              <span
                className={`mds-status-pill ${runtimeStatus.className}`}
                role="status"
                aria-live="polite"
                aria-atomic="true"
              >
                <span className="mds-status-dot" aria-hidden="true" />
                {runtimeStatus.label}
                <span className="mds-status-sep" aria-hidden="true">·</span>
                <span className="mds-status-unit">{runtimeStatus.detail}</span>
              </span>
              <IconTooltip label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}>
                <button
                  type="button"
                  className="mds-theme-toggle"
                  onClick={onToggleTheme}
                  aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
                >
                  {theme === 'dark'
                    ? <Sun size={17} aria-hidden="true" />
                    : <Moon size={17} aria-hidden="true" />}
                </button>
              </IconTooltip>
            </div>
          </div>

          <nav className="mds-demo-sequence has-three" aria-label="Chalk-talk sequence">
            <button
              type="button"
              className={isDiscovery ? 'is-active' : ''}
              aria-current={isDiscovery ? 'step' : undefined}
              aria-label="1 Discovery, Experience"
              onClick={openDiscovery}
            >
              <span className="mds-demo-sequence-index" aria-hidden="true">1</span>
              <span className="mds-demo-sequence-copy" aria-hidden="true">
                <strong>Discovery</strong>
                <small>Experience</small>
              </span>
            </button>
            <i aria-hidden="true" />
            <button
              type="button"
              className={isLadder ? 'is-active' : ''}
              aria-current={isLadder ? 'step' : undefined}
              aria-label="2 Capability ladder, Architecture"
              onClick={openLadder}
            >
              <span className="mds-demo-sequence-index" aria-hidden="true">2</span>
              <span className="mds-demo-sequence-copy" aria-hidden="true">
                <strong>Capability ladder</strong>
                <small>Architecture</small>
              </span>
            </button>
            <i aria-hidden="true" />
            <button
              type="button"
              className={isFinale ? 'is-active' : ''}
              aria-current={isFinale ? 'step' : undefined}
              aria-label="3 Stateful recovery, Proof"
              onClick={openFinale}
            >
              <span className="mds-demo-sequence-index" aria-hidden="true">3</span>
              <span className="mds-demo-sequence-copy" aria-hidden="true">
                <strong>Stateful recovery</strong>
                <small>Proof</small>
              </span>
            </button>
          </nav>

          {isDiscovery ? (
            <DiscoveryWorkspace
              state={state}
              greeting={greetingPart}
              onClear={clearIntoLadder}
            />
          ) : isLadder ? (
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
              onOpenProof={openLadder}
              showComposer={false}
            />
          )}
        </div>

        {(isLadder || (isFinale && recoveryStage === 'ready')) && (
          <div className="mds-desktop-dock">
            {isLadder ? (
              <ChatComposer state={state} proofMode />
            ) : (
              <ChatComposer state={state} recoveryMode />
            )}
          </div>
        )}
      </main>

      {isLadder && (
        <aside className="mds-desktop-right">
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
        </aside>
      )}

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
