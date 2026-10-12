# MeridianWorkflow

The AgentCore Runtime that runs the Meridian Phase 5 workflow. Its entrypoint streams the events of
`backend.agents.phase_05_workflow.runtime_entry.workflow_turn`.

## Payload contract

Send one JSON object with `event` set to `workflow_turn`. Any other event, and any key outside this
list, is rejected with an `error` event.

- `mode` is `ping`, `start` or `resume`. A ping answers with a ready `result` and runs nothing.
- `thread_id` and `query` are required non-empty strings for `start` and `resume`.
- `traveler_id` depends on `MERIDIAN_AGENTCORE_AUTH`. In `iam` mode it is a required non-empty string.
  In `jwt` mode the traveler comes from the `traveler_id` claim of the bearer token in the
  `Authorization` header, the field is optional, and a different `traveler_id` is refused with an
  `authorization` error.
- `travelers_count` is optional and defaults to 1.
- `review_only` is optional and must be a boolean. It defaults to false.

The payload names no pause point. Only code that builds a runner can set one.

The Runtime streams one JSON object per event:

- `{"type": "heartbeat", "worker_instance_id": ...}` every 10 seconds while the graph runs.
- `{"type": "result", "state": {...}}` once, with `activities`, `packages`, `response`,
  `conversation_id`, `workflow_status`, `resumed_after_restart`, `resumed_from_checkpoint`,
  `execution_id` and `worker_instance_id`. A ping result carries only `workflow_status` and
  `worker_instance_id`.
- `{"type": "error", "code": ..., "message": ...}` once, instead of a result. The code is one of
  `request`, `authorization`, `token_expired`, `conflict`, `lease_lost`, `hold_unknown` or
  `internal`. `token_expired` means the caller's access token has expired or is about to; sign in
  again and resume.

## Environment

- `AURORA_CLUSTER_ARN` and `AURORA_DATABASE` name the Amazon Aurora cluster and database.
- `AURORA_SECRET_ARN` is the ARN of the `meridian_workflow` secret, so the workflow runs as that
  login under row level security.
- `RLS_APP_ROLE` is the restricted role the scoped sessions step down to (`meridian_app`).
- `MERIDIAN_AGENTCORE_AUTH` is `iam` (the default) or `jwt`. In `jwt` mode the Runtime needs
  `Authorization` in its request header allowlist so the code can read the caller's token; the
  render sets the allowlist and a Cognito JWT authorizer (`requestHeaderAllowlist` in
  `scripts/render_agentcore_config.py`).
- `AGENTCORE_SKIP_CLI_SYNC=1` stops the backend from syncing the AgentCore CLI configuration.
- `ENVIRONMENT=production`.
- The CDK wiring adds the Gateway URL as `AGENTCORE_GATEWAY_MERIDIAN_AURORA_JWT_URL` for the
  `meridian-aurora-jwt` Gateway, or `AGENTCORE_GATEWAY_MERIDIAN_AURORA_URL` for the `iam` Gateway.
  `gateway_url.py` reads the first and then the second, and `main.py` copies the result to
  `AGENTCORE_GATEWAY_URL` before the backend modules import.

## Deploy

The `backend/` directory beside `main.py` is a generated, gitignored copy of the backend modules the
workflow imports. Run `scripts/render_agentcore_config.py`, which stages it, then run
`agentcore deploy -y` from `meridian/meridian_agentcore/`. Run
`scripts/stage_workflow_runtime.py --check` to see whether the copy is stale.
