/**
 * TypeScript types for Meridian frontend
 */

export interface Product {
  product_id: string;
  name: string;
  brand: string;
  price: number;
  description: string;
  image_url: string;
  category: string;
  available_sizes?: string[] | null;
  similarity?: number;
}

export interface ProductListResponse {
  products: Product[];
  total: number;
}

export interface TripPackage {
  package_id: string;
  name: string;
  trip_type: string;
  destination: string;
  region: string;
  price_per_person: number;
  operator: string;
  description: string;
  image_url: string;
  durations?: string[] | null;
  availability?: Record<string, unknown> | null;
  highlights?: string[] | null;
  similarity?: number;
}

export interface PackageListResponse {
  packages: TripPackage[];
  total: number;
}

export type ActivityType = 'search' | 'embedding' | 'tool_call' | 'database' | 'error' | 'inventory' | 'order' | 'delegation' | 'mcp' | 'reasoning' | 'result' | 'security';

export type TraceSpanCategory =
  | 'runtime'
  | 'memory_short'
  | 'memory_long'
  | 'orchestration'
  | 'model'
  | 'tool'
  | 'data'
  | 'synthesis'
  | 'security';

export type TraceSpanStatus = 'ok' | 'cache_hit' | 'streaming' | 'held' | 'delegated' | 'preview';

export interface TraceTelemetryField {
  label: string;
  value: string;
  mono?: boolean;
}

export interface LongTermMemoryFact {
  key: string;
  value: string;
  source?: string;
  confidence?: number;
}

export interface TraceMemorySnapshot {
  shortTerm?: { label: string; items: string[] };
  longTerm?: { label: string; facts: LongTermMemoryFact[] };
}

export interface TraceTelemetry {
  category: TraceSpanCategory;
  component: string;
  status?: TraceSpanStatus;
  fields?: TraceTelemetryField[];
  memory?: TraceMemorySnapshot;
  tokens?: { input?: number; output?: number };
}

export interface ActivityEntry {
  id: string;
  timestamp: string;
  activity_type: ActivityType;
  title: string;
  details?: string;
  sql_query?: string;
  execution_time_ms?: number;
  agent_name?: string;
  agent_file?: string;
  telemetry?: TraceTelemetry;
  // Aliases for camelCase access
  type?: ActivityType;
  agentName?: string;
  executionTimeMs?: number;
  sqlQuery?: string;
  agentFile?: string;
}

export interface OrderItem {
  product_id: string;
  name: string;
  size?: string;
  quantity: number;
  unit_price: number;
}

export interface Order {
  order_id: string;
  items: OrderItem[];
  subtotal: number;
  tax: number;
  shipping: number;
  total: number;
  status: string;
  estimated_delivery?: string;
}

export type Phase = 1 | 2 | 3 | 4 | 5;

export interface ChatRequest {
  message: string;
  phase: Phase;
  customer_id?: string;
  conversation_id?: string;
}

export interface ChatResponse {
  message: string;
  products?: Product[];
  order?: Order;
  activities: ActivityEntry[];
  follow_ups?: string[];
  conversation_id?: string;
  memory_facts?: LongTermMemoryFact[];
}

export interface TravelerProfile {
  full_name?: string;
  home_airport?: string;
  party_size?: number;
  budget_min?: number;
  budget_max?: number;
  seat_preference?: string;
  dietary_notes?: string;
  trip_goal?: string;
}

export interface MemoryProfileResponse {
  traveler_id: string;
  facts: LongTermMemoryFact[];
  profile?: TravelerProfile;
}

export interface Message {
  role: 'user' | 'bot';
  type?: 'text' | 'products' | 'order';
  text: string;
  products?: Product[];
  order?: Order;
  follow_ups?: string[];
}

// Alias for backward compatibility
export type ChatMessage = Message;

export interface PhaseInfo {
  id: Phase;
  name: string;
  description: string;
  color: string;
}

export interface OrderRequest {
  product_id: string;
  size?: string;
  quantity?: number;
  phase: Phase;
}

export interface OrderResponse {
  message: string;
  order?: Order;
  activities: ActivityEntry[];
}
