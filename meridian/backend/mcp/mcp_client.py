"""
MCP (Model Context Protocol) Client for Meridian.

Connects to ``awslabs.postgres-mcp-server`` for Phase 2 database operations.
The MCP server uses the same Aurora cluster as Phase 1, routed through MCP
stdio transport instead of inline RDS Data API calls.

AWS docs:
  - RDS Data API (used by postgres-mcp-server ``rdsapi`` connection method):
    https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.html
  - Aurora PostgreSQL:
    https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/Aurora.AuroraPostgreSQL.html
  - IAM database authentication (``pgwire_iam`` connection method):
    https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/UsingWithRDS.IAMDBAuth.html

MCP server source (awslabs):
  https://github.com/awslabs/mcp/tree/main/src/postgres-mcp-server
"""

import os
import asyncio
import json
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager
from dataclasses import dataclass

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


@dataclass
class MCPConnectionConfig:
    """Configuration for MCP postgres server connection."""

    # Connection method: 'rdsapi', 'pgwire', or 'pgwire_iam'
    connection_method: str = "rdsapi"

    # Database type: 'APG' (Aurora PostgreSQL) or 'RPG' (RDS PostgreSQL)
    database_type: str = "APG"

    # Aurora cluster identifier (for rdsapi method)
    cluster_identifier: Optional[str] = None

    # Database endpoint (for pgwire methods)
    database_endpoint: Optional[str] = None

    # Aurora cluster ARN + Secrets Manager ARN for the RDS Data API.
    # postgres-mcp-server@1.0.9 takes these as server-start CLI flags
    # (--resource_arn / --secret_arn). These mirror the same env vars the
    # Phase 1 RDS Data API path (db/rds_data_client.py) and the custom
    # concierge MCP client already rely on, so a single .env populates all
    # three paths.
    cluster_arn: Optional[str] = None
    secret_arn: Optional[str] = None

    # Database name
    database_name: str = "meridian"

    # AWS region
    aws_region: str = "us-east-1"

    # AWS profile (optional)
    aws_profile: Optional[str] = None

    # Allow write queries
    allow_write_query: bool = False

    @classmethod
    def from_env(cls) -> "MCPConnectionConfig":
        """Create config from environment variables."""
        return cls(
            connection_method=os.getenv("MCP_CONNECTION_METHOD", "rdsapi"),
            database_type=os.getenv("MCP_DATABASE_TYPE", "APG"),
            cluster_identifier=os.getenv("AURORA_CLUSTER_IDENTIFIER"),
            database_endpoint=os.getenv("AURORA_DATABASE_ENDPOINT"),
            cluster_arn=os.getenv("AURORA_CLUSTER_ARN"),
            secret_arn=os.getenv("AURORA_SECRET_ARN"),
            database_name=os.getenv("AURORA_DATABASE", "meridian"),
            aws_region=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
            aws_profile=os.getenv("AWS_PROFILE"),
            allow_write_query=os.getenv("MCP_ALLOW_WRITE", "false").lower() == "true",
        )


