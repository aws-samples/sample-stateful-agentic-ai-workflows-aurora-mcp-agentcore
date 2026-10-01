"""
Phase 3 Package Agent - Specialized in trip package details and departure availability.

Implements package operations using:
- RDS Data API for Aurora PostgreSQL access
- Claude via Amazon Bedrock (configurable model_id)

AWS docs:
  - RDS Data API:
    https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.html
  - Bedrock model IDs:
    https://docs.aws.amazon.com/bedrock/latest/userguide/model-ids.html
"""

import os
import uuid
from datetime import datetime, timezone
from typing import Callable, Any, Optional

from strands import Agent, tool
from strands.models import BedrockModel
from pydantic import BaseModel

from backend.config import config
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


class PackageAgent:
    """Package Agent specialized in trip package details and departure availability."""

    def __init__(self, activity_callback: Optional[Callable[[ActivityEntry], Any]] = None):
        self.activity_callback = activity_callback or (lambda x: None)
        self.db = get_rds_data_client()

        self.model = BedrockModel(
            model_id=config.bedrock.model_id,
            region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1")
        )

        self.agent = Agent(
            model=self.model,
            tools=[self._get_details_tool, self._check_availability_tool],
            system_prompt=self._get_system_prompt()
        )

    def _get_system_prompt(self) -> str:
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
        entry = ActivityEntry(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            activity_type=activity_type,
            title=title,
            details=details,
            sql_query=sql_query,
            execution_time_ms=execution_time_ms,
            agent_name="PackageAgent"
        )
        self.activity_callback(entry)

    @tool
    async def _get_details_tool(self, package_id: str) -> dict:
        """Get detailed trip package information."""
        return await self.get_package_details(package_id)

    @tool
    async def _check_availability_tool(self, package_id: str, duration: Optional[str] = None) -> dict:
        """Check departure availability for a trip package."""
        return await self.check_departure_availability(package_id, duration)

    async def get_package_details(self, package_id: str) -> dict:
        """Get detailed information about a trip package."""
        start_time = datetime.now(timezone.utc)

        query = """
            SELECT package_id, name, operator, price_per_person, description,
                   image_url, trip_type, destination, durations, availability, highlights
            FROM trip_packages
            WHERE package_id = %s
        """

        result = await self.db.execute_one(query, (package_id,))

        execution_time = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)

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

    async def check_departure_availability(self, package_id: str, duration: Optional[str] = None) -> dict:
        """Check departure availability for a trip package."""
        start_time = datetime.now(timezone.utc)

        query = """
            SELECT package_id, name, durations, availability
            FROM trip_packages
            WHERE package_id = %s
        """

        result = await self.db.execute_one(query, (package_id,))

        execution_time = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)

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


def create_package_agent(
    activity_callback: Optional[Callable[[ActivityEntry], Any]] = None
) -> PackageAgent:
    """Create a Package agent instance."""
    return PackageAgent(activity_callback=activity_callback)
