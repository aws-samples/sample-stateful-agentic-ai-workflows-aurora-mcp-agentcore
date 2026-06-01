import { useEffect, useRef, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import type { ChatFilters } from '../hooks/useMeridianShowcase';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';

type ChipKey = 'travelers' | 'dates' | 'spa' | 'flights';

export function ChatComposer({ state, compact = false }: { state: MeridianShowcaseState; compact?: boolean }) {
  const [openChip, setOpenChip] = useState<ChipKey | null>(null);

  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    void state.submitPrompt();
  };

  // Phase-specific example prompts seed the composer with a canonical
  // walkthrough query for whichever phase is selected.
  const queryStarters = compact ? [] : state.phaseExamples.slice(0, 3);

  const updateFilters = (patch: Partial<ChatFilters>) => {
    state.setChatFilters({ ...state.chatFilters, ...patch });
  };

  const closePopover = () => setOpenChip(null);
  const toggleChip = (key: ChipKey) => setOpenChip((prev) => (prev === key ? null : key));

  return (
    <div className={`mds-chat-composer-wrap${compact ? ' is-compact' : ''}`}>
      {queryStarters.length > 0 && (
        <div className="mds-chat-query-starters" aria-label="Query starters for this phase">
          <span className="mds-chat-starter-label">Try</span>
          {queryStarters.map((prompt, index) => {
            // The third pill in each phase is intentionally a "stretch"
            // prompt - one this phase cannot fully handle, so the next
            // phase has something to demonstrate against. Marked with a
            // distinct amber border so the presenter knows which is which.
            // Workflow (Phase 5) is the finale — there's no next phase to
            // motivate, and all three pills are solid LangGraph successes,
            // so none of them are marked as a stretch.
            const isStretch =
              state.phaseLabel !== 'Workflow' &&
              index === queryStarters.length - 1 &&
              queryStarters.length === 3;
            return (
              <button
                key={prompt}
                type="button"
                className={`mds-chat-starter-chip${isStretch ? ' is-stretch' : ''}`}
                disabled={state.isLoading}
                onClick={() => void state.applyPhaseExample(prompt)}
                title={isStretch ? `Stretch query — exposes ${state.phaseLabel}'s limits` : prompt}
              >
                {prompt}
              </button>
            );
          })}
        </div>
      )}
      <form className={`mds-chat-composer${compact ? ' is-compact' : ''}`} onSubmit={onSubmit}>
        <input
          value={state.currentPrompt}
          onChange={(event) => state.setCurrentPrompt(event.target.value)}
          placeholder={'Ask Meridian anything — "a calm wine trip in October, under $2,500"…'}
          disabled={state.isLoading}
          aria-label="Ask Meridian anything"
        />
        <button
          type="submit"
          className="mds-chat-send"
          disabled={state.isLoading || !state.currentPrompt.trim()}
          aria-label="Send message"
        >
          {state.isLoading ? (
            <span className="mds-chat-send-spinner" aria-hidden="true" />
          ) : (
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M5 12h14M13 6l6 6-6 6" />
            </svg>
          )}
        </button>
      </form>
      {!compact && (
        <div className="mds-chat-quick-actions" aria-label="Quick concierge actions">
          <TravelersChip
            filters={state.chatFilters}
            isOpen={openChip === 'travelers'}
            disabled={state.isLoading}
            onToggle={() => toggleChip('travelers')}
            onClose={closePopover}
            onChange={(travelers) => updateFilters({ travelers })}
          />
          <DatesChip
            filters={state.chatFilters}
            isOpen={openChip === 'dates'}
            disabled={state.isLoading}
            onToggle={() => toggleChip('dates')}
            onClose={closePopover}
            onChange={(startDate, endDate) => updateFilters({ startDate, endDate })}
          />
          <ToggleChip
            label="Add Spa"
            activeLabel="Spa included"
            active={state.chatFilters.spa}
            disabled={state.isLoading}
            onToggle={() => updateFilters({ spa: !state.chatFilters.spa })}
            icon={<SpaIcon />}
          />
          <ToggleChip
            label="Direct flights"
            activeLabel="Direct flights"
            active={state.chatFilters.directFlights}
            disabled={state.isLoading}
            onToggle={() => updateFilters({ directFlights: !state.chatFilters.directFlights })}
            icon={<PlaneIcon />}
          />
          {hasAnyFilter(state.chatFilters) && (
            <button
              type="button"
              className="mds-chat-action-chip is-clear"
              onClick={() => state.resetChatFilters()}
              title="Clear all filters"
            >
              <span className="mds-chat-action-chip-icon" aria-hidden="true">
                <CloseIcon />
              </span>
              Clear
            </button>
          )}
        </div>
      )}
    </div>
  );
}

