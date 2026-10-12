"""
MCP (Model Context Protocol) module for Meridian.

Provides MCP client integration for database operations via awslabs.postgres-mcp-server.
"""

from backend.mcp.mcp_client import (
    MCPPostgresClient,
    MCPConnectionConfig,
    get_mcp_client,
    mcp_session,
)

__all__ = [
    "MCPPostgresClient",
    "MCPConnectionConfig",
    "get_mcp_client",
    "mcp_session",
]
