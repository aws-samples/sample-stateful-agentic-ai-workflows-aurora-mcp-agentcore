/**
 * API client for Meridian backend
 */
import {
  SESSION_ENDED_MESSAGE,
  STREAM_REFRESHED_MESSAGE,
  authorizedFetch,
  endSessionAfterStreamExpiry,
  refreshAfterStreamExpiry,
  requestJson,
} from './request';
import { getAccessToken, setBearerOrigin } from '../auth/accessToken';
import { CURRENT_TRAVELER } from './currentTraveler';
import { SignInRequiredError, TokenExpiredError, readChatStream, type ChatStreamEvent } from './chatStream';
import type {
  BookingRequest,
  BookingResponse,
  ChatRequest,
  ChatResponse,
  LongTermMemoryFact,
  MemoryProfileResponse,
  OrderRequest,
  OrderResponse,
  Product,
  ProductListResponse,
} from '../types';
import type {
  JourneyDocument,
  JourneySummary,
  SessionStop,
} from '../showcase/journey/types';

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, '');
}

interface BrowserLocation {
  protocol: string;
  hostname: string;
}

export function resolveBackendOriginFor(
  explicit: string | undefined,
  isDev: boolean,
  location?: BrowserLocation,
): string {
  if (explicit?.trim()) return trimTrailingSlash(explicit.trim());

  if (isDev || !location) return 'http://localhost:8013';

  const { protocol, hostname } = location;
  if (['localhost', '127.0.0.1', '[::1]', '::1'].includes(hostname)) {
    const localHostname = hostname === '::1' ? '[::1]' : hostname;
    return `${protocol}//${localHostname}:8013`;
  }

  return `${protocol}//${hostname}`;
}

function resolveBackendOrigin(): string {
  const explicit = import.meta.env.VITE_API_ORIGIN as string | undefined;
  return resolveBackendOriginFor(
    explicit,
    import.meta.env.DEV,
    typeof window === 'undefined' ? undefined : window.location,
  );
}

const BACKEND_ORIGIN = resolveBackendOrigin();
const explicitApiBase = import.meta.env.VITE_API_BASE_URL as string | undefined;
const API_BASE = explicitApiBase?.trim()
  ? trimTrailingSlash(explicitApiBase.trim())
  : `${BACKEND_ORIGIN}/api`;

export function healthOriginFor(apiBase: string, fallbackOrigin: string): string {
  try {
    return new URL(apiBase, fallbackOrigin).origin;
  } catch {
    return fallbackOrigin;
  }
}

const JSON_HEADERS = { 'Content-Type': 'application/json' };

export function healthUrlsFor(origin: string): string[] {
  const normalized = trimTrailingSlash(origin);
  // Public /health establishes process liveness only. It cannot replace a
  // failed authenticated API check (including an expired presenter session).
  return [`${normalized}/api/health`];
}

const API_ORIGIN = healthOriginFor(API_BASE, BACKEND_ORIGIN);
const HEALTH_URL_CANDIDATES = healthUrlsFor(API_ORIGIN);
// The access token goes to the API's origin and nowhere else.
setBearerOrigin(API_ORIGIN);

/**
 * Fetch all products from the backend
 */
export async function fetchProducts(category?: string, limit = 50, featured = false, signal?: AbortSignal): Promise<Product[]> {
  const params = new URLSearchParams();
  if (category) params.set('category', category);
  params.set('limit', limit.toString());
  if (featured) params.set('featured', 'true');
  
  const data = await requestJson<ProductListResponse>(`${API_BASE}/products?${params}`, { signal });
  return data.products;
}

/**
 * Fetch a single product by ID
 */
export async function fetchProduct(productId: string): Promise<Product> {
  return requestJson(`${API_BASE}/products/${encodeURIComponent(productId)}`);
}

/**
 * Stream a turn. An HTTP 401 is recovered by authorizedFetch. A token that expires inside the
 * stream is refreshed and the turn is sent again once, but only if nothing reached the caller
 * yet; after content the turn may have written state, so the person decides whether to resend.
 */
