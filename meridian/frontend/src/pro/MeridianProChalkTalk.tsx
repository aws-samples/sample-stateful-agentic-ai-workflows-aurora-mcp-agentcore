import { useEffect, useMemo, useState } from 'react';
import { sendChatMessage } from '../api/client';
import type { ActivityEntry, Phase } from '../types';
import './meridianProChalkTalk.css';

type ChatLine = { role: 'user' | 'assistant'; text: string };

const PHASES: { id: Phase; title: string; subtitle: string }[] = [
  { id: 1, title: 'Phase 1 · SQL', subtitle: 'Direct Aurora filters' },
  { id: 2, title: 'Phase 2 · MCP', subtitle: 'postgres-mcp-server tooling' },
  { id: 3, title: 'Phase 3 · Retrieval', subtitle: 'Hybrid semantic + lexical' },
  { id: 4, title: 'Phase 4 · Memory', subtitle: 'AgentCore + Aurora RLS' },
  { id: 5, title: 'Phase 5 · Orchestration', subtitle: 'LangGraph state control' },
];

const STARTER_PROMPT =
  'A slow week somewhere we can drink good wine. Jordan cannot do red-eyes.';

const INITIAL_LINES: ChatLine[] = [
  { role: 'assistant', text: 'Good morning, Alex. Where should Meridian take you next?' },
  { role: 'user', text: STARTER_PROMPT },
];

const SAMPLE_TRACE: ActivityEntry[] = [
  {
    id: 'boot-1',
    timestamp: new Date().toISOString(),
    activity_type: 'reasoning',
    title: 'Concierge context boot',
    details: 'Loaded traveler profile, preferences, and session context.',
    execution_time_ms: 41,
    agent_name: 'ProductionAgent',
  },
  {
    id: 'boot-2',
    timestamp: new Date().toISOString(),
    activity_type: 'tool_call',
    title: 'Gateway semantic_trip_search',
    details: 'Mapped natural language intent to ranked trip candidates.',
    execution_time_ms: 162,
    agent_name: 'ProductionAgent',
  },
];

function traceLabel(entry: ActivityEntry): string {
  const ms = entry.execution_time_ms != null ? `${entry.execution_time_ms}ms` : 'live';
  return `${entry.title} · ${ms}`;
}

