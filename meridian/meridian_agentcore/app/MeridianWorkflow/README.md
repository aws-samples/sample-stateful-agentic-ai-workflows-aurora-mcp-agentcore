# MeridianWorkflow

The AgentCore Runtime that runs the Meridian Phase 5 workflow. Its entrypoint streams the events of
`backend.agents.phase_05_workflow.runtime_entry.workflow_turn`.

## Payload contract

Send one JSON object with `event` set to `workflow_turn`. Any other event, and any key outside this
list, is rejected with an `error` event.

- `mode` is `ping`, `start` or `resume`. A ping answers with a ready `result` and runs nothing.
- `thread_id`, `traveler_id` and `query` are required non-empty strings for `start` and `resume`.
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
  `request`, `authorization`, `conflict`, `lease_lost`, `hold_unknown` or `internal`.

## Environment

- `AURORA_CLUSTER_ARN` and `AURORA_DATABASE` name the AWS Aurora cluster and database.
- `AURORA_SECRET_ARN` is the ARN of the `meridian_workflow` secret, so the workflow runs as that
  login under row level security.
- `AGENTCORE_SKIP_CLI_SYNC=1` stops the backend from syncing the AgentCore CLI configuration.
- `ENVIRONMENT=production`.
- The CDK wiring adds the Gateway URL as `AGENTCORE_GATEWAY_MERIDIAN_AURORA_URL`. `main.py` copies it
  to `AGENTCORE_GATEWAY_URL` before the backend modules import.

## Deploy

The `backend/` directory beside `main.py` is a generated, gitignored copy of the backend modules the
workflow imports. Run `scripts/render_agentcore_config.py`, which stages it, then run
`agentcore deploy -y` from `meridian/meridian_agentcore/`. Run
`scripts/stage_workflow_runtime.py --check` to see whether the copy is stale.
