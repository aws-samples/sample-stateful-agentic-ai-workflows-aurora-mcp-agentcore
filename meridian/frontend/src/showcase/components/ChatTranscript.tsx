import type { ReactNode } from 'react';
import { Component, useEffect, useLayoutEffect, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Message, Product } from '../../types';
import type { MeridianShowcaseState } from '../hooks/useMeridianShowcase';
import { TripVisual } from './TripVisual';

// Tiny error boundary so a markdown render crash doesn't take down the
// whole transcript. Falls back to plain text when react-markdown chokes
// on a malformed input from the model.
class MarkdownBoundary extends Component<
  { children: ReactNode; fallback: ReactNode },
  { error: boolean }
> {
  state = { error: false };
  static getDerivedStateFromError() {
    return { error: true };
  }
  componentDidCatch(error: unknown) {
    console.warn('[ChatTranscript] markdown render failed', error);
  }
  render() {
    return this.state.error ? this.props.fallback : this.props.children;
  }
}

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
        <div className="mds-empty-state">
          <div className="mds-empty-title">Ask me about your next trip.</div>
          <div className="mds-empty-sub">
            I'm running in <b>{state.phaseLabel}</b> mode. Try a starter below, or type your own.
          </div>
          {state.phaseExamples.length > 0 && (
            <button
              type="button"
              className="mds-empty-starter"
              onClick={() => void state.applyPhaseExample(state.phaseExamples[0], true)}
              disabled={state.isLoading}
            >
              {state.phaseExamples[0]}
            </button>
          )}
        </div>
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
  // RULES OF HOOKS: useTypewriterReveal must be called on every render
  // (you can't conditionally skip a hook). We always call it; the
  // `useTypewriter` flag decides whether to *show* the streaming output
  // or the static text. The hook itself runs unconditionally so React
  // sees the same hook order every render and never throws
  // "Cannot read properties of undefined (reading 'length')".
  const text = message.text ?? '';
  // Stream every latest bot turn regardless of length. The reveal hook
  // self-caps the duration internally, so even a 2000-char reply lands
  // in roughly 4-6 seconds — closer to ChatGPT's perceived cadence than
  // a hard pop. Older turns render their full text immediately so
  // scrolling history stays fast.
  const useTypewriter = isLatestBot && text.length > 0;
  const streamed = useTypewriterReveal(text);
  const visible = useTypewriter ? streamed : text;
  // Only show the streaming caret once a few characters are revealed AND
  // the stream still has more to go - a caret floating in an empty bubble
  // looks abrupt.
  const isEmptyStream = useTypewriter && visible.length === 0;

  // Notify the hook when the latest bot turn finishes revealing so the
  // recommendation grid can fade in once the message reads as complete.
  // Non-typewriter messages are considered complete immediately.
  const { markLatestStreamComplete } = state;
  useEffect(() => {
    if (!isLatestBot) return;
    if (message.role !== 'bot') return;
    if (!useTypewriter) {
      markLatestStreamComplete();
      return;
    }
    if (visible.length >= text.length && text.length > 0) {
      markLatestStreamComplete();
    }
  }, [
    isLatestBot,
    message.role,
    useTypewriter,
    visible.length,
    text.length,
    markLatestStreamComplete,
  ]);
  const hasInlineProducts = message.role === 'bot' && (message.products?.length ?? 0) > 0;
  // Per-turn expand state. Each bot bubble owns whether its product
  // cards are expanded inline - so old turn results stay attached to
  // the message that produced them and don't vanish when a later turn
  // arrives. Default expanded for the most recent bot turn (so the
  // first thing the user sees on a fresh reply is the cards), and
  // collapsed for older history (so the transcript stays scannable).
  const [expanded, setExpanded] = useState<boolean>(isLatestBot);
  // When a turn becomes the latest (e.g. the current latest just got
  // bumped by a newer bot reply), default older turns to collapsed so
  // the chat history doesn't become a wall of cards.
  useEffect(() => {
    if (!isLatestBot) setExpanded(false);
  }, [isLatestBot]);

  // Gate the summary chip + inline grid for THIS turn until either:
  //   (a) it's an older turn (already revealed),
  //   (b) the typewriter on this latest turn has fully revealed the text.
  // Without this gate, the cards animate in alongside (or even before)
  // the streaming text, making the images feel ahead of the reply.
  // For older turns we always show the chip — they've long since streamed.
  const productsRevealed = !isLatestBot || state.latestStreamComplete;

  const bubbleClass = `mds-message-bubble${hasInlineProducts ? ' has-products' : ''}`;
  const wrapperClass = `mds-message ${message.role}${hasInlineProducts ? ' has-products' : ''}`;

  return (
    <div className={wrapperClass}>
      <div className="mds-message-role">{message.role === 'user' ? 'Alex' : 'Meridian'}</div>
      {!isEmptyStream && (
        <div className={bubbleClass}>
          {message.role === 'bot' ? (
            <MarkdownText source={visible || ' '} />
          ) : (
            <span className="mds-message-text">{visible || ' '}</span>
          )}
          {hasInlineProducts && message.products && productsRevealed && (
            <>
              <ResponseMetaTags state={state} />
              <ProductSummaryChip
                products={message.products}
                state={state}
                expanded={expanded}
                onToggle={() => setExpanded((prev) => !prev)}
              />
              {expanded && (
                <InlineProductGrid products={message.products} state={state} />
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

// Bot replies render through react-markdown + remark-gfm so the
// concierge's `**bold**`, bullet lists, ordered lists, blockquotes,
// inline code, and tables all parse to real DOM nodes - the same
// stack ChatGPT / Claude.ai / Linear use.
//
// We strip emoji codepoints from the source on the way in: the
// system prompt asks the LLM not to emit them, this is a final
// safety net so any emoji that slipped through never reaches the DOM.
function MarkdownText({ source }: { source: string }) {
  const cleaned = stripEmojis(source);
  return (
    <div className="mds-message-text mds-md">
      <MarkdownBoundary
        fallback={<p style={{ whiteSpace: 'pre-wrap', margin: 0 }}>{cleaned}</p>}
      >
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          // Disallow raw HTML so an LLM-injected <script> can't reach
          // the DOM. react-markdown defaults to escaping it, but we
          // make the contract explicit here.
          skipHtml
        >
          {cleaned}
        </ReactMarkdown>
      </MarkdownBoundary>
    </div>
  );
}

// Strip emoji + pictograph characters from the markdown source. The
// system prompt asks the LLM not to emit them; this is a final safety
// net so any that slip through never reach the DOM. Uses the standard
// Unicode "Emoji" property (\p{Emoji}) plus ZWJ and variation selectors
// that combine multi-char emoji sequences.
//
// Some "Emoji" code points are also normal punctuation (#, *, digits,
// etc.) - the {Emoji_Presentation} property excludes those, so a literal
// '#' in a markdown heading isn't accidentally stripped. We OR in the
// extended pictographic set to catch newer / less-common pictographs.
function stripEmojis(source: string): string {
  try {
    return source
      .replace(/[\p{Extended_Pictographic}\p{Emoji_Presentation}]/gu, '')
      .replace(/\u200D/g, '') // zero-width joiner
      .replace(/\uFE0F/g, '') // variation selector-16
      .replace(/  +/g, ' ');
  } catch {
    // Older runtimes without Unicode property escape support fall back
    // to a no-op rather than crashing the whole component tree.
    return source;
  }
}

function money(price: number): string {
  return `$${price.toLocaleString('en-US', { maximumFractionDigits: 0 })}`;
}

// Compact summary chip - "4 trips · $1,599 to $1,899 · jump to results".
// Anchored inside the bot bubble so prior-turn results stay attached to
// chat history without re-painting full thumbnails (the rich grid below
// Inline "trace receipt" under the concierge reply (mockup parity). Every tag
// is phase-aware so the receipt names exactly what the current phase's
// architecture does — the demo's whole thesis is that each phase composes a
// new capability onto the last, and the receipt must never over-claim:
//
//   Phase 1 SQL        → "Direct SQL"            (RDS Data API; no tools, no memory)
//   Phase 2 MCP        → "MCP tools"             (catalog reached through MCP)
//   Phase 3 Retrieval  → "Hybrid retrieval · reranked" (pgvector + Cohere) —
//                        STILL no memory; that gap is the motivator for Phase 4
//   Phase 4 Production  → "memory: N prefs"       (AgentCore memory invoked here)
//   Phase 5 Workflow    → "LangGraph · checkpointed" + memory (recall node)
//
// The capability label is keyed to phase (not parsed from span categories,
// which vary by query path); the prefs count + latency are read from live
// state. memory only appears at phase >= 4 — never before the phase that
// introduces it, even though memoryFacts may already be loaded in the rail.
const PHASE_CAPABILITY: Record<number, string> = {
  1: 'Direct SQL',
  2: 'MCP tools',
  3: 'Hybrid retrieval · reranked',
  4: 'AgentCore memory',
  5: 'LangGraph · checkpointed',
};

function ResponseMetaTags({ state }: { state: MeridianShowcaseState }) {
  const phase = state.selectedPhase;
  const capability = PHASE_CAPABILITY[phase];
  const prefs = phase >= 4 ? state.memoryFacts.length : 0;
  const latency = state.totalLatencyMs;
  const showMemory = phase >= 4 && prefs > 0;

  if (!capability && !showMemory && !latency) return null;

  return (
    <div className="mds-msg-meta" aria-label="Trace summary">
      {capability && (
        <span className="mds-msg-meta-tag">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M12 2 4 7v6c0 5 3.5 7.5 8 9 4.5-1.5 8-4 8-9V7l-8-5Z" />
          </svg>
          {capability}
        </span>
      )}
      {showMemory && (
        <span className="mds-msg-meta-tag">memory: {prefs} {prefs === 1 ? 'pref' : 'prefs'}</span>
      )}
      {!!latency && <span className="mds-msg-meta-tag">{latency}ms</span>}
    </div>
  );
}

// the chat already shows the active turn's products).
function ProductSummaryChip({
  products,
  state,
  expanded,
  onToggle,
}: {
  products: Product[];
  state: MeridianShowcaseState;
  expanded: boolean;
  onToggle: () => void;
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

  return (
    <button
      type="button"
      className={`mds-msg-result-chip${expanded ? ' is-active' : ''}`}
      onClick={() => {
        // Pin the first product on expand so the right-rail trip
        // detail drawer reflects this turn's results, then toggle.
        if (!expanded && products[0]) state.setSelectedTrip(products[0]);
        onToggle();
      }}
      title={expanded ? 'Hide these trips' : 'Show these trips'}
      aria-expanded={expanded}
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
      <span className="mds-msg-result-chip-caret" aria-hidden="true">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
          <path d="M6 9l6 6 6-6" />
        </svg>
      </span>
    </button>
  );
}

// Inline product grid that slides out below the summary chip when the
// user expands the bubble. Renders the per-turn products attached to
// THIS bot message - independent of state.recommendations - so older
// turns retain their cards even after later turns swap the active set.
function InlineProductGrid({
  products,
  state,
}: {
  products: Product[];
  state: MeridianShowcaseState;
}) {
  return (
    <div className="mds-msg-grid" role="region" aria-label="Trips for this turn">
      {products.map((product, index) => (
        <InlineProductCard
          key={product.product_id}
          product={product}
          state={state}
          index={index}
        />
      ))}
    </div>
  );
}

function InlineProductCard({
  product,
  state,
  index,
}: {
  product: Product;
  state: MeridianShowcaseState;
  index: number;
}) {
  const matchPct =
    product.similarity != null ? Math.round(product.similarity * 100) : null;
  const saved = state.savedTripIds.has(product.product_id);
  const selected = state.selectedTrip?.product_id === product.product_id;
  return (
    <article
      className={`mds-msg-card${selected ? ' is-selected' : ''}`}
      style={{ animationDelay: `${Math.min(index * 60, 360)}ms` }}
      tabIndex={0}
      role="button"
      onClick={() => state.selectTrip(product)}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          state.selectTrip(product);
        }
      }}
    >
      <span className="mds-msg-card-img" aria-hidden="true">
        <TripVisual product={product} compact />
      </span>
      <span className="mds-msg-card-fade" aria-hidden="true" />
      <span className="mds-msg-card-overlay">
        {matchPct != null && (
          <span className="mds-msg-card-match">
            <span className="mds-msg-card-match-dot" aria-hidden="true" />
            {matchPct}% match
          </span>
        )}
        <span className="mds-msg-card-title">{product.name}</span>
        <span className="mds-msg-card-sub">{product.brand}</span>
        <span className="mds-msg-card-row">
          <span className="mds-msg-card-price">
            <span>From</span>
            <b>{money(product.price)}</b>
          </span>
          <span
            className="mds-msg-card-actions"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              type="button"
              onClick={() => state.holdTrip(product)}
              disabled={state.isLoading}
            >
              Hold
            </button>
            <button
              type="button"
              onClick={() => state.planTrip(product)}
              disabled={state.isLoading}
            >
              Plan
            </button>
            <button
              type="button"
              onClick={() => state.saveTrip(product)}
              aria-pressed={saved}
            >
              {saved ? 'Saved' : 'Save'}
            </button>
          </span>
        </span>
      </span>
    </article>
  );
}

// Reveal the text one character (or small chunk) at a time so the chat
// reads as a left-to-right stream rather than a hard pop. Cadence is
// tuned to match ChatGPT / Claude.ai's perceived feel: ~3 chars per
// 30ms tick → ~100 chars/sec, which is fast enough for a 600-char
// reply to finish in ~6 seconds without dragging on a 2000-char one.
//
// We deliberately do NOT clamp duration to a fixed budget — that's what
// produced the old "long replies just block-paint" behavior. Instead we
// keep the per-tick rate constant so longer replies stream proportionally
// longer (and shorter replies finish quickly). A 6-second hard ceiling
// catches edge cases (3000+ char replies) so the animation can't drag
// the demo to a halt.
function useTypewriterReveal(text: string): string {
  // Start with the first 2 chars already revealed so the bubble pops in
  // *with content*, not as an empty rectangle. The first chunk arriving
  // immediately is what makes the stream feel alive on slow renders.
  const initial = text.slice(0, Math.min(2, text.length));
  const [visible, setVisible] = useState(initial);

  useEffect(() => {
    if (!text) {
      setVisible('');
      return undefined;
    }

    const seed = text.slice(0, Math.min(2, text.length));
    setVisible(seed);
    if (seed.length >= text.length) return undefined;

    // ChatGPT-ish cadence: 3 chars per 30ms tick = ~100 cps perceived.
    // Hard ceiling at 6s so a wildly long reply doesn't drag forever.
    const stepMs = 30;
    const charsPerStep = 3;
    const ceilingMs = 6000;

    let cursor = seed.length;
    const id = window.setInterval(() => {
      cursor = Math.min(text.length, cursor + charsPerStep);
      setVisible(text.slice(0, cursor));
      if (cursor >= text.length) {
        window.clearInterval(id);
      }
    }, stepMs);

    // Failsafe: even if the interval is interrupted (StrictMode cleanup,
    // tab backgrounding, very long text), the full text lands within
    // the ceiling. Calculate the natural finish time for this length
    // first, then take the smaller of (natural, ceiling).
    const naturalDurationMs = Math.ceil(text.length / charsPerStep) * stepMs;
    const failsafeMs = Math.min(naturalDurationMs + 200, ceilingMs);
    const failsafe = window.setTimeout(() => {
      setVisible(text);
      window.clearInterval(id);
    }, failsafeMs);

    return () => {
      window.clearInterval(id);
      window.clearTimeout(failsafe);
    };
  }, [text]);

  return visible;
}
