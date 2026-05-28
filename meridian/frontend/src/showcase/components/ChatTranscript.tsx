import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { Message, Product } from '../../types';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';

// Visible turn cap. The transcript supports unlimited history (we keep
// every turn in state), but at any moment we only render the most recent
// N pairs so the chat stays performant during long stage walk-throughs.
// Older turns simply scroll up off-screen as the user adds new turns.
const VISIBLE_TURN_LIMIT = 24;

function findScrollParent(node: HTMLElement | null): HTMLElement | null {
  let cur: HTMLElement | null = node?.parentElement ?? null;
  while (cur) {
    const style = window.getComputedStyle(cur);
    const oy = style.overflowY;
    if ((oy === 'auto' || oy === 'scroll') && cur.scrollHeight > cur.clientHeight) {
      return cur;
    }
    cur = cur.parentElement;
  }
  return null;
}

export function ChatTranscript({ state, compact = false }: { state: MeridianShowcaseState; compact?: boolean }) {
  const visibleMessages = compact
    ? state.messages.slice(-3)
    : state.messages.slice(-VISIBLE_TURN_LIMIT);
  const containerRef = useRef<HTMLDivElement>(null);
  const lastMessageCountRef = useRef(visibleMessages.length);

  // Smooth auto-scroll whenever a new turn arrives. Since the chat
  // transcript no longer owns its own scrollbar (the whole main column
  // scrolls as one), walk up to find the nearest scrollable ancestor and
  // scroll *that* to the bottom.
  useLayoutEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    if (visibleMessages.length === lastMessageCountRef.current) return;
    lastMessageCountRef.current = visibleMessages.length;

    const scroller = findScrollParent(container);
    if (!scroller) return;
    const distanceFromBottom =
      scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight;
    if (distanceFromBottom < 240) {
      scroller.scrollTo({ top: scroller.scrollHeight, behavior: 'smooth' });
    }
  }, [visibleMessages.length]);

  return (
    <div ref={containerRef} className={`mds-chat-transcript${compact ? ' is-compact' : ''}`} aria-live="polite">
      {visibleMessages.length === 0 && !state.isLoading ? (
        <div className="mds-empty">Start with a trip idea to wake up the concierge.</div>
      ) : (
        visibleMessages.map((message, index) => (
          <ChatMessage
            key={`${message.role}-${index}-${message.text.slice(0, 12)}`}
            message={message}
            state={state}
            isLatestBot={
              message.role === 'bot' &&
              index === visibleMessages.length - 1 &&
              !state.isLoading
            }
          />
        ))
      )}
      {state.isLoading && (
        <div className="mds-message bot">
          <div className="mds-message-role">Meridian</div>
          <div className="mds-message-bubble">
            <span className="mds-running-dot" />
            Running tools and composing...
          </div>
        </div>
      )}
    </div>
  );
}

function ChatMessage({
  message,
  isLatestBot,
  state,
}: {
  message: Message;
  isLatestBot: boolean;
  state: MeridianShowcaseState;
}) {
  // Only the most-recent bot turn typewriter-streams; older turns render
  // their full text immediately so revisiting history is fast.
  const text = message.text ?? '';
  const useTypewriter = isLatestBot && text.length > 0 && text.length < 800;
  const visible = useTypewriter ? useTypewriterReveal(text) : text;
  // Only show the streaming caret once a few characters are revealed AND
  // the stream still has more to go - a caret floating in an empty bubble
  // looks abrupt.
  const isEmptyStream = useTypewriter && visible.length === 0;
  const hasInlineProducts = message.role === 'bot' && (message.products?.length ?? 0) > 0;
  const bubbleClass = `mds-message-bubble${hasInlineProducts ? ' has-products' : ''}`;
  const wrapperClass = `mds-message ${message.role}${hasInlineProducts ? ' has-products' : ''}`;

  return (
    <div className={wrapperClass}>
      <div className="mds-message-role">{message.role === 'user' ? 'Alex' : 'Meridian'}</div>
      {!isEmptyStream && (
        <div className={bubbleClass}>
          <span className="mds-message-text">{visible || ' '}</span>
          {hasInlineProducts && message.products && (
            <ProductSummaryChip products={message.products} state={state} />
          )}
        </div>
      )}
    </div>
  );
}

