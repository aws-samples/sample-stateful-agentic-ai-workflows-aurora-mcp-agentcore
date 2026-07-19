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

function trimTrailingSlash(value: string): string {
  return value.replace(/\/+$/, '');
}

function resolveBackendOrigin(): string {
  const explicit = import.meta.env.VITE_API_ORIGIN as string | undefined;
  if (explicit?.trim()) return trimTrailingSlash(explicit.trim());

  // In local dev we always run FastAPI on localhost:8000.
  // Using the page host can break when the frontend is opened via a proxy/domain.
  if (import.meta.env.DEV || typeof window === 'undefined') return 'http://localhost:8000';

  const { protocol, hostname } = window.location;
  return `${protocol}//${hostname}`;
}

const explicitApiBase = import.meta.env.VITE_API_BASE_URL as string | undefined;
const API_BASE = explicitApiBase?.trim()
  ? trimTrailingSlash(explicitApiBase.trim())
  : `${resolveBackendOrigin()}/api`;

const HEALTH_URL_CANDIDATES = [
  `${resolveBackendOrigin()}/health`,
  `${resolveBackendOrigin()}/api/health`,
  'http://127.0.0.1:8000/health',
  'http://127.0.0.1:8000/api/health',
];

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
export async function sendChatMessage(request: ChatRequest): Promise<ChatResponse> {
  const response = await fetch(`${API_BASE}/chat`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
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
  const response = await fetch(`${API_BASE}/memory/${travelerId}`);
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
      headers: { 'Content-Type': 'application/json' },
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
    { method: 'DELETE' },
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
    headers: {
      'Content-Type': 'application/json',
    },
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
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ traveler_id: travelerId }),
  });
  if (!response.ok) {
    throw new Error(`RLS probe failed: ${response.statusText}`);
  }
  return response.json();
}