async function streamChatMessage(
  request: ChatRequest, signal: AbortSignal | undefined, onEvent: (event: ChatStreamEvent) => void,
): Promise<ChatResponse> {
  for (let attempt = 0; ; attempt += 1) {
    const response = await authorizedFetch(`${API_BASE}/chat/stream`, {
      method: 'POST', signal, cache: 'no-store',
      headers: { ...JSON_HEADERS, Accept: 'text/event-stream' }, body: JSON.stringify(request),
    });
    const refusedToken = getAccessToken();
    try {
      return await readChatStream(response, onEvent, signal);
    } catch (error) {
      if (error instanceof SignInRequiredError) {
        await refreshAfterStreamExpiry(refusedToken);
        endSessionAfterStreamExpiry();
        throw new Error(SESSION_ENDED_MESSAGE);
      }
      if (!(error instanceof TokenExpiredError)) throw error;
      if (error.contentDelivered) {
        void refreshAfterStreamExpiry(refusedToken);
        throw new Error(STREAM_REFRESHED_MESSAGE);
      }
      if (attempt > 0 || !await refreshAfterStreamExpiry(refusedToken)) {
        endSessionAfterStreamExpiry();
        throw new Error(SESSION_ENDED_MESSAGE);
      }
    }
  }
}

/**
 * Send a chat message to the AI assistant
 */
export async function sendChatMessage(request: ChatRequest, signal?: AbortSignal, onEvent?: (event: ChatStreamEvent) => void): Promise<ChatResponse> {
  if (request.phase === 4 && onEvent) {
    return streamChatMessage(request, signal, onEvent);
  }
  return requestJson(`${API_BASE}/chat`, {
    method: 'POST', signal, headers: JSON_HEADERS, body: JSON.stringify(request),
  });
}

/**
 * Fetch long-term memory profile from Aurora (Phase 4)
 */
export async function fetchMemoryProfile(travelerId = CURRENT_TRAVELER, signal?: AbortSignal): Promise<MemoryProfileResponse> {
  return requestJson(`${API_BASE}/memory/${encodeURIComponent(travelerId)}`, {
    signal,
  });
}

export async function updateMemoryFact(
  travelerId: string,
  key: string,
  value: string,
): Promise<LongTermMemoryFact> {
  return requestJson(
    `${API_BASE}/memory/${encodeURIComponent(travelerId)}/facts/${encodeURIComponent(key)}`,
    {
      method: 'PATCH',
      headers: JSON_HEADERS,
      body: JSON.stringify({ value }),
    },
  );
}

export async function deleteMemoryFact(travelerId: string, key: string): Promise<void> {
  return requestJson(
    `${API_BASE}/memory/${encodeURIComponent(travelerId)}/facts/${encodeURIComponent(key)}`,
    { method: 'DELETE' },
  );
}

export interface SessionIdentity {
  traveler_id: string;
  authentication: string;
}

/** Ask the API who it believes is calling. The answer comes from the verified credential. */
export async function fetchSessionIdentity(signal?: AbortSignal): Promise<SessionIdentity> {
  const identity = await requestJson<SessionIdentity>(`${API_BASE}/me`, { signal });
  if (typeof identity?.traveler_id !== 'string' || !identity.traveler_id) {
    throw new Error('The API did not name a traveler.');
  }
  return identity;
}

/**
 * Fetch authenticated backend status with the same deadline as other API calls.
 */
export async function fetchHealth<THealth = unknown>(signal?: AbortSignal): Promise<THealth> {
  return requestJson<THealth>(HEALTH_URL_CANDIDATES[0], { signal });
}

/**
 * Perform semantic search for products
 */
export async function searchProducts(query: string, phase: 1 | 2 | 3 = 3): Promise<ChatResponse> {
  return sendChatMessage({
    message: query,
    phase,
  });
}

/**
 * Process an order for a product
 */
export async function confirmBooking(request: BookingRequest, signal?: AbortSignal): Promise<BookingResponse> {
  return requestJson(`${API_BASE}/chat/book`, {
    method: 'POST', signal, headers: JSON_HEADERS, body: JSON.stringify(request),
  });
}

export async function processOrder(request: OrderRequest, signal?: AbortSignal): Promise<OrderResponse> {
  return requestJson(`${API_BASE}/chat/order`, {
    method: 'POST', signal, headers: JSON_HEADERS, body: JSON.stringify(request),
  });
}

export interface HoldLookup {
  conversationId: string;
  productId: string;
  duration: string;
  quantity: number;
}

export async function readHold(intent: HoldLookup, signal?: AbortSignal): Promise<OrderResponse> {
  const params = new URLSearchParams({ conversation_id: intent.conversationId, product_id: intent.productId,
    duration: intent.duration, quantity: String(intent.quantity) });
  return requestJson(`${API_BASE}/chat/holds?${params}`, { signal });
}

