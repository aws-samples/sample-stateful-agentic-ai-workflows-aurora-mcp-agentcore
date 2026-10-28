"""
Gateway target MeridianCatalog: search_trips, compare_packages, price_range,
region_inventory, currency_convert and hybrid_trip_search.

The function deploys alone and needs only boto3, so it carries a port of
backend/tools/catalog_tools.py and backend/retrieval/hybrid.py.
tests/test_catalog_lambda_parity.py pins the SQL text, the merge, the price and stock gate,
the rerank document and reading, the candidate pool, the price-ceiling parser, the FX table and
the payload shape to those modules, and runs both through the same fakes end to end. It runs as
the meridian_catalog login (AURORA_SECRET_ARN is that login's secret), which migration 022 limits
to reading trip_packages and calling package_places_left, so it reads the catalog and never
writes.
"""

from __future__ import annotations

import json
import os
import re
from decimal import Decimal
from typing import Any

import boto3
from botocore.config import Config

EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "cohere.embed-v4:0")
RERANK_MODEL = os.getenv("RERANK_MODEL", "cohere.rerank-v3-5:0")
EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "1024"))
RERANK_CANDIDATE_MULTIPLIER = 5
MIN_CANDIDATES = 25
MAX_LIMIT = 20
MAX_QUERY_CHARS = 500
EMBEDDING_ATTEMPTS = 2
# The function runs 30 s. One search makes EMBEDDING_ATTEMPTS embedding calls and one rerank
# call, and each call is one attempt in all (botocore's total_max_attempts; its max_attempts
# counts retries after the first try, and the legacy mode retries timeouts). At most
# 3 x (2 + 5) = 21 s of Bedrock time leaves the reads and the fallbacks in time. Standard
# mode, as the other fail-fast clients in this repository use.
BEDROCK_CONFIG = Config(
    connect_timeout=2, read_timeout=5, retries={"mode": "standard", "total_max_attempts": 1})
DETAIL_CHARS = 120
MAX_DOCUMENT_CHARS = 2048
MAX_COMPARE = 4
MIN_PLAIN_PRICE = 100

CONFIG: tuple[str, str, str] | None = None
RDS = None
BEDROCK = None

SEARCH_COLUMNS = (
    "package_id, name, trip_type, destination, region, price_per_person, operator, "
    "description, image_url, durations, package_places_left(package_id) AS availability, "
    "highlights"
)
TEXT_COLUMNS = ("name", "description", "operator", "destination", "trip_type")
SEMANTIC_SQL = "SELECT * FROM semantic_trip_search(%s::vector, %s::integer)"
# The same text as hybrid.LEXICAL_SQL and hybrid.HYDRATE_SQL. The parity tests fail, and print
# the module's text, if they differ.
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
HYDRATE_SQL = """
    SELECT package_id, name, operator, price_per_person, description, image_url,
           trip_type, destination, region, durations,
           package_places_left(package_id) AS availability, highlights
    FROM trip_packages
    WHERE package_id IN ({placeholders})
"""
JSON_COLUMNS = ("durations", "availability", "highlights")
FX_PER_USD = {
    "USD": 1.0, "EUR": 0.92, "GBP": 0.79, "JPY": 156.4, "AUD": 1.52,
    "CAD": 1.37, "INR": 83.5, "BRL": 5.45, "CHF": 0.88, "MXN": 17.1,
}
# A fact, not a failure: the Runtime and the Phase 4 audit record the call as allowed.
UNSUPPORTED_CURRENCY = "unsupported_currency"
PRICE_CEILING = re.compile(
    r"(?:under|below|less than|<)\s*(\$)?\s*(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(?:\s*(k)\b)?",
    re.IGNORECASE,
)


class ToolInputError(ValueError):
    """The caller's arguments are unusable; the message is safe to return."""


def _boot() -> None:
    global CONFIG, RDS, BEDROCK
    if CONFIG is None:
        CONFIG = (
            os.environ["AURORA_CLUSTER_ARN"],
            os.environ["AURORA_SECRET_ARN"],
            os.environ["AURORA_DATABASE"],
        )
    if RDS is None:
        RDS = boto3.client("rds-data")
    if BEDROCK is None:
        BEDROCK = boto3.client("bedrock-runtime", config=BEDROCK_CONFIG)


