import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { fetchHealth, fetchMemoryProfile, fetchProducts, processOrder, sendChatMessage } from '../../api/client';
import type { LongTermMemoryFact, Message, OrderResponse, Phase, Product } from '../../types';
import { runConfigEmbedLabel, runConfigModelLabel, type BackendHealth } from '../../lib/runConfig';
import {
  SHOWCASE_PHASES,
  chatResponseToMessages,
  chatResponseToTraceSpans,
  healthResponseToStatus,
  memoryResponseToFacts,
  packagesResponseToRecommendations,
  phaseLabelFor,
  productsFromChatResponse,
  type BackendStatus,
  type ShowcaseTraceSpan,
  type ShowcaseTraceTab,
} from '../lib/showcaseAdapters';
import {
  SHOWCASE_FALLBACK_FACTS,
  SHOWCASE_FALLBACK_RECOMMENDATIONS,
  SHOWCASE_INITIAL_PROMPT,
  SHOWCASE_TRAVELER_ID,
  buildShowcaseFallbackChatResponse,
  buildShowcaseFallbackOrder,
} from '../lib/showcaseFallbackData';

export interface ActionDrawerState {
  kind: 'hold' | 'plan' | 'compare' | 'save';
  product: Product;
  message: string;
  order?: OrderResponse['order'];
  live: boolean;
}

export interface MeridianShowcaseState {
  selectedPhase: Phase;
  phaseLabel: string;
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
  estimatedCostUsd: number;
  setCurrentPrompt: (value: string) => void;
  setTraceTab: (tab: ShowcaseTraceTab) => void;
  setExpandedSpanId: (id: string | null) => void;
  setSelectedTrip: (product: Product | null) => void;
  setSelectedPhase: (phase: Phase) => void;
  submitPrompt: (prompt?: string) => Promise<void>;
  replayLastPrompt: () => Promise<void>;
  replayTrace: () => void;
  selectTrip: (product: Product) => void;
  holdTrip: (product: Product) => void;
  planTrip: (product: Product) => Promise<void>;
  saveTrip: (product: Product) => void;
  compareTrip: (product: Product) => void;
  closeActionDrawer: () => void;
  clearError: () => void;
}

const INITIAL_MESSAGES: Message[] = [
  {
    role: 'bot',
    type: 'text',
    text: 'Good morning, Alex. Tell me the trip you want, then watch Meridian route the request through live tools, memory, and trace.',
  },
];

const PHASE_DELAYS: Record<Phase, number> = { 1: 420, 2: 360, 3: 300, 4: 280, 5: 260 };

