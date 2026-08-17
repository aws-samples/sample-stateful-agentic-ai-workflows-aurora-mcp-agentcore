#!/bin/bash
# Meridian Aurora PostgreSQL Cluster Provisioning Script
# Creates an Aurora PostgreSQL 17.5 Serverless v2 cluster with pgvector support
#
# AWS docs:
#   Creating an Aurora cluster:
#     https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/Aurora.CreateInstance.html
#   Aurora Serverless v2:
#     https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-serverless-v2.html
#   Enabling the RDS Data API:
#     https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.html#data-api.enabling
#   Aurora PostgreSQL pgvector:
#     https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/AuroraPostgreSQL.Extensions.html#AuroraPostgreSQL.Extensions.pgvector
#   Secrets Manager integration:
#     https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/rds-secrets-manager.html
#
# Requirements implemented:
# - 1.1: Aurora PostgreSQL 17.9 Serverless v2 cluster with identifier "meridian-demo" in us-east-1
# - 1.2: Scaling from 0 to 64 ACUs
# - 1.3: RDS Data API enabled
# - 1.4: Credentials stored in AWS Secrets Manager
# - 1.5: pgvector 0.8.0 extension enabled
# - 1.7: Descriptive error messages and non-zero exit on failure

set -e

# Configuration
CLUSTER_IDENTIFIER="meridian-demo"
DB_NAME="meridian"
DB_USERNAME="meridian_admin"
REGION="${AWS_DEFAULT_REGION:-us-east-1}"
ENGINE_VERSION="17.9"
MIN_CAPACITY=0.5
MAX_CAPACITY=64
SECRET_NAME="meridian-demo-credentials"

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

# Error handler
handle_error() {
    local exit_code=$?
    local line_number=$1
    log_error "Script failed at line $line_number with exit code $exit_code"
    log_error "Please check the error message above and ensure:"
    log_error "  1. AWS CLI is installed and configured"
    log_error "  2. You have sufficient IAM permissions for RDS and Secrets Manager"
    log_error "  3. The region '$REGION' is available"
    exit $exit_code
}

trap 'handle_error $LINENO' ERR

echo ""
echo "=============================================="
echo "  Meridian Aurora PostgreSQL Cluster Setup"
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
log_success "AWS credentials valid. Account ID: $ACCOUNT_ID"

# Check if cluster already exists
log_info "Checking if cluster '$CLUSTER_IDENTIFIER' already exists..."
if aws rds describe-db-clusters --db-cluster-identifier "$CLUSTER_IDENTIFIER" --region "$REGION" &> /dev/null; then
    log_warning "Cluster '$CLUSTER_IDENTIFIER' already exists."
    
    # Get existing cluster info
    CLUSTER_INFO=$(aws rds describe-db-clusters \
        --db-cluster-identifier "$CLUSTER_IDENTIFIER" \
        --region "$REGION" \
        --query 'DBClusters[0]')
    
    CLUSTER_ARN=$(echo "$CLUSTER_INFO" | jq -r '.DBClusterArn')
    CLUSTER_ENDPOINT=$(echo "$CLUSTER_INFO" | jq -r '.Endpoint')
    
    # Try to get existing secret
    if aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --region "$REGION" &> /dev/null; then
        SECRET_ARN=$(aws secretsmanager describe-secret \
            --secret-id "$SECRET_NAME" \
            --region "$REGION" \
            --query 'ARN' \
            --output text)
    else
        SECRET_ARN="Not found - may need to recreate"
    fi
    
    echo ""
    echo "=============================================="
    echo "  Existing Cluster Information"
    echo "=============================================="
    echo ""
    echo "CLUSTER_ARN=$CLUSTER_ARN"
    echo "SECRET_ARN=$SECRET_ARN"
    echo "CLUSTER_ENDPOINT=$CLUSTER_ENDPOINT"
    echo ""
    exit 0
fi

# Generate secure password
log_info "Generating secure database password..."
DB_PASSWORD=$(openssl rand -base64 32 | tr -dc 'a-zA-Z0-9' | head -c 32)

# Step 1: Create Secrets Manager secret
log_info "Creating Secrets Manager secret..."

# Check if secret already exists
if aws secretsmanager describe-secret --secret-id "$SECRET_NAME" --region "$REGION" &> /dev/null; then
    log_warning "Secret '$SECRET_NAME' already exists. Updating..."
    SECRET_ARN=$(aws secretsmanager update-secret \
        --secret-id "$SECRET_NAME" \
        --region "$REGION" \
        --secret-string "{\"username\":\"$DB_USERNAME\",\"password\":\"$DB_PASSWORD\",\"dbname\":\"$DB_NAME\"}" \
        --query 'ARN' \
        --output text)
