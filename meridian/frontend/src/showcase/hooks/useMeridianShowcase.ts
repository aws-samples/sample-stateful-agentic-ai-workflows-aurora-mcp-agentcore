import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  fetchHealth,
  fetchMemoryProfile,
  processOrder,
  sendChatMessage,
} from '../../api/client';
import type { ChatResponse, LongTermMemoryFact, Message, OrderResponse, Phase, Product } from '../../types';
import { runConfigEmbedLabel, runConfigModelLabel, type BackendHealth } from '../../lib/runConfig';
import {
  SHOWCASE_EXAMPLE_PROMPTS,
  SHOWCASE_PHASES,
  chatResponseToMessages,
  chatResponseToTraceSpans,
  healthResponseToStatus,
  memoryResponseToFacts,
  phaseLabelFor,
  productsFromChatResponse,
  type BackendStatus,
  type ShowcaseTraceSpan,
  type ShowcaseTraceTab,
} from '../lib/showcaseAdapters';
import { SHOWCASE_INITIAL_PROMPT, SHOWCASE_TRAVELER_ID } from '../lib/showcaseFallbackData';

export interface ActionDrawerState {
  kind: 'hold' | 'plan' | 'compare' | 'save';
  product: Product;
  message: string;
  order?: OrderResponse['order'];
  live: boolean;
}

// Refinement filters captured by the action-chip popovers below the
// composer. They get appended to whatever prompt the presenter types
// when submitPrompt fires, so the agent sees the full traveler intent
// without the composer text getting noisy.
export interface ChatFilters {
  travelers: number; // 0 = unset
  startDate: string | null; // YYYY-MM-DD
  endDate: string | null;
  spa: boolean;
  directFlights: boolean;
}

export const EMPTY_FILTERS: ChatFilters = {
  travelers: 0,
  startDate: null,
  endDate: null,
  spa: false,
  directFlights: false,
};

export interface MeridianShowcaseState {
  selectedPhase: Phase;
  phaseLabel: string;
  /** Transient "what this rung adds" callout, set when the presenter
   *  advances to a higher phase. Null when dismissed or on a backward
   *  switch. Drives the phase-diff banner. */
  phaseHint: { label: string; adds: string; tech?: string } | null;
  dismissPhaseHint: () => void;
  travelerId: string;
  messages: Message[];
  currentPrompt: string;
  recommendations: Product[];
  selectedTrip: Product | null;
  savedTripIds: Set<string>;
  memoryFacts: LongTermMemoryFact[];
  traceSpans: ShowcaseTraceSpan[];
  traceTab: ShowcaseTraceTab;
  expandedSpanId: string | null;
  replayIndex: number;
  isReplaying: boolean;
  isLoading: boolean;
  error: string | null;
  backendStatus: BackendStatus;
  backendHealth: BackendHealth | null;
  isFallbackMode: boolean;
  conversationId: string | null;
  lastPrompt: string | null;
  actionDrawer: ActionDrawerState | null;
  modelLabel: string;
  embedLabel: string;
  totalLatencyMs: number;
  phaseExamples: string[];
  chatFilters: ChatFilters;
  setChatFilters: (next: ChatFilters) => void;
  resetChatFilters: () => void;
  setCurrentPrompt: (value: string) => void;
  setTraceTab: (tab: ShowcaseTraceTab) => void;
  setExpandedSpanId: (id: string | null) => void;
  setSelectedTrip: (product: Product | null) => void;
  setSelectedPhase: (phase: Phase) => void;
  submitPrompt: (prompt?: string) => Promise<void>;
  applyPhaseExample: (prompt: string, runImmediately?: boolean) => Promise<void>;
  replayLastPrompt: () => Promise<void>;
  replayTrace: () => void;
  selectTrip: (product: Product) => void;
  holdTrip: (product: Product) => void;
  planTrip: (product: Product) => Promise<void>;
  saveTrip: (product: Product) => void;
  compareTrip: (product: Product) => void;
  closeActionDrawer: () => void;
  clearError: () => void;
  clearChat: () => void;
  // True only when the latest bot reply's typewriter has finished
  // revealing. While false, downstream surfaces (recommendation grid)
  // wait so they don't appear before the message reads as complete.
  latestStreamComplete: boolean;
  markLatestStreamComplete: () => void;
}

