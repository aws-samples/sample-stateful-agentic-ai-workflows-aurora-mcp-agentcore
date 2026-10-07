# Meridian operator scripts

Run from `meridian/` with the virtual environment active. Follow
[Operations](../docs/OPERATIONS.md) for complete commands and prerequisites.
The numbered capability source lives in [backend/agents](../backend/agents/README.md);
these shared scripts keep one stable command path for operators and CI.

| Step | Scripts | Effect |
| --- | --- | --- |
| **01 - Prepare** | `setup.sh`, `verify_installation.py`, `provision_preflight.py` | Local setup and read-only provisioning checks. Aurora provisioning and teardown use the CDK app in `infra/`. |
| **02 - Load Aurora** | `init_aurora_schema.py`, `apply_migrations.py`, `seed_data.py`, `travel_catalog.py` | Write schema, tracked migrations and sample catalog. Initialization and seeding refuse existing data. Never delete or reorder migration history. |
| **03 - Bind identity** | `bind_current_identity.py`, `bind_gateway_workload.py`, `bind_web_backend_role.py`, `apply_rls_force_and_decoy.py` | Write workload grants or demonstration RLS policies. Review the target first. |
| **04 - Configure AgentCore** | `render_agentcore_config.py`, `sync_agentcore_env.py`, `publish_gateway_parameters.py` | Render ignored account-specific config, optionally update local environment, publish SSM settings. See the AgentCore runbook. |
| **04b - Workflow Runtime** | `provision_workflow_login.py`, `stage_workflow_runtime.py`, `bind_workflow_runtime.py`, `smoke_workflow_runtime.py` | `provision_workflow_login.py` gives the `meridian_workflow` login a password in Secrets Manager and creates its access policy; without `--apply` it only reports. `stage_workflow_runtime.py` copies the workflow's backend modules into the Runtime bundle, and the render runs it. `bind_workflow_runtime.py` writes the Runtime role's traveler binding. `smoke_workflow_runtime.py` pings the deployed Runtime and touches no row. Order is in the runbook's workflow Runtime steps. |
| **05 - Verify services** | `test_aurora_connection.py`, `test_semantic_search.py`, `verify_agentcore.py`, `smoke_gateway_tools.py`, `warm_demo.py` | Real AWS reads/invocations; these can incur model or service charges. `warm_demo.py` runs one read-only turn per phase (no holds) so the first turn on stage is not cold; `--hosted` warms the published site. |
| **06 - Exercise actions and recovery** | `smoke_production_turn.py`, `validate_demo.py`, `kill_and_resume_demo.py`, `lost_response_demo.py` | Live integration exercises, including writes. Read each script's help and cleanup contract; hosted full-demo writes require an explicit flag. |
| **06b - Stop and resume proof** | `stop_and_resume_proof.py` | Stops a Runtime session and resumes it on a new microVM. `--during waiting` stops while the run waits for review; `--during running` stops while the hold step runs and exits 2 if the run finished first. Needs the backend on `MERIDIAN_PROOF_API`; removes its own rows unless `--keep`. |
| **07 - Publish** | `publish.py`, `published.py` | Review the CDK plan, apply to the established hosting resources, then inspect the non-secret local release record. Publication does not replace authenticated validation. |
| **08 - Maintain** | `release_demo_bookings.py`, `install_catalog_images.py`, `start_checkpoint_tunnel.sh` | Targeted booking release, catalog artwork installation, or an optional private PostgreSQL tunnel. |

Retired manual cluster create/delete helpers and the orphaned root Node lockfile
were removed. Use the maintained CDK provisioning and teardown path. Local logs,
release receipts, generated configuration, credentials and build output stay
outside Git.
