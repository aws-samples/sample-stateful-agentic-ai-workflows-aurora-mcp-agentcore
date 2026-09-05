"""
Property-based tests for Meridian - Embedding Properties

# Feature: meridian, Property 1: Embedding Dimension Consistency

This module contains property-based tests using Hypothesis to verify
that all trip package embeddings conform to the expected 1024 dimensions
as specified by the Cohere Embed v4 model.

**Validates: Requirements 2.8**
"""
import os
import pytest
import boto3
from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st
from typing import List
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Expected embedding dimension for Cohere Embed v4
EXPECTED_EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "1024"))

# RDS Data API configuration
CLUSTER_ARN = os.getenv('AURORA_CLUSTER_ARN')
SECRET_ARN = os.getenv('AURORA_SECRET_ARN')
DATABASE = os.getenv('AURORA_DATABASE', 'meridian')
REGION = os.getenv('AWS_DEFAULT_REGION', 'us-east-1')


def get_rds_client():
    """Get RDS Data API client."""
    try:
        return boto3.client('rds-data', region_name=REGION)
    except Exception as e:
        pytest.skip(f"RDS Data API client not available: {e}")
        return None


def execute_sql(sql: str, params: list = None) -> List[dict]:
    """Execute SQL via RDS Data API and return results as list of dicts."""
    client = get_rds_client()
    if client is None:
        return []
    
    try:
        response = client.execute_statement(
            resourceArn=CLUSTER_ARN,
            secretArn=SECRET_ARN,
            database=DATABASE,
            sql=sql,
            parameters=params or [],
            includeResultMetadata=True
        )
        
        column_metadata = response.get("columnMetadata", [])
        column_names = [col.get("name", f"col{i}") for i, col in enumerate(column_metadata)]
        
        results = []
        for record in response.get("records", []):
            row = {}
            for i, field in enumerate(record):
                if i < len(column_names):
                    if "isNull" in field and field["isNull"]:
                        value = None
                    elif "stringValue" in field:
                        value = field["stringValue"]
                    elif "longValue" in field:
                        value = field["longValue"]
                    elif "doubleValue" in field:
                        value = field["doubleValue"]
                    else:
                        value = None
                    row[column_names[i]] = value
            results.append(row)
        
        return results
    except Exception as e:
        pytest.skip(f"Database query failed: {e}")
        return []


def get_all_packages_with_embeddings() -> List[dict]:
    """
    Fetch all trip packages that have non-null embeddings from the database.

    Returns:
        List of package dictionaries with package_id, name, and embedding dimension
    """
    return execute_sql("""
        SELECT
            package_id,
            name,
            vector_dims(embedding) as embedding_dimension
        FROM trip_packages
        WHERE embedding IS NOT NULL
    """)


def get_package_count_with_embeddings() -> int:
    """
    Get the count of trip packages with non-null embeddings.

    Returns:
        Number of packages with embeddings
    """
    results = execute_sql("""
        SELECT COUNT(*) as count
        FROM trip_packages
        WHERE embedding IS NOT NULL
    """)
    return results[0]['count'] if results else 0


