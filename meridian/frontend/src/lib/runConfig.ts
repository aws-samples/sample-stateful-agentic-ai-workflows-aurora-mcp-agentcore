/** The fields of `/api/health` the showcase reads. */
export interface BackendHealth {
  status: string;
  bedrock_model_id?: string;
  embedding_model_id?: string;
  snapshot_store?: string;
  snapshot_durable?: boolean;
  snapshot_required?: boolean;
}
