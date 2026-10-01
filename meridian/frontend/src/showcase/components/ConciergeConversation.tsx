import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { AlertTriangle, ArrowRight, CalendarDays, ChevronDown, Heart, Plane, Search, UsersRound, X } from 'lucide-react';
import { ChatComposer } from './ChatComposer';
import { ShowcaseMarkdown } from './ChatTranscript';
import { ConciergeBell } from '../icons/TravelIcons';
import { ConciergeRail } from '../surfaces/ConciergeRail';
import { TripHoldReceipt } from './TripHoldReceipt';
import { ALEX_IMAGE_URL } from '../lib/personas';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';

const STARTERS = [
  { label: 'A culture trip to Tokyo', prompt: 'Help me plan a culture trip to Tokyo' },
  { label: 'A quiet wine country escape', prompt: 'A quiet wine country escape for two' },
];

export function ConciergeConversation({ state, onSaved, onRecovery }: {
  state: MeridianShowcaseState;
  onSaved: () => void;
  onRecovery: () => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const followingRef = useRef(true);
  const lastUserCount = useRef(0);
  const lastMessageCount = useRef(0);
  const [hasMoreBelow, setHasMoreBelow] = useState(false);
  const userCount = state.messages.filter(message => message.role === 'user').length;
  const hasTurn = state.messages.length > 0;
  const profile = state.travelerProfile ?? state.previewProfile;
  const busy = state.isLoading || state.memoryLoading;
  const latestHold = state.tripHolds?.[state.tripHolds.length - 1];
  const dates = state.chatFilters.startDate
    ? new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric' }).format(new Date(`${state.chatFilters.startDate}T12:00:00`))
    : 'Dates not set';

  const updateScrollPosition = useCallback(() => {
    const pane = scrollRef.current;
    if (pane) {
      const below = pane.scrollHeight - pane.scrollTop - pane.clientHeight > 80;
      followingRef.current = !below;
      setHasMoreBelow(below);
    }
  }, []);

  useEffect(() => {
    const pane = scrollRef.current;
    if (!pane || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(updateScrollPosition);
    observer.observe(pane);
    const transcript = pane.querySelector('ol');
    if (transcript) observer.observe(transcript);
    return () => observer.disconnect();
  }, [updateScrollPosition]);

  useLayoutEffect(() => {
    const pane = scrollRef.current;
    const addedMessage = state.messages.length > lastMessageCount.current;
    if (pane && addedMessage && (followingRef.current || userCount > lastUserCount.current)) {
      const latest = pane.querySelector<HTMLElement>('.mc-message:last-child');
      if (latest) pane.scrollTop += latest.getBoundingClientRect().top - pane.getBoundingClientRect().top - 16;
    }
    // Reveal the start of a new answer once; updates must not pull a reader
    // away from the paragraph they are reading.
    updateScrollPosition();
    lastUserCount.current = userCount;
    lastMessageCount.current = state.messages.length;
  }, [state.messages, state.isLoading, state.error, userCount, updateScrollPosition]);

  return <section className={`mc-studio-conversation${hasTurn ? ' has-messages' : ''}`} aria-label="Your concierge">
    <header className="mc-studio-conversation-header">
      <div><h2>Your concierge</h2><p>{hasTurn ? 'A little planning. More to look forward to.' : 'Tell me what your next trip looks like.'}</p></div>
      {hasTurn && <button type="button" className="mc-text-button" onClick={state.clearChat} disabled={busy}>New chat</button>}
    </header>
    <div className="mc-studio-messages" ref={scrollRef} tabIndex={0} role="region" aria-label="Concierge responses"
      onScroll={updateScrollPosition}>
      <ol className="mc-conversation" aria-label="Conversation with Meridian" aria-live={state.isLoading ? "off" : "polite"} aria-relevant="additions text">
        {state.messages.map((message, index) => <li key={`${index}-${message.role}`} className={`mc-message is-${message.role}`}>
          <span className="mc-message-author">{message.role === 'user' ? 'You' : <>
            <ConciergeBell size={16} aria-hidden="true" />Meridian
            {message.streaming && <span className="mc-message-model">· Writing</span>}
            {message.incomplete && <span className="mc-message-model">· Incomplete response</span>}
            {message.modelLabel && <span className="mc-message-model">· {message.modelLabel}</span>}
          </>}</span>
          {message.role === 'user' ? <p>{message.text}</p> : <ShowcaseMarkdown source={message.text} />}
        </li>)}
      </ol>
      {!hasTurn && <div className="mc-studio-starters" aria-label="Trip ideas">
        {STARTERS.map(starter => <button type="button" key={starter.prompt} disabled={busy}
          onClick={() => void state.applyPhaseExample(starter.prompt, true, 4)} aria-label={starter.prompt}>
          <Search size={20} aria-hidden="true" /><span>{starter.label}</span><ArrowRight size={17} aria-hidden="true" />
        </button>)}
      </div>}
      {state.isLoading && <div className="mc-loading" role="status"><ConciergeBell size={18} aria-hidden="true" /><span>{state.chatProgress || 'Finding the right options for you…'}</span></div>}
      {state.error && <div className="mc-error" role="alert"><AlertTriangle size={19} aria-hidden="true" /><div><strong>A request needs attention.</strong><p>{state.error}</p></div>
        <button type="button" onClick={state.clearError} disabled={state.isLoading}><X size={15} aria-hidden="true" />Dismiss</button></div>}
      {latestHold && <div className="mc-studio-receipt"><TripHoldReceipt hold={latestHold} compact /></div>}
    </div>
    {hasMoreBelow && <button type="button" className="mc-text-button mc-studio-latest" onClick={() => {
      if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      updateScrollPosition();
    }}>Latest activity<ChevronDown size={16} aria-hidden="true" /></button>}
    <div className="mc-studio-context">
      <details className="mc-studio-brief">
        <summary><span>Your travel context</span><span>View travel brief<ChevronDown size={15} aria-hidden="true" /></span></summary>
        <div className="mc-studio-brief-body" role="region" aria-label="Travel brief details" tabIndex={0}><ConciergeRail state={state} onSaved={onSaved} onRecovery={onRecovery} /></div>
      </details>
      <div className="mc-studio-context-values">
        <span className="mc-studio-person"><img src={ALEX_IMAGE_URL} width="40" height="40" alt="" />Alex Morgan</span>
        <span><Plane size={20} aria-hidden="true" />{profile?.home_airport || 'Airport not set'}</span>
        <span><UsersRound size={20} aria-hidden="true" />{state.travelersCount ? `${state.travelersCount} ${state.travelersCount === 1 ? 'adult' : 'adults'}` : 'Party not set'}</span>
        <span><CalendarDays size={20} aria-hidden="true" />{dates}</span>
      </div>
      <button type="button" className="mc-studio-saved mc-text-button" onClick={onSaved}><Heart size={15} aria-hidden="true" />Saved trips{state.savedTrips.length > 0 ? ` (${state.savedTrips.length})` : ''}<ArrowRight size={14} aria-hidden="true" /></button>
    </div>
    <div className="mc-studio-compose" id="concierge-compose"><ChatComposer state={state} conciergeMode hideStarters /></div>
  </section>;
}
