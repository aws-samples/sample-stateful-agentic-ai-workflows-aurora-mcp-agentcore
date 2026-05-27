/**
 * API client for Meridian backend
 */
import type { Product, ProductListResponse, ChatRequest, ChatResponse, OrderRequest, OrderResponse, MemoryProfileResponse } from '../types';

const API_BASE = 'http://localhost:8000/api';
const HEALTH_URL = 'http://localhost:8000/health';

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

/**
 * Fetch backend health from the FastAPI root health endpoint.
 */
export async function fetchHealth<THealth = unknown>(): Promise<THealth> {
  const response = await fetch(HEALTH_URL);
  if (!response.ok) {
    throw new Error(`Health request failed: ${response.statusText}`);
  }
  return response.json();
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
