#!/usr/bin/env bash
#
# Open an SSM port-forwarding session from this laptop to the private Aurora
# endpoint. Keep this process running while the Phase 5 backend uses
# LANGGRAPH_CHECKPOINT_HOST=127.0.0.1 and LANGGRAPH_CHECKPOINT_PORT=15432.

set -euo pipefail

REGION="${AWS_DEFAULT_REGION:-us-east-1}"
CLUSTER_IDENTIFIER="${AURORA_CLUSTER_IDENTIFIER:-meridian-demo}"
TUNNEL_INSTANCE_NAME="${MERIDIAN_TUNNEL_INSTANCE_NAME:-meridian-demo-ssm-tunnel}"
LOCAL_PORT="${LANGGRAPH_CHECKPOINT_LOCAL_PORT:-15432}"

for command_name in aws session-manager-plugin; do
    if ! command -v "$command_name" >/dev/null 2>&1; then
        echo "Missing required command: $command_name" >&2
        exit 1
    fi
done

INSTANCE_ID="$(
    aws ec2 describe-instances \
        --region "$REGION" \
        --filters \
            "Name=tag:Name,Values=$TUNNEL_INSTANCE_NAME" \
            "Name=instance-state-name,Values=running" \
        --query 'Reservations[0].Instances[0].InstanceId' \
        --output text
)"

if [[ -z "$INSTANCE_ID" || "$INSTANCE_ID" == "None" ]]; then
    echo "No running SSM tunnel instance named $TUNNEL_INSTANCE_NAME." >&2
    exit 1
fi

DB_HOST="$(
    aws rds describe-db-clusters \
        --region "$REGION" \
        --db-cluster-identifier "$CLUSTER_IDENTIFIER" \
        --query 'DBClusters[0].Endpoint' \
        --output text
)"

if [[ -z "$DB_HOST" || "$DB_HOST" == "None" ]]; then
    echo "Aurora cluster endpoint not found for $CLUSTER_IDENTIFIER." >&2
    exit 1
fi

echo "Forwarding 127.0.0.1:$LOCAL_PORT -> $DB_HOST:5432 through $INSTANCE_ID"
echo "Keep this terminal open during the Phase 5 checkpoint demo."

exec aws ssm start-session \
    --region "$REGION" \
    --target "$INSTANCE_ID" \
    --document-name AWS-StartPortForwardingSessionToRemoteHost \
    --parameters "{\"host\":[\"$DB_HOST\"],\"portNumber\":[\"5432\"],\"localPortNumber\":[\"$LOCAL_PORT\"]}"
