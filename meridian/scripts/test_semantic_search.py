"""
Test semantic trip search with Cohere Embed v4 against trip_packages.
"""
import json
import os

import boto3
from dotenv import load_dotenv
from rich.console import Console

load_dotenv()
console = Console()

EMBEDDING_MODEL_ID = os.getenv("EMBEDDING_MODEL", "cohere.embed-v4:0")
EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "1024"))
REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
CLUSTER_ARN = os.getenv("AURORA_CLUSTER_ARN")
SECRET_ARN = os.getenv("AURORA_SECRET_ARN")
DATABASE = os.getenv("AURORA_DATABASE", "meridian")


def generate_query_embedding(bedrock_client, query: str) -> list:
    request_body = {
        "texts": [query],
        "input_type": "search_query",
        "embedding_types": ["float"],
        "output_dimension": EMBEDDING_DIMENSION,
        "truncate": "END",
    }
    response = bedrock_client.invoke_model(
        modelId=EMBEDDING_MODEL_ID,
        body=json.dumps(request_body),
        contentType="application/json",
        accept="application/json",
    )
    body = json.loads(response["body"].read())
    embeddings = body.get("embeddings", {})
    if isinstance(embeddings, dict):
        return embeddings["float"][0]
    return embeddings[0]


def semantic_search(rds_client, bedrock_client, query: str, limit: int = 5):
    console.print(f"\n[cyan]Query: '{query}'[/cyan]")
    query_embedding = generate_query_embedding(bedrock_client, query)
    embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

    sql = """
        SELECT package_id, name, trip_type, destination, price_per_person,
               1 - (embedding <=> :query_embedding::vector) AS similarity
        FROM trip_packages
        WHERE embedding IS NOT NULL
        ORDER BY embedding <=> :query_embedding::vector
        LIMIT :limit
    """
    response = rds_client.execute_statement(
        resourceArn=CLUSTER_ARN,
        secretArn=SECRET_ARN,
        database=DATABASE,
        sql=sql,
        parameters=[
            {"name": "query_embedding", "value": {"stringValue": embedding_str}},
            {"name": "limit", "value": {"longValue": limit}},
        ],
    )
    return response.get("records", [])


def test_semantic_search():
    console.print("\n[bold blue]Testing semantic trip search[/bold blue]")
    if not CLUSTER_ARN or not SECRET_ARN:
        console.print("[red]Missing AURORA_CLUSTER_ARN or AURORA_SECRET_ARN[/red]")
        return

    bedrock_client = boto3.client("bedrock-runtime", region_name=REGION)
    rds_client = boto3.client("rds-data", region_name=REGION)

    for query in [
        "Romantic week in Europe",
        "Tokyo culture trip for two",
        "Family-friendly beach resort",
    ]:
        results = semantic_search(rds_client, bedrock_client, query, limit=3)
        if not results:
            console.print("[yellow]No results[/yellow]")
            continue
        for i, record in enumerate(results, 1):
            name = record[1].get("stringValue", "")
            dest = record[3].get("stringValue", "")
            sim = record[5].get("doubleValue", 0)
            console.print(f"  {i}. {name} ({dest}) — similarity {sim:.3f}")


if __name__ == "__main__":
    test_semantic_search()
