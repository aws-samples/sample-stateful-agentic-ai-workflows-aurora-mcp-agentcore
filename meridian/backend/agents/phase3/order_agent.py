"""
Phase 3 Booking Agent - Specialized in trip booking processing.

Implements order operations using:
- RDS Data API for Aurora PostgreSQL access
- Claude Opus 4.7 via Amazon Bedrock (cross-region inference)

Requirements: 11.4
"""

import os
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
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


class OrderAgent:
    """
    Order Agent specialized in order processing.
    
    Requirements:
    - 11.4: Order_Agent specialized in order processing
    """
    
    def __init__(self, activity_callback: Optional[Callable[[ActivityEntry], Any]] = None):
        """
        Initialize Order agent.
        
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
        
        # Create agent with order tools
        self.agent = Agent(
            model=self.model,
            tools=[self._calculate_total_tool, self._process_order_tool],
            system_prompt=self._get_system_prompt()
        )
    
    def _get_system_prompt(self) -> str:
        """Get the system prompt for the order agent."""
        return """You are a Booking Agent specialized in processing trip reservations.

Your capabilities:
- Calculate booking totals including tax and service fees
- Create booking records in Aurora

Guidelines:
- Always show price breakdown before confirming
- Apply reduced fees for bookings over $2,000 per person
- Tax rate is 8.5%
- Confirm traveler count and duration before finalizing"""
    
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
            agent_name="OrderAgent"
        )
        self.activity_callback(entry)
    
    @tool
    async def _calculate_total_tool(self, items: List[dict]) -> dict:
        """
        Calculate order total.
        
        Args:
            items: List of items with product_id, quantity, optional size
            
        Returns:
            Order total breakdown
        """
        return await self.calculate_total(items)
    
    @tool
    async def _process_order_tool(self, customer_id: str, items: List[dict]) -> dict:
        """
        Process a new order.
        
        Args:
            customer_id: Customer identifier
            items: List of order items
            
        Returns:
            Order confirmation
        """
        return await self.process_order(customer_id, items)
    
    async def calculate_total(self, items: List[dict]) -> dict:
        """
        Calculate booking total including tax and service fee.

        Args:
            items: List of items with package_id, travelers_count, optional duration

        Returns:
            Booking total breakdown
        """
        start_time = datetime.utcnow()
        
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
        
        # Calculate tax (8.5%) and service fee
        tax = subtotal * Decimal('0.085')
        service_fee = Decimal('0') if subtotal >= Decimal('100') else Decimal('9.99')
        total = subtotal + tax + service_fee
        
        execution_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)
        
        self._log_activity(
            activity_type="order",
            title=f"Calculate total for {len(items)} items",
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
    
    async def process_order(self, customer_id: str, items: List[dict]) -> dict:
        """
        Process a new order.
        
        Args:
            customer_id: Customer identifier
            items: List of order items
            
        Returns:
            Order confirmation
        """
        start_time = datetime.utcnow()
        
        # Calculate totals
        totals = await self.calculate_total(items)
        
        # Generate order ID
        booking_id = f"BKG-{uuid.uuid4().hex[:8].upper()}"

        insert_booking = """
            INSERT INTO bookings (booking_id, traveler_id, status, total_amount)
            VALUES (%s, %s, 'confirmed', %s)
        """
        await self.db.execute(insert_booking, (booking_id, customer_id, totals['total']))

        for item in totals['items']:
            insert_line = """
                INSERT INTO booking_lines (booking_id, package_id, duration, travelers_count, unit_price)
                VALUES (%s, %s, %s, %s, %s)
            """
            await self.db.execute(insert_line, (
                booking_id,
                item['package_id'],
                item.get('duration'),
                item['travelers_count'],
                item['unit_price']
            ))

        execution_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)

        self._log_activity(
            activity_type="order",
            title=f"Booking processed: {booking_id}",
            details=f"Traveler: {customer_id}, Total: ${totals['total']:.2f}",
            sql_query="INSERT INTO bookings...; INSERT INTO booking_lines...",
            execution_time_ms=execution_time
        )

        departure_date = datetime.utcnow() + timedelta(days=30)

        return {
            "booking_id": booking_id,
            "status": "confirmed",
            "items": totals['items'],
            "subtotal": totals['subtotal'],
            "tax": totals['tax'],
            "shipping": totals['shipping'],
            "total": totals['total'],
            "estimated_departure": departure_date.strftime("%B %d, %Y")
        }


def create_order_agent(
    activity_callback: Optional[Callable[[ActivityEntry], Any]] = None
) -> OrderAgent:
    """Create an Order agent instance."""
    return OrderAgent(activity_callback=activity_callback)
