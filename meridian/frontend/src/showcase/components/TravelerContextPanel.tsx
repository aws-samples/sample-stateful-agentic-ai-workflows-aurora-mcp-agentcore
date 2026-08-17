import { useEffect, useRef, useState } from 'react';
import { AnimatePresence, motion } from 'framer-motion';
import {
  CheckCircle2,
  ChevronDown,
  Database,
  History,
  Loader2,
  LockKeyhole,
  SlidersHorizontal,
  UserRound,
} from 'lucide-react';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { ALEX_IMAGE_URL, ALEX_NAME } from '../lib/personas';

// Snake-case schema keys read as "authentic Aurora data" for some fields
// (no_red_eye, vegetarian_friendly) but feel awkward for multi-word
// concepts (loyalty_programs, travel_style, recent_trips). Whitelist the
// ones we want to keep snake_case; humanize the rest.
const VERBATIM_KEYS = new Set([
  'no_red_eye',
  'vegetarian_friendly',
  'home_airport',
  'budget_cap',
  'avoid_connections',
]);

function formatFactKey(key: string): string {
  if (VERBATIM_KEYS.has(key)) return key;
  return key.replace(/_/g, ' ');
}

const PROFILE_KEYS = new Set(['home_airport', 'party_size', 'budget_cap']);
const PLAN_KEYS = new Set(['recent_trips', 'tokyo_culture', 'trip_goal']);
const DRAWER_ONLY_KEYS = new Set(['loyalty_programs', 'recent_trips']);

// Respect the OS reduced-motion setting: spring pops become plain fades.
const prefersReducedMotion =
  typeof window !== 'undefined' &&
  typeof window.matchMedia === 'function' &&
  window.matchMedia('(prefers-reduced-motion: reduce)').matches;