function hasAnyFilter(f: ChatFilters): boolean {
  return f.travelers > 0 || !!f.startDate || !!f.endDate || f.spa || f.directFlights;
}

// ----------------------------- Travelers chip -----------------------------

function TravelersChip({
  filters,
  isOpen,
  disabled,
  onToggle,
  onClose,
  onChange,
}: {
  filters: ChatFilters;
  isOpen: boolean;
  disabled: boolean;
  onToggle: () => void;
  onClose: () => void;
  onChange: (count: number) => void;
}) {
  const active = filters.travelers > 0;
  const label = active
    ? `${filters.travelers} ${filters.travelers === 1 ? 'traveler' : 'travelers'}`
    : 'Add travelers';

  const adjust = (delta: number) => {
    const next = Math.max(0, Math.min(8, filters.travelers + delta));
    onChange(next);
  };

  return (
    <ChipPopoverShell
      isOpen={isOpen}
      onClose={onClose}
      trigger={
        <ActionChipButton
          label={label}
          active={active}
          disabled={disabled}
          onClick={onToggle}
          icon={<TravelersIcon />}
        />
      }
    >
      <div className="mds-popover-title">How many travelers?</div>
      <div className="mds-popover-stepper">
        <button
          type="button"
          className="mds-popover-step-btn"
          onClick={() => adjust(-1)}
          disabled={filters.travelers <= 0}
          aria-label="Decrease travelers"
        >
          −
        </button>
        <span className="mds-popover-step-value">
          <b>{filters.travelers || 0}</b>
          <em>{filters.travelers === 1 ? 'traveler' : 'travelers'}</em>
        </span>
        <button
          type="button"
          className="mds-popover-step-btn"
          onClick={() => adjust(1)}
          disabled={filters.travelers >= 8}
          aria-label="Increase travelers"
        >
          +
        </button>
      </div>
      <div className="mds-popover-row">
        <button
          type="button"
          className="mds-popover-link"
          onClick={() => {
            onChange(0);
            onClose();
          }}
          disabled={!active}
        >
          Clear
        </button>
        <button type="button" className="mds-popover-primary" onClick={onClose}>
          Done
        </button>
      </div>
    </ChipPopoverShell>
  );
}

// ----------------------------- Dates chip -----------------------------

function DatesChip({
  filters,
  isOpen,
  disabled,
  onToggle,
  onClose,
  onChange,
}: {
  filters: ChatFilters;
  isOpen: boolean;
  disabled: boolean;
  onToggle: () => void;
  onClose: () => void;
  onChange: (startDate: string | null, endDate: string | null) => void;
}) {
  const hasStart = !!filters.startDate;
  const hasEnd = !!filters.endDate;
  const label = hasStart && hasEnd
    ? `${formatShort(filters.startDate!)} – ${formatShort(filters.endDate!)}`
    : hasStart
      ? `From ${formatShort(filters.startDate!)}`
      : 'Change dates';
  const active = hasStart || hasEnd;

  return (
    <ChipPopoverShell
      isOpen={isOpen}
      onClose={onClose}
      trigger={
        <ActionChipButton
          label={label}
          active={active}
          disabled={disabled}
          onClick={onToggle}
          icon={<CalendarIcon />}
        />
      }
    >
      <div className="mds-popover-title">When are you going?</div>
      <div className="mds-popover-dates">
        <label>
          <span>From</span>
          <input
            type="date"
            value={filters.startDate ?? ''}
            min={todayISO()}
            onChange={(e) => {
              const v = e.target.value || null;
              const fixedEnd =
                v && filters.endDate && filters.endDate < v ? null : filters.endDate;
              onChange(v, fixedEnd);
            }}
          />
        </label>
        <label>
          <span>To</span>
          <input
            type="date"
            value={filters.endDate ?? ''}
            min={filters.startDate ?? todayISO()}
            onChange={(e) => onChange(filters.startDate, e.target.value || null)}
          />
        </label>
      </div>
      <div className="mds-popover-row">
        <button
          type="button"
          className="mds-popover-link"
          onClick={() => {
            onChange(null, null);
            onClose();
          }}
          disabled={!active}
        >
          Clear
        </button>
        <button type="button" className="mds-popover-primary" onClick={onClose}>
          Done
        </button>
      </div>
    </ChipPopoverShell>
  );
}

