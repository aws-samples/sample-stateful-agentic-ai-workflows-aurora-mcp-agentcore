"""
Database layer for Meridian.

Contains:
- rds_data_client.py: RDS Data API client for Aurora PostgreSQL
- embedding_service.py: Cohere Embed v4 embeddings (Bedrock)
- schema.sql: Travel-native Aurora schema (trip_packages, travelers, memory)
"""

from .rds_data_client import RDSDataClient, get_rds_data_client

__all__ = ["RDSDataClient", "get_rds_data_client"]
