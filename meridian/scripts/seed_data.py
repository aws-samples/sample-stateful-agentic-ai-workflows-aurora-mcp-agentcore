"""
Seed Meridian Aurora: trip packages (with embeddings), travelers, profiles, preferences.

AWS docs:
  - RDS Data API (batch inserts):
    https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/data-api.html
  - Cohere Embed v4 (package embeddings):
    https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-embed-v4.html
  - Aurora pgvector:
    https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/AuroraPostgreSQL.Extensions.html#AuroraPostgreSQL.Extensions.pgvector
"""
import json
import os
import uuid
from urllib.parse import urlparse

import boto3
from dotenv import load_dotenv
from rich.console import Console
from rich.progress import track
from rich.table import Table

try:
    # Preferred when executed as a module: python -m scripts.seed_data
    from .travel_catalog import (
        TRIP_PACKAGES,
        TRAVELERS,
        TRAVELER_PROFILES,
        TRAVELER_PREFERENCES,
        DEMO_TRAVELER_ID,
        DEMO_CONVERSATIONS,
        DEMO_CONVERSATION_MESSAGES,
        DEMO_TRIP_INTERACTIONS,
        DEMO_BOOKINGS,
    )
except ImportError:
    # Backward-compatible path when executed as a script: python scripts/seed_data.py
    from travel_catalog import (
        TRIP_PACKAGES,
        TRAVELERS,
        TRAVELER_PROFILES,
        TRAVELER_PREFERENCES,
        DEMO_TRAVELER_ID,
        DEMO_CONVERSATIONS,
        DEMO_CONVERSATION_MESSAGES,
        DEMO_TRIP_INTERACTIONS,
        DEMO_BOOKINGS,
    )

load_dotenv()
console = Console()

EMBEDDING_MODEL_ID = os.getenv("EMBEDDING_MODEL", "cohere.embed-v4:0")
EMBEDDING_DIMENSION = int(os.getenv("EMBEDDING_DIMENSION", "1024"))
BEDROCK_REGION = os.getenv("BEDROCK_REGION", "us-west-2")
AURORA_REGION = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
CLUSTER_ARN = os.getenv("AURORA_CLUSTER_ARN")
SECRET_ARN = os.getenv("AURORA_SECRET_ARN")
DATABASE = os.getenv("AURORA_DATABASE", "meridian")


def _sanitize_loopback_proxy_env() -> None:
    """
    Remove stale loopback proxy settings (common in IDE/sandbox shells).

    These values often look like http://127.0.0.1:56xxx and can cause
    ProxyConnectionError for boto3 calls if the local proxy process is gone.
    We only clear loopback proxies; non-loopback corporate proxies are preserved.
    """
    proxy_keys = [
        "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "all_proxy",
    ]
    for key in proxy_keys:
        value = os.environ.get(key)
        if not value:
            continue
        try:
            host = (urlparse(value).hostname or "").lower()
        except Exception:
            host = ""
        if host in {"127.0.0.1", "localhost", "::1"}:
            os.environ.pop(key, None)


_sanitize_loopback_proxy_env()


def bedrock():
    return boto3.client("bedrock-runtime", region_name=BEDROCK_REGION)


def rds():
    return boto3.client("rds-data", region_name=AURORA_REGION)


def run_sql(sql: str, parameters=None):
    kwargs = dict(
        resourceArn=CLUSTER_ARN,
        secretArn=SECRET_ARN,
        database=DATABASE,
        sql=sql,
    )
    if parameters:
        kwargs["parameters"] = parameters
    return rds().execute_statement(**kwargs)


def embed(client, text: str) -> list[float]:
    body = {
        "texts": [text],
        "input_type": "search_document",
        "embedding_types": ["float"],
        "truncate": "RIGHT",
        "output_dimension": EMBEDDING_DIMENSION,
    }
    resp = client.invoke_model(
        modelId=EMBEDDING_MODEL_ID,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )
    return json.loads(resp["body"].read())["embeddings"]["float"][0]


def package_text(pkg: dict) -> str:
    return ". ".join([
        pkg["name"],
        pkg["description"],
        f"Trip type: {pkg['trip_type']}",
        f"Destination: {pkg['destination']}, {pkg['region']}",
        f"Operator: {pkg['operator']}",
        f"Durations: {', '.join(pkg['durations'])}",
    ])


