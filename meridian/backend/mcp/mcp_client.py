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
import sys
import json
from typing import Optional, List, Dict, Any
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from backend.mcp.subprocess_env import aws_subprocess_env


@dataclass
class MCPConnectionConfig:
    """Configuration for MCP postgres server connection."""

    # This application supports the pinned server's rdsapi startup contract.
    connection_method: str = "rdsapi"

    # Aurora PostgreSQL only.
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
        self._exit_stack: Optional[AsyncExitStack] = None

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
        if self.config.connection_method != "rdsapi" or self.config.database_type != "APG":
            raise ValueError("Phase 2 requires Aurora PostgreSQL through the RDS Data API")
        if not self.config.cluster_arn or not self.config.secret_arn:
            raise ValueError("Phase 2 requires AURORA_CLUSTER_ARN and AURORA_SECRET_ARN")
        # The server and its SDK are installed from the application hash lock.
        # Never resolve dependencies or download an executable during a turn.
        args = ["-m", "awslabs.postgres_mcp_server.server"]

        # Pass the connection config as server-start flags. With these set,
        # run_query sends only {sql} and there is no per-call ambiguity.
        args += [
            f"--resource_arn={self.config.cluster_arn}",
            f"--secret_arn={self.config.secret_arn}",
            f"--database={self.config.database_name}",
            f"--region={self.config.aws_region}",
            f"--readonly={'False' if self.config.allow_write_query else 'True'}",
        ]

        env = {
            **aws_subprocess_env(),
            "AWS_REGION": self.config.aws_region,
            "AWS_DEFAULT_REGION": self.config.aws_region,
            "FASTMCP_LOG_LEVEL": "ERROR",
        }

        if self.config.aws_profile:
            env["AWS_PROFILE"] = self.config.aws_profile

        return StdioServerParameters(
            command=sys.executable,
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

        self._exit_stack = AsyncExitStack()
        try:
            read, write = await self._exit_stack.enter_async_context(
                stdio_client(self._get_server_params())
            )
            self.session = await self._exit_stack.enter_async_context(ClientSession(read, write))
            await self.session.initialize()
            tools_response = await self.session.list_tools()
            self._available_tools = [
                {"name": tool.name, "description": tool.description, "input_schema": tool.inputSchema}
                for tool in tools_response.tools
            ]
            self._connected = True
        except BaseException:
            await self.disconnect()
            raise

    async def disconnect(self) -> None:
        """Close partial or complete startup in the same task that opened it."""
        stack, self._exit_stack = self._exit_stack, None
        self._connected = False
        self.session = None
        if stack is not None:
            await stack.aclose()

    async def connect_to_database(self) -> Dict[str, Any]:
        """Connection is configured at startup; 1.0.9 has no connection tool."""
        if not self._connected:
            await self.connect()
        return {"message": "connected via server-start flags"}

    async def run_query(self, sql: str) -> List[Dict]:
        """Execute SQL using the pinned server's tool contract."""
        if not self._connected:
            await self.connect()
        result = await self.session.call_tool("run_query", {"sql": sql})
        if getattr(result, "isError", False):
            raise RuntimeError("PostgreSQL MCP query failed")
        return self._parse_query_result(result)

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


def get_mcp_client() -> MCPPostgresClient:
    """Return a client owned by one request, never a cross-task stdio singleton."""
    return MCPPostgresClient()


@asynccontextmanager
async def mcp_session():
    """Open and close the transport in one task, including failed startup."""
    client = get_mcp_client()
    try:
        await client.connect()
        await client.connect_to_database()
        yield client
    finally:
        await client.disconnect()
