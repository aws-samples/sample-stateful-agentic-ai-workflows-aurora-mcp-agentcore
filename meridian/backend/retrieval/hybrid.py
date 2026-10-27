"""The one hybrid trip search: vector and full-text candidates, gated, merged, reranked.

Phase 3's search tool and Phase 5's search step both call ``retrieval_search``, so a fix to
the lexical arm, the eligibility gate or the rerank fallback lands in both.

  * semantic arm: pgvector cosine through ``semantic_trip_search`` (HNSW index), on a query
    embedded as ``search_query`` by Cohere Embed v4;
  * lexical arm: PostgreSQL full-text search, positive terms OR-joined, negations kept;
  * merge by package, join to the live catalog (durations, places left, highlights);
  * gate on the prompt's price ceiling and on ``package_places_left``;
  * Cohere Rerank 3.5 on Amazon Bedrock, with the candidate order as the labelled fallback.

AWS docs:
  - Cohere Embed v4 on Bedrock:
    https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-embed-v4.html
  - Aurora PostgreSQL pgvector:
    https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/AuroraPostgreSQL.Extensions.html#AuroraPostgreSQL.Extensions.pgvector
"""

import asyncio
import json
from typing import Any, Dict, List, Optional, Tuple

from backend.activity import ActivityEntry, Product, create_activity
from backend.catalog_compat import row_to_api_product
from backend.config import config
from backend.db.embedding_service import EmbeddingUnavailable, get_embedding_service
from backend.db.rds_data_client import get_rds_data_client
from backend.search_utils import display_sql, parse_price_ceiling
from backend.security.redaction import mask_account_ids
from backend.timing import clock, elapsed_ms
from backend.trace_names import RETRIEVAL_CODE

AGENT_FILE = "retrieval/hybrid.py"
MIN_CANDIDATES = 25

# Cast both arguments: Python ints arrive over the RDS Data API as bigint, and the function
# is declared semantic_trip_search(vector, integer).
SEMANTIC_SQL = "SELECT * FROM semantic_trip_search(%s::vector, %s::integer)"

# websearch_to_tsquery joins bare terms with AND, so a conversational prompt would need every
# stemmed term in one row and the arm would return nothing. The positive terms are rewritten
# to OR and ts_rank orders the result. Negations stay ANDed: turning "tokyo -luxury" into
# "tokyo OR NOT luxury" makes !luxury match every non-luxury row. Traveler text is only ever
# the bound parameter; the concatenation works on PostgreSQL's own canonical tsquery text.
LEXICAL_SQL = """
    WITH raw AS (
        SELECT websearch_to_tsquery('english', %s)::text AS text_query
    ),
    parts AS (
        SELECT
            (SELECT string_agg(part, ' | ')
               FROM unnest(string_to_array(text_query, ' & ')) AS part
              WHERE left(part, 1) <> '!') AS positives,
            (SELECT string_agg(part, ' & ')
               FROM unnest(string_to_array(text_query, ' & ')) AS part
              WHERE left(part, 1) = '!') AS negatives
        FROM raw
    ),
    q AS (
        SELECT CASE
            WHEN positives IS NULL THEN negatives
            WHEN negatives IS NULL THEN '(' || positives || ')'
            ELSE '(' || positives || ') & ' || negatives
        END::tsquery AS tsq
        FROM parts
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
        package_places_left(package_id) AS availability,
        highlights,
        ts_rank(search_vector, q.tsq) AS lexical_score
    FROM trip_packages, q
    WHERE search_vector @@ q.tsq
    ORDER BY lexical_score DESC
    LIMIT %s
"""

# The semantic function returns a compact ranking shape. Joining every merged candidate to the
# catalog gives semantic-only hits the same card facts and places left that lexical hits carry.
HYDRATE_SQL = """
    SELECT package_id, name, operator, price_per_person, description, image_url,
           trip_type, destination, region, durations,
           package_places_left(package_id) AS availability, highlights
    FROM trip_packages
    WHERE package_id IN ({placeholders})
"""


def candidate_limit(limit: int) -> int:
    """How many candidates each arm fetches: a pool wider than the answer, floored at 25.

    The reranker needs candidates to choose from. Fetching only ``limit`` rows would leave it
    nothing to reorder. This is the recall step; the reranker is the precision step.
    """
    return max(limit * config.search.rerank_candidate_multiplier, MIN_CANDIDATES)