// Clean slate by design — the chat transcript stays empty until the
// presenter types a real prompt and Aurora streams the first turn back.
const INITIAL_MESSAGES: Message[] = [];

function formatDateLabel(iso: string): string {
  // ISO YYYY-MM-DD into "Sep 14" — readable inside the prompt text.
  const [, m, d] = iso.split('-');
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
  const monthIdx = Math.max(0, Math.min(11, Number(m) - 1));
  return `${months[monthIdx]} ${Number(d)}`;
}

export function decoratePromptWithFilters(prompt: string, filters: ChatFilters): string {
  const fragments: string[] = [];
  if (filters.travelers > 0) {
    fragments.push(filters.travelers === 1 ? 'for 1 traveler' : `for ${filters.travelers} travelers`);
  }
  if (filters.startDate && filters.endDate) {
    fragments.push(`between ${formatDateLabel(filters.startDate)} and ${formatDateLabel(filters.endDate)}`);
  } else if (filters.startDate) {
    fragments.push(`starting ${formatDateLabel(filters.startDate)}`);
  }
  if (filters.spa) fragments.push('with spa access included');
  if (filters.directFlights) fragments.push('with direct flights only');

  if (fragments.length === 0) return prompt;
  // Use commas + Oxford-style "and" for the final fragment so the
  // composed sentence reads naturally for the agent.
  const tail =
    fragments.length === 1
      ? fragments[0]
      : `${fragments.slice(0, -1).join(', ')}, ${fragments[fragments.length - 1]}`;
  return `${prompt} (${tail})`;
}

const PHASE_DELAYS: Record<Phase, number> = { 1: 420, 2: 360, 3: 300, 4: 280, 5: 260 };

