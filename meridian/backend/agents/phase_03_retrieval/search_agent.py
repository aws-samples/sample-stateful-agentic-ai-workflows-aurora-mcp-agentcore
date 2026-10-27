"""
Phase 3: Search Agent (a Strands @tool over the one hybrid search).

Walkthrough
-----------
`_hybrid_search_tool` is the tool the Phase 3 model can call. Its body is ordinary code:
`backend/retrieval/hybrid.py` embeds the query (Cohere Embed v4, 1024 dimensions), fetches
pgvector and full-text candidates, merges them by package, keeps the trips that fit the
budget and have places left, and reranks them with Cohere Rerank 3.5. Phase 5's search step
runs the same function, so there is one implementation to read and to fix.

This class forwards that module's trace spans to the supervisor's callback and shapes its
rows for the model.

AWS docs:
  - Cohere Embed v4 on Bedrock:
    https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-embed-v4.html
  - Aurora PostgreSQL pgvector:
    https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/AuroraPostgreSQL.Extensions.html#AuroraPostgreSQL.Extensions.pgvector

"""

from typing import Any, Callable, List, Optional

from strands import Agent, tool

from backend.activity import ActivityEntry
from backend.bedrock_model import bedrock_model
from backend.retrieval import hybrid


class SearchAgent:
    """Search specialist for the Phase 3 supervisor: hybrid search over the trip catalog."""

    def __init__(self, activity_callback: Optional[Callable[[ActivityEntry], Any]] = None):
        self.activity_callback = activity_callback or (lambda entry: None)

        self.model = bedrock_model()

        self.agent = Agent(
            model=self.model,
            tools=[self._hybrid_search_tool],
            system_prompt=self._get_system_prompt()
        )

    def _get_system_prompt(self) -> str:
        """Get the system prompt for the search agent."""
        return """You are a Search Agent specialized in finding trip packages for travelers.

Your capabilities:
- Hybrid search over the trip catalog using natural language

You combine Cohere Embed v4 + pgvector (semantic) with PostgreSQL
full-text search (lexical), keep trips that fit the budget and have places left,
then re-rank with Cohere Rerank 3.5.

When searching:
- Understand the traveler's intent (destination, vibe, party size hints)
- Use hybrid search for open-ended trip discovery
- Return relevant packages with similarity scores"""

    @tool
    async def _hybrid_search_tool(self, query: str, limit: int = 5) -> List[dict]:
        """
        Search for trip packages using hybrid search (semantic + full-text + rerank).

        Args:
            query: Natural language search query
            limit: Maximum number of results (default 5)

        Returns:
            List of packages with similarity scores
        """
        return await self.hybrid_search(query, limit)

    async def hybrid_search(self, query: str, limit: int = 5) -> dict:
        """
        Run the hybrid retrieval pipeline and report each step to the supervisor.

        Args:
            query: Search query text
            limit: Maximum number of results

        Returns:
            Dict with the ranked packages and the query.
        """
        rows, spans, _counts = await hybrid.retrieval_rows(query, limit)
        for span in spans:
            self.activity_callback(span)
        return {"packages": [hybrid.package_payload(row) for row in rows], "query": query}


def create_search_agent(
    activity_callback: Optional[Callable[[ActivityEntry], Any]] = None
) -> SearchAgent:
    """Create a Search agent instance."""
    return SearchAgent(activity_callback=activity_callback)
