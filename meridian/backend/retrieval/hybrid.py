"""Hybrid trip search: vector and lexical candidates, fused, then reranked."""

import asyncio
import re
from typing import Any, List

from backend.activity import ActivityEntry, Product, create_activity
from backend.catalog_compat import row_to_api_product
from backend.config import config
from backend.db.embedding_service import get_embedding_service
from backend.db.rds_data_client import get_rds_data_client
from backend.timing import clock, elapsed_ms


async def retrieval_search(query: str, limit: int = 5) -> tuple[List[Product], List[ActivityEntry]]:
    """
    Retrieval mode: hybrid candidates + Cohere rerank.
    - Candidate retrieval: pgvector semantic + tsvector lexical search on Aurora
    - Ranking: Cohere Rerank on Bedrock
    """
    activities = []

    db = get_rds_data_client()

    # Optional budget filter parsed from natural language.
    price_filter = None
    price_match = re.search(r'(?:under|below|less than|<)\s*\$?(\d+(?:\.\d{2})?)', query.lower())
    if price_match:
        price_filter = float(price_match.group(1))

    activities.append(create_activity(
        activity_type="reasoning",
        title="Delegating to SearchAgent",
        details="Supervisor routing search request to specialized agent",
        agent_name="RetrievalAgent",
        agent_file="agents/phase_03_retrieval/supervisor.py"
    ))

    activities.append(create_activity(
        activity_type="embedding",
        title="Generating query embedding",
        details="Cohere Embed v4 Embeddings (1024d)",
        agent_name="SearchAgent",
        agent_file="agents/phase_03_retrieval/search_agent.py"
    ))
    
    embedding_service = get_embedding_service()
    embedding_started = clock()
    query_embedding = await asyncio.to_thread(embedding_service.generate_text_embedding, query)
    embedding_time = elapsed_ms(embedding_started)
    embedding_str = '[' + ','.join(str(x) for x in query_embedding) + ']'

    activities.append(create_activity(
        activity_type="embedding",
        title="Embedding generated",
        execution_time_ms=embedding_time,
        agent_name="SearchAgent",
        agent_file="agents/phase_03_retrieval/search_agent.py"
    ))

    # Step 2: Hybrid candidate retrieval (semantic + lexical).
    activities.append(create_activity(
        activity_type="search",
        title="Hybrid candidate retrieval",
        details="pgvector cosine + tsvector/ts_rank",
        agent_name="SearchAgent",
        agent_file="agents/phase_03_retrieval/search_agent.py"
    ))
    candidate_limit = max(limit * config.search.rerank_candidate_multiplier, 25)
    # Cast the limit to ::integer — the function signature is
    # semantic_trip_search(vector, integer); Python ints arrive over the
    # RDS Data API as bigint and Postgres can't resolve the overload
    # otherwise ("function semantic_trip_search(vector, bigint) does not exist").
    semantic_sql = """
        SELECT * FROM semantic_trip_search(%s::vector, %s::integer)
    """
    search_started = clock()
    semantic_rows = await db.execute(semantic_sql, (embedding_str, candidate_limit))
    if price_filter is not None:
        semantic_rows = [r for r in semantic_rows if float(r["price_per_person"]) <= price_filter]

    # websearch_to_tsquery joins bare terms with AND, so a conversational
    # prompt requires every stemmed term in one row and the lexical arm
    # returns nothing. Rewrite the operators to OR and let ts_rank order the
    # results. Keep this in step with SearchAgent.hybrid_search.
    lexical_sql = """
        WITH q AS (
            SELECT replace(
                websearch_to_tsquery('english', %s)::text, '&', '|'
            )::tsquery AS tsq
        )
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
            ts_rank(search_vector, q.tsq) AS lexical_score
        FROM trip_packages, q
        WHERE search_vector @@ q.tsq
    """
    lexical_params: list[Any] = [query]
    if price_filter is not None:
        lexical_sql += " AND price_per_person <= %s"
        lexical_params.append(price_filter)
    lexical_sql += " ORDER BY lexical_score DESC LIMIT %s"
    lexical_params.append(candidate_limit)
    lexical_rows = await db.execute(lexical_sql, tuple(lexical_params))
    # Both queries, measured together: the semantic and the lexical arm.
    search_time = elapsed_ms(search_started)

    # Merge candidates by package_id so rerank sees one entry per trip package.
    merged_by_package: dict[str, dict[str, Any]] = {}
    for row in semantic_rows:
        merged_by_package[row["package_id"]] = dict(row)
    for row in lexical_rows:
        existing = merged_by_package.get(row["package_id"])
        if existing:
            existing["lexical_score"] = float(row.get("lexical_score", 0.0))
        else:
            merged_by_package[row["package_id"]] = dict(row)
    candidate_rows = list(merged_by_package.values())

    activities.append(create_activity(
        activity_type="search",
        title="Hybrid candidates fetched",
        details=(
            f"{len(candidate_rows)} unique candidates "
            f"(semantic={len(semantic_rows)}, lexical={len(lexical_rows)})"
        ),
        sql_query=(
            "SELECT * FROM semantic_trip_search(query_vector, candidate_limit); "
            "SELECT ... ts_rank(search_vector, websearch_to_tsquery(...)) ..."
        ),
        execution_time_ms=search_time,
        agent_name="SearchAgent",
        agent_file="agents/phase_03_retrieval/search_agent.py"
    ))

    # Step 3: Cohere rerank over semantic candidates.
    embedding_service = get_embedding_service()
    docs = [
        " | ".join([
            row.get("name", "") or "",
            row.get("destination", "") or "",
            row.get("trip_type", "") or "",
            row.get("operator", "") or "",
            row.get("description", "") or "",
        ])
        for row in candidate_rows
    ]
    ranked_rows = candidate_rows
    rerank_failed = False
    rerank_started = clock()
    try:
        ranked = await asyncio.to_thread(embedding_service.rerank_documents, query, docs, top_n=limit)
        ranked_rows = [candidate_rows[item["index"]] for item in ranked if item["index"] < len(candidate_rows)]
    except Exception:
        rerank_failed = True
        ranked_rows = candidate_rows[:limit]
    rerank_time = elapsed_ms(rerank_started)
    activities.append(create_activity(
        activity_type="search",
        title="Cohere rerank applied" if not rerank_failed else "Cohere rerank unavailable",
        details=(
            f"Reranked to top {len(ranked_rows[:limit])} trips"
            if not rerank_failed
            else "Falling back to semantic rank order"
        ),
        execution_time_ms=rerank_time,
        agent_name="SearchAgent",
        agent_file="agents/phase_03_retrieval/search_agent.py"
    ))

    activities.append(create_activity(
        activity_type="result",
        title=f"SearchAgent returned {len(ranked_rows[:limit])} results",
        details="Returning ranked trips to RetrievalAgent",
        agent_name="RetrievalAgent",
        agent_file="agents/phase_03_retrieval/supervisor.py"
    ))

    products = [Product(**row_to_api_product(row)) for row in ranked_rows[:limit]]
    return products, activities
