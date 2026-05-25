"""
Phase 3 Supervisor Agent - Orchestrates specialized sub-agents.

Implements the enterprise pattern with:
- Supervisor agent with no direct tools
- Delegation logic to Search, Product, and Order agents
- Claude Opus 4.7 via Amazon Bedrock

Requirements: 11.1, 11.5
"""

import os
import uuid
from datetime import datetime
from typing import Callable, Any, Optional, List

from strands import Agent, tool
from strands.models import BedrockModel
from pydantic import BaseModel


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
    products: Optional[List[dict]] = None
    order: Optional[dict] = None


class SupervisorAgent:
    """
    Phase 3 Supervisor Agent that orchestrates specialized sub-agents.
    
    Requirements:
    - 11.1: Includes a Supervisor_Agent that orchestrates specialized sub-agents
    - 11.5: Supervisor has no direct tools and operates purely through delegation
    """
    
    def __init__(
        self,
        search_agent,
        product_agent,
        order_agent,
        activity_callback: Optional[Callable[[ActivityEntry], Any]] = None
    ):
        """
        Initialize Supervisor agent.
        
        Args:
            search_agent: Search agent for semantic / hybrid trip search
            product_agent: Package agent for trip details and departure availability
            order_agent: Booking agent for reservation processing
            activity_callback: Optional callback for reporting agent activities
        """
        self.search_agent = search_agent
        self.product_agent = product_agent
        self.order_agent = order_agent
        self.activity_callback = activity_callback or (lambda x: None)

        # Cache of the most-recent search result so the live API can return
        # structured packages alongside the LLM-generated reply text.
        self.last_search_packages: List[dict] = []
        self.last_search_query: Optional[str] = None

        # Initialize Bedrock model - Claude Opus 4.7 (cross-region inference)
        self.model = BedrockModel(
            model_id="global.anthropic.claude-opus-4-7-v1",
            region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1")
        )

        # Supervisor delegates via @tool functions; Bedrock chooses which to call.
        self.agent = Agent(
            model=self.model,
            tools=[
                self._delegate_to_search,
                self._delegate_to_product,
                self._delegate_to_order,
            ],
            system_prompt=self._get_system_prompt()
        )
    
    def _get_system_prompt(self) -> str:
        """Get the system prompt for the supervisor."""
        return """You are a supervisor agent for Meridian, coordinating specialized travel agents.

You have three specialized agents you can delegate to:
1. Search Agent - For finding trip packages via semantic text search
2. Package Agent - For package details and departure availability
3. Booking Agent - For calculating totals and processing reservations

Your role is to:
- Understand traveler requests
- Delegate to the appropriate specialized agent
- Coordinate multi-step workflows (e.g., search -> availability -> book)
- Synthesize responses from multiple agents

Guidelines:
- For trip discovery, delegate to Search Agent
- For package details or departure slots, delegate to Package Agent
- For bookings and pricing, delegate to Booking Agent
- You can delegate to multiple agents in sequence for complex requests"""
    
    def _log_activity(
        self,
        activity_type: str,
        title: str,
        details: Optional[str] = None,
        sql_query: Optional[str] = None,
        execution_time_ms: Optional[int] = None,
        agent_name: str = "SupervisorAgent"
    ):
        """Log an activity entry. Requirement 11.9."""
        entry = ActivityEntry(
            id=str(uuid.uuid4()),
            timestamp=datetime.utcnow().isoformat() + "Z",
            activity_type=activity_type,
            title=title,
            details=details,
            sql_query=sql_query,
            execution_time_ms=execution_time_ms,
            agent_name=agent_name
        )
        self.activity_callback(entry)
    
    @tool
    async def _delegate_to_search(self, query: str) -> dict:
        """
        Delegate to the Search Agent for natural-language trip discovery.

        Args:
            query: Natural-language search query (destination, vibe, budget, party size).

        Returns:
            {"packages": [...], "query": str} — semantic search results from Aurora.
        """
        start_time = datetime.utcnow()

        self._log_activity(
            activity_type="delegation",
            title="Delegating to Search Agent",
            details=f"Query: {query}"
        )

        result = await self.search_agent.semantic_search(query)

        # Cache for the live API caller to read structured packages out of band.
        self.last_search_packages = result.get("packages", [])
        self.last_search_query = query

        execution_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)

        self._log_activity(
            activity_type="delegation",
            title="Search Agent completed",
            details=f"Found {len(self.last_search_packages)} packages",
            execution_time_ms=execution_time,
            agent_name="SearchAgent"
        )

        return result
    
    @tool
    async def _delegate_to_product(self, action: str, package_id: Optional[str] = None, duration: Optional[str] = None) -> dict:
        """
        Delegate to the Package Agent for trip details or departure availability.

        Args:
            action: 'details' for package info, or 'availability' for departure slots.
            package_id: Trip package identifier (e.g. "CTY-002").
            duration: Optional duration option for availability check (e.g. "7 days").

        Returns:
            Package information or availability data.
        """
        start_time = datetime.utcnow()

        self._log_activity(
            activity_type="delegation",
            title="Delegating to Package Agent",
            details=f"Action: {action}, Package: {package_id}"
        )

        if action == "details":
            result = await self.product_agent.get_product_details(package_id)
        elif action in ("availability", "inventory"):
            result = await self.product_agent.check_inventory_status(package_id, duration)
        else:
            result = {"error": f"Unknown action: {action}"}
        
        execution_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)
        
        self._log_activity(
            activity_type="delegation",
            title="Package Agent completed",
            execution_time_ms=execution_time,
            agent_name="ProductAgent"
        )
        
        return result
    
    @tool
    async def _delegate_to_order(self, action: str, customer_id: Optional[str] = None, items: Optional[List[dict]] = None) -> dict:
        """
        Delegate to the Booking Agent for booking calculations or reservations.
        
        Args:
            action: 'calculate' or 'process'
            customer_id: Customer identifier (for process)
            items: Order items
            
        Returns:
            Order information from Order Agent
        """
        start_time = datetime.utcnow()
        
        self._log_activity(
            activity_type="delegation",
            title="Delegating to Booking Agent",
            details=f"Action: {action}, Items: {len(items or [])}"
        )
        
        if action == "calculate":
            result = await self.order_agent.calculate_total(items or [])
        elif action == "process":
            result = await self.order_agent.process_order(customer_id, items or [])
        else:
            result = {"error": f"Unknown action: {action}"}
        
        execution_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)
        
        self._log_activity(
            activity_type="delegation",
            title="Booking Agent completed",
            execution_time_ms=execution_time,
            agent_name="OrderAgent"
        )
        
        return result
    
    async def process_search(
        self,
        query: str,
        activity_callback: Optional[Callable[[ActivityEntry], Any]] = None,
    ) -> tuple[list[dict], str]:
        """
        Run a Bedrock-driven supervisor turn focused on trip search.

        Returns:
            (packages, llm_message) — packages from the SearchAgent tool call
            (cached on the supervisor) and the supervisor's final reply text.
        """
        if activity_callback:
            self.activity_callback = activity_callback

        self.last_search_packages = []
        self.last_search_query = None

        self._log_activity(
            activity_type="delegation",
            title="Supervisor processing search request",
            details=f"Query: {query[:100]}{'…' if len(query) > 100 else ''}"
        )

        start_time = datetime.utcnow()
        prompt = (
            f"Traveler asks: {query}\n\n"
            "Use the Search Agent to find matching trip packages, then briefly summarize the top results."
        )
        result = await self.agent.invoke_async(prompt)
        execution_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)

        self._log_activity(
            activity_type="delegation",
            title="Supervisor completed coordination",
            execution_time_ms=execution_time
        )

        return self.last_search_packages, str(result)

    async def process_message(
        self,
        message: str,
        customer_id: str,
        activity_callback: Optional[Callable[[ActivityEntry], Any]] = None,
    ) -> AgentResponse:
        """
        Process a traveler message by coordinating sub-agents through Bedrock-driven
        Strands tool delegation.

        Args:
            message: The traveler's message
            customer_id: Identifier for the traveler
            activity_callback: Optional callback for activity updates

        Returns:
            AgentResponse with message, optional packages, and optional booking
        """
        if activity_callback:
            self.activity_callback = activity_callback

        self._log_activity(
            activity_type="delegation",
            title="Supervisor processing request",
            details=f"Traveler: {customer_id} · Message: {message[:100]}{'…' if len(message) > 100 else ''}"
        )

        start_time = datetime.utcnow()

        # Bedrock LLM selects which delegation tool to invoke for this turn.
        prompt = (
            f"Traveler {customer_id} asks: {message}\n\n"
            "Choose the right specialist (search / package / booking) and synthesize a reply."
        )
        result = await self.agent.invoke_async(prompt)

        execution_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)

        self._log_activity(
            activity_type="delegation",
            title="Supervisor completed coordination",
            execution_time_ms=execution_time
        )

        return AgentResponse(
            message=str(result),
            products=None,
            order=None
        )