export function MeridianProChalkTalk() {
  const [phaseIndex, setPhaseIndex] = useState(3);
  const [composer, setComposer] = useState('');
  const [sending, setSending] = useState(false);
  const [conversationId, setConversationId] = useState<string | undefined>();
  const [lines, setLines] = useState<ChatLine[]>(INITIAL_LINES);
  const [traces, setTraces] = useState<ActivityEntry[]>(SAMPLE_TRACE);
  const activePhase = PHASES[phaseIndex];

  useEffect(() => {
    const timer = window.setInterval(() => {
      setPhaseIndex((i) => (i + 1) % PHASES.length);
    }, 4600);
    return () => window.clearInterval(timer);
  }, []);

  const quickPrompts = useMemo(
    () => [
      'Boutique hotels with spa options',
      'No red-eye flights from BOS',
      'One-week wine + wellness itinerary',
      'Direct flight only',
    ],
    [],
  );

  async function sendMessage(text: string) {
    const trimmed = text.trim();
    if (!trimmed || sending) return;
    setSending(true);
    setLines((prev) => [...prev, { role: 'user', text: trimmed }]);
    setComposer('');
    try {
      const response = await sendChatMessage({
        message: trimmed,
        phase: activePhase.id,
        customer_id: 'trv_meridian_demo',
        conversation_id: conversationId,
      });
      setConversationId(response.conversation_id || conversationId);
      setLines((prev) => [...prev, { role: 'assistant', text: response.message }]);
      if (response.activities?.length) {
        setTraces(response.activities.slice(0, 7));
      }
    } catch (error) {
      const reason = error instanceof Error ? error.message : 'Backend not reachable';
      setLines((prev) => [
        ...prev,
        { role: 'assistant', text: `I hit a backend error: ${reason}. The chalk talk shell is still live.` },
      ]);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="mpc-root">
      <div className="mpc-stage">
        <aside className="mpc-left">
          <div className="mpc-brand">
            <span className="mpc-brand-mark" />
            Meridian
          </div>
          <nav className="mpc-nav" aria-label="Meridian app navigation">
            <button type="button" className="is-active">Concierge</button>
            <button type="button">Trips</button>
            <button type="button">Discover</button>
            <button type="button">Profile</button>
            <button type="button">Preferences</button>
            <button type="button">Messages</button>
            <button type="button">Settings</button>
          </nav>
          <div className="mpc-phase-rail">
            <h3>5-Phase Flow</h3>
            {PHASES.map((phase, idx) => (
              <button
                key={phase.id}
                type="button"
                className={`mpc-phase-item${idx === phaseIndex ? ' is-active' : ''}${idx < phaseIndex ? ' is-complete' : ''}`}
                onClick={() => setPhaseIndex(idx)}
              >
                <b>{phase.title}</b>
                <span>{phase.subtitle}</span>
              </button>
            ))}
          </div>
          <div className="mpc-left-persona">
            <img
              src="https://images.unsplash.com/photo-1544005313-94ddf0286df2?auto=format&fit=crop&w=320&q=80"
              alt="Companion profile"
            />
            <div>
              <strong>Jordan Chen</strong>
              <span>Companion persona</span>
            </div>
          </div>
        </aside>

        <main className="mpc-chat">
          <div className="mpc-chat-head">
            <h2>Concierge Chat</h2>
            <p>{activePhase.title} active · flow auto-progresses top → bottom for storytelling.</p>
          </div>

          <div className="mpc-thread">
            {lines.map((line, idx) => (
              <div key={`${line.role}-${idx}`} className={`mpc-msg ${line.role}`}>
                {line.text}
              </div>
            ))}
          </div>

          <div className="mpc-quick-row">
            {quickPrompts.map((prompt) => (
              <button key={prompt} type="button" onClick={() => sendMessage(prompt)}>
                {prompt}
              </button>
            ))}
          </div>

          <form
            className="mpc-composer"
            onSubmit={(e) => {
              e.preventDefault();
              void sendMessage(composer);
            }}
          >
            <input
              value={composer}
              onChange={(e) => setComposer(e.target.value)}
              placeholder="Ask Meridian anything..."
            />
            <button type="submit" disabled={sending}>
              {sending ? 'Thinking...' : 'Send'}
            </button>
          </form>
        </main>

        <aside className="mpc-right">
          <section className="mpc-profile">
            <h3>Traveler Context</h3>
            <div className="mpc-person">
              <img
                src="https://images.unsplash.com/photo-1535713875002-d1d0cf377fde?auto=format&fit=crop&w=320&q=80"
                alt="Traveler profile"
              />
              <div>
                <strong>Alex Morgan</strong>
                <span>alex.morgan@gmail.com</span>
              </div>
            </div>
            <dl>
              <dt>Travel style</dt>
              <dd>Boutique, immersive, relaxed</dd>
              <dt>Preferences</dt>
              <dd>Wine, wellness, walkable towns, premium food</dd>
              <dt>Loyalty</dt>
              <dd>Marriott Bonvoy · Delta SkyMiles</dd>
              <dt>Companion</dt>
              <dd>Jordan · avoids red-eye flights</dd>
            </dl>
          </section>

          <section className="mpc-trace">
            <div className="mpc-trace-head">
              <h3>Meridian Traces</h3>
              <span>Live</span>
            </div>
            <ul>
              {traces.map((trace, idx) => (
                <li key={trace.id || `${trace.title}-${idx}`} style={{ animationDelay: `${idx * 80}ms` }}>
                  <b>{trace.activity_type}</b>
                  <span>{traceLabel(trace)}</span>
                </li>
              ))}
            </ul>
          </section>
        </aside>
      </div>
    </div>
  );
}

export default MeridianProChalkTalk;
