"""Run one workflow turn as meridian_workflow, the way the Runtime will.

Usage: python -m tests.workflow_login_process <thread_id> <mode>
  start     fresh run; pauses at the confirmation and prints the result status
  resume    resume the saved run to the end and print the result status
"""

import os
import sys

from dotenv import load_dotenv

load_dotenv()
os.environ["AURORA_SECRET_ARN"] = os.environ["AURORA_WORKFLOW_SECRET_ARN"]

import asyncio  # noqa: E402
import json  # noqa: E402
from unittest.mock import AsyncMock  # noqa: E402

from backend.agents.phase_05_workflow.runner import WorkflowCommand  # noqa: E402
from backend.agents.phase_05_workflow.service import build_workflow_runner  # noqa: E402
from tests.phase5_support import GatewayFake, fake_availability, fake_search  # noqa: E402

QUERY = "My flight was canceled. Rework my Tokyo trip and check availability."


async def main(thread_id: str, mode: str) -> None:
    runner = build_workflow_runner(
        search_fn=fake_search, availability_fn=fake_availability,
        gateway_call=GatewayFake(), lease_seconds=15, heartbeat_seconds=3,
    )
    runner._nodes._prepare_governed_hold = AsyncMock(return_value=("jrn_live", 400000))
    runner._nodes._booking_status = AsyncMock(return_value=None)
    command = WorkflowCommand(
        query=QUERY, traveler_id="trv_meridian_demo", thread_id=thread_id,
        resume=mode == "resume", travelers_count=2,
    )
    result = await runner.run(command)
    print(json.dumps({"event": "done", "workflow_status": result["workflow_status"]}), flush=True)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], sys.argv[2]))
