/* eslint-disable react-refresh/only-export-components */
/**
 * Cross-section bridge into the concierge workspace.
 *
 * The bridge exposes:
 *  - `phase` as React state, so any section can highlight the active mode
 *    (e.g. the phase journey rail) without prop drilling.
 *  - `openConcierge(opts)` to focus the composer, switch phase, or submit a
 *    prompt from outside the workspace (hero CTA, system dry-run, etc.).
 *  - `register(handlers)` for the workspace to plug in its imperative
 *    handles (setPhase, sendMessage, etc.).
 */
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import type { Phase } from '../types';

export interface AgentHandlers {
  phase: Phase;
  setPhase: (phase: Phase) => void;
  setInput: (text: string) => void;
  focusComposer: () => void;
  sendMessage: (text: string, options?: { phase?: Phase }) => void;
  clearChat: () => void;
  replayLast: () => void;
}

export interface OpenConciergeOptions {
  phase?: Phase;
  prompt?: string;
  /** When true, submits the prompt immediately (if non-empty). */
  send?: boolean;
  focus?: boolean;
  clear?: boolean;
}

interface AgentBridgeValue {
  register: (handlers: AgentHandlers | null) => void;
  openConcierge: (opts?: OpenConciergeOptions) => void;
  phase: Phase;
}

const AgentBridgeContext = createContext<AgentBridgeValue | null>(null);

export function AgentBridgeProvider({ children }: { children: ReactNode }) {
  const handlersRef = useRef<AgentHandlers | null>(null);
  // Phase is React state (not a ref) so consumers re-render when the
  // workspace's active phase changes — the journey rail uses this to
  // light up the active step.
  const [phase, setPhase] = useState<Phase>(4);

  const register = useCallback((handlers: AgentHandlers | null) => {
    handlersRef.current = handlers;
    if (handlers) setPhase(handlers.phase);
  }, []);

  const openConcierge = useCallback((opts?: OpenConciergeOptions) => {
    const h = handlersRef.current;
    if (opts?.clear) h?.clearChat();
    if (opts?.phase != null) {
      h?.setPhase(opts.phase);
      setPhase(opts.phase);
    }
    if (opts?.prompt != null) h?.setInput(opts.prompt);
    if (opts?.send && opts.prompt?.trim()) {
      h?.sendMessage(opts.prompt.trim(), { phase: opts.phase });
    } else if (opts?.focus !== false) {
      h?.focusComposer();
    }
    document.getElementById('agent')?.scrollIntoView({ behavior: 'smooth' });
  }, []);

  const value = useMemo<AgentBridgeValue>(
    () => ({ register, openConcierge, phase }),
    [register, openConcierge, phase],
  );

  return <AgentBridgeContext.Provider value={value}>{children}</AgentBridgeContext.Provider>;
}

export function useAgentBridge(): AgentBridgeValue {
  const ctx = useContext(AgentBridgeContext);
  if (!ctx) {
    throw new Error('useAgentBridge must be used within AgentBridgeProvider');
  }
  return ctx;
}