export function useMeridianShowcase(): MeridianShowcaseState {
  // Start the showcase at Phase 1 (SQL) so a stage walk-through can begin
  // with the simplest data path — direct SQL filters over Aurora — and
  // progressively introduce MCP, Retrieval, Production, and Workflow.
  const [selectedPhase, setSelectedPhaseState] = useState<Phase>(1);
  const [phaseHint, setPhaseHint] = useState<MeridianShowcaseState['phaseHint']>(null);
  const [messages, setMessages] = useState<Message[]>(INITIAL_MESSAGES);
  const [currentPrompt, setCurrentPrompt] = useState(SHOWCASE_INITIAL_PROMPT);
  const [recommendations, setRecommendations] = useState<Product[]>([]);
  const [selectedTrip, setSelectedTrip] = useState<Product | null>(null);
  const [savedTripIds, setSavedTripIds] = useState<Set<string>>(new Set());
  const [memoryFacts, setMemoryFacts] = useState<LongTermMemoryFact[]>([]);
  const [traceSpans, setTraceSpans] = useState<ShowcaseTraceSpan[]>([]);
  const [traceTab, setTraceTab] = useState<ShowcaseTraceTab>('spans');
  const [expandedSpanId, setExpandedSpanId] = useState<string | null>(null);
  const [replayIndex, setReplayIndex] = useState(-1);
  const [isReplaying, setIsReplaying] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  // True once the latest bot reply's typewriter has finished revealing.
  // Reset to false at the moment a new chat request fires; flipped back
  // to true by ChatMessage when its typewriter reaches the end of the
  // text. Initially true so the empty state isn't held back.
  const [latestStreamComplete, setLatestStreamComplete] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [backendStatus, setBackendStatus] = useState<BackendStatus>('checking');
  const [backendHealth, setBackendHealth] = useState<BackendHealth | null>(null);
  // No fallback mode — /showcase is live-Aurora-only. The flag remains in
  // state so existing consumers that read it still type-check, but it stays
  // false for the lifetime of the session.
  const [isFallbackMode] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [lastPrompt, setLastPrompt] = useState<string | null>(null);
  const [actionDrawer, setActionDrawer] = useState<ActionDrawerState | null>(null);
  const [chatFilters, setChatFiltersState] = useState<ChatFilters>(EMPTY_FILTERS);
  const replayTimers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const mounted = useRef(true);

  // Safety net: force-mark the latest reply as complete if it has been
  // streaming for too long. Without this, a typewriter that fails to
  // notify completion (component unmount mid-stream, regex crash in the
  // markdown source, etc.) leaves the UI permanently hiding the
  // recommendation grid and any other gated surface.
  //
  // The window starts when isLoading flips false (chat response arrived)
  // and lasts 6 seconds - longer than any typewriter run (max ~1.7s).
  useEffect(() => {
    if (latestStreamComplete) return;
    if (isLoading) return; // still waiting on the backend; don't time out yet
    const id = window.setTimeout(() => {
      setLatestStreamComplete(true);
    }, 6000);
    return () => window.clearTimeout(id);
  }, [latestStreamComplete, isLoading]);

  const clearReplayTimers = useCallback(() => {
    replayTimers.current.forEach((timer) => clearTimeout(timer));
    replayTimers.current = [];
  }, []);

  useEffect(() => {
    // React 18 StrictMode runs the effect+cleanup pair twice in dev. We must
    // re-arm `mounted` at the start of every mount, otherwise the first
    // cleanup permanently flips it to false and async catch handlers bail
    // out before they can set fallback state on the surviving instance.
    mounted.current = true;
    return () => {
      mounted.current = false;
      clearReplayTimers();
    };
  }, [clearReplayTimers]);

  useEffect(() => {
    const loadHealth = async () => {
      try {
        const health = await fetchHealth<BackendHealth>();
        if (!mounted.current) return;
        setBackendHealth(health);
        setBackendStatus(healthResponseToStatus(health));
      } catch {
        if (!mounted.current) return;
        setBackendStatus('offline');
        // The "Backend offline" badge in the top bar is enough on its own —
        // no banner needed unless the user actively tries to chat or plan.
      }
    };

    void loadHealth();
    const interval = setInterval(loadHealth, 30000);
    return () => clearInterval(interval);
  }, []);

  // Aurora-only memory prefetch on mount: pulls the seeded
  // traveler_preferences for SHOWCASE_TRAVELER_ID so the "For you"
  // panel shows real facts (no_red_eye, vegetarian_friendly, boutique
  // style, $3,200 cap, ...) regardless of which phase is active. No
  // fixture fallback - if Aurora is offline the panel stays empty,
  // which honestly matches the "live data only" philosophy.
  useEffect(() => {
    let cancelled = false;
    const loadMemory = async () => {
      try {
        const profile = await fetchMemoryProfile(SHOWCASE_TRAVELER_ID);
        if (cancelled || !mounted.current) return;
        const facts = memoryResponseToFacts(profile);
        if (facts.length) setMemoryFacts(facts);
      } catch {
        if (cancelled || !mounted.current) return;
        // Quietly leave the panel empty - the "Backend offline" badge
        // already tells the user why.
      }
    };
    void loadMemory();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    // Clean-slate showcase: do NOT prefetch featured packages on mount —
    // we want the recommendation grid to stay empty until the presenter
    // submits a real prompt and Aurora streams back the matching trips.
    //
    // Traveler memory is also left empty by default; once the presenter
    // graduates to Phase 4/5 the chat response carries `memory_facts`
    // that populate the right rail naturally.
    return undefined;
  }, []);

  const replayTrace = useCallback(() => {
    clearReplayTimers();
    if (!traceSpans.length) {
      setReplayIndex(-1);
      setIsReplaying(false);
      return;
    }
    setIsReplaying(true);
    setReplayIndex(-1);
    traceSpans.forEach((_, index) => {
      const timer = setTimeout(() => {
        setReplayIndex(index);
        if (index === traceSpans.length - 1) {
          const finish = setTimeout(() => setIsReplaying(false), 520);
          replayTimers.current.push(finish);
        }
      }, index * 320);
      replayTimers.current.push(timer);
    });
  }, [clearReplayTimers, traceSpans]);

  const applyChatResponse = useCallback((prompt: string, response: ChatResponse) => {
    // The user-side bubble was already appended optimistically when they
    // hit Send (see submitPrompt). Drop any pre-existing user bubble for
    // THIS prompt before re-applying the full pair so we don't get a
    // double-render. chatResponseToMessages always appends [user, bot];
    // we trim the trailing optimistic user bubble first to keep history
    // clean.
    setMessages((prior) => {
      const trimmed =
        prior.length > 0 &&
        prior[prior.length - 1].role === 'user' &&
        prior[prior.length - 1].text === prompt
          ? prior.slice(0, -1)
          : prior;
      return chatResponseToMessages(trimmed, prompt, response);
    });
    const nextTrace = chatResponseToTraceSpans(response, prompt);
    setTraceSpans(nextTrace);
    setExpandedSpanId(nextTrace[0]?.id ?? null);
    setTraceTab('spans');
    if (response.conversation_id) setConversationId(response.conversation_id);
    if (response.memory_facts?.length) setMemoryFacts(response.memory_facts);
    const products = productsFromChatResponse(response);
    // Always sync the recommendation grid to THIS turn's products. If the
    // turn returned 0 (a zero-result query, like SQL keyword search
    // failing on an intent prompt), clear the grid so the previous turn's
    // cards don't appear to be the latest result.
    setRecommendations(products);
    setSelectedTrip(products.length ? products[0] : null);
  }, []);

  const submitPrompt = useCallback(
    async (overridePrompt?: string) => {
      const baseRaw = (overridePrompt ?? currentPrompt).trim();
      if (!baseRaw || isLoading) return;

      // Decorate the user's prompt with the active action-chip filters so
      // the backend agent sees the full traveler intent. The decorated
      // string is what we send to /api/chat AND what we record as the
      // turn's user-facing message — that way the chat transcript shows
      // exactly what was searched.
      const decorated = decoratePromptWithFilters(baseRaw, chatFilters);

      clearReplayTimers();
      setReplayIndex(-1);
      setIsReplaying(false);
      setIsLoading(true);
      // Reset stream-complete so downstream surfaces (recommendation
      // grid) wait until the typewriter finishes revealing this turn.
      setLatestStreamComplete(false);
      setError(null);
      setLastPrompt(decorated);
      setCurrentPrompt('');

      // Echo the user's prompt into the transcript IMMEDIATELY so the
      // question doesn't disappear into a 5-12s dark hole while the
      // backend works. The "Running tools and composing..." bubble
      // (rendered by ChatTranscript when isLoading is true) sits below
      // it, giving the user a visible pulse from prompt → response.
      // applyChatResponse trims this optimistic bubble before re-applying
      // the full pair so we don't double-render.
      setMessages((prior) => [...prior, { role: 'user', text: decorated }]);

      try {
        const response = await sendChatMessage({
          message: decorated,
          phase: selectedPhase,
          ...(selectedPhase >= 4
            ? {
                customer_id: SHOWCASE_TRAVELER_ID,
                conversation_id: conversationId ?? undefined,
              }
            : {}),
        });
        if (!mounted.current) return;
        setBackendStatus('online');
        applyChatResponse(decorated, response);
        // Filters are per-turn - clear them after a successful submit so
        // the next prompt starts clean (matches the intuition of every
        // major chat product).
        setChatFiltersState(EMPTY_FILTERS);
      } catch {
        if (!mounted.current) return;
        setBackendStatus('offline');
        setError(
          'Live chat request failed. Confirm Aurora + FastAPI are running on localhost:8000 — the showcase only renders real Aurora data.',
        );
      } finally {
        if (mounted.current) setIsLoading(false);
      }
    },
    [applyChatResponse, chatFilters, clearReplayTimers, conversationId, currentPrompt, isLoading, selectedPhase],
  );

  const applyPhaseExample = useCallback(
    async (prompt: string, runImmediately = false) => {
      setCurrentPrompt(prompt);
      if (runImmediately) {
        await submitPrompt(prompt);
      }
    },
    [submitPrompt],
  );

  const replayLastPrompt = useCallback(async () => {
    if (lastPrompt) await submitPrompt(lastPrompt);
  }, [lastPrompt, submitPrompt]);

  const setSelectedPhase = useCallback((phase: Phase) => {
    let phaseChanged = false;
    setSelectedPhaseState((prev) => {
      phaseChanged = phase !== prev;
      // Surface the "what this rung adds" callout only when advancing to a
      // higher phase — that's the narrative beat (each mode composes onto
      // the last). Backward switches stay quiet so re-demoing an earlier
      // mode doesn't spam the banner.
      const meta = SHOWCASE_PHASES.find((p) => p.phase === phase);
      if (phase > prev && meta?.adds) {
        // Sticky: the callout stays until the presenter clicks Close.
        // These are deliberate talking points — auto-dismissing them
        // mid-sentence would undercut the narrative beat.
        setPhaseHint({ label: meta.label, adds: meta.adds, tech: meta.tech });
      } else {
        setPhaseHint(null);
      }
      return phase;
    });
    // Switching to a different phase auto-clears the conversation so each
    // mode starts from a clean slate — the presenter doesn't want Phase 2's
    // transcript bleeding into the Retrieval demo. Re-clicking the active
    // pill is a no-op (don't wipe an in-progress conversation). Phase choice
    // and Aurora-backed memory facts are preserved.
    if (phaseChanged) {
      clearReplayTimers();
      setMessages([]);
      setCurrentPrompt('');
      setLastPrompt(null);
      setRecommendations([]);
      setSelectedTrip(null);
      setTraceSpans([]);
      setExpandedSpanId(null);
      setTraceTab('spans');
      setReplayIndex(-1);
      setIsReplaying(false);
      setConversationId(null);
      setActionDrawer(null);
      setError(null);
      setChatFiltersState(EMPTY_FILTERS);
      setLatestStreamComplete(true);
    }
    // No auto-prompt: leave the composer empty so the presenter types
    // intent freshly for each phase walkthrough.
  }, [clearReplayTimers]);

  const dismissPhaseHint = useCallback(() => {
    setPhaseHint(null);
  }, []);

  const selectTrip = useCallback((product: Product) => {
    setSelectedTrip(product);
  }, []);

  const holdTrip = useCallback((product: Product) => {
    setSelectedTrip(product);
    setActionDrawer({
      kind: 'hold',
      product,
      message: `${product.name} is held for 12 hours in this local workspace.`,
      live: backendStatus === 'online' && !isFallbackMode,
    });
    setMessages((prior) => [
      ...prior,
      {
        role: 'bot',
        type: 'text',
        text: `Held ${product.name} for 12 hours. You can still compare or plan before confirming.`,
      },
    ]);
  }, [backendStatus, isFallbackMode]);

  const planTrip = useCallback(
    async (product: Product) => {
      if (isLoading) return;
      setSelectedTrip(product);
      setIsLoading(true);
      setLatestStreamComplete(false);
      setError(null);
      const prompt = `Plan trip: ${product.name}`;
      setMessages((prior) => [...prior, { role: 'user', text: prompt }]);
      try {
        const response = await processOrder({
          product_id: product.product_id,
          size: product.available_sizes?.[0] ?? undefined,
          quantity: 1,
          phase: selectedPhase,
        });
        if (!mounted.current) return;
        setMessages((prior) => [
          ...prior,
          { role: 'bot', type: response.order ? 'order' : 'text', text: response.message, order: response.order },
        ]);
        const nextTrace = chatResponseToTraceSpans(
          { message: response.message, activities: response.activities, order: response.order },
          prompt,
        );
        setTraceSpans(nextTrace);
        setExpandedSpanId(nextTrace[0]?.id ?? null);
        setActionDrawer({
          kind: 'plan',
          product,
          message: response.message,
          order: response.order,
          live: true,
        });
      } catch {
        if (!mounted.current) return;
        setBackendStatus('offline');
        setError(
          `Unable to plan ${product.name}: live booking endpoint is unavailable. Restart the FastAPI backend.`,
        );
      } finally {
        if (mounted.current) setIsLoading(false);
      }
    },
    [isLoading, selectedPhase],
  );

  const saveTrip = useCallback((product: Product) => {
    setSavedTripIds((prior) => {
      const next = new Set(prior);
      if (next.has(product.product_id)) next.delete(product.product_id);
      else next.add(product.product_id);
      return next;
    });
    setActionDrawer({
      kind: 'save',
      product,
      message: `Saved ${product.name} to this session.`,
      live: false,
    });
  }, []);

  const compareTrip = useCallback((product: Product) => {
    setSelectedTrip(product);
    setActionDrawer({
      kind: 'compare',
      product,
      message: `${product.name} is now pinned for comparison against ${recommendations[0]?.name ?? 'the top match'}.`,
      live: false,
    });
  }, [recommendations]);

  const clearChat = useCallback(() => {
    // Reset every per-conversation surface back to its empty state. The
    // Phase selector, traveler memory facts (those come from Aurora), and
    // saved trip set are intentionally preserved - the presenter usually
    // wants to keep their phase choice + remembered preferences when
    // wiping the visible conversation.
    clearReplayTimers();
    setMessages([]);
    setCurrentPrompt('');
    setLastPrompt(null);
    setRecommendations([]);
    setSelectedTrip(null);
    setTraceSpans([]);
    setExpandedSpanId(null);
    setTraceTab('spans');
    setReplayIndex(-1);
    setIsReplaying(false);
    setConversationId(null);
    setActionDrawer(null);
    setError(null);
    setChatFiltersState(EMPTY_FILTERS);
    setLatestStreamComplete(true);
  }, [clearReplayTimers]);

  // Lifted by ChatMessage when its typewriter reaches the end of the
  // text (or whenever a non-streaming render path completes).
  const markLatestStreamComplete = useCallback(() => {
    setLatestStreamComplete(true);
  }, []);

  const totalLatencyMs = useMemo(
    () => traceSpans.reduce((total, span) => total + span.latencyMs, 0),
    [traceSpans],
  );

  return {
    selectedPhase,
    phaseLabel: phaseLabelFor(selectedPhase),
    phaseHint,
    dismissPhaseHint,
    travelerId: SHOWCASE_TRAVELER_ID,
    messages,
    currentPrompt,
    recommendations,
    selectedTrip,
    savedTripIds,
    memoryFacts,
    traceSpans,
    traceTab,
    expandedSpanId,
    replayIndex,
    isReplaying,
    isLoading,
    error,
    backendStatus,
    backendHealth,
    isFallbackMode,
    conversationId,
    lastPrompt,
    actionDrawer,
    modelLabel: runConfigModelLabel(selectedPhase, backendHealth),
    embedLabel: runConfigEmbedLabel(selectedPhase, backendHealth),
    totalLatencyMs,
    phaseExamples: SHOWCASE_EXAMPLE_PROMPTS[selectedPhase] ?? [],
    chatFilters,
    setChatFilters: setChatFiltersState,
    resetChatFilters: () => setChatFiltersState(EMPTY_FILTERS),
    setCurrentPrompt,
    setTraceTab,
    setExpandedSpanId,
    setSelectedTrip,
    setSelectedPhase,
    submitPrompt,
    applyPhaseExample,
    replayLastPrompt,
    replayTrace,
    selectTrip,
    holdTrip,
    planTrip,
    saveTrip,
    compareTrip,
    closeActionDrawer: () => setActionDrawer(null),
    clearError: () => setError(null),
    clearChat,
    latestStreamComplete,
    markLatestStreamComplete,
  };
}

export { SHOWCASE_PHASES, PHASE_DELAYS };
