"""
Gateway target: semantic_trip_search.

This Lambda is designed for AgentCore Gateway `lambda-function-arn` targets.
It accepts `{query, limit}` arguments, generates a Cohere Embed v4 vector with
Bedrock, then executes Aurora `semantic_trip_search(...)` over RDS Data API.
"""

from __future__ import annotations

import json
import os
from decimal import Decimal
from typing import Any, Dict, List

import boto3

BEDROCK_MODEL_ID = os.getenv("EMBEDDING_MODEL", "cohere.embed-v4:0")
EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "1024"))
DB_NAME = os.environ["AURORA_DATABASE"]
CLUSTER_ARN = os.environ["AURORA_CLUSTER_ARN"]
SECRET_ARN = os.environ["AURORA_SECRET_ARN"]

bedrock = boto3.client("bedrock-runtime")
rds = boto3.client("rds-data")


def _extract_args(event: Dict[str, Any]) -> Dict[str, Any]:
    """Accept common Gateway/Lambda envelope variants."""
    if isinstance(event.get("arguments"), dict):
        return event["arguments"]
    if isinstance(event.get("input"), dict):
        return event["input"]
    if isinstance(event.get("params"), dict):
        params = event["params"]
        if isinstance(params.get("arguments"), dict):
            return params["arguments"]
    return event


def _embed_query(query: str) -> List[float]:
    body = {
        "texts": [query],
        "input_type": "search_query",
        "embedding_types": ["float"],
        "output_dimension": EMBEDDING_DIMENSION,
    }
    response = bedrock.invoke_model(
        modelId=BEDROCK_MODEL_ID,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )
    payload = json.loads(response["body"].read())
    if isinstance(payload.get("embedding"), list):
        return payload["embedding"]
    embeddings = payload.get("embeddings")
    if isinstance(embeddings, dict):
        floats = embeddings.get("float")
        if isinstance(floats, list) and floats:
            return floats[0]
    if isinstance(embeddings, list) and embeddings:
        first = embeddings[0]
        if isinstance(first, dict) and isinstance(first.get("float"), list):
            return first["float"]
        if isinstance(first, list):
            return first
    raise RuntimeError("Unexpected embedding response shape")


def _field_value(field: Dict[str, Any]) -> Any:
    if field.get("isNull"):
        return None
    if "stringValue" in field:
        return field["stringValue"]
    if "longValue" in field:
        return field["longValue"]
    if "doubleValue" in field:
        return field["doubleValue"]
    if "booleanValue" in field:
        return field["booleanValue"]
    if "blobValue" in field:
        return field["blobValue"]
    if "arrayValue" in field:
        arr = field["arrayValue"]
        values = arr.get("stringValues") or arr.get("longValues") or arr.get("doubleValues")
        return values if values is not None else []
    return None


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    return value


def _execute_semantic_search(query: str, limit: int) -> List[Dict[str, Any]]:
    embedding = _embed_query(query)
    embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"
    sql = (
        "SELECT package_id, name, operator, price_per_person, description, image_url, "
        "trip_type, destination, region, durations, similarity "
        "FROM semantic_trip_search(CAST(:query_embedding AS vector), CAST(:result_limit AS integer))"
    )
    response = rds.execute_statement(
        resourceArn=CLUSTER_ARN,
        secretArn=SECRET_ARN,
        database=DB_NAME,
        sql=sql,
        includeResultMetadata=True,
        parameters=[
            {"name": "query_embedding", "value": {"stringValue": embedding_str}},
            {"name": "result_limit", "value": {"longValue": int(limit)}},
        ],
    )
    columns = [c.get("name", "") for c in response.get("columnMetadata", [])]
    records = response.get("records", [])
    packages: List[Dict[str, Any]] = []
    for record in records:
        row = {columns[idx]: _field_value(field) for idx, field in enumerate(record)}
        # durations is jsonb in Aurora; decode if delivered as text.
        durations = row.get("durations")
        if isinstance(durations, str):
            try:
                row["durations"] = json.loads(durations)
            except json.JSONDecodeError:
                pass
        packages.append(_to_jsonable(row))
    return packages


def lambda_handler(event: Dict[str, Any], _context: Any) -> Dict[str, Any]:
    args = _extract_args(event or {})
    query = str(args.get("query", "")).strip()
    limit = int(args.get("limit", 5))

    if not query:
        return {"error": "query is required", "packages": []}

    try:
        packages = _execute_semantic_search(query=query, limit=max(1, min(limit, 20)))
        return {"packages": packages}
    except Exception as exc:
        return {"error": str(exc), "packages": []}
