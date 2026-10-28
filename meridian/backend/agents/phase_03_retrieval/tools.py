"""The two Phase 3 tools that run in this process; the rest come over MCP stdio."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from strands import tool

from backend.activity import ActivityEntry
from backend.agents.strands_hooks import is_setup_error
from backend.security.redaction import mask_log_text, masked_traceback
from backend.tools import catalog_tools, package_tools

logger = logging.getLogger(__name__)

LOCAL_TOOLS = ("hybrid_trip_search", "get_package_details")
# The answer for a call that failed, in the shape the MeridianCatalog Lambda answers with.
TOOL_FAILED = "catalog_tool_failed"
DETAIL_CHARS = 300


def local_tools(spans: List[ActivityEntry]) -> list:
    """Strands tools for ``LOCAL_TOOLS``. The hybrid search appends its own spans to ``spans``."""

    async def hybrid_trip_search(query: str, limit: int = 5) -> Dict[str, Any]:
        try:
            result, activities = await catalog_tools.hybrid_trip_search_with_spans(query, limit)
        except Exception as error:
            return failure_answer("hybrid_trip_search", error)
        spans.extend(activities)
        return result

    hybrid_trip_search.__doc__ = catalog_tools.hybrid_trip_search.__doc__

    async def get_package_details(packageId: str) -> Dict[str, Any]:
        try:
            return await package_tools.get_package_details(packageId)
        except Exception as error:
            return failure_answer("get_package_details", error)

    get_package_details.__doc__ = package_tools.get_package_details.__doc__

    return [tool(hybrid_trip_search), tool(get_package_details)]


def failure_answer(name: str, error: Exception) -> Dict[str, Any]:
    """What the model is told when a local tool fails: a fixed answer, never the error text.

    The log gets the exception class, a masked detail and the masked traceback.

    Raises:
        Exception: ``error`` itself when the tool could not be set up, so the stop hook in
            ``backend/agents/strands_hooks.py`` ends the turn instead of the model answering.
    """
    if is_setup_error(error):
        raise error
    logger.warning("Phase 3 tool %s failed (%s): %s\n%s", name, type(error).__name__,
                   mask_log_text(str(error))[:DETAIL_CHARS], masked_traceback(error))
    return {"error": TOOL_FAILED, "tool": name}