export async function readBooking(bookingId: string, signal?: AbortSignal): Promise<OrderResponse> {
  return requestJson(`${API_BASE}/chat/bookings/${encodeURIComponent(bookingId)}`, { signal });
}

/**
 * Governance probe (Phase 4): proves the workload grant, runs the same COUNT(*)
 * scoped vs unscoped, and returns live CREATE POLICY USING clauses.
 */
export interface RlsTableResult {
  table: string;
  scoped_count: number;
  unscoped_count: number;
  error?: string | null;
}

export interface RlsPolicy {
  table: string;
  policy: string;
  using_clause?: string | null;
}

export interface RlsProbeResponse {
  traveler_id: string;
  authorization: {
    provider: string;
    subject_id: string;
    principal: string;
    requested_traveler_id: string;
    decision: 'allow' | 'deny';
    binding_id?: string | null;
    audit_id?: string | null;
  };
  negative_control: {
    requested_traveler_id: string;
    display_name?: string | null;
    decision: 'allow' | 'deny' | 'not_applicable';
    reason?: string | null;
    audit_id?: string | null;
  };
  tables: RlsTableResult[];
  policies: RlsPolicy[];
  debug?: {
    effective_role?: string | null;
    rls_active?: boolean | null;
    scope?: string | null;
    authorization_provider?: string | null;
    authorization_subject?: string | null;
    error?: string | null;
  } | null;
}

export async function fetchRlsProbe(
  travelerId = CURRENT_TRAVELER,
): Promise<RlsProbeResponse> {
  return requestJson(`${API_BASE}/diagnostics/rls-probe`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({ traveler_id: travelerId }),
  });

}

export interface SessionReceiptLine {
  label: string;
  table: string;
  /** null when Aurora could not answer; never a stand-in for zero. */
  count: number | null;
  detail?: string | null;
  scoped: boolean;
}

export interface SessionReceiptResponse {
  traveler_id: string;
  since: string;
  lines: SessionReceiptLine[];
  authorization_subject?: string | null;
  durable_checkpoints: boolean;
  checkpoint_backend?: string | null;
  checkpoint_backend_durable?: boolean;
}

/** Everything this session committed to Aurora, counted table by table. */
export async function fetchSessionReceipt(
  travelerId = CURRENT_TRAVELER,
  windowMinutes = 90,
  conversationId: string | null = null,
): Promise<SessionReceiptResponse> {
  return requestJson(`${API_BASE}/diagnostics/session-receipt`, {
    method: 'POST',
    headers: JSON_HEADERS,
    body: JSON.stringify({
      traveler_id: travelerId,
      window_minutes: windowMinutes,
      // Scopes the checkpoint count to this session's workflow thread.
      conversation_id: conversationId,
    }),
  });

}

/**
 * List the caller's journeys, newest first.
 *
 * The shell needs a journey id before it can read a document, and a demo
 * machine should not have to be told one by hand.
 */
export async function fetchJourneys(limit = 10, signal?: AbortSignal, threadId?: string): Promise<JourneySummary[]> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (threadId) params.set("thread_id", threadId);
  const data = await requestJson<{ journeys: JourneySummary[] }>(`${API_BASE}/journeys?${params}`, { signal });
  return data.journeys ?? [];
}

/** Read one journey's evidence document. */
export async function fetchJourneyDocument(
  journeyId: string,
  signal?: AbortSignal,
): Promise<JourneyDocument> {
  const document = await requestJson<JourneyDocument>(
    `${API_BASE}/journeys/${encodeURIComponent(journeyId)}`, { signal },
  );
  return { ...document, received_at: Date.now() };
}

export type StopSessionResponse = {
  stopped: boolean;
  runtime_session_id: string;
  outcome: SessionStop['outcome'];
  stopped_at: string;
  stopped_during: SessionStop['stopped_during'];
  last_step: string | null;
};

/** Stop the journey's workflow Runtime session; Aurora records the stop. */
export async function stopRuntimeSession(
  journeyId: string,
  signal?: AbortSignal,
): Promise<StopSessionResponse> {
  return requestJson<StopSessionResponse>(
    `${API_BASE}/journeys/${encodeURIComponent(journeyId)}/stop-session`,
    { method: 'POST', signal, headers: JSON_HEADERS },
  );
}