def clear_data():
    for sql in [
        "DELETE FROM booking_lines",
        "DELETE FROM bookings",
        "DELETE FROM trip_interactions",
        "DELETE FROM conversation_messages",
        "DELETE FROM conversations",
        "DELETE FROM traveler_preferences",
        "DELETE FROM traveler_profiles",
        "DELETE FROM travelers",
        "DELETE FROM trip_packages",
    ]:
        try:
            run_sql(sql)
        except Exception:
            pass


def seed_packages(bedrock_client):
    ok = 0
    for pkg in track(TRIP_PACKAGES, description="Packages"):
        vec = embed(bedrock_client, package_text(pkg))
        vec_str = "[" + ",".join(str(x) for x in vec) + "]"
        run_sql(
            """
            INSERT INTO trip_packages (
                package_id, name, trip_type, destination, region,
                price_per_person, operator, description, image_url,
                durations, availability, highlights, embedding
            ) VALUES (
                :package_id, :name, :trip_type, :destination, :region,
                :price_per_person, :operator, :description, :image_url,
                :durations::jsonb, :availability::jsonb, :highlights::jsonb, :embedding::vector
            )
            ON CONFLICT (package_id) DO UPDATE SET
                name = EXCLUDED.name,
                trip_type = EXCLUDED.trip_type,
                destination = EXCLUDED.destination,
                region = EXCLUDED.region,
                price_per_person = EXCLUDED.price_per_person,
                operator = EXCLUDED.operator,
                description = EXCLUDED.description,
                image_url = EXCLUDED.image_url,
                durations = EXCLUDED.durations,
                availability = EXCLUDED.availability,
                highlights = EXCLUDED.highlights,
                embedding = EXCLUDED.embedding
            """,
            [
                {"name": "package_id", "value": {"stringValue": pkg["package_id"]}},
                {"name": "name", "value": {"stringValue": pkg["name"]}},
                {"name": "trip_type", "value": {"stringValue": pkg["trip_type"]}},
                {"name": "destination", "value": {"stringValue": pkg["destination"]}},
                {"name": "region", "value": {"stringValue": pkg["region"]}},
                {"name": "price_per_person", "value": {"doubleValue": float(pkg["price_per_person"])}},
                {"name": "operator", "value": {"stringValue": pkg["operator"]}},
                {"name": "description", "value": {"stringValue": pkg["description"]}},
                {"name": "image_url", "value": {"stringValue": pkg["image_url"]}},
                {"name": "durations", "value": {"stringValue": json.dumps(pkg["durations"])}},
                {"name": "availability", "value": {"stringValue": json.dumps(pkg["availability"])}},
                {"name": "highlights", "value": {"stringValue": json.dumps(pkg["highlights"])}},
                {"name": "embedding", "value": {"stringValue": vec_str}},
            ],
        )
        ok += 1
    return ok