export function useMeridianShowcase(): MeridianShowcaseState {
  const [selectedPhase, setSelectedPhaseState] = useState<Phase>(4);
  const [messages, setMessages] = useState<Message[]>(INITIAL_MESSAGES);
  const [currentPrompt, setCurrentPrompt] = useState(SHOWCASE_INITIAL_PROMPT);
  const [recommendations, setRecommendations] = useState<Product[]>(SHOWCASE_FALLBACK_RECOMMENDATIONS);
  const [selectedTrip, setSelectedTrip] = useState<Product | null>(SHOWCASE_FALLBACK_RECOMMENDATIONS[0]);
  const [savedTripIds, setSavedTripIds] = useState<Set<string>>(new Set());
  const [memoryFacts, setMemoryFacts] = useState<LongTermMemoryFact[]>(SHOWCASE_FALLBACK_FACTS);
  const [traceSpans, setTraceSpans] = useState<ShowcaseTraceSpan[]>([]);
  const [traceTab, setTraceTab] = useState<ShowcaseTraceTab>('spans');
  const [expandedSpanId, setExpandedSpanId] = useState<string | null>(null);
  const [replayIndex, setReplayIndex] = useState(-1);
  const [isReplaying, setIsReplaying] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [backendStatus, setBackendStatus] = useState<BackendStatus>('checking');
  const [backendHealth, setBackendHealth] = useState<BackendHealth | null>(null);
  const [isFallbackMode, setIsFallbackMode] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [lastPrompt, setLastPrompt] = useState<string | null>(null);
  const [actionDrawer, setActionDrawer] = useState<ActionDrawerState | null>(null);
  const replayTimers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const mounted = useRef(true);

  const clearReplayTimers = useCallback(() => {
    replayTimers.current.forEach((timer) => clearTimeout(timer));
    replayTimers.current = [];
  }, []);

  useEffect(() => {
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
        setIsFallbackMode(true);
      }
    };

    void loadHealth();
    const interval = setInterval(loadHealth, 30000);
    return () => clearInterval(interval);
  }, []);

  useEffect(() => {
    const loadMemory = async () => {
      try {
        const response = await fetchMemoryProfile(SHOWCASE_TRAVELER_ID);
        if (!mounted.current) return;
        setMemoryFacts(memoryResponseToFacts(response));
      } catch {
        if (!mounted.current) return;
        setMemoryFacts(SHOWCASE_FALLBACK_FACTS);
        setIsFallbackMode(true);
      }
    };

    const loadPackages = async () => {
      try {
        const products = await fetchProducts(undefined, 9, true);
        if (!mounted.current) return;
        const next = packagesResponseToRecommendations(products);
        setRecommendations(next);
        setSelectedTrip((current) => current ?? next[0] ?? null);
      } catch {
        if (!mounted.current) return;
        setRecommendations(SHOWCASE_FALLBACK_RECOMMENDATIONS);
        setSelectedTrip((current) => current ?? SHOWCASE_FALLBACK_RECOMMENDATIONS[0]);
        setIsFallbackMode(true);
      }
    };

    void loadMemory();
    void loadPackages();
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

  const applyChatResponse = useCallback((prompt: string, response: ReturnType<typeof buildShowcaseFallbackChatResponse>) => {
    setMessages((prior) => chatResponseToMessages(prior, prompt, response));
    const nextTrace = chatResponseToTraceSpans(response, prompt);
    setTraceSpans(nextTrace);
    setExpandedSpanId(nextTrace[0]?.id ?? null);
    setTraceTab('spans');
    if (response.conversation_id) setConversationId(response.conversation_id);
    if (response.memory_facts?.length) setMemoryFacts(response.memory_facts);
    const products = productsFromChatResponse(response);
    if (products.length) {
      setRecommendations(products);
      setSelectedTrip(products[0]);
    }
  }, []);

  const submitPrompt = useCallback(
    async (overridePrompt?: string) => {
      const prompt = (overridePrompt ?? currentPrompt).trim();
      if (!prompt || isLoading) return;

      clearReplayTimers();
      setReplayIndex(-1);
      setIsReplaying(false);
      setIsLoading(true);
      setError(null);
      setLastPrompt(prompt);
      setCurrentPrompt('');

      try {
        const response = await sendChatMessage({
          message: prompt,
          phase: selectedPhase,
          ...(selectedPhase === 4
            ? {
                customer_id: SHOWCASE_TRAVELER_ID,
                conversation_id: conversationId ?? undefined,
              }
            : {}),
        });
        if (!mounted.current) return;
        setBackendStatus('online');
        applyChatResponse(prompt, response);
      } catch {
        if (!mounted.current) return;
        setBackendStatus('offline');
        setIsFallbackMode(true);
        const response = buildShowcaseFallbackChatResponse(prompt, selectedPhase);
        applyChatResponse(prompt, response);
        setError('Backend unavailable. Running the showcase with deterministic fixture data.');
      } finally {
        if (mounted.current) setIsLoading(false);
      }
    },
    [applyChatResponse, clearReplayTimers, conversationId, currentPrompt, isLoading, selectedPhase],
  );

  const replayLastPrompt = useCallback(async () => {
    if (lastPrompt) await submitPrompt(lastPrompt);
  }, [lastPrompt, submitPrompt]);

  const setSelectedPhase = useCallback((phase: Phase) => {
    setSelectedPhaseState(phase);
    setTraceSpans((spans) =>
      spans.map((span, index) =>
        index === 0
          ? {
              ...span,
              name: `${phaseLabelFor(phase)} mode selected`,
              details: `Subsequent requests will use phase ${phase}.`,
            }
          : span,
      ),
    );
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
        setIsFallbackMode(true);
        setBackendStatus('offline');
        const response = buildShowcaseFallbackOrder(product, selectedPhase);
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
          live: false,
        });
        setError('Booking backend unavailable. Created a local demo hold instead.');
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

  const totalLatencyMs = useMemo(
    () => traceSpans.reduce((total, span) => total + span.latencyMs, 0),
    [traceSpans],
  );
  const estimatedCostUsd = useMemo(
    () => traceSpans.reduce((total, span) => total + (span.costUsd ?? 0), 0),
    [traceSpans],
  );

  return {
    selectedPhase,
    phaseLabel: phaseLabelFor(selectedPhase),
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
    estimatedCostUsd,
    setCurrentPrompt,
    setTraceTab,
    setExpandedSpanId,
    setSelectedTrip,
    setSelectedPhase,
    submitPrompt,
    replayLastPrompt,
    replayTrace,
    selectTrip,
    holdTrip,
    planTrip,
    saveTrip,
    compareTrip,
    closeActionDrawer: () => setActionDrawer(null),
    clearError: () => setError(null),
  };
}

export { SHOWCASE_PHASES, PHASE_DELAYS };
