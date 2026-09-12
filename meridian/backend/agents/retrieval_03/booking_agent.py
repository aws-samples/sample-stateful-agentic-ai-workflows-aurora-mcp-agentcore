"""
Phase 3 Booking Agent - Read-only trip price estimates.

Reads catalog prices using:
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
from decimal import Decimal
from typing import Callable, Any, Optional, List

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


class BookingAgent:
    """Read-only pricing specialist; booking writes require the governed flow."""

    def __init__(self, activity_callback: Optional[Callable[[ActivityEntry], Any]] = None):
        self.activity_callback = activity_callback or (lambda x: None)
        self.db = get_rds_data_client()

        self.model = BedrockModel(
            model_id=config.bedrock.model_id,
            region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1")
        )

        self.agent = Agent(
            model=self.model,
            tools=[self._calculate_booking_total_tool],
            system_prompt=self._get_system_prompt()
        )

    def _get_system_prompt(self) -> str:
        return """You are Meridian's read-only trip pricing specialist.

Your capabilities:
- Calculate sample estimates from catalog prices using the pricing tool

Guidelines:
- Present the tool's price breakdown as an estimate, not a reservation
- A price estimate does not hold capacity or confirm a booking
- Direct hold and booking requests to Meridian's governed confirmation flow"""

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
            agent_name="BookingAgent"
        )
        self.activity_callback(entry)

    @tool
    async def _calculate_booking_total_tool(self, items: List[dict]) -> dict:
        """Calculate booking total for trip line items."""
        return await self.calculate_booking_total(items)

    async def calculate_booking_total(self, items: List[dict]) -> dict:
        """Calculate booking total including tax and service fee."""
        start_time = datetime.now(timezone.utc)

        subtotal = Decimal('0')
        item_details = []

        for item in items:
            query = "SELECT package_id, name, price_per_person FROM trip_packages WHERE package_id = %s"
            package = await self.db.execute_one(query, (item['package_id'],))

            if package:
                travelers = item.get('travelers_count', item.get('quantity', 1))
                item_total = package['price_per_person'] * travelers
                subtotal += item_total
                item_details.append({
                    "package_id": package['package_id'],
                    "name": package['name'],
                    "travelers_count": travelers,
                    "duration": item.get('duration'),
                    "unit_price": float(package['price_per_person']),
                    "total": float(item_total)
                })

        tax = subtotal * Decimal('0.085')
        service_fee = Decimal('0') if subtotal >= Decimal('100') else Decimal('9.99')
        total = subtotal + tax + service_fee

        execution_time = int((datetime.now(timezone.utc) - start_time).total_seconds() * 1000)

        self._log_activity(
            activity_type="booking",
            title=f"Calculate total for {len(items)} trip(s)",
            details=f"Subtotal: ${float(subtotal):.2f}, Total: ${float(total):.2f}",
            execution_time_ms=execution_time
        )

        return {
            "items": item_details,
            "subtotal": float(subtotal),
            "tax": float(tax),
            # NOTE: key kept as `shipping` to match the Order pydantic model on the
            # API surface; surfaced in UI as "Service fee".
            "shipping": float(service_fee),
            "total": float(total),
            "free_service_fee_applied": service_fee == 0
        }


def create_booking_agent(
    activity_callback: Optional[Callable[[ActivityEntry], Any]] = None
) -> BookingAgent:
    """Create a Booking agent instance."""
    return BookingAgent(activity_callback=activity_callback)