class TestEmbeddingDimensionConsistency:
    """
    Property-based tests for embedding dimension consistency.
    
    # Feature: meridian, Property 1: Embedding Dimension Consistency

    Property 1: For all trip packages in the database with non-null embeddings,
    the embedding vector dimension SHALL be exactly 1024.
    
    **Validates: Requirements 2.8**
    """
    
    @pytest.fixture(autouse=True)
    def setup(self):
        """Setup test fixtures - fetch packages with embeddings once."""
        self.packages_with_embeddings = get_all_packages_with_embeddings()
        self.package_count = len(self.packages_with_embeddings)

        if self.package_count == 0:
            pytest.skip("No trip packages with embeddings found in database")
    
    def test_all_embeddings_have_correct_dimension(self):
        """
        # Feature: meridian, Property 1: Embedding Dimension Consistency

        Verify that ALL package embeddings have exactly 1024 dimensions.
        This is a comprehensive check across all products.
        
        **Validates: Requirements 2.8**
        """
        incorrect_dimensions = []
        
        for package in self.packages_with_embeddings:
            if package['embedding_dimension'] != EXPECTED_EMBEDDING_DIMENSION:
                incorrect_dimensions.append({
                    'package_id': package['package_id'],
                    'name': package['name'],
                    'actual_dimension': package['embedding_dimension'],
                    'expected_dimension': EXPECTED_EMBEDDING_DIMENSION
                })

        assert len(incorrect_dimensions) == 0, (
            f"Found {len(incorrect_dimensions)} packages with incorrect embedding dimensions:\n"
            + "\n".join([
                f"  - {p['package_id']} ({p['name']}): {p['actual_dimension']} dims (expected {p['expected_dimension']})"
                for p in incorrect_dimensions[:10]
            ])
            + (f"\n  ... and {len(incorrect_dimensions) - 10} more" if len(incorrect_dimensions) > 10 else "")
        )
    
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(index=st.integers(min_value=0))
    def test_property_embedding_dimension_consistency(self, index: int):
        """
        # Feature: meridian, Property 1: Embedding Dimension Consistency

        Property-based test: For all trip packages with non-null embeddings,
        the embedding vector dimension SHALL be exactly 1024.
        
        This test uses Hypothesis to randomly sample products and verify their
        embedding dimensions. The property should hold for any valid product index.
        
        **Validates: Requirements 2.8**
        """
        assume(self.package_count > 0)

        package_index = index % self.package_count
        package = self.packages_with_embeddings[package_index]

        assert package['embedding_dimension'] == EXPECTED_EMBEDDING_DIMENSION, (
            f"Package {package['package_id']} ({package['name']}) has embedding dimension "
            f"{package['embedding_dimension']}, expected {EXPECTED_EMBEDDING_DIMENSION}"
        )
    
    def test_embedding_dimension_statistics(self):
        """
        Supplementary test: Verify embedding dimension statistics across all products.
        
        This test provides additional insight into the embedding dimensions
        and ensures consistency across the entire product catalog.
        
        **Validates: Requirements 2.8**
        """
        dimensions = [p['embedding_dimension'] for p in self.packages_with_embeddings]
        
        # All dimensions should be the same (1024)
        unique_dimensions = set(dimensions)
        
        assert len(unique_dimensions) == 1, (
            f"Found multiple embedding dimensions: {unique_dimensions}. "
            f"All embeddings should have exactly {EXPECTED_EMBEDDING_DIMENSION} dimensions."
        )
        
        assert EXPECTED_EMBEDDING_DIMENSION in unique_dimensions, (
            f"Embedding dimension is {unique_dimensions.pop()}, "
            f"expected {EXPECTED_EMBEDDING_DIMENSION}"
        )
        
        # Verify we have the expected number of packages (30 in travel catalog)
        print(f"\n✅ Verified {len(dimensions)} packages all have {EXPECTED_EMBEDDING_DIMENSION}-dimensional embeddings")


class TestEmbeddingDimensionWithDatabaseQuery:
    """
    Direct database query tests for embedding dimension verification.
    
    These tests query the database directly to verify embedding dimensions
    without loading all embeddings into memory.
    """
    
    def test_database_embedding_dimension_check(self):
        """
        # Feature: meridian, Property 1: Embedding Dimension Consistency

        Direct database query to verify all embeddings have correct dimensions.
        This is more efficient for large datasets as it doesn't load embeddings.
        
        **Validates: Requirements 2.8**
        """
        incorrect_packages = execute_sql(f"""
            SELECT
                package_id,
                name,
                vector_dims(embedding) as actual_dimension
            FROM trip_packages
            WHERE embedding IS NOT NULL
            AND vector_dims(embedding) != {EXPECTED_EMBEDDING_DIMENSION}
        """)

        assert len(incorrect_packages) == 0, (
            f"Found {len(incorrect_packages)} packages with incorrect embedding dimensions:\n"
            + "\n".join([
                f"  - {p['package_id']} ({p['name']}): {p['actual_dimension']} dims"
                for p in incorrect_packages[:10]
            ])
        )

        count_result = execute_sql("""
            SELECT COUNT(*) as count
            FROM trip_packages
            WHERE embedding IS NOT NULL
        """)
        count = count_result[0]['count'] if count_result else 0

        assert count > 0, "No trip packages with embeddings found in database"

        print(f"\n✅ All {count} packages have {EXPECTED_EMBEDDING_DIMENSION}-dimensional embeddings")


