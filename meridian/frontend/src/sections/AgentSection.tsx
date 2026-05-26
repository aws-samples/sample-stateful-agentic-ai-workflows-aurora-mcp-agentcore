/**
 * AgentSection — Meridian Pro 3-pane workspace
 *
 * Left rail: traveler card · run config · starters
 * Center: chat with inline reasoning + recommendation grid + composer
 * Right: Gantt-style trace timeline with tabs (spans / memory / sql / cost)
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { FadeIn } from '../components/FadeIn';
import { ProConciergeResponse } from '../components/ProConciergeResponse';
import { ProTraceTimeline } from '../components/ProTraceTimeline';
import { DEMO_TRAVELER_ID, DEMO_PERSONA_FALLBACK } from '../components/TravelerPersona';
import { ProductThumb } from '../components/ProductThumb';
import { useAgentBridge } from '../context/AgentBridge';
import { enrichTraceActivities } from '../utils/traceTelemetry';
import {
  activitiesToStageSpans,
  buildReasoningChain,
  sumSpanLatency,
} from '../lib/activityToStageSpan';
import { fetchMemoryProfile, sendChatMessage, processOrder } from '../api/client';
import { DEMO_PROMPT } from '../lib/proDemoData';
import { PHASE_AGENT_MODE, PHASE_PILL } from '../lib/phaseLabels';
import {
  runConfigEmbedLabel,
  runConfigModelLabel,
  type BackendHealth,
} from '../lib/runConfig';
import type { ActivityEntry, LongTermMemoryFact, Message, Phase, Product } from '../types';

const PHASE_LABELS = PHASE_AGENT_MODE;

const PHASE_INFO: Record<Phase, {
  beat: string;
  capabilities: string[];
  starters: string[];
  highlight?: string;
}> = {
  1: {
    beat: 'The lab. Direct RDS Data API. Breaks on "romantic week in Europe."',
    capabilities: ['SQL WHERE filters', 'Trip type · operator · price', 'RDS Data API'],
    starters: ['City breaks', 'Beach & Resort', 'Business travel under $1500'],
    highlight: 'Romantic week in Europe',
  },
  2: {
    beat: 'MCP changes the interface, not the intelligence. Same gap as SQL.',
    capabilities: ['MCP run_query', 'Typed schema · IAM auth', 'Still keyword-only'],
    starters: ['Adventure & Outdoors', 'Wellness & Luxury', 'Tokyo culture trip'],
    highlight: 'Beach vacation with snorkeling',
  },
  3: {
    beat: 'Where natural language works. Cohere Embed v4 + hybrid pgvector + tsvector.',
    capabilities: ['Strands supervisor', 'Hybrid retrieval (1024d)', 'Specialist agents'],
    starters: [
      'Romantic week in Europe',
      'Weekend in Paris under $2k',
      'Family-friendly beach resort',
    ],
  },
  4: {
    beat: 'Production. AgentCore Runtime + Gateway + Memory + Identity. Aurora RLS for durable prefs.',
    capabilities: [
      'AgentCore Runtime session',
      'AgentCore Gateway MCP search',
      'AgentCore Memory mirror',
      'Aurora RLS + traveler_preferences',
    ],
    starters: [
      'Tokyo trip for two in October',
      'Beach escape under $2500 — remember our food allergies',
      'What did we discuss last time about Iceland?',
    ],
    highlight: DEMO_PROMPT,
  },
  5: {
    beat: 'LangGraph StateGraph: explicit, branchable, resumable. PostgresSaver checkpoints in Aurora.',
    capabilities: ['Conditional edges', 'PostgresSaver checkpoints', 'AgentCore + LangGraph + Strands'],
    starters: [
      'Watch our Tokyo dates and rebook the hotel if we slip a week',
      'Plan and hold our anniversary Tuscany trip end-to-end',
      'Resume the Iceland workflow we paused last month',
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

interface SkillSpec {
  name: string;
  agent: string;
  signature: string;
  args: { name: string; type: string; note?: string }[];
  returns: string;
  beat: string;
  file: string;
  example: string;
}

const PHASE_SKILLS: Record<Phase, SkillSpec[]> = {
  1: [
    {
      name: 'sql_filter',
      agent: 'SQLAgent',
      signature: 'run_sql(category, max_price)',
      args: [
        { name: 'category', type: 'str', note: 'e.g. "Beach & Resort"' },
        { name: 'max_price', type: 'float', note: 'optional ceiling' },
      ],
      returns: 'list[trip_package]',
      beat: 'Plain SQL WHERE clause on trip_packages via the RDS Data API.',
      file: 'agents/phase1/agent.py',
      example: 'SELECT * FROM trip_packages WHERE category = $1 AND price <= $2',
    },
  ],
  2: [
    {
      name: 'run_query',
      agent: 'postgres-mcp-server',
      signature: 'run_query(sql, params, dry_run=false)',
      args: [
        { name: 'sql', type: 'str', note: 'allow-listed SELECT' },
        { name: 'params', type: 'list', note: 'positional bind values' },
        { name: 'dry_run', type: 'bool', note: 'returns plan only' },
      ],
      returns: 'rows[]',
      beat: 'Same catalog queries — but exposed as a typed MCP tool with IAM auth.',
      file: 'mcp/postgres/server.py',
      example: 'POST /tools/run_query → Aurora via RDS Data API',
    },
  ],
  3: [
    {
      name: 'search',
      agent: 'SearchAgent',
      signature: '_semantic_search_tool(query, limit=5)',
      args: [
        { name: 'query', type: 'str', note: 'natural language' },
        { name: 'limit', type: 'int', note: 'default 5' },
      ],
      returns: 'list[trip_package + similarity]',
      beat: 'Hybrid pgvector + tsvector ranking on Cohere Embed v4 (1024d).',
      file: 'agents/phase3/search_agent.py',
      example: '"romantic week in Europe" → Tuscany, Provence, Lake Como',
    },
    {
      name: 'availability',
      agent: 'PackageAgent',
      signature: '_check_availability_tool(package_id, duration?)',
      args: [
        { name: 'package_id', type: 'str', note: 'e.g. "CTY-002"' },
        { name: 'duration', type: 'str?', note: 'e.g. "7 days"' },
      ],
      returns: 'departure_slots[]',
      beat: 'Departure slot lookup against trip_packages.availability.',
      file: 'agents/phase3/package_agent.py',
      example: 'CTY-002 · 7 days → 4 dates remaining in May',
    },
    {
      name: 'booking',
      agent: 'BookingAgent',
      signature: '_process_booking_tool(customer_id, items)',
      args: [
        { name: 'customer_id', type: 'str' },
        { name: 'items', type: 'list[dict]', note: 'package_id, travelers_count, duration' },
      ],
      returns: 'booking { booking_id, total, estimated_departure }',
      beat: 'Calculate, hold, and persist a booking against bookings + booking_lines.',
      file: 'agents/phase3/booking_agent.py',
      example: 'Hold CTY-002 · 7 days · 2 travelers → BKG-4F2C8A1B',
    },
  ],
  4: [
    {
      name: 'process_turn',
      agent: 'ProductionAgent',
      signature: 'process_turn(message, traveler_id, conversation_id)',
      args: [
        { name: 'message', type: 'str' },
        { name: 'traveler_id', type: 'str' },
        { name: 'conversation_id', type: 'str', note: 'optional' },
      ],
      returns: 'reply + products + memory_facts',
      beat: 'AgentCore Runtime + Gateway + MemoryAgent @tools + Aurora RLS.',
      file: 'agents/phase4/concierge.py',
      example: 'Wine country + no red-eyes → hybrid search under RLS scope',
    },
    {
      name: 'recall_session',
      agent: 'MemoryAgent',
      signature: 'recall_session_context(conversation_id, limit=6)',
      args: [
        { name: 'conversation_id', type: 'str' },
        { name: 'limit', type: 'int', note: 'recent turns' },
      ],
      returns: 'messages[]',
      beat: 'Short-term turn context from conversation_messages.',
      file: 'agents/phase4/memory_agent.py',
      example: 'Last 6 turns of conv_8a91 → "Tokyo · October · two"',
    },
    {
      name: 'recall_facts',
      agent: 'MemoryAgent',
      signature: 'recall_traveler_preferences(traveler_id, limit=8)',
      args: [
        { name: 'traveler_id', type: 'str' },
        { name: 'limit', type: 'int' },
      ],
      returns: 'facts[] { key, value, confidence }',
      beat: 'Long-term traveler facts from traveler_preferences.',
      file: 'agents/phase4/memory_agent.py',
      example: 'aj_chen → party=2, allergies=[shellfish], budget≤$3.2k',
    },
    {
      name: 'similar_trips',
      agent: 'MemoryAgent',
      signature: 'recall_similar_interactions(traveler_id, query, limit=3)',
      args: [
        { name: 'traveler_id', type: 'str' },
        { name: 'query', type: 'str' },
        { name: 'limit', type: 'int' },
      ],
      returns: 'interactions[] + similarity',
      beat: 'pgvector recall over interaction_embeddings.',
      file: 'agents/phase4/memory_agent.py',
      example: '"slow + warm" → past Sicily + Sardinia conversations',
    },
    {
      name: 'persist_turn',
      agent: 'MemoryAgent',
      signature: 'persist_turn(conversation_id, role, text, embedding)',
      args: [
        { name: 'conversation_id', type: 'str' },
        { name: 'role', type: 'str', note: 'user | assistant' },
        { name: 'text', type: 'str' },
        { name: 'embedding', type: 'vector(1024)' },
      ],
      returns: 'message_id',
      beat: 'Write turn + embedding so the next session knows this one happened.',
      file: 'agents/phase4/memory_agent.py',
      example: 'Insert into conversation_messages + interaction_embeddings',
    },
  ],
  5: [
    {
      name: 'classify',
      agent: 'OrchestrationAgent',
      signature: 'classify_intent(state)',
      args: [{ name: 'state', type: 'WorkflowState', note: 'last user msg' }],
      returns: 'intent ∈ {search, availability, recall, plan}',
      beat: 'Branches the StateGraph to the right specialist edge.',
      file: 'agents/phase5/workflow.py',
      example: '"watch our dates" → plan (long-running)',
    },
    {
      name: 'checkpoint',
      agent: 'PostgresSaver',
      signature: 'save_checkpoint(thread_id, state)',
      args: [
        { name: 'thread_id', type: 'str', note: 'durable workflow id' },
        { name: 'state', type: 'WorkflowState' },
      ],
      returns: 'checkpoint_id',
      beat: 'Aurora-backed checkpoints — resume weeks later.',
      file: 'agents/phase5/workflow.py',
      example: 'thread th_2614 paused at "watch dates" → resumed in October',
    },
    {
      name: 'synthesize',
      agent: 'OrchestrationAgent',
      signature: 'synthesize_reply(state)',
      args: [{ name: 'state', type: 'WorkflowState' }],
      returns: 'message + follow_ups',
      beat: 'Compose final reply from accumulated tool outputs.',
      file: 'agents/phase5/workflow.py',
      example: 'search + availability + recall → "Hold CTY-002, 4 dates left"',
    },
  ],
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
  const [backendHealth, setBackendHealth] = useState<BackendHealth | null>(null);
  const [traceId, setTraceId] = useState<string | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [memoryFacts, setMemoryFacts] = useState<LongTermMemoryFact[]>([]);
  const [, setItinerary] = useState<ItineraryItem[]>([]);
  const [activeTraceTab, setActiveTraceTab] = useState<'spans' | 'memory' | 'sql' | 'cost'>('spans');
  const [activeSkill, setActiveSkill] = useState<string | null>(null);
  const [selectedSpanIdx, setSelectedSpanIdx] = useState<number | null>(null);
  const [lastQuery, setLastQuery] = useState<string | null>(null);

  const chatFeedRef = useRef<HTMLDivElement>(null);
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

  // Backend health (+ Bedrock model id for Run config)
  useEffect(() => {
    const check = async () => {
      try {
        const res = await fetch('http://localhost:8000/health');
        if (res.ok) {
          setConnectionStatus('connected');
          const data = (await res.json()) as BackendHealth;
          setBackendHealth(data);
        } else {
          setConnectionStatus('disconnected');
        }
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

  // Auto-scroll the chat feed only — never the page. scrollIntoView would
  // walk every scroll ancestor and yank the whole window up, hiding the chat.
  const scrollChatToBottom = (behavior: ScrollBehavior = 'smooth') => {
    const feed = chatFeedRef.current;
    if (!feed) return;
    requestAnimationFrame(() => {
      feed.scrollTo({ top: feed.scrollHeight, behavior });
    });
  };
  const prevMsgCount = useRef(0);
  const wasTyping = useRef(false);
  useEffect(() => {
    if (msgs.length > prevMsgCount.current) {
      scrollChatToBottom();
    }
    prevMsgCount.current = msgs.length;
  }, [msgs]);
  useEffect(() => {
    if (typing && !wasTyping.current && msgs.length > 0) {
      scrollChatToBottom();
    }
    wasTyping.current = typing;
  }, [typing, msgs.length]);
  useEffect(() => {
    if (typing && (acts.length > 0 || pendingActs.length > 0)) {
      scrollChatToBottom('auto');
    }
  }, [typing, acts.length, pendingActs.length]);

  const revealActivitiesProgressively = (
    activities: ActivityEntry[],
    onComplete: () => void,
    forPhase: Phase = phase,
  ) => {
    if (activities.length === 0) {
      onComplete();
      return;
    }
    const delay = phaseDelays[forPhase];
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

  const send = async (overrideText?: string, sendOpts?: { phase?: Phase }) => {
    const text = (overrideText ?? input).trim();
    if (!text || typing) return;
    const effectivePhase = sendOpts?.phase ?? phase;
    lastUserTextRef.current = text;
    setLastQuery(text);
    setInput('');
    const userMsg: Message = { role: 'user', text };
    const history = [...msgs, userMsg];
    setMsgs((p) => [...p, userMsg]);
    setTyping(true);
    setActs([]);
    setCurrentStep(-1);
    setPendingActs([]);
    setFollowUps([]);
    setSelectedSpanIdx(null);

    const tid = ensureTraceId();

    if (activityTimerRef.current) {
      clearTimeout(activityTimerRef.current);
      activityTimerRef.current = null;
    }

    try {
      const response = await sendChatMessage({
        message: text,
        phase: effectivePhase,
        ...(effectivePhase === 4
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
        enrichTraceActivities(effectivePhase, text, response.activities, tid, history, {
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
        effectivePhase,
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
    setActiveSkill(null);
    setSelectedSpanIdx(null);
    setLastQuery(null);
  }, []);

  const switchPhase = (i: number) => applyPhase((i + 1) as Phase);

  const clearChat = useCallback(() => {
    setMsgs([]);
    setActs([]);
    setPendingActs([]);
    setCurrentStep(-1);
    setFollowUps([]);
    setSelectedSpanIdx(null);
    setLastQuery(null);
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
      sendMessage: (text, options) => {
        void sendRef.current(text, options);
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

  const stageSpans = useMemo(() => activitiesToStageSpans(acts), [acts]);
  const reasoningChain = useMemo(() => buildReasoningChain(stageSpans), [stageSpans]);
  const spanLatencyTotal = useMemo(() => sumSpanLatency(stageSpans), [stageSpans]);

  const lastBotMessage = useMemo(() => {
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].role === 'bot') return msgs[i].text;
    }
    return '';
  }, [msgs]);

  const primaryProduct = useMemo(() => {
    for (let i = msgs.length - 1; i >= 0; i--) {
      const m = msgs[i];
      if (m.role === 'bot' && m.type === 'products' && m.products?.length) {
        return m.products[0];
      }
    }
    return null;
  }, [msgs]);

  const modelSpanIdx = useMemo(
    () => stageSpans.findIndex((s) => s.kind === 'model'),
    [stageSpans],
  );

  const traceActiveIndex =
    currentStep >= 0
      ? currentStep
      : !typing && stageSpans.length > 0
        ? stageSpans.length - 1
        : -1;

  const replyPhase: 'pending' | 'composing' | 'composed' = (() => {
    if (typing) {
      if (!stageSpans.length) return 'pending';
      if (modelSpanIdx === -1) {
        return traceActiveIndex >= stageSpans.length - 1 ? 'composing' : 'pending';
      }
      if (traceActiveIndex < modelSpanIdx) return 'pending';
      return 'composing';
    }
    if (lastBotMessage && stageSpans.length) return 'composed';
    return 'pending';
  })();

  const traceLive = currentStep >= 0;
  const memorySpanLive = traceActiveIndex >= 0 && stageSpans[traceActiveIndex]?.kind === 'memory';
  const totalSpans = acts.length + pendingActs.length;
  const sqlSpans = acts.filter((a) => Boolean(a.sql_query));
  const memorySpans = acts.filter((a) =>
    ['memory_short', 'memory_long'].includes(a.telemetry?.category ?? ''),
  );

  const skills = PHASE_SKILLS[phase];
  const activeSkillSpec = skills.find((s) => s.name === activeSkill) ?? null;

  return (
    <section id="agent" className="mp-section mp-section--workspace">
      <FadeIn>
        <div className="mp-section-h-row">
          <div className="mp-section-h">
            <div className="mp-label-row">Phases 1–5 · live workspace</div>
            <h2>The room where the concierge works.</h2>
            <p>
              Three panes: traveler context on the left, dialogue in the middle, a cinematic trace
              on the right that <em>produces</em> the grounded concierge reply — same detail as the
              kiosk demo, in Daylight Studio.
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
                    <span className="pdot" /> {PHASE_PILL[p]}
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

          {/* Skills strip — what the supervisor can call in this phase */}
          <div className="mp-skills" data-p={String(phase)}>
            <div className="mp-skills-label">
              Skills
              <span className="mp-skills-sub">
                Phase {phase} · {skills.length} {skills.length === 1 ? 'tool' : 'tools'}
              </span>
            </div>
            <div className="mp-skills-row">
              {skills.map((s) => (
                <button
                  key={s.name}
                  type="button"
                  className={`mp-skill-pill${activeSkill === s.name ? ' active' : ''}`}
                  onClick={() =>
                    setActiveSkill((cur) => (cur === s.name ? null : s.name))
                  }
                  title={s.beat}
                >
                  <span className="dot" />
                  <span className="nm">{s.name}</span>
                  <span className="ag">{s.agent}</span>
                </button>
              ))}
            </div>
            {activeSkillSpec && (
              <div className="mp-skill-pop">
                <div className="mp-skill-pop-h">
                  <div>
                    <div className="ttl">
                      <code>{activeSkillSpec.name}</code>
                      <span className="agent">{activeSkillSpec.agent}</span>
                    </div>
                    <div className="beat">{activeSkillSpec.beat}</div>
                  </div>
                  <button
                    type="button"
                    className="close"
                    onClick={() => setActiveSkill(null)}
                    aria-label="Close"
                  >
                    ×
                  </button>
                </div>
                <div className="mp-skill-pop-grid">
                  <div className="cell">
                    <div className="k">Signature</div>
                    <pre>{activeSkillSpec.signature}</pre>
                  </div>
                  <div className="cell">
                    <div className="k">Args</div>
                    <ul>
                      {activeSkillSpec.args.map((a) => (
                        <li key={a.name}>
                          <code>{a.name}</code>
                          <span className="ty">: {a.type}</span>
                          {a.note && <span className="nt"> — {a.note}</span>}
                        </li>
                      ))}
                    </ul>
                  </div>
                  <div className="cell">
                    <div className="k">Returns</div>
                    <pre>{activeSkillSpec.returns}</pre>
                  </div>
                  <div className="cell wide">
                    <div className="k">Example</div>
                    <pre>{activeSkillSpec.example}</pre>
                  </div>
                </div>
                <div className="mp-skill-pop-foot">
                  <code>{activeSkillSpec.file}</code>
                </div>
              </div>
            )}
          </div>

          {/* 3-pane grid */}
          <div className="mp-ws-grid">
            {/* LEFT RAIL */}
            <aside className="mp-ws-side">
              <div className="mp-side-h">Traveler</div>
              <div className="mp-traveler-card mp-fancy-panel">
                <div className="mp-tv-head">
                  <div className="mp-tv-avatar">A·J</div>
                  <div className="mp-tv-meta">
                    <div className="name">{DEMO_PERSONA_FALLBACK.full_name ?? 'Alex & Jordan Chen'}</div>
                    <div className="sub">{DEMO_TRAVELER_ID}</div>
                  </div>
                </div>
                {lastQuery && (
                  <div className={`mp-intent-prompt${memorySpanLive ? ' memory-live' : ''}`}>
                    <span className="mp-intent-label">Current intent</span>
                    <p>{lastQuery}</p>
                  </div>
                )}
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
              <div className="mp-side-card mp-fancy-panel">
                <div className="row"><span>Mode</span><b>{PHASE_LABELS[phase]}</b></div>
                <div className="row">
                  <span>Model</span>
                  <b>{runConfigModelLabel(phase, backendHealth)}</b>
                </div>
                {phase >= 3 && (
                  <div className="row">
                    <span>Embed</span>
                    <b>{runConfigEmbedLabel(phase, backendHealth)}</b>
                  </div>
                )}
                <div className="row">
                  <span>Stack</span>
                  <b>
                    {phase === 1 ? 'RDS Data API'
                      : phase === 2 ? 'MCP · run_query'
                      : phase === 3 ? 'Strands · supervisor'
                      : phase === 4 ? 'Strands · @tool memory'
                      : 'LangGraph · StateGraph'}
                  </b>
                </div>
                <div className="row">
                  <span>Aurora</span>
                  <b>
                    {phase <= 2 ? 'WHERE filters'
                      : phase === 3 ? 'pgvector + tsvector'
                      : phase === 4 ? 'RLS · audit row'
                      : 'PostgresSaver checkpoint'}
                  </b>
                </div>
                <div className="row">
                  <span>State</span>
                  <b>{phase >= 5 ? 'durable + resumable' : phase === 4 ? 'session + memory' : 'stateless'}</b>
                </div>
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
                  fontSize: 13,
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
              <div className="mp-chat-feed" ref={chatFeedRef}>
                {msgs.length === 0 && !typing && (
                  <div className="mp-turn bot">
                    <div className="av">M</div>
                    <div className="mp-bubble">
                      <p style={{ margin: 0 }}>
                        Hi — pick a starter on the left, or describe the trip you have in mind. In
                        Phase 4 is production mode — AgentCore Runtime, Gateway, Memory, and Aurora
                        RLS ground every reply in your stored traveler facts.
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
                                    style={{ width: '100%', height: '100%', borderRadius: 12 }}
                                    emojiSize={40}
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
                            <span className="tag">
                              ▸ {phase >= 3 ? 'supervisor' : PHASE_LABELS[phase].toLowerCase()}
                            </span>{' '}
                            →{' '}
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
                      fontSize: 13,
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
                    <button
                      type="button"
                      title="Replay last query"
                      onClick={() =>
                        lastUserTextRef.current && void send(lastUserTextRef.current)
                      }
                      disabled={!lastUserTextRef.current || typing}
                    >
                      ↺
                    </button>
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
                {activeTraceTab === 'spans' && (
                  <>
                    <ProTraceTimeline
                      spans={stageSpans}
                      activities={acts}
                      activeIndex={traceActiveIndex}
                      selectedIndex={selectedSpanIdx}
                      onSelect={setSelectedSpanIdx}
                      totalLatencyMs={spanLatencyTotal || totalMs}
                    />
                    <ProConciergeResponse
                      reply={lastBotMessage || 'Your grounded reply will appear here as the trace completes.'}
                      reasoning={reasoningChain}
                      phase={replyPhase}
                      primaryProduct={primaryProduct}
                      visible={typing || Boolean(lastBotMessage) || stageSpans.length > 0}
                    />
                  </>
                )}

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
                              fontSize: 12,
                              fontFamily: 'ui-monospace, "SF Mono", monospace',
                              color: 'var(--mp-dim)',
                            }}
                          >
                            {f.key}
                          </div>
                          <div style={{ fontSize: 14, color: 'var(--mp-ink)', marginTop: 2 }}>
                            {f.value}
                          </div>
                          {(f.confidence != null || f.source) && (
                            <div
                              style={{
                                fontSize: 11.5,
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
                              fontSize: 12,
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
                      fontSize: 14,
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
                      {phase >= 3 ? (
                        <>
                          Bedrock <code>{runConfigModelLabel(phase, backendHealth)}</code>
                          {' · '}
                          {runConfigEmbedLabel(phase, backendHealth)} · pgvector HNSW
                        </>
                      ) : (
                        <>No LLM this phase · SQL/MCP only · pricing N/A</>
                      )}
                      {' · '}approximate
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