def seed_travelers():
    for t in TRAVELERS:
        run_sql(
            """
            INSERT INTO travelers (traveler_id, full_name, email, home_airport)
            VALUES (:traveler_id, :full_name, :email, :home_airport)
            ON CONFLICT (traveler_id) DO UPDATE SET
                full_name = EXCLUDED.full_name,
                email = EXCLUDED.email,
                home_airport = EXCLUDED.home_airport
            """,
            [
                {"name": "traveler_id", "value": {"stringValue": t["traveler_id"]}},
                {"name": "full_name", "value": {"stringValue": t["full_name"]}},
                {"name": "email", "value": {"stringValue": t["email"]}},
                {"name": "home_airport", "value": {"stringValue": t["home_airport"]}},
            ],
        )
    for p in TRAVELER_PROFILES:
        run_sql(
            """
            INSERT INTO traveler_profiles (
                traveler_id, party_size, budget_min, budget_max,
                preferred_cabin, seat_preference, dietary_notes, trip_goal, loyalty_programs
            ) VALUES (
                :traveler_id, :party_size, :budget_min, :budget_max,
                :preferred_cabin, :seat_preference, :dietary_notes, :trip_goal, :loyalty_programs::jsonb
            )
            ON CONFLICT (traveler_id) DO UPDATE SET
                party_size = EXCLUDED.party_size,
                budget_min = EXCLUDED.budget_min,
                budget_max = EXCLUDED.budget_max,
                preferred_cabin = EXCLUDED.preferred_cabin,
                seat_preference = EXCLUDED.seat_preference,
                dietary_notes = EXCLUDED.dietary_notes,
                trip_goal = EXCLUDED.trip_goal,
                loyalty_programs = EXCLUDED.loyalty_programs,
                updated_at = CURRENT_TIMESTAMP
            """,
            [
                {"name": "traveler_id", "value": {"stringValue": p["traveler_id"]}},
                {"name": "party_size", "value": {"longValue": p["party_size"]}},
                {"name": "budget_min", "value": {"doubleValue": float(p["budget_min"])}},
                {"name": "budget_max", "value": {"doubleValue": float(p["budget_max"])}},
                {"name": "preferred_cabin", "value": {"stringValue": p["preferred_cabin"]}},
                {"name": "seat_preference", "value": {"stringValue": p["seat_preference"]}},
                {"name": "dietary_notes", "value": {"stringValue": p["dietary_notes"]}},
                {"name": "trip_goal", "value": {"stringValue": p["trip_goal"]}},
                {"name": "loyalty_programs", "value": {"stringValue": json.dumps(p["loyalty_programs"])}},
            ],
        )
    for pref in TRAVELER_PREFERENCES:
        pref_id = f"pref_{uuid.uuid4().hex[:10]}"
        run_sql(
            """
            INSERT INTO traveler_preferences (
                preference_id, traveler_id, preference_type, preference_key,
                preference_value, confidence, signal_count, source
            ) VALUES (
                :preference_id, :traveler_id, :preference_type, :preference_key,
                :preference_value, :confidence, 1, :source
            )
            ON CONFLICT (traveler_id, preference_type, preference_key) DO UPDATE SET
                preference_value = EXCLUDED.preference_value,
                confidence = EXCLUDED.confidence,
                source = EXCLUDED.source,
                last_seen_at = CURRENT_TIMESTAMP
            """,
            [
                {"name": "preference_id", "value": {"stringValue": pref_id}},
                {"name": "traveler_id", "value": {"stringValue": DEMO_TRAVELER_ID}},
                {"name": "preference_type", "value": {"stringValue": pref["preference_type"]}},
                {"name": "preference_key", "value": {"stringValue": pref["preference_key"]}},
                {"name": "preference_value", "value": {"stringValue": pref["preference_value"]}},
                {"name": "confidence", "value": {"doubleValue": float(pref["confidence"])}},
                {"name": "source", "value": {"stringValue": pref["source"]}},
            ],
        )


def _embed_search_query(client, text: str) -> list[float]:
    """Embed conversational text. Use input_type='search_query' so the
    vector lives in the same space as trip_packages.embedding (which use
    search_document). Cohere Embed v4 separates these two."""
    body = {
        "texts": [text],
        "input_type": "search_query",
        "embedding_types": ["float"],
        "truncate": "RIGHT",
        "output_dimension": EMBEDDING_DIMENSION,
    }
    resp = client.invoke_model(
        modelId=EMBEDDING_MODEL_ID,
        body=json.dumps(body),
        contentType="application/json",
        accept="application/json",
    )
    return json.loads(resp["body"].read())["embeddings"]["float"][0]


def _vec_str(vec: list[float]) -> str:
    return "[" + ",".join(str(x) for x in vec) + "]"


def seed_conversations(bedrock_client):
    # Parent rows first.
    for conv in DEMO_CONVERSATIONS:
        run_sql(
            """
            INSERT INTO conversations (
                conversation_id, traveler_id, started_at, last_message_at,
                message_count, summary
            ) VALUES (
                :conversation_id, :traveler_id, :started_at::timestamp,
                :last_message_at::timestamp, :message_count, :summary
            )
            ON CONFLICT (conversation_id) DO UPDATE SET
                last_message_at = EXCLUDED.last_message_at,
                message_count = EXCLUDED.message_count,
                summary = EXCLUDED.summary
            """,
            [
                {"name": "conversation_id", "value": {"stringValue": conv["conversation_id"]}},
                {"name": "traveler_id", "value": {"stringValue": DEMO_TRAVELER_ID}},
                {"name": "started_at", "value": {"stringValue": conv["started_at"]}},
                {"name": "last_message_at", "value": {"stringValue": conv["last_message_at"]}},
                {
                    "name": "message_count",
                    "value": {
                        "longValue": sum(
                            1 for m in DEMO_CONVERSATION_MESSAGES
                            if m["conversation_id"] == conv["conversation_id"]
                        )
                    },
                },
                {"name": "summary", "value": {"stringValue": conv["summary"]}},
            ],
        )

    # Messages, with embeddings so semantic recall works on first turn.
    for i, msg in enumerate(track(
        DEMO_CONVERSATION_MESSAGES, description="Conversation messages",
    )):
        msg_id = f"msg_{i:03d}_{uuid.uuid4().hex[:6]}"
        vec = _embed_search_query(bedrock_client, msg["content"])
        # Compute timestamp as conversation.started_at + offset_minutes
        # so the messages within a thread are correctly ordered by time.
        conv = next(c for c in DEMO_CONVERSATIONS if c["conversation_id"] == msg["conversation_id"])
        run_sql(
            """
            INSERT INTO conversation_messages (
                message_id, conversation_id, role, content, embedding, created_at
            ) VALUES (
                :message_id, :conversation_id, :role, :content,
                :embedding::vector,
                (:base::timestamp + (:offset_min || ' minutes')::interval)
            )
            """,
            [
                {"name": "message_id", "value": {"stringValue": msg_id}},
                {"name": "conversation_id", "value": {"stringValue": msg["conversation_id"]}},
                {"name": "role", "value": {"stringValue": msg["role"]}},
                {"name": "content", "value": {"stringValue": msg["content"]}},
                {"name": "embedding", "value": {"stringValue": _vec_str(vec)}},
                {"name": "base", "value": {"stringValue": conv["started_at"]}},
                {"name": "offset_min", "value": {"stringValue": str(msg["offset_minutes"])}},
            ],
        )


