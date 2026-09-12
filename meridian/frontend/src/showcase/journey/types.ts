import type { ActivityEntry } from '../../types';

/** The evidence document served by `GET /api/journeys/{journey_id}`.
 *
 * Mirrors the contract in the journey shell spec, section 4. Every section is
 * either observed with a named source, or explicitly unavailable with a
 * reason. Nothing is optional-by-omission: a surface that cannot tell "absent"
 * from "not applicable" ends up asserting things it has not checked.
 */

export type Unavailable = {
  status: 'unavailable';
  reason: string;
  source?: string;
};

export type Observed<T> = { status: string; source: string } & T;

export type Evidence<T> = Unavailable | Observed<T>;

export function isObserved<T>(
  section: Evidence<T> | undefined,
): section is Observed<T> {
  return !!section && section.status !== 'unavailable';
}

export type JourneyExecution = {
  execution_id: string;
  attempt: number;
  worker_id: string;
  status: string;
  started_at: string | null;
  ended_at: string | null;
  lease_expires_at: string | null;
};

export type JourneyCheckpoint = {
  thread_id: string;
  checkpoint_id: string;
  parent_checkpoint_id: string | null;
  checkpoint_ns: string;
  committed_at: string | null;
};

export type JourneyHold = {
  label: string;
  hold_request_id: string;
  booking_id: string;
  created_by_execution_id: string | null;
  hold_expires_at: string | null;
  hold_created_at?: string | null;
  observed_at?: string | null;
  /** Set once the traveler confirmed the booking; catalog inventory only, no payment. */
  confirmed_at?: string | null;
  package_id: string | null;
  duration: string | null;
  travelers_count: number | null;
  /** Decimal amounts from the booking record, never recalculated from the catalog. */
  unit_price?: string | null;
  total_amount?: string | null;
  hold_records: number;
};

export type JourneyAuthorization = {
  audit_id: string;
  identity_provider: string;
  subject: string;
  decision: string;
  reason: string | null;
  observed_at: string;
};

export type JourneyMessage = {
  message_id: string;
  role: string;
  content: string;
  created_at: string | null;
};

export type JourneyRecommendation = {
  product_id?: string;
  package_id?: string;
  name?: string;
  price?: number;
  price_per_person?: number;
  image_url?: string;
  destination?: string;
  available_sizes?: string[];
};

export type JourneyDocument = {
  /** Browser receive time for an advancing database clock, added by the API client. */
  received_at?: number;
  observed_at?: string | null;
  journey_id: string;
  traveler_id: string;
  status: string;
  checkpoint_backend: { kind: string; durable: boolean };
  active_thread_id: string | null;
  workflow?: Evidence<{
    conversation_id: string;
    query: string;
    message: string;
    workflow_status: 'paused' | 'resumed' | 'complete';
    next_nodes: string[];
    activities: ActivityEntry[];
    travelers_count: number;
    execution_id?: string | null;
    resumed_from_checkpoint?: string | null;
    resumed_after_restart: boolean;
  }>;
  executions: Evidence<{ items: JourneyExecution[] }>;
  checkpoint: Evidence<JourneyCheckpoint>;
  selected_plan: Evidence<{ package_id: string }>;
  recommendations: Evidence<{ items: JourneyRecommendation[] }>;
  pending_decision: Evidence<{
    hold_request_id: string;
    package_id: string;
    prompt: string;
  }>;
  conversation: Evidence<{ messages: JourneyMessage[] }>;
  hold: Evidence<JourneyHold>;
  authorization: Evidence<JourneyAuthorization>;
  rls: Evidence<Record<string, unknown>>;
};

export type JourneySummary = {
  journey_id: string;
  status: string;
  checkpoint_backend: string;
  active_thread_id: string | null;
  execution_count: number;
  created_at: string;
  updated_at: string;
};
