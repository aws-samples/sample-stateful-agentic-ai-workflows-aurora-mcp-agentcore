"""Retrieval mode agent exports."""

from .supervisor import RetrievalAgent
from .search_agent import SearchAgent, create_search_agent
from .package_agent import PackageAgent, create_package_agent
from .booking_agent import BookingAgent, create_booking_agent

__all__ = [
    "RetrievalAgent",
    "SearchAgent",
    "PackageAgent",
    "BookingAgent",
    "create_search_agent",
    "create_package_agent",
    "create_booking_agent",
    "create_retrieval_system",
]


def create_retrieval_system(activity_callback=None):
    """Create the complete retrieval multi-agent system."""
    search_agent = create_search_agent(activity_callback)
    package_agent = create_package_agent(activity_callback)
    booking_agent = create_booking_agent(activity_callback)
    return RetrievalAgent(
        search_agent=search_agent,
        package_agent=package_agent,
        booking_agent=booking_agent,
        activity_callback=activity_callback,
    )