def _encode(value: Any) -> dict:
    if value is None:
        return {"isNull": True}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"longValue": value}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, Decimal):
        return {"stringValue": str(value)}
    if isinstance(value, (list, dict)):
        return {"stringValue": json.dumps(value)}
    return {"stringValue": str(value)}


def execute(sql: str, params: tuple = ()) -> list[dict]:
    """Run one read. ``%s`` placeholders become named Data API parameters, as the backend does."""
    named = sql
    parameters = []
    for index, value in enumerate(params):
        named = named.replace("%s", f":p{index}", 1)
        parameters.append({"name": f"p{index}", "value": _encode(value)})
    cluster_arn, secret_arn, database = CONFIG
    response = RDS.execute_statement(
        resourceArn=cluster_arn, secretArn=secret_arn, database=database, sql=named,
        formatRecordsAs="JSON", parameters=parameters,
    )
    rows = json.loads(response.get("formattedRecords") or "[]")
    for found in rows:
        for column in JSON_COLUMNS:
            if isinstance(found.get(column), str):
                found[column] = json.loads(found[column])
    return rows


def results_to_packages(rows: list[dict]) -> list[dict]:
    """Catalog rows as search_trips returns them (port of backend.search_utils).

    The Data API's JSON format returns DECIMAL as a string and NULL as null: the price becomes
    a float and the text columns an empty string, as the backend's typed-record path gives.
    """
    return [
        {
            "package_id": row["package_id"],
            "name": row["name"],
            "trip_type": row["trip_type"],
            "destination": row.get("destination") or "",
            "region": row.get("region") or "",
            "price_per_person": float(row["price_per_person"]),
            "operator": row.get("operator") or "",
            "description": row.get("description") or "",
            "image_url": row.get("image_url") or "",
            "durations": row.get("durations"),
            "availability": row.get("availability"),
            "highlights": row.get("highlights"),
        }
        for row in rows
    ]