// ----------------------------- Toggle chip -----------------------------

function ToggleChip({
  label,
  activeLabel,
  active,
  disabled,
  onToggle,
  icon,
}: {
  label: string;
  activeLabel: string;
  active: boolean;
  disabled: boolean;
  onToggle: () => void;
  icon: ReactNode;
}) {
  return (
    <ActionChipButton
      label={active ? activeLabel : label}
      active={active}
      disabled={disabled}
      onClick={onToggle}
      icon={icon}
      aria-pressed={active}
    />
  );
}

// ----------------------------- Chip primitives -----------------------------

function ActionChipButton({
  label,
  active,
  disabled,
  onClick,
  icon,
  ...rest
}: {
  label: string;
  active: boolean;
  disabled: boolean;
  onClick: () => void;
  icon: ReactNode;
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const className = `mds-chat-action-chip${active ? ' is-active' : ''}`;
  return (
    <button
      type="button"
      className={className}
      disabled={disabled}
      onClick={onClick}
      title={label}
      {...rest}
    >
      <span className="mds-chat-action-chip-icon" aria-hidden="true">
        {icon}
      </span>
      {label}
      {active && <span className="mds-chat-action-chip-dot" aria-hidden="true" />}
    </button>
  );
}

function ChipPopoverShell({
  trigger,
  isOpen,
  onClose,
  children,
}: {
  trigger: ReactNode;
  isOpen: boolean;
  onClose: () => void;
  children: ReactNode;
}) {
  const wrapRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!isOpen) return undefined;
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [isOpen, onClose]);

  return (
    <span ref={wrapRef} className={`mds-chip-pop-wrap${isOpen ? ' is-open' : ''}`}>
      {trigger}
      {isOpen && (
        <div className="mds-chip-pop" role="dialog" aria-modal="false">
          {children}
        </div>
      )}
    </span>
  );
}

// ----------------------------- Helpers -----------------------------

function todayISO(): string {
  return new Date().toISOString().slice(0, 10);
}

function formatShort(iso: string): string {
  const [, m, d] = iso.split('-');
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const monthIdx = Math.max(0, Math.min(11, Number(m) - 1));
  return `${months[monthIdx]} ${Number(d)}`;
}

// ----------------------------- Icons -----------------------------

function TravelersIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="9" cy="9" r="3.4" />
      <path d="M3 19a6 6 0 0 1 12 0" />
      <circle cx="17" cy="8" r="2.6" />
      <path d="M14.5 19a4.6 4.6 0 0 1 7 0" />
    </svg>
  );
}

function CalendarIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="5" width="18" height="16" rx="2" />
      <path d="M3 10h18" />
      <path d="M8 3v4" />
      <path d="M16 3v4" />
    </svg>
  );
}

function SpaIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 4c-2.5 3-2.5 6 0 9" />
      <path d="M12 4c2.5 3 2.5 6 0 9" />
      <path d="M4 14c1.6 1.4 3.6 1.6 6 1" />
      <path d="M20 14c-1.6 1.4-3.6 1.6-6 1" />
      <path d="M5 19c2.6 1.6 11.4 1.6 14 0" />
    </svg>
  );
}

function PlaneIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 16v-2l-8-5V3.5a1.5 1.5 0 0 0-3 0V9l-8 5v2l8-2.5V19l-2.5 1.5V22l4-1 4 1v-1.5L13 19v-5.5z" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M6 6l12 12" />
      <path d="M18 6L6 18" />
    </svg>
  );
}
