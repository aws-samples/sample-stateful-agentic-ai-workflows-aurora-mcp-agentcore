#!/usr/bin/env python3
"""Prove the governed Production path end to end against the deployed runtime.

Four invocations on one conversation, each streamed over SSE straight from
AgentCore Runtime, so the proof does not depend on the FastAPI backend:

  1. search        : the agent discovers the gateway tools and searches Aurora
  2. unconfirmed   : "hold it now" without the traveler's confirmation; Cedar denies
  3. confirmed     : the same hold with hold_confirmed=True; Cedar permits, Aurora holds
  4. over budget   : confirmed again with a tiny budget ceiling; Cedar denies

Every event is saved under meridian/.local/verification/<conversation>.json.

Usage:
    cd meridian
    python scripts/smoke_production_turn.py [--traveler trv_meridian_demo]
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.agentcore.runtime import get_agentcore_runtime  # noqa: E402

load_dotenv()


def describe(decision) -> None:
    for span in decision.activities:
        status = (span.get("telemetry") or {}).get("status", "ok")
        print(f"  [{status}] {span['title']}")
    print(f"  trace_id={decision.trace_id} elapsed={decision.elapsed_ms} ms")
    print(f"  reply: {decision.message[:220]}")


def hold_target(decision) -> dict | None:
    for package in decision.packages:
        availability = package.get("availability") or {}
        durations = package.get("durations") or list(availability.keys())
        if not durations:
            continue
        duration = durations[0]
        return {
            "package_id": package["package_id"],
            "duration": duration,
            "travelers": 2,
            "unit_price_cents": int(round(float(package.get("price_per_person") or 0) * 100)),
        }
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--traveler", default="trv_meridian_demo")
    args = parser.parse_args()
    runtime = get_agentcore_runtime()
    conversation = f"smoke-{uuid.uuid4().hex[:10]}"
    context = "Alex Morgan flies from JFK, party of two, shellfish allergy, boutique hotels."
    turns = []

    print("1. search")
    search = runtime.invoke_turn(
        conversation, args.traveler, "Find Tokyo trips that fit my saved preferences.",
        context, budget_ceiling_cents=700000, travelers_count=2,
    )
    describe(search)
    turns.append(("search", search))
    target = hold_target(search)
    if not target:
        print("no package with a duration came back; cannot continue")
        return 1

    print("2. unconfirmed hold (expect a Cedar deny)")
    denied = runtime.invoke_turn(
        conversation, args.traveler,
        f"Hold {target['package_id']} for {target['duration']} for two travelers now.",
        context, budget_ceiling_cents=700000, travelers_count=2,
    )
    describe(denied)
    turns.append(("unconfirmed", denied))

    print("3. confirmed hold (expect held)")
    held = runtime.invoke_turn(
        conversation, args.traveler, "Place the hold I confirmed.", context,
        budget_ceiling_cents=700000, travelers_count=2, hold_confirmed=True, hold_target=target,
    )
    describe(held)
    turns.append(("confirmed", held))

    print("4. confirmed but over budget (expect a Cedar deny)")
    over = runtime.invoke_turn(
        conversation, args.traveler, "Place the hold I confirmed.", context,
        budget_ceiling_cents=1000, travelers_count=2, hold_confirmed=True, hold_target=target,
    )
    describe(over)
    turns.append(("over_budget", over))

    folder = ROOT / ".local" / "verification"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{conversation}.json"
    path.write_text(json.dumps({
        "at": datetime.now(timezone.utc).isoformat(),
        "conversation": conversation,
        "turns": [{"name": name, **_plain(decision)} for name, decision in turns],
    }, indent=2, ensure_ascii=False) + "\n")
    print(f"saved {path}")

    outcomes = {
        "unconfirmed denied": denied.policy_decision == "deny" and denied.hold is None,
        "confirmed held": (held.hold or {}).get("status") == "held",
        "over budget denied": over.policy_decision == "deny" and over.hold is None,
    }
    for label, ok in outcomes.items():
        print(f"  {'PASS' if ok else 'FAIL'} {label}")
    return 0 if all(outcomes.values()) else 1


def _plain(decision) -> dict:
    return {
        "message": decision.message,
        "activities": decision.activities,
        "packages": decision.packages,
        "hold": decision.hold,
        "hold_refused": decision.hold_refused,
        "policy_decision": decision.policy_decision,
        "trace_id": decision.trace_id,
        "usage": decision.usage,
        "elapsed_ms": decision.elapsed_ms,
        "runtime_session_id": decision.runtime_session_id,
    }


if __name__ == "__main__":
    raise SystemExit(main())
