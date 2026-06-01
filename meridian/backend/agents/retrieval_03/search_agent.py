"""
Phase 3 — Search Agent (Strands @tool + hybrid search over Aurora).

Presenter walkthrough
---------------------
Show `_hybrid_search_tool` and `hybrid_search()` when explaining
specialist agents under the Phase 3 supervisor. The agent exposes one
tool; inside it runs the full hybrid pipeline: embed (1024-dim Cohere
Embed v4) → pgvector candidates (`semantic_trip_search`) + tsvector
lexical candidates → merge/dedup → Cohere Rerank 3.5.

AWS docs:
  - Cohere Embed v4 on Bedrock:
    https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-embed-v4.html
  - Aurora PostgreSQL pgvector:
    https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/AuroraPostgreSQL.Extensions.html#AuroraPostgreSQL.Extensions.pgvector
  - RDS Data API (hybrid SQL execution):
    https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.html

"""

import os
import uuid
from datetime import datetime
from typing import Callable, Any, Optional, List

from strands import Agent, tool
from strands.models import BedrockModel

from backend.config import config
from pydantic import BaseModel

from backend.db.embedding_service import get_embedding_service
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


# Cohere Embed v4 Configuration (1024 dimensions)
EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "1024"))


class SearchAgent:
    """
    Search Agent specialized in semantic trip package search.

    """

    def __init__(self, activity_callback: Optional[Callable[[ActivityEntry], Any]] = None):
        self.activity_callback = activity_callback or (lambda x: None)
        self.db = get_rds_data_client()
        self.embedding_service = get_embedding_service()

        self.model = BedrockModel(
            model_id=config.bedrock.model_id,
            region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1")
        )

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
full-text search (lexical), then re-rank with Cohere Rerank 3.5.

