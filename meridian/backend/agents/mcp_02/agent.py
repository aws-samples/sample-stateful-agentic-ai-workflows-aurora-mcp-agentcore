"""
Phase 2 — MCP Agent (Strands + postgres-mcp-server).

Presenter walkthrough
---------------------
Show this module when explaining MCP as a *transport* layer:
  • `MCPClient` discovers tools from awslabs.postgres-mcp-server at runtime
  • Same Aurora schema as Phase 1 — different wire protocol (MCP vs inline SQL)

Live demo note: `chat.py` → `mcp_search()` uses `backend/mcp/mcp_client.py`
for the workshop demo path. This file shows the Strands-native MCP integration.

AWS docs:
  - RDS Data API (postgres-mcp-server ``rdsapi`` mode):
    https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.html
  - Aurora PostgreSQL:
    https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/Aurora.AuroraPostgreSQL.html

MCP server (awslabs):
  https://github.com/awslabs/mcp/tree/main/src/postgres-mcp-server

"""

import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Callable, Any, Optional

from mcp import StdioServerParameters, stdio_client
from strands import Agent
from strands.models import BedrockModel
from strands.tools.mcp import MCPClient
from pydantic import BaseModel

from backend.config import config


class ActivityEntry(BaseModel):
    """Model for agent activity entries."""
    id: str
    timestamp: str
    activity_type: str
    title: str
    details: Optional[str] = None
    sql_query: Optional[str] = None
    execution_time_ms: Optional[int] = None
    agent_name: Optional[str] = None


class MCPAgent:
    """
    Phase 2 travel concierge with MCP abstraction layer.
    
    Uses Strands SDK with MCP client for database operations via RDS Data API.
    
    """
    
    def __init__(self, activity_callback: Optional[Callable[[ActivityEntry], Any]] = None):
        """
        Initialize Phase 2 agent.
        
        Args:
            activity_callback: Optional callback for reporting agent activities
        """
        self.activity_callback = activity_callback or (lambda x: None)
        
        # Initialize Bedrock model - Sonnet 5 by default (cross-region inference)
        self.model = BedrockModel(
            model_id=config.bedrock.model_id,
            region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1")
        )
        
        # Strands MCPClient takes a transport factory. The stdio transport
        # spawns awslabs.postgres-mcp-server with the same start-up flags the
        # live Phase 2 client uses (backend/mcp/mcp_client.py), so the server
        # already knows the cluster and no connect_to_database call is needed.
        # Pinned to @1.0.9: @latest drifted to auto-discovering the Secrets
        # Manager secret, which fails for a Serverless v2 secret whose name
        # carries a random suffix; the pin avoids that on stage.
        self.mcp_client = MCPClient(
            lambda: stdio_client(
                StdioServerParameters(
                    command=sys.executable,
                    args=[
                        "-m", "awslabs.postgres_mcp_server.server",
                        f"--resource_arn={os.getenv('AURORA_CLUSTER_ARN', '')}",
                        f"--secret_arn={os.getenv('AURORA_SECRET_ARN', '')}",
                        f"--database={os.getenv('AURORA_DATABASE', 'meridian')}",
                        f"--region={os.getenv('AWS_DEFAULT_REGION', 'us-east-1')}",
                        "--readonly=True",
                    ],
                )
            )
        )

        # Create agent - tools are discovered from the MCP server on first use
        self.agent = None

    def _initialize_agent(self):
        """Start the MCP session and build the agent from the discovered tools."""
        if self.agent is not None:
            return

        # Strands manages the MCP session on a background thread; start() opens
        # it and list_tools_sync() runs tools/list against the running server.
        self.mcp_client.start()
        mcp_tools = self.mcp_client.list_tools_sync()

        self._log_activity(
            activity_type="mcp",
            title="MCP server connected",
            details=f"Discovered {len(mcp_tools)} tools from postgres-mcp-server"
        )

        # Create agent with discovered MCP tools
        self.agent = Agent(
            model=self.model,
            tools=mcp_tools,
            system_prompt=self._get_system_prompt()
        )
    
    def _get_system_prompt(self) -> str:
        """Get the system prompt for the travel concierge."""
        return """You are a helpful travel concierge for Meridian.

You have access to read-only database tools through MCP (Model Context Protocol) that allow you to:
- Query trip_packages for catalog search and filters
- Check departure availability on packages
- Holds and bookings go through Meridian's governed confirmation flow, not SQL

The database schema includes:
- trip_packages: package_id, name, operator, price_per_person, description, image_url, trip_type, destination, durations, availability, embedding
- travelers: traveler_id, full_name, email, home_airport
- traveler_profiles: party_size, budget_min, budget_max, trip_goal, dietary_notes
- bookings: booking_id, traveler_id, status, total_amount
- booking_lines: booking_id, package_id, duration, travelers_count, unit_price

Guidelines:
- Be friendly and helpful
- Use SQL queries through MCP tools for accurate trip information
- Recommend packages based on traveler needs
- Never claim to have placed a hold or booking

Trip types:
- City Breaks, Beach & Resort, Adventure & Outdoors, Wellness & Luxury, Family Trips, Business Travel"""
    
    def _log_activity(
        self,
        activity_type: str,
        title: str,
        details: Optional[str] = None,
        sql_query: Optional[str] = None,
        execution_time_ms: Optional[int] = None
    ):
        """Log an activity entry."""
        entry = ActivityEntry(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            activity_type=activity_type,
            title=title,
            details=details,
            sql_query=sql_query,
            execution_time_ms=execution_time_ms,
            agent_name="MCPAgent"
        )
        self.activity_callback(entry)
    
    def close(self):
        """Stop the MCP session and the server it spawned."""
        if self.mcp_client:
            self.mcp_client.stop(None, None, None)


def create_mcp_agent(
    activity_callback: Optional[Callable[[ActivityEntry], Any]] = None
) -> MCPAgent:
    """Create an MCP agent instance."""
    return MCPAgent(activity_callback=activity_callback)
