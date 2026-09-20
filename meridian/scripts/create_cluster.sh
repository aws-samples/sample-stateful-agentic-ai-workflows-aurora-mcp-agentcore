#!/bin/bash
# Legacy provisioning entry point retained so old instructions fail safely.
# New infrastructure remains blocked pending the repository-required
# aws-secrets-manager/asm-exec workflow and an authorized target environment.
set -euo pipefail

if [ "${1:-}" = "--apply" ]; then
    echo "Provisioning is disabled: the legacy credential path has not passed the required secret-handling review." >&2
    echo "Use an existing configured Aurora cluster. See docs/READINESS_2026-09-20.md for the provisioning blocker." >&2
    exit 2
fi

cat <<'PLAN'
Plan only: Meridian requires an Aurora PostgreSQL cluster with Data API,
pgvector, private instances, encryption, and reviewed backup/retention settings.
No AWS calls or resource changes were made.

New-cluster provisioning is disabled, including --apply. The old script passed
credentials as process arguments and could overwrite an existing secret.
Restore this capability only with reviewed infrastructure-as-code and the
secret-handling workflow required by AGENTS.md. The current local demo can
continue using its established configured Aurora cluster.

See docs/READINESS_2026-09-20.md for the outstanding provisioning gate.
PLAN