When searching:
- Understand the traveler's intent (destination, vibe, party size hints)
- Use hybrid search for open-ended trip discovery
- Return relevant packages with similarity scores"""

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
            agent_name="SearchAgent"
        )
        self.activity_callback(entry)

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
        Run the hybrid retrieval pipeline: embed → pgvector + tsvector
        candidates → merge/dedup → Cohere Rerank 3.5.
        
        Args:
            query: Search query text
            limit: Maximum number of results
            
        Returns:
            Dict with packages list and similarity scores
        """
        start_time = datetime.utcnow()
        
        # Generate query embedding
        self._log_activity(
            activity_type="embedding",
            title="Generating text embedding",
            details=f"Query: {query[:50]}..."
        )
        
        query_embedding = self.embedding_service.generate_text_embedding(
            query, input_type="search_query"
        )

        embedding_time = int((datetime.utcnow() - start_time).total_seconds() * 1000)

        self._log_activity(
            activity_type="embedding",
            title="Text embedding generated",
            details=f"Dimension: {len(query_embedding)} ({EMBEDDING_DIMENSION}d pgvector)",
            execution_time_ms=embedding_time
        )

        search_start = datetime.utcnow()
        # Pull a WIDER pool than we'll return (limit*5, floored at 25). The
        # reranker needs candidates to choose from — feeding it only `limit`
        # rows would leave it nothing to reorder. This is the recall step;
        # the reranker is the precision step.
        candidate_limit = max(limit * config.search.rerank_candidate_multiplier, 25)

        # --- Arm 1 of the hybrid retrieval: SEMANTIC (meaning) -----------------
        # semantic_trip_search() is a pgvector cosine search (HNSW index). It is
        # genuinely semantic-only; the "hybrid" part is assembled in Python below
        # by merging these rows with the lexical arm.
        # Cast both args explicitly. Without ::integer, Python ints arrive as
        # bigint and Postgres can't find a matching overload (the function is
        # declared as semantic_trip_search(vector, integer)).
        sql = """
            SELECT * FROM semantic_trip_search(%s::vector, %s::integer)
        """

        embedding_str = '[' + ','.join(map(str, query_embedding)) + ']'

        semantic_rows = await self.db.execute(sql, (embedding_str, candidate_limit))
        
        search_time = int((datetime.utcnow() - search_start).total_seconds() * 1000)
        
        self._log_activity(
            activity_type="search",
            title=f"Semantic search: '{query}'",
            details=f"Found {len(semantic_rows)} semantic candidates",
            sql_query="SELECT * FROM semantic_trip_search(...)",
            execution_time_ms=search_time
        )

        # --- Arm 2 of the hybrid retrieval: LEXICAL (exact terms) -------------
        # PostgreSQL full-text search over the generated `search_vector` tsvector.
        # websearch_to_tsquery parses the query the way a search box would;
        # ts_rank scores the match. This arm catches exact terms — a destination
        # name, an operator — that pure embeddings can blur.
        lexical_sql = """
            SELECT
                package_id,
                name,
                operator,
                price_per_person,
                description,
                image_url,
                trip_type,
                destination,
                region,
                durations,
                ts_rank(search_vector, websearch_to_tsquery('english', %s)) AS lexical_score
            FROM trip_packages
            WHERE search_vector @@ websearch_to_tsquery('english', %s)
            ORDER BY lexical_score DESC
            LIMIT %s
        """
        lexical_rows = await self.db.execute(lexical_sql, (query, query, candidate_limit))
        # --- Merge + dedup by package_id -------------------------------------
        # This is the "fusion" step: union the two candidate pools keyed on
        # package_id so a trip found by BOTH arms appears once (and carries its
        # lexical_score). No weighted blending here — the reranker owns final order.
        merged_by_package: dict[str, dict] = {}
        for row in semantic_rows:
            merged_by_package[row["package_id"]] = dict(row)
        for row in lexical_rows:
            existing = merged_by_package.get(row["package_id"])
            if existing:
                existing["lexical_score"] = float(row.get("lexical_score", 0.0))
            else:
                merged_by_package[row["package_id"]] = dict(row)
        results = list(merged_by_package.values())
        self._log_activity(
            activity_type="search",
            title="Lexical candidates merged",
            details=f"{len(results)} unique candidates (lexical={len(lexical_rows)})",
            sql_query="SELECT ... ts_rank(search_vector, websearch_to_tsquery(...)) ...",
        )

        # --- Final stage: RERANK (precision) ---------------------------------
        # Cohere Rerank 3.5 is a cross-encoder: it reads the query and each
        # candidate *together* and scores true relevance, fixing the ordering
        # that the two retrieval arms can only approximate. We flatten each
        # candidate to a single text block for scoring.
        rerank_start = datetime.utcnow()
        docs = [
            " | ".join(
                [
                    r.get("name", "") or "",
                    r.get("destination", "") or "",
                    r.get("trip_type", "") or "",
                    r.get("operator", "") or "",
                    r.get("description", "") or "",
                ]
            )
            for r in results
        ]
        ranked = []
        try:
            ranked = self.embedding_service.rerank_documents(query, docs, top_n=limit)
        except Exception as exc:
            # Graceful degradation: if the reranker is unavailable, we keep the
            # merged hybrid order rather than failing the request.
            self._log_activity(
                activity_type="reasoning",
                title="Cohere rerank unavailable, using semantic rank",
                details=str(exc)[:160],
            )

        rerank_time = int((datetime.utcnow() - rerank_start).total_seconds() * 1000)
        if ranked:
            # rerank_documents returns [{index, score}] pointing back into
            # `results`; reorder the merged pool by the reranker's verdict AND
            # carry the reranker's relevance score onto each row as `similarity`.
            # Without this, cards would display the stale pgvector cosine value
            # (or 0.0 for lexical-only hits) while being ordered by the
            # reranker — so the shown % wouldn't match the card order.
            self._log_activity(
                activity_type="search",
                title="Cohere rerank applied",
                details=f"Top {min(limit, len(results))} hybrid candidates reranked",
                execution_time_ms=rerank_time,
            )
            ordered_rows = []
            for item in ranked:
                idx = item["index"]
                if idx < len(results):
                    row = dict(results[idx])
                    row["similarity"] = float(item.get("score", 0.0))
                    ordered_rows.append(row)
        else:
            # Fallback: no reranker — keep the merged hybrid order and the
            # pgvector cosine similarity already on each row.
            ordered_rows = results[:limit]

        packages = []
        for r in ordered_rows[:limit]:
            packages.append({
                "package_id": r['package_id'],
                "name": r['name'],
                "operator": r['operator'],
                "price_per_person": float(r['price_per_person']),
                "description": r['description'],
                "image_url": r['image_url'],
                "trip_type": r['trip_type'],
                "destination": r.get('destination'),
                "similarity": float(r.get('similarity', 0.0))
            })

        # Guarantee descending similarity so the UI cards always read top-down
        # by match strength, matching the order the prose describes. (Cohere
        # already returns sorted; this is belt-and-suspenders for the fallback
        # path and any future API change.)
        packages.sort(key=lambda p: p["similarity"], reverse=True)

        return {"packages": packages, "query": query}
    


def create_search_agent(
    activity_callback: Optional[Callable[[ActivityEntry], Any]] = None
) -> SearchAgent:
    """Create a Search agent instance."""
    return SearchAgent(activity_callback=activity_callback)
