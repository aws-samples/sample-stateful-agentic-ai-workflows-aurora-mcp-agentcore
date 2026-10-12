/** The fields of `/api/health` the showcase reads. */
export interface BackendHealth {
  status: string;
  bedrock_model_id?: string;
  embedding_model_id?: string;
  checkpoint_backend?: string;
  checkpoint_durable?: boolean;
  checkpoint_required?: boolean;
}