def total_places(availability: Any) -> int:
    """Total places left across a package's durations.

    Args:
        availability: ``package_places_left``'s object (duration to places), the older
            ``{"quantity": n}`` shape, or its JSON text.

    Returns:
        The total, or 0 when the shape is unrecognized or empty.
    """
    if isinstance(availability, str):
        try:
            availability = json.loads(availability)
        except (ValueError, TypeError):
            return 0
    if not isinstance(availability, dict):
        return 0
    if "quantity" in availability:
        try:
            return int(availability["quantity"])
        except (ValueError, TypeError):
            return 0
    total = 0
    for value in availability.values():
        try:
            total += int(value)
        except (ValueError, TypeError):
            continue
    return total


def places_by_package(rows: List[Dict[str, Any]]) -> Dict[str, int]:
    """Places left per package id, from each row's hydrated ``availability``."""
    return {str(row["package_id"]): total_places(row.get("availability")) for row in rows}


def merge_by_package(
    semantic_rows: List[Dict[str, Any]], lexical_rows: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Union the two arms by package id: semantic order first, lexical-only rows after.

    A trip both arms found appears once and carries its ``lexical_score``. No weights are
    blended here; the reranker owns the final order.
    """
    merged: Dict[str, Dict[str, Any]] = {}
    for row in semantic_rows:
        merged[row["package_id"]] = dict(row)
    for row in lexical_rows:
        existing = merged.get(row["package_id"])
        if existing:
            existing["lexical_score"] = float(row.get("lexical_score", 0.0))
        else:
            merged[row["package_id"]] = dict(row)
    return list(merged.values())


def apply_gate(
    rows: List[Dict[str, Any]], price_ceiling: Optional[float], places_left: Dict[str, int]
) -> List[Dict[str, Any]]:
    """Keep the rows a traveler could actually take, in their order.

    Relevance is not eligibility. A row is dropped when its price is over the ceiling or
    when its package has no places left. A package missing from ``places_left`` counts as
    having none: unknown stock is not stock.
    """
    kept = []
    for row in rows:
        price = row.get("price_per_person")
        if price_ceiling is not None and price is not None and float(price) > price_ceiling:
            continue
        if places_left.get(str(row["package_id"]), 0) <= 0:
            continue
        kept.append(row)
    return kept


def rerank_document(row: Dict[str, Any]) -> str:
    """The text the reranker reads for one candidate."""
    fields = ("name", "destination", "trip_type", "operator", "description")
    return " | ".join(row.get(field, "") or "" for field in fields)


def package_payload(row: Dict[str, Any]) -> Dict[str, Any]:
    """A ranked row as the dict the Phase 3 tool returns to the model."""
    return {
        "package_id": row["package_id"],
        "name": row["name"],
        "operator": row.get("operator"),
        "price_per_person": float(row["price_per_person"]),
        "description": row.get("description"),
        "image_url": row.get("image_url"),
        "trip_type": row.get("trip_type"),
        "destination": row.get("destination"),
        "region": row.get("region"),
        "durations": row.get("durations") or [],
        "availability": row.get("availability") or {},
        "highlights": row.get("highlights") or [],
        "similarity": float(row.get("similarity", 0.0)),
        "pre_rerank_position": row.get("pre_rerank_position"),
        "pre_rerank_similarity": float(row.get("pre_rerank_similarity", 0.0)),
        "rank_delta": row.get("rank_delta", 0),
    }


def _span(
    kind: str, title: str, *, details: Optional[str] = None, sql: Optional[str] = None,
    ms: Optional[int] = None,
) -> ActivityEntry:
    return create_activity(
        activity_type=kind, title=title, details=details, sql_query=sql,
        execution_time_ms=ms, agent_name=RETRIEVAL_CODE, agent_file=AGENT_FILE,
    )


def _opening_spans() -> List[ActivityEntry]:
    return [
        _span(
            "reasoning", "Hybrid retrieval started",
            details=(
                "Code embeds the query, fetches pgvector and full-text candidates, keeps "
                "the ones that fit the budget and have places left, and reranks them with "
                "Cohere. The calling step decides when to search; no model runs inside."
            ),
        ),
        _span(
            "embedding", "Generating query embedding",
            details="Cohere Embed v4 (1024d), input_type search_query",
        ),
    ]


async def _embed(query: str) -> Tuple[Optional[str], ActivityEntry]:
    """Embed the query as a search query; return the pgvector literal and the timing span.

    A vector from a different model is not a degraded answer, it is a wrong one. When the
    corpus model cannot be reached the semantic arm is dropped and the lexical arm answers
    alone: fewer results, all of them real, and the trace says so.
    """
    service = get_embedding_service()
    started = clock()
    try:
        vector = await asyncio.to_thread(
            service.generate_text_embedding, query, input_type="search_query"
        )
    except EmbeddingUnavailable as exc:
        return None, _span(
            "embedding", "Semantic arm unavailable, lexical only",
            details=mask_account_ids(str(exc))[:160], ms=elapsed_ms(started),
        )
    literal = "[" + ",".join(str(value) for value in vector) + "]"
    return literal, _span(
        "embedding", "Embedding generated", details=f"{len(vector)} dimensions",
        ms=elapsed_ms(started),
    )


async def _arms(
    db: Any, query: str, vector: Optional[str], limit: int
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], int, int]:
    """Run both arms; return their rows, their combined time and the pool size."""
    pool = candidate_limit(limit)
    started = clock()
    semantic = await db.execute(SEMANTIC_SQL, (vector, pool)) if vector is not None else []
    lexical = await db.execute(LEXICAL_SQL, (query, pool))
    return semantic, lexical, elapsed_ms(started), pool


def _arms_span(
    query: str, vector: Optional[str], pool: int, counts: Dict[str, Any], ms: int
) -> ActivityEntry:
    statements = []
    if vector is not None:
        statements.append(display_sql(SEMANTIC_SQL, (vector, pool)))
    statements.append(display_sql(LEXICAL_SQL, (query, pool)))
    return _span(
        "search", "Hybrid candidates fetched",
        details=(
            f"{counts['merged']} unique candidates "
            f"(semantic={counts['semantic']}, lexical={counts['lexical']})"
        ),
        sql="\n\n".join(statements), ms=ms,
    )


async def _hydrate(
    db: Any, merged: List[Dict[str, Any]]
) -> Tuple[List[Dict[str, Any]], Optional[ActivityEntry]]:
    """Join every candidate to the live catalog, keeping the arms' scores."""
    if not merged:
        return merged, None
    ids = tuple(str(row["package_id"]) for row in merged)
    sql = HYDRATE_SQL.format(placeholders=", ".join(["%s"] * len(ids)))
    started = clock()
    detail_rows = await db.execute(sql, ids)
    ms = elapsed_ms(started)
    by_id = {str(row["package_id"]): dict(row) for row in detail_rows}
    hydrated = []
    for candidate in merged:
        scores = {
            key: candidate[key] for key in ("similarity", "lexical_score") if key in candidate
        }
        hydrated.append({**candidate, **by_id.get(str(candidate["package_id"]), {}), **scores})
    span = _span(
        "search", "Catalog card details hydrated",
        details=f"{len(detail_rows)} candidates joined to durations, places left and highlights",
        sql=display_sql(sql, ids), ms=ms,
    )
    return hydrated, span


def _gate(
    rows: List[Dict[str, Any]], query: str
) -> Tuple[List[Dict[str, Any]], Optional[ActivityEntry]]:
    ceiling = parse_price_ceiling(query)
    kept = apply_gate(rows, ceiling, places_by_package(rows))
    dropped = len(rows) - len(kept)
    if not dropped:
        return kept, None
    over = sum(
        1 for row in rows
        if ceiling is not None and row.get("price_per_person") is not None
        and float(row["price_per_person"]) > ceiling
    )
    reasons = []
    if over:
        reasons.append(f"{over} over the ${ceiling:,.0f} budget")
    if dropped - over:
        reasons.append(f"{dropped - over} with no places left")
    span = _span(
        "search", "Eligibility filter applied",
        details=f"Dropped {dropped} of {len(rows)} candidates: " + ", ".join(reasons),
    )
    return kept, span


def _candidate_order(rows: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    """The fallback ranking: the order the two arms produced, with no movement."""
    ordered = [dict(row) for row in rows[:limit]]
    for position, row in enumerate(ordered):
        row["pre_rerank_position"] = position
        row["rank_delta"] = 0
    return ordered


def _apply_rerank(
    rows: List[Dict[str, Any]], ranked: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Reorder by the reranker's verdict and carry its score and each row's movement."""
    ordered: List[Dict[str, Any]] = []
    for item in ranked:
        index = item["index"]
        if not 0 <= index < len(rows):
            continue
        row = dict(rows[index])
        row["similarity"] = float(item.get("score", 0.0))
        row["rank_delta"] = row["pre_rerank_position"] - len(ordered)
        ordered.append(row)
    return ordered


async def _rerank(
    query: str, rows: List[Dict[str, Any]], limit: int
) -> Tuple[List[Dict[str, Any]], ActivityEntry, bool]:
    """Cohere Rerank 3.5 over the eligible candidates; the candidate order on any failure.

    Returns:
        The ranked rows, the span, and whether the reranker's order was applied.
    """
    if not rows:
        span = _span("search", "Cohere rerank skipped", details="No eligible candidates.")
        return [], span, False
    service = get_embedding_service()
    documents = [rerank_document(row) for row in rows]
    started = clock()
    try:
        ranked = await asyncio.to_thread(
            service.rerank_documents, query, documents, top_n=limit
        )
        ordered = _apply_rerank(rows, ranked)[:limit]
        if not ordered:
            raise RuntimeError("the reranker returned no usable results for these candidates")
    except Exception as exc:  # noqa: BLE001 - any rerank failure keeps the candidate order
        reason = mask_account_ids(str(exc))[:120]
        span = _span(
            "search", "Cohere rerank unavailable, using candidate order",
            details=(
                f"{exc.__class__.__name__}: {reason}. The candidates keep the order the two "
                "arms produced."
            ),
            ms=elapsed_ms(started),
        )
        return _candidate_order(rows, limit), span, False
    span = _span(
        "search", "Cohere rerank applied",
        details=f"Top {len(ordered)} hybrid candidates reranked", ms=elapsed_ms(started),
    )
    return ordered, span, True


async def retrieval_rows(
    query: str, limit: int = 5
) -> Tuple[List[Dict[str, Any]], List[ActivityEntry], Dict[str, Any]]:
    """Run the hybrid search and return the ranked catalog rows, trace spans and counts.

    Args:
        query: The traveler's words. A price ceiling in them ("under $2,000") is applied.
        limit: How many trips to return.

    Returns:
        The ranked rows (each with ``similarity``, ``pre_rerank_position``,
        ``pre_rerank_similarity`` and ``rank_delta``), the spans in the order they ran, and
        the counts ``{"semantic", "lexical", "merged", "eligible": int, "reranked": bool}``
        taken from the arms, the merge, the gate and the reranker, so a caller never has to
        parse them back out of span text.
    """
    db = get_rds_data_client()
    spans = _opening_spans()
    vector, embedded = await _embed(query)
    spans.append(embedded)

    semantic, lexical, arms_ms, pool = await _arms(db, query, vector, limit)
    merged = merge_by_package(semantic, lexical)
    counts: Dict[str, Any] = {
        "semantic": len(semantic), "lexical": len(lexical), "merged": len(merged),
    }
    spans.append(_arms_span(query, vector, pool, counts, arms_ms))

    hydrated, hydrate_span = await _hydrate(db, merged)
    eligible, gate_span = _gate(hydrated, query)
    spans.extend(span for span in (hydrate_span, gate_span) if span)
    counts["eligible"] = len(eligible)

    # Stamp each candidate's position and cosine before the reranker overwrites `similarity`,
    # so the UI can show the order the arms produced and animate the reranker's verdict.
    for position, row in enumerate(eligible):
        row["pre_rerank_position"] = position
        row["pre_rerank_similarity"] = float(row.get("similarity", 0.0))

    ranked, rerank_span, reranked = await _rerank(query, eligible, limit)
    counts["reranked"] = reranked
    spans.append(rerank_span)
    spans.append(_span(
        "result", f"Hybrid retrieval returned {len(ranked)} results",
        details="Ranked trips returned to the calling step",
    ))
    return ranked, spans, counts


def _product(row: Dict[str, Any]) -> Product:
    return Product(
        **row_to_api_product(row),
        pre_rerank_position=row.get("pre_rerank_position"),
        pre_rerank_similarity=row.get("pre_rerank_similarity"),
        rank_delta=row.get("rank_delta"),
    )


async def retrieval_search(
    query: str, limit: int = 5
) -> Tuple[List[Product], List[ActivityEntry]]:
    """Hybrid retrieval as the API's products: pgvector and full-text candidates, then rerank.

    Args:
        query: The traveler's words.
        limit: How many trips to return.

    Returns:
        The products in ranked order, and the trace spans.
    """
    rows, spans, _counts = await retrieval_rows(query, limit)
    return [_product(row) for row in rows], spans
