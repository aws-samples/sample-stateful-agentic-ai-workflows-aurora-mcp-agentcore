"""Live demo contract checks against a local Meridian backend.

Uses real Aurora, Bedrock, MCP, AgentCore Runtime and Gateway. Records each
result and timing; never replaces unavailable services with fixtures. Deletes
only this run's demo records unless --keep is requested. Cloud service audit
traces remain subject to their configured retention.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

BASE = "http://127.0.0.1:8013"
TRAVELER = os.getenv("DEMO_TRAVELER_ID", "trv_meridian_demo")
FINALE = (
    "My JFK-to-Tokyo flight was canceled. Rework the trip, then check duration "
    "availability for the best three options."
)
RECALL = "Recall my Tokyo plan and saved preferences: home airport, food needs, and budget."
WINE = "Find a quiet, romantic wine-country retreat with a private villa."
OUT = Path(".local/demo-validation/evidence.json")
BROWSER_DEADLINE_MS = 55_000

client: httpx.Client
steps: list[dict] = []


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def record(name: str, method: str, path: str, payload=None, **checks):
    started = now()
    t0 = time.perf_counter()
    try:
        if method == "GET":
            response = client.get(path, params=payload)
        else:
            response = client.post(path, json=payload)
        elapsed = int((time.perf_counter() - t0) * 1000)
        try:
            body = response.json()
        except ValueError:
            body = {"raw": response.text[:400]}
        status = response.status_code
    except Exception as exc:  # noqa: BLE001 - evidence must record the failure
        elapsed = int((time.perf_counter() - t0) * 1000)
        body = {"exception": repr(exc)}
        status = None
    entry = {
        "step": name,
        "started_at": started,
        "method": method,
        "path": path,
        "request": payload,
        "http_status": status,
        "elapsed_ms": elapsed,
        "within_browser_deadline": elapsed <= BROWSER_DEADLINE_MS,
    }
    if isinstance(body, dict):
        entry["message"] = (body.get("message") or "")[:600]
        entry["products"] = [p.get("product_id") for p in (body.get("products") or [])]
        entry["rank_deltas"] = [p.get("rank_delta") for p in (body.get("products") or [])]
        entry["follow_ups"] = body.get("follow_ups")
        entry["conversation_id"] = body.get("conversation_id")
        entry["workflow_status"] = body.get("workflow_status")
        entry["workflow_resumed_after_restart"] = body.get("workflow_resumed_after_restart")
        entry["memory_fact_keys"] = [f.get("key") for f in (body.get("memory_facts") or [])]
        entry["activity_titles"] = [a.get("title") for a in (body.get("activities") or [])]
        entry["activity_statuses"] = [
            ((a.get("telemetry") or {}).get("status") or a.get("activity_type"))
            for a in (body.get("activities") or [])
        ]
        if "order" in body:
            entry["order"] = body.get("order")
        if body.get("detail"):
            entry["detail"] = body.get("detail")
    entry["raw_keys"] = sorted(body.keys()) if isinstance(body, dict) else None
    entry["checks"] = {}
    for label, predicate in checks.items():
        try:
            entry["checks"][label] = bool(predicate(body, status))
        except Exception as exc:  # noqa: BLE001
            entry["checks"][label] = f"error: {exc!r}"
    steps.append(entry)
    verdict = "PASS" if all(v is True for v in entry["checks"].values()) else "FAIL"
    print(f"[{verdict}] {name}: http={status} {elapsed} ms checks={entry['checks']}")
    if isinstance(body, dict) and body.get("message"):
        print("    ", body["message"][:220].replace("\n", " "))
    return body, status


def main() -> int:
    ok = lambda b, s: s == 200  # noqa: E731
    has_products = lambda b, s: bool(b.get("products"))  # noqa: E731
    no_products = lambda b, s: not b.get("products")  # noqa: E731

    record("health", "GET", "/api/health", None, ok=ok,
           durable=lambda b, s: b.get("checkpoint_backend") == "AuroraDataApiSaver" and b.get("checkpoint_durable") is True)
    record("memory_profile", "GET", f"/api/memory/{TRAVELER}", None, ok=ok,
           facts=lambda b, s: len(b.get("facts") or []) >= 5,
           budget=lambda b, s: b.get("budget_ceiling_per_traveler_cents") is not None)
    record("rls_probe", "POST", "/api/diagnostics/rls-probe", {}, ok=ok,
           allow=lambda b, s: b["authorization"]["decision"].lower() == "allow",
           deny_decoy=lambda b, s: b["negative_control"]["decision"].lower() == "deny",
           scoped_lt_unscoped=lambda b, s: all(t["scoped_count"] <= t["unscoped_count"] for t in b["tables"] if not t.get("error"))
           and any(t["scoped_count"] < t["unscoped_count"] for t in b["tables"] if not t.get("error")))

    # Phase 1
    record("p1_city_under_2000", "POST", "/api/chat",
           {"message": "Show me city trips under $2,000 per traveler.", "phase": 1, "customer_id": TRAVELER},
           ok=ok, products=has_products,
           sql_span=lambda b, s: any(a.get("sql_query") for a in b["activities"]))
    record("p1_stretch_compare_eur", "POST", "/api/chat",
           {"message": "Compare three trip types and convert each price to euros.", "phase": 1, "customer_id": TRAVELER},
           ok=ok, boundary=lambda b, s: "Switch to MCP" in b["message"], no_products=no_products)

    # Phase 2
    record("p2_compare_eur", "POST", "/api/chat",
           {"message": "Compare three trip types and convert each price to euros.", "phase": 2, "customer_id": TRAVELER},
           ok=ok, compared=lambda b, s: "Compared 3 packages" in b["message"] and "EUR" in b["message"],
           products=has_products,
           concierge_tools=lambda b, s: any("meridian-concierge · compare_packages" in (a.get("title") or "") for a in b["activities"]))
    record("p2_offseason_tokyo", "POST", "/api/chat",
           {"message": "What is the off-season price range for Tokyo trips in November?", "phase": 2, "customer_id": TRAVELER},
           ok=ok, band=lambda b, s: "Seasonal price band" in b["message"] or "No pricing data" in b["message"])
    record("p2_loyalty_scoped", "POST", "/api/chat",
           {"message": "What is my Marriott Bonvoy loyalty status?", "phase": 2, "customer_id": TRAVELER},
           ok=ok, loyalty=lambda b, s: "Loyalty" in b["message"] and "pts" in b["message"],
           not_refused=lambda b, s: "refused" not in b["message"].lower())
    record("p2_stretch_wine", "POST", "/api/chat",
           {"message": WINE, "phase": 2, "customer_id": TRAVELER},
           ok=ok, boundary=lambda b, s: "Switch to Retrieval" in b["message"], no_products=no_products)

    # Phase 3
    record("p3_wine_hybrid_rerank", "POST", "/api/chat",
           {"message": WINE, "phase": 3, "customer_id": TRAVELER},
           ok=ok, products=has_products,
           rerank=lambda b, s: any("rerank applied" in (a.get("title") or "").lower() for a in b["activities"]),
           rank_meta=lambda b, s: all(p.get("rank_delta") is not None for p in b["products"]))
    record("p3_tuscany_lengths", "POST", "/api/chat",
           {"message": "Which trip lengths are still available for Tuscany Wine & Wellness?", "phase": 3, "customer_id": TRAVELER},
           ok=ok, package=lambda b, s: "Tuscany Wine & Wellness" in b["message"],
           inventory=lambda b, s: "package places" in b["message"])
    record("p3_stretch_recall", "POST", "/api/chat",
           {"message": RECALL, "phase": 3, "customer_id": TRAVELER},
           ok=ok, honest=lambda b, s: "Switch to Production" in b["message"], no_products=no_products)

    # Phase 4
    p4, _ = record("p4_tokyo_preferences", "POST", "/api/chat",
                   {"message": "Find Tokyo trips that fit my saved preferences.", "phase": 4, "customer_id": TRAVELER, "memory_enabled": True, "travelers_count": 2},
                   ok=ok, products=has_products,
                   grant=lambda b, s: any(a.get("title") == "Workload traveler grant allowed" for a in b["activities"]),
                   runtime=lambda b, s: any("AgentCore Runtime · turn complete" in (a.get("title") or "") for a in b["activities"]),
                   persisted=lambda b, s: any("persist_turn" in (a.get("title") or "") for a in b["activities"]),
                   facts=lambda b, s: len(b.get("memory_facts") or []) > 0,
                   conversation=lambda b, s: bool(b.get("conversation_id")))
    conv4 = p4.get("conversation_id")
    record("p4_recall_same_conversation", "POST", "/api/chat",
           {"message": RECALL, "phase": 4, "customer_id": TRAVELER, "conversation_id": conv4, "memory_enabled": True, "travelers_count": 2},
           ok=ok, same_conv=lambda b, s: b.get("conversation_id") == conv4,
           mentions_jfk=lambda b, s: "JFK" in b["message"] or "jfk" in b["message"].lower(),
           mentions_shellfish=lambda b, s: "shellfish" in b["message"].lower())
    record("p4_finale_bridge", "POST", "/api/chat",
           {"message": FINALE, "phase": 4, "customer_id": TRAVELER, "conversation_id": conv4, "memory_enabled": True, "travelers_count": 2},
           ok=ok, bridge=lambda b, s: "Switch to Workflow" in b["message"],
           chip=lambda b, s: "Run this in Workflow" in (b.get("follow_ups") or []))
    record("p4_memory_disabled", "POST", "/api/chat",
           {"message": "Find Tokyo trips that fit my saved preferences.", "phase": 4, "customer_id": TRAVELER, "memory_enabled": False},
           ok=ok, off=lambda b, s: "Traveler context is off" in b["message"], no_products=no_products)

    # Phase 5: pause, resume, evidence
    p5, _ = record("p5_finale_pause", "POST", "/api/chat",
                   {"message": FINALE, "phase": 5, "customer_id": TRAVELER, "travelers_count": 2},
                   ok=ok, paused=lambda b, s: b.get("workflow_status") == "paused",
                   products=has_products,
                   resume_chip=lambda b, s: (b.get("follow_ups") or []) == ["Resume workflow from checkpoint"],
                   durable_span=lambda b, s: any(
                       any(f.get("label") == "checkpoint_durable" and f.get("value") == "true"
                           for f in ((a.get("telemetry") or {}).get("fields") or []))
                       for a in b["activities"]))
    conv5 = p5.get("conversation_id")
    p5r, _ = record("p5_resume", "POST", "/api/chat",
                    {"message": "Resume workflow from checkpoint", "phase": 5, "customer_id": TRAVELER, "conversation_id": conv5, "resume": True, "travelers_count": 2},
                    ok=ok, resumed=lambda b, s: b.get("workflow_status") == "resumed",
                    same_thread=lambda b, s: b.get("conversation_id") == conv5,
                    availability=lambda b, s: any("Workflow node: availability" in (a.get("title") or "") for a in b["activities"]),
                    hold_node=lambda b, s: any((a.get("title") or "").startswith("Workflow node: hold") for a in b["activities"]),
                    hold_recorded=lambda b, s: "Aurora recorded courtesy hold" in b["message"],
                    cedar_allow=lambda b, s: any(
                        any(f.get("label") == "cedar_decision" and f.get("value") == "allow"
                            for f in ((a.get("telemetry") or {}).get("fields") or []))
                        for a in b["activities"]))
    record("p5_resume_again_conflict", "POST", "/api/chat",
           {"message": "Resume workflow from checkpoint", "phase": 5, "customer_id": TRAVELER, "conversation_id": conv5, "resume": True, "travelers_count": 2},
           conflict=lambda b, s: s == 409)
    journeys, _ = record("journeys_list", "GET", "/api/journeys", {"limit": 10, "thread_id": conv5},
                         ok=ok)
    journey_id = None
    if isinstance(journeys, list):
        for row in journeys:
            if row.get("active_thread_id") == conv5:
                journey_id = row.get("journey_id")
    elif isinstance(journeys, dict):
        for row in journeys.get("journeys") or journeys.get("items") or []:
            if row.get("active_thread_id") == conv5:
                journey_id = row.get("journey_id")
    steps[-1]["resolved_journey_id"] = journey_id
    steps[-1]["checks"]["journey_resolved"] = bool(journey_id)
    if journey_id:
        record("journey_document", "GET", f"/api/journeys/{journey_id}", None, ok=ok,
               checkpoint=lambda b, s: b["checkpoint"]["status"] != "unavailable",
               hold=lambda b, s: b["hold"]["status"] != "unavailable",
               executions=lambda b, s: len(b["executions"].get("items") or []) >= 2,
               authorization=lambda b, s: b["authorization"]["status"] != "unavailable",
               durable=lambda b, s: b["checkpoint_backend"]["durable"] is True,
               workflow_resumed=lambda b, s: b.get("workflow", {}).get("workflow_status") == "resumed")
    record("session_receipt", "POST", "/api/diagnostics/session-receipt",
           {"conversation_id": conv5, "window_minutes": 90}, ok=ok,
           rows=lambda b, s: sum(l["count"] for l in b["lines"]) > 0,
           durable=lambda b, s: b.get("checkpoint_backend_durable") is True)
    record("p5_amalfi_lengths", "POST", "/api/chat",
           {"message": "Which trip lengths are still available for Amalfi Coast Villa Week?", "phase": 5, "customer_id": TRAVELER, "travelers_count": 2},
           ok=ok, complete=lambda b, s: b.get("workflow_status") == "complete", products=has_products)

    # Phase 4 direct hold and confirmation (governed writes through Gateway + Cedar)
    hold, _ = record("p4_order_hold", "POST", "/api/chat/order",
                     {"product_id": "CTY-002", "size": "7 nights", "quantity": 2, "phase": 4, "traveler_id": TRAVELER, "conversation_id": conv4},
                     ok=ok, held=lambda b, s: (b.get("order") or {}).get("status") == "held",
                     cedar=lambda b, s: any(
                         any(f.get("label") == "cedar_decision" and f.get("value") == "allow"
                             for f in ((a.get("telemetry") or {}).get("fields") or []))
                         for a in b["activities"]))
    booking_id = (hold.get("order") or {}).get("order_id")
    steps[-1]["checks"]["booking_recorded"] = bool(booking_id)
    record("p4_hold_readback", "GET", "/api/chat/holds",
           {"conversation_id": conv4, "product_id": "CTY-002", "duration": "7 nights", "quantity": 2},
           ok=ok, same_booking=lambda b, s: (b.get("order") or {}).get("order_id") == booking_id)
    if booking_id:
        record("p4_booking_read", "GET", f"/api/chat/bookings/{booking_id}", None, ok=ok,
               held=lambda b, s: (b.get("order") or {}).get("status") == "held")
        record("p4_confirm_booking", "POST", "/api/chat/book",
               {"booking_id": booking_id, "phase": 4, "traveler_id": TRAVELER, "conversation_id": conv4},
               ok=ok, confirmed=lambda b, s: (b.get("order") or {}).get("status") == "confirmed")
        record("p4_booking_read_after", "GET", f"/api/chat/bookings/{booking_id}", None, ok=ok,
               confirmed=lambda b, s: (b.get("order") or {}).get("status") == "confirmed")
    record("p4_hold_over_party_limit", "POST", "/api/chat/order",
           {"product_id": "CTY-002", "size": "7 nights", "quantity": 8, "phase": 4, "traveler_id": TRAVELER, "conversation_id": conv4},
           ok=ok, not_held=lambda b, s: not b.get("order"),
           denied=lambda b, s: any(((a.get("telemetry") or {}).get("status")) == "denied" for a in b["activities"]))

    # Authorization boundary at the HTTP layer
    record("decoy_traveler_forbidden", "POST", "/api/chat",
           {"message": "Show me city trips.", "phase": 1, "customer_id": "trv_demo_decoy"},
           forbidden=lambda b, s: s == 403)
    record("decoy_memory_forbidden", "GET", "/api/memory/trv_demo_decoy", None,
           forbidden=lambda b, s: s == 403)

    summary = {
        "generated_at": now(),
        "base_url": BASE,
        "steps": steps,
        "conversation_ids": {"phase4": conv4, "phase5": conv5, "journey": journey_id, "booking": booking_id},
        "failures": [s["step"] for s in steps if not all(v is True for v in s["checks"].values())],
        "over_browser_deadline": [s["step"] for s in steps if not s["within_browser_deadline"]],
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary, indent=2))
    print("\nfailures:", summary["failures"])
    print("over 55 s browser deadline:", summary["over_browser_deadline"])
    print("evidence:", OUT)
    return 0 if not summary["failures"] and not summary["over_browser_deadline"] else 1


async def cleanup() -> None:
    from backend.db.rds_data_client import get_rds_data_client
    from scripts.kill_and_resume_demo import _purge

    db = get_rds_data_client()
    conversations = {step["conversation_id"] for step in steps if step.get("conversation_id")}
    bookings = {step["order"]["order_id"] for step in steps if step.get("order")}
    for thread in conversations:
        rows = await db.execute("SELECT journey_id FROM journey_threads WHERE thread_id = %s", (thread,))
        for row in rows:
            await _purge(db, row["journey_id"], thread)
    for booking in bookings:
        for table in ("hold_requests", "booking_lines", "bookings"):
            await db.execute(f"DELETE FROM {table} WHERE booking_id = %s", (booking,))
    for thread in conversations:
        await db.execute("DELETE FROM trip_interactions WHERE conversation_id = %s", (thread,))
        await db.execute("DELETE FROM conversation_messages WHERE conversation_id = %s", (thread,))
        await db.execute("DELETE FROM conversations WHERE conversation_id = %s", (thread,))
    print(f"Cleanup: removed this run's {len(conversations)} conversations and {len(bookings)} direct booking receipts; cloud audit traces retained.")


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=BASE)
    parser.add_argument("--output", type=Path, default=OUT)
    parser.add_argument("--keep", action="store_true", help="Keep this run's database records for inspection")
    args = parser.parse_args()
    BASE, OUT = args.base_url, args.output
    # This command creates test bookings. Do not silently target a hosted site.
    from urllib.parse import urlparse
    if urlparse(BASE).hostname not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("Use a local backend configured for the authorized demo environment")
    with httpx.Client(base_url=BASE, timeout=60.0) as client:
        try:
            exit_code = main()
        finally:
            if not args.keep:
                asyncio.run(cleanup())
    sys.exit(exit_code)
