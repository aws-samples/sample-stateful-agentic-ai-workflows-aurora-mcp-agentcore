#!/usr/bin/env python3
"""Ping the deployed MeridianWorkflow Runtime without touching any journey or Aurora row."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.agentcore.workflow_runtime import get_workflow_runtime, workflow_session_id  # noqa: E402
from scripts.agentcore_caller import caller_scope  # noqa: E402


def main() -> int:
    with caller_scope():
        state = asyncio.run(
            get_workflow_runtime().ping(workflow_session_id("smoke", "phase5-smoke"))
        )
    if state.get("workflow_status") == "ready":
        print(f"MeridianWorkflow ready on {state.get('worker_instance_id')}")
        return 0
    print(state)
    return 1


if __name__ == "__main__":
    sys.exit(main())