def _number(value: Any, name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as error:
        raise ToolInputError(f"{name} must be a number") from error


def _bounded(value: Any) -> int:
    try:
        return max(1, min(int(value), MAX_LIMIT))
    except (TypeError, ValueError) as error:
        raise ToolInputError("limit must be a whole number") from error


def parse_price_ceiling(query: str) -> float | None:
    """The budget ceiling a prompt names, in dollars, or None (port of backend.search_utils)."""
    for match in PRICE_CEILING.finditer(query):
        dollar, whole, fraction, thousands = match.groups()
        value = float(whole.replace(",", "") + (fraction or ""))
        if thousands:
            return value * 1000
        if dollar or value >= MIN_PLAIN_PRICE:
            return value
    return None


def candidate_limit(limit: int) -> int:
    """How many candidates each arm fetches before the rerank."""
    return max(limit * RERANK_CANDIDATE_MULTIPLIER, MIN_CANDIDATES)


def total_places(availability: Any) -> int:
    """Total remaining places across a package's durations, or ``{"quantity": n}``."""
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


def places_by_package(rows: list[dict]) -> dict[str, int]:
    """Places left per package id, from each row's hydrated ``availability``."""
    return {str(found["package_id"]): total_places(found.get("availability")) for found in rows}


def merge_by_package(semantic_rows: list[dict], lexical_rows: list[dict]) -> list[dict]:
    """One entry per package: semantic rows first, lexical scores added or appended."""
    merged: dict[str, dict] = {}
    for found in semantic_rows:
        merged[found["package_id"]] = dict(found)
    for found in lexical_rows:
        existing = merged.get(found["package_id"])
        if existing:
            existing["lexical_score"] = float(found.get("lexical_score", 0.0))
        else:
            merged[found["package_id"]] = dict(found)
    return list(merged.values())


def apply_gate(rows: list[dict], price_ceiling: float | None, places_left: dict) -> list[dict]:
    """Drop rows over the ceiling and rows whose package has no places left, keeping order.

    A package missing from ``places_left`` counts as having none: unknown stock is not stock.
    """
    kept = []
    for found in rows:
        price = found.get("price_per_person")
        if price_ceiling is not None and price is not None and float(price) > price_ceiling:
            continue
        if places_left.get(str(found["package_id"]), 0) <= 0:
            continue
        kept.append(found)
    return kept


def rerank_document(found: dict) -> str:
    """The text the reranker reads for a candidate."""
    fields = ("name", "destination", "trip_type", "operator", "description")
    return " | ".join(found.get(field, "") or "" for field in fields)


def package_payload(found: dict) -> dict:
    """A ranked row as the tool returns it (port of hybrid.package_payload)."""
    return {
        "package_id": found["package_id"],
        "name": found["name"],
        "operator": found.get("operator"),
        "price_per_person": float(found["price_per_person"]),
        "description": found.get("description"),
        "image_url": found.get("image_url"),
        "trip_type": found.get("trip_type"),
        "destination": found.get("destination"),
        "region": found.get("region"),
        "durations": found.get("durations") or [],
        "availability": found.get("availability") or {},
        "highlights": found.get("highlights") or [],
        "similarity": float(found.get("similarity", 0.0)),
        "pre_rerank_position": found.get("pre_rerank_position"),
        "pre_rerank_similarity": float(found.get("pre_rerank_similarity", 0.0)),
        "rank_delta": found.get("rank_delta", 0),
    }


def candidate_order(rows: list[dict], limit: int) -> list[dict]:
    """The fallback ranking: the order the two arms produced (port of hybrid._candidate_order)."""
    ordered = [dict(found) for found in rows[:limit]]
    for position, found in enumerate(ordered):
        found["pre_rerank_position"] = position
        found["rank_delta"] = 0
    return ordered


def apply_rerank(rows: list[dict], ranked: list[dict]) -> list[dict]:
    """Reorder by the reranker's verdict (port of hybrid._apply_rerank).

    An index outside the candidates, or one already placed, is skipped, so each candidate is
    listed at most once.
    """
    ordered: list[dict] = []
    placed: set[int] = set()
    for item in ranked:
        index = item["index"]
        if not 0 <= index < len(rows) or index in placed:
            continue
        placed.add(index)
        found = dict(rows[index])
        found["similarity"] = float(item.get("score", 0.0))
        found["rank_delta"] = found["pre_rerank_position"] - len(ordered)
        ordered.append(found)
    return ordered


def rerank_results(payload: dict) -> list[dict]:
    """Cohere's ranking as ``{"index", "score"}`` items.

    Port of the reading in backend/db/embedding_service.EmbeddingService.rerank_documents.
    """
    found = payload.get("results") or payload.get("rerank_results") or []
    return [
        {"index": item["index"],
         "score": float(item.get("relevance_score", item.get("score", 0.0)))}
        for item in found if isinstance(item.get("index"), int)
    ]


def _hydrate(merged: list[dict]) -> list[dict]:
    """Join every candidate to the live catalog, keeping the arms' scores (hybrid._hydrate)."""
    if not merged:
        return merged
    ids = tuple(str(found["package_id"]) for found in merged)
    sql = HYDRATE_SQL.format(placeholders=", ".join(["%s"] * len(ids)))
    by_id = {str(found["package_id"]): dict(found) for found in execute(sql, ids)}
    hydrated = []
    for candidate in merged:
        scores = {
            key: candidate[key] for key in ("similarity", "lexical_score") if key in candidate
        }
        hydrated.append({**candidate, **by_id.get(str(candidate["package_id"]), {}), **scores})
    return hydrated


def _log(**fields: Any) -> None:
    print(json.dumps({"meridian_tool": "hybrid_trip_search", **fields}), flush=True)


def _embed_once(query: str) -> list:
    body = {
        "texts": [query], "input_type": "search_query", "truncate": "RIGHT",
        "embedding_types": ["float"], "output_dimension": EMBEDDING_DIMENSION,
    }
    response = BEDROCK.invoke_model(
        modelId=EMBEDDING_MODEL, body=json.dumps(body),
        contentType="application/json", accept="application/json",
    )
    return json.loads(response["body"].read())["embeddings"]["float"][0]


def _embed(query: str) -> str | None:
    """The query as a pgvector literal, or None when Cohere Embed v4 cannot answer.

    Like EmbeddingService.generate_text_embedding, a failed call is tried EMBEDDING_ATTEMPTS
    times and a wrong dimension is not retried. The semantic arm is then dropped and the
    lexical arm answers alone: a vector from any other model would be a wrong answer.
    """
    for _ in range(EMBEDDING_ATTEMPTS):
        try:
            vector = _embed_once(query)
        except Exception as error:  # noqa: BLE001 - retried, then lexical only; type logged
            _log(semantic="retry", error_type=type(error).__name__)
            continue
        if len(vector) != EMBEDDING_DIMENSION:
            _log(semantic="unavailable", error_type="dimension_mismatch")
            return None
        return "[" + ",".join(str(value) for value in vector) + "]"
    _log(semantic="unavailable", attempts=EMBEDDING_ATTEMPTS)
    return None


def _rerank(query: str, rows: list[dict], limit: int) -> tuple[list[dict], bool]:
    """Cohere Rerank 3.5 over the eligible rows; the candidate order and False on any failure.

    No eligible row skips the reranker. A reply with no usable index is a failure, as in
    backend/retrieval/hybrid.py.
    """
    if not rows:
        return [], False
    body = {
        "api_version": 2, "query": query,
        "documents": [rerank_document(found)[:MAX_DOCUMENT_CHARS] for found in rows],
        "top_n": max(1, min(limit, len(rows))),
    }
    try:
        response = BEDROCK.invoke_model(
            modelId=RERANK_MODEL, body=json.dumps(body),
            contentType="application/json", accept="application/json",
        )
        ranked = rerank_results(json.loads(response["body"].read()))
        ordered = apply_rerank(rows, ranked)[:limit]
        if not ordered:
            raise RuntimeError("the reranker returned no usable results for these candidates")
    except Exception as error:  # noqa: BLE001 - the candidate order, said so, the type logged
        _log(rerank="unavailable", error_type=type(error).__name__)
        return candidate_order(rows, limit), False
    return ordered, True


def _query_argument(args: dict) -> str:
    query = args.get("query")
    if not isinstance(query, str) or not query.strip():
        raise ToolInputError("query is required")
    if len(query) > MAX_QUERY_CHARS:
        raise ToolInputError(f"query is longer than {MAX_QUERY_CHARS} characters")
    return query.strip()


def hybrid_trip_search(args: dict) -> dict:
    """The port of hybrid.retrieval_rows: both arms, the catalog join, the gate, the rerank."""
    query = _query_argument(args)
    limit = _bounded(args.get("limit", 5))
    pool = candidate_limit(limit)
    vector = _embed(query)
    semantic = execute(SEMANTIC_SQL, (vector, pool)) if vector is not None else []
    lexical = execute(LEXICAL_SQL, (query, pool))
    merged = merge_by_package(semantic, lexical)
    hydrated = _hydrate(merged)
    eligible = apply_gate(hydrated, parse_price_ceiling(query), places_by_package(hydrated))
    for position, found in enumerate(eligible):
        found["pre_rerank_position"] = position
        found["pre_rerank_similarity"] = float(found.get("similarity", 0.0))
    ranked, reranked = _rerank(query, eligible, limit)
    return {
        "packages": [package_payload(found) for found in ranked],
        "candidates": {"semantic": len(semantic), "lexical": len(lexical), "merged": len(merged)},
        "reranked": reranked,
    }


def _escape_like(text: str) -> str:
    """LIKE's wildcards made literal (port of catalog_tools._escape_like).

    The statement has no ESCAPE clause: the RDS Data API reads the literal ``'\\'`` as an
    escaped quote and binds no parameter after it. The backslash is PostgreSQL's default.
    """
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _search_where(args: dict) -> tuple[str, list[Any]]:
    """The WHERE clause and its values, in placeholder order (port of catalog_tools._where)."""
    clauses: list[str] = []
    values: list[Any] = []
    if args.get("trip_type"):
        clauses.append("trip_type = %s")
        values.append(str(args["trip_type"]))
    if args.get("max_price_per_person") is not None:
        clauses.append("price_per_person <= %s")
        values.append(_number(args["max_price_per_person"], "max_price_per_person"))
    if args.get("text"):
        like = " OR ".join(f"{column} ILIKE %s" for column in TEXT_COLUMNS)
        clauses.append(f"({like})")
        values.extend([f"%{_escape_like(str(args['text']))}%"] * len(TEXT_COLUMNS))
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", values


def _numbered(sql: str) -> str:
    """Each ``%s`` as ``$1``, ``$2`` in order (port of backend.search_utils.numbered_sql)."""
    counter = iter(range(1, 10_000))
    return re.sub(r"%s", lambda _match: f"${next(counter)}", " ".join(sql.split()))


def search_trips(args: dict) -> dict:
    where, values = _search_where(args)
    values.append(_bounded(args.get("limit", 5)))
    sql = (f"SELECT {SEARCH_COLUMNS} FROM trip_packages{where} "
           "ORDER BY price_per_person ASC, package_id ASC LIMIT %s")
    found = execute(sql, tuple(values))
    return {"packages": results_to_packages(found), "sql": _numbered(sql), "parameters": values}


def compare_packages(args: dict) -> list[dict]:
    ids = args.get("package_ids")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise ToolInputError("package_ids must be a list of package id strings")
    if not ids:
        return []
    ids = ids[:MAX_COMPARE]
    placeholders = ",".join(["%s"] * len(ids))
    sql = (
        "SELECT package_id, name, destination, region, operator, trip_type, price_per_person, "
        f"durations, highlights FROM trip_packages WHERE package_id IN ({placeholders})"
    )
    return [
        {
            "package_id": r["package_id"], "name": r["name"], "destination": r.get("destination"),
            "region": r.get("region"), "operator": r.get("operator"),
            "trip_type": r.get("trip_type"), "price_per_person": float(r["price_per_person"]),
            "durations": r.get("durations") or [],
            "top_highlights": (r.get("highlights") or [])[:3],
        }
        for r in execute(sql, tuple(ids))
    ]


def price_range(args: dict) -> dict:
    destination = str(args.get("destination", "")).strip()
    if not destination:
        raise ToolInputError("destination is required")
    pattern = f"%{destination}%"
    rows = execute(
        "SELECT MIN(price_per_person)::float AS min_price, AVG(price_per_person)::float AS "
        "avg_price, MAX(price_per_person)::float AS max_price, COUNT(*) AS sample_size "
        "FROM trip_packages WHERE destination ILIKE %s OR region ILIKE %s",
        (pattern, pattern),
    )
    found = rows[0] if rows else None
    if not found or not found.get("sample_size"):
        return {"destination": destination, "sample_size": 0}
    return {
        "destination": destination, "low": round(found["min_price"], 2),
        "average": round(found["avg_price"], 2), "high": round(found["max_price"], 2),
        "sample_size": int(found["sample_size"]),
        "note": ("Not seasonal: the catalog holds one price per package, not "
                 "per-month or per-season prices."),
    }


def _slots(package_id: str, availability: dict) -> int:
    try:
        return sum(int(v or 0) for v in availability.values())
    except (TypeError, ValueError) as error:
        raise ValueError(f"availability for package {package_id} is not a whole number") from error


def region_inventory(args: dict) -> dict:
    region = str(args.get("region", "")).strip()
    if not region:
        raise ToolInputError("region is required")
    trip_type = args.get("trip_type") or None
    sql = ("SELECT package_id, name, trip_type, package_places_left(package_id) AS availability "
           "FROM trip_packages WHERE region ILIKE %s")
    params: list[Any] = [f"%{region}%"]
    if trip_type:
        sql += " AND trip_type = %s"
        params.append(str(trip_type))
    rows = execute(sql, tuple(params))
    by_type: dict[str, int] = {}
    slots = 0
    for found in rows:
        kind = found.get("trip_type") or "Other"
        by_type[kind] = by_type.get(kind, 0) + 1
        availability = found.get("availability") or {}
        if isinstance(availability, dict):
            slots += _slots(found["package_id"], availability)
    return {"region": region, "trip_type": trip_type, "package_count": len(rows),
            "total_departure_slots": slots, "package_count_by_trip_type": by_type}


def currency_convert(args: dict) -> dict:
    try:
        amount = float(args["amount"])
    except (KeyError, TypeError, ValueError) as error:
        raise ToolInputError("amount must be a number") from error
    source = str(args.get("from_ccy", "")).upper()
    target = str(args.get("to_ccy", "")).upper()
    if source not in FX_PER_USD or target not in FX_PER_USD:
        return {"error": UNSUPPORTED_CURRENCY, "supported": sorted(FX_PER_USD)}
    usd = amount / FX_PER_USD[source]
    return {
        "from": source, "to": target, "amount": amount,
        "converted": round(usd * FX_PER_USD[target], 2),
        "rate": round(FX_PER_USD[target] / FX_PER_USD[source], 6),
        "note": "indicative rate, not for settlement",
    }


TOOLS = {
    "search_trips": search_trips,
    "compare_packages": compare_packages,
    "price_range": price_range,
    "region_inventory": region_inventory,
    "currency_convert": currency_convert,
    "hybrid_trip_search": hybrid_trip_search,
}


ARN = re.compile(r"arn:[a-z0-9-]+:[^\s\"',)]*")
ACCOUNT = re.compile(r"(?<![0-9A-Za-z])\d{12}(?![0-9A-Za-z])")
_TOKEN_CHARS = r"[A-Za-z0-9._~+/=-]"
CREDENTIAL = re.compile(
    r"""(?<![A-Za-z])(password|secret|token|key|authorization)("""
    r"""\\?["']\s*[:=]\s*|\s*=\s*|"""
    rf"""\s*:\s*(?=["']?(?:eyJ[\w-]{{5,}}\.[\w-]{{5,}}\.|{_TOKEN_CHARS}{{16,}}(?!{_TOKEN_CHARS}))))"""
    r"""(?!["']?(?:Bearer\b|<masked>|<token>))("[^"]*"|'[^']*'|\S+)""",
    re.IGNORECASE,
)
JWT = re.compile(r"eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]*")
BEARER = re.compile(r"""Bearer:?\s+['"]?[A-Za-z0-9._~+/=-]{8,}""", re.IGNORECASE)


def _mask_credentials(text: str) -> str:
    """Bearer tokens, credentials and JWTs masked, as backend/security/redaction.py does."""
    bearer_masked = BEARER.sub("Bearer <token>", text)
    return JWT.sub("<token>", CREDENTIAL.sub(r"\1\2<masked>", bearer_masked))


def _masked_detail(error: Exception) -> str:
    """The exception text for the log only: ARNs, account ids and credentials masked, cut short."""
    text = _mask_credentials(str(error))
    text = ACCOUNT.sub("<acct>", ARN.sub("<arn>", text))
    return text[:DETAIL_CHARS]


def lambda_handler(event, context):
    _boot()
    custom = getattr(getattr(context, "client_context", None), "custom", None) or {}
    tool = custom.get("bedrockAgentCoreToolName", "")
    name = tool.split("___")[-1]
    if name not in TOOLS:
        raise ValueError(f"Unknown Meridian tool: {tool or '(none)'}")
    try:
        result = TOOLS[name](event or {})
    except ToolInputError as error:
        result = {"error": str(error)}
    except Exception as error:  # noqa: BLE001 - the caller gets a named error, the log the type
        print(json.dumps({
            "meridian_tool": name, "ok": False, "error_type": type(error).__name__,
            "detail": _masked_detail(error),
            "gatewayRequestId": custom.get("bedrockAgentCoreAwsRequestId"),
        }), flush=True)
        result = {"error": "catalog_tool_failed", "tool": name}
    if isinstance(result, list):
        result = {"result": result}
    print(json.dumps({
        "meridian_tool": name,
        "ok": result.get("error") in (None, UNSUPPORTED_CURRENCY),
        "gatewayRequestId": custom.get("bedrockAgentCoreAwsRequestId"),
    }), flush=True)
    return result
