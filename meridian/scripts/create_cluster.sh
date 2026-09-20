#!/bin/bash
# Legacy provisioning entry point retained so old instructions fail safely.
# Provision through infra/bin/meridian-aurora.ts with an explicit target.
set -euo pipefail

if [ "${1:-}" = "--apply" ]; then
    echo "Provisioning is disabled: use the separate encrypted Aurora CDK entry point instead of this retired credential path." >&2
    echo "See docs/DEPLOYMENT_FOLLOWUP.md for target preflight and the CDK workflow." >&2
    exit 2
fi

cat <<'PLAN'
Plan only: Meridian requires an Aurora PostgreSQL cluster with Data API,
pgvector, private instances, encryption, and reviewed backup/retention settings.
No AWS calls or resource changes were made.

This legacy script is disabled, including --apply. The old script passed
credentials as process arguments and could overwrite an existing secret.
The separate Aurora CDK entry point supports encrypted creation or snapshot
restore without exposing credential values. Run the read-only provisioning
preflight and review the target-specific CDK diff first.

See docs/DEPLOYMENT_FOLLOWUP.md for the workflow and fresh-account limitations.
PLAN