export function TravelerContextPanel({
  state,
  onOpenMemory,
  compact = false,
  collapsed = false,
  onToggleCollapsed,
}: {
  state: MeridianShowcaseState;
  onOpenMemory: () => void;
  compact?: boolean;
  /** Collapsed mode keeps just the header + chevron so the trace panel
   *  below can claim the freed vertical space. Used on tall demos /
   *  zoomed-in views where the audience needs the trace fully visible. */
  collapsed?: boolean;
  onToggleCollapsed?: () => void;
}) {
  const memoryAvailable = state.selectedPhase >= 4;
  const memoryOn = memoryAvailable && state.memoryEnabled;
  const facts = memoryOn
    ? state.memoryFacts
        .filter((fact) => !DRAWER_ONLY_KEYS.has(fact.key))
        .slice(0, compact ? 5 : 8)
    : [];
  const profileFacts = facts.filter((fact) => PROFILE_KEYS.has(fact.key));
  const planFacts = facts.filter((fact) => PLAN_KEYS.has(fact.key));
  const preferenceFacts = facts.filter(
    (fact) => !PROFILE_KEYS.has(fact.key) && !PLAN_KEYS.has(fact.key),
  );
  const agentCoreObserved = state.traceSpans.some((span) =>
    /agentcore memory|semantic retrieve|recent session events/i.test(span.name),
  );

  // "Writeback you can watch": Phase 4's concierge persists each turn to
  // Aurora inside the RLS transaction, emitting a "Strands @tool persist_turn"
  // span. When that span appears on a turn we pulse a "+N written to Aurora"
  // badge on the header so the audience FEELS the write actually happen.
  // The row count is the persist_turn write shape: 2 conversation_messages +
  // 1 trip_interaction = 3 rows (backend/agents/production_04/memory_agent.py
  // persist_turn, ~lines 220-271). If a span ever exposes an explicit count
  // we prefer that.
  const persistSpan =
    state.selectedPhase >= 4
      ? state.traceSpans.find((s) => /persist[_ ]turn/i.test(s.name))
      : undefined;
  const writebackRows = (() => {
    if (!persistSpan) return 3;
    const rowsField = persistSpan.fields?.find((f) => /rows|count/i.test(f.label));
    const parsed = rowsField ? parseInt(rowsField.value, 10) : NaN;
    return Number.isFinite(parsed) ? parsed : 3;
  })();

  const [justWrote, setJustWrote] = useState(false);
  const lastPersistId = useRef<string | null>(null);
  useEffect(() => {
    if (!persistSpan) return;
    if (persistSpan.id === lastPersistId.current) return;
    lastPersistId.current = persistSpan.id;
    setJustWrote(true);
    const timer = setTimeout(() => setJustWrote(false), 2500);
    return () => clearTimeout(timer);
  }, [persistSpan]);

  const className = [
    'mds-panel',
    'mds-traveler-panel',
    compact ? 'is-compact' : '',
    collapsed ? 'is-collapsed' : '',
  ]
    .filter(Boolean)
    .join(' ');
  const memoryStatus = state.memoryLoading ? 'connecting' : memoryOn ? 'on' : 'off';

  return (
    <section className={className}>
      <div className="mds-panel-head">
        {onToggleCollapsed ? (
          <button
            type="button"
            className="mds-collapse-toggle"
            onClick={onToggleCollapsed}
            aria-expanded={!collapsed}
            aria-label={collapsed ? 'Expand traveler context panel' : 'Collapse traveler context panel'}
            title={collapsed ? 'Expand traveler context' : 'Collapse so the trace fills the rail'}
          >
            <span className="mds-collapse-chevron" aria-hidden="true">
              <span className="mds-collapse-chevron-inner">
                <ChevronDown size={12} strokeWidth={2.6} />
              </span>
            </span>
            <strong>Traveler context</strong>
            {collapsed && memoryOn && state.memoryFacts.length > 0 && (
              <span className="mds-collapse-hint">{state.memoryFacts.length} facts</span>
            )}
          </button>
        ) : (
          <strong>Traveler context</strong>
        )}
        <AnimatePresence>
          {justWrote && (
            <motion.span
              className="mds-writeback-badge"
              initial={prefersReducedMotion ? { opacity: 0 } : { opacity: 0, scale: 0.7, y: -2 }}
              animate={prefersReducedMotion ? { opacity: 1 } : { opacity: 1, scale: 1, y: 0 }}
              exit={prefersReducedMotion ? { opacity: 0 } : { opacity: 0, scale: 0.8 }}
              transition={prefersReducedMotion ? { duration: 0 } : { type: 'spring', stiffness: 500, damping: 30 }}
              aria-live="polite"
            >
              <span className="mds-writeback-dot" aria-hidden="true" />
              +{writebackRows} written to Aurora
            </motion.span>
          )}
        </AnimatePresence>
        <button type="button" onClick={onOpenMemory} disabled={!memoryOn}>
          Memory
        </button>
      </div>
      {!collapsed && (
        <>
          <div className={`mds-memory-control${memoryOn ? ' is-enabled' : ''}`}>
            <span className="mds-memory-control-icon" aria-hidden="true">
              <Database size={17} />
            </span>
            <span className="mds-memory-control-copy">
              <strong>Use traveler context</strong>
              <small>
                {memoryAvailable
                  ? 'Authorize scoped recall and writeback'
                  : 'Available when Production is selected'}
              </small>
            </span>
            <button
              type="button"
              role="switch"
              className="mds-memory-switch"
              aria-checked={memoryOn}
              aria-label={`Use traveler context: ${memoryStatus}`}
              disabled={!memoryAvailable || state.memoryLoading}
              onClick={() => void state.setMemoryEnabled(!memoryOn)}
            >
              <span aria-hidden="true"><i /></span>
              <b>
                {memoryStatus.charAt(0).toUpperCase() + memoryStatus.slice(1)}
              </b>
            </button>
          </div>

          {!memoryAvailable ? (
            <div className="mds-memory-gate">
              <LockKeyhole size={22} aria-hidden="true" />
              <div>
                <strong>Unlocks in Production</strong>
                <span>Earlier phases receive no traveler profile or prior-turn context.</span>
              </div>
            </div>
          ) : state.memoryLoading ? (
            <div className="mds-memory-gate is-loading" role="status">
              <Loader2 size={22} aria-hidden="true" />
              <div>
                <strong>Authorizing Alex's context</strong>
                <span>Applying the traveler grant and loading scoped Aurora facts.</span>
              </div>
            </div>
          ) : !memoryOn ? (
            <div className="mds-memory-gate">
              <LockKeyhole size={22} aria-hidden="true" />
              <div>
                <strong>Context disconnected</strong>
                <span>No preferences, prior plans, or conversation memory will be read or written.</span>
              </div>
            </div>
          ) : (
            <motion.div
              className="mds-memory-enabled-content"
              initial={prefersReducedMotion ? { opacity: 0 } : { opacity: 0, y: 8 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: prefersReducedMotion ? 0 : 0.24 }}
            >
              <div className="mds-profile-line">
                <span className="mds-avatar is-photo" aria-hidden="true">
                  <img
                    src={ALEX_IMAGE_URL}
                    alt={ALEX_NAME}
                    width="640"
                    height="960"
                    loading="lazy"
                  />
                </span>
                <div>
                  <strong>Alex Morgan</strong>
                  <small>{state.travelerId}</small>
                </div>
                <span className="mds-memory-authorized">
                  <CheckCircle2 size={13} aria-hidden="true" />
                  Authorized
                </span>
              </div>

              <div className="mds-memory-provenance">
                <span><Database size={12} />Aurora · RLS scoped</span>
                {agentCoreObserved && <span><History size={12} />AgentCore session recalled</span>}
              </div>

              <MemoryFactGroup
                icon={UserRound}
                label="Profile"
                facts={profileFacts}
              />
              <MemoryFactGroup
                icon={SlidersHorizontal}
                label="Preferences"
                facts={preferenceFacts}
              />
              <MemoryFactGroup
                icon={History}
                label="Prior plans"
                facts={planFacts}
              />
            </motion.div>
          )}

          {state.memoryToggleError && (
            <div className="mds-memory-toggle-error" role="alert">
              {state.memoryToggleError}
            </div>
          )}

        </>
      )}
    </section>
  );
}

function MemoryFactGroup({
  icon: Icon,
  label,
  facts,
}: {
  icon: typeof UserRound;
  label: string;
  facts: MeridianShowcaseState['memoryFacts'];
}) {
  if (!facts.length) return null;

  return (
    <section className="mds-memory-fact-group">
      <header>
        <Icon size={13} aria-hidden="true" />
        <span>{label}</span>
      </header>
      <div className="mds-fact-list">
        {facts.map((fact) => (
          <div className="mds-fact-row" key={fact.key}>
            <span>{formatFactKey(fact.key)}</span>
            <b>{fact.value}</b>
          </div>
        ))}
      </div>
    </section>
  );
}
