/**
 * AgentSection — Meridian Pro 3-pane workspace
 *
 * Left rail: traveler card · run config · starters
 * Center: chat with inline reasoning + recommendation grid + composer
 * Right: Gantt-style trace timeline with tabs (spans / memory / sql / cost)
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { FadeIn } from '../components/FadeIn';
import { GanttSpan } from '../components/GanttSpan';
import { DEMO_TRAVELER_ID, DEMO_PERSONA_FALLBACK } from '../components/TravelerPersona';
import { ProductThumb } from '../components/ProductThumb';
import { useAgentBridge } from '../context/AgentBridge';
import { enrichTraceActivities } from '../utils/traceTelemetry';
import { fetchMemoryProfile, sendChatMessage, processOrder } from '../api/client';
import type { ActivityEntry, LongTermMemoryFact, Message, Phase, Product } from '../types';

const PHASE_LABELS: Record<Phase, string> = {
  1: 'SQL',
  2: 'MCP',
  3: 'Retrieval',
  4: 'Memory',
  5: 'Orchestration',
};

const PHASE_INFO: Record<Phase, {
  beat: string;
  capabilities: string[];
  starters: string[];
  highlight?: string;
}> = {
  1: {
    beat: 'Plain SQL filters on trip_packages via the RDS Data API.',
    capabilities: ['Trip type filter', 'Operator filter', 'Price filter'],
    starters: ['City breaks', 'Beach & Resort', 'Business travel under $1500'],
    highlight: 'Romantic week in Europe',
  },
  2: {
    beat: 'Same catalog queries — but tools are exposed via MCP.',
    capabilities: ['Trip type filter', 'Operator filter', 'MCP run_query'],
    starters: ['Adventure & Outdoors', 'Wellness & Luxury', 'Tokyo culture trip'],
    highlight: 'Beach vacation with snorkeling',
  },
  3: {
    beat: 'Hybrid pgvector + tsvector — vague requests resolve to packages.',
    capabilities: ['Natural language', 'Hybrid ranking', 'Cohere v4'],
    starters: [
      'Weekend in Paris under $2k',
      'Is the Maldives package available?',
      'Family-friendly beach resort',
    ],
  },
  4: {
    beat: 'ConciergeOrchestrator + Strands @tool memory — grounded in Aurora.',
    capabilities: ['Traveler profile', 'Session + interactions', 'Strands @tool recall/persist'],
    starters: [
      'Tokyo trip for two in October',
      'Beach escape under $2500 — remember our food allergies',
      'What did we discuss last time about Iceland?',
    ],
  },
  5: {
    beat: 'LangGraph StateGraph — classify → search/availability/recall → synthesize, with checkpoints.',
    capabilities: ['Explicit edges', 'Checkpointed state', 'PostgresSaver / MemorySaver'],
    starters: [
      'Tokyo culture trip for two',
      'Is the Maldives package available?',
      'Remember what we discussed about Iceland',
    ],
  },
};

// Phase color for the composer chip + accent dots in pill row
const PHASE_COLOR: Record<Phase, string> = {
  1: 'var(--mp-p1)',
  2: 'var(--mp-p2)',
  3: 'var(--mp-p3)',
  4: 'var(--mp-p4)',
  5: 'var(--mp-p5, #6d28d9)',
};

interface ItineraryItem {
  product: Product;
  quantity: number;
  duration?: string;
}

const TRAVELER_TAGS_FALLBACK = ['Slow travel', 'Wine country', 'No red-eyes', 'Veg-friendly', 'Boutique'];

export function AgentSection() {
  const { register } = useAgentBridge();
  const [phase, setPhase] = useState<Phase>(4);
  const [msgs, setMsgs] = useState<Message[]>([]);
  const [acts, setActs] = useState<ActivityEntry[]>([]);
  const [pendingActs, setPendingActs] = useState<ActivityEntry[]>([]);
  const [currentStep, setCurrentStep] = useState<number>(-1);
  const [input, setInput] = useState('');
  const [typing, setTyping] = useState(false);
  const [followUps, setFollowUps] = useState<string[]>([]);
  const [connectionStatus, setConnectionStatus] = useState<'connected' | 'disconnected' | 'checking'>('checking');
  const [traceId, setTraceId] = useState<string | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [memoryFacts, setMemoryFacts] = useState<LongTermMemoryFact[]>([]);
  const [, setItinerary] = useState<ItineraryItem[]>([]);
  const [activeTraceTab, setActiveTraceTab] = useState<'spans' | 'memory' | 'sql' | 'cost'>('spans');

  const chatEnd = useRef<HTMLDivElement>(null);
  const composerRef = useRef<HTMLInputElement>(null);
  const activityTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const lastUserTextRef = useRef<string | null>(null);
  const [travelerTags, setTravelerTags] = useState<string[]>(TRAVELER_TAGS_FALLBACK);

  const currentPhase = PHASE_INFO[phase];

  const ensureTraceId = (): string => {
    if (traceId) return traceId;
    const id = `tr_${Math.random().toString(36).slice(2, 8)}`;
    setTraceId(id);
    return id;
  };

  useEffect(() => {
    fetchMemoryProfile(DEMO_TRAVELER_ID)
      .then((res) => {
        if (res.facts?.length) {
          setMemoryFacts(res.facts);
          const tags = res.facts
            .slice(0, 5)
            .map((f) => (f.value.length > 28 ? `${f.value.slice(0, 26)}…` : f.value));
          if (tags.length) setTravelerTags(tags);
        }
      })
      .catch(() => {});
  }, []);

  // Backend health
  useEffect(() => {
    const check = async () => {
      try {
        const res = await fetch('http://localhost:8000/health');
        setConnectionStatus(res.ok ? 'connected' : 'disconnected');
      } catch {
        setConnectionStatus('disconnected');
      }
    };
    check();
    const t = setInterval(check, 30000);
    return () => clearInterval(t);
  }, []);

  // Phase delays
  const phaseDelays: Record<Phase, number> = { 1: 600, 2: 450, 3: 350, 4: 300, 5: 300 };

  // Auto-scroll on message changes
  const prevMsgCount = useRef(0);
  const wasTyping = useRef(false);
  useEffect(() => {
    if (msgs.length > prevMsgCount.current) {
      chatEnd.current?.scrollIntoView({ behavior: 'smooth' });
    }
    prevMsgCount.current = msgs.length;
  }, [msgs]);
  useEffect(() => {
    if (typing && !wasTyping.current && msgs.length > 0) {
      chatEnd.current?.scrollIntoView({ behavior: 'smooth' });
    }
    wasTyping.current = typing;
  }, [typing, msgs.length]);

  const revealActivitiesProgressively = (
    activities: ActivityEntry[],
    onComplete: () => void,
  ) => {
    if (activities.length === 0) {
      onComplete();
      return;
    }
    const delay = phaseDelays[phase];
    let index = 0;
    setActs([activities[0]]);
    setCurrentStep(0);
    setPendingActs(activities.slice(1));
    index = 1;

    const showNext = () => {
      if (index < activities.length) {
        const next = activities[index];
        setActs((prev) => [...prev, next]);
        setCurrentStep(index);
        setPendingActs(activities.slice(index + 1));
        index++;
        activityTimerRef.current = setTimeout(showNext, delay);
      } else {
        setCurrentStep(-1);
        setPendingActs([]);
        onComplete();
      }
    };

    if (activities.length > 1) {
      activityTimerRef.current = setTimeout(showNext, delay);
    } else {
      setCurrentStep(-1);
      onComplete();
    }
  };

  const send = async (overrideText?: string) => {
    const text = (overrideText ?? input).trim();
    if (!text || typing) return;
    lastUserTextRef.current = text;
    setInput('');
    const userMsg: Message = { role: 'user', text };
    const history = [...msgs, userMsg];
    setMsgs((p) => [...p, userMsg]);
    setTyping(true);
    setActs([]);
    setCurrentStep(-1);
    setPendingActs([]);
    setFollowUps([]);

    const tid = ensureTraceId();

    if (activityTimerRef.current) {
      clearTimeout(activityTimerRef.current);
      activityTimerRef.current = null;
    }

    try {
      const response = await sendChatMessage({
        message: text,
        phase,
        ...(phase === 4
          ? {
              customer_id: DEMO_TRAVELER_ID,
              conversation_id: conversationId ?? undefined,
            }
          : {}),
      });

      if (response.conversation_id) setConversationId(response.conversation_id);
      if (response.memory_facts?.length) {
        setMemoryFacts(response.memory_facts);
        window.dispatchEvent(
          new CustomEvent('meridian-memory-update', { detail: response.memory_facts }),
        );
      }

      const botResponse = response;

      revealActivitiesProgressively(
        enrichTraceActivities(phase, text, response.activities, tid, history, {
          productCount: botResponse.products?.length,
        }),
        () => {
          if (botResponse.follow_ups) setFollowUps(botResponse.follow_ups);

          if (botResponse.products && botResponse.products.length > 0) {
            setMsgs((p) => [
              ...p,
              {
                role: 'bot',
                type: 'products',
                text: botResponse.message,
                products: botResponse.products,
              },
            ]);
          } else if (botResponse.order) {
            setMsgs((p) => [
              ...p,
              {
                role: 'bot',
                type: 'order',
                text: botResponse.message,
                order: botResponse.order,
              },
            ]);
          } else {
            setMsgs((p) => [
              ...p,
              { role: 'bot', type: 'text', text: botResponse.message },
            ]);
          }
          setTyping(false);
        },
      );
    } catch (error) {
      console.error('Chat error:', error);
      setConnectionStatus('disconnected');
      setMsgs((p) => [
        ...p,
        {
          role: 'bot',
          type: 'text',
          text: 'Unable to reach the backend. Make sure FastAPI is running on localhost:8000.',
        },
      ]);
      setTyping(false);
    }
  };

  const applyPhase = useCallback((next: Phase) => {
    if (activityTimerRef.current) {
      clearTimeout(activityTimerRef.current);
      activityTimerRef.current = null;
    }
    setPhase(next);
    setMsgs([]);
    setActs([]);
    setPendingActs([]);
    setCurrentStep(-1);
    setFollowUps([]);
    setTyping(false);
  }, []);

  const switchPhase = (i: number) => applyPhase((i + 1) as Phase);

  const clearChat = useCallback(() => {
    setMsgs([]);
    setActs([]);
    setPendingActs([]);
    setCurrentStep(-1);
    setFollowUps([]);
    if (activityTimerRef.current) {
      clearTimeout(activityTimerRef.current);
      activityTimerRef.current = null;
    }
  }, []);

  const sendRef = useRef(send);
  sendRef.current = send;

  useEffect(() => {
    register({
      phase,
      setPhase: applyPhase,
      setInput,
      focusComposer: () => composerRef.current?.focus(),
      sendMessage: (text) => {
        void sendRef.current(text);
      },
      clearChat,
      replayLast: () => {
        if (lastUserTextRef.current) void sendRef.current(lastUserTextRef.current);
      },
    });
    return () => register(null);
  }, [phase, applyPhase, clearChat, register]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        composerRef.current?.focus();
        document.getElementById('agent')?.scrollIntoView({ behavior: 'smooth' });
      }
      if (e.key === 'Escape' && msgs.length > 0) clearChat();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [msgs.length, clearChat]);

  useEffect(() => {
    return () => {
      if (activityTimerRef.current) clearTimeout(activityTimerRef.current);
    };
  }, []);

  const handleOrder = async (product: Product) => {
    if (typing) return;
    const orderQuery = `Order: ${product.name}`;
    const newUser: Message = { role: 'user', text: orderQuery };
    const orderHistory: Message[] = [...msgs, newUser];

    setMsgs((p) => [...p, newUser]);
    setTyping(true);
    setActs([]);
    setCurrentStep(-1);
    setPendingActs([]);
    setFollowUps([]);

    const tid = ensureTraceId();

    if (activityTimerRef.current) {
      clearTimeout(activityTimerRef.current);
      activityTimerRef.current = null;
    }

    try {
      const response = await processOrder({
        product_id: product.product_id,
        size: product.available_sizes?.[0] || undefined,
        quantity: 1,
        phase,
      });

      revealActivitiesProgressively(
        enrichTraceActivities(phase, orderQuery, response.activities, tid, orderHistory, {
          productCount: 0,
        }),
        () => {
          if (response.order) {
            setMsgs((p) => [
              ...p,
              {
                role: 'bot',
                type: 'order',
                text: response.message,
                order: response.order,
              },
            ]);
          } else {
            setMsgs((p) => [...p, { role: 'bot', type: 'text', text: response.message }]);
          }
          setTyping(false);
        },
      );
    } catch (error) {
      console.error('Order error:', error);
      setMsgs((p) => [
        ...p,
        {
          role: 'bot',
          type: 'text',
          text: 'Sorry, I could not process the booking. Try again in a moment.',
        },
      ]);
      setTyping(false);
    }
  };

  const handleHoldTrip = (product: Product) => {
    setItinerary((prev) => {
      const existing = prev.find((item) => item.product.product_id === product.product_id);
      if (existing) {
        return prev.map((item) =>
          item.product.product_id === product.product_id
            ? { ...item, quantity: item.quantity + 1 }
            : item,
        );
      }
      return [...prev, { product, quantity: 1, duration: product.available_sizes?.[0] }];
    });
    setMsgs((p) => [
      ...p,
      {
        role: 'bot',
        type: 'text',
        text: `Added **${product.name}** to your itinerary. Keep exploring or book when you're ready.`,
      },
    ]);
  };

  // Derived totals for the trace stats
  const totalMs = useMemo(
    () => acts.reduce((sum, a) => sum + (a.execution_time_ms ?? 0), 0),
    [acts],
  );

  const totalSpans = acts.length + pendingActs.length;
  const traceLive = currentStep >= 0;

  // SQL spans, memory facts (for tabs)
  const sqlSpans = acts.filter((a) => Boolean(a.sql_query));
  const memorySpans = acts.filter((a) =>
    ['memory_short', 'memory_long'].includes(a.telemetry?.category ?? ''),
  );

  return (
    <section id="agent" className="mp-section">
      <FadeIn>
        <div className="mp-section-h-row">
          <div className="mp-section-h">
            <div className="mp-label-row">Concierge workspace</div>
            <h2>
              The room where the <em className="serif">concierge</em> works.
            </h2>
            <p>
              Three panes: traveler context on the left, dialogue in the middle, a real trace on
              the right. The trace is permalinked and OpenTelemetry-compatible — devs and travelers
              see different views of the same source of truth.
            </p>
          </div>
          <div className="actions">
            <button
              type="button"
              className="mp-btn ghost sm"
              onClick={() => traceId && navigator.clipboard?.writeText(traceId)}
              disabled={!traceId}
            >
              {traceId ? 'Copy trace id' : 'Share trace'}
            </button>
            <button
              type="button"
              className="mp-btn ghost sm"
              onClick={() => lastUserTextRef.current && send(lastUserTextRef.current)}
              disabled={!lastUserTextRef.current || typing}
              title="Resend last query"
            >
              Replay
            </button>
            <button type="button" className="mp-btn ghost sm" onClick={clearChat}>
              Clear ⎋
            </button>
          </div>
        </div>
      </FadeIn>

      <FadeIn delay={0.1}>
        <div className="mp-workspace">
          {/* Top bar */}
          <div className="mp-ws-bar">
            <div className="mp-ws-crumbs">
              <b>Alex &amp; Jordan Chen</b>
              <span className="sep">/</span>
              conversation <b>{conversationId ?? 'new'}</b>
              <span className="sep">/</span>
              trace <b>{traceId ?? '—'}</b>
            </div>
            <div className="right">
              <div className="mp-pill-row">
                {([1, 2, 3, 4, 5] as Phase[]).map((p) => (
                  <button
                    key={p}
                    type="button"
                    className={`mp-ppill${phase === p ? ' active' : ''}`}
                    data-p={String(p)}
                    onClick={() => switchPhase(p - 1)}
                  >
                    <span className="pdot" /> {PHASE_LABELS[p]}
                  </button>
                ))}
              </div>
              <div className="mp-ws-key" title={
                connectionStatus === 'connected'
                  ? 'Backend connected'
                  : connectionStatus === 'checking'
                    ? 'Checking…'
                    : 'Backend offline'
              }>
                <kbd>⌘</kbd>
                <kbd>K</kbd>
                <span style={{ marginLeft: 4 }}>·</span>
                <kbd>esc</kbd> clear
              </div>
            </div>
          </div>

          {/* 3-pane grid */}
          <div className="mp-ws-grid">
            {/* LEFT RAIL */}
            <aside className="mp-ws-side">
              <div className="mp-side-h">Traveler</div>
              <div className="mp-traveler-card">
                <div className="mp-tv-head">
                  <div className="mp-tv-avatar">A·J</div>
                  <div className="mp-tv-meta">
                    <div className="name">{DEMO_PERSONA_FALLBACK.full_name ?? 'Alex & Jordan Chen'}</div>
                    <div className="sub">{DEMO_TRAVELER_ID}</div>
                  </div>
                </div>
                <div className="mp-tv-tags">
                  {travelerTags.map((tag) => (
                    <span key={tag}>{tag}</span>
                  ))}
                </div>
                <div className="mp-tv-foot">
                  {memoryFacts.length || 8} long-term facts ·{' '}
                  <button
                    type="button"
                    className="mp-link-btn"
                    onClick={() =>
                      document.getElementById('memory')?.scrollIntoView({ behavior: 'smooth' })
                    }
                  >
                    inspect →
                  </button>
                </div>
              </div>

              <div className="mp-side-h">Run config</div>
              <div className="mp-side-card">
                <div className="row"><span>Mode</span><b>{PHASE_LABELS[phase]}</b></div>
                <div className="row"><span>Model</span><b>claude-sonnet</b></div>
                <div className="row"><span>Tools</span><b>{phase >= 2 ? '6 · MCP' : '3 · direct'}</b></div>
                <div className="row"><span>Budget</span><b>$0.06 / turn</b></div>
                <div className="row"><span>Cap</span><b>$3,200 trip</b></div>
              </div>

              <div className="mp-side-h">Try asking</div>
              <div className="mp-starter">
                {currentPhase.starters.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => {
                      setInput(s);
                      composerRef.current?.focus();
                    }}
                  >
                    {s}
                  </button>
                ))}
                {currentPhase.highlight && (
                  <button type="button" className="warn" onClick={() => setInput(currentPhase.highlight!)}>
                    {currentPhase.highlight}
                  </button>
                )}
              </div>

              <div className="mp-side-h">This phase</div>
              <div
                style={{
                  fontSize: 12,
                  color: 'var(--mp-muted)',
                  lineHeight: 1.5,
                  padding: '0 4px',
                }}
              >
                {currentPhase.beat}
              </div>
            </aside>

            {/* CHAT */}
            <main className="mp-ws-chat">
              <div className="mp-chat-feed">
                {msgs.length === 0 && !typing && (
                  <div className="mp-turn bot">
                    <div className="av">M</div>
                    <div className="mp-bubble">
                      <p style={{ margin: 0 }}>
                        Hi — pick a starter on the left, or describe the trip you have in mind. In
                        Phase 4 I'll ground every reply in your stored traveler memory.
                      </p>
                    </div>
                  </div>
                )}

                {msgs.map((m, i) => (
                  <div key={i} className={`mp-turn ${m.role}`}>
                    {m.role === 'bot' ? <div className="av">M</div> : <div className="av">A·J</div>}
                    <div className="mp-bubble">
                      {m.role === 'user' ? (
                        m.text
                      ) : m.type === 'products' && m.products ? (
                        <>
                          <p style={{ margin: 0 }}>{m.text}</p>
                          <div className="mp-rec-grid">
                            {m.products.map((pr) => (
                              <div key={pr.product_id} className="mp-rec-card">
                                <div className="mp-rec-thumb">
                                  <ProductThumb
                                    imageUrl={pr.image_url}
                                    category={pr.category}
                                    alt={pr.name}
                                    style={{ width: '100%', height: '100%', borderRadius: 10 }}
                                    emojiSize={22}
                                  />
                                </div>
                                <div className="mp-rec-meta">
                                  <div className="mp-rec-name">{pr.name}</div>
                                  <div className="mp-rec-sub">
                                    {pr.brand} · {pr.category}
                                  </div>
                                </div>
                                <div className="mp-rec-side">
                                  {pr.similarity != null && (
                                    <div className="mp-rec-match">
                                      {(pr.similarity * 100).toFixed(0)}% match
                                    </div>
                                  )}
                                  <div className="mp-rec-price">${pr.price.toFixed(0)}</div>
                                  <div className="mp-rec-actions">
                                    <button
                                      type="button"
                                      onClick={() => handleHoldTrip(pr)}
                                      disabled={typing}
                                    >
                                      Hold
                                    </button>
                                    <button
                                      type="button"
                                      className="primary"
                                      onClick={() => handleOrder(pr)}
                                      disabled={typing}
                                    >
                                      Plan trip
                                    </button>
                                  </div>
                                </div>
                              </div>
                            ))}
                          </div>
                        </>
                      ) : m.type === 'order' && m.order ? (
                        <div className="mp-booking">
                          <div className="mp-booking-head">
                            <span>✅</span> Booking confirmed · {m.order.order_id}
                          </div>
                          {m.order.items.map((it, idx) => (
                            <div key={idx} className="mp-booking-row">
                              <span>{it.name}{it.size ? ` (${it.size})` : ''}</span>
                              <span>${it.unit_price.toFixed(2)}</span>
                            </div>
                          ))}
                          <div className="mp-booking-divider" />
                          <div className="mp-booking-row">
                            <span style={{ color: 'var(--mp-muted)' }}>Subtotal</span>
                            <span>${m.order.subtotal.toFixed(2)}</span>
                          </div>
                          <div className="mp-booking-row">
                            <span style={{ color: 'var(--mp-muted)' }}>Tax</span>
                            <span>${m.order.tax.toFixed(2)}</span>
                          </div>
                          <div className="mp-booking-row">
                            <span style={{ color: 'var(--mp-muted)' }}>Service fee</span>
                            <span>{m.order.shipping === 0 ? 'FREE' : `$${m.order.shipping.toFixed(2)}`}</span>
                          </div>
                          <div className="mp-booking-divider" />
                          <div className="mp-booking-total">
                            <span>Total</span>
                            <span>${m.order.total.toFixed(2)}</span>
                          </div>
                          {m.order.estimated_delivery && (
                            <div className="mp-booking-eta">
                              ✈️ Departure: <b>{m.order.estimated_delivery}</b>
                            </div>
                          )}
                        </div>
                      ) : (
                        m.text
                      )}

                      {/* inline reasoning trace on bot replies */}
                      {m.role === 'bot' &&
                        i === msgs.length - 1 &&
                        !typing &&
                        acts.length > 0 && (
                          <div className="reasoning">
                            <span className="tag">▸ supervisor</span> →{' '}
                            {acts
                              .slice(0, 4)
                              .map((a) => a.title.replace(/\s+/g, ' '))
                              .join(' → ')}
                            {acts.length > 4 ? ' → …' : ''}
                          </div>
                        )}
                    </div>
                  </div>
                ))}

                {typing && (
                  <div className="mp-turn bot">
                    <div className="av">M</div>
                    <div className="mp-bubble">
                      <span style={{ display: 'inline-flex', gap: 6 }}>
                        {[0, 1, 2].map((k) => (
                          <span
                            key={k}
                            style={{
                              width: 6,
                              height: 6,
                              borderRadius: 3,
                              background: 'var(--mp-soft)',
                              animation: 'mp-pulse 1.2s ease-in-out infinite',
                              animationDelay: `${k * 0.15}s`,
                              display: 'inline-block',
                            }}
                          />
                        ))}
                      </span>
                    </div>
                  </div>
                )}

                {!typing && followUps.length > 0 && (
                  <div className="mp-followups">
                    {followUps.map((fu) => (
                      <button key={fu} type="button" onClick={() => void send(fu)} disabled={typing}>
                        {fu}
                      </button>
                    ))}
                  </div>
                )}

                {connectionStatus === 'disconnected' && msgs.length === 0 && (
                  <div
                    style={{
                      fontSize: 12,
                      color: 'var(--mp-accent-2)',
                      background: 'rgba(255,91,31,0.06)',
                      border: '1px solid rgba(255,91,31,0.25)',
                      padding: '10px 12px',
                      borderRadius: 10,
                    }}
                  >
                    Backend offline — start <code>uvicorn backend.main:app</code> on
                    localhost:8000 to wire up the trace.
                  </div>
                )}

                <div ref={chatEnd} />
              </div>

              {/* Composer */}
              <div className="mp-composer">
                <div className="mp-composer-input">
                  <span className="mp-composer-chip" data-p={String(phase)} style={{ color: PHASE_COLOR[phase] }}>
                    {PHASE_LABELS[phase]}
                  </span>
                  <input
                    ref={composerRef}
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && void send()}
                    placeholder={
                      phase === 4
                        ? 'Ask the concierge — anything about your trip…'
                        : 'Ask about destinations, dates, or trip style…'
                    }
                  />
                  <div className="mp-composer-tools">
                    <button type="button" title="Attach (coming soon)">⊕</button>
                    <button type="button" title="Voice (coming soon)">🎙</button>
                    <button type="button" title="Clear" onClick={clearChat}>⌫</button>
                  </div>
                </div>
                <button
                  type="button"
                  className="mp-composer-send"
                  onClick={() => void send()}
                  disabled={typing || !input.trim()}
                  aria-label="Send"
                >
                  ↑
                </button>
              </div>
            </main>

            {/* TRACE */}
            <aside className="mp-ws-trace">
              <div className="mp-trace-head">
                <div className="ttl">
                  Trace
                  <small>
                    {traceId ?? '—'} · {totalSpans} spans · {totalMs}ms
                  </small>
                </div>
                <span className={`mp-trace-live${traceLive ? '' : ' idle'}`}>
                  {traceLive ? 'live' : 'idle'}
                </span>
              </div>

              <div className="mp-trace-tabs">
                <button
                  type="button"
                  className={`mp-trace-tab${activeTraceTab === 'spans' ? ' active' : ''}`}
                  onClick={() => setActiveTraceTab('spans')}
                >
                  Spans <span className="count">{totalSpans}</span>
                </button>
                <button
                  type="button"
                  className={`mp-trace-tab${activeTraceTab === 'memory' ? ' active' : ''}`}
                  onClick={() => setActiveTraceTab('memory')}
                >
                  Memory <span className="count">{memoryFacts.length || memorySpans.length}</span>
                </button>
                <button
                  type="button"
                  className={`mp-trace-tab${activeTraceTab === 'sql' ? ' active' : ''}`}
                  onClick={() => setActiveTraceTab('sql')}
                >
                  SQL <span className="count">{sqlSpans.length}</span>
                </button>
                <button
                  type="button"
                  className={`mp-trace-tab${activeTraceTab === 'cost' ? ' active' : ''}`}
                  onClick={() => setActiveTraceTab('cost')}
                >
                  Cost
                </button>
              </div>

              <div className="mp-trace-list">
                {activeTraceTab === 'spans' &&
                  (totalSpans === 0 ? (
                    <div className="mp-trace-empty">
                      <div className="pulser">
                        <span /> <span /> <span />
                      </div>
                      <div>Waiting for activity</div>
                      <div className="hint">Send a query to see the full agent trace</div>
                    </div>
                  ) : (
                    <div className="mp-gantt">
                      {acts.map((a, i) => (
                        <GanttSpan
                          key={a.id ?? `done-${i}`}
                          entry={a}
                          index={i}
                          totalSpans={totalSpans}
                          state={i === currentStep ? 'live' : 'done'}
                        />
                      ))}
                      {pendingActs.map((a, i) => (
                        <GanttSpan
                          key={a.id ?? `pending-${i}`}
                          entry={a}
                          index={acts.length + i}
                          totalSpans={totalSpans}
                          state="pending"
                        />
                      ))}
                    </div>
                  ))}

                {activeTraceTab === 'memory' &&
                  (memoryFacts.length === 0 ? (
                    <div className="mp-trace-empty">
                      <div>No long-term memory recalled yet</div>
                      <div className="hint">Switch to Phase 4 and send a query</div>
                    </div>
                  ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                      {memoryFacts.map((f, i) => (
                        <div
                          key={`${f.key}-${i}`}
                          style={{
                            padding: '10px 12px',
                            background: 'var(--mp-paper-2)',
                            border: '1px solid var(--mp-line)',
                            borderRadius: 10,
                          }}
                        >
                          <div
                            style={{
                              fontSize: 11,
                              fontFamily: 'ui-monospace, "SF Mono", monospace',
                              color: 'var(--mp-dim)',
                            }}
                          >
                            {f.key}
                          </div>
                          <div style={{ fontSize: 13, color: 'var(--mp-ink)', marginTop: 2 }}>
                            {f.value}
                          </div>
                          {(f.confidence != null || f.source) && (
                            <div
                              style={{
                                fontSize: 10.5,
                                color: 'var(--mp-dim)',
                                marginTop: 4,
                                fontFamily: 'ui-monospace, "SF Mono", monospace',
                              }}
                            >
                              {f.confidence != null ? `conf ${f.confidence.toFixed(2)}` : ''}
                              {f.confidence != null && f.source ? ' · ' : ''}
                              {f.source ?? ''}
                            </div>
                          )}
                        </div>
                      ))}
                    </div>
                  ))}

                {activeTraceTab === 'sql' &&
                  (sqlSpans.length === 0 ? (
                    <div className="mp-trace-empty">
                      <div>No SQL emitted on this turn</div>
                      <div className="hint">Phase 1 / 2 will show direct queries</div>
                    </div>
                  ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                      {sqlSpans.map((s, i) => (
                        <div key={s.id ?? `sql-${i}`}>
                          <div
                            style={{
                              fontSize: 11,
                              color: 'var(--mp-dim)',
                              marginBottom: 4,
                              fontFamily: 'ui-monospace, "SF Mono", monospace',
                            }}
                          >
                            {s.title}
                          </div>
                          <pre className="mp-gspan-sql">{s.sql_query}</pre>
                        </div>
                      ))}
                    </div>
                  ))}

                {activeTraceTab === 'cost' && (
                  <div
                    style={{
                      padding: '8px 4px',
                      fontSize: 13,
                      color: 'var(--mp-muted)',
                      lineHeight: 1.7,
                    }}
                  >
                    <div>
                      <b style={{ color: 'var(--mp-ink)' }}>${(totalMs * 0.00003).toFixed(4)}</b>{' '}
                      estimated for this turn
                    </div>
                    <div>
                      <b style={{ color: 'var(--mp-ink)' }}>{totalSpans}</b> spans ·{' '}
                      <b style={{ color: 'var(--mp-ink)' }}>{totalMs}ms</b> total
                    </div>
                    <div>
                      Bedrock <code>claude-sonnet</code> · pgvector HNSW · pricing approximate
                    </div>
                  </div>
                )}
              </div>

              <div className="mp-trace-stats">
                <div className="cell">Total<b>{totalMs}ms</b></div>
                <div className="cell">Spans<b>{totalSpans}</b></div>
                <div className="cell">
                  Mode<b>{PHASE_LABELS[phase]}</b>
                </div>
                <div className="cell">
                  Vectors<b>1024d</b>
                </div>
              </div>
            </aside>
          </div>
        </div>
      </FadeIn>
    </section>
  );
}
