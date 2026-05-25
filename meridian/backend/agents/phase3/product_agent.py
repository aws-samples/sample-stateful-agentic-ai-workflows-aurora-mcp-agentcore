"""
Phase 3 Package Agent - Specialized in trip package details and availability.

Implements package operations using:
- RDS Data API for Aurora PostgreSQL access
- Claude Opus 4.7 via Amazon Bedrock (cross-region inference)
"""

import os
import uuid
from datetime import datetime
from typing import Callable, Any, Optional, List

from strands import Agent, tool
from strands.models import BedrockModel
from pydantic import BaseModel

from backend.db.rds_data_client import get_rds_data_client


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


class ProductAgent:
    """Package Agent specialized in trip package details and departure availability."""
    
    def __init__(self, activity_callback: Optional[Callable[[ActivityEntry], Any]] = None):
        """
        Initialize Product agent.
        
        Args:
            activity_callback: Optional callback for reporting agent activities
        """
        self.activity_callback = activity_callback or (lambda x: None)
        self.db = get_rds_data_client()
        
        # Initialize model (cross-region inference)
        self.model = BedrockModel(
            model_id="global.anthropic.claude-opus-4-7-v1",
            region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1")
        )
        
        # Create agent with product tools
        self.agent = Agent(
            model=self.model,
            tools=[self._get_details_tool, self._check_inventory_tool],
            system_prompt=self._get_system_prompt()
        )
    
    def _get_system_prompt(self) -> str:
        """Get the system prompt for the product agent."""
        return """You are a Package Agent specialized in trip package information.

Your capabilities:
- Get detailed package information by ID
- Check departure availability and duration options

When helping travelers:
- Provide accurate package details (destination, operator, highlights)
- Check slot availability before recommending
- Suggest alternatives if departures are sold out"""
    
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
            timestamp=datetime.utcnow().isoformat() + "Z",
            activity_type=activity_type,
            title=title,
            details=details,
            sql_query=sql_query,
            execution_time_ms=execution_time_ms,
            agent_name="ProductAgent"
        )
        self.activity_callback(entry)
    
    @tool
    async def _get_details_tool(self, package_id: str) -> dict:
        """
        Get detailed trip package information.

        Args:
            package_id: Trip package identifier

        Returns:
            Package details
        """
        return await self.get_product_details(package_id)

    @tool
    async def _check_inventory_tool(self, package_id: str, duration: Optional[str] = None) -> dict:
        """
        Check departure availability for a trip package.

        Args:
            package_id: Trip package identifier
            duration: Optional duration option to check (e.g. "7 days")

        Returns:
            Availability status
        """
        return await self.check_inventory_status(package_id, duration)
    
    async def get_product_details(self, package_id: str) -> dict:
        """Get detailed information about a trip package."""
        start_time = datetime.utcnow()

        query = """
            SELECT package_id, name, operator, price_per_person, description,
                   image_url, trip_type, destination, durations, availability, highlights
            FROM trip_packages
            WHERE package_id = %s
        """

        result = await self.db.execute_one(query, (package_id,))

        execution_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)

        self._log_activity(
            activity_type="search",
            title=f"Package details: {package_id}",
            details=f"Found: {result['name'] if result else 'Not found'}",
            sql_query=query.strip(),
            execution_time_ms=execution_time
        )

        if not result:
            return {"error": f"Package {package_id} not found"}

        result['price_per_person'] = float(result['price_per_person'])
        return dict(result)

    async def check_inventory_status(self, package_id: str, duration: Optional[str] = None) -> dict:
        """Check departure availability for a trip package."""
        start_time = datetime.utcnow()

        query = """
            SELECT package_id, name, durations, availability
            FROM trip_packages
            WHERE package_id = %s
        """

        result = await self.db.execute_one(query, (package_id,))

        execution_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)

        self._log_activity(
            activity_type="availability",
            title=f"Availability check: {package_id}" + (f" duration {duration}" if duration else ""),
            sql_query=query.strip(),
            execution_time_ms=execution_time
        )

        if not result:
            return {"error": f"Package {package_id} not found"}

        availability = result.get('availability') or {}

        if duration:
            slots = availability.get(duration, 0)
            return {
                "package_id": package_id,
                "name": result['name'],
                "duration": duration,
                "slots": slots,
                "available": slots > 0,
                "durations": result.get('durations'),
            }

        total = sum(availability.values()) if isinstance(availability, dict) else 0
        return {
            "package_id": package_id,
            "name": result['name'],
            "total_slots": total,
            "availability_by_duration": availability,
            "available": total > 0,
            "durations": result.get('durations'),
        }


def create_product_agent(
    activity_callback: Optional[Callable[[ActivityEntry], Any]] = None
) -> ProductAgent:
    """Create a Product agent instance."""
    return ProductAgent(activity_callback=activity_callback)
