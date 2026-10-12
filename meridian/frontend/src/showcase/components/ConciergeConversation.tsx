import { memo, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { AlertTriangle, ArrowRight, CalendarDays, ChevronDown, Heart, Loader2, Plane, Search, UsersRound, X } from 'lucide-react';
import { ChatComposer } from './ChatComposer';
import { ShowcaseMarkdown } from './ChatTranscript';
import { ConciergeBell } from '../icons/TravelIcons';
import { ConciergeRail } from '../surfaces/ConciergeRail';
import { TripHoldReceipt } from './TripHoldReceipt';
import { TravelerAvatar } from './TravelerAvatar';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import type { Message } from '../../types';
import { useStreamingText } from '../hooks/useStreamingText';

const STARTERS = [
  { label: 'A culture trip to Tokyo', prompt: 'Help me plan a culture trip to Tokyo' },
  { label: 'A quiet wine country escape', prompt: 'A quiet wine country escape for two' },
];
const ResponseMarkdown = memo(ShowcaseMarkdown);

const ConciergeReply = memo(function ConciergeReply({ message, progress, latest }: { message: Message; progress?: string; latest: boolean }) {
  const text = useStreamingText(message.text, Boolean(message.streaming), message.incomplete);
  const writing = Boolean(message.streaming) || text !== message.text;
  return <>
    <span className="mc-message-author">
      {writing ? <Loader2 className="mc-response-spinner" size={16} aria-hidden="true" /> : <ConciergeBell size={16} aria-hidden="true" />}
      Meridian
      {writing && <span className="mc-message-model mc-response-status" role="status">{progress || 'Writing your reply…'}</span>}
      {message.incomplete && <span className="mc-message-model">Incomplete response</span>}
      {!writing && message.modelLabel && <span className="mc-message-model">{message.modelLabel}</span>}
    </span>
    <div className={`mc-response-body${writing ? ' is-streaming' : ''}`} aria-busy={writing} aria-live={latest ? 'polite' : 'off'} aria-atomic="true">
      {text && <ResponseMarkdown source={text} />}
    </div>
  </>;
});

export function ConciergeConversation({ state, onSaved, onRecovery, onReviewRecovery, onPreferences, openTravelBrief, onTravelBriefOpened }: {
  state: MeridianShowcaseState;
  onSaved: () => void;
  onRecovery: () => void;
  onReviewRecovery?: () => void;
  onPreferences?: () => void;
  openTravelBrief?: boolean;
  onTravelBriefOpened?: () => void;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const briefRef = useRef<HTMLDetailsElement>(null);
  const followingRef = useRef(true);
  const scrollTopRef = useRef(0);
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

  useEffect(() => {
    if (!openTravelBrief || !briefRef.current) return;
    briefRef.current.open = true;
    briefRef.current.querySelector('summary')?.focus({ preventScroll: true });
    briefRef.current.scrollIntoView?.({ block: 'nearest' });
    onTravelBriefOpened?.();
  }, [openTravelBrief, onTravelBriefOpened]);

  const measureScrollPosition = useCallback(() => {
    const pane = scrollRef.current;
    const below = Boolean(pane && pane.scrollHeight - pane.scrollTop - pane.clientHeight > 80);
    setHasMoreBelow(below);
    return below;
  }, []);

  const updateScrollPosition = useCallback(() => {
    const pane = scrollRef.current;
    if (!pane) return;
    const below = measureScrollPosition();
    // A queued scroll event can arrive after the next text chunk grew. Only
    // an actual upward movement means the reader left the live edge.
    if (pane.scrollTop < scrollTopRef.current - 1) followingRef.current = false;
    else if (!below) followingRef.current = true;
    scrollTopRef.current = pane.scrollTop;
  }, [measureScrollPosition]);

  useEffect(() => {
    const pane = scrollRef.current;
    if (!pane || typeof ResizeObserver === 'undefined') return;
    let frame = 0;
    const observer = new ResizeObserver(() => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        if (followingRef.current) {
          pane.scrollTop = pane.scrollHeight;
          scrollTopRef.current = pane.scrollTop;
        }
        measureScrollPosition();
      });
    });
    observer.observe(pane);
    const transcript = pane.querySelector('ol');
    if (transcript) observer.observe(transcript);
    return () => { cancelAnimationFrame(frame); observer.disconnect(); };
  }, [measureScrollPosition]);

  useLayoutEffect(() => {
    const pane = scrollRef.current;
    const addedMessage = state.messages.length > lastMessageCount.current;
    if (pane && addedMessage && (followingRef.current || userCount > lastUserCount.current)) {
      const latest = pane.querySelector<HTMLElement>('.mc-message:last-child');
      if (latest) pane.scrollTop += latest.getBoundingClientRect().top - pane.getBoundingClientRect().top - 16;
      scrollTopRef.current = pane.scrollTop;
      followingRef.current = !measureScrollPosition();
    }
    // Growth follows only a reader who stayed at the end. Measuring growth
    // must not overwrite that intent before ResizeObserver can follow it.
    measureScrollPosition();
    lastUserCount.current = userCount;
    lastMessageCount.current = state.messages.length;
  }, [state.messages, state.isLoading, state.error, userCount, measureScrollPosition]);

  const lastMessage = state.messages[state.messages.length - 1];
  // A hold or confirmation runs in the trip dialog; it is not a concierge reply.
  const messages = state.isLoading && !state.pendingWrite && !lastMessage?.streaming
    ? [...state.messages, { role: 'bot' as const, text: '', streaming: true }]
    : state.messages;

  return <section className={`mc-studio-conversation${hasTurn ? ' has-messages' : ''}`} aria-label="Your concierge">
    <header className="mc-studio-conversation-header">
      <div><h2>Your concierge</h2><p>{hasTurn ? 'A little planning. More to look forward to.' : 'Tell me what your next trip looks like.'}</p></div>
      {hasTurn && <button type="button" className="mc-text-button" onClick={state.clearChat} disabled={busy}>New chat</button>}
    </header>
    <div className="mc-studio-messages" ref={scrollRef} tabIndex={0} role="region" aria-label="Concierge responses"
      onScroll={updateScrollPosition}>
      <ol className="mc-conversation" aria-label="Conversation with Meridian">
        {messages.map((message, index) => <li key={`${index}-${message.role}`} className={`mc-message is-${message.role}`}>
          {message.role === 'user' ? <><span className="mc-message-author">You</span><p>{message.text}</p></>
            : <ConciergeReply message={message} progress={index === messages.length - 1 ? state.chatProgress : undefined} latest={index === messages.length - 1} />}
        </li>)}
      </ol>
      {state.recoveryRequest && !state.isLoading && onReviewRecovery && <button type="button" className="mc-text-button" onClick={onReviewRecovery}>
        Review recovery plan<ArrowRight size={16} aria-hidden="true" />
      </button>}
      {!hasTurn && <div className="mc-studio-starters" aria-label="Trip ideas">
        {STARTERS.map(starter => <button type="button" key={starter.prompt} disabled={busy}
          onClick={() => void state.applyPhaseExample(starter.prompt, true, 4)} aria-label={starter.prompt}>
          <Search size={20} aria-hidden="true" /><span>{starter.label}</span><ArrowRight size={17} aria-hidden="true" />
        </button>)}
      </div>}
      {state.error && <div className="mc-error" role="alert"><AlertTriangle size={19} aria-hidden="true" /><div><strong>A request needs attention.</strong><p>{state.error}</p></div>
        <button type="button" onClick={state.clearError} disabled={state.isLoading}><X size={15} aria-hidden="true" />Dismiss</button></div>}
      {latestHold && <div className="mc-studio-receipt"><TripHoldReceipt hold={latestHold} compact /></div>}
    </div>
    {hasMoreBelow && <button type="button" className="mc-text-button mc-studio-latest" onClick={() => {
      if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
      updateScrollPosition();
    }}>Latest activity<ChevronDown size={16} aria-hidden="true" /></button>}
    <div className="mc-studio-context">
      <details className="mc-studio-brief" ref={briefRef}>
        <summary><span>Your travel context</span><span>View travel brief<ChevronDown size={15} aria-hidden="true" /></span></summary>
        <div className="mc-studio-brief-body" role="region" aria-label="Travel brief details" tabIndex={0}><ConciergeRail state={state} onSaved={onSaved} onRecovery={onRecovery} onPreferences={onPreferences} /></div>
      </details>
      <div className="mc-studio-context-values">
        <span className="mc-studio-person"><TravelerAvatar traveler={state.traveler} width={40} height={40} />{state.traveler.name}</span>
        <span><Plane size={20} aria-hidden="true" />{profile?.home_airport || 'Airport not set'}</span>
        <span><UsersRound size={20} aria-hidden="true" />{state.travelersCount ? `${state.travelersCount} ${state.travelersCount === 1 ? 'adult' : 'adults'}` : 'Party not set'}</span>
        <span><CalendarDays size={20} aria-hidden="true" />{dates}</span>
      </div>
      <button type="button" className="mc-studio-saved mc-text-button" onClick={onSaved}><Heart size={15} aria-hidden="true" />Saved trips{state.savedTrips.length > 0 ? ` (${state.savedTrips.length})` : ''}<ArrowRight size={14} aria-hidden="true" /></button>
    </div>
    <div className="mc-studio-compose" id="concierge-compose"><ChatComposer state={state} conciergeMode hideStarters /></div>
  </section>;
}