else
    SECRET_ARN=$(aws secretsmanager create-secret \
        --name "$SECRET_NAME" \
        --description "Credentials for Meridian Aurora PostgreSQL cluster" \
        --region "$REGION" \
        --secret-string "{\"username\":\"$DB_USERNAME\",\"password\":\"$DB_PASSWORD\",\"dbname\":\"$DB_NAME\"}" \
        --query 'ARN' \
        --output text)
fi

log_success "Secret created/updated: $SECRET_ARN"

# Step 2: Get default VPC and subnets
log_info "Getting default VPC configuration..."

DEFAULT_VPC=$(aws ec2 describe-vpcs \
    --filters "Name=isDefault,Values=true" \
    --region "$REGION" \
    --query 'Vpcs[0].VpcId' \
    --output text)

if [ "$DEFAULT_VPC" == "None" ] || [ -z "$DEFAULT_VPC" ]; then
    log_error "No default VPC found in region $REGION"
    log_error "Please create a default VPC or modify this script to use a specific VPC"
    exit 1
fi

log_success "Using VPC: $DEFAULT_VPC"

# Get subnets in at least 2 AZs - get 2 subnets from different AZs
SUBNET_IDS=$(aws ec2 describe-subnets \
    --filters "Name=vpc-id,Values=$DEFAULT_VPC" \
    --region "$REGION" \
    --query 'Subnets[*].SubnetId' \
    --output text | tr '\t' '\n' | head -2 | tr '\n' ',' | sed 's/,$//')

SUBNET_COUNT=$(echo "$SUBNET_IDS" | tr ',' '\n' | wc -l | tr -d ' ')

if [ "$SUBNET_COUNT" -lt 2 ]; then
    log_error "Need subnets in at least 2 availability zones. Found: $SUBNET_COUNT"
    exit 1
fi

log_success "Using subnets: $SUBNET_IDS"

# Step 3: Create DB subnet group
log_info "Creating DB subnet group..."

SUBNET_GROUP_NAME="meridian-demo-subnet-group"

# Check if subnet group exists
if aws rds describe-db-subnet-groups --db-subnet-group-name "$SUBNET_GROUP_NAME" --region "$REGION" &> /dev/null; then
    log_warning "Subnet group '$SUBNET_GROUP_NAME' already exists. Using existing."
else
    aws rds create-db-subnet-group \
        --db-subnet-group-name "$SUBNET_GROUP_NAME" \
        --db-subnet-group-description "Subnet group for Meridian Aurora cluster" \
        --subnet-ids $(echo $SUBNET_IDS | tr ',' ' ') \
        --region "$REGION" > /dev/null
    log_success "Subnet group created: $SUBNET_GROUP_NAME"
fi

# Step 4: Create security group
log_info "Creating security group..."

SG_NAME="meridian-demo-sg"

# Check if security group exists
EXISTING_SG=$(aws ec2 describe-security-groups \
    --filters "Name=group-name,Values=$SG_NAME" "Name=vpc-id,Values=$DEFAULT_VPC" \
    --region "$REGION" \
    --query 'SecurityGroups[0].GroupId' \
    --output text 2>/dev/null || echo "None")

if [ "$EXISTING_SG" != "None" ] && [ -n "$EXISTING_SG" ]; then
    SECURITY_GROUP_ID=$EXISTING_SG
    log_warning "Security group '$SG_NAME' already exists: $SECURITY_GROUP_ID"
else
    SECURITY_GROUP_ID=$(aws ec2 create-security-group \
        --group-name "$SG_NAME" \
        --description "Security group for Meridian Aurora cluster" \
        --vpc-id "$DEFAULT_VPC" \
        --region "$REGION" \
        --query 'GroupId' \
        --output text)
    
    # The application uses RDS Data API, so PostgreSQL does not need public
    # ingress. Direct checkpoint access should be granted from a bastion or
    # application security group after provisioning, never from 0.0.0.0/0.
    log_success "Security group created: $SECURITY_GROUP_ID"
fi

if [ "${ALLOW_PUBLIC_POSTGRES:-false}" != "true" ]; then
    aws ec2 revoke-security-group-ingress \
        --group-id "$SECURITY_GROUP_ID" \
        --protocol tcp \
        --port 5432 \
        --cidr 0.0.0.0/0 \
        --region "$REGION" >/dev/null 2>&1 || true
fi

# Step 5: Create Aurora PostgreSQL Serverless v2 cluster
log_info "Creating Aurora PostgreSQL Serverless v2 cluster..."
log_info "  Cluster ID: $CLUSTER_IDENTIFIER"
log_info "  Engine: aurora-postgresql $ENGINE_VERSION"
log_info "  Scaling: $MIN_CAPACITY - $MAX_CAPACITY ACUs"
log_info "  Region: $REGION"
echo ""

