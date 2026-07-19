import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import type { Product } from '../../types';
import { SHOWCASE_EXAMPLE_PROMPTS, SHOWCASE_PHASES } from '../lib/showcaseAdapters';
import { ALEX_IMAGE_URL, ALEX_NAME } from '../lib/personas';
import { X } from 'lucide-react';
import { useDialogA11y } from '../hooks/useDialogA11y';

// Lightweight-but-complete side panels for the sidebar nav. Each panel
// renders REAL session / Aurora state — no fixtures — so a presenter (or a
// booth visitor) can click any nav item and land on something coherent
// instead of a dead button. All four share the existing drawer chrome
// (.mds-drawer) so they match the Memory drawer visually.

export type NavPanelId = 'trips' | 'discover' | 'profile' | 'messages';

const PANEL_TITLE: Record<NavPanelId, string> = {
  trips: 'Your trips',
  discover: 'Discover',
  profile: 'Profile',
  messages: 'Messages',
};

function money(n: number): string {
  return `$${n.toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
}

export function NavPanelDrawer({
  state,
  panel,
  onClose,
}: {
  state: MeridianShowcaseState;
  panel: NavPanelId | null;
  onClose: () => void;
}) {
  const dialogRef = useDialogA11y(Boolean(panel), onClose);
  if (!panel) return null;

  return (
    <div className="mds-drawer-backdrop" onClick={onClose} role="presentation">
      <aside
        ref={dialogRef}
        className="mds-drawer"
        role="dialog"
        aria-modal="true"
        aria-label={PANEL_TITLE[panel]}
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <header>
          <div>
            <span>{PANEL_TITLE[panel]}</span>
            <strong>Alex Morgan</strong>
          </div>
          <button type="button" onClick={onClose} aria-label={`Close ${PANEL_TITLE[panel]}`}>
            <X size={17} />
          </button>
        </header>

        {panel === 'trips' && <TripsPanel state={state} onClose={onClose} />}
        {panel === 'discover' && <DiscoverPanel state={state} onClose={onClose} />}
        {panel === 'profile' && <ProfilePanel state={state} />}
        {panel === 'messages' && <MessagesPanel state={state} />}
      </aside>
    </div>
  );
}

// --- Trips: saved trips + this turn's recommendations -------------------
function TripsPanel({ state, onClose }: { state: MeridianShowcaseState; onClose: () => void }) {
  const saved = state.savedTrips;
  const browsing = state.recommendations.filter((p) => !state.savedTripIds.has(p.product_id));

  if (saved.length === 0 && browsing.length === 0) {
    return (
      <div className="mds-navpanel-empty">
        <b>No trips yet</b>
        <span>Ask the concierge for a destination, then Save the ones you like — they'll collect here.</span>
      </div>
    );
  }

  return (
    <div className="mds-drawer-list">
      {saved.length > 0 && (
        <div className="mds-navpanel-section">
          <div className="mds-navpanel-section-head">Saved · {saved.length}</div>
          {saved.map((p) => (
            <TripRow key={p.product_id} product={p} state={state} onClose={onClose} saved />
          ))}
        </div>
      )}
      {browsing.length > 0 && <div className="mds-navpanel-section">
        <div className="mds-navpanel-section-head">
          {saved.length > 0 ? 'This turn' : `Results · ${browsing.length}`}
        </div>
        {browsing.map((p) => (
          <TripRow key={p.product_id} product={p} state={state} onClose={onClose} />
        ))}
      </div>}
    </div>
  );
}

function TripRow({
  product,
  state,
  onClose,
  saved = false,
}: {
  product: Product;
  state: MeridianShowcaseState;
  onClose: () => void;
  saved?: boolean;
}) {
  const matchPct = product.similarity != null ? Math.round(product.similarity * 100) : null;
  return (
    <button
      type="button"
      className="mds-navpanel-trip"
      onClick={() => {
        state.selectTrip(product);
        onClose();
      }}
    >
      <div className="mds-navpanel-trip-main">
        <strong>{product.name}</strong>
        <small>{product.brand}</small>
      </div>
      <div className="mds-navpanel-trip-meta">
        <b>{money(product.price)}</b>
        {matchPct != null && <span>{matchPct}% match</span>}
        {saved && <span className="mds-navpanel-saved-dot" aria-label="Saved" />}
      </div>
    </button>
  );
}

// --- Discover: curated starter prompts across all five modes ------------
function DiscoverPanel({ state, onClose }: { state: MeridianShowcaseState; onClose: () => void }) {
  return (
    <div className="mds-drawer-list">
      <div className="mds-navpanel-hint">
        Tap a prompt to load it into the composer and switch to that mode.
      </div>
      {SHOWCASE_PHASES.map((phase) => {
        const prompts = SHOWCASE_EXAMPLE_PROMPTS[phase.phase] ?? [];
        if (prompts.length === 0) return null;
        return (
          <div className="mds-navpanel-section" key={phase.phase}>
            <div className="mds-navpanel-section-head">
              {phase.label}
              <em>{phase.description}</em>
            </div>
            {prompts.map((prompt) => (
              <button
                key={prompt}
                type="button"
                className="mds-navpanel-prompt"
                onClick={() => {
                  state.setSelectedPhase(phase.phase);
                  state.setCurrentPrompt(prompt);
                  onClose();
                }}
              >
                {prompt}
              </button>
            ))}
          </div>
        );
      })}
    </div>
  );
}

// --- Profile: traveler identity + memory + session stats ----------------
function ProfilePanel({ state }: { state: MeridianShowcaseState }) {
  const userTurns = state.messages.filter((m) => m.role === 'user').length;
  const saved = state.savedTripIds.size;

  return (
    <div className="mds-drawer-list">
      <div className="mds-navpanel-profile-head">
        <span className="mds-avatar is-photo" aria-hidden="true">
          <img src={ALEX_IMAGE_URL} alt={ALEX_NAME} loading="lazy" />
        </span>
        <div>
          <strong>Alex Morgan</strong>
          <small>{state.travelerId}</small>
        </div>
      </div>

      <div className="mds-navpanel-stats">
        <div>
          <b>{userTurns}</b>
          <span>prompts</span>
        </div>
        <div>
          <b>{saved}</b>
          <span>saved</span>
        </div>
        <div>
          <b>{state.phaseLabel}</b>
          <span>mode</span>
        </div>
      </div>

      <div className="mds-navpanel-section">
        <div className="mds-navpanel-section-head">Memory facts · {state.memoryFacts.length}</div>
        {state.memoryFacts.length === 0 ? (
          <div className="mds-navpanel-hint">
            Switch to Production and ask a question — traveler facts load from Aurora here.
          </div>
        ) : (
          state.memoryFacts.map((fact) => (
            <div className="mds-navpanel-fact" key={fact.key}>
              <span>{fact.key.replace(/_/g, ' ')}</span>
              <b>{fact.value}</b>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

// --- Messages: the session transcript as a scannable list ---------------
function MessagesPanel({ state }: { state: MeridianShowcaseState }) {
  if (state.messages.length === 0) {
    return (
      <div className="mds-navpanel-empty">
        <b>No messages yet</b>
        <span>Your conversation with the concierge will appear here as you chat.</span>
      </div>
    );
  }
  return (
    <div className="mds-drawer-list">
      {state.messages.map((message, idx) => (
        <div
          key={`${message.role}-${idx}`}
          className={`mds-navpanel-msg is-${message.role}`}
        >
          <span className="mds-navpanel-msg-role">{message.role === 'user' ? 'Alex' : 'Meridian'}</span>
          <span className="mds-navpanel-msg-text">{message.text}</span>
        </div>
      ))}
    </div>
  );
}
