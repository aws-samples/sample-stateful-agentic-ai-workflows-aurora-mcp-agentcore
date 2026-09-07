/**
 * API client for Meridian backend
 */
import type {
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

  if (isDev || !location) return 'http://localhost:8000';

  const { protocol, hostname } = location;
  if (['localhost', '127.0.0.1', '[::1]', '::1'].includes(hostname)) {
    const localHostname = hostname === '::1' ? '[::1]' : hostname;
    return `${protocol}//${localHostname}:8000`;
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

function apiHeaders(json = false): HeadersInit {
  return {
    ...(json ? { 'Content-Type': 'application/json' } : {}),
  };
}

export function healthUrlsFor(origin: string): string[] {
  const normalized = trimTrailingSlash(origin);
  return [`${normalized}/health`, `${normalized}/api/health`];
}

const HEALTH_URL_CANDIDATES = healthUrlsFor(
  healthOriginFor(API_BASE, BACKEND_ORIGIN),
);

/**
 * Fetch all products from the backend
 */
export async function fetchProducts(category?: string, limit = 50, featured = false): Promise<Product[]> {
  const params = new URLSearchParams();
  if (category) params.set('category', category);
  params.set('limit', limit.toString());
  if (featured) params.set('featured', 'true');
  
  const response = await fetch(`${API_BASE}/products?${params}`);
  if (!response.ok) {
    throw new Error(`Failed to fetch products: ${response.statusText}`);
  }
  
  const data: ProductListResponse = await response.json();
  return data.products;
}

/**
 * Fetch a single product by ID
 */
export async function fetchProduct(productId: string): Promise<Product> {
  const response = await fetch(`${API_BASE}/products/${productId}`);
  if (!response.ok) {
    throw new Error(`Failed to fetch product: ${response.statusText}`);
  }
  return response.json();
}

/**
 * Send a chat message to the AI assistant
 */
export async function sendChatMessage(request: ChatRequest, signal?: AbortSignal): Promise<ChatResponse> {
  const response = await fetch(`${API_BASE}/chat`, {
    method: 'POST',
    signal,
    headers: apiHeaders(true),
    body: JSON.stringify(request),
  });
  
  if (!response.ok) {
    throw new Error(`Chat request failed: ${response.statusText}`);
  }
  
  return response.json();
}

/**
 * Fetch long-term memory profile from Aurora (Phase 4)
 */
export async function fetchMemoryProfile(travelerId = 'trv_meridian_demo'): Promise<MemoryProfileResponse> {
  const response = await fetch(`${API_BASE}/memory/${travelerId}`, {
    headers: apiHeaders(),
  });
  if (!response.ok) {
    throw new Error(`Memory profile request failed: ${response.statusText}`);
  }
  return response.json();
}

export async function updateMemoryFact(
  travelerId: string,
  key: string,
  value: string,
): Promise<LongTermMemoryFact> {
  const response = await fetch(
    `${API_BASE}/memory/${encodeURIComponent(travelerId)}/facts/${encodeURIComponent(key)}`,
    {
      method: 'PATCH',
      headers: apiHeaders(true),
      body: JSON.stringify({ value }),
    },
  );
  if (!response.ok) {
    throw new Error(`Memory update failed: ${response.statusText}`);
  }
  return response.json();
}

export async function deleteMemoryFact(travelerId: string, key: string): Promise<void> {
  const response = await fetch(
    `${API_BASE}/memory/${encodeURIComponent(travelerId)}/facts/${encodeURIComponent(key)}`,
    { method: 'DELETE', headers: apiHeaders() },
  );
  if (!response.ok) {
    throw new Error(`Memory delete failed: ${response.statusText}`);
  }
}

/**
 * Fetch backend health from the FastAPI root health endpoint.
 */
export async function fetchHealth<THealth = unknown>(): Promise<THealth> {
  let lastError: Error | null = null;

  for (const url of HEALTH_URL_CANDIDATES) {
    try {
      const response = await fetch(url);
      if (!response.ok) {
        throw new Error(`Health request failed: ${response.status} ${response.statusText}`);
      }
      return response.json();
    } catch (error) {
      lastError = error instanceof Error ? error : new Error('Unknown health request error');
    }
  }

  throw lastError ?? new Error('Health request failed for all candidates');
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
export async function processOrder(request: OrderRequest): Promise<OrderResponse> {
  const response = await fetch(`${API_BASE}/chat/order`, {
    method: 'POST',
    headers: apiHeaders(true),
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    throw new Error(`Order request failed: ${response.statusText}`);
  }

  return response.json();
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
    decision: 'allow' | 'deny';
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
  travelerId = 'trv_meridian_demo',
): Promise<RlsProbeResponse> {
  const response = await fetch(`${API_BASE}/diagnostics/rls-probe`, {
    method: 'POST',
    headers: apiHeaders(true),
    body: JSON.stringify({ traveler_id: travelerId }),
  });
  if (!response.ok) {
    throw new Error(`RLS probe failed: ${response.statusText}`);
  }
  return response.json();
}

export interface SessionReceiptLine {
  label: string;
  table: string;
  count: number;
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
  travelerId = 'trv_meridian_demo',
  windowMinutes = 90,
  conversationId: string | null = null,
): Promise<SessionReceiptResponse> {
  const response = await fetch(`${API_BASE}/diagnostics/session-receipt`, {
    method: 'POST',
    headers: apiHeaders(true),
    body: JSON.stringify({
      traveler_id: travelerId,
      window_minutes: windowMinutes,
      // Scopes the checkpoint count to this session's workflow thread.
      conversation_id: conversationId,
    }),
  });
  if (!response.ok) {
    throw new Error(`Session receipt failed: ${response.statusText}`);
  }
  return response.json();
}

/**
 * List the caller's journeys, newest first.
 *
 * The shell needs a journey id before it can read a document, and a demo
 * machine should not have to be told one by hand.
 */
export async function fetchJourneys(limit = 10): Promise<JourneySummary[]> {
  const response = await fetch(`${API_BASE}/journeys?limit=${limit}`);
  if (!response.ok) {
    throw new Error(`Failed to list journeys: ${response.statusText}`);
  }
  const data = (await response.json()) as { journeys: JourneySummary[] };
  return data.journeys ?? [];
}

/** Read one journey's evidence document. */
export async function fetchJourneyDocument(
  journeyId: string,
): Promise<JourneyDocument> {
  const response = await fetch(
    `${API_BASE}/journeys/${encodeURIComponent(journeyId)}`,
  );
  if (!response.ok) {
    throw new Error(`Failed to read journey ${journeyId}: ${response.statusText}`);
  }
  const document = (await response.json()) as JourneyDocument;
  return { ...document, received_at: Date.now() };
}