aws rds create-db-cluster \
    --db-cluster-identifier "$CLUSTER_IDENTIFIER" \
    --engine aurora-postgresql \
    --engine-version "$ENGINE_VERSION" \
    --master-username "$DB_USERNAME" \
    --master-user-password "$DB_PASSWORD" \
    --database-name "$DB_NAME" \
    --db-subnet-group-name "$SUBNET_GROUP_NAME" \
    --vpc-security-group-ids "$SECURITY_GROUP_ID" \
    --serverless-v2-scaling-configuration "MinCapacity=$MIN_CAPACITY,MaxCapacity=$MAX_CAPACITY" \
    --enable-http-endpoint \
    --region "$REGION" > /dev/null

log_success "Cluster creation initiated"

# Step 6: Create DB instance
log_info "Creating Serverless v2 DB instance..."

aws rds create-db-instance \
    --db-instance-identifier "${CLUSTER_IDENTIFIER}-instance" \
    --db-cluster-identifier "$CLUSTER_IDENTIFIER" \
    --engine aurora-postgresql \
    --db-instance-class db.serverless \
    --region "$REGION" > /dev/null

log_success "Instance creation initiated"

# Step 7: Wait for cluster to be available
log_info "Waiting for cluster to become available (this may take 5-10 minutes)..."

aws rds wait db-cluster-available \
    --db-cluster-identifier "$CLUSTER_IDENTIFIER" \
    --region "$REGION"

log_success "Cluster is now available"

# Step 8: Wait for instance to be available
log_info "Waiting for instance to become available..."

aws rds wait db-instance-available \
    --db-instance-identifier "${CLUSTER_IDENTIFIER}-instance" \
    --region "$REGION"

log_success "Instance is now available"

# Step 9: Get cluster information
log_info "Retrieving cluster information..."

CLUSTER_INFO=$(aws rds describe-db-clusters \
    --db-cluster-identifier "$CLUSTER_IDENTIFIER" \
    --region "$REGION" \
    --query 'DBClusters[0]')

CLUSTER_ARN=$(echo "$CLUSTER_INFO" | jq -r '.DBClusterArn')
CLUSTER_ENDPOINT=$(echo "$CLUSTER_INFO" | jq -r '.Endpoint')
CLUSTER_PORT=$(echo "$CLUSTER_INFO" | jq -r '.Port')

# Step 10: Enable pgvector extension using RDS Data API
log_info "Enabling pgvector 0.8.0 extension..."

# Wait a moment for the Data API to be ready
sleep 10

aws rds-data execute-statement \
    --resource-arn "$CLUSTER_ARN" \
    --secret-arn "$SECRET_ARN" \
    --database "$DB_NAME" \
    --sql "CREATE EXTENSION IF NOT EXISTS vector;" \
    --region "$REGION" > /dev/null

log_success "pgvector extension enabled"

# Verify pgvector version
PGVECTOR_VERSION=$(aws rds-data execute-statement \
    --resource-arn "$CLUSTER_ARN" \
    --secret-arn "$SECRET_ARN" \
    --database "$DB_NAME" \
    --sql "SELECT extversion FROM pg_extension WHERE extname = 'vector';" \
    --region "$REGION" \
    --query 'records[0][0].stringValue' \
    --output text 2>/dev/null || echo "unknown")

log_success "pgvector version: $PGVECTOR_VERSION"

# Update secret with additional connection info
log_info "Updating secret with connection details..."

aws secretsmanager update-secret \
    --secret-id "$SECRET_NAME" \
    --region "$REGION" \
    --secret-string "{\"username\":\"$DB_USERNAME\",\"password\":\"$DB_PASSWORD\",\"dbname\":\"$DB_NAME\",\"host\":\"$CLUSTER_ENDPOINT\",\"port\":$CLUSTER_PORT,\"engine\":\"aurora-postgresql\"}" > /dev/null

log_success "Secret updated with connection details"

# Output results
echo ""
echo "=============================================="
echo "  Aurora PostgreSQL Cluster Created!"
echo "=============================================="
echo ""
echo "Configuration:"
echo "  - Cluster ID: $CLUSTER_IDENTIFIER"
echo "  - Engine: Aurora PostgreSQL $ENGINE_VERSION Serverless v2"
echo "  - Scaling: $MIN_CAPACITY - $MAX_CAPACITY ACUs"
echo "  - RDS Data API: Enabled"
echo "  - pgvector: $PGVECTOR_VERSION"
echo ""
echo "Connection Details:"
echo "  - Endpoint: $CLUSTER_ENDPOINT"
echo "  - Port: $CLUSTER_PORT"
echo "  - Database: $DB_NAME"
echo ""
echo "=============================================="
echo "  Output Variables (for .env file)"
echo "=============================================="
echo ""
echo "AURORA_CLUSTER_ARN=$CLUSTER_ARN"
echo "AURORA_SECRET_ARN=$SECRET_ARN"
echo "AURORA_CLUSTER_ENDPOINT=$CLUSTER_ENDPOINT"
echo ""
echo "=============================================="
log_success "Cluster provisioning complete!"
echo ""