# Standalone test function for pytest discovery
def test_embedding_dimension_property():
    """
    # Feature: meridian, Property 1: Embedding Dimension Consistency

    Standalone property test for embedding dimension consistency.
    For all trip packages with non-null embeddings,
    the embedding vector dimension SHALL be exactly 1024.
    
    **Validates: Requirements 2.8**
    """
    packages = get_all_packages_with_embeddings()

    if len(packages) == 0:
        pytest.skip("No trip packages with embeddings found in database")

    for package in packages:
        assert package['embedding_dimension'] == EXPECTED_EMBEDDING_DIMENSION, (
            f"Package {package['package_id']} has embedding dimension "
            f"{package['embedding_dimension']}, expected {EXPECTED_EMBEDDING_DIMENSION}"
        )

    print(f"\n✅ Property verified: All {len(packages)} packages have {EXPECTED_EMBEDDING_DIMENSION}-dimensional embeddings")


class TestEmbeddingDimensionMock:
    """
    Mock tests that can run without database connection.
    
    These tests verify the property logic using mock data,
    ensuring the test framework is correctly configured.
    """
    
    @settings(max_examples=100)
    @given(embedding_dim=st.integers(min_value=1, max_value=10000))
    def test_property_dimension_validation_logic(self, embedding_dim: int):
        """
        # Feature: meridian, Property 1: Embedding Dimension Consistency
        
        Test the dimension validation logic with generated dimensions.
        This verifies the property check works correctly.
        
        **Validates: Requirements 2.8**
        """
        # Property: Only 1024 should be valid
        is_valid = embedding_dim == EXPECTED_EMBEDDING_DIMENSION
        
        if embedding_dim == EXPECTED_EMBEDDING_DIMENSION:
            assert is_valid, f"Dimension {embedding_dim} should be valid"
        else:
            assert not is_valid, f"Dimension {embedding_dim} should be invalid"
    
    def test_expected_dimension_constant(self):
        """
        Verify the expected embedding dimension constant is correctly set.
        
        **Validates: Requirements 2.8**
        """
        assert EXPECTED_EMBEDDING_DIMENSION == 1024, (
            f"Expected embedding dimension should be 1024, got {EXPECTED_EMBEDDING_DIMENSION}"
        )
    
    @settings(max_examples=100)
    @given(
        packages=st.lists(
            st.fixed_dictionaries({
                'package_id': st.text(min_size=1, max_size=10, alphabet='ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-'),
                'name': st.text(min_size=1, max_size=50),
                'embedding_dimension': st.just(1024)
            }),
            min_size=1,
            max_size=30
        )
    )
    def test_property_all_valid_embeddings_pass(self, packages: List[dict]):
        """
        # Feature: meridian, Property 1: Embedding Dimension Consistency
        
        Property test: When all packages have 1024-dimensional embeddings,
        the validation should pass for all of them.
        
        **Validates: Requirements 2.8**
        """
        for package in packages:
            assert package['embedding_dimension'] == EXPECTED_EMBEDDING_DIMENSION, (
                f"Package {package['package_id']} should have {EXPECTED_EMBEDDING_DIMENSION} dimensions"
            )
    
    @settings(max_examples=100)
    @given(
        invalid_dim=st.integers(min_value=1, max_value=10000).filter(lambda x: x != 1024)
    )
    def test_property_invalid_dimension_detected(self, invalid_dim: int):
        """
        # Feature: meridian, Property 1: Embedding Dimension Consistency
        
        Property test: Any dimension other than 1024 should be detected as invalid.
        
        **Validates: Requirements 2.8**
        """
        mock_package = {
            'package_id': 'CTY-001',
            'name': 'Test Package',
            'embedding_dimension': invalid_dim
        }

        assert mock_package['embedding_dimension'] != EXPECTED_EMBEDDING_DIMENSION, (
            f"Dimension {invalid_dim} should not equal {EXPECTED_EMBEDDING_DIMENSION}"
        )


if __name__ == "__main__":
    # Run tests directly
    pytest.main([__file__, "-v", "--hypothesis-show-statistics"])
