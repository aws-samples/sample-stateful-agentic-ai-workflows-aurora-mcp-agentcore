"""Test Aurora PostgreSQL connection via RDS Data API"""
import os
import sys
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

print("Testing Aurora PostgreSQL connection with RDS Data API...")
print(f"Cluster ARN: {os.getenv('AURORA_CLUSTER_ARN', 'Not set')[:80]}...")
print(f"Database: {os.getenv('AURORA_DATABASE', 'meridian')}")
print(f"Region: {os.getenv('AWS_DEFAULT_REGION', 'us-east-1')}")
print()

# Check if credentials are loaded
if not os.getenv('AURORA_CLUSTER_ARN'):
    print("❌ Error: AURORA_CLUSTER_ARN not set in .env file")
    print("Please configure .env file with Aurora credentials")
    sys.exit(1)

if not os.getenv('AURORA_SECRET_ARN'):
    print("❌ Error: AURORA_SECRET_ARN not set in .env file")
    print("Please configure .env file with Secrets Manager ARN")
    sys.exit(1)

try:
    import boto3
    
    # Initialize RDS Data API client
    rds_data = boto3.client(
        'rds-data',
        region_name=os.getenv('AWS_DEFAULT_REGION', 'us-east-1')
    )
    
    cluster_arn = os.getenv('AURORA_CLUSTER_ARN')
    secret_arn = os.getenv('AURORA_SECRET_ARN')
    database = os.getenv('AURORA_DATABASE', 'meridian')
    
    # Test connection with version query
    response = rds_data.execute_statement(
        resourceArn=cluster_arn,
        secretArn=secret_arn,
        database=database,
        sql="SELECT version();"
    )
    
    version = response['records'][0][0]['stringValue']
    print("✅ Connection successful!")
    print(f"✅ PostgreSQL version: {version[:60]}...")
    
    # Check for pgvector extension
    response = rds_data.execute_statement(
        resourceArn=cluster_arn,
        secretArn=secret_arn,
        database=database,
        sql="""
            SELECT EXISTS (
                SELECT 1 FROM pg_extension WHERE extname = 'vector'
            );
        """
    )
    
    has_vector = response['records'][0][0]['booleanValue']
    
    if has_vector:
        print("✅ pgvector extension is installed")
        
        # Get vector extension version
        response = rds_data.execute_statement(
            resourceArn=cluster_arn,
            secretArn=secret_arn,
            database=database,
            sql="SELECT extversion FROM pg_extension WHERE extname = 'vector';"
        )
        vector_version = response['records'][0][0]['stringValue']
        print(f"✅ pgvector version: {vector_version}")
    else:
        print("⚠️  pgvector extension not installed (will install in next step)")
    
    print("\n🎉 Aurora PostgreSQL is ready to use via RDS Data API!")
    
except ImportError:
    print("❌ boto3 not installed")
    print("Run: pip install boto3")
    sys.exit(1)
    
except Exception as e:
    print(f"❌ Connection failed: {e}")
    print("\nTroubleshooting:")
    print("1. Verify Aurora cluster is running")
    print("2. Check that Data API is enabled on the cluster")
    print("3. Confirm AURORA_CLUSTER_ARN and AURORA_SECRET_ARN in .env are correct")
    print("4. Verify IAM permissions for rds-data:ExecuteStatement")
    sys.exit(1)