def seed_trip_interactions(bedrock_client):
    for it in track(DEMO_TRIP_INTERACTIONS, description="Trip interactions"):
        # Embed the query_text so semantic_recall_interactions can match
        # new prompts to past intents by meaning.
        vec = _embed_search_query(bedrock_client, it["query_text"])
        params = [
            {"name": "interaction_id", "value": {"stringValue": it["interaction_id"]}},
            {"name": "traveler_id", "value": {"stringValue": DEMO_TRAVELER_ID}},
            {"name": "query_text", "value": {"stringValue": it["query_text"]}},
            {"name": "response_summary", "value": {"stringValue": it["response_summary"]}},
            {"name": "packages_shown", "value": {"stringValue": json.dumps(it["packages_shown"])}},
            {"name": "embedding", "value": {"stringValue": _vec_str(vec)}},
        ]
        if it.get("conversation_id"):
            params.append({"name": "conversation_id", "value": {"stringValue": it["conversation_id"]}})
            run_sql(
                """
                INSERT INTO trip_interactions (
                    interaction_id, traveler_id, conversation_id,
                    query_text, response_summary, packages_shown, embedding
                ) VALUES (
                    :interaction_id, :traveler_id, :conversation_id,
                    :query_text, :response_summary, :packages_shown::jsonb,
                    :embedding::vector
                )
                """,
                params,
            )
        else:
            run_sql(
                """
                INSERT INTO trip_interactions (
                    interaction_id, traveler_id,
                    query_text, response_summary, packages_shown, embedding
                ) VALUES (
                    :interaction_id, :traveler_id,
                    :query_text, :response_summary, :packages_shown::jsonb,
                    :embedding::vector
                )
                """,
                params,
            )


def seed_bookings():
    for bk in DEMO_BOOKINGS:
        run_sql(
            """
            INSERT INTO bookings (
                booking_id, traveler_id, status, total_amount, created_at, confirmed_at
            ) VALUES (
                :booking_id, :traveler_id, :status, :total_amount,
                :created_at::timestamp,
                CASE WHEN :confirmed_at = '' THEN NULL ELSE :confirmed_at::timestamp END
            )
            ON CONFLICT (booking_id) DO UPDATE SET
                status = EXCLUDED.status,
                total_amount = EXCLUDED.total_amount,
                confirmed_at = EXCLUDED.confirmed_at
            """,
            [
                {"name": "booking_id", "value": {"stringValue": bk["booking_id"]}},
                {"name": "traveler_id", "value": {"stringValue": DEMO_TRAVELER_ID}},
                {"name": "status", "value": {"stringValue": bk["status"]}},
                {"name": "total_amount", "value": {"doubleValue": float(bk["total_amount"])}},
                {"name": "created_at", "value": {"stringValue": bk["created_at"]}},
                {"name": "confirmed_at", "value": {"stringValue": bk.get("confirmed_at") or ""}},
            ],
        )
        for ln in bk["lines"]:
            run_sql(
                """
                INSERT INTO booking_lines (
                    booking_id, package_id, duration, travelers_count, unit_price
                ) VALUES (
                    :booking_id, :package_id, :duration, :travelers_count, :unit_price
                )
                """,
                [
                    {"name": "booking_id", "value": {"stringValue": bk["booking_id"]}},
                    {"name": "package_id", "value": {"stringValue": ln["package_id"]}},
                    {"name": "duration", "value": {"stringValue": ln["duration"]}},
                    {"name": "travelers_count", "value": {"longValue": int(ln["travelers_count"])}},
                    {"name": "unit_price", "value": {"doubleValue": float(ln["unit_price"])}},
                ],
            )