class MCPPostgresClient:
    """
    MCP client for Aurora PostgreSQL via awslabs.postgres-mcp-server.

    This client connects to the MCP server via stdio transport and provides
    methods to execute SQL queries through MCP tool invocations.
    """

    def __init__(self, config: Optional[MCPConnectionConfig] = None):
        """
        Initialize MCP client.

        Args:
            config: Connection configuration. If None, loads from environment.
        """
        self.config = config or MCPConnectionConfig.from_env()
        self.session: Optional[ClientSession] = None
        self._connected = False
        self._available_tools: List[Dict] = []

    def _get_server_params(self) -> StdioServerParameters:
        """Build server parameters for stdio transport.

        Pinned to @1.0.9: this version takes the connection config once, at
        server start, via CLI flags (--resource_arn / --secret_arn / etc.) —
        matching the DAT403 workshop's known-good pin. @latest drifted to a
        shape that requires db_endpoint and auto-discovers the secret, and
        that auto-discovery resolves secretArn to None for a Serverless v2
        cluster whose secret name carries a random suffix
        (e.g. meridian-demo-credentials-gThG49) — which is the root cause of
        the Phase 2 `ParamValidationError: Invalid length for parameter
        secretArn, value: 4` (the literal string "None").
        """
        args = ["awslabs.postgres-mcp-server@1.0.9"]

        # Pass the connection config as server-start flags. With these set,
        # run_query sends only {sql} and there is no per-call ambiguity.
        if self.config.cluster_arn and self.config.secret_arn:
            args += [
                f"--resource_arn={self.config.cluster_arn}",
                f"--secret_arn={self.config.secret_arn}",
                f"--database={self.config.database_name}",
                f"--region={self.config.aws_region}",
                f"--readonly={'False' if self.config.allow_write_query else 'True'}",
            ]
        elif self.config.allow_write_query:
            args.append("--allow_write_query")

        env = {
            "AWS_REGION": self.config.aws_region,
            "FASTMCP_LOG_LEVEL": "ERROR",
        }

        if self.config.aws_profile:
            env["AWS_PROFILE"] = self.config.aws_profile

        # Pass through AWS credentials if set
        if os.getenv("AWS_ACCESS_KEY_ID"):
            env["AWS_ACCESS_KEY_ID"] = os.getenv("AWS_ACCESS_KEY_ID")
        if os.getenv("AWS_SECRET_ACCESS_KEY"):
            env["AWS_SECRET_ACCESS_KEY"] = os.getenv("AWS_SECRET_ACCESS_KEY")
        if os.getenv("AWS_SESSION_TOKEN"):
            env["AWS_SESSION_TOKEN"] = os.getenv("AWS_SESSION_TOKEN")

        return StdioServerParameters(
            command="uvx",
            args=args,
            env=env
        )

    async def connect(self) -> None:
        """
        Connect to the MCP postgres server.

        Establishes stdio transport and initializes the MCP session.
        """
        if self._connected:
            return

        server_params = self._get_server_params()

        # Create stdio transport and session
        self._stdio_context = stdio_client(server_params)
        stdio_transport = await self._stdio_context.__aenter__()
        self._read, self._write = stdio_transport

        self._session_context = ClientSession(self._read, self._write)
        self.session = await self._session_context.__aenter__()

        # Initialize session
        await self.session.initialize()

        # Get available tools
        tools_response = await self.session.list_tools()
        self._available_tools = [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.inputSchema
            }
            for tool in tools_response.tools
        ]

        self._connected = True

    async def disconnect(self) -> None:
        """Disconnect from the MCP server."""
        if not self._connected:
            return

        if hasattr(self, '_session_context'):
            await self._session_context.__aexit__(None, None, None)
        if hasattr(self, '_stdio_context'):
            await self._stdio_context.__aexit__(None, None, None)

        self._connected = False
        self.session = None

    def _uses_startup_flags(self) -> bool:
        """True when the server was started with --resource_arn/--secret_arn
        flags (1.0.9 path), meaning the DB connection is already established
        at startup and there is no separate connect_to_database step."""
        return bool(self.config.cluster_arn and self.config.secret_arn)

    async def connect_to_database(self) -> Dict[str, Any]:
        """
        Connect to the Aurora PostgreSQL database via MCP.

        Uses the configured connection method (rdsapi, pgwire, or pgwire_iam).

        Returns:
            Connection result from MCP server
        """
        if not self._connected:
            await self.connect()

        # 1.0.9 with startup flags is already connected — there is no
        # connect_to_database tool to call. No-op so the session context
        # manager (which always calls this) stays compatible.
        if self._uses_startup_flags():
            return {"message": "connected via server-start flags"}

        # Build connection arguments - all parameters required by postgres-mcp-server
        args = {
            "database": self.config.database_name,
            "database_type": self.config.database_type,
            "connection_method": self.config.connection_method,
            "region": self.config.aws_region,
            "port": 5432,
        }
        
        # cluster_identifier is required
        if self.config.cluster_identifier:
            args["cluster_identifier"] = self.config.cluster_identifier
        else:
            args["cluster_identifier"] = ""
            
        # db_endpoint - required param but can be empty for rdsapi
        if self.config.database_endpoint:
            args["db_endpoint"] = self.config.database_endpoint
        else:
            args["db_endpoint"] = ""

        result = await self.session.call_tool("connect_to_database", args)
        return self._parse_tool_result(result)

    async def run_query(self, sql: str) -> List[Dict]:
        """
        Execute a SQL query through MCP.

        Args:
            sql: SQL query string

        Returns:
            List of result rows as dictionaries
        """
        if not self._connected:
            await self.connect()
            await self.connect_to_database()

        # On the 1.0.9 startup-flag path the server already holds the
        # connection, so run_query takes only the SQL. (The legacy per-call
        # connection shape below is kept for the non-flag fallback.)
        if self._uses_startup_flags():
            result = await self.session.call_tool("run_query", {"sql": sql})
            return self._parse_query_result(result)

        # Build query arguments - legacy MCP server required connection params
        # with each query.
        args = {"sql": sql}

        # Add connection parameters based on method
        if self.config.connection_method == "rdsapi":
            args["connection_method"] = "rdsapi"
            args["database"] = self.config.database_name
            if self.config.cluster_identifier:
                args["cluster_identifier"] = self.config.cluster_identifier
            # db_endpoint is optional for rdsapi but may be required
            if self.config.database_endpoint:
                args["db_endpoint"] = self.config.database_endpoint
        else:
            args["connection_method"] = self.config.connection_method
            args["database"] = self.config.database_name
            args["db_endpoint"] = self.config.database_endpoint
            args["cluster_identifier"] = self.config.cluster_identifier or ""

        result = await self.session.call_tool("run_query", args)
        return self._parse_query_result(result)

    def _parse_tool_result(self, result) -> Dict[str, Any]:
        """Parse a generic tool result."""
        if hasattr(result, 'content') and result.content:
            for content in result.content:
                if hasattr(content, 'text'):
                    try:
                        return json.loads(content.text)
                    except json.JSONDecodeError:
                        return {"message": content.text}
        return {"message": str(result)}

    @staticmethod
    def _decode_json_columns(row: Dict) -> Dict:
        """Postgres ``json``/array columns arrive from postgres-mcp-server as
        JSON-encoded *strings* (e.g. ``durations = '["3 nights"]'``). Decode
        any string value that looks like a JSON array or object so callers
        get real lists/dicts (the product hydration downstream expects
        ``durations`` to be a list, not a string)."""
        if not isinstance(row, dict):
            return row
        decoded = {}
        for key, value in row.items():
            if isinstance(value, str):
                stripped = value.strip()
                if stripped[:1] in ("[", "{"):
                    try:
                        decoded[key] = json.loads(stripped)
                        continue
                    except json.JSONDecodeError:
                        pass
            decoded[key] = value
        return decoded

    def _parse_query_result(self, result) -> List[Dict]:
        """Parse a query result into list of dictionaries.

        Handles two server contracts:
          - 1.0.9: one ``TextContent`` block *per row*, each a JSON object,
            with ``json``/array columns JSON-encoded as strings.
          - legacy/@latest: a single block holding the whole result set as a
            list or a ``{rows|data|result}`` wrapper.
        """
        if not (hasattr(result, "content") and result.content):
            return []

        rows: List[Dict] = []
        for content in result.content:
            if not hasattr(content, "text"):
                continue
            try:
                data = json.loads(content.text)
            except json.JSONDecodeError:
                continue

            # Unwrap the known single-block container shapes.
            if isinstance(data, dict):
                if "rows" in data and isinstance(data["rows"], list):
                    data = data["rows"]
                elif "data" in data and isinstance(data["data"], list):
                    data = data["data"]
                elif "result" in data:
                    inner = data["result"]
                    data = inner.get("rows", []) if isinstance(inner, dict) else inner

            if isinstance(data, list):
                rows.extend(d for d in data if isinstance(d, dict))
            elif isinstance(data, dict):
                # 1.0.9 per-row block (one object per TextContent).
                rows.append(data)

        return [self._decode_json_columns(r) for r in rows]

    @property
    def available_tools(self) -> List[Dict]:
        """Get list of available MCP tools."""
        return self._available_tools


# Global client instance
_mcp_client: Optional[MCPPostgresClient] = None


def get_mcp_client() -> MCPPostgresClient:
    """Get or create the global MCP client instance."""
    global _mcp_client
    if _mcp_client is None:
        _mcp_client = MCPPostgresClient()
    return _mcp_client


@asynccontextmanager
async def mcp_session():
    """
    Context manager for MCP session.

    Handles connection lifecycle automatically.

    Usage:
        async with mcp_session() as client:
            results = await client.run_query("SELECT * FROM trip_packages LIMIT 5")
    """
    client = get_mcp_client()
    try:
        await client.connect()
        await client.connect_to_database()
        yield client
    finally:
        # Keep connection alive for reuse
        pass
