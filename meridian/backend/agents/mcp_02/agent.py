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
import uuid
from datetime import datetime, timezone
from typing import Callable, Any, Optional, List

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


class AgentResponse(BaseModel):
    """Response from agent processing."""
    message: str
    packages: Optional[List[dict]] = None
    booking: Optional[dict] = None


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
        
        # Initialize MCP client for postgres-mcp-server
        # The MCP server is configured without connection args - connection is established
        # via connect_to_database tool with connection_method: "rdsapi"
        self.mcp_client = MCPClient(
            server_name="postgres-mcp-server",
            command="uvx",
            # Pinned to @1.0.9 to match the live runtime client
            # (backend/mcp/mcp_client.py). @latest drifted to auto-discovering
            # the Secrets Manager secret, which fails for a Serverless v2 secret
            # whose name carries a random suffix; the pin avoids that on stage.
            args=["awslabs.postgres-mcp-server@1.0.9"]
        )
        
        # Store connection parameters for database connection
        self.db_config = {
            "region": os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
            "database_type": "APG",  # Aurora PostgreSQL
            "connection_method": "rdsapi",
            "cluster_identifier": os.getenv("AURORA_CLUSTER_IDENTIFIER", ""),
            "db_endpoint": os.getenv("AURORA_CLUSTER_ENDPOINT", ""),
            "database": os.getenv("AURORA_DATABASE", "meridian"),
            "port": 5432
        }
        
        # Create agent - tools will be auto-discovered from MCP server
        self.agent = None  # Initialized in async context
    
    async def _initialize_agent(self):
        """Initialize the agent with MCP tools."""
        if self.agent is not None:
            return
        
        # Connect to MCP server and discover tools
        await self.mcp_client.connect()
        mcp_tools = await self.mcp_client.list_tools()
        
        self._log_activity(
            activity_type="mcp",
            title="MCP server connected",
            details=f"Discovered {len(mcp_tools)} tools from postgres-mcp-server"
        )
        
        # Establish database connection via MCP connect_to_database tool
        # This uses RDS Data API (connection_method: "rdsapi")
        connect_tool = next((t for t in mcp_tools if t.name == "connect_to_database"), None)
        if connect_tool:
            await connect_tool(**self.db_config)
            self._log_activity(
                activity_type="mcp",
                title="Database connection established",
                details=f"Connected to {self.db_config['database']} via RDS Data API"
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

You have access to database tools through MCP (Model Context Protocol) that allow you to:
- Query trip_packages for catalog search and filters
- Check departure availability on packages
- Process bookings for travelers

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
- Always confirm booking details before processing

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
    
    def _wrap_mcp_tool(self, tool_func, tool_name: str):
        """Wrap an MCP tool to add activity logging."""
        async def wrapped(*args, **kwargs):
            start_time = datetime.now(timezone.utc)
            
            self._log_activity(
                activity_type="mcp",
                title=f"MCP tool invocation: {tool_name}",
                details=f"Args: {kwargs}"
            )
            
            result = await tool_func(*args, **kwargs)
            
            execution_time = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)
            
            self._log_activity(
                activity_type="mcp",
                title=f"MCP tool completed: {tool_name}",
                details=f"Result received",
                execution_time_ms=execution_time
            )
            
            return result
        
        return wrapped
    
    async def process_message(
        self,
        message: str,
        customer_id: str,
        activity_callback: Optional[Callable[[ActivityEntry], Any]] = None
    ) -> AgentResponse:
        """
        Process a customer message and return a response.
        
        Args:
            message: The customer's message
            customer_id: Identifier for the customer
            activity_callback: Optional callback for activity updates
            
        Returns:
            AgentResponse with message, optional packages, and optional booking
        """
        if activity_callback:
            self.activity_callback = activity_callback
        
        # Initialize agent if needed
        await self._initialize_agent()
        
        self._log_activity(
            activity_type="mcp",
            title="Processing customer message via MCP",
            details=f"Message: {message[:100]}..."
        )
        
        start_time = datetime.now(timezone.utc)
        
        # Run the agent
        response = await self.agent.run(message)
        
        execution_time = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)
        
        self._log_activity(
            activity_type="mcp",
            title="Agent response generated",
            execution_time_ms=execution_time
        )
        
        return AgentResponse(
            message=str(response),
            packages=None,
            booking=None
        )
    
    async def close(self):
        """Close MCP client connection."""
        if self.mcp_client:
            await self.mcp_client.disconnect()


def create_mcp_agent(
    activity_callback: Optional[Callable[[ActivityEntry], Any]] = None
) -> MCPAgent:
    """Create an MCP agent instance."""
    return MCPAgent(activity_callback=activity_callback)
