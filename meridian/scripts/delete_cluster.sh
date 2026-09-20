#!/bin/bash
# Meridian Aurora PostgreSQL Cluster Deletion Script
# Deletes the Aurora PostgreSQL cluster and associated resources
#
# Requirements implemented:
# - 1.6: Delete Aurora cluster and associated Secrets Manager secret
# - 1.7: Handle non-existent resources gracefully (descriptive messages)

set -euo pipefail

# Configuration
CLUSTER_IDENTIFIER="meridian-demo"
REGION="${AWS_DEFAULT_REGION:-us-east-1}"

# Destruction requires the caller to name the account explicitly. No AWS
# calls or changes occur for the default plan. This cluster can host other
# demo databases; never apply this plan to the shared presenter environment.
if [ "$#" -ne 2 ] || [ "$1" != "--apply" ]; then
    echo "Plan: delete ${CLUSTER_IDENTIFIER}-instance, snapshot and delete $CLUSTER_IDENTIFIER."
    echo "Then separately inspect: python scripts/cleanup_resources.py"
    echo "Apply only to an owned disposable cluster: $0 --apply EXPECTED_ACCOUNT_ID"
    exit 0
fi
EXPECTED_ACCOUNT_ID="$2"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Helper functions
log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

echo ""
echo "=============================================="
echo "  Meridian Aurora PostgreSQL Cluster Deletion"
echo "=============================================="
echo ""

# Check prerequisites
log_info "Checking prerequisites..."

# Check AWS CLI
if ! command -v aws &> /dev/null; then
    log_error "AWS CLI is not installed. Please install it first."
    log_error "Visit: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
    exit 1
fi

# Check AWS credentials
if ! aws sts get-caller-identity &> /dev/null; then
    log_error "AWS credentials are not configured or invalid."
    log_error "Please run 'aws configure' or set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY"
    exit 1
fi

ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
if [ "$ACCOUNT_ID" != "$EXPECTED_ACCOUNT_ID" ]; then
    log_error "Account mismatch. No resource was deleted."
    exit 1
fi
log_success "AWS credentials valid. Account ID: $ACCOUNT_ID"

# AccessDenied, expired credentials and service errors are never "not found".
aws_exists() {
    local missing_code="$1"
    shift
    local response
    if response=$(aws "$@" 2>&1); then
        return 0
    elif [[ "$response" == *"($missing_code)"* ]]; then
        return 1
    fi
    log_error "$response"
    exit 1
}

# Track if any resources were found

# Step 1: Delete DB instance (if exists)
log_info "Checking for DB instance '${CLUSTER_IDENTIFIER}-instance'..."

if aws_exists DBInstanceNotFound rds describe-db-instances --db-instance-identifier "${CLUSTER_IDENTIFIER}-instance" --region "$REGION"; then
    log_info "Deleting DB instance '${CLUSTER_IDENTIFIER}-instance'..."
    
    aws rds delete-db-instance \
        --db-instance-identifier "${CLUSTER_IDENTIFIER}-instance" \
        --skip-final-snapshot \
        --region "$REGION" > /dev/null
    
    log_info "Waiting for DB instance to be deleted (this may take several minutes)..."
    
    # Wait for instance deletion with timeout handling
    if aws rds wait db-instance-deleted \
        --db-instance-identifier "${CLUSTER_IDENTIFIER}-instance" \
        --region "$REGION" 2>/dev/null; then
        log_success "DB instance deleted"
    else
        log_error "Instance deletion did not finish. Inspect AWS before continuing."
        exit 1
    fi
else
    log_warning "DB instance '${CLUSTER_IDENTIFIER}-instance' does not exist. Skipping."
fi

# Step 2: Delete Aurora cluster (if exists)
log_info "Checking for Aurora cluster '$CLUSTER_IDENTIFIER'..."

if aws_exists DBClusterNotFoundFault rds describe-db-clusters --db-cluster-identifier "$CLUSTER_IDENTIFIER" --region "$REGION"; then
    log_info "Deleting Aurora cluster '$CLUSTER_IDENTIFIER'..."
    
    aws rds delete-db-cluster \
        --db-cluster-identifier "$CLUSTER_IDENTIFIER" \
        --final-db-snapshot-identifier "${CLUSTER_IDENTIFIER}-final-$(date -u +%Y%m%d%H%M%S)" \
        --region "$REGION" > /dev/null
    
    log_info "Waiting for cluster to be deleted (this may take several minutes)..."
    
    # Wait for cluster deletion with timeout handling
    if aws rds wait db-cluster-deleted \
        --db-cluster-identifier "$CLUSTER_IDENTIFIER" \
        --region "$REGION" 2>/dev/null; then
        log_success "Aurora cluster deleted"
    else
        log_error "Cluster deletion did not finish. Ancillary resources were retained."
        exit 1
    fi
else
    log_warning "Aurora cluster '$CLUSTER_IDENTIFIER' does not exist. Skipping."
fi

# Network and secret deletion has a separate read-only plan and must not run
# while the database is still alive. Retained final snapshots continue to cost.
log_success "Database deletion finished. A final cluster snapshot was retained."
echo "Review ancillary resources next: python scripts/cleanup_resources.py"
echo "Final snapshots must be reviewed separately for retention and storage cost."
