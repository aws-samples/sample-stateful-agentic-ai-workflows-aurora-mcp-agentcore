/**
 * ConciergeStudio — dark glass concierge surface
 *
 * The hero workspace for Meridian. Mounted at `/` via ConciergeApp. Owns the
 * `id="agent"` anchor so AgentBridge.openConcierge keeps scrolling here. Wired
 * to the real backend via sendChatMessage / fetchProducts / fetchMemoryProfile.
 *
 * Visual reference: meridian/docs/meridian.png. Cinematic dark stage — left
 * rail with line-icon nav, hero greeting + chat, photographic recommendation
 * cards, traveler context + live activity on the right, command bar with quick
 * chips. Workshop affordances (phase pills, demo-query tray, marketing header)
 * are intentionally absent so the surface reads as a polished product.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useAgentBridge } from '../context/AgentBridge';
import { fetchMemoryProfile, fetchProducts, sendChatMessage } from '../api/client';
import { DEMO_TRAVELER_ID, DEMO_PERSONA_FALLBACK } from '../components/TravelerPersona';
import type {
  ActivityEntry,
  LongTermMemoryFact,
  Phase,
  Product,
  TravelerProfile,
} from '../types';

const QUICK_CHIPS = ['Add travelers', 'Change dates', 'Add spa', 'Direct flights'];

const ACTIVITY_PIPELINE = [
  'Understanding your request',
  'Searching preference-matched destinations',
  'Checking availability & pricing',
  'Curating personalized recommendations',
  'Optimizing your itinerary',
];

const ART_BY_CATEGORY: Record<string, string> = {
  Wine: 'vineyard',
  'Wine country': 'vineyard',
  Wellness: 'spa',
  'Wellness & Luxury': 'spa',
  'Beach & Resort': 'coast',
  Beach: 'coast',
  'City Breaks': 'city',
  'Adventure & Outdoors': 'mountain',
  Adventure: 'mountain',
  'Business travel': 'city',
};

const ALEX_AVATAR =
  'https://images.unsplash.com/photo-1500648767791-00dcc994a43e?auto=format&fit=facearea&facepad=2.4&w=240&h=240&q=80';

const FALLBACK_RECS: RecCard[] = [
  {
    product_id: 'fallback-willamette',
    name: 'Willamette Valley, Oregon',
    brand: 'Wine country',
    category: 'Wine',
    price: 1950,
    image_url:
      'https://images.unsplash.com/photo-1507525428034-b723cf961d3e?auto=format&fit=crop&w=720&q=80',
    dates: 'Nov 7 – 10',
    art: 'vineyard',
    tag: 'Trending',
  },
  {
    product_id: 'fallback-napa',
    name: 'Napa Valley, California',
    brand: 'Wine country',
    category: 'Wine',
    price: 2450,
    image_url:
      'https://images.unsplash.com/photo-1465146344425-f00d5f5c8f07?auto=format&fit=crop&w=720&q=80',
    dates: 'Nov 14 – 17',
    art: 'valley',
    tag: '',
  },
  {
    product_id: 'fallback-mendoza',
    name: 'Mendoza, Argentina',
    brand: 'Wine country',
    category: 'Wine',
    price: 1850,
    image_url:
      'https://images.unsplash.com/photo-1519681393784-d120267933ba?auto=format&fit=crop&w=720&q=80',
    dates: 'Nov 21 – 24',
    art: 'mountain',
    tag: '',
  },
];

function pickArt(category: string, idx: number): string {
  if (ART_BY_CATEGORY[category]) return ART_BY_CATEGORY[category];
  const arts = ['vineyard', 'valley', 'mountain', 'coast', 'city', 'spa'];
  return arts[idx % arts.length];
}

interface RecCard {
  product_id: string;
  name: string;
  brand: string;
  category: string;
  price: number;
  image_url: string;
  dates: string;
  art: string;
  tag: string;
}

function toRec(p: Product, idx: number): RecCard {
  const dateRanges = ['Nov 7 – 10', 'Nov 14 – 17', 'Nov 21 – 24', 'Dec 5 – 8', 'Dec 12 – 15'];
  return {
    product_id: p.product_id,
    name: p.name,
    brand: p.brand,
    category: p.category,
    price: p.price,
    image_url: p.image_url,
    dates: dateRanges[idx] ?? '—',
    art: pickArt(p.category, idx),
    tag: idx === 0 ? 'Trending' : '',
  };
}

type NavKey = 'concierge' | 'trips' | 'discover' | 'profile' | 'preferences' | 'messages';

interface NavItem {
  key: NavKey;
  label: string;
  icon: JSX.Element;
  badge?: number;
}

// Stroke-line icons sized to match the screenshot's quiet, premium nav.
const ICON = {
  concierge: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="9" />
      <path d="M16.2 7.8 13.6 13l-5.2 2.6 2.6-5.2 5.2-2.6Z" />
    </svg>
  ),
  trips: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="7" width="18" height="13" rx="2" />
      <path d="M9 7V5a2 2 0 0 1 2-2h2a2 2 0 0 1 2 2v2" />
      <path d="M3 12h18" />
    </svg>
  ),
  discover: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="11" cy="11" r="7" />
      <path d="m20 20-3.5-3.5" />
    </svg>
  ),
  profile: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="8" r="4" />
      <path d="M4 21c1.6-4 4.6-6 8-6s6.4 2 8 6" />
    </svg>
  ),
  preferences: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4 7h10" />
      <path d="M20 7h-2" />
      <circle cx="16" cy="7" r="2" />
      <path d="M4 17h4" />
      <path d="M20 17h-8" />
      <circle cx="10" cy="17" r="2" />
    </svg>
  ),
  messages: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M4 6.5C4 5.7 4.7 5 5.5 5h13c.8 0 1.5.7 1.5 1.5v9c0 .8-.7 1.5-1.5 1.5H9.4l-3.6 3v-3H5.5C4.7 17 4 16.3 4 15.5v-9Z" />
    </svg>
  ),
  settings: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 14.6 21 16l-2 3.5-2.1-.6a7.7 7.7 0 0 1-1.7 1l-.4 2.1h-4l-.4-2.1a7.7 7.7 0 0 1-1.7-1L6.6 19.5 4.6 16l1.6-1.4a7.7 7.7 0 0 1 0-1.2L4.6 12l2-3.5 2.1.6a7.7 7.7 0 0 1 1.7-1l.4-2.1h4l.4 2.1a7.7 7.7 0 0 1 1.7 1l2.1-.6L21 12l-1.6 1.4c.04.4.04.8 0 1.2Z" />
    </svg>
  ),
  send: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M5 12h14" />
      <path d="m13 6 6 6-6 6" />
    </svg>
  ),
  star: (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <path d="M12 3.2 14 9h5.8l-4.7 3.4 1.8 5.6L12 14.6 7.1 18l1.8-5.6L4.2 9H10l2-5.8Z" />
    </svg>
  ),
  caret: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="m6 9 6 6 6-6" />
    </svg>
  ),
  more: (
    <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
      <circle cx="6" cy="12" r="1.4" />
      <circle cx="12" cy="12" r="1.4" />
      <circle cx="18" cy="12" r="1.4" />
    </svg>
  ),
  arrow: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M9 6l6 6-6 6" />
    </svg>
  ),
  check: (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="m5 12 5 5L20 7" />
    </svg>
  ),
};

const NAV_ITEMS: NavItem[] = [
  { key: 'concierge', label: 'Concierge', icon: ICON.concierge },
  { key: 'trips', label: 'Trips', icon: ICON.trips },
  { key: 'discover', label: 'Discover', icon: ICON.discover },
  { key: 'profile', label: 'Profile', icon: ICON.profile },
  { key: 'preferences', label: 'Preferences', icon: ICON.preferences },
  { key: 'messages', label: 'Messages', icon: ICON.messages, badge: 2 },
];

export function ConciergeStudio() {
  const { register } = useAgentBridge();
  // Phase still drives backend behavior, but no UI surface exposes it on `/`.
  const [phase, setPhase] = useState<Phase>(4);
  const [profile, setProfile] = useState<TravelerProfile>(DEMO_PERSONA_FALLBACK);
  const [memoryFacts, setMemoryFacts] = useState<LongTermMemoryFact[]>([]);
  const [activities, setActivities] = useState<ActivityEntry[]>([]);
  const [totalMs, setTotalMs] = useState<number>(0);
  const [recs, setRecs] = useState<RecCard[]>(FALLBACK_RECS);
  const [userPrompt, setUserPrompt] = useState<string>(
    "I'm looking for a long weekend in wine country in November. Boutique, walkable towns, great food, and relaxing spa options.",
  );
  const [botLede, setBotLede] = useState<string>(
    "Perfect. I've found a few places that match your style.",
  );
  const [composer, setComposer] = useState('');
  const [typing, setTyping] = useState(false);
  // Initial pose mirrors the keynote screenshot: the first four steps complete
  // with green checks and the final step shows the live spinner — communicates
  // "Meridian is still optimizing" before the user sends a fresh request.
  const [activeStep, setActiveStep] = useState<number>(ACTIVITY_PIPELINE.length - 1);
  const [revealed, setRevealed] = useState<number>(ACTIVITY_PIPELINE.length - 1);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [activeNav, setActiveNav] = useState<NavKey>('concierge');

  const composerRef = useRef<HTMLInputElement>(null);
  const stepTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    fetchProducts(undefined, 12, true)
      .then((items) => {
        if (items.length) {
          const preferred = items
            .filter((p) =>
              ['Wine', 'Wellness', 'Wellness & Luxury', 'City Breaks'].includes(p.category),
            )
            .slice(0, 3);
          const picks = preferred.length === 3 ? preferred : items.slice(0, 3);
          setRecs(picks.map((p, i) => toRec(p, i)));
        }
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    fetchMemoryProfile(DEMO_TRAVELER_ID)
      .then((res) => {
        if (res.profile) setProfile({ ...DEMO_PERSONA_FALLBACK, ...res.profile });
        if (res.facts?.length) setMemoryFacts(res.facts);
      })
      .catch(() => {});
  }, []);

  const startActivityPipeline = useCallback(() => {
    if (stepTimerRef.current) clearTimeout(stepTimerRef.current);
    setRevealed(0);
    setActiveStep(0);
    let i = 0;
    const tick = () => {
      i += 1;
      if (i < ACTIVITY_PIPELINE.length) {
        setRevealed(i);
        setActiveStep(i);
        stepTimerRef.current = setTimeout(tick, 700);
      } else {
        // Mirror the screenshot: four steps complete, the final one stays
        // pending to keep the "live" pulse alive on the right rail.
        setRevealed(ACTIVITY_PIPELINE.length - 1);
        setActiveStep(ACTIVITY_PIPELINE.length - 1);
      }
    };
    stepTimerRef.current = setTimeout(tick, 700);
  }, []);

  const send = useCallback(
    async (text: string, options?: { phase?: Phase }) => {
      if (!text.trim() || typing) return;
      const effectivePhase = options?.phase ?? phase;
      setUserPrompt(text.trim());
      setComposer('');
      setTyping(true);
      startActivityPipeline();

      try {
        const response = await sendChatMessage({
          message: text.trim(),
          phase: effectivePhase,
          ...(effectivePhase === 4
            ? {
                customer_id: DEMO_TRAVELER_ID,
                conversation_id: conversationId ?? undefined,
              }
            : {}),
        });
        if (response.conversation_id) setConversationId(response.conversation_id);
        if (response.activities?.length) {
          setActivities(response.activities);
          setTotalMs(response.activities.reduce((s, a) => s + (a.execution_time_ms ?? 0), 0));
        }
        if (response.memory_facts?.length) {
          setMemoryFacts(response.memory_facts);
          window.dispatchEvent(
            new CustomEvent<LongTermMemoryFact[]>('meridian-memory-update', {
              detail: response.memory_facts,
            }),
          );
        }
        if (response.products && response.products.length) {
          setRecs(response.products.slice(0, 3).map((p, i) => toRec(p, i)));
        }
        if (response.message) setBotLede(response.message);
      } catch {
        setBotLede(
          'I lost the thread to Aurora — start the backend (uvicorn backend.main:app) and try again.',
        );
      } finally {
        setTyping(false);
      }
    },
    [conversationId, phase, startActivityPipeline, typing],
  );

  const sendRef = useRef(send);
  sendRef.current = send;
  useEffect(() => {
    register({
      phase,
      setPhase: (p: Phase) => setPhase(p),
      setInput: (t: string) => setComposer(t),
      focusComposer: () => composerRef.current?.focus(),
      sendMessage: (text, options) => {
        void sendRef.current(text, options);
      },
      clearChat: () => {
        setUserPrompt('');
        setBotLede('');
        setRevealed(0);
        setActiveStep(-1);
      },
      replayLast: () => {
        if (userPrompt) void sendRef.current(userPrompt);
      },
    });
    return () => register(null);
  }, [phase, register, userPrompt]);

  useEffect(
    () => () => {
      if (stepTimerRef.current) clearTimeout(stepTimerRef.current);
    },
    [],
  );

  // Keynote identity: the screenshot is "Alex Morgan / Explorer". The shared
  // DEMO_PERSONA_FALLBACK is a couple ("Alex & Jordan Chen") used by the
  // marketing scroll's persona card — for the concierge surface we normalize
  // to a single name so the avatar/email/greeting all line up.
  const rawName = profile.full_name ?? 'Alex Morgan';
  const fullName = rawName.includes('&') ? 'Alex Morgan' : rawName;
  const firstName = fullName.split(/\s+/)[0] ?? 'there';
  const initials = fullName
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((s) => s[0]?.toUpperCase() ?? '')
    .join('');
  const email = `${firstName.toLowerCase()}.morgan@gmail.com`;

  const interests = useMemo(() => {
    const fact = memoryFacts.find((f) => /interests/i.test(f.key));
    return fact?.value ?? 'Wine, food, architecture, wellness';
  }, [memoryFacts]);

  const travelStyle = useMemo(() => {
    const style = memoryFacts.find((f) => /style/i.test(f.key))?.value;
    const pace = memoryFacts.find((f) => /pace/i.test(f.key))?.value;
    return [style, pace].filter(Boolean).join(' · ') || 'Boutique, immersive, relaxed';
  }, [memoryFacts]);

  const recentTrips = 'Tuscany, Kyoto, Palm Springs';
  const loyalty = 'Marriott Bonvoy, Delta SkyMiles';

  return (
    <section id="agent" className="cs-stage" aria-label="Meridian concierge">
      <div className="cs-shell">
        {/* LEFT RAIL */}
        <aside className="cs-rail">
          <div className="cs-brand">
            <span className="cs-brand-glyph" aria-hidden="true" />
            <span>Meridian</span>
          </div>

          <nav className="cs-nav" aria-label="Primary">
            {NAV_ITEMS.map((item) => (
              <button
                key={item.key}
                className={activeNav === item.key ? 'active' : ''}
                onClick={() => setActiveNav(item.key)}
                type="button"
              >
                <span className="cs-nav-label">
                  <span className="cs-nav-ic">{item.icon}</span>
                  {item.label}
                </span>
                {item.badge ? <span className="cs-nav-badge">{item.badge}</span> : null}
              </button>
            ))}
          </nav>

          <div className="cs-rail-foot">
            <button type="button" className="cs-rail-settings">
              <span className="cs-nav-ic">{ICON.settings}</span>
              Settings
            </button>
            <div className="cs-user">
              <div className="cs-avatar cs-avatar--photo">
                <img src={ALEX_AVATAR} alt={fullName} />
                <span className="cs-avatar-fallback">{initials}</span>
              </div>
              <div className="meta">
                <div className="name">{fullName}</div>
                <div className="role">Explorer</div>
              </div>
            </div>
          </div>
        </aside>

        {/* MAIN */}
        <main className="cs-main">
          <div className="cs-topbar">
            <span className="cs-chip">
              <span className="cs-chip-ic gold">{ICON.star}</span>
              VIP {ICON.caret}
            </span>
            <span className="cs-chip">
              <span className="cs-chip-dot" /> USD {ICON.caret}
            </span>
            <span className="cs-chip cs-chip-icon">{ICON.more}</span>
          </div>

          <div className="cs-greeting">
            <h1>
              Good morning, <span className="accent">{firstName}</span>.
            </h1>
            <p>Where would you like to go next?</p>
          </div>

          <div className="cs-thread">
            <div className="cs-bubble-user">{userPrompt}</div>

            <p className="cs-lede">
              {typing ? 'Curating recommendations…' : botLede}
            </p>

            <div className="cs-rec-row">
              {recs.slice(0, 3).map((r) => (
                <article
                  key={r.product_id}
                  className="cs-rec"
                  onClick={() => void send(`Tell me more about ${r.name}.`)}
                >
                  <div className="cs-rec-art" data-art={r.art}>
                    {r.image_url ? <img src={r.image_url} alt={r.name} /> : null}
                    {r.tag && <span className="cs-rec-tag">{r.tag}</span>}
                  </div>
                  <div className="cs-rec-body">
                    <div className="cs-rec-name">{r.name}</div>
                    <div className="cs-rec-dates">{r.dates}</div>
                    <div className="cs-rec-foot">
                      <div className="cs-rec-price">
                        <small>From</small>${r.price.toLocaleString()}
                      </div>
                      <button
                        type="button"
                        className="cs-rec-go"
                        aria-label={`Open ${r.name}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          void send(`Tell me more about ${r.name}.`);
                        }}
                      >
                        {ICON.arrow}
                      </button>
                    </div>
                  </div>
                </article>
              ))}
            </div>

            <button
              type="button"
              className="cs-more"
              onClick={() => void send('Show me a few more recommendations like these.')}
            >
              View more recommendations
            </button>
          </div>

          <div className="cs-composer">
            <div className="cs-composer-input">
              <input
                ref={composerRef}
                value={composer}
                onChange={(e) => setComposer(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    void send(composer);
                  }
                }}
                placeholder="Ask Meridian anything…"
              />
              <button
                type="button"
                className="cs-composer-send"
                aria-label="Send"
                onClick={() => void send(composer)}
                disabled={typing || !composer.trim()}
              >
                {ICON.send}
              </button>
            </div>
            <div className="cs-quick">
              {QUICK_CHIPS.map((chip) => (
                <button
                  key={chip}
                  type="button"
                  onClick={() => {
                    setComposer(chip);
                    composerRef.current?.focus();
                  }}
                >
                  {chip}
                </button>
              ))}
            </div>
          </div>
        </main>

        {/* RIGHT COLUMN */}
        <aside className="cs-side">
          <div className="cs-card">
            <div className="cs-card-h">
              <h3>Traveler context</h3>
              <button className="edit" type="button">Edit</button>
            </div>
            <div className="cs-traveler-id">
              <div className="cs-avatar cs-avatar--photo">
                <img src={ALEX_AVATAR} alt={fullName} />
                <span className="cs-avatar-fallback">{initials}</span>
              </div>
              <div>
                <div className="name">{fullName}</div>
                <div className="email">{email}</div>
              </div>
            </div>
            <dl className="cs-meta">
              <div className="cs-meta-row">
                <dt>Profile</dt>
                <dd>Explorer</dd>
              </div>
              <div className="cs-meta-row">
                <dt>Travel style</dt>
                <dd>{travelStyle}</dd>
              </div>
              <div className="cs-meta-row">
                <dt>Interests</dt>
                <dd>{interests}</dd>
              </div>
              <div className="cs-meta-row">
                <dt>Loyalty programs</dt>
                <dd>{loyalty}</dd>
              </div>
              <div className="cs-meta-row">
                <dt>Recent trips</dt>
                <dd>{recentTrips}</dd>
              </div>
            </dl>
            <button
              type="button"
              className="cs-view-all"
              onClick={() => document.getElementById('memory')?.scrollIntoView({ behavior: 'smooth' })}
            >
              View all
            </button>
          </div>

          <div className="cs-card">
            <div className="cs-card-h">
              <h3>Meridian activity</h3>
              <span className="cs-live">
                <span className="cs-live-dot" />
                Live
              </span>
            </div>

            {totalMs > 0 && (
              <div className="cs-activity-meta">
                {totalMs}ms · {activities.length} spans
              </div>
            )}

            {activities.length > 0 ? (
              <div className="cs-trace-list">
                {activities.map((a, i) => {
                  const cat = a.telemetry?.category ?? 'runtime';
                  const tag = cat.replace(/_/g, ' ');
                  return (
                    <div key={a.id ?? i} className="cs-trace-row">
                      <div>
                        <div className="ttl">
                          <span className={`cs-trace-tag ${cat}`}>{tag}</span>
                          {a.title}
                        </div>
                        {a.agent_name && (
                          <div className="sub">
                            {a.agent_name}
                            {a.agent_file ? ` · ${a.agent_file}` : ''}
                          </div>
                        )}
                        {a.details && <div className="sub">{a.details}</div>}
                      </div>
                      {a.execution_time_ms != null && (
                        <div className="ms">{a.execution_time_ms}ms</div>
                      )}
                    </div>
                  );
                })}
              </div>
            ) : (
              <ul className="cs-activity">
                {ACTIVITY_PIPELINE.map((label, i) => {
                  const state =
                    i < revealed ? 'done' : i === activeStep ? 'live' : 'pending';
                  return (
                    <li key={label} className={`cs-activity-row ${state}`}>
                      <span className={`cs-activity-dot ${state}`}>
                        {state === 'done' ? ICON.check : null}
                      </span>
                      <span className="cs-activity-label">{label}</span>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </aside>
      </div>
    </section>
  );
}

// Convenience aliased export so legacy imports (App.tsx) keep working.
export const AgentSection = ConciergeStudio;