function money(price: number): string {
  return `$${price.toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
}

// Compact summary chip - "4 trips · $1,599 to $1,899 · jump to results".
// Anchored inside the bot bubble so prior-turn results stay attached to
// chat history without re-painting full thumbnails (the rich grid below
// the chat already shows the active turn's products).
function ProductSummaryChip({
  products,
  state,
}: {
  products: Product[];
  state: MeridianShowcaseState;
}) {
  const prices = products.map((p) => p.price).filter((n) => Number.isFinite(n));
  const minPrice = prices.length ? Math.min(...prices) : null;
  const maxPrice = prices.length ? Math.max(...prices) : null;
  const priceRange =
    minPrice != null && maxPrice != null
      ? minPrice === maxPrice
        ? money(minPrice)
        : `${money(minPrice)} – ${money(maxPrice)}`
      : null;
  const isActive = state.recommendations[0]?.product_id === products[0]?.product_id;

  return (
    <button
      type="button"
      className={`mds-msg-result-chip${isActive ? ' is-active' : ''}`}
      onClick={() => state.setSelectedTrip(products[0] ?? null)}
      title={isActive ? 'These results are showing below' : 'Pin this turn back to focus'}
    >
      <span className="mds-msg-result-chip-icon" aria-hidden="true">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 7h18" />
          <path d="M3 12h18" />
          <path d="M3 17h12" />
        </svg>
      </span>
      <span className="mds-msg-result-chip-text">
        <b>{products.length} {products.length === 1 ? 'trip' : 'trips'}</b>
        {priceRange && <span> · {priceRange}</span>}
      </span>
      {isActive && <span className="mds-msg-result-chip-dot" aria-hidden="true" />}
    </button>
  );
}

// Reveal the text one character (or grapheme-ish chunk) at a time so the
// chat reads as a flowing stream rather than a hard pop. Always reveals
// to completion - never gets stuck at the seed even under StrictMode's
// double-mount-and-cleanup in dev (the previous lastTextRef short-circuit
// would skip the second mount and leave the bubble stuck at "I f").
function useTypewriterReveal(text: string): string {
  // Start with the first 3 chars already revealed so the bubble pops in
  // *with content*, not as an empty rectangle.
  const initial = text.slice(0, Math.min(3, text.length));
  const [visible, setVisible] = useState(initial);

  useEffect(() => {
    if (!text) {
      setVisible('');
      return undefined;
    }

    const seed = text.slice(0, Math.min(3, text.length));
    setVisible(seed);
    if (seed.length >= text.length) return undefined;

    const totalDurationMs = Math.min(1700, Math.max(420, text.length * 18));
    const stepMs = 28;
    const steps = Math.max(1, Math.floor(totalDurationMs / stepMs));
    const charsPerStep = Math.max(1, Math.ceil(text.length / steps));

    let cursor = seed.length;
    const id = window.setInterval(() => {
      cursor = Math.min(text.length, cursor + charsPerStep);
      setVisible(text.slice(0, cursor));
      if (cursor >= text.length) {
        window.clearInterval(id);
      }
    }, stepMs);

    // Failsafe: even if the interval is interrupted (StrictMode cleanup,
    // tab backgrounding, etc.), the full text lands within the animation
    // budget + a small grace period.
    const failsafe = window.setTimeout(() => {
      setVisible(text);
      window.clearInterval(id);
    }, totalDurationMs + 200);

    return () => {
      window.clearInterval(id);
      window.clearTimeout(failsafe);
    };
  }, [text]);

  return visible;
}