# A second, decoy traveler so the RLS probe (Phase 4) shows a real diff:
# unscoped reads see Alex + decoy rows, scoped reads see only the active
# traveler's. Without this there is exactly one traveler and scoped==unscoped,
# which makes "watch RLS filter the rows" invisible. Preferences only (no
# embedding needed) — that's the table the probe defaults to.
DECOY_TRAVELER_ID = "trv_demo_decoy"
DECOY_PREFERENCES = [
    {"preference_type": "lodging", "preference_key": "style", "preference_value": "budget hostels"},
    {"preference_type": "trip", "preference_key": "pace", "preference_value": "fast-paced backpacking"},
    {"preference_type": "dietary", "preference_key": "vegan", "preference_value": "strict vegan"},
    {"preference_type": "flight", "preference_key": "cabin", "preference_value": "economy only"},
    {"preference_type": "region", "preference_key": "interest", "preference_value": "Southeast Asia"},
]


def seed_decoy_traveler():
    """Insert a second traveler with a few preferences so the RLS probe shows
    unscoped > scoped (per-traveler isolation made visible)."""
    run_sql(
        """
        INSERT INTO travelers (traveler_id, full_name, email, home_airport)
        VALUES (:traveler_id, :full_name, :email, :home_airport)
        ON CONFLICT (traveler_id) DO UPDATE SET full_name = EXCLUDED.full_name
        """,
        [
            {"name": "traveler_id", "value": {"stringValue": DECOY_TRAVELER_ID}},
            {"name": "full_name", "value": {"stringValue": "Jordan Lee"}},
            {"name": "email", "value": {"stringValue": "jordan.lee@example.com"}},
            {"name": "home_airport", "value": {"stringValue": "SFO"}},
        ],
    )
    for pref in DECOY_PREFERENCES:
        pref_id = f"pref_{uuid.uuid4().hex[:10]}"
        run_sql(
            """
            INSERT INTO traveler_preferences (
                preference_id, traveler_id, preference_type, preference_key,
                preference_value, confidence, signal_count, source
            ) VALUES (
                :preference_id, :traveler_id, :preference_type, :preference_key,
                :preference_value, 0.9, 1, 'decoy_seed'
            )
            ON CONFLICT (traveler_id, preference_type, preference_key) DO UPDATE SET
                preference_value = EXCLUDED.preference_value
            """,
            [
                {"name": "preference_id", "value": {"stringValue": pref_id}},
                {"name": "traveler_id", "value": {"stringValue": DECOY_TRAVELER_ID}},
                {"name": "preference_type", "value": {"stringValue": pref["preference_type"]}},
                {"name": "preference_key", "value": {"stringValue": pref["preference_key"]}},
                {"name": "preference_value", "value": {"stringValue": pref["preference_value"]}},
            ],
        )


def main():
    if not CLUSTER_ARN or not SECRET_ARN:
        console.print("[red]Missing Aurora credentials in .env[/red]")
        return
    console.print("[bold]Seeding Meridian travel data[/bold]")
    clear_data()
    bc = bedrock()
    n = seed_packages(bc)
    seed_travelers()
    seed_decoy_traveler()
    seed_conversations(bc)
    seed_trip_interactions(bc)
    seed_bookings()
    table = Table(title="Seed summary")
    table.add_column("Entity")
    table.add_column("Count")
    table.add_row("trip_packages", str(n))
    table.add_row("travelers", f"{len(TRAVELERS)} + 1 decoy")
    table.add_row("decoy_preferences", str(len(DECOY_PREFERENCES)))
    table.add_row("traveler_profiles", str(len(TRAVELER_PROFILES)))
    table.add_row("traveler_preferences", str(len(TRAVELER_PREFERENCES)))
    table.add_row("conversations", str(len(DEMO_CONVERSATIONS)))
    table.add_row("conversation_messages", str(len(DEMO_CONVERSATION_MESSAGES)))
    table.add_row("trip_interactions", str(len(DEMO_TRIP_INTERACTIONS)))
    table.add_row(
        "bookings",
        f"{len(DEMO_BOOKINGS)} ({sum(len(b['lines']) for b in DEMO_BOOKINGS)} lines)",
    )
    console.print(table)


if __name__ == "__main__":
    main()
